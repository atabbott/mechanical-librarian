"""`regenerate-derived`, the offline command (ADR-0108, "The offline command").

Each test runs the real command over a store the product's own writers made.
A store at this build has nothing to regenerate, so "another build" is made
the way an upgrade leaves it: the ledger names another derived version, or
the derived partition is the frozen earlier one in
`tests/fixtures/derived/pre-cutover-mutated.sql`.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import socket
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mechanical_librarian.adapters.persistence import regeneration, schema
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.regeneration import (
    SQLiteRegenerationStore,
)
from mechanical_librarian.application.metadata_policy import PolicyContentionError
from mechanical_librarian.application.metadata_report import (
    DerivedRegenerationRequired,
    LedgerRebuildRequired,
    MetadataReportError,
)
from mechanical_librarian.application.regenerate_derived import (
    Cause,
    Outcome,
    Reason,
    regenerate_derived,
)
from mechanical_librarian.application.review_pipeline import (
    ReviewPreparationSupervisor,
)
from mechanical_librarian.bootstrap import container
from mechanical_librarian.domain.human_decision import COVER_SELECTION_AUTHORITY_SCHEMA
from mechanical_librarian.interfaces.cli import regenerate as cli
from tests.support.field_effects import effects  # noqa: F401  # reason: the companion pair's fixture asks for it.
from tests.support.genre_reports import genre_report
from tests.support.ledger_partition import (
    _derived_setup,
    copied_store,
    opens,
    rewrite,
)
from tests.support.ledger_partition import regenerate as strip_derived_rows
from tests.support.owner_values import OwnerStore
from tests.unit.test_field_effects import audiobook_pair  # noqa: F401  # reason: a fixture, used by name.

GENRES = {"kind": "list", "value": ["Fantasy"]}
FIXTURE = Path(__file__).parents[1] / "fixtures" / "derived" / "pre-cutover-mutated.sql"
# Ledger rows the regeneration itself writes; every other ledger row stays.
_WRITTEN_BY_THE_RUN = {
    "ledger_meta",
    "derived_regenerations",
    "field_effect_authorities",
}


def _store_mode() -> Any:
    raise FileNotFoundError


def _run(
    path: Path,
    *,
    retire_pending: bool = False,
    configuration: Callable[[], Any] = _store_mode,
) -> tuple[int, list[str], Outcome]:
    outcome = regenerate_derived(
        SQLiteRegenerationStore(path, lambda: 1_700_000_000),
        retire_pending=retire_pending,
        configuration=configuration,
    )
    out = io.StringIO()
    status = cli.render(outcome, out.write)
    return status, out.getvalue().splitlines(), outcome


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fresh(root: Path) -> Path:
    root.mkdir(mode=0o700)
    path = root / "application.sqlite3"
    open_repository(path).close()
    return path


def _another_build(path: Path) -> None:
    """The ledger names a derived partition this build did not make."""
    assert rewrite(path, "UPDATE ledger_meta SET derived_version=2") == 1


def _ledger(
    path: Path, *, but: frozenset[str] | set[str] = frozenset()
) -> dict[str, list[object]]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in sorted(schema.LEDGER_TABLES - but)
        }
    finally:
        connection.close()


def _derived(path: Path) -> dict[str, list[tuple[object, ...]]]:
    connection = sqlite3.connect(path)
    try:
        return {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in _derived_setup()
        }
    finally:
        connection.close()


def _refuses_unchanged(path: Path, **arguments: Any) -> tuple[int, list[str], Outcome]:
    """Run the command and require it changed nothing on the volume."""
    before = (_sha(path), sorted(os.listdir(path.parent)))
    result = _run(path, **arguments)
    assert result[0] == 1, result
    assert (_sha(path), sorted(os.listdir(path.parent))) == before
    return result


@pytest.fixture
def owner(tmp_path: Path) -> Iterator[OwnerStore]:
    root = tmp_path / "owner"
    root.mkdir(mode=0o700)
    store = OwnerStore(root, genre_report())
    try:
        yield store
    finally:
        store.close()


def _closed(store: OwnerStore) -> Path:
    store.close()
    path = store.root / "owner.sqlite"
    _another_build(path)
    return path


# --- The three argument vectors ---------------------------------------------


@pytest.mark.parametrize(
    ("vector", "parsed"),
    [
        ([], None),
        (["regenerate-derived"], False),
        (["regenerate-derived", "--retire-pending"], True),
    ],
    ids=["server", "command", "command_with_flag"],
)
def test_the_three_argument_vectors(vector: list[str], parsed: bool | None) -> None:
    assert cli.parse_container_arguments(vector) is parsed


@pytest.mark.parametrize(
    "vector",
    [
        ["--retire-pending"],
        ["x"],
        ["regenerate-derived", "x"],
        ["regenerate-derived", "--retire-pending", "x"],
    ],
    ids=["flag_alone", "other_word", "other_flag", "extra_word"],
)
def test_any_other_vector_exits_2_before_anything_opens(
    vector: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "no-volume"
    monkeypatch.setattr(container, "DATA", missing)
    monkeypatch.setattr(container, "DATABASE", missing / "application.sqlite3")
    monkeypatch.setattr(container, "_serve", lambda: pytest.fail("the server started"))
    with pytest.raises(SystemExit) as exited:
        container.main(vector)
    assert exited.value.code == 2
    assert capsys.readouterr().err == cli.USAGE + "\n"
    assert not missing.exists()


def test_the_container_runs_the_command_on_its_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    monkeypatch.setattr(container, "DATA", path.parent)
    monkeypatch.setattr(container, "DATABASE", path)
    monkeypatch.setattr(container, "CONFIG", path.parent / "config.json")
    with pytest.raises(SystemExit) as exited:
        container.main(["regenerate-derived"])
    assert exited.value.code == 0
    assert capsys.readouterr().out == cli.REGENERATED + "\n"
    assert opens(path)
    with pytest.raises(SystemExit) as again:
        container.main(["regenerate-derived"])
    assert again.value.code == 0
    assert capsys.readouterr().out == cli.NOTHING_TO_DO + "\n"


# --- Admission ------------------------------------------------------------------


def test_nothing_to_do_on_a_database_of_this_build(tmp_path: Path) -> None:
    path = _fresh(tmp_path / "data")
    before = _sha(path)
    status, lines, _ = _run(path)
    assert (status, lines) == (0, [cli.NOTHING_TO_DO])
    assert _sha(path) == before
    assert os.listdir(path.parent) == ["application.sqlite3"]


def test_no_database(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir(mode=0o700)
    status, lines, _ = _run(tmp_path / "data" / "application.sqlite3")
    assert (status, lines) == (1, [cli.REFUSALS[regeneration.Refusal.NO_DATABASE]])
    assert os.listdir(tmp_path / "data") == []


def test_a_running_server_refuses_with_its_own_message(tmp_path: Path) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    # A running server holds the binding's lock for as long as it runs.
    live = regeneration._LinuxDatabaseBinding.create(path, os.open(path, os.O_RDWR))
    try:
        _, lines, _ = _refuses_unchanged(path)
    finally:
        live.close()
    assert lines == [cli.REFUSALS[regeneration.Refusal.IN_USE]]


@pytest.mark.parametrize(
    ("statement", "refusal"),
    [
        ("DROP TABLE ledger_meta", LedgerRebuildRequired),
        ("UPDATE ledger_meta SET ledger_format_version=2", LedgerRebuildRequired),
    ],
    ids=["no_ledger_record", "another_ledger_format"],
)
def test_another_ledger_needs_a_clean_rebuild(
    tmp_path: Path, statement: str, refusal: type[Exception]
) -> None:
    path = _fresh(tmp_path / "data")
    rewrite(path, statement)
    _, lines, _ = _refuses_unchanged(path)
    assert lines == [str(refusal())]


def test_an_altered_ledger_object_refuses_as_today(tmp_path: Path) -> None:
    path = _fresh(tmp_path / "data")
    rewrite(path, "ALTER TABLE owner_links ADD COLUMN planted TEXT")
    _, lines, _ = _refuses_unchanged(path)
    assert lines == ["database is unavailable or incompatible"]


def test_a_ledger_row_its_check_refuses_is_not_admitted(tmp_path: Path) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE field_effect_control SET paused=7")
        connection.commit()
    finally:
        connection.close()
    _, lines, _ = _refuses_unchanged(path)
    assert lines == [
        "database is unavailable or incompatible: CHECK constraint failed in "
        "field_effect_control"
    ]


def test_a_database_that_fails_the_integrity_check_refuses(tmp_path: Path) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    connection = sqlite3.connect(path)
    try:
        (page,) = connection.execute(
            "SELECT rootpage FROM sqlite_master WHERE name='field_effects_target'"
        ).fetchone()
        (size,) = connection.execute("PRAGMA page_size").fetchone()
    finally:
        connection.close()
    with path.open("r+b") as database:
        database.seek((page - 1) * size)
        database.write(b"\x0d" + b"\xff" * 20)
    _, lines, _ = _refuses_unchanged(path)
    assert lines == [cli.REFUSALS[regeneration.Refusal.INTEGRITY]]


def test_a_free_space_shortfall_refuses_before_any_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    size = path.stat().st_size
    monkeypatch.setattr(
        regeneration.os,
        "fstatvfs",
        lambda _descriptor: SimpleNamespace(f_bavail=4 * size - 1, f_frsize=1),
    )
    _, lines, outcome = _refuses_unchanged(path)
    assert (outcome.free, outcome.needed) == (4 * size - 1, 4 * size)
    assert lines == [
        cli.NO_SPACE.format(free=cli._size(4 * size - 1), needed=cli._size(4 * size))
    ]


def test_the_free_space_refusal_names_an_earlier_restore_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    assert _run(path)[0] == 0
    _another_build(path)
    monkeypatch.setattr(
        regeneration.os, "fstatvfs", lambda _d: SimpleNamespace(f_bavail=0, f_frsize=1)
    )
    _, lines, _ = _refuses_unchanged(path)
    assert lines[0].endswith(" " + cli.NO_SPACE_HELD)


# --- The regeneration ------------------------------------------------------------


def test_the_regeneration_recreates_derived_state_and_keeps_the_ledger(
    owner: OwnerStore,
) -> None:
    decision = owner.decide("accept", GENRES, destination="genres")
    owner.write(decision)
    path = _closed(owner)
    ledger = _ledger(path, but=_WRITTEN_BY_THE_RUN)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM review_cases").fetchone() == (
            1,
        )
        (current,) = connection.execute(
            "SELECT count(*) FROM field_effect_authorities WHERE current=1"
        ).fetchone()
        before_meta = connection.execute(
            "SELECT derived_epoch,derived_version,derived_schema_digest FROM ledger_meta"
        ).fetchone()
    connection.close()
    status, lines, _ = _run(path)
    assert status == 0
    assert lines[-1] == cli.REGENERATED
    assert opens(path)
    assert _ledger(path, but=_WRITTEN_BY_THE_RUN) == ledger
    assert _derived(path) == _derived_setup()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT count(*) FROM field_effect_authorities WHERE current=1"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT ledger_format_version,derived_epoch,derived_version,"
            "derived_schema_digest FROM ledger_meta"
        ).fetchone() == (
            1,
            1,
            schema.DERIVED_VERSION,
            schema.build_partition().derived_schema_digest,
        )
        (row,) = connection.execute(
            "SELECT derived_epoch_before,derived_epoch_after,derived_version_before,"
            "derived_version_after,derived_schema_digest_before,"
            "derived_schema_digest_after,ledger_format_version_before,"
            "ledger_format_version_after,started_at,finished_at,backup_sha256,"
            "decision_ordinal_hwm,retire_pending,counts_document "
            "FROM derived_regenerations"
        ).fetchall()
        (hwm,) = connection.execute(
            "SELECT ordinal FROM owner_answer_ordinal"
        ).fetchone()
    finally:
        connection.close()
    backup = path.parent / "regeneration-backup" / "epoch-0.sqlite3"
    assert row[:8] == (
        0,
        1,
        2,
        schema.DERIVED_VERSION,
        before_meta[2],
        schema.build_partition().derived_schema_digest,
        1,
        1,
    )
    assert row[8:13] == (1_700_000_000, 1_700_000_000, _sha(backup), hwm, 0)
    counts = json.loads(row[13])
    assert counts["authorities_revoked"] == current >= 1
    assert counts["answers_written"] == 1
    assert counts["derived_objects_recreated"] == len(
        schema.build_partition().derived_objects
    )
    assert sorted(counts) == sorted(regeneration._COUNT_KEYS)


def test_the_restore_point_is_private_verified_and_restorable(tmp_path: Path) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    ledger = _ledger(path)
    assert _run(path)[0] == 0
    directory = path.parent / "regeneration-backup"
    backup = directory / "epoch-0.sqlite3"
    assert os.listdir(directory) == ["epoch-0.sqlite3"]
    assert directory.stat().st_mode & 0o777 == 0o700
    assert backup.stat().st_mode & 0o777 == 0o600
    # It is the database as it stood, ledger and derived partition alike.
    assert _ledger(backup) == ledger
    with pytest.raises(DerivedRegenerationRequired):
        open_repository(backup, create=False)
    # The previous release, whose derived version this is, opens it in full.
    restored = tmp_path / "restored"
    restored.mkdir(mode=0o700)
    (restored / "application.sqlite3").write_bytes(backup.read_bytes())
    rewrite(
        restored / "application.sqlite3", "UPDATE ledger_meta SET derived_version=1"
    )
    assert opens(restored / "application.sqlite3")
    # A later run replaces it with the next epoch's copy.
    _another_build(path)
    assert _run(path)[0] == 0
    assert os.listdir(directory) == ["epoch-1.sqlite3"]
    # Recovery and the next open ignore the directory.
    open_repository(path, create=False, recover_stale_bindings=True).close()


@pytest.mark.parametrize(
    "entry",
    ["wide_directory", "regular_file", "foreign_entry"],
    ids=["wide_directory", "regular_file", "foreign_entry"],
)
def test_an_unsafe_restore_point_folder_refuses(tmp_path: Path, entry: str) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    target = path.parent / "regeneration-backup"
    if entry == "regular_file":
        target.write_bytes(b"")
    else:
        target.mkdir(mode=0o700)
        if entry == "wide_directory":
            target.chmod(0o755)
        else:
            (target / "notes.txt").write_bytes(b"")
    before = _sha(path)
    status, lines, _ = _run(path)
    assert (status, lines) == (1, [cli.REFUSALS[regeneration.Refusal.BACKUP_UNSAFE]])
    assert _sha(path) == before
    assert not [name for name in os.listdir(path.parent) if name.startswith(".")]


def test_a_copy_that_does_not_match_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    digest = regeneration._table_digest
    calls = iter(range(10_000))
    monkeypatch.setattr(
        regeneration,
        "_table_digest",
        lambda connection, name, sql: digest(connection, name, sql) + str(next(calls)),
    )
    before = _sha(path)
    status, lines, _ = _run(path)
    assert (status, lines) == (1, [cli.REFUSALS[regeneration.Refusal.BACKUP_MISMATCH]])
    assert _sha(path) == before
    assert os.listdir(path.parent / "regeneration-backup") == []


@pytest.mark.parametrize(
    "phase",
    ["_drop_derived", "_revoke_authorities", "_recreate_derived", "_validate_schema"],
    ids=["drop", "ledger_writes", "recreate", "checks"],
)
def test_a_failure_in_the_transaction_rolls_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    real = getattr(regeneration, phase)

    def failing(*arguments: Any) -> Any:
        real(*arguments)
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(regeneration, phase, failing)
    before = _sha(path)
    status, lines, _ = _run(path)
    assert (status, lines) == (1, [cli.REFUSALS[regeneration.Refusal.ROLLED_BACK]])
    assert _sha(path) == before
    monkeypatch.undo()
    assert _run(path)[0] == 0
    assert opens(path)


def test_an_interrupted_copy_is_cleaned_by_recovery(tmp_path: Path) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    scratch = path.parent / (".metadata-report-recovery-" + "a" * 32)
    scratch.mkdir(mode=0o700)
    (scratch / "database.sqlite").write_bytes(b"partial")
    (scratch / "database.sqlite").chmod(0o600)
    assert _run(path)[0] == 0
    assert sorted(os.listdir(path.parent)) == [
        "application.sqlite3",
        "regeneration-backup",
    ]


# --- Planted objects and an earlier build's derived partition ------------------------


def test_an_earlier_builds_derived_partition_is_replaced(owner: OwnerStore) -> None:
    owner.write(owner.decide("accept", GENRES, destination="genres"))
    owner.close()
    path = owner.root / "owner.sqlite"
    ledger = _ledger(path, but=_WRITTEN_BY_THE_RUN)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(FIXTURE.read_text(encoding="utf-8"))
        objects = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    _, derived = schema._partition(objects)
    assert (
        schema._derived_digest(derived)
        != schema.build_partition().derived_schema_digest
    )
    names = {name for _, name, _, _ in objects}
    assert {
        "library_visit_previous",
        "review_cases_previous_order",
        "human_decisions_previous_attention",
    } <= names
    with pytest.raises(DerivedRegenerationRequired):
        open_repository(path, create=False)
    status, _, _ = _run(path)
    assert status == 0
    assert opens(path)
    assert _ledger(path, but=_WRITTEN_BY_THE_RUN) == ledger
    assert _derived(path) == _derived_setup()
    connection = sqlite3.connect(path)
    try:
        assert (
            sorted(
                connection.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
            )
            == schema._expected_schema_objects()
        )
        (counts,) = connection.execute(
            "SELECT counts_document FROM derived_regenerations"
        ).fetchone()
    finally:
        connection.close()
    # Everything outside the build's ledger list is dropped, the planted
    # trigger on a ledger table included (S12).
    ledger_list = {(k, n, q) for k, n, _, q in schema.build_partition().ledger_objects}
    planted = [o for o in objects if (o[0], o[1], o[3]) not in ledger_list]
    assert ("trigger", "human_decisions_previous_attention") in {
        (k, n) for k, n, _, _ in planted
    }
    assert json.loads(counts)["derived_objects_dropped"] == len(planted)


def test_drop_order_is_children_first_and_refuses_a_cycle() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
        "CREATE TABLE child(id INTEGER PRIMARY KEY, parent REFERENCES parent(id));"
        "CREATE TABLE grandchild(id INTEGER PRIMARY KEY, child REFERENCES child(id),"
        " self REFERENCES grandchild(id));"
        "CREATE TABLE a(id INTEGER PRIMARY KEY, b REFERENCES b(id));"
        "CREATE TABLE b(id INTEGER PRIMARY KEY, a REFERENCES a(id));"
    )
    assert regeneration._children_first(
        connection, {"parent", "child", "grandchild"}
    ) == ["grandchild", "child", "parent"]
    with pytest.raises(MetadataReportError):
        regeneration._children_first(connection, {"a", "b"})


# --- The gate ----------------------------------------------------------------------


def test_a_waiting_write_blocks_until_it_is_retired(owner: OwnerStore) -> None:
    owner.plan(owner.decide("accept", GENRES, destination="genres"))
    path = _closed(owner)
    _, lines, outcome = _refuses_unchanged(path)
    (item,) = outcome.items
    assert (item.reason, item.destination) == (Reason.WAITING, "genres")
    assert lines == [
        cli.GATE_HEADING,
        f"  {item.connection}/{item.library}, work {item.item_id}, Genres: "
        f"{cli.REASONS[Reason.WAITING]} {cli.NO_CAUSE}",
    ]
    # The flag retires such a write, which this build cannot do yet.
    _, lines, _ = _refuses_unchanged(path, retire_pending=True)
    assert lines == [cli.REFUSALS[regeneration.Refusal.RETIRE_NOT_BUILT]]


def test_a_write_that_reached_audiobookshelf_blocks_even_with_the_flag(
    owner: OwnerStore,
) -> None:
    operation = owner.plan(owner.decide("accept", GENRES, destination="genres"))
    owner.target.fail_next = True
    assert owner.service.apply(operation.plan.plan_id).state == "retryable"
    path = _closed(owner)
    for flag in (False, True):
        _, lines, outcome = _refuses_unchanged(path, retire_pending=flag)
        assert [item.reason for item in outcome.items] == [Reason.MAY_HAVE_RECEIVED]
        assert lines[1].endswith(
            f"Genres: {cli.REASONS[Reason.MAY_HAVE_RECEIVED]} {cli.NO_CAUSE}"
        )


def test_the_gate_names_what_it_can_see_offline(owner: OwnerStore) -> None:
    owner.plan(owner.decide("accept", GENRES, destination="genres"))
    path = _closed(owner)
    rewrite(path, "UPDATE field_effect_control SET paused=1")
    rewrite(path, "UPDATE review_preparation_owner_control SET paused=1")
    configured = SimpleNamespace(libraries=())
    _, lines, outcome = _refuses_unchanged(path, configuration=lambda: configured)
    (item,) = outcome.items
    assert item.causes == (
        Cause.WRITES_PAUSED,
        Cause.PREPARATION_PAUSED,
        Cause.LIBRARY_NOT_CONFIGURED,
    )
    assert lines[1].endswith(
        "Writes are paused. Preparation is paused. "
        "This library is no longer in the settings."
    )
    # In store mode, a store with no settings names no library reason.
    _, _, stored = _refuses_unchanged(path)
    assert stored.items[0].causes == (Cause.WRITES_PAUSED, Cause.PREPARATION_PAUSED)


def test_open_resume_and_processing_policy_predicates() -> None:
    """The gate's Resume and policy reads, on rows put straight in: a failed
    request is not open, a closed one is not, and only the active
    generation's transition counts."""
    connection = sqlite3.connect(":memory:")
    schema._register_casefold(connection)
    schema._create_schema(connection)
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    ).fetchall():
        connection.execute(f'DROP TRIGGER "{name}"')
    connection.execute("PRAGMA foreign_keys=OFF")

    def event(event_id: str, item: str, kind: str, request: str | None) -> None:
        connection.execute(
            "INSERT INTO owner_field_events(event_id,connection_name,library_name,"
            "item_id,destination,kind,request_id,over_ordinal,scan_id,occurred_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (event_id, "home", "Books", item, "genres", kind, request, 1, "scan", 1),
        )

    ids = {name: "owner_field_event_" + name * 64 for name in "abcdef"}
    event(ids["a"], "open", "resume_requested", None)
    event(ids["b"], "failed", "resume_requested", None)
    event(ids["c"], "failed", "resume_failed", ids["b"])
    event(ids["d"], "closed", "resume_requested", None)
    event(ids["e"], "closed", "resume_superseded", ids["d"])
    assert connection.execute(regeneration._OPEN_RESUME_SQL).fetchall() == [
        ("home", "Books", "open", "genres")
    ]
    assert connection.execute(regeneration._POLICY_PROCESSING_SQL).fetchone() == (0,)
    connection.execute("UPDATE metadata_policy_transitions SET state='processing'")
    assert connection.execute(regeneration._POLICY_PROCESSING_SQL).fetchone() == (1,)
    connection.execute("UPDATE metadata_policy_state SET generation=2")
    assert connection.execute(regeneration._POLICY_PROCESSING_SQL).fetchone() == (0,)


