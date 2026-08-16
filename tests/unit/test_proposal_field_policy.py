"""Proposal field policy: subtitle scope admission, atomic series,
complete safe cover artifacts, and the eligibility rules that
refuse to manufacture evidence.
"""

from dataclasses import replace

import pytest

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


def test_populated_subjective_fields_do_not_get_replaced() -> None:
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
    assert (
        ProposalOutcome.UNRESOLVED_CHOICE
        == _result(proposal_set, TargetDestination.GENRES).outcome
    )


@pytest.mark.parametrize(
    "name", ("hardcover", "open_library"), ids=("hardcover", "open_library")
)
def test_local_work_subtitle_and_provider_edition_subtitle_are_admitted(
    name: str,
) -> None:
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
    assert {"Local work subtitle"} == {
        str(value.value.value) for value in local_result.alternatives
    }
    assert {ProposalScope.WORK} == {value.scope for value in local_result.alternatives}
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
    assert {"Edition subtitle"} == {
        str(value.value.value) for value in provider_result.alternatives
    }
    assert {ProposalScope.EDITION} == {
        value.scope for value in provider_result.alternatives
    }


def test_subtitle_scope_admission_is_source_sensitive() -> None:
    local_edition = _local_subtitle("Wrong local edition subtitle", ClaimScope.EDITION)
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
    assert ProposalOutcome.INELIGIBLE == local_result.outcome
    assert () == local_result.alternatives
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
    assert ProposalOutcome.INELIGIBLE == provider_result.outcome
    assert () == provider_result.alternatives


def test_equal_subtitles_keep_local_and_provider_scopes_separate() -> None:
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
    assert 2 == len(result.alternatives)
    assert {ProposalScope.WORK, ProposalScope.EDITION} == {
        alternative.scope for alternative in result.alternatives
    }
    assert all(
        len(alternative.correlations) == 1 for alternative in result.alternatives
    )


def test_candidates_from_one_provider_are_one_correlation_group() -> None:
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
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert 1 == len(result.alternatives[0].correlations)
    assert "insufficient_independent_corroboration" in result.reasons


def test_partial_multiple_series_never_invents_pairing() -> None:
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
            _report(providers=(_provider("catalogue", "edition-1", claims),))
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_complete_multiple_series_preserves_exact_names_and_sequences() -> None:
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
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome, result.to_data()
    assert (
        TargetSeries("Reading Order", "2"),
        TargetSeries("Publication Order", None),
    ) == result.alternatives[0].value.value


@pytest.mark.parametrize(
    "state",
    (FieldState.MALFORMED, FieldState.OVERSIZED),
    ids=("malformed", "oversized"),
)
def test_unusable_series_sequence_withholds_the_source_aggregate(
    state: FieldState,
) -> None:
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
                providers=(_provider("catalogue", "edition-1", claims),),
            )
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome
    assert () == result.alternatives


def test_series_preservation_normalizes_names_but_not_sequences() -> None:
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
    assert ProposalOutcome.KEEP == result.outcome


def test_series_preservation_accepts_local_sequence_before_name() -> None:
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
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert (TargetSeries("Reading Order", "2"),) == result.alternatives[0].value.value


def test_series_without_sequence_remains_eligible() -> None:
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
                providers=(_provider("open_library", "edition-1", claims),),
            )
        ),
        TargetDestination.SERIES,
    )
    assert ProposalOutcome.KEEP == result.outcome


def test_cover_requires_one_complete_safe_artifact() -> None:
    artifact = CoverArtifact.create(b"cover", b"preview", "image/png", 10, 20)
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
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
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
    assert ProposalOutcome.DEFER == incomplete.outcome
    assert "cover_gallery_incomplete" in incomplete.reasons


def test_verified_incumbent_and_alternative_require_owner_cover_choice() -> None:
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

    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert ("current_cover_verified", "owner_cover_choice_required") == result.reasons
    assert 2 == len(result.alternatives)


