"""Bounded Audiobookshelf v2.35.1 book-catalogue reader."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from mechanical_librarian.application.catalogue import CatalogueEntry, CatalogueError
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
)
from mechanical_librarian.domain.metadata_report import (
    canonical_json,
    validate_observed_title,
)


class AudiobookshelfCatalogueReader:
    def __init__(self, *, client_factory: Callable[[str], httpx.Client], mounts: dict[str, MediaMountConfiguration], maximum_bytes: int, timeout_seconds: int) -> None:
        self._client_factory = client_factory
        self._mounts = mounts
        self._maximum_bytes = maximum_bytes
        self._timeout_seconds = timeout_seconds

    def read_library(self, connection: str, library: LibraryConfiguration) -> tuple[CatalogueEntry, ...]:
        try:
            with self._client_factory(connection) as client:
                libraries = _get(client, "/api/libraries", self._maximum_bytes, self._timeout_seconds)
                if not any(type(value) is dict and value.get("id") == library.library_id and value.get("mediaType") == "book" for value in libraries.get("libraries", [])):
                    raise CatalogueError("configured library is unavailable")
                summaries: list[dict[str, Any]] = []
                page = 0
                total: int | None = None
                while True:
                    payload = _get(client, f"/api/libraries/{quote(library.library_id, safe='')}/items", self._maximum_bytes, self._timeout_seconds, {"page": str(page), "limit": "100", "minified": "1", "collapsed": "0"})
                    rows = payload.get("results")
                    if type(rows) is not list or len(rows) > 100:
                        raise CatalogueError("catalogue response is invalid")
                    reported = payload.get("total")
                    if type(reported) is not int or reported < 0 or reported > 10_000:
                        raise CatalogueError("catalogue limit exceeded")
                    total = reported if total is None else total
                    if total != reported or len(summaries) + len(rows) > total:
                        raise CatalogueError("catalogue response is invalid")
                    if any(type(row) is not dict for row in rows):
                        raise CatalogueError("catalogue response is invalid")
                    summaries.extend(rows)
                    if len(summaries) == total:
                        break
                    if not rows:
                        raise CatalogueError("catalogue response is invalid")
                    page += 1
                    if page > 100:
                        raise CatalogueError("catalogue limit exceeded")
                mount = None if library.mount is None else self._mounts[library.mount]
                return tuple(_entry(connection, library, mount, summary, _get(client, f"/api/items/{quote(str(summary.get('id')), safe='')}", self._maximum_bytes, self._timeout_seconds, {"expanded": "1"})) for summary in summaries)
        except CatalogueError:
            raise
        except (httpx.HTTPError, OSError, UnicodeError, ValueError, TypeError) as error:
            raise CatalogueError("catalogue service unavailable") from error


def _get(client: httpx.Client, path: str, maximum: int, timeout: int, params: dict[str, str] | None = None) -> dict[str, Any]:
    response = client.get(path, params=params, headers={"Accept": "application/json", "Accept-Encoding": "identity"}, timeout=timeout)
    if response.status_code != 200 or response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise CatalogueError("catalogue service unavailable")
    if len(response.content) > maximum:
        raise CatalogueError("catalogue response is oversized")
    try:
        value = json.loads(response.content.decode("utf-8", "strict"))
    except (UnicodeError, ValueError):
        raise CatalogueError("catalogue response is invalid") from None
    if type(value) is not dict:
        raise CatalogueError("catalogue response is invalid")
    return value


def _entry(connection: str, library: LibraryConfiguration, mount: MediaMountConfiguration | None, summary: dict[str, Any], detail: dict[str, Any]) -> CatalogueEntry:
    item_id = summary.get("id")
    if type(item_id) is not str or detail.get("id") != item_id or summary.get("libraryId") != library.library_id or detail.get("libraryId") != library.library_id or summary.get("updatedAt") != detail.get("updatedAt") or type(detail.get("updatedAt")) is not int:
        raise CatalogueError("catalogue item changed")
    media = detail.get("media")
    if type(media) is not dict:
        raise CatalogueError("catalogue response is invalid")
    metadata = media.get("metadata")
    if type(metadata) is not dict:
        raise CatalogueError("catalogue response is invalid")
    title = validate_observed_title(metadata.get("title"))
    authors = metadata.get("authors", [])
    if type(authors) is not list or len(authors) > 32 or any(type(value) is not str or not 1 <= len(value) <= 256 for value in authors):
        raise CatalogueError("catalogue response is invalid")
    ebook = media.get("ebookFile")
    plural = media.get("ebookFiles")
    has_audio = type(media.get("audioFiles")) is list
    if ebook is None and plural is None:
        classification = "none"; file_identity = service_path = format_value = mount_name = None; has_ebook = False
    elif type(ebook) is dict and plural is None:
        has_ebook = True
        metadata_file = ebook.get("metadata")
        fmt = ebook.get("ebookFormat")
        ino = ebook.get("ino")
        relative = metadata_file.get("relPath") if type(metadata_file) is dict else None
        if type(fmt) is not str or fmt.lower() != "epub" or type(ino) is not str or type(relative) is not str or not relative or relative.startswith("/") or ".." in relative.split("/"):
            classification = "needs-attention"; file_identity = service_path = format_value = mount_name = None
        else:
            classification = "singular-supported" if mount is not None else "needs-attention"
            file_identity = hashlib.sha256(canonical_json({"ino": ino, "path": relative, "format": "epub"}).encode()).hexdigest()
            service_path = relative; format_value = "epub"; mount_name = None if mount is None else mount.name
    else:
        classification = "needs-attention"; file_identity = service_path = format_value = mount_name = None; has_ebook = True
    return CatalogueEntry(connection, library.name, library.library_id, item_id, detail["updatedAt"], title, tuple(authors), has_ebook, has_audio, classification, file_identity, service_path, format_value, mount_name)


__all__ = ["AudiobookshelfCatalogueReader"]
