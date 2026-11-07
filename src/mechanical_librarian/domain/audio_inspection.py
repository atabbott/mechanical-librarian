"""Private, closed values returned by the isolated audiobook inspector.

This vocabulary intentionally belongs to the single audiobook operation, exactly
as ``epub_inspection`` belongs to the single EPUB one.  It is not a shared
media-evidence protocol and not a future format extension point.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final, Mapping, cast

from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    replace_forbidden_codepoints,
    validate_projection_text,
    validate_role_projection,
)
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion

# `v5` returns each interpreted sidecar on its own -- its name, its facts and
# its association text -- beside the file's own association text, so the
# parent can decide which documents belong to the item before any fact becomes
# a claim (#1481).  The child still reads one file and names no ordinal: which
# track a fact came from, and whether a sidecar belongs, is the parent's
# knowledge.
AUDIO_INSPECTION_SCHEMA = "audio-inspection-v5"
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
# Association text: a sidecar's or a file's title and creators, read only so the
# parent can ask whether a document belongs to the item (#1481).  A value past
# the bound, or one the projection grammar refuses, is refused whole and counts
# as none, so association fails closed.
MAX_ASSOCIATION_CHARACTERS = 255
MAX_ASSOCIATION_CREATORS = 16
# The newly read Libation keys a manifest entry records as shadow evidence.
MAX_SIDECAR_SHADOW = 16
# The largest track set one item may be composed from.  The reference library's
# widest audiobook is 352 files, and every count, byte, and second below is
# bounded against this number rather than against whatever a target answers.
MAX_AUDIO_TRACKS = 512
# The widest raw audio-file list a target payload may state before this build
# stops reading it.  The set an item is admitted on is the set the service
# plays, so the records it excludes are dropped before the track bound above is
# applied -- and this is the bound that keeps finding that out finite.
MAX_AUDIO_FILE_RECORDS = 2 * MAX_AUDIO_TRACKS
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
# ISO 639-2's special codes, plus the literal `xxx` taggers write for "none".
# Each says the recording's language was not stated, or is not one language, so
# none is a language observation; the child never emits one, and the parent
# refuses a fact that holds one (#1250).
NON_LINGUISTIC_LANGUAGE_CODES: Final = frozenset({"und", "mul", "mis", "zxx", "xxx"})
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
    """Exactly what preparing one item can notice about its media, and no more.

    These are observations about the *files*, not evidence about the work: none
    of them names a target field, and none can become a proposal.  The
    vocabulary is closed so a reader can enumerate it.

    Most are composed from the track set.  Four are composed from what a single
    file's headers stated about itself -- its container, its codec, whether it
    stated a running time at all, and whether its samples are protected -- and
    one, ``runtime_short_of_provider``, is recorded at preparation time because
    the comparison it names is made against a provider runtime that publication
    then strips: a candidate the corroboration band did not admit keeps no
    edition-scope ``DURATION`` claim, so nothing could be derived from the
    published report afterwards.
    """

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
    RUNTIME_SHORT_OF_PROVIDER = "runtime_short_of_provider"


# The stream codecs an admitted audiobook container is expected to carry, held
# once because two modules read it: the composition that notices a departure
# and the health vocabulary that states the bound it broke.  Anything else is
# observed and never refused -- an AC-3 stream in an `.m4b` is a real recording
# an owner may well want to keep, and this build is not the authority that
# decides otherwise.
ADMITTED_AUDIO_CODECS = frozenset({"aac", "mp3"})
# The demuxer family each admitted file extension names, keyed by the
# extension.  `format_name` is one string per demuxer -- ffprobe joins the
# names a single demuxer answers to with commas and prints that whole string --
# so the container fact is compared to it by equality.  The MOV demuxer answers
# to six names at once, which is why the value looks like a list and is not one.
MOV_CONTAINER_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
MP3_CONTAINER_FAMILY = "mp3"
# Read-only at runtime, not merely un-rebindable: `Final` stops this name being
# reassigned and stops nothing else, and a table any importer can edit in place
# is a table that decides what `container_unexpected` means for every item the
# process goes on to inspect.
ADMITTED_AUDIO_CONTAINERS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "m4a": MOV_CONTAINER_FAMILY,
        "m4b": MOV_CONTAINER_FAMILY,
        "mp3": MP3_CONTAINER_FAMILY,
    }
)
# The MP4 sample-entry four-character codes that name protected samples.  They
# are the one encryption marker the stripped probe surfaces from headers alone:
# an Apple FairPlay entry reaches `codec_tag_string` verbatim, while an ISO
# common-encryption entry is rewritten to its `frma` original format before the
# probe ever reports it and leaves no marker to read.
PROTECTED_SAMPLE_ENTRY_TAGS = frozenset({"drms", "drmi", "drma"})


class AudioEvidenceSurface(StrEnum):
    """Exactly the surfaces this slice reads, named one per grammar."""

    CONTAINER_FORMAT = "container_format"
    CONTAINER_STREAM = "container_stream"
    MP4_ILST = "mp4_ilst"
    MP4_FREEFORM = "mp4_freeform"
    MP4_FREEFORM_TONE = "mp4_freeform_tone"
    ID3_FRAME = "id3_frame"
    ID3_USER_TEXT = "id3_user_text"
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


class AudioSidecarKind(StrEnum):
    """What one recognised document beside an audiobook is (#1481).

    ``audiobookshelf_record`` is Audiobookshelf's own ``metadata.json`` or
    ``metadata.abs``: a copy of the target's record, digested for currentness
    and never read.  The other four are the sidecar grammars.
    """

    AUDIOBOOKSHELF_RECORD = "audiobookshelf_record"
    LIBATION = "libation"
    OPF = "opf"
    NFO = "nfo"
    CUE = "cue"


class AudioSidecarBinding(StrEnum):
    """Whether a document is named for one track or belongs to the folder."""

    TRACK = "track"
    FOLDER = "folder"


class AudioSidecarRefusal(StrEnum):
    """Why a document, or part of one, does not speak for the item (#1481).

    Closed and value-free, recorded in the report's sidecar manifest only.
    The first five end evaluation for the document; the last two withhold
    part of it.  Declaration order is the order a manifest entry lists them.
    """

    TARGET_RECORD = "target_record"
    SHARED_FOLDER = "shared_folder"
    OTHER_FILE = "other_file"
    OTHER_BOOK = "other_book"
    COLLECTION = "collection"
    OTHER_EDITION = "other_edition"
    SIDECARS_DISAGREE = "sidecars_disagree"


TERMINAL_SIDECAR_REFUSALS: Final = frozenset(
    {
        AudioSidecarRefusal.TARGET_RECORD,
        AudioSidecarRefusal.SHARED_FOLDER,
        AudioSidecarRefusal.OTHER_FILE,
        AudioSidecarRefusal.OTHER_BOOK,
        AudioSidecarRefusal.COLLECTION,
    }
)
# The refusals the parent decides from the listing alone, before any child
# reads a byte, and which currentness therefore recomputes.
PARENT_SIDECAR_REFUSALS: Final = frozenset(
    {AudioSidecarRefusal.TARGET_RECORD, AudioSidecarRefusal.SHARED_FOLDER}
)
# The surface each readable kind is interpreted under.  The target's record has
# none: it never reaches the child.
SIDECAR_KIND_SURFACES: Final[Mapping[AudioSidecarKind, AudioEvidenceSurface]] = (
    MappingProxyType(
        {
            AudioSidecarKind.LIBATION: AudioEvidenceSurface.SIDECAR_LIBATION,
            AudioSidecarKind.OPF: AudioEvidenceSurface.SIDECAR_OPF,
            AudioSidecarKind.NFO: AudioEvidenceSurface.SIDECAR_NFO,
            AudioSidecarKind.CUE: AudioEvidenceSurface.SIDECAR_CUE,
        }
    )
)
# The fields a sidecar can carry, and so the fields a manifest entry can name
# as disputed or echoed.  Performance type is never an echo: no target field
# holds it.
SIDECAR_FIELDS: Final = frozenset(AudioInspectionField) - {
    AudioInspectionField.DURATION
}
ECHO_FIELDS: Final = SIDECAR_FIELDS - {AudioInspectionField.PERFORMANCE_TYPE}


class AudioSidecarShadowField(StrEnum):
    """Libation keys read as shadow evidence only, until #1482 qualifies them."""

    SERIES_TITLE = "series_title"
    FORMAT_TYPE = "format_type"


def association_text(value: object) -> str | None:
    """Return one title or creator as association text, or none at all.

    Whitespace is collapsed first.  A value past the bound, or one holding a
    code point the projection grammar refuses, is refused whole.
    """
    if type(value) is not str:
        return None
    text = " ".join(value.split())
    if not text or len(text) > MAX_ASSOCIATION_CHARACTERS:
        return None
    try:
        return validate_projection_text(
            text, "audio association text", MAX_ASSOCIATION_CHARACTERS
        )
    except EvidenceValueError:
        return None


def _valid_association(value: object) -> bool:
    return type(value) is str and association_text(value) == value


def _valid_creators(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) <= MAX_ASSOCIATION_CREATORS
        and all(_valid_association(item) for item in value)
    )


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


def unnamed_track_name(ordinal: int) -> str:
    """Name the track this build could publish no file name for."""

    return f"track-{ordinal}"


@dataclass(frozen=True, slots=True)
class TrackNameProjection:
    """The manifest name for one track, and how the projection arrived at it.

    ``synthesized`` says the name is the track's position because nothing the
    filesystem stated survived projection; ``repaired`` says at least one
    refused codepoint became U+FFFD.  Both are carried as facts of the
    projection rather than read back off the string, because a filesystem can
    legitimately state a name of literal U+FFFD characters or a file literally
    called ``track-1``, and neither of those is this build's doing.
    """

    name: str
    synthesized: bool
    repaired: bool


def project_track_name_detail(name: str, ordinal: int) -> TrackNameProjection:
    """Return the manifest name for one track, or the position it holds.

    A file name is untrusted text read from a mounted filesystem, and a real
    library states names no bounded evidence value admits: one 75-file
    audiobook carries a double-encoded en dash, which leaves the C1 controls
    U+0080 and U+0093 in 71 of its names.  Refusing the manifest entry there
    denies the whole item its evidence set over a value nothing is decided
    from -- currentness is witnessed by the per-track identities, digests, and
    order, and ``unchanged`` compares no name at all -- so this projection is
    total and deterministic instead.  Every codepoint the evidence bounds
    refuse becomes U+FFFD in place, keeping the shape of what the filesystem
    said, and a name with nothing of its own left is published as its own
    position.  Survival is judged on the characters the filesystem stated and
    kept, never on the substitution's output, so a name made of literal U+FFFD
    is a name.  The domain validator is not loosened: this is what is handed
    to it, and a retained document whose name still carries a raw control
    character fails closed exactly as before.
    """

    normalized = unicodedata.normalize("NFC", name)
    replaced = replace_forbidden_codepoints(normalized)
    repaired = replaced != normalized
    # Truncation can expose whitespace the first strip did not reach, and a
    # trimmed value is what the validator admits.
    projected = replaced.strip()[:MAX_SIDECAR_NAME_CHARACTERS].strip()
    survived = any(
        stated == kept and not stated.isspace()
        for stated, kept in zip(
            normalized[:MAX_SIDECAR_NAME_CHARACTERS],
            replaced[:MAX_SIDECAR_NAME_CHARACTERS],
            strict=True,
        )
    )
    if not projected or not survived:
        # Nothing the filesystem said survived, so a row of replacement
        # characters would state a name rather than admit there is none.
        return TrackNameProjection(unnamed_track_name(ordinal), True, repaired)
    try:
        return TrackNameProjection(
            validate_projection_text(
                projected, "audio track name", MAX_SIDECAR_NAME_CHARACTERS
            ),
            False,
            repaired,
        )
    except EvidenceValueError:
        return TrackNameProjection(unnamed_track_name(ordinal), True, repaired)


def project_track_name(name: str, ordinal: int) -> str:
    """Return only the manifest name; see ``project_track_name_detail``."""

    return project_track_name_detail(name, ordinal).name


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
    """One noticed property of one item's media, never evidence about it.

    ``tracks`` names the files an observation is about and is empty when it is
    about the item as a whole; ``field`` names the tagged field two files
    disagreed on; ``candidate`` names the provider candidate a comparison was
    made against.  Each is set by exactly the kinds that have one, and the
    three together are the whole citation an owner is offered -- an observation
    carries no value, no bound, and no wording of its own, because none of
    those is a property of the media and all three would then be persisted
    prose this build could never correct.
    """

    kind: AudioObservationKind
    tracks: tuple[int, ...] = ()
    field: AudioInspectionField | None = None
    candidate: str | None = None

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
        if self.candidate is not None:
            try:
                validate_projection_text(
                    self.candidate,
                    "audio observation candidate",
                    MAX_OBSERVED_CHARACTERS,
                )
            except EvidenceValueError as error:
                raise ValueError("audio observation is malformed") from error

    def to_data(self) -> dict[str, object]:
        return {
            "candidate": self.candidate,
            "field": None if self.field is None else self.field.value,
            "kind": self.kind.value,
            "tracks": list(self.tracks),
        }

    def __repr__(self) -> str:
        return "AudioObservation(<redacted>)"


@dataclass(frozen=True, repr=False)
class AudioContainerFacts:
    """What one file's own headers stated about its container and its stream.

    Read from headers and nothing else: no sample is decoded, no byte past the
    probe's own bounded read is touched, and every member is a value the
    container wrote down about itself.  They are facts rather than claims --
    none names a target field and none can become a proposal -- and they are
    carried per file rather than per item because a set whose fourth track is
    the odd one out has to be able to say so.

    Exactly the four values something reads.  A fact carried on the protocol
    that nothing composes into an observation is a value the child pays to
    serialize, the report pays to hold, and no owner is ever told, so the MP4
    major brand was dropped rather than kept against a future reader: the
    demuxer family already answers the question it would have been asked.

    ``None`` throughout means the probe stated nothing, which is not the same
    as stating something unexpected: an absent container name is read as an
    absent fact and composes no observation at all.
    """

    container: str | None = None
    codec: str | None = None
    codec_tag: str | None = None
    duration_stated: bool = False

    def __post_init__(self) -> None:
        if type(self.duration_stated) is not bool:
            raise ValueError("audio container facts are malformed")
        for value in (self.container, self.codec, self.codec_tag):
            if value is None:
                continue
            try:
                validate_projection_text(
                    value, "audio container fact", MAX_OBSERVED_CHARACTERS
                )
            except EvidenceValueError as error:
                raise ValueError("audio container facts are malformed") from error

    def to_protocol_data(self) -> dict[str, object]:
        return {
            "codec": self.codec,
            "codec_tag": self.codec_tag,
            "container": self.container,
            "duration_stated": self.duration_stated,
        }

    def __repr__(self) -> str:
        return "AudioContainerFacts(<redacted>)"


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
class AudioSidecarShadow:
    """One newly read Libation value, recorded and never a claim (#1482)."""

    field: AudioSidecarShadowField
    observed: str
    canonical: str | None = None

    def __post_init__(self) -> None:
        if type(self.field) is not AudioSidecarShadowField:
            raise ValueError("audio sidecar shadow is malformed")
        try:
            validate_projection_text(
                self.observed, "audio sidecar shadow", MAX_OBSERVED_CHARACTERS
            )
            if self.canonical is not None:
                validate_projection_text(
                    self.canonical, "audio sidecar shadow", MAX_OBSERVED_CHARACTERS
                )
        except EvidenceValueError as error:
            raise ValueError("audio sidecar shadow is malformed") from error

    def to_data(self) -> dict[str, object]:
        return {
            "canonical": self.canonical,
            "field": self.field.value,
            "observed": self.observed,
        }

    def __repr__(self) -> str:
        return "AudioSidecarShadow(<redacted>)"


def _ordered_members(value: object, vocabulary: type[StrEnum]) -> bool:
    """A tuple of distinct members of one closed vocabulary, in its own order."""
    if type(value) is not tuple or any(type(item) is not vocabulary for item in value):
        return False
    order = list(vocabulary)
    positions = [order.index(item) for item in value]
    return positions == sorted(set(positions))


@dataclass(frozen=True, repr=False)
class AudioSidecarDigest:
    """One recognized document present beside the audio file when it was read.

    The name and digest are a currentness witness rather than evidence: the
    digest names the exact bytes an observation was interpreted from, so a
    document that is added, removed, or edited after publication makes the
    report stale -- including one that was refused.

    The rest is the document's closed, value-free classification (#1481):
    which kind it is and what it is bound to, the refusals that withheld it or
    part of it, the claims it contributed, the fields withheld because
    sidecars disagreed on them or because they repeat the target's own record,
    and the Libation keys read as shadow evidence only.
    """

    name: str
    digest: str
    kind: AudioSidecarKind
    binding: AudioSidecarBinding
    refusals: tuple[AudioSidecarRefusal, ...] = ()
    claims: tuple[str, ...] = ()
    disputed: tuple[AudioInspectionField, ...] = ()
    echoed: tuple[AudioInspectionField, ...] = ()
    shadow: tuple[AudioSidecarShadow, ...] = ()

    def __post_init__(self) -> None:
        if type(self.digest) is not str or _SHA256.fullmatch(self.digest) is None:
            raise ValueError("audio sidecar digest is malformed")
        try:
            validate_projection_text(
                self.name, "audio sidecar name", MAX_SIDECAR_NAME_CHARACTERS
            )
        except EvidenceValueError as error:
            raise ValueError("audio sidecar name is malformed") from error
        if (
            type(self.kind) is not AudioSidecarKind
            or type(self.binding) is not AudioSidecarBinding
            or not _ordered_members(self.refusals, AudioSidecarRefusal)
            or not _ordered_members(self.disputed, AudioInspectionField)
            or not _ordered_members(self.echoed, AudioInspectionField)
            or type(self.shadow) is not tuple
            or len(self.shadow) > MAX_SIDECAR_SHADOW
            or any(type(value) is not AudioSidecarShadow for value in self.shadow)
        ):
            raise ValueError("audio sidecar classification is malformed")
        if (
            type(self.claims) is not tuple
            or len(self.claims) > MAX_FACTS
            or any(
                type(value) is not str or _CLAIM_REFERENCE.fullmatch(value) is None
                for value in self.claims
            )
            or len(set(self.claims)) != len(self.claims)
        ):
            raise ValueError("audio sidecar claim provenance is malformed")
        refusals = set(self.refusals)
        terminal = refusals & TERMINAL_SIDECAR_REFUSALS
        record = self.kind is AudioSidecarKind.AUDIOBOOKSHELF_RECORD
        if (
            # A terminal refusal ends evaluation, so it stands alone and the
            # document contributed nothing.
            (terminal and (len(self.refusals) != 1 or self.claims))
            or (terminal and (self.disputed or self.echoed or self.shadow))
            or record != (AudioSidecarRefusal.TARGET_RECORD in refusals)
            or (record and self.binding is not AudioSidecarBinding.FOLDER)
            or (
                AudioSidecarRefusal.SHARED_FOLDER in refusals
                and self.binding is not AudioSidecarBinding.FOLDER
            )
            or (
                AudioSidecarRefusal.OTHER_FILE in refusals
                and self.kind is not AudioSidecarKind.CUE
            )
            or bool(self.disputed)
            != (AudioSidecarRefusal.SIDECARS_DISAGREE in refusals)
            or not set(self.disputed) <= SIDECAR_FIELDS
            or not set(self.echoed) <= ECHO_FIELDS
            or (
                self.echoed
                and self.kind not in {AudioSidecarKind.OPF, AudioSidecarKind.NFO}
            )
            or (self.shadow and self.kind is not AudioSidecarKind.LIBATION)
        ):
            raise ValueError("audio sidecar classification is inconsistent")

    @property
    def refused(self) -> bool:
        """Whether a refusal ended evaluation, so nothing was taken from it."""
        return bool(set(self.refusals) & TERMINAL_SIDECAR_REFUSALS)

    def to_data(self) -> dict[str, object]:
        return {
            "binding": self.binding.value,
            "claims": list(self.claims),
            "digest": self.digest,
            "disputed": [value.value for value in self.disputed],
            "echoed": [value.value for value in self.echoed],
            "kind": self.kind.value,
            "name": self.name,
            "refusals": [value.value for value in self.refusals],
            "shadow": [value.to_data() for value in self.shadow],
        }

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
        # Only a description is ever cut to its bound, so only a description can
        # be the 512-character prefix an `oversized` fact records (#1250).
        if (
            self.state == "oversized"
            and self.field is not AudioInspectionField.DESCRIPTION
        ):
            raise ValueError("audio inspection state is inconsistent")
        if self.field is AudioInspectionField.LANGUAGE and (
            self.observed_value.casefold() in NON_LINGUISTIC_LANGUAGE_CODES
            or (self.canonical_value or "").casefold() in NON_LINGUISTIC_LANGUAGE_CODES
        ):
            raise ValueError("audio language fact names no language")
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
class AudioSidecarReading:
    """What the child read from one sidecar, kept apart from every other.

    ``facts`` are the document's evidence, which become claims only if the
    parent associates the document with the item.  ``title``, ``creators`` and
    ``file_reference`` (a CUE sheet's first ``FILE`` value, compared as a bare
    string and never opened) are association text: they serve the parent's
    belonging rules and never leave the inspector (#1481).
    """

    name: str
    surface: AudioEvidenceSurface
    facts: tuple[AudioInspectionFact, ...] = ()
    title: str | None = None
    creators: tuple[str, ...] = ()
    file_reference: str | None = None
    shadow: tuple[AudioSidecarShadow, ...] = ()
    # A CUE sheet whose first `FILE` value is not readable association text --
    # a path, a control character, an over-long name -- names no track of the
    # item, so it is recorded as naming another file rather than none.
    file_refused: bool = False

    def __post_init__(self) -> None:
        try:
            validate_projection_text(
                self.name, "audio sidecar name", MAX_SIDECAR_NAME_CHARACTERS
            )
        except EvidenceValueError as error:
            raise ValueError("audio sidecar reading is malformed") from error
        if (
            self.surface not in SIDECAR_KIND_SURFACES.values()
            or type(self.surface) is not AudioEvidenceSurface
            or type(self.facts) is not tuple
            or len(self.facts) > MAX_FACTS
            or any(
                type(value) is not AudioInspectionFact or value.surface != self.surface
                for value in self.facts
            )
            or (self.title is not None and not _valid_association(self.title))
            or not _valid_creators(self.creators)
            or (
                self.file_reference is not None
                and not _valid_association(self.file_reference)
            )
            or (
                self.file_reference is not None
                and self.surface is not AudioEvidenceSurface.SIDECAR_CUE
            )
            or type(self.file_refused) is not bool
            or (
                self.file_refused
                and (
                    self.file_reference is not None
                    or self.surface is not AudioEvidenceSurface.SIDECAR_CUE
                )
            )
            or type(self.shadow) is not tuple
            or len(self.shadow) > MAX_SIDECAR_SHADOW
            or any(type(value) is not AudioSidecarShadow for value in self.shadow)
            or (
                self.shadow
                and self.surface is not AudioEvidenceSurface.SIDECAR_LIBATION
            )
        ):
            raise ValueError("audio sidecar reading is malformed")

    @property
    def kind(self) -> AudioSidecarKind:
        return next(
            kind
            for kind, surface in SIDECAR_KIND_SURFACES.items()
            if surface is self.surface
        )

    def to_data(self) -> dict[str, object]:
        return {
            "creators": list(self.creators),
            "facts": [value.to_data() for value in self.facts],
            "file": self.file_reference,
            "file_refused": self.file_refused,
            "name": self.name,
            "shadow": [value.to_data() for value in self.shadow],
            "surface": self.surface.value,
            "title": self.title,
        }

    def __repr__(self) -> str:
        return "AudioSidecarReading(<redacted>)"


_FACT_KEYS = frozenset(
    {"canonical", "field", "observed", "role", "state", "support", "surface"}
)


def _fact_from_data(item: object, source: Fingerprint | None) -> AudioInspectionFact:
    if type(item) is not dict or set(item) != _FACT_KEYS:
        raise ValueError("audio inspection protocol is invalid")
    # A fact-bearing document always carries a source: `AudioInspectionFact`
    # rejects a `None` source, and the `ValueError` it raises is caught by the
    # caller as an invalid protocol.
    return AudioInspectionFact(
        AudioInspectionField(item["field"]),
        cast("Fingerprint", source),
        item["observed"],
        item["canonical"],
        item["support"],
        AudioEvidenceSurface(item["surface"]),
        item["state"],
        item["role"],
    )


def _shadow_from_data(item: object) -> AudioSidecarShadow:
    if type(item) is not dict or set(item) != {"canonical", "field", "observed"}:
        raise ValueError("audio sidecar shadow is malformed")
    return AudioSidecarShadow(
        AudioSidecarShadowField(item["field"]), item["observed"], item["canonical"]
    )


def _reading_from_data(item: object, source: Fingerprint | None) -> AudioSidecarReading:
    if type(item) is not dict or set(item) != {
        "creators",
        "facts",
        "file",
        "file_refused",
        "name",
        "shadow",
        "surface",
        "title",
    }:
        raise ValueError("audio inspection protocol is invalid")
    if (
        type(item["facts"]) is not list
        or type(item["creators"]) is not list
        or type(item["shadow"]) is not list
    ):
        raise ValueError("audio inspection protocol is invalid")
    return AudioSidecarReading(
        item["name"],
        AudioEvidenceSurface(item["surface"]),
        tuple(_fact_from_data(value, source) for value in item["facts"]),
        item["title"],
        tuple(item["creators"]),
        item["file"],
        tuple(_shadow_from_data(value) for value in item["shadow"]),
        item["file_refused"],
    )


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
    # ``None`` rather than an empty value object: a child that stated no
    # container facts at all and a child that stated a container with nothing
    # unexpected in it are different reports, and only the second one may
    # compose an observation.
    container_facts: AudioContainerFacts | None = None
    chapter_rows_omitted: bool = False
    # Association text and per-sidecar readings (#1481).  The parent decides
    # which sidecars belong to the item from these, and never publishes them:
    # the `album` tag and the person tags serve the belonging rules only.
    album: str | None = None
    creators: tuple[str, ...] = ()
    sidecars: tuple[AudioSidecarReading, ...] = ()

    def __post_init__(self) -> None:
        if (
            (self.album is not None and not _valid_association(self.album))
            or not _valid_creators(self.creators)
            or type(self.sidecars) is not tuple
            or len(self.sidecars) > AudioInspectionLimits.HARD_CAPS["sidecar_documents"]
            or any(type(value) is not AudioSidecarReading for value in self.sidecars)
            or len({value.name for value in self.sidecars}) != len(self.sidecars)
            or len(self.facts) + sum(len(value.facts) for value in self.sidecars)
            > MAX_FACTS
        ):
            raise ValueError("audio inspection is malformed")
        if self.source is not None and any(
            fact.source != self.source
            for value in self.sidecars
            for fact in value.facts
        ):
            raise ValueError("audio inspection fact source is inconsistent")
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
            or type(self.chapter_rows_omitted) is not bool
            or (
                self.container_facts is not None
                and type(self.container_facts) is not AudioContainerFacts
            )
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
            or self.chapter_rows_omitted
            or self.title is not None
            or self.container_facts is not None
            or self.album is not None
            or self.creators
            or self.sidecars
        ):
            raise ValueError("failed audio inspection cannot retain evidence")

    def to_json(self) -> str:
        """Canonical closed protocol payload for the isolated child boundary."""
        return json.dumps(
            {
                "album": self.album,
                "chapters": [value.to_data() for value in self.chapters],
                "chapters_truncated": self.chapters_truncated,
                "chapter_rows_omitted": self.chapter_rows_omitted,
                "container_facts": None
                if self.container_facts is None
                else self.container_facts.to_protocol_data(),
                "creators": list(self.creators),
                "embedded_cover": self.embedded_cover.to_protocol_data(),
                "facts": [value.to_data() for value in self.facts],
                "outcome": self.outcome.value,
                "schema": AUDIO_INSPECTION_SCHEMA,
                "sidecars": [value.to_data() for value in self.sidecars],
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
                    "album",
                    "chapters",
                    "chapters_truncated",
                    "chapter_rows_omitted",
                    "container_facts",
                    "creators",
                    "embedded_cover",
                    "facts",
                    "outcome",
                    "schema",
                    "sidecars",
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
            container_data = value["container_facts"]
            if container_data is not None and (
                type(container_data) is not dict
                or set(container_data)
                != {"codec", "codec_tag", "container", "duration_stated"}
            ):
                raise ValueError
            facts = tuple(
                _fact_from_data(item, source)
                for item in value["facts"]
                if type(item) is dict and set(item) == _FACT_KEYS
            )
            if (
                type(value["creators"]) is not list
                or type(value["sidecars"]) is not list
            ):
                raise ValueError
            sidecars = tuple(
                _reading_from_data(item, source) for item in value["sidecars"]
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
                None
                if container_data is None
                else AudioContainerFacts(
                    container_data["container"],
                    container_data["codec"],
                    container_data["codec_tag"],
                    container_data["duration_stated"],
                ),
                value["chapter_rows_omitted"],
                value["album"],
                tuple(value["creators"]),
                sidecars,
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("audio inspection protocol is invalid") from None
        if result.to_json() != serialized:
            raise ValueError("audio inspection protocol is not canonical")
        return result

    def __repr__(self) -> str:
        return "AudioInspection(<redacted>)"


__all__ = [
    "ADMITTED_AUDIO_CODECS",
    "ADMITTED_AUDIO_CONTAINERS",
    "AUDIO_CLAIM_BASES",
    "AUDIO_INSPECTION_SCHEMA",
    "AUDIO_SURFACE_BASIS",
    "MOV_CONTAINER_FAMILY",
    "MP3_CONTAINER_FAMILY",
    "PROTECTED_SAMPLE_ENTRY_TAGS",
    "AudioChapter",
    "AudioChapterComposition",
    "AudioContainerFacts",
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
    "AudioSidecarBinding",
    "AudioSidecarDigest",
    "AudioSidecarKind",
    "AudioSidecarReading",
    "AudioSidecarRefusal",
    "AudioSidecarShadow",
    "AudioSidecarShadowField",
    "AudioTrackDigest",
    "ECHO_FIELDS",
    "MAX_ASSOCIATION_CHARACTERS",
    "MAX_ASSOCIATION_CREATORS",
    "PARENT_SIDECAR_REFUSALS",
    "SIDECAR_FIELDS",
    "SIDECAR_KIND_SURFACES",
    "TERMINAL_SIDECAR_REFUSALS",
    "association_text",
    "MAX_AUDIO_FILE_RECORDS",
    "MAX_AUDIO_TRACKS",
    "MAX_CHAPTERS",
    "MAX_PUBLIC_REPORT_BYTES",
    "MAX_SIDECAR_NAME_CHARACTERS",
    "MULTI_FILE_WALL_SECONDS",
    "NON_LINGUISTIC_LANGUAGE_CODES",
    "TrackNameProjection",
    "normalize_duration_seconds",
    "project_track_name",
    "project_track_name_detail",
    "unnamed_track_name",
]
