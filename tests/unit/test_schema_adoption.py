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
    _REVIEW_WORK_AGED_CLAIM_INDEX_SQL,
    _validate_review_field_facts_predecessor_schema,
)
from mechanical_librarian.adapters.persistence.schema_adoption import (
    _application_table_digest,
    adopt_schema,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.schema_adoption import (
    REVIEW_FIELD_FACTS_V1 as REVIEW_READ_INDEXES_V1,
)
from mechanical_librarian.application.schema_adoption import (
    SchemaAdoptionRecoveryRequired,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
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
    _drop_field_facts_projection(connection)
    connection.execute("DROP INDEX review_preparation_work_due_promotion")
    connection.execute("DROP INDEX review_preparation_work_aged_claim")
    connection.execute(
        "CREATE INDEX review_preparation_work_aged_claim "
        "ON review_preparation_work(state,library_name,updated_at,ordinal)"
    )
    connection.execute("PRAGMA writable_schema=ON")
    connection.execute(
        "UPDATE sqlite_master SET sql=replace(sql,\"'queued','delayed','running'\",\"'queued','running'\") "
        "WHERE type='table' AND name='review_preparation_work'"
    )
    connection.execute("PRAGMA writable_schema=OFF")
    # Every malformed-document test starts from the one frozen schema the
    # offline plan accepts, so its later rejection proves canonical validation.
    _validate_review_field_facts_predecessor_schema(connection)


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


def test_exact_predecessor_adopts_field_facts_without_changing_canonical_tables(
    tmp_path: Path,
) -> None:
    database = _predecessor(tmp_path)
    backup = tmp_path / "application.before-review-indexes.sqlite3"
    before = _table_digest(database)

    with pytest.raises(MetadataReportError):
        open_repository(database, create=False)

    assert {
        "plan": REVIEW_READ_INDEXES_V1,
        "status": "adopted",
    } == adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        "plan": REVIEW_READ_INDEXES_V1,
        "status": "already-current",
    } == adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
    assert database.read_bytes() == before
    assert not backup.exists()


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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
    assert database.read_bytes() == before
    assert not backup.exists()

    database = _predecessor(tmp_path / "second")
    backup = database.parent / "backup.sqlite3"
    backup.write_bytes(b"reserved")
    before = database.read_bytes()
    with pytest.raises(MetadataReportError, match="backup already exists"):
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
    assert database.read_bytes() == before
    assert backup.read_bytes() == b"reserved"


def test_named_predecessor_contract_is_frozen_against_future_current_schema(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import mechanical_librarian.adapters.persistence.schema as schema

    database = _predecessor(tmp_path)
    current = schema._expected_schema_objects
    monkeypatch.setattr(
        schema,
        "_expected_schema_objects",
        lambda: [*current(), ("index", "future_schema_object", "future sql")],
    )
    connection = sqlite3.connect(database)
    try:
        schema._validate_review_field_facts_predecessor_schema(connection)
    finally:
        connection.close()


@pytest.mark.parametrize("suffix", ("-journal", "-wal", "-shm"))
def test_recovery_sidecars_are_rejected_without_backup(
    tmp_path: Path, suffix: str
) -> None:
    database = _predecessor(tmp_path)
    sidecar = Path(f"{database}{suffix}")
    sidecar.write_bytes(b"retained")
    backup = tmp_path / "backup.sqlite3"

    with pytest.raises(MetadataReportError):
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
    assert sidecar.read_bytes() == b"retained"
    assert not backup.exists()


def test_symlink_source_and_unknown_plan_are_rejected(tmp_path: Path) -> None:
    database = _predecessor(tmp_path)
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(database)

    with pytest.raises((MetadataReportError, OSError)):
        adopt_schema(alias, tmp_path / "backup.sqlite3", REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
    assert database.read_bytes() == before
    assert not backup.exists()


def test_projection_index_failure_rolls_back_tables_and_retains_verified_backup(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    database = _predecessor(tmp_path)
    before = database.read_bytes()
    backup = tmp_path / "backup.sqlite3"
    monkeypatch.setattr(
        "mechanical_librarian.adapters.persistence.schema_adoption._SHADOW_OUTCOME_FIELD_FACTS_CASE_INDEX_SQL",
        "CREATE INDEX invalid plan text",
    )

    with pytest.raises(MetadataReportError, match="failed safely"):
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
    assert backup.read_bytes() == before
    connection = sqlite3.connect(database)
    objects = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%field_facts%'"
        )
    }
    connection.close()
    assert not objects


def test_claim_index_replacement_failure_rolls_back_and_retains_verified_backup(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    import mechanical_librarian.adapters.persistence.schema_adoption as adoption

    database = _predecessor(tmp_path)
    before = database.read_bytes()
    backup = tmp_path / "backup.sqlite3"
    monkeypatch.setattr(
        adoption,
        "_REVIEW_WORK_AGED_CLAIM_INDEX_SQL",
        "CREATE INDEX invalid plan text",
    )

    with pytest.raises(MetadataReportError, match="failed safely"):
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
    assert backup.read_bytes() == before
    connection = sqlite3.connect(database)
    aged_claim = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' "
        "AND name='review_preparation_work_aged_claim'"
    ).fetchone()
    connection.close()
    assert aged_claim == (
        "CREATE INDEX review_preparation_work_aged_claim "
        "ON review_preparation_work(state,library_name,updated_at,ordinal)",
    )


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
            REVIEW_READ_INDEXES_V1,
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
    assert result.stdout == ('{"plan":"review-field-facts-v1","status":"adopted"}\n')
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)


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
    assert adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)["status"] == "adopted"
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
            adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
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
        adopt_schema(database, backup, REVIEW_READ_INDEXES_V1)
    assert not backup.exists()
