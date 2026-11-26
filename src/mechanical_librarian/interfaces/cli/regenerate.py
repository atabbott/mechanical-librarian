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
        "/data/regeneration-backup is not a private folder this container made. "
        "Move it out of /data, then run regenerate-derived again."
    ),
    Refusal.BACKUP_MISMATCH: (
        "The restore point did not match the database, so nothing was changed. "
        "Run regenerate-derived again."
    ),
    Refusal.ROLLED_BACK: (
        "Regeneration stopped and was rolled back, so nothing was changed. "
        "Run regenerate-derived again."
    ),
}
NO_SPACE = (
    "The /data volume has {free} free and regenerate-derived needs {needed}. "
    "Free some space and run it again."
)
NO_SPACE_HELD = "Deleting /data/regeneration-backup frees the earlier restore point."
NOTHING_TO_DO = "Nothing to regenerate: this database already matches this build."

GATE_HEADING = "Nothing was changed. These need to finish on the previous image first:"
REASONS = {
    Reason.MAY_HAVE_RECEIVED: (
        "Audiobookshelf may have received this write. Start the previous image "
        "with the connection reachable and let it finish."
    ),
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
NO_CAUSE = "Start the previous image and read Activity."

UNWRITTEN = (
    "Answers not yet written: {n}. Each is kept if a new reading asks the same "
    "question, and asked again otherwise."
)
RETIRED = (
    "Answers whose waiting write will be retired: {n}. Each is asked again, "
    "and Automatic stays off the field."
)
WRITTEN = "Answers already written: {n}."
AUTHORED = "Descriptions you wrote that are not yet in Audiobookshelf: {n}."
COVERS = "Cover choices not yet written: {n}."
COVER_KEPT = "work {item_id}: keeps your last written cover"
COVER_NONE = (
    "work {item_id}: no cover choice remains, and Automatic still leaves this "
    "cover alone."
)
STOPPED = "Writes stopped for your decision, which leave Activity: {n}."
UPPER_LIMITS = (
    "Counts can include answers from earlier readings, so they are upper limits."
)
REPLACED = "The restore point already in /data/regeneration-backup will be replaced."
GO_AHEAD = "Nothing was changed. To go ahead, run regenerate-derived --retire-pending."
REGENERATED = (
    "Regenerated. Start the server and preparation reads every library again. "
    "Your answers, settings and write history are kept. A restore point is in "
    "/data/regeneration-backup. Delete it once you are satisfied; until then "
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


def _gate_line(item: BlockingItem) -> str:
    causes = " ".join(CAUSES[cause] for cause in item.causes) or NO_CAUSE
    if item.connection is None:
        return f"  {REASONS[item.reason]} {causes}"
    assert item.destination is not None
    return (
        f"  {item.connection}/{item.library}, work {item.item_id}, "
        f"{destination_label(item.destination)}: {REASONS[item.reason]} {causes}"
    )


def _report_lines(report: LibraryReport, retire_pending: bool) -> list[str]:
    """One library's lists. With `--retire-pending`, list 1's answers whose
    waiting write is retired are listed apart (copy-draft L1-flag)."""
    retired = report.retired if retire_pending else ()
    kept = [entry for entry in report.unwritten if entry not in retired]
    lines = [f"{report.connection}/{report.library}"]
    lines.append("  " + UNWRITTEN.format(n=len(kept)))
    lines.extend(_work(e) for e in kept)
    if retired:
        lines.append("  " + RETIRED.format(n=len(retired)))
        lines.extend(_work(e) for e in retired)
    lines.append("  " + WRITTEN.format(n=report.written))
    if report.authored:
        lines.append("  " + AUTHORED.format(n=len(report.authored)))
        lines.extend(_work(e) for e in report.authored)
    if report.covers:
        lines.append("  " + COVERS.format(n=len(report.covers)))
        lines.extend(
            "    "
            + (COVER_KEPT if pointer.keeps_written else COVER_NONE).format(
                item_id=pointer.item_id
            )
            for pointer in report.covers
        )
    if report.stopped:
        lines.append("  " + STOPPED.format(n=len(report.stopped)))
        lines.extend(_work(e) for e in report.stopped)
    return lines


def _reports(outcome: Outcome) -> list[str]:
    reports = [report for report in outcome.reports if not report.empty]
    lines = [
        line
        for report in reports
        for line in _report_lines(report, outcome.retire_pending)
    ]
    if reports:
        lines.append(UPPER_LIMITS)
    if outcome.backup_held:
        lines.append(REPLACED)
    return lines


def render(outcome: Outcome, write: Callable[[str], object]) -> int:
    """Print what happened, one line per call, and return the exit status:
    0 regenerated or nothing to do, 1 a refusal or a report."""
    lines: list[str]
    if outcome.kind == "refused":
        assert outcome.refusal is not None
        lines = [REFUSALS[outcome.refusal]]
    elif outcome.kind == "failed":
        lines = [str(outcome.message)]
    elif outcome.kind == "nothing_to_do":
        lines = [NOTHING_TO_DO]
    elif outcome.kind == "no_space":
        message = NO_SPACE.format(
            free=_size(outcome.free), needed=_size(outcome.needed)
        )
        lines = [message + (" " + NO_SPACE_HELD if outcome.backup_held else "")]
    elif outcome.kind == "gate":
        lines = [GATE_HEADING, *(_gate_line(item) for item in outcome.items)]
    elif outcome.kind == "report":
        lines = [*_reports(outcome), GO_AHEAD]
    else:
        lines = [*_reports(outcome), REGENERATED]
    for line in lines:
        write(line + "\n")
    return 0 if outcome.kind in {"regenerated", "nothing_to_do"} else 1


__all__ = ["USAGE", "parse_container_arguments", "render"]
