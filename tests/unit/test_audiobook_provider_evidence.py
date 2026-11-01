"""The `metadata-report-v42` audiobook provider grammar.

Marketplace provenance, the bounded chapter-structure value, the duration
agreement band, and the lifted refusal that let an audio report carry a
provider observation at all.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mechanical_librarian.application.metadata_report import (
    _audio_with_runtime_observations,
)
from mechanical_librarian.domain.audio_inspection import AudioObservationKind
from mechanical_librarian.domain.metadata_report import (
    AUDIOBOOK_MARKETPLACES,
    DURATION_AGREEMENT_BAND_SECONDS,
    MAX_PROVIDER_CHAPTER_DURATIONS,
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonMethod,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderChapterStructure,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetFacts,
    canonical_asin,
    comparison_links,
    default_target_fields,
)
from tests.support.audiobook_reports import audio_local_claims, qualified_audiobook
from tests.support.metadata_report_doubles import single_track_audio_facts


def _structure_evidence(
    structure: ProviderChapterStructure,
    *,
    scope: ClaimScope = ClaimScope.EDITION,
) -> ProviderEvidence:
    return ProviderEvidence(
        ProviderEvidenceKind.CHAPTER_STRUCTURE,
        str(structure.runtime_seconds),
        scope,
        chapters=structure,
    )


def _duration_claim(value: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.DURATION,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )


def _provider_duration(value: str) -> ProviderClaim:
    return ProviderClaim.create(
        provider="audnexus",
        candidate="asin-1",
        kind=ClaimKind.DURATION,
        value=value,
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("B002V1BFKM", "B002V1BFKM"),
        ("b002v1bfkm", "B002V1BFKM"),
        ("  0743563123  ", "0743563123"),
        ("074356312X", "074356312X"),
        ("B002V1BFK", None),
        ("B002V1BFKMM", None),
        ("B002-1BFKM", None),
        (None, None),
        (10, None),
    ],
)
def test_a_canonical_asin_is_ten_characters_and_never_prefix_filtered(
    value: object, expected: str | None
) -> None:
    """ADR-0061 counted seventeen genuine ISBN-10-shaped ASINs on 103.

    A `B0` filter would have refused every one of them, so shape is the only
    thing checked here.
    """
    assert expected == canonical_asin(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("marketplace", AUDIOBOOK_MARKETPLACES)
def test_every_admitted_marketplace_is_edition_scoped_provenance(
    marketplace: str,
) -> None:
    evidence = ProviderEvidence(
        ProviderEvidenceKind.ASIN_MARKETPLACE, marketplace, ClaimScope.EDITION
    )
    assert marketplace == evidence.value
    with pytest.raises(ReportValueError):
        ProviderCandidate(
            "asin-1",
            "work-1",
            (),
            (replace(evidence, scope=ClaimScope.WORK),),
        )


@pytest.mark.parametrize("marketplace", ["zz", "US", "", "united_states"])
def test_a_marketplace_outside_the_closed_set_is_refused(marketplace: str) -> None:
    with pytest.raises(ReportValueError):
        ProviderEvidence(
            ProviderEvidenceKind.ASIN_MARKETPLACE, marketplace, ClaimScope.EDITION
        )


def test_the_chapter_vector_is_bounded_and_states_its_own_overflow() -> None:
    """1,024 is the same ceiling ADR-0063 puts on embedded chapters.

    Neither side of a comparison can be longer than the other can ever be,
    and the 2 MiB report document is what makes that a bound rather than a
    preference.
    """
    full = ProviderChapterStructure(
        MAX_PROVIDER_CHAPTER_DURATIONS + 6,
        604_800,
        (300,) * MAX_PROVIDER_CHAPTER_DURATIONS,
        True,
    )
    assert full.durations_truncated
    for keywords in (
        {"durations_seconds": (300,) * (MAX_PROVIDER_CHAPTER_DURATIONS + 1)},
        # An overflow flag on a vector that never filled, or a count the
        # vector already holds, both describe a truncation that did not
        # happen.
        {"durations_seconds": (300, 300)},
        {"chapter_count": MAX_PROVIDER_CHAPTER_DURATIONS},
        {"durations_truncated": False},
    ):
        with pytest.raises(ReportValueError):
            replace(full, **keywords)
    with pytest.raises(ReportValueError):
        ProviderChapterStructure(3, 900, (300, 300))
    with pytest.raises(ReportValueError):
        ProviderChapterStructure(2, 900, (300, -1))
    assert () == ProviderChapterStructure(0, 43_200).durations_seconds


def test_chapter_structure_binds_to_its_kind_its_runtime_and_one_candidate() -> None:
    structure = ProviderChapterStructure(3, 1_800, (600, 600, 600))
    evidence = _structure_evidence(structure)
    assert structure == evidence.chapters
    with pytest.raises(ReportValueError):
        replace(evidence, value="1799")
    with pytest.raises(ReportValueError):
        ProviderEvidence(
            ProviderEvidenceKind.CHAPTER_STRUCTURE, "1800", ClaimScope.EDITION
        )
    with pytest.raises(ReportValueError):
        ProviderEvidence(
            ProviderEvidenceKind.PAGE_COUNT,
            "1800",
            ClaimScope.EDITION,
            chapters=structure,
        )
    other = ProviderChapterStructure(2, 1_200, (600, 600))
    with pytest.raises(ReportValueError):
        ProviderCandidate(
            "asin-1",
            "work-1",
            (),
            tuple(
                sorted(
                    (_structure_evidence(structure), _structure_evidence(other)),
                    key=lambda value: value.value,
                )
            ),
        )


def test_the_chapter_structure_survives_the_report_document() -> None:
    structure = ProviderChapterStructure(3, 1_800, (600, 601, 599))
    report = _audio_report(
        (
            ProviderEvidence(
                ProviderEvidenceKind.ASIN_MARKETPLACE, "uk", ClaimScope.EDITION
            ),
            _structure_evidence(structure),
        )
    )
    replayed = MetadataReport.from_json(report.to_json())
    assert report == replayed
    evidence = replayed.providers[0].candidates[0].evidence
    assert {"uk"} == {
        value.value
        for value in evidence
        if value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
    }
    assert structure == next(
        value.chapters
        for value in evidence
        if value.kind is ProviderEvidenceKind.CHAPTER_STRUCTURE
    )


@pytest.mark.parametrize(
    ("provider_value", "outcome", "method"),
    [
        ("43200.000", Comparison.AGREEMENT, ComparisonMethod.EXACT),
        ("43200", Comparison.AGREEMENT, ComparisonMethod.DURATION_BAND_AGREEMENT),
        (
            f"{43_200 + DURATION_AGREEMENT_BAND_SECONDS}.000",
            Comparison.AGREEMENT,
            ComparisonMethod.DURATION_BAND_AGREEMENT,
        ),
        (
            f"{43_200 - DURATION_AGREEMENT_BAND_SECONDS}.000",
            Comparison.AGREEMENT,
            ComparisonMethod.DURATION_BAND_AGREEMENT,
        ),
        (
            f"{43_200 + DURATION_AGREEMENT_BAND_SECONDS}.001",
            Comparison.DISAGREEMENT,
            ComparisonMethod.NONE,
        ),
    ],
)
def test_two_running_times_agree_inside_the_band_and_not_outside_it(
    provider_value: str, outcome: Comparison, method: ComparisonMethod
) -> None:
    """A provider publishes minutes; the container publishes milliseconds.

    Equality would report two correct statements of one recording as a
    disagreement, which is exactly what the band exists to stop.
    """
    local = _duration_claim("43200.000")
    links = comparison_links(_provider_duration(provider_value), (local,))
    assert 1 == len(links)
    assert outcome == links[0].outcome
    assert method == links[0].method


def test_no_ebook_comparison_reaches_the_duration_band() -> None:
    """The band is reached by claim kind, and only `DURATION` has one.

    Every ebook kind still compares exactly as it did before `v32`.
    """
    for kind, value, local_value, canonical in (
        (ClaimKind.TITLE, "The Example", "the  example", "the example"),
        (ClaimKind.ISBN, "9780306406157", "9780306406157", "9780306406157"),
        (ClaimKind.PUBLISHER, "Example Press", "Example Press", "Example Press"),
    ):
        local = LocalClaim.create(
            kind=kind,
            observed=local_value,
            canonical=canonical,
            excerpt=local_value,
            applicability="not_applicable" if kind is not ClaimKind.ISBN else "ebook",
            basis="package_metadata",
            checksum="not_applicable"
            if kind is not ClaimKind.ISBN
            else "valid_isbn_13",
            applicability_basis="not_applicable"
            if kind is not ClaimKind.ISBN
            else "ebook",
        )
        provider = ProviderClaim.create(
            provider="google_books",
            candidate="edition-1",
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=local.scope,
            applicability=local.applicability,
        )
        links = comparison_links(provider, (local,))
        assert ComparisonMethod.DURATION_BAND_AGREEMENT != links[0].method


def test_a_v33_audio_report_may_carry_a_provider_observation() -> None:
    """ADR-0063 refused one because no audiobook provider existed.

    ADR-0061 admits four, so the refusal is gone; the pipeline still
    dispatches none until the activation packet, which is a different fact.
    """
    report = _audio_report(())
    assert "metadata-report-v66" == report.schema_version
    assert ProviderQueryKind.ASIN == report.providers[0].query_kind
    assert "audible_catalog" == report.providers[0].authority
    assert report == MetadataReport.from_json(report.to_json())


@pytest.mark.parametrize(
    "authority", ["audible_catalog", "audnexus", "audiosilo", "itunes_search"]
)
def test_every_admitted_audiobook_adapter_may_be_an_authority(authority: str) -> None:
    assert (
        authority
        == ProviderObservation(
            "catalogue", ProviderStatus.NO_MATCH, authority=authority
        ).authority
    )
    with pytest.raises(ReportValueError):
        ProviderObservation("catalogue", ProviderStatus.NO_MATCH, authority="audible")


def _audio_report(evidence: tuple[ProviderEvidence, ...]) -> MetadataReport:
    candidate = ProviderCandidate("asin-1", "work-1", (), evidence)
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "audiobooks",
            "item-1",
            default_target_fields("The Recording"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(),
        local_reasons=("no_local_audio_claims",),
        providers=(
            ProviderObservation(
                "catalogue",
                ProviderStatus.CANDIDATES,
                candidates=(candidate,),
                query_kind=ProviderQueryKind.ASIN,
                authority="audible_catalog",
            ),
        ),
        audio=single_track_audio_facts(),
    )


def test_a_shaped_lane_records_the_truncation_publication_would_lose() -> None:
    """Media-health comparison survives alongside informational runtime hints."""
    claims = audio_local_claims()
    observation = qualified_audiobook(claims, duration="72000.000")

    (candidate,) = observation.candidates
    assert candidate.runtime_short_of_local is True
    assert [
        claim.value for claim in candidate.claims if claim.kind is ClaimKind.DURATION
    ] == ["72000.000"]
    assert all(
        claim.scope is ClaimScope.WORK
        or claim.kind in {ClaimKind.ASIN, ClaimKind.DURATION}
        for claim in candidate.claims
    )

    audio = single_track_audio_facts()
    composed = _audio_with_runtime_observations(audio, (observation,))

    assert composed is not None
    assert [
        (value.kind, value.candidate, value.tracks)
        for value in composed.observations
        if value.kind is AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER
    ] == [
        (
            AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER,
            candidate.identity,
            (),
        )
    ]


def test_a_corroborated_lane_records_no_truncation_and_keeps_its_runtime() -> None:
    """Inside the band there is nothing to record and nothing was lost.

    A candidate the band admitted agrees with this recording by construction,
    so asking whether the recording is short of it would answer a question
    admission has already closed -- and its own `DURATION` claim survives
    publication, so the read-time derivation can ask again from the document.
    """
    claims = audio_local_claims()
    observation = qualified_audiobook(claims, duration="58200.000")

    (candidate,) = observation.candidates
    assert candidate.runtime_short_of_local is False
    assert any(
        claim.kind is ClaimKind.DURATION and claim.scope is ClaimScope.EDITION
        for claim in candidate.claims
    )

    audio = single_track_audio_facts()

    assert _audio_with_runtime_observations(audio, (observation,)) == audio


def test_a_longer_recording_than_the_edition_states_records_nothing() -> None:
    """A recording with more audio than the edition claims is not truncated."""
    claims = audio_local_claims()
    observation = qualified_audiobook(claims, duration="40000.000")

    (candidate,) = observation.candidates
    assert candidate.runtime_short_of_local is False
    assert (
        _audio_with_runtime_observations(single_track_audio_facts(), (observation,))
        == single_track_audio_facts()
    )


def test_two_providers_disagreeing_about_one_edition_record_nothing() -> None:
    """A disagreement between authorities is not evidence of a shortfall.

    Both answers name the same edition with informational runtime hints. One
    says this recording is short of that edition by hours; the other compared
    the same two numbers and says it is not. Recording the accusation because
    one provider made it would publish the more alarming half of a
    contradiction as though it were the finding.

    Qualification cannot see this: it runs once per provider and never has the
    second answer in hand. Assembly does, which is where the identity is
    decided.
    """
    claims = audio_local_claims()
    short = qualified_audiobook(claims, duration="72000.000", provider="audnexus")
    long_enough = qualified_audiobook(claims, duration="40000.000")

    (short_candidate,) = short.candidates
    (long_candidate,) = long_enough.candidates
    assert short_candidate.identity == long_candidate.identity
    assert short_candidate.runtime_short_of_local is True
    assert long_candidate.runtime_short_of_local is False
    # Raw hints remain available to fallback vetoes without changing media health.
    for candidate, duration in (
        (short_candidate, "72000.000"),
        (long_candidate, "40000.000"),
    ):
        assert [
            claim.value
            for claim in candidate.claims
            if claim.kind is ClaimKind.DURATION
        ] == [duration]

    audio = single_track_audio_facts()

    assert _audio_with_runtime_observations(audio, (short, long_enough)) == audio
    assert _audio_with_runtime_observations(audio, (long_enough, short)) == audio


def test_an_item_with_no_audio_section_records_no_truncation() -> None:
    """An ebook report has no audio facts to append an observation to."""
    claims = audio_local_claims()
    observation = qualified_audiobook(claims, duration="72000.000")

    assert _audio_with_runtime_observations(None, (observation,)) is None
