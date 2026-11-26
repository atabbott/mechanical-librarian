"""`HumanDecision.carried` and the `settled` application (ADR-0108, step 4)."""

from __future__ import annotations

import dataclasses
import json

import pytest

from mechanical_librarian.domain.decision_question import (
    DecisionQuestion,
    decision_question,
    value_target_part,
)
from mechanical_librarian.domain.field_effect import project_decision_value
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    admitted_applications,
    case_question,
    decision_actions,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.audiobook_reports import admitted_audiobook_report
from tests.support.proposal_evidence import (
    _fields,
    _report,
    consensus_local_title,
    consensus_provider_title,
)
from tests.support.review_cases import _report as _plain_report


def _case(target: str, *, providers: tuple[str, ...] = ("Plain Work", "Other Work")):
    title = TargetField(TargetDestination.TITLE, TargetFieldState.VALUE, target)
    report = _report(
        title=target,
        fields=_fields(target, title=title),
        local_claims=(consensus_local_title("Current Title"),),
        providers=tuple(
            consensus_provider_title(provider, value)
            for provider, value in zip(
                ("open_library", "hardcover"), providers, strict=False
            )
        ),
    )
    report = MetadataReport.from_json(report.to_json())
    return ReviewCase.create(evaluate_report(report), report)


def _field(case: ReviewCase, destination: str) -> dict[str, object] | None:
    return next(
        (
            field
            for field in case.target["fields"]
            if field.get("destination") == destination
        ),
        None,
    )


def _proposal(case: ReviewCase, destination: str = "title") -> dict[str, object]:
    return next(
        value for value in case.proposals if value["destination"] == destination
    )


def _answer(
    case: ReviewCase,
    outcome: str,
    value: dict[str, object] | None,
    *,
    key: str = "carried-original-0001",
) -> HumanDecision:
    proposal = _proposal(case)
    return HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome=outcome,
        selected_value=value,
        idempotency_key=key,
        occurred_at=1_000,
    )


def _asked(case: ReviewCase, destination: str = "title") -> DecisionQuestion:
    question = decision_question(case.proposals, _field(case, destination), destination)
    assert question is not None
    return question


def _written(question: DecisionQuestion, value: dict[str, object]) -> DecisionQuestion:
    return DecisionQuestion(
        question.offer_part,
        value_target_part("title", (project_decision_value("title", value),)),
    )


_PLAIN: dict[str, object] = {"kind": "text", "value": "Plain Work"}


def _carry(
    case: ReviewCase,
    original: HumanDecision,
    expected: DecisionQuestion,
    *,
    written: bool,
    episode: int = 7,
) -> HumanDecision:
    return HumanDecision.carried(
        case,
        original,
        offer=case.proposals,
        expected=expected,
        written=written,
        episode=episode,
        occurred_at=2_000,
    )


def test_unwritten_accept_is_carried_with_its_application() -> None:
    case = _case("Current Title")
    original = _answer(case, "accept", _PLAIN)

    carried = _carry(case, original, _asked(case), written=False)

    assert carried.application == "apply"
    assert carried.carried_from == original.decision_id
    assert (carried.outcome, carried.selected_value) == ("accept", _PLAIN)
    assert carried.episode == 7
    assert carried.decision_id != original.decision_id
    assert HumanDecision.from_json(carried.to_json()) == carried
    carried.validate_for(case)


def test_written_accept_is_carried_settled_where_its_value_now_preserves() -> None:
    """a1: once V is written, V preserves the target and the new case's action
    validator would refuse the accept; the question check's equality and the
    original's outcome shape validate the settled copy (contract T10)."""
    before, after = _case("Current Title"), _case("Plain Work")
    original = _answer(before, "accept", _PLAIN)
    preserved = next(
        alternative
        for alternative in _proposal(after)["result"]["alternatives"]
        if alternative["value"] == _PLAIN
    )
    assert preserved["assessment"] == "preserves_target"

    carried = _carry(after, original, _written(_asked(before), _PLAIN), written=True)

    assert (carried.application, carried.review_case_id) == (
        "settled",
        after.review_case_id,
    )
    assert carried.proposal_id == _proposal(after)["proposal_id"]
    carried.validate_for(after)
    with pytest.raises(HumanDecisionError):
        dataclasses.replace(carried, application="apply").validate_for(after)


@pytest.mark.parametrize(
    "outcome", ["keep_current", "reject"], ids=["keep_current", "reject"]
)
def test_keep_and_reject_are_carried_settled(outcome: str) -> None:
    case = _case("Current Title")
    original = _answer(case, outcome, None)

    carried = _carry(case, original, _asked(case), written=False)

    assert (carried.application, carried.selected_value) == ("settled", None)


def test_carrying_a_carried_row_names_its_root() -> None:
    case = _case("Current Title")
    original = _answer(case, "accept", _PLAIN)
    first = _carry(case, original, _asked(case), written=False, episode=3)

    second = _carry(case, first, _asked(case), written=False, episode=9)

    assert second.carried_from == original.decision_id
    # One carried row per visit: the root, the case and the episode.
    assert second.idempotency_digest != first.idempotency_digest
    assert _carry(case, first, _asked(case), written=False, episode=9) == second