def test_populated_unverified_cover_is_manual_only_with_typed_incumbent_gap() -> None:
    artifact = CoverArtifact.create(b"cover", b"preview", "image/png", 10, 20)
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
    assert ProposalOutcome.UNRESOLVED_CHOICE == result.outcome
    assert (
        "current_cover_unavailable",
        "current_cover_unverified",
        "owner_cover_choice_required",
    ) == result.reasons
    assert 1 == len(result.alternatives)
    assert result.selected_alternative_id is None


def test_non_google_markup_description_is_ineligible() -> None:
    claim = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.DESCRIPTION,
        "Summary <em>",
        scope=ClaimScope.WORK,
    )
    result = _result(
        evaluate_report(
            _report(providers=(_provider("catalogue", "edition-1", (claim,)),))
        ),
        TargetDestination.DESCRIPTION,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome


def test_transformed_google_visible_angle_characters_remain_eligible() -> None:
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
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert "2 < 3" == result.alternatives[0].value.value


@pytest.mark.parametrize(
    "applicability",
    ("ebook", "print", "unknown", "ambiguous"),
    ids=("ebook", "print", "unknown", "ambiguous"),
)
@pytest.mark.parametrize(
    ("destination", "kind", "value"),
    (
        (TargetDestination.SUBTITLE, ClaimKind.SUBTITLE, "Edition subtitle"),
        (TargetDestination.ASIN, ClaimKind.ASIN, "B012345678"),
    ),
    ids=("subtitle", "asin"),
)
def test_provider_subtitle_and_asin_require_exact_ebook_applicability(
    destination: TargetDestination, kind: ClaimKind, value: str, applicability: str
) -> None:
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
    assert (
        ProposalOutcome.PROPOSE_CHANGE
        if applicability == "ebook"
        else ProposalOutcome.INELIGIBLE
    ) == result.outcome


_MATCHING_DESCRIPTION = "A complete description " + "x" * 700
_DIFFERING_DESCRIPTION = "A different description " + "y" * 700


@pytest.mark.parametrize(
    ("candidate", "expected"),
    (
        (_MATCHING_DESCRIPTION, ProposalOutcome.KEEP),
        (_DIFFERING_DESCRIPTION, ProposalOutcome.UNRESOLVED_CHOICE),
    ),
    ids=("matching", "differing"),
)
def test_long_descriptions_compare_without_title_bounds(
    candidate: str, expected: ProposalOutcome
) -> None:
    field = TargetField(
        TargetDestination.DESCRIPTION,
        TargetFieldState.VALUE,
        _MATCHING_DESCRIPTION,
    )
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
    assert expected == result.outcome


def test_malformed_target_is_ineligible_even_with_evidence() -> None:
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
                providers=(_provider("catalogue", "edition-1", (claim,)),),
            )
        ),
        TargetDestination.LANGUAGE,
    )
    assert ProposalOutcome.INELIGIBLE == result.outcome


def test_identifier_and_explicit_rules_do_not_manufacture_evidence() -> None:
    isbn = _claim(
        "catalogue",
        "edition-1",
        ClaimKind.ISBN,
        "0306406152",
        scope=ClaimScope.EDITION,
        applicability="ebook",
    )
    fields = _fields(
        isbn=TargetField(TargetDestination.ISBN, TargetFieldState.MISSING),
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
    assert ProposalOutcome.DEFER == isbn_result.outcome
    assert () == isbn_result.alternatives
    assert "identifier_applicability_unresolved" in isbn_result.reasons
    assert (
        ProposalOutcome.DEFER
        == _result(proposal_set, TargetDestination.EXPLICIT).outcome
    )


def test_explicit_local_ebook_isbn_remains_eligible() -> None:
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
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert "0306406152" == result.alternatives[0].value.value


def test_isbn_family_observations_form_one_empty_target_alternative() -> None:
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
    assert ProposalOutcome.PROPOSE_CHANGE == result.outcome
    assert 1 == len(result.alternatives)
    assert "9780306406157" == result.alternatives[0].value.value
    assert ProposalAssessment.FILLS_EMPTY == result.alternatives[0].assessment
    assert 3 == len(result.alternatives[0].evidence_references)
