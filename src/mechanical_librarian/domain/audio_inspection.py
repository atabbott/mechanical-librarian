"""Private, closed values returned by the isolated audiobook inspector.

This vocabulary intentionally belongs to the single audiobook operation, exactly
as ``epub_inspection`` belongs to the single EPUB one.  It is not a shared
media-evidence protocol and not a future format extension point.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Mapping, cast

from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
    validate_role_projection,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion

# `v2` adds exactly one nullable member, `title`, so a per-track chapter can be
# named by the recording rather than by its file name.  The child still reads
# one file and still answers about that file alone.
AUDIO_INSPECTION_SCHEMA = "audio-inspection-v2"
AUDIO_LIMIT_PROFILE = "audio-inspection-limits-v1"
# The retained report's own ceiling, and deliberately not a smaller one.  A
# legal inspection of a file with 1,024 chapters titled to the full 280
# characters serializes to about 1.2 MB before a single fact is added, so any
# tighter cap refuses evidence this vocabulary admits and loses the whole
# inspection.  A larger cap would be no use either: an audio section the
# retained report cannot hold is evidence that can never be published.
MAX_PUBLIC_REPORT_BYTES = 2_097_152
MAX_CHAPTERS = 1_024
MAX_CHAPTER_TITLE_CHARACTERS = 280
MAX_FACTS = 256
# The same two widths `LocalClaim` applies to an observation and the excerpt
# that supports it, restated here so a fact this domain admits can never be
# refused when it becomes a claim.
MAX_OBSERVED_CHARACTERS = 280
MAX_DESCRIPTION_CHARACTERS = 512
MAX_CANONICAL_CHARACTERS = 512
# One day is already far past any published single-file audiobook; the bound
# exists so a hostile container cannot state a duration no arithmetic below
# can hold, not to judge a recording.
MAX_DURATION_MILLISECONDS = 604_800_000
# One directory entry name, at the filesystem's own component ceiling.  The
# manifest records names the sibling listing already proved are single
# components, so nothing wider can reach it.
MAX_SIDECAR_NAME_CHARACTERS = 255
# The largest track set one item may be composed from.  The reference library's
# widest audiobook is 352 files, and every count, byte, and second below is
# bounded against this number rather than against whatever a target answers.
MAX_AUDIO_TRACKS = 512
# One item's whole inspection: every track's digest, probe, and interpretation
# together.  The per-file wall stays what F43 set, so this is the bound that
# stops a wide item from occupying a worker for hours rather than the bound on
# any single file.
MULTI_FILE_WALL_SECONDS = 600
# One track's declared position in the target's own order.  Wider than any
# published recording, and narrow enough that the value stays arithmetic.
MAX_TRACK_SEQUENCE = 100_000

_FIELD_STATES = frozenset(
    {"valid", "malformed", "oversized", "unsupported", "untyped", "inapplicable"}
)
_DECIMAL_SECONDS = re.compile(r"^\d{1,9}(?:\.\d{1,9})?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# The format token the location grammar agreed for a track.  Spelled as a shape
# here rather than as the report's own format set, which lives in a module that
# imports this one.
_FORMAT_TOKEN = re.compile(r"^[a-z0-9]{1,8}$")
_CLAIM_REFERENCE = re.compile(r"^local_[0-9a-f]{64}$")


class AudioInspectionOutcome(StrEnum):
    """Exactly the states this capability can end in, and no others.

    Every member below has a producer, and preparation maps every member onto
    an owner-facing failure code.  The vocabulary deliberately does not mirror
    ``EpubInspectionOutcome``: an unreachable member is a state no test can
    reach and no mapping can be proven against.
    """

    SUCCESS = "success"
    NO_APPLICABLE_EVIDENCE = "no_applicable_evidence"
    INVALID_CONFIGURATION = "invalid_configuration"
    UNSUPPORTED_FORMAT = "unsupported_format"
    MALFORMED = "malformed"
    RESOURCE_LIMIT = "resource_limit"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    UNAVAILABLE_SOURCE = "unavailable_source"
    PROBE_FAILURE = "probe_failure"
    CHILD_FAILURE = "child_failure"
    MULTI_FILE_BUDGET_EXCEEDED = "multi_file_budget_exceeded"
    INTERNAL_FAILURE = "internal_failure"

    @property
    def succeeded(self) -> bool:
        return self in {self.SUCCESS, self.NO_APPLICABLE_EVIDENCE}


class AudioChapterComposition(StrEnum):
    """How one item's retained chapter section was arrived at.

    Named on the report because a concatenated vector and a per-track one are
    different evidence about the same recording, and a reader comparing chapter
    structure has to know which it is holding.
    """

    SINGLE_FILE = "single_file"
    EMBEDDED_CONCATENATED = "embedded_concatenated"
    PER_TRACK = "per_track"


class AudioObservationKind(StrEnum):
    """Exactly what composing one item's tracks can notice, and nothing else.

    These are observations about the *set*, not evidence about the work: none
    of them names a target field, and none can become a proposal.  The
    vocabulary is closed so a reader can enumerate it.
    """

    TRACK_SEQUENCE_GAP = "track_sequence_gap"
    TRACK_SEQUENCE_DISORDER = "track_sequence_disorder"
    TRACK_DURATION_UNAVAILABLE = "track_duration_unavailable"
    CHAPTER_ATOMS_PARTIAL = "chapter_atoms_partial"
    TAG_DISAGREES_ACROSS_TRACKS = "tag_disagrees_across_tracks"
    ASIN_DISAGREES_ACROSS_TRACKS = "asin_disagrees_across_tracks"


class AudioEvidenceSurface(StrEnum):
    """Exactly the surfaces this slice reads, named one per grammar."""

    CONTAINER_FORMAT = "container_format"
    CONTAINER_STREAM = "container_stream"
    MP4_ILST = "mp4_ilst"
    MP4_FREEFORM = "mp4_freeform"
    MP4_FREEFORM_TONE = "mp4_freeform_tone"
    ID3_FRAME = "id3_frame"
    ID3_USER_TEXT = "id3_user_text"
    SIDECAR_AUDIOBOOKSHELF = "sidecar_audiobookshelf"
    SIDECAR_LIBATION = "sidecar_libation"
    SIDECAR_OPF = "sidecar_opf"
    SIDECAR_NFO = "sidecar_nfo"
    SIDECAR_CUE = "sidecar_cue"
    PATH_FOLDER = "path_folder"
    PATH_FILE = "path_file"


# The three report-facing bases each surface rolls up to.  A surface names the
# grammar that was read; a basis names where the bytes came from, which is what
# `LocalClaim` and the owner-facing field results distinguish.
AUDIO_SURFACE_BASIS: Mapping[AudioEvidenceSurface, str] = MappingProxyType(
    {
        AudioEvidenceSurface.CONTAINER_FORMAT: "embedded_tag",
        AudioEvidenceSurface.CONTAINER_STREAM: "embedded_tag",
        AudioEvidenceSurface.MP4_ILST: "embedded_tag",
        AudioEvidenceSurface.MP4_FREEFORM: "embedded_tag",
        AudioEvidenceSurface.MP4_FREEFORM_TONE: "embedded_tag",
        AudioEvidenceSurface.ID3_FRAME: "embedded_tag",
        AudioEvidenceSurface.ID3_USER_TEXT: "embedded_tag",
        AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF: "sidecar",
        AudioEvidenceSurface.SIDECAR_LIBATION: "sidecar",
        AudioEvidenceSurface.SIDECAR_OPF: "sidecar",
        AudioEvidenceSurface.SIDECAR_NFO: "sidecar",
        AudioEvidenceSurface.SIDECAR_CUE: "sidecar",
        AudioEvidenceSurface.PATH_FOLDER: "path",
        AudioEvidenceSurface.PATH_FILE: "path",
    }
)
AUDIO_CLAIM_BASES = frozenset(AUDIO_SURFACE_BASIS.values())


class AudioInspectionField(StrEnum):
    ASIN = "asin"
    CONTRIBUTOR = "contributor"
    SERIES = "series"
    SERIES_SEQUENCE = "series_sequence"
    DURATION = "duration"
    PERFORMANCE_TYPE = "performance_type"
    LANGUAGE = "language"
    DESCRIPTION = "description"


class AudioPerformanceType(StrEnum):
    UNABRIDGED = "unabridged"
    ABRIDGED = "abridged"
    DRAMATIZED = "dramatized"
    FULL_CAST = "full_cast"
    UNKNOWN = "unknown"


def normalize_duration_seconds(value: str) -> str:
    """Return one canonical millisecond-precision seconds string.

    ffprobe states a duration as decimal seconds with a source-chosen number of
    fractional digits, so ``43200``, ``43200.0`` and ``43200.000000`` are the
    same recording.  A single canonical spelling is what lets the report's own
    identity stay stable across two probes of one unchanged file.
    """
    if type(value) is not str or _DECIMAL_SECONDS.fullmatch(value) is None:
        raise ValueError("audio duration is invalid")
    whole, _, fraction = value.partition(".")
    milliseconds = int(whole) * 1000 + int((fraction + "000")[:3])
    if not 0 < milliseconds <= MAX_DURATION_MILLISECONDS:
        raise ValueError("audio duration is invalid")
    return f"{milliseconds // 1000}.{milliseconds % 1000:03d}"


@dataclass(frozen=True)
class AudioInspectionLimits:
    """Configured values bounded by immutable ``audio-inspection-limits-v1`` caps."""

    source_bytes: int = 8_589_934_592
    probe_output_bytes: int = 8_388_608
    sidecar_bytes: int = 1_048_576
    sidecar_documents: int = 8
    chapters: int = MAX_CHAPTERS
    streams: int = 64
    tags: int = 512
    tag_value_characters: int = 16_384
    wall_seconds: int = 60

    HARD_CAPS: ClassVar[Mapping[str, int]] = MappingProxyType(
        {
            "source_bytes": 17_179_869_184,
            "probe_output_bytes": 16_777_216,
            "sidecar_bytes": 4_194_304,
            "sidecar_documents": 16,
            "chapters": MAX_CHAPTERS,
            "streams": 256,
            "tags": 2_048,
            "tag_value_characters": 65_536,
            "wall_seconds": 60,
        }
    )
    MAX_REPORT_BYTES = MAX_PUBLIC_REPORT_BYTES
    MAX_FACTS = MAX_FACTS

    def __post_init__(self) -> None:
        for name, hard_cap in self.HARD_CAPS.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= hard_cap:
                raise EvidenceValueError(
                    "audio inspection limit is outside its immutable cap"
                )

    @property
    def profile(self) -> str:
        return AUDIO_LIMIT_PROFILE

    def to_data(self) -> dict[str, int | str]:
        return {
            "profile": self.profile,
            **{name: getattr(self, name) for name in self.HARD_CAPS},
        }


@dataclass(frozen=True, repr=False)
class AudioChapter:
    """One bounded embedded chapter, retained as structure rather than a claim."""

    ordinal: int
    start_milliseconds: int
    end_milliseconds: int
    title: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.ordinal) is not int
            or not 1 <= self.ordinal <= MAX_CHAPTERS
            or type(self.start_milliseconds) is not int
            or type(self.end_milliseconds) is not int
            or not 0 <= self.start_milliseconds <= MAX_DURATION_MILLISECONDS
            or not 0 <= self.end_milliseconds <= MAX_DURATION_MILLISECONDS
            or self.end_milliseconds < self.start_milliseconds
        ):
            raise ValueError("audio chapter is malformed")
        if self.title is not None:
            try:
                validate_projection_text(
                    self.title, "audio chapter title", MAX_CHAPTER_TITLE_CHARACTERS
                )
            except EvidenceValueError as error:
                raise ValueError("audio chapter is malformed") from error

    def to_data(self) -> dict[str, object]:
        return {
            "end_milliseconds": self.end_milliseconds,
            "ordinal": self.ordinal,
            "start_milliseconds": self.start_milliseconds,
            "title": self.title,
        }

    def __repr__(self) -> str:
        return "AudioChapter(<redacted>)"


@dataclass(frozen=True, repr=False)
class AudioEmbeddedCover:
    """Presence of embedded cover art, and the surface that showed it.

    Deliberately never a ``CoverSource``: this slice records that the file
    carries art, and grants no acquisition, decoding, or election authority
    over it.
    """

    present: bool
    surface: AudioEvidenceSurface | None = None
    tracks: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if type(self.present) is not bool or (
            self.surface is not None and type(self.surface) is not AudioEvidenceSurface
        ):
            raise ValueError("audio embedded cover is malformed")
        if self.present != (self.surface is not None):
            raise ValueError("audio embedded cover provenance is inconsistent")
        if (
            type(self.tracks) is not tuple
            or len(self.tracks) > MAX_AUDIO_TRACKS
            or any(
                type(value) is not int or not 1 <= value <= MAX_AUDIO_TRACKS
                for value in self.tracks
            )
            or list(self.tracks) != sorted(set(self.tracks))
            or (self.tracks and not self.present)
        ):
            raise ValueError("audio embedded cover provenance is inconsistent")

    def to_data(self) -> dict[str, object]:
        """The report's own shape, which names which tracks carried the art."""
        return {**self.to_protocol_data(), "tracks": list(self.tracks)}

    def to_protocol_data(self) -> dict[str, object]:
        """The child boundary's shape, which has no ordinals to state.

        A child inspects one file and knows nothing about the set it belongs
        to, so the ordinals are composed by the parent and deliberately never
        cross the protocol.
        """
        return {
            "present": self.present,
            "surface": None if self.surface is None else self.surface.value,
        }

    def __repr__(self) -> str:
        return "AudioEmbeddedCover(<redacted>)"


