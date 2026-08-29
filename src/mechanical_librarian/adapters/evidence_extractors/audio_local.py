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

from mechanical_librarian.domain.audio_inspection import (
    MAX_CHAPTERS,
    MAX_DURATION_MILLISECONDS,
    AudioChapter,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioInspectionFact,
    AudioInspectionField,
    AudioInspectionLimits,
    AudioInspectionOutcome,
    AudioPerformanceType,
    normalize_duration_seconds,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.identifiers import Fingerprint

# A bracketed ten-character upper-case token is the one path grammar Audible
# exports and every downstream tool reproduces.  It is deliberately not an ASIN
# shape filter: an ISBN-10-shaped ASIN matches it exactly as a `B0` one does,
# and nothing downstream narrows it further.
_BRACKETED_TOKEN = re.compile(r"\[([A-Z0-9]{10})\]")
_TONE_NAMESPACE = "com.pilabor.tone"
_TRUE_VALUES = frozenset({"1", "true", "yes", "y"})
_FALSE_VALUES = frozenset({"0", "false", "no", "n"})
_SEQUENCE = re.compile(r"^\d{1,4}(?:\.\d{1,3})?$")
_CHAPTER_SECONDS = re.compile(r"^\d{1,9}(?:\.\d{1,9})?$")
_XML_ELEMENT = re.compile(
    r"<(?:[A-Za-z][\w.-]*:)?(language|description|title|creator|contributor)\b[^>]*>"
    r"([^<]{0,4096})</",
    re.IGNORECASE,
)
_XML_META = re.compile(
    r"<meta\b[^>]*\bname\s*=\s*\"([\w:.-]{1,64})\"[^>]*\bcontent\s*=\s*\"([^\"]{0,512})\"",
    re.IGNORECASE,
)
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
    "prid": AudioInspectionField.ASIN,
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


def sidecar_surface(name: str, media_name: str) -> AudioEvidenceSurface | None:
    """Classify one directory entry as a supported sidecar, or not at all."""
    if type(name) is not str or type(media_name) is not str or "/" in name:
        return None
    lowered = name.lower()
    stem = media_name.rsplit(".", 1)[0].lower()
    if lowered == "metadata.json":
        return AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF
    if lowered.endswith(".metadata.json"):
        return (
            AudioEvidenceSurface.SIDECAR_LIBATION
            if lowered[: -len(".metadata.json")] == stem
            else None
        )
    if lowered.endswith(".opf"):
        return AudioEvidenceSurface.SIDECAR_OPF
    if lowered.endswith(".nfo"):
        return AudioEvidenceSurface.SIDECAR_NFO
    if lowered.endswith(".cue"):
        return AudioEvidenceSurface.SIDECAR_CUE
    return None


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
    container = _format_object(probe.get("format"))
    if container is None:
        return AudioInterpretation(AudioInspectionOutcome.UNSUPPORTED_FORMAT)
    streams = probe.get("streams")
    if type(streams) is not list or len(streams) > limits.streams:
        return AudioInterpretation(AudioInspectionOutcome.MALFORMED)
    if not any(
        type(stream) is dict and stream.get("codec_type") == "audio"
        for stream in streams
    ):
        return AudioInterpretation(AudioInspectionOutcome.UNSUPPORTED_FORMAT)

    builder = _Builder(source)
    tag_surface = _tag_surface_for(container)
    builder.duration(container.get("duration"))
    builder.stream_language(streams)
    _tag_facts(builder, container.get("tags"), tag_surface, limits)
    for sidecar in sidecars[: limits.sidecar_documents]:
        _sidecar_facts(builder, sidecar, limits)
    builder.bracketed(path)
    chapters, truncated = _chapters(probe.get("chapters"), limits)
    cover = _embedded_cover(streams)
    facts = builder.result(limits)
    outcome = (
        AudioInspectionOutcome.SUCCESS
        if facts or chapters or cover.present
        else AudioInspectionOutcome.NO_APPLICABLE_EVIDENCE
    )
    return AudioInterpretation(outcome, facts, chapters, cover, truncated)


class _Builder:
    """Collect facts in one deterministic order without duplicating a surface."""

    def __init__(self, source: Fingerprint) -> None:
        self._source = source
        self._seen: set[tuple[str, str, str, str | None]] = set()
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
        support = text if key is None else _support(key, text)
        resolved = canonical if canonical is not None else text
        identity = (field.value, surface.value, text, role)
        if identity in self._seen:
            return
        try:
            fact = AudioInspectionFact(
                field, self._source, text, resolved, support, surface, "valid", role
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

    def result(self, limits: AudioInspectionLimits) -> tuple[AudioInspectionFact, ...]:
        return tuple(self._facts[: limits.MAX_FACTS])


def _support(key: str, observed: str) -> str:
    candidate = f"{key}: {observed}"
    try:
        validate_projection_text(candidate, "audio support", len(candidate))
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


def _format_object(value: object) -> dict[str, object] | None:
    if type(value) is not dict:
        return None
    return value


def _tag_surface_for(container: dict[str, object]) -> AudioEvidenceSurface:
    names = container.get("format_name")
    if type(names) is str and "mp3" in names.split(","):
        return AudioEvidenceSurface.ID3_FRAME
    return AudioEvidenceSurface.MP4_ILST


def _normalized_key(key: str) -> tuple[str, bool]:
    """Reduce one exported tag key, and say whether it came from a freeform atom.

    ``-export_all`` spells an MP4 freeform atom as ``----:<mean>:<name>``, so
    the trailing component is the tag a writer intended and the mean is what
    identifies the writer.  Everything else is a well-known key.
    """
    lowered = key.strip().lower()
    if not lowered.startswith("----:"):
        return lowered.replace("©", ""), False
    parts = lowered.split(":")
    return parts[-1], len(parts) >= 3 and parts[1] == _TONE_NAMESPACE


def _tag_facts(
    builder: _Builder,
    tags: object,
    default_surface: AudioEvidenceSurface,
    limits: AudioInspectionLimits,
) -> None:
    if type(tags) is not dict:
        return
    for index, (raw_key, value) in enumerate(sorted(tags.items())):
        if index >= limits.tags or type(raw_key) is not str:
            continue
        if type(value) is str and len(value) > limits.tag_value_characters:
            continue
        key, freeform = _normalized_key(raw_key)
        surface = _surface_for(key, freeform, default_surface)
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
    key: str, freeform: bool, default_surface: AudioEvidenceSurface
) -> AudioEvidenceSurface:
    if freeform:
        return AudioEvidenceSurface.MP4_FREEFORM_TONE
    if default_surface is AudioEvidenceSurface.ID3_FRAME:
        return (
            AudioEvidenceSurface.ID3_FRAME
            if key in _ID3_WELL_KNOWN
            else AudioEvidenceSurface.ID3_USER_TEXT
        )
    return default_surface


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
) -> tuple[tuple[AudioChapter, ...], bool]:
    if type(value) is not list:
        return (), False
    cap = min(limits.chapters, MAX_CHAPTERS)
    chapters: list[AudioChapter] = []
    for item in value[:cap]:
        if type(item) is not dict:
            continue
        start = _milliseconds(item.get("start_time"))
        end = _milliseconds(item.get("end_time"))
        if start is None or end is None or end < start:
            continue
        tags = item.get("tags")
        title = _clean(tags.get("title")) if type(tags) is dict else None
        try:
            chapters.append(AudioChapter(len(chapters) + 1, start, end, _title(title)))
        except ValueError:
            continue
    return tuple(chapters), len(value) > cap


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


