"""Metadata-report SQLite persistence: schema admission, binding, and
transaction boundaries.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import mechanical_librarian.adapters.persistence.binding as persistence_binding
import mechanical_librarian.adapters.persistence.metadata_reports as persistence_reports
import mechanical_librarian.adapters.persistence.schema as persistence_schema
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from tests.support.metadata_report_schemas import (
    create_legacy_schema,
    create_prior_schema,
    forged_latin_work_report_json,
    leave_hot_journal,
    synthetic_latin_work_report,
    synthetic_reordered_work_report,
    synthetic_report,
)


def test_publication_currentness_runs_once_before_the_writer_lock() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        calls: list[None] = []

        def currentness() -> bool:
            calls.append(None)
            return True

        try:
            repository.publish(synthetic_report(), currentness)
        finally:
            repository.close()

    assert 1 == len(calls)


def test_package_assertion_rolls_back_a_package_pointer_race() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        source = synthetic_report("Source")
        successor = synthetic_report("Successor")
        try:
            repository.publish(source, lambda: True)
            repository._connection.execute("PRAGMA ignore_check_constraints=ON")
            repository._connection.execute(
                "UPDATE current_review_packages SET outcome='hostile'"
            )
            repository._connection.execute("PRAGMA ignore_check_constraints=OFF")

            with pytest.raises(MetadataReportError, match="review package is invalid"):
                repository.publish(
                    successor,
                    lambda: True,
                    source.report_id,
                    expected_package=(
                        None,
                        source.report_id,
                        None,
                        None,
                        "report_only",
                    ),
                )

            assert source.to_json() == repository.current(
                source.target.connection,
                source.target.library,
                source.target.item,
                source.file.identity,
            )
            with pytest.raises(MetadataReportError, match="review package is invalid"):
                repository.current_review_package(
                    source.target.connection, source.target.library, source.target.item
                )
            assert (
                repository._connection.execute(
                    "SELECT report_id FROM metadata_reports WHERE report_id=?",
                    (successor.report_id,),
                ).fetchone()
                is None
            )
        finally:
            repository.close()


def test_absent_field_effect_consent_remains_a_semantic_absence() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        try:
            with pytest.raises(FieldEffectError) as raised:
                repository.current_field_effect_consent("primary", "ebooks", "title")
        finally:
            repository.close()

    assert raised.value.code == "consent"


def test_slow_external_currentness_does_not_hold_the_publication_lock() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        slow = open_repository(path, cross_thread=True)
        fast = open_repository(
            path,
            create=False,
            cross_thread=True,
            authority=slow.authority,
        )
        entered = threading.Event()
        release = threading.Event()
        fast_completed = threading.Event()
        errors: list[tuple[str, Exception]] = []

        def slow_currentness() -> bool:
            entered.set()
            release.wait(5)
            return True

        def publish_slow() -> None:
            try:
                slow.publish(synthetic_report("Slow title"), slow_currentness)
            except Exception as error:
                errors.append(("slow", error))

        def publish_fast() -> None:
            try:
                fast.publish(synthetic_report("Fast title"), lambda: True)
            except Exception as error:
                errors.append(("fast", error))
            finally:
                fast_completed.set()

        slow_thread = threading.Thread(target=publish_slow)
        fast_thread = threading.Thread(target=publish_fast)
        slow_thread.start()
        assert entered.wait(1)
        fast_thread.start()
        try:
            assert fast_completed.wait(1)
        finally:
            release.set()
            slow_thread.join(5)
            fast_thread.join(5)
            fast.close()
            slow.close()

        assert not slow_thread.is_alive()
        assert not fast_thread.is_alive()
        assert [name for name, _ in errors] == ["slow"]
        assert isinstance(errors[0][1], MetadataReportError)


@pytest.mark.parametrize(
    ("kind", "prepare"),
    (
        (
            "legacy v1",
            create_legacy_schema,
        ),
        (
            "previous v9",
            create_prior_schema,
        ),
        (
            "current report schema with stale proposal policy",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v49",
                "fixed-conservative-v6",
            ),
        ),
        (
            "previous v12",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v12",
            ),
        ),
        (
            "previous v17",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v17",
            ),
        ),
        (
            "previous v18",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v18",
            ),
        ),
        (
            "previous v19",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v19",
            ),
        ),
        (
            "previous v20",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v20",
            ),
        ),
        (
            "previous v21",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v21",
            ),
        ),
        (
            "previous v22",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v22",
            ),
        ),
        (
            "immediate previous v23",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v23",
            ),
        ),
        (
            "immediate previous v24",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v24",
            ),
        ),
        (
            "previous v25",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v25",
            ),
        ),
        (
            "immediate previous v26",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v26",
            ),
        ),
        (
            "immediate previous v27",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v27",
            ),
        ),
        (
            "immediate previous v28",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v28",
            ),
        ),
        (
            "immediate previous v30",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v30",
            ),
        ),
        (
            "previous v31",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v31",
            ),
        ),
        (
            "previous v32",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v32",
            ),
        ),
        (
            "previous v33",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v33",
            ),
        ),
        (
            "immediate previous v35",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v35",
            ),
        ),
        (
            "previous proposal policy v3",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v49",
                "fixed-conservative-v3",
            ),
        ),
        (
            "previous proposal policy v4",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v49",
                "fixed-conservative-v4",
            ),
        ),
        (
            "previous proposal policy v5",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v49",
                "fixed-conservative-v5",
            ),
        ),
        (
            "immediate previous proposal policy v7",
            lambda connection: create_prior_schema(
                connection,
                "metadata-report-v49",
                "fixed-conservative-v7",
            ),
        ),
        (
            "F09k report schema",
            lambda connection: (
                connection.execute(
                    persistence_schema._REPORTS_SQL.replace(
                        "= 'metadata-report-v6'",
                        "= 'metadata-report-v5'",
                    )
                ),
                connection.execute(persistence_schema._CURRENT_SQL),
                connection.execute(persistence_schema._NO_UPDATE_SQL),
                connection.execute(persistence_schema._NO_DELETE_SQL),
            ),
        ),
        (
            "F09i document bound",
            lambda connection: (
                connection.execute(
                    persistence_schema._REPORTS_SQL.replace(
                        "BETWEEN 2 AND 2097152", "BETWEEN 2 AND 262144"
                    )
                ),
                connection.execute(persistence_schema._CURRENT_SQL),
                connection.execute(persistence_schema._NO_UPDATE_SQL),
                connection.execute(persistence_schema._NO_DELETE_SQL),
            ),
        ),
        (
            "F09j one mebibyte bound",
            lambda connection: (
                connection.execute(
                    persistence_schema._REPORTS_SQL.replace(
                        "BETWEEN 2 AND 2097152", "BETWEEN 2 AND 1048576"
                    )
                ),
                connection.execute(persistence_schema._CURRENT_SQL),
                connection.execute(persistence_schema._NO_UPDATE_SQL),
                connection.execute(persistence_schema._NO_DELETE_SQL),
            ),
        ),
        (
            "intermediate extra column",
            lambda connection: (
                connection.execute(persistence_schema._REPORTS_SQL),
                connection.execute(
                    "ALTER TABLE metadata_reports ADD COLUMN intermediate TEXT"
                ),
            ),
        ),
    ),
)
def test_legacy_and_intermediate_schemas_are_rejected_without_mutation(
    kind: str, prepare: Callable[[sqlite3.Connection], object]
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        connection = sqlite3.connect(path)
        prepare(connection)
        connection.commit()
        connection.close()
        before = path.read_bytes()
        with pytest.raises(MetadataReportError):
            open_repository(path)
        assert path.read_bytes() == before


def test_clean_database_has_exact_current_tables_and_replays() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        first = synthetic_report()
        assert repository.publish(first, lambda: True) == first.to_json()
        assert repository.publish(first, lambda: True) == first.to_json()
        second = synthetic_report("Changed title")
        repository.publish(second, lambda: True, first.report_id)
        repository.close()

        connection = sqlite3.connect(path)
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        schema_version = connection.execute(
            "SELECT schema_version FROM metadata_reports"
        ).fetchone()
        rows = connection.execute(
            "SELECT report_id FROM metadata_reports ORDER BY report_id"
        ).fetchall()
        current = connection.execute(
            "SELECT report_id FROM current_metadata_reports"
        ).fetchone()
        connection.close()
        assert tables == [
            ("automatic_decisions",),
            ("automatic_safety_state",),
            ("browse_fact_identity",),
            ("cover_consideration_events",),
            ("cover_considerations",),
            ("cover_selection_authorities",),
            ("current_cover_considerations",),
            ("current_cover_selection_authorities",),
            ("current_description_advice",),
            ("current_description_decisions",),
            ("current_metadata_report_lineages",),
            ("current_metadata_reports",),
            ("current_review_case_lineages",),
            ("current_review_packages",),
            ("description_advice_attempts",),
            ("description_advice_candidates",),
            ("description_decisions",),
            ("description_preparation_pending",),
            ("field_effect_actions",),
            ("field_effect_authorities",),
            ("field_effect_cleanups",),
            ("field_effect_consents",),
            ("field_effect_control",),
            ("field_effect_events",),
            ("field_effect_manual_holds",),
            ("field_effect_no_changes",),
            ("field_effect_suspensions",),
            ("field_effects",),
            ("human_decision_events",),
            ("human_decisions",),
            ("metadata_policy_drafts",),
            ("metadata_policy_resolutions",),
            ("metadata_policy_state",),
            ("metadata_policy_transition_items",),
            ("metadata_policy_transitions",),
            ("metadata_report_browse_authors",),
            ("metadata_report_browse_facts",),
            ("metadata_report_cover_references",),
            ("metadata_reports",),
            ("owner_setting_secrets",),
            ("owner_settings",),
            ("proposal_set_browse_facts",),
            ("proposal_sets",),
            ("provider_recovery_outbox",),
            ("review_case_field_facts",),
            ("review_case_transitions",),
            ("review_cases",),
            ("review_preparation_absences",),
            ("review_preparation_connection_conditions",),
            ("review_preparation_control",),
            ("review_preparation_presentation",),
            ("review_preparation_scans",),
            ("review_preparation_work",),
            ("reviewer_access",),
            ("reviewer_access_secrets",),
            ("shadow_decision_outcomes",),
            ("shadow_outcome_field_facts",),
        ]
        assert len(rows) == 2
        assert schema_version == ("metadata-report-v49",)
        assert current == (second.report_id,)

        reopened = open_repository(path)
        assert reopened.get(first.report_id) == first.to_json()
        assert (
            reopened.current("primary", "ebooks", "item-1", "a" * 64)
            == second.to_json()
        )
        reopened.close()


def test_latin_work_lineages_survive_sqlite_publication_and_reopen() -> None:
    report = synthetic_latin_work_report()
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        try:
            assert report.to_json() == repository.publish(report, lambda: True)
            replayed = MetadataReport.from_json(repository.get(report.report_id))
        finally:
            repository.close()

    assert replayed == report
    assert replayed.providers[0].dispatch_lineage == (
        report.providers[0].dispatch_lineage
    )
    assert replayed.providers[0].transformation_lineage == (
        report.providers[0].transformation_lineage
    )


def test_reordered_work_authors_survive_sqlite_publication_and_reopen() -> None:
    report = synthetic_reordered_work_report()
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        try:
            assert report.to_json() == repository.publish(report, lambda: True)
            replayed = MetadataReport.from_json(repository.get(report.report_id))
        finally:
            repository.close()

    assert replayed == report
    assert replayed.providers[0].transformation_lineage is None


def test_sqlite_replay_rejects_latin_work_lineage_mismatch() -> None:
    report = synthetic_latin_work_report()
    forged_id, forged_json = forged_latin_work_report_json()
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        try:
            repository._connection.execute(
                "INSERT INTO metadata_reports(report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    forged_id,
                    report.target.connection,
                    report.target.library,
                    report.target.item,
                    report.file.identity,
                    report.file.content_digest,
                    report.schema_version,
                    hashlib.sha256(forged_json.encode()).hexdigest(),
                    forged_json,
                ),
            )

            with pytest.raises(
                MetadataReportError, match="persisted report is invalid"
            ):
                repository.get(forged_id)
        finally:
            repository.close()


def test_provider_recovery_outbox_rejects_digest_corruption_on_restart() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        report = synthetic_report()
        repository.publish(report, lambda: True)
        outbox_id = persistence_schema._provider_recovery_outbox_id(
            report.target.connection,
            report.target.library,
            report.target.item,
            None,
            report.report_id,
            None,
            None,
            "report_only",
        )
        repository._connection.execute(
            "INSERT INTO provider_recovery_outbox(outbox_id,connection_name,"
            "library_name,item_id,proposal_set_id,report_id,review_case_id,"
            "checkpoint,outcome,created_at,delivered_at) "
            "VALUES(?,?,?,?,NULL,?,NULL,NULL,'report_only',1,NULL)",
            (
                outbox_id,
                report.target.connection,
                report.target.library,
                report.target.item,
                report.report_id,
            ),
        )
        repository._connection.commit()
        repository.close()
        open_repository(path).close()

        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE provider_recovery_outbox SET outbox_id=?",
            ("recovery_outbox_" + "0" * 64,),
        )
        connection.commit()
        connection.close()
        with pytest.raises(MetadataReportError, match="outbox"):
            open_repository(path)


def test_same_candidate_replay_converges_after_a_stale_null_baseline() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = synthetic_report()
        repository.publish(report, lambda: True)
        assert (
            repository.publish(report, lambda: True, expected_current=None)
            == report.to_json()
        )
        changed = synthetic_report("Changed title")
        with pytest.raises(MetadataReportError, match="current report changed"):
            repository.publish(changed, lambda: True, expected_current=None)
        repository.close()


def test_preexisting_unknown_database_is_rejected_without_change() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "old.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE legacy(value TEXT)")
        connection.commit()
        connection.close()
        before = path.read_bytes()
        with pytest.raises(MetadataReportError):
            open_repository(path)
        assert path.read_bytes() == before


def test_prior_shadow_outcome_schema_is_rejected_without_change(tmp_path, monkeypatch):
    from contextlib import closing

    from mechanical_librarian.adapters.persistence import schema

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/shadow-outcome-v9.json").read_text()
    )
    assert fixture["source_sha"] == "08ae544e20ea79037f7e4a1d5f66b482554ed1dc"
    cover_fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/cover-authority-v2.json").read_text()
    )
    path = tmp_path / "prior-shadow.sqlite"
    predecessor = json.loads(
        (
            Path(__file__).parents[1]
            / "support/proposal_predecessors/fixed-conservative-v32.json"
        ).read_text()
    )
    with monkeypatch.context() as old:
        old.setattr(
            persistence_schema,
            "_COVER_CONSIDERATIONS_SQL",
            json.loads(
                (
                    Path(__file__).parents[1]
                    / "fixtures/cover-consideration-embedded-report.json"
                ).read_text()
            )["cover_considerations_sql"],
        )
        old.setattr(
            persistence_schema, "_PROPOSAL_SETS_SQL", predecessor["proposal_sets_ddl"]
        )
        # Reconstruct the frozen producer's reviewer table too; never update
        # its historical inventory digest to the current schema.
        old.setattr(
            schema,
            "_REVIEWER_ACCESS_SQL",
            "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=1),document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 262144))",
        )
        old.setattr(
            schema,
            "_COVER_SELECTION_AUTHORITIES_SQL",
            cover_fixture["cover_selection_authorities_sql"],
        )
        old.setattr(
            schema,
            "_SHADOW_DECISION_OUTCOMES_SQL",
            fixture["shadow_decision_outcomes_sql"],
        )
        with closing(open_repository(path)) as repository:
            # The producer predates #1199's report browse facts, #1202's
            # history indexes and #1204's indexes.
            repository._connection.executescript(
                "DROP TRIGGER metadata_reports_project_browse_facts;"
                "DROP TABLE metadata_report_browse_authors;"
                "DROP TABLE metadata_report_browse_facts;"
                "DROP TRIGGER proposal_sets_project_browse_facts;"
                "DROP TABLE proposal_set_browse_facts;"
                "DROP TABLE browse_fact_identity;"
                "DROP INDEX human_decisions_case_destination;"
                "DROP INDEX metadata_policy_resolutions_resolved;"
                "DROP INDEX field_effects_decision;"
                "DROP INDEX field_effects_companion_decision;"
                "DROP INDEX field_effects_ready;"
                "DROP INDEX field_effects_waiting;"
                "DROP INDEX field_effects_target;"
                "DROP INDEX description_decisions_case;"
            )
            rows = repository._connection.execute(
                "SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            assert len(rows) == 104
            assert (
                hashlib.sha256(
                    json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest()
                == fixture["schema_inventory_sha256"]
            )
    before = path.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    assert before == path.read_bytes()


def test_preview_digest_requires_exact_report_binding() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        artifact = CoverArtifact.create(b"source", b"preview", "image/png", 1, 1)
        source = CoverSource(
            cover_source_id(CoverSourceKind.EPUB, "cover"),
            CoverSourceKind.EPUB,
            CoverSourceState.RETRIEVED,
            artifact,
        )
        report = synthetic_report()
        report = MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            cover_sources=(source,),
        )
        repository = open_repository(path)
        repository.publish(report, lambda: True)
        assert artifact.preview_digest == repository.preview_digest(
            report.report_id, artifact.artifact_id, artifact.preview_variant
        )
        assert repository.artifact_is_referenced(artifact.original_digest)
        repository._connection.execute(
            "DROP TRIGGER metadata_report_cover_references_no_delete"
        )
        repository._connection.execute(
            "DELETE FROM metadata_report_cover_references WHERE report_id=?",
            (report.report_id,),
        )
        with pytest.raises(MetadataReportError, match="cover references"):
            repository.artifact_is_referenced(artifact.original_digest)
        with pytest.raises(MetadataReportError):
            repository.preview_digest(
                report.report_id, "cover_" + "0" * 64, artifact.preview_variant
            )
        repository.close()


def test_corrupt_document_and_envelope_disagreement_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        report = synthetic_report()
        repository.publish(report, lambda: True)
        repository._connection.execute("DROP TRIGGER metadata_reports_no_update")
        repository._connection.execute("UPDATE metadata_reports SET document='{}'")
        with pytest.raises(MetadataReportError):
            repository.get(report.report_id)
        repository._connection.execute(
            "UPDATE metadata_reports SET document=?,document_digest=?",
            (report.to_json(), hashlib.sha256(report.to_json().encode()).hexdigest()),
        )
        repository._connection.execute(
            "UPDATE metadata_reports SET connection_name='other'"
        )
        with pytest.raises(MetadataReportError):
            repository.get(report.report_id)
        unsafe = json.loads(report.to_json())
        unsafe["local"]["claims"][0]["canonical"] = "/Users/private/title"
        document = json.dumps(
            unsafe, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        repository._connection.execute(
            "UPDATE metadata_reports SET connection_name=?,document=?,document_digest=?",
            ("primary", document, hashlib.sha256(document.encode()).hexdigest()),
        )
        with pytest.raises(MetadataReportError):
            repository.get(report.report_id)
        repository.close()


@pytest.mark.parametrize(
    "statement",
    (
        "CREATE VIEW unexpected_view AS SELECT report_id FROM metadata_reports",
        "CREATE INDEX unexpected_index ON metadata_reports(connection_name)",
    ),
    ids=("view", "index"),
)
def test_existing_schema_rejects_unexpected_objects_without_mutation(
    statement: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        repository.publish(synthetic_report(), lambda: True)
        repository.close()
        connection = sqlite3.connect(path)
        connection.execute(statement)
        connection.commit()
        expected_objects = connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
        expected_counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM metadata_reports),(SELECT COUNT(*) FROM current_metadata_reports)"
        ).fetchone()
        connection.close()
        before = path.read_bytes()
        with pytest.raises(MetadataReportError):
            open_repository(path)
        assert path.read_bytes() == before
        connection = sqlite3.connect(path)
        assert (
            connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            == expected_objects
        )
        assert (
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM metadata_reports),(SELECT COUNT(*) FROM current_metadata_reports)"
            ).fetchone()
            == expected_counts
        )
        connection.close()


def test_candidate_cleanup_ignores_unrelated_malformed_history() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = synthetic_report()
        repository.publish(report, lambda: True)
        repository._connection.execute("DROP TRIGGER metadata_reports_no_update")
        repository._connection.execute(
            "UPDATE metadata_reports SET document='{}',document_digest=? WHERE report_id=?",
            (hashlib.sha256(b"{}").hexdigest(), report.report_id),
        )
        assert not repository.artifact_is_referenced("0" * 64)
        repository.close()


@pytest.mark.parametrize(
    "kind",
    ("analyze", "sequence", "arbitrary"),
)
def test_existing_schema_rejects_persistent_sqlite_master_system_objects_without_mutation(
    kind: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        repository.publish(synthetic_report(), lambda: True)
        repository.close()
        connection = sqlite3.connect(path)
        if kind == "analyze":
            connection.execute("ANALYZE")
        elif kind == "sequence":
            connection.execute(
                "CREATE TABLE fixture_sequence(id INTEGER PRIMARY KEY AUTOINCREMENT)"
            )
            connection.execute("DROP TABLE fixture_sequence")
        else:
            connection.execute("CREATE TABLE fixture_extra(value TEXT)")
            connection.execute("PRAGMA writable_schema=ON")
            connection.execute(
                "UPDATE sqlite_master SET name='sqlite_fixture_extra',tbl_name='sqlite_fixture_extra',sql='CREATE TABLE sqlite_fixture_extra(value TEXT)' WHERE name='fixture_extra'"
            )
            connection.execute("PRAGMA writable_schema=OFF")
        connection.commit()
        expected_objects = connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        expected_counts = connection.execute(
            "SELECT (SELECT COUNT(*) FROM metadata_reports),(SELECT COUNT(*) FROM current_metadata_reports)"
        ).fetchone()
        connection.close()
        before = path.read_bytes()
        with pytest.raises(MetadataReportError):
            open_repository(path)
        assert path.read_bytes() == before
        connection = sqlite3.connect(path)
        assert (
            connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
            == expected_objects
        )
        assert (
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM metadata_reports),(SELECT COUNT(*) FROM current_metadata_reports)"
            ).fetchone()
            == expected_counts
        )
        connection.close()


def test_exact_schema_isolates_unselected_history_and_validates_selected_rows_and_pointers() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        primary = synthetic_report()
        other = MetadataReport.create(
            target=TargetFacts(
                "secondary", "ebooks", "item-2", default_target_fields("Other title")
            ),
            file=FileFacts(0, "c" * 64, "d" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
        )
        repository.publish(primary, lambda: True)
        repository.publish(other, lambda: True)
        repository.close()
        connection = sqlite3.connect(path)
        connection.execute("DROP TRIGGER metadata_reports_no_update")
        # `other` is a current Library row, so its browse facts are checked at
        # open (#1199): this edit keeps them, and a fact-changing one refuses
        # open. Historical isolation is in
        # test_unselected_historical_report_tamper_does_not_block_open.
        payload = json.loads(other.to_json())
        del payload["field_results"]
        document = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        connection.execute(
            "UPDATE metadata_reports SET document=?,document_digest=? WHERE report_id=?",
            (document, hashlib.sha256(document.encode()).hexdigest(), other.report_id),
        )
        connection.execute(persistence_schema._NO_UPDATE_SQL)
        connection.commit()
        connection.close()
        repository = open_repository(path)
        assert repository.get(primary.report_id) == primary.to_json()
        with pytest.raises(MetadataReportError):
            repository.get(other.report_id)
        repository.close()


def _tamper_report_document(path: Path, report_id: str, document: str) -> None:
    connection = sqlite3.connect(path)
    connection.execute("DROP TRIGGER metadata_reports_no_update")
    connection.execute(
        "UPDATE metadata_reports SET document=?,document_digest=? WHERE report_id=?",
        (document, hashlib.sha256(document.encode()).hexdigest(), report_id),
    )
    connection.execute(persistence_schema._NO_UPDATE_SQL)
    connection.commit()
    connection.close()


@pytest.mark.parametrize("document", ["{}", "not json"])
def test_unselected_historical_report_tamper_does_not_block_open(document: str) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        historical = synthetic_report()
        successor = synthetic_report("Successor")
        repository.publish(historical, lambda: True)
        repository.publish(successor, lambda: True, historical.report_id)
        repository.close()
        _tamper_report_document(path, historical.report_id, document)
        repository = open_repository(path)
        assert repository.get(successor.report_id) == successor.to_json()
        with pytest.raises(MetadataReportError):
            repository.get(historical.report_id)
        repository.close()


def test_participating_report_fact_tamper_refuses_open() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        current = synthetic_report()
        repository.publish(current, lambda: True)
        repository.close()
        _tamper_report_document(path, current.report_id, "{}")
        with pytest.raises(
            MetadataReportError, match="^database is unavailable or incompatible$"
        ):
            open_repository(path)


def test_selected_corrupt_pointer_blocks_only_its_publication_without_mutation() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        primary = synthetic_report()
        other = MetadataReport.create(
            target=TargetFacts(
                "secondary", "ebooks", "item-2", default_target_fields("Other title")
            ),
            file=FileFacts(0, "c" * 64, "d" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
        )
        repository.publish(primary, lambda: True)
        repository.publish(other, lambda: True)
        repository.close()
        connection = sqlite3.connect(path)
        connection.execute(
            "UPDATE current_metadata_reports SET report_id=? WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?",
            (
                other.report_id,
                primary.target.connection,
                primary.target.library,
                primary.target.item,
                primary.file.identity,
            ),
        )
        connection.commit()
        connection.close()
        before = path.read_bytes()
        repository = open_repository(path)
        assert (
            repository.current(
                other.target.connection,
                other.target.library,
                other.target.item,
                other.file.identity,
            )
            == other.to_json()
        )
        successor = synthetic_report("Successor")
        with pytest.raises(MetadataReportError):
            repository.publish(successor, lambda: True)
        repository.close()
        assert path.read_bytes() == before


def test_pointer_failure_rolls_back_new_report() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        repository._connection.execute(
            "CREATE TRIGGER fail_pointer BEFORE INSERT ON current_metadata_reports BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END"
        )
        with pytest.raises(MetadataReportError):
            repository.publish(synthetic_report(), lambda: True)
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM metadata_reports"
        ).fetchone() == (0,)
        repository.close()


def test_external_precondition_failure_precedes_report_transaction() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        calls: list[bool] = []
        with pytest.raises(MetadataReportError):
            repository.publish(
                synthetic_report(),
                lambda: calls.append(repository._connection.in_transaction) or False,  # type: ignore[func-returns-value]  # reason: `append(...) or False` deliberately records the transaction state and still yields the currentness verdict.
            )
        assert calls == [False]
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM metadata_reports"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM current_metadata_reports"
        ).fetchone() == (0,)
        repository.close()


def test_wal_incompatible_database_is_rejected_byte_for_byte_before_mutation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "other.sqlite"
        connection = sqlite3.connect(path)
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE legacy(value TEXT)")
        connection.commit()
        connection.close()
        before = {value.name: value.read_bytes() for value in root.iterdir()}
        with pytest.raises(MetadataReportError):
            open_repository(path)
        after = {value.name: value.read_bytes() for value in root.iterdir()}
        assert after == before


def test_incompatible_hot_journal_is_rejected_and_preserved_byte_for_byte() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "other.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("CREATE TABLE legacy(value TEXT)")
        connection.execute("INSERT INTO legacy VALUES('original')")
        connection.commit()
        connection.close()
        journal = leave_hot_journal(path)
        before = {
            path.name: path.read_bytes(),
            journal.name: journal.read_bytes(),
        }
        with pytest.raises(MetadataReportError):
            open_repository(path)
        after = {
            path.name: path.read_bytes(),
            journal.name: journal.read_bytes(),
        }
        assert after == before


def test_path_swap_to_symlink_cannot_bind_or_write_alternate_database() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "reports.sqlite"
        repository = open_repository(path)
        repository.close()
        original = root / "original.sqlite"
        alternate = root / "alternate.sqlite"
        connection = sqlite3.connect(alternate)
        connection.execute("CREATE TABLE alternate(value TEXT)")
        connection.commit()
        connection.close()
        alternate_before = alternate.read_bytes()
        real_connect = sqlite3.connect
        calls = 0

        def swapping_connect(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                path.rename(original)
                path.symlink_to(alternate)
            return real_connect(*args, **kwargs)

        try:
            with (
                patch.object(
                    persistence_schema.sqlite3,
                    "connect",
                    side_effect=swapping_connect,
                ),
                pytest.raises(MetadataReportError),
            ):
                open_repository(path)
        finally:
            if path.is_symlink():
                path.unlink()
            if original.exists():
                original.rename(path)
        assert alternate.read_bytes() == alternate_before


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_double_path_swap_cannot_hide_the_alternate_sqlite_connection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "reports.sqlite"
        repository = open_repository(path)
        repository.close()
        alternate = root / "alternate.sqlite"
        alternate_repository = open_repository(alternate)
        alternate_repository.close()
        original = root / "original.sqlite"
        alternate_before = alternate.read_bytes()
        real_connect = sqlite3.connect
        calls = 0

        def double_swapping_connect(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls != 2:
                return real_connect(*args, **kwargs)
            path.rename(original)
            path.symlink_to(alternate)
            connection = real_connect(*args, **kwargs)
            path.unlink()
            original.rename(path)
            return connection

        try:
            with patch.object(
                persistence_schema.sqlite3,
                "connect",
                side_effect=double_swapping_connect,
            ):
                safely_bound = open_repository(path)
                safely_bound.close()
        finally:
            if path.is_symlink():
                path.unlink()
            if original.exists():
                if path.exists():
                    path.unlink()
                original.rename(path)
        assert alternate.read_bytes() == alternate_before


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_descriptor_binding_refuses_competing_root_and_reopens_after_close() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "reports.sqlite"
        repository = open_repository(path)
        aliases = tuple(
            value.name
            for value in root.iterdir()
            if value.name.startswith(".metadata-report-binding-")
        )
        assert len(aliases) == 1
        report = synthetic_report()
        repository.publish(report, lambda: True)
        competing_open_started = time.monotonic()
        with pytest.raises(MetadataReportError):
            open_repository(path)
        assert time.monotonic() - competing_open_started < 1
        assert repository.get(report.report_id) == report.to_json()
        repository.close()
        assert (
            tuple(
                value.name
                for value in root.iterdir()
                if value.name.startswith(".metadata-report-binding-")
            )
            == ()
        )
        reopened = open_repository(path)
        assert reopened.get(report.report_id) == report.to_json()
        reopened.close()
        assert tuple(value.name for value in root.iterdir()) == ("reports.sqlite",)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_descriptor_reuse_during_journal_churn_keeps_bound_open_available() -> (
    None
):
    """A journal-close may reuse a descriptor number held before SQLite opens."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        repository.close()
        descriptor = os.open(path, os.O_RDONLY)
        churned_descriptor = os.open(path, os.O_RDONLY)
        connection = None
        try:
            # A journal descriptor may close between the connection proof's
            # setup and SQLite opening its exact database descriptor.
            connection = persistence_binding._connect_bound(
                path,
                descriptor,
                lambda: os.close(churned_descriptor),
            )
            assert connection.execute("PRAGMA database_list").fetchone()[1:] == (
                "main",
                str(path.absolute()),
            )
        finally:
            if connection is not None:
                connection.close()
            os.close(descriptor)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_raced_configured_hot_journal_cannot_control_alias_open() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "reports.sqlite"
        repository = open_repository(path)
        repository.close()
        real_connect = sqlite3.connect
        raced: dict[str, bytes] = {}
        observed_uris: list[str] = []

        def racing_connect(database, *args, **kwargs):
            observed_uris.append(str(database))
            if len(observed_uris) == 2:
                journal = leave_hot_journal(path)
                raced["main"] = path.read_bytes()
                raced["journal"] = journal.read_bytes()
            return real_connect(database, *args, **kwargs)

        with (
            patch.object(
                persistence_schema.sqlite3,
                "connect",
                side_effect=racing_connect,
            ),
            pytest.raises(MetadataReportError),
        ):
            open_repository(path)
        journal = Path(f"{path}-journal")
        assert len(observed_uris) == 2
        assert "/proc/self/fd/" in observed_uris[1]
        assert ".metadata-report-binding-" in observed_uris[1]
        assert str(path.absolute()) not in observed_uris[1]
        assert path.read_bytes() == raced["main"]
        assert journal.read_bytes() == raced["journal"]
        assert set(value.name for value in root.iterdir()) == {
            "reports.sqlite",
            "reports.sqlite-journal",
        }


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_publication_requires_configured_sidecars_to_remain_absent() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "reports.sqlite"
        repository = open_repository(path)
        sidecar = Path(f"{path}-journal")
        sidecar.write_bytes(b"configured-path-race")
        with pytest.raises(MetadataReportError):
            repository.publish(synthetic_report(), lambda: True)
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM metadata_reports"
        ).fetchone() == (0,)
        repository.close()
        assert sidecar.read_bytes() == b"configured-path-race"
        assert set(value.name for value in root.iterdir()) == {
            "reports.sqlite",
            "reports.sqlite-journal",
        }


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_parent_replacement_at_final_precommit_check_rolls_back() -> None:
    with tempfile.TemporaryDirectory() as directory:
        outer = Path(directory) / "outer"
        outer.mkdir(mode=0o700)
        live = outer / "live"
        live.mkdir(mode=0o700)
        path = live / "reports.sqlite"
        repository = open_repository(path)
        original_bytes = path.read_bytes()
        displaced = outer / "displaced"
        replacement_bytes = b"replacement-database"
        binding = repository._binding
        assert binding is not None
        require_current = binding.require_current
        calls = 0

        def replace_parent_at_final_check() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                live.rename(displaced)
                live.mkdir(mode=0o700)
                path.write_bytes(replacement_bytes)
            require_current()

        with (
            patch.object(
                binding,
                "require_current",
                side_effect=replace_parent_at_final_check,
            ),
            pytest.raises(MetadataReportError),
        ):
            repository.publish(synthetic_report(), lambda: True)
        assert calls == 2
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM metadata_reports"
        ).fetchone() == (0,)
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM current_metadata_reports"
        ).fetchone() == (0,)
        assert (displaced / "reports.sqlite").read_bytes() == original_bytes
        assert path.read_bytes() == replacement_bytes
        repository.close()
        assert tuple(value.name for value in displaced.iterdir()) == ("reports.sqlite",)
        with pytest.raises(MetadataReportError):
            open_repository(path)
        assert path.read_bytes() == replacement_bytes

        path.unlink()
        live.rmdir()
        displaced.rename(live)
        reopened = open_repository(path)
        with pytest.raises(MetadataReportError):
            reopened.get(synthetic_report().report_id)
        reopened.close()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_database_authority_allows_same_process_derived_connection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        owner = open_repository(path)
        derived = open_repository(
            path,
            create=False,
            authority=owner.authority,
        )
        derived.publish(synthetic_report(), lambda: True)
        assert derived.get(synthetic_report().report_id) == synthetic_report().to_json()
        derived.close()
        owner.close()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_outer_control_keeps_successor_valid_after_predecessor_close() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        control = open_repository(path)
        authority = control.authority
        predecessor = open_repository(path, authority=authority)
        successor = open_repository(path, authority=authority)
        predecessor.close()
        successor.publish(synthetic_report(), lambda: True)
        assert (
            successor.get(synthetic_report().report_id) == synthetic_report().to_json()
        )
        successor.close()
        control.close()
        with pytest.raises(MetadataReportError, match="database authority changed"):
            open_repository(path, authority=authority)


