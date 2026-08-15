"""One closed, owner-readable classification for a review field."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

ReviewFieldState = Literal[
    "needs_attention", "manual_fallback", "automatic_recorded",
    "human_recorded", "unsupported",
]
ReviewNavigationRole = Literal["attention", "inspect"]
ReviewFieldCause = Literal["unsupported", "automatic_safety_suspended", "external_field_failure", "external_connection_failure", "external_recovered"]
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
) -> ReviewFieldClassification:
    """Classify a field consistently for queue, detail, and navigation use."""
    preservation_only = bool(proposed_values) and all(
        _canonical_value(value) == _canonical_value(current_value)
        for value in proposed_values
    )
    if has_human_decision:
        return ReviewFieldClassification(
            "human_recorded", "Your recorded decision is retained.", False,
            False, True, "inspect", preservation_only,
        )
    if cause == "unsupported":
        return ReviewFieldClassification(
            "unsupported", _CAUSE_GUIDANCE[cause], False, False, False, "inspect", preservation_only,
        )
    if suspended:
        return ReviewFieldClassification(
            "unsupported", _CAUSE_GUIDANCE[cause or "automatic_safety_suspended"],
            False, False, False, "inspect", preservation_only,
        )
    if automatic_outcome == "abstention":
        return ReviewFieldClassification(
            "manual_fallback", "Automatic handling abstained, so this field needs your review.",
            True, True, False, "attention", preservation_only,
        )
    if automatic_outcome is not None:
        return ReviewFieldClassification(
            "automatic_recorded", "Automatic handling recorded this field; you may inspect or override it.",
            False, True, True, "inspect", preservation_only,
        )
    if attention_candidate:
        return ReviewFieldClassification(
            "needs_attention", "This field needs your review.", True, True,
            False, "attention", preservation_only,
        )
    return ReviewFieldClassification(
        "unsupported", "There is no owner decision available for this field.",
        False, False, False, "inspect", preservation_only,
    )


def _canonical_value(value: object) -> object:
    """Compare the domain value, not the API proposal wrapper."""
    if isinstance(value, Mapping) and set(value) == {"kind", "value"}:
        return value["value"]
    return value


__all__ = ["ReviewFieldClassification", "ReviewFieldState", "ReviewNavigationRole", "ReviewFieldCause", "classify_review_field"]
