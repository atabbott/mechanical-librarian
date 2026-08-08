from __future__ import annotations

import unittest

from mechanical_librarian.domain.automatic_decision import AutomaticDecision, AutomaticDecisionError, AutomaticSafetyState, F19A_QUALIFICATION_MANIFEST_SHA256, QUALIFIED_POLICY_OPTIONS


class AutomaticDecisionTests(unittest.TestCase):
    def test_candidate_is_immutable_and_binds_all_authority_inputs(self) -> None:
        value = AutomaticDecision.create(
            review_case_id="review_case_" + "a" * 64, report_id="report_" + "b" * 64,
            proposal_set_id="proposal_set_" + "c" * 64, proposal_id="review_proposal_" + "d" * 64,
            target_fingerprint="e" * 64, destination="title", outcome="candidate",
            candidate_id="alternative_" + "f" * 64, resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1, rule_id="shadow_rule_" + "1" * 64, policy_category="title_layout",
            policy_option="preserve", qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
            source_outcome_id="shadow_outcome_" + "3" * 64, occurred_at=1,
            bundle_id="automatic_bundle_" + "4" * 64,
        )
        self.assertEqual("automatic_policy", value.to_data()["origin"])
        self.assertEqual("candidate", value.outcome)

    def test_ignore_has_no_candidate(self) -> None:
        with self.assertRaises(AutomaticDecisionError):
            AutomaticDecision.create(
                review_case_id="review_case_" + "a" * 64, report_id="report_" + "b" * 64,
                proposal_set_id="proposal_set_" + "c" * 64, proposal_id="review_proposal_" + "d" * 64,
                target_fingerprint="e" * 64, destination="title", outcome="ignore",
                candidate_id="alternative_" + "f" * 64, resolved_policy_id="resolved_policy_" + "0" * 64,
                policy_generation=1, rule_id="shadow_rule_" + "1" * 64, policy_category="title_layout",
                policy_option="preserve", qualification_manifest_sha256="2" * 64,
                source_outcome_id="shadow_outcome_" + "3" * 64, occurred_at=1,
                bundle_id="automatic_bundle_" + "4" * 64,
            )

    def test_unqualified_imprint_cannot_create_authority(self) -> None:
        with self.assertRaises(AutomaticDecisionError):
            AutomaticDecision.create(
                review_case_id="review_case_" + "a" * 64, report_id="report_" + "b" * 64,
                proposal_set_id="proposal_set_" + "c" * 64, proposal_id="review_proposal_" + "d" * 64,
                target_fingerprint="e" * 64, destination="publisher", outcome="keep_current",
                candidate_id=None, resolved_policy_id="resolved_policy_" + "0" * 64,
                policy_generation=1, rule_id="shadow_rule_" + "1" * 64, policy_category="publisher_preference",
                policy_option="imprint", qualification_manifest_sha256="2" * 64,
                source_outcome_id="shadow_outcome_" + "3" * 64, occurred_at=1,
                bundle_id=None,
            )

    def test_safety_state_fails_closed_per_category_and_globally(self) -> None:
        self.assertFalse(AutomaticSafetyState(False, None, (("title_layout", "safety"),)).permits("title_layout"))
        self.assertFalse(AutomaticSafetyState(False, "identity", ()).permits("date_precision"))
        self.assertFalse(AutomaticSafetyState(True, None, ()).permits("date_precision"))

    def test_qualification_manifest_defers_imprint(self) -> None:
        self.assertNotIn("publisher_preference:imprint", QUALIFIED_POLICY_OPTIONS)
