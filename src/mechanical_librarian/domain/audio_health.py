"""What is wrong with an audiobook's media, stated as closed observations.

An owner reading an audiobook's evidence could see every field the inspection
read and still not be told the one thing they can act on: that the recording
runs an hour short of the edition it was matched to, that its chapter cuts are
not that edition's, that its declared track order has a hole in it, or that a
file could not be read at all.  Those are properties of the *media*, not
evidence about the work, so they are named here in their own closed vocabulary
rather than added to the claim set.

Three rules hold the separation, and they are why this module exists beside
``audio_inspection`` rather than inside it:

* an observation is never a claim, a proposal, or a decision -- it names no
  target field, carries no authority, and nothing downstream reads it;
* every kind names the bound it violated, held once in ``AUDIO_HEALTH_BOUNDS``
  so the vocabulary can be enumerated and a projection cannot invent one;
* every observation cites what it was read from -- a local claim reference, a
  candidate identity, a composition kind, a declared track sequence, or a
  track ordinal -- so an owner can check the statement against the report.

``derive_audio_health`` is pure over a published report: it reads what the
report already retains and computes nothing new about the bytes.  A provider's
running time survives into the report only for the lanes that keep an
edition-scope ``DURATION`` claim, so the truncation class is stated for those
lanes and silent for the rest rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from mechanical_librarian.domain.audio_inspection import (
    ADMITTED_AUDIO_CODECS,
    MAX_AUDIO_TRACKS,
    AudioObservation,
    AudioObservationKind,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    RUNTIME_BAND_TIGHT_SECONDS,
    CorroborationOutcome,
    candidate_runtime_seconds,
    chapter_vector_agreement,
    first_divergent_chapter,
    local_chapter_durations_seconds,
    local_claim_runtime_seconds,
)
from mechanical_librarian.domain.evidence_bounds import (
    MAX_SHORT_TEXT_LENGTH,
    EvidenceValueError,
    validate_text,
)
from mechanical_librarian.domain.metadata_report import (
    AudioFacts,
    ClaimKind,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderEvidenceKind,
)


class AudioHealthError(ValueError):
    """A media-health observation or its input is malformed."""


class AudioHealthKind(StrEnum):
    """Exactly what this build can state about an item's media, and no more.

    The vocabulary is closed so an owner can enumerate what they will ever be
    told, and so a projection cannot quietly widen it.  Nothing here names a
    target field: these are statements about files, not about the work.
    """

    RUNTIME_SHORT_OF_PROVIDER = "runtime_short_of_provider"
    CHAPTER_STRUCTURE_MISMATCH = "chapter_structure_mismatch"
    TRACK_SEQUENCE_GAP = "track_sequence_gap"
    TRACK_SEQUENCE_DISORDER = "track_sequence_disorder"
    TRACK_DURATION_UNAVAILABLE = "track_duration_unavailable"
    CHAPTER_ATOMS_PARTIAL = "chapter_atoms_partial"
    TAG_DISAGREES_ACROSS_TRACKS = "tag_disagrees_across_tracks"
    ASIN_DISAGREES_ACROSS_TRACKS = "asin_disagrees_across_tracks"
    CONTAINER_UNEXPECTED = "container_unexpected"
    CODEC_UNEXPECTED = "codec_unexpected"
    DURATION_UNSTATED = "duration_unstated"
    STREAM_ENCRYPTED = "stream_encrypted"
    MEDIA_UNREADABLE = "media_unreadable"


def _shown_seconds(seconds: int) -> int:
    """The seconds a running time reads as: whole minutes from an hour up."""
    return round(seconds / 60) * 60 if seconds >= 3600 else seconds


def hours_minutes(seconds: int) -> str:
    """A running time in the owner's units: `8 h 34 min`, `1 min 30 s` (#1286)."""
    shown = _shown_seconds(seconds)
    hours, rest = divmod(shown, 3600)
    minutes, remainder = divmod(rest, 60)
    parts = (
        ((f"{hours} h",) if hours else ())
        + ((f"{minutes} min",) if minutes else ())
        + ((f"{remainder} s",) if remainder or not shown else ())
    )
    return " ".join(parts)


