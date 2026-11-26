"""Startup's persisted-state table and its one refusal family (ADR-0108).

Startup reads `ledger_meta` first. An absent file is created; an exact one
opens; another build's derived partition, or an earlier ledger format this
build's migrations reach, asks for `regenerate-derived`; any other ledger
format, or no ledger record, asks for a clean rebuild; and an extra or
altered object in either partition refuses as it did before. Startup never
migrates.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
    open_shadow_preview_repository,
)
from mechanical_librarian.application.metadata_report import (
    DerivedRegenerationRequired,
    LedgerMigrationRequired,
    LedgerRebuildRequired,
    MetadataReportError,
    PersistedStateRefusal,
)
from tests.support.ledger_partition import _derived_setup, opens, rewrite
from tests.unit.test_ledger_migrations import chain, identity

_TODAY = "database is unavailable or incompatible"
_REGENERATION = (
    "This database was prepared by another build. Stop the container, run "
    "docker compose run --rm mechanical-librarian regenerate-derived, then "
    "start it again. Upgrades has the steps."
)
_REBUILD = (
    "This database is from a build this one cannot read, so it needs a clean "
    "rebuild. Upgrades has the steps."
)
_REGENERATION_ROW = (
    "INSERT INTO derived_regenerations(regeneration_id,derived_epoch_before,"
    "derived_epoch_after,derived_version_before,derived_version_after,"
    "derived_schema_digest_before,derived_schema_digest_after,"
    "ledger_format_version_before,ledger_format_version_after,build_version,"
    "started_at,finished_at,backup_sha256,decision_ordinal_hwm,retire_pending,"
    "counts_document) VALUES(1,0,1,1,1,?,?,1,1,'0.0.0',10,20,?,0,0,'{}')"
)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """An exact store of this build, closed."""
    path = tmp_path / "application.sqlite3"
    open_repository(path).close()
    return path


def _state(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def _refusal(path: Path) -> MetadataReportError:
    before = _state(path)
    with pytest.raises(MetadataReportError) as raised:
        open_repository(path, create=False)
    # Startup refuses before any write.
    assert _state(path) == before
    return raised.value


def test_an_absent_file_is_created_with_the_ledger_record(tmp_path: Path) -> None:
    path = tmp_path / "application.sqlite3"
    repository = open_repository(path)
    try:
        connection = repository._connection
        assert connection.execute(
            "SELECT singleton,ledger_format_version,derived_epoch,derived_version,"
            "derived_schema_digest FROM ledger_meta"
        ).fetchall() == [schema.setup_ledger_meta_row()]
        assert schema.setup_ledger_meta_row()[1:4] == (1, 0, 1)
        assert connection.execute(
            "SELECT count(*) FROM derived_regenerations"
        ).fetchone() == (0,)
    finally:
        repository.close()


def test_an_exact_file_opens(store: Path) -> None:
    assert opens(store)


@pytest.mark.parametrize(
    "assignment",
    ["derived_version=2", "derived_schema_digest='" + "0" * 64 + "'"],
    ids=["derived_version", "derived_digest"],
)
def test_another_builds_derived_partition_asks_for_a_regeneration(
    store: Path, assignment: str
) -> None:
    assert rewrite(store, f"UPDATE ledger_meta SET {assignment}") == 1
    refusal = _refusal(store)
    assert type(refusal) is DerivedRegenerationRequired
    assert str(refusal) == _REGENERATION


@pytest.mark.parametrize(
    "change",
    [
        "DROP TABLE ledger_meta",
        "DELETE FROM ledger_meta",
        "UPDATE ledger_meta SET ledger_format_version=2",
        # Another format comes first even when the derived partition is
        # another build's too.
        "UPDATE ledger_meta SET ledger_format_version=2,derived_version=2",
    ],
    ids=["no_table", "no_row", "format_2", "format_2_and_derived"],
)
def test_no_ledger_record_or_another_format_needs_a_clean_rebuild(
    store: Path, change: str
) -> None:
    rewrite(store, change)
    refusal = _refusal(store)
    assert type(refusal) is LedgerRebuildRequired
    assert str(refusal) == _REBUILD


@pytest.mark.parametrize(
    "change",
    [
        "CREATE TABLE planted(value TEXT)",
        "CREATE INDEX planted ON review_cases(report_id)",
        # Another build's trigger on a ledger table is derived (ADR-0108).
        "CREATE TRIGGER planted AFTER INSERT ON owner_links BEGIN SELECT 1; END",
        "ALTER TABLE owner_links ADD COLUMN planted TEXT",
        # An altered ledger object refuses before the derived record is read.
        "ALTER TABLE owner_links ADD COLUMN planted TEXT; "
        "UPDATE ledger_meta SET derived_version=2",
    ],
    ids=[
        "derived_table",
        "derived_index",
        "trigger_on_ledger_table",
        "ledger_table",
        "ledger_table_and_derived",
    ],
)
def test_an_extra_or_altered_object_refuses_as_today(store: Path, change: str) -> None:
    connection = sqlite3.connect(store)
    try:
        connection.executescript(change)
    finally:
        connection.close()
    refusal = _refusal(store)
    assert not isinstance(refusal, PersistedStateRefusal)
    assert str(refusal) == _TODAY


@pytest.mark.parametrize(
    ("change", "family"),
    [
        ("UPDATE ledger_meta SET derived_version=2", DerivedRegenerationRequired),
        ("UPDATE ledger_meta SET ledger_format_version=2", LedgerRebuildRequired),
    ],
    ids=["derived", "ledger"],
)
def test_the_shadow_preview_open_raises_the_same_family(
    store: Path, change: str, family: type[PersistedStateRefusal]
) -> None:
    rewrite(store, change)
    before = _state(store)
    with pytest.raises(family):
        open_shadow_preview_repository(store)
    assert _state(store) == before


@pytest.mark.parametrize(
    "change",
    [
        "",
        # An earlier format is decided on its version alone, before any
        # object or the derived record is compared.
        "ALTER TABLE owner_links ADD COLUMN planted TEXT",
        "UPDATE ledger_meta SET derived_version=2",
        "CREATE TABLE planted(value TEXT)",
    ],
    ids=["exact", "altered_ledger_object", "another_derived", "planted_derived"],
)
def test_an_earlier_ledger_the_chain_reaches_asks_for_a_regeneration(
    store: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Startup never migrates: it names the command, in the words another
    build's derived partition gets, and changes nothing."""
    connection = sqlite3.connect(store)
    try:
        connection.executescript(change)
    finally:
        connection.close()
    chain(monkeypatch, identity(1), current=2)
    refusal = _refusal(store)
    assert type(refusal) is LedgerMigrationRequired
    assert isinstance(refusal, DerivedRegenerationRequired)
    assert str(refusal) == _REGENERATION
    before = _state(store)
    with pytest.raises(LedgerMigrationRequired):
        open_shadow_preview_repository(store)
    assert _state(store) == before