def test_a_resume_and_a_policy_check_render_as_the_gate_names_them() -> None:
    lines: list[str] = []
    cli.render(
        Outcome(
            "gate",
            items=(
                regeneration.BlockingItem(
                    Reason.RESUME, (), "home", "Books", "item-1", "description"
                ),
                regeneration.BlockingItem(Reason.POLICY, (Cause.PREPARATION_PAUSED,)),
            ),
        ),
        lines.append,
    )
    assert lines == [
        cli.GATE_HEADING + "\n",
        "  home/Books, work item-1, Description: "
        f"{cli.REASONS[Reason.RESUME]} {cli.NO_CAUSE}\n",
        f"  {cli.REASONS[Reason.POLICY]} Preparation is paused.\n",
    ]


# --- The report and the decision rule ------------------------------------------------


def test_an_unwritten_answer_is_reported_and_the_run_goes_ahead(
    owner: OwnerStore,
) -> None:
    owner.decide("accept", GENRES, destination="genres")
    target = owner.case.target
    path = _closed(owner)
    status, lines, _ = _run(path)
    assert status == 0
    assert lines == [
        f"{target['connection']}/{target['library']}",
        "  " + cli.UNWRITTEN.format(n=1),
        f"    work {target['item']}, Genres",
        "  " + cli.WRITTEN.format(n=0),
        cli.UPPER_LIMITS,
        cli.REGENERATED,
    ]


