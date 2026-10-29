from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import ExitStack, closing
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.activity_facts import ActivityFactStore
from mechanical_librarian.adapters.persistence.cover_consideration_documents import (
    compact_cover_consideration,
    expand_cover_consideration,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.field_effect import FieldEffectError
from tests.support.cover_retention import retained_cover_case
from tests.support.metadata_report_schemas import (
    PRIOR_HUMAN_DECISIONS_SQL,
    PRIOR_REVIEW_CASE_FIELD_FACTS_INSERT_SQL,
    PRIOR_REVIEW_WORK_SQL,
)


def test_real_producer_roundtrip_replay_history_and_restart(tmp_path: Path) -> None:
    with retained_cover_case(tmp_path) as (repository, service, case, report):
        expanded = repository.current_cover_consideration_document(case.review_case_id)
        assert expanded is not None
        row = repository._connection.execute(
            "SELECT document,document_digest FROM cover_considerations"
        ).fetchone()
        stored = json.loads(row[0])
        assert "report_document" not in stored
        assert (
            stored["report_document_digest"]
            == hashlib.sha256(report.to_json().encode()).hexdigest()
        )
        assert row[1] == hashlib.sha256(expanded.encode()).hexdigest()
        assert (
            expand_cover_consideration(
                row[0],
                report.to_json(),
                stored["report_document_digest"],
                row[1],
                report.report_id,
            )
            == expanded
        )
        assert len(row[0].encode()) < len(expanded.encode())
        service.ensure(
            case.review_case_id,
            report.to_json(),
            next(value for value in case.proposals if value["destination"] == "cover"),
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM cover_considerations"
        ).fetchone() == (1,)
        assert (
            repository.latest_cover_consideration_document(case.review_case_id)
            == expanded
        )
        assert (
            repository.current_cover_consideration(
                case.review_case_id, json.loads(expanded)["replay_digest"]
            )
            == expanded
        )
        history = repository.cover_consideration_history(case.review_case_id)
        assert history[0]["consideration_document"] == expanded
        for source in report.cover_sources:
            assert source.artifact is not None
            assert repository.artifact_is_referenced(source.artifact.original_digest)
            assert repository.artifact_is_referenced(source.artifact.preview_digest)
        with pytest.raises(sqlite3.IntegrityError):
            repository._connection.execute("DELETE FROM metadata_reports")
        repository._connection.rollback()
    reopened = open_repository(tmp_path / "application.sqlite3", create=False)
    try:
        assert (
            reopened.current_cover_consideration_document(case.review_case_id)
            == expanded
        )
        assert reopened.cover_consideration_history(case.review_case_id) == history
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_report",
        "wrong_report",
        "report_digest",
        "document_digest",
        "embedded",
        "missing_digest",
        "noncanonical",
        "oversize",
    ),
)
def test_compact_expansion_refuses_invalid_binding(
    tmp_path: Path, corruption: str
) -> None:
    with retained_cover_case(tmp_path) as (repository, _service, _case, report):
        document, digest = repository._connection.execute(
            "SELECT document,document_digest FROM cover_considerations"
        ).fetchone()
        report_document = report.to_json()
        report_digest = hashlib.sha256(report_document.encode()).hexdigest()
        value = json.loads(document)
        if corruption == "missing_report":
            report_document = cast(str, None)
        elif corruption == "wrong_report":
            report_document = report_document.replace('"item-1"', '"other-item"')
        elif corruption == "report_digest":
            report_digest = "0" * 64
        elif corruption == "document_digest":
            digest = "0" * 64
        elif corruption == "embedded":
            value["report_document"] = report_document
            document = _canonical_json(value)
        elif corruption == "missing_digest":
            del value["report_document_digest"]
            document = _canonical_json(value)
        elif corruption == "noncanonical":
            document += " "
        else:
            value["padding"] = "x" * 12_582_912
            document = _canonical_json(value)
        with pytest.raises(
            MetadataReportError, match="cover consideration is unavailable"
        ):
            expand_cover_consideration(
                document, report_document, report_digest, digest, report.report_id
            )