def _sidecar_facts(
    builder: _Builder, sidecar: AudioSidecar, limits: AudioInspectionLimits
) -> None:
    if type(sidecar) is not AudioSidecar:
        return
    if sidecar.surface in {
        AudioEvidenceSurface.SIDECAR_AUDIOBOOKSHELF,
        AudioEvidenceSurface.SIDECAR_LIBATION,
    }:
        _json_sidecar_facts(builder, sidecar)
    elif sidecar.surface is AudioEvidenceSurface.SIDECAR_OPF:
        _opf_sidecar_facts(builder, sidecar)
    elif sidecar.surface is AudioEvidenceSurface.SIDECAR_NFO:
        _line_sidecar_facts(builder, sidecar, _NFO_LINE, limits)
    elif sidecar.surface is AudioEvidenceSurface.SIDECAR_CUE:
        _line_sidecar_facts(builder, sidecar, _CUE_LINE, limits)


_JSON_KEYS: dict[str, AudioInspectionField] = {
    "asin": AudioInspectionField.ASIN,
    "narrator": AudioInspectionField.CONTRIBUTOR,
    "narrators": AudioInspectionField.CONTRIBUTOR,
    "language": AudioInspectionField.LANGUAGE,
    "description": AudioInspectionField.DESCRIPTION,
    "publishersummary": AudioInspectionField.DESCRIPTION,
    "seriesname": AudioInspectionField.SERIES,
    "series": AudioInspectionField.SERIES,
    "seriesposition": AudioInspectionField.SERIES_SEQUENCE,
    "sequence": AudioInspectionField.SERIES_SEQUENCE,
}


