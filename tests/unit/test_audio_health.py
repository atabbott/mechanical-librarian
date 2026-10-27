"""The closed media-health vocabulary and the derivation that states it.

Every case here is semantic: a report built exactly as preparation builds one,
read through ``derive_audio_health``, and checked for the statement it should
make and the statements it must not. Nothing here opens a file or dispatches a
provider, because the derivation does neither.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    qualify_provider_observation,
    recording_identity_basis,
)
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
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetFacts,
    audio_set_content_digest,
    default_target_fields,
)
from tests.support.audiobook_reports import (
    asserted_asin_report,
    audio_local_claims,
    audio_target_fields,
    audiobook_candidate,
    qualified_audiobook,
)

# The running time `audio_local_claims` states, rounded as the corroboration
# rules round it, and the running time a candidate has to state for the local
# recording to be short of it by more than the tight band.
_LOCAL_SECONDS = 58_244
_LONG_PROVIDER_SECONDS = "72000.000"
_CORROBORATING_PROVIDER_SECONDS = "58200.000"
_LOCAL_ASIN = "B08G9PRS1K"
# Every persisted observation kind that is about the files themselves. The one
# kind left out cites a provider candidate instead, so it has no track to name
# and is proved through the truncation cases.
_TRACK_CITED_KINDS = tuple(
    kind
    for kind in AudioObservationKind
    if kind is not AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER
)


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


def _retained_asin_lane(
    claims: tuple[LocalClaim, ...],
    provider: str,
    duration: str = _LONG_PROVIDER_SECONDS,
) -> ProviderObservation:
    """One per-ASIN answer from one provider, qualified as the adapter does.

    The item's own surfaces name the ASIN, so ADR-0061 lets the answer through
    unshaped and its edition-scope running time survives into the report --
    which is what makes it a candidate the read-time derivation can compare.
    """
    return qualify_provider_observation(
        ProviderObservation(
            provider,
            ProviderStatus.CANDIDATES,
            (),
            (audiobook_candidate(claims, duration=duration, provider=provider),),
            ProviderQueryKind.ASIN,
            "audible_catalog",
        ),
        MetadataLookup(ProviderQueryKind.ASIN, claims),
    )


def _demoted_search_lane(
    claims: tuple[LocalClaim, ...],
    provider: str = "catalogue",
    identity: str = "audible-product-B0DEMOTED1",
    *,
    retain_runtime: bool = True,
) -> ProviderObservation:
    """A demoted answer retains informational runtime without recording authority.

    The item names no ASIN and the runtime band does not corroborate the answer.
    Qualification keeps its duration as informational evidence. The explicit
    absence fixture removes the source duration before qualification, preserving
    a candidate against which a persisted observation can still be read.
    """
    candidate = audiobook_candidate(
        claims,
        identity=identity,
        asin=identity.rsplit("-", 1)[-1],
        duration=_LONG_PROVIDER_SECONDS,
        provider=provider,
    )
    if not retain_runtime:
        candidate = replace(
            candidate,
            claims=tuple(
                claim
                for claim in candidate.claims
                if claim.kind is not ClaimKind.DURATION
            ),
        )
    return qualify_provider_observation(
        ProviderObservation(
            provider,
            ProviderStatus.CANDIDATES,
            (),
            (candidate,),
            ProviderQueryKind.ASIN,
            "audible_catalog",
        ),
        MetadataLookup(
            ProviderQueryKind.ASIN,
            claims,
            work_title="Project Hail Mary",
            work_authors=("Andy Weir",),
        ),
    )


def test_every_health_kind_projects_with_its_citation_and_bound() -> None:
    """The vocabulary is closed, and every kind in it states a rule.

    A kind with no bound is an accusation with no rule behind it, and a kind
    the projection cannot carry is a statement that exists only in the domain.
    The count is asserted so widening the vocabulary is a deliberate edit here.
    """
    assert set(AUDIO_HEALTH_BOUNDS) == set(AudioHealthKind)
    assert len(AudioHealthKind) == 13

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
    # 58,244 s and 72,000 s, read in hours and minutes (#1286).
    assert stated.observed == (
        "16 h 11 min of local audio, 3 h 49 min shorter than the matched edition"
    )
    assert stated.expected == "20 h stated by the matched edition"
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
            # Genuine absence is silent; demotion alone now retains runtime.
            _report(
                audio_local_claims(),
                audio=_single_track(),
                providers=(
                    _demoted_search_lane(audio_local_claims(), retain_runtime=False),
                ),
            ),
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
    assert stated.observed == "2 embedded chapters; chapter 1 runs 1 min 40 s"
    assert stated.expected == (
        "2 chapters stated by the matched edition; chapter 1 runs 8 h 5 min"
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
            "4 embedded chapters; chapter 2 runs 3 min 20 s, aligned past this "
            "recording's first chapter",
            "2 chapters stated by the matched edition; chapter 1 runs 8 h 5 min",
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
        f"{MAX_CHAPTERS} embedded chapters; chapter 1023 runs 9 min 50 s, aligned past "
        "this recording's first chapter"
    )
    assert stated.expected == (
        f"{MAX_PROVIDER_CHAPTER_DURATIONS} chapters stated by the matched edition; "
        "chapter 1022 runs 168 h"
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
        "6 embedded chapters; chapter 4 runs 5 min 50 s, aligned past this "
        "recording's first chapter"
    )
    assert stated.expected == (
        "5 chapters stated by the matched edition; chapter 3 runs 5 min"
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
    """Every track-cited observation reaches the owner with its sequences.

    A gap and a disorder are statements about what each file *declared*, so the
    ordinal alone is not enough: the citation binds each ordinal to the
    sequence the manifest published for it.  The container and stream kinds the
    probe states join them under the same rule.

    ``runtime_short_of_provider`` is the one persisted kind that is not about
    the track set -- it cites a provider candidate rather than a file -- so it
    is proved separately below and excluded here.
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
                for kind in _TRACK_CITED_KINDS
            ),
        ),
    )

    health = derive_audio_health(report)

    assert {value.kind.value for value in health} == {
        kind.value for kind in _TRACK_CITED_KINDS
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


@pytest.mark.parametrize("retain_runtime", (False, True))
def test_a_persisted_truncation_is_stated_once_with_runtime_precedence(
    retain_runtime: bool,
) -> None:
    """Retained informational runtime wins; genuine absence uses the record."""
    claims = audio_local_claims()
    report = _report(
        claims,
        audio=AudioFacts(
            tracks=(AudioTrackDigest(1, "a" * 64, "b" * 64, "m4b", "book.m4b"),),
            observations=(
                AudioObservation(
                    AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER,
                    candidate="audible-product-B0DEMOTED1",
                ),
            ),
        ),
        providers=(_demoted_search_lane(claims, retain_runtime=retain_runtime),),
    )

    health = derive_audio_health(report)

    assert [value.kind for value in health] == [
        AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER
    ]
    stated = health[0]
    assert stated.tracks == ()
    assert stated.cited[-1] == "audible-product-B0DEMOTED1"
    candidate = report.providers[0].candidates[0]
    assert all(
        evidence.kind is not ProviderEvidenceKind.ASIN_MARKETPLACE
        for evidence in candidate.evidence
    )
    assert recording_identity_basis(report) is None
    durations = tuple(
        claim.value for claim in candidate.claims if claim.kind is ClaimKind.DURATION
    )
    if retain_runtime:
        assert durations == (_LONG_PROVIDER_SECONDS,)
        assert stated.observed == (
            "16 h 11 min of local audio, 3 h 49 min shorter than the matched edition"
        )
        assert stated.expected == "20 h stated by the matched edition"
    else:
        assert durations == ()
        assert stated.observed == "16 h 11 min of local audio"
        assert stated.expected is not None and "did not retain" in stated.expected
    assert (
        stated.bound == AUDIO_HEALTH_BOUNDS[AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER]
    )


def test_one_candidate_short_of_this_recording_is_stated_exactly_once() -> None:
    """The precedence rule, written down and proved rather than relied on.

    Retained runtime and a preparation-time record may describe the same
    candidate. The retained evidence wins because it carries both running
    times; the persisted record must not add a duplicate statement.
    """
    report = asserted_asin_report(
        provider_duration=_LONG_PROVIDER_SECONDS, local_asin=_LOCAL_ASIN
    )
    identity = report.providers[0].candidates[0].identity
    doubled = _report(
        report.local_claims,
        audio=AudioFacts(
            tracks=(AudioTrackDigest(1, "a" * 64, "b" * 64, "m4b", "book.m4b"),),
            observations=(
                AudioObservation(
                    AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER,
                    candidate=identity,
                ),
            ),
        ),
        providers=report.providers,
    )

    health = derive_audio_health(doubled)

    assert [value.kind for value in health] == [
        AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER
    ]
    # The derived statement is the one kept: it carries both running times.
    assert health[0].expected == "20 h stated by the matched edition"


def test_a_persisted_truncation_without_a_candidate_states_nothing() -> None:
    """An observation naming no edition is a comparison against nothing."""
    report = _report(
        audio_local_claims(),
        audio=AudioFacts(
            tracks=(AudioTrackDigest(1, "a" * 64, "b" * 64, "m4b", "book.m4b"),),
            observations=(
                AudioObservation(AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER),
            ),
        ),
    )

    assert derive_audio_health(report) == ()


def test_two_providers_answering_about_one_edition_state_it_once() -> None:
    """One edition, one statement, however many providers named it.

    Two audiobook authorities answering about the same product retain the same
    candidate identity, and because the item carries its own ASIN both keep the
    edition-scope running time unshaped. Reading each in turn would state the
    same truncation twice about one edition, which an owner cannot tell apart
    from two separate findings.
    """
    claims = (*audio_local_claims(), _asin_claim(_LOCAL_ASIN))
    providers = tuple(
        sorted(
            (
                _retained_asin_lane(claims, "audnexus"),
                _retained_asin_lane(claims, "catalogue"),
            ),
            key=lambda value: value.provider,
        )
    )
    report = _report(claims, audio=_single_track(), providers=providers)

    assert {
        candidate.identity
        for observation in report.providers
        for candidate in observation.candidates
    } == {"audible-product-B08G9PRS1K"}

    health = derive_audio_health(report)

    assert [value.kind for value in health] == [
        AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER
    ]
    assert health[0].cited[-1] == "audible-product-B08G9PRS1K"


def test_a_retained_running_time_decides_the_edition_a_record_also_names() -> None:
    """Evidence the report still holds outranks a record of evidence it lost.

    Two authorities answered about one edition. One answer survived
    publication with its running time and puts this recording inside the band;
    the other was demoted before publication and left only preparation's
    record behind. Reading the record anyway would tell the owner that the
    edition their report can still be checked against is an hour longer than
    this recording, on the authority of a number the report no longer has.
    """
    claims = (*audio_local_claims(), _asin_claim(_LOCAL_ASIN))
    providers = tuple(
        sorted(
            (
                _retained_asin_lane(claims, "audnexus", f"{_LOCAL_SECONDS}.000"),
                _retained_asin_lane(claims, "catalogue", f"{_LOCAL_SECONDS + 30}.000"),
            ),
            key=lambda value: value.provider,
        )
    )
    report = _report(
        claims,
        audio=AudioFacts(
            tracks=(AudioTrackDigest(1, "a" * 64, "b" * 64, "m4b", "book.m4b"),),
            observations=(
                AudioObservation(
                    AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER,
                    candidate="audible-product-B08G9PRS1K",
                ),
            ),
        ),
        providers=providers,
    )

    assert {
        candidate.identity
        for observation in report.providers
        for candidate in observation.candidates
    } == {"audible-product-B08G9PRS1K"}

    assert derive_audio_health(report) == ()


def test_a_persisted_truncation_states_no_local_runtime_it_does_not_have() -> None:
    """The record survives an item that states no single running time of its own.

    Preparation made the comparison when this recording still stated one
    running time; what the published report holds may be nothing comparable at
    all -- two tracks disagreeing, or a total past the domain's ceiling. The
    statement is still true and is still made, and it says nothing about a
    local running time rather than inventing one.
    """
    claims = audio_local_claims(duration=None)
    report = _report(
        claims,
        audio=AudioFacts(
            tracks=(AudioTrackDigest(1, "a" * 64, "b" * 64, "m4b", "book.m4b"),),
            observations=(
                AudioObservation(
                    AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER,
                    candidate="audible-product-B0DEMOTED1",
                ),
            ),
        ),
        providers=(_demoted_search_lane(claims, retain_runtime=False),),
    )

    health = derive_audio_health(report)

    assert [value.kind for value in health] == [
        AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER
    ]
    assert health[0].cited == ("audible-product-B0DEMOTED1",)
    assert health[0].observed is None
    assert health[0].expected is not None and "did not retain" in health[0].expected


def test_runtime_reads_in_hours_and_minutes_and_states_the_difference() -> None:
    """A running time reads as the owner reads a clock, never as seconds (#1286).

    30,846 s against 31,680 s is 834 s apart, far outside the 90 s band: it
    is stated, and the difference is the one between the two times as shown.
    """
    from mechanical_librarian.domain.audio_health import _hours_minutes, _shown_seconds

    assert _hours_minutes(30_846) == "8 h 34 min"
    assert _hours_minutes(31_680) == "8 h 48 min"
    assert _hours_minutes(_shown_seconds(31_680) - _shown_seconds(30_846)) == "14 min"
    assert _hours_minutes(90) == "1 min 30 s"
    assert _hours_minutes(45) == "45 s"
    assert _hours_minutes(0) == "0 s"
    assert _hours_minutes(7_200) == "2 h"
    assert AUDIO_HEALTH_BOUNDS[AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER] == (
        "A complete recording runs within 1 min 30 s of the edition's stated "
        "running time."
    )

    def stated(provider_seconds: int) -> list[str | None]:
        report = asserted_asin_report(
            provider_duration=f"{provider_seconds}.000", local_asin=_LOCAL_ASIN
        )
        return [value.observed for value in derive_audio_health(report)]

    assert stated(_LOCAL_SECONDS + 834) == [
        "16 h 11 min of local audio, 14 min shorter than the matched edition"
    ]
    # Negative control: a recording within the band is not short of it.
    assert stated(_LOCAL_SECONDS + 90) == []
    for value in stated(_LOCAL_SECONDS + 834):
        assert value is not None and "seconds" not in value
