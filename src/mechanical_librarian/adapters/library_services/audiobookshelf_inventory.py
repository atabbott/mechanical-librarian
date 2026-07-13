"""Bounded, read-only Audiobookshelf 2.35.1 inventory traversal."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from mechanical_librarian.application.inventory_reconciliation import (
    InventoryConnectionVerification,
    InventorySource,
)
from mechanical_librarian.application.ports import (
    FailureKind,
    OperationUsage,
    OperationContext,
    PortFailure,
    PortResult,
    RetryAdvice,
    SafeSummary,
)
from mechanical_librarian.domain.identifiers import (
    AdapterVersion,
    ExternalItemId,
    LibraryId,
    SchemaVersion,
    ServiceVersion,
    TargetId,
    canonical_json,
    is_canonical_uuid4,
)
from mechanical_librarian.domain.inventory import (
    ItemLifecycle,
    LibraryConnection,
    MediaType,
)
from mechanical_librarian.domain.inventory_v2 import (
    FieldObservation,
    InventoryObservation,
    InventoryPass,
    InventorySnapshotV2,
    ObservationField,
    ObservationState,
    ScanBounds,
    ScanUsage,
)


_SERVICE_VERSION = ServiceVersion("2.35.1")
_ADAPTER_VERSION = AdapterVersion("abs-inventory-v1")
_NORMALIZER_VERSION = SchemaVersion("abs-book-normalizer-v1")
_MAX_FIELD_LENGTH = 131_072
_MAX_VALUES = 1_000


class AudiobookshelfInventoryReader(InventorySource):
    """Read accessible book-library pages and expanded item details only."""

    def __init__(
        self,
        *,
        client_factory: Callable[[], PortResult[httpx.Client]],
        connection_verifier: Callable[
            [
                LibraryConnection,
                tuple[LibraryId, ...],
                ScanBounds,
                OperationContext,
            ],
            InventoryConnectionVerification,
        ]
        | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._connection_verifier = connection_verifier

    def verify_connection(
        self,
        connection: LibraryConnection,
        libraries: tuple[LibraryId, ...],
        bounds: ScanBounds,
        context: OperationContext,
    ) -> InventoryConnectionVerification:
        if self._connection_verifier is None:
            return InventoryConnectionVerification(ScanUsage())
        return self._connection_verifier(connection, libraries, bounds, context)

    def verify_library(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        context: OperationContext,
    ) -> PortResult[ScanUsage]:
        invalid = _validate_request(connection, library, bounds, context)
        if invalid is not None:
            return PortResult.failed(invalid)
        response = self._get(
            connection, "/api/libraries", None, bounds.max_bytes, context
        )
        if response.failure is not None:
            return PortResult.failed(response.failure)
        value, size = response.value
        if not isinstance(value, dict) or not isinstance(value.get("libraries"), list):
            return _failed(
                FailureKind.INVALID_RESPONSE, "inventory library response invalid"
            )
        matches = []
        seen: set[str] = set()
        for item in value["libraries"]:
            if not isinstance(item, dict) or not is_canonical_uuid4(item.get("id")):
                return _failed(
                    FailureKind.INVALID_RESPONSE, "inventory library response invalid"
                )
            if item["id"] in seen:
                return _failed(
                    FailureKind.CONFLICT, "inventory library response unstable"
                )
            seen.add(item["id"])
            if item["id"] == library.value:
                matches.append(item)
        if len(matches) != 1:
            return _failed(
                FailureKind.NOT_FOUND, "configured inventory library unavailable"
            )
        if matches[0].get("mediaType") != "book":
            return _failed(
                FailureKind.UNSUPPORTED, "configured inventory library unsupported"
            )
        return PortResult.success(ScanUsage(response_bytes=size))

    def read_pass(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        context: OperationContext,
    ) -> PortResult[InventoryPass]:
        invalid = _validate_request(connection, library, bounds, context)
        if invalid is not None:
            return PortResult.failed(invalid)
        snapshots: list[InventorySnapshotV2] = []
        seen: set[str] = set()
        page_signatures: set[str] = set()
        response_bytes = 0
        page_number = 0
        expected_total: int | None = None
        recent_item = False

        while True:
            if page_number >= bounds.max_pages:
                return _failed(
                    FailureKind.RESOURCE_EXHAUSTED, "inventory page bound exhausted"
                )
            query = {
                "limit": str(bounds.page_size),
                "page": str(page_number),
                "sort": "addedAt",
                "desc": "0",
                "minified": "1",
                "collapseseries": "0",
            }
            page = self._get(
                connection,
                f"/api/libraries/{library.value}/items",
                query,
                bounds.max_bytes - response_bytes,
                context,
            )
            if page.failure is not None:
                return PortResult.failed(
                    _add_usage(
                        page.failure,
                        ScanUsage(page_number, len(snapshots), response_bytes),
                    )
                )
            payload, size = page.value
            response_bytes += size
            parsed = _page(payload, page_number, bounds.page_size)
            if parsed is None:
                return _failed(
                    FailureKind.INVALID_RESPONSE, "inventory page response invalid"
                )
            results, total = parsed
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                return _failed(FailureKind.CONFLICT, "inventory page response unstable")
            signature = hashlib.sha256(
                canonical_json([item.get("id") for item in results]).encode("utf-8")
            ).hexdigest()
            if results and signature in page_signatures:
                return _failed(FailureKind.CONFLICT, "inventory page cycle detected")
            page_signatures.add(signature)
            page_number += 1

            for summary in results:
                if len(seen) >= bounds.max_items:
                    return _failed(
                        FailureKind.RESOURCE_EXHAUSTED, "inventory item bound exhausted"
                    )
                identity = _summary_identity(summary, library)
                if identity is None:
                    return _failed(
                        FailureKind.INVALID_RESPONSE, "inventory item summary invalid"
                    )
                if identity in seen:
                    return _failed(FailureKind.CONFLICT, "inventory item duplicated")
                seen.add(identity)
                detail = self._get(
                    connection,
                    f"/api/items/{identity}",
                    {"expanded": "1"},
                    bounds.max_bytes - response_bytes,
                    context,
                )
                if detail.failure is not None:
                    return PortResult.failed(
                        _add_usage(
                            detail.failure,
                            ScanUsage(page_number, len(snapshots), response_bytes),
                        )
                    )
                detail_value, detail_size = detail.value
                response_bytes += detail_size
                try:
                    normalized = _normalize(
                        detail_value,
                        summary,
                        connection,
                        library,
                        context,
                    )
                except (ValueError, TypeError, RecursionError, OverflowError):
                    return _failed(
                        FailureKind.INVALID_RESPONSE,
                        "inventory item normalization invalid",
                    )
                if normalized.failure is not None:
                    return PortResult.failed(normalized.failure)
                snapshot, updated_millis = normalized.value
                snapshots.append(snapshot)
                threshold = context.time.now() - timedelta(
                    seconds=bounds.stability_delay_seconds
                )
                if (
                    updated_millis is not None
                    and updated_millis / 1000 >= threshold.timestamp()
                ):
                    recent_item = True

            if len(seen) > total:
                return _failed(FailureKind.CONFLICT, "inventory page response unstable")
            if not results or len(seen) == total:
                break
            if len(results) != bounds.page_size:
                return _failed(
                    FailureKind.INVALID_RESPONSE, "inventory page response incomplete"
                )

        if expected_total != len(seen):
            return _failed(
                FailureKind.INVALID_RESPONSE, "inventory page response incomplete"
            )
        return PortResult.success(
            InventoryPass(
                tuple(snapshots),
                ScanUsage(page_number, len(snapshots), response_bytes),
                recent_item,
            )
        )

    def _get(
        self,
        connection: LibraryConnection,
        path: str,
        query: Mapping[str, str] | None,
        remaining_bytes: int,
        context: OperationContext,
    ) -> PortResult[tuple[object, int]]:
        if remaining_bytes <= 0:
            return _failed(
                FailureKind.RESOURCE_EXHAUSTED, "inventory response bound exhausted"
            )
        checkpoint = context.preflight_result(item_count=1, serialized_bytes=0)
        if checkpoint.failure is not None:
            return PortResult.failed(checkpoint.failure)
        client_result = self._client_factory()
        if client_result.failure is not None or not isinstance(
            client_result.value, httpx.Client
        ):
            return (
                PortResult.failed(client_result.failure)
                if client_result.failure is not None
                else _failed(FailureKind.UNAVAILABLE, "inventory client unavailable")
            )
        consumed_bytes = 0
        try:
            with client_result.value as client:
                client.cookies.clear()
                remaining_seconds = max(
                    0.001,
                    (context.deadline.at - context.time.now()).total_seconds(),
                )
                configured_timeout = client.timeout
                with client.stream(
                    "GET",
                    connection.endpoint.value + path,
                    params=query,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    timeout=httpx.Timeout(
                        connect=_clipped_timeout(
                            configured_timeout.connect, remaining_seconds
                        ),
                        read=_clipped_timeout(
                            configured_timeout.read, remaining_seconds
                        ),
                        write=_clipped_timeout(
                            configured_timeout.write, remaining_seconds
                        ),
                        pool=_clipped_timeout(
                            configured_timeout.pool, remaining_seconds
                        ),
                    ),
                ) as response:
                    checkpoint = context.preflight_result(
                        item_count=1, serialized_bytes=0
                    )
                    if checkpoint.failure is not None:
                        return PortResult.failed(checkpoint.failure)
                    status_failure = _status_failure(
                        response.status_code,
                        path=path,
                        retry_after=response.headers.get("retry-after"),
                        now=context.time.now(),
                    )
                    if status_failure is not None:
                        return PortResult.failed(status_failure)
                    if (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .lower()
                        != "application/json"
                    ):
                        return _failed(
                            FailureKind.INVALID_RESPONSE,
                            "inventory response type invalid",
                        )
                    if response.headers.get(
                        "content-encoding", ""
                    ).strip().lower() not in {"", "identity"}:
                        return _failed(
                            FailureKind.INVALID_RESPONSE,
                            "inventory response encoding invalid",
                        )
                    declared = response.headers.get("content-length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except ValueError:
                            return _failed(
                                FailureKind.INVALID_RESPONSE,
                                "inventory response invalid",
                            )
                        if declared_size < 0 or declared_size > remaining_bytes:
                            return _failed(
                                FailureKind.RESOURCE_EXHAUSTED,
                                "inventory response bound exhausted",
                            )
                    payload = bytearray()
                    for chunk in response.iter_raw():
                        consumed_bytes += len(chunk)
                        if len(payload) + len(chunk) > remaining_bytes:
                            return _failed(
                                FailureKind.RESOURCE_EXHAUSTED,
                                "inventory response bound exhausted",
                                usage=ScanUsage(response_bytes=consumed_bytes),
                            )
                        checkpoint = context.preflight_result(
                            item_count=1, serialized_bytes=len(payload) + len(chunk)
                        )
                        if checkpoint.failure is not None:
                            return PortResult.failed(checkpoint.failure)
                        payload.extend(chunk)
                    try:
                        value = json.loads(
                            bytes(payload).decode("utf-8"),
                            object_pairs_hook=_unique_object,
                            parse_constant=_reject_json_constant,
                        )
                    except (UnicodeError, ValueError, RecursionError):
                        return _failed(
                            FailureKind.INVALID_RESPONSE,
                            "inventory response invalid",
                            usage=ScanUsage(response_bytes=consumed_bytes),
                        )
                    return PortResult.success((value, len(payload)))
        except httpx.TimeoutException:
            return _failed(
                FailureKind.TIMEOUT,
                "inventory read timed out",
                retryable=True,
                usage=ScanUsage(response_bytes=consumed_bytes),
            )
        except (httpx.RequestError, httpx.StreamError, OSError):
            return _failed(
                FailureKind.UNAVAILABLE,
                "inventory target unavailable",
                retryable=True,
                usage=ScanUsage(response_bytes=consumed_bytes),
            )


def _normalize(
    detail: object,
    summary: Mapping[str, object],
    connection: LibraryConnection,
    library: LibraryId,
    context: OperationContext,
) -> PortResult[tuple[InventorySnapshotV2, int | None]]:
    if not isinstance(detail, dict):
        return _failed(FailureKind.INVALID_RESPONSE, "inventory item detail invalid")
    identity = summary["id"]
    if detail.get("id") != identity or detail.get("libraryId") != library.value:
        return _failed(FailureKind.CONFLICT, "inventory item detail changed")
    if any(
        detail.get(name) != summary.get(name)
        for name in ("updatedAt", "isMissing", "isInvalid")
    ):
        return _failed(FailureKind.CONFLICT, "inventory item detail changed")
    if detail.get("mediaType") != "book" or not isinstance(detail.get("media"), dict):
        return _failed(FailureKind.UNSUPPORTED, "inventory media type unsupported")
    media = detail["media"]
    metadata = media.get("metadata")
    if not isinstance(metadata, dict):
        return _failed(FailureKind.INVALID_RESPONSE, "inventory item metadata invalid")
    included_audio, _ = _included_audio_files(media)
    ebook_file = media.get("ebookFile")
    if included_audio:
        media_type = MediaType.AUDIOBOOK
    elif isinstance(ebook_file, dict):
        media_type = MediaType.EBOOK
    else:
        return _failed(FailureKind.UNSUPPORTED, "inventory item files unsupported")

    file_objects, file_state = _file_objects(media)
    fields = (
        _scalar(metadata, "title", ObservationField.TITLE),
        _scalar(metadata, "subtitle", ObservationField.SUBTITLE),
        _structured(metadata, "authors", ObservationField.AUTHORS, ("id", "name")),
        _structured(metadata, "narrators", ObservationField.NARRATORS, ("id", "name")),
        _structured(
            metadata, "series", ObservationField.SERIES, ("id", "name", "sequence")
        ),
        _identifiers(metadata),
        _scalar(metadata, "publishedDate", ObservationField.PUBLISHED_DATE),
        _scalar(
            metadata,
            "publishedYear",
            ObservationField.PUBLISHED_YEAR,
            allow_integer=True,
        ),
        _scalar(metadata, "publisher", ObservationField.PUBLISHER),
        _scalar(metadata, "description", ObservationField.DESCRIPTION),
        _strings(metadata, "genres", ObservationField.GENRES),
        _scalar(metadata, "language", ObservationField.LANGUAGE),
        _files_field(file_objects, file_state),
        _cover_field(detail, media),
        _number(media, "duration", ObservationField.DURATION),
        _structured(
            media,
            "chapters",
            ObservationField.CHAPTERS,
            ("id", "start", "end", "title"),
        ),
        _number(detail, "updatedAt", ObservationField.UPDATED_AT, integer=True),
    )
    warnings = tuple(
        f"{item.field.value}_{item.state.value}"
        for item in fields
        if item.state in {ObservationState.MALFORMED, ObservationState.UNSUPPORTED}
    )
    if _identifiers_quarantined(metadata):
        warnings += ("identifiers_quarantined",)
    if file_state is ObservationState.PRESENT and _files_quarantined(media):
        warnings += ("files_quarantined",)
    files = _file_identities(file_objects)
    cover = _cover_identity(detail, media)
    target = TargetId(
        connection.adapter,
        connection.id,
        library,
        ExternalItemId(str(identity)),
    )
    try:
        observation = InventoryObservation.create(
            target=target,
            media_type=media_type,
            lifecycle=(
                ItemLifecycle.UNAVAILABLE
                if summary["isMissing"] or summary["isInvalid"]
                else ItemLifecycle.ACTIVE
            ),
            fields=fields,
            file_identities=files,
            cover_identity=cover,
            adapter_version=_ADAPTER_VERSION,
            normalizer_version=_NORMALIZER_VERSION,
            service_version=_SERVICE_VERSION,
            warnings=warnings,
            observed_at=context.observed_at,
        )
    except ValueError:
        return _failed(
            FailureKind.INVALID_RESPONSE, "inventory item normalization invalid"
        )
    return PortResult.success(
        (InventorySnapshotV2.create(observation), int(summary["updatedAt"]))
    )


def _page(
    value: object, page: int, limit: int
) -> tuple[list[dict[str, object]], int] | None:
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        return None
    raw_page = value.get("page")
    raw_limit = value.get("limit")
    if not _exact_page_value(raw_page, page) or not _exact_page_value(raw_limit, limit):
        return None
    total = value.get("total")
    if type(total) is not int or total < 0:
        return None
    results = value["results"]
    if len(results) > limit or any(not isinstance(item, dict) for item in results):
        return None
    return results, total


def _exact_page_value(value: object, expected: int) -> bool:
    return (type(value) is int and value == expected) or (
        type(value) is str and value == str(expected)
    )


def _summary_identity(summary: object, library: LibraryId) -> str | None:
    if not isinstance(summary, dict) or not is_canonical_uuid4(summary.get("id")):
        return None
    if summary.get("libraryId") != library.value or summary.get("mediaType") != "book":
        return None
    if (
        type(summary.get("updatedAt")) is not int
        or summary["updatedAt"] < 0
        or type(summary.get("isMissing")) is not bool
        or type(summary.get("isInvalid")) is not bool
    ):
        return None
    return str(summary["id"])


def _scalar(
    source: Mapping[str, object],
    key: str,
    field: ObservationField,
    *,
    allow_integer: bool = False,
) -> FieldObservation:
    if key not in source:
        return FieldObservation(field, ObservationState.MISSING)
    value = source[key]
    if value is None:
        return FieldObservation(field, ObservationState.NULL)
    if type(value) is not str and not (allow_integer and type(value) is int):
        return FieldObservation(field, ObservationState.MALFORMED)
    text = str(value).strip()
    if not text:
        return FieldObservation(field, ObservationState.EMPTY)
    if len(text) > _MAX_FIELD_LENGTH or "\x00" in text:
        return FieldObservation(field, ObservationState.MALFORMED)
    return FieldObservation(
        field, ObservationState.PRESENT, (text,), (canonical_json(value),)
    )


def _number(
    source: Mapping[str, object],
    key: str,
    field: ObservationField,
    *,
    integer: bool = False,
) -> FieldObservation:
    if key not in source:
        return FieldObservation(field, ObservationState.MISSING)
    value = source[key]
    if value is None:
        return FieldObservation(field, ObservationState.NULL)
    valid = type(value) is int if integer else type(value) in {int, float}
    if not valid or value < 0 or (type(value) is float and not math.isfinite(value)):
        return FieldObservation(field, ObservationState.MALFORMED)
    return FieldObservation(
        field, ObservationState.PRESENT, (str(value),), (canonical_json(value),)
    )


def _strings(
    source: Mapping[str, object], key: str, field: ObservationField
) -> FieldObservation:
    if key not in source:
        return FieldObservation(field, ObservationState.MISSING)
    value = source[key]
    if value is None:
        return FieldObservation(field, ObservationState.NULL)
    if not isinstance(value, list):
        return FieldObservation(field, ObservationState.MALFORMED)
    if not value:
        return FieldObservation(field, ObservationState.EMPTY)
    if len(value) > _MAX_VALUES:
        return FieldObservation(field, ObservationState.MALFORMED)
    values = tuple(item.strip() for item in value if type(item) is str and item.strip())
    if len(values) != len(value) or any(
        len(item) > _MAX_FIELD_LENGTH for item in values
    ):
        return FieldObservation(field, ObservationState.MALFORMED)
    return FieldObservation(
        field,
        ObservationState.PRESENT,
        values,
        tuple(canonical_json(item) for item in value),
    )


def _structured(
    source: Mapping[str, object],
    key: str,
    field: ObservationField,
    allowed: tuple[str, ...],
) -> FieldObservation:
    if key not in source:
        return FieldObservation(field, ObservationState.MISSING)
    value = source[key]
    if value is None:
        return FieldObservation(field, ObservationState.NULL)
    if not isinstance(value, list):
        return FieldObservation(field, ObservationState.MALFORMED)
    if not value:
        return FieldObservation(field, ObservationState.EMPTY)
    if len(value) > _MAX_VALUES:
        return FieldObservation(field, ObservationState.MALFORMED)
    normalized: list[str] = []
    raw: list[str] = []
    for item in value:
        if type(item) is str and item.strip():
            raw.append(canonical_json(item))
            normalized.append(canonical_json({"name": item.strip()}))
            continue
        if not isinstance(item, dict):
            return FieldObservation(field, ObservationState.MALFORMED)
        projected = {name: item[name] for name in allowed if name in item}
        if not projected or any(
            not _finite_scalar(part) for part in projected.values()
        ):
            return FieldObservation(field, ObservationState.MALFORMED)
        raw.append(canonical_json(projected))
        normalized.append(canonical_json(projected))
    return FieldObservation(
        field, ObservationState.PRESENT, tuple(normalized), tuple(raw)
    )


def _identifiers(metadata: Mapping[str, object]) -> FieldObservation:
    # Book.toJSON() in the frozen 2.35.1 contract establishes only these keys.
    names = ("asin", "isbn")
    present = [(name, metadata[name]) for name in names if name in metadata]
    if not present:
        return FieldObservation(ObservationField.IDENTIFIERS, ObservationState.MISSING)
    states = tuple(_identifier_state(value) for _, value in present)
    valid: list[tuple[str, str]] = [
        (name, str(value))
        for (name, value), state in zip(present, states, strict=True)
        if state is ObservationState.PRESENT
    ]
    if valid:
        values = tuple(
            canonical_json({"kind": name, "value": value.strip()})
            for name, value in valid
        )
        raw = tuple(
            canonical_json({"kind": name, "value": value}) for name, value in valid
        )
        return FieldObservation(
            ObservationField.IDENTIFIERS,
            ObservationState.PRESENT,
            values,
            raw,
        )
    if all(state is ObservationState.NULL for state in states):
        return FieldObservation(ObservationField.IDENTIFIERS, ObservationState.NULL)
    if all(state is ObservationState.EMPTY for state in states):
        return FieldObservation(ObservationField.IDENTIFIERS, ObservationState.EMPTY)
    return FieldObservation(ObservationField.IDENTIFIERS, ObservationState.MALFORMED)


def _identifier_state(value: object) -> ObservationState:
    if value is None:
        return ObservationState.NULL
    if type(value) is not str:
        return ObservationState.MALFORMED
    if not value.strip():
        return ObservationState.EMPTY
    if len(value) > 4_096 or "\x00" in value:
        return ObservationState.MALFORMED
    return ObservationState.PRESENT


def _identifiers_quarantined(metadata: Mapping[str, object]) -> bool:
    states = tuple(
        _identifier_state(metadata[name])
        for name in ("asin", "isbn")
        if name in metadata
    )
    return ObservationState.PRESENT in states and any(
        state is not ObservationState.PRESENT for state in states
    )


def _file_objects(
    media: Mapping[str, object],
) -> tuple[list[Mapping[str, object]], ObservationState]:
    values: list[Mapping[str, object]] = []
    audio, malformed = _included_audio_files(media)
    audio_present = "audioFiles" in media
    ebook_present = "ebookFile" in media
    for item in audio:
        if not _has_file_identity(item):
            malformed = True
        else:
            values.append(item)
    ebook = media.get("ebookFile")
    if isinstance(ebook, dict):
        if _has_file_identity(ebook):
            values.append(ebook)
        else:
            malformed = True
    elif ebook is not None:
        malformed = True
    if values:
        state = ObservationState.PRESENT
    elif malformed:
        state = ObservationState.MALFORMED
    elif not audio_present and not ebook_present:
        state = ObservationState.MISSING
    elif media.get("audioFiles") is None and ebook is None:
        state = ObservationState.NULL
    elif media.get("audioFiles") == [] and ebook is None:
        state = ObservationState.EMPTY
    else:
        state = ObservationState.UNSUPPORTED
    return values, state


def _files_quarantined(media: Mapping[str, object]) -> bool:
    audio, malformed = _included_audio_files(media)
    if any(_file_has_invalid_optional_fact(item) for item in audio):
        malformed = True
    ebook = media.get("ebookFile")
    if isinstance(ebook, dict):
        malformed = malformed or _file_has_invalid_optional_fact(ebook)
    elif ebook is not None:
        malformed = True
    return malformed


def _included_audio_files(
    media: Mapping[str, object],
) -> tuple[list[Mapping[str, object]], bool]:
    if "audioFiles" not in media or media["audioFiles"] is None:
        return [], False
    raw = media["audioFiles"]
    if not isinstance(raw, list):
        return [], True
    included: list[Mapping[str, object]] = []
    malformed = False
    for item in raw:
        if not isinstance(item, dict):
            malformed = True
            continue
        if item.get("exclude") is True:
            continue
        if "exclude" in item and item["exclude"] is not False:
            malformed = True
            continue
        included.append(item)
    return included, malformed


def _files_field(
    values: list[Mapping[str, object]], state: ObservationState
) -> FieldObservation:
    if state is not ObservationState.PRESENT:
        return FieldObservation(ObservationField.FILES, state)
    return FieldObservation(
        ObservationField.FILES,
        ObservationState.PRESENT,
        tuple(sorted({_file_descriptor(item) for item in values})),
    )


def _file_descriptor(item: Mapping[str, object]) -> str:
    descriptor: dict[str, object] = {"identity": _file_identity(item)}
    aliases = {
        "index": "index",
        "format": "format",
        "duration": "duration_seconds",
        "codec": "codec",
        "bitRate": "bit_rate",
        "mimeType": "media_type",
        "ebookFormat": "format",
    }
    for source, destination in aliases.items():
        value = item.get(source)
        if _finite_scalar(value) and len(str(value)) <= 256:
            descriptor[destination] = value
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        nested_aliases = {
            "ext": "extension",
            "size": "size_bytes",
            "mtimeMs": "modified_ms",
            "ctimeMs": "changed_ms",
            "birthtimeMs": "created_ms",
        }
        for source, destination in nested_aliases.items():
            value = metadata.get(source)
            if _finite_scalar(value) and len(str(value)) <= 256:
                descriptor[destination] = value
    return canonical_json(descriptor)


def _file_identities(values: list[Mapping[str, object]]) -> tuple[str, ...]:
    return tuple(sorted({_file_identity(item) for item in values}))


_FILE_IDENTITY_FIELDS = (
    "ino",
    "index",
    "format",
    "duration",
    "codec",
    "bitRate",
    "mimeType",
    "ebookFormat",
)

_FILE_METADATA_IDENTITY_FIELDS = (
    "filename",
    "ext",
    "path",
    "relPath",
    "size",
    "mtimeMs",
    "ctimeMs",
    "birthtimeMs",
)

_STABLE_FILE_IDENTITY_FIELDS = ("ino",)
_STABLE_FILE_METADATA_FIELDS = ("filename", "path", "relPath")


def _file_identity(item: Mapping[str, object]) -> str:
    projected: dict[str, object] = {
        name: item[name]
        for name in _FILE_IDENTITY_FIELDS
        if name in item and _finite_scalar(item[name])
    }
    metadata = item.get("metadata")
    if isinstance(metadata, Mapping):
        nested = {
            name: metadata[name]
            for name in _FILE_METADATA_IDENTITY_FIELDS
            if name in metadata and _finite_scalar(metadata[name])
        }
        if nested:
            projected["metadata"] = nested
    if not projected:
        raise ValueError("inventory file identity is unsupported")
    return _identity(projected)


def _has_file_identity(item: Mapping[str, object]) -> bool:
    metadata = item.get("metadata")
    stable = any(
        name in item and _finite_scalar(item[name]) and str(item[name])
        for name in _STABLE_FILE_IDENTITY_FIELDS
    ) or (
        isinstance(metadata, Mapping)
        and any(
            name in metadata and _finite_scalar(metadata[name]) and str(metadata[name])
            for name in _STABLE_FILE_METADATA_FIELDS
        )
    )
    if not stable:
        return False
    try:
        _file_identity(item)
    except ValueError:
        return False
    return True


def _file_has_invalid_optional_fact(item: Mapping[str, object]) -> bool:
    if "metadata" in item and not isinstance(item["metadata"], Mapping):
        return True
    if any(
        name in item and not _finite_scalar(item[name])
        for name in _FILE_IDENTITY_FIELDS
    ):
        return True
    metadata = item.get("metadata")
    return isinstance(metadata, Mapping) and any(
        name in metadata and not _finite_scalar(metadata[name])
        for name in _FILE_METADATA_IDENTITY_FIELDS
    )


def _finite_scalar(value: object) -> bool:
    return type(value) in {str, int} or (type(value) is float and math.isfinite(value))


def _cover_value(_detail: Mapping[str, object], media: Mapping[str, object]) -> object:
    return media["coverPath"] if "coverPath" in media else None


def _cover_field(
    _detail: Mapping[str, object], media: Mapping[str, object]
) -> FieldObservation:
    if "coverPath" not in media:
        return FieldObservation(ObservationField.COVER, ObservationState.MISSING)
    value = media["coverPath"]
    if value is None:
        return FieldObservation(ObservationField.COVER, ObservationState.NULL)
    if type(value) is not str:
        return FieldObservation(ObservationField.COVER, ObservationState.MALFORMED)
    if not value:
        return FieldObservation(ObservationField.COVER, ObservationState.EMPTY)
    return FieldObservation(
        ObservationField.COVER, ObservationState.PRESENT, (_identity(value),)
    )


def _cover_identity(
    _detail: Mapping[str, object], media: Mapping[str, object]
) -> str | None:
    value = media.get("coverPath") if "coverPath" in media else None
    return _identity(value) if type(value) is str and value else None


def _identity(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _clipped_timeout(configured: float | None, remaining: float) -> float:
    return remaining if configured is None else min(configured, remaining)


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _validate_request(
    connection: object, library: object, bounds: object, context: object
) -> PortFailure | None:
    if (
        type(connection) is not LibraryConnection
        or connection.adapter.value != "audiobookshelf"
        or type(library) is not LibraryId
        or not is_canonical_uuid4(library.value)
        or type(bounds) is not ScanBounds
        or type(context) is not OperationContext
    ):
        return _failure(FailureKind.INVALID_REQUEST, "inventory request invalid")
    return None


def _status_failure(
    status: int,
    *,
    path: str = "",
    retry_after: str | None = None,
    now: datetime | None = None,
) -> PortFailure | None:
    if status == 200:
        return None
    if status == 401:
        return _failure(FailureKind.AUTHENTICATION, "inventory authentication failed")
    if status == 403:
        return _failure(FailureKind.AUTHORIZATION, "inventory authorization failed")
    if status == 404:
        if path.startswith("/api/items/"):
            return _failure(
                FailureKind.CONFLICT,
                "inventory item changed during scan",
            )
        return _failure(FailureKind.NOT_FOUND, "inventory resource unavailable")
    if status == 408:
        return _failure(FailureKind.TIMEOUT, "inventory read timed out", retryable=True)
    if status == 429:
        return _failure(
            FailureKind.RATE_LIMITED,
            "inventory read rate limited",
            retryable=True,
            retry_after=_retry_after(retry_after, now),
        )
    if 300 <= status < 400:
        return _failure(FailureKind.INVALID_RESPONSE, "inventory redirect rejected")
    if status >= 500:
        return _failure(
            FailureKind.UNAVAILABLE, "inventory target unavailable", retryable=True
        )
    return _failure(FailureKind.INVALID_RESPONSE, "inventory response invalid")


def _failure(
    kind: FailureKind,
    summary: str,
    *,
    retryable: bool = False,
    retry_after: timedelta | None = None,
    usage: ScanUsage = ScanUsage(),
) -> PortFailure:
    return PortFailure(
        kind,
        RetryAdvice(retryable, retry_after),
        SafeSummary(summary),
        OperationUsage(usage.pages, usage.items, usage.response_bytes),
    )


def _retry_after(value: str | None, now: datetime | None) -> timedelta | None:
    if value is None or len(value) > 128:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.isascii() and candidate.isdecimal():
        seconds = int(candidate)
        delay = timedelta(seconds=seconds)
    else:
        if now is None or now.tzinfo is None or now.utcoffset() is None:
            return None
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        delay = parsed.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    return delay if timedelta(0) < delay <= timedelta(days=1) else None


def _failed(
    kind: FailureKind,
    summary: str,
    *,
    retryable: bool = False,
    usage: ScanUsage = ScanUsage(),
) -> PortResult[Any]:
    return PortResult.failed(
        _failure(kind, summary, retryable=retryable, usage=usage)
    )


def _add_usage(failure: PortFailure, usage: ScanUsage) -> PortFailure:
    return PortFailure(
        failure.kind,
        failure.retry,
        failure.safe_summary,
        OperationUsage(
            failure.usage.pages + usage.pages,
            failure.usage.items + usage.items,
            failure.usage.response_bytes + usage.response_bytes,
        ),
    )


__all__ = ["AudiobookshelfInventoryReader"]
