from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.review_sql import (
    _LATEST_PREPARATION_WORK_SQL,
    _SHADOW_OUTCOME_HISTORY_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _HUMAN_DECISION_NO_UPDATE_SQL,
    _NO_UPDATE_SQL,
    _PROPOSAL_NO_UPDATE_SQL,
    _REVIEW_NO_UPDATE_SQL,
    _REVIEW_OWNER_DECISION_ACTIONS_V1_SOURCE_FACT_VALUES_SQL,
    _REVIEW_OWNER_DECISION_ACTIONS_V1_SOURCE_FACTS_INSERT_SQL,
    _REVIEW_OWNER_DECISION_ACTIONS_V1_SOURCE_FACTS_SQL,
    _REVIEW_WORK_AGED_CLAIM_INDEX_SQL,
)
from mechanical_librarian.adapters.persistence.schema_adoption import (
    _application_table_digest,
    adopt_schema,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.application.schema_adoption import (
    OWNER_SETTINGS_V1,
    REVIEW_OWNER_DECISION_ACTIONS_V1,
    SchemaAdoptionRecoveryRequired,
)
from mechanical_librarian.domain.human_decision import HumanDecision, decision_actions
from mechanical_librarian.domain.owner_settings import OwnerSettingsDocument
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.review_cases import _report

_INDEXES = (
    "review_preparation_work_lineage_history",
    "shadow_decision_outcomes_case_history",
)


def _drop_field_facts_projection(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TRIGGER shadow_outcomes_project_field_facts")
    connection.execute("DROP INDEX shadow_outcome_field_facts_case")
    connection.execute("DROP TABLE shadow_outcome_field_facts")
    connection.execute("DROP TRIGGER review_cases_project_field_facts")
    connection.execute("DROP TABLE review_case_field_facts")


def _freeze_review_field_facts_predecessor(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE owner_setting_secrets")
    connection.execute("DROP TABLE owner_settings")
    connection.execute("DROP TRIGGER review_cases_project_field_facts")
    connection.execute("DROP TABLE review_case_field_facts")
    connection.execute(_REVIEW_OWNER_DECISION_ACTIONS_V1_SOURCE_FACTS_SQL)
    values = _REVIEW_OWNER_DECISION_ACTIONS_V1_SOURCE_FACT_VALUES_SQL
    connection.execute(
        "INSERT INTO review_case_field_facts(review_case_id,destination,"
        "unresolved_choice,manual_bibliographic_attention) SELECT rc.review_case_id,"
        + values
        + " FROM review_cases rc,json_each(rc.document,'$.results') result"
    )
    connection.execute(_REVIEW_OWNER_DECISION_ACTIONS_V1_SOURCE_FACTS_INSERT_SQL)


def _predecessor(root: Path) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    database = root / "application.sqlite3"
    repository = open_repository(database)
    repository.close()
    connection = sqlite3.connect(database)
    _freeze_review_field_facts_predecessor(connection)
    connection.commit()
    connection.close()
    return database


def _owner_settings_predecessor(root: Path) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    database = root / "application.sqlite3"
    repository = open_repository(database)
    repository.close()
    connection = sqlite3.connect(database)
    connection.execute("DROP TABLE owner_setting_secrets")
    connection.execute("DROP TABLE owner_settings")
    connection.commit()
    connection.close()
    return database


def _table_digest(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return _application_table_digest(connection)
    finally:
        connection.close()


def _predecessor_with_case(root: Path) -> tuple[Path, str, str, str]:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    database = root / "application.sqlite3"
    repository = open_repository(database)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    repository.close()
    connection = sqlite3.connect(database)
    _freeze_review_field_facts_predecessor(connection)
    connection.commit()
    connection.close()
    return database, report.report_id, proposal.proposal_set_id, str(case_id)


def _predecessor_with_decision(root: Path) -> Path:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    database = root / "application.sqlite3"
    repository = open_repository(database)
    report = _report()
    repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    repository.publish_proposal(proposal_set)
    case_id = json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(str(case_id)))["case"]
    )
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"]
        in {"propose_change", "acceptable_choice", "unresolved_choice"}
        or value["manual_required"] is True
    )
    decision = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination=str(proposal["destination"]),
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="schema-adoption-test-0001",
        occurred_at=1_000,
    )
    repository.record_human_decision(decision)
    repository.close()
    connection = sqlite3.connect(database)
    _freeze_review_field_facts_predecessor(connection)
    connection.commit()
    connection.close()
    return database


