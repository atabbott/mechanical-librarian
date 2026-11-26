"""`regenerate-derived`, the offline command (ADR-0108, "The offline command").

Each test runs the real command over a store the product's own writers made.
A store at this build has nothing to regenerate, so "another build" is made
the way an upgrade leaves it: the ledger names another derived version, or
the derived partition is the frozen earlier one in
`tests/fixtures/derived/pre-cutover-mutated.sql`.
"""

from __future__ import annotations

import dataclasses
import errno
import hashlib
import io
import json
import os
import socket
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    BlockingItem,
    Cause,
    Inspection,
    LibraryReport,
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
    strip_derived_rows,
)
from tests.support.owner_field_record import assert_record_recomputes
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


def test_nothing_to_do_needs_no_free_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database of this build is not copied, so a full volume still says
    there is nothing to regenerate rather than asking for space."""
    path = _fresh(tmp_path / "data")
    before = _sha(path)
    monkeypatch.setattr(
        regeneration.os,
        "fstatvfs",
        lambda _descriptor: SimpleNamespace(f_bavail=0, f_frsize=1),
    )
    entered = _scratch_entries(monkeypatch)
    status, lines, _ = _run(path)
    assert (status, lines) == (0, [cli.NOTHING_TO_DO])
    assert entered == []
    assert _sha(path) == before


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE browse_fact_identity SET unicode_version='15.1.0-other'",
        "UPDATE browse_fact_identity SET value_flag_rules='value-flag-rules-v0'",
    ],
    ids=["another_unicode_version", "another_rule_list"],
)
def test_browse_facts_folded_under_another_python_are_regenerated(
    owner: OwnerStore, statement: str
) -> None:
    """Browse facts and value flags are derived (ADR-0108): when they were
    folded under another Unicode version or rule list, startup names the
    regeneration and the command regenerates instead of saying the database
    already matches. The schema and the ledger's derived version are this
    build's, so only the seed row differs."""
    owner.decide("accept", GENRES, destination="genres")
    owner.close()
    path = owner.root / "owner.sqlite"
    assert rewrite(path, statement) == 1
    with pytest.raises(DerivedRegenerationRequired):
        open_repository(path, create=False)
    status, lines, outcome = _run(path)
    assert (status, outcome.kind) == (0, "regenerated"), lines
    assert opens(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT singleton,unicode_version,value_flag_rules FROM browse_fact_identity"
        ).fetchall() == [schema._SETUP_BROWSE_FACT_IDENTITY_ROW]
    finally:
        connection.close()
    assert _run(path)[:2] == (0, [cli.NOTHING_TO_DO])


def test_a_browse_fact_identity_that_is_not_one_row_refuses_as_today(
    tmp_path: Path,
) -> None:
    """The control: an identity table without its one row is an altered
    derived object, which the full comparison refuses as before; only a row
    naming another Unicode version or rule list is another build's."""
    path = _fresh(tmp_path / "data")
    rewrite(path, "DELETE FROM browse_fact_identity")
    with pytest.raises(MetadataReportError) as startup:
        open_repository(path, create=False)
    assert not isinstance(startup.value, DerivedRegenerationRequired)
    assert str(startup.value) == "database is unavailable or incompatible"


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


def test_an_altered_ledger_object_refuses_with_its_own_line(tmp_path: Path) -> None:
    """Refused before anything else, as startup refuses it (S1 precedence),
    but in the command's own words (copy R10), never startup's internal
    text."""
    path = _fresh(tmp_path / "data")
    rewrite(path, "ALTER TABLE owner_links ADD COLUMN planted TEXT")
    with pytest.raises(MetadataReportError) as startup:
        open_repository(path, create=False)
    _, lines, outcome = _refuses_unchanged(path)
    assert outcome.refusal is regeneration.Refusal.LEDGER_ALTERED
    assert lines == [cli.REFUSALS[regeneration.Refusal.LEDGER_ALTERED]]
    assert str(startup.value) not in lines[0]


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
    _, lines, outcome = _refuses_unchanged(path)
    # Any failure the command names no other way prints R9, never its own text.
    assert (outcome.kind, outcome.message) == ("failed", None)
    assert lines == [cli.FAILED]


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


# Steps admission's dry run calls first, on its private copy, before the
# transaction calls them again (ADR-0108 step 1).
_DRY_RUN_TOO = frozenset({"_recreate_derived", "migrate_ledger"})


def _fails_in_the_transaction(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    error: BaseException,
    *,
    after_the_step: bool = True,
) -> None:
    """The transaction's call of ``phase`` raises ``error``, after doing its
    work unless ``after_the_step`` is false; the dry run's call, if any,
    runs as it is."""
    real = getattr(regeneration, phase)
    calls: list[None] = []

    def failing(*arguments: Any) -> Any:
        calls.append(None)
        if phase in _DRY_RUN_TOO and len(calls) == 1:
            return real(*arguments)
        if after_the_step:
            real(*arguments)
        raise error

    monkeypatch.setattr(regeneration, phase, failing)


