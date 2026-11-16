"""One short-lived value per owner session, held in this process only.

A value is keyed by the SHA-256 of the session token, never the token, so
nothing here can be replayed as a credential. Putting a value replaces the
session's last one, a value older than its time to live reads as absent, and
logout discards it. Nothing is written to disk: a restart discards every
value. Search state is the first consumer (#1477 S13); #1478 reuses it.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from typing import Generic, TypeVar

_Value = TypeVar("_Value")


class SessionMemory(Generic[_Value]):
    """Values per session with a time to live; bounded, thread-safe."""

    def __init__(
        self,
        ttl_seconds: float,
        clock: Callable[[], float],
        *,
        maximum_sessions: int = 256,
    ) -> None:
        if not 0 < ttl_seconds <= 86_400 or not 1 <= maximum_sessions <= 65_536:
            raise ValueError("session memory bounds are invalid")
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._maximum = maximum_sessions
        self._values: dict[str, tuple[float, _Value]] = {}
        self._lock = threading.Lock()

    def put(self, session_token: str, value: _Value) -> None:
        """Hold `value` for the session, replacing whatever it held."""
        key = _key(session_token)
        with self._lock:
            now = self._clock()
            self._expire(now)
            self._values.pop(key, None)
            if len(self._values) >= self._maximum:
                # The oldest session's value goes first: it expires soonest.
                oldest = min(self._values, key=lambda name: self._values[name][0])
                del self._values[oldest]
            self._values[key] = (now, value)

    def get(self, session_token: str) -> _Value | None:
        """The session's value, or `None` when it has none or it expired."""
        key = _key(session_token)
        with self._lock:
            held = self._values.get(key)
            if held is None:
                return None
            if self._clock() - held[0] > self._ttl:
                del self._values[key]
                return None
            return held[1]

    def discard(self, session_token: str) -> None:
        """Forget the session's value, as logout does."""
        key = _key(session_token)
        with self._lock:
            self._values.pop(key, None)

    def _expire(self, now: float) -> None:
        for name in [
            name for name, (held, _) in self._values.items() if now - held > self._ttl
        ]:
            del self._values[name]


def _key(session_token: str) -> str:
    if type(session_token) is not str or not session_token:
        raise ValueError("session token is invalid")
    return hashlib.sha256(session_token.encode("utf-8", "surrogatepass")).hexdigest()


__all__ = ["SessionMemory"]
