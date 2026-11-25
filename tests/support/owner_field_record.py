"""Prove the stored record of the owner's answers is the ledger's fold (ADR-0108).

Every answer site writes `owner_field_answers` incrementally beside the
hold, and every hold Resume site writes Resume's `owner_field_events`.
`assert_record_recomputes` recomputes the whole record from the ledger alone,
the owner's answers and Resume's events together, and compares it with what
was stored, by the comparison open makes, `open_resume_id` included;
`assert_records_recompute` does that for every store a test left under its
directory, which the hold modules run at teardown so each of their scenarios
is checked, including stores no test opens again.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.owner_field_answers import (
    record_differences,
)

_SQLITE_HEADER = b"SQLite format 3\x00"
_SIDE_FILES = ("-wal", "-shm", "-journal")


def assert_record_recomputes(connection: sqlite3.Connection) -> None:
    """The stored record equals the fold of the ledger's owner answers and
    Resume's events, by the comparison open makes."""
    differing = record_differences(connection)
    assert not differing, f"record differs from the ledger's fold: {differing}"


def _stores(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.endswith(_SIDE_FILES):
            continue
        with path.open("rb") as file:
            if file.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER:
                yield path


def assert_store_recomputes(path: Path) -> bool:
    """Check the store at ``path``, opened read-only so the check changes
    nothing; False where it has no record (a predecessor schema a test
    built, or no store at all)."""
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        try:
            present = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='owner_field_answers'"
            ).fetchone()
        except sqlite3.DatabaseError:
            return False
        if present is None:
            return False
        assert_record_recomputes(connection)
        return True
    finally:
        connection.close()


def assert_records_recompute(root: Path) -> int:
    """Check every store under ``root``; returns how many had a record."""
    return sum(assert_store_recomputes(path) for path in _stores(root))


@pytest.fixture(autouse=True)
def record_recomputes(tmp_path: Path) -> Iterator[None]:
    """At teardown, every store the test left recomputes to its record.

    Autouse fixtures are set up first and torn down last, so the test's own
    stores are closed by then.
    """
    yield
    assert_records_recompute(tmp_path)
