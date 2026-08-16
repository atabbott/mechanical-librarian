"""Proposal authors and narrators: complete-list requirements, typed
non-author credits, identity-bound aliases, and the controlled
package-creator placeholder.
"""

import unittest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _identity_bound_alias_report,
    _local_creator,
    _partially_bound_direct_author_report,
    _provider,
    _report,
    _result,
)


class ProposalAuthorTests(unittest.TestCase):
    def test_complete_authors_never_drop_a_coauthor(self) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            ("Ada Author", "Bea Author"),
        )
        claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.CREATOR,
            "Ada Author",
            scope=ClaimScope.WORK,
            role="author",
        )
        proposal_set = evaluate_report(
            _report(
                fields=_fields(authors=authors),
                providers=(_provider("catalogue", "edition-1", (claim,)),),
            )
        )
        result = _result(proposal_set, TargetDestination.AUTHORS)
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertIn(
            "insufficient_independent_corroboration", result.reasons
        )

    def test_duplicate_creator_sets_do_not_become_an_authors_proposal(self) -> None:
        local_claims = (
            _local_creator("Ada Author", occurrence=0),
            _local_creator("ADA  AUTHOR", occurrence=1),
        )
        provider_claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "Ada Author",
                scope=ClaimScope.WORK,
                role="author",
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "ADA  AUTHOR",
                scope=ClaimScope.WORK,
                role="author",
                order=1,
            ),
        )
        candidate = finalize_candidate_comparisons(
            ProviderCandidate(
                "edition-1",
                "catalogue-work",
                provider_claims,
            ),
            local_claims,
        )
        result = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=local_claims,
                    providers=(
                        ProviderObservation(
                            "catalogue",
                            ProviderStatus.CANDIDATES,
                            candidates=(candidate,),
                        ),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_identity_bound_aliases_do_not_grant_three_author_authority(
        self,
    ) -> None:
        report = _identity_bound_alias_report()
        creator_claims = tuple(
            claim
            for claim in report.providers[0].candidates[0].claims
            if claim.kind is ClaimKind.CREATOR
        )
        self.assertTrue(
            all(
                any(link.outcome is Comparison.AGREEMENT for link in claim.links)
                for claim in creator_claims
            )
        )
        result = _result(evaluate_report(report), TargetDestination.AUTHORS)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(
            (("Ada Example", "Bob Example"),),
            tuple(alternative.value.value for alternative in result.alternatives),
        )
        self.assertTrue(
            all(
                reference.startswith("local_")
                for alternative in result.alternatives
                for reference in alternative.evidence_references
            )
        )

    def test_incomplete_identity_bindings_preserve_direct_author_evidence(
        self,
    ) -> None:
        report = _partially_bound_direct_author_report()
        provider_references = {
            claim.reference
            for claim in report.providers[0].candidates[0].claims
            if claim.kind is ClaimKind.CREATOR
        }
        result = _result(evaluate_report(report), TargetDestination.AUTHORS)
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(
            (("Ada Example", "Bob Example"),),
            tuple(alternative.value.value for alternative in result.alternatives),
        )
        self.assertTrue(
            provider_references.issubset(
                {
                    reference
                    for alternative in result.alternatives
                    for reference in alternative.evidence_references
                }
            )
        )

    def test_provider_typed_non_author_credits_preserve_complete_authors(self) -> None:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "Ada Author",
                scope=ClaimScope.WORK,
                role="author",
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CREATOR,
                "Bea Author",
                scope=ClaimScope.WORK,
                role="author",
                order=1,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CONTRIBUTOR,
                "Ed Editor",
                scope=ClaimScope.WORK,
                role="editor",
                order=2,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CONTRIBUTOR,
                "Ivy Illustrator",
                scope=ClaimScope.WORK,
                role="illustrator",
                order=3,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(_provider("catalogue", "edition-1", claims),),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(("Ada Author", "Bea Author"), result.alternatives[0].value.value)

    def test_ambiguous_provider_author_credits_do_not_become_partial_authors(self) -> None:
        for role in (None, "author", "pseudonym", "collective"):
            with self.subTest(role=role):
                claims = (
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.CREATOR,
                        "Ada Author",
                        scope=ClaimScope.WORK,
                        role="author",
                    ),
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.CONTRIBUTOR,
                        "Ambiguous Credit",
                        scope=ClaimScope.WORK,
                        role=role,
                        order=1,
                    ),
                )
                result = _result(
                    evaluate_report(
                        _report(
                            title=None,
                            fields=_fields(
                                None,
                                authors=TargetField(
                                    TargetDestination.AUTHORS,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            providers=(
                                _provider("catalogue", "edition-1", claims),
                            ),
                        )
                    ),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                self.assertEqual((), result.alternatives)

    def test_incomplete_provider_author_list_is_withheld(self) -> None:
        for state in (FieldState.MALFORMED, FieldState.OVERSIZED):
            with self.subTest(state=state):
                claims = (
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.CREATOR,
                        "Ada Author",
                        scope=ClaimScope.WORK,
                        role="author",
                    ),
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate="edition-1",
                        kind=ClaimKind.CREATOR,
                        value=None,
                        state=state,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        role="author",
                        raw_order=1,
                    ),
                )
                result = _result(
                    evaluate_report(
                        _report(
                            title=None,
                            fields=_fields(
                                None,
                                authors=TargetField(
                                    TargetDestination.AUTHORS,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            providers=(
                                _provider("catalogue", "edition-1", claims),
                            ),
                        )
                    ),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                self.assertEqual((), result.alternatives)

    def test_unrepresentable_author_like_or_unknown_contributor_withholds_authors(
        self,
    ) -> None:
        for source in ("local", "provider"):
            for role in ("author", "pseudonym", "collective", None):
                with self.subTest(source=source, role=role):
                    claims: tuple[LocalClaim, ...] | tuple[ProviderClaim, ...]
                    if source == "local":
                        claims = (
                            LocalClaim.create(
                                kind=ClaimKind.CREATOR,
                                observed="Ada Author",
                                canonical="Ada Author",
                                excerpt="Ada Author",
                                applicability="not_applicable",
                                checksum="not_applicable",
                                applicability_basis="not_applicable",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                            LocalClaim.create(
                                kind=ClaimKind.CONTRIBUTOR,
                                observed="Unrepresentable Credit",
                                canonical=None,
                                excerpt="Unrepresentable Credit",
                                applicability="not_applicable",
                                checksum="not_applicable",
                                applicability_basis="not_applicable",
                                scope=ClaimScope.WORK,
                                state=FieldState.MALFORMED,
                                role=role,
                                occurrence=1,
                            ),
                        )
                        proposal_set = evaluate_report(
                            _report(
                                title=None,
                                fields=_fields(
                                    None,
                                    authors=TargetField(
                                        TargetDestination.AUTHORS,
                                        TargetFieldState.MISSING,
                                    ),
                                ),
                                local_claims=claims,
                            )
                        )
                    else:
                        claims = (
                            _claim(
                                "catalogue",
                                "edition-1",
                                ClaimKind.CREATOR,
                                "Ada Author",
                                scope=ClaimScope.WORK,
                                role="author",
                            ),
                            ProviderClaim.create(
                                provider="catalogue",
                                candidate="edition-1",
                                kind=ClaimKind.CONTRIBUTOR,
                                value=None,
                                state=FieldState.MALFORMED,
                                scope=ClaimScope.WORK,
                                applicability="not_applicable",
                                role=role,
                                raw_order=1,
                            ),
                        )
                        proposal_set = evaluate_report(
                            _report(
                                title=None,
                                fields=_fields(
                                    None,
                                    authors=TargetField(
                                        TargetDestination.AUTHORS,
                                        TargetFieldState.MISSING,
                                    ),
                                ),
                                providers=(
                                    _provider("catalogue", "edition-1", claims),
                                ),
                            )
                        )
                    result = _result(proposal_set, TargetDestination.AUTHORS)
                    self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                    self.assertEqual((), result.alternatives)

    def test_equivalent_observations_coalesce_with_a_deterministic_display(self) -> None:
        providers = (
            _provider(
                "hardcover",
                "edition-1",
                (
                    _claim(
                        "hardcover",
                        "edition-1",
                        ClaimKind.TITLE,
                        "foo bar",
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority="hardcover",
            ),
            _provider(
                "open_library",
                "edition-2",
                (
                    _claim(
                        "open_library",
                        "edition-2",
                        ClaimKind.TITLE,
                        "Foo  Bar",
                        scope=ClaimScope.WORK,
                    ),
                ),
                authority="open_library",
            ),
        )
        result = _result(
            evaluate_report(_report(title=None, providers=providers)),
            TargetDestination.TITLE,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(1, len(result.alternatives))
        self.assertEqual("Foo Bar", result.alternatives[0].value.value)
        self.assertEqual(2, len(result.alternatives[0].correlations))

    def test_ambiguous_local_creator_does_not_become_partial_authors(self) -> None:
        claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed=name,
                canonical=name,
                excerpt=name,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                role=role,
                occurrence=index,
            )
            for index, (name, role) in enumerate(
                (("Ada Author", "author"), ("Unknown Credit", None))
            )
        )
        local_result = _result(
            evaluate_report(
                _report(
                    title=None,
                    fields=_fields(
                        None,
                        authors=TargetField(
                            TargetDestination.AUTHORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=claims,
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, local_result.outcome)
        self.assertEqual((), local_result.alternatives)

    def test_controlled_package_placeholder_never_becomes_an_author_candidate(
        self,
    ) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        for value in (
            "Unknown",
            "UNKNOWN",
            "UnKnOwN",
            "Ｕｎｋｎｏｗｎ",
        ):
            with self.subTest(value=value):
                claim = _local_creator(value)
                report = _report(
                    fields=_fields(None, authors=authors),
                    local_claims=(claim,),
                )
                proposal_set = evaluate_report(report)
                result = _result(proposal_set, TargetDestination.AUTHORS)
                self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                self.assertEqual((), result.alternatives)
                self.assertEqual(("no_supported_evidence",), result.reasons)
                self.assertEqual(claim, report.local_claims[0])
                self.assertEqual(
                    report,
                    MetadataReport.from_json(report.to_json()),
                )
                self.assertEqual(
                    proposal_set,
                    ProposalSet.from_json(proposal_set.to_json()),
                )
                self.assertEqual(proposal_set, evaluate_report(report))

    def test_controlled_placeholder_uses_existing_nonmutating_target_outcome(
        self,
    ) -> None:
        claim = _local_creator("Unknown")
        cases = (
            (
                TargetFieldState.MISSING,
                None,
                ProposalOutcome.INELIGIBLE,
                "no_supported_evidence",
            ),
            (
                TargetFieldState.NULL,
                None,
                ProposalOutcome.INELIGIBLE,
                "no_supported_evidence",
            ),
            (
                TargetFieldState.EMPTY,
                None,
                ProposalOutcome.INELIGIBLE,
                "no_supported_evidence",
            ),
            (
                TargetFieldState.UNLOADED,
                None,
                ProposalOutcome.INELIGIBLE,
                "target_state_unusable",
            ),
            (
                TargetFieldState.VALUE,
                ("Current Author",),
                ProposalOutcome.DEFER,
                "no_positive_preservation_evidence",
            ),
        )
        for state, current, outcome, reason in cases:
            with self.subTest(state=state):
                result = _result(
                    evaluate_report(
                        _report(
                            fields=_fields(
                                None,
                                authors=TargetField(
                                    TargetDestination.AUTHORS,
                                    state,
                                    current,
                                ),
                            ),
                            local_claims=(claim,),
                        )
                    ),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(outcome, result.outcome)
                self.assertEqual((), result.alternatives)
                self.assertEqual((reason,), result.reasons)

    def test_mixed_package_creators_keep_real_author_order(self) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        cases = (
            (
                (
                    _local_creator("Unknown", occurrence=0),
                    _local_creator("Ada Author", occurrence=1),
                    _local_creator("Bea Writer", occurrence=2),
                ),
                ("Ada Author", "Bea Writer"),
            ),
            (
                (
                    _local_creator("Ada Author", occurrence=0),
                    _local_creator("UNKNOWN", occurrence=1),
                    _local_creator("Bea Writer", occurrence=2),
                ),
                ("Ada Author", "Bea Writer"),
            ),
        )
        for claims, expected in cases:
            with self.subTest(expected=expected):
                report = _report(
                    fields=_fields(None, authors=authors),
                    local_claims=claims,
                )
                result = _result(
                    evaluate_report(report),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
                self.assertEqual(expected, result.alternatives[0].value.value)
                self.assertEqual(
                    tuple(
                        sorted(
                            claim.reference
                            for claim in claims
                            if claim.observed.strip().casefold() != "unknown"
                        )
                    ),
                    result.alternatives[0].evidence_references,
                )
                self.assertEqual(claims, report.local_claims)

    def test_placeholder_rule_is_exact_and_creator_context_specific(self) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        for value in ("Anonymous", "Anon.", "Unknown Author", "The Unknown"):
            with self.subTest(value=value):
                result = _result(
                    evaluate_report(
                        _report(
                            fields=_fields(None, authors=authors),
                            local_claims=(_local_creator(value),),
                        )
                    ),
                    TargetDestination.AUTHORS,
                )
                self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
                self.assertEqual((value,), result.alternatives[0].value.value)

        opening_unknown = _result(
            evaluate_report(
                _report(
                    fields=_fields(None, authors=authors),
                    local_claims=(
                        _local_creator("Unknown", basis="opening_matter"),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, opening_unknown.outcome)
        self.assertEqual(
            ("Unknown",), opening_unknown.alternatives[0].value.value
        )

        provider_unknown = _result(
            evaluate_report(
                _report(
                    fields=_fields(None, authors=authors),
                    providers=(
                        _provider(
                            "catalogue",
                            "edition-1",
                            (
                                _claim(
                                    "catalogue",
                                    "edition-1",
                                    ClaimKind.CREATOR,
                                    "Unknown",
                                    scope=ClaimScope.WORK,
                                    role="author",
                                ),
                            ),
                        ),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, provider_unknown.outcome)
        self.assertEqual(
            ("Unknown",), provider_unknown.alternatives[0].value.value
        )

        mixed_context = _result(
            evaluate_report(
                _report(
                    fields=_fields(None, authors=authors),
                    local_claims=(
                        _local_creator("Unknown", occurrence=0),
                        _local_creator(
                            "Real Author",
                            basis="opening_matter",
                            occurrence=1,
                        ),
                    ),
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, mixed_context.outcome)
        self.assertEqual(
            ("Real Author",), mixed_context.alternatives[0].value.value
        )

        unrelated_report = _report(
            title=None,
            fields=_fields(
                None,
                title=TargetField(
                    TargetDestination.TITLE,
                    TargetFieldState.MISSING,
                ),
                authors=authors,
            ),
            local_claims=(
                LocalClaim.create(
                    kind=ClaimKind.TITLE,
                    observed="Unknown",
                    canonical="Unknown",
                    excerpt="Unknown",
                    applicability="not_applicable",
                    basis="package_metadata",
                    checksum="not_applicable",
                    applicability_basis="not_applicable",
                    scope=ClaimScope.WORK,
                ),
            ),
        )
        unrelated = evaluate_report(unrelated_report)
        self.assertEqual(
            ProposalOutcome.PROPOSE_CHANGE,
            _result(unrelated, TargetDestination.TITLE).outcome,
        )
        self.assertEqual(
            ProposalOutcome.INELIGIBLE,
            _result(unrelated, TargetDestination.AUTHORS).outcome,
        )

    def test_placeholder_changes_only_the_authors_policy_result(self) -> None:
        authors = TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.MISSING,
        )
        baseline = evaluate_report(
            _report(fields=_fields(None, authors=authors))
        )
        with_placeholder = evaluate_report(
            _report(
                fields=_fields(None, authors=authors),
                local_claims=(_local_creator("Unknown"),),
            )
        )
        self.assertEqual(
            {
                result.destination: result
                for result in baseline.results
                if result.destination is not TargetDestination.AUTHORS
            },
            {
                result.destination: result
                for result in with_placeholder.results
                if result.destination is not TargetDestination.AUTHORS
            },
        )

    def test_ambiguous_local_contributor_does_not_become_partial_narrators(
        self,
    ) -> None:
        claims = tuple(
            LocalClaim.create(
                kind=ClaimKind.CONTRIBUTOR,
                observed=name,
                canonical=name,
                excerpt=name,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.EDITION,
                role=role,
                occurrence=index,
            )
            for index, (name, role) in enumerate(
                (("Nora Narrator", "narrator"), ("Unknown Credit", None))
            )
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        narrators=TargetField(
                            TargetDestination.NARRATORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=claims,
                )
            ),
            TargetDestination.NARRATORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_ambiguous_provider_contributor_does_not_become_partial_narrators(
        self,
    ) -> None:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CONTRIBUTOR,
                "Nora Narrator",
                scope=ClaimScope.EDITION,
                role="narrator",
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.CONTRIBUTOR,
                "Unknown Credit",
                scope=ClaimScope.EDITION,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        narrators=TargetField(
                            TargetDestination.NARRATORS,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(_provider("catalogue", "edition-1", claims),),
                )
            ),
            TargetDestination.NARRATORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)


if __name__ == "__main__":
    unittest.main()