def test_existing_database_runs_full_schema_validation_once_before_writable_use() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        repository.close()
        validate = persistence_schema._validate_schema
        with (
            patch.object(
                persistence_schema, "_validate_schema", wraps=validate
            ) as read,
            patch.object(
                persistence_reports, "_validate_schema", wraps=validate
            ) as write,
        ):
            reopened = open_repository(path, create=False)
        reopened.close()
    assert read.call_count + write.call_count == 1


def test_foreign_key_corruption_stops_before_writable_connection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        repository.close()
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO current_metadata_reports "
            "(connection_name,library_name,item_id,file_identity,report_id) "
            "VALUES ('primary','fiction','item','file','missing-report')"
        )
        connection.commit()
        connection.close()
        with patch.object(persistence_reports, "_connect_bound") as writable:
            with pytest.raises(MetadataReportError):
                open_repository(path, create=False)
    writable.assert_not_called()


def test_integrity_failure_stops_before_writable_connection() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        repository.close()
        validated = sqlite3.connect(
            f"file:{path.absolute()}?mode=ro", uri=True, isolation_level=None
        )
        execute = validated.execute

        def fail_integrity(statement: str):
            if statement == "PRAGMA integrity_check":
                result = Mock()
                result.fetchone.return_value = ("database disk image is malformed",)
                return result
            return execute(statement)

        validation_connection = Mock(wraps=validated)
        validation_connection.execute.side_effect = fail_integrity
        validation_connection.close.side_effect = validated.close
        with (
            patch.object(
                persistence_schema.sqlite3,
                "connect",
                return_value=validation_connection,
            ),
            patch.object(persistence_reports, "_connect_bound") as writable,
        ):
            with pytest.raises(MetadataReportError):
                open_repository(path, create=False)
    writable.assert_not_called()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_derived_connections_borrow_the_live_authoritys_schema_validation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        owner = open_repository(path)
        validate = persistence_schema._validate_schema
        with (
            patch.object(
                persistence_schema, "_validate_schema", wraps=validate
            ) as read,
            patch.object(
                persistence_reports, "_validate_schema", wraps=validate
            ) as write,
        ):
            for _ in range(8):
                derived = open_repository(
                    path,
                    create=False,
                    authority=owner.authority,
                )
                derived.close()
        owner.close()
    assert read.call_count + write.call_count == 0