def _held_after_a_stopped_run(path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A run that took its restore point and then stopped inside the
    transaction, leaving the database as it was and `epoch-0` held: the
    state recovery leaves after a run killed there."""
    _fails_in_the_transaction(
        monkeypatch, "_recreate_derived", sqlite3.OperationalError("disk I/O error")
    )
    assert _run(path)[2].refusal is regeneration.Refusal.ROLLED_BACK
    monkeypatch.undo()
    held = path.parent / "regeneration-backup" / "epoch-0.sqlite3"
    assert os.listdir(held.parent) == [held.name]
    return held


def _fits_only_with(
    path: Path, held: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, int]:
    """The free-space measure reports a volume that holds the reserve only
    when the held copy's bytes count as free."""
    needed = 4 * path.stat().st_size
    free = needed - held.stat().st_size
    monkeypatch.setattr(
        regeneration.os,
        "fstatvfs",
        lambda _descriptor: SimpleNamespace(f_bavail=free, f_frsize=1),
    )
    return free, needed


def test_a_held_copy_the_rerun_replaces_counts_as_free_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a stopped run, its own restore point of the same epoch and
    ledger counts as free; it is removed just before the new copy is taken,
    and the new copy and the database keep the ledger as it stood."""
    path = _fresh(tmp_path / "data")
    _another_build(path)
    ledger = _ledger(path)
    held = _held_after_a_stopped_run(path, monkeypatch)
    assert _ledger(held) == ledger
    _fits_only_with(path, held, monkeypatch)
    events: list[object] = []
    real_unlink, real_verify = os.unlink, regeneration._verify_copy

    def unlink(name: Any, *arguments: Any, **keywords: Any) -> None:
        if name == held.name:
            # The new copy's scratch file exists and is still empty.
            scratch = path.parent.glob(".metadata-report-recovery-*/database.sqlite")
            events.append(("unlink", [copy.stat().st_size for copy in scratch]))
        real_unlink(name, *arguments, **keywords)

    def verify(source: sqlite3.Connection, copy: Path) -> None:
        events.append("copy_taken")
        real_verify(source, copy)

    monkeypatch.setattr(regeneration.os, "unlink", unlink)
    monkeypatch.setattr(regeneration, "_verify_copy", verify)
    status, lines, outcome = _run(path)
    assert (status, outcome.kind) == (0, "regenerated"), lines
    assert events == [("unlink", [0]), "copy_taken"]
    assert os.listdir(held.parent) == [held.name]
    assert _ledger(held) == ledger
    assert _ledger(path, but=_WRITTEN_BY_THE_RUN) == {
        table: rows
        for table, rows in ledger.items()
        if table not in _WRITTEN_BY_THE_RUN
    }
    assert opens(path)


def test_a_volume_with_room_never_reads_the_held_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _fresh(tmp_path / "data")
    _another_build(path)
    _held_after_a_stopped_run(path, monkeypatch)
    monkeypatch.setattr(
        regeneration, "_replaced_copy", _fails_with(AssertionError("read"))
    )
    assert _run(path)[0] == 0


def test_without_the_credit_at_the_gate_the_copy_is_replaced_after_the_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission needed the held copy's bytes, but the volume had room by the
    time the run checked again: the held copy is replaced by the rename, as
    without a credit, and never removed before the new copy exists."""
    path = _fresh(tmp_path / "data")
    _another_build(path)
    held = _held_after_a_stopped_run(path, monkeypatch)
    free, _ = _fits_only_with(path, held, monkeypatch)
    measures = iter([free])
    monkeypatch.setattr(
        regeneration.os,
        "fstatvfs",
        lambda _descriptor: SimpleNamespace(
            f_bavail=next(measures, 1 << 40), f_frsize=1
        ),
    )
    real_unlink = os.unlink
    removed: list[Any] = []

    def unlink(name: Any, *arguments: Any, **keywords: Any) -> None:
        removed.append(name)
        real_unlink(name, *arguments, **keywords)

    monkeypatch.setattr(regeneration.os, "unlink", unlink)
    assert _run(path)[0] == 0
    assert held.name not in removed
    assert os.listdir(held.parent) == [held.name]


def _another_epoch(held: Path, _monkeypatch: pytest.MonkeyPatch) -> Path:
    return held.rename(held.with_name("epoch-7.sqlite3"))


def _ledger_differs(held: Path, _monkeypatch: pytest.MonkeyPatch) -> Path:
    assert rewrite(held, "UPDATE field_effect_control SET paused=1") == 1
    return held


def _no_ledger_table(held: Path, _monkeypatch: pytest.MonkeyPatch) -> Path:
    connection = sqlite3.connect(held)
    try:
        connection.execute("DROP TABLE owner_answer_ordinal")
        connection.commit()
    finally:
        connection.close()
    return held


def _not_sqlite(held: Path, _monkeypatch: pytest.MonkeyPatch) -> Path:
    held.write_bytes(b"\xff" * held.stat().st_size)
    return held


def _swap(held: Path) -> None:
    """The same bytes and times under the same name, in another file."""
    other = held.with_name("swapped")
    other.write_bytes(held.read_bytes())
    information = held.stat()
    os.utime(other, ns=(information.st_atime_ns, information.st_mtime_ns))
    other.chmod(0o600)
    os.replace(other, held)


def _swapped_before_the_gate(held: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    real = regeneration.SQLiteRegenerationSession.inspect

    def inspect(self: Any, configured: Any) -> Any:
        _swap(held)
        return real(self, configured)

    monkeypatch.setattr(regeneration.SQLiteRegenerationSession, "inspect", inspect)
    return held


def _swapped_before_the_copy(held: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    real = regeneration.SQLiteRegenerationSession.back_up

    def back_up(self: Any) -> None:
        _swap(held)
        real(self)

    monkeypatch.setattr(regeneration.SQLiteRegenerationSession, "back_up", back_up)
    return held


@pytest.mark.parametrize(
    "spoil",
    [
        _another_epoch,
        _ledger_differs,
        _no_ledger_table,
        _not_sqlite,
        _swapped_before_the_gate,
        _swapped_before_the_copy,
    ],
    ids=[
        "another_epoch",
        "ledger_differs",
        "no_ledger_table",
        "not_sqlite",
        "swapped_before_the_gate",
        "swapped_before_the_copy",
    ],
)
def test_a_held_copy_that_does_not_qualify_refuses_for_space_and_is_kept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spoil: Callable[[Path, pytest.MonkeyPatch], Path],
) -> None:
    """Only a held copy of the database's epoch and ledger, unchanged since
    admission checked it, counts as free; otherwise the free-space refusal
    stands as it reads today and the copy stays."""
    path = _fresh(tmp_path / "data")
    _another_build(path)
    kept = spoil(_held_after_a_stopped_run(path, monkeypatch), monkeypatch)
    free, needed = _fits_only_with(path, kept, monkeypatch)
    before = _sha(kept)
    _, lines, outcome = _refuses_unchanged(path)
    assert (outcome.kind, outcome.free, outcome.needed) == ("no_space", free, needed)
    assert outcome.backup_held
    assert lines == [_no_space_line(outcome)]
    assert os.listdir(kept.parent) == [kept.name]
    assert _sha(kept) == before


def _scratch_entries(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record each scratch copy the run starts; the copy is still made."""
    real = regeneration._scratch
    entered: list[str] = []

    @contextmanager
    def scratch(binding: Any) -> Iterator[tuple[int, Path]]:
        entered.append("scratch")
        with real(binding) as value:
            yield value

    monkeypatch.setattr(regeneration, "_scratch", scratch)
    return entered


def _scratch_meets_a_full_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """Admission's scratch copy cannot grow: SQLite's own SQLITE_FULL, met on
    the copy's first write, as on a volume with no room left. The scratch
    file is the one connection the run opens on an empty file."""
    real = regeneration._prepare

    def prepare(connection: sqlite3.Connection) -> None:
        real(connection)
        if connection.execute("PRAGMA page_count").fetchone() == (0,):
            connection.execute("PRAGMA max_page_count=1")

    monkeypatch.setattr(regeneration, "_prepare", prepare)


def _no_space_line(outcome: Outcome) -> str:
    line = cli.NO_SPACE.format(
        free=cli._size(outcome.free), needed=cli._size(outcome.needed)
    )
    return line + (" " + cli.NO_SPACE_HELD if outcome.backup_held else "")


def _no_scratch_left(path: Path) -> bool:
    return not [n for n in os.listdir(path.parent) if n.startswith(".metadata")]


def test_a_volume_without_room_for_one_copy_refuses_before_admission_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The volume has less free than the database's own size, so admission's
    scratch copy could not be written: the free-space refusal comes first,
    and the copy is never started (review round 1, finding 1)."""
    path = _fresh(tmp_path / "data")
    _another_build(path)
    size = path.stat().st_size
    monkeypatch.setattr(
        regeneration.os,
        "fstatvfs",
        lambda _descriptor: SimpleNamespace(f_bavail=size - 1, f_frsize=1),
    )
    _scratch_meets_a_full_volume(monkeypatch)
    entered = _scratch_entries(monkeypatch)
    status, lines, outcome = _refuses_unchanged(path)
    assert (status, outcome.kind) == (1, "no_space")
    assert (outcome.free, outcome.needed) == (size - 1, 4 * size)
    assert lines == [_no_space_line(outcome)]
    assert entered == []
    assert _no_scratch_left(path)


def _fails_with(error: BaseException) -> Callable[..., Any]:
    def failing(*_arguments: Any, **_keywords: Any) -> Any:
        raise error

    return failing


def _sqlite_full() -> sqlite3.OperationalError:
    error = sqlite3.OperationalError("database or disk is full")
    error.sqlite_errorcode = sqlite3.SQLITE_FULL
    return error


def _enospc() -> OSError:
    return OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))


def _full_at_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        regeneration, "recover_private_bindings", _fails_with(_enospc())
    )


