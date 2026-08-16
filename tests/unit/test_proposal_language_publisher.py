"""Proposal language and publisher policy: regional compatibility,
alias mapping, base/script/region contradictions, distinct publisher
business levels, and manifestation-bound anchors.
"""

import unittest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    IdentifierRelation,
    LocalClaim,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalAssessment,
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _claim,
    _explicit_local_ebook_isbn,
    _fields,
    _manifestation_publisher_provider,
    _provider,
    _publication_local_isbn,
    _report,
    _result,
)


class ProposalLanguagePublisherTests(unittest.TestCase):
    def test_language_policy_preserves_supported_regional_target(self) -> None:
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.VALUE,
                "en-US",
            )
        )
        providers = (
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.LANGUAGE,
                        "en",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
            _provider(
                "beta",
                "beta-edition",
                (
                    _claim(
                        "beta",
                        "beta-edition",
                        ClaimKind.LANGUAGE,
                        "eng",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
        )

        result = _result(
            evaluate_report(_report(fields=fields, providers=providers)),
            TargetDestination.LANGUAGE,
        )

        self.assertEqual(ProposalOutcome.KEEP, result.outcome)
        self.assertEqual(("field_policy_preservation",), result.reasons)
        self.assertIsNone(result.selected_alternative_id)
        self.assertEqual(
            {"en", "eng"},
            {alternative.value.value for alternative in result.alternatives},
        )
        self.assertTrue(
            all(
                alternative.assessment is ProposalAssessment.PRESERVES_TARGET
                for alternative in result.alternatives
            )
        )

    def test_language_policy_rejects_candidate_that_contradicts_target_detail(
        self,
    ) -> None:
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.VALUE,
                "en-US",
            )
        )
        providers = (
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.LANGUAGE,
                        "en",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
            _provider(
                "beta",
                "beta-edition",
                (
                    _claim(
                        "beta",
                        "beta-edition",
                        ClaimKind.LANGUAGE,
                        "en-GB",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
        )

        result = _result(
            evaluate_report(_report(fields=fields, providers=providers)),
            TargetDestination.LANGUAGE,
        )

        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(("field_policy_conflict",), result.reasons)
        self.assertEqual(
            {
                ProposalAssessment.PRESERVES_TARGET,
                ProposalAssessment.REPLACES_TARGET,
            },
            {alternative.assessment for alternative in result.alternatives},
        )

    def test_language_policy_rejects_sole_target_detail_contradiction(
        self,
    ) -> None:
        controls = (
            ("region", "en-US", "en-GB"),
            ("script", "en-Latn", "en-Cyrl"),
        )
        for name, target, candidate in controls:
            with self.subTest(name=name):
                fields = _fields(
                    language=TargetField(
                        TargetDestination.LANGUAGE,
                        TargetFieldState.VALUE,
                        target,
                    )
                )
                providers = tuple(
                    _provider(
                        provider,
                        f"{provider}-edition",
                        (
                            _claim(
                                provider,
                                f"{provider}-edition",
                                ClaimKind.LANGUAGE,
                                candidate,
                                scope=ClaimScope.EDITION,
                            ),
                        ),
                        authority=authority,
                    )
                    for provider, authority in (
                        ("hardcover", "hardcover"),
                        ("open_library", "open_library"),
                    )
                )

                result = _result(
                    evaluate_report(
                        _report(fields=fields, providers=providers)
                    ),
                    TargetDestination.LANGUAGE,
                )

                self.assertEqual(
                    ProposalOutcome.UNRESOLVED_CHOICE,
                    result.outcome,
                )
                self.assertEqual(
                    ("field_policy_conflict",),
                    result.reasons,
                )
                self.assertEqual(1, len(result.alternatives))
                self.assertEqual(
                    ProposalAssessment.REPLACES_TARGET,
                    result.alternatives[0].assessment,
                )
                self.assertEqual(
                    2,
                    len(result.alternatives[0].correlations),
                )

    def test_language_policy_keeps_sole_less_specific_evidence(self) -> None:
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.VALUE,
                "en-Latn-US",
            )
        )
        provider = _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    "eng",
                    scope=ClaimScope.EDITION,
                ),
            ),
        )

        result = _result(
            evaluate_report(_report(fields=fields, providers=(provider,))),
            TargetDestination.LANGUAGE,
        )

        self.assertEqual(ProposalOutcome.KEEP, result.outcome)
        self.assertEqual(("field_policy_preservation",), result.reasons)
        self.assertEqual(1, len(result.alternatives))
        self.assertEqual(
            ProposalAssessment.PRESERVES_TARGET,
            result.alternatives[0].assessment,
        )

    def test_equivalent_language_candidates_are_an_acceptable_choice(self) -> None:
        providers = (
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.LANGUAGE,
                        "en",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
            _provider(
                "beta",
                "beta-edition",
                (
                    _claim(
                        "beta",
                        "beta-edition",
                        ClaimKind.LANGUAGE,
                        "eng",
                        scope=ClaimScope.EDITION,
                    ),
                ),
            ),
        )
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MISSING,
            )
        )

        proposal_set = evaluate_report(
            _report(fields=fields, providers=providers)
        )
        result = _result(proposal_set, TargetDestination.LANGUAGE)

        self.assertEqual(ProposalOutcome.ACCEPTABLE_CHOICE, result.outcome)
        self.assertEqual(("field_policy_acceptable_choice",), result.reasons)
        self.assertIsNone(result.selected_alternative_id)
        self.assertEqual(
            {"en", "eng"},
            {alternative.value.value for alternative in result.alternatives},
        )
        self.assertEqual(
            proposal_set,
            ProposalSet.from_json(proposal_set.to_json()),
        )

    def test_deprecated_bh_language_alias_maps_to_favored_bih(self) -> None:
        providers = tuple(
            _provider(
                provider,
                f"{provider}-edition",
                (
                    _claim(
                        provider,
                        f"{provider}-edition",
                        ClaimKind.LANGUAGE,
                        value,
                        scope=ClaimScope.EDITION,
                    ),
                ),
            )
            for provider, value in (("alpha", "bh"), ("beta", "bih"))
        )
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MISSING,
            )
        )

        proposal_set = evaluate_report(
            _report(fields=fields, providers=providers)
        )
        result = _result(proposal_set, TargetDestination.LANGUAGE)

        self.assertEqual(ProposalOutcome.ACCEPTABLE_CHOICE, result.outcome)
        self.assertEqual(
            {"bh", "bih"},
            {alternative.value.value for alternative in result.alternatives},
        )
        self.assertEqual(
            proposal_set,
            ProposalSet.from_json(proposal_set.to_json()),
        )

    def test_language_policy_rejects_base_script_region_and_malformed_conflicts(
        self,
    ) -> None:
        controls = (
            ("different_base", "fr", "eng"),
            ("different_script", "en-Latn", "en-Cyrl"),
            ("different_region", "en-US", "en-GB"),
            ("malformed", "en", "english"),
        )
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MISSING,
            )
        )
        for name, left, right in controls:
            with self.subTest(name=name):
                providers = (
                    _provider(
                        "alpha",
                        "alpha-edition",
                        (
                            _claim(
                                "alpha",
                                "alpha-edition",
                                ClaimKind.LANGUAGE,
                                left,
                                scope=ClaimScope.EDITION,
                            ),
                        ),
                    ),
                    _provider(
                        "beta",
                        "beta-edition",
                        (
                            _claim(
                                "beta",
                                "beta-edition",
                                ClaimKind.LANGUAGE,
                                right,
                                scope=ClaimScope.EDITION,
                            ),
                        ),
                    ),
                )
                result = _result(
                    evaluate_report(
                        _report(fields=fields, providers=providers)
                    ),
                    TargetDestination.LANGUAGE,
                )
                self.assertEqual(
                    ProposalOutcome.UNRESOLVED_CHOICE,
                    result.outcome,
                )
                self.assertEqual(("field_policy_conflict",), result.reasons)

        single_malformed = _provider(
            "alpha",
            "alpha-edition",
            (
                _claim(
                    "alpha",
                    "alpha-edition",
                    ClaimKind.LANGUAGE,
                    "english",
                    scope=ClaimScope.EDITION,
                ),
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    providers=(single_malformed,),
                )
            ),
            TargetDestination.LANGUAGE,
        )
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(("field_policy_conflict",), result.reasons)

    def test_private_use_language_region_cannot_be_selected(self) -> None:
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MISSING,
            )
        )
        controls = (
            (
                "private_use",
                "en-AA",
                ProposalOutcome.UNRESOLVED_CHOICE,
                ("field_policy_conflict",),
                False,
            ),
            (
                "registered",
                "en-US",
                ProposalOutcome.PROPOSE_CHANGE,
                ("unique_typed_candidate",),
                True,
            ),
        )
        for name, value, outcome, reasons, selected in controls:
            with self.subTest(name=name):
                provider = _provider(
                    "catalogue",
                    "edition-1",
                    (
                        _claim(
                            "catalogue",
                            "edition-1",
                            ClaimKind.LANGUAGE,
                            value,
                            scope=ClaimScope.EDITION,
                        ),
                    ),
                )

                result = _result(
                    evaluate_report(
                        _report(fields=fields, providers=(provider,))
                    ),
                    TargetDestination.LANGUAGE,
                )

                self.assertEqual(outcome, result.outcome)
                self.assertEqual(reasons, result.reasons)
                self.assertEqual(value, result.alternatives[0].value.value)
                self.assertEqual(
                    selected,
                    result.selected_alternative_id is not None,
                )

    def test_publisher_policy_keeps_distinct_supported_business_levels(self) -> None:
        local_publisher = LocalClaim.create(
            kind=ClaimKind.PUBLISHER,
            observed="Tor Books",
            canonical="Tor Books",
            excerpt="Tor Books",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
        )
        providers = (
            _manifestation_publisher_provider(
                "alpha",
                "alpha-edition",
                ClaimKind.IMPRINT,
                "Tor",
            ),
            _manifestation_publisher_provider(
                "beta",
                "beta-edition",
                ClaimKind.PUBLISHER,
                "Macmillan Publishing Group",
            ),
        )
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.VALUE,
                "Tor Books",
            )
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(
                        _explicit_local_ebook_isbn(),
                        local_publisher,
                    ),
                    providers=providers,
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.KEEP, result.outcome)
        self.assertEqual(("field_policy_preservation",), result.reasons)
        self.assertEqual(
            {"Tor Books", "Tor", "Macmillan Publishing Group"},
            {alternative.value.value for alternative in result.alternatives},
        )
        references = {
            reference
            for alternative in result.alternatives
            for reference in alternative.evidence_references
        }
        self.assertIn(local_publisher.reference, references)
        self.assertTrue(
            {
                claim.reference
                for provider in providers
                for claim in provider.candidates[0].claims
                if claim.kind in {ClaimKind.PUBLISHER, ClaimKind.IMPRINT}
            }.issubset(references)
        )

    def test_distinct_publishers_without_a_supported_target_are_acceptable_choice(
        self,
    ) -> None:
        providers = (
            _manifestation_publisher_provider(
                "alpha",
                "alpha-edition",
                ClaimKind.IMPRINT,
                "Tor",
            ),
            _manifestation_publisher_provider(
                "beta",
                "beta-edition",
                ClaimKind.PUBLISHER,
                "Macmillan",
            ),
        )
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=providers,
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.ACCEPTABLE_CHOICE, result.outcome)
        self.assertEqual(("field_policy_acceptable_choice",), result.reasons)
        self.assertIsNone(result.selected_alternative_id)
        self.assertEqual(
            {"Tor", "Macmillan"},
            {alternative.value.value for alternative in result.alternatives},
        )

    def test_publisher_policy_requires_valid_manifestation_bound_evidence(
        self,
    ) -> None:
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )
        invalid_or_wrong_scope = (
            _provider(
                "alpha",
                "alpha-edition",
                (
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.ISBN,
                        "9780306406157",
                        scope=ClaimScope.EDITION,
                        applicability="ebook",
                    ),
                    _claim(
                        "alpha",
                        "alpha-edition",
                        ClaimKind.PUBLISHER,
                        "<not-a-publisher>",
                        scope=ClaimScope.EDITION,
                        order=1,
                    ),
                ),
            ),
            _provider(
                "beta",
                "beta-work",
                (
                    _claim(
                        "beta",
                        "beta-work",
                        ClaimKind.PUBLISHER,
                        "Associated Work Publisher",
                        scope=ClaimScope.WORK,
                    ),
                ),
            ),
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=invalid_or_wrong_scope,
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_publisher_policy_does_not_treat_target_syntax_as_evidence(self) -> None:
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.VALUE,
                "Macmillan",
            )
        )
        provider = _manifestation_publisher_provider(
            "alpha",
            "alpha-edition",
            ClaimKind.PUBLISHER,
            "Tor Books",
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=(provider,),
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(
            ("insufficient_independent_corroboration",),
            result.reasons,
        )

    def test_publisher_policy_excludes_wrong_manifestation_provider(self) -> None:
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )
        provider = _manifestation_publisher_provider(
            "alpha",
            "unrelated-edition",
            ClaimKind.PUBLISHER,
            "Wrong Manifestation Press",
            isbn="9783161484100",
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(_explicit_local_ebook_isbn(),),
                    providers=(provider,),
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_publisher_provider_requires_accepted_identifier_anchor(self) -> None:
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )
        controls = (
            (
                "unresolved",
                (_publication_local_isbn(),),
                "unknown",
                IdentifierRelation.UNRESOLVED,
                ProposalOutcome.INELIGIBLE,
            ),
            (
                "associated",
                (_explicit_local_ebook_isbn(),),
                "unknown",
                IdentifierRelation.ASSOCIATED,
                ProposalOutcome.INELIGIBLE,
            ),
            (
                "accepted",
                (_explicit_local_ebook_isbn(),),
                "ebook",
                IdentifierRelation.ACCEPTED,
                ProposalOutcome.PROPOSE_CHANGE,
            ),
        )
        for name, local_claims, applicability, relation, outcome in controls:
            with self.subTest(name=name):
                provider = _manifestation_publisher_provider(
                    "catalogue",
                    "edition-1",
                    ClaimKind.PUBLISHER,
                    "Bound Press",
                    applicability=applicability,
                )
                report = _report(
                    fields=fields,
                    local_claims=local_claims,
                    providers=(provider,),
                )
                provider_relations = {
                    evidence.relation
                    for evidence in report.identifier_evidence
                    if evidence.provider == "catalogue"
                    and evidence.candidate_identity == "edition-1"
                }

                result = _result(
                    evaluate_report(report),
                    TargetDestination.PUBLISHER,
                )

                self.assertEqual({relation}, provider_relations)
                self.assertEqual(outcome, result.outcome)
                self.assertEqual(
                    ("Bound Press",)
                    if outcome is ProposalOutcome.PROPOSE_CHANGE
                    else (),
                    tuple(
                        alternative.value.value
                        for alternative in result.alternatives
                    ),
                )

    def test_publisher_anchor_must_reference_accepted_edition_isbn(self) -> None:
        provider = _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "9780306406157",
                    scope=ClaimScope.EDITION,
                    applicability="unknown",
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "9780306406157",
                    scope=ClaimScope.WORK,
                    applicability="ebook",
                    order=1,
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.PUBLISHER,
                    "Wrong Scope Press",
                    scope=ClaimScope.EDITION,
                    order=2,
                ),
            ),
        )
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )
        report = _report(
            fields=fields,
            local_claims=(_explicit_local_ebook_isbn(),),
            providers=(provider,),
        )
        isbn_claims = {
            claim.reference: claim
            for claim in provider.candidates[0].claims
            if claim.kind is ClaimKind.ISBN
        }
        provider_evidence = {
            (
                isbn_claims[evidence.reference].scope,
                isbn_claims[evidence.reference].applicability,
            ): evidence.relation
            for evidence in report.identifier_evidence
            if evidence.provider == "catalogue"
        }

        result = _result(
            evaluate_report(report),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(
            {
                (ClaimScope.EDITION, "unknown"): IdentifierRelation.ASSOCIATED,
                (ClaimScope.WORK, "ebook"): IdentifierRelation.ACCEPTED,
            },
            provider_evidence,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_selected_epub_publisher_does_not_need_provider_anchor(self) -> None:
        local_publisher = LocalClaim.create(
            kind=ClaimKind.PUBLISHER,
            observed="Selected EPUB Press",
            canonical="Selected EPUB Press",
            excerpt="Selected EPUB Press",
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
        )
        fields = _fields(
            publisher=TargetField(
                TargetDestination.PUBLISHER,
                TargetFieldState.MISSING,
            )
        )

        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    local_claims=(local_publisher,),
                )
            ),
            TargetDestination.PUBLISHER,
        )

        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(
            "Selected EPUB Press",
            result.alternatives[0].value.value,
        )
        self.assertEqual(
            (local_publisher.reference,),
            result.alternatives[0].evidence_references,
        )


if __name__ == "__main__":
    unittest.main()
