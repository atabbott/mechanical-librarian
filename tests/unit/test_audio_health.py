"""The closed media-health vocabulary and the derivation that states it.

Every case here is semantic: a report built exactly as preparation builds one,
read through ``derive_audio_health``, and checked for the statement it should
make and the statements it must not. Nothing here opens a file or dispatches a
provider, because the derivation does neither.
"""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.audio_health import (
    AUDIO_HEALTH_BOUNDS,
    AudioHealthError,
    AudioHealthKind,
    AudioHealthObservation,
    derive_audio_health,
    media_unreadable_observation,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_CHAPTERS,
    AudioChapter,
    AudioChapterComposition,
    AudioInspectionField,
    AudioObservation,
    AudioObservationKind,
    AudioTrackDigest,
)
from mechanical_librarian.domain.evidence_bounds import MAX_SHORT_TEXT_LENGTH
from mechanical_librarian.domain.metadata_report import (
    MAX_PROVIDER_CHAPTER_DURATIONS,
    AudioFacts,
    ClaimKind,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderChapterStructure,
    TargetFacts,
    audio_set_content_digest,
    default_target_fields,
)
from tests.support.audiobook_reports import (
    asserted_asin_report,
    audio_local_claims,
    audio_target_fields,
    qualified_audiobook,
)

# The running time `audio_local_claims` states, rounded as the corroboration
# rules round it, and the running time a candidate has to state for the local
# recording to be short of it by more than the tight band.
_LOCAL_SECONDS = 58_244
_LONG_PROVIDER_SECONDS = "72000.000"
_CORROBORATING_PROVIDER_SECONDS = "58200.000"
_LOCAL_ASIN = "B08G9PRS1K"


def _asin_claim(value: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        basis="embedded_tag",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=3,
    )


def _report(
    local_claims: tuple[LocalClaim, ...],
    *,
    audio: AudioFacts,
    content_digest: str = "b" * 64,
    providers: tuple[object, ...] = (),
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", audio_target_fields()),
        file=FileFacts(0, "a" * 64, content_digest, "m4b"),
        local_claims=local_claims,
        local_reasons=tuple(
            sorted({f"{claim.basis}_{claim.kind.value}" for claim in local_claims})
        ),
        providers=providers,  # type: ignore[arg-type]
        audio=audio,
    )


def _single_track(
    *,
    chapters: tuple[AudioChapter, ...] = (),
    chapters_truncated: bool = False,
) -> AudioFacts:
    return AudioFacts(
        chapters=chapters,
        chapters_truncated=chapters_truncated,
        tracks=(AudioTrackDigest(1, "a" * 64, "b" * 64, "m4b", "book.m4b"),),
    )


def _corroborated_report(
    *,
    chapters: tuple[AudioChapter, ...] = (),
    chapters_truncated: bool = False,
    provider_chapters: ProviderChapterStructure | None = None,
) -> MetadataReport:
    """One recording whose identity the runtime band settled, with its chapters.

    Chapter structure survives publication only on a corroborated candidate, so
    this is the only shape the chapter class can ever be stated on.
    """
    claims = audio_local_claims()
    return _report(
        claims,
        audio=_single_track(chapters=chapters, chapters_truncated=chapters_truncated),
        providers=(qualified_audiobook(claims, chapters=provider_chapters),),
    )


def _running_chapters(durations_seconds: tuple[int, ...]) -> tuple[AudioChapter, ...]:
    """One embedded chapter table with exactly these durations, back to back."""
    chapters: list[AudioChapter] = []
    start = 0
    for ordinal, seconds in enumerate(durations_seconds, 1):
        chapters.append(AudioChapter(ordinal, start, start + seconds * 1_000))
        start += seconds * 1_000
    return tuple(chapters)


