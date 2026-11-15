"""The write plan's rows and files say exactly what will change, as words (#1336)."""

from __future__ import annotations

import csv
import io
import json
import re
import threading
from dataclasses import replace
from typing import cast

import pytest

from mechanical_librarian.application import write_plan
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.application.write_plan import (
    FILE_BYTES,
    STALLED_CONSUMER_SECONDS,
    PlanFileEncoder,
    PlanFormat,
    PlanRow,
    ProducedStream,
    group_order,
    inline_row,
    plan_reason,
)
from mechanical_librarian.domain.field_effect import FieldEffectError

_COVER = "cover_" + "a" * 64


def _row(**values: object) -> PlanRow:
    fields: dict[str, object] = {
        "library": "books",
        "work": "Real Tigers",
        "authors": "Mick Herron",
        "destination": "title",
        "kind": "human",
        "current": "Old title",
        "new": "New title",
        "reason": "You chose this value",
        "sources": ("Hardcover",),
    }
    fields.update(values)
    return PlanRow(**fields)  # type: ignore[arg-type]  # reason: the test supplies each field by name


def _file(
    format: PlanFormat,
    rows: list[PlanRow],
    *,
    total: int | None = None,
    unreadable: int = 0,
    labels: dict[str, str] | None = None,
) -> bytes:
    encoder = PlanFileEncoder(format, labels or {"books": "Household books"})
    body = encoder.head
    for row in rows:
        line = encoder.row(row)
        if line is None:
            break
        body += line
    return body + encoder.tail(
        total=len(rows) if total is None else total,
        unreadable=unreadable,
        made_at=1_700_000_000,
    )


def _csv(body: bytes) -> list[list[str]]:
    assert body.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"), newline="")))


@pytest.mark.parametrize(
    ("value", "cell"),
    (
        ("=1+1", "'=1+1"),
        ("+44 20", "'+44 20"),
        ("-5", "'-5"),
        ("@SUM(A1)", "'@SUM(A1)"),
        (' "quoted" ', '"quoted"'),
        ("one\r\ntwo", "one two"),
        ("\t=1+1", "'=1+1"),
        ("bell\x07here", "bell here"),
    ),
    ids=("equals", "plus", "minus", "at", "quote", "crlf", "tab", "bell"),
)
def test_a_hostile_cell_reads_as_text_on_one_line(value: str, cell: str) -> None:
    body = _file("csv", [_row(work=value, new=value, authors=value)])

    rows = _csv(body)

    assert rows[0][:3] == ["Library", "Work", "Authors"]
    assert rows[1][1] == rows[1][2] == rows[1][6] == cell
    # Every cell is quoted and every record ends in CRLF, with no bare line feed.
    assert body.count(b"\n") == body.count(b"\r\n") == 2
    assert re.fullmatch(rb'("([^"]|"")*",)*"([^"]|"")*"', body.split(b"\r\n")[1])


def test_json_keeps_exact_values_and_round_trips() -> None:
    exact = _row(
        destination="authors",
        current=['Ada "A" Lovelace', "=Bob"],
        new=["Line\nbreak", "Tab\there", " "],
        sources=(),
    )

    body = _file("json", [exact, _row(current=None)])

    document = json.loads(body)
    assert document["changes"][0] == {
        "library": "Household books",
        "work": "Real Tigers",
        "authors": "Mick Herron",
        "field": "Authors",
        "chosen_by": "You",
        "current_value": ['Ada "A" Lovelace', "=Bob"],
        "new_value": ["Line\nbreak", "Tab\there", " "],
        "reason": "You chose this value",
        "sources": [],
    }
    assert document["changes"][1]["current_value"] is None
    assert {key: document[key] for key in document if key != "changes"} == {
        "listed": 2,
        "made_at": 1_700_000_000,
        "total": 2,
        "truncated": False,
        "unreadable": 0,
    }
    # One row per line: received rows are line feeds less the head's.
    assert body.count(b"\n") - 1 == 2


