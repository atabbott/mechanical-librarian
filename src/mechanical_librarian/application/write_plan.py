"""The write plan: every prepared change, as the owner reads it before writes.

A plan is computed on request and never stored: the inline plan inside one
read snapshot, a file in short snapshots that each end before their rows are
sent, so no download holds up another connection's commit. Its rows are words
only: no work, item or decision identity, path, artifact key or
cover token reaches the inline plan or either file (#1336).
"""

from __future__ import annotations

import csv
import io
import json
import queue
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.description_advice import AuthoredDescriptionDecision
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.domain.owner_settings import (
    PROVIDER_LABELS,
    audio_edition_label,
    provider_label,
)
from mechanical_librarian.domain.readable_activity import (
    ABSENT,
    EVIDENCE_LIMIT,
    chooser_label,
    destination_label,
    plain_text,
    policy_option_label,
    policy_setting_label,
    value_text,
)

# Every prepared change is listed inline up to this many; past it, each group
# shows a few examples and the inline plan stays within a fixed size.
INLINE_ALL: Final = 25
EXAMPLES_PER_GROUP: Final = 2
INLINE_ROWS: Final = 60
# A file lists at most this many changes in at most this many bytes, and says
# so inside itself when it stops short. Neither bound depends on load.
FILE_ROWS: Final = 20_000
FILE_BYTES: Final = 16_777_216
# A closing application is noticed within this many resolved rows, and one
# snapshot of a file read resolves no more than this many.
CLOSING_CHECK_ROWS: Final = 100
# A file's snapshot takes no further row once it has worked this long, so a
# commit on another connection waits about this long at most. The row being
# resolved when the time passes is finished.
CHUNK_SNAPSHOT_SECONDS: Final = 0.25

# A source the library itself holds: its own cover or local claims (D4).
LOCAL_LIBRARY_RECORD: Final = "Local library record"
# Every name a row's sources can carry: each provider, by book and by audio
# edition, an unnamed provider either way, and the library's own record.
PLAN_SOURCE_LABELS: Final = frozenset(
    {provider_label(authority) for authority in (*PROVIDER_LABELS, None)}
    | {audio_edition_label(authority) for authority in (*PROVIDER_LABELS, None)}
    | {LOCAL_LIBRARY_RECORD}
)

PlanKind = Literal["human", "automatic"]
PlanFormat = Literal["csv", "json"]

_DESTINATION_ORDER: Final = {
    destination.value: index for index, destination in enumerate(TargetDestination)
}
_CSV_HEADER: Final = (
    "Library",
    "Work",
    "Authors",
    "Field",
    "Chosen by",
    "Current value",
    "New value",
    "Reason",
    "Sources",
)
# A cell a spreadsheet would read as a formula is prefixed so it reads as text.
_FORMULA_PREFIXES: Final = ("=", "+", "-", "@")
# Room kept under the byte bound for the notes a truncated file ends with.
_NOTE_RESERVE: Final = 1_024
# Chunks a producer may run ahead of a slow client: bounded memory.
_QUEUED_CHUNKS: Final = 8
_QUEUE_WAIT_SECONDS: Final = 0.25
# A producer whose chunks nobody has taken for this long gives the file up and
# releases its reader: a suspended tab must not hold a read open (#1336).
STALLED_CONSUMER_SECONDS: Final = 60.0


@dataclass(frozen=True, slots=True)
class PlanRow:
    """One prepared change, with its exact current and new values."""

    library: str
    work: str
    authors: str
    destination: str
    kind: PlanKind
    current: object
    new: object
    reason: str
    sources: tuple[str, ...]


def group_order(
    library_order: Sequence[str],
) -> Callable[[tuple[str, str, str]], tuple[object, ...]]:
    """Order (library, destination, kind) groups as the plan shows them.

    Libraries keep their configured order, fields their canonical order, and
    an owner's choices come before automatic ones.
    """
    libraries = {library: index for index, library in enumerate(library_order)}

    def key(group: tuple[str, str, str]) -> tuple[object, ...]:
        library, destination, kind = group
        return (
            libraries.get(library, len(libraries)),
            library,
            _DESTINATION_ORDER.get(destination, len(_DESTINATION_ORDER)),
            destination,
            kind != "human",
        )

    return key


