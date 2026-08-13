"""Scoped, bounded Audiobookshelf current-item reader."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    CoverReadError,
    CoverSourceChangedError,
    CoverTimeoutError,
    TargetFile,
    TargetNotFoundError,
    TargetCover,
    TargetSnapshot,
)
from mechanical_librarian.domain.metadata_report import (
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
    canonical_json,
    isbn_family,
    validate_observed_title,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_ebook_location import normalize_ebook_location


_ASIN = re.compile(r"^[A-Za-z0-9]{10}$")
_MISSING = object()


class AudiobookshelfItemReader:
    def __init__(self, *, client_factory: Callable[[], httpx.Client], endpoint: str, library_id: str, service_path: str, maximum_bytes: int, timeout_seconds: float = 5.0) -> None:
        if type(timeout_seconds) not in {int, float} or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 60:
            raise ValueError("target timeout is invalid")
        self._client_factory = client_factory
        self._endpoint = endpoint.rstrip("/")
        self._library_id = library_id
        self._service_path = service_path.rstrip("/") or "/"
        self._maximum_bytes = maximum_bytes
        self._timeout_seconds = float(timeout_seconds)

    def read(self, item: str, *, deadline: float | None = None) -> TargetSnapshot:
        if type(item) is not str or not 1 <= len(item) <= 512 or item.strip() != item or any(character in item for character in "\x00/\\"):
            raise MetadataReportError("target item is invalid")
        try:
            client = self._client_factory()
            with client, client.stream(
                "GET",
                f"{self._endpoint}/api/items/{quote(item, safe='')}",
                params={"expanded": "1"},
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                timeout=_phase_timeout(deadline, self._timeout_seconds),
            ) as response:
                if response.status_code == 404:
                    raise TargetNotFoundError("target item is absent")
                if response.status_code in {401, 403}:
                    raise MetadataReportError("target item is unavailable")
                if response.status_code != 200:
                    raise MetadataReportError("target service is unavailable")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise MetadataReportError("target response is invalid")
                payload = _bounded_body(response, self._maximum_bytes, deadline=deadline)
        except MetadataReportError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as error:
            raise MetadataReportError("target service is unavailable") from error
        try:
            source = json.loads(
                payload.decode("utf-8", "strict"),
                object_pairs_hook=_unique_object,
                parse_constant=_reject_number,
            )
            return _snapshot(source, item, self._library_id, self._service_path)
        except MetadataReportError:
            raise
        except (UnicodeError, ValueError, TypeError, KeyError, RecursionError, json.JSONDecodeError) as error:
            raise MetadataReportError("target response is invalid") from error

    def read_cover(self, cover: TargetCover, *, deadline: float | None = None) -> bytes | None:
        """One deliberately credential-free raw cover request.

        The caller owns the surrounding item-detail reads and drift decision.
        """
        if type(cover) is not TargetCover or not cover.path.startswith("/") or not cover.item:
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

    def _read_raw_cover(self, cover: TargetCover, deadline: float | None) -> bytes | None:
        with httpx.Client(trust_env=False, follow_redirects=False, headers={"User-Agent": ""}) as client:
            host = _raw_cover_authority(self._endpoint)
            with client.stream("GET", f"{self._endpoint}{cover.path}", headers={"Host": host, "Accept-Encoding": "identity"}, timeout=_phase_timeout(deadline, self._timeout_seconds)) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise MetadataReportError("target cover is unavailable")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise CoverReadError("unsupported_format")
                content_encoding = response.headers.get("content-encoding")
                if content_encoding is not None and content_encoding.lower() != "identity":
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


def _snapshot(source: object, item: str, library_id: str, service_path: str) -> TargetSnapshot:
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
    files = tuple(
        _file(value, service_path, root) for value in files_source
    )
    identities = tuple(value.identity for value in files)
    if len(set(identities)) != len(identities):
        raise MetadataReportError("target file identities are duplicated")
    media_kind = "ebook" if files else "audiobook" if type(media.get("audioFiles")) is list else "unsupported"
    cover_path = root.get("coverPath")
    cover = None
    if cover_path is not None:
        if (
            type(cover_path) is str
            and cover_path.startswith("/")
            and len(cover_path) <= 1024
        ):
            # The service's metadata-provided route is evidence of presence only.
            # Acquisition is pinned to its one documented raw endpoint so a hostile
            # detail response cannot redirect this credential-free request.
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
    return TargetSnapshot(media_kind, title, files, cover, fields)


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
            _target_field(source, destination)
            for destination in TargetDestination
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
                _target_projection_text(
                    raw, "target title", MAX_SCALAR_CHARACTERS
                )
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
                destination is TargetDestination.ISBN
                and isbn_family(value) is None
            ) or (
                destination is TargetDestination.ASIN
                and _ASIN.fullmatch(value) is None
            ):
                raise ValueError("target identifier is malformed")
            if oversized:
                return TargetField(destination, TargetFieldState.OVERSIZED)
            return TargetField(destination, TargetFieldState.VALUE, value)
        if destination is TargetDestination.DESCRIPTION:
            value, oversized = _target_projection_text(
                raw, "target description", MAX_DESCRIPTION_CHARACTERS
            )
            if oversized:
                return TargetField(destination, TargetFieldState.OVERSIZED)
            return TargetField(
                destination,
                TargetFieldState.VALUE,
                value,
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
                    if type(sequence_source) is int and type(sequence_source) is not bool:
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
                tuple(
                    TargetSeries(name, sequence)
                    for name, sequence in series_values
                ),
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
        len(value)
        if type(value) is str and len(value) > maximum
        else maximum
    )
    text = validate_projection_text(value, label, validation_maximum)
    return text, len(text) > maximum


def _cover_field(
    root: dict[str, Any], cover: TargetCover | None
) -> TargetField:
    destination = TargetDestination.COVER
    if "coverPath" not in root:
        return TargetField(destination, TargetFieldState.MISSING)
    raw = root["coverPath"]
    if raw is None:
        return TargetField(destination, TargetFieldState.NULL)
    if raw == "":
        return TargetField(destination, TargetFieldState.EMPTY)
    if cover is None:
        return TargetField(destination, TargetFieldState.MALFORMED)
    return TargetField(destination, TargetFieldState.VALUE, cover.identity)


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


def _bounded_body(response: httpx.Response, maximum: int, *, deadline: float | None = None) -> bytes:
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


__all__ = ["AudiobookshelfItemReader"]
