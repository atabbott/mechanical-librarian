"""The record of the owner's answer per field of a book, as a pure fold (ADR-0108).

The store writes `owner_field_answers` incrementally in each answer's own
transaction. This fold recomputes the same rows from the ledger alone: the
owner's human decisions and authored descriptions in ordinal order, each cover
answer naming its owner selection. It gives a withdrawal the answer it
restores, and it is what the recomputation test compares the stored record
with. Resume's events join it with Resume (C2b); until then no event exists.
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
        # Admitted, never written until the question check (C4').
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
    the case's target field ('null' for an authored description).
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
) -> OwnerFieldAnswer:
    """The record once ``row`` replaces ``current`` as the field's answer.

    The replaced answer is the one a withdrawal restores only when it was
    given on another case or episode; otherwise the new answer restores what
    the replaced one would have (ADR-0108, "Withdrawal and carry eligibility").
    """
    previous = None
    if current is not None:
        previous = (
            current.answer_id
            if (current.review_case_id, current.episode)
            != (row.review_case_id, row.episode)
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
        offer_digest=None,
        seen_target_digest=None,
    )


def _standing(
    history: list[OwnerAnswerRow], row: OwnerAnswerRow
) -> OwnerAnswerRow | None:
    """The field's latest owner decision of ``row``'s case episode before it.

    The episode is the case's decision floor when ``row`` was recorded, so
    this is exactly the row the store read as standing then.
    """
    return next(
        (
            earlier
            for earlier in reversed(history)
            if earlier.source == "human"
            and earlier.review_case_id == row.review_case_id
            and earlier.destination == row.destination
            and earlier.ordinal > row.episode
            and earlier.outcome not in _ANNOTATIONS
        ),
        None,
    )


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


def _fold(
    rows: Iterable[OwnerAnswerRow],
) -> tuple[dict[ItemKey, OwnerFieldAnswer], dict[str, OwnerFieldAnswer]]:
    record: dict[ItemKey, OwnerFieldAnswer] = {}
    # Each answer as it last stood in the record, for a later restore.
    stood: dict[str, OwnerFieldAnswer] = {}
    history: list[OwnerAnswerRow] = []
    for row in sorted(rows, key=lambda value: value.ordinal):
        key = row.key
        current = record.get(key)
        standing = _standing(history, row) if row.source == "human" else None
        history.append(row)
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
            )
        if key in record:
            stood[record[key].answer_id] = record[key]
    return record, stood


def fold_owner_field_answers(
    rows: Iterable[OwnerAnswerRow],
) -> dict[ItemKey, OwnerFieldAnswer]:
    """The record the ledger's owner answers give, by item key and destination."""
    return _fold(rows)[0]


def restored_answer(
    rows: Iterable[OwnerAnswerRow], answer_id: str | None
) -> OwnerFieldAnswer | None:
    """The answer ``answer_id`` as a withdrawal restores it: as it last stood
    in the record, no longer carried; None when it never stood there."""
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

