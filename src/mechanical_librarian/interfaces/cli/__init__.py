"""Minimal command-line entrypoint."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


def _parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="mechanical-librarian",
        description="Mechanical Librarian (pre-release foundation)",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the empty CLI surface and return success."""

    _parser().parse_args(argv)
    return 0


__all__ = ["main"]
