"""Scoped, bounded Audiobookshelf current-item reader."""

from __future__ import annotations

import hashlib
import json
import posixpath
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    TargetFile,
    TargetCover,
    TargetSnapshot,
)
from mechanical_librarian.domain.metadata_report import (
    canonical_json,
    validate_observed_title,
)


class AudiobookshelfItemReader:
    def __init__(self, *, client_factory: Callable[[], httpx.Client], endpoint: str, library_id: str, service_path: str, maximum_bytes: int) -> None:
        self._client_factory = client_factory
        self._endpoint = endpoint.rstrip("/")
        self._library_id = library_id
        self._service_path = service_path.rstrip("/") or "/"
        self._maximum_bytes = maximum_bytes

    def read(self, item: str) -> TargetSnapshot:
        if type(item) is not str or not 1 <= len(item) <= 512 or item.strip() != item or any(character in item for character in "\x00/\\"):
            raise MetadataReportError("target item is invalid")
        try:
            client = self._client_factory()
            with client, client.stream(
                "GET",
                f"{self._endpoint}/api/items/{quote(item, safe='')}",
                params={"expanded": "1"},
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as response:
                if response.status_code in {401, 403, 404}:
                    raise MetadataReportError("target item is unavailable")
                if response.status_code != 200:
                    raise MetadataReportError("target service is unavailable")
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise MetadataReportError("target response is invalid")
                payload = _bounded_body(response, self._maximum_bytes)
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
        if type(cover) is not TargetCover or not cover.path.startswith("/"):
            raise MetadataReportError("target cover is invalid")
        if deadline is not None and time.monotonic() >= deadline:
            raise MetadataReportError("target cover timed out")
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, headers={}) as client:
                host = httpx.URL(self._endpoint).host
                with client.stream("GET", f"{self._endpoint}{cover.path}", headers={"Host": host, "Accept-Encoding": "identity"}, timeout=5.0) as response:
                    if response.status_code == 404:
                        return None
                    if response.status_code != 200:
                        raise MetadataReportError("target cover is unavailable")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
                        raise MetadataReportError("target cover is invalid")
                    return _bounded_body(response, 8 * 1024 * 1024, deadline=deadline)
        except MetadataReportError:
            raise
        except (httpx.HTTPError, OSError, ValueError) as error:
            raise MetadataReportError("target cover is unavailable") from error


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
    metadata = _mapping(media.get("metadata"))
    title_value = metadata.get("title")
    title = validate_observed_title(title_value)
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
    files = tuple(_file(value, service_path) for value in files_source)
    identities = tuple(value.identity for value in files)
    if len(set(identities)) != len(identities):
        raise MetadataReportError("target file identities are duplicated")
    media_kind = "ebook" if files else "audiobook" if type(media.get("audioFiles")) is list else "unsupported"
    cover_path = root.get("coverPath")
    cover = None
    if cover_path is not None:
        if type(cover_path) is not str or not cover_path.startswith("/") or len(cover_path) > 1024:
            raise MetadataReportError("target response is invalid")
        cover = TargetCover(hashlib.sha256(cover_path.encode()).hexdigest(), cover_path)
    return TargetSnapshot(media_kind, title, files, cover)


def _file(source: object, service_path: str) -> TargetFile:
    item = _mapping(source)
    metadata = _mapping(item.get("metadata"))
    format_value = item.get("ebookFormat")
    if type(format_value) is not str:
        extension = metadata.get("ext")
        format_value = extension[1:] if type(extension) is str and extension.startswith(".") else ""
    format_value = format_value.lower()
    if not 1 <= len(format_value) <= 16 or not format_value.isascii() or not format_value.isalnum():
        raise MetadataReportError("target file mapping is invalid")
    relative = metadata.get("relPath")
    path = metadata.get("path")
    has_relative = type(relative) is str and bool(relative)
    if relative is not None and not has_relative:
        raise MetadataReportError("target file mapping is invalid")
    from_path = None
    if path is not None:
        if type(path) is not str:
            raise MetadataReportError("target file mapping is invalid")
        from_path = _relative_service_path(path, service_path)
    if not has_relative:
        if from_path is None:
            raise MetadataReportError("target file mapping is invalid")
        relative = from_path
    elif from_path is not None and from_path != relative:
        raise MetadataReportError("target file mapping is invalid")
    normalized = posixpath.normpath(relative)
    if not 1 <= len(relative) <= 1024 or normalized != relative or relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/")):
        raise MetadataReportError("target file mapping is invalid")
    size = metadata.get("size")
    inode = item.get("ino", metadata.get("ino"))
    if type(size) is not int or size < 0 or type(inode) is not str or not 1 <= len(inode) <= 256:
        raise MetadataReportError("target file identity is invalid")
    identity = hashlib.sha256(canonical_json({"format": format_value, "inode": inode, "relative": relative, "size": size}).encode("utf-8")).hexdigest()
    return TargetFile(identity, relative, format_value)


def _relative_service_path(path: str, service_path: str) -> str:
    if service_path == "/":
        if not path.startswith("/") or path == "/":
            raise MetadataReportError("target file mapping is invalid")
        return path[1:]
    prefix = service_path + "/"
    if not path.startswith(prefix):
        raise MetadataReportError("target file mapping is invalid")
    return path[len(prefix) :]


def _bounded_body(response: httpx.Response, maximum: int, *, deadline: float | None = None) -> bytes:
    chunks: list[bytes] = []
    length = 0
    for chunk in response.iter_bytes():
        if deadline is not None and time.monotonic() >= deadline:
            raise MetadataReportError("target cover timed out")
        length += len(chunk)
        if length > maximum:
            raise MetadataReportError("target response is oversized")
        chunks.append(chunk)
    if not chunks:
        raise MetadataReportError("target response is invalid")
    return b"".join(chunks)


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
