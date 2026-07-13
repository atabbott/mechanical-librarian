"""Bounded read-only Audiobookshelf authorization and compatibility probe."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import httpx

from mechanical_librarian.application.ports import (
    FailureKind,
    HealthReport,
    HealthStatus,
    LibraryConnectionFacts,
    LibraryConnectionDiagnosticsRequest,
    OperationContext,
    PortFailure,
    PortResult,
    RetryAdvice,
    SafeSummary,
)
from mechanical_librarian.domain.identifiers import (
    AdapterVersion,
    CompatibilityVersion,
    LibraryId,
    ServiceVersion,
    is_canonical_uuid4,
)
from mechanical_librarian.domain.inventory import ServiceEndpoint


_MAX_RESPONSE_BYTES = 65_536
_VERSION = re.compile(
    r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$"
)
_PINNED_VERSION = "2.35.1"


class AudiobookshelfConnectionProbe:
    """Use only status and authorization routes; POST authorize is observational."""

    def __init__(
        self,
        endpoint: ServiceEndpoint,
        *,
        client_factory: Callable[[], PortResult[httpx.Client]],
        response_meter: Callable[[int], bool] | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._client_factory = client_factory
        self._response_meter = response_meter

    def probe(
        self, request: LibraryConnectionDiagnosticsRequest
    ) -> PortResult[LibraryConnectionFacts]:
        if type(request) is not LibraryConnectionDiagnosticsRequest:
            return _failed(FailureKind.INVALID_REQUEST, "connection request invalid")
        if any(
            not is_canonical_uuid4(library.value)
            for library in request.expected_libraries
        ):
            return _failed(FailureKind.INVALID_REQUEST, "connection request invalid")
        context = request.context
        checkpoint = context.preflight_result(item_count=1, serialized_bytes=0)
        if checkpoint.failure is not None:
            return PortResult.failed(checkpoint.failure)
        client_result = self._client_factory()
        if client_result.failure is not None:
            return PortResult.failed(client_result.failure)
        if not isinstance(client_result.value, httpx.Client):
            return _failed(
                FailureKind.INVALID_RESPONSE, "connection client unavailable"
            )
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        try:
            with client_result.value as client:
                client.cookies.clear()
                status = self._request_json(
                    client, "GET", self._endpoint.value + "/status", headers, context
                )
                if status.failure is not None:
                    return PortResult.failed(status.failure)
                identified, version = _status_version(status.value)
                if not identified:
                    return _failed(
                        FailureKind.INVALID_RESPONSE,
                        "connection response invalid",
                    )
                if version is None or version.value != _PINNED_VERSION:
                    return PortResult.success(
                        _incompatible_facts(version, context.observed_at)
                    )
                client.cookies.clear()
                authorize = self._request_json(
                    client,
                    "POST",
                    self._endpoint.value + "/api/authorize",
                    headers,
                    context,
                    authenticated=True,
                )
                if authorize.failure is not None:
                    return PortResult.failed(authorize.failure)
        except httpx.TimeoutException:
            return _failed(
                FailureKind.TIMEOUT, "connection probe timed out", retryable=True
            )
        except (httpx.RequestError, httpx.StreamError, OSError):
            return _failed(
                FailureKind.UNAVAILABLE, "connection target unavailable", retryable=True
            )

        return _facts(
            version,
            authorize.value,
            context.observed_at,
            request.expected_libraries,
        )

    def _request_json(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        headers: dict[str, str],
        context: OperationContext,
        *,
        authenticated: bool = False,
    ) -> PortResult[object]:
        checkpoint = context.preflight_result(item_count=1, serialized_bytes=0)
        if checkpoint.failure is not None:
            return PortResult.failed(checkpoint.failure)
        remaining = max(
            0.001,
            (context.deadline.at - context.time.now()).total_seconds(),
        )
        configured_timeout = client.timeout
        with client.stream(
            method,
            url,
            headers=headers,
            timeout=httpx.Timeout(
                connect=_clipped_timeout(configured_timeout.connect, remaining),
                read=_clipped_timeout(configured_timeout.read, remaining),
                write=_clipped_timeout(configured_timeout.write, remaining),
                pool=_clipped_timeout(configured_timeout.pool, remaining),
            ),
        ) as response:
            if response.status_code == 401:
                return _failed(
                    (
                        FailureKind.AUTHENTICATION
                        if authenticated
                        else FailureKind.INVALID_RESPONSE
                    ),
                    (
                        "connection authentication failed"
                        if authenticated
                        else "connection response invalid"
                    ),
                )
            if response.status_code == 403:
                return _failed(
                    (
                        FailureKind.AUTHORIZATION
                        if authenticated
                        else FailureKind.INVALID_RESPONSE
                    ),
                    (
                        "connection authorization failed"
                        if authenticated
                        else "connection response invalid"
                    ),
                )
            if response.status_code == 408:
                return _failed(
                    FailureKind.TIMEOUT, "connection probe timed out", retryable=True
                )
            if response.status_code == 429:
                return _failed(
                    FailureKind.RATE_LIMITED,
                    "connection probe rate limited",
                    retryable=True,
                    retry_after=_retry_after(
                        response.headers.get("retry-after"), context.time.now()
                    ),
                )
            if response.status_code in {404, 405}:
                return _failed(FailureKind.INCOMPATIBLE, "connection probe unsupported")
            if 300 <= response.status_code < 400:
                return _failed(
                    FailureKind.INVALID_RESPONSE, "connection redirect rejected"
                )
            if response.status_code >= 500:
                return _failed(
                    FailureKind.UNAVAILABLE,
                    "connection target unavailable",
                    retryable=True,
                )
            if response.status_code != 200:
                return _failed(
                    FailureKind.INVALID_RESPONSE, "connection response invalid"
                )
            content_type = (
                response.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if content_type != "application/json":
                return _failed(
                    FailureKind.INVALID_RESPONSE, "connection response invalid"
                )
            content_encoding = (
                response.headers.get("content-encoding", "").strip().lower()
            )
            if content_encoding not in {"", "identity"}:
                return _failed(
                    FailureKind.INVALID_RESPONSE, "connection response encoding invalid"
                )
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    return _failed(
                        FailureKind.INVALID_RESPONSE, "connection response invalid"
                    )
                if declared_length < 0:
                    return _failed(
                        FailureKind.INVALID_RESPONSE, "connection response invalid"
                    )
                if declared_length > _MAX_RESPONSE_BYTES:
                    return _failed(
                        FailureKind.RESOURCE_EXHAUSTED, "connection response too large"
                    )
            payload = bytearray()
            for chunk in response.iter_raw():
                if self._response_meter is not None and not self._response_meter(
                    len(chunk)
                ):
                    return _failed(
                        FailureKind.RESOURCE_EXHAUSTED,
                        "connection response too large",
                    )
                checkpoint = context.preflight_result(
                    item_count=1, serialized_bytes=len(payload) + len(chunk)
                )
                if checkpoint.failure is not None:
                    return PortResult.failed(checkpoint.failure)
                if len(payload) + len(chunk) > _MAX_RESPONSE_BYTES:
                    return _failed(
                        FailureKind.RESOURCE_EXHAUSTED, "connection response too large"
                    )
                payload.extend(chunk)
            try:
                value = json.loads(
                    bytes(payload).decode("utf-8"),
                    object_pairs_hook=_unique_object,
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeError, ValueError, RecursionError):
                return _failed(
                    FailureKind.INVALID_RESPONSE, "connection response invalid"
                )
            if not isinstance(value, dict):
                return _failed(
                    FailureKind.INVALID_RESPONSE, "connection response invalid"
                )
            return PortResult.success(value)


def _clipped_timeout(configured: float | None, remaining: float) -> float:
    return remaining if configured is None else min(configured, remaining)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _status_version(status: object) -> tuple[bool, ServiceVersion | None]:
    if not isinstance(status, dict) or status.get("app") != "audiobookshelf":
        return False, None
    raw_version = status.get("serverVersion")
    if type(raw_version) is not str or _VERSION.fullmatch(raw_version) is None:
        return True, None
    return True, ServiceVersion(raw_version)


def _incompatible_facts(
    version: ServiceVersion | None, checked_at
) -> LibraryConnectionFacts:
    return LibraryConnectionFacts(
        version,
        False,
        False,
        AdapterVersion("abs-connection-v1"),
        CompatibilityVersion("abs-2.35.1-fixture-v1"),
        HealthReport(
            HealthStatus.DEGRADED,
            checked_at,
            SafeSummary("connection compatibility unsupported"),
        ),
    )


def _facts(
    version: ServiceVersion,
    authorize: object,
    checked_at,
    expected_libraries: tuple[LibraryId, ...],
) -> PortResult[LibraryConnectionFacts]:
    if not isinstance(authorize, dict) or not isinstance(authorize.get("user"), dict):
        return _failed(FailureKind.INVALID_RESPONSE, "connection response invalid")
    user = authorize["user"]
    permissions = user.get("permissions")
    libraries = user.get("librariesAccessible")
    active = user.get("isActive") is True
    all_libraries = (
        permissions.get("accessAllLibraries") if isinstance(permissions, dict) else None
    )
    all_tags = (
        permissions.get("accessAllTags") if isinstance(permissions, dict) else None
    )
    explicit_content = (
        permissions.get("accessExplicitContent")
        if isinstance(permissions, dict)
        else None
    )
    scoped_libraries: frozenset[LibraryId] | None = None
    if isinstance(libraries, list) and libraries:
        try:
            parsed_libraries = tuple(_library_id(value) for value in libraries)
            scoped_libraries = frozenset(parsed_libraries)
        except ValueError:
            scoped_libraries = None
    positive_access = all_libraries is True or (
        all_libraries is False
        and scoped_libraries is not None
        and set(expected_libraries).issubset(scoped_libraries)
    )
    profile_complete = (
        active
        and isinstance(permissions, dict)
        and type(permissions.get("download")) is bool
        and type(all_libraries) is bool
        and all_tags is True
        and explicit_content is True
        and positive_access
    )
    download = bool(permissions.get("download")) if profile_complete else False
    return PortResult.success(
        LibraryConnectionFacts(
            version,
            profile_complete,
            download,
            AdapterVersion("abs-connection-v1"),
            CompatibilityVersion("abs-2.35.1-fixture-v1"),
            HealthReport(
                (
                    HealthStatus.HEALTHY
                    if profile_complete and version is not None
                    else HealthStatus.DEGRADED
                ),
                checked_at,
                SafeSummary(
                    "connection profile verified"
                    if profile_complete and version is not None
                    else "connection profile degraded"
                ),
            ),
        )
    )


def _library_id(value: object) -> LibraryId:
    if not is_canonical_uuid4(value):
        raise ValueError("Audiobookshelf library identifier is invalid")
    return LibraryId(value)


def _failure(
    kind: FailureKind,
    summary: str,
    *,
    retryable: bool = False,
    retry_after: timedelta | None = None,
) -> PortFailure:
    return PortFailure(
        kind, RetryAdvice(retryable, retry_after), SafeSummary(summary)
    )


def _failed(
    kind: FailureKind,
    summary: str,
    *,
    retryable: bool = False,
    retry_after: timedelta | None = None,
) -> PortResult[LibraryConnectionFacts]:
    return PortResult.failed(
        _failure(
            kind,
            summary,
            retryable=retryable,
            retry_after=retry_after,
        )
    )


def _retry_after(value: str | None, now: datetime) -> timedelta | None:
    if value is None or now.tzinfo is None or now.utcoffset() is None:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128:
        return None
    if candidate.isascii() and candidate.isdecimal():
        try:
            delay = timedelta(seconds=int(candidate))
        except (ValueError, OverflowError):
            return None
    else:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        delay = parsed.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    return delay if timedelta(0) < delay <= timedelta(days=1) else None


__all__ = ["AudiobookshelfConnectionProbe"]
