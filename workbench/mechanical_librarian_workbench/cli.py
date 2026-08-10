"""Source-only presentation for the closed contributor workbench."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from .catalogue import CAPABILITIES


def main(argv: Sequence[str] | None = None, *, stdout: TextIO | None = None,
         stderr: TextIO | None = None) -> int:
    """Render the catalogue; scenarios are invoked from their typed modules."""
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    parser = argparse.ArgumentParser(prog="mechanical-librarian-workbench")
    parser.add_argument("--format", choices=("summary", "json"), default="summary")
    try:
        arguments = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as error:
        if error.code:
            errors.write("Workbench failed: invalid invocation.\n")
        return int(error.code)
    if arguments.format == "json":
        output.write("[" + ",".join(item.to_json() for item in CAPABILITIES) + "]\n")
    else:
        for item in CAPABILITIES:
            output.write(f"{item.name}: {item.kind}\n")
    return 0
