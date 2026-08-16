"""Proposal field policy: subtitle scope admission, atomic series,
complete safe cover artifacts, and the eligibility rules that
refuse to manufacture evidence.
"""

import unittest
from dataclasses import replace

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
    LocalClaim,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    comparison_links,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalAssessment,
    ProposalOutcome,
    ProposalScope,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _claim,
    _explicit_local_ebook_isbn,
    _fields,
    _local_subtitle,
    _provider,
    _report,
    _result,
)


class ProposalFieldPolicyTests(unittest.TestCase):
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
            ProposalOutcome.UNRESOLVED_CHOICE,
            _result(proposal_set, TargetDestination.GENRES).outcome,
        )

    def test_local_work_subtitle_and_provider_edition_subtitle_are_admitted(self) -> None:
        local = _local_subtitle("Local work subtitle", ClaimScope.WORK)
        local_result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        subtitle=TargetField(
                            TargetDestination.SUBTITLE,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(local,),
                )
            ),
            TargetDestination.SUBTITLE,
        )
        self.assertEqual(
            {"Local work subtitle"},
            {str(value.value.value) for value in local_result.alternatives},
        )
        self.assertEqual(
            {ProposalScope.WORK},
            {value.scope for value in local_result.alternatives},
        )
        for name in ("hardcover", "open_library"):
            with self.subTest(provider=name):
                provider = _provider(
                    name,
                    "edition-1",
                    (
                        _claim(
                            name,
                            "edition-1",
                            ClaimKind.SUBTITLE,
                            "Edition subtitle",
                            scope=ClaimScope.EDITION,
                            applicability="ebook",
                        ),
                    ),
                )
                provider_result = _result(
                    evaluate_report(
                        _report(
                            fields=_fields(
                                subtitle=TargetField(
                                    TargetDestination.SUBTITLE,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            providers=(provider,),
                        )
                    ),
                    TargetDestination.SUBTITLE,
                )
                self.assertEqual(
                    {"Edition subtitle"},
                    {str(value.value.value) for value in provider_result.alternatives},
                )
                self.assertEqual(
                    {ProposalScope.EDITION},
                    {value.scope for value in provider_result.alternatives},
                )

    def test_subtitle_scope_admission_is_source_sensitive(self) -> None:
        local_edition = _local_subtitle(
            "Wrong local edition subtitle", ClaimScope.EDITION
        )
        provider_work = _provider(
            "open_library",
            "edition-1",
            (
                _claim(
                    "open_library",
                    "edition-1",
                    ClaimKind.SUBTITLE,
                    "Wrong provider work subtitle",
                    scope=ClaimScope.WORK,
                ),
            ),
        )
        local_result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        subtitle=TargetField(
                            TargetDestination.SUBTITLE,
                            TargetFieldState.MISSING,
                        )
                    ),
                    local_claims=(local_edition,),
                )
            ),
            TargetDestination.SUBTITLE,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, local_result.outcome)
        self.assertEqual((), local_result.alternatives)
        provider_result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        subtitle=TargetField(
                            TargetDestination.SUBTITLE,
                            TargetFieldState.MISSING,
                        )
                    ),
                    providers=(provider_work,),
                )
            ),
            TargetDestination.SUBTITLE,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, provider_result.outcome)
        self.assertEqual((), provider_result.alternatives)

    def test_equal_subtitles_keep_local_and_provider_scopes_separate(self) -> None:
        local = _local_subtitle("Shared subtitle", ClaimScope.WORK)
        provisional = _claim(
            "hardcover",
            "edition-1",
            ClaimKind.SUBTITLE,
            "Shared subtitle",
            scope=ClaimScope.EDITION,
            applicability="ebook",
        )
        provider_claim = replace(
            provisional,
            links=comparison_links(provisional, (local,)),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        subtitle=TargetField(
                            TargetDestination.SUBTITLE,
                            TargetFieldState.MISSING,
                        )
                    ),
                    local_claims=(local,),
                    providers=(
                        _provider(
                            "hardcover",
                            "edition-1",
                            (provider_claim,),
                            authority="hardcover",
                        ),
                    ),
                )
            ),
            TargetDestination.SUBTITLE,
        )
        self.assertEqual(2, len(result.alternatives))
        self.assertEqual(
            {ProposalScope.WORK, ProposalScope.EDITION},
            {alternative.scope for alternative in result.alternatives},
        )
        self.assertTrue(
            all(len(alternative.correlations) == 1 for alternative in result.alternatives)
        )

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
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
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

    def test_complete_multiple_series_preserves_exact_names_and_sequences(self) -> None:
        def claims(provider: str) -> tuple[ProviderClaim, ...]:
            return (
                _claim(
                    provider,
                    "edition-1",
                    ClaimKind.SERIES,
                    "Reading Order",
                    scope=ClaimScope.WORK,
                    order=0,
                ),
                _claim(
                    provider,
                    "edition-1",
                    ClaimKind.SERIES_SEQUENCE,
                    "2",
                    scope=ClaimScope.WORK,
                    order=1,
                ),
                _claim(
                    provider,
                    "edition-1",
                    ClaimKind.SERIES,
                    "Publication Order",
                    scope=ClaimScope.WORK,
                    order=2,
                ),
                ProviderClaim.create(
                    provider=provider,
                    candidate="edition-1",
                    kind=ClaimKind.SERIES_SEQUENCE,
                    value=None,
                    state=FieldState.ABSENT,
                    scope=ClaimScope.WORK,
                    applicability="not_applicable",
                    raw_order=3,
                ),
            )

        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        series=TargetField(
                            TargetDestination.SERIES,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    providers=(
                        _provider("catalogue", "edition-1", claims("catalogue")),
                        _provider(
                            "open_library",
                            "edition-1",
                            claims("open_library"),
                        ),
                    ),
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(
            ProposalOutcome.PROPOSE_CHANGE,
            result.outcome,
            result.to_data(),
        )
        self.assertEqual(
            (
                TargetSeries("Reading Order", "2"),
                TargetSeries("Publication Order", None),
            ),
            result.alternatives[0].value.value,
        )

    def test_unusable_series_sequence_withholds_the_source_aggregate(self) -> None:
        for state in (FieldState.MALFORMED, FieldState.OVERSIZED):
            with self.subTest(state=state):
                claims = (
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.SERIES,
                        "Reading order",
                        scope=ClaimScope.WORK,
                    ),
                    ProviderClaim.create(
                        provider="catalogue",
                        candidate="edition-1",
                        kind=ClaimKind.SERIES_SEQUENCE,
                        value=None,
                        state=state,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        raw_order=1,
                    ),
                )
                result = _result(
                    evaluate_report(
                        _report(
                            title=None,
                            fields=_fields(
                                None,
                                series=TargetField(
                                    TargetDestination.SERIES,
                                    TargetFieldState.MISSING,
                                ),
                            ),
                            providers=(
                                _provider("catalogue", "edition-1", claims),
                            ),
                        )
                    ),
                    TargetDestination.SERIES,
                )
                self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)
                self.assertEqual((), result.alternatives)

    def test_series_preservation_normalizes_names_but_not_sequences(self) -> None:
        claims = (
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES,
                "Reading Order",
                scope=ClaimScope.WORK,
            ),
            _claim(
                "catalogue",
                "edition-1",
                ClaimKind.SERIES_SEQUENCE,
                "2",
                scope=ClaimScope.WORK,
                order=1,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        series=TargetField(
                            TargetDestination.SERIES,
                            TargetFieldState.VALUE,
                            (TargetSeries("reading   order", "2"),),
                        ),
                    ),
                    providers=(_provider("catalogue", "edition-1", claims),),
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(ProposalOutcome.KEEP, result.outcome)

    def test_series_preservation_accepts_local_sequence_before_name(self) -> None:
        claims = (
            LocalClaim.create(
                kind=ClaimKind.SERIES_SEQUENCE,
                observed="2",
                canonical="2",
                excerpt="2",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                occurrence=0,
            ),
            LocalClaim.create(
                kind=ClaimKind.SERIES,
                observed="Reading Order",
                canonical="Reading Order",
                excerpt="Reading Order",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                occurrence=1,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(),
                    local_claims=claims,
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(
            (TargetSeries("Reading Order", "2"),),
            result.alternatives[0].value.value,
        )

    def test_series_without_sequence_remains_eligible(self) -> None:
        claims = (
            _claim(
                "open_library",
                "edition-1",
                ClaimKind.SERIES,
                "Reading Order",
                scope=ClaimScope.WORK,
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        series=TargetField(
                            TargetDestination.SERIES,
                            TargetFieldState.VALUE,
                            (TargetSeries("reading order", None),),
                        ),
                    ),
                    providers=(
                        _provider("open_library", "edition-1", claims),
                    ),
                )
            ),
            TargetDestination.SERIES,
        )
        self.assertEqual(ProposalOutcome.KEEP, result.outcome)

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
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
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

    def test_verified_incumbent_and_alternative_require_owner_cover_choice(self) -> None:
        incumbent = CoverArtifact.create(
            b"incumbent", b"incumbent-preview", "image/png", 10, 20
        )
        alternative = CoverArtifact.create(
            b"alternative", b"alternative-preview", "image/png", 12, 24
        )
        sources = (
            CoverSource(
                cover_source_id(CoverSourceKind.ABS, "current"),
                CoverSourceKind.ABS,
                CoverSourceState.RETRIEVED,
                incumbent,
            ),
            CoverSource(
                cover_source_id(CoverSourceKind.EPUB, "embedded"),
                CoverSourceKind.EPUB,
                CoverSourceState.RETRIEVED,
                alternative,
            ),
        )
        fields = tuple(
            TargetField(
                value.destination,
                TargetFieldState.VALUE,
                incumbent.artifact_id,
            )
            if value.destination is TargetDestination.COVER
            else value
            for value in _fields("Current title")
        )
        result = _result(
            evaluate_report(_report(fields=fields, covers=sources)),
            TargetDestination.COVER,
        )

        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(
            ("current_cover_verified", "owner_cover_choice_required"),
            result.reasons,
        )
        self.assertEqual(2, len(result.alternatives))

    def test_populated_unverified_cover_is_manual_only_with_typed_incumbent_gap(self) -> None:
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
        self.assertEqual(ProposalOutcome.UNRESOLVED_CHOICE, result.outcome)
        self.assertEqual(
            (
                "current_cover_unavailable",
                "current_cover_unverified",
                "owner_cover_choice_required",
            ),
            result.reasons,
        )
        self.assertEqual(1, len(result.alternatives))
        self.assertIsNone(result.selected_alternative_id)

    def test_non_google_markup_description_is_ineligible(self) -> None:
        claim = _claim("catalogue", "edition-1", ClaimKind.DESCRIPTION, "Summary <em>", scope=ClaimScope.WORK)
        result = _result(
            evaluate_report(
                _report(
                    providers=(_provider("catalogue", "edition-1", (claim,)),)
                )
            ),
            TargetDestination.DESCRIPTION,
        )
        self.assertEqual(ProposalOutcome.INELIGIBLE, result.outcome)

    def test_transformed_google_visible_angle_characters_remain_eligible(self) -> None:
        claim = _claim(
            "google",
            "edition-1",
            ClaimKind.DESCRIPTION,
            "2 < 3",
            scope=ClaimScope.WORK,
        )
        result = _result(
            evaluate_report(
                _report(
                    providers=(
                        _provider(
                            "google",
                            "edition-1",
                            (claim,),
                            (
                                ProviderEvidence(
                                    ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
                                    "google-books-html-visible-text-v1",
                                    ClaimScope.WORK,
                                ),
                            ),
                        ),
                    )
                )
            ),
            TargetDestination.DESCRIPTION,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual("2 < 3", result.alternatives[0].value.value)

    def test_provider_subtitle_and_asin_require_exact_ebook_applicability(self) -> None:
        cases = (
            (TargetDestination.SUBTITLE, ClaimKind.SUBTITLE, "Edition subtitle"),
            (TargetDestination.ASIN, ClaimKind.ASIN, "B012345678"),
        )
        for destination, kind, value in cases:
            for applicability in ("ebook", "print", "unknown", "ambiguous"):
                claim = _claim(
                    "catalogue",
                    "edition-1",
                    kind,
                    value,
                    scope=ClaimScope.EDITION,
                    applicability=applicability,
                )
                result = _result(
                    evaluate_report(
                        _report(
                            providers=(
                                _provider(
                                    "catalogue",
                                    "edition-1",
                                    (claim,),
                                ),
                            )
                        )
                    ),
                    destination,
                )
                with self.subTest(
                    destination=destination,
                    applicability=applicability,
                ):
                    self.assertEqual(
                        (
                            ProposalOutcome.PROPOSE_CHANGE
                            if applicability == "ebook"
                            else ProposalOutcome.INELIGIBLE
                        ),
                        result.outcome,
                    )

    def test_long_descriptions_compare_without_title_bounds(self) -> None:
        matching = "A complete description " + "x" * 700
        differing = "A different description " + "y" * 700
        field = TargetField(
            TargetDestination.DESCRIPTION,
            TargetFieldState.VALUE,
            matching,
        )
        for candidate, expected in (
            (matching, ProposalOutcome.KEEP),
            (differing, ProposalOutcome.UNRESOLVED_CHOICE),
        ):
            with self.subTest(candidate=candidate[:12]):
                result = _result(
                    evaluate_report(
                        _report(
                            fields=_fields(description=field),
                            providers=(
                                _provider(
                                    "catalogue",
                                    "edition-1",
                                    (
                                        _claim(
                                            "catalogue",
                                            "edition-1",
                                            ClaimKind.DESCRIPTION,
                                            candidate,
                                            scope=ClaimScope.WORK,
                                        ),
                                    ),
                                ),
                            ),
                        )
                    ),
                    TargetDestination.DESCRIPTION,
                )
                self.assertEqual(expected, result.outcome)

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
        self.assertEqual(ProposalOutcome.DEFER, isbn_result.outcome)
        self.assertEqual((), isbn_result.alternatives)
        self.assertIn(
            "identifier_applicability_unresolved", isbn_result.reasons
        )
        self.assertEqual(
            ProposalOutcome.DEFER,
            _result(proposal_set, TargetDestination.EXPLICIT).outcome,
        )

    def test_explicit_local_ebook_isbn_remains_eligible(self) -> None:
        isbn = LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed="0306406152",
            canonical="0306406152",
            excerpt="0306406152",
            applicability="ebook",
            checksum="valid_isbn_10",
            applicability_basis="ebook",
            scope=ClaimScope.EDITION,
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        isbn=TargetField(
                            TargetDestination.ISBN,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(isbn,),
                )
            ),
            TargetDestination.ISBN,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual("0306406152", result.alternatives[0].value.value)

    def test_isbn_family_observations_form_one_empty_target_alternative(self) -> None:
        local = _explicit_local_ebook_isbn()
        provider = _provider(
            "catalogue",
            "edition-1",
            (
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "0306406152",
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                ),
                _claim(
                    "catalogue",
                    "edition-1",
                    ClaimKind.ISBN,
                    "9780306406157",
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                    order=1,
                ),
            ),
        )
        result = _result(
            evaluate_report(
                _report(
                    fields=_fields(
                        isbn=TargetField(
                            TargetDestination.ISBN,
                            TargetFieldState.MISSING,
                        ),
                    ),
                    local_claims=(local,),
                    providers=(provider,),
                )
            ),
            TargetDestination.ISBN,
        )
        self.assertEqual(ProposalOutcome.PROPOSE_CHANGE, result.outcome)
        self.assertEqual(1, len(result.alternatives))
        self.assertEqual("9780306406157", result.alternatives[0].value.value)
        self.assertEqual(ProposalAssessment.FILLS_EMPTY, result.alternatives[0].assessment)
        self.assertEqual(3, len(result.alternatives[0].evidence_references))


if __name__ == "__main__":
    unittest.main()
