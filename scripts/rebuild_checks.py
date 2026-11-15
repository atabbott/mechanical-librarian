"""Run the read-only workbench checks over one folder of copies.

A workbench instrument, deliberately not part of the installed product: run it
from the checkout an install was built from, against a folder holding copies
of that install's files under these names (where each comes from is in
``docs/operations/rebuild-checks.md``):

- ``application.sqlite3`` (required);
- ``catalogue.sqlite3``;
- ``config.json`` when the install has one, otherwise ``review-bootstrap.json``;
- ``provider-recovery.sqlite``.

Each check runs in this process through its own ``main`` and its output is
saved under a fixed name in a new owner-only folder:

- ``cold-start-report.json`` from ``cold_start_report.py``, with ``--config``
  when the copies hold ``config.json``, otherwise ``--bootstrap``;
- ``work-route-measurement.json`` and ``work-route-provenance.json`` from
  ``work_route_measurement.py``, plain and with ``--provenance``;
- ``hardcover-series-sample.txt`` from ``hardcover_series_sample.py``, with the
  address the install's own settings file records;
- ``partial-author-replay.json`` from ``partial_author_replay.py`` when this
  checkout has that script; without it the check is skipped, not failed.

One failing check does not stop the others, and a failed check leaves no
file. Every line printed is also saved as ``summary.txt``. The exit status is
0 when every check finished and 1 otherwise. No check uses the network, and
nothing is written outside the output folder, except that reading the
catalogue copy can leave ``catalogue.sqlite3-shm`` and ``catalogue.sqlite3-wal``
beside it.

Usage:
    uv run python scripts/rebuild_checks.py \
        --copies /copies --output /results \
        [--mount-root books=/host/path/to/mount ...] [--limit 50]
"""

from __future__ import annotations

import argparse
import importlib
import io
import os
import sys
from collections.abc import Callable
from contextlib import redirect_stderr
from pathlib import Path
from types import ModuleType
from typing import TextIO

from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.settings_authority import (
    load_review_bootstrap,
)

# Direct execution puts scripts/ on sys.path; the test import resolves the
# checks as the scripts namespace package.
try:
    from scripts import (
        cold_start_report,
        hardcover_series_sample,
        work_route_measurement,
    )
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import cold_start_report  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    import hardcover_series_sample  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    import work_route_measurement  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

_DESCRIPTION = (
    "Run the read-only workbench checks over a folder of copies and write each "
    "result to a new owner-only folder."
)

COLD_START_FILE = "cold-start-report.json"
WORK_ROUTE_FILE = "work-route-measurement.json"
PROVENANCE_FILE = "work-route-provenance.json"
HARDCOVER_FILE = "hardcover-series-sample.txt"
REPLAY_FILE = "partial-author-replay.json"
SUMMARY_FILE = "summary.txt"

_NO_COPIES = (
    "No folder at the --copies path. Give it the folder that holds your copies."
)
_NO_APPLICATION = (
    "The --copies folder has no application.sqlite3. Copy it there and run again."
)
_OUTPUT_EXISTS = (
    "The --output folder already exists. Give a new folder name for this run."
)
_OUTPUT_UNCREATABLE = (
    "Cannot create the --output folder. Give a path inside a folder you own."
)

_Main = Callable[[list[str], TextIO], int]


def _partial_author_replay() -> ModuleType | None:
    """The partial-author replay script, or None when this checkout lacks it.

    Only the replay module itself being absent means the check is skipped; a
    failure inside it, or a module it needs being absent, is raised.
    """
    for name in ("scripts.partial_author_replay", "partial_author_replay"):
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name not in {name, "scripts"}:
                raise
    return None


def _stderr_message(captured: str) -> str | None:
    if "error: " in captured:
        message = captured.rpartition("error: ")[2].strip()
        if message:
            return message
    lines = [line.strip() for line in captured.splitlines() if line.strip()]
    return lines[-1] if lines else None


def _run(main: _Main, argv: list[str], file: str, script: str) -> tuple[str, str]:
    """Run one check in this process: its output, or the line saying why not."""
    output = io.StringIO()
    captured = io.StringIO()
    try:
        with redirect_stderr(captured):
            code = main(argv, output)
    except SystemExit as stop:
        if isinstance(stop.code, str):
            return "", f"Did not write {file}: {stop.code}"
        message = _stderr_message(captured.getvalue())
        if message is not None:
            return "", f"Did not write {file}: {message}"
        status = 0 if stop.code is None else stop.code
        return "", f"Did not write {file}: it stopped with exit code {status}."
    except Exception as error:
        return "", (
            f"Did not write {file}: it stopped with {type(error).__name__}. "
            f"Run scripts/{script}.py on its own to see why."
        )
    if code != 0:
        return "", f"Did not write {file}: it stopped with exit code {code}."
    return output.getvalue(), ""


