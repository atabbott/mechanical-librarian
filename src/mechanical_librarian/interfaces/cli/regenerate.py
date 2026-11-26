"""The container's argument vectors and `regenerate-derived`'s words.

The entrypoint accepts exactly three argument vectors (ADR-0108): none (the
server), `regenerate-derived` and `regenerate-derived --retire-pending`.
Every string the command prints is here, so the owner's choice among the
drafts is a change in one place. Items are named only by connection,
library, work item id and the field's label, never by a title, author, path
or value.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from mechanical_librarian.application.regenerate_derived import (
    BlockingItem,
    Cause,
    LibraryReport,
    Outcome,
    Reason,
    Refusal,
    WorkField,
)
from mechanical_librarian.domain.readable_activity import destination_label

USAGE = (
    "Usage: regenerate-derived [--retire-pending]. "
    "With no arguments the container starts the server."
)
COMMAND = "regenerate-derived"
RETIRE_PENDING = "--retire-pending"

REFUSALS = {
    Refusal.IN_USE: (
        "The server is still running on this volume. Stop it with docker "
        "compose down, then run regenerate-derived again."
    ),
    Refusal.NO_DATABASE: (
        "/data has no application database. Start the server to set one up."
    ),
    Refusal.INTEGRITY: (
        "The database failed SQLite's integrity check, so nothing was changed. "
        "Restore your backup of /data."
    ),
    Refusal.BACKUP_UNSAFE: (
        "/data/regeneration-backup is not a private folder Mechanical Librarian "
        "made. Move it out of /data, then run regenerate-derived again."
    ),
    Refusal.BACKUP_MISMATCH: (
        "The restore point did not match the database, so nothing was changed. "
        "Run regenerate-derived again."
    ),
    Refusal.ROLLED_BACK: (
        "Regeneration stopped and was rolled back, so the database is unchanged. "
        "Run regenerate-derived again."
    ),
    Refusal.LEDGER_ALTERED: (
        "The database's records do not match this build, so nothing was "
        "changed. Start the build that made it, or restore your backup of /data."
    ),
}
# Any other stop with nothing changed; nothing internal is ever printed.
FAILED = (
    "Regeneration could not continue, so nothing was changed. Run "
    "regenerate-derived again; if this repeats, restore your backup of /data."
)
NO_SPACE = (
    "The /data volume has {free} free and regenerate-derived needs {needed}. "
    "Free some space and run it again."
)
NO_SPACE_HELD = "Deleting /data/regeneration-backup frees the earlier restore point."
NOTHING_TO_DO = "Nothing to regenerate: this database already matches this build."

# The gate. H1 names what must finish first, even with the flag; H2 names,
# without the flag, what `--retire-pending` would retire, before the report.
MUST_FINISH = "Nothing was changed. These need to finish on the previous build first:"
STILL_WAITING = (
    "Nothing was changed. These are still waiting. Let them finish on the "
    "previous build, or retire them with regenerate-derived --retire-pending "
    "after reading the report below:"
)
REASONS = {
    Reason.MAY_HAVE_RECEIVED: "Audiobookshelf may have received this write.",
    Reason.WAITING: "A write is waiting to be sent.",
    Reason.RESUME: "Resume automatic handling is waiting for its scan.",
    Reason.POLICY: "A policy change is still checking works.",
}
CAUSES = {
    Cause.WRITES_PAUSED: "Writes are paused.",
    Cause.FIELD_SUSPENDED: "Writes to this field are suspended.",
    Cause.CONNECTION_SUSPENDED: "This connection is suspended.",
    Cause.PREPARATION_PAUSED: "Preparation is paused.",
    Cause.LIBRARY_NOT_CONFIGURED: "This library is no longer in the settings.",
}
# The one thing to do ends every group line.
FINISH_REACHABLE = (
    "Start the previous build with the connection reachable and let it finish."
)
ADD_LIBRARY_BACK = "Add the library back in Settings on the previous build."
READ_ACTIVITY = "Start the previous build and read Activity."

# The report; a list whose count is 0 is left out.
UNWRITTEN = (
    "Answers not yet written: {n}. Each is kept if a new reading asks the same "
    "question, and asked again otherwise."
)
RETIRES = (
    "Answers whose waiting write --retire-pending retires: {n}. Each is asked "
    "again, and Automatic stays off the field."
)
RETIRED = (
    "Answers whose waiting write was retired: {n}. Each is asked again, and "
    "Automatic stays off the field."
)
WRITTEN = "Answers already written: {n}."
AUTHORED = (
    "Descriptions you wrote that are not yet in Audiobookshelf: {n}. Each is "
    "asked again, and Automatic stays off the description."
)
COVERS = "Cover choices not yet written: {n}."
COVER_KEPT = "Keeps your last written cover:"
COVER_NONE = "No cover choice remains, and Automatic still leaves the cover alone:"
STOPPED = (
    "Writes stopped for your decision: {n}. Activity no longer counts them as "
    "needing you."
)
UPPER_LIMITS = (
    "Counts can include answers from earlier readings, so they are upper limits."
)
REPLACES = "The restore point already in /data/regeneration-backup will be replaced."
REPLACED = "The earlier restore point in /data/regeneration-backup was replaced."
GO_AHEAD = "Nothing was changed. To go ahead, run regenerate-derived --retire-pending."
# Success (owner's choice, option B): with no report printed, and after a
# printed report, whose lists say which answers are kept.
REGENERATED = (
    "Regenerated. Your answers, settings and write history are kept. Start the "
    "server to read every library again. Delete /data/regeneration-backup once "
    "you are satisfied; while it is there, unused cover files are kept."
)
REGENERATED_AS_LISTED = (
    "Regenerated. Your settings and write history are kept, and your answers as "
    "listed above. Start the server to read every library again. Delete "
    "/data/regeneration-backup once you are satisfied; while it is there, "
    "unused cover files are kept."
)


def parse_container_arguments(arguments: Sequence[str]) -> bool | None:
    """None for the server, False or True for `regenerate-derived` without or
    with `--retire-pending`; any other vector raises `SystemExit(2)` after one
    usage line, before anything is opened."""
    vector = list(arguments)
    if not vector:
        return None
    if vector == [COMMAND]:
        return False
    if vector == [COMMAND, RETIRE_PENDING]:
        return True
    sys.stderr.write(USAGE + "\n")
    raise SystemExit(2)


def _size(value: int) -> str:
    if value < 1024:
        return f"{value} bytes"
    scaled = float(value)
    for unit in ("KiB", "MiB", "GiB"):
        scaled /= 1024
        if scaled < 1024 or unit == "GiB":
            break
    return f"{scaled:.1f} {unit}"


def _work(entry: WorkField) -> str:
    return f"    work {entry.item_id}, {destination_label(entry.destination)}"


def _action(reason: Reason, causes: tuple[Cause, ...]) -> str:
    """The one thing to do: a library no longer configured can never finish
    until it is added back, whatever the item waits for."""
    if Cause.LIBRARY_NOT_CONFIGURED in causes:
        return ADD_LIBRARY_BACK
    if reason is Reason.MAY_HAVE_RECEIVED:
        return FINISH_REACHABLE
    return READ_ACTIVITY


def _gate_lines(items: tuple[BlockingItem, ...]) -> list[str]:
    """One line per reason and its causes, ending with what to do, then the
    items it names, in the order they were found."""
    groups: dict[tuple[Reason, tuple[Cause, ...]], list[BlockingItem]] = {}
    for item in items:
        groups.setdefault((item.reason, item.causes), []).append(item)
    lines: list[str] = []
    for (reason, causes), grouped in groups.items():
        sentences = [REASONS[reason], *(CAUSES[cause] for cause in causes)]
        lines.append(" ".join([*sentences, _action(reason, causes)]))
        for item in grouped:
            if item.connection is None:
                continue
            assert item.destination is not None
            lines.append(
                f"  {item.connection}/{item.library}, work {item.item_id}, "
                f"{destination_label(item.destination)}"
            )
    return lines


def _report_lines(report: LibraryReport, retired_line: str | None) -> list[str]:
    """One library's lists, each left out at 0. Given `retired_line`, list
    1's answers whose waiting write the flag retires are listed apart."""
    retired = report.retired if retired_line is not None else ()
    kept = [entry for entry in report.unwritten if entry not in retired]
    lines = [f"{report.connection}/{report.library}"]
    if kept:
        lines.append("  " + UNWRITTEN.format(n=len(kept)))
    if retired_line is not None and retired:
        lines.append("  " + retired_line.format(n=len(retired)))
        lines.extend(_work(e) for e in retired)
    if report.written:
        lines.append("  " + WRITTEN.format(n=report.written))
    if report.authored:
        lines.append("  " + AUTHORED.format(n=len(report.authored)))
        lines.extend(_work(e) for e in report.authored)
    if report.covers:
        lines.append("  " + COVERS.format(n=len(report.covers)))
        for heading, keeps in ((COVER_KEPT, True), (COVER_NONE, False)):
            items = [p.item_id for p in report.covers if p.keeps_written is keeps]
            if items:
                lines.append("    " + heading)
                lines.extend(f"      work {item}" for item in items)
    if report.stopped:
        lines.append("  " + STOPPED.format(n=len(report.stopped)))
        lines.extend(_work(e) for e in report.stopped)
    return lines