def test_dependent_open_borrows_a_root_that_is_mid_transaction() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        root = open_repository(path)
        root._connection.execute("BEGIN IMMEDIATE")
        root._connection.execute(
            "UPDATE review_preparation_control SET paused=1,updated_at=7 "
            "WHERE singleton=1"
        )
        if not sys.platform.startswith("linux"):
            # Off Linux the rollback journal sits beside the configured name,
            # so it is exactly the sidecar a dependent open has to read as the
            # root's open transaction. The Linux private alias absorbs it.
            assert Path(f"{path}-journal").is_file()
        validate = persistence_schema._validate_schema
        try:
            with (
                patch.object(
                    persistence_schema, "_validate_schema", wraps=validate
                ) as read,
                patch.object(
                    persistence_reports, "_validate_schema", wraps=validate
                ) as write,
            ):
                for _ in range(8):
                    dependent = open_repository(
                        path, create=False, authority=root.authority
                    )
                    dependent.close()
        finally:
            root._connection.execute("ROLLBACK")
            root.close()
    assert read.call_count + write.call_count == 0


def test_portable_dependent_open_requires_the_authoritys_configured_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A portable authority cannot lend one database's journal namespace to an alias."""
    monkeypatch.setattr(persistence_reports.sys, "platform", "portable-test")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        configured = root / "configured.sqlite"
        alias = root / "alias.sqlite"
        owner = open_repository(configured)
        authority = owner.authority
        assert type(authority) is persistence_binding._PortableDatabaseAuthority
        owner._connection.execute("BEGIN IMMEDIATE")
        owner._connection.execute(
            "UPDATE review_preparation_control SET paused=1,updated_at=7 "
            "WHERE singleton=1"
        )
        try:
            assert Path(f"{configured}-journal").is_file()
            dependent = open_repository(configured, create=False, authority=authority)
            dependent.close()
            os.link(configured, alias)
            before = alias.read_bytes()
            with (
                patch.object(
                    persistence_schema.sqlite3,
                    "connect",
                    side_effect=AssertionError("alias must fail before connecting"),
                ),
                pytest.raises(MetadataReportError, match="database authority changed"),
            ):
                open_repository(alias, create=False, authority=authority)
            assert alias.read_bytes() == before
        finally:
            owner._connection.execute("ROLLBACK")
            owner.close()


