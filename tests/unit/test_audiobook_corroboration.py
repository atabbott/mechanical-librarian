"""Audiobook structural corroboration: runtime bands, chapter-vector
alignment including brand intro and outro offsets, cover perceptual-hash
distance at work scope, and the closed reason vocabulary each rule states.
"""

from __future__ import annotations

import pytest

from mechanical_librarian.domain import audiobook_corroboration, metadata_report
from mechanical_librarian.domain.audio_inspection import AudioChapter
from mechanical_librarian.domain.audiobook_corroboration import (
    CHAPTER_DURATION_TOLERANCE_SECONDS,
    COVER_PERCEPTUAL_HASH_DISTANCE,
    RUNTIME_BAND_LOOSE_SECONDS,
    RUNTIME_BAND_TIGHT_SECONDS,
    STRONG_CHAPTER_AGREEMENT_MINIMUM,
    CorroborationOutcome,
    CorroborationReason,
    CorroborationSignal,
    CorroborationValueError,
    chapter_vector_agreement,
    cover_hash_agreement,
    local_chapter_durations_seconds,
    local_runtime_seconds,
    runtime_agreement,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonMethod,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderChapterStructure,
    ProviderClaim,
    TargetFacts,
    comparison_links,
    default_target_fields,
)
from tests.support.metadata_report_doubles import single_track_audio_facts


def _structure(
    durations: tuple[int, ...],
    *,
    runtime: int | None = None,
    truncated: bool = False,
    chapter_count: int | None = None,
) -> ProviderChapterStructure:
    return ProviderChapterStructure(
        len(durations) if chapter_count is None else chapter_count,
        sum(durations) if runtime is None else runtime,
        durations,
        truncated,
    )


@pytest.mark.parametrize(
    ("local", "provider", "outcome", "reason"),
    [
        (
            36_000,
            36_000,
            CorroborationOutcome.CORROBORATED,
            CorroborationReason.RUNTIME_WITHIN_TIGHT_BAND,
        ),
        (
            36_000,
            36_000 + RUNTIME_BAND_TIGHT_SECONDS,
            CorroborationOutcome.CORROBORATED,
            CorroborationReason.RUNTIME_WITHIN_TIGHT_BAND,
        ),
        (
            36_000,
            36_000 - RUNTIME_BAND_TIGHT_SECONDS - 1,
            CorroborationOutcome.WEAKLY_CORROBORATED,
            CorroborationReason.RUNTIME_WITHIN_LOOSE_BAND,
        ),
        (
            36_000,
            36_000 + RUNTIME_BAND_LOOSE_SECONDS,
            CorroborationOutcome.WEAKLY_CORROBORATED,
            CorroborationReason.RUNTIME_WITHIN_LOOSE_BAND,
        ),
        (
            36_000,
            36_000 + RUNTIME_BAND_LOOSE_SECONDS + 1,
            CorroborationOutcome.CONTRADICTED,
            CorroborationReason.RUNTIME_OUTSIDE_BANDS,
        ),
        (
            None,
            36_000,
            CorroborationOutcome.NOT_COMPARABLE,
            CorroborationReason.RUNTIME_UNAVAILABLE,
        ),
        (
            36_000,
            None,
            CorroborationOutcome.NOT_COMPARABLE,
            CorroborationReason.RUNTIME_UNAVAILABLE,
        ),
    ],
)
def test_runtime_bands_separate_agreement_from_a_different_recording(
    local: int | None,
    provider: int | None,
    outcome: CorroborationOutcome,
    reason: CorroborationReason,
) -> None:
    """Each band edge is exercised on both sides of its own boundary.

    A provider publishes minutes and a container publishes milliseconds, so
    the tight band is what makes two correct statements of one recording read
    as agreement instead of disagreement.
    """
    signal = runtime_agreement(local, provider)
    assert outcome == signal.outcome
    assert (reason.value,) == signal.reasons


def test_the_runtime_gate_owns_its_band_independently_of_the_report_comparison() -> (
    None
):
    """Two rules happen to share the number 90; neither may move the other.

    The report's duration band decides whether a stated claim links to a local
    one; this module's tight band decides whether a runtime corroborates an
    identity. They are measured separately and retuned separately (ADR-0064),
    so retuning either one leaves the other's verdict exactly as it was.
    """
    assert 90 == RUNTIME_BAND_TIGHT_SECONDS
    assert not hasattr(audiobook_corroboration, "DURATION_AGREEMENT_BAND_SECONDS")

    local_claim = LocalClaim.create(
        kind=ClaimKind.DURATION,
        observed="43200.000",
        canonical="43200.000",
        excerpt="43200.000",
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )
    provider_claim = ProviderClaim.create(
        provider="audnexus",
        candidate="asin-1",
        kind=ClaimKind.DURATION,
        value="43289.000",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
    )

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(metadata_report, "DURATION_AGREEMENT_BAND_SECONDS", 5)
        retuned_report = runtime_agreement(36_000, 36_089)
    assert CorroborationOutcome.CORROBORATED == retuned_report.outcome
    assert (
        CorroborationReason.RUNTIME_WITHIN_TIGHT_BAND.value,
    ) == retuned_report.reasons

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(audiobook_corroboration, "RUNTIME_BAND_TIGHT_SECONDS", 5)
        links = comparison_links(provider_claim, (local_claim,))
    assert 1 == len(links)
    assert Comparison.AGREEMENT == links[0].outcome
    assert ComparisonMethod.DURATION_BAND_AGREEMENT == links[0].method


