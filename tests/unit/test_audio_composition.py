"""The rules that turn one item's per-track inspections into one evidence set.

These run against constructed ``AudioInspection`` values and no probe binary:
composition is pure arithmetic over what each isolated child returned, and it
is where an item's one running time, one chapter vector, and per-file claim
provenance are decided.
"""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.audio_composition import (
    InspectedTrack,
    attach_claim_references,
    compose_audio_inspections,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_CHAPTERS,
    MAX_DURATION_MILLISECONDS,
    AudioChapter,
    AudioChapterComposition,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioInspection,
    AudioInspectionFact,
    AudioInspectionField,
    AudioInspectionOutcome,
    AudioObservationKind,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion


def _source(ordinal: int) -> Fingerprint:
    return Fingerprint("sha256", SchemaVersion("local-file-v1"), f"{ordinal:064x}")


def _fact(
    ordinal: int,
    field: AudioInspectionField,
    observed: str,
    *,
    surface: AudioEvidenceSurface = AudioEvidenceSurface.ID3_USER_TEXT,
    canonical: str | None = None,
    role: str | None = None,
) -> AudioInspectionFact:
    return AudioInspectionFact(
        field,
        _source(ordinal),
        observed,
        observed if canonical is None else canonical,
        observed,
        surface,
        "valid",
        role,
    )


def _duration(ordinal: int, milliseconds: int) -> AudioInspectionFact:
    observed = f"{milliseconds // 1000}.{milliseconds % 1000:03d}"
    return _fact(
        ordinal,
        AudioInspectionField.DURATION,
        observed,
        surface=AudioEvidenceSurface.CONTAINER_FORMAT,
    )


def _track(
    ordinal: int,
    facts: tuple[AudioInspectionFact, ...] = (),
    *,
    chapters: tuple[AudioChapter, ...] = (),
    cover: bool = False,
    title: str | None = None,
    sequence: int | None = None,
    name: str | None = None,
) -> InspectedTrack:
    return InspectedTrack(
        ordinal,
        f"{ordinal:064x}",
        f"{ordinal + 100:064x}",
        "mp3",
        name or f"{ordinal:02d}.mp3",
        AudioInspection(
            AudioInspectionOutcome.SUCCESS,
            _source(ordinal),
            facts,
            chapters,
            AudioEmbeddedCover(True, AudioEvidenceSurface.CONTAINER_STREAM)
            if cover
            else AudioEmbeddedCover(False),
            False,
            title,
        ),
        sequence,
    )


def test_the_item_states_one_running_time_and_the_tracks_keep_their_own() -> None:
    """Per-track durations are manifest facts, never claims.

    The runtime-corroboration gate elects a single agreed local value, so a
    claim per file would leave it nothing to elect and would corroborate no
    edition at all.
    """
    composed = compose_audio_inspections(
        tuple(
            _track(ordinal, (_duration(ordinal, 60_000 * ordinal),))
            for ordinal in (1, 2, 3)
        )
    )

    durations = [
        fact for fact in composed.facts if fact.field is AudioInspectionField.DURATION
    ]
    assert [value.canonical_value for value in durations] == ["360.000"]
    assert durations[0].surface is AudioEvidenceSurface.CONTAINER_FORMAT
    assert [value.duration_milliseconds for value in composed.tracks] == [
        60_000,
        120_000,
        180_000,
    ]


def test_a_single_file_item_passes_its_own_duration_fact_through_unchanged() -> None:
    """A one-track item is the one-track case, not a recomposed one.

    ffprobe's own spelling of the duration is what the report has always
    retained, and re-deriving it would restate every existing single-file
    report as different evidence.
    """
    original = _fact(
        1,
        AudioInspectionField.DURATION,
        "43200.000000",
        surface=AudioEvidenceSurface.CONTAINER_FORMAT,
        canonical="43200.000",
    )

    composed = compose_audio_inspections((_track(1, (original,)),))

    assert composed.facts == (original,)
    assert composed.composition is AudioChapterComposition.SINGLE_FILE
    assert len(composed.tracks) == 1


def test_a_track_without_a_duration_leaves_the_item_with_no_running_time() -> None:
    """A partial sum is a shorter recording than the one on the mount."""
    composed = compose_audio_inspections(
        (
            _track(1, (_duration(1, 60_000),)),
            _track(2, ()),
        )
    )

    assert all(
        fact.field is not AudioInspectionField.DURATION for fact in composed.facts
    )
    assert [
        (value.kind, value.tracks)
        for value in composed.observations
        if value.kind is AudioObservationKind.TRACK_DURATION_UNAVAILABLE
    ] == [(AudioObservationKind.TRACK_DURATION_UNAVAILABLE, (2,))]


def test_a_missing_duration_leaves_the_item_with_no_chapter_vector_either() -> None:
    """Every chapter offset is a sum of the durations before it.

    Counting a track of unknown length as zero seconds placed the tracks
    after it on top of it, so the published vector overlapped itself and, in
    the per-track shape, stated chapters of no length at all. Nothing is
    published instead, and the observations still record that atoms existed.
    """
    composed = compose_audio_inspections(
        (
            _track(1, (), chapters=(AudioChapter(1, 0, 4_000, "One"),)),
            _track(
                2,
                (_duration(2, 10_000),),
                chapters=(AudioChapter(1, 0, 9_000, "Two"),),
            ),
        )
    )

    assert composed.chapters == ()
    assert composed.chapters_truncated is False
    assert composed.composition is AudioChapterComposition.PER_TRACK
    assert [(value.kind, value.tracks) for value in composed.observations] == [
        (AudioObservationKind.TRACK_DURATION_UNAVAILABLE, (1,)),
        (AudioObservationKind.CHAPTER_ATOMS_PARTIAL, (1, 2)),
    ]


def test_a_chapter_ending_past_the_duration_ceiling_publishes_no_vector() -> None:
    """A position past seven days is a position in no recording.

    The summed running time is already withheld at that ceiling; a chapter
    vector built on the same sum would have raised out of the domain and
    reached the owner as an internal failure instead of as absent evidence.
    """
    milliseconds = 2 * MAX_DURATION_MILLISECONDS // 3
    composed = compose_audio_inspections(
        tuple(
            _track(
                ordinal,
                (_duration(ordinal, milliseconds),),
                chapters=(AudioChapter(1, 0, milliseconds, f"Part {ordinal}"),),
            )
            for ordinal in (1, 2)
        )
    )

    assert composed.chapters == ()
    assert composed.chapters_truncated is False
    assert composed.composition is AudioChapterComposition.PER_TRACK
    assert all(
        fact.field is not AudioInspectionField.DURATION for fact in composed.facts
    )
    # Every track stated a running time, so there is no track the closed
    # `track_duration_unavailable` vocabulary could name.  The absent claim and
    # the absent vector are what state the overflow.
    assert [(value.kind, value.tracks) for value in composed.observations] == [
        (AudioObservationKind.CHAPTER_ATOMS_PARTIAL, (1, 2)),
    ]


def test_an_atom_past_its_own_track_composes_as_though_it_had_none() -> None:
    """An atom offset past its own file would land inside the next track.

    What a container says about itself is bounded by itself: within one file
    an over-long atom is that file's own claim, but concatenated into an item
    it becomes a chapter overlapping its neighbour.  So the track composes as
    carrying no atoms and the item takes the per-track shape the mixed rule
    already dictates, while the observation still names every track that had
    atoms to drop.
    """
    composed = compose_audio_inspections(
        (
            _track(
                1,
                (_duration(1, 10_000),),
                chapters=(AudioChapter(1, 0, 12_000, "Over"),),
            ),
            _track(
                2,
                (_duration(2, 10_000),),
                chapters=(AudioChapter(1, 0, 9_000, "Two"),),
            ),
        )
    )

    assert composed.composition is AudioChapterComposition.PER_TRACK
    assert [
        (value.start_milliseconds, value.end_milliseconds)
        for value in composed.chapters
    ] == [(0, 10_000), (10_000, 20_000)]
    assert [(value.kind, value.tracks) for value in composed.observations] == [
        (AudioObservationKind.CHAPTER_ATOMS_PARTIAL, (1, 2)),
    ]


def test_embedded_atoms_on_every_track_concatenate_with_cumulative_offsets() -> None:
    composed = compose_audio_inspections(
        (
            _track(
                1,
                (_duration(1, 10_000),),
                chapters=(AudioChapter(1, 0, 4_000, "One"),),
            ),
            _track(
                2,
                (_duration(2, 10_000),),
                chapters=(
                    AudioChapter(1, 0, 5_000, "Two"),
                    AudioChapter(2, 5_000, 9_000, "Three"),
                ),
            ),
        )
    )

    assert composed.composition is AudioChapterComposition.EMBEDDED_CONCATENATED
    assert [
        (value.ordinal, value.start_milliseconds, value.end_milliseconds, value.title)
        for value in composed.chapters
    ] == [
        (1, 0, 4_000, "One"),
        (2, 10_000, 15_000, "Two"),
        (3, 15_000, 19_000, "Three"),
    ]


def test_atoms_on_only_some_tracks_become_one_chapter_per_track() -> None:
    """A mixed vector would state a chapter structure the recording lacks."""
    composed = compose_audio_inspections(
        (
            _track(
                1,
                (_duration(1, 10_000),),
                chapters=(AudioChapter(1, 0, 4_000, "One"),),
            ),
            _track(2, (_duration(2, 10_000),), title="Recorded part two"),
        )
    )

    assert composed.composition is AudioChapterComposition.PER_TRACK
    assert [
        (value.start_milliseconds, value.end_milliseconds, value.title)
        for value in composed.chapters
    ] == [(0, 10_000, "01"), (10_000, 20_000, "Recorded part two")]
    assert AudioObservationKind.CHAPTER_ATOMS_PARTIAL in {
        value.kind for value in composed.observations
    }


def test_a_per_track_chapter_falls_back_to_the_exported_file_stem() -> None:
    composed = compose_audio_inspections(
        (
            _track(1, (_duration(1, 1_000),), name="Chapter One.mp3"),
            _track(2, (_duration(2, 1_000),), name="Chapter Two.mp3"),
        )
    )

    assert [value.title for value in composed.chapters] == [
        "Chapter One",
        "Chapter Two",
    ]


def test_a_chapter_vector_past_the_ceiling_is_cut_and_says_so() -> None:
    composed = compose_audio_inspections(
        tuple(
            _track(
                ordinal,
                (_duration(ordinal, 5_000),),
                chapters=tuple(
                    AudioChapter(position, position * 1_000, position * 1_000 + 500)
                    for position in range(1, 5)
                ),
            )
            for ordinal in range(1, 300)
        )
    )

    assert len(composed.chapters) == MAX_CHAPTERS
    assert composed.chapters_truncated is True


def test_identical_tags_collapse_to_one_claim_under_every_track_that_held_it() -> None:
    tracks = tuple(
        _track(
            ordinal,
            (
                _duration(ordinal, 1_000),
                _fact(
                    ordinal,
                    AudioInspectionField.CONTRIBUTOR,
                    "A Narrator",
                    role="narrator",
                ),
            ),
        )
        for ordinal in (1, 2, 3)
    )

    composed = compose_audio_inspections(tracks)

    narrators = [
        (fact, ordinals)
        for fact, ordinals in zip(composed.facts, composed.provenance, strict=True)
        if fact.field is AudioInspectionField.CONTRIBUTOR
    ]
    assert len(narrators) == 1
    assert narrators[0][1] == (1, 2, 3)
    manifest = attach_claim_references(
        composed, tuple(f"local_{index:064x}" for index in range(len(composed.facts)))
    )
    assert all(f"local_{1:064x}" in value.claims for value in manifest)


def test_tracks_that_disagree_each_keep_their_own_claim_and_are_observed() -> None:
    """Nothing here elects between two tracks: both are retained evidence.

    Electing locally would decide with a tag what only the provider path can
    decide, and dropping one would hide that the files disagree at all.
    """
    tracks = (
        _track(
            1,
            (
                _duration(1, 1_000),
                _fact(1, AudioInspectionField.ASIN, "B0CW1KL2QD"),
            ),
        ),
        _track(
            2,
            (
                _duration(2, 1_000),
                _fact(2, AudioInspectionField.ASIN, "B0DIFFEREN"),
            ),
        ),
    )

    composed = compose_audio_inspections(tracks)

    assert sorted(
        str(fact.canonical_value)
        for fact in composed.facts
        if fact.field is AudioInspectionField.ASIN
    ) == ["B0CW1KL2QD", "B0DIFFEREN"]
    kinds = {value.kind for value in composed.observations}
    assert AudioObservationKind.TAG_DISAGREES_ACROSS_TRACKS in kinds
    assert [
        (value.kind, value.tracks)
        for value in composed.observations
        if value.kind is AudioObservationKind.ASIN_DISAGREES_ACROSS_TRACKS
    ] == [(AudioObservationKind.ASIN_DISAGREES_ACROSS_TRACKS, (1, 2))]


def _asin_tracks(
    ordinals: tuple[int, ...], embedded: str, path: str
) -> tuple[InspectedTrack, ...]:
    return tuple(
        _track(
            ordinal,
            (
                _duration(ordinal, 1_000),
                _fact(ordinal, AudioInspectionField.ASIN, embedded),
                _fact(
                    ordinal,
                    AudioInspectionField.ASIN,
                    path,
                    surface=AudioEvidenceSurface.PATH_FILE,
                ),
            ),
        )
        for ordinal in ordinals
    )


def test_two_surfaces_of_one_track_are_not_a_cross_track_asin_disagreement() -> None:
    """This observation is about the files disagreeing with each other.

    An embedded tag and a bracketed file name stating different ASINs is one
    file disagreeing with itself on two surfaces the provider path already
    keeps apart, and every track carrying that same pair has said the same
    thing. Counting the distinct values instead of comparing the tracks fired
    on agreement, and fired on a single-file item with nothing to compare.
    """
    agreeing = compose_audio_inspections(
        _asin_tracks((1, 2), "B0CW1KL2QD", "B0DIFFEREN")
    )
    alone = compose_audio_inspections(_asin_tracks((1,), "B0CW1KL2QD", "B0DIFFEREN"))

    assert AudioObservationKind.ASIN_DISAGREES_ACROSS_TRACKS not in {
        value.kind for value in agreeing.observations
    }
    assert AudioObservationKind.ASIN_DISAGREES_ACROSS_TRACKS not in {
        value.kind for value in alone.observations
    }


def test_a_silent_track_has_not_disagreed_with_a_speaking_one() -> None:
    composed = compose_audio_inspections(
        (
            _track(
                1,
                (
                    _duration(1, 1_000),
                    _fact(1, AudioInspectionField.SERIES, "Discworld"),
                ),
            ),
            _track(2, (_duration(2, 1_000),)),
        )
    )

    assert AudioObservationKind.TAG_DISAGREES_ACROSS_TRACKS not in {
        value.kind for value in composed.observations
    }


def test_folder_evidence_is_item_level_and_carries_no_track_provenance() -> None:
    """A sidecar belongs to the folder, so no file may claim to have held it."""
    composed = compose_audio_inspections(
        (
            _track(
                1,
                (
                    _duration(1, 1_000),
                    _fact(
                        1,
                        AudioInspectionField.ASIN,
                        "B0CW1KL2QD",
                        surface=AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF,
                    ),
                ),
            ),
            _track(2, (_duration(2, 1_000),)),
        )
    )

    manifest = attach_claim_references(
        composed, tuple(f"local_{index:064x}" for index in range(len(composed.facts)))
    )
    assert all(value.claims == () for value in manifest)


def test_any_track_carrying_art_makes_the_item_carry_it_with_its_ordinals() -> None:
    composed = compose_audio_inspections(
        (
            _track(1, (_duration(1, 1_000),)),
            _track(2, (_duration(2, 1_000),), cover=True),
            _track(3, (_duration(3, 1_000),), cover=True),
        )
    )

    assert composed.embedded_cover.present is True
    assert composed.embedded_cover.tracks == (2, 3)


@pytest.mark.parametrize(
    ("sequences", "expected"),
    (
        ((1, 2, 3), set()),
        ((1, 2, 4), {AudioObservationKind.TRACK_SEQUENCE_GAP}),
        ((1, None, 3), {AudioObservationKind.TRACK_SEQUENCE_GAP}),
        (
            (2, 1, 3),
            {AudioObservationKind.TRACK_SEQUENCE_DISORDER},
        ),
    ),
    ids=("whole", "hole", "undeclared", "out-of-order"),
)
def test_the_declared_track_order_is_observed_rather_than_enforced(
    sequences: tuple[int | None, ...], expected: set[AudioObservationKind]
) -> None:
    """Order is the service's list order; these say what the index claimed.

    Nothing is refused for them: they exist so a later slice can act on a set
    that is missing a part rather than silently preparing it.
    """
    composed = compose_audio_inspections(
        tuple(
            _track(ordinal, (_duration(ordinal, 1_000),), sequence=sequence)
            for ordinal, sequence in enumerate(sequences, 1)
        )
    )

    assert {
        value.kind
        for value in composed.observations
        if value.kind
        in {
            AudioObservationKind.TRACK_SEQUENCE_GAP,
            AudioObservationKind.TRACK_SEQUENCE_DISORDER,
        }
    } == expected


def test_claim_references_must_line_up_with_the_composed_facts() -> None:
    composed = compose_audio_inspections((_track(1, (_duration(1, 1_000),)),))

    with pytest.raises(ValueError):
        attach_claim_references(composed, ())