def _full_at_admission_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    _scratch_meets_a_full_volume(monkeypatch)


def _full_at_backup_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    real = os.mkdir

    def mkdir(name: Any, *arguments: Any, **keywords: Any) -> None:
        if name == regeneration.BACKUP_DIRECTORY:
            raise _enospc()
        real(name, *arguments, **keywords)

    monkeypatch.setattr(regeneration.os, "mkdir", mkdir)


def _full_at_backup_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(regeneration.os, "fsync", _fails_with(_enospc()))


def _full_in_the_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    _fails_in_the_transaction(
        monkeypatch, "_recreate_derived", _sqlite_full(), after_the_step=False
    )


@pytest.mark.parametrize(
    "full",
    [
        _full_at_recovery,
        _full_at_admission_copy,
        _full_at_backup_folder,
        _full_at_backup_sync,
        _full_in_the_transaction,
    ],
    ids=[
        "recovery",
        "admission_copy",
        "backup_folder",
        "backup_sync",
        "transaction",
    ],
)
def test_a_volume_that_fills_during_the_run_refuses_with_the_free_space_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    full: Callable[[pytest.MonkeyPatch], None],
) -> None:
    """A write that meets a full volume after the free-space check passed
    (another writer filled it) is the free-space refusal, measured then;
    the database is unchanged and no scratch copy is left."""
    path = _fresh(tmp_path / "data")
    _another_build(path)
    before = _sha(path)
    full(monkeypatch)
    status, lines, outcome = _run(path)
    assert (status, outcome.kind) == (1, "no_space")
    assert lines == [_no_space_line(outcome)]
    assert _sha(path) == before
    assert _no_scratch_left(path)
    backups = path.parent / "regeneration-backup"
    # Only the transaction runs after a retained copy; it keeps it (step 5).
    held = sorted(os.listdir(backups)) if backups.exists() else []
    assert held == (["epoch-0.sqlite3"] if full is _full_in_the_transaction else [])
    assert outcome.backup_held is bool(held)
    monkeypatch.undo()
    assert _run(path)[0] == 0