# The one bound each kind violates, in the owner's words. Held in one table
# because a kind without a stated bound is an accusation without a rule, and a
# projection that composed its own wording would drift from the rule the
# derivation actually applied.
AUDIO_HEALTH_BOUNDS: Final[dict[AudioHealthKind, str]] = {
    AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER: (
        "A complete recording runs within "
        f"{hours_minutes(RUNTIME_BAND_TIGHT_SECONDS)} of the edition's stated "
        "running time."
    ),
    AudioHealthKind.CHAPTER_STRUCTURE_MISMATCH: (
        "The embedded chapter durations align with the matched edition's, "
        "allowing one skipped chapter at each end."
    ),
    AudioHealthKind.TRACK_SEQUENCE_GAP: (
        "The declared track sequence runs without a hole."
    ),
    AudioHealthKind.TRACK_SEQUENCE_DISORDER: (
        "The declared track sequence rises with the file order."
    ),
    AudioHealthKind.TRACK_DURATION_UNAVAILABLE: (
        "Every track states its own running time."
    ),
    AudioHealthKind.CHAPTER_ATOMS_PARTIAL: (
        "Either every track carries embedded chapters or none does."
    ),
    AudioHealthKind.TAG_DISAGREES_ACROSS_TRACKS: (
        "One item states one value per tagged field."
    ),
    AudioHealthKind.ASIN_DISAGREES_ACROSS_TRACKS: (
        "One item states one ASIN across its files."
    ),
    AudioHealthKind.CONTAINER_UNEXPECTED: (
        "Each file holds the container its admitted file type names."
    ),
    AudioHealthKind.CODEC_UNEXPECTED: (
        "Each file's audio stream is "
        + " or ".join(sorted(ADMITTED_AUDIO_CODECS))
        + "."
    ),
    AudioHealthKind.DURATION_UNSTATED: (
        "Each file's container states the running time of what it holds."
    ),
    AudioHealthKind.STREAM_ENCRYPTED: (
        "No file's samples are protected by a rights-management scheme."
    ),
    AudioHealthKind.MEDIA_UNREADABLE: (
        "Every file of the item opens and reads within the supported bounds."
    ),
}
# One citation per track is the widest an item-wide set observation gets, plus
# the handful of item-level references a truncation or chapter statement adds.
MAX_HEALTH_CITATIONS: Final = MAX_AUDIO_TRACKS + 8
# Every kind of every observation an item can carry, at one observation per
# kind per track for the set-level ones. The same shape the report's own
# observation bound uses.
MAX_HEALTH_OBSERVATIONS: Final = len(AudioHealthKind) * MAX_AUDIO_TRACKS

_SET_OBSERVATION_KINDS: Final[dict[AudioObservationKind, AudioHealthKind]] = {
    AudioObservationKind.TRACK_SEQUENCE_GAP: AudioHealthKind.TRACK_SEQUENCE_GAP,
    AudioObservationKind.TRACK_SEQUENCE_DISORDER: (
        AudioHealthKind.TRACK_SEQUENCE_DISORDER
    ),
    AudioObservationKind.TRACK_DURATION_UNAVAILABLE: (
        AudioHealthKind.TRACK_DURATION_UNAVAILABLE
    ),
    AudioObservationKind.CHAPTER_ATOMS_PARTIAL: AudioHealthKind.CHAPTER_ATOMS_PARTIAL,
    AudioObservationKind.TAG_DISAGREES_ACROSS_TRACKS: (
        AudioHealthKind.TAG_DISAGREES_ACROSS_TRACKS
    ),
    AudioObservationKind.ASIN_DISAGREES_ACROSS_TRACKS: (
        AudioHealthKind.ASIN_DISAGREES_ACROSS_TRACKS
    ),
    AudioObservationKind.CONTAINER_UNEXPECTED: AudioHealthKind.CONTAINER_UNEXPECTED,
    AudioObservationKind.CODEC_UNEXPECTED: AudioHealthKind.CODEC_UNEXPECTED,
    AudioObservationKind.DURATION_UNSTATED: AudioHealthKind.DURATION_UNSTATED,
    AudioObservationKind.STREAM_ENCRYPTED: AudioHealthKind.STREAM_ENCRYPTED,
}


