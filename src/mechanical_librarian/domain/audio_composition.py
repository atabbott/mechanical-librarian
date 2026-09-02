"""Compose one audiobook item's inspected tracks into a single evidence set.

Every function here is pure: it is handed the closed ``AudioInspection`` each
isolated child returned, plus the target facts the parent already proved about
each file, and returns closed domain values.  Nothing here opens a file, spawns
a process, or reads a clock -- the budget that governs those lives with the
parent that owns them.

The composition rules exist because an item is the unit an owner has, and a
track is not.  A recording split across 352 files states one running time, one
chapter structure, and one set of tags; publishing 352 durations would give the
runtime gate nothing it could elect, and publishing the first track's alone
would state a running time no recording has.
"""

from __future__ import annotations

from dataclasses import dataclass

from mechanical_librarian.domain.audio_inspection import (
    MAX_CHAPTER_TITLE_CHARACTERS,
    MAX_CHAPTERS,
    AudioChapter,
    AudioChapterComposition,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioInspection,
    AudioInspectionFact,
    AudioInspectionField,
    AudioObservation,
    AudioObservationKind,
    AudioTrackDigest,
    normalize_duration_seconds,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)

# The surfaces a folder carries rather than a file.  They are interpreted once
# for the whole item -- the parent hands them to the first track's child only --
# so they belong to no track and carry no track provenance.
_FOLDER_SURFACES = frozenset(
    {
        AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF,
        AudioEvidenceSurface.SIDECAR_LIBATION,
        AudioEvidenceSurface.SIDECAR_OPF,
        AudioEvidenceSurface.SIDECAR_NFO,
        AudioEvidenceSurface.SIDECAR_CUE,
        AudioEvidenceSurface.PATH_FOLDER,
    }
)


@dataclass(frozen=True, slots=True)
class InspectedTrack:
    """One track the parent opened, digested, probed, and interpreted."""

    ordinal: int
    identity: str
    digest: str
    format: str
    name: str
    inspection: AudioInspection
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class ComposedAudio:
    """One item's composed evidence, before its facts become claims.

    ``provenance`` is aligned with ``facts``: entry *i* names the track
    ordinals that carried fact *i*, and is empty for a folder-level fact.  The
    caller turns the facts into claims and writes their references back onto
    ``tracks``, which is the only step that needs a claim identity to exist.
    """

    facts: tuple[AudioInspectionFact, ...]
    provenance: tuple[tuple[int, ...], ...]
    chapters: tuple[AudioChapter, ...]
    chapters_truncated: bool
    embedded_cover: AudioEmbeddedCover
    composition: AudioChapterComposition
    observations: tuple[AudioObservation, ...]
    tracks: tuple[AudioTrackDigest, ...]


def compose_audio_inspections(tracks: tuple[InspectedTrack, ...]) -> ComposedAudio:
    """Reduce one item's ordered per-track inspections to one evidence set."""
    if not tracks:
        raise ValueError("audio composition needs at least one track")
    durations = tuple(_track_duration(value.inspection) for value in tracks)
    facts, provenance = _compose_facts(tracks, durations)
    chapters, truncated, composition, atoms_partial = _compose_chapters(
        tracks, durations
    )
    manifest = tuple(
        AudioTrackDigest(
            track.ordinal,
            track.identity,
            track.digest,
            track.format,
            track.name,
            track.sequence,
            duration,
        )
        for track, duration in zip(tracks, durations, strict=True)
    )
    return ComposedAudio(
        facts,
        provenance,
        chapters,
        truncated,
        _compose_cover(tracks),
        composition,
        _observations(tracks, durations, facts, provenance, atoms_partial),
        manifest,
    )