def _check_violation(path: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE field_effect_control SET paused=7")
        connection.commit()
    finally:
        connection.close()


def _authority_changed(_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        regeneration._LinuxDatabaseBinding,
        "require_current",
        _fails_with(MetadataReportError("database authority changed")),
    )


def _recovery_fails(_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        regeneration,
        "recover_private_bindings",
        _fails_with(sqlite3.OperationalError("disk I/O error")),
    )


def _inspection_fails(_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        regeneration.SQLiteRegenerationSession,
        "_inspect",
        _fails_with(sqlite3.OperationalError("disk I/O error")),
    )


def _backup_fails(_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        regeneration.os, "fsync", _fails_with(OSError(errno.EIO, "I/O error"))
    )


def _no_verified_backup(_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        regeneration.SQLiteRegenerationSession, "back_up", lambda _self: None
    )


@pytest.mark.parametrize(
    "failure",
    [
        _check_violation,
        _authority_changed,
        _recovery_fails,
        _inspection_fails,
        _backup_fails,
        _no_verified_backup,
    ],
    ids=[
        "ledger_check",
        "authority_changed",
        "recovery",
        "inspection",
        "backup",
        "no_verified_backup",
    ],
)
def test_a_failure_the_command_does_not_name_prints_only_its_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: Callable[[Path, pytest.MonkeyPatch], None],
) -> None:
    """Through the container: R9 on stdout, nothing on stderr, no traceback
    and no internal text, exit 1, the database unchanged (review round 1,
    findings 4 and 5)."""
    path = _fresh(tmp_path / "data")
    _another_build(path)
    failure(path, monkeypatch)
    before = _sha(path)
    monkeypatch.setattr(container, "DATA", path.parent)
    monkeypatch.setattr(container, "DATABASE", path)
    monkeypatch.setattr(container, "CONFIG", path.parent / "config.json")
    with pytest.raises(SystemExit) as exited:
        container.main(["regenerate-derived"])
    assert exited.value.code == 1
    assert capsys.readouterr() == (cli.FAILED + "\n", "")
    assert _sha(path) == before
    assert _no_scratch_left(path)


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
    # A report was printed (one answer already written), so the result says
    # the answers are kept as listed (copy OK2).
    assert lines[-1] == cli.REGENERATED_AS_LISTED
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
    _fails_in_the_transaction(
        monkeypatch, phase, sqlite3.OperationalError("disk I/O error")
    )
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
        # Names SQLite does not reserve, though `LIKE 'sqlite_%'` matches
        # them: only the exact `sqlite_` prefix is SQLite's own.
        connection.executescript(
            "CREATE TABLE sqlitex(a); CREATE INDEX sqlitey ON human_decisions(ordinal);"
        )
        objects = connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT GLOB 'sqlite_*' ORDER BY rowid"
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
        "sqlitex",
        "sqlitey",
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
    assert {
        ("trigger", "human_decisions_previous_attention"),
        ("table", "sqlitex"),
        ("index", "sqlitey"),
    } <= {(k, n) for k, n, _, _ in planted}
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


def _stage(owner: OwnerStore, plan_id: str, state: str) -> None:
    """Move a planned write to `state` through the real writer, as the
    applier leaves it when the server stops."""
    if state == "planned":
        return
    if state == "connection":
        owner.repository.transition_field_effect(
            plan_id, "planned", "stale", owner.clock(), "connection"
        )
        return
    owner.repository.claim_field_effect(plan_id, "planned", "c" * 64, owner.clock())
    if state in {"applying", "reconciling"}:
        owner.repository.transition_field_effect(
            plan_id, "claimed", state, owner.clock()
        )
    elif state == "retryable":
        owner.repository.transition_field_effect(
            plan_id, "claimed", "retryable", owner.clock(), "not_sent"
        )


