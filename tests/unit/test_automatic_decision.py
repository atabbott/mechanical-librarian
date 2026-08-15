from __future__ import annotations

import hashlib
import json
import unittest

from mechanical_librarian.domain.automatic_decision import (
    F19A_QUALIFICATION_MANIFEST_SHA256,
    QUALIFIED_POLICY_OPTIONS,
    AutomaticDecision,
    AutomaticDecisionError,
    AutomaticSafetyState,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_QUALIFICATION_CANDIDATE_SHA256,
)


class AutomaticDecisionTests(unittest.TestCase):
    @staticmethod
    def _digest(value: object) -> str:
        return hashlib.sha256(json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")).hexdigest()

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
        self.assertEqual(value, AutomaticDecision.from_json(value.to_json()))

    def test_persisted_decision_requires_exact_canonical_document(self) -> None:
        value = AutomaticDecision.create(
            review_case_id="review_case_" + "a" * 64,
            report_id="report_" + "b" * 64,
            proposal_set_id="proposal_set_" + "c" * 64,
            proposal_id="review_proposal_" + "d" * 64,
            target_fingerprint="e" * 64,
            destination="language",
            outcome="keep_current",
            candidate_id=None,
            resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1,
            rule_id="shadow_rule_" + "1" * 64,
            policy_category="language_rendering",
            policy_option="preserve",
            qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
            source_outcome_id="shadow_outcome_" + "3" * 64,
            occurred_at=1,
            bundle_id=None,
        )
        with self.assertRaises(AutomaticDecisionError):
            AutomaticDecision.from_json(value.to_json() + "\n")
        with self.assertRaises(AutomaticDecisionError):
            AutomaticDecision.from_json(
                value.to_json().replace(
                    '"schema_version":"automatic-decision-v1"',
                    '"schema_version":"automatic-decision-v2"',
                )
            )

    def test_qualified_isbn_decision_binds_the_f20_candidate(self) -> None:
        value = AutomaticDecision.create(
            review_case_id="review_case_" + "a" * 64,
            report_id="report_" + "b" * 64,
            proposal_set_id="proposal_set_" + "c" * 64,
            proposal_id="review_proposal_" + "d" * 64,
            target_fingerprint="e" * 64,
            destination="isbn",
            outcome="candidate",
            candidate_id="alternative_" + "f" * 64,
            resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1,
            rule_id="shadow_rule_" + "1" * 64,
            policy_category="isbn_form",
            policy_option="prefer_isbn13",
            qualification_manifest_sha256=F20_QUALIFICATION_CANDIDATE_SHA256,
            source_outcome_id="shadow_outcome_" + "3" * 64,
            occurred_at=1,
            bundle_id=None,
        )
        self.assertEqual(
            F20_QUALIFICATION_CANDIDATE_SHA256,
            value.qualification_manifest_sha256,
        )
        with self.assertRaises(AutomaticDecisionError):
            AutomaticDecision.create(
                **{
                    key: value
                    for key, value in value._core().items()
                    if key != "qualification_manifest_sha256"
                },
                qualification_manifest_sha256=F19A_QUALIFICATION_MANIFEST_SHA256,
                occurred_at=1,
            )

    def test_prior_f20_decision_remains_readable_without_granting_new_authority(self) -> None:
        current = AutomaticDecision.create(
            review_case_id="review_case_" + "a" * 64,
            report_id="report_" + "b" * 64,
            proposal_set_id="proposal_set_" + "c" * 64,
            proposal_id="review_proposal_" + "d" * 64,
            target_fingerprint="e" * 64,
            destination="genres",
            outcome="candidate",
            candidate_id="alternative_" + "f" * 64,
            resolved_policy_id="resolved_policy_" + "0" * 64,
            policy_generation=1,
            rule_id="shadow_rule_" + "1" * 64,
            policy_category="genres",
            policy_option="add",
            qualification_manifest_sha256=F20_QUALIFICATION_CANDIDATE_SHA256,
            source_outcome_id="shadow_outcome_" + "3" * 64,
            occurred_at=1,
            bundle_id=None,
        )
        prior_digests = (
            "901e25432750d3f02df1cd311f5a7146e56d5686bd4b6bc0ac589e5db3eb221a",
            "ba42a24185273170f024d861c5dc6865baf298a785068800ab0a0f3f4a6da31a",
        )
        for prior_digest in prior_digests:
            prior_core = {
                **current._core(),
                "qualification_manifest_sha256": prior_digest,
            }
            prior = AutomaticDecision(
                decision_id="automatic_decision_" + self._digest(
                    {**prior_core, "occurred_at": 1}
                ),
                **prior_core,
                idempotency_digest=self._digest(prior_core),
                occurred_at=1,
            )
            self.assertEqual(prior, AutomaticDecision.from_json(prior.to_json()))
            with self.assertRaises(AutomaticDecisionError):
                AutomaticDecision.create(
                    **{
                        key: value
                        for key, value in current._core().items()
                        if key != "qualification_manifest_sha256"
                    },
                    qualification_manifest_sha256=prior_digest,
                    occurred_at=2,
                )

        fill_core = {
            **current._core(),
            "policy_option": "fill_empty",
            "qualification_manifest_sha256": prior_digests[0],
        }
        with self.assertRaises(AutomaticDecisionError):
            AutomaticDecision(
                decision_id="automatic_decision_" + "4" * 64,
                **fill_core,
                idempotency_digest="5" * 64,
                occurred_at=1,
            )

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
