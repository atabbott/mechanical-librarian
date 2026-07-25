import unittest

from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
    ProposalValueError,
    evaluate_report,
)


def _fields(
    current_title: str | None = "Current title",
    **overrides: TargetField,
) -> tuple[TargetField, ...]:
    values = {
        value.destination: value
        for value in default_target_fields(current_title)
    }
    values.update(
        {
            TargetDestination[key.upper()]: value
            for key, value in overrides.items()
        }
    )
    return tuple(values[destination] for destination in TargetDestination)


def _claim(
    provider: str,
    candidate: str,
    kind: ClaimKind,
    value: str,
    *,
    scope: ClaimScope,
    order: int = 0,
    role: str | None = None,
    applicability: str = "not_applicable",
) -> ProviderClaim:
    return ProviderClaim.create(
        provider=provider,
        candidate=candidate,
        kind=kind,
        value=value,
        state=FieldState.VALID,
        scope=scope,
        applicability=applicability,
        role=role,
        raw_order=order,
    )


def _provider(
    name: str,
    candidate: str,
    claims: tuple[ProviderClaim, ...],
    evidence: tuple[ProviderEvidence, ...] = (),
) -> ProviderObservation:
    return ProviderObservation(
        name,
        ProviderStatus.CANDIDATES,
        (),
        (ProviderCandidate(candidate, f"{name}-work", claims, evidence),),
    )


def _report(
    *,
    title: str | None = "Current title",
    fields: tuple[TargetField, ...] | None = None,
    local_claims: tuple[LocalClaim, ...] = (),
    providers: tuple[ProviderObservation, ...] = (),
    covers: tuple[CoverSource, ...] = (),
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            title,
            _fields(title) if fields is None else fields,
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=local_claims,
        local_reasons=(
            ("opening_matter_creator",)
            if local_claims
            else ("no_opening_claims",)
        ),
        providers=tuple(sorted(providers, key=lambda value: value.provider)),
        cover_sources=covers,
    )


def _result(proposal_set: ProposalSet, destination: TargetDestination):
    return next(
        value
        for value in proposal_set.results
        if value.destination is destination
    )