def _predecessor_with_retained_preservation_decision(
    root: Path,
) -> tuple[Path, str, str]:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    database = root / "application.sqlite3"
    repository = open_repository(database)
    report, _category, _option, _destination = _automatic_no_change_inputs(
        "exact_title"
    )
    repository.publish(report, lambda: True)
    proposal_set = evaluate_report(report)
    repository.publish_proposal(proposal_set)
    case_id = json.loads(repository.admit_review_case(proposal_set))["review_case_id"]
    repository.close()
    connection = sqlite3.connect(database)
    try:
        document = connection.execute(
            "SELECT document FROM review_cases WHERE review_case_id=?", (case_id,)
        ).fetchone()[0]
        case = ReviewCase.from_json(document)
        proposal = next(
            value for value in case.proposals if value["destination"] == "title"
        )
        selected = next(
            value["value"]
            for value in proposal["result"]["alternatives"]
            if value["assessment"] == "preserves_target"
        )
        legacy_proposal = json.loads(json.dumps(proposal))
        next(
            value
            for value in legacy_proposal["result"]["alternatives"]
            if value["value"] == selected
        )["assessment"] = "replaces_target"
        legacy_proposal["result"]["outcome"] = "unresolved_choice"
        legacy_case = type(
            "LegacyCase",
            (),
            {
                "review_case_id": case.review_case_id,
                "target_fingerprint": case.target_fingerprint,
                "proposals": (legacy_proposal,),
            },
        )()
        decision = HumanDecision.create(
            legacy_case,
            proposal_id=legacy_proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=legacy_proposal["destination"],
            actor="owner",
            outcome="accept",
            selected_value=selected,
            idempotency_key="schema-adoption-preserved-choice-0001",
            occurred_at=1_000,
        )
        connection.execute(
            "INSERT INTO human_decisions(decision_id,review_case_id,proposal_id,"
            "target_fingerprint,destination,actor,outcome,selected_value_document,"
            "idempotency_digest,request_digest,occurred_at,document) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision.decision_id,
                decision.review_case_id,
                decision.proposal_id,
                decision.target_fingerprint,
                decision.destination,
                decision.actor,
                decision.outcome,
                json.dumps(
                    selected, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                ),
                decision.idempotency_digest,
                decision.request_digest,
                decision.occurred_at,
                decision.to_json(),
            ),
        )
        _freeze_review_field_facts_predecessor(connection)
        connection.commit()
        return database, case_id, decision.decision_id
    finally:
        connection.close()


def test_exact_predecessor_adopts_field_facts_without_changing_canonical_tables(
    tmp_path: Path,
) -> None:
    database = _predecessor(tmp_path)
    backup = tmp_path / "application.before-owner-decision-actions.sqlite3"
    before = _table_digest(database)

    with pytest.raises(MetadataReportError):
        open_repository(database, create=False)

    assert {
        "plan": REVIEW_OWNER_DECISION_ACTIONS_V1,
        "status": "adopted",
    } == adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert before == _table_digest(database)
    assert backup.is_file()
    assert os.stat(backup).st_mode & 0o777 == 0o600

    connection = sqlite3.connect(database)
    assert (
        connection.execute("SELECT count(*) FROM review_case_field_facts").fetchone()
        == connection.execute(
            "SELECT count(*) FROM review_cases rc,json_each(rc.document,'$.results')"
        ).fetchone()
    )
    assert (
        connection.execute("SELECT count(*) FROM shadow_outcome_field_facts").fetchone()
        == connection.execute(
            "SELECT count(*) FROM shadow_decision_outcomes shadow,"
            "json_each(shadow.document,'$.outcomes')"
        ).fetchone()
    )
    lineage_plan = connection.execute(
        "EXPLAIN QUERY PLAN " + _LATEST_PREPARATION_WORK_SQL,
        ("primary", "books", "item-1"),
    ).fetchall()
    shadow_plan = connection.execute(
        "EXPLAIN QUERY PLAN " + _SHADOW_OUTCOME_HISTORY_SQL,
        ("review_case_1", 100),
    ).fetchall()
    aged_claim = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND name='review_preparation_work_aged_claim'"
    ).fetchone()
    connection.close()
    assert any(_INDEXES[0] in str(row) for row in lineage_plan)
    assert any(_INDEXES[1] in str(row) for row in shadow_plan)
    assert aged_claim == (_REVIEW_WORK_AGED_CLAIM_INDEX_SQL,)

    repository = open_repository(database, create=False)
    repository.close()