def plan_reason(
    decision: HumanDecision
    | AutomaticDecision
    | CoverAutomaticDecision
    | AuthoredDescriptionDecision,
) -> str:
    """Say why a prepared change is planned, in the owner's words."""
    if type(decision) is HumanDecision:
        if decision.outcome == "owner_entered":
            return "You entered this value"
        if decision.outcome == "keep_current":
            return "You chose to restore the earlier value"
        return (
            "You chose this cover"
            if decision.destination == "cover"
            else "You chose this value"
        )
    if type(decision) is AuthoredDescriptionDecision:
        return (
            "You chose this value"
            if decision.origin == "human"
            else "Description advice"
        )
    automatic = (
        decision
        if type(decision) is AutomaticDecision
        or type(decision) is CoverAutomaticDecision
        else None
    )
    if automatic is None:
        raise ValueError("unsupported decision")
    return (
        f"{policy_setting_label(automatic.policy_category)}: "
        f"{policy_option_label(automatic.policy_option, automatic.policy_category)}"
    )


def plan_value_text(
    destination: str, value: object, *, current: bool, limit: int = EVIDENCE_LIMIT
) -> str:
    """State one value as owner text; a cover is words, never its identity."""
    if destination == "cover":
        if value is None:
            return ABSENT
        return "Current cover" if current else "Selected cover"
    return value_text(destination, value, limit=limit)


def inline_row(row: PlanRow) -> dict[str, object]:
    return {
        "work": row.work,
        "authors": row.authors,
        "current": plan_value_text(row.destination, row.current, current=True),
        "new": plan_value_text(row.destination, row.new, current=False),
        "reason": row.reason,
        "sources": list(row.sources),
    }


def _exact(destination: str, value: object, *, current: bool) -> object:
    if destination == "cover" and value is not None:
        return "Current cover" if current else "Selected cover"
    return value


def _file_fields(row: PlanRow, library_labels: Mapping[str, str]) -> dict[str, object]:
    return {
        "library": library_labels.get(row.library, row.library),
        "work": row.work,
        "authors": row.authors,
        "field": destination_label(row.destination),
        "chosen_by": chooser_label(
            "human" if row.kind == "human" else "automatic_policy"
        ),
        "current_value": _exact(row.destination, row.current, current=True),
        "new_value": _exact(row.destination, row.new, current=False),
        "reason": row.reason,
        "sources": list(row.sources),
    }


def _cell(text: str) -> str:
    text = plain_text(text)
    return "'" + text if text.startswith(_FORMULA_PREFIXES) else text