def test_a_cover_is_words_and_no_identity_reaches_any_surface() -> None:
    cover = _row(
        destination="cover",
        kind="automatic",
        current=_COVER,
        new="cover_" + "b" * 64,
        reason="Cover: Fill from confirmed covers",
        sources=("Audible",),
    )
    empty = _row(destination="cover", current=None, new=_COVER)

    assert inline_row(cover)["current"] == "Current cover"
    assert inline_row(cover)["new"] == "Selected cover"
    assert inline_row(empty)["current"] == "Not set"
    formats: tuple[PlanFormat, ...] = ("csv", "json")
    for format in formats:
        body = _file(format, [cover, empty])
        assert b"cover_" not in body
        assert b"Current cover" in body and b"Selected cover" in body
    assert "cover_" not in json.dumps([inline_row(cover), inline_row(empty)])


def test_an_absent_value_says_not_set_and_a_row_states_no_identity() -> None:
    row = _row(current=None, new="", sources=())

    inline = inline_row(row)

    assert inline == {
        "work": "Real Tigers",
        "authors": "Mick Herron",
        "current": "Not set",
        "new": "Not set",
        "reason": "You chose this value",
        "sources": [],
    }
    assert _csv(_file("csv", [row]))[1][5:7] == ["Not set", "Not set"]
    keys = set(json.loads(_file("json", [row]))["changes"][0])
    assert not any(
        word in key for key in keys for word in ("id", "item", "path", "token")
    )


def test_a_long_value_is_whole_in_the_file_and_bounded_inline() -> None:
    description = "word " * 400
    row = _row(destination="description", current=None, new=description.strip())

    assert len(cast(str, inline_row(row)["new"])) <= write_plan.EVIDENCE_LIMIT
    assert _csv(_file("csv", [row]))[1][6] == description.strip()


def test_a_short_file_says_so_inside_itself() -> None:
    rows = [_row(), _row()]

    csv_rows = _csv(_file("csv", rows, total=5, unreadable=1))
    document = json.loads(_file("json", rows, total=5, unreadable=1))

    assert csv_rows[-2:] == [
        ["Lists 2 of 5 prepared changes. The rest are not in this file."],
        ["1 prepared change was not listed because it could not be read."],
    ]
    assert (document["listed"], document["total"], document["unreadable"]) == (2, 5, 1)
    assert document["truncated"] is True
    plural = _csv(_file("csv", rows, total=4, unreadable=2))
    assert plural[-1] == [
        "2 prepared changes were not listed because they could not be read."
    ]
    # Every change accounted for, so no shortfall note: header, rows, one note.
    assert len(plural) == 1 + 2 + 1


