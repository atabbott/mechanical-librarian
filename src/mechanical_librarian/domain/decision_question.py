"""The question an owner answer was given against (ADR-0108, "The question check").

`decision_question` splits one field's question in two halves. The **offer
part** is every offered alternative's value and lineage rule for the field, or
for the whole title, subtitle and series group: the offer digest's member
documents without proposal ids, so a new case, episode or derived epoch that
offers the same values for the same reasons asks the same question. The
**target part** is the Audiobookshelf state and value of the answered
destination only. Missing, null and empty fold into one empty state, as a
pin's fingerprint folds them; an unloaded, malformed or oversized value, and a
field the case did not read at all, each stay distinct, so they never equal a
value.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from mechanical_librarian.domain.review_case import LEVEL_COMBINATION_DESTINATIONS

DECISION_QUESTION_SCHEMA: Final = "decision-question-v1"
_EMPTY_STATES: Final = frozenset({"missing", "null", "empty"})


class OfferShapeError(ValueError):
    """A case's offer that is not the projection's shape."""


@dataclass(frozen=True, slots=True)
class DecisionQuestion:
    """The two halves, each a SHA-256 hex digest."""

    offer_part: str
    target_part: str


def offer_members(
    offer: Sequence[Mapping[str, object]], destination: str
) -> list[dict[str, object]] | None:
    """The member documents of one field's offer, each naming its proposal id.

    Each member lists every alternative by value and lineage rule (a level
    alternative names ``work_identity:<level>``, a live one no rule), sorted.
    The title, subtitle and series group lists every member the offer has.
    None when the offer has no proposal for ``destination``.
    """
    members = (
        LEVEL_COMBINATION_DESTINATIONS
        if destination in LEVEL_COMBINATION_DESTINATIONS
        else (destination,)
    )
    by_destination = {proposal.get("destination"): proposal for proposal in offer}
    if destination not in by_destination:
        return None
    data: list[dict[str, object]] = []
    for member in (value for value in members if value in by_destination):
        proposal = by_destination[member]
        result = proposal.get("result")
        lineage = proposal.get("lineage", {})
        if (
            type(result) is not dict
            or type(result.get("alternatives")) is not list
            or type(proposal.get("proposal_id")) is not str
            or type(lineage) is not dict
        ):
            raise OfferShapeError("offer is not a projected offer")
        data.append(
            {
                "alternatives": sorted(
                    (
                        {
                            "rule": cast(
                                "dict[str, object]",
                                lineage.get(alternative["alternative_id"], {}),
                            ).get("rule"),
                            "value": alternative["value"],
                        }
                        for alternative in cast(
                            "list[dict[str, object]]", result["alternatives"]
                        )
                    ),
                    key=canonical,
                ),
                "destination": member,
                "proposal_id": proposal["proposal_id"],
            }
        )
    return data


def target_part(destination: str, field: Mapping[str, object] | None) -> str:
    """The target half for one recorded target field, as a case or a
    decision's `target_field_document` copy holds it (None where the case
    read no such field)."""
    state = "absent" if field is None else field.get("state")
    value = field.get("value") if field is not None and state == "value" else None
    if state in _EMPTY_STATES:
        state = "empty"
    return _digest(
        {
            "destination": destination,
            "part": "target",
            "schema": DECISION_QUESTION_SCHEMA,
            "state": state,
            "value": value,
        }
    )


def value_target_part(destination: str, written: tuple[object]) -> str:
    """The target half a case reads once ``written[0]``, a projected target
    value (None for a clear), is in the field: a verified write is read back
    as exactly the projected value."""
    value = written[0]
    return target_part(
        destination,
        {"state": "empty", "value": None}
        if value is None
        else {"state": "value", "value": value},
    )


def decision_question(
    offer: Sequence[Mapping[str, object]],
    target_field: Mapping[str, object] | None,
    destination: str,
) -> DecisionQuestion | None:
    """One field's question on a case: its current offer and its recorded
    target field. None when the offer has no proposal for the field."""
    members = offer_members(offer, destination)
    if members is None:
        return None
    return DecisionQuestion(
        _digest(
            {
                "members": [
                    {
                        key: value
                        for key, value in member.items()
                        if key != "proposal_id"
                    }
                    for member in members
                ],
                "part": "offer",
                "schema": DECISION_QUESTION_SCHEMA,
            }
        ),
        target_part(destination, target_field),
    )


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