def test_dependent_opens_continue_while_a_writer_thread_churns_journals() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        root = open_repository(path)
        authority = root.authority
        assert authority is not None
        finished = threading.Event()
        writer_failures: list[BaseException] = []
        counts: dict[str, int] = {}

        def churn() -> None:
            writer = None
            try:
                writer = open_repository(path, create=False, authority=authority)
                for updated_at in range(40):
                    writer._connection.execute("BEGIN IMMEDIATE")
                    writer._connection.execute(
                        "UPDATE review_preparation_control "
                        "SET paused=?,updated_at=? WHERE singleton=1",
                        (updated_at % 2 == 0, updated_at),
                    )
                    writer._connection.execute("COMMIT")
            except BaseException as error:
                writer_failures.append(error)
            finally:
                finished.set()
                if writer is not None:
                    writer.close()

        writer_thread = threading.Thread(target=churn)
        writer_thread.start()
        deadline = time.monotonic() + 20
        try:
            while True:
                try:
                    reader = open_repository(path, create=False, authority=authority)
                    reader.close()
                    counts["ok"] = counts.get("ok", 0) + 1
                except MetadataReportError as error:
                    counts[str(error)] = counts.get(str(error), 0) + 1
                if finished.is_set() or time.monotonic() > deadline:
                    break
        finally:
            writer_thread.join(20)
            root.close()
    assert [] == writer_failures
    assert ["ok"] == sorted(counts)
    assert counts["ok"] >= 1


