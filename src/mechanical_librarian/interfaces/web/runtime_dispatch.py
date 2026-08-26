"""One-listener dispatch with request-scoped runtime generations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, TypeVar

Scope = Any
Receive = Callable[[], Awaitable[Any]]
Send = Callable[[Any], Awaitable[None]]
ASGIApplication = Callable[[Scope, Receive, Send], Awaitable[None]]
_Result = TypeVar("_Result")


async def _unavailable(scope: Scope, receive: Receive, send: Send) -> None:
    if scope.get("type") != "http":
        return
    await send(
        {
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": b"unavailable\n"})


@dataclass(slots=True)
class RuntimeGeneration:
    """An independently owned ASGI target and its admission lifecycle."""

    application: ASGIApplication
    start_dormant: Callable[[], None] = lambda: None
    stop_admission: Callable[[], None] = lambda: None
    resume_admission: Callable[[], None] = lambda: None
    admit: Callable[[], None] = lambda: None
    begin_drain: Callable[[], None] = lambda: None
    close: Callable[[], None] = lambda: None
    shutdown_seconds: int = 30
    _active: int = field(default=0, init=False)
    _started: bool = field(default=False, init=False)
    _admitting: bool = field(default=False, init=False)
    _draining: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)

    @property
    def admitting(self) -> bool:
        return self._admitting


class MutableRuntimeDispatcher:
    """Capture one target for each admitted HTTP request before dispatching it."""

    def __init__(
        self,
        initial: RuntimeGeneration,
        request_shutdown: Callable[[], None] = lambda: None,
        apply_shutdown_budget: Callable[[int], None] = lambda _seconds: None,
    ) -> None:
        self._current = initial
        self._unavailable = RuntimeGeneration(_unavailable)
        self._unavailable._started = True
        self._unavailable._admitting = True
        self._lock = RLock()
        self._request_shutdown = request_shutdown
        self._apply_shutdown_budget = apply_shutdown_budget

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "lifespan":
            await self._lifespan(receive, send)
            return
        generation = self._capture()
        try:
            await generation.application(scope, receive, send)
        finally:
            self._release(generation)

    def bind_outer_server(
        self,
        request_shutdown: Callable[[], None],
        apply_shutdown_budget: Callable[[int], None],
    ) -> None:
        """Bind the one listener owner before it can be updated or stopped."""
        with self._lock:
            self._request_shutdown = request_shutdown
            self._apply_shutdown_budget = apply_shutdown_budget

    def current(self) -> RuntimeGeneration:
        with self._lock:
            return self._current

    def prepare(self, generation: RuntimeGeneration) -> None:
        """Perform every fallible generation start step without admission."""
        if generation._started or generation._closed:
            raise RuntimeError("runtime generation preparation is invalid")
        try:
            generation.start_dormant()
        except BaseException:
            generation.close()
            generation._closed = True
            raise
        generation._started = True

    def transition(
        self,
        expected: RuntimeGeneration,
        successor: RuntimeGeneration,
        commit: Callable[[], _Result],
    ) -> _Result:
        """Commit and publish with captures and scheduler admissions serialized."""
        if not successor._started or successor._admitting or successor._closed:
            raise RuntimeError("runtime successor is unavailable")
        result: _Result
        with self._lock:
            if self._current is not expected or not expected._admitting:
                successor.close()
                successor._closed = True
                raise RuntimeError("runtime generation is stale")
            predecessor = expected
            try:
                predecessor.stop_admission()
            except BaseException:
                self._fail_closed_locked(predecessor, successor)
                raise
            predecessor._admitting = False
            try:
                result = commit()
            except BaseException:
                try:
                    predecessor.resume_admission()
                    predecessor._admitting = True
                except BaseException:
                    self._fail_closed_locked(predecessor, successor)
                else:
                    successor.close()
                    successor._closed = True
                raise
            try:
                self._current = successor
                self._apply_shutdown_budget(successor.shutdown_seconds)
                successor.admit()
                successor._admitting = True
            except BaseException:
                self._fail_closed_locked(predecessor, successor)
                raise
            predecessor._draining = True
        try:
            predecessor.begin_drain()
        except BaseException:
            self._request_shutdown()
            raise
        self._close_if_drained(predecessor)
        return result

    def start(self) -> None:
        """Prepare and admit the initial generation before listener admission."""
        generation = self._current
        self.prepare(generation)
        try:
            self._apply_shutdown_budget(generation.shutdown_seconds)
            generation.admit()
        except BaseException:
            generation.close()
            generation._closed = True
            raise
        generation._admitting = True

    def _fail_closed_locked(self, *generations: RuntimeGeneration) -> None:
        """Make an impossible post-commit lifecycle fault externally inert."""
        self._current = self._unavailable
        for generation in generations:
            try:
                generation.stop_admission()
            except BaseException:
                pass
            generation._admitting = False
            generation._draining = True
        try:
            self._request_shutdown()
        except BaseException:
            pass
        for generation in generations:
            try:
                generation.begin_drain()
            except BaseException:
                pass
            if generation._active == 0 and not generation._closed:
                generation._closed = True
                try:
                    generation.close()
                except BaseException:
                    pass

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        """Own lifespan once; generations start and stop independently."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                with self._lock:
                    generation = self._current
                    generation._admitting = False
                    generation._draining = True
                generation.stop_admission()
                generation.begin_drain()
                self._close_if_drained(generation)
                await send({"type": "lifespan.shutdown.complete"})
                return

    def _capture(self) -> RuntimeGeneration:
        with self._lock:
            generation = (
                self._current if self._current._admitting else self._unavailable
            )
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