@pytest.mark.parametrize(
    ("sources", "current", "stored"),
    [
        ((2,), 3, 1),
        ((1,), 3, 1),
        ((1,), 2, 3),
        ((), 2, 1),
    ],
    ids=["first_step_missing", "last_step_missing", "newer", "no_chain"],
)
def test_a_ledger_the_chain_does_not_reach_needs_a_clean_rebuild(
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    sources: tuple[int, ...],
    current: int,
    stored: int,
) -> None:
    assert rewrite(store, f"UPDATE ledger_meta SET ledger_format_version={stored}")
    chain(monkeypatch, *(identity(source) for source in sources), current=current)
    refusal = _refusal(store)
    assert type(refusal) is LedgerRebuildRequired
    assert str(refusal) == _REBUILD


def test_the_build_partition_is_the_fresh_schema_split_by_ownership() -> None:
    build = schema.build_partition()
    ledger = {name for _, name, _, _ in build.ledger_objects}
    derived = {name for _, name, _, _ in build.derived_objects}
    assert not ledger & derived
    assert {table for _, _, table, _ in build.ledger_objects} == (
        schema.LEDGER_TABLES | schema.LEDGER_VIEWS
    )
    # Only the attention triggers on ledger tables, which write derived
    # state, are derived objects of a ledger table.
    attention = {
        name for kind, name, _ in schema._ATTENTION_FACT_OBJECTS if kind == "trigger"
    }
    on_ledger = [
        (kind, name)
        for kind, name, table, _ in build.derived_objects
        if table in schema.LEDGER_TABLES
    ]
    assert on_ledger
    assert all(kind == "trigger" and name in attention for kind, name in on_ledger)
    assert not attention & ledger
    assert {"ledger_meta", "derived_regenerations"} <= ledger
    assert {
        table: list(rows) for table, rows in build.derived_seed_rows
    } == _derived_setup()
    assert build.derived_schema_digest == schema.setup_ledger_meta_row()[4]