def test_root_open_still_refuses_a_leftover_journal_beside_a_current_database() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        open_repository(path).close()
        journal = leave_hot_journal(path)
        before = journal.read_bytes()
        with pytest.raises(
            MetadataReportError, match="database recovery state is unsupported"
        ):
            open_repository(path, create=False)
        assert journal.read_bytes() == before


def test_dependent_open_after_the_root_closes_reports_a_changed_authority() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        root = open_repository(path)
        authority = root.authority
        dependent = open_repository(path, create=False, authority=authority)
        dependent.close()
        root.close()
        with pytest.raises(MetadataReportError, match="database authority changed"):
            open_repository(path, create=False, authority=authority)


def test_dependent_open_still_refuses_write_ahead_sidecars() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        root = open_repository(path)
        try:
            for suffix in ("-wal", "-shm"):
                sidecar = Path(f"{path}{suffix}")
                sidecar.write_bytes(b"")
                with pytest.raises(
                    MetadataReportError,
                    match="database recovery state is unsupported",
                ):
                    open_repository(path, create=False, authority=root.authority)
                sidecar.unlink()
        finally:
            root.close()


def test_dependent_open_refuses_a_replaced_database_inode() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        root = open_repository(path)
        replacement = Path(directory) / "replacement.sqlite"
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        try:
            # Off Linux the authority itself refuses the new inode; on Linux
            # the named-identity check inside the private binding refuses it
            # first, under its own message.
            with pytest.raises(
                MetadataReportError,
                match="database authority changed|database pathname changed",
            ):
                open_repository(path, create=False, authority=root.authority)
        finally:
            root.close()