def _counts(path: Path) -> dict[str, int]:
    connection = sqlite3.connect(path)
    try:
        (document,) = connection.execute(
            "SELECT counts_document FROM derived_regenerations"
        ).fetchone()
    finally:
        connection.close()
    counts: dict[str, int] = json.loads(document)
    return counts


@pytest.mark.parametrize(
    "state",
    ["planned", "claimed", "retryable", "connection"],
    ids=["planned", "claimed", "retryable-not-sent", "connection-stale"],
)
def test_a_waiting_write_blocks_until_it_is_retired(
    owner: OwnerStore, state: str
) -> None:
    """Without the flag the waiting write stops the run after the report
    says what the flag retires (ADR-0108 step 4: confirmation after reading
    the report); with it the write closes superseded through the revocation
    (a claimed one first goes back to retryable, not sent), the same report
    prints as done, and the answer it would have written stays the record's,
    retired and no longer carried."""
    decision = owner.decide("accept", GENRES, destination="genres")
    operation = owner.plan(decision)
    _stage(owner, operation.plan.plan_id, state)
    target = owner.case.target
    path = _closed(owner)
    _, lines, outcome = _refuses_unchanged(path)
    assert outcome.kind == "report"
    (item,) = outcome.items
    assert (item.reason, item.destination) == (Reason.WAITING, "genres")
    library = f"{target['connection']}/{target['library']}"
    assert lines == [
        cli.STILL_WAITING,
        f"{cli.REASONS[Reason.WAITING]} {cli.READ_ACTIVITY}",
        f"  {library}, work {target['item']}, Genres",
        library,
        "  " + cli.RETIRES.format(n=1),
        f"    work {target['item']}, Genres",
        cli.UPPER_LIMITS,
        cli.GO_AHEAD,
    ]
    before = _ledger(path)
    status, lines, _ = _run(path, retire_pending=True)
    assert (status, lines) == (
        0,
        [
            library,
            "  " + cli.RETIRED.format(n=1),
            f"    work {target['item']}, Genres",
            cli.UPPER_LIMITS,
            cli.REGENERATED_AS_LISTED,
        ],
    )
    assert opens(path)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT state,json_extract(document,'$.failure') FROM field_effects"
        ).fetchall() == [("superseded", "authority_superseded")]
        kinds = [
            kind
            for (kind,) in connection.execute(
                "SELECT kind FROM field_effect_events ORDER BY sequence"
            )
        ]
        assert kinds[-1] == "superseded"
        if state == "claimed":
            assert kinds[-3:] == ["claimed", "retryable", "superseded"]
        assert connection.execute(
            "SELECT answer_id,carry_eligible FROM owner_field_answers"
        ).fetchall() == [(decision.decision_id, 0)]
        assert connection.execute(
            "SELECT kind,answer_id,request_id FROM owner_field_events"
        ).fetchall() == [("retired", decision.decision_id, None)]
        assert_record_recomputes(connection)
    finally:
        connection.close()
    # Every other ledger row is as it was: the decision and its plan's start.
    after = _ledger(path)
    assert after["human_decisions"] == before["human_decisions"]
    counts = _counts(path)
    assert {key: counts[key] for key in regeneration._RETIREMENT_KEYS} == {
        "answers_retired": 1,
        "plans_released": int(state == "claimed"),
        "plans_retired": 1,
        "policy_transitions_settled": 0,
        "resume_requests_closed": 0,
    }


def test_without_the_flag_nothing_is_retired(owner: OwnerStore) -> None:
    """A run with nothing waiting writes no retirement: the record keeps its
    answer carry-eligible and the audit counts none."""
    decision = owner.decide("accept", GENRES, destination="genres")
    path = _closed(owner)
    assert _run(path)[0] == 0
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT answer_id,carry_eligible FROM owner_field_answers"
        ).fetchall() == [(decision.decision_id, 1)]
        assert connection.execute(
            "SELECT count(*) FROM owner_field_events"
        ).fetchone() == (0,)
    finally:
        connection.close()
    assert not any(_counts(path)[key] for key in regeneration._RETIREMENT_KEYS)


@pytest.mark.parametrize(
    "state",
    ["applying", "reconciling", "retryable-after-applying"],
    ids=["applying", "reconciling", "retryable-after-applying"],
)
def test_a_write_that_reached_audiobookshelf_blocks_even_with_the_flag(
    owner: OwnerStore, state: str
) -> None:
    operation = owner.plan(owner.decide("accept", GENRES, destination="genres"))
    if state == "retryable-after-applying":
        owner.target.fail_next = True
        assert owner.service.apply(operation.plan.plan_id).state == "retryable"
    else:
        _stage(owner, operation.plan.plan_id, state)
    target = owner.case.target
    path = _closed(owner)
    for flag in (False, True):
        _, lines, outcome = _refuses_unchanged(path, retire_pending=flag)
        assert outcome.kind == "gate"
        assert [item.reason for item in outcome.items] == [Reason.MAY_HAVE_RECEIVED]
        assert lines == [
            cli.MUST_FINISH,
            f"{cli.REASONS[Reason.MAY_HAVE_RECEIVED]} {cli.FINISH_REACHABLE}",
            f"  {target['connection']}/{target['library']}, work {target['item']}, "
            "Genres",
        ]


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
    # The causes follow the reason; a library no longer configured can never
    # finish, so the line ends by asking for it back.
    assert lines[1] == (
        "A write is waiting to be sent. Writes are paused. Preparation is "
        "paused. This library is no longer in the settings. Add the library "
        "back in Settings on the previous build."
    )
    # In store mode, a store with no settings names no library reason.
    _, lines, stored = _refuses_unchanged(path)
    assert stored.items[0].causes == (Cause.WRITES_PAUSED, Cause.PREPARATION_PAUSED)
    assert lines[1].endswith("Preparation is paused. " + cli.READ_ACTIVITY)


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


