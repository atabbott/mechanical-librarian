"""Scoped, bounded Audiobookshelf current-item reader."""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_catalogue import (
    declared_track_index,
    included_audio_records,
    target_connection_code,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_cover_path import (
    observed_cover_path,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_ebook_location import (
    normalize_ebook_location,
)
from mechanical_librarian.adapters.providers.visible_text import (
    DESCRIPTION_OVERSIZED,
    carries_markup,
    decoded_description,
    shown_description,
)
from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.metadata_report import (
    AudioItemInspectionError,
    CoverReadError,
    CoverSourceChangedError,
    CoverTimeoutError,
    MetadataReportError,
    TargetCover,
    TargetFile,
    TargetNotFoundError,
    TargetSnapshot,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_AUDIO_TRACKS,
    AudioInspectionOutcome,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_incumbent_description,
    validate_projection_text,
)
from mechanical_librarian.domain.metadata_report import (
    AUDIO_FILE_FORMATS,
    MAX_CONTRIBUTIONS,
    MAX_DESCRIPTION_CHARACTERS,
    MAX_SCALAR_CHARACTERS,
    MAX_SERIES,
    MAX_TAGS,
    ReportValueError,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    isbn_family,
    normalize_date_value,
    validate_observed_title,
)

_ASIN = re.compile(r"^[A-Za-z0-9]{10}$")
_MISSING = object()


def visible_stored_description(value: str) -> object:
    """Read one stored Audiobookshelf description as the text it shows (#1424).

    Audiobookshelf keeps a description as sanitised HTML. Through the supported
    item read, 2.26.0, 2.36.0, 2.37.1 and 2.37.2 all store a bare `&`, `<` and
    `>` as `&amp;`, `&lt;` and `&gt;`, keep `&amp;`, quotes and `<b>`, and drop
    `<script>` with its content. Mechanical Librarian works in visible text, so
    the stored value is read by the one reading every provider and local
    description passes through, and text without markup keeps its paragraphs
    exact (#1080). The service shows an escaped tag as its literal brackets, so
    stored text that is markup only once decoded is never rendered as if it
    were a tag. A value over the description bound is oversized before
    anything reads it, one that reading refuses, or that is markup only once
    decoded, is `None`, and one that shows no text is returned as stored.
    """
    shown, rendered = shown_description(value)
    return None if rendered and not carries_markup(value) else shown


class AudiobookshelfItemReader:
    def __init__(
        self,
        *,
        client_factory: Callable[[], httpx.Client],
        endpoint: str,
        library_id: str,
        service_path: str,
        maximum_bytes: int,
        timeout_seconds: float = 5.0,
    ) -> None:
        if (
            type(timeout_seconds) not in {int, float}
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= 60
        ):
            raise ValueError("target timeout is invalid")
        self._client_factory = client_factory
        self._endpoint = endpoint.rstrip("/")
        self._library_id = library_id
        self._service_path = service_path.rstrip("/") or "/"
        self._maximum_bytes = maximum_bytes
        self._timeout_seconds = float(timeout_seconds)

    def read(self, item: str, *, deadline: float | None = None) -> TargetSnapshot:
        if (
            type(item) is not str
            or not 1 <= len(item) <= 512
            or item.strip() != item
            or any(character in item for character in "\x00/\\")
        ):
            raise MetadataReportError("target item is invalid")
        try:
            client = self._client_factory()
            with (
                client,
                client.stream(
                    "GET",
                    f"{self._endpoint}/api/items/{quote(item, safe='')}",
                    params={"expanded": "1"},
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    timeout=_phase_timeout(deadline, self._timeout_seconds),
                ) as response,
            ):
                if response.status_code == 404:
                    raise TargetNotFoundError("target item is absent")
                if response.status_code == 401:
                    raise TargetConnectionError("authentication")
                if response.status_code == 403:
                    raise TargetConnectionError("authorization")
                if response.status_code != 200:
                    raise TargetConnectionError("unavailable")
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                if content_type != "application/json":
                    raise MetadataReportError("target response is invalid")
                payload = _bounded_body(
                    response, self._maximum_bytes, deadline=deadline
                )
        except MetadataReportError:
            raise
        except (httpx.HTTPError, OSError) as error:
            raise TargetConnectionError(target_connection_code(error)) from None
        try:
            source = json.loads(
                payload.decode("utf-8", "strict"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_number,
            )
            return _snapshot(source, item, self._library_id, self._service_path)
        except MetadataReportError:
            raise
        except (
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            RecursionError,
            json.JSONDecodeError,
        ) as error:
            raise MetadataReportError("target response is invalid") from error

    def read_cover(
        self, cover: TargetCover, *, deadline: float | None = None
    ) -> bytes | None:
        """One deliberately credential-free raw cover request.

        The caller owns the surrounding item-detail reads and drift decision.
        """
        if (
            type(cover) is not TargetCover
            or not cover.path.startswith("/")
            or not cover.item
        ):
            raise MetadataReportError("target cover is invalid")
        if deadline is not None and time.monotonic() >= deadline:
            raise CoverTimeoutError("target cover timed out")
        before = self.read(cover.item, deadline=deadline)
        if before.cover != cover:
            raise CoverSourceChangedError("target cover changed")
        try:
            value = self._read_raw_cover(cover, deadline)
            after = self.read(cover.item, deadline=deadline)
            if after.cover != before.cover:
                raise CoverSourceChangedError("target cover changed")
            if self._read_raw_cover(cover, deadline) != value:
                raise CoverSourceChangedError("target cover changed")
            return value
        except MetadataReportError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as error:
            raise MetadataReportError("target cover is unavailable") from error

    def _read_raw_cover(
        self, cover: TargetCover, deadline: float | None
    ) -> bytes | None:
        with httpx.Client(
            trust_env=False, follow_redirects=False, headers={"User-Agent": ""}
        ) as client:
            host = _raw_cover_authority(self._endpoint)
            with client.stream(
                "GET",
                f"{self._endpoint}{cover.path}",
                headers={"Host": host, "Accept-Encoding": "identity"},
                timeout=_phase_timeout(deadline, self._timeout_seconds),
            ) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise MetadataReportError("target cover is unavailable")
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .lower()
                    .strip()
                )
                if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise CoverReadError("unsupported_format")
                content_encoding = response.headers.get("content-encoding")
                if (
                    content_encoding is not None
                    and content_encoding.lower() != "identity"
                ):
                    raise CoverReadError("unsupported_format")
                body = _bounded_body(response, 8 * 1024 * 1024, deadline=deadline)
                if _cover_signature_media_type(body) != content_type:
                    raise CoverReadError("unsupported_format")
                return body


def _raw_cover_authority(endpoint: str) -> str:
    """Preserve the configured URL authority, including a non-default port."""
    try:
        return httpx.URL(endpoint).netloc.decode("ascii")
    except (AttributeError, UnicodeError, ValueError) as error:
        raise MetadataReportError("target cover is invalid") from error


def _cover_signature_media_type(body: bytes) -> str | None:
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def _snapshot(
    source: object, item: str, library_id: str, service_path: str
) -> TargetSnapshot:
    root = _mapping(source)
    if (
        root.get("id") != item
        or root.get("libraryId") != library_id
        or type(root.get("isInvalid")) is not bool
        or type(root.get("isMissing")) is not bool
        or root["isInvalid"]
        or root["isMissing"]
        or root.get("mediaType") != "book"
    ):
        raise MetadataReportError("target response is invalid")
    media = _mapping(root.get("media"))
    files_value = media.get("ebookFiles")
    single = media.get("ebookFile")
    if files_value is not None and single is not None:
        raise MetadataReportError("target response is invalid")
    if files_value is None:
        files_source = [] if single is None else [single]
    elif type(files_value) is list:
        files_source = files_value
    else:
        raise MetadataReportError("target response is invalid")
    if len(files_source) > 128:
        raise MetadataReportError("target response is invalid")
    audio_source = media.get("audioFiles")
    # The bound is measured on the set the service plays, so the records it
    # excludes are dropped before the count: an item of five hundred and
    # twenty files with ten excluded is a set this build composes.
    audio_records = included_audio_records(audio_source) or ()
    companion = None
    if audio_records and files_source:
        # Included audio beside an ebook is a recording with a supplementary
        # ebook, as the service shows it: the item is the audiobook, and one
        # usable EPUB is kept only as corroborating evidence (#1319). Any other
        # ebook shape is simply not evidence; it never makes the recording
        # unpreparable.
        companion = _companion(files_source, service_path, root)
        files_source = []
    files = tuple(_file(value, service_path, root) for value in files_source)
    if files:
        media_kind = "ebook"
    elif type(audio_source) is list:
        media_kind = "audiobook"
        if len(audio_records) > MAX_AUDIO_TRACKS:
            # A set this build cannot compose is a bound this build states,
            # not a target that answered badly: it is reported as the item's
            # own typed terminal rather than as an invalid response.
            raise AudioItemInspectionError(
                AudioInspectionOutcome.MULTI_FILE_BUDGET_EXCEEDED
            )
        files = _audio_files(audio_records, service_path, root)
    else:
        media_kind = "unsupported"
    identities = tuple(value.identity for value in files)
    if len(set(identities)) != len(identities):
        raise MetadataReportError("target file identities are duplicated")
    cover_state, cover_path, _ = observed_cover_path(root)
    cover = None
    if cover_state is TargetFieldState.VALUE and cover_path is not None:
        # Metadata declares presence, never the acquisition URL.
        cover = TargetCover(
            hashlib.sha256(cover_path.encode()).hexdigest(),
            f"/api/items/{quote(item, safe='')}/cover?raw=1",
            item,
        )
    fields = _target_fields(root, media, cover)
    title_field = fields[0]
    title = (
        title_field.value
        if title_field.state is TargetFieldState.VALUE
        and type(title_field.value) is str
        else None
    )
    return TargetSnapshot(media_kind, title, files, cover, fields, companion)


def _target_fields(
    root: dict[str, Any],
    media: dict[str, Any],
    cover: TargetCover | None,
) -> tuple[TargetField, ...]:
    source = media.get("metadata", _MISSING)
    if source is _MISSING:
        fields = [
            TargetField(destination, TargetFieldState.UNLOADED)
            for destination in TargetDestination
        ]
    elif type(source) is not dict:
        fields = [
            TargetField(destination, TargetFieldState.MALFORMED)
            for destination in TargetDestination
        ]
    else:
        fields = [
            _target_field(source, destination) for destination in TargetDestination
        ]
    if "tags" in media:
        fields[list(TargetDestination).index(TargetDestination.TAGS)] = _target_field(
            {TargetDestination.TAGS.value: media["tags"]}, TargetDestination.TAGS
        )
    fields[-1] = _cover_field(root, cover)
    return tuple(fields)


def _target_field(
    metadata: dict[str, Any], destination: TargetDestination
) -> TargetField:
    raw = metadata.get(destination.value, _MISSING)
    if raw is _MISSING:
        return TargetField(destination, TargetFieldState.MISSING)
    if raw is None:
        return TargetField(destination, TargetFieldState.NULL)
    if raw == "" or raw == []:
        return TargetField(destination, TargetFieldState.EMPTY)
    try:
        if destination is TargetDestination.TITLE:
            if type(raw) is str and len(raw) > MAX_SCALAR_CHARACTERS:
                _target_projection_text(raw, "target title", MAX_SCALAR_CHARACTERS)
                return TargetField(destination, TargetFieldState.OVERSIZED)
            try:
                title = validate_observed_title(raw)
            except ReportValueError as error:
                if type(raw) is str:
                    raise MetadataReportError("target response is invalid") from error
                raise ValueError("target title is malformed") from None
            except (TypeError, ValueError):
                raise ValueError("target title is malformed") from None
            return TargetField(
                destination,
                TargetFieldState.VALUE,
                title,
            )
        if destination in {
            TargetDestination.SUBTITLE,
            TargetDestination.PUBLISHER,
            TargetDestination.ISBN,
            TargetDestination.ASIN,
            TargetDestination.LANGUAGE,
        }:
            value, oversized = _target_projection_text(
                raw, "target scalar", MAX_SCALAR_CHARACTERS
            )
            if (
                destination is TargetDestination.ISBN and isbn_family(value) is None
            ) or (
                destination is TargetDestination.ASIN and _ASIN.fullmatch(value) is None
            ):
                raise ValueError("target identifier is malformed")
            if oversized:
                return TargetField(destination, TargetFieldState.OVERSIZED)
            return TargetField(destination, TargetFieldState.VALUE, value)
        if destination is TargetDestination.DESCRIPTION:
            visible = visible_stored_description(raw) if type(raw) is str else raw
            if visible is DESCRIPTION_OVERSIZED:
                # Checked as stored, so an oversized value carrying forbidden
                # text stays malformed rather than merely oversized.
                validate_incumbent_description(
                    raw, "target description", max(len(raw), MAX_DESCRIPTION_CHARACTERS)
                )
                return TargetField(destination, TargetFieldState.OVERSIZED)
            # Markup the renderer will not read, or that shows no text at all,
            # is admitted as stored, as before #1424, so a markup-only
            # placeholder is still recognised as one (#1250).
            value = validate_incumbent_description(
                raw if visible is None else visible,
                "target description",
                MAX_DESCRIPTION_CHARACTERS,
            )
            # Writing the admitted text back reproduces the stored form only
            # where Audiobookshelf stores that text itself or with just `&`,
            # `<` and `>` escaped; anything else is formatting a restore from
            # here would lose (#1424).
            return TargetField(
                destination,
                TargetFieldState.VALUE,
                value,
                restorable=raw in {value, html.escape(value, quote=False)}
                and not carries_markup(value)
                and decoded_description(value) == value,
            )
        if destination in {
            TargetDestination.AUTHORS,
            TargetDestination.NARRATORS,
            TargetDestination.GENRES,
            TargetDestination.TAGS,
        }:
            maximum = (
                MAX_CONTRIBUTIONS
                if destination
                in {TargetDestination.AUTHORS, TargetDestination.NARRATORS}
                else MAX_TAGS
            )
            if type(raw) is not list:
                raise ValueError("target list is malformed")
            contributor_values: list[str] = []
            oversized = len(raw) > maximum
            for item in raw:
                value, value_oversized = _target_projection_text(
                    item.get("name") if type(item) is dict else item,
                    "target list value",
                    MAX_SCALAR_CHARACTERS,
                )
                contributor_values.append(value)
                oversized = oversized or value_oversized
            if oversized:
                return TargetField(destination, TargetFieldState.OVERSIZED)
            return TargetField(
                destination,
                TargetFieldState.VALUE,
                tuple(contributor_values),
            )
        if destination is TargetDestination.SERIES:
            if type(raw) is not list:
                raise ValueError("target series is malformed")
            series_values: list[tuple[str, str | None]] = []
            oversized = len(raw) > MAX_SERIES
            for item in raw:
                if type(item) is not dict:
                    raise ValueError("target series is malformed")
                name, name_oversized = _target_projection_text(
                    item.get("name"),
                    "target series name",
                    MAX_SCALAR_CHARACTERS,
                )
                oversized = oversized or name_oversized
                sequence_source = item.get("sequence")
                sequence = None
                if sequence_source is not None:
                    if (
                        type(sequence_source) is int
                        and type(sequence_source) is not bool
                    ):
                        sequence_source = str(sequence_source)
                    sequence, sequence_oversized = _target_projection_text(
                        sequence_source,
                        "target series sequence",
                        MAX_SCALAR_CHARACTERS,
                    )
                    oversized = oversized or sequence_oversized
                series_values.append((name, sequence))
            if oversized:
                return TargetField(destination, TargetFieldState.OVERSIZED)
            return TargetField(
                destination,
                TargetFieldState.VALUE,
                tuple(TargetSeries(name, sequence) for name, sequence in series_values),
            )
        if destination is TargetDestination.PUBLISHED_YEAR:
            value = raw
            if type(value) is int and type(value) is not bool:
                value = str(value)
            if (
                type(value) is not str
                or len(value) != 4
                or not value.isascii()
                or not value.isdigit()
            ):
                raise ValueError("target year is malformed")
            return TargetField(destination, TargetFieldState.VALUE, value)
        if destination is TargetDestination.PUBLISHED_DATE:
            value, oversized = _target_projection_text(
                raw, "target publication date", MAX_SCALAR_CHARACTERS
            )
            if len(value) in {4, 7}:
                normalized, _ = normalize_date_value(value)
                if normalized != value:
                    raise ValueError("target publication date is malformed")
            else:
                date.fromisoformat(value)
            if oversized:
                return TargetField(destination, TargetFieldState.OVERSIZED)
            return TargetField(destination, TargetFieldState.VALUE, value)
        if destination is TargetDestination.EXPLICIT:
            if type(raw) is not bool:
                raise ValueError("target explicit value is malformed")
            return TargetField(destination, TargetFieldState.VALUE, raw)
    except EvidenceValueError as error:
        if destination is TargetDestination.TITLE and type(raw) is str:
            raise MetadataReportError("target response is invalid") from error
        return TargetField(destination, TargetFieldState.MALFORMED)
    except (TypeError, ValueError):
        return TargetField(destination, TargetFieldState.MALFORMED)
    raise MetadataReportError("target metadata destination is unsupported")


def _target_projection_text(
    value: object, label: str, maximum: int
) -> tuple[str, bool]:
    validation_maximum = (
        len(value) if type(value) is str and len(value) > maximum else maximum
    )
    text = validate_projection_text(value, label, validation_maximum)
    return text, len(text) > maximum


def _cover_field(root: dict[str, Any], cover: TargetCover | None) -> TargetField:
    state, _, _ = observed_cover_path(root)
    if state is TargetFieldState.VALUE:
        return (
            TargetField(TargetDestination.COVER, state, cover.identity)
            if cover is not None
            else TargetField(TargetDestination.COVER, TargetFieldState.MALFORMED)
        )
    return TargetField(TargetDestination.COVER, state)


def _file(
    source: object,
    service_path: str,
    item: object,
) -> TargetFile:
    try:
        location = normalize_ebook_location(
            source, service_path=service_path, item=item
        )
    except ValueError as error:
        raise MetadataReportError(str(error)) from None
    return TargetFile(location.identity, location.relative_path, location.format)


def _companion(source: list[Any], service_path: str, item: object) -> TargetFile | None:
    if len(source) != 1:
        return None
    try:
        value = _file(source[0], service_path, item)
    except MetadataReportError:
        return None
    return value if value.format == "epub" else None


def _audio_files(
    source: tuple[Any, ...], service_path: str, item: object
) -> tuple[TargetFile, ...]:
    """Surface the item's audio tracks, or none of them.

    An audio record that does not resolve to one agreed service-relative path
    in a supported format makes the whole set unusable rather than a shorter
    one: a report is bound to a file *index*, so silently dropping a member
    would renumber the survivors and bind a report to a different track. This
    is deliberately not an error -- an unusable audiobook item still reads,
    and simply has no file a report can be prepared from.

    The records are the ones the service plays; the excluded ones were dropped
    by `included_audio_records` before the set was counted.
    """
    values: list[TargetFile] = []
    for value in source:
        try:
            location = normalize_ebook_location(
                value, service_path=service_path, item=item
            )
        except ValueError:
            return ()
        if location.format not in AUDIO_FILE_FORMATS:
            return ()
        values.append(
            TargetFile(
                location.identity,
                location.relative_path,
                location.format,
                declared_track_index(value),
            )
        )
    return tuple(values)


def _bounded_body(
    response: httpx.Response, maximum: int, *, deadline: float | None = None
) -> bytes:
    chunks: list[bytes] = []
    length = 0
    for chunk in response.iter_bytes():
        if deadline is not None and time.monotonic() >= deadline:
            raise CoverTimeoutError("target cover timed out")
        length += len(chunk)
        if length > maximum:
            raise CoverReadError("oversized")
        chunks.append(chunk)
    if not chunks:
        raise MetadataReportError("target response is invalid")
    return b"".join(chunks)


def _phase_timeout(deadline: float | None, configured: float) -> float:
    if deadline is None:
        return configured
    remaining = min(configured, deadline - time.monotonic())
    if remaining <= 0:
        raise CoverTimeoutError("target cover timed out")
    return remaining


def _mapping(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise MetadataReportError("target response is invalid")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MetadataReportError("target response is invalid")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise MetadataReportError("target response is invalid")


__all__ = ["AudiobookshelfItemReader", "visible_stored_description"]
