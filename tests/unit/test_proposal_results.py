"""Proposal result shape: the exact sixteen results, linked destination
dependencies, closed value scopes, canonical replay, and rejection
of forged payloads.
"""

import json
import unittest
from typing import cast

from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    canonical_json,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
    ProposalAlternative,
    ProposalAssessment,
    ProposalOutcome,
    ProposalResult,
    ProposalScope,
    ProposalSet,
    ProposalValue,
    ProposalValueError,
    ProposalValueKind,
    _ignored_policy_targets,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _provider,
    _report,
    _result,
)


class ProposalResultTests(unittest.TestCase):
    def test_shared_title_subtitle_destination_requires_every_owner_to_ignore(self) -> None:
        from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile

        base = dict(MetadataPolicyProfile.builtin().destinations)
        cases = (
            ({**base, "title_layout": "ignore", "subtitle": "ask"}, False),
            ({**base, "title_layout": "ask", "subtitle": "ignore"}, False),
            ({**base, "title_layout": "ignore", "subtitle": "ignore"}, True),
        )
        for destinations, ignored in cases:
            with self.subTest(destinations=destinations):
                self.assertEqual(
                    ignored,
                    TargetDestination.SUBTITLE
                    in _ignored_policy_targets(destinations),
                )

    def test_exact_sixteen_results_are_deterministic_and_round_trip(self) -> None:
        proposal_set = evaluate_report(_report())
        self.assertEqual(tuple(TargetDestination), tuple(
            value.destination for value in proposal_set.results
        ))
        self.assertEqual(
            {"connection", "fields", "item", "library"},
            set(cast(dict[str, object], _report().to_data()["target"])),
        )
        self.assertEqual(
            {
                "policy_version",
                "resolved_policy_id",
                "relationships",
                "report",
                "results",
                "schema_version",
            },
            set(proposal_set.to_data()),
        )
        self.assertEqual(
            proposal_set.proposal_set_id,
            ProposalSet.from_json(proposal_set.to_json()).proposal_set_id,
        )
        self.assertEqual("fixed-conservative-v8", POLICY_VERSION)
        self.assertEqual(POLICY_VERSION, proposal_set.policy_version)
        self.assertRegex(proposal_set.resolved_policy_id, r"^resolved_policy_[0-9a-f]{64}$")

    def test_prior_policy_document_is_rejected_and_current_replay_is_exact(
        self,
    ) -> None:
        current = evaluate_report(_report())
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v1"

        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v2"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v4"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v6"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["policy_version"] = "fixed-conservative-v7"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))
        prior = json.loads(current.to_json())
        prior["schema_version"] = "proposal-set-v1"
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))

        prior = json.loads(current.to_json())
        del prior["resolved_policy_id"]
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(prior))

        self.assertEqual(current, ProposalSet.from_json(current.to_json()))
        self.assertEqual(current, evaluate_report(_report()))

    def test_linked_destinations_retain_fixed_dependencies(self) -> None:
        proposal_set = evaluate_report(_report())
        dependencies = {
            result.destination: result.dependencies for result in proposal_set.results
        }
        self.assertEqual(
            (TargetDestination.SUBTITLE, TargetDestination.SERIES),
            dependencies[TargetDestination.TITLE],
        )
        self.assertEqual(
            (TargetDestination.TITLE,), dependencies[TargetDestination.SUBTITLE]
        )
        self.assertEqual(
            (TargetDestination.TITLE,), dependencies[TargetDestination.SERIES]
        )
        self.assertEqual(
            (TargetDestination.PUBLISHED_YEAR,),
            dependencies[TargetDestination.PUBLISHED_DATE],
        )
        self.assertEqual(
            (TargetDestination.PUBLISHED_DATE,),
            dependencies[TargetDestination.PUBLISHED_YEAR],
        )
        self.assertEqual(proposal_set, ProposalSet.from_json(proposal_set.to_json()))
        self.assertEqual(proposal_set, evaluate_report(_report()))
        self.assertNotIn("excerpt", proposal_set.to_json())
        self.assertNotIn("/Users/private", proposal_set.to_json())
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(proposal_set.to_json() + " ")

    def test_result_variants_reject_contradictory_choices(self) -> None:
        proposed = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        title=TargetField(
                            TargetDestination.TITLE,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(
                        _provider(
                            "catalogue",
                            "edition-1",
                            (
                                _claim(
                                    "catalogue",
                                    "edition-1",
                                    ClaimKind.TITLE,
                                    "A title",
                                    scope=ClaimScope.WORK,
                                ),
                            ),
                        ),
                    ),
                )
            ),
            TargetDestination.TITLE,
        )
        alternative = proposed.alternatives[0]
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.PROPOSE_CHANGE,
                (),
                None,
                ("unique_typed_candidate",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.ACCEPTABLE_CHOICE,
                (alternative,),
                None,
                ("field_policy_acceptable_choice",),
            )
        incompatible_language = tuple(
            ProposalAlternative.create(
                ProposalValue(ProposalValueKind.TEXT, value),
                ProposalAssessment.FILLS_EMPTY,
                ("provider_" + character * 64,),
                ("correlation_" + character * 64,),
                ProposalScope.EDITION,
            )
            for value, character in (("en", "a"), ("fr", "b"))
        )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.ACCEPTABLE_CHOICE,
                tuple(
                    sorted(
                        incompatible_language,
                        key=lambda value: value.alternative_id,
                    )
                ),
                None,
                ("field_policy_acceptable_choice",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.UNRESOLVED_CHOICE,
                (alternative,),
                alternative.alternative_id,
                ("competing_evidence",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.DEFER,
                (alternative,),
                None,
                ("provider_unavailable",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.KEEP,
                (alternative,),
                None,
                ("positive_preservation_evidence",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.REJECT,
                (alternative,),
                None,
                ("policy_reject",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.INELIGIBLE,
                (alternative,),
                None,
                ("target_state_unusable",),
            )
        self.assertEqual(
            ProposalOutcome.REJECT,
            ProposalResult(
                TargetDestination.TITLE,
                ProposalOutcome.REJECT,
                (),
                None,
                ("policy_reject",),
            ).outcome,
        )

    def test_language_result_validation_encodes_target_compatibility(self) -> None:
        def language_alternative(
            value: str,
            assessment: ProposalAssessment,
            character: str,
        ) -> ProposalAlternative:
            return ProposalAlternative.create(
                ProposalValue(ProposalValueKind.TEXT, value),
                assessment,
                ("provider_" + character * 64,),
                ("correlation_" + character * 64,),
                ProposalScope.EDITION,
            )

        preserves = language_alternative(
            "en",
            ProposalAssessment.PRESERVES_TARGET,
            "a",
        )
        replaces_region = language_alternative(
            "en-GB",
            ProposalAssessment.REPLACES_TARGET,
            "b",
        )
        replaces_region_alias = language_alternative(
            "eng-GB",
            ProposalAssessment.REPLACES_TARGET,
            "c",
        )
        compatible_preserves = language_alternative(
            "eng",
            ProposalAssessment.PRESERVES_TARGET,
            "d",
        )
        mixed = tuple(
            sorted(
                (preserves, replaces_region),
                key=lambda value: value.alternative_id,
            )
        )
        replacements = tuple(
            sorted(
                (replaces_region, replaces_region_alias),
                key=lambda value: value.alternative_id,
            )
        )
        all_preserve = tuple(
            sorted(
                (preserves, compatible_preserves),
                key=lambda value: value.alternative_id,
            )
        )

        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.KEEP,
                mixed,
                None,
                ("field_policy_preservation",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.KEEP,
                (preserves,),
                None,
                ("positive_preservation_evidence",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.ACCEPTABLE_CHOICE,
                replacements,
                None,
                ("field_policy_acceptable_choice",),
            )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.PROPOSE_CHANGE,
                (replaces_region,),
                replaces_region.alternative_id,
                ("unique_typed_candidate",),
            )
        self.assertEqual(
            ProposalOutcome.UNRESOLVED_CHOICE,
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.UNRESOLVED_CHOICE,
                mixed,
                None,
                ("field_policy_conflict",),
            ).outcome,
        )
        self.assertEqual(
            ProposalOutcome.UNRESOLVED_CHOICE,
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.UNRESOLVED_CHOICE,
                (replaces_region,),
                None,
                ("field_policy_conflict",),
            ).outcome,
        )
        with self.assertRaises(ProposalValueError):
            ProposalResult(
                TargetDestination.LANGUAGE,
                ProposalOutcome.UNRESOLVED_CHOICE,
                all_preserve,
                None,
                ("field_policy_conflict",),
            )

    def test_destination_value_scope_contract_is_exhaustive(self) -> None:
        values = {
            ProposalValueKind.TEXT: ProposalValue(ProposalValueKind.TEXT, "Value"),
            ProposalValueKind.BOOLEAN: ProposalValue(ProposalValueKind.BOOLEAN, True),
            ProposalValueKind.LIST: ProposalValue(ProposalValueKind.LIST, ("Value",)),
            ProposalValueKind.SERIES: ProposalValue(
                ProposalValueKind.SERIES, (TargetSeries("Series"),)
            ),
            ProposalValueKind.COVER: ProposalValue(
                ProposalValueKind.COVER, "cover_" + "a" * 64
            ),
        }
        expected = {
            TargetDestination.TITLE: {(ProposalValueKind.TEXT, ProposalScope.WORK)},
            TargetDestination.SUBTITLE: {
                (ProposalValueKind.TEXT, ProposalScope.WORK),
                (ProposalValueKind.TEXT, ProposalScope.EDITION),
            },
            TargetDestination.AUTHORS: {(ProposalValueKind.LIST, ProposalScope.WORK)},
            TargetDestination.NARRATORS: {(ProposalValueKind.LIST, ProposalScope.EDITION)},
            TargetDestination.SERIES: {(ProposalValueKind.SERIES, ProposalScope.WORK)},
            TargetDestination.GENRES: {(ProposalValueKind.LIST, ProposalScope.WORK)},
            TargetDestination.PUBLISHED_YEAR: {
                (ProposalValueKind.TEXT, ProposalScope.WORK),
                (ProposalValueKind.TEXT, ProposalScope.EDITION),
            },
            TargetDestination.PUBLISHED_DATE: {
                (ProposalValueKind.TEXT, ProposalScope.WORK),
                (ProposalValueKind.TEXT, ProposalScope.EDITION),
            },
            TargetDestination.PUBLISHER: {(ProposalValueKind.TEXT, ProposalScope.EDITION)},
            TargetDestination.DESCRIPTION: {(ProposalValueKind.TEXT, ProposalScope.WORK)},
            TargetDestination.ISBN: {(ProposalValueKind.TEXT, ProposalScope.EDITION)},
            TargetDestination.ASIN: {(ProposalValueKind.TEXT, ProposalScope.EDITION)},
            TargetDestination.LANGUAGE: {(ProposalValueKind.TEXT, ProposalScope.EDITION)},
            TargetDestination.EXPLICIT: {(ProposalValueKind.BOOLEAN, ProposalScope.EDITION)},
            TargetDestination.TAGS: {(ProposalValueKind.LIST, ProposalScope.WORK)},
            TargetDestination.COVER: {(ProposalValueKind.COVER, ProposalScope.COVER)},
        }
        self.assertEqual(set(TargetDestination), set(expected))
        for destination, allowed in expected.items():
            for kind, value in values.items():
                for scope in ProposalScope:
                    candidate_value = (
                        ProposalValue(ProposalValueKind.TEXT, "2001")
                        if (
                            kind is ProposalValueKind.TEXT
                            and destination is TargetDestination.PUBLISHED_YEAR
                        )
                        else ProposalValue(
                            ProposalValueKind.TEXT, "2001-02-03"
                        )
                        if (
                            kind is ProposalValueKind.TEXT
                            and destination is TargetDestination.PUBLISHED_DATE
                        )
                        else value
                    )
                    alternative = ProposalAlternative.create(
                        candidate_value,
                        ProposalAssessment.FILLS_EMPTY,
                        ("local_" + "a" * 64,),
                        ("correlation_" + "a" * 64,),
                        scope,
                        date_event=(
                            DateEvent.MANIFESTATION_PUBLICATION
                            if destination
                            in {
                                TargetDestination.PUBLISHED_YEAR,
                                TargetDestination.PUBLISHED_DATE,
                            }
                            else None
                        ),
                        date_precision=(
                            DatePrecision.YEAR
                            if destination is TargetDestination.PUBLISHED_YEAR
                            else DatePrecision.DAY
                            if destination is TargetDestination.PUBLISHED_DATE
                            else None
                        ),
                    )
                    arguments = (
                        destination,
                        ProposalOutcome.PROPOSE_CHANGE,
                        (alternative,),
                        alternative.alternative_id,
                        (
                            "unique_safe_cover",
                        )
                        if destination is TargetDestination.COVER
                        else ("unique_typed_candidate",),
                    )
                    with self.subTest(destination=destination, kind=kind, scope=scope):
                        if (kind, scope) in allowed:
                            ProposalResult(*arguments)
                        else:
                            with self.assertRaises(ProposalValueError):
                                ProposalResult(*arguments)

    def test_canonical_parse_rejects_forged_destination_payload_and_reasons(self) -> None:
        proposal_set = evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    title=TargetField(TargetDestination.TITLE, TargetFieldState.MISSING),
                ),
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.TITLE,
                                "A title",
                                scope=ClaimScope.WORK,
                            ),
                        ),
                    ),
                ),
            )
        )
        document = json.loads(proposal_set.to_json())
        result = next(
            value for value in document["results"] if value["destination"] == "title"
        )
        original = _result(proposal_set, TargetDestination.TITLE).alternatives[0]
        for value, scope in (
            (ProposalValue(ProposalValueKind.BOOLEAN, True), ProposalScope.WORK),
            (ProposalValue(ProposalValueKind.TEXT, "A title"), ProposalScope.COVER),
        ):
            forged = ProposalAlternative.create(
                value,
                original.assessment,
                original.evidence_references,
                original.correlations,
                scope,
            )
            result["alternatives"] = [forged.to_data()]
            result["selected_alternative_id"] = forged.alternative_id
            with self.subTest(value=value.kind, scope=scope):
                with self.assertRaises(ProposalValueError):
                    ProposalSet.from_json(canonical_json(document))
        result["alternatives"] = []
        result["selected_alternative_id"] = None
        for outcome, reasons in (
            ("defer", ["unique_typed_candidate"]),
            ("reject", ["no_supported_evidence"]),
        ):
            result["outcome"] = outcome
            result["reasons"] = reasons
            with self.subTest(outcome=outcome, reasons=reasons):
                with self.assertRaises(ProposalValueError):
                    ProposalSet.from_json(canonical_json(document))

    def test_destination_date_semantics_reject_constructed_and_replayed_mismatch(
        self,
    ) -> None:
        reference = "provider_" + "a" * 64
        correlation = "correlation_" + "b" * 64
        invalid = (
            (
                TargetDestination.TITLE,
                DateEvent.MANIFESTATION_PUBLICATION,
                DatePrecision.DAY,
            ),
            (
                TargetDestination.PUBLISHED_YEAR,
                DateEvent.WORK_FIRST_PUBLICATION,
                DatePrecision.YEAR,
            ),
            (
                TargetDestination.PUBLISHED_DATE,
                DateEvent.MANIFESTATION_PUBLICATION,
                DatePrecision.YEAR,
            ),
            (
                TargetDestination.PUBLISHED_DATE,
                DateEvent.MANIFESTATION_PUBLICATION,
                DatePrecision.MONTH,
            ),
        )
        for destination, event, precision in invalid:
            alternative = ProposalAlternative.create(
                ProposalValue(ProposalValueKind.TEXT, "2001"),
                ProposalAssessment.FILLS_EMPTY,
                (reference,),
                (correlation,),
                ProposalScope.WORK,
                date_event=event,
                date_precision=precision,
            )
            with self.subTest(destination=destination):
                with self.assertRaises(ProposalValueError):
                    ProposalResult(
                        destination,
                        ProposalOutcome.PROPOSE_CHANGE,
                        (alternative,),
                        alternative.alternative_id,
                        ("unique_typed_candidate",),
                    )

        proposal_set = evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    title=TargetField(
                        TargetDestination.TITLE, TargetFieldState.MISSING
                    ),
                ),
                providers=(
                    _provider(
                        "catalogue",
                        "edition-1",
                        (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.TITLE,
                                "A title",
                                scope=ClaimScope.WORK,
                            ),
                        ),
                    ),
                ),
            )
        )
        document = json.loads(proposal_set.to_json())
        result = next(
            value for value in document["results"] if value["destination"] == "title"
        )
        original = _result(proposal_set, TargetDestination.TITLE).alternatives[0]
        forged = ProposalAlternative.create(
            original.value,
            original.assessment,
            original.evidence_references,
            original.correlations,
            original.scope,
            date_event=DateEvent.MANIFESTATION_PUBLICATION,
            date_precision=DatePrecision.DAY,
        )
        result["alternatives"] = [forged.to_data()]
        result["selected_alternative_id"] = forged.alternative_id
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(canonical_json(document))

    def test_unique_empty_fill_and_positive_preservation_are_explainable(self) -> None:
        title_claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.TITLE,
            "A Better Title",
            scope=ClaimScope.WORK,
        )
        provider = _provider("catalogue", "edition-1", (title_claim,))
        empty = evaluate_report(
            _report(
                title=None,
                fields=_fields(
                    None,
                    title=TargetField(
                        TargetDestination.TITLE,
                        TargetFieldState.MISSING,
                    ),
                ),
                providers=(provider,),
            )
        )
        result = _result(empty, TargetDestination.TITLE)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertIsNotNone(result.selected_alternative_id)
        matching_claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.TITLE,
            "Current title",
            scope=ClaimScope.WORK,
        )
        preserved = evaluate_report(
            _report(
                providers=(
                    _provider("catalogue", "edition-1", (matching_claim,)),
                )
            )
        )
        self.assertEqual(
            ProposalOutcome.KEEP,
            _result(preserved, TargetDestination.TITLE).outcome,
        )

    def test_non_review_result_cannot_carry_any_selection(self) -> None:
        result = _result(evaluate_report(_report()), TargetDestination.TITLE)
        with self.assertRaises(ProposalValueError):
            type(result)(
                result.destination,
                result.outcome,
                result.alternatives,
                "alternative_" + "f" * 64,
                result.reasons,
                result.dependencies,
            )

    def test_invalid_unicode_is_a_closed_value_error(self) -> None:
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json("\ud800")


if __name__ == "__main__":
    unittest.main()