@dataclass(frozen=True)
class AudioHealthObservation:
    """One typed statement about an item's media, with its citation and bound.

    ``tracks`` names the files the statement is about, empty when it is about
    the item as a whole.  ``cited`` names what it was read from and is never
    empty: an observation an owner cannot check against the report is an
    assertion, and this vocabulary states none.  ``observed`` and ``expected``
    carry the two values that make the bound concrete where there are two.
    """

    kind: AudioHealthKind
    cited: tuple[str, ...]
    tracks: tuple[int, ...] = ()
    observed: str | None = None
    expected: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not AudioHealthKind
            or type(self.tracks) is not tuple
            or len(self.tracks) > MAX_AUDIO_TRACKS
            or any(
                type(value) is not int or not 1 <= value <= MAX_AUDIO_TRACKS
                for value in self.tracks
            )
            or list(self.tracks) != sorted(set(self.tracks))
        ):
            raise AudioHealthError("audio health observation is malformed")
        if (
            type(self.cited) is not tuple
            or not self.cited
            or len(self.cited) > MAX_HEALTH_CITATIONS
        ):
            raise AudioHealthError("audio health citation is malformed")
        try:
            for value in self.cited:
                validate_text(value, "audio health citation", MAX_SHORT_TEXT_LENGTH)
            for stated in (self.observed, self.expected):
                if stated is not None:
                    validate_text(stated, "audio health value", MAX_SHORT_TEXT_LENGTH)
        except EvidenceValueError as error:
            raise AudioHealthError("audio health observation is malformed") from error

    @property
    def bound(self) -> str:
        """The rule this observation says the media broke."""
        return AUDIO_HEALTH_BOUNDS[self.kind]

    def to_data(self) -> dict[str, object]:
        return {
            "bound": self.bound,
            "cited": list(self.cited),
            "expected": self.expected,
            "kind": self.kind.value,
            "observed": self.observed,
            "tracks": list(self.tracks),
        }


def media_unreadable_observation(track: int | None) -> AudioHealthObservation:
    """State that a file could not be read, naming it when one is known.

    A terminal item has no report at all, so this is the one class built from
    the terminal record rather than derived from retained evidence.  A
    single-file item records no ordinal and cites the item instead.
    """
    if track is not None and (
        type(track) is not int or not 1 <= track <= MAX_AUDIO_TRACKS
    ):
        raise AudioHealthError("audio health track ordinal is malformed")
    return AudioHealthObservation(
        AudioHealthKind.MEDIA_UNREADABLE,
        ("this item's audio files",) if track is None else (f"track {track}",),
        () if track is None else (track,),
    )


def derive_audio_health(report: MetadataReport) -> tuple[AudioHealthObservation, ...]:
    """Read one published report into the media-health statements it supports.

    Pure and read-only: nothing here opens a file, dispatches a provider, or
    touches the evaluator, proposal, or decision paths.  A report with no audio
    section states nothing, and so does one whose retained evidence leaves a
    class uncomparable -- a silence is an absence of proof, never a pass.
    """
    if type(report) is not MetadataReport:
        raise AudioHealthError("audio health input is invalid")
    audio = report.audio
    if audio is None:
        return ()
    observations = [
        *_runtime_observations(report, audio),
        *_chapter_observations(report, audio),
        *_set_observations(audio),
    ]
    return tuple(
        sorted(
            observations[:MAX_HEALTH_OBSERVATIONS],
            key=lambda value: (value.kind.value, value.cited, value.tracks),
        )
    )


