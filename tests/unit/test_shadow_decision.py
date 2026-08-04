from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from mechanical_librarian.domain import shadow_decision as shadow_decision_module
from mechanical_librarian.domain.shadow_decision import (
    DECISION_POLICY_SHA256,
    DECISION_POLICY_VERSION,
    ShadowDecisionError,
    ShadowDecisionSet,
    decide_shadow_packet,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
)
from mechanical_librarian.domain.shadow_review import (
    ReviewerProfileIdentity,
    ShadowReviewPacket,
    ShadowReviewRequest,
    ShadowReviewValueError,
)
from mechanical_librarian.domain.shadow_review_actions import (
    ACTION_POLICY_VERSION,
    PolicyAction,
    PolicyActionCatalogue,
    PolicyActionCompilationError,
    _action_id,
    _exclude,
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


def _local_title(value: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=0,
    )


def _combination_packet(
    *,
    current_title: str | None = None,
    providers: tuple | None = None,
) -> ShadowReviewPacket:
    report = _bibliographic_report(
        current_title=current_title,
        local_claims=(_local_title("Synthetic Work"),),
        providers=(
            providers
            if providers is not None
            else (
                _source(
                    "hardcover",
                    "edition-1",
                    titles=("Synthetic Work",),
                    series="Synthetic Series",
                ),
            )
        ),
    )
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    return ShadowReviewPacket.create(case, proposal, report)


def _catalogue_with_witness_variant(
    packet: ShadowReviewPacket,
    *,
    destination: str,
    variant: str,
) -> PolicyActionCatalogue:
    catalogue = compile_shadow_review_actions(packet)
    proposal = next(
        value for value in packet.proposals if value["destination"] == destination
    )
    combination_id = packet.bibliographic_combinations[0]["combination_id"]
    witness = next(
        action
        for action in catalogue.actions
        if action.proposal_id == proposal["proposal_id"]
        and action.recommendation != "manual"
        and combination_id in action.combination_ids
    )
    actions = [value for value in catalogue.actions if value is not witness]
    exclusions = list(catalogue.exclusions)
    if variant == "excluded":
        exclusions.append(_exclude(witness, {"synthetic_witness_exclusion"}))
    elif variant == "unbound":
        actions.append(replace(witness, combination_ids=()))
    elif variant in {"wrong_kind", "wrong_candidate"}:
        candidate_id = "alternative_" + "f" * 64
        recommendation = "recommend_candidate"
        actions.append(
            PolicyAction(
                _action_id(proposal["proposal_id"], recommendation, candidate_id),
                proposal["proposal_id"],
                destination,
                recommendation,
                candidate_id,
                witness.citation_ids,
                witness.combination_ids,
            )
        )
    elif variant != "missing":
        raise AssertionError("unknown witness variant")
    return PolicyActionCatalogue(
        tuple(sorted(actions, key=lambda value: value.action_id)),
        tuple(sorted(exclusions, key=lambda value: value.exclusion_id)),
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

    def test_supported_title_projects_through_exact_unsupported_witnesses(self) -> None:
        packet = _combination_packet()
        self.assertEqual(1, len(packet.bibliographic_combinations))
        catalogue = compile_shadow_review_actions(packet)
        decisions = {
            value.destination: value for value in decide_shadow_packet(packet).decisions
        }

        title = decisions["title"]
        self.assertEqual("recommend_candidate", title.recommendation)
        self.assertEqual("unique_supported_candidate", title.reason_code)
        selected = catalogue.action_for(
            title.proposal_id,
            title.recommendation,
            title.candidate_id,
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.action_id, title.action_id)
        self.assertEqual(selected.citation_ids, title.citation_ids)
        self.assertEqual(selected.combination_ids, title.combination_ids)

        for destination in ("subtitle", "series"):
            with self.subTest(destination=destination):
                sibling = decisions[destination]
                manual = catalogue.action_for(sibling.proposal_id, "manual", None)
                self.assertIsNotNone(manual)
                assert manual is not None
                self.assertEqual(manual.action_id, sibling.action_id)
                self.assertEqual("manual", sibling.recommendation)
                self.assertEqual("unsupported_destination", sibling.reason_code)
                self.assertEqual(manual.citation_ids, sibling.citation_ids)
                self.assertEqual((), sibling.combination_ids)

    def test_supported_title_preservation_projects_through_exact_witnesses(self) -> None:
        decisions = {
            value.destination: value
            for value in decide_shadow_packet(
                _combination_packet(current_title="Synthetic Work")
            ).decisions
        }
        self.assertEqual("recommend_keep_current", decisions["title"].recommendation)
        self.assertEqual(
            "unique_supported_preservation", decisions["title"].reason_code
        )
        for destination in ("subtitle", "series"):
            self.assertEqual("manual", decisions[destination].recommendation)
            self.assertEqual(
                "unsupported_destination", decisions[destination].reason_code
            )

    def test_missing_excluded_wrong_or_unbound_witness_keeps_title_manual(self) -> None:
        packet = _combination_packet()
        controls = (
            ("series", "missing"),
            ("series", "excluded"),
            ("series", "unbound"),
            ("subtitle", "wrong_kind"),
            ("series", "wrong_candidate"),
        )
        for destination, variant in controls:
            with self.subTest(destination=destination, variant=variant):
                catalogue = _catalogue_with_witness_variant(
                    packet,
                    destination=destination,
                    variant=variant,
                )
                with patch(
                    "mechanical_librarian.domain.shadow_decision."
                    "compile_shadow_review_actions",
                    return_value=catalogue,
                ):
                    decisions = {
                        value.destination: value
                        for value in decide_shadow_packet(packet).decisions
                    }
                self.assertEqual("manual", decisions["title"].recommendation)
                self.assertEqual(
                    "bibliographic_combination_unresolved",
                    decisions["title"].reason_code,
                )
                for sibling in ("subtitle", "series"):
                    self.assertEqual("manual", decisions[sibling].recommendation)
                    self.assertEqual(
                        "unsupported_destination", decisions[sibling].reason_code
                    )

    def test_unbound_eligible_title_does_not_preempt_exact_combination_match(self) -> None:
        packet = _combination_packet()
        catalogue = compile_shadow_review_actions(packet)
        title_proposal = next(
            value for value in packet.proposals if value["destination"] == "title"
        )
        title_action = next(
            value
            for value in catalogue.actions
            if value.proposal_id == title_proposal["proposal_id"]
            and value.recommendation == "recommend_candidate"
        )
        competing = PolicyAction(
            _action_id(
                title_action.proposal_id,
                "recommend_candidate",
                "alternative_" + "f" * 64,
            ),
            title_action.proposal_id,
            title_action.destination,
            "recommend_candidate",
            "alternative_" + "f" * 64,
            title_action.citation_ids,
            title_action.combination_ids,
        )
        original = shadow_decision_module._eligible_actions

        def ambiguous(
            packet_value: ShadowReviewPacket,
            catalogue_value: PolicyActionCatalogue,
            proposal: dict[str, object],
        ) -> tuple[PolicyAction, ...]:
            eligible = original(packet_value, catalogue_value, proposal)
            return (
                (*eligible, competing)
                if proposal["destination"] == "title"
                else eligible
            )

        with patch(
            "mechanical_librarian.domain.shadow_decision._eligible_actions",
            side_effect=ambiguous,
        ):
            title = next(
                value
                for value in decide_shadow_packet(packet).decisions
                if value.destination == "title"
            )
        self.assertEqual("recommend_candidate", title.recommendation)
        self.assertEqual("unique_supported_candidate", title.reason_code)

    def test_identical_fully_witnessed_combinations_select_title_once(self) -> None:
        packet = _combination_packet(
            providers=(
                _source(
                    "hardcover",
                    "edition-1",
                    titles=("Synthetic Work",),
                    subtitle="Synthetic Subtitle",
                ),
                _source(
                    "open_library",
                    "edition-2",
                    titles=("Synthetic Work",),
                    series="Synthetic Series",
                ),
            )
        )
        self.assertEqual(2, len(packet.bibliographic_combinations))
        decisions = {
            value.destination: value for value in decide_shadow_packet(packet).decisions
        }
        self.assertEqual("recommend_candidate", decisions["title"].recommendation)
        self.assertEqual(
            "unique_supported_candidate", decisions["title"].reason_code
        )
        self.assertEqual(
            tuple(
                value["combination_id"]
                for value in packet.bibliographic_combinations
            ),
            decisions["title"].combination_ids,
        )

    def test_identical_combination_projection_is_provider_order_invariant(self) -> None:
        providers = (
            _source(
                "hardcover",
                "edition-1",
                titles=("Synthetic Work",),
                subtitle="Synthetic Subtitle",
            ),
            _source(
                "open_library",
                "edition-2",
                titles=("Synthetic Work",),
                series="Synthetic Series",
            ),
        )
        first = decide_shadow_packet(_combination_packet(providers=providers))
        second = decide_shadow_packet(
            _combination_packet(providers=tuple(reversed(providers)))
        )
        self.assertEqual(first.to_json(), second.to_json())

    def test_packet_boundary_rejects_unreachable_combination_shapes(self) -> None:
        packet = _combination_packet()
        mutations = (
            lambda selections: selections.pop(),
            lambda selections: selections.reverse(),
            lambda selections: selections.__setitem__(
                1, json.loads(json.dumps(selections[0]))
            ),
            lambda selections: selections[0].__setitem__("destination", "authors"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                data = json.loads(packet.to_json())
                mutate(data["bibliographic_combinations"][0]["selections"])
                with self.assertRaises(ShadowReviewValueError):
                    _rebuild(data)

    def test_catalogue_boundary_rejects_duplicate_action_keys(self) -> None:
        catalogue = compile_shadow_review_actions(_combination_packet())
        with self.assertRaises(PolicyActionCompilationError):
            PolicyActionCatalogue(
                tuple(
                    sorted(
                        (*catalogue.actions, catalogue.actions[0]),
                        key=lambda value: value.action_id,
                    )
                ),
                catalogue.exclusions,
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

    def test_v4_policy_identity_rejects_v3_replay(self) -> None:
        self.assertEqual(
            "deterministic-shadow-decision-policy-v4", DECISION_POLICY_VERSION
        )
        self.assertEqual(
            "b889ef93baeff7f326fb997fd8883e47b066dd894d468ee4b28cfe4bbb12da6d",
            DECISION_POLICY_SHA256,
        )
        packet = _packet()
        document = json.loads(decide_shadow_packet(packet).to_json())
        document["policy_version"] = "deterministic-shadow-decision-policy-v3"
        document["policy_sha256"] = "3fb6b1daf0ecad7009400ecaa3ab5360e6083c1b79bcb88c4d62c43755091b3a"
        core = {
            key: value
            for key, value in document.items()
            if key != "decision_set_id"
        }
        document["decision_set_id"] = "shadow_decision_set_" + hashlib.sha256(
            json.dumps(
                core,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with self.assertRaises(ShadowDecisionError):
            ShadowDecisionSet.from_json(
                json.dumps(
                    document,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
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
