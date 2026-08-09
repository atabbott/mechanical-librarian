from __future__ import annotations

import json
import unittest

from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.unit.test_review_case_persistence import _report


class HumanDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        report = _report()
        self.case = ReviewCase.create(evaluate_report(report), report)
        self.proposal = next(
            value
            for value in self.case.proposals
            if value["result"]["outcome"] == "unresolved_choice"
        )

    def create(
        self,
        outcome: str,
        selected_value: dict[str, object] | None,
        *,
        key: str = "domain-decision-0001",
    ) -> HumanDecision:
        return HumanDecision.create(
            self.case,
            proposal_id=self.proposal["proposal_id"],
            target_fingerprint=self.case.target_fingerprint,
            destination=self.proposal["destination"],
            actor="owner",
            outcome=outcome,
            selected_value=selected_value,
            idempotency_key=key,
            occurred_at=1_000,
        )

    def test_all_three_outcomes_are_canonical_and_exact_version_bound(self) -> None:
        selected = self.proposal["result"]["alternatives"][0]["value"]
        for outcome, value in (
            ("accept", selected),
            ("keep_current", None),
            ("reject", None),
        ):
            with self.subTest(outcome=outcome):
                decision = self.create(
                    outcome,
                    value,
                    key=f"domain-{outcome}-decision",
                )
                self.assertEqual(
                    HumanDecision.from_json(decision.to_json()), decision
                )
                decision.validate_for(self.case)

    def test_invalid_selection_nonactionable_field_and_tampering_fail_closed(
        self,
    ) -> None:
        with self.assertRaises(HumanDecisionError) as selection:
            self.create("accept", {"kind": "text", "value": "invented"})
        self.assertEqual(selection.exception.code, "invalid_selection")
        resolved = next(
            value
            for value in self.case.proposals
            if value["result"]["outcome"] in {"keep", "reject", "defer", "ineligible"}
            and value["manual_required"] is False
        )
        with self.assertRaises(HumanDecisionError) as unsupported:
            HumanDecision.create(
                self.case,
                proposal_id=resolved["proposal_id"],
                target_fingerprint=self.case.target_fingerprint,
                destination=resolved["destination"],
                actor="owner",
                outcome="reject",
                selected_value=None,
                idempotency_key="domain-resolved-0001",
                occurred_at=1_000,
            )
        self.assertEqual(unsupported.exception.code, "unsupported")
        decision = self.create("reject", None)
        tampered = json.loads(decision.to_json())
        tampered["destination"] = "subtitle"
        with self.assertRaises(HumanDecisionError):
            HumanDecision.from_json(
                json.dumps(tampered, separators=(",", ":"), sort_keys=True)
            )

    def test_owner_can_replace_an_automatic_actionable_selection(self) -> None:
        resolved = json.loads(json.dumps(self.proposal))
        resolved["result"]["outcome"] = "propose_change"
        case = type(
            "ResolvedCase", (), {
                "review_case_id": self.case.review_case_id,
                "target_fingerprint": self.case.target_fingerprint,
                "proposals": (resolved,),
            },
        )()
        selected = resolved["result"]["alternatives"][0]["value"]
        decision = HumanDecision.create(
            case,
            proposal_id=resolved["proposal_id"],
            target_fingerprint=self.case.target_fingerprint,
            destination=resolved["destination"],
            actor="owner",
            outcome="accept",
            selected_value=selected,
            idempotency_key="domain-automatic-override-0001",
            occurred_at=1_000,
        )
        decision.validate_for(case)


if __name__ == "__main__":
    unittest.main()