def attach_claim_references(
    composed: ComposedAudio, references: tuple[str, ...]
) -> tuple[AudioTrackDigest, ...]:
    """Record which composed claims each track carried.

    ``references`` is aligned with ``composed.facts``, which is the order the
    caller created claims in.  A claim listed under several ordinals is one
    claim: identical evidence read from every track is one observation about
    the work, not one per file.
    """
    if len(references) != len(composed.facts):
        raise ValueError("audio claim references do not match the composed facts")
    carried: dict[int, list[str]] = {track.ordinal: [] for track in composed.tracks}
    for reference, ordinals in zip(references, composed.provenance, strict=True):
        for ordinal in ordinals:
            values = carried[ordinal]
            if reference not in values:
                values.append(reference)
    return tuple(
        AudioTrackDigest(
            track.ordinal,
            track.identity,
            track.digest,
            track.format,
            track.name,
            track.sequence,
            track.duration_milliseconds,
            tuple(carried[track.ordinal]),
        )
        for track in composed.tracks
    )


def _track_duration(inspection: AudioInspection) -> int | None:
    """Read one track's own running time from the container-format fact."""
    for fact in inspection.facts:
        if (
            fact.field is AudioInspectionField.DURATION
            and fact.surface is AudioEvidenceSurface.CONTAINER_FORMAT
            and fact.canonical_value is not None
        ):
            whole, _, fraction = fact.canonical_value.partition(".")
            return int(whole) * 1000 + int(fraction)
    return None


def _value_key(
    fact: AudioInspectionFact,
) -> tuple[str, str, str, str | None, str, str | None]:
    return (
        fact.field.value,
        fact.surface.value,
        fact.observed_value,
        fact.canonical_value,
        fact.state,
        fact.role,
    )


def _compose_facts(
    tracks: tuple[InspectedTrack, ...], durations: tuple[int | None, ...]
) -> tuple[tuple[AudioInspectionFact, ...], tuple[tuple[int, ...], ...]]:
    """Collapse identical evidence and retain disagreeing evidence as its own.

    Two tracks stating the same narrator have made one observation about the
    work; two tracks stating different ASINs have made two, and electing
    between them here would decide with local evidence what only the provider
    path can decide.  So the first collapses and the second is retained whole,
    each claim carrying the ordinals that stated it.
    """
    order: list[AudioInspectionFact] = []
    positions: dict[tuple[str, str, str, str | None, str, str | None], int] = {}
    carried: list[list[int]] = []
    for track in tracks:
        for fact in track.inspection.facts:
            if fact.field is AudioInspectionField.DURATION:
                # Per-track running times are recorded on the manifest, never
                # as claims: one item states one duration.
                continue
            key = _value_key(fact)
            position = positions.get(key)
            if position is None:
                positions[key] = len(order)
                order.append(fact)
                carried.append(
                    [] if fact.surface in _FOLDER_SURFACES else [track.ordinal]
                )
                continue
            ordinals = carried[position]
            if fact.surface not in _FOLDER_SURFACES and track.ordinal not in ordinals:
                ordinals.append(track.ordinal)
    composed = _composed_duration(tracks, durations)
    facts = tuple(order) if composed is None else (composed, *order)
    provenance = tuple(tuple(value) for value in carried)
    return facts, (provenance if composed is None else ((), *provenance))


def _composed_duration(
    tracks: tuple[InspectedTrack, ...], durations: tuple[int | None, ...]
) -> AudioInspectionFact | None:
    """State the item's one running time, or state none at all.

    A set missing any track's duration has no item running time to state: the
    sum of what is present is a shorter recording than the one on the mount,
    and publishing it would corroborate the wrong edition.  Nothing is
    published instead, and the observation says why.
    """
    if any(value is None for value in durations):
        return None
    if len(tracks) == 1:
        return next(
            (
                fact
                for fact in tracks[0].inspection.facts
                if fact.field is AudioInspectionField.DURATION
                and fact.surface is AudioEvidenceSurface.CONTAINER_FORMAT
            ),
            None,
        )
    total = sum(value for value in durations if value is not None)
    observed = f"{total // 1000}.{total % 1000:03d}"
    source = tracks[0].inspection.source
    if source is None:
        return None
    try:
        canonical = normalize_duration_seconds(observed)
        return AudioInspectionFact(
            AudioInspectionField.DURATION,
            # The composed duration belongs to the item, and an item has no
            # fingerprint of its own at this layer.  The first track's is the
            # one source every reader of this fact can resolve.
            source,
            observed,
            canonical,
            observed,
            AudioEvidenceSurface.CONTAINER_FORMAT,
        )
    except ValueError:
        # A summed running time past the domain's own seven-day ceiling states
        # no recording, so the item publishes none and says so.
        return None