def _csv_line(cells: Sequence[str]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerow(
        [_cell(cell) for cell in cells]
    )
    return buffer.getvalue().encode("utf-8")


def _csv_row(row: PlanRow, library_labels: Mapping[str, str]) -> bytes:
    fields = _file_fields(row, library_labels)
    return _csv_line(
        (
            str(fields["library"]),
            row.work,
            row.authors,
            str(fields["field"]),
            str(fields["chosen_by"]),
            plan_value_text(
                row.destination, row.current, current=True, limit=sys.maxsize
            ),
            plan_value_text(row.destination, row.new, current=False, limit=sys.maxsize),
            row.reason,
            ", ".join(row.sources),
        )
    )


def _json_row(row: PlanRow, library_labels: Mapping[str, str]) -> bytes:
    return json.dumps(
        _file_fields(row, library_labels), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


class PlanFileEncoder:
    """Encode one plan file as it is made, within its byte bound.

    The head comes first, then one line per row, then the notes (CSV) or the
    summary fields (JSON) that state any shortfall. Every row ends in a line
    feed and the head holds exactly one, so a reader counts received rows as
    line feeds less one.
    """

    def __init__(self, format: PlanFormat, library_labels: Mapping[str, str]) -> None:
        self._format = format
        self._labels = library_labels
        self.head = (
            b"\xef\xbb\xbf" + _csv_line(_CSV_HEADER)
            if format == "csv"
            else b'{"changes":[\n'
        )
        self._used = len(self.head) + _NOTE_RESERVE
        self.listed = 0

    def row(self, row: PlanRow) -> bytes | None:
        """Encode one row, or ``None`` once the file would exceed its bound."""
        if self._format == "csv":
            line = _csv_row(row, self._labels)
        else:
            line = (b"," if self.listed else b"") + _json_row(row, self._labels) + b"\n"
        if self._used + len(line) > FILE_BYTES:
            return None
        self._used += len(line)
        self.listed += 1
        return line

    def tail(self, *, total: int, unreadable: int, changed: int, made_at: int) -> bytes:
        truncated = self.listed + unreadable + changed < total
        if self._format == "json":
            return (
                b"],"
                + json.dumps(
                    {
                        "changed": changed,
                        "listed": self.listed,
                        "made_at": made_at,
                        "total": total,
                        "truncated": truncated,
                        "unreadable": unreadable,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")[1:]
            )
        notes = b""
        if truncated:
            notes += _csv_line(
                (
                    f"Lists {self.listed:,} of {total:,} prepared changes. "
                    "The rest are not in this file.",
                )
            )
        if unreadable:
            notes += _csv_line(
                (
                    "1 prepared change was not listed because it could not be read."
                    if unreadable == 1
                    else f"{unreadable:,} prepared changes were not listed because "
                    "they could not be read.",
                )
            )
        if changed:
            notes += _csv_line(
                (
                    "1 prepared change was not listed because it changed while "
                    "this file was being made."
                    if changed == 1
                    else f"{changed:,} prepared changes were not listed because "
                    "they changed while this file was being made.",
                )
            )
        return notes


class ProducedStream:
    """Bytes one producer thread makes and the response sends as they arrive.

    The producer owns its reader for the whole file and ends by itself: when
    the file is made, on a fault, when the application is closing, when the
    consumer stops it, or when no chunk has been taken for
    ``STALLED_CONSUMER_SECONDS`` (a download nobody is reading). Giving up is
    an abort, not a truncation: the consumer then gets the fault, so a short
    file never ends as a complete one. The ``when_closed`` releases run once,
    from the producer's own exit, after its read has ended.
    """

    def __init__(
        self,
        produce: Callable[[Callable[[bytes], None], Callable[[], bool]], None],
        closing: Callable[[], bool],
        *,
        stalled_seconds: float | None = None,
    ) -> None:
        self._chunks: queue.Queue[bytes] = queue.Queue(maxsize=_QUEUED_CHUNKS)
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._closing = closing
        self._stalled_seconds = (
            STALLED_CONSUMER_SECONDS if stalled_seconds is None else stalled_seconds
        )
        self._taken_at = time.monotonic()
        # Only a producer that returned normally ends the body normally.
        self._outcome: BaseException | None = GuardedApplicationError("unavailable")
        self._lock = threading.Lock()
        self._released: list[Callable[[], None]] | None = []
        self._thread = threading.Thread(
            target=self._run, args=(produce,), name="write-plan-file", daemon=True
        )
        self._thread.start()

    def cancelled(self) -> bool:
        return self._stop.is_set() or self._closing()

    def _put(self, chunk: bytes) -> bool:
        while not self.cancelled():
            try:
                self._chunks.put(chunk, timeout=_QUEUE_WAIT_SECONDS)
                return True
            except queue.Full:
                if time.monotonic() - self._taken_at > self._stalled_seconds:
                    return False
        return False

    def _emit(self, chunk: bytes) -> None:
        if not self._put(chunk):
            raise GuardedApplicationError("unavailable")

    def _run(
        self, produce: Callable[[Callable[[bytes], None], Callable[[], bool]], None]
    ) -> None:
        try:
            produce(self._emit, self.cancelled)
            # A producer that noticed a stop and returned did not finish.
            if not self.cancelled():
                self._outcome = None
        except BaseException as error:  # noqa: BLE001 - handed to the consumer
            self._outcome = error
        finally:
            self._finished.set()
            with self._lock:
                released, self._released = self._released or [], None
            for release in released:
                release()

    def next_chunk(self) -> bytes | None:
        """Wait for the next chunk; ``None`` only at a complete end.

        A producer that stopped short, for any reason, raises its fault here,
        so a consumer that resumes late never sees a normal end of body.
        """
        while True:
            try:
                chunk = self._chunks.get(timeout=_QUEUE_WAIT_SECONDS)
            except queue.Empty:
                if self._finished.is_set():
                    # Every chunk was queued before the producer finished.
                    try:
                        chunk = self._chunks.get_nowait()
                    except queue.Empty:
                        if self._outcome is None:
                            return None
                        raise self._outcome from None
                elif self._stop.is_set():
                    raise GuardedApplicationError("unavailable") from None
                else:
                    continue
            self._taken_at = time.monotonic()
            return chunk

    def when_closed(self, release: Callable[[], None]) -> None:
        """Run ``release`` once, when the producer has ended its read."""
        with self._lock:
            if self._released is not None:
                self._released.append(release)
                return
        release()

    def stop(self) -> None:
        """Ask the producer to stop; it releases its reader on its own exit."""
        self._stop.set()

    def close(self) -> None:
        """Stop the producer and wait until its reader is released."""
        self._stop.set()
        self._thread.join()
