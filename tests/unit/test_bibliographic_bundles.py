from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionIncompatibleError,
    ReviewCase,
)
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
    ShadowReviewRequest,
    ShadowReviewResult,
    ShadowReviewSemanticError,
    ShadowReviewValueError,
    canonical_json,
)
from mechanical_librarian.domain.shadow_review_actions import (
    compile_shadow_review_actions,
)
from tests.unit.test_proposal_set import _claim, _fields, _provider, _report
from tests.unit.test_shadow_reviews import _identity


def _bibliographic_report(
    *,
    current_title: str,
    providers: tuple,
):
    return _report(
        fields=_fields(
            current_title,
            subtitle=TargetField(
                TargetDestination.SUBTITLE,
                TargetFieldState.MISSING,
            ),
            series=TargetField(
                TargetDestination.SERIES,
                TargetFieldState.MISSING,
            ),
        ),
        providers=providers,
    )


def _source(
    name: str,
    candidate: str,
    *,
    titles: tuple[str, ...],
    edition_title: str | None = None,
    subtitle: str | None = None,
    series: str | None = None,
    sequence: str | None = None,
):
    claims = [
        _claim(
            name,
            candidate,
            ClaimKind.TITLE,
            title,
            scope=ClaimScope.WORK,
            order=index,
        )
        for index, title in enumerate(titles)
    ]
    if edition_title is not None:
        claims.append(
            _claim(
                name,
                candidate,
                ClaimKind.TITLE,
                edition_title,
                scope=ClaimScope.EDITION,
                order=len(claims),
            )
        )
    if subtitle is not None:
        claims.append(
            _claim(
                name,
                candidate,
                ClaimKind.SUBTITLE,
                subtitle,
                scope=ClaimScope.EDITION,
                order=len(claims),
            )
        )
    if series is not None:
        claims.append(
            _claim(
                name,
                candidate,
                ClaimKind.SERIES,
                series,
                scope=ClaimScope.WORK,
                order=len(claims),
            )
        )
    if sequence is not None:
        claims.append(
            _claim(
                name,
                candidate,
                ClaimKind.SERIES_SEQUENCE,
                sequence,
                scope=ClaimScope.WORK,
                order=len(claims),
            )
        )
    return _provider(
        name,
        candidate,
        tuple(claims),
        authority=(
            name
            if name in {"google_books", "hardcover", "open_library"}
            else None
        ),
    )


def _selections(proposal) -> list[dict[str, object]]:
    return [
        selection.to_data()
        for combination in proposal.bibliographic_combinations
        for selection in combination.selections
    ]


