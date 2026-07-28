"""Small safety primitives shared by provider-local retry loops."""

from __future__ import annotations

import errno
import math
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime

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
    return category if _has_safe_cause(error) else None


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


def _has_safe_cause(error: BaseException) -> bool:
    seen = {id(error)}
    current = error.__cause__
    for _ in range(4):
        if current is None or id(current) in seen:
            return False
        seen.add(id(current))
        if isinstance(current, ssl.SSLError):
            return False
        if type(current) is socket.gaierror:
            return (
                current.errno == socket.EAI_AGAIN
                if socket.EAI_AGAIN is not None
                else False
            )
        if isinstance(
            current,
            (ConnectionAbortedError, ConnectionResetError, BrokenPipeError),
        ):
            return True
        if type(current) is TimeoutError:
            return True
        if isinstance(current, OSError) and current.errno in _SAFE_OS_ERRNOS:
            return True
        current = current.__cause__
    return False


__all__ = [
    "ProviderAttemptRecord",
    "bounded_attempt_elapsed",
    "bounded_retry_after",
    "transient_transport_failure",
]