@dataclass(frozen=True, repr=False)
class AudioObservation:
    """One noticed property of a composed track set, never evidence about it."""

    kind: AudioObservationKind
    tracks: tuple[int, ...] = ()
    field: AudioInspectionField | None = None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not AudioObservationKind
            or (self.field is not None and type(self.field) is not AudioInspectionField)
            or type(self.tracks) is not tuple
            or len(self.tracks) > MAX_AUDIO_TRACKS
            or any(
                type(value) is not int or not 1 <= value <= MAX_AUDIO_TRACKS
                for value in self.tracks
            )
            or list(self.tracks) != sorted(set(self.tracks))
        ):
            raise ValueError("audio observation is malformed")

    def to_data(self) -> dict[str, object]:
        return {
            "field": None if self.field is None else self.field.value,
            "kind": self.kind.value,
            "tracks": list(self.tracks),
        }

    def __repr__(self) -> str:
        return "AudioObservation(<redacted>)"


@dataclass(frozen=True, repr=False)
class AudioTrackDigest:
    """One track of a composed item, with the provenance its claims carry.

    ``digest`` is the currentness witness for the track's own bytes and
    ``identity`` is the target's identity for it, so a track that is replaced,
    reordered, or edited moves one of the two.  ``claims`` names every composed
    claim this track carried, which is how per-file provenance survives the
    collapse of identical tags into one claim.
    """

    ordinal: int
    identity: str
    digest: str
    format: str
    name: str
    sequence: int | None = None
    duration_milliseconds: int | None = None
    claims: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.ordinal) is not int
            or not 1 <= self.ordinal <= MAX_AUDIO_TRACKS
            or type(self.identity) is not str
            or _SHA256.fullmatch(self.identity) is None
            or type(self.digest) is not str
            or _SHA256.fullmatch(self.digest) is None
            or type(self.format) is not str
            or _FORMAT_TOKEN.fullmatch(self.format) is None
        ):
            raise ValueError("audio track is malformed")
        try:
            validate_projection_text(
                self.name, "audio track name", MAX_SIDECAR_NAME_CHARACTERS
            )
        except EvidenceValueError as error:
            raise ValueError("audio track name is malformed") from error
        if self.sequence is not None and (
            type(self.sequence) is not int
            or not 0 <= self.sequence <= MAX_TRACK_SEQUENCE
        ):
            raise ValueError("audio track sequence is malformed")
        if self.duration_milliseconds is not None and (
            type(self.duration_milliseconds) is not int
            or not 1 <= self.duration_milliseconds <= MAX_DURATION_MILLISECONDS
        ):
            raise ValueError("audio track duration is malformed")
        if (
            type(self.claims) is not tuple
            or len(self.claims) > MAX_FACTS
            or any(
                type(value) is not str or _CLAIM_REFERENCE.fullmatch(value) is None
                for value in self.claims
            )
            or len(set(self.claims)) != len(self.claims)
        ):
            raise ValueError("audio track claim provenance is malformed")

    def to_data(self) -> dict[str, object]:
        return {
            "claims": list(self.claims),
            "digest": self.digest,
            "duration_milliseconds": self.duration_milliseconds,
            "format": self.format,
            "identity": self.identity,
            "name": self.name,
            "ordinal": self.ordinal,
            "sequence": self.sequence,
        }

    def __repr__(self) -> str:
        return "AudioTrackDigest(<redacted>)"