def test_a_different_question_is_refused() -> None:
    case = _case("Current Title")
    original = _answer(case, "accept", _PLAIN)
    asked = _asked(case)

    with pytest.raises(HumanDecisionError, match="stale"):
        _carry(
            case, original, DecisionQuestion("0" * 64, asked.target_part), written=False
        )
    with pytest.raises(HumanDecisionError, match="stale"):
        _carry(
            case, original, DecisionQuestion(asked.offer_part, "0" * 64), written=False
        )


def test_an_unwritten_accept_keeps_full_validation() -> None:
    """An unwritten value plans, so the new offer must still offer it (R2)."""
    before = _case("Current Title")
    original = _answer(before, "accept", _PLAIN)
    after = _case("Plain Work")

    with pytest.raises(HumanDecisionError):
        HumanDecision.carried(
            after,
            original,
            offer=after.proposals,
            expected=DecisionQuestion(
                _asked(before).offer_part, _asked(after).target_part
            ),
            written=False,
            episode=1,
            occurred_at=2_000,
        )


@pytest.mark.parametrize(
    "change",
    ["cause", "withdraw", "pin", "episode"],
    ids=["cause", "withdraw", "pin", "episode"],
)
def test_what_is_never_carried_is_refused(change: str) -> None:
    case = _case("Current Title")
    original = _answer(case, "accept", _PLAIN)
    episode = 1
    if change == "cause":
        original = HumanDecision.matching_changed(
            case, original, "identifier", case.proposals, 1_500
        )
    elif change in {"withdraw", "pin"}:
        original = _answer(case, change, None, key=f"carried-{change}-0001")
    else:
        episode = -1

    with pytest.raises(HumanDecisionError, match="invalid"):
        _carry(case, original, _asked(case), written=False, episode=episode)


def test_settled_is_admitted_on_both_media_and_only_on_a_carried_row() -> None:
    report = admitted_audiobook_report()
    audiobook = ReviewCase.create(evaluate_report(report), report)
    case = _case("Current Title")
    assert "settled" not in admitted_applications(case.media_kind)
    assert "settled" not in admitted_applications(audiobook.media_kind)
    carried = _carry(
        case, _answer(case, "keep_current", None), _asked(case), written=False
    )
    carried.validate_for(case)

    document = json.loads(carried.to_json())
    document["carried_from"] = None
    with pytest.raises(HumanDecisionError, match="invalid"):
        HumanDecision.from_json(json.dumps(document))
    ordinary = json.loads(_answer(case, "keep_current", None).to_json())
    ordinary["application"] = "settled"
    with pytest.raises(HumanDecisionError, match="invalid"):
        HumanDecision.from_json(json.dumps(ordinary))


def test_a_settled_carry_validates_on_an_audiobook_case() -> None:
    report = admitted_audiobook_report()
    case = ReviewCase.create(evaluate_report(report), report)
    proposal = next(value for value in case.proposals if decision_actions(value))
    destination = str(proposal["destination"])
    original = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination=destination,
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="carried-audiobook-0001",
        occurred_at=1_000,
    )
    question = case_question(case, case.proposals, destination)
    assert question is not None

    carried = HumanDecision.carried(
        case,
        original,
        offer=case.proposals,
        expected=question,
        written=False,
        episode=2,
        occurred_at=2_000,
    )

    assert carried.application == "settled"
    carried.validate_for(case)


def test_a_new_command_never_names_a_root() -> None:
    case = _case("Current Title")
    carried = _carry(case, _answer(case, "accept", _PLAIN), _asked(case), written=False)

    with pytest.raises(HumanDecisionError, match="stale"):
        carried.validate_new_for(case)


def test_existing_decision_ids_do_not_move() -> None:
    """carried_from enters the request digest only when not null (contract
    T7): an owner request's decision keeps the id it had at human-decision-v8
    (values measured at 881a92c)."""
    report = _plain_report()
    case = ReviewCase.create(evaluate_report(report), report)
    proposal = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )
    value = next(
        alternative["value"]
        for alternative in proposal["result"]["alternatives"]
        if alternative["assessment"] != "preserves_target"
    )
    ids = {}
    for outcome, selected in (("accept", value), ("keep_current", None)):
        decision = HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=str(proposal["destination"]),
            actor="owner",
            outcome=outcome,
            selected_value=selected,
            idempotency_key="domain-decision-0001",
            occurred_at=1_000,
        )
        assert decision.carried_from is None
        ids[outcome] = (decision.decision_id, decision.request_digest)

    assert ids == {
        "accept": (
            "human_decision_b53f9032b155321e8e441706d82e48c80537918ed7600eacca5f6989ac5ffca9",
            "b9cdee37827ad8ff3bcb79793ad02bd7612d0b6169cbb1e03db04539d194cd6d",
        ),
        "keep_current": (
            "human_decision_db4542ef67fe507a2f5c5020774169e6868c7a26e34a843ec8ca16dade151b8f",
            "1c68f51146775e15653c1348bcd2b25e293ce625e232e65ef069b19025a07f33",
        ),
    }
