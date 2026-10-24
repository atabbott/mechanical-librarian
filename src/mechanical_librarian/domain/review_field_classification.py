"""One closed, owner-readable classification for a review field."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

ReviewFieldState = Literal[
    "needs_attention",
    "manual_fallback",
    "automatic_recorded",
    "human_recorded",
    "unsupported",
    "owner_choosable",
]
ReviewNavigationRole = Literal["attention", "inspect"]
ReviewFieldCause = Literal[
    "unsupported",
    "automatic_safety_suspended",
    "external_field_failure",
    "external_connection_failure",
    "external_recovered",
]
_CAUSE_GUIDANCE: dict[ReviewFieldCause, str] = {
    "unsupported": "This field is unavailable for the current service capability.",
    "automatic_safety_suspended": "Automatic handling is suspended pending a safety recovery.",
    "external_field_failure": "This field is waiting for an external service recovery.",
    "external_connection_failure": "This connection is waiting for an external service recovery.",
    "external_recovered": "External recovery completed; this field can be reviewed again.",
}


@dataclass(frozen=True, slots=True)
class ReviewFieldClassification:
    """The interaction state of one immutable review proposal.

    This is deliberately a projection rule, not new decision authority.  The
    case, durable decisions, safety state, and capability state remain the
    sources of truth; callers supply their already-validated facts.
    """

    state: ReviewFieldState
    reason: str
    needs_attention: bool
    editable: bool
    recorded: bool
    navigation_role: ReviewNavigationRole
    preservation_only: bool


def classify_review_field(
    *,
    attention_candidate: bool,
    automatic_outcome: str | None,
    has_human_decision: bool,
    suspended: bool,
    cause: ReviewFieldCause | None = None,
    current_value: object,
    proposed_values: tuple[object, ...],
    override_available: bool = True,
    owner_choosable: bool = False,
    changeable: bool = True,
    owner_entry: bool = False,
) -> ReviewFieldClassification:
    """Classify a field consistently for queue, detail, and navigation use.

    `owner_choosable` is a recording's undecided fill that no shared reason
    queues: it stays editable in its case but is never attention (#1249).
    Every other input decides first. `changeable` says whether any owner
    control could replace a recorded decision, and `owner_entry` whether the
    owner may type a value or choose genres; each changes only the reason
    (ADR-0085).
    """
    preservation_only = bool(proposed_values) and all(
        _canonical_value(value) == _canonical_value(current_value)
        for value in proposed_values
    )
    if has_human_decision:
        # A recorded decision stays revisable for exactly as long as the store
        # will supersede it. `record_human_decision` refuses only while an
        # earlier effect is in flight, so editability reports that same
        # boundary rather than a stricter projection-only rule.
        return ReviewFieldClassification(
            "human_recorded",
            (
                "Your recorded decision is retained. You can change it here."
                if override_available and changeable
                else "Your recorded decision is retained."
                if override_available
                else "Your recorded decision is being applied and cannot be changed yet."
            ),
            False,
            override_available,
            True,
            "inspect",
            preservation_only,
        )
    if cause == "unsupported":
        return ReviewFieldClassification(
            "unsupported",
            _CAUSE_GUIDANCE[cause],
            False,
            False,
            False,
            "inspect",
            preservation_only,
        )
    if automatic_outcome == "abstention":
        return ReviewFieldClassification(
            "manual_fallback",
            "Automatic handling abstained, so this field needs your review.",
            True,
            True,
            False,
            "attention",
            preservation_only,
        )
    if automatic_outcome is not None:
        return ReviewFieldClassification(
            "automatic_recorded",
            (
                "Automatic handling recorded this field; you may inspect or override it."
                if override_available
                else "Automatic handling is being applied and cannot be overridden yet."
            ),
            False,
            override_available,
            True,
            "inspect",
            preservation_only,
        )
    if cause == "automatic_safety_suspended":
        return ReviewFieldClassification(
            "needs_attention",
            _CAUSE_GUIDANCE[cause],
            True,
            True,
            False,
            "attention",
            preservation_only,
        )
    if suspended:
        return ReviewFieldClassification(
            "unsupported",
            _CAUSE_GUIDANCE[cause or "automatic_safety_suspended"],
            False,
            False,
            False,
            "inspect",
            preservation_only,
        )
    if attention_candidate:
        return ReviewFieldClassification(
            "needs_attention",
            "This field needs your review.",
            True,
            True,
            False,
            "attention",
            preservation_only,
        )
    if owner_choosable:
        return ReviewFieldClassification(
            "owner_choosable",
            "No review is needed. You can still keep the current value or "
            "choose a proposed one.",
            False,
            True,
            False,
            "inspect",
            preservation_only,
        )
    return ReviewFieldClassification(
        "unsupported",
        "There is no other value to choose from. You can enter your own."
        if owner_entry
        else "There is no owner decision available for this field.",
        False,
        False,
        False,
        "inspect",
        preservation_only,
    )


def _canonical_value(value: object) -> object:
    """Compare the domain value, not the API proposal wrapper."""
    if isinstance(value, Mapping) and set(value) == {"kind", "value"}:
        return value["value"]
    return value


__all__ = [
    "ReviewFieldClassification",
    "ReviewFieldState",
    "ReviewNavigationRole",
    "ReviewFieldCause",
    "classify_review_field",
]