def test_schema_validation_reads_one_transaction_snapshot() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        open_repository(path).close()
        validate = persistence_schema._validate_schema
        transactions: list[bool] = []

        def record(connection: sqlite3.Connection) -> None:
            transactions.append(connection.in_transaction)
            validate(connection)

        with patch.object(persistence_schema, "_validate_schema", record):
            reopened = open_repository(path, create=False)
        reopened.close()
    assert [True] == transactions


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_database_parent_must_be_owner_only() -> None:
    with tempfile.TemporaryDirectory() as directory:
        parent = Path(directory) / "shared"
        parent.mkdir()
        parent.chmod(0o755)
        path = parent / "reports.sqlite"
        with pytest.raises(MetadataReportError):
            open_repository(path)
        assert not path.exists()
        parent.chmod(0o700)
        repository = open_repository(path)
        repository.close()
        assert tuple(value.name for value in parent.iterdir()) == ("reports.sqlite",)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux descriptor proof"
)
def test_linux_database_ancestor_must_exclude_other_users() -> None:
    with tempfile.TemporaryDirectory() as directory:
        shared = Path(directory) / "shared"
        shared.mkdir(mode=0o700)
        parent = shared / "private"
        parent.mkdir(mode=0o700)
        shared.chmod(0o777)
        path = parent / "reports.sqlite"
        with pytest.raises(MetadataReportError):
            open_repository(path)
        assert not path.exists()
        shared.chmod(0o700)
        repository = open_repository(path)
        repository.close()
        assert tuple(value.name for value in parent.iterdir()) == ("reports.sqlite",)


def test_current_pointer_scope_must_agree_with_decoded_report() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        primary = synthetic_report("Primary title")
        other = MetadataReport.create(
            target=TargetFacts(
                "secondary", "other", "item-2", default_target_fields("Other title")
            ),
            file=FileFacts(0, "c" * 64, "d" * 64),
            local_claims=(),
            local_reasons=("no_opening_claims",),
            providers=(),
        )
        repository.publish(primary, lambda: True)
        repository.publish(other, lambda: True)
        repository._connection.execute(
            "UPDATE current_metadata_reports SET report_id=? "
            "WHERE connection_name=? AND library_name=? AND item_id=? AND file_identity=?",
            (
                other.report_id,
                "primary",
                "ebooks",
                "item-1",
                "a" * 64,
            ),
        )
        with pytest.raises(MetadataReportError):
            repository.current("primary", "ebooks", "item-1", "a" * 64)
        repository.close()


@pytest.mark.parametrize(
    "mutation",
    (
        "INSERT INTO reviewer_access VALUES(1,2,'{}')",
        "INSERT INTO reviewer_access_secrets VALUES('unexpected','secret-key')",
    ),
)
def test_startup_rejects_malformed_reviewer_access_content(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "reports.sqlite"
    repository = open_repository(path)
    repository.close()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(mutation)
    with pytest.raises(MetadataReportError, match="reviewer access state"):
        open_repository(path)


@pytest.mark.parametrize("state", tuple(CoverSourceState))
def test_inventaire_cover_outcome_survives_publication_and_reopen(
    tmp_path: Path, state: CoverSourceState
) -> None:
    from mechanical_librarian.domain.cover_evidence import (
        CoverDesignation,
        CoverProvenance,
        CoverTruncationReason,
    )

    source = CoverSource(
        cover_source_id(CoverSourceKind.INVENTAIRE, "synthetic-work"),
        CoverSourceKind.INVENTAIRE,
        state,
        artifact=CoverArtifact.create(
            b"synthetic original", b"synthetic preview", "image/png", 2, 3
        )
        if state is CoverSourceState.RETRIEVED
        else None,
        timeout_scope="attempted" if state is CoverSourceState.TIMEOUT else None,
        provenance=CoverProvenance(
            "inventaire",
            CoverDesignation.TRUNCATION,
            truncation_reason=CoverTruncationReason.PROVIDER_LIMIT,
        )
        if state is CoverSourceState.TRUNCATED
        else CoverProvenance(
            "inventaire", CoverDesignation.WORK, "inventaire-work-wd-Q1"
        ),
    )
    seed = synthetic_report()
    report = MetadataReport.create(
        target=seed.target,
        file=seed.file,
        local_claims=seed.local_claims,
        local_reasons=seed.local_reasons,
        providers=seed.providers,
        cover_sources=(source,),
    )
    path = tmp_path / "reports.sqlite"
    repository = open_repository(path)
    repository.publish(report, lambda: True)
    repository.close()
    reopened = open_repository(path, create=False)
    try:
        restored = MetadataReport.from_json(reopened.get(report.report_id))
        assert restored == report
        assert restored.cover_sources == (source,)
        assert restored.schema_version == "metadata-report-v49"
    finally:
        reopened.close()


def _current_audio_report(
    cited_candidate: str = "audible-product-B08G9PRS1K",
) -> MetadataReport:
    """One current audiobook report exercising retained audio provenance.

    `cited_candidate` is the identity the runtime-truncation observation names,
    so a caller can build the same report through the same constructor while
    citing an edition no lane retains.
    """
    from dataclasses import replace as _replace

    from mechanical_librarian.application.metadata_report import (
        MetadataLookup,
        qualify_provider_observation,
    )
    from mechanical_librarian.domain.audio_inspection import (
        AudioObservation,
        AudioObservationKind,
    )
    from mechanical_librarian.domain.metadata_report import ProviderQueryKind
    from tests.support.audiobook_reports import (
        asin_lane_observation,
        audio_local_claims,
        audio_target_fields,
    )
    from tests.support.metadata_report_doubles import single_track_audio_facts

    claims = audio_local_claims()
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=(
            qualify_provider_observation(
                asin_lane_observation(claims, duration="72000.000"),
                MetadataLookup(ProviderQueryKind.ASIN, claims),
            ),
        ),
        audio=_replace(
            single_track_audio_facts("b" * 64),
            observations=(
                AudioObservation(
                    AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER,
                    candidate=cited_candidate,
                ),
            ),
        ),
    )


