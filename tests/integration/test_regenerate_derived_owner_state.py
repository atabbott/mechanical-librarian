"""The owner's state across `regenerate-derived` (ADR-0108, #2040).

Each proof makes its store through the product's own writers, runs the real
command over it as an upgrade leaves it (the ledger names another derived
version) and reads the result through the real readers.
"""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

from mechanical_librarian.adapters.persistence.regeneration import (
    SQLiteRegenerationStore,
)
from mechanical_librarian.application.regenerate_derived import (
    Outcome,
    Reason,
    regenerate_derived,
)
from mechanical_librarian.interfaces.cli import regenerate as cli
from tests.support.ledger_partition import opens, rewrite
from tests.support.owner_field_record import assert_record_recomputes, request_resume
from tests.support.prepared_library import accept, prepared


def _store_mode() -> Any:
    raise FileNotFoundError


def _another_build(path: Path) -> None:
    """The ledger names a derived partition this build did not make, as an
    upgrade leaves it."""
    assert rewrite(path, "UPDATE ledger_meta SET derived_version=2") == 1


def _run(path: Path, *, retire_pending: bool = False) -> tuple[int, list[str], Outcome]:
    outcome = regenerate_derived(
        SQLiteRegenerationStore(path, lambda: 1_700_000_000),
        retire_pending=retire_pending,
        configuration=_store_mode,
    )
    out = io.StringIO()
    status = cli.render(outcome, out.write)
    return status, out.getvalue().splitlines(), outcome


def _rows(path: Path, sql: str) -> list[tuple[Any, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


# --- Resume, through the page's own request -----------------------------------


def test_an_open_resume_refuses_until_the_flag_closes_it_over_its_answer(
    tmp_path: Path,
) -> None:
    """A Resume the owner asked through the real request blocks the command;
    with `--retire-pending` it closes `resume_superseded` naming the answer
    it covered, the answer stays the record's, no longer carried, and the
    record still equals the ledger's fold."""
    with prepared(tmp_path, automatic=True) as library:
        root = library.root
        owner = accept(library.case(), "authors", "owner-answers-authors", 11)
        root.record_human_decision(owner)
        request = request_resume(root, library.case_id(), "authors", 12)
        assert request is not None
        target = library.case().target
        root.close()
        path = tmp_path / "application.sqlite3"
        _another_build(path)
        before = path.read_bytes()
        status, lines, outcome = _run(path)
        assert status == 1
        assert [item.reason for item in outcome.items] == [Reason.RESUME]
        assert lines == [
            cli.GATE_HEADING,
            f"  {target['connection']}/{target['library']}, work {target['item']}, "
            f"Authors: {cli.REASONS[Reason.RESUME]} {cli.NO_CAUSE}",
        ]
        assert path.read_bytes() == before
        assert not (tmp_path / "regeneration-backup").exists()
        status, lines, _ = _run(path, retire_pending=True)
        assert (status, lines[-1]) == (0, cli.REGENERATED), lines
        assert opens(path)
        assert _rows(
            path,
            "SELECT kind,request_id,answer_id FROM owner_field_events ORDER BY rowid",
        ) == [
            ("resume_requested", None, owner.decision_id),
            ("resume_superseded", request, owner.decision_id),
        ]
        assert _rows(
            path,
            "SELECT answer_id,open_resume_id,carry_eligible FROM owner_field_answers",
        ) == [(owner.decision_id, None, 0)]
        connection = sqlite3.connect(path)
        try:
            assert_record_recomputes(connection)
            (counts,) = connection.execute(
                "SELECT counts_document FROM derived_regenerations"
            ).fetchone()
        finally:
            connection.close()
        assert json.loads(counts)["resume_requests_closed"] == 1