class ProposalSetTests(unittest.TestCase):
    def test_exact_sixteen_results_are_deterministic_and_round_trip(self) -> None:
        proposal_set = evaluate_report(_report())
        self.assertEqual(tuple(TargetDestination), tuple(
            value.destination for value in proposal_set.results
        ))
        self.assertEqual(proposal_set, ProposalSet.from_json(proposal_set.to_json()))
        self.assertEqual(proposal_set, evaluate_report(_report()))
        self.assertNotIn("excerpt", proposal_set.to_json())
        self.assertNotIn("/Users/private", proposal_set.to_json())
        with self.assertRaises(ProposalValueError):
            ProposalSet.from_json(proposal_set.to_json() + " ")

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
        self.assertEqual(ProposalOutcome.NEEDS_REVIEW, result.outcome)
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
        self.assertEqual(ProposalOutcome.DEFER, result.outcome)
        self.assertIn(
            "insufficient_independent_corroboration", result.reasons
        )

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
                    local_claims=claims,
                )
            ),
            TargetDestination.AUTHORS,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_populated_subjective_fields_do_not_get_replaced(self) -> None:
        genres = TargetField(
            TargetDestination.GENRES,
            TargetFieldState.VALUE,
            ("Fantasy",),
        )
        claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.GENRE,
            "Mystery",
            scope=ClaimScope.WORK,
        )
        proposal_set = evaluate_report(
            _report(
                fields=_fields(genres=genres),
                providers=(_provider("catalogue", "edition-1", (claim,)),),
            )
        )
        self.assertEqual(
            ProposalOutcome.DEFER,
            _result(proposal_set, TargetDestination.GENRES).outcome,
        )

    def test_independent_corroboration_can_propose_a_populated_replacement(self) -> None:
        providers = tuple(
            _provider(
                name,
                f"{name}-edition",
                (
                    _claim(
                        name,
                        f"{name}-edition",
                        ClaimKind.TITLE,
                        "Corrected title",
                        scope=ClaimScope.WORK,
                    ),
                ),
            )
            for name in ("alpha", "beta")
        )
        result = _result(
            evaluate_report(_report(providers=providers)),
            TargetDestination.TITLE,
        )
        self.assertEqual(ProposalOutcome.NEEDS_REVIEW, result.outcome)
        self.assertEqual(2, len(result.alternatives[0].correlations))

    def test_candidates_from_one_provider_are_one_correlation_group(self) -> None:
        candidates = tuple(
            ProviderCandidate(
                identity,
                "catalogue-work",
                (
                    _claim(
                        "catalogue",
                        identity,
                        ClaimKind.TITLE,
                        "Corrected title",
                        scope=ClaimScope.WORK,
                    ),
                ),
            )
            for identity in ("edition-1", "edition-2")
        )
        provider = ProviderObservation(
            "catalogue",
            ProviderStatus.CANDIDATES,
            (),
            candidates,
        )
        result = _result(
            evaluate_report(_report(providers=(provider,))),
            TargetDestination.TITLE,
        )
        self.assertEqual(ProposalOutcome.DEFER, result.outcome)
        self.assertEqual(1, len(result.alternatives[0].correlations))
        self.assertIn(
            "insufficient_independent_corroboration", result.reasons
        )

    def test_partial_multiple_series_never_invents_pairing(self) -> None:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES,
                "Reading order",
                scope=ClaimScope.WORK,
                order=0,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES,
                "Publication order",
                scope=ClaimScope.WORK,
                order=1,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES_SEQUENCE,
                "2",
                scope=ClaimScope.WORK,
                order=2,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    providers=(_provider("catalogue", "edition-1", claims),)
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_work_publication_fill_preserves_year_date_consistency(self) -> None:
        evidence = (
            ProviderEvidence(
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                "2001-02-03",
                ClaimScope.WORK,
            ),
        )
        provider = _provider("catalogue", "edition-1", (), evidence)
        fields = _fields(
            published_year=TargetField(
                TargetDestination.PUBLISHED_YEAR,
                TargetFieldState.VALUE,
                "1999",
            ),
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE,
                TargetFieldState.MISSING,
            ),
        )
        proposal_set = evaluate_report(
            _report(fields=fields, providers=(provider,))
        )
        date_result = _result(
            proposal_set, TargetDestination.PUBLISHED_DATE
        )
        self.assertEqual(ProposalOutcome.DEFER, date_result.outcome)
        self.assertIn("date_year_conflict", date_result.reasons)

    def test_full_date_refining_a_populated_year_needs_independent_sources(
        self,
    ) -> None:
        year = TargetField(
            TargetDestination.PUBLISHED_YEAR,
            TargetFieldState.VALUE,
            "2001",
        )
        claims = tuple(
            _claim(
                provider,
                f"{provider}-edition",
                ClaimKind.PUBLICATION_DATE,
                "2001-02-03",
                scope=ClaimScope.EDITION,
            )
            for provider in ("alpha", "beta")
        )
        one_source = _result(
            evaluate_report(
                _report(
                    fields=_fields(published_year=year),
                    providers=(
                        _provider("alpha", "alpha-edition", (claims[0],)),
                    ),
                )
            ),
            TargetDestination.PUBLISHED_DATE,
        )
        self.assertEqual(ProposalOutcome.DEFER, one_source.outcome)
        self.assertIn(
            "date_refinement_requires_corroboration",
            one_source.reasons,
        )
        two_sources = _result(
            evaluate_report(
                _report(
                    fields=_fields(published_year=year),
                    providers=(
                        _provider("alpha", "alpha-edition", (claims[0],)),
                        _provider("beta", "beta-edition", (claims[1],)),
                    ),
                )
            ),
            TargetDestination.PUBLISHED_DATE,
        )
        self.assertEqual(ProposalOutcome.NEEDS_REVIEW, two_sources.outcome)

    def test_cover_requires_one_complete_safe_artifact(self) -> None:
        artifact = CoverArtifact.create(
            b"cover", b"preview", "image/png", 10, 20
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
        proposal_set = evaluate_report(_report(covers=(source,)))
        result = _result(proposal_set, TargetDestination.COVER)
        self.assertEqual(ProposalOutcome.NEEDS_REVIEW, result.outcome)
        timeout = CoverSource(
            cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:timeout"),
            CoverSourceKind.HARDCOVER,
            CoverSourceState.TIMEOUT,
            timeout_scope="attempted",
            provenance=CoverProvenance(
                "catalogue",
                CoverDesignation.WORK,
                "catalogue-work",
            ),
        )
        incomplete = _result(
            evaluate_report(_report(covers=(source, timeout))),
            TargetDestination.COVER,
        )
        self.assertEqual(ProposalOutcome.DEFER, incomplete.outcome)
        self.assertIn("cover_gallery_incomplete", incomplete.reasons)

    def test_populated_unverified_cover_is_never_replaced(self) -> None:
        artifact = CoverArtifact.create(
            b"cover", b"preview", "image/png", 10, 20
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
        fields = _fields(
            cover=TargetField(
                TargetDestination.COVER,
                TargetFieldState.VALUE,
                "current-cover",
            )
        )
        result = _result(
            evaluate_report(_report(fields=fields, covers=(source,))),
            TargetDestination.COVER,
        )
        self.assertEqual(ProposalOutcome.DEFER, result.outcome)
        self.assertIn("current_cover_unverified", result.reasons)

    def test_markup_description_is_not_a_plain_text_candidate(self) -> None:
        claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.DESCRIPTION,
            "Summary <em>",
            scope=ClaimScope.WORK,
        )
        result = _result(
            evaluate_report(
                _report(
                    providers=(_provider("catalogue", "edition-1", (claim,)),)
                )
            ),
            TargetDestination.DESCRIPTION,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
        self.assertEqual((), result.alternatives)

    def test_malformed_target_is_ineligible_even_with_evidence(self) -> None:
        claim = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.LANGUAGE,
            "en",
            scope=ClaimScope.EDITION,
        )
        fields = _fields(
            language=TargetField(
                TargetDestination.LANGUAGE,
                TargetFieldState.MALFORMED,
            )
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=fields,
                    providers=(
                        _provider("catalogue", "edition-1", (claim,)),
                    ),
                )
            ),
            TargetDestination.LANGUAGE,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)

    def test_identifier_and_explicit_rules_do_not_manufacture_evidence(self) -> None:
        isbn = _claim(
            "catalogue",
            "edition-1",
            ClaimKind.ISBN,
            "0306406152",
            scope=ClaimScope.EDITION,
            applicability="ebook",
        )
        fields = _fields(
            isbn=TargetField(
                TargetDestination.ISBN, TargetFieldState.MISSING
            ),
            explicit=TargetField(
                TargetDestination.EXPLICIT,
                TargetFieldState.VALUE,
                False,
            ),
        )
        proposal_set = evaluate_report(
            _report(
                fields=fields,
                providers=(_provider("catalogue", "edition-1", (isbn,)),),
            )
        )
        isbn_result = _result(proposal_set, TargetDestination.ISBN)
        self.assertEqual(ProposalOutcome.NEEDS_REVIEW, isbn_result.outcome)
        self.assertEqual(
            "0306406152", isbn_result.alternatives[0].value.value
        )
        self.assertEqual(
            ProposalOutcome.DEFER,
            _result(proposal_set, TargetDestination.EXPLICIT).outcome,
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
