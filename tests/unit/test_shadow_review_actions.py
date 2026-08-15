from __future__ import annotations

import copy
import inspect
import json
import unittest

from hypothesis import given, settings
from hypothesis import strategies as st

from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
)
from mechanical_librarian.domain.shadow_review_actions import (
    _DESTINATION_POLICIES,
    ACTION_CATALOGUE_SCHEMA,
    ACTION_POLICY_VERSION,
    PolicyActionCatalogue,
    PolicyActionCompilationError,
    compile_shadow_review_actions,
)
from tests.unit.shadow_packet_fixtures import synthetic_packet
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source,
)
from tests.unit.test_proposal_set import _report


def _packet_data() -> dict[str, object]:
    return synthetic_packet().to_data()


def _packet(data: dict[str, object]) -> ShadowReviewPacket:
    return ShadowReviewPacket(
        data["review_case_id"],
        tuple(data["proposals"]),
        tuple(data["citations"]),
        tuple(data["correlations"]),
        data["schema_version"],
        data["bibliographic_relationship_schema_version"],
        tuple(data["bibliographic_combinations"]),
    )


class ShadowReviewActionCompilerTests(unittest.TestCase):
    def test_destination_policy_is_exhaustive(self) -> None:
        self.assertEqual(
            {destination.value for destination in TargetDestination},
            set(_DESTINATION_POLICIES),
        )

    @settings(max_examples=30, deadline=None)
    @given(st.binary(min_size=32, max_size=32))
    def test_action_and_exclusion_ids_ignore_opaque_case_digest(
        self,
        digest: bytes,
    ) -> None:
        first = _packet_data()
        first["proposals"][0]["candidates"][0]["assessment"] = "unknown"
        second = json.loads(json.dumps(first))
        second["review_case_id"] = "review_case_" + digest.hex()

        first_catalogue = compile_shadow_review_actions(_packet(first))
        second_catalogue = compile_shadow_review_actions(_packet(second))

        self.assertEqual(
            [value.action_id for value in first_catalogue.actions],
            [value.action_id for value in second_catalogue.actions],
        )
        self.assertEqual(
            [value.exclusion_id for value in first_catalogue.exclusions],
            [value.exclusion_id for value in second_catalogue.exclusions],
        )

    def test_compiler_boundary_accepts_only_the_packet(self) -> None:
        self.assertEqual(
            ["packet"],
            list(inspect.signature(compile_shadow_review_actions).parameters),
        )

    def test_catalogue_rejects_forged_contract_versions(self) -> None:
        catalogue = compile_shadow_review_actions(
            synthetic_packet()
        )

        with self.assertRaises(PolicyActionCompilationError):
            PolicyActionCatalogue(
                catalogue.actions,
                catalogue.exclusions,
                ACTION_POLICY_VERSION,
                ACTION_CATALOGUE_SCHEMA + "-forged",
            )

    def test_manual_proposal_compiles_candidate_and_explicit_manual_independently(
        self,
    ) -> None:
        packet = synthetic_packet()

        catalogue = compile_shadow_review_actions(packet)

        actions = {
            (value.recommendation, value.candidate_id)
            for value in catalogue.actions
        }
        candidate_id = packet.proposals[0]["candidates"][0]["candidate_id"]
        self.assertEqual(
            {
                ("manual", None),
                ("recommend_candidate", candidate_id),
            },
            actions,
        )
        self.assertEqual(
            "manual",
            packet.proposals[0]["decision_summary"]["evidence_sufficiency"],
        )

    def test_cover_candidate_uses_cover_scope_and_not_applicable_binding(
        self,
    ) -> None:
        artifact = CoverArtifact.create(
            b"cover",
            b"preview",
            "image/png",
            10,
            20,
        )
        source = CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:cover"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.RETRIEVED,
            artifact,
            provenance=CoverProvenance(
                "catalogue",
                CoverDesignation.WORK,
                "catalogue-work",
            ),
        )
        report = _report(covers=(source,))
        proposal_set = evaluate_report(report)
        packet = ShadowReviewPacket.create(
            ReviewCase.create(proposal_set, report),
            proposal_set,
            report,
        )
        cover_proposal = next(
            proposal
            for proposal in packet.proposals
            if proposal["destination"] == TargetDestination.COVER.value
        )

        catalogue = compile_shadow_review_actions(packet)

        self.assertIsNotNone(
            catalogue.action_for(
                cover_proposal["proposal_id"],
                "recommend_candidate",
                cover_proposal["candidates"][0]["candidate_id"],
            )
        )

    def test_keep_current_rejects_unbound_preservation_evidence(self) -> None:
        data = _packet_data()
        proposal = data["proposals"][0]
        proposal["current"] = {
            "destination": proposal["destination"],
            "state": "value",
            "value": "Current title",
        }
        candidate = proposal["candidates"][0]
        candidate["assessment"] = "preserves_target"
        candidate["correlations"] = []

        catalogue = compile_shadow_review_actions(_packet(data))

        self.assertIsNone(
            catalogue.action_for(
                proposal["proposal_id"],
                "recommend_keep_current",
                None,
            )
        )
        keep_exclusion = next(
            exclusion
            for exclusion in catalogue.exclusions
            if exclusion.recommendation == "recommend_keep_current"
        )
        self.assertIn("correlation_binding_missing", keep_exclusion.reasons)

    def test_unknown_candidate_fails_closed_with_stable_exclusion_identity(
        self,
    ) -> None:
        first = _packet_data()
        first["proposals"][0]["candidates"][0]["assessment"] = {
            "confidence": "synthetic",
            "problems": [],
        }
        second = json.loads(json.dumps(first))
        second["review_case_id"] = "review_case_" + ("f" * 64)

        first_catalogue = compile_shadow_review_actions(_packet(first))
        second_catalogue = compile_shadow_review_actions(_packet(second))

        self.assertEqual(
            ["manual"],
            [value.recommendation for value in first_catalogue.actions],
        )
        first_exclusion = next(
            value
            for value in first_catalogue.exclusions
            if value.recommendation == "recommend_candidate"
        )
        second_exclusion = next(
            value
            for value in second_catalogue.exclusions
            if value.recommendation == "recommend_candidate"
        )
        self.assertEqual(
            ("assessment_unknown",),
            first_exclusion.reasons,
        )
        self.assertEqual(
            first_exclusion.exclusion_id,
            second_exclusion.exclusion_id,
        )
        self.assertEqual(
            first_catalogue.actions[0].action_id,
            second_catalogue.actions[0].action_id,
        )

    def test_language_keep_current_uses_compatible_cited_source_evidence(
        self,
    ) -> None:
        data = _packet_data()
        proposal = data["proposals"][0]
        proposal["destination"] = "language"
        proposal["current"] = {
            "destination": "language",
            "state": "value",
            "value": "EN-US",
        }
        candidate = proposal["candidates"][0]
        candidate["assessment"] = "replaces_target"
        candidate["scope"] = "edition"
        candidate["value"] = {"kind": "text", "value": "EN-US"}
        candidate["decision_summary"]["evidence_scope"] = "edition"
        candidate["decision_summary"]["selected_manifestation_binding"] = (
            "established"
        )

        catalogue = compile_shadow_review_actions(_packet(data))

        keep = next(
            value
            for value in catalogue.actions
            if value.recommendation == "recommend_keep_current"
        )
        self.assertEqual(tuple(candidate["evidence_references"]), keep.citation_ids)

    def test_preserving_candidate_binds_to_preserve_combination(self) -> None:
        report = _bibliographic_report(
            current_title="secret history",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("Secret History",),
                    subtitle="A Mistborn Novella",
                ),
            ),
        )
        proposal_set = evaluate_report(report)
        packet = ShadowReviewPacket.create(
            ReviewCase.create(proposal_set, report),
            proposal_set,
            report,
        )

        catalogue = compile_shadow_review_actions(packet)

        candidate_action = next(
            value
            for value in catalogue.actions
            if value.destination == "title"
            and value.recommendation == "recommend_candidate"
        )
        self.assertEqual(
            (packet.bibliographic_combinations[0]["combination_id"],),
            candidate_action.combination_ids,
        )

    def test_catalogue_is_stable_under_equivalent_packet_ordering(self) -> None:
        report = _bibliographic_report(
            current_title="Secret History: Mistborn",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("Secret History",),
                    series="Mistborn",
                ),
            ),
        )
        proposal_set = evaluate_report(report)
        packet = ShadowReviewPacket.create(
            ReviewCase.create(proposal_set, report),
            proposal_set,
            report,
        )
        reordered = copy.deepcopy(packet)
        for attribute in (
            "proposals",
            "citations",
            "correlations",
            "bibliographic_combinations",
        ):
            object.__setattr__(
                reordered,
                attribute,
                tuple(reversed(getattr(reordered, attribute))),
            )

        self.assertEqual(
            compile_shadow_review_actions(packet).to_json(),
            compile_shadow_review_actions(reordered).to_json(),
        )




    def test_excluded_candidate_compiles_only_manual_action(self) -> None:
        data = _packet_data()
        candidate = data["proposals"][0]["candidates"][0]
        candidate["assessment"] = {"confidence": "synthetic", "problems": []}

        catalogue = compile_shadow_review_actions(_packet(data))

        proposal_id = data["proposals"][0]["proposal_id"]
        self.assertIsNone(
            catalogue.action_for(
                proposal_id, "recommend_candidate", candidate["candidate_id"]
            )
        )
        self.assertIsNotNone(catalogue.action_for(proposal_id, "manual", None))
        self.assertTrue(
            any(value.proposal_id == proposal_id for value in catalogue.exclusions)
        )

    def test_candidate_action_binds_complete_citation_closure(self) -> None:
        data = _packet_data()
        candidate = data["proposals"][0]["candidates"][0]
        second_citation = "synthetic_citation_" + ("9" * 64)
        data["citations"].append(
            {**data["citations"][0], "citation_id": second_citation}
        )
        candidate["evidence_references"].append(second_citation)

        action = compile_shadow_review_actions(_packet(data)).action_for(
            data["proposals"][0]["proposal_id"],
            "recommend_candidate",
            candidate["candidate_id"],
        )

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(
            tuple(candidate["evidence_references"]), action.citation_ids
        )

    def test_manual_action_needs_no_manufactured_citations(self) -> None:
        data = _packet_data()
        data["proposals"][0]["candidates"] = []
        data["citations"] = []
        data["correlations"] = []

        action = compile_shadow_review_actions(_packet(data)).action_for(
            data["proposals"][0]["proposal_id"], "manual", None
        )

        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual((), action.citation_ids)

    def test_admission_is_crossed_with_destination_not_historical_frequency(
        self,
    ) -> None:
        for destination, scope, binding in (
            ("authors", "work", "associated_work_only"),
            ("language", "edition", "established"),
            ("publisher", "edition", "established"),
        ):
            with self.subTest(destination=destination):
                data = _packet_data()
                proposal = data["proposals"][0]
                proposal["destination"] = destination
                candidate = proposal["candidates"][0]
                candidate["scope"] = scope
                candidate["decision_summary"]["evidence_scope"] = scope
                candidate["decision_summary"][
                    "selected_manifestation_binding"
                ] = binding

                catalogue = compile_shadow_review_actions(_packet(data))

                self.assertTrue(
                    any(
                        value.recommendation == "recommend_candidate"
                        for value in catalogue.actions
                    )
                )

    def test_negative_controls_fail_closed_in_structurally_feasible_fields(
        self,
    ) -> None:
        controls = []

        title = _packet_data()
        title["proposals"][0]["candidates"][0]["assessment"] = "unknown"
        controls.append(("title", title, "assessment_unknown"))

        isbn = _packet_data()
        isbn_proposal = isbn["proposals"][0]
        isbn_proposal["destination"] = "isbn"
        isbn_candidate = isbn_proposal["candidates"][0]
        isbn_candidate["scope"] = "edition"
        isbn_candidate["decision_summary"]["evidence_scope"] = "edition"
        isbn_candidate["decision_summary"][
            "selected_manifestation_binding"
        ] = "established"
        isbn_candidate["decision_summary"]["support"]["local_evidence"] = 0
        controls.append(("isbn", isbn, "evidence_support_absent"))

        publication = _packet_data()
        publication_proposal = publication["proposals"][0]
        publication_proposal["destination"] = "publishedDate"
        publication_candidate = publication_proposal["candidates"][0]
        publication_candidate["scope"] = "edition"
        publication_candidate["value"] = {"kind": "text", "value": "2001-02-03"}
        publication_candidate["date_event"] = "manifestation_publication"
        publication_candidate["date_precision"] = "day"
        publication_candidate["correlations"] = []
        publication_candidate["decision_summary"]["evidence_scope"] = "edition"
        publication_candidate["decision_summary"][
            "selected_manifestation_binding"
        ] = "established"
        publication_candidate["decision_summary"]["publication"] = {
            "destination": "publishedDate",
            "event": "manifestation_publication",
            "logical_field": "publication",
            "paired_destination_consistency": "consistent",
            "precision": "day",
            "report_only": True,
            "write_eligibility": "unsupported",
        }
        controls.append(
            ("publishedDate", publication, "correlation_binding_missing")
        )

        for destination, data, reason in controls:
            with self.subTest(destination=destination):
                catalogue = compile_shadow_review_actions(_packet(data))
                self.assertFalse(
                    any(
                        value.recommendation == "recommend_candidate"
                        for value in catalogue.actions
                    )
                )
                self.assertTrue(
                    any(reason in value.reasons for value in catalogue.exclusions)
                )


if __name__ == "__main__":
    unittest.main()
