"""Bounded Audiobookshelf v2.35.1 book-catalogue reader."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, cast
from urllib.parse import quote

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_ebook_location import (
    EbookLocation,
    normalize_ebook_location,
)
from mechanical_librarian.application.catalogue import (
    CatalogueEntry,
    CatalogueError,
    TargetConnectionError,
)
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
)
from mechanical_librarian.domain.metadata_report import (
    AUDIO_FILE_FORMATS,
    MAX_AUDIO_TRACKS,
    ReportValueError,
    audio_set_identity,
    canonical_json,
    validate_observed_title,
)


def target_connection_code(error: BaseException) -> str:
    """Classify only the closed, owner-actionable target transport outcome."""
    if isinstance(error, httpx.TransportError):
        current: BaseException | None = error
        while current is not None:
            if "SSL" in type(current).__name__.upper():
                return "tls"
            current = current.__cause__
    return "unavailable"


class AudiobookshelfCatalogueReader:
    def __init__(
        self,
        *,
        client_factory: Callable[[str], httpx.Client],
        mounts: dict[str, MediaMountConfiguration],
        maximum_bytes: int,
        timeout_seconds: int,
    ) -> None:
        self._client_factory = client_factory
        self._mounts = mounts
        self._maximum_bytes = maximum_bytes
        self._timeout_seconds = timeout_seconds

    def read_library(
        self, connection: str, library: LibraryConfiguration, remaining: int
    ) -> tuple[CatalogueEntry, ...]:
        if type(remaining) is not int or not 0 <= remaining <= 10_000:
            raise CatalogueError("catalogue limit exceeded")
        try:
            with self._client_factory(connection) as client:
                libraries = _get(
                    client, "/api/libraries", self._maximum_bytes, self._timeout_seconds
                )
                if library.library_id not in _book_library_ids(libraries):
                    raise CatalogueError("configured library is unavailable")
                summaries: list[dict[str, Any]] = []
                page = 0
                total: int | None = None
                while True:
                    payload = _get(
                        client,
                        f"/api/libraries/{quote(library.library_id, safe='')}/items",
                        self._maximum_bytes,
                        self._timeout_seconds,
                        {
                            "page": str(page),
                            "limit": "100",
                            "minified": "1",
                            "collapsed": "0",
                        },
                    )
                    rows = payload.get("results")
                    if type(rows) is not list or len(rows) > 100:
                        raise CatalogueError("catalogue response is invalid")
                    reported = payload.get("total")
                    if (
                        type(reported) is not int
                        or reported < 0
                        or reported > remaining
                    ):
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
                return tuple(
                    _entry(
                        connection,
                        library,
                        mount,
                        summary,
                        _get(
                            client,
                            f"/api/items/{quote(str(summary.get('id')), safe='')}",
                            self._maximum_bytes,
                            self._timeout_seconds,
                            {"expanded": "1"},
                        ),
                    )
                    for summary in summaries
                )
        except CatalogueError:
            raise
        except httpx.TransportError as error:
            # httpx keeps certificate verification enabled in the configured
            # target client.  Classifying the outcome must not weaken it.
            raise TargetConnectionError(target_connection_code(error)) from None
        except (httpx.HTTPError, OSError):
            raise TargetConnectionError("unavailable") from None

    def probe_connection(
        self, connection: str, libraries: tuple[LibraryConfiguration, ...]
    ) -> None:
        """Perform the bounded configured ABS trust/read authority probe."""
        try:
            with self._client_factory(connection) as client:
                payload = _get(
                    client,
                    "/api/libraries",
                    self._maximum_bytes,
                    self._timeout_seconds,
                )
                available = _book_library_ids(payload)
                if any(library.library_id not in available for library in libraries):
                    # Deliberately retain only the closed owner-actionable
                    # condition; configured IDs never cross this boundary.
                    raise TargetConnectionError("unavailable")
        except TargetConnectionError:
            raise
        except Exception as error:
            raise TargetConnectionError(target_connection_code(error)) from None


def _get(
    client: httpx.Client,
    path: str,
    maximum: int,
    timeout: int,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    with client.stream(
        "GET",
        path,
        params=params,
        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        timeout=timeout,
    ) as response:
        if response.status_code == 401:
            raise TargetConnectionError("authentication")
        if response.status_code == 403:
            raise TargetConnectionError("authorization")
        if (
            response.status_code != 200
            or response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            raise TargetConnectionError("unavailable")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > maximum:
                raise CatalogueError("catalogue response is oversized")
            chunks.append(chunk)
        payload = b"".join(chunks)
    try:
        _json_depth(payload)
        value = json.loads(
            payload.decode("utf-8", "strict"),
            object_pairs_hook=_unique,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise CatalogueError("catalogue response is invalid") from None
    if type(value) is not dict:
        raise CatalogueError("catalogue response is invalid")
    return value


def _entry(
    connection: str,
    library: LibraryConfiguration,
    mount: MediaMountConfiguration | None,
    summary: dict[str, Any],
    detail: dict[str, Any],
) -> CatalogueEntry:
    item_id = summary.get("id")
    if (
        type(item_id) is not str
        or detail.get("id") != item_id
        or summary.get("libraryId") != library.library_id
        or detail.get("libraryId") != library.library_id
        or summary.get("updatedAt") != detail.get("updatedAt")
        or type(detail.get("updatedAt")) is not int
    ):
        raise CatalogueError("catalogue item changed")
    media = detail.get("media")
    if type(media) is not dict:
        raise CatalogueError("catalogue response is invalid")
    metadata = media.get("metadata")
    if type(metadata) is not dict:
        raise CatalogueError("catalogue response is invalid")
    title, invalid_title = _observed_catalogue_title(metadata.get("title"))
    authors = metadata.get("authors", [])
    if type(authors) is not list or len(authors) > 32:
        raise CatalogueError("catalogue response is invalid")
    names = tuple(
        value
        if type(value) is str
        else value.get("name")
        if type(value) is dict
        else None
        for value in authors
    )
    if any(type(value) is not str or not 1 <= len(value) <= 256 for value in names):
        raise CatalogueError("catalogue response is invalid")
    if (
        summary.get("isMissing") is True
        or summary.get("isInvalid") is True
        or detail.get("isMissing") is True
        or detail.get("isInvalid") is True
    ):
        raise CatalogueError("catalogue item is unavailable")
    ebook = media.get("ebookFile")
    plural = media.get("ebookFiles")
    audio_revisions: tuple[object, ...] = (None,)
    has_audio = type(media.get("audioFiles")) is list
    if ebook is None and (plural is None or plural == []):
        has_ebook = False
        # An audiobook library reaches preparation only through a named
        # read-only mount, and as one item however many files that item holds,
        # which mirrors the singular-supported posture the ebook branch below
        # already holds. An unmounted audiobook library keeps exactly the
        # disposition it had before F43: nothing to prepare, and nothing to
        # attend to.
        audio_mount = mount
        # Each audio file carries its own revision exactly as an EPUB does, and
        # those are the signals that move when the media changes.  Without them
        # the fingerprint falls back to the item timestamp, which Audiobookshelf
        # also advances on playback -- so listening to an audiobook would
        # re-prepare it, re-hashing multiple gigabytes for files nobody wrote.
        audio_revisions = _audio_set_metadata(media.get("audioFiles"))
        audio_locations = (
            ()
            if audio_mount is None
            else _audio_set_locations(media.get("audioFiles"), audio_mount, detail)
        )
        if not audio_locations or audio_mount is None:
            classification = "none"
            file_identity = service_path = format_value = mount_name = None
        else:
            # One item is one preparable unit whether it holds one file or
            # three hundred, so the classification is unchanged and the
            # identity is the ordered set's.
            classification = "singular-supported"
            file_identity = audio_set_identity(
                tuple(value.identity for value in audio_locations)
            )
            service_path = audio_locations[0].relative_path
            format_value = audio_locations[0].format
            mount_name = audio_mount.name
    elif type(ebook) is dict and plural is None:
        has_ebook = True
        audio_revisions = (ebook.get("metadata"),)
        try:
            location = normalize_ebook_location(
                ebook,
                service_path=None if mount is None else mount.service_path,
                item=detail,
            )
        except ValueError:
            classification = "needs-attention"
            file_identity = service_path = format_value = mount_name = None
        else:
            if location.format != "epub":
                classification = "needs-attention"
                file_identity = service_path = format_value = mount_name = None
            else:
                classification = "singular-supported"
                file_identity = location.identity
                service_path = location.relative_path
                format_value = location.format
                mount_name = None if mount is None else mount.name
    else:
        classification = "needs-attention"
        file_identity = service_path = format_value = mount_name = None
        has_ebook = True
        audio_revisions = (None,)
    if invalid_title:
        # This is deliberately an item disposition, never a target-health
        # condition.  Dropping the usable location prevents report preparation
        # from rereading the untrimmed title through its stricter item boundary.
        classification = "needs-attention"
        file_identity = service_path = format_value = mount_name = None
    reviewed_fields = {
        name: {
            "present": name in metadata,
            "value": (
                _fingerprint_observed_title(metadata.get(name))
                if name == "title"
                else metadata.get(name)
            ),
        }
        for name in (
            "title",
            "subtitle",
            "authors",
            "narrators",
            "series",
            "genres",
            "tags",
            "publishedYear",
            "publishedDate",
            "publisher",
            "description",
            "isbn",
            "asin",
            "language",
            "explicit",
        )
    }
    cover_reference = next(
        (
            value
            for value in (
                detail.get("coverPath"),
                media.get("coverPath"),
                metadata.get("coverPath"),
            )
            if type(value) is str
        ),
        None,
    )
    cover_field = {
        "present": "coverPath" in detail,
        "value": detail.get("coverPath"),
    }
    revisions = tuple(_file_revision(record) for record in audio_revisions)
    # A one-file item keeps the exact revision shape it had before multi-file
    # items were admitted, so every existing single-file fingerprint is
    # unchanged; beyond one file the ordered list is what moves when any track
    # does.
    media_revision: object = revisions[0] if len(revisions) == 1 else list(revisions)
    # Audiobookshelf's item timestamp is also advanced by playback activity, so
    # prefer the file revision when it is available.  Without one, retain the
    # item timestamp as the conservative material-change signal.  A cover path
    # has no independent revision in the supported payload, so its bytes also
    # require that fallback -- but only where this build acquires them.  An
    # audiobook's art is recorded as presence and never acquired, so a cover
    # path does not make its fingerprint depend on a timestamp playback moves.
    item_revision_fallback = (
        detail["updatedAt"]
        if not all(revisions) or (cover_reference is not None and has_ebook)
        else None
    )
    source_fingerprint = hashlib.sha256(
        canonical_json(
            {
                "schema": "audiobookshelf-review-source-v1",
                "identity": {"item_id": item_id, "library_id": library.library_id},
                "metadata": reviewed_fields,
                "cover": {
                    "field": cover_field,
                    "selected_reference": cover_reference,
                },
                "media": {
                    "file_identity": file_identity,
                    "has_audio": has_audio,
                    "has_ebook": has_ebook,
                    "revision": media_revision,
                    "item_revision_fallback": item_revision_fallback,
                },
            }
        ).encode("utf-8")
    ).hexdigest()
    # `names` was rejected above unless every element is a 1..256 character str.
    return CatalogueEntry(
        connection,
        library.name,
        library.library_id,
        item_id,
        detail["updatedAt"],
        title,
        cast("tuple[str, ...]", names),
        has_ebook,
        has_audio,
        classification,
        file_identity,
        service_path,
        format_value,
        mount_name,
        source_fingerprint,
    )


def _audio_set_metadata(value: object) -> tuple[object, ...]:
    """Return every track's revision record, in the service's own order.

    One record per track, because a set is only as current as its least
    current member: a list holding one empty record still falls back to the
    item timestamp, exactly as a single missing revision always did.
    """
    if type(value) is not list or not value:
        return (None,)
    return tuple(
        record.get("metadata") if type(record) is dict else None
        for record in value
        if not (type(record) is dict and record.get("exclude") is True)
    ) or (None,)


def _file_revision(record: object) -> dict[str, object]:
    return {
        name: value
        for name in ("mtimeMs", "mtime", "modifiedAt", "lastModified")
        if type(record) is dict
        and (value := record.get(name)) is not None
        and (
            (type(value) is int and value >= 0)
            or (type(value) is str and 1 <= len(value) <= 128)
        )
    }


def _audio_set_locations(
    value: object, mount: MediaMountConfiguration, detail: dict[str, Any]
) -> tuple[EbookLocation, ...]:
    """Admit one item's whole ordered audio set, or nothing at all.

    The location grammar itself is format-neutral -- it agrees a service
    path, a format token, and an identity -- so the audio branch reuses it
    rather than restating it, and narrows only which formats it will accept.
    A set with one unusable member is admitted as nothing rather than as a
    shorter set: a report bound to a subset of the recording is bound to a
    recording the owner does not have.
    """
    if type(value) is not list or not 1 <= len(value) <= MAX_AUDIO_TRACKS:
        return ()
    locations: list[EbookLocation] = []
    for record in value:
        if type(record) is dict and record.get("exclude") is True:
            continue
        try:
            location = normalize_ebook_location(
                record, service_path=mount.service_path, item=detail
            )
        except ValueError:
            return ()
        if location.format not in AUDIO_FILE_FORMATS:
            return ()
        locations.append(location)
    identities = {value.identity for value in locations}
    if len(identities) != len(locations):
        return ()
    return tuple(locations)


def _observed_catalogue_title(value: object) -> tuple[str, bool]:
    """Project one target title without promoting item data to target health."""
    try:
        return validate_observed_title(value), False
    except ReportValueError:
        if type(value) is str:
            try:
                return validate_observed_title(value.strip()), True
            except ReportValueError:
                pass
        return "Untitled item requiring attention", True


def _fingerprint_observed_title(value: object) -> object:
    """Keep a title current without requiring unsafe text to be encodable."""
    encoded = canonical_json(value)
    try:
        encoded.encode("utf-8")
    except UnicodeEncodeError:
        return {
            "encoding": "utf-8-surrogatepass-sha256",
            "digest": hashlib.sha256(
                encoded.encode("utf-8", "surrogatepass")
            ).hexdigest(),
        }
    return value


def _book_library_ids(payload: dict[str, Any]) -> set[str]:
    values = payload.get("libraries")
    if type(values) is not list:
        raise CatalogueError("catalogue response is invalid")
    return {
        value["id"]
        for value in values
        if type(value) is dict
        and type(value.get("id")) is str
        and value.get("mediaType") == "book"
    }


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate field")
        value[key] = item
    return value


def _reject_constant(_: str) -> float:
    raise ValueError("non-finite JSON")


def _json_depth(payload: bytes) -> None:
    """Reject syntactically JSON-like nesting beyond the service protocol cap."""
    depth = 0
    quoted = False
    escaped = False
    for byte in payload:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 100:
                raise CatalogueError("catalogue response is invalid")
        elif byte in (93, 125):
            depth -= 1
            if depth < 0:
                raise CatalogueError("catalogue response is invalid")
    if quoted or escaped or depth:
        raise CatalogueError("catalogue response is invalid")


__all__ = ["AudiobookshelfCatalogueReader"]