def _compose_chapters(
    tracks: tuple[InspectedTrack, ...], durations: tuple[int | None, ...]
) -> tuple[tuple[AudioChapter, ...], bool, AudioChapterComposition, bool]:
    """Build the item's one chapter vector from what the tracks carry.

    Concatenation is only honest where every track carries atoms: a vector
    mixing one track's real chapters with another's whole-file span would
    read as a chapter structure the recording does not have.  Where the atoms
    are partial, every track becomes one chapter and the observation records
    that atoms were dropped.
    """
    if len(tracks) == 1:
        inspection = tracks[0].inspection
        return (
            inspection.chapters,
            inspection.chapters_truncated,
            AudioChapterComposition.SINGLE_FILE,
            False,
        )
    with_atoms = tuple(bool(track.inspection.chapters) for track in tracks)
    truncated = any(track.inspection.chapters_truncated for track in tracks)
    offsets = _offsets(durations)
    spans: list[tuple[int, int, str | None]]
    if all(with_atoms):
        spans = [
            (
                offset + chapter.start_milliseconds,
                offset + chapter.end_milliseconds,
                chapter.title,
            )
            for offset, track in zip(offsets, tracks, strict=True)
            for chapter in track.inspection.chapters
        ]
        composition = AudioChapterComposition.EMBEDDED_CONCATENATED
        partial = False
    else:
        spans = [
            (offset, offset + (duration or 0), _chapter_title(track))
            for offset, duration, track in zip(offsets, durations, tracks, strict=True)
        ]
        composition = AudioChapterComposition.PER_TRACK
        partial = any(with_atoms)
    return (
        tuple(
            AudioChapter(ordinal, start, end, title)
            for ordinal, (start, end, title) in enumerate(spans[:MAX_CHAPTERS], 1)
        ),
        truncated or len(spans) > MAX_CHAPTERS,
        composition,
        partial,
    )


def _offsets(durations: tuple[int | None, ...]) -> tuple[int, ...]:
    values: list[int] = []
    running = 0
    for duration in durations:
        values.append(running)
        running += duration or 0
    return tuple(values)


def _chapter_title(track: InspectedTrack) -> str | None:
    """Name one track's chapter by the recording, then by the exported file."""
    if track.inspection.title is not None:
        return track.inspection.title
    stem = track.name.rsplit(".", 1)[0]
    try:
        return validate_projection_text(
            stem, "audio chapter title", MAX_CHAPTER_TITLE_CHARACTERS
        )
    except EvidenceValueError:
        return None


def _compose_cover(tracks: tuple[InspectedTrack, ...]) -> AudioEmbeddedCover:
    """Record that the item carries art, and which of its files hold it."""
    carrying = tuple(
        track.ordinal for track in tracks if track.inspection.embedded_cover.present
    )
    if not carrying:
        return AudioEmbeddedCover(False)
    surface = next(
        track.inspection.embedded_cover.surface
        for track in tracks
        if track.inspection.embedded_cover.present
    )
    return AudioEmbeddedCover(True, surface, carrying)


def _observations(
    tracks: tuple[InspectedTrack, ...],
    durations: tuple[int | None, ...],
    facts: tuple[AudioInspectionFact, ...],
    provenance: tuple[tuple[int, ...], ...],
    atoms_partial: bool,
) -> tuple[AudioObservation, ...]:
    values: list[AudioObservation] = []
    values.extend(_sequence_observations(tracks))
    missing = tuple(
        track.ordinal
        for track, duration in zip(tracks, durations, strict=True)
        if duration is None
    )
    if missing or not any(
        fact.field is AudioInspectionField.DURATION for fact in facts
    ):
        values.append(
            AudioObservation(AudioObservationKind.TRACK_DURATION_UNAVAILABLE, missing)
        )
    if atoms_partial:
        values.append(
            AudioObservation(
                AudioObservationKind.CHAPTER_ATOMS_PARTIAL,
                tuple(track.ordinal for track in tracks if track.inspection.chapters),
            )
        )
    values.extend(_disagreement_observations(facts, provenance))
    return tuple(values)