def test_a_stopped_write_needs_the_flag(owner: OwnerStore) -> None:
    decision = owner.decide("accept", GENRES, destination="genres")
    operation = owner.plan(decision, seed=False)
    stopped = owner.service.apply(operation.plan.plan_id)
    assert (stopped.state, stopped.failure) == ("stale", "target_changed")
    target = owner.case.target
    path = _closed(owner)
    _, lines, _ = _refuses_unchanged(path)
    assert lines[-4:] == [
        "  " + cli.STOPPED.format(n=1),
        f"    work {target['item']}, Genres",
        cli.UPPER_LIMITS,
        cli.GO_AHEAD,
    ]
    status, lines, _ = _run(path, retire_pending=True)
    assert (status, lines[-1]) == (0, cli.REGENERATED)
    assert opens(path)


def test_the_output_names_no_title_author_path_or_value(
    owner: OwnerStore, capsys: pytest.CaptureFixture[str]
) -> None:
    owner.decide("accept", GENRES, destination="genres")
    path = _closed(owner)
    connection = sqlite3.connect(path)
    try:
        sentinels = {
            value
            for (value,) in connection.execute(
                "SELECT display_title FROM human_decisions"
            )
        } | {"Fantasy", str(path), str(path.parent)}
    finally:
        connection.close()
    _, lines, _ = _run(path)
    printed = "\n".join(lines) + capsys.readouterr().err
    assert sentinels and not [value for value in sentinels if value in printed]