def test_a_chapter_vector_aligns_within_tolerance_and_diverges_beyond_it() -> None:
    local = (600, 601, 599, 602, 600, 598)
    within = tuple(value + CHAPTER_DURATION_TOLERANCE_SECONDS for value in local)
    signal = chapter_vector_agreement(local, _structure(within))
    assert CorroborationOutcome.CORROBORATED == signal.outcome
    assert (CorroborationReason.CHAPTER_VECTOR_ALIGNED.value,) == signal.reasons
    assert len(local) == signal.compared

    # An interior chapter, so no permitted leading or trailing skip can hide
    # the disagreement by dropping the chapter that carries it.
    beyond = (*within[:3], within[3] + 1, *within[4:])
    diverged = chapter_vector_agreement(local, _structure(beyond))
    assert CorroborationOutcome.CONTRADICTED == diverged.outcome
    assert (CorroborationReason.CHAPTER_VECTOR_DIVERGED.value,) == diverged.reasons
    assert 0 == diverged.compared


def test_a_branded_intro_and_outro_align_as_an_offset() -> None:
    """One publisher's intro and one credit block, and no more than one each.

    A second unmatched chapter at either end stops being a systematic offset
    and becomes a different cut of the recording, which this must not call
    agreement.
    """
    body = (600, 601, 599, 602, 600, 598)
    signal = chapter_vector_agreement(body, _structure((7, *body, 11)))
    assert CorroborationOutcome.CORROBORATED == signal.outcome
    assert (
        CorroborationReason.CHAPTER_VECTOR_ALIGNED_WITH_OFFSET.value,
    ) == signal.reasons
    assert len(body) == signal.compared

    assert (
        CorroborationOutcome.CORROBORATED
        == chapter_vector_agreement((7, *body), _structure((*body, 11))).outcome
    )
    assert (
        CorroborationOutcome.CONTRADICTED
        == chapter_vector_agreement(body, _structure((7, 9, *body, 11))).outcome
    )


def test_a_short_alignment_is_weak_rather_than_strong() -> None:
    """Below five compared chapters the measured collision rate is 2.59%.

    The alignment is still real, so the rule states it and marks it weak
    rather than discarding a signal that is merely not decisive.
    """
    short = tuple(600 + index for index in range(STRONG_CHAPTER_AGREEMENT_MINIMUM - 1))
    signal = chapter_vector_agreement(short, _structure(short))
    assert CorroborationOutcome.WEAKLY_CORROBORATED == signal.outcome
    assert (
        CorroborationReason.CHAPTER_VECTOR_ALIGNED.value,
        CorroborationReason.CHAPTER_VECTOR_BELOW_STRONG_MINIMUM.value,
    ) == signal.reasons
    assert len(short) == signal.compared

    exact = tuple(600 + index for index in range(STRONG_CHAPTER_AGREEMENT_MINIMUM))
    assert (
        CorroborationOutcome.CORROBORATED
        == chapter_vector_agreement(exact, _structure(exact)).outcome
    )


@pytest.mark.parametrize(
    ("local", "provider", "reason"),
    [
        ((), None, CorroborationReason.CHAPTER_VECTOR_UNAVAILABLE),
        ((600, 600), None, CorroborationReason.CHAPTER_VECTOR_UNAVAILABLE),
        (
            (600, 600),
            _structure((), runtime=1_200, chapter_count=2),
            CorroborationReason.CHAPTER_VECTOR_UNAVAILABLE,
        ),
        (
            (600, 600),
            _structure(
                (600,) * 1_024,
                runtime=604_800,
                chapter_count=1_100,
                truncated=True,
            ),
            CorroborationReason.CHAPTER_VECTOR_INCOMPLETE,
        ),
    ],
)
def test_an_absent_or_partial_vector_is_not_comparable(
    local: tuple[int, ...],
    provider: ProviderChapterStructure | None,
    reason: CorroborationReason,
) -> None:
    signal = chapter_vector_agreement(local, provider)
    assert CorroborationOutcome.NOT_COMPARABLE == signal.outcome
    assert (reason.value,) == signal.reasons


def test_a_locally_truncated_chapter_table_is_not_comparable() -> None:
    local = (600, 601, 599, 602, 600, 598)
    signal = chapter_vector_agreement(local, _structure(local), local_truncated=True)
    assert CorroborationOutcome.NOT_COMPARABLE == signal.outcome
    assert (CorroborationReason.CHAPTER_VECTOR_INCOMPLETE.value,) == signal.reasons