@dataclass(frozen=True, repr=False)
class AudioSidecarDigest:
    """One recognized sidecar present beside the audio file when it was read.

    The pair is a currentness witness rather than evidence: the digest names
    the exact bytes an observation was interpreted from, so a sidecar that is
    added, removed, or edited after publication makes the report stale.
    """

    name: str
    digest: str

    def __post_init__(self) -> None:
        if type(self.digest) is not str or _SHA256.fullmatch(self.digest) is None:
            raise ValueError("audio sidecar digest is malformed")
        try:
            validate_projection_text(
                self.name, "audio sidecar name", MAX_SIDECAR_NAME_CHARACTERS
            )
        except EvidenceValueError as error:
            raise ValueError("audio sidecar name is malformed") from error

    def to_data(self) -> dict[str, object]:
        return {"digest": self.digest, "name": self.name}

    def __repr__(self) -> str:
        return "AudioSidecarDigest(<redacted>)"


@dataclass(frozen=True, repr=False)
class AudioInspectionFact:
    """One bounded audio fact with the exact surface it was read from."""

    field: AudioInspectionField
    source: Fingerprint
    observed_value: str
    canonical_value: str | None
    supporting_context: str
    surface: AudioEvidenceSurface
    state: str = "valid"
    role: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.field) is not AudioInspectionField
            or type(self.source) is not Fingerprint
            or type(self.surface) is not AudioEvidenceSurface
            or self.state not in _FIELD_STATES
        ):
            raise ValueError("audio inspection fact is malformed")
        maximum = (
            MAX_DESCRIPTION_CHARACTERS
            if self.field is AudioInspectionField.DESCRIPTION
            else MAX_OBSERVED_CHARACTERS
        )
        try:
            validate_projection_text(
                self.observed_value, "audio observed value", maximum
            )
            validate_projection_text(self.supporting_context, "audio support", maximum)
            if self.canonical_value is not None:
                validate_projection_text(
                    self.canonical_value,
                    "audio canonical value",
                    MAX_CANONICAL_CHARACTERS,
                )
        except EvidenceValueError as error:
            raise ValueError("audio inspection fact is malformed") from error
        if not self.observed_value or not self.supporting_context:
            raise ValueError("audio inspection fact is malformed")
        if self.observed_value not in self.supporting_context:
            raise ValueError("audio inspection support does not carry its observation")
        if self.role is not None and (
            self.field is not AudioInspectionField.CONTRIBUTOR
            or not _valid_role(self.role)
        ):
            raise ValueError("audio inspection role is invalid")
        if self.field is AudioInspectionField.CONTRIBUTOR and self.role is None:
            raise ValueError("audio contributor fact needs a role")
        if (self.state == "valid") != (self.canonical_value is not None):
            raise ValueError("audio inspection state is inconsistent")
        if self.state != "valid":
            return
        # ``canonical_value`` is a ``str`` on every path below: the guard above
        # rejects a valid fact without one.
        canonical = cast("str", self.canonical_value)
        if self.field is AudioInspectionField.DURATION:
            try:
                expected = normalize_duration_seconds(self.observed_value)
            except ValueError as error:
                raise ValueError("audio duration fact is inconsistent") from error
            if canonical != expected:
                raise ValueError("audio duration fact is inconsistent")
        elif self.field is AudioInspectionField.PERFORMANCE_TYPE and canonical not in {
            value.value for value in AudioPerformanceType
        }:
            raise ValueError("audio performance type is outside its vocabulary")

    @property
    def basis(self) -> str:
        return AUDIO_SURFACE_BASIS[self.surface]

    def to_data(self) -> dict[str, object]:
        return {
            "canonical": self.canonical_value,
            "field": self.field.value,
            "observed": self.observed_value,
            "role": self.role,
            "state": self.state,
            "support": self.supporting_context,
            "surface": self.surface.value,
        }

    def __repr__(self) -> str:
        return "AudioInspectionFact(<redacted>)"