def test_an_observation_may_only_cite_an_edition_the_report_retains() -> None:
    """A citation is a comparison, and a comparison names both of its sides.

    The health projection presents the cited identity as the edition this
    recording was measured against -- something an owner can go and open. An
    identity in none of the report's lanes is not that; it is a name with
    nothing behind it, and a reader cannot tell the two apart. So it is refused
    where the report is built and where it is reopened, and both refusals are
    proved because a document can reach this build by either route.

    Demotion is not deletion. The candidate a preparation-time record names was
    demoted to work scope precisely because the band did not corroborate it,
    and it is still retained under its own identity, so the record it left
    behind still resolves -- which is the whole case the citation exists for.
    """
    from mechanical_librarian.domain.metadata_report import (
        ClaimKind,
        ReportValueError,
    )

    published = _current_audio_report()
    retained = {
        candidate.identity
        for observation in published.providers
        for candidate in observation.candidates
    }
    assert published.audio is not None
    assert published.audio.observations[0].candidate in retained
    # Retained informational runtime does not grant edition applicability;
    # the preparation-time observation still resolves its candidate identity.
    assert any(
        claim.kind is ClaimKind.DURATION
        for observation in published.providers
        for candidate in observation.candidates
        for claim in candidate.claims
    )

    # Built through the same constructor, so its identity digest is consistent
    # with its own contents and the citation rule is the only thing left that
    # can refuse it. The message is matched for the same reason: a report this
    # test mutated after construction would be refused by the identity check
    # whether the citation rule existed or not, and would prove nothing.
    with pytest.raises(ReportValueError, match="unknown candidate"):
        _current_audio_report("audible-product-B0NOSUCH99")

    document = json.loads(published.to_json())
    for observation in document["audio"]["observations"]:
        observation["candidate"] = "audible-product-B0NOSUCH99"
    with pytest.raises(ReportValueError, match="unknown candidate"):
        MetadataReport.from_json(json.dumps(document))


def test_a_v39_producer_document_is_refused_rather_than_read_as_current() -> None:
    """The clean-rebuild rule, proved at the reader rather than at the database.

    ADR-0013 supplies no migration, so the only correct answer to a document a
    previous producer wrote is a refusal. Three separate v40 moves are proved
    here because any one of them read leniently would admit a document this
    build cannot interpret: the discriminator itself, the candidate citation an
    audio observation now carries, and the runtime comparison a provider
    candidate now records.
    """
    from mechanical_librarian.domain.metadata_report import ReportValueError

    published = _current_audio_report()
    current = json.loads(published.to_json())
    assert current["schema_version"] == "metadata-report-v49"
    assert MetadataReport.from_json(published.to_json()) == published

    previous = json.loads(json.dumps(current))
    previous["schema_version"] = "metadata-report-v39"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(json.dumps(previous))

    narrowed = json.loads(json.dumps(current))
    for observation in narrowed["audio"]["observations"]:
        del observation["candidate"]
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(json.dumps(narrowed))

    unmarked = json.loads(json.dumps(current))
    for provider in unmarked["providers"]:
        for candidate in provider["candidates"]:
            del candidate["runtime_short_of_local"]
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(json.dumps(unmarked))