def test_the_waiting_items_render_grouped_with_their_action_last() -> None:
    """One line per reason and causes, its action last, then the works it
    names (copy v2): repeated items do not repeat the sentence, and a policy
    check names no work."""
    waiting = regeneration.BlockingItem(
        Reason.WAITING, (), "home", "Books", "item-1", "title"
    )
    lines: list[str] = []
    cli.render(
        Outcome(
            "report",
            items=(
                waiting,
                regeneration.BlockingItem(
                    Reason.RESUME, (), "home", "Books", "item-1", "description"
                ),
                dataclasses.replace(waiting, item_id="item-2", destination="series"),
                regeneration.BlockingItem(Reason.POLICY, (Cause.PREPARATION_PAUSED,)),
            ),
        ),
        lines.append,
    )
    assert [line.rstrip("\n") for line in lines] == [
        cli.STILL_WAITING,
        "A write is waiting to be sent. Start the previous build and read Activity.",
        "  home/Books, work item-1, Title",
        "  home/Books, work item-2, Series",
        "Resume automatic handling is waiting for its scan. Start the previous "
        "build and read Activity.",
        "  home/Books, work item-1, Description",
        "A policy change is still checking works. Preparation is paused. Start "
        "the previous build and read Activity.",
        cli.GO_AHEAD,
    ]


# --- The report and the decision rule ------------------------------------------------


class _Session:
    """A session that admits a database of another build and records what
    the run wrote."""

    def __init__(self, inspection: Inspection) -> None:
        self.inspection = inspection
        self.written: list[str] = []

    def derived_is_current(self) -> bool:
        return False

    def free_space(self) -> tuple[int, int]:
        return 1, 0

    def store_libraries(self) -> None:
        return None

    def inspect(self, _configured: object) -> Inspection:
        return self.inspection

    def back_up(self) -> None:
        self.written.append("backup")

    def regenerate(self, *, retire_pending: bool, inspection: Inspection) -> None:
        assert inspection is self.inspection
        self.written.append(f"regenerate retire_pending={retire_pending}")

    def close(self) -> None:
        pass


class _Store:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def admit(self) -> _Session:
        return self.session


_SENT = BlockingItem(Reason.MAY_HAVE_RECEIVED, (), "home", "Books", "item-1", "title")
_WAITING = BlockingItem(Reason.WAITING, (), "home", "Books", "item-2", "genres")
_AUTHORED = LibraryReport(
    "home", "Books", authored=(regeneration.WorkField("item-3", "description"),)
)
_ONLY_LIST_1 = LibraryReport(
    "home", "Books", unwritten=(regeneration.WorkField("item-4", "series"),)
)


@pytest.mark.parametrize(
    ("blocking", "retirable", "report", "flag", "kind", "items"),
    [
        ((_SENT,), (_WAITING,), _AUTHORED, False, "gate", (_SENT,)),
        ((_SENT,), (_WAITING,), _AUTHORED, True, "gate", (_SENT,)),
        ((), (_WAITING,), _ONLY_LIST_1, False, "report", (_WAITING,)),
        ((), (), _AUTHORED, False, "report", ()),
        ((), (_WAITING,), _AUTHORED, True, "regenerated", ()),
        ((), (), _ONLY_LIST_1, False, "regenerated", ()),
    ],
    ids=[
        "sent_write_stops_without_the_flag",
        "sent_write_stops_with_the_flag",
        "waiting_write_stops_for_the_report",
        "list_3_stops_for_the_report",
        "flag_confirms_the_report",
        "nothing_to_confirm_goes_ahead",
    ],
)
def test_the_decision_rule(
    blocking: tuple[BlockingItem, ...],
    retirable: tuple[BlockingItem, ...],
    report: LibraryReport,
    flag: bool,
    kind: str,
    items: tuple[BlockingItem, ...],
) -> None:
    """ADR-0108 step 4: a write that may have reached Audiobookshelf stops
    the run, flag or not, and only it is named; without the flag, anything
    the flag would retire, and lists 3 to 5, stop the run with the report, so
    the flag only confirms what was printed; nothing is written unless the
    run goes ahead."""
    session = _Session(Inspection(blocking, retirable, (report,), False))
    outcome = regenerate_derived(
        _Store(session), retire_pending=flag, configuration=_store_mode
    )
    assert (outcome.kind, outcome.items) == (kind, items)
    if kind == "regenerated":
        assert session.written == ["backup", f"regenerate retire_pending={flag}"]
    else:
        assert session.written == []
    if kind == "report":
        assert outcome.reports == (report,)


