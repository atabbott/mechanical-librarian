"""Composition root for executable Mechanical Librarian processes."""

from __future__ import annotations

from collections.abc import Sequence

from mechanical_librarian.interfaces.cli import main as cli_main


def main(argv: Sequence[str] | None = None) -> int:
    """Assemble the current process and delegate to its CLI interface."""

    return cli_main(argv)


__all__ = ["main"]