def _valid_role(value: object) -> bool:
    try:
        validate_role_projection(value)
    except EvidenceValueError:
        return False
    return True


@dataclass(frozen=True, repr=False)
class AudioInspection:
    """The one private projection consumed by the retained audiobook report."""

    outcome: AudioInspectionOutcome
    source: Fingerprint | None
    facts: tuple[AudioInspectionFact, ...] = ()
    chapters: tuple[AudioChapter, ...] = ()
    embedded_cover: AudioEmbeddedCover = AudioEmbeddedCover(False)
    chapters_truncated: bool = False
    title: str | None = None

    def __post_init__(self) -> None:
        if self.title is not None:
            try:
                validate_projection_text(
                    self.title, "audio track title", MAX_CHAPTER_TITLE_CHARACTERS
                )
            except EvidenceValueError as error:
                raise ValueError("audio inspection is malformed") from error
        if (
            type(self.outcome) is not AudioInspectionOutcome
            or (self.source is not None and type(self.source) is not Fingerprint)
            or type(self.facts) is not tuple
            or len(self.facts) > MAX_FACTS
            or any(type(value) is not AudioInspectionFact for value in self.facts)
            or type(self.chapters) is not tuple
            or len(self.chapters) > MAX_CHAPTERS
            or any(type(value) is not AudioChapter for value in self.chapters)
            or type(self.embedded_cover) is not AudioEmbeddedCover
            or type(self.chapters_truncated) is not bool
        ):
            raise ValueError("audio inspection is malformed")
        if self.outcome is AudioInspectionOutcome.SUCCESS and self.source is None:
            raise ValueError("successful audio inspection needs a source")
        if self.source is not None and any(
            value.source != self.source for value in self.facts
        ):
            raise ValueError("audio inspection fact source is inconsistent")
        if tuple(value.ordinal for value in self.chapters) != tuple(
            range(1, len(self.chapters) + 1)
        ):
            raise ValueError("audio chapter ordinals are inconsistent")
        if self.embedded_cover.tracks:
            # The child reports one file and never a set, so an ordinal here
            # could only have been forged by something other than the child.
            raise ValueError("audio inspection cannot carry track provenance")
        if not self.outcome.succeeded and (
            self.facts
            or self.chapters
            or self.embedded_cover.present
            or self.chapters_truncated
            or self.title is not None
        ):
            raise ValueError("failed audio inspection cannot retain evidence")

    def to_json(self) -> str:
        """Canonical closed protocol payload for the isolated child boundary."""
        return json.dumps(
            {
                "chapters": [value.to_data() for value in self.chapters],
                "chapters_truncated": self.chapters_truncated,
                "embedded_cover": self.embedded_cover.to_protocol_data(),
                "facts": [value.to_data() for value in self.facts],
                "outcome": self.outcome.value,
                "schema": AUDIO_INSPECTION_SCHEMA,
                "title": self.title,
                "source": None
                if self.source is None
                else {
                    "algorithm": self.source.algorithm,
                    "digest": self.source.digest,
                    "schema": self.source.schema.value,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, serialized: str) -> "AudioInspection":
        if (
            type(serialized) is not str
            or len(serialized.encode("utf-8")) > MAX_PUBLIC_REPORT_BYTES
        ):
            raise ValueError("audio inspection protocol is invalid")
        try:
            value = json.loads(serialized)
            if (
                type(value) is not dict
                or set(value)
                != {
                    "chapters",
                    "chapters_truncated",
                    "embedded_cover",
                    "facts",
                    "outcome",
                    "schema",
                    "source",
                    "title",
                }
                or value["schema"] != AUDIO_INSPECTION_SCHEMA
            ):
                raise ValueError
            source_data = value["source"]
            source = (
                None
                if source_data is None
                else Fingerprint(
                    source_data["algorithm"],
                    SchemaVersion(source_data["schema"]),
                    source_data["digest"],
                )
            )
            cover_data = value["embedded_cover"]
            if type(cover_data) is not dict or set(cover_data) != {
                "present",
                "surface",
            }:
                raise ValueError
            # A fact-bearing document always carries a source: `AudioInspectionFact`
            # rejects a `None` source, and the `ValueError` it raises is caught
            # below as an invalid protocol.
            facts = tuple(
                AudioInspectionFact(
                    AudioInspectionField(item["field"]),
                    cast("Fingerprint", source),
                    item["observed"],
                    item["canonical"],
                    item["support"],
                    AudioEvidenceSurface(item["surface"]),
                    item["state"],
                    item["role"],
                )
                for item in value["facts"]
                if type(item) is dict
                and set(item)
                == {
                    "canonical",
                    "field",
                    "observed",
                    "role",
                    "state",
                    "support",
                    "surface",
                }
            )
            chapters = tuple(
                AudioChapter(
                    item["ordinal"],
                    item["start_milliseconds"],
                    item["end_milliseconds"],
                    item["title"],
                )
                for item in value["chapters"]
                if type(item) is dict
                and set(item)
                == {"end_milliseconds", "ordinal", "start_milliseconds", "title"}
            )
            result = cls(
                AudioInspectionOutcome(value["outcome"]),
                source,
                facts,
                chapters,
                AudioEmbeddedCover(
                    cover_data["present"],
                    None
                    if cover_data["surface"] is None
                    else AudioEvidenceSurface(cover_data["surface"]),
                ),
                value["chapters_truncated"],
                value["title"],
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("audio inspection protocol is invalid") from None
        if result.to_json() != serialized:
            raise ValueError("audio inspection protocol is not canonical")
        return result

    def __repr__(self) -> str:
        return "AudioInspection(<redacted>)"


__all__ = [
    "AUDIO_CLAIM_BASES",
    "AUDIO_INSPECTION_SCHEMA",
    "AUDIO_SURFACE_BASIS",
    "AudioChapter",
    "AudioChapterComposition",
    "AudioEmbeddedCover",
    "AudioEvidenceSurface",
    "AudioInspection",
    "AudioInspectionFact",
    "AudioInspectionField",
    "AudioInspectionLimits",
    "AudioInspectionOutcome",
    "AudioObservation",
    "AudioObservationKind",
    "AudioPerformanceType",
    "AudioSidecarDigest",
    "AudioTrackDigest",
    "MAX_AUDIO_TRACKS",
    "MAX_CHAPTERS",
    "MAX_PUBLIC_REPORT_BYTES",
    "MAX_SIDECAR_NAME_CHARACTERS",
    "MULTI_FILE_WALL_SECONDS",
    "normalize_duration_seconds",
]