def test_an_unwritten_answer_is_reported_and_the_run_goes_ahead(
    owner: OwnerStore,
) -> None:
    owner.decide("accept", GENRES, destination="genres")
    target = owner.case.target
    path = _closed(owner)
    status, lines, _ = _run(path)
    assert status == 0
    # A count of 0 is left out, and list 1 is a count (copy v2 section 7).
    assert lines == [
        f"{target['connection']}/{target['library']}",
        "  " + cli.UNWRITTEN.format(n=1),
        cli.UPPER_LIMITS,
        cli.REGENERATED_AS_LISTED,
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
    assert (status, lines[-1]) == (0, cli.REGENERATED_AS_LISTED)
    assert "  " + cli.STOPPED.format(n=1) in lines
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
            regeneration.CoverPointer("item-5", True),
        ),
    )
    assert report.stops_without_flag
    cli.render(Outcome("report", reports=(report,), backup_held=True), lines.append)
    # No waiting item, so no gate heading; covers are grouped by what each
    # keeps, each group's sentence once.
    assert [line.rstrip("\n") for line in lines] == [
        "home/Books",
        "  " + cli.AUTHORED.format(n=1),
        "    work item-2, Description",
        "  " + cli.COVERS.format(n=3),
        "    " + cli.COVER_KEPT,
        "      work item-3",
        "      work item-5",
        "    " + cli.COVER_NONE,
        "      work item-4",
        cli.UPPER_LIMITS,
        cli.REPLACES,
        cli.GO_AHEAD,
    ]


@pytest.mark.parametrize(
    ("retire_pending", "unwritten_line"),
    [(True, None), (False, "  " + cli.UNWRITTEN.format(n=2))],
    ids=["with_the_flag", "without_the_flag"],
)
def test_a_run_that_went_ahead_prints_the_report_done(
    retire_pending: bool, unwritten_line: str | None
) -> None:
    """After the run the report says what was done: retired, not retires;
    the earlier restore point was replaced; and the result says the answers
    are kept as listed (copy L1-flag-done, F2-done, OK2). Without the flag
    nothing was retired, so list 1 counts every unwritten answer."""
    entry = regeneration.WorkField("item-1", "title")
    report = regeneration.LibraryReport(
        "home",
        "Books",
        unwritten=(entry, regeneration.WorkField("item-2", "series")),
        retired=(entry,),
    )
    lines: list[str] = []
    status = cli.render(
        Outcome(
            "regenerated",
            reports=(report,),
            backup_held=True,
            retire_pending=retire_pending,
        ),
        lines.append,
    )
    middle = (
        [unwritten_line]
        if unwritten_line
        else [
            "  " + cli.UNWRITTEN.format(n=1),
            "  " + cli.RETIRED.format(n=1),
            "    work item-1, Title",
        ]
    )
    assert (status, [line.rstrip("\n") for line in lines]) == (
        0,
        [
            "home/Books",
            *middle,
            cli.UPPER_LIMITS,
            cli.REPLACED,
            cli.REGENERATED_AS_LISTED,
        ],
    )


def test_a_run_with_no_report_says_every_answer_is_kept() -> None:
    """OK1 only when no report was printed: a library with nothing to list
    prints nothing, and the replaced restore point is still said."""
    lines: list[str] = []
    cli.render(
        Outcome(
            "regenerated",
            reports=(regeneration.LibraryReport("home", "Books"),),
            backup_held=True,
        ),
        lines.append,
    )
    assert lines == [cli.REPLACED + "\n", cli.REGENERATED + "\n"]


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


def test_a_policy_change_still_checking_settles_with_the_flag(tmp_path: Path) -> None:
    """A policy activation's transition still processing, made by the real
    activation, refuses without the flag; with it the transition settles from
    its header, every unfinished work counted failed, and the store opens."""
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )
    from tests.unit.test_metadata_policy_activation import (
        _current_case_with_completed_work,
        _draft,
    )

    path = tmp_path / "policy.sqlite"
    repository, report, _, _ = _current_case_with_completed_work(path)
    try:
        draft = _draft()
        repository.create_metadata_policy_draft(draft.canonical_json)
        resolved = resolve_policy(draft, report.target.library)
        transition = repository.activate_metadata_policy(
            draft.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            30,
            expected_active_generation=1,
        )
        assert transition is not None
        assert (transition.state, transition.completed, transition.total) == (
            "processing",
            0,
            1,
        )
    finally:
        repository.close()
    _another_build(path)
    _, lines, outcome = _refuses_unchanged(path)
    assert [item.reason for item in outcome.items] == [Reason.POLICY]
    assert lines == [
        cli.STILL_WAITING,
        f"{cli.REASONS[Reason.POLICY]} {cli.READ_ACTIVITY}",
        cli.GO_AHEAD,
    ]
    status, lines, _ = _run(path, retire_pending=True)
    assert (status, lines) == (0, [cli.REGENERATED])
    reopened = open_repository(path, create=False)
    try:
        settled = reopened.metadata_policy_transition()
        assert settled is not None
        assert (
            settled.transition_id,
            settled.state,
            settled.completed,
            settled.failed,
            settled.total,
        ) == (transition.transition_id, "completed_with_failures", 0, 1, 1)
    finally:
        reopened.close()
    assert _counts(path)["policy_transitions_settled"] == 1


