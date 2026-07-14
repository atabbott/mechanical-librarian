"""Inert package boundary for executable composition."""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Load the broad composition root only for the ordinary application process."""

    from .composition import main as composed_main

    return composed_main(argv)


__all__ = ["main"]
