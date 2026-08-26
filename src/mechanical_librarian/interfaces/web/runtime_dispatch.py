"""One-listener dispatch with request-scoped runtime generations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any

Scope = Any
Receive = Callable[[], Awaitable[Any]]
Send = Callable[[Any], Awaitable[None]]
ASGIApplication = Callable[[Scope, Receive, Send], Awaitable[None]]


@dataclass(slots=True)
class RuntimeGeneration:
    """An independently owned ASGI target and its drain/close lifecycle."""

    application: ASGIApplication
    start: Callable[[], None] = lambda: None
    begin_drain: Callable[[], None] = lambda: None
    close: Callable[[], None] = lambda: None
    _active: int = field(default=0, init=False)
    _draining: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)


class MutableRuntimeDispatcher:
    """Capture one target for each admitted HTTP request before dispatching it."""

    def __init__(self, initial: RuntimeGeneration) -> None:
        self._current = initial
        self._lock = RLock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "lifespan":
            await self._lifespan(receive, send)
            return
        generation = self._admit()
        try:
            await generation.application(scope, receive, send)
        finally:
            self._release(generation)

    def replace(self, successor: RuntimeGeneration) -> RuntimeGeneration:
        """Publish a pre-built successor before draining the retired target."""
        with self._lock:
            predecessor = self._current
            self._current = successor
            predecessor._draining = True
        # Candidate construction has already completed. From this point a
        # start fault is catastrophic rather than a rollback signal: authority
        # may already have committed and no request may return to setup.
        successor.start()
        predecessor.begin_drain()
        self._close_if_drained(predecessor)
        return predecessor

    def start(self) -> None:
        """Start the initial dormant generation before listener admission."""
        self._current.start()

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        """Own lifespan once; generations start and stop independently."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                with self._lock:
                    generation = self._current
                    generation._draining = True
                generation.begin_drain()
                self._close_if_drained(generation)
                await send({"type": "lifespan.shutdown.complete"})
                return

    def _admit(self) -> RuntimeGeneration:
        with self._lock:
            generation = self._current
            generation._active += 1
            return generation

    def _release(self, generation: RuntimeGeneration) -> None:
        with self._lock:
            generation._active -= 1
            if generation._active < 0:
                raise RuntimeError("runtime generation admission is invalid")
        self._close_if_drained(generation)

    def _close_if_drained(self, generation: RuntimeGeneration) -> None:
        with self._lock:
            if (
                not generation._draining
                or generation._active != 0
                or generation._closed
            ):
                return
            generation._closed = True
        generation.close()


__all__ = ["ASGIApplication", "MutableRuntimeDispatcher", "RuntimeGeneration"]