def _reports(outcome: Outcome, *, done: bool) -> list[str]:
    """The report, before the run (`done` false) or after it."""
    retired_line = RETIRED if outcome.retire_pending else (None if done else RETIRES)
    reports = [report for report in outcome.reports if not report.empty]
    lines = [line for report in reports for line in _report_lines(report, retired_line)]
    if reports:
        lines.append(UPPER_LIMITS)
    if outcome.backup_held:
        lines.append(REPLACED if done else REPLACES)
    return lines


def _regenerated(outcome: Outcome) -> list[str]:
    lines = _reports(outcome, done=True)
    printed = any(not report.empty for report in outcome.reports)
    return [*lines, REGENERATED_AS_LISTED if printed else REGENERATED]


def render(outcome: Outcome, write: Callable[[str], object]) -> int:
    """Print what happened, one line per call, and return the exit status:
    0 regenerated or nothing to do, 1 a refusal or a report."""
    lines: list[str]
    if outcome.kind == "refused":
        assert outcome.refusal is not None
        lines = [REFUSALS[outcome.refusal]]
    elif outcome.kind == "startup":
        assert outcome.message is not None
        lines = [outcome.message]
    elif outcome.kind == "failed":
        lines = [FAILED]
    elif outcome.kind == "nothing_to_do":
        lines = [NOTHING_TO_DO]
    elif outcome.kind == "no_space":
        message = NO_SPACE.format(
            free=_size(outcome.free), needed=_size(outcome.needed)
        )
        lines = [message + (" " + NO_SPACE_HELD if outcome.backup_held else "")]
    elif outcome.kind == "gate":
        lines = [MUST_FINISH, *_gate_lines(outcome.items)]
    elif outcome.kind == "report":
        waiting = [STILL_WAITING, *_gate_lines(outcome.items)] if outcome.items else []
        lines = [*waiting, *_reports(outcome, done=False), GO_AHEAD]
    else:
        lines = _regenerated(outcome)
    for line in lines:
        write(line + "\n")
    return 0 if outcome.kind in {"regenerated", "nothing_to_do"} else 1


__all__ = ["USAGE", "parse_container_arguments", "render"]