@pytest.mark.parametrize("populated", (False, True))
def test_actual_embedded_predecessor_refused_without_mutation(
    tmp_path: Path, populated: bool
) -> None:
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/cover-consideration-embedded-report.json"
        ).read_text()
    )
    assert fixture["source_sha"] == "3b11eefebd70ecf7d26015cc9e3f6ff78e95521c"
    database = tmp_path / "predecessor.sqlite"
    connection = sqlite3.connect(database)
    support = Path(__file__).parents[1]
    # The producer also predates #1245's report discriminator, #1252's proposal
    # and decision policy literals, which its own DDL carried, and #1253's
    # field-fact clause.
    with (
        patch.object(
            schema, "_COVER_CONSIDERATIONS_SQL", fixture["cover_considerations_sql"]
        ),
        patch.object(
            schema,
            "_PROPOSAL_SETS_SQL",
            json.loads(
                (
                    support
                    / "support/proposal_predecessors/fixed-conservative-v33.json"
                ).read_text()
            )["proposal_sets_ddl"],
        ),
        patch.object(
            schema,
            "_SHADOW_DECISION_OUTCOMES_SQL",
            json.loads((support / "fixtures/series-strip-guard-v20.json").read_text())[
                "shadow_decision_outcomes_sql"
            ],
        ),
        patch.object(
            schema,
            "_REPORTS_SQL",
            schema._REPORTS_SQL.replace(
                "= 'metadata-report-v62'", "= 'metadata-report-v49'"
            ),
        ),
        patch.object(
            schema,
            "_REVIEW_CASE_FIELD_FACTS_INSERT_SQL",
            PRIOR_REVIEW_CASE_FIELD_FACTS_INSERT_SQL,
        ),
        patch.object(schema, "_HUMAN_DECISIONS_SQL", PRIOR_HUMAN_DECISIONS_SQL),
        patch.object(schema, "_REVIEW_WORK_SQL", PRIOR_REVIEW_WORK_SQL),
    ):
        schema._create_schema(connection)
    # The producer predates #1199's report browse facts, #1202's
    # history indexes, #1204's indexes, #1211's case indexes and #1232's
    # attention facts.
    connection.executescript(
        "".join(
            f"DROP {kind.upper()} {name};"
            for kind, name, _ in reversed(schema._ATTENTION_FACT_OBJECTS)
        )
        + "DROP TRIGGER metadata_reports_project_browse_facts;"
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
        "DROP INDEX current_review_case_lineages_case;"
        "DROP INDEX current_review_packages_case;"
    )
    rows = connection.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    assert (
        hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        == fixture["schema_inventory_sha256"]
    )
    if populated:
        # These are complete rows from the actual base producer, not a current
        # fixture relabelled as a predecessor. All foreign keys remain valid.
        for table, rows in fixture["populated_rows"].items():
            connection.executemany(
                'INSERT OR REPLACE INTO "'
                + table
                + '" VALUES('
                + ",".join("?" for _ in rows[0])
                + ")",
                rows,
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.commit()
    connection.close()
    before = database.read_bytes()
    with pytest.raises(MetadataReportError, match="unavailable or incompatible"):
        open_repository(database, create=False)
    assert database.read_bytes() == before


def test_publication_report_mismatch_rolls_back(tmp_path: Path) -> None:
    with retained_cover_case(tmp_path) as (repository, _service, case, report):
        original = repository.current_cover_consideration_document(case.review_case_id)
        assert original is not None
        value = json.loads(original)
        value["consideration_id"] = "cover_consideration_" + "9" * 64
        value["supersedes_id"] = json.loads(original)["consideration_id"]
        value["report_document"] = report.to_json() + " "
        with pytest.raises(MetadataReportError, match="invalid"):
            repository.record_cover_consideration(
                _canonical_json(value),
                review_case_id=case.review_case_id,
                report_id=report.report_id,
                replay_digest=value["replay_digest"],
                supersedes_id=value["supersedes_id"],
                occurred_at=11,
            )
        assert not repository._connection.in_transaction
        assert (
            repository.current_cover_consideration_document(case.review_case_id)
            == original
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM cover_considerations"
        ).fetchone() == (1,)
        with pytest.raises(MetadataReportError, match="invalid"):
            compact_cover_consideration(original, report.to_json() + " ")


@pytest.mark.parametrize(
    "corruption", ("missing_report", "report_bytes", "report_digest")
)
@pytest.mark.parametrize("cached", (False, True))
def test_selected_read_and_activity_refuse_corrupt_canonical_report(
    tmp_path: Path, corruption: str, cached: bool
) -> None:
    with (
        retained_cover_case(tmp_path) as (repository, _service, case, report),
        ExitStack() as stack,
    ):
        reader = repository
        if cached:
            reader = stack.enter_context(
                closing(
                    open_repository(
                        tmp_path / "application.sqlite3",
                        create=False,
                        authority=repository.authority,
                    )
                )
            )
            reader.configure_activity_page_reader(ActivityFactStore())
        assert reader.field_effect_activity_page(
            search=None, event_type="all", occurred_after=0, page=1, page_size=25
        )["events"]
        connection = repository._connection
        connection.execute("PRAGMA foreign_keys=OFF")
        if corruption == "missing_report":
            connection.execute("DROP TRIGGER metadata_reports_no_delete")
            connection.execute(
                "DELETE FROM metadata_reports WHERE report_id=?", (report.report_id,)
            )
            connection.execute(schema._NO_DELETE_SQL)
        else:
            connection.execute("DROP TRIGGER metadata_reports_no_update")
            if corruption == "report_bytes":
                connection.execute(
                    "UPDATE metadata_reports SET document=document || ' ' WHERE report_id=?",
                    (report.report_id,),
                )
            else:
                connection.execute(
                    "UPDATE metadata_reports SET document_digest=? WHERE report_id=?",
                    ("0" * 64, report.report_id),
                )
            connection.execute(schema._NO_UPDATE_SQL)
        connection.commit()
        with pytest.raises(MetadataReportError, match="unavailable"):
            repository.current_cover_consideration_document(case.review_case_id)
        with pytest.raises(MetadataReportError, match="unavailable"):
            repository.cover_consideration_history(case.review_case_id)
        with pytest.raises(FieldEffectError, match="persistence"):
            reader.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )


@pytest.mark.parametrize(
    "mutation", ("embedded", "missing_digest", "wrong_type", "invalid_digest")
)
def test_sqlite_refuses_noncompact_document_shape(
    tmp_path: Path, mutation: str
) -> None:
    with retained_cover_case(tmp_path) as (repository, _service, _case, report):
        connection = repository._connection
        value = json.loads(
            connection.execute("SELECT document FROM cover_considerations").fetchone()[
                0
            ]
        )
        if mutation == "embedded":
            value["report_document"] = report.to_json()
        elif mutation == "missing_digest":
            del value["report_document_digest"]
        elif mutation == "wrong_type":
            value["report_document_digest"] = None
        else:
            value["report_document_digest"] = "z" * 64
        connection.execute("DROP TRIGGER cover_considerations_no_update")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE cover_considerations SET document=?", (_canonical_json(value),)
            )
        connection.rollback()


def test_expanded_limit_applies_even_when_compact_document_fits(tmp_path: Path) -> None:
    with retained_cover_case(tmp_path) as (repository, _service, _case, report):
        value = json.loads(
            repository._connection.execute(
                "SELECT document FROM cover_considerations"
            ).fetchone()[0]
        )
        value["padding"] = ""
        value["padding"] = "x" * (12_582_912 - len(_canonical_json(value).encode()) - 1)
        stored = _canonical_json(value)
        assert len(stored.encode()) < 12_582_912
        report_digest = value.pop("report_document_digest")
        value["report_document"] = report.to_json()
        expanded = _canonical_json(value)
        assert len(expanded.encode()) > 12_582_912
        with pytest.raises(MetadataReportError, match="unavailable"):
            expand_cover_consideration(
                stored,
                report.to_json(),
                report_digest,
                hashlib.sha256(expanded.encode()).hexdigest(),
                report.report_id,
            )


def test_corrupt_report_sql_type_rolls_back_publication(tmp_path: Path) -> None:
    with retained_cover_case(tmp_path) as (repository, _service, case, report):
        original = repository.current_cover_consideration_document(case.review_case_id)
        assert original is not None
        value = json.loads(original)
        value["supersedes_id"] = value["consideration_id"]
        value["consideration_id"] = "cover_consideration_" + "9" * 64
        connection = repository._connection
        connection.execute("DROP TRIGGER metadata_reports_no_update")
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE metadata_reports SET document=CAST(document AS BLOB)"
        )
        connection.execute("PRAGMA ignore_check_constraints=OFF")
        connection.execute(schema._NO_UPDATE_SQL)
        connection.commit()
        with pytest.raises(MetadataReportError, match="cover consideration is invalid"):
            repository.record_cover_consideration(
                _canonical_json(value),
                review_case_id=case.review_case_id,
                report_id=report.report_id,
                replay_digest=value["replay_digest"],
                supersedes_id=value["supersedes_id"],
                occurred_at=11,
            )
        assert not connection.in_transaction
        assert connection.execute(
            "SELECT count(*) FROM cover_considerations"
        ).fetchone() == (1,)