def test_the_run_contacts_no_network(
    owner: OwnerStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner.write(owner.decide("accept", GENRES, destination="genres"))
    path = _closed(owner)

    def refused(*_arguments: object) -> None:
        raise AssertionError("regenerate-derived opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    assert _run(path)[0] == 0


def test_the_report_renders_covers_and_authored_descriptions() -> None:
    lines: list[str] = []
    report = regeneration.LibraryReport(
        "home",
        "Books",
        authored=(regeneration.WorkField("item-2", "description"),),
        covers=(
            regeneration.CoverPointer("item-3", True),
            regeneration.CoverPointer("item-4", False),
        ),
    )
    assert report.stops_without_flag
    cli.render(Outcome("report", reports=(report,), backup_held=True), lines.append)
    assert [line.rstrip("\n") for line in lines] == [
        "home/Books",
        "  " + cli.UNWRITTEN.format(n=0),
        "  " + cli.WRITTEN.format(n=0),
        "  " + cli.AUTHORED.format(n=1),
        "    work item-2, Description",
        "  " + cli.COVERS.format(n=2),
        "    " + cli.COVER_KEPT.format(item_id="item-3"),
        "    " + cli.COVER_NONE.format(item_id="item-4"),
        cli.UPPER_LIMITS,
        cli.REPLACED,
        cli.GO_AHEAD,
    ]


# --- After a regeneration ------------------------------------------------------------


def test_retry_refuses_and_the_store_reopens_after_a_regeneration(
    tmp_path: Path,
) -> None:
    from tests.unit.test_metadata_policy_activation import _partly_failed_activation

    repository, _, _, _, _, failed, _ = _partly_failed_activation(
        tmp_path / "partly.sqlite", "provider"
    )
    repository.close()
    path = tmp_path / "partly.sqlite"
    _another_build(path)
    status, lines, outcome = _run(path, retire_pending=True)
    assert status == 0, lines
    reopened = open_repository(path, create=False)
    try:
        with pytest.raises(PolicyContentionError) as refused:
            reopened.retry_metadata_policy_transition(failed.transition_id, 30)
        assert refused.value.condition == "stale"
    finally:
        reopened.close()
    assert opens(path)


def test_the_next_start_asks_every_configured_library_to_scan(tmp_path: Path) -> None:
    path = _fresh(tmp_path / "data")
    libraries = (("home", "Books", "library-books"), ("home", "Talks", "library-talks"))
    repository = open_repository(path, create=False)
    try:
        for connection, library, library_id in libraries:
            repository.request_review_scan(connection, library, library_id, "a" * 64, 5)
    finally:
        repository.close()
    _another_build(path)

    def requested() -> list[tuple[str, str]]:
        calls: list[tuple[str, str]] = []
        repository = open_repository(path, create=False)
        try:
            pipeline = SimpleNamespace(
                recovery_required=False,
                libraries=libraries,
                status=lambda: repository.review_pipeline_status(
                    tuple(sorted((name, shelf) for name, shelf, _ in libraries))
                ),
                request=lambda connection, library, **_: calls.append(
                    (connection, library)
                ),
            )
            ReviewPreparationSupervisor(
                pipeline,  # type: ignore[arg-type]  # reason: only the members scheduling reads are present.
                polling_interval_seconds=60,
                clock=lambda: 10,
            )._schedule_due()
        finally:
            repository.close()
        return calls

    rewrite(path, "UPDATE ledger_meta SET derived_version=1")
    assert requested() == []  # each library's scan is still queued
    _another_build(path)
    assert _run(path)[0] == 0
    assert requested() == [("home", "Books"), ("home", "Talks")]


# --- The ledger-only admission's one relaxation (contract Amendment 2) ---------------


@pytest.mark.parametrize("kind", ["planned", "verified"], ids=["live", "terminal"])
def test_only_a_live_companion_plan_needs_the_offline_admission(
    audiobook_pair: Any,  # noqa: F811
    tmp_path: Path,
    kind: str,
) -> None:
    """A live title plan with its subtitle companion needs its two automatic
    decisions, which are derived: with them gone, ordinary open refuses it and
    the offline admission admits it, and the command reaches its gate. A
    terminal plan needs no relaxation (negative control)."""
    operation = audiobook_pair.plan()
    if kind == "verified":
        assert audiobook_pair.service.apply(operation.plan.plan_id).state == "verified"
    path = copied_store(audiobook_pair.repo, tmp_path / "copy")
    stripped = tmp_path / "stripped"
    stripped.mkdir(mode=0o700)
    (stripped / "application.sqlite3").write_bytes(path.read_bytes())
    strip_derived_rows(stripped / "application.sqlite3")
    assert opens(stripped / "application.sqlite3") is (kind == "verified")
    connection = sqlite3.connect(stripped / "application.sqlite3")
    try:
        schema._register_casefold(connection)
        connection.create_function("ml_cover_consideration_current", 2, lambda *_: 0)
        schema._validate_schema_contents(connection, offline=True)
        if kind == "planned":
            with pytest.raises(MetadataReportError):
                schema._validate_schema_contents(connection)
    finally:
        connection.close()
    path.chmod(0o600)  # as the server's own file is
    _another_build(path)
    status, lines, outcome = _run(path)
    if kind == "planned":
        assert status == 1
        assert [item.reason for item in outcome.items] == [Reason.WAITING]
    else:
        assert (status, lines[-1]) == (0, cli.REGENERATED)


# --- Owner cover pointers ------------------------------------------------------------


def test_an_unwritten_cover_choice_needs_the_flag_and_its_pointer_goes(
    tmp_path: Path,
) -> None:
    from tests.support.cover_taste import record_owner_cover_decisions

    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    path = root / "application.sqlite3"
    repository = open_repository(path)
    try:
        record_owner_cover_decisions(root, repository, 2)
    finally:
        repository.close()
    _another_build(path)
    with sqlite3.connect(path) as connection:
        items = [
            item
            for (item,) in connection.execute(
                "SELECT item_id FROM owner_cover_pointers ORDER BY item_id"
            )
        ]
    connection.close()
    assert items == ["item-0", "item-1"]
    _, lines, _ = _refuses_unchanged(path)
    assert lines[3:6] == [
        "  " + cli.COVERS.format(n=2),
        *("    " + cli.COVER_NONE.format(item_id=item) for item in items),
    ]
    assert lines[-1] == cli.GO_AHEAD
    status, _, _ = _run(path, retire_pending=True)
    assert status == 0
    assert opens(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT count(*) FROM owner_cover_pointers"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM owner_cover_selections"
        ).fetchone() == (2,)
        (counts,) = connection.execute(
            "SELECT counts_document FROM derived_regenerations"
        ).fetchone()
    finally:
        connection.close()
    assert json.loads(counts)["cover_pointers_deleted"] == 2


def test_the_pointer_plan_keeps_repoints_or_deletes() -> None:
    """On rows put straight in: a pointer at a written choice, a keep or a
    rejection stays; one at an unwritten choice names the work's latest
    written choice, or goes when there is none."""
    connection = sqlite3.connect(":memory:")
    schema._register_casefold(connection)
    schema._create_schema(connection)
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    ).fetchall():
        connection.execute(f'DROP TRIGGER "{name}"')
    connection.execute("PRAGMA foreign_keys=OFF")
    ordinal = iter(range(1, 100))

    def choice(item: str, tag: str, outcome: str, *, written: bool = False) -> str:
        decision = "human_decision_" + tag * 64
        selection = "cover_selection_" + tag * 64
        connection.execute(
            "INSERT INTO human_decisions(ordinal,decision_id,review_case_id,proposal_id,"
            "target_fingerprint,destination,actor,outcome,selected_value_document,"
            "idempotency_digest,request_digest,occurred_at,document,connection_name,"
            "library_name,item_id,target_field_document) VALUES(?,?,?,?,?,'cover',"
            "'owner',?,'null',?,?,1,'{}','home','Books',?,'null')",
            (
                next(ordinal),
                decision,
                "review_case_" + "0" * 64,
                "review_proposal_" + "0" * 64,
                "0" * 64,
                outcome,
                tag * 64,
                "0" * 64,
                item,
            ),
        )
        connection.execute(
            "INSERT INTO owner_cover_selections(selection_id,connection_name,"
            "library_name,item_id,decision_id,consideration_id,occurred_at,document,"
            "cover_took_over) VALUES(?,'home','Books',?,?,?,1,?,0)",
            (
                selection,
                item,
                decision,
                "cover_consideration_" + "0" * 64,
                json.dumps({"schema_version": COVER_SELECTION_AUTHORITY_SCHEMA}),
            ),
        )
        if written:
            connection.execute(
                "INSERT INTO field_effect_no_changes(decision_id,review_case_id,"
                "report_id,connection_name,library_name,work_id,destination,"
                "target_fingerprint,target_version,origin,occurred_at,event_id) "
                "VALUES(?,?,?,'home','Books',?,'cover','0','0','human',1,?)",
                (decision, "review_case_" + "0" * 64, "report_" + "0" * 64, item, tag),
            )
        return selection

    def point(item: str, selection: str) -> None:
        connection.execute(
            "INSERT INTO owner_cover_pointers VALUES('home','Books',?,?)",
            (item, selection),
        )

    point("written", choice("written", "1", "accept", written=True))
    point("kept", choice("kept", "2", "keep_current"))
    point("rejected", choice("rejected", "3", "reject"))
    earlier = choice("repointed", "4", "accept", written=True)
    choice("repointed", "5", "accept", written=True)
    later = "cover_selection_" + "5" * 64
    point("repointed", choice("repointed", "6", "accept"))
    point("deleted", choice("deleted", "7", "accept"))
    assert earlier != later
    assert regeneration._pointer_plan(connection) == [
        ("home", "Books", "deleted", None),
        ("home", "Books", "repointed", later),
    ]


def test_an_unwritten_authored_description_needs_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.support.description_advice import _prepared, qualify_description_advice

    qualify_description_advice(monkeypatch)
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    repository, store, binding, candidate, _ = _prepared(root)
    try:
        store.admit(candidate.candidate_id, binding, "human", "a" * 64, 11)
        (item,) = repository._connection.execute(
            "SELECT item_id FROM owner_field_answers WHERE source='authored_description'"
        ).fetchone()
    finally:
        repository.close()
    path = root / "reports.sqlite"
    path.chmod(0o600)
    _another_build(path)
    _, lines, _ = _refuses_unchanged(path)
    assert lines[-4:-2] == [
        "  " + cli.AUTHORED.format(n=1),
        f"    work {item}, Description",
    ]
    status, _, _ = _run(path, retire_pending=True)
    assert status == 0
    assert opens(path)


def test_an_answer_is_written_by_its_companion_write_or_its_own_rows() -> None:
    """The shared written condition, on rows put straight in: a subtitle a
    title plan wrote as its companion, and an authored description with no
    `human_decisions` row, read as written; an answer with neither does not."""
    from mechanical_librarian.adapters.persistence.owner_field_answers import (
        answer_written_sql,
    )

    connection = sqlite3.connect(":memory:")
    schema._register_casefold(connection)
    schema._create_schema(connection)
    connection.execute("PRAGMA foreign_keys=OFF")
    subtitle, authored, unwritten = (
        "human_decision_" + tag * 64 for tag in ("a", "b", "c")
    )
    connection.execute(
        "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,"
        "work_id,destination,state,attempt,updated_at,document) "
        "VALUES(?,?,'home','Books','item-1','title','verified',1,1,?)",
        (
            "effect_plan_" + "0" * 64,
            "human_decision_" + "d" * 64,
            json.dumps(
                {
                    "plan": {
                        "subtitle_companion": {"authority": {"decision_id": subtitle}}
                    }
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO field_effect_no_changes(decision_id,review_case_id,report_id,"
        "connection_name,library_name,work_id,destination,target_fingerprint,"
        "target_version,origin,occurred_at,event_id) VALUES(?,?,?,'home','Books',"
        "'item-2','description','0','0','human',1,'event')",
        (authored, "review_case_" + "0" * 64, "report_" + "0" * 64),
    )
    for ordinal, decision in enumerate((subtitle, unwritten), start=1):
        connection.execute(
            "INSERT INTO human_decisions(ordinal,decision_id,review_case_id,proposal_id,"
            "target_fingerprint,destination,actor,outcome,selected_value_document,"
            "idempotency_digest,request_digest,occurred_at,document,connection_name,"
            "library_name,item_id,target_field_document) VALUES(?,?,?,?,?,'subtitle',"
            "'owner','accept','null',?,?,1,'{}','home','Books','item-1','null')",
            (
                ordinal,
                decision,
                "review_case_" + "0" * 64,
                "review_proposal_" + "0" * 64,
                "0" * 64,
                str(ordinal) * 64,
                "0" * 64,
            ),
        )
    connection.execute(
        "INSERT INTO owner_authored_descriptions(decision_id,review_case_id,"
        "candidate_id,idempotency_digest,document,connection_name,library_name,"
        "item_id,candidate_document,ordinal,episode) VALUES(?,?,?,?,'{}','home',"
        "'Books','item-2','{}',3,0)",
        (
            authored,
            "review_case_" + "0" * 64,
            "description_candidate_" + "0" * 64,
            "9" * 64,
        ),
    )
    written = [
        connection.execute(
            f"SELECT {answer_written_sql(':a')}", {"a": answer}
        ).fetchone()[0]
        for answer in (subtitle, authored, unwritten)
    ]
    assert written == [1, 1, 0]