def test_the_regeneration_audit_and_its_dismissal_are_append_only(
    store: Path,
) -> None:
    digest = "a" * 64
    connection = sqlite3.connect(store)
    try:
        connection.execute(_REGENERATION_ROW, (digest, digest, digest))
        connection.execute(
            "INSERT INTO regeneration_notice_dismissals(regeneration_id,dismissed_at) "
            "VALUES(1,20)"
        )
        connection.commit()
        for statement, message in (
            ("UPDATE derived_regenerations SET finished_at=30", "derived regen"),
            ("DELETE FROM derived_regenerations", "derived regen"),
            ("UPDATE regeneration_notice_dismissals SET dismissed_at=30", "dismiss"),
            ("DELETE FROM regeneration_notice_dismissals", "dismiss"),
        ):
            with pytest.raises(sqlite3.IntegrityError, match=message):
                connection.execute(statement)
        # One dismissal per regeneration.
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            connection.execute(
                "INSERT INTO regeneration_notice_dismissals(regeneration_id,"
                "dismissed_at) VALUES(1,40)"
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("dismissed_at", "admitted"),
    [(20, True), (19, False)],
    ids=["after_finish", "before_finish"],
)
def test_a_dismissal_is_of_a_finished_regeneration(
    store: Path, dismissed_at: int, admitted: bool
) -> None:
    digest = "a" * 64

    def change(connection: sqlite3.Connection) -> None:
        connection.execute(_REGENERATION_ROW, (digest, digest, digest))
        connection.execute(
            "INSERT INTO regeneration_notice_dismissals(regeneration_id,dismissed_at) "
            "VALUES(1,?)",
            (dismissed_at,),
        )

    connection = sqlite3.connect(store)
    try:
        change(connection)
        connection.commit()
    finally:
        connection.close()
    assert opens(store) is admitted


@pytest.mark.parametrize(
    "column",
    [
        "derived_epoch_after=5",
        "derived_schema_digest_after='x'",
        "retire_pending=2",
        "counts_document='[]'",
        "finished_at=1",
    ],
    ids=["epoch_step", "digest", "flag", "counts", "finished_before_started"],
)
def test_the_regeneration_audit_refuses_a_malformed_row(
    store: Path, column: str
) -> None:
    digest = "a" * 64
    connection = sqlite3.connect(store)
    try:
        connection.execute(_REGENERATION_ROW, (digest, digest, digest))
        connection.execute("DROP TRIGGER derived_regenerations_no_update")
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            connection.execute(f"UPDATE derived_regenerations SET {column}")
    finally:
        connection.rollback()
        connection.close()


def test_setup_admits_only_the_fresh_ledger_record(store: Path) -> None:
    """A store left before setup's first commit holds exactly the ledger
    record a fresh schema seeds; any other record is not that store."""
    repository = open_repository(store, create=False)
    try:
        assert repository.setup_state_store().is_exact_schema_only()
    finally:
        repository.close()
    rewrite(store, "UPDATE ledger_meta SET derived_epoch=1")
    repository = open_repository(store, create=False)
    try:
        assert not repository.setup_state_store().is_exact_schema_only()
    finally:
        repository.close()
