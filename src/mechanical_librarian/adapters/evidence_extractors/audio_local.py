"""Interpretation of one bounded audiobook probe, its sidecars and its path.

Everything here is pure: it is handed already-bounded text -- a probe document,
sidecar documents, and two path components -- and returns closed domain values.
No byte of it reaches a filesystem, a socket, or a subprocess.  The isolated
child in ``bootstrap.audio_child`` is what runs it, exactly as the EPUB child
runs ``epub_local``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from mechanical_librarian.adapters.providers.visible_text import stated_description
from mechanical_librarian.domain.audio_inspection import (
    MAX_ASSOCIATION_CREATORS,
    MAX_CHAPTERS,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_DURATION_MILLISECONDS,
    MAX_FACTS,
    MAX_OBSERVED_CHARACTERS,
    MAX_SIDECAR_SHADOW,
    NON_LINGUISTIC_LANGUAGE_CODES,
    AudioChapter,
    AudioContainerFacts,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioInspectionFact,
    AudioInspectionField,
    AudioInspectionLimits,
    AudioInspectionOutcome,
    AudioPerformanceType,
    AudioSidecarBinding,
    AudioSidecarKind,
    AudioSidecarReading,
    AudioSidecarShadow,
    AudioSidecarShadowField,
    association_file_reference,
    association_text,
    normalize_duration_seconds,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.identifiers import Fingerprint

# The whole shape of an ASIN: ten ASCII alphanumerics, and nothing else.  It is
# a well-formedness gate, never a filter and never prefix-based -- an
# ISBN-10-shaped ASIN is as well-formed as a `B0` one, and a value that fails
# it is retained as malformed rather than discarded.  Both cases are admitted
# here because the value is matched before it is upper-cased: `"ß".upper()` is
# `"SS"`, so a nine-character value tested after conversion would have been
# grown into a well-formed ten-character one that no file ever held.
_ASIN_TOKEN = re.compile(r"[A-Za-z0-9]{10}")
# The same shape bracketed is the one path grammar Audible exports and every
# downstream tool reproduces.
_BRACKETED_TOKEN = re.compile(r"\[([A-Z0-9]{10})\]")
_TONE_NAMESPACE = "com.pilabor.tone"
_TRUE_VALUES = frozenset({"1", "true", "yes", "y"})
_FALSE_VALUES = frozenset({"0", "false", "no", "n"})
_SEQUENCE = re.compile(r"^\d{1,4}(?:\.\d{1,3})?$")
_CHAPTER_SECONDS = re.compile(r"^\d{1,9}(?:\.\d{1,9})?$")
# The head of one recognised OPF element, matched anchored at a `<` and never
# searched: the prefix run is bounded by the next character no name can hold,
# so each attempt costs the name it reads, and names never overlap (#1481).
_XML_ELEMENT_HEAD = re.compile(
    r"<(?:[A-Za-z][\w.-]*:)?(language|description|title|creator|contributor)\b",
    re.IGNORECASE,
)
# One `name="value"` pair of a captured attribute run, consumed in sequence so
# an attribute is recognised by its name rather than found anywhere in the run.
# A value is text the file chose: `title="fake role=\'nrt\'"` declares a title,
# and searching the run for a role would have read it as one.
_XML_ATTRIBUTE = re.compile(
    r"\s*([A-Za-z_:][\w.:-]{0,63})\s*=\s*(\"[^\"]{0,512}\"|\'[^\']{0,512}\')"
)
# The run itself is unbounded in the element grammar, so the walk over it is
# not: no OPF element carries this many attributes.
_MAX_XML_ATTRIBUTES = 32
# MARC relators for a spoken performance.  An OPF contributor is a contributor
# of some kind; only these two say the kind is the one this evidence claims.
_NARRATOR_ROLES = frozenset({"nrt", "prf"})
# A `<meta>` is read only when a double-quoted `name` precedes a double-quoted
# `content`.  Each fragment is matched anchored at a candidate position found by
# a literal scan, never searched across the tag (#1481).
_XML_META_HEAD = re.compile(r"<meta\b", re.IGNORECASE)
_XML_META_NAME_WORD = re.compile(r"name", re.IGNORECASE)
_XML_META_NAME = re.compile(r"\bname\s*=\s*\"([\w:.-]{1,64})\"", re.IGNORECASE)
_XML_META_CONTENT_WORD = re.compile(r"content", re.IGNORECASE)
_XML_META_CONTENT = re.compile(r"\bcontent\s*=\s*\"([^\"]{0,512})\"", re.IGNORECASE)
# The scanner's own bounds: an element or meta tag must close within this many
# characters of its name, a name is at most 64 characters, element text runs at
# most this far, and at most 64 matches are read per grammar.
_MAX_XML_TAG_CHARACTERS = 4_096
_MAX_XML_NAME_CHARACTERS = 64
_MAX_XML_MATCHES = 64
_NFO_LINE = re.compile(r"^\s*([A-Za-z][\w .-]{0,63})\s*:\s*(.{1,512})$")
_CUE_LINE = re.compile(r"^\s*(REM\s+[A-Z_]{1,32}|PERFORMER|TITLE)\s+(.{1,512})$")
_XML_ENTITIES = (
    ("&lt;", "<"),
    ("&gt;", ">"),
    ("&quot;", '"'),
    ("&apos;", "'"),
    ("&amp;", "&"),
)

# ID3v2 frames ffprobe maps onto its own well-known keys.  Anything else in an
# MP3's format tags reached ffprobe through a `TXXX` user-defined frame, which
# is a different surface and is named as one.
_ID3_WELL_KNOWN = frozenset(
    {
        "album",
        "album_artist",
        "artist",
        "comment",
        "composer",
        "copyright",
        "date",
        "disc",
        "encoder",
        "genre",
        "language",
        "publisher",
        "title",
        "track",
    }
)

# One closed tag grammar, applied after `_normalized_key`.  Series and sequence
# deliberately never read `album`: for a single-file audiobook that key carries
# the book title, and reading it as a series name would manufacture a series
# for every unserialised work.
_TAG_FIELDS: dict[str, AudioInspectionField] = {
    "asin": AudioInspectionField.ASIN,
    "audible_asin": AudioInspectionField.ASIN,
    "cdek": AudioInspectionField.ASIN,
    "narrator": AudioInspectionField.CONTRIBUTOR,
    "narrators": AudioInspectionField.CONTRIBUTOR,
    "narrated_by": AudioInspectionField.CONTRIBUTOR,
    "composer": AudioInspectionField.CONTRIBUTOR,
    "series": AudioInspectionField.SERIES,
    "show": AudioInspectionField.SERIES,
    "movementname": AudioInspectionField.SERIES,
    "series-part": AudioInspectionField.SERIES_SEQUENCE,
    "series_part": AudioInspectionField.SERIES_SEQUENCE,
    "part": AudioInspectionField.SERIES_SEQUENCE,
    "movement": AudioInspectionField.SERIES_SEQUENCE,
    "language": AudioInspectionField.LANGUAGE,
    "description": AudioInspectionField.DESCRIPTION,
    "synopsis": AudioInspectionField.DESCRIPTION,
    "comment": AudioInspectionField.DESCRIPTION,
    "longdescription": AudioInspectionField.DESCRIPTION,
}
# The MP4 metadata keys ffmpeg has its own name for.  Everything else in an
# MP4's exported tags is an atom it could not name, which is what
# `_surface_for` distinguishes.
_MP4_WELL_KNOWN = frozenset(
    {
        "album",
        "album_artist",
        "artist",
        "comment",
        "compilation",
        "composer",
        "copyright",
        "date",
        "description",
        "disc",
        "encoder",
        "episode_id",
        "episode_sort",
        "gapless_playback",
        "genre",
        "grouping",
        "hd_video",
        "lyrics",
        "media_type",
        "performer",
        "publisher",
        "purchase_date",
        "season_number",
        "show",
        "sort_album",
        "sort_album_artist",
        "sort_artist",
        "sort_composer",
        "sort_name",
        "sort_show",
        "synopsis",
        "title",
        "track",
    }
)
_ABRIDGED_KEYS = frozenset({"abridged", "isabridged"})
_PERFORMANCE_KEYS = frozenset({"performancetype", "performance_type", "narratortype"})


@dataclass(frozen=True, slots=True)
class AudioSidecar:
    """One bounded sidecar document, already decoded by the isolated child."""

    surface: AudioEvidenceSurface
    name: str
    text: str


@dataclass(frozen=True, slots=True)
class AudioPathContext:
    """The two path components the bracketed-token grammar may read.

    Components, never a path: a joined path is exactly what the evidence bounds
    refuse to project, and a single name carries all the grammar needs.
    """

    file: str
    folder: str | None = None


@dataclass(frozen=True, slots=True)
class AudioInterpretation:
    outcome: AudioInspectionOutcome
    facts: tuple[AudioInspectionFact, ...] = ()
    chapters: tuple[AudioChapter, ...] = ()
    embedded_cover: AudioEmbeddedCover = AudioEmbeddedCover(False)
    chapters_truncated: bool = False
    container_facts: AudioContainerFacts | None = None
    # What the container calls this recording.  Deliberately not a fact: a
    # title claim from every track of a multi-file item would flood the title
    # lane with the same value, and the report already reads its title from the
    # target.  It exists so a per-track chapter can be named by the recording.
    title: str | None = None
    chapter_rows_omitted: bool = False
    # Association text and per-sidecar readings (#1481): the `album` tag, the
    # person tags, and each sidecar's own facts and association text, kept
    # apart so the parent can decide which documents belong to the item.
    album: str | None = None
    creators: tuple[str, ...] = ()
    sidecars: tuple[AudioSidecarReading, ...] = ()

    def __post_init__(self) -> None:
        if type(self.chapter_rows_omitted) is not bool:
            raise ValueError("audio chapter omission is invalid")


@dataclass(frozen=True, slots=True)
class SidecarClassification:
    """One recognised document's kind, binding and, when bound, its track.

    ``track`` is the 1-based position in the item's track names of the track a
    document is named for, and ``None`` for a folder-level document.
    """

    kind: AudioSidecarKind
    binding: AudioSidecarBinding
    track: int | None = None


_RECORD_NAMES = frozenset({"metadata.json", "metadata.abs"})
_LIBATION_SUFFIX = ".metadata.json"
_FOLDER_KINDS = {
    ".opf": AudioSidecarKind.OPF,
    ".nfo": AudioSidecarKind.NFO,
    ".cue": AudioSidecarKind.CUE,
}


def classify_sidecar(
    name: str, media_names: tuple[str, ...]
) -> SidecarClassification | None:
    """Classify one directory entry as a recognised document, or not at all.

    Audiobookshelf's own ``metadata.json`` and ``metadata.abs`` are its record.
    A document whose name less its extension is a track's name less its
    extension is bound to that track; Libation's ``<stem>.metadata.json`` is
    recognised only so.  Every other ``.opf``, ``.nfo`` and ``.cue`` belongs to
    the folder (#1481).
    """
    if (
        type(name) is not str
        or "/" in name
        or type(media_names) is not tuple
        or any(type(value) is not str for value in media_names)
    ):
        return None
    lowered = name.lower()
    stems = tuple(value.rsplit(".", 1)[0].lower() for value in media_names)
    if lowered in _RECORD_NAMES:
        return SidecarClassification(
            AudioSidecarKind.AUDIOBOOKSHELF_RECORD, AudioSidecarBinding.FOLDER
        )
    if lowered.endswith(_LIBATION_SUFFIX):
        stem = lowered[: -len(_LIBATION_SUFFIX)]
        return (
            SidecarClassification(
                AudioSidecarKind.LIBATION,
                AudioSidecarBinding.TRACK,
                stems.index(stem) + 1,
            )
            if stem in stems
            else None
        )
    kind = _FOLDER_KINDS.get(lowered[-4:])
    if kind is None:
        return None
    stem = lowered[:-4]
    if stem in stems:
        return SidecarClassification(
            kind, AudioSidecarBinding.TRACK, stems.index(stem) + 1
        )
    return SidecarClassification(kind, AudioSidecarBinding.FOLDER)


def interpret(
    probe_text: str,
    sidecars: tuple[AudioSidecar, ...],
    path: AudioPathContext,
    source: Fingerprint,
    limits: AudioInspectionLimits,
) -> AudioInterpretation:
    """Turn one bounded probe document and its context into closed evidence."""
    try:
        probe = json.loads(probe_text)
    except (RecursionError, ValueError):
        return AudioInterpretation(AudioInspectionOutcome.MALFORMED)
    if type(probe) is not dict:
        return AudioInterpretation(AudioInspectionOutcome.MALFORMED)
    container = probe.get("format")
    if type(container) is not dict:
        return AudioInterpretation(AudioInspectionOutcome.UNSUPPORTED_FORMAT)
    streams = probe.get("streams")
    if type(streams) is not list or len(streams) > limits.streams:
        return AudioInterpretation(AudioInspectionOutcome.MALFORMED)
    if not any(
        type(stream) is dict and stream.get("codec_type") == "audio"
        for stream in streams
    ):
        return AudioInterpretation(AudioInspectionOutcome.UNSUPPORTED_FORMAT)

    builder = _Builder(source, limits)
    tag_surface = _tag_surface_for(container)
    builder.duration(container.get("duration"))
    builder.stream_language(streams)
    _tag_facts(builder, container.get("tags"), tag_surface, limits)
    builder.bracketed(path)
    facts = builder.result()
    # Each sidecar is read on its own, under the one fact budget the whole
    # child answer shares, so the parent can keep or drop a document whole.
    readings: list[AudioSidecarReading] = []
    budget = MAX_FACTS - len(facts)
    for sidecar in sidecars[: limits.sidecar_documents]:
        reading = _sidecar_reading(sidecar, source, limits, budget)
        if reading is None:
            continue
        budget -= len(reading.facts)
        readings.append(reading)
    chapters, truncated, omitted = _chapters(probe.get("chapters"), limits)
    cover = _embedded_cover(streams)
    album, creators = _file_association(container.get("tags"), limits)
    outcome = (
        AudioInspectionOutcome.SUCCESS
        if facts or chapters or cover.present or any(r.facts for r in readings)
        else AudioInspectionOutcome.NO_APPLICABLE_EVIDENCE
    )
    return AudioInterpretation(
        outcome,
        facts,
        chapters,
        cover,
        truncated,
        _container_facts(container, streams),
        _container_title(container),
        omitted,
        album,
        creators,
        tuple(readings),
    )


# The tags whose values name a person, read only as association text.
_PERSON_TAG_KEYS = frozenset(
    {"artist", "album_artist", "composer", "narrator", "narrators", "narrated_by"}
)


def _file_association(
    tags: object, limits: AudioInspectionLimits
) -> tuple[str | None, tuple[str, ...]]:
    """Read the file's own association text: its `album` tag and person tags."""
    if type(tags) is not dict:
        return None, ()
    album: str | None = None
    creators: list[str] = []
    for index, (raw_key, value) in enumerate(sorted(tags.items())):
        if index >= limits.tags:
            break
        if type(raw_key) is not str or type(value) is not str:
            continue
        if len(value) > limits.tag_value_characters:
            continue
        key, _mean = _normalized_key(raw_key)
        text = association_text(value)
        if text is None:
            continue
        if key == "album" and album is None:
            album = text
        elif (
            key in _PERSON_TAG_KEYS
            and text not in creators
            and len(creators) < MAX_ASSOCIATION_CREATORS
        ):
            creators.append(text)
    return album, tuple(creators)


class _Builder:
    """Collect facts in one deterministic order without duplicating a surface."""

    def __init__(
        self,
        source: Fingerprint,
        limits: AudioInspectionLimits,
        budget: int = MAX_FACTS,
    ) -> None:
        self._source = source
        self._limits = limits
        self._budget = max(0, min(budget, limits.MAX_FACTS))
        self._seen: set[tuple[str, str, str, str | None, str]] = set()
        self._facts: list[AudioInspectionFact] = []

    def add(
        self,
        field: AudioInspectionField,
        surface: AudioEvidenceSurface,
        observed: object,
        *,
        key: str | None = None,
        canonical: str | None = None,
        role: str | None = None,
    ) -> None:
        text = _clean(observed)
        if text is None:
            return
        state = "valid"
        if field is AudioInspectionField.LANGUAGE and (
            text.casefold() in NON_LINGUISTIC_LANGUAGE_CODES
        ):
            return
        if field is AudioInspectionField.DESCRIPTION:
            bounded = bounded_description(text, self._limits)
            if bounded is None:
                return
            text, state = bounded
        support = text if key is None else _support(key, text, _text_bound(field))
        resolved: str | None = canonical if canonical is not None else text
        if state == "oversized":
            resolved = None
        elif field is AudioInspectionField.DESCRIPTION:
            # Read once, as every description is (#1424).
            shown = stated_description(text)
            state, resolved = (
                ("valid", shown) if type(shown) is str else ("malformed", None)
            )
        elif field is AudioInspectionField.ASIN:
            # Every surface that can carry an ASIN arrives here, so the shape
            # is checked once.  A value that is not one is still what the file
            # said: it is kept, marked malformed, and given no canonical form
            # for anything downstream to match on.
            if _ASIN_TOKEN.fullmatch(text) is None:
                state, resolved = "malformed", None
            else:
                # ASCII by construction above, so this cannot change a length.
                resolved = text.upper()
        identity = (field.value, surface.value, text, role, state)
        if identity in self._seen:
            return
        try:
            fact = AudioInspectionFact(
                field, self._source, text, resolved, support, surface, state, role
            )
        except ValueError:
            return
        self._seen.add(identity)
        self._facts.append(fact)

    def duration(self, value: object) -> None:
        if type(value) is not str:
            return
        try:
            canonical = normalize_duration_seconds(value)
        except ValueError:
            return
        self.add(
            AudioInspectionField.DURATION,
            AudioEvidenceSurface.CONTAINER_FORMAT,
            value,
            canonical=canonical,
        )

    def stream_language(self, streams: list[object]) -> None:
        for stream in streams:
            if type(stream) is not dict or stream.get("codec_type") != "audio":
                continue
            tags = stream.get("tags")
            if type(tags) is dict:
                self.add(
                    AudioInspectionField.LANGUAGE,
                    AudioEvidenceSurface.CONTAINER_STREAM,
                    tags.get("language"),
                    key="language",
                )
            return

    def bracketed(self, path: AudioPathContext) -> None:
        if type(path) is not AudioPathContext:
            return
        for surface, name in (
            (AudioEvidenceSurface.PATH_FOLDER, path.folder),
            (AudioEvidenceSurface.PATH_FILE, path.file),
        ):
            if type(name) is not str:
                continue
            for token in _BRACKETED_TOKEN.findall(name)[:4]:
                self.add(AudioInspectionField.ASIN, surface, token, key="name")

    def performance(
        self, surface: AudioEvidenceSurface, key: str, value: object
    ) -> None:
        resolved = _performance_type(key, value)
        if resolved is None:
            return
        observed, canonical = resolved
        self.add(
            AudioInspectionField.PERFORMANCE_TYPE,
            surface,
            observed,
            key=key,
            canonical=canonical,
        )

    def result(self) -> tuple[AudioInspectionFact, ...]:
        return tuple(self._facts[: self._budget])


def bounded_description(
    value: object, limits: AudioInspectionLimits
) -> tuple[str, str] | None:
    """Return the description text a fact may carry, and the state it carries it in.

    A description within the 512-character bound is returned whole and
    ``valid``.  Whitespace normalization runs first, so a control it folds --
    a tab, a line break, NEL, U+2028 or U+2029 -- is already a space.  A
    longer value is then validated whole -- so a credential, a path or any
    other forbidden codepoint anywhere in it still refuses the value rather
    than being cut off -- and only then reduced to its first 512 code points,
    in the ``oversized`` state.  That state is the record of the cut: the
    prefix is evidence that a description exists, never a value to propose or
    write, which is why it carries no canonical form (#1250).  A value past
    the configured tag width, or one that fails validation, is ``None``.

    Pure, so the same rule is reachable without a probe document.
    """
    text = _clean(value)
    if text is None or len(text) <= MAX_DESCRIPTION_CHARACTERS:
        return None if text is None else (text, "valid")
    if len(text) > limits.tag_value_characters:
        return None
    try:
        validate_projection_text(text, "audio description", limits.tag_value_characters)
    except EvidenceValueError:
        return None
    return text[:MAX_DESCRIPTION_CHARACTERS].rstrip(), "oversized"


def _text_bound(field: AudioInspectionField) -> int:
    return (
        MAX_DESCRIPTION_CHARACTERS
        if field is AudioInspectionField.DESCRIPTION
        else MAX_OBSERVED_CHARACTERS
    )


def _support(key: str, observed: str, maximum: int) -> str:
    """Name the key an observation was read under, when there is room for it.

    The observation itself is what the fact exists to carry, so a key that
    would push the excerpt past its bound is dropped rather than allowed to
    take the whole fact down with it.
    """
    candidate = f"{key}: {observed}"
    if len(candidate) > maximum:
        return observed
    try:
        validate_projection_text(candidate, "audio support", maximum)
    except EvidenceValueError:
        return observed
    return candidate


def _clean(value: object) -> str | None:
    if type(value) is int and type(value) is not bool:
        value = str(value)
    if type(value) is not str:
        return None
    text = " ".join(value.split())
    return text or None


def _container_facts(
    container: dict[str, object], streams: list[object]
) -> AudioContainerFacts:
    """State what this file's headers said about its own container and stream.

    Headers only.  Every value here is one the probe read out of the container
    without decoding a sample, and each is reported exactly as the container
    wrote it -- an unrecognised codec name is carried through rather than
    corrected, because the point of the fact is to let the parent say the
    container stated something this build did not expect.  Four values and no
    more: a fact nothing composes into an observation would be carried at every
    layer and read by none.

    ``duration_stated`` is about the *container*, not about the value: a
    container that wrote down a running time has stated one even when the
    number it wrote is outside what the domain will hold, and the two are
    different things to tell an owner.

    The first audio stream is the one described.  A file whose second stream is
    an attached picture says nothing about its audio, and an item whose tracks
    differ is already described one file at a time.
    """
    names = container.get("format_name")
    stream = next(
        (
            value
            for value in streams
            if type(value) is dict and value.get("codec_type") == "audio"
        ),
        None,
    )
    return AudioContainerFacts(
        _clean(names),
        None if stream is None else _clean(stream.get("codec_name")),
        None if stream is None else _clean(stream.get("codec_tag_string")),
        "duration" in container,
    )


def _tag_surface_for(container: dict[str, object]) -> AudioEvidenceSurface:
    names = container.get("format_name")
    if type(names) is str and "mp3" in names.split(","):
        return AudioEvidenceSurface.ID3_FRAME
    return AudioEvidenceSurface.MP4_ILST


def _normalized_key(key: str) -> tuple[str, str | None]:
    """Reduce one exported tag key, and name the freeform mean it arrived under.

    ``-export_all`` spells an MP4 freeform atom as ``----:<mean>:<name>``, so
    the trailing component is the tag a writer intended and the mean is what
    identifies the writer.  Everything else is a well-known key, reported with
    no mean at all; a freeform atom whose export omitted the mean is reported
    with an empty one, because the atom is still freeform and the writer is
    still unknown.
    """
    lowered = key.strip().lower()
    if not lowered.startswith("----:"):
        return lowered.replace("©", ""), None
    parts = lowered.split(":")
    return parts[-1], parts[1] if len(parts) >= 3 else ""


def _tag_facts(
    builder: _Builder,
    tags: object,
    default_surface: AudioEvidenceSurface,
    limits: AudioInspectionLimits,
) -> None:
    if type(tags) is not dict:
        return
    for index, (raw_key, value) in enumerate(sorted(tags.items())):
        if index >= limits.tags:
            break
        if type(raw_key) is not str:
            continue
        if type(value) is str and len(value) > limits.tag_value_characters:
            continue
        key, mean = _normalized_key(raw_key)
        surface = _surface_for(key, mean, default_surface)
        if key in _ABRIDGED_KEYS or key in _PERFORMANCE_KEYS:
            builder.performance(surface, key, value)
            continue
        field = _TAG_FIELDS.get(key)
        if field is None:
            continue
        if field is AudioInspectionField.SERIES_SEQUENCE and not _is_sequence(value):
            continue
        builder.add(
            field,
            surface,
            value,
            key=key,
            role="narrator" if field is AudioInspectionField.CONTRIBUTOR else None,
        )


def _surface_for(
    key: str, mean: str | None, default_surface: AudioEvidenceSurface
) -> AudioEvidenceSurface:
    """Name the surface one exported key actually came from.

    ffprobe flattens every container's metadata into one ``tags`` object and
    does not say which box or frame a key arrived in, so the surface is
    reconstructed from what only that surface can produce.

    For MP3, a key outside ffmpeg's own ID3 mapping reached the document
    through a ``TXXX`` user-defined frame, which is the one way an arbitrary
    description becomes a key.

    For MP4, a key outside ffmpeg's mapping is an atom it has no name for, and
    ``-export_all`` exported it verbatim.  A raw ilst atom is a four-character
    type -- ``CDEK`` -- so a longer unmapped key can only have come
    from a ``----`` freeform atom.  Which writer emitted it is a separate
    question: only an explicit ``com.pilabor.tone`` mean answers it, so a
    freeform atom under any other mean, or under none the export spelled out,
    is named as the freeform atom it is and attributed to nobody.
    """
    if mean is not None:
        return (
            AudioEvidenceSurface.MP4_FREEFORM_TONE
            if mean == _TONE_NAMESPACE
            else AudioEvidenceSurface.MP4_FREEFORM
        )
    if default_surface is AudioEvidenceSurface.ID3_FRAME:
        return (
            AudioEvidenceSurface.ID3_FRAME
            if key in _ID3_WELL_KNOWN
            else AudioEvidenceSurface.ID3_USER_TEXT
        )
    if key in _MP4_WELL_KNOWN or len(key) <= 4:
        return AudioEvidenceSurface.MP4_ILST
    return AudioEvidenceSurface.MP4_FREEFORM


def _is_sequence(value: object) -> bool:
    text = _clean(value)
    return text is not None and _SEQUENCE.fullmatch(text) is not None


def _performance_type(key: str, value: object) -> tuple[str, str] | None:
    text = _clean(value)
    if text is None:
        return None
    lowered = text.lower()
    if key in _ABRIDGED_KEYS:
        if lowered in _TRUE_VALUES:
            return text, AudioPerformanceType.ABRIDGED.value
        if lowered in _FALSE_VALUES:
            return text, AudioPerformanceType.UNABRIDGED.value
        return None
    normalized = lowered.replace(" ", "_").replace("-", "_")
    if normalized in {value.value for value in AudioPerformanceType}:
        return text, normalized
    return None


def _chapters(
    value: object, limits: AudioInspectionLimits
) -> tuple[tuple[AudioChapter, ...], bool, bool]:
    if type(value) is not list:
        return (), False, False
    cap = min(limits.chapters, MAX_CHAPTERS)
    chapters: list[AudioChapter] = []
    omitted = False
    for item in value[:cap]:
        if type(item) is not dict:
            omitted = True
            continue
        start = _milliseconds(item.get("start_time"))
        end = _milliseconds(item.get("end_time"))
        if start is None or end is None or end < start:
            omitted = True
            continue
        tags = item.get("tags")
        title = _clean(tags.get("title")) if type(tags) is dict else None
        try:
            chapters.append(AudioChapter(len(chapters) + 1, start, end, _title(title)))
        except ValueError:
            omitted = True
            continue
    return tuple(chapters), len(value) > cap, omitted


def _container_title(container: dict[str, object]) -> str | None:
    """Read what the container calls this file, projected or not at all.

    A track title is text the file chose, so it is bounded and projected here
    exactly as an observed value is; what fails projection is simply absent and
    the composition falls back to the exported file name.

    The whole normalized text is projected, never a prefix of it: a title cut
    to the bound would name the chapter something the file does not say, and
    an invented title is worse evidence than the exported file name the
    composition falls back to.
    """
    tags = container.get("tags")
    if type(tags) is not dict:
        return None
    text = _clean(tags.get("title"))
    if text is None:
        return None
    try:
        return validate_projection_text(
            text, "audio track title", MAX_OBSERVED_CHARACTERS
        )
    except EvidenceValueError:
        return None


def _title(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        validate_projection_text(value, "audio chapter title", len(value) or 1)
    except EvidenceValueError:
        return None
    return value[:280]


def _milliseconds(value: object) -> int | None:
    """Read one chapter boundary, which -- unlike a duration -- may be zero."""
    if type(value) is not str or _CHAPTER_SECONDS.fullmatch(value.strip()) is None:
        return None
    whole, _, fraction = value.strip().partition(".")
    total = int(whole) * 1000 + int((fraction + "000")[:3])
    return total if 0 <= total <= MAX_DURATION_MILLISECONDS else None


def _embedded_cover(streams: list[object]) -> AudioEmbeddedCover:
    for stream in streams:
        if type(stream) is not dict:
            continue
        disposition = stream.get("disposition")
        attached = type(disposition) is dict and disposition.get("attached_pic") == 1
        if attached or (
            stream.get("codec_type") == "video"
            and stream.get("codec_name") in {"mjpeg", "png", "bmp", "webp"}
        ):
            return AudioEmbeddedCover(True, AudioEvidenceSurface.CONTAINER_STREAM)
    return AudioEmbeddedCover(False)


def _sidecar_reading(
    sidecar: AudioSidecar,
    source: Fingerprint,
    limits: AudioInspectionLimits,
    budget: int,
) -> AudioSidecarReading | None:
    """Read one sidecar's facts and association text, apart from every other."""
    if type(sidecar) is not AudioSidecar:
        return None
    builder = _Builder(source, limits, budget)
    text = _DocumentText()
    if sidecar.surface is AudioEvidenceSurface.SIDECAR_LIBATION:
        _libation_facts(builder, text, sidecar)
    elif sidecar.surface is AudioEvidenceSurface.SIDECAR_OPF:
        _opf_sidecar_facts(builder, text, sidecar)
    elif sidecar.surface is AudioEvidenceSurface.SIDECAR_NFO:
        _line_sidecar_facts(builder, text, sidecar, _NFO_LINE, limits)
    elif sidecar.surface is AudioEvidenceSurface.SIDECAR_CUE:
        _line_sidecar_facts(builder, text, sidecar, _CUE_LINE, limits)
    else:
        return None
    try:
        return AudioSidecarReading(
            sidecar.name,
            sidecar.surface,
            builder.result(),
            text.title,
            tuple(text.creators),
            text.file_reference,
            tuple(text.shadow),
            text.file_refused,
        )
    except ValueError:
        return None


class _DocumentText:
    """One document's association text and shadow values, as it is read."""

    def __init__(self) -> None:
        self.title: str | None = None
        self.creators: list[str] = []
        self.file_reference: str | None = None
        self.file_refused = False
        self.shadow: list[AudioSidecarShadow] = []

    def name_title(self, value: object) -> None:
        if self.title is None:
            self.title = association_text(value)

    def add_creator(self, value: object) -> None:
        text = association_text(value)
        if (
            text is not None
            and text not in self.creators
            and len(self.creators) < MAX_ASSOCIATION_CREATORS
        ):
            self.creators.append(text)

    def add_shadow(self, field: AudioSidecarShadowField, value: object) -> None:
        observed = _clean(value)
        if observed is None or len(self.shadow) >= MAX_SIDECAR_SHADOW:
            return
        canonical: str | None = observed
        if field is AudioSidecarShadowField.FORMAT_TYPE:
            normalized = observed.lower().replace(" ", "_").replace("-", "_")
            canonical = (
                normalized
                if normalized in {member.value for member in AudioPerformanceType}
                else None
            )
        try:
            entry = AudioSidecarShadow(field, observed, canonical)
        except ValueError:
            return
        if entry not in self.shadow:
            self.shadow.append(entry)


# The JSON keys a Libation export carries that this build reads as claims,
# after `_libation_key`.  Libation writes the Audible catalogue product, so a
# key it never writes is not read (#1481).
_LIBATION_CLAIM_KEYS: dict[str, AudioInspectionField] = {
    "asin": AudioInspectionField.ASIN,
    "language": AudioInspectionField.LANGUAGE,
    "publishersummary": AudioInspectionField.DESCRIPTION,
}
_MAX_JSON_VALUES = 16


def _libation_key(raw_key: str) -> str:
    return raw_key.strip().lower().replace("_", "")


def _libation_facts(
    builder: _Builder, text: _DocumentText, sidecar: AudioSidecar
) -> None:
    """Read what a Libation export carries, and nothing it does not.

    Claims: `asin`, `narrators[].name`, `language`, `publisher_summary` and
    `series[].sequence`.  Shadow only, until #1482 qualifies them:
    `series[].title` and `format_type`.  Association text only: `title` and
    `authors[].name`.  A product under a `product` wrapper is read wrapped.
    """
    try:
        document = json.loads(sidecar.text)
    except (RecursionError, ValueError):
        return
    if type(document) is not dict:
        return
    wrapped = document.get("product")
    if "asin" not in document and type(wrapped) is dict:
        document = wrapped
    for raw_key, value in sorted(document.items()):
        if type(raw_key) is not str:
            continue
        key = _libation_key(raw_key)
        support_key = raw_key.strip().lower()
        field = _LIBATION_CLAIM_KEYS.get(key)
        if field is not None:
            builder.add(field, sidecar.surface, value, key=support_key)
        elif key == "narrators" and type(value) is list:
            for item in value[:_MAX_JSON_VALUES]:
                if type(item) is dict:
                    builder.add(
                        AudioInspectionField.CONTRIBUTOR,
                        sidecar.surface,
                        item.get("name"),
                        key=support_key,
                        role="narrator",
                    )
        elif key == "series" and type(value) is list:
            for item in value[:_MAX_JSON_VALUES]:
                if type(item) is not dict:
                    continue
                sequence = item.get("sequence")
                if _is_sequence(sequence):
                    builder.add(
                        AudioInspectionField.SERIES_SEQUENCE,
                        sidecar.surface,
                        sequence,
                        key=support_key,
                    )
                text.add_shadow(AudioSidecarShadowField.SERIES_TITLE, item.get("title"))
        elif key == "formattype":
            text.add_shadow(AudioSidecarShadowField.FORMAT_TYPE, value)
        elif key == "title":
            text.name_title(value)
        elif key == "authors" and type(value) is list:
            for item in value[:_MAX_JSON_VALUES]:
                if type(item) is dict:
                    text.add_creator(item.get("name"))


def _xml_role(attributes: str) -> str | None:
    """Return one element's declared role, read from its tokenized attributes.

    The run is tokenized rather than searched, so only a parsed attribute name
    can supply a role and no attribute's value can pretend to be one.
    """
    return next(
        (
            value.strip().lower()
            for name, value in _xml_attributes(attributes).items()
            if name == "role" or name.endswith(":role")
        ),
        None,
    )


def _is_narrator_role(attributes: str) -> bool:
    """Say whether one OPF contributor's declared role is a narration role.

    A `dc:contributor` names somebody who is not the author; the role attribute
    is what says which somebody.  An editor is not a narrator, and a
    contributor carrying no role at all has not said, so neither becomes
    narrator evidence.
    """
    return _xml_role(attributes) in _NARRATOR_ROLES


def _xml_attributes(run: str) -> dict[str, str]:
    """Read one element's attribute run as the name/value pairs it declares.

    Consumed strictly in sequence: the first token that is not a quoted pair
    ends the read, because whatever follows it is no longer being understood.
    A repeated name keeps its first value, which is the one an XML reader that
    accepted the document at all would have taken.
    """
    values: dict[str, str] = {}
    position = 0
    while position < len(run) and len(values) < _MAX_XML_ATTRIBUTES:
        match = _XML_ATTRIBUTE.match(run, position)
        if match is None:
            break
        values.setdefault(match.group(1).lower(), match.group(2)[1:-1])
        position = match.end()
    return values


def _decode_entities(value: str) -> str:
    for entity, character in _XML_ENTITIES:
        value = value.replace(entity, character)
    return value


class _NextIndex:
    """The next position of one character at or after a growing position.

    Every query position is at least the one before it, so the text is
    searched forward once in total rather than once per query.
    """

    def __init__(self, text: str, character: str) -> None:
        self._text = text
        self._character = character
        self._found = -1
        self._exhausted = False

    def at_or_after(self, position: int) -> int:
        if self._exhausted:
            return -1
        if self._found < position:
            self._found = self._text.find(self._character, position)
            if self._found < 0:
                self._exhausted = True
        return self._found


def _xml_elements(text: str) -> list[tuple[str, str, str]]:
    """Find the recognised OPF elements in one linear pass (#1481).

    Each `<` is tried once.  A name is read anchored at it; the tag must close
    at a `>` within 4 KiB of the name, and the element text runs to the next
    `<`, within 4 KiB, which must open `</`.  These are the facts the
    backtracking element pattern found, or fewer: a tag too long to read here
    skips every candidate that shares its `>`, because that pattern would have
    consumed them with it.
    """
    matches: list[tuple[str, str, str]] = []
    closes = _NextIndex(text, ">")
    opens = _NextIndex(text, "<")
    position = 0
    while len(matches) < _MAX_XML_MATCHES:
        start = text.find("<", position)
        if start < 0:
            break
        head = _XML_ELEMENT_HEAD.match(text, start)
        if head is None:
            position = start + 1
            continue
        end = head.end()
        close = closes.at_or_after(end)
        if close < 0:
            break
        if (
            end - start - 1 > _MAX_XML_NAME_CHARACTERS
            or close - end > _MAX_XML_TAG_CHARACTERS
        ):
            position = close + 1
            continue
        stop = opens.at_or_after(close + 1)
        if (
            stop < 0
            or stop - close - 1 > _MAX_XML_TAG_CHARACTERS
            or not text.startswith("</", stop)
        ):
            position = start + 1
            continue
        matches.append((head.group(1), text[end:close], text[close + 1 : stop]))
        position = stop + 2
    return matches


def _xml_metas(text: str) -> list[tuple[str, str]]:
    """Find `<meta name="…" content="…">` pairs in one linear pass (#1481).

    Only a double-quoted `name` before a double-quoted `content` is read, as
    the backtracking meta pattern required, and like it the last such `name`
    and the last `content` after it are the pair.  A tag must close at a `>`
    within 4 KiB and hold no other `<`, so tags never overlap; a reversed or
    single-quoted pair is never read.
    """
    matches: list[tuple[str, str]] = []
    closes = _NextIndex(text, ">")
    position = 0
    while len(matches) < _MAX_XML_MATCHES:
        start = text.find("<", position)
        if start < 0:
            break
        head = _XML_META_HEAD.match(text, start)
        if head is None:
            position = start + 1
            continue
        close = closes.at_or_after(head.end())
        if close < 0:
            break
        if (
            close - head.end() > _MAX_XML_TAG_CHARACTERS
            or text.find("<", head.end(), close) >= 0
        ):
            position = start + 1
            continue
        contents = [
            found
            for word in _XML_META_CONTENT_WORD.finditer(text, head.end(), close)
            if (found := _XML_META_CONTENT.match(text, word.start())) is not None
        ]
        if not contents:
            position = start + 1
            continue
        content = contents[-1]
        name = None
        for word in _XML_META_NAME_WORD.finditer(text, head.end(), close):
            found = _XML_META_NAME.match(text, word.start())
            if found is not None and found.end() <= content.start():
                name = found
        if name is None:
            position = start + 1
            continue
        matches.append((name.group(1), content.group(1)))
        position = content.end()
    return matches


def _opf_sidecar_facts(
    builder: _Builder, text: _DocumentText, sidecar: AudioSidecar
) -> None:
    """Read a closed set of OPF elements as bounded text, not as a document.

    This is deliberately a recognition grammar rather than an XML parser: no
    external entity, no DTD, and no nesting is ever resolved, so an OPF sidecar
    cannot become a parser attack surface the way a real document tree would.
    `dc:title` and each `dc:creator` with no role or the role `aut` are read as
    association text only.
    """
    for name, attributes, raw in _xml_elements(sidecar.text):
        lowered = name.lower()
        value = _decode_entities(raw)
        if lowered == "language":
            builder.add(
                AudioInspectionField.LANGUAGE, sidecar.surface, value, key="dc:language"
            )
        elif lowered == "description":
            builder.add(
                AudioInspectionField.DESCRIPTION,
                sidecar.surface,
                value,
                key="dc:description",
            )
        elif lowered == "contributor" and _is_narrator_role(attributes):
            builder.add(
                AudioInspectionField.CONTRIBUTOR,
                sidecar.surface,
                value,
                key="dc:contributor",
                role="narrator",
            )
        elif lowered == "title":
            text.name_title(value)
        elif lowered == "creator" and _xml_role(attributes) in {None, "aut"}:
            text.add_creator(value)
    for name, raw in _xml_metas(sidecar.text):
        key = name.lower()
        value = _decode_entities(raw)
        if key.endswith("series"):
            builder.add(AudioInspectionField.SERIES, sidecar.surface, value, key=key)
        elif key.endswith("series_index") or key.endswith("seriesindex"):
            if _is_sequence(value):
                builder.add(
                    AudioInspectionField.SERIES_SEQUENCE,
                    sidecar.surface,
                    value,
                    key=key,
                )
        elif key.endswith("asin"):
            builder.add(AudioInspectionField.ASIN, sidecar.surface, value, key=key)


_LINE_KEYS: dict[str, AudioInspectionField] = {
    "asin": AudioInspectionField.ASIN,
    "audible asin": AudioInspectionField.ASIN,
    "rem asin": AudioInspectionField.ASIN,
    "narrator": AudioInspectionField.CONTRIBUTOR,
    "narrators": AudioInspectionField.CONTRIBUTOR,
    "performer": AudioInspectionField.CONTRIBUTOR,
    "rem narrator": AudioInspectionField.CONTRIBUTOR,
    "series": AudioInspectionField.SERIES,
    "rem series": AudioInspectionField.SERIES,
    "series part": AudioInspectionField.SERIES_SEQUENCE,
    "rem series_part": AudioInspectionField.SERIES_SEQUENCE,
    "language": AudioInspectionField.LANGUAGE,
    "rem language": AudioInspectionField.LANGUAGE,
    "description": AudioInspectionField.DESCRIPTION,
    "plot": AudioInspectionField.DESCRIPTION,
}
# NFO lines read as association text only.
_NFO_TITLE_KEYS = frozenset({"title"})
_NFO_CREATOR_KEYS = frozenset({"author", "authors"})


def _cue_file_value(line: str) -> str | None:
    """Return a CUE `FILE` line's raw file value, never a path read or opened."""
    stripped = line.strip()
    if stripped[:5].upper() != "FILE " and stripped[:5].upper() != "FILE\t":
        return None
    rest = stripped[5:].strip()
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end > 0 else ""
    parts = rest.split()
    return parts[0] if parts else ""


def _line_sidecar_facts(
    builder: _Builder,
    text: _DocumentText,
    sidecar: AudioSidecar,
    grammar: re.Pattern[str],
    limits: AudioInspectionLimits,
) -> None:
    cue = sidecar.surface is AudioEvidenceSurface.SIDECAR_CUE
    # A CUE sheet's disc-level TITLE and PERFORMER come before its first TRACK.
    disc_level = True
    file_seen = False
    for line in sidecar.text.splitlines()[: limits.tags]:
        if cue:
            if line.strip()[:6].upper() in {"TRACK ", "TRACK\t"}:
                disc_level = False
            if not file_seen:
                value = _cue_file_value(line)
                if value is not None:
                    # Compared later as a bare string against the item's track
                    # names.  A value that is not readable association text --
                    # a path, a control, an over-long name -- names none of
                    # them, so it is recorded as another file, not as none.
                    file_seen = True
                    text.file_reference = association_file_reference(value)
                    text.file_refused = text.file_reference is None
        matched = grammar.match(line)
        if matched is None:
            continue
        key = " ".join(matched.group(1).split()).lower()
        value = matched.group(2).strip().strip('"')
        if cue and disc_level:
            if key == "title":
                text.name_title(value)
            elif key == "performer":
                text.add_creator(value)
        elif not cue:
            if key in _NFO_TITLE_KEYS:
                text.name_title(value)
            elif key in _NFO_CREATOR_KEYS:
                text.add_creator(value)
        if key in {"abridged", "rem abridged"}:
            builder.performance(sidecar.surface, "abridged", value)
            continue
        field = _LINE_KEYS.get(key)
        if field is None:
            continue
        if field is AudioInspectionField.SERIES_SEQUENCE and not _is_sequence(value):
            continue
        builder.add(
            field,
            sidecar.surface,
            value,
            key=key,
            role="narrator" if field is AudioInspectionField.CONTRIBUTOR else None,
        )


__all__ = [
    "AudioInterpretation",
    "AudioPathContext",
    "AudioSidecar",
    "SidecarClassification",
    "bounded_description",
    "classify_sidecar",
    "interpret",
]