@pytest.mark.parametrize("format", ("csv", "json"), ids=("csv", "json"))
def test_the_byte_bound_stops_a_file_with_room_for_its_notes(
    format: PlanFormat, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(write_plan, "FILE_BYTES", 4_096)
    rows = [_row(new="x" * 500) for _ in range(20)]

    body = _file(format, rows, total=20)

    assert len(body) <= 4_096
    if format == "json":
        document = json.loads(body)
        assert document["truncated"] is True
        assert document["listed"] == len(document["changes"]) < 20
    else:
        assert _csv(body)[-1][0].startswith("Lists ")
    assert FILE_BYTES == 16_777_216


def test_reasons_name_who_chose_in_the_owners_words() -> None:
    from tests.support.field_effects import decision

    accept = decision("title", {"kind": "text", "value": "New"})
    assert plan_reason(accept) == "You chose this value"
    assert plan_reason(decision("cover", None)) == "You chose this cover"
    for outcome, reason in (
        ("owner_entered", "You entered this value"),
        ("keep_current", "You chose to restore the earlier value"),
    ):
        assert plan_reason(replace(accept, outcome=outcome)) == reason


def test_groups_follow_library_then_field_then_owner_first() -> None:
    groups = [
        ("zeta", "title", "automatic"),
        ("alpha", "cover", "human"),
        ("alpha", "title", "automatic"),
        ("alpha", "title", "human"),
        ("unconfigured", "title", "human"),
    ]

    assert sorted(groups, key=group_order(("alpha", "zeta"))) == [
        ("alpha", "title", "human"),
        ("alpha", "title", "automatic"),
        ("alpha", "cover", "human"),
        ("zeta", "title", "automatic"),
        ("unconfigured", "title", "human"),
    ]


def _stream(produce, closing=lambda: False) -> ProducedStream:
    return ProducedStream(produce, closing)


def test_a_stream_sends_chunks_in_order_and_releases_once() -> None:
    released: list[str] = []

    def produce(emit, _cancelled) -> None:
        for index in range(20):
            emit(f"{index}\n".encode())

    stream = _stream(produce)
    stream.when_closed(lambda: released.append("released"))
    chunks = []
    while (chunk := stream.next_chunk()) is not None:
        chunks.append(chunk)
    stream.close()
    stream.close()

    assert b"".join(chunks) == b"".join(f"{i}\n".encode() for i in range(20))
    assert released == ["released"]


def test_a_consumer_that_goes_away_stops_a_blocked_producer() -> None:
    ended = threading.Event()

    def produce(emit, cancelled) -> None:
        try:
            while not cancelled():
                emit(b"row\n")
        finally:
            ended.set()

    stream = _stream(produce)
    assert stream.next_chunk() == b"row\n"
    stream.close()

    assert ended.is_set()
    assert stream.next_chunk() is None or stream.cancelled()


def test_a_closing_application_stops_the_producer_at_its_next_step() -> None:
    closing = threading.Event()

    def produce(emit, cancelled) -> None:
        emit(b"head\n")
        closing.set()
        if cancelled():
            raise GuardedApplicationError("unavailable")
        emit(b"never\n")

    stream = _stream(produce, closing.is_set)
    try:
        assert stream.next_chunk() == b"head\n"
        with pytest.raises(GuardedApplicationError, match="^unavailable$"):
            stream.next_chunk()
    finally:
        stream.close()


def test_a_fault_reaches_the_consumer_after_the_bytes_already_made() -> None:
    def produce(emit, _cancelled) -> None:
        emit(b"head\n")
        raise FieldEffectError("persistence")

    stream = _stream(produce)
    try:
        assert stream.next_chunk() == b"head\n"
        with pytest.raises(FieldEffectError, match="^persistence$"):
            stream.next_chunk()
    finally:
        stream.close()


class _Reader:
    """A reader held for the length of one file, as the export reader is."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.inside = threading.Event()
        self.left = threading.Event()

    def endless(self, emit, cancelled) -> None:
        with self.lock:
            self.inside.set()
            self.left.clear()
            try:
                emit(b"head\n")
                while not cancelled():
                    emit(b"row\n")
            finally:
                self.inside.clear()
                self.left.set()


def _resume(stream: ProducedStream) -> None:
    """A consumer back after the producer gave up: the fault, never an end."""
    with pytest.raises(GuardedApplicationError, match="^unavailable$"):
        for _ in range(100):
            assert stream.next_chunk() is not None


def test_a_stalled_consumer_is_released_when_the_application_closes() -> None:
    reader, closing, released = _Reader(), threading.Event(), threading.Event()
    stream = ProducedStream(reader.endless, closing.is_set)
    stream.when_closed(released.set)
    assert stream.next_chunk() == b"head\n"
    assert reader.inside.wait(5)

    # The consumer stops reading; nothing closes the stream.
    closing.set()

    assert reader.left.wait(5)
    assert released.wait(5)
    _resume(stream)
    stream.close()


def test_a_consumer_nobody_reads_is_released_after_the_stall_bound() -> None:
    reader, released = _Reader(), threading.Event()
    stream = ProducedStream(reader.endless, lambda: False, stalled_seconds=0.3)
    stream.when_closed(released.set)
    assert stream.next_chunk() == b"head\n"

    assert reader.left.wait(5)
    assert released.wait(5)
    _resume(stream)
    # The reader is free: a second file on it proceeds and completes.
    second = ProducedStream(lambda emit, _cancelled: emit(b"next\n"), lambda: False)
    try:
        assert reader.lock.acquire(timeout=1)
        reader.lock.release()
        assert second.next_chunk() == b"next\n"
        assert second.next_chunk() is None
    finally:
        second.close()
    assert STALLED_CONSUMER_SECONDS == 60.0


def test_a_release_registered_after_the_producer_ended_runs_at_once() -> None:
    stream = ProducedStream(lambda emit, _cancelled: emit(b"only\n"), lambda: False)
    assert stream.next_chunk() == b"only\n"
    assert stream.next_chunk() is None
    released: list[str] = []
    stream.when_closed(lambda: released.append("released"))
    stream.close()
    stream.close()
    assert released == ["released"]


def test_a_stopped_stream_never_ends_its_body_normally() -> None:
    reader = _Reader()
    stream = ProducedStream(reader.endless, lambda: False)
    assert stream.next_chunk() == b"head\n"
    stream.stop()
    assert reader.left.wait(5)
    _resume(stream)
    stream.close()