class BibliographicBundleTests(unittest.TestCase):
    def test_title_to_subtitle_is_one_explicit_complete_combination(self) -> None:
        report = _bibliographic_report(
            current_title="Secret History: A Mistborn Novella",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("Secret History",),
                    subtitle="A Mistborn Novella",
                ),
            ),
        )

        proposal = evaluate_report(report)

        self.assertEqual(1, len(proposal.bibliographic_combinations))
        selections = proposal.bibliographic_combinations[0].selections
        self.assertEqual(
            tuple(
                (
                    TargetDestination.TITLE,
                    TargetDestination.SUBTITLE,
                    TargetDestination.SERIES,
                )
            ),
            tuple(value.destination for value in selections),
        )
        self.assertEqual("candidate", selections[0].kind.value)
        self.assertEqual("candidate", selections[1].kind.value)
        self.assertEqual("preserve_target", selections[2].kind.value)
        self.assertIsNotNone(selections[2].target_field_fingerprint)

    def test_title_to_series_does_not_duplicate_the_component(self) -> None:
        report = _bibliographic_report(
            current_title="Secret History: Mistborn 3.5",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("Secret History",),
                    series="Mistborn",
                    sequence="3.5",
                ),
            ),
        )

        proposal = evaluate_report(report)
        selected = _selections(proposal)
        title_ids = {
            value["alternative_id"]
            for value in selected
            if value["destination"] == TargetDestination.TITLE.value
            and value["kind"] == "candidate"
        }
        title = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.TITLE
        )
        values_by_id = {
            value.alternative_id: value.value.value for value in title.alternatives
        }

        self.assertEqual({"Secret History"}, {values_by_id[value] for value in title_ids})

    def test_independently_supported_clean_title_can_pair_with_series(self) -> None:
        report = _bibliographic_report(
            current_title="Old title",
            providers=(
                _source(
                    "open_library",
                    "edition-1",
                    titles=("Secret History: A Mistborn Novella",),
                    series="Mistborn",
                ),
                _source(
                    "google_books",
                    "edition-2",
                    titles=("Secret History",),
                ),
                _source(
                    "hardcover",
                    "edition-3",
                    titles=("Secret History",),
                ),
            ),
        )

        proposal = evaluate_report(report)
        title = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.TITLE
        )
        values_by_id = {
            value.alternative_id: value.value.value for value in title.alternatives
        }
        selected_titles = {
            selection.alternative_id
            for combination in proposal.bibliographic_combinations
            for selection in combination.selections
            if selection.destination is TargetDestination.TITLE
            and selection.alternative_id is not None
        }

        self.assertIn("Secret History", {values_by_id[value] for value in selected_titles})
        self.assertNotIn(
            "Secret History: A Mistborn Novella",
            {values_by_id[value] for value in selected_titles},
        )

    def test_ambiguous_work_titles_remain_provenance_not_title_candidates(self) -> None:
        for titles in (
            ("Collected Stories", "Component Story"),
            ("The Shadow", "La Sombra"),
        ):
            with self.subTest(titles=titles):
                report = _bibliographic_report(
                    current_title="Current manifestation title",
                    providers=(
                        _source(
                            "catalogue",
                            "edition-1",
                            titles=titles,
                            series="Archive",
                        ),
                    ),
                )
                proposal = evaluate_report(report)
                title = next(
                    value
                    for value in proposal.results
                    if value.destination is TargetDestination.TITLE
                )

                self.assertTrue(
                    all(
                        alternative.value.value not in titles
                        for alternative in title.alternatives
                    )
                )
                self.assertEqual(
                    set(titles),
                    {
                        claim.value
                        for claim in report.providers[0].candidates[0].claims
                        if claim.kind is ClaimKind.TITLE
                    },
                )

    def test_component_work_title_cannot_replace_manifestation_title(self) -> None:
        report = _bibliographic_report(
            current_title="Collected Stories",
            providers=(
                _source(
                    "open_library",
                    "edition-1",
                    titles=("Component Story",),
                    edition_title="Collected Stories",
                    series="Archive",
                ),
            ),
        )

        proposal = evaluate_report(report)
        title = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.TITLE
        )

        self.assertNotIn(
            "Component Story",
            {value.value.value for value in title.alternatives},
        )
        self.assertIn(
            "Component Story",
            {
                claim.value
                for claim in report.providers[0].candidates[0].claims
                if claim.kind is ClaimKind.TITLE
            },
        )

    def test_no_safe_combination_leaves_affected_results_unresolved(self) -> None:
        report = _bibliographic_report(
            current_title="Mistborn Secret History",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("Mistborn Secret History",),
                    series="Mistborn",
                ),
            ),
        )

        proposal = evaluate_report(report)

        self.assertEqual((), proposal.bibliographic_combinations)
        affected = {
            value.destination: value
            for value in proposal.results
            if value.destination
            in {TargetDestination.TITLE, TargetDestination.SERIES}
        }
        self.assertTrue(
            all(
                value.outcome is ProposalOutcome.UNRESOLVED_CHOICE
                and value.reasons == ("no_safe_bibliographic_combination",)
                for value in affected.values()
            )
        )

    def test_sequence_only_duplication_is_also_withheld(self) -> None:
        report = _bibliographic_report(
            current_title="Secret History 3.5",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=("Secret History 3.5",),
                    series="Mistborn",
                    sequence="3.5",
                ),
            ),
        )

        proposal = evaluate_report(report)

        self.assertEqual((), proposal.bibliographic_combinations)
        self.assertEqual(
            ("no_safe_bibliographic_combination",),
            next(
                value
                for value in proposal.results
                if value.destination is TargetDestination.SERIES
            ).reasons,
        )

    def test_no_safe_combination_requires_manual_reviewer_advice(self) -> None:
        report = _bibliographic_report(
            current_title="Mistborn Secret History",
            providers=(
                _source(
                    "catalogue",
                    "edition-1",
                    titles=(),
                    series="Mistborn",
                ),
            ),
        )
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        title_result = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.TITLE
        )
        self.assertEqual((), title_result.alternatives)
        self.assertIs(ProposalOutcome.DEFER, title_result.outcome)
        self.assertTrue(
            all(
                value["manual_required"]
                for value in case.proposals
                if value["destination"]
                in {
                    TargetDestination.TITLE.value,
                    TargetDestination.SUBTITLE.value,
                    TargetDestination.SERIES.value,
                }
            )
        )
        packet = ShadowReviewPacket.create(case, proposal, report)
        request = ShadowReviewRequest.create(case, _identity(), packet)
        citation = packet.citations[0]["citation_id"]
        self.assertEqual(
            {
                TargetDestination.TITLE.value,
                TargetDestination.SUBTITLE.value,
                TargetDestination.SERIES.value,
            },
            {value["destination"] for value in packet.proposals},
        )
        self.assertEqual(
            [],
            next(
                value["candidates"]
                for value in packet.proposals
                if value["destination"] == TargetDestination.TITLE.value
            ),
        )
        title_packet = next(
            value
            for value in packet.proposals
            if value["destination"] == TargetDestination.TITLE.value
        )
        self.assertEqual(
            {
                "evidence_sufficiency": "manual",
                "target_only_preservation": 1,
            },
            title_packet["decision_summary"],
        )
        self.assertTrue(
            all(
                value["outcome"] == ProposalOutcome.UNRESOLVED_CHOICE.value
                and value["reasons"]
                == ["no_safe_bibliographic_combination"]
                for value in packet.proposals
            )
        )
        recommendations = tuple(
            {
                "candidate_id": (
                    value["candidates"][0]["candidate_id"]
                    if value["candidates"]
                    else None
                ),
                "citations": [citation],
                "problems": [],
                "proposal_id": value["proposal_id"],
                "reasons": ["Candidate is present."],
                "recommendation": (
                    "recommend_candidate"
                    if value["candidates"]
                    else "recommend_keep_current"
                ),
            }
            for value in packet.proposals
        )

        with self.assertRaises(ShadowReviewSemanticError):
            ShadowReviewResult(
                request.review_request_id,
                recommendations,
            ).validate_for(request)
        catalogue = compile_shadow_review_actions(packet)
        affected_proposal_ids = {
            value["proposal_id"]
            for value in packet.proposals
            if "no_safe_bibliographic_combination" in value["reasons"]
        }
        self.assertFalse(
            any(
                action.proposal_id in affected_proposal_ids
                and action.recommendation != "manual"
                for action in catalogue.actions
            )
        )
        self.assertTrue(
            all(
                "no_safe_bibliographic_combination" in exclusion.reasons
                for exclusion in catalogue.exclusions
                if exclusion.proposal_id in affected_proposal_ids
            )
        )
        manual_entries = []
        for value in recommendations:
            action = catalogue.action_for(value["proposal_id"], "manual", None)
            assert action is not None
            manual_entries.append(
                {
                    **value,
                    "candidate_id": None,
                    "citations": list(action.citation_ids),
                    "problems": ["No safe bibliographic combination."],
                    "recommendation": "manual",
                }
            )
        manual = tuple(manual_entries)
        ShadowReviewResult(
            request.review_request_id,
            manual,
        ).validate_for(request)

    def test_combination_identity_round_trips_canonically(self) -> None:
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
        proposal = evaluate_report(report)

        replay = ProposalSet.from_json(proposal.to_json())

        self.assertEqual(proposal, replay)
        self.assertEqual(
            proposal.bibliographic_combinations,
            replay.bibliographic_combinations,
        )

    def test_relationships_do_not_change_per_destination_proposal_ids(
        self,
    ) -> None:
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
        with_relationships = evaluate_report(report)
        without_relationships = ProposalSet.create(
            report_id=with_relationships.report_id,
            target_fingerprint=with_relationships.target_fingerprint,
            results=with_relationships.results,
        )

        self.assertNotEqual(
            with_relationships.proposal_set_id,
            without_relationships.proposal_set_id,
        )
        case_with_relationships = ReviewCase.create(
            with_relationships,
            report,
        )
        case_without_relationships = ReviewCase.create(
            without_relationships,
            report,
        )
        self.assertNotEqual(
            case_with_relationships.review_case_id,
            case_without_relationships.review_case_id,
        )
        self.assertEqual(
            {
                value["destination"]: value["proposal_id"]
                for value in case_with_relationships.proposals
            },
            {
                value["destination"]: value["proposal_id"]
                for value in case_without_relationships.proposals
            },
        )
        packet_with_relationships = ShadowReviewPacket.create(
            case_with_relationships,
            with_relationships,
            report,
        )
        packet_without_relationships = ShadowReviewPacket.create(
            case_without_relationships,
            without_relationships,
            report,
        )
        self.assertNotEqual(
            packet_with_relationships.to_json(),
            packet_without_relationships.to_json(),
        )
        self.assertNotEqual(
            ShadowReviewRequest.create(
                case_with_relationships,
                _identity(),
                packet_with_relationships,
            ).review_request_id,
            ShadowReviewRequest.create(
                case_without_relationships,
                _identity(),
                packet_without_relationships,
            ).review_request_id,
        )

    def test_confirmed_current_title_is_a_preserve_member(self) -> None:
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
        proposal = evaluate_report(report)
        self.assertEqual(proposal, ProposalSet.from_json(proposal.to_json()))
        self.assertEqual(1, len(proposal.bibliographic_combinations))
        combination = proposal.bibliographic_combinations[0]
        title_selection = next(
            value
            for value in combination.selections
            if value.destination is TargetDestination.TITLE
        )
        self.assertEqual("preserve_target", title_selection.kind.value)
        self.assertIsNone(title_selection.alternative_id)
        self.assertIsNotNone(title_selection.target_field_fingerprint)
        self.assertEqual(1, len(combination.source_representations))

        case = ReviewCase.create(proposal, report)
        self.assertEqual(case, ReviewCase.from_json(case.to_json()))
        packet = ShadowReviewPacket.create(case, proposal, report)
        title_packet = next(
            value
            for value in packet.proposals
            if value["destination"] == TargetDestination.TITLE.value
        )
        self.assertNotEqual(
            title_packet["current"]["value"],
            title_packet["candidates"][0]["value"]["value"],
        )
        self.assertEqual(
            {
                "evidence_sufficiency": "keep_current",
                "target_only_preservation": 0,
            },
            title_packet["decision_summary"],
        )
        self.assertEqual(
            packet,
            ShadowReviewPacket.from_json(packet.to_json(), report),
        )
        request = ShadowReviewRequest.create(case, _identity(), packet)
        packet_combination = packet.bibliographic_combinations[0]
        catalogue = compile_shadow_review_actions(packet)
        recommendations = []
        for proposal_entry in packet.proposals:
            destination = proposal_entry["destination"]
            selection = next(
                value
                for value in packet_combination["selections"]
                if value["destination"] == destination
            )
            recommendation = {
                "candidate_id": (
                    selection["alternative_id"]
                    if selection["kind"] == "candidate"
                    else None
                ),
                "problems": [],
                "proposal_id": proposal_entry["proposal_id"],
                "reasons": ["Exact allowed bibliographic combination."],
                "recommendation": (
                    "recommend_candidate"
                    if selection["kind"] == "candidate"
                    else "recommend_keep_current"
                ),
            }
            action = catalogue.action_for(
                recommendation["proposal_id"],
                recommendation["recommendation"],
                recommendation["candidate_id"],
            )
            assert action is not None
            recommendation["citations"] = list(action.citation_ids)
            recommendations.append(recommendation)
        result = ShadowReviewResult(
            request.review_request_id,
            tuple(recommendations),
        )
        result.validate_for(request)
        self.assertEqual(
            packet_combination["combination_id"],
            result.selected_bibliographic_combination_id(request),
        )

        invalid = json.loads(result.to_json())
        title_proposal = next(
            value
            for value in packet.proposals
            if value["destination"] == TargetDestination.TITLE.value
        )
        title_recommendation = next(
            value
            for value in invalid["recommendations"]
            if value["proposal_id"] == title_proposal["proposal_id"]
        )
        title_recommendation["candidate_id"] = title_proposal["candidates"][0][
            "candidate_id"
        ]
        title_recommendation["recommendation"] = "recommend_candidate"
        candidate_action = catalogue.action_for(
            title_recommendation["proposal_id"],
            title_recommendation["recommendation"],
            title_recommendation["candidate_id"],
        )
        assert candidate_action is not None
        title_recommendation["citations"] = list(
            candidate_action.citation_ids
        )
        candidate_result = ShadowReviewResult.from_json(
            json.dumps(
                invalid,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            request,
        )
        self.assertEqual(
            packet_combination["combination_id"],
            candidate_result.selected_bibliographic_combination_id(request),
        )

    def test_packet_and_result_bind_one_allowed_combination(self) -> None:
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
        proposal = evaluate_report(report)
        case = ReviewCase.create(proposal, report)
        packet = ShadowReviewPacket.create(case, proposal, report)
        self.assertEqual(case, ReviewCase.from_json(case.to_json()))
        self.assertEqual(
            packet,
            ShadowReviewPacket.from_json(packet.to_json(), report),
        )
        request = ShadowReviewRequest.create(case, _identity(), packet)
        combination = packet.bibliographic_combinations[0]
        catalogue = compile_shadow_review_actions(packet)
        for selection in combination["selections"]:
            if selection["kind"] != "candidate":
                continue
            candidate = next(
                candidate
                for proposal_entry in packet.proposals
                for candidate in proposal_entry["candidates"]
                if candidate["candidate_id"]
                == selection["alternative_id"]
            )
            self.assertEqual(
                [combination["combination_id"]],
                candidate["decision_summary"][
                    "bibliographic_compatibility_ids"
                ],
            )
        forged_packet = json.loads(packet.to_json())
        constrained_candidate = next(
            candidate
            for proposal_entry in forged_packet["proposals"]
            for candidate in proposal_entry["candidates"]
            if candidate["decision_summary"][
                "bibliographic_compatibility_ids"
            ]
        )
        constrained_candidate["decision_summary"][
            "bibliographic_compatibility_ids"
        ] = []
        with self.assertRaises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(
                canonical_json(forged_packet),
                report,
            )
        recommendation_by_destination = {}
        for proposal_entry in packet.proposals:
            destination = proposal_entry["destination"]
            selection = next(
                (
                    value
                    for value in combination["selections"]
                    if value["destination"] == destination
                ),
                None,
            )
            if selection is None:
                candidate = proposal_entry["candidates"][0]["candidate_id"]
                kind = "recommend_candidate"
            elif selection["kind"] == "candidate":
                candidate = selection["alternative_id"]
                kind = "recommend_candidate"
            else:
                candidate = None
                kind = "recommend_keep_current"
            recommendation = {
                "candidate_id": candidate,
                "problems": [],
                "proposal_id": proposal_entry["proposal_id"],
                "reasons": ["Exact allowed bibliographic combination."],
                "recommendation": kind,
            }
            action = catalogue.action_for(
                recommendation["proposal_id"],
                recommendation["recommendation"],
                recommendation["candidate_id"],
            )
            assert action is not None
            recommendation["citations"] = list(action.citation_ids)
            recommendation_by_destination[destination] = recommendation
        recommendations = tuple(
            recommendation_by_destination[value["destination"]]
            for value in packet.proposals
        )
        result = ShadowReviewResult(request.review_request_id, recommendations)
        result.validate_for(request)
        self.assertEqual(
            combination["combination_id"],
            result.selected_bibliographic_combination_id(request),
        )

        contradictory = json.loads(result.to_json())
        title_proposal_id = next(
            value["proposal_id"]
            for value in packet.proposals
            if value["destination"] == TargetDestination.TITLE.value
        )
        title_recommendation = next(
            value
            for value in contradictory["recommendations"]
            if value["proposal_id"] == title_proposal_id
        )
        title_recommendation["candidate_id"] = None
        title_recommendation["recommendation"] = "recommend_keep_current"
        with self.assertRaises(ShadowReviewSemanticError):
            ShadowReviewResult.from_json(
                json.dumps(
                    contradictory,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                request,
            )

    def test_persistence_replay_and_currentness_include_combinations(self) -> None:
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
        proposal = evaluate_report(report)
        without_relationship = ProposalSet.create(
            report_id=proposal.report_id,
            target_fingerprint=proposal.target_fingerprint,
            results=proposal.results,
        )
        self.assertNotEqual(
            proposal.proposal_set_id,
            without_relationship.proposal_set_id,
        )

        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            self.assertEqual(
                proposal.to_json(),
                repository.get_proposal(proposal.proposal_set_id),
            )
            first = json.loads(repository.admit_review_case(proposal))
            replay = json.loads(repository.admit_review_case(proposal))
            self.assertEqual("admitted", first["status"])
            self.assertEqual("replayed", replay["status"])

            repository.publish_proposal(without_relationship)
            with self.assertRaises(ReviewAdmissionIncompatibleError):
                repository.admit_review_case(without_relationship)
            repository.close()


if __name__ == "__main__":
    unittest.main()