def test_the_sweep_keeps_every_file_while_the_restore_point_is_kept(
    owner: OwnerStore,
) -> None:
    """After a regeneration the artifact sweep removes nothing while
    `regeneration-backup` holds the copy, and removes an unreferenced file
    once the owner deletes the folder."""
    import shutil

    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )

    owner.decide("accept", GENRES, destination="genres")
    path = _closed(owner)
    assert _run(path)[0] == 0
    backup = path.parent / "regeneration-backup"
    assert os.listdir(backup) == ["epoch-0.sqlite3"]
    store = CoverArtifactStore(path.parent / "artifacts")
    stray = path.parent / "artifacts" / ("e" * 64)
    stray.write_bytes(b"left by an earlier epoch")
    stray.chmod(0o600)

    def sweep() -> None:
        repository = open_repository(path, create=False)
        try:
            store.recover_unreferenced(repository.artifact_is_referenced)
        finally:
            repository.close()

    sweep()
    assert stray.exists()
    shutil.rmtree(backup)
    sweep()
    assert not stray.exists()


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
    start = lines.index("  " + cli.COVERS.format(n=2))
    assert lines[start : start + 4] == [
        "  " + cli.COVERS.format(n=2),
        "    " + cli.COVER_NONE,
        *(f"      work {item}" for item in items),
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


def test_the_ledger_references_the_owners_covers() -> None:
    """On rows put straight in, the sweep's ledger references: a pointer's
    selection, a cover answer the record holds and each work's latest written
    selection are kept by original and preview, and a waiting cover plan by
    its desired original; an earlier written selection a later one replaced,
    an unwritten one nothing names and a closed plan's value are not."""
    from mechanical_librarian.adapters.persistence import reports

    connection = sqlite3.connect(":memory:")
    schema._register_casefold(connection)
    schema._create_schema(connection)
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'"
    ).fetchall():
        connection.execute(f'DROP TRIGGER "{name}"')
    connection.execute("PRAGMA foreign_keys=OFF")
    ordinal = iter(range(1, 100))

    def choice(item: str, tag: str, *, written: bool = False) -> str:
        decision = "human_decision_" + tag * 64
        selection = "cover_selection_" + tag * 64
        connection.execute(
            "INSERT INTO human_decisions(ordinal,decision_id,review_case_id,proposal_id,"
            "target_fingerprint,destination,actor,outcome,selected_value_document,"
            "idempotency_digest,request_digest,occurred_at,document,connection_name,"
            "library_name,item_id,target_field_document) VALUES(?,?,?,?,?,'cover',"
            "'owner','accept','null',?,?,1,'{}','home','Books',?,'null')",
            (
                next(ordinal),
                decision,
                "review_case_" + "0" * 64,
                "review_proposal_" + "0" * 64,
                "0" * 64,
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
                json.dumps(
                    {
                        "schema_version": COVER_SELECTION_AUTHORITY_SCHEMA,
                        "candidate_artifact_id": "cover_" + tag * 64,
                        "original_sha256": tag * 64,
                        "preview_sha256": tag * 63 + "f",
                    }
                ),
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

    connection.execute(
        "INSERT INTO owner_cover_pointers VALUES('home','Books','pointed',?)",
        (choice("pointed", "1"),),
    )
    connection.execute(
        "INSERT INTO owner_field_answers(connection_name,library_name,item_id,"
        "destination,source,answer_id,ordinal,outcome,value_document,recorded_at,"
        "review_case_id,episode,carry_eligible,cover_selection_id) VALUES('home',"
        "'Books','answered','cover','human',?,50,'accept','null',1,?,0,1,?)",
        (
            "human_decision_" + "2" * 64,
            "review_case_" + "0" * 64,
            choice("answered", "2"),
        ),
    )
    choice("written", "3", written=True)
    choice("written", "4", written=True)
    choice("unnamed", "5")
    for state, tag in (("planned", "6"), ("superseded", "7")):
        connection.execute(
            "INSERT INTO field_effects(plan_id,decision_id,connection_name,"
            "library_name,work_id,destination,state,attempt,updated_at,document) "
            "VALUES(?,?,'home','Books','planned','cover',?,0,1,?)",
            (
                "effect_plan_" + tag * 64,
                "automatic_decision_" + tag * 64,
                state,
                json.dumps({"plan": {"desired_value": "cover_" + tag * 64}}),
            ),
        )

    def referenced(digest: str) -> bool:
        return (
            connection.execute(
                reports._LEDGER_COVER_REFERENCE_SQL,
                {"artifact": "cover_" + digest, "digest": digest},
            ).fetchone()
            is not None
        )

    for tag in "124":
        assert referenced(tag * 64) and referenced(tag * 63 + "f"), tag
    assert referenced("6" * 64)
    for tag in "357":
        assert not referenced(tag * 64) and not referenced(tag * 63 + "f"), tag


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
