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
    "unsupported": "Your library service cannot change this field.",
    "automatic_safety_suspended": (
        "Automatic is paused, so this field is waiting for you."
    ),
    "external_field_failure": (
        "This field is waiting for the library to accept changes to it again."
    ),
    "external_connection_failure": (
        "This field is waiting for the library connection to recover."
    ),
    "external_recovered": (
        "The library connection recovered. You can review this field again."
    ),
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
    immaterial_alternatives: bool = False,
    kept_cover: bool = False,
    kept_by_fill_policy: bool = False,
    kept_by_tags_off: bool = False,
    single_source: bool = False,
) -> ReviewFieldClassification:
    """Classify a field consistently for queue, detail, and navigation use.

    `owner_choosable` is an undecided choice that no shared reason queues: a
    recording's fill (#1249) or a kept readable cover (#1310). It stays
    editable in its case but is never attention.
    Every other input decides first. `changeable` says whether any owner
    control could replace a recorded decision, and `owner_entry` whether the
    owner may type a value or choose genres; each changes only the reason
    (ADR-0085). `immaterial_alternatives` says an optional choice is optional
    because its alternatives only trim or extend the current value (#1253), so
    its one statement says why no review is needed (#1450). `kept_cover` says
    the optional choice is a cover kept as it is, so its statement speaks of
    covers rather than values (#1310). `kept_by_fill_policy` says it is
    optional because the owner's setting only fills empty fields and the
    current value passes the floor, `kept_by_tags_off` because the owner's
    tags setting is off, and `single_source` because only one uncorroborated
    source proposes a value (#1310).
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
                "You decided this field. You can change it here."
                if override_available and changeable
                else "You decided this field."
                if override_available
                else "Your decision is being applied and cannot be changed yet."
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
            "This field needs your choice.",
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
                "Automatic decided this field. You can change it here."
                if override_available
                else "Automatic\u2019s change is being applied and cannot be changed yet."
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
            (
                "The proposed values only shorten or extend the current one, so "
                "no review is needed. You can still keep the current value or "
                "choose a proposed one."
                if immaterial_alternatives
                else "Your current cover is kept, so no review is needed. You can "
                "still choose another cover."
                if kept_cover
                else "Your setting only fills empty fields, so the current value is "
                "kept and no review is needed. You can still choose a proposed one."
                if kept_by_fill_policy
                else "Tags are off in your settings, so your tags are left as they are "
                "and no review is needed. You can still choose proposed tags."
                if kept_by_tags_off
                else "Only one source suggests this, and nothing else confirms it, "
                "so it isn't used and no review is needed. You can still choose it."
                if single_source
                else "No review is needed. You can still keep the current value or "
                "choose a proposed one."
            ),
            False,
            True,
            False,
            "inspect",
            preservation_only,
        )
    return ReviewFieldClassification(
        "unsupported",
        "No source offered a different value. You can enter your own."
        if owner_entry
        else "Nothing to decide for this field.",
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