def test_owner_settings_exact_predecessor_adopts_without_changing_canonical_tables(
    tmp_path: Path,
) -> None:
    database = _owner_settings_predecessor(tmp_path)
    backup = tmp_path / "application.before-owner-settings.sqlite3"
    before = _table_digest(database)

    with pytest.raises(MetadataReportError):
        open_repository(database, create=False)
    assert {"plan": OWNER_SETTINGS_V1, "status": "adopted"} == adopt_schema(
        database, backup, OWNER_SETTINGS_V1
    )
    assert before == _table_digest(database)
    assert backup.is_file()
    repository = open_repository(database, create=False)
    repository.close()


def test_owner_settings_adoption_refuses_wrong_predecessor_and_tampering(
    tmp_path: Path,
) -> None:
    legacy = _predecessor(tmp_path / "legacy")
    legacy_before = legacy.read_bytes()
    with pytest.raises(MetadataReportError, match="incompatible"):
        adopt_schema(
            legacy,
            legacy.parent / "owner-settings-backup.sqlite3",
            OWNER_SETTINGS_V1,
        )
    assert legacy.read_bytes() == legacy_before

    tampered = _owner_settings_predecessor(tmp_path / "tampered")
    connection = sqlite3.connect(tampered)
    connection.execute("CREATE INDEX unexpected_owner_index ON review_cases(report_id)")
    connection.commit()
    connection.close()
    tampered_before = tampered.read_bytes()
    with pytest.raises(MetadataReportError, match="incompatible"):
        adopt_schema(
            tampered,
            tampered.parent / "backup.sqlite3",
            OWNER_SETTINGS_V1,
        )
    assert tampered.read_bytes() == tampered_before


@pytest.mark.parametrize(
    "tamper",
    (
        "UPDATE owner_settings SET document=' '||document",
        "UPDATE owner_settings SET revision=revision+1",
        "INSERT INTO owner_setting_secrets VALUES('provider.unknown.key','secret-1234')",
    ),
)
def test_exact_schema_open_and_adoption_refuse_corrupt_owner_settings(
    tmp_path: Path, tamper: str
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    repository.close()
    document = OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v1",
            "revision": 1,
            "connections": [{"name": "primary", "endpoint": "https://example.invalid"}],
            "libraries": [
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "library",
                    "media_kind": "ebook",
                }
            ],
        }
    )
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO owner_settings VALUES(1,1,?)",
        (_canonical_json(document.model_dump()),),
    )
    connection.execute(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        ("connection.primary.key", "synthetic-secret"),
    )
    connection.execute(tamper)
    connection.commit()
    connection.close()

    with pytest.raises(MetadataReportError, match="incompatible"):
        open_repository(database, create=False)
    with pytest.raises(MetadataReportError, match="incompatible"):
        adopt_schema(
            database,
            tmp_path / "noncanonical.before.sqlite3",
            OWNER_SETTINGS_V1,
        )