def test_every_health_kind_projects_with_its_citation_and_bound() -> None:
    """The vocabulary is closed, and every kind in it states a rule.

    A kind with no bound is an accusation with no rule behind it, and a kind
    the projection cannot carry is a statement that exists only in the domain.
    The count is asserted so widening the vocabulary is a deliberate edit here.
    """
    assert set(AUDIO_HEALTH_BOUNDS) == set(AudioHealthKind)
    assert len(AudioHealthKind) == 9

    projected = {
        kind: AudioHealthObservation(kind, ("cited evidence",), (1,)).to_data()
        for kind in AudioHealthKind
    }

    assert {str(value["kind"]) for value in projected.values()} == {
        kind.value for kind in AudioHealthKind
    }
    for kind, data in projected.items():
        assert set(data) == {
            "bound",
            "cited",
            "expected",
            "kind",
            "observed",
            "tracks",
        }
        assert data["bound"] == AUDIO_HEALTH_BOUNDS[kind]
        assert data["cited"] == ["cited evidence"]
        assert data["tracks"] == [1]
    # An observation citing nothing cannot be checked against the report, so
    # the shape refuses it rather than projecting an unsourced claim.
    with pytest.raises(AudioHealthError):
        AudioHealthObservation(AudioHealthKind.MEDIA_UNREADABLE, ())


def test_an_unreadable_track_is_named_by_its_ordinal() -> None:
    """The terminal class cites the one file an owner has to look at."""
    named = media_unreadable_observation(4)
    whole = media_unreadable_observation(None)

    assert (named.kind, named.tracks, named.cited) == (
        AudioHealthKind.MEDIA_UNREADABLE,
        (4,),
        ("track 4",),
    )
    assert (whole.tracks, whole.cited) == ((), ("this item's audio files",))
    with pytest.raises(AudioHealthError):
        media_unreadable_observation(0)


def test_a_short_recording_reads_short_of_its_provider_runtime() -> None:
    """The class the slice exists for, on the lane that retains a runtime.

    The item carries its own ASIN, so the per-ASIN answer passes through
    unshaped and its edition-scope running time survives into the report. That
    is the evidence the derivation compares, and the local claim references and
    the candidate identity are cited so an owner can check the comparison.
    """
    report = asserted_asin_report(
        provider_duration=_LONG_PROVIDER_SECONDS, local_asin=_LOCAL_ASIN
    )

    health = derive_audio_health(report)

    assert [value.kind for value in health] == [
        AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER
    ]
    stated = health[0]
    assert stated.tracks == ()
    assert stated.observed == f"{_LOCAL_SECONDS} seconds of local audio"
    assert stated.expected == "72000 seconds stated by the matched edition"
    assert stated.cited[-1] == "audible-product-B08G9PRS1K"
    assert stated.cited[:-1] and all(
        value.startswith("local_") for value in stated.cited[:-1]
    )
    assert (
        stated.bound == AUDIO_HEALTH_BOUNDS[AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER]
    )


@pytest.mark.parametrize(
    "report",
    (
        pytest.param(
            asserted_asin_report(
                provider_duration=_CORROBORATING_PROVIDER_SECONDS,
                local_asin=_LOCAL_ASIN,
            ),
            id="within-the-tight-band",
        ),
        pytest.param(
            # No local ASIN, so ADR-0061 demotes the asserted lane's candidate
            # to work scope and its running time never reaches the report. The
            # silence is the documented limit of a read-time derivation, not a
            # statement that the recording is complete.
            asserted_asin_report(provider_duration=_LONG_PROVIDER_SECONDS),
            id="no-retained-provider-runtime",
        ),
    ),
)
def test_a_recording_within_band_states_nothing(report: MetadataReport) -> None:
    assert derive_audio_health(report) == ()


def test_a_recording_without_its_own_runtime_states_nothing() -> None:
    """One side of a comparison is not a comparison."""
    claims = (*audio_local_claims(duration=None), _asin_claim(_LOCAL_ASIN))

    assert derive_audio_health(_report(claims, audio=_single_track())) == ()