def _json_sidecar_facts(builder: _Builder, sidecar: AudioSidecar) -> None:
    try:
        document = json.loads(sidecar.text)
    except (RecursionError, ValueError):
        return
    if type(document) is not dict:
        return
    for raw_key, value in sorted(document.items()):
        if type(raw_key) is not str:
            continue
        key = raw_key.strip().lower().replace("_", "")
        if key in {"abridged", "isabridged"}:
            builder.performance(
                sidecar.surface,
                "abridged",
                str(value).lower() if type(value) is bool else value,
            )
            continue
        field = _JSON_KEYS.get(key)
        if field is None:
            continue
        for observed in _json_values(field, value)[:16]:
            if field is AudioInspectionField.SERIES_SEQUENCE and not _is_sequence(
                observed
            ):
                continue
            builder.add(
                field,
                sidecar.surface,
                observed,
                key=raw_key.strip().lower(),
                role="narrator" if field is AudioInspectionField.CONTRIBUTOR else None,
            )


def _json_values(field: AudioInspectionField, value: object) -> list[object]:
    if type(value) is list:
        values: list[object] = []
        for item in value:
            if type(item) is dict:
                values.append(item.get("name"))
                if field is AudioInspectionField.SERIES:
                    sequence = item.get("sequence")
                    if sequence is not None:
                        values.append(sequence)
            else:
                values.append(item)
        return values
    return [value]


def _decode_entities(value: str) -> str:
    for entity, character in _XML_ENTITIES:
        value = value.replace(entity, character)
    return value


def _opf_sidecar_facts(builder: _Builder, sidecar: AudioSidecar) -> None:
    """Read a closed set of OPF elements as bounded text, not as a document.

    This is deliberately a recognition grammar rather than an XML parser: no
    external entity, no DTD, and no nesting is ever resolved, so an OPF sidecar
    cannot become a parser attack surface the way a real document tree would.
    """
    for name, raw in _XML_ELEMENT.findall(sidecar.text)[:64]:
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
        elif lowered == "contributor":
            builder.add(
                AudioInspectionField.CONTRIBUTOR,
                sidecar.surface,
                value,
                key="dc:contributor",
                role="narrator",
            )
    for name, raw in _XML_META.findall(sidecar.text)[:64]:
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


def _line_sidecar_facts(
    builder: _Builder,
    sidecar: AudioSidecar,
    grammar: re.Pattern[str],
    limits: AudioInspectionLimits,
) -> None:
    for line in sidecar.text.splitlines()[: limits.tags]:
        matched = grammar.match(line)
        if matched is None:
            continue
        key = " ".join(matched.group(1).split()).lower()
        value = matched.group(2).strip().strip('"')
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
    "interpret",
    "sidecar_surface",
]
