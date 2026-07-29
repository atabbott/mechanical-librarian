"""Small safety primitives shared by provider-local retry loops."""

from __future__ import annotations

import errno
import math
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Callable, TypeVar

import httpcore
import httpx


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
        self.logical = 0
        self.initial = 0
        self.replays = 0
        self.undispatched_incomplete = 0
        self.retry_dispatches = 0

    def dispatch(self, *, retry: bool) -> None:
        if retry:
            self.retry_dispatches += 1

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
        self._pending.add(self._key(**arguments))  # type: ignore[arg-type]

    def allows_retry(self, attempts: int, ceiling: int) -> bool:
        return attempts + len(self._pending) < ceiling

    def register_detail(self, identity: str) -> None:
        self._pending.add((self._authority, self._revision, "detail", identity))

    def begin_detail(self, identity: str) -> None:
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
        self.logical += 1
        key = self._key(
            kind=kind, matched_family=matched_family, title=title,
            authors=authors, projection=projection,
        )
        completed = self._completed.get(key)
        if completed is not None:
            self.replays += 1
            return completed, True  # type: ignore[return-value]
        self._pending.discard(key)
        self.initial += 1
        result = execute()
        self._completed[key] = result
        return result, False

    def accounting(self, retries: int = 0) -> tuple[int, int, int, int, int]:
        """Return closed aggregate-safe request accounting, never request data."""
        return (
            self.logical,
            self.initial,
            self.retry_dispatches,
            self.replays,
            self.undispatched_incomplete,
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


def transient_transport_failure(error: BaseException) -> str | None:
    """Classify only transport failures whose types prove transientness."""

    causes = _bounded_cause_chain(error)
    if causes is None or any(
        isinstance(cause, ssl.SSLError) for cause in causes
    ):
        return None
    if type(error) is httpx.ConnectTimeout:
        return "transport_connect_timeout"
    if type(error) is httpx.ReadTimeout:
        return "transport_read_timeout"
    typed = {
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
        seconds = int(normalized)
        return float(seconds) if seconds <= 60 else None
    try:
        parsed = datetime.strptime(
            normalized, "%a, %d %b %Y %H:%M:%S GMT"
        ).replace(tzinfo=timezone.utc)
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
            cause.errno == socket.EAI_AGAIN
            if socket.EAI_AGAIN is not None
            else False
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
    "bounded_attempt_elapsed",
    "bounded_retry_after",
    "transient_transport_failure",
]