def test_a_diverged_chapter_vector_is_a_mismatch() -> None:
    """Cuts that do not align inside the tolerance are a different recording.

    The same candidate whose aligned vector states nothing is the control: the
    class fires on the comparison, not on the presence of chapters.
    """
    diverged = _corroborated_report(
        chapters=(
            AudioChapter(1, 0, 100_000, "One"),
            AudioChapter(2, 100_000, 300_000, "Two"),
        )
    )
    aligned = _corroborated_report(
        chapters=(
            AudioChapter(1, 0, 29_100_000, "One"),
            AudioChapter(2, 29_100_000, 58_200_000, "Two"),
        )
    )

    health = derive_audio_health(diverged)

    assert [value.kind for value in health] == [
        AudioHealthKind.CHAPTER_STRUCTURE_MISMATCH
    ]
    stated = health[0]
    assert stated.cited == (
        AudioChapterComposition.SINGLE_FILE.value,
        "audible-product-B08G9PRS1K",
    )
    # Two counts are not a discrepancy: what the owner is told is the first cut
    # the two recordings disagree on and how long it runs on each side.
    assert stated.observed == "2 embedded chapters; chapter 1 runs 100 s"
    assert stated.expected == (
        "2 chapters stated by the matched edition; chapter 1 runs 29100 s"
    )
    assert derive_audio_health(aligned) == ()


@pytest.mark.parametrize(
    ("local_seconds", "observed", "expected"),
    (
        pytest.param(
            # No end-skip makes these two comparable at all, so no cut was ever
            # compared and the counts are the whole of what can be said.
            (29_100, 29_100, 1_000, 2_000, 3_000, 4_000),
            "6 embedded chapters",
            "2 chapters stated by the matched edition",
            id="counts-alone",
        ),
        pytest.param(
            # The only alignment the skip grid admits drops this recording's
            # first and last chapters, so the cut is named under that alignment
            # and the statement says which end was skipped.
            (100, 200, 300, 400),
            "4 embedded chapters; chapter 2 runs 200 s, aligned past this "
            "recording's first chapter",
            "2 chapters stated by the matched edition; chapter 1 runs 29100 s",
            id="counts-and-a-cut",
        ),
    ),
)
def test_a_differing_chapter_count_states_both_counts(
    local_seconds: tuple[int, ...], observed: str, expected: str
) -> None:
    """Vectors of different lengths still name the cut that diverged, if one did.

    A recording whose cuts run out early diverges on count alone and there is
    no cut to point at; one that diverges inside the shared prefix is named
    there, because the count is the weaker of the two facts.
    """
    report = _corroborated_report(chapters=_running_chapters(local_seconds))

    health = derive_audio_health(report)

    assert [value.kind for value in health] == [
        AudioHealthKind.CHAPTER_STRUCTURE_MISMATCH
    ]
    assert (health[0].observed, health[0].expected) == (observed, expected)


def test_a_chapter_discrepancy_stays_within_the_projection_bound() -> None:
    """The widest vectors either side admits still state a projectable string.

    Both bounds are at their maximum here -- `MAX_CHAPTERS` embedded chapters
    against `MAX_PROVIDER_CHAPTER_DURATIONS` stated ones -- with a skip in
    play, four-digit ordinals on both sides and the longest duration the
    vector admits on the stated side, which is the widest this sentence gets.
    The observation would refuse a value past `MAX_SHORT_TEXT_LENGTH`, so
    deriving it at all is half the proof and the lengths are asserted for the
    other half.
    """
    body = tuple(590 for _ in range(MAX_CHAPTERS - 1))
    stated_body = tuple(
        604_800 if position == MAX_CHAPTERS - 3 else 590
        for position in range(MAX_CHAPTERS - 1)
    )
    report = _corroborated_report(
        # A branded intro this recording carries and the edition does not, so
        # the alignment skips it and every ordinal after it is offset by one.
        chapters=_running_chapters((7, *body)),
        provider_chapters=ProviderChapterStructure(
            MAX_PROVIDER_CHAPTER_DURATIONS, 604_800, (*stated_body, 11), False
        ),
    )

    health = derive_audio_health(report)

    assert [value.kind for value in health] == [
        AudioHealthKind.CHAPTER_STRUCTURE_MISMATCH
    ]
    stated = health[0]
    assert stated.observed == (
        f"{MAX_CHAPTERS} embedded chapters; chapter 1023 runs 590 s, aligned past "
        "this recording's first chapter"
    )
    assert stated.expected == (
        f"{MAX_PROVIDER_CHAPTER_DURATIONS} chapters stated by the matched edition; "
        "chapter 1022 runs 604800 s"
    )
    assert max(len(stated.observed), len(stated.expected)) <= MAX_SHORT_TEXT_LENGTH