def _runtime_observations(
    report: MetadataReport, audio: AudioFacts
) -> list[AudioHealthObservation]:
    """State every edition whose running time this recording is short of, once.

    Two lanes reach the same statement and the precedence between them is
    settled here, in one place, so an owner is never told the same thing twice.

    *Derived* is preferred. A retained ``DURATION`` claim can be compared now,
    including informational runtime alongside demoted work-level metadata. The
    comparison carries both running times without granting recording identity
    or metadata authority.

    *Persisted* fills the rest. Where the report has no comparable provider
    runtime, preparation's recorded comparison still names the candidate. It
    states that the runtime was not retained rather than inventing a number.

    Retained evidence wins outright. Any comparable running time the report
    still holds for an edition decides that edition, in band or short, and the
    persisted record for it states nothing -- one provider's demoted answer
    cannot accuse an edition that a second provider's surviving answer puts
    within the band. The record is read only where the report has no running
    time of its own to compare, which is the case it was written for.

    One statement per edition, whichever route reached it. Two providers can
    answer about the same edition and the report retains both answers under one
    identity, so the guard is applied to the derived route as well: an owner
    told twice that this recording is short of one edition learns nothing the
    second time and has no way to tell the repetition from two real findings.
    """
    local = local_claim_runtime_seconds(report.local_claims)
    references = tuple(
        sorted(
            claim.reference
            for claim in report.local_claims
            if _stated_local_runtime(claim)
        )
    )
    stated: list[AudioHealthObservation] = []
    # Editions this report can still compare for itself, whatever the answer,
    # and editions already stated once.
    compared: set[str] = set()
    derived: set[str] = set()
    if local is not None and references:
        for candidate in _retained_candidates(report):
            provider = candidate_runtime_seconds(candidate)
            if provider is None:
                continue
            compared.add(candidate.identity)
            if (
                provider - local <= RUNTIME_BAND_TIGHT_SECONDS
                or candidate.identity in derived
            ):
                continue
            derived.add(candidate.identity)
            stated.append(
                AudioHealthObservation(
                    AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER,
                    (*references, candidate.identity),
                    # The difference is the one between the two times as
                    # shown, so the three figures agree on the page.
                    observed=(
                        f"{hours_minutes(local)} of local audio, "
                        + hours_minutes(
                            _shown_seconds(provider) - _shown_seconds(local)
                        )
                        + " shorter than the matched edition"
                    ),
                    expected=(
                        f"{hours_minutes(provider)} stated by the matched edition"
                    ),
                )
            )
    for observation in audio.observations:
        if (
            observation.kind is not AudioObservationKind.RUNTIME_SHORT_OF_PROVIDER
            or observation.candidate is None
            or observation.candidate in derived
            or observation.candidate in compared
        ):
            continue
        derived.add(observation.candidate)
        stated.append(
            AudioHealthObservation(
                AudioHealthKind.RUNTIME_SHORT_OF_PROVIDER,
                (*references, observation.candidate),
                observed=(
                    None if local is None else f"{hours_minutes(local)} of local audio"
                ),
                expected=(
                    "a longer running time stated by this edition, which "
                    "publication did not retain"
                ),
            )
        )
    return stated