def test_exact_current_is_a_verified_noop_without_creating_a_backup(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    repository.close()
    before = database.read_bytes()
    backup = tmp_path / "unused.sqlite3"

    assert {
        "plan": REVIEW_OWNER_DECISION_ACTIONS_V1,
        "status": "already-current",
    } == adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert database.read_bytes() == before
    assert not backup.exists()


def test_adoption_and_current_case_read_retain_a_historical_preservation_accept(
    tmp_path: Path,
) -> None:
    database, case_id, decision_id = _predecessor_with_retained_preservation_decision(
        tmp_path
    )
    assert (
        adopt_schema(
            database, tmp_path / "backup.sqlite3", REVIEW_OWNER_DECISION_ACTIONS_V1
        )["status"]
        == "adopted"
    )

    repository = open_repository(database, create=False)
    try:
        decisions = repository.get_review_case_decisions(case_id)
        decision = HumanDecision.from_json(decisions[0])
        assert decision.decision_id == decision_id
        fields = InspectCurrentReviewCase(repository).run(case_id)["fields"]
        title = next(value for value in fields if value["destination"] == "title")
        assert title["human_decision"]["decision_id"] == decision_id
        source = repository.field_effect_authority_source(decision)
        assert source["destination"] == "title"
        assert source["decision_value"] == decision.selected_value
    finally:
        repository.close()


def test_adoption_backfill_matches_the_domain_action_contract(tmp_path: Path) -> None:
    database, _report_id, _proposal_id, case_id = _predecessor_with_case(tmp_path)
    connection = sqlite3.connect(database)
    document = connection.execute(
        "SELECT document FROM review_cases WHERE review_case_id=?", (case_id,)
    ).fetchone()[0]
    case = ReviewCase.from_json(document)
    expected = {
        str(proposal["destination"]): int(bool(decision_actions(proposal)))
        for proposal in case.proposals
    }
    connection.close()
    assert (
        adopt_schema(
            database, tmp_path / "backup.sqlite3", REVIEW_OWNER_DECISION_ACTIONS_V1
        )["status"]
        == "adopted"
    )
    connection = sqlite3.connect(database)
    actual = dict(
        connection.execute(
            "SELECT destination,has_owner_decision_actions FROM review_case_field_facts "
            "WHERE review_case_id=?",
            (case_id,),
        )
    )
    connection.close()
    assert actual == expected


def test_unrecognized_schema_and_existing_backup_fail_without_mutation(
    tmp_path: Path,
) -> None:
    database = _predecessor(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE INDEX unexpected_review_index ON review_cases(report_id)"
    )
    connection.commit()
    connection.close()
    before = database.read_bytes()
    backup = tmp_path / "backup.sqlite3"

    with pytest.raises(MetadataReportError):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert database.read_bytes() == before
    assert not backup.exists()

    database = _predecessor(tmp_path / "second")
    backup = database.parent / "backup.sqlite3"
    backup.write_bytes(b"reserved")
    before = database.read_bytes()
    with pytest.raises(MetadataReportError, match="backup already exists"):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert database.read_bytes() == before
    assert backup.read_bytes() == b"reserved"


def test_plan_target_is_frozen_against_future_current_schema(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import mechanical_librarian.adapters.persistence.schema_adoption as adoption

    database = _predecessor(tmp_path)
    current = adoption._expected_schema_objects
    monkeypatch.setattr(
        adoption,
        "_expected_schema_objects",
        lambda: [*current(), ("index", "future_schema_object", "future sql")],
    )
    monkeypatch.setattr(
        adoption.Path,
        "absolute",
        lambda _path: pytest.fail("future schema check accessed a pathname"),
    )
    with pytest.raises(MetadataReportError, match="unavailable for this build"):
        adopt_schema(
            database, tmp_path / "backup.sqlite3", REVIEW_OWNER_DECISION_ACTIONS_V1
        )


@pytest.mark.parametrize("suffix", ("-journal", "-wal", "-shm"))
def test_recovery_sidecars_are_rejected_without_backup(
    tmp_path: Path, suffix: str
) -> None:
    database = _predecessor(tmp_path)
    sidecar = Path(f"{database}{suffix}")
    sidecar.write_bytes(b"retained")
    backup = tmp_path / "backup.sqlite3"

    with pytest.raises(MetadataReportError):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert sidecar.read_bytes() == b"retained"
    assert not backup.exists()


def test_symlink_source_and_unknown_plan_are_rejected(tmp_path: Path) -> None:
    database = _predecessor(tmp_path)
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(database)

    with pytest.raises((MetadataReportError, OSError)):
        adopt_schema(
            alias, tmp_path / "backup.sqlite3", REVIEW_OWNER_DECISION_ACTIONS_V1
        )
    with pytest.raises(MetadataReportError, match="plan is unsupported"):
        adopt_schema(database, tmp_path / "backup.sqlite3", "arbitrary-sql")


def test_backup_write_failure_removes_partial_file_without_mutating_source(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    database = _predecessor(tmp_path)
    before = database.read_bytes()
    backup = tmp_path / "backup.sqlite3"

    def refuse_write(_descriptor: int, _value: object) -> int:
        raise OSError("synthetic full disk")

    monkeypatch.setattr(
        "mechanical_librarian.adapters.persistence.schema_adoption.os.write",
        refuse_write,
    )
    with pytest.raises(MetadataReportError, match="backup is unavailable"):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert database.read_bytes() == before
    assert not backup.exists()


def test_installed_module_command_adopts_a_generated_predecessor(
    tmp_path: Path,
) -> None:
    database = _predecessor(tmp_path)
    backup = tmp_path / "backup.sqlite3"
    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "mechanical_librarian",
            "schema-adopt",
            REVIEW_OWNER_DECISION_ACTIONS_V1,
            "--database",
            str(database),
            "--backup",
            str(backup),
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == (
        '{"plan":"review-owner-decision-actions-v1","status":"adopted"}\n'
    )
    assert result.stderr == ""
    repository = open_repository(database, create=False)
    repository.close()


def test_closed_wal_database_is_rejected_before_backup_or_mutation(
    tmp_path: Path,
) -> None:
    database = _predecessor(tmp_path)
    connection = sqlite3.connect(database)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    connection.close()
    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)
    before = database.read_bytes()
    backup = tmp_path / "backup.sqlite3"

    with pytest.raises(MetadataReportError):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert database.read_bytes() == before
    assert not backup.exists()


@pytest.mark.parametrize(
    ("table", "identity_column", "identity_index", "trigger", "trigger_sql"),
    (
        (
            "metadata_reports",
            "report_id",
            1,
            "metadata_reports_no_update",
            _NO_UPDATE_SQL,
        ),
        (
            "proposal_sets",
            "proposal_set_id",
            2,
            "proposal_sets_no_update",
            _PROPOSAL_NO_UPDATE_SQL,
        ),
        (
            "review_cases",
            "review_case_id",
            3,
            "review_cases_no_update",
            _REVIEW_NO_UPDATE_SQL,
        ),
    ),
)
def test_malformed_canonical_source_document_is_rejected_before_backup(
    tmp_path: Path,
    table: str,
    identity_column: str,
    identity_index: int,
    trigger: str,
    trigger_sql: str,
) -> None:
    database, *identities = _predecessor_with_case(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute(f"DROP TRIGGER {trigger}")
    connection.execute(
        f"UPDATE {table} SET document='{{}}' WHERE {identity_column}=?",
        (identities[identity_index - 1],),
    )
    connection.execute(trigger_sql)
    connection.commit()
    connection.close()
    backup = tmp_path / "backup.sqlite3"

    with pytest.raises(MetadataReportError):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert not backup.exists()


def test_nonstandard_json_columns_are_validated_before_backup(tmp_path: Path) -> None:
    database = _predecessor(tmp_path / "safety")
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE automatic_safety_state SET category_suspensions_document='{}'"
    )
    connection.commit()
    connection.close()
    backup = database.parent / "backup.sqlite3"
    with pytest.raises(MetadataReportError):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert not backup.exists()

    database = _predecessor_with_decision(tmp_path / "decision")
    connection = sqlite3.connect(database)
    connection.execute("DROP TRIGGER human_decisions_no_update")
    connection.execute(
        "UPDATE human_decisions SET selected_value_document='{\"unexpected\":true}'"
    )
    connection.execute(_HUMAN_DECISION_NO_UPDATE_SQL)
    connection.commit()
    connection.close()
    backup = database.parent / "backup.sqlite3"
    with pytest.raises(MetadataReportError):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert not backup.exists()


def test_ambiguous_commit_and_every_postcommit_failure_require_recovery(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import mechanical_librarian.adapters.persistence.schema_adoption as adoption

    database = _predecessor(tmp_path)
    backup = tmp_path / "backup.sqlite3"
    real_connect = adoption._connect_bound

    class AmbiguousCommit:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __getattr__(self, name: str) -> Any:
            return getattr(self.connection, name)

        def commit(self) -> None:
            self.connection.commit()
            raise sqlite3.OperationalError("synthetic ambiguous commit")

    monkeypatch.setattr(
        adoption,
        "_connect_bound",
        lambda *args, **kwargs: AmbiguousCommit(real_connect(*args, **kwargs)),
    )
    with pytest.raises(SchemaAdoptionRecoveryRequired):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    connection = sqlite3.connect(database)
    indexes = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    connection.close()
    assert set(_INDEXES) <= indexes


def test_interrupt_after_durable_commit_requires_recovery(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import mechanical_librarian.adapters.persistence.schema_adoption as adoption

    database = _predecessor(tmp_path)
    backup = tmp_path / "backup.sqlite3"
    real_connect = adoption._connect_bound

    class InterruptedCommit:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __getattr__(self, name: str) -> Any:
            return getattr(self.connection, name)

        def commit(self) -> None:
            self.connection.commit()
            raise KeyboardInterrupt

    monkeypatch.setattr(
        adoption,
        "_connect_bound",
        lambda *args, **kwargs: InterruptedCommit(real_connect(*args, **kwargs)),
    )
    with pytest.raises(SchemaAdoptionRecoveryRequired):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    connection = sqlite3.connect(database)
    indexes = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
    }
    connection.close()
    assert set(_INDEXES) <= indexes


@pytest.mark.parametrize(
    "failure_seam",
    ("schema", "backup", "backup-content", "cleanup"),
)
def test_postcommit_validation_and_cleanup_failures_require_recovery(
    tmp_path: Path, monkeypatch: MonkeyPatch, failure_seam: str
) -> None:
    import mechanical_librarian.adapters.persistence.schema_adoption as adoption

    database = _predecessor(tmp_path)
    backup = tmp_path / "backup.sqlite3"
    if failure_seam == "schema":
        real_validate = adoption._validate_schema
        calls = 0

        def fail_second(connection: sqlite3.Connection) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise MetadataReportError("synthetic postcommit validation")
            real_validate(connection)

        monkeypatch.setattr(adoption, "_validate_schema", fail_second)
    elif failure_seam == "backup":
        monkeypatch.setattr(
            adoption,
            "_require_backup_current",
            lambda *_args: (_ for _ in ()).throw(
                MetadataReportError("synthetic backup replacement")
            ),
        )
    elif failure_seam == "backup-content":
        real_validate = adoption._validate_schema
        calls = 0

        def corrupt_backup_after_commit(connection: sqlite3.Connection) -> None:
            nonlocal calls
            calls += 1
            real_validate(connection)
            if calls == 2:
                backup.write_bytes(b"same inode, wrong recovery content")

        monkeypatch.setattr(adoption, "_validate_schema", corrupt_backup_after_commit)
    else:
        real_close = adoption._close_adoption_resources

        def fail_after_close(*args: Any) -> None:
            real_close(*args)
            raise MetadataReportError("synthetic cleanup failure")

        monkeypatch.setattr(adoption, "_close_adoption_resources", fail_after_close)

    with pytest.raises(SchemaAdoptionRecoveryRequired):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    if failure_seam == "backup-content":
        assert backup.read_bytes() == b"same inode, wrong recovery content"


def test_postvalidation_path_replacement_cannot_report_success(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import mechanical_librarian.adapters.persistence.schema_adoption as adoption

    database = _predecessor(tmp_path)
    alternate_root = tmp_path / "alternate"
    alternate = _predecessor(alternate_root)
    displaced = tmp_path / "displaced.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    real_digest = adoption._application_table_digest
    calls = 0

    def replace_after_second_digest(connection: sqlite3.Connection) -> str:
        nonlocal calls
        result = real_digest(connection)
        calls += 1
        if calls == 2:
            database.rename(displaced)
            alternate.rename(database)
        return result

    monkeypatch.setattr(
        adoption, "_application_table_digest", replace_after_second_digest
    )
    with pytest.raises(SchemaAdoptionRecoveryRequired):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)


def test_backup_directory_entry_is_durable_before_index_commit(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import mechanical_librarian.adapters.persistence.schema_adoption as adoption

    database = _predecessor(tmp_path)
    backup = tmp_path / "backup.sqlite3"
    real_fsync = adoption.os.fsync
    directory_synced = False

    def record_fsync(descriptor: int) -> None:
        nonlocal directory_synced
        directory_synced = directory_synced or stat.S_ISDIR(
            os.fstat(descriptor).st_mode
        )
        real_fsync(descriptor)

    monkeypatch.setattr(adoption.os, "fsync", record_fsync)
    assert (
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)["status"]
        == "adopted"
    )
    assert directory_synced


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux authority lock")
def test_live_database_parent_authority_is_rejected_without_waiting(
    tmp_path: Path,
) -> None:
    import fcntl

    database = _predecessor(tmp_path)
    backup = tmp_path / "backup.sqlite3"
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(MetadataReportError):
            adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    finally:
        fcntl.flock(parent, fcntl.LOCK_UN)
        os.close(parent)
    assert not backup.exists()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux binding aliases"
)
def test_stale_private_binding_for_source_inode_is_rejected_before_backup(
    tmp_path: Path,
) -> None:
    database = _predecessor(tmp_path)
    alias = tmp_path / (
        ".metadata-report-binding-00000000000000000000000000000000.sqlite"
    )
    os.link(database, alias)
    Path(f"{alias}-journal").write_bytes(b"retained hot journal")
    backup = tmp_path / "backup.sqlite3"

    with pytest.raises(MetadataReportError):
        adopt_schema(database, backup, REVIEW_OWNER_DECISION_ACTIONS_V1)
    assert not backup.exists()
