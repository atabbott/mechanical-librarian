"""Prove the stored record of the owner's answers is the ledger's fold (ADR-0108).

Every answer site writes `owner_field_answers` incrementally beside the
hold, and Resume's request, completion and failure write its
`owner_field_events`; `request_resume`, `complete_resume` and `resume` drive
those as the page and the scheduler do. `assert_record_recomputes` recomputes the whole record from the ledger alone,
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
from typing import Any

import pytest

from mechanical_librarian.adapters.persistence.owner_field_answers import (
    record_differences,
)

_SQLITE_HEADER = b"SQLite format 3\x00"


def insert_record_row(
    connection: sqlite3.Connection,
    review_case_id: str,
    destination: str,
    *,
    answer_id: str = "human_decision_" + "1" * 64,
    pin_fingerprint: str | None = None,
    cover_selection_id: str | None = None,
) -> None:
    """Put a record row for the field of the current case's book with no
    ledger answer behind it, for a unit test of one reader; a store left so
    no longer recomputes, so only modules without `record_recomputes` use
    it."""
    connection.execute(
        "INSERT INTO owner_field_answers(connection_name,library_name,item_id,"
        "destination,source,answer_id,ordinal,outcome,value_document,recorded_at,"
        "review_case_id,episode,carry_eligible,pin_fingerprint,cover_selection_id) "
        "SELECT connection_name,library_name,item_id,?,'human',?,"
        "(SELECT ordinal+1 FROM owner_answer_ordinal),'accept','null',0,"
        "review_case_id,0,1,?,? FROM current_review_case_lineages "
        "WHERE review_case_id=?",
        (destination, answer_id, pin_fingerprint, cover_selection_id, review_case_id),
    )
    connection.execute("UPDATE owner_answer_ordinal SET ordinal=ordinal+1")


def mark_cover_taken_over(connection: sqlite3.Connection, selection_id: str) -> None:
    """Mark the owner's cover selection as taking the cover over from
    Automatic, past the append-only guard, for a unit test of the readers of
    that fact; the guard is put back."""
    (guard,) = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' "
        "AND name='owner_cover_selections_no_update'"
    ).fetchone()
    connection.execute("DROP TRIGGER owner_cover_selections_no_update")
    connection.execute(
        "UPDATE owner_cover_selections SET cover_took_over=1 WHERE selection_id=?",
        (selection_id,),
    )
    connection.execute(guard)


def hold_token(repository: Any, review_case_id: str, destination: str) -> str | None:
    """The decision the case's active hold for the field names, which the
    case page, pins and withdrawal still read until C2b's packet B2."""
    row = repository._connection.execute(
        "SELECT json_extract(document,'$.decision_id') FROM field_effect_manual_holds "
        "WHERE review_case_id=? AND destination=? AND active=1",
        (review_case_id, destination),
    ).fetchone()
    return None if row is None else str(row[0])


def hold_context(
    repository: Any, review_case_id: str, destination: str
) -> tuple[str, str] | None:
    """The connection and library of a current case with an active hold for
    the field, as the hold readers B2 removes read it."""
    row = repository._connection.execute(
        "SELECT lineage.connection_name,lineage.library_name "
        "FROM current_review_case_lineages lineage "
        "JOIN field_effect_manual_holds hold ON hold.review_case_id=lineage.review_case_id "
        "WHERE lineage.review_case_id=? AND hold.destination=? AND hold.active=1",
        (review_case_id, destination),
    ).fetchone()
    return None if row is None else (str(row[0]), str(row[1]))


def request_resume(
    repository: Any,
    review_case_id: str,
    destination: str,
    at: int,
    *,
    scan_id: str | None = None,
) -> str | None:
    """Ask Resume of the field of the current case, as the page does: on the
    library's latest scan (one is requested where it has none), over the
    record's answer. Returns the open request, or None where it is refused."""
    context = repository.owner_field_resume_context(review_case_id, destination)
    if context is None:
        return None
    connection, library, answer = context
    if scan_id is None:
        latest = repository._connection.execute(
            "SELECT scan_id FROM review_preparation_scans WHERE connection_name=? "
            "AND library_name=? ORDER BY requested_at DESC,rowid DESC LIMIT 1",
            (connection, library),
        ).fetchone()
        scan_id = (
            str(latest[0])
            if latest is not None
            else repository.request_review_scan(
                connection, library, f"library-{library}", "a" * 64, at
            ).scan_id
        )
    if not repository.request_owner_field_resume(
        review_case_id, destination, answer, scan_id, at, at
    ):
        return None
    (request,) = repository._connection.execute(
        "SELECT record.open_resume_id FROM owner_field_answers record "
        "JOIN current_review_case_lineages lineage "
        "ON lineage.connection_name=record.connection_name "
        "AND lineage.library_name=record.library_name "
        "AND lineage.item_id=record.item_id "
        "WHERE lineage.review_case_id=? AND record.destination=?",
        (review_case_id, destination),
    ).fetchone()
    return str(request)


def complete_resume(repository: Any, request_id: str, at: int) -> bool:
    """Complete an open request as its scan's success does."""
    row = repository._connection.execute(
        "SELECT connection_name,library_name,item_id,destination "
        "FROM owner_field_events WHERE event_id=?",
        (request_id,),
    ).fetchone()
    assert row is not None
    return bool(repository.complete_owner_field_resume(*row, request_id, at))


def resume(repository: Any, review_case_id: str, destination: str, at: int) -> bool:
    """Resume the field of the current case and let its checks complete."""
    request = request_resume(repository, review_case_id, destination, at)
    return request is not None and complete_resume(repository, request, at)


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