def _chapter_observations(
    report: MetadataReport, audio: AudioFacts
) -> list[AudioHealthObservation]:
    """State every retained edition whose chapter cuts this recording does not have.

    The chapter structure a provider states survives publication only on a
    corroborated candidate, so this class fires exactly on the items whose
    identity was settled by structure. A vector either side truncated is not
    comparable and states nothing.

    The two counts are always stated; the diverging cut is stated only when
    there is one to name. `first_divergent_chapter` answers `None` both when
    the cuts it could compare all agree and when the two vectors differ in
    length by more than its skip grid can absorb -- forty chapters against
    twelve align nowhere -- and in neither case is there a cut an owner could
    open the file on. The counts alone are what that item is told, which is
    exactly what the contradiction was made of.
    """
    local = local_chapter_durations_seconds(audio)
    if not local:
        return []
    stated: list[AudioHealthObservation] = []
    for candidate in _retained_candidates(report):
        for evidence in candidate.evidence:
            if (
                evidence.kind is not ProviderEvidenceKind.CHAPTER_STRUCTURE
                or evidence.chapters is None
            ):
                continue
            signal = chapter_vector_agreement(
                local,
                evidence.chapters,
                local_truncated=audio.chapters_truncated,
            )
            if signal.outcome is not CorroborationOutcome.CONTRADICTED:
                continue
            observed = f"{len(local)} embedded chapters"
            expected = (
                f"{len(evidence.chapters.durations_seconds)} chapters "
                "stated by the matched edition"
            )
            cut = first_divergent_chapter(local, evidence.chapters.durations_seconds)
            if cut is not None:
                ordinal, ran, stated_ordinal, stated_seconds = cut
                observed += f"; chapter {ordinal} runs {hours_minutes(ran)}"
                expected += (
                    f"; chapter {stated_ordinal} runs {hours_minutes(stated_seconds)}"
                )
                # The two ordinals differ when the alignment skipped an end
                # chapter on one side, which a publisher's branded intro
                # routinely causes. The side that was skipped says so, because
                # two bare chapter numbers that disagree read as a muddle
                # rather than as the comparison that was actually made.
                if ordinal > stated_ordinal:
                    observed += ", aligned past this recording's first chapter"
                elif stated_ordinal > ordinal:
                    expected += ", aligned past the edition's first chapter"
            stated.append(
                AudioHealthObservation(
                    AudioHealthKind.CHAPTER_STRUCTURE_MISMATCH,
                    (audio.composition.value, candidate.identity),
                    observed=observed,
                    expected=expected,
                )
            )
    return stated


def _set_observations(audio: AudioFacts) -> list[AudioHealthObservation]:
    """Project what composing the track set noticed, with the sequences it read.

    The retained observation names ordinals; what an owner has to see beside
    them is the sequence each of those files *declared*, because a gap and a
    disorder are both statements about declared numbering rather than about
    file order.
    """
    sequences = {track.ordinal: track.sequence for track in audio.tracks}
    return [
        AudioHealthObservation(
            _SET_OBSERVATION_KINDS[observation.kind],
            _set_citations(observation, sequences),
            observation.tracks,
        )
        for observation in audio.observations
        if observation.kind in _SET_OBSERVATION_KINDS
    ]


def _set_citations(
    observation: AudioObservation, sequences: dict[int, int | None]
) -> tuple[str, ...]:
    field = () if observation.field is None else (observation.field.value,)
    cited = tuple(
        f"track {ordinal} declares sequence "
        + ("none" if sequences.get(ordinal) is None else str(sequences[ordinal]))
        for ordinal in observation.tracks
    )
    return (*field, *cited) or ("this item's audio files",)


def _retained_candidates(report: MetadataReport) -> tuple[ProviderCandidate, ...]:
    return tuple(
        candidate
        for observation in report.providers
        for candidate in observation.candidates
    )


def _stated_local_runtime(claim: LocalClaim) -> bool:
    return (
        claim.kind is ClaimKind.DURATION
        and claim.state is FieldState.VALID
        and claim.canonical is not None
    )


__all__ = [
    "AUDIO_HEALTH_BOUNDS",
    "MAX_HEALTH_CITATIONS",
    "MAX_HEALTH_OBSERVATIONS",
    "AudioHealthError",
    "AudioHealthKind",
    "AudioHealthObservation",
    "derive_audio_health",
    "media_unreadable_observation",
]