def _sequence_observations(
    tracks: tuple[InspectedTrack, ...],
) -> tuple[AudioObservation, ...]:
    """Say whether the target's own declared track order is whole and ordered.

    These are what a later slice reads to decide whether a file set is missing
    a part; nothing here refuses an item for them.  A set that declares no
    position at all has said nothing to disagree with.
    """
    declared = tuple(
        (track.ordinal, track.sequence)
        for track in tracks
        if track.sequence is not None
    )
    if not declared:
        return ()
    values: list[AudioObservation] = []
    missing = tuple(track.ordinal for track in tracks if track.sequence is None)
    ordered = sorted(sequence for _ordinal, sequence in declared)
    ranked: dict[int, int] = {}
    for position, sequence in enumerate(ordered):
        ranked.setdefault(sequence, position)
    holes = tuple(
        ordinal
        for ordinal, sequence in declared
        if sequence != ordered[0] + ranked[sequence]
    )
    if missing or holes:
        values.append(
            AudioObservation(
                AudioObservationKind.TRACK_SEQUENCE_GAP,
                tuple(sorted(set(missing) | set(holes))),
            )
        )
    disorder = tuple(
        ordinal
        for (ordinal, sequence), (_previous_ordinal, previous) in zip(
            declared[1:], declared[:-1], strict=True
        )
        if sequence <= previous
    )
    if disorder:
        values.append(
            AudioObservation(AudioObservationKind.TRACK_SEQUENCE_DISORDER, disorder)
        )
    return tuple(values)


def _disagreement_observations(
    facts: tuple[AudioInspectionFact, ...],
    provenance: tuple[tuple[int, ...], ...],
) -> tuple[AudioObservation, ...]:
    """Name the surfaces on which two tracks of one item said different things.

    A track that says nothing about a field has not disagreed with one that
    does: silence is absent evidence.  Only two tracks that both spoke and
    spoke differently are a disagreement.
    """
    spoken: dict[
        tuple[AudioInspectionField, AudioEvidenceSurface],
        dict[int, set[tuple[str, str | None, str, str | None]]],
    ] = {}
    for fact, ordinals in zip(facts, provenance, strict=True):
        for ordinal in ordinals:
            spoken.setdefault((fact.field, fact.surface), {}).setdefault(
                ordinal, set()
            ).add(
                (
                    fact.observed_value,
                    fact.canonical_value,
                    fact.state,
                    fact.role,
                )
            )
    values: list[AudioObservation] = []
    for (field, _surface), per_track in sorted(
        spoken.items(), key=lambda item: (item[0][0].value, item[0][1].value)
    ):
        if len({frozenset(value) for value in per_track.values()}) > 1:
            values.append(
                AudioObservation(
                    AudioObservationKind.TAG_DISAGREES_ACROSS_TRACKS,
                    tuple(sorted(per_track)),
                    field,
                )
            )
    asins: dict[int, set[str]] = {}
    for fact, ordinals in zip(facts, provenance, strict=True):
        if fact.field is not AudioInspectionField.ASIN or fact.canonical_value is None:
            continue
        for ordinal in ordinals:
            asins.setdefault(ordinal, set()).add(fact.canonical_value)
    if len({value for values_ in asins.values() for value in values_}) > 1:
        values.append(
            AudioObservation(
                AudioObservationKind.ASIN_DISAGREES_ACROSS_TRACKS,
                tuple(sorted(asins)),
                AudioInspectionField.ASIN,
            )
        )
    return tuple(values)


__all__ = [
    "ComposedAudio",
    "InspectedTrack",
    "attach_claim_references",
    "compose_audio_inspections",
]
