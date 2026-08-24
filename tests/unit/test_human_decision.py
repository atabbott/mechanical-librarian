from __future__ import annotations

import json

import pytest

from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    decision_actions,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.review_cases import _report


@pytest.fixture
def case() -> ReviewCase:
    report = _report()
    return ReviewCase.create(evaluate_report(report), report)


@pytest.fixture
def proposal(case: ReviewCase) -> dict[str, object]:
    return next(
        value
        for value in case.proposals
        if value["result"]["outcome"] == "unresolved_choice"
    )


def _create(
    case: ReviewCase,
    proposal: dict[str, object],
    outcome: str,
    selected_value: dict[str, object] | None,
    *,
    key: str = "domain-decision-0001",
) -> HumanDecision:
    return HumanDecision.create(
        case,
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=proposal["destination"],
        actor="owner",
        outcome=outcome,
        selected_value=selected_value,
        idempotency_key=key,
        occurred_at=1_000,
    )


@pytest.mark.parametrize("outcome", ("accept", "keep_current", "reject"))
def test_all_three_outcomes_are_canonical_and_exact_version_bound(
    case: ReviewCase, proposal: dict[str, object], outcome: str
) -> None:
    value = (
        proposal["result"]["alternatives"][0]["value"] if outcome == "accept" else None
    )
    decision = _create(
        case,
        proposal,
        outcome,
        value,
        key=f"domain-{outcome}-decision",
    )
    assert HumanDecision.from_json(decision.to_json()) == decision
    decision.validate_for(case)


def test_invalid_selection_nonactionable_field_and_tampering_fail_closed(
    case: ReviewCase, proposal: dict[str, object]
) -> None:
    with pytest.raises(HumanDecisionError) as selection:
        _create(case, proposal, "accept", {"kind": "text", "value": "invented"})
    assert selection.value.code == "invalid_selection"
    resolved = next(
        value
        for value in case.proposals
        if value["result"]["outcome"] in {"keep", "reject", "defer", "ineligible"}
        and value["manual_required"] is False
    )
    with pytest.raises(HumanDecisionError) as unsupported:
        HumanDecision.create(
            case,
            proposal_id=resolved["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination=resolved["destination"],
            actor="owner",
            outcome="reject",
            selected_value=None,
            idempotency_key="domain-resolved-0001",
            occurred_at=1_000,
        )
    assert unsupported.value.code == "unsupported"
    decision = _create(case, proposal, "reject", None)
    tampered = json.loads(decision.to_json())
    tampered["destination"] = "subtitle"
    with pytest.raises(HumanDecisionError):
        HumanDecision.from_json(
            json.dumps(tampered, separators=(",", ":"), sort_keys=True)
        )


@pytest.mark.parametrize(
    ("outcome", "manual_required", "alternatives", "expected"),
    (
        (
            "unresolved_choice",
            False,
            [{"value": {"kind": "text", "value": "new"}}],
            ("keep_current", "accept"),
        ),
        (
            "propose_change",
            False,
            [{"value": {"kind": "text", "value": "new"}}],
            ("keep_current", "accept"),
        ),
        ("acceptable_choice", False, [], ()),
        ("keep", True, [], ()),
        ("keep", False, [], ()),
        ("defer", False, [], ()),
        ("unresolved_choice", False, None, ()),
    ),
)
def test_decision_actions_fail_closed_to_the_exact_human_decision_contract(
    outcome: str,
    manual_required: bool,
    alternatives: object,
    expected: tuple[str, ...],
) -> None:
    proposal: dict[str, object] = {
        "destination": "title",
        "manual_required": manual_required,
        "result": {"outcome": outcome, "alternatives": alternatives},
    }
    assert decision_actions(proposal) == expected


def test_owner_can_replace_an_automatic_actionable_selection(
    case: ReviewCase, proposal: dict[str, object]
) -> None:
    resolved = json.loads(json.dumps(proposal))
    resolved["result"]["outcome"] = "propose_change"
    replacement = type(
        "ResolvedCase",
        (),
        {
            "review_case_id": case.review_case_id,
            "target_fingerprint": case.target_fingerprint,
            "proposals": (resolved,),
        },
    )()
    selected = resolved["result"]["alternatives"][0]["value"]
    decision = HumanDecision.create(
        replacement,
        proposal_id=resolved["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination=resolved["destination"],
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="domain-automatic-override-0001",
        occurred_at=1_000,
    )
    decision.validate_for(replacement)