def _save(folder: Path, file: str, text: str) -> str:
    """Save one result owner-only; the line saying so, or why not."""
    path = folder / file
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except OSError as error:
        return f"Did not write {file}: saving it stopped with {type(error).__name__}."
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
    except OSError as error:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        return f"Did not write {file}: saving it stopped with {type(error).__name__}."
    try:
        with stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(text)
    except OSError as error:
        try:
            path.unlink()
        except OSError:
            pass
        return f"Did not write {file}: saving it stopped with {type(error).__name__}."
    return f"Wrote {file}."


def _origin(settings: tuple[str, Path]) -> str | None:
    """The address the install's settings file records, or None if unreadable."""
    option, path = settings
    try:
        if option == "--config":
            review = load_configuration_file(path).review
            return None if review is None else review.canonical_origin
        return load_review_bootstrap(path).canonical_origin
    except (ConfigurationError, OSError):
        return None


def _create_output(copies: Path, output: Path) -> None:
    if not copies.is_dir():
        raise SystemExit(_NO_COPIES)
    if not (copies / "application.sqlite3").is_file():
        raise SystemExit(_NO_APPLICATION)
    if os.path.lexists(output):
        raise SystemExit(_OUTPUT_EXISTS)
    try:
        os.mkdir(output, 0o700)
        os.chmod(output, 0o700)
    except OSError as error:
        raise SystemExit(_OUTPUT_UNCREATABLE) from error


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument(
        "--copies", required=True, type=Path, help="the folder that holds your copies"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="a new folder for the results"
    )
    parser.add_argument(
        "--mount-root",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="map a media mount to a folder on this computer, for the cold-start report",
    )
    parser.add_argument("--limit", help="how many Hardcover links to list (default 50)")
    arguments = parser.parse_args(argv)
    printed = sys.stdout if stdout is None else stdout
    copies: Path = arguments.copies
    folder: Path = arguments.output
    _create_output(copies, folder)

    application = str(copies / "application.sqlite3")
    settings: tuple[str, Path] | None = None
    if os.path.lexists(copies / "config.json"):
        settings = ("--config", copies / "config.json")
    elif (copies / "review-bootstrap.json").exists():
        settings = ("--bootstrap", copies / "review-bootstrap.json")
    no_settings = (
        "the --copies folder has neither config.json nor review-bootstrap.json."
    )

    lines: list[str] = []
    failed = 0
    total = 0

    def report(line: str, *, counted: bool = True, failure: bool = False) -> None:
        nonlocal failed, total
        if counted:
            total += 1
        if failure:
            failed += 1
        lines.append(line)
        printed.write(line + "\n")
        printed.flush()

    def check(main_: _Main, argv: list[str], file: str, script: str) -> None:
        text, refusal = _run(main_, argv, file, script)
        line = refusal or _save(folder, file, text)
        report(line, failure=not line.startswith("Wrote "))

    if settings is None:
        report(f"Did not write {COLD_START_FILE}: {no_settings}", failure=True)
    else:
        cold_start = [
            "--database",
            application,
            "--catalogue",
            str(copies / "catalogue.sqlite3"),
            settings[0],
            str(settings[1]),
        ]
        recovery = copies / "provider-recovery.sqlite"
        if recovery.exists():
            cold_start += ["--recovery-database", str(recovery)]
        for value in arguments.mount_root:
            cold_start += ["--mount-root", value]
        check(cold_start_report.main, cold_start, COLD_START_FILE, "cold_start_report")

    check(
        work_route_measurement.main,
        ["--database", application],
        WORK_ROUTE_FILE,
        "work_route_measurement",
    )
    check(
        work_route_measurement.main,
        ["--database", application, "--provenance"],
        PROVENANCE_FILE,
        "work_route_measurement",
    )

    if settings is None:
        report(f"Did not write {HARDCOVER_FILE}: {no_settings}", failure=True)
    else:
        origin = _origin(settings)
        if origin is None:
            report(
                f"Did not write {HARDCOVER_FILE}: this checkout cannot read the "
                f"address in {settings[1].name}.",
                failure=True,
            )
        else:
            hardcover = ["--database", application, "--origin", origin]
            if arguments.limit is not None:
                hardcover += ["--limit", arguments.limit]
            check(
                hardcover_series_sample.main,
                hardcover,
                HARDCOVER_FILE,
                "hardcover_series_sample",
            )

    try:
        replay = _partial_author_replay()
    except Exception as error:
        report(
            f"Did not write {REPLAY_FILE}: it stopped with {type(error).__name__}. "
            "Run scripts/partial_author_replay.py on its own to see why.",
            failure=True,
        )
    else:
        if replay is None:
            report(
                f"Skipped {REPLAY_FILE}: this checkout has no "
                "scripts/partial_author_replay.py.",
                counted=False,
            )
        else:
            module = replay
            check(
                lambda argv, out: int(module.main(argv, out)),
                ["--database", application],
                REPLAY_FILE,
                "partial_author_replay",
            )

    if failed:
        report(
            f"{failed} of {total} checks did not finish. "
            f"The results that were written are in {folder}.",
            counted=False,
        )
    else:
        report(f"Every check finished. The results are in {folder}.", counted=False)
    saved = _save(folder, SUMMARY_FILE, "\n".join(lines) + "\n")
    if not saved.startswith("Wrote "):
        printed.write(saved + "\n")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
