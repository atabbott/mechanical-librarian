"""Small safety primitives shared by provider-local retry loops."""

from __future__ import annotations

import errno
import math
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from enum import Enum
from typing import Callable, TypeVar

import httpcore
import httpx

from mechanical_librarian.domain.metadata_report import (
    ProviderRequestAccounting,
    ProviderStatus,
)

_SAFE_OS_ERRNOS = frozenset(
    {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EPIPE,
        errno.ETIMEDOUT,
    }
)


_Result = TypeVar("_Result")


class ProviderRequestPacer:
    """Serialize request starts behind one shared provider-authority clock."""

    def __init__(
        self,
        minimum_interval_seconds: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            type(minimum_interval_seconds) not in {int, float}
            or isinstance(minimum_interval_seconds, bool)
            or not 0 < minimum_interval_seconds <= 60
        ):
            raise ValueError("provider pacing interval is invalid")
        self._minimum_interval_seconds = float(minimum_interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._last_start: float | None = None
        self._lock = threading.Lock()

    def wait(self, deadline: float | None = None) -> bool:
        with self._lock:
            now = self._clock()
            delay = (
                0.0
                if self._last_start is None
                else max(
                    0.0,
                    self._minimum_interval_seconds - (now - self._last_start),
                )
            )
            if deadline is not None and now + delay >= deadline:
                return False
            if delay:
                self._sleep(delay)
            now = self._clock()
            if deadline is not None and now >= deadline:
                return False
            self._last_start = now
            return True


class ProviderIncompleteCause(str, Enum):
    """Closed, process-local reasons that prevent a provider dispatch."""

    DEADLINE = "deadline"
    AGGREGATE_BYTES = "aggregate_bytes"
    DISPATCH_COUNT = "dispatch_count"


@dataclass(frozen=True, slots=True)
class ProviderIncompleteCauseRecord:
    """Aggregate-safe evidence for one undispatched provider request."""

    cause: ProviderIncompleteCause


class _DiscoveryRequestLedger:
    """Keep one provider observation's semantically identical lane work private.

    The key deliberately contains only provider-controlled request semantics.  It
    never retains local references, credentials, raw response content, or the
    caller's lane identity.  Values are immutable provider observations, so a
    later lane can only replay the exact completed result.
    """

    def __init__(self, authority: str, revision: str) -> None:
        self._authority = authority
        self._revision = revision
        self._completed: dict[tuple[object, ...], object] = {}
        self._pending: set[tuple[object, ...]] = set()
        self._registered: dict[tuple[object, ...], int] = {}
        self._unresolved: dict[tuple[object, ...], int] = {}
        self.logical = 0
        self.initial = 0
        self.replays = 0
        self.undispatched_incomplete = 0
        self.retry_dispatches = 0

    def dispatch(self, *, retry: bool) -> None:
        if retry:
            self.retry_dispatches += 1
        else:
            self.initial += 1

    def _key(
        self,
        *,
        kind: str,
        matched_family: str | None,
        title: str | None,
        authors: tuple[str, ...],
        projection: str,
    ) -> tuple[object, ...]:
        # ISBN and work requests have different canonical arguments.  Sorting
        # author names matches the providers' set-like discovery contract while
        # retaining the explicit operation discriminator.
        return (
            self._authority,
            self._revision,
            kind,
            projection,
            matched_family if kind == "isbn" else None,
            title if kind == "work" else None,
            tuple(sorted(authors)) if kind == "work" else (),
        )

    def register(self, **arguments: object) -> None:
        self._register(self._key(**arguments))  # type: ignore[arg-type]

    def _register(self, key: tuple[object, ...]) -> None:
        self.logical += 1
        self._registered[key] = self._registered.get(key, 0) + 1
        self._unresolved[key] = self._unresolved.get(key, 0) + 1
        if key not in self._completed:
            self._pending.add(key)

    def allows_retry(self, attempts: int, ceiling: int) -> bool:
        return attempts + len(self._pending) < ceiling

    def _resolve_occurrence(self, key: tuple[object, ...]) -> None:
        pending = self._unresolved.get(key, 0)
        if pending < 1:
            raise RuntimeError("request occurrence is not registered")
        if pending == 1:
            del self._unresolved[key]
        else:
            self._unresolved[key] = pending - 1

    def register_detail(self, identity: str) -> None:
        self._register((self._authority, self._revision, "detail", identity))

    def begin_detail(self, identity: str) -> None:
        """Reserve compatibility for the detail dispatch boundary."""
        self._pending.discard((self._authority, self._revision, "detail", identity))

    def resolve(
        self,
        *,
        kind: str,
        matched_family: str | None,
        title: str | None,
        authors: tuple[str, ...],
        projection: str,
        execute: Callable[[], _Result],
    ) -> tuple[_Result, bool]:
        key = self._key(
            kind=kind,
            matched_family=matched_family,
            title=title,
            authors=authors,
            projection=projection,
        )
        if self._unresolved.get(key, 0) == 0:
            self._register(key)
        self._resolve_occurrence(key)
        completed = self._completed.get(key)
        if completed is not None:
            self.replays += 1
            return completed, True  # type: ignore[return-value]
        self._pending.discard(key)
        initial_before = self.initial
        result = execute()
        self._completed[key] = result
        if self.initial == initial_before:
            self.undispatched_incomplete += 1
        return result, False

    def resolve_detail(
        self, identity: str, *, execute: Callable[[], _Result]
    ) -> tuple[_Result, bool]:
        key = (self._authority, self._revision, "detail", identity)
        if self._unresolved.get(key, 0) == 0:
            self._register(key)
        self._resolve_occurrence(key)
        completed = self._completed.get(key)
        if completed is not None:
            self.replays += 1
            return completed, True  # type: ignore[return-value]
        self._pending.discard(key)
        initial_before = self.initial
        result = execute()
        self._completed[key] = result
        if self.initial == initial_before:
            self.undispatched_incomplete += 1
        return result, False

    def finalize_pending_details(self) -> None:
        """Close reserved detail work that a terminal sibling prevents sending."""
        details = tuple(sorted(key for key in self._unresolved if key[2] == "detail"))
        self._pending.difference_update(details)
        self.undispatched_incomplete += sum(
            self._unresolved.pop(key) for key in details
        )

    def accounting(self) -> ProviderRequestAccounting:
        """Return closed aggregate-safe request accounting, never request data."""
        return ProviderRequestAccounting(
            logical_requests=self.logical,
            initial_dispatches=self.initial,
            retry_dispatches=self.retry_dispatches,
            replays=self.replays,
            undispatched_incomplete=self.undispatched_incomplete,
        )


@dataclass(frozen=True, slots=True)
class ProviderAttemptRecord:
    """Aggregate-safe, process-local evidence for one provider dispatch."""

    provider: str
    ordinal: int
    category: str
    delay_seconds: float | None
    elapsed_seconds: float
    outcome: str


class ProviderFailure(Exception):
    """One provider dispatch's terminal outcome, carried out of its retry loop.

    Each metadata adapter raises and catches this locally.  The identity is
    shared so the three adapters cannot drift on what a provider failure
    carries; which statuses, categories and bounds an adapter attaches to it
    remain that adapter's own and are unchanged.
    """

    def __init__(
        self,
        status: ProviderStatus,
        *,
        category: str = "provider_failure",
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(status.value)
        self.status = status
        self.category = category
        self.retryable = retryable
        self.retry_after = retry_after


def transient_transport_failure(error: BaseException) -> str | None:
    """Classify only transport failures whose types prove transientness."""

    causes = _bounded_cause_chain(error)
    if causes is None or any(isinstance(cause, ssl.SSLError) for cause in causes):
        return None
    if type(error) is httpx.ConnectTimeout:
        return "transport_connect_timeout"
    if type(error) is httpx.ReadTimeout:
        return "transport_read_timeout"
    typed: dict[type[BaseException], str] = {
        httpx.ConnectError: "transport_connect_error",
        httpx.ReadError: "transport_read_error",
        httpx.WriteError: "transport_write_error",
        httpx.RemoteProtocolError: "transport_remote_protocol_error",
    }
    category = typed.get(type(error))
    if category is None:
        return None
    if type(error) is httpx.RemoteProtocolError and any(
        type(cause) is httpcore.RemoteProtocolError for cause in causes
    ):
        return category
    return category if any(_is_safe_cause(cause) for cause in causes) else None


def bounded_retry_after(value: str | None, *, now: float) -> float | None:
    """Return a valid Retry-After delay bounded to one minute."""

    if value is None or type(value) is not str or len(value) > 64:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.isascii() and normalized.isdigit():
        if len(normalized) > 2:
            return None
        seconds: float = int(normalized)
        return float(seconds) if seconds <= 60 else None
    try:
        parsed = datetime.strptime(normalized, "%a, %d %b %Y %H:%M:%S GMT").replace(
            tzinfo=timezone.utc
        )
        if format_datetime(parsed, usegmt=True) != normalized:
            return None
        seconds = parsed.timestamp() - now
    except (OverflowError, ValueError):
        return None
    return seconds if 0.0 <= seconds <= 60.0 else None


def bounded_attempt_elapsed(started: float, finished: float) -> float:
    """Normalize attempt timing to the fixed provider horizon."""

    elapsed = finished - started
    if not math.isfinite(elapsed) or elapsed <= 0.0:
        return 0.0
    return min(elapsed, 60.0)


def _bounded_cause_chain(
    error: BaseException,
) -> tuple[BaseException, ...] | None:
    """Return a short, complete cause chain or reject ambiguous nesting."""

    seen = {id(error)}
    current = error.__cause__
    causes: list[BaseException] = []
    for _ in range(4):
        if current is None:
            return tuple(causes)
        if id(current) in seen:
            return None
        seen.add(id(current))
        causes.append(current)
        current = current.__cause__
    return None if current is not None else tuple(causes)


def _is_safe_cause(cause: BaseException) -> bool:
    if type(cause) is socket.gaierror:
        return (
            cause.errno == socket.EAI_AGAIN if socket.EAI_AGAIN is not None else False
        )
    if isinstance(
        cause,
        (ConnectionAbortedError, ConnectionResetError, BrokenPipeError),
    ):
        return True
    if type(cause) is TimeoutError:
        return True
    return isinstance(cause, OSError) and cause.errno in _SAFE_OS_ERRNOS


__all__ = [
    "_DiscoveryRequestLedger",
    "ProviderAttemptRecord",
    "ProviderFailure",
    "ProviderIncompleteCause",
    "ProviderIncompleteCauseRecord",
    "bounded_attempt_elapsed",
    "bounded_retry_after",
    "transient_transport_failure",
]