def test_a_diverging_cut_is_named_under_the_alignment_the_gate_would_choose() -> None:
    """A branded intro shifts the ordinals; the cut named is the one compared.

    The agreement rule tolerates one skipped chapter at each end, so a
    recording whose first chapter the edition does not have is compared from
    its second. Naming the raw first pair instead would accuse chapter one of
    a discrepancy that the rule never looked at.
    """
    stated_seconds = (100, 200, 300, 400, 500)
    report = _corroborated_report(
        chapters=_running_chapters((10, 100, 200, 350, 400, 500)),
        provider_chapters=ProviderChapterStructure(
            len(stated_seconds), sum(stated_seconds), stated_seconds, False
        ),
    )

    health = derive_audio_health(report)

    assert [value.kind for value in health] == [
        AudioHealthKind.CHAPTER_STRUCTURE_MISMATCH
    ]
    stated = health[0]
    assert stated.observed == (
        "6 embedded chapters; chapter 4 runs 350 s, aligned past this "
        "recording's first chapter"
    )
    assert stated.expected == (
        "5 chapters stated by the matched edition; chapter 3 runs 300 s"
    )


def test_an_incomplete_vector_states_nothing() -> None:
    """A partial cut proves nothing about the rest of it."""
    truncated = _corroborated_report(
        chapters=(
            AudioChapter(1, 0, 100_000, "One"),
            AudioChapter(2, 100_000, 300_000, "Two"),
        ),
        chapters_truncated=True,
    )
    absent = _corroborated_report()

    assert derive_audio_health(truncated) == ()
    assert derive_audio_health(absent) == ()


def test_retained_set_observations_project_with_their_tracks() -> None:
    """All six composed observations reach the owner, with declared sequences.

    A gap and a disorder are statements about what each file *declared*, so the
    ordinal alone is not enough: the citation binds each ordinal to the
    sequence the manifest published for it.
    """
    digests = (f"{1:064x}", f"{2:064x}")
    claims = audio_local_claims()
    report = _report(
        claims,
        content_digest=audio_set_content_digest(digests),
        audio=AudioFacts(
            composition=AudioChapterComposition.PER_TRACK,
            tracks=(
                AudioTrackDigest(1, "a" * 64, digests[0], "mp3", "01.mp3", sequence=1),
                AudioTrackDigest(2, "b" * 64, digests[1], "mp3", "02.mp3", sequence=3),
            ),
            observations=tuple(
                AudioObservation(
                    kind,
                    (1, 2),
                    (
                        AudioInspectionField.ASIN
                        if kind is AudioObservationKind.ASIN_DISAGREES_ACROSS_TRACKS
                        else None
                    ),
                )
                for kind in AudioObservationKind
            ),
        ),
    )

    health = derive_audio_health(report)

    assert {value.kind.value for value in health} == {
        kind.value for kind in AudioObservationKind
    }
    assert all(value.tracks == (1, 2) for value in health)
    gap = next(
        value for value in health if value.kind is AudioHealthKind.TRACK_SEQUENCE_GAP
    )
    assert gap.cited == ("track 1 declares sequence 1", "track 2 declares sequence 3")
    asin = next(
        value
        for value in health
        if value.kind is AudioHealthKind.ASIN_DISAGREES_ACROSS_TRACKS
    )
    assert asin.cited[0] == AudioInspectionField.ASIN.value


def test_an_ebook_report_states_no_media_health() -> None:
    """Nothing derives a media statement from an item with no audio at all."""
    report = MetadataReport.create(
        target=TargetFacts(
            "primary", "ebooks", "item-2", default_target_fields("Recorded work")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "epub"),
        local_claims=(),
        local_reasons=("no_local_claims",),
        providers=(),
    )

    assert derive_audio_health(report) == ()
    with pytest.raises(AudioHealthError):
        derive_audio_health("not a report")  # type: ignore[arg-type]