def test_cover_hashes_corroborate_a_work_and_never_an_edition() -> None:
    """An audiobook's art routinely differs from the print edition's.

    So the rule answers about the work and declines about the edition, rather
    than reporting a contradiction that is only a different cover.
    """
    left = "f" * 16
    near = "f" * 15 + "0"
    far = "0" * 16
    within = cover_hash_agreement(left, near, ClaimScope.WORK)
    assert CorroborationOutcome.CORROBORATED == within.outcome
    assert (CorroborationReason.COVER_HASH_WITHIN_DISTANCE.value,) == within.reasons
    outside = cover_hash_agreement(left, far, ClaimScope.WORK)
    assert CorroborationOutcome.CONTRADICTED == outside.outcome
    assert (CorroborationReason.COVER_HASH_OUTSIDE_DISTANCE.value,) == outside.reasons
    edition = cover_hash_agreement(left, left, ClaimScope.EDITION)
    assert CorroborationOutcome.NOT_COMPARABLE == edition.outcome
    assert (CorroborationReason.COVER_HASH_SCOPE_INELIGIBLE.value,) == edition.reasons


def test_the_cover_distance_threshold_is_the_boundary_it_names() -> None:
    left = "0" * 16
    at_threshold = f"{(1 << COVER_PERCEPTUAL_HASH_DISTANCE) - 1:016x}"
    beyond = f"{(1 << (COVER_PERCEPTUAL_HASH_DISTANCE + 1)) - 1:016x}"
    assert (
        CorroborationOutcome.CORROBORATED
        == cover_hash_agreement(left, at_threshold, ClaimScope.WORK).outcome
    )
    assert (
        CorroborationOutcome.CONTRADICTED
        == cover_hash_agreement(left, beyond, ClaimScope.WORK).outcome
    )


@pytest.mark.parametrize(
    "provider_hash", [None, "", "f" * 15, "f" * 17, "g" * 16, "0X" + "0" * 14]
)
def test_a_hash_outside_its_shape_is_unavailable_rather_than_a_contradiction(
    provider_hash: str | None,
) -> None:
    signal = cover_hash_agreement("f" * 16, provider_hash, ClaimScope.WORK)
    assert CorroborationOutcome.NOT_COMPARABLE == signal.outcome
    assert (CorroborationReason.COVER_HASH_UNAVAILABLE.value,) == signal.reasons


def test_local_facts_project_onto_whole_seconds() -> None:
    audio = single_track_audio_facts(
        chapters=(
            AudioChapter(1, 0, 600_499),
            AudioChapter(2, 600_499, 1_201_000),
        )
    )
    assert (600, 601) == local_chapter_durations_seconds(audio)
    assert () == local_chapter_durations_seconds(None)


def test_one_agreed_local_duration_is_a_runtime_and_two_are_not() -> None:
    """Two surfaces disagreeing leave no local runtime, rather than a guess."""
    assert 3_600 == local_runtime_seconds(_audio_report(("3600.000",)))
    assert 3_600 == local_runtime_seconds(_audio_report(("3600", "3600.000")))
    assert local_runtime_seconds(_audio_report(("3600.000", "3599.000"))) is None
    assert local_runtime_seconds(_audio_report(())) is None


def test_a_signal_refuses_a_reason_set_its_outcome_cannot_state() -> None:
    with pytest.raises(CorroborationValueError):
        CorroborationSignal(
            CorroborationOutcome.CORROBORATED,
            (CorroborationReason.RUNTIME_OUTSIDE_BANDS.value,),
        )
    with pytest.raises(CorroborationValueError):
        CorroborationSignal(CorroborationOutcome.CORROBORATED, ("invented_reason",))
    with pytest.raises(CorroborationValueError):
        CorroborationSignal(
            CorroborationOutcome.WEAKLY_CORROBORATED,
            (
                CorroborationReason.CHAPTER_VECTOR_BELOW_STRONG_MINIMUM.value,
                CorroborationReason.CHAPTER_VECTOR_ALIGNED.value,
            ),
        )


def test_the_rules_refuse_an_input_outside_their_types() -> None:
    with pytest.raises(CorroborationValueError):
        chapter_vector_agreement(("600",), None)  # type: ignore[arg-type]
    with pytest.raises(CorroborationValueError):
        cover_hash_agreement("f" * 16, "f" * 16, "work")  # type: ignore[arg-type]
    with pytest.raises(CorroborationValueError):
        local_runtime_seconds(None)  # type: ignore[arg-type]
    with pytest.raises(CorroborationValueError):
        local_chapter_durations_seconds("audio")  # type: ignore[arg-type]


def _audio_report(durations: tuple[str, ...]) -> MetadataReport:
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.DURATION,
            observed=value,
            canonical=value,
            excerpt=value,
            applicability="not_applicable",
            basis="embedded_tag",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            state=FieldState.VALID,
            occurrence=occurrence,
        )
        for occurrence, value in enumerate(durations, 1)
    )
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "audiobooks", "item-1", default_target_fields("The Recording")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("no_local_audio_claims",),
        providers=(),
        audio=single_track_audio_facts(),
    )
