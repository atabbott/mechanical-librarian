from __future__ import annotations

import json
import unittest

from mechanical_librarian.domain.shadow_decision import (
    DECISION_POLICY_SHA256,
    DECISION_POLICY_VERSION,
    ShadowDecisionError,
    ShadowDecisionSet,
    decide_shadow_packet,
)
from mechanical_librarian.domain.shadow_review import (
    ReviewerProfileIdentity,
    ShadowReviewPacket,
    ShadowReviewRequest,
)
from mechanical_librarian.domain.shadow_review_actions import (
    ACTION_POLICY_VERSION,
    compile_shadow_review_actions,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source


def _identity() -> ReviewerProfileIdentity:
    return ReviewerProfileIdentity(
        "a" * 64,
        "codex-cli 0.146.0-alpha.3.1",
        "b" * 64,
        "bubblewrap-v1",
        "gpt-5.6-sol",
        "medium",
        "shadow-review-prompt-v3",
        "codex-shadow-profile-v2",
        "shadow-review-packet-v5",
        "shadow-review-result-v2",
        "qualification-only-policy-v1",
        120,
        65_536,
        536_870_912,
        16,
        131_072,
    )


def _packet(
    destination: str = "title",
    *,
    sufficiency: str = "candidate",
    provider_support: int = 1,
    local_support: int = 1,
    target_only: int = 0,
    candidate_count: int = 1,
) -> ShadowReviewPacket:
    data = json.loads(ShadowReviewRequest.synthetic_canary(_identity()).packet.to_json())
    proposal = data["proposals"][0]
    proposal["destination"] = destination
    proposal["decision_summary"] = {
        "evidence_sufficiency": sufficiency,
        "target_only_preservation": target_only,
    }
    candidate = proposal["candidates"][0]
    work_field = destination in {"title", "authors"}
    candidate["scope"] = "work" if work_field else "edition"
    candidate["decision_summary"]["evidence_scope"] = candidate["scope"]
    candidate["decision_summary"]["selected_manifestation_binding"] = (
        "associated_work_only" if work_field else "established"
    )
    candidate["decision_summary"]["support"] = {
        "independent_provider_correlations": provider_support,
        "local_evidence": local_support,
        "target_only_preservation": 0,
    }
    if destination in {"publishedDate", "publishedYear"}:
        precision = "day" if destination == "publishedDate" else "year"
        candidate["date_event"] = "manifestation_publication"
        candidate["date_precision"] = precision
        candidate["value"] = {
            "kind": "text",
            "value": "2024-08-04" if precision == "day" else "2024",
        }
        data["citations"][0]["fact"].update(
            {
                "date_event": "manifestation_publication",
                "date_precision": precision,
            }
        )
        candidate["decision_summary"]["publication"] = {
            "destination": destination,
            "event": "manifestation_publication",
            "logical_field": "publication",
            "paired_destination_consistency": "consistent",
            "precision": precision,
            "report_only": True,
            "write_eligibility": "unsupported",
        }
    else:
        candidate["date_event"] = None
        candidate["date_precision"] = None
        candidate["decision_summary"]["publication"] = None
    if candidate_count == 0:
        proposal["candidates"] = []
        data["correlations"] = []
    elif candidate_count == 2:
        second = json.loads(json.dumps(candidate))
        second["candidate_id"] = "alternative_5" + "5" * 63
        proposal["candidates"].append(second)
        data["correlations"][0]["candidate_ids"].append(second["candidate_id"])
    return ShadowReviewPacket(
        data["review_case_id"],
        tuple(data["proposals"]),
        tuple(data["citations"]),
        tuple(data["correlations"]),
        data["schema_version"],
        data["bibliographic_relationship_schema_version"],
        tuple(data["bibliographic_combinations"]),
    )


def _rebuild(data: dict[str, object]) -> ShadowReviewPacket:
    return ShadowReviewPacket(
        data["review_case_id"],
        tuple(data["proposals"]),
        tuple(data["citations"]),
        tuple(data["correlations"]),
        data["schema_version"],
        data["bibliographic_relationship_schema_version"],
        tuple(data["bibliographic_combinations"]),
    )


class ShadowDecisionTests(unittest.TestCase):
    def test_supported_fields_select_the_unique_eligible_candidate(self) -> None:
        for destination in (
            "title",
            "authors",
            "language",
            "publisher",
            "publishedDate",
            "publishedYear",
        ):
            with self.subTest(destination=destination):
                packet = _packet(destination)
                result = decide_shadow_packet(packet)
                self.assertEqual(1, len(result.decisions))
                decision = result.decisions[0]
                self.assertEqual("recommend_candidate", decision.recommendation)
                self.assertEqual("unique_supported_candidate", decision.reason_code)
                self.assertIsNotNone(
                    compile_shadow_review_actions(packet).action_for(
                        decision.proposal_id,
                        decision.recommendation,
                        decision.candidate_id,
                    )
                )

    def test_every_unsupported_field_selects_exact_manual_action(self) -> None:
        for destination in (
            "subtitle",
            "narrators",
            "series",
            "genres",
            "description",
            "isbn",
            "asin",
            "explicit",
            "tags",
            "cover",
        ):
            with self.subTest(destination=destination):
                result = decide_shadow_packet(_packet(destination))
                decision = result.decisions[0]
                self.assertEqual("manual", decision.recommendation)
                self.assertEqual("unsupported_destination", decision.reason_code)

    def test_insufficient_conflicting_and_ambiguous_inputs_abstain(self) -> None:
        controls = (
            (_packet(sufficiency="manual"), "evidence_sufficiency_manual"),
            (_packet(provider_support=0), "insufficient_support"),
            (_packet(local_support=0), "insufficient_support"),
            (_packet(target_only=1), "target_only_preservation"),
            (_packet(candidate_count=0), "no_eligible_action"),
            (_packet(candidate_count=2), "ambiguous_eligible_actions"),
        )
        for packet, reason in controls:
            with self.subTest(reason=reason):
                decision = decide_shadow_packet(packet).decisions[0]
                self.assertEqual("manual", decision.recommendation)
                self.assertEqual(reason, decision.reason_code)

    def test_keep_current_requires_one_evidence_positive_preservation(self) -> None:
        data = json.loads(_packet().to_json())
        proposal = data["proposals"][0]
        proposal["decision_summary"]["evidence_sufficiency"] = "keep_current"
        proposal["current"] = {"state": "value", "value": "Synthetic title"}
        proposal["candidates"][0]["assessment"] = "preserves_target"
        decision = decide_shadow_packet(_rebuild(data)).decisions[0]
        self.assertEqual("recommend_keep_current", decision.recommendation)
        self.assertEqual("unique_supported_preservation", decision.reason_code)

        proposal["candidates"][0]["decision_summary"]["support"][
            "independent_provider_correlations"
        ] = 0
        decision = decide_shadow_packet(_rebuild(data)).decisions[0]
        self.assertEqual("manual", decision.recommendation)
        self.assertEqual("insufficient_support", decision.reason_code)

    def test_published_date_precision_refinement_abstains_safely(self) -> None:
        month = json.loads(_packet("publishedDate").to_json())
        month_candidate = month["proposals"][0]["candidates"][0]
        month_candidate["date_precision"] = "month"
        month_candidate["value"] = {"kind": "text", "value": "2024-08"}
        month_candidate["decision_summary"]["publication"]["precision"] = "month"
        self.assertEqual(
            "manual", decide_shadow_packet(_rebuild(month)).decisions[0].recommendation
        )

        timestamp = json.loads(_packet("publishedDate").to_json())
        timestamp["citations"][0]["fact"]["date_precision"] = "timestamp"
        self.assertEqual(
            "manual",
            decide_shadow_packet(_rebuild(timestamp)).decisions[0].recommendation,
        )

        timestamp["proposals"][0]["candidates"][0]["decision_summary"]["support"][
            "independent_provider_correlations"
        ] = 2
        self.assertEqual(
            "recommend_candidate",
            decide_shadow_packet(_rebuild(timestamp)).decisions[0].recommendation,
        )

    def test_active_bibliographic_combination_never_selects_a_partial_set(self) -> None:
        report = _bibliographic_report(
            current_title="Synthetic Work: Synthetic Series",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("Synthetic Work",),
                    series="Synthetic Series",
                ),
            ),
        )
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        self.assertTrue(packet.bibliographic_combinations)
        decisions = decide_shadow_packet(packet).decisions
        constrained = {"title", "subtitle", "series"}
        self.assertTrue(
            all(
                value.recommendation == "manual"
                for value in decisions
                if value.destination in constrained
            )
        )

    def test_identity_replay_and_document_validation_are_canonical(self) -> None:
        packet = _packet()
        first = decide_shadow_packet(packet)
        second = decide_shadow_packet(packet)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertEqual(first, ShadowDecisionSet.from_json(first.to_json(), packet))
        self.assertEqual(DECISION_POLICY_VERSION, first.policy_version)
        self.assertEqual(DECISION_POLICY_SHA256, first.policy_sha256)
        self.assertEqual(ACTION_POLICY_VERSION, first.compiler_policy_version)
        document = json.loads(first.to_json())
        document["decision_set_id"] = "shadow_decision_set_" + "0" * 64
        with self.assertRaises(ShadowDecisionError):
            ShadowDecisionSet.from_json(
                json.dumps(document, separators=(",", ":"), sort_keys=True),
                packet,
            )

    def test_bounded_parser_rejects_noncanonical_and_oversized_documents(self) -> None:
        packet = _packet()
        serialized = decide_shadow_packet(packet).to_json()
        with self.assertRaises(ShadowDecisionError):
            ShadowDecisionSet.from_json(" " + serialized, packet)
        with self.assertRaises(ShadowDecisionError):
            ShadowDecisionSet.from_json(serialized + (" " * 1_000_000), packet)


if __name__ == "__main__":
    unittest.main()
