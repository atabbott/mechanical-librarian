"""One closed, owner-readable classification for a review field."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReviewFieldClassification:
    """The interaction state of one immutable review proposal.

    This is deliberately a projection rule, not new decision authority.  The
    case, durable decisions, safety state, and capability state remain the
    sources of truth; callers supply their already-validated facts.
    """

    state: str
    reason: str
    needs_attention: bool
    editable: bool
    recorded: bool
    navigation_role: str
    preservation_only: bool


def classify_review_field(
    *,
    attention_candidate: bool,
    automatic_outcome: str | None,
    has_human_decision: bool,
    suspended: bool,
    current_value: object,
    proposed_values: tuple[object, ...],
) -> ReviewFieldClassification:
    """Classify a field consistently for queue, detail, and navigation use."""
    preservation_only = bool(proposed_values) and all(
        value == current_value for value in proposed_values
    )
    if has_human_decision:
        return ReviewFieldClassification(
            "human_recorded", "Your recorded decision is retained.", False,
            False, True, "inspect", preservation_only,
        )
    if suspended:
        return ReviewFieldClassification(
            "unsupported", "Automatic handling is suspended pending a safety or external recovery.",
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


__all__ = ["ReviewFieldClassification", "classify_review_field"]