def test_actual_v40_producer_is_refused_without_omission_adoption() -> None:
    from mechanical_librarian.domain.metadata_report import ReportValueError

    old = (
        Path(__file__).parents[1]
        / "support/audio_prior_producer/metadata-report-v40.json"
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(old.read_text())


@pytest.mark.parametrize("omitted", (False, True))
def test_current_omission_evidence_roundtrips_sqlite(
    tmp_path: Path, omitted: bool
) -> None:
    from tests.support.audiobook_reports import chapter_runtime_report

    report = chapter_runtime_report(chapter_rows_omitted=omitted)
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        repository.publish(report, lambda: True)
        restored = MetadataReport.from_json(repository.get(report.report_id))
        assert restored.audio is not None
        assert restored.audio.chapter_rows_omitted is omitted
        assert restored == report
    finally:
        repository.close()
    assert (
        report.report_id
        != chapter_runtime_report(chapter_rows_omitted=not omitted).report_id
    )


@pytest.mark.parametrize("value", (None, 0, 1, "false", "missing"))
def test_current_report_omission_requires_exact_bool_with_valid_digest(value) -> None:
    from mechanical_librarian.domain.metadata_report import (
        ReportValueError,
        canonical_json,
    )
    from tests.support.audiobook_reports import chapter_runtime_report

    document = json.loads(chapter_runtime_report().to_json())
    if value == "missing":
        del document["audio"]["chapter_rows_omitted"]
    else:
        document["audio"]["chapter_rows_omitted"] = value
    identity = dict(document)
    del identity["report_id"]
    document["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(identity).encode()).hexdigest()
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(canonical_json(document))


def test_actual_v41_producer_is_refused_without_lookup_adoption() -> None:
    import hashlib

    from mechanical_librarian.domain.metadata_report import ReportValueError

    old = (
        Path(__file__).parents[1]
        / "support/audio_prior_producer/metadata-report-v41.json"
    )
    assert (
        hashlib.sha256(old.read_bytes()).hexdigest()
        == "f1f5cb13d5e02d6bcbb70eaf97647903de88a1c8f4750b5bda5a219e7d14a8d7"
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(old.read_text())


def test_actual_v44_producer_artifacts_refuse_without_mutation(tmp_path: Path) -> None:
    from mechanical_librarian.domain.metadata_report import ReportValueError

    fixtures = Path(__file__).parents[1] / "fixtures" / "metadata-report-v44"
    report = (fixtures / "report-v44.json").read_bytes()
    sql = (fixtures / "reports-v44.sql").read_bytes()
    assert (
        hashlib.sha256(report).hexdigest()
        == "21aacba76100f6dad54855f639683a755ab9ea206bb241f66d8c463770c01f9e"
    )
    assert (
        hashlib.sha256(sql).hexdigest()
        == "f030510d00d1d3bee5ac2ac67eb68b6bd5f194eed7b518baa97eac0e8d7a7a43"
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(report.decode())
    database = tmp_path / "prior.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(sql.decode())
    connection.commit()
    connection.close()
    before = database.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(database)
    assert database.read_bytes() == before
    assert (fixtures / "report-v44.json").read_bytes() == report
    assert (fixtures / "reports-v44.sql").read_bytes() == sql


def test_actual_v45_producer_artifacts_refuse_without_mutation(tmp_path: Path) -> None:
    from mechanical_librarian.domain.metadata_report import ReportValueError

    fixtures = Path(__file__).parents[1] / "fixtures" / "metadata-report-v45"
    report = (fixtures / "report-v45.json").read_bytes()
    sql = (fixtures / "reports-v45.sql").read_bytes()
    assert (
        hashlib.sha256(report).hexdigest()
        == "faabf04dafeac10366a3d29362c671d61a90fd587c61f22cdbee1d05ad2b27c7"
    )
    assert (
        hashlib.sha256(sql).hexdigest()
        == "83667f163b2e7a6ff5cc22c1de12b67a292c936717a01a22e72c0541ce33d132"
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(report.decode())
    database = tmp_path / "prior.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(sql.decode())
    connection.commit()
    connection.close()
    before = database.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(database)
    assert database.read_bytes() == before
    assert (fixtures / "report-v45.json").read_bytes() == report
    assert (fixtures / "reports-v45.sql").read_bytes() == sql


def test_actual_v46_producer_artifacts_refuse_without_mutation(tmp_path: Path) -> None:
    from mechanical_librarian.domain.metadata_report import ReportValueError

    fixtures = Path(__file__).parents[1] / "fixtures" / "metadata-report-v46"
    report = (fixtures / "report-v46.json").read_bytes()
    sql = (fixtures / "reports-v46.sql").read_bytes()
    assert (
        hashlib.sha256(report).hexdigest()
        == "f55be7704eab4b26c865fd8575b819ccd4a86e21cf98d3f865106817777ad0ff"
    )
    assert (
        hashlib.sha256(sql).hexdigest()
        == "1d3eff3cc98790e0634ac8f28e0a92ecec3adf5848a97e63ce122432878f41e8"
    )
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(report.decode())
    database = tmp_path / "prior.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(sql.decode())
    connection.commit()
    connection.close()
    before = database.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(database)
    assert database.read_bytes() == before
    assert (fixtures / "report-v46.json").read_bytes() == report
    assert (fixtures / "reports-v46.sql").read_bytes() == sql


def test_current_schema_cannot_relabel_falsely_valid_predecessor_title() -> None:
    from mechanical_librarian.domain.metadata_report import (
        ReportValueError,
        canonical_json,
    )

    document = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/metadata-report-v46/report-v46.json"
        ).read_text()
    )
    document["schema_version"] = "metadata-report-v49"
    document.pop("report_id")
    document["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(document).encode()).hexdigest()
    )
    with pytest.raises(ReportValueError, match="title is invalid"):
        MetadataReport.from_json(canonical_json(document))


@pytest.mark.parametrize("fixture_name", ["store-v8.sql", "empty-v8.sql"])
def test_genuine_qualification_predecessor_refused_without_mutation(
    tmp_path, fixture_name
):
    fixture = Path(__file__).parents[1] / "fixtures/qualification-authority-predecessor"
    path = tmp_path / "predecessor.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript((fixture / fixture_name).read_text())
        count = connection.execute(
            "SELECT count(*) FROM shadow_decision_outcomes"
        ).fetchone()[0]
    assert (count > 0) is (fixture_name == "store-v8.sql")
    before = path.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    assert path.read_bytes() == before


@pytest.mark.parametrize("populated", (False, True))
def test_cover_authority_v2_schema_rejects_empty_and_populated_store_before_mutation(
    tmp_path, monkeypatch, populated
):
    from mechanical_librarian.adapters.persistence import schema
    from tests.unit.test_policy_impact_persistence import _case_report

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/cover-authority-v2.json").read_text()
    )
    assert fixture["source_sha"] == "9867cdafd5f42f7282980e20b607189826498c11"
    path = tmp_path / "prior-cover.sqlite"
    with monkeypatch.context() as prior:
        prior.setattr(
            schema,
            "_COVER_SELECTION_AUTHORITIES_SQL",
            fixture["cover_selection_authorities_sql"],
        )
        repository = open_repository(path)
        if populated:
            repository.publish(_case_report(1), lambda: True)
        repository.close()
    before = path.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ("empty.sql", "populated.sql"))
def test_owner_cover_predecessor_refuses_without_mutation(tmp_path, name):
    import hashlib
    import sqlite3

    fixture = Path(__file__).parents[1] / "fixtures/owner-cover-predecessor"
    provenance = json.loads((fixture / "provenance.json").read_text())
    assert provenance["source_sha"] == "0b9dc854e730b329e68410f09a53f2f9103f617f"
    raw = (fixture / name).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == provenance["files"][name]
    database = tmp_path / "previous.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(raw.decode())
    database.chmod(0o600)
    before = database.read_bytes()
    with pytest.raises(MetadataReportError, match="incompatible"):
        open_repository(database)
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "fixture_name,source_sha,populated",
    (
        ("title-conflict-v18.json", "54584c4ba542593d72ddb9016b3542af42e8b4b7", False),
        (
            "title-preservation-v19.json",
            "148ad97f1d40524014f5aa179ca1590466adabfb",
            False,
        ),
        (
            "title-preservation-v19.json",
            "148ad97f1d40524014f5aa179ca1590466adabfb",
            True,
        ),
    ),
)
def test_actual_v18_title_conflict_state_is_rejected_without_change(
    tmp_path, monkeypatch, fixture_name, source_sha, populated
):
    from contextlib import closing

    from mechanical_librarian.domain.shadow_decision import ShadowDecisionSet
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures" / fixture_name).read_text()
    )
    assert fixture["source_sha"] == source_sha
    for parser, key in (
        (ShadowDecisionSet.from_json, "decision"),
        (ShadowOutcomeSet.from_json, "outcome"),
    ):
        with pytest.raises(ValueError):
            parser(fixture[key])
    path = tmp_path / "prior-title.sqlite"
    predecessor = json.loads(
        (
            Path(__file__).parents[1]
            / "support/proposal_predecessors/fixed-conservative-v32.json"
        ).read_text()
    )
    with monkeypatch.context() as old:
        old.setattr(
            persistence_schema,
            "_COVER_CONSIDERATIONS_SQL",
            json.loads(
                (
                    Path(__file__).parents[1]
                    / "fixtures/cover-consideration-embedded-report.json"
                ).read_text()
            )["cover_considerations_sql"],
        )
        old.setattr(
            persistence_schema, "_PROPOSAL_SETS_SQL", predecessor["proposal_sets_ddl"]
        )
        old.setattr(
            persistence_schema,
            "_SHADOW_DECISION_OUTCOMES_SQL",
            fixture["shadow_decision_outcomes_sql"],
        )
        with closing(open_repository(path)) as repository:
            # The producer predates #1199's report browse facts, #1202's
            # history indexes and #1204's indexes.
            repository._connection.executescript(
                "DROP TRIGGER metadata_reports_project_browse_facts;"
                "DROP TABLE metadata_report_browse_authors;"
                "DROP TABLE metadata_report_browse_facts;"
                "DROP TRIGGER proposal_sets_project_browse_facts;"
                "DROP TABLE proposal_set_browse_facts;"
                "DROP TABLE browse_fact_identity;"
                "DROP INDEX human_decisions_case_destination;"
                "DROP INDEX metadata_policy_resolutions_resolved;"
                "DROP INDEX field_effects_decision;"
                "DROP INDEX field_effects_companion_decision;"
                "DROP INDEX field_effects_ready;"
                "DROP INDEX field_effects_waiting;"
                "DROP INDEX field_effects_target;"
                "DROP INDEX description_decisions_case;"
            )
            rows = repository._connection.execute(
                "SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            ).fetchall()
            assert (
                hashlib.sha256(
                    json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
                ).hexdigest()
                == fixture["schema_inventory_sha256"]
            )
            if populated:
                for table in ("metadata_reports", "proposal_sets", "review_cases"):
                    for row in fixture["parents"][table]:
                        repository._connection.execute(
                            f"INSERT INTO {table} VALUES({','.join('?' for _ in row)})",
                            row,
                        )
                outcome = json.loads(fixture["outcome"])
                repository._connection.execute(
                    "INSERT INTO shadow_decision_outcomes VALUES(?,?,?,?,?,?,?,?)",
                    tuple(
                        outcome[key]
                        for key in (
                            "outcome_set_id",
                            "review_case_id",
                            "report_id",
                            "proposal_set_id",
                            "resolved_policy_id",
                            "source_decision_set_id",
                        )
                    )
                    + (fixture["evaluated_at"], fixture["outcome"]),
                )
                assert repository._connection.execute(
                    "SELECT document FROM shadow_decision_outcomes"
                ).fetchall() == [(fixture["outcome"],)]
                assert (
                    repository._connection.execute(
                        "PRAGMA foreign_key_check"
                    ).fetchall()
                    == []
                )
    before = path.read_bytes()
    inventory = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == inventory


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("none", None),
        ("length", "persisted report is invalid"),
        ("digest_and_envelope", "persisted report is invalid"),
        ("document_and_envelope", "persisted report is invalid"),
        ("envelope", "persisted report envelope disagrees"),
    ],
)
def test_validated_report_row_preserves_complete_validation_and_error_precedence(
    mutation: str, message: str | None
) -> None:
    report = synthetic_report()
    document = report.to_json()
    row = [
        report.report_id,
        report.target.connection,
        report.target.library,
        report.target.item,
        report.file.identity,
        report.file.content_digest,
        persistence_schema.REPORT_SCHEMA,
        hashlib.sha256(document.encode()).hexdigest(),
        document,
    ]
    if mutation == "length":
        row.pop()
    elif mutation != "none":
        row[1] = "wrong-connection"
        if mutation == "digest_and_envelope":
            row[7] = "0" * 64
        elif mutation == "document_and_envelope":
            row[8] = "{}"
            row[7] = hashlib.sha256(b"{}").hexdigest()
    if message is None:
        assert persistence_schema._validated_report_row(tuple(row)) == report
        assert persistence_schema._validate_report_row(tuple(row)) == document
    else:
        for validate in (
            persistence_schema._validated_report_row,
            persistence_schema._validate_report_row,
        ):
            with pytest.raises(MetadataReportError, match="^" + message + "$"):
                validate(tuple(row))
