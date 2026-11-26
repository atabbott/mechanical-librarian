"""The record of the owner's answer per field of a book, as a pure fold (ADR-0108).

The store writes `owner_field_answers` incrementally in each answer's own
transaction. This fold recomputes the same rows from the ledger alone: the
owner's human decisions and authored descriptions in ordinal order, each cover
answer naming its owner selection, interleaved with Resume's events. It gives a
withdrawal the answer it restores, and it is what the recomputation test
compares the stored record with.

A carried answer counts as given on its root original's case and episode,
and names that original as the answer a withdrawal restores. Two events clear
carry eligibility without changing the answer: `retired`, naming the record's
answer, and a `resume_superseded` naming the answer its request still covers,
which the question check writes when it closes a failed Resume (and
`--retire-pending` when it closes an open one).

Every event stores `over_ordinal`, the counter's value when it was written
(for a request, the coverage bound). An event at position c follows every
answer with ordinal at or below c and precedes every answer above it; events
at one position keep the order the store wrote them (ADR-0108, "Resume
automatic handling").
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Final, Literal

from mechanical_librarian.domain.human_decision import (
    VALUE_OUTCOMES,
    chosen_value_fingerprint,
    field_fingerprint,
)

OWNER_FIELD_EVENT_SCHEMA: Final = "owner-field-event-v1"
OWNER_FIELD_EVENT_KINDS: Final = frozenset(
    {
        "resume_requested",
        "resume_completed",
        "resume_failed",
        "resume_superseded",
        # Written by regenerate-derived --retire-pending (ADR-0108).
        "retired",
    }
)
# What an answer of the case's episode settles a field with, so a pin over
# it marks that answer rather than standing in its place (ADR-0085,
# Amendment 2).
_STANDING_OUTCOMES: Final = frozenset({"accept", "keep_current", "owner_entered"})
# Rows that are never a field's latest decision of an episode.
_ANNOTATIONS: Final = frozenset({"pin", "unpin"})

ItemKey = tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class OwnerAnswerRow:
    """One ledger row the record folds, as the store reads it.

    ``outcome`` is the human decision's outcome, or ``authored`` for an
    owner authored description; ``cause`` is set only on Book matching's
    valueless row. ``target_field_document`` is the human decision's copy of
    the case's target field ('null' for an authored description). The
    question halves are the decision's own, null where it has none.
    ``carried_from`` names a carried row's root original, and
    ``given_case_id`` and ``given_episode`` are where the answer counts as
    given: the root's case and episode for a carried row, else its own.
    """

    source: Literal["human", "authored_description"]
    answer_id: str
    ordinal: int
    connection_name: str
    library_name: str
    item_id: str
    destination: str
    review_case_id: str
    episode: int
    outcome: str
    cause: str | None
    value_document: str
    display_title: str | None
    recorded_at: int
    target_field_document: str
    cover_selection_id: str | None
    offer_digest: str | None
    seen_target_digest: str | None
    carried_from: str | None
    given_case_id: str
    given_episode: int

    @property
    def key(self) -> ItemKey:
        return (self.connection_name, self.library_name, self.item_id, self.destination)

    @property
    def given_on(self) -> tuple[str, int]:
        return (self.given_case_id, self.given_episode)


@dataclass(frozen=True, slots=True)
class OwnerFieldEventRow:
    """One `owner_field_events` row, in its column order.

    A request names no request and its ``over_ordinal`` is the bound of the
    answers it covers; completion, failure and supersession name their
    request.
    """

    event_id: str
    connection_name: str
    library_name: str
    item_id: str
    destination: str
    kind: str
    request_id: str | None
    over_ordinal: int
    scan_id: str | None
    answer_id: str | None
    occurred_at: int

    @property
    def key(self) -> ItemKey:
        return (self.connection_name, self.library_name, self.item_id, self.destination)


@dataclass(frozen=True, slots=True)
class OwnerFieldAnswer:
    """One `owner_field_answers` row, in its column order."""

    connection_name: str
    library_name: str
    item_id: str
    destination: str
    source: str
    answer_id: str
    ordinal: int
    outcome: str
    value_document: str
    display_title: str | None
    recorded_at: int
    review_case_id: str
    episode: int
    previous_answer_id: str | None
    carry_eligible: bool
    open_resume_id: str | None
    pin_fingerprint: str | None
    cover_selection_id: str | None
    offer_digest: str | None
    seen_target_digest: str | None

    @property
    def key(self) -> ItemKey:
        return (self.connection_name, self.library_name, self.item_id, self.destination)


def answer_from(
    row: OwnerAnswerRow,
    current: OwnerFieldAnswer | None,
    pin_fingerprint: str | None = None,
    current_given_on: tuple[str, int] | None = None,
) -> OwnerFieldAnswer:
    """The record once ``row`` replaces ``current`` as the field's answer.

    The replaced answer is the one a withdrawal restores only when it was
    given on another case or episode; otherwise the new answer restores what
    the replaced one would have (ADR-0108, "Withdrawal and carry eligibility").
    ``current_given_on`` is where ``current`` counts as given, its root's
    case and episode where it was carried. A carried row restores its root.
    The record takes the row's case, episode and question halves.
    """
    previous = None
    if row.carried_from is not None:
        previous = row.carried_from
    elif current is not None:
        given_on = (
            (current.review_case_id, current.episode)
            if current_given_on is None
            else current_given_on
        )
        previous = (
            current.answer_id
            if given_on != row.given_on
            else current.previous_answer_id
        )
    return OwnerFieldAnswer(
        connection_name=row.connection_name,
        library_name=row.library_name,
        item_id=row.item_id,
        destination=row.destination,
        source=row.source,
        answer_id=row.answer_id,
        ordinal=row.ordinal,
        outcome=row.outcome,
        value_document=row.value_document,
        display_title=row.display_title,
        recorded_at=row.recorded_at,
        review_case_id=row.review_case_id,
        episode=row.episode,
        previous_answer_id=previous,
        carry_eligible=True,
        open_resume_id=None,
        pin_fingerprint=pin_fingerprint,
        cover_selection_id=row.cover_selection_id,
        offer_digest=row.offer_digest,
        seen_target_digest=row.seen_target_digest,
    )


def _standing(
    latest: dict[tuple[str, str], OwnerAnswerRow], row: OwnerAnswerRow
) -> OwnerAnswerRow | None:
    """The field's latest owner decision of ``row``'s case episode before it.

    ``latest`` holds each case field's latest owner decision so far, pins and
    unpins aside; it is of the episode when its ordinal is above the
    episode's floor. The episode is the case's decision floor when ``row``
    was recorded, so this is exactly the row the store read as standing then.
    """
    earlier = latest.get((row.review_case_id, row.destination))
    return earlier if earlier is not None and earlier.ordinal > row.episode else None


def _shown_fingerprint(row: OwnerAnswerRow) -> str | None:
    field = json.loads(row.target_field_document)
    return field_fingerprint(row.destination, field if type(field) is dict else None)


def pin_fingerprint(row: OwnerAnswerRow, standing: OwnerAnswerRow | None) -> str | None:
    """What a pin keeps: the chosen value of the answer it marks, otherwise
    the value its case showed (ADR-0085, Amendment 2)."""
    if standing is not None and standing.outcome in VALUE_OUTCOMES:
        return chosen_value_fingerprint(
            row.destination, json.loads(standing.value_document)
        )
    return _shown_fingerprint(row)


def _answer(
    record: dict[ItemKey, OwnerFieldAnswer],
    stood: dict[str, OwnerFieldAnswer],
    latest: dict[tuple[str, str], OwnerAnswerRow],
    given_on: dict[str, tuple[str, int]],
    row: OwnerAnswerRow,
) -> None:
    """Fold one owner answer into ``record``."""
    key = row.key
    current = record.get(key)
    given_on[row.answer_id] = row.given_on
    standing = _standing(latest, row) if row.source == "human" else None
    if row.source == "human" and row.outcome not in _ANNOTATIONS:
        latest[(row.review_case_id, row.destination)] = row
    if row.outcome == "unpin":
        if current is not None:
            record[key] = replace(current, pin_fingerprint=None)
    elif row.outcome == "pin" and (
        standing is not None and standing.outcome in _STANDING_OUTCOMES
    ):
        # A pin over a standing answer marks the record; it replaces nothing.
        if current is not None:
            record[key] = replace(
                current, pin_fingerprint=pin_fingerprint(row, standing)
            )
    elif row.outcome == "withdraw":
        if (
            current is None
            or standing is None
            or current.answer_id != standing.answer_id
        ):
            pass
        elif row.cause is not None:
            # Book matching asked the field again: the answer stays,
            # but it is no longer carried (ADR-0108).
            record[key] = replace(current, carry_eligible=False)
        elif current.previous_answer_id in stood:
            record[key] = replace(
                stood[current.previous_answer_id], carry_eligible=False
            )
        else:
            del record[key]
    else:
        record[key] = answer_from(
            row,
            current,
            pin_fingerprint(row, standing) if row.outcome == "pin" else None,
            None if current is None else given_on[current.answer_id],
        )
    if key in record:
        stood[record[key].answer_id] = record[key]


def _fold(
    rows: Iterable[OwnerAnswerRow],
    events: Iterable[OwnerFieldEventRow] = (),
) -> tuple[dict[ItemKey, OwnerFieldAnswer], dict[str, OwnerFieldAnswer]]:
    record: dict[ItemKey, OwnerFieldAnswer] = {}
    # Each answer as it last stood in the record, for a later restore.
    stood: dict[str, OwnerFieldAnswer] = {}
    # Each case field's latest owner decision, pins and unpins aside, so a
    # store's open folds its whole ledger in one pass.
    latest: dict[tuple[str, str], OwnerAnswerRow] = {}
    # Where each answer counts as given, for the restore rule.
    given_on: dict[str, tuple[str, int]] = {}
    requests: dict[str, OwnerFieldEventRow] = {}
    # Each field's unclosed request, by the order the store wrote them.
    unclosed: dict[ItemKey, OwnerFieldEventRow] = {}
    entries: list[tuple[int, int, int, OwnerAnswerRow | OwnerFieldEventRow]] = [
        (row.ordinal, 0, index, row) for index, row in enumerate(rows)
    ]
    entries.extend(
        (event.over_ordinal, 1, index, event) for index, event in enumerate(events)
    )
    for _, _, _, entry in sorted(entries, key=lambda value: value[:3]):
        if isinstance(entry, OwnerAnswerRow):
            _answer(record, stood, latest, given_on, entry)
            continue
        key = entry.key
        current = record.get(key)
        if entry.kind == "resume_requested":
            requests[entry.event_id] = entry
            unclosed[key] = entry
            continue
        if entry.kind == "retired":
            # The answer's plan was retired: it stays, no longer carried.
            if current is not None and current.answer_id == entry.answer_id:
                record[key] = replace(current, carry_eligible=False)
                stood[current.answer_id] = record[key]
            continue
        request = requests.get(entry.request_id or "")
        if request is None or request.key != key:
            continue
        if entry.kind == "resume_completed":
            # Completion hands back every answer the request covers; an
            # answer given after it was asked superseded it instead.
            if current is not None and current.ordinal <= request.over_ordinal:
                del record[key]
        elif (
            entry.kind == "resume_superseded"
            and current is not None
            and current.answer_id == entry.answer_id
            and current.ordinal <= request.over_ordinal
        ):
            # A request closed over the answer it covers hands the field
            # back: nothing carries or plans (ADR-0108, step 0). An answer
            # that supersedes a request is above its bound, a request asked
            # again names no answer, and an emptying withdrawal leaves none.
            record[key] = replace(current, carry_eligible=False)
            stood[current.answer_id] = record[key]
        if entry.kind in {"resume_completed", "resume_superseded"} and (
            unclosed.get(key) is request
        ):
            del unclosed[key]
    for key, request in unclosed.items():
        current = record.get(key)
        if current is not None and current.ordinal <= request.over_ordinal:
            record[key] = replace(current, open_resume_id=request.event_id)
    return record, stood


def fold_owner_field_answers(
    rows: Iterable[OwnerAnswerRow],
    events: Iterable[OwnerFieldEventRow],
) -> dict[ItemKey, OwnerFieldAnswer]:
    """The record the ledger's owner answers and Resume's events give, by
    item key and destination.

    A completion deletes the field's answer when the request covers it. The
    field's one unclosed request stays open on the final answer when it
    covers that answer: a withdrawal that restores an earlier answer keeps
    it, a failure changes nothing, and an answer or emptying withdrawal
    after it closed it with its own event.
    """
    return _fold(rows, events)[0]


def restored_answer(
    rows: Iterable[OwnerAnswerRow], answer_id: str | None
) -> OwnerFieldAnswer | None:
    """The answer ``answer_id`` as a withdrawal restores it: as it last stood
    in the record, no longer carried; None when it never stood there.

    It carries no open Resume: the store sets the field's open request on it.
    """
    if answer_id is None:
        return None
    stood = _fold(rows)[1].get(answer_id)
    return None if stood is None else replace(stood, carry_eligible=False)


def owner_field_event_id(
    *,
    connection_name: str,
    library_name: str,
    item_id: str,
    destination: str,
    kind: str,
    request_id: str | None,
    over_ordinal: int,
    scan_id: str | None,
    answer_id: str | None,
    occurred_at: int,
    previous_event_id: str | None,
) -> str:
    """An event's id: its columns and the key's previous latest event, so the
    key's history is one chain a store cannot reorder or drop from unseen."""
    return (
        "owner_field_event_"
        + hashlib.sha256(
            json.dumps(
                {
                    "answer_id": answer_id,
                    "connection_name": connection_name,
                    "destination": destination,
                    "item_id": item_id,
                    "kind": kind,
                    "library_name": library_name,
                    "occurred_at": occurred_at,
                    "over_ordinal": over_ordinal,
                    "previous_event_id": previous_event_id,
                    "request_id": request_id,
                    "scan_id": scan_id,
                    "schema_version": OWNER_FIELD_EVENT_SCHEMA,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
