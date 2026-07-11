"""Composition root for executable Mechanical Librarian processes."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from mechanical_librarian.adapters.persistence.audit import SQLiteAuditRepository
from mechanical_librarian.application.state_check import (
    CheckDurableState,
    StateCheckOutcome,
    StateCheckResult,
)
from mechanical_librarian.infrastructure.persistence.sqlite import (
    SchemaError,
    connect,
    migrate,
)
from mechanical_librarian.interfaces.cli import main as cli_main


def _run_state_check(database: Path) -> StateCheckResult:
    connection = None
    try:
        connection = connect(database)
        migrate(connection)
        return CheckDurableState(SQLiteAuditRepository(connection)).run()
    except SchemaError:
        return StateCheckResult(StateCheckOutcome.SETUP_FAILURE)
    finally:
        if connection is not None:
            connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Assemble the current process and delegate to its CLI interface."""

    return cli_main(argv, state_check_runner=_run_state_check)


__all__ = ["main"]
