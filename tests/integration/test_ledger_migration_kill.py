"""A process killed during a ledger migration (ADR-0108 steps 5 and 6).

The child runs the real `regenerate-derived` over a copy of the frozen v1
ledger with a synthetic step registered in the child only. The step rewrites
the record's rows, then the child kills itself with SIGKILL on the step's
second call, inside the transaction, after the restore point was taken. The
retained copy holds the ledger as it was; startup names the command and
changes nothing; the rerun recovers the journal and completes the migration
with every decision, the record and its events as they were.
"""

from __future__ import annotations

import hashlib
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import LedgerMigrationRequired
from tests.support.ledger_chain import chain, identity
from tests.support.ledger_fixture import ledger_dump
from tests.unit.test_ledger_fixture import _WRITTEN_BY_A_RUN, _copy

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux inode binding")

_CHILD = """
import os, signal, sys
from pathlib import Path
from mechanical_librarian.adapters.persistence import (
    ledger_migrations, regeneration, schema,
)
from mechanical_librarian.adapters.persistence.ledger_migrations import LedgerMigration
from mechanical_librarian.adapters.persistence.regeneration import (
    SQLiteRegenerationStore,
)
from mechanical_librarian.application.regenerate_derived import regenerate_derived
calls = []
def migrate(connection):
    calls.append(None)
    killing = sys.argv[2] == "kill" and len(calls) == 2
    if killing:
        # Spilling every page syncs the journal and writes the database
        # itself, so only the journal's rollback restores it.
        connection.execute("PRAGMA cache_size=1")
    connection.execute("UPDATE owner_field_answers SET recorded_at=recorded_at")
    if killing:
        os.kill(os.getpid(), signal.SIGKILL)
ledger_migrations.LEDGER_MIGRATIONS = (LedgerMigration(1, migrate),)
schema.LEDGER_FORMAT_VERSION = 2
regeneration.LEDGER_FORMAT_VERSION = 2
def configuration():
    raise FileNotFoundError
outcome = regenerate_derived(
    SQLiteRegenerationStore(Path(sys.argv[1]), lambda: 1_800_000_000),
    retire_pending=False,
    configuration=configuration,
)
print(outcome.kind, len(calls))
"""


def _child(path: Path, mode: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _CHILD, str(path), mode],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _state(directory: Path) -> dict[str, str]:
    """Every file beside the database and in its folders, by content."""
    return {
        str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _kept(dump: dict[str, list[tuple[object, ...]]]) -> dict[str, object]:
    return {t: r for t, r in dump.items() if t not in _WRITTEN_BY_A_RUN}


def test_a_kill_during_the_migration_rolls_back_and_the_rerun_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _copy(tmp_path / "data")
    before = ledger_dump(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    killed = _child(path, "kill")
    assert killed.returncode == -signal.SIGKILL, killed.stderr
    (alias,) = path.parent.glob(".metadata-report-binding-*.sqlite")
    assert Path(f"{alias}-journal").stat().st_size > 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() != sha
    retained = path.parent / "regeneration-backup" / "epoch-1.sqlite3"
    connection = sqlite3.connect(f"file:{retained}?mode=ro", uri=True)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()
    assert ledger_dump(retained) == before
    # Startup under the build that migrates: it recovers nothing it would
    # have to write and names the command.
    chain(monkeypatch, identity(1), current=2)
    files = _state(path.parent)
    with pytest.raises(LedgerMigrationRequired):
        open_repository(path, create=False, recover_stale_bindings=True)
    assert _state(path.parent) == files
    assert alias.exists()
    monkeypatch.undo()
    rerun = _child(path, "complete")
    assert (rerun.returncode, rerun.stdout.split()) == (0, ["regenerated", "2"]), (
        rerun.stderr
    )
    assert not alias.exists()
    assert not list(path.parent.glob(".metadata-report-*"))
    after = ledger_dump(path)
    assert _kept(after) == _kept(before)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(
            "SELECT ledger_format_version FROM ledger_meta"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT ledger_format_version_before,ledger_format_version_after "
            "FROM derived_regenerations ORDER BY regeneration_id DESC LIMIT 1"
        ).fetchone() == (1, 2)
    finally:
        connection.close()
