"""The deliberately small installed runtime command surface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from mechanical_librarian.application.configuration import ConfigurationError

ReviewServeRunner = Callable[[Path, Path, Path, Path, str, int], None]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid invocation")


def _port(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("invalid port") from error
    if str(port) != value or not 1 <= port <= 65_535:
        raise argparse.ArgumentTypeError("invalid port")
    return port


def _parser() -> _Parser:
    parser = _Parser(prog="mechanical-librarian", description="Mechanical Librarian runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    review_serve = commands.add_parser("review-serve", help="serve the authenticated owner review interface")
    review_serve.add_argument("--config", required=True, metavar="PATH", type=Path)
    review_serve.add_argument("--database", required=True, metavar="PATH", type=Path)
    review_serve.add_argument("--catalogue", required=True, metavar="PATH", type=Path)
    review_serve.add_argument("--artifacts", required=True, metavar="PATH", type=Path)
    review_serve.add_argument("--host", choices=("127.0.0.1", "::1", "0.0.0.0", "::"), default="127.0.0.1")
    review_serve.add_argument("--port", type=_port, default=8080)
    return parser


def main(argv: Sequence[str] | None = None, *, review_serve_runner: ReviewServeRunner | None = None,
         stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    """Run the one supported installed operation without exposing workbench tools."""
    del stdout
    errors = sys.stderr if stderr is None else stderr
    try:
        arguments = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except ValueError:
        errors.write("Review server failed: invalid invocation.\n")
        return 2
    try:
        if review_serve_runner is None:
            raise RuntimeError("runtime composition unavailable")
        review_serve_runner(arguments.config, arguments.database, arguments.catalogue, arguments.artifacts, arguments.host, arguments.port)
    except ConfigurationError:
        errors.write("Review server failed: configuration unavailable.\n")
        return 3
    except Exception:
        errors.write("Review server failed safely.\n")
        return 1
    return 0


__all__ = ["main"]
