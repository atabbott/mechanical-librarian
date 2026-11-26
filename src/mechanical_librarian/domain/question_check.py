"""The question check's steps for one record answer (ADR-0108, "The question check").

At the first projection of a book's case or episode that is not the record
answer's, each text field and the description with a record row is checked
once: carried when the new case asks the same question, otherwise asked again
or left for a retry. `check_question` is steps 0 to 5 over plain values the
store reads from the ledger and the staged case; it writes nothing. Pinned and
pin-only answers and covers are skipped with no outcome (contract T6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from mechanical_librarian.domain.decision_question import (
    DecisionQuestion,
    value_target_part,
)

# `resume_pending` and `deferred` are provisional, replaced by the retry's
# outcome; every other outcome is final.
CARRY_OUTCOMES: Final = (
    "carried",
    "resume_pending",
    "deferred",
    "asked_again",
    "question_changed",
    "target_changed",
    "invalid",
)
PROVISIONAL_CARRY_OUTCOMES: Final = frozenset({"resume_pending", "deferred"})
# Refusals of the carried row that a retry of the same check may not see
# again: the case moved, a decision landed, the link or the offer changed.
# Any other refusal of the constructor is final (contract T6, Q11).
_TRANSIENT_REFUSALS: Final = frozenset(
    {"stale", "already_decided", "link_changed", "offer_changed"}
)

CarryOutcome = Literal[
    "carried",
    "resume_pending",
    "deferred",
    "asked_again",
    "question_changed",
    "target_changed",
    "invalid",
]


@dataclass(frozen=True, slots=True)
class CheckedAnswer:
    """The record answer as the check reads it.

    ``resume`` is the field's open request: none, open, or open with a
    failure as its latest event. ``plan`` is the carry chain's plan state
    that matters: one in flight (planned, claimed, applying, reconciling,
    retryable, or stale for its connection), one stopped for the owner, or
    neither. ``written`` holds the projected value a verified plan or a
    no-change row of the chain put in the field (None inside for a clear).
    ``corrective`` marks a keep that goes back on an earlier write.
    """

    destination: str
    source: Literal["human", "authored_description"]
    outcome: str
    pinned: bool
    carry_eligible: bool
    resume: Literal["none", "open", "failed"]
    plan: Literal["none", "in_flight", "stopped"]
    written: tuple[object] | None
    corrective: bool
    offer_digest: str | None
    seen_target_digest: str | None


@dataclass(frozen=True, slots=True)
class QuestionStep:
    """What the check does for one answer.

    ``outcome`` None skips the answer with no row. ``close_resume`` closes a
    failed Resume as superseded and clears carry eligibility, in the same
    transaction (step 0). A ``carried`` step forms the carried row, `settled`
    when ``written``.
    """

    outcome: CarryOutcome | None
    close_resume: bool = False
    written: bool = False


def check_question(
    answer: CheckedAnswer, question: DecisionQuestion | None, actionable: bool
) -> QuestionStep:
    """Steps 0 to 5 for ``answer`` against the new case's ``question`` of the
    field (None where the case offers no such field) and whether the case
    offers the owner a decision there."""
    if answer.destination == "cover" or answer.pinned or answer.outcome == "pin":
        return QuestionStep(None)
    # 0. An open Resume holds the field; a failed one hands it back.
    if answer.resume == "open":
        return QuestionStep("resume_pending")
    if answer.resume == "failed":
        return QuestionStep("asked_again", close_resume=True)
    # 1. Withdrawn, retired, superseded by Book matching, or handed back.
    if not answer.carry_eligible:
        return QuestionStep("asked_again")
    # 2. A write of the answer is still under way.
    if answer.plan == "in_flight":
        return QuestionStep("deferred")
    # 5. An authored description has no offer part to compare.
    if answer.source == "authored_description":
        return QuestionStep("asked_again")
    # 3. Same question: the offer part, then the expected target.
    if (
        question is None
        or not actionable
        or answer.offer_digest is None
        or question.offer_part != answer.offer_digest
    ):
        return QuestionStep("question_changed")
    if answer.plan == "stopped":
        return QuestionStep("asked_again")
    expected = (
        answer.seen_target_digest
        if answer.written is None
        else value_target_part(answer.destination, answer.written)
    )
    if question.target_part != expected:
        return QuestionStep("target_changed")
    # 5. A keep that goes back on a write it never made writes again only
    # where the owner says so.
    if (
        answer.outcome == "keep_current"
        and answer.corrective
        and answer.written is None
    ):
        return QuestionStep("asked_again")
    # 4. Carry.
    return QuestionStep("carried", written=answer.written is not None)


def carry_refusal_outcome(code: str) -> CarryOutcome | None:
    """The outcome of a carried row the constructor refused with ``code``:
    None for a refusal a retry may not meet again (no row; the retry checks
    again), otherwise `invalid`."""
    return None if code in _TRANSIENT_REFUSALS else "invalid"
