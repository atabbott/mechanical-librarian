"""The question check's steps 0 to 5 over plain values (ADR-0108)."""

from __future__ import annotations

import dataclasses

import pytest

from mechanical_librarian.domain.decision_question import (
    DecisionQuestion,
    target_part,
    value_target_part,
)
from mechanical_librarian.domain.question_check import (
    CARRY_OUTCOMES,
    CheckedAnswer,
    QuestionStep,
    carry_refusal_outcome,
    check_question,
)

_OFFER = "a" * 64
_SEEN = target_part("title", {"state": "value", "value": "Current Title"})
_ASKED = DecisionQuestion(_OFFER, _SEEN)
_ANSWER = CheckedAnswer(
    destination="title",
    source="human",
    outcome="accept",
    pinned=False,
    carry_eligible=True,
    resume="none",
    plan="none",
    written=None,
    corrective=False,
    offer_digest=_OFFER,
    seen_target_digest=_SEEN,
)


def _check(
    question: DecisionQuestion | None = _ASKED, actionable: bool = True, **changes
) -> QuestionStep:
    return check_question(dataclasses.replace(_ANSWER, **changes), question, actionable)


@pytest.mark.parametrize(
    "changes",
    [{"destination": "cover"}, {"pinned": True}, {"outcome": "pin"}],
    ids=["cover", "pinned", "pin_only"],
)
def test_covers_and_pins_are_skipped_with_no_outcome(changes: dict) -> None:
    assert _check(**changes) == QuestionStep(None)
    # Ahead of every other step.
    assert _check(resume="failed", carry_eligible=False, **changes) == QuestionStep(
        None
    )


def test_step_0_an_open_resume_is_pending() -> None:
    assert _check(resume="open") == QuestionStep("resume_pending")
    assert _check(resume="open", carry_eligible=False) == QuestionStep("resume_pending")


def test_step_0_a_failed_resume_is_closed_and_asked_again() -> None:
    assert _check(resume="failed") == QuestionStep("asked_again", close_resume=True)
    assert _check(resume="failed", plan="in_flight") == QuestionStep(
        "asked_again", close_resume=True
    )


def test_step_1_an_answer_not_carry_eligible_is_asked_again() -> None:
    assert _check(carry_eligible=False) == QuestionStep("asked_again")
    assert _check(carry_eligible=False, plan="in_flight") == QuestionStep("asked_again")


def test_step_2_a_write_in_flight_defers() -> None:
    assert _check(plan="in_flight") == QuestionStep("deferred")
    assert _check(plan="in_flight", question=None) == QuestionStep("deferred")


def test_step_3_a_different_offer_is_a_changed_question() -> None:
    assert _check(DecisionQuestion("b" * 64, _SEEN)) == QuestionStep("question_changed")


@pytest.mark.parametrize(
    "question, actionable",
    [(None, True), (_ASKED, False)],
    ids=["field_absent", "not_actionable"],
)
def test_step_5_a_field_absent_or_not_actionable_is_a_changed_question(
    question: DecisionQuestion | None, actionable: bool
) -> None:
    assert _check(question, actionable) == QuestionStep("question_changed")


def test_an_answer_without_halves_never_matches() -> None:
    assert _check(offer_digest=None, seen_target_digest=None) == QuestionStep(
        "question_changed"
    )


def test_step_3_a_plan_stopped_for_the_owner_never_matches() -> None:
    assert _check(plan="stopped") == QuestionStep("asked_again")
    # A changed offer still reads as a changed question first.
    assert _check(DecisionQuestion("b" * 64, _SEEN), plan="stopped") == QuestionStep(
        "question_changed"
    )


def test_step_3_an_unwritten_answer_expects_the_target_it_saw() -> None:
    moved = DecisionQuestion(
        _OFFER, target_part("title", {"state": "value", "value": "Moved"})
    )

    assert _check(moved) == QuestionStep("target_changed")
    assert _check() == QuestionStep("carried", written=False)


def test_step_3_a_written_answer_expects_its_own_value() -> None:
    written = DecisionQuestion(_OFFER, value_target_part("title", ("Plain Work",)))

    assert _check(written, written=("Plain Work",)) == QuestionStep(
        "carried", written=True
    )
    assert _check(written) == QuestionStep("target_changed")
    assert _check(_ASKED, written=("Plain Work",)) == QuestionStep("target_changed")


def test_step_3_a_written_clear_expects_the_empty_field() -> None:
    empty = DecisionQuestion(_OFFER, target_part("title", {"state": "null"}))

    assert _check(empty, written=(None,), outcome="owner_entered") == QuestionStep(
        "carried", written=True
    )


def test_step_5_an_unwritten_corrective_keep_is_asked_again() -> None:
    assert _check(outcome="keep_current", corrective=True) == QuestionStep(
        "asked_again"
    )
    assert _check(
        DecisionQuestion(_OFFER, value_target_part("title", ("Restored",))),
        outcome="keep_current",
        corrective=True,
        written=("Restored",),
    ) == QuestionStep("carried", written=True)
    assert _check(outcome="keep_current") == QuestionStep("carried")


def test_step_5_an_authored_description_is_asked_again() -> None:
    assert _check(
        destination="description",
        source="authored_description",
        outcome="authored",
        offer_digest=None,
        seen_target_digest=None,
    ) == QuestionStep("asked_again")


@pytest.mark.parametrize(
    "code, outcome",
    [
        ("stale", None),
        ("already_decided", None),
        ("link_changed", None),
        ("offer_changed", None),
        ("invalid", "invalid"),
        ("unsupported", "invalid"),
        ("owner_value_unchanged", "invalid"),
    ],
    ids=[
        "stale",
        "already_decided",
        "link_changed",
        "offer_changed",
        "invalid",
        "unsupported",
        "owner_value_unchanged",
    ],
)
def test_step_5_a_refused_carry(code: str, outcome: str | None) -> None:
    assert carry_refusal_outcome(code) == outcome


def test_every_outcome_is_reached() -> None:
    reached = {
        _check(resume="open").outcome,
        _check(plan="in_flight").outcome,
        _check(carry_eligible=False).outcome,
        _check(None).outcome,
        _check(DecisionQuestion(_OFFER, "c" * 64)).outcome,
        _check().outcome,
        carry_refusal_outcome("invalid"),
    }
    assert reached == set(CARRY_OUTCOMES)
