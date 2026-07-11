"""Stable, value-free command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from mechanical_librarian.application.state_check import (
    StateCheckOutcome,
    StateCheckResult,
)


StateCheckRunner = Callable[[Path], StateCheckResult]

_FAILURE_MESSAGES = {
    StateCheckOutcome.INVALID: "State check failed: invalid state.\n",
    StateCheckOutcome.UNAVAILABLE: "State check failed: state is unavailable.\n",
    StateCheckOutcome.INTEGRITY_FAILURE: (
        "State check failed: state integrity could not be verified.\n"
    ),
    StateCheckOutcome.CORRUPT: "State check failed: state is corrupt or incompatible.\n",
    StateCheckOutcome.READBACK_MISMATCH: (
        "State check failed: audit readback did not match.\n"
    ),
    StateCheckOutcome.SETUP_FAILURE: "State check failed: state could not be verified.\n",
    StateCheckOutcome.INTERNAL_FAILURE: "State check failed: internal diagnostic error.\n",
}


def _database_path(value: str) -> Path:
    path = Path(value)
    if path == Path(":memory:"):
        raise argparse.ArgumentTypeError(
            "database must be a durable filesystem path"
        )
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mechanical-librarian",
        description="Mechanical Librarian operator diagnostics",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    state_check = commands.add_parser(
        "state-check",
        help="create or open state and verify durable audit readback",
    )
    state_check.add_argument(
        "--database", required=True, metavar="PATH", type=_database_path
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    state_check_runner: StateCheckRunner,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one parsed command without exposing paths or exception values."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    arguments = _parser().parse_args(argv)
    try:
        result = state_check_runner(arguments.database)
    except Exception:
        errors.write(_FAILURE_MESSAGES[StateCheckOutcome.INTERNAL_FAILURE])
        return 1
    if (
        type(result) is not StateCheckResult
        or type(result.outcome) is not StateCheckOutcome
    ):
        errors.write(_FAILURE_MESSAGES[StateCheckOutcome.INTERNAL_FAILURE])
        return 1
    if result.succeeded:
        output.write("State check passed.\n")
        return 0
    errors.write(
        _FAILURE_MESSAGES.get(
            result.outcome,
            _FAILURE_MESSAGES[StateCheckOutcome.INTERNAL_FAILURE],
        )
    )
    return 1


__all__ = ["StateCheckRunner", "main"]
