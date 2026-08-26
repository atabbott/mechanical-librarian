from __future__ import annotations

import asyncio
from threading import RLock
from typing import Any, cast

from mechanical_librarian.bootstrap.composition import _PreparedRuntimeActivation
from mechanical_librarian.interfaces.web.runtime_dispatch import (
    MutableRuntimeDispatcher,
    RuntimeGeneration,
)


class _ActivationController:
    def __init__(self, events: list[str]) -> None:
        self._lock = RLock()
        self.dispatcher = MutableRuntimeDispatcher(
            RuntimeGeneration(
                _unreachable,
                begin_drain=lambda: events.append("predecessor-drain"),
                close=lambda: events.append("predecessor-close"),
            )
        )
        self._candidate = RuntimeGeneration(
            _unreachable,
            start=lambda: events.append("candidate-start"),
            close=lambda: events.append("candidate-close"),
        )
        self._events = events

    def prepare(
        self, document: object, revision: int, secrets: object
    ) -> RuntimeGeneration:
        self._events.append("candidate-prepare")
        return self._candidate


async def _unreachable(scope: object, receive: object, send: object) -> None:
    raise AssertionError("generation target must not receive a request")


def _activation(events: list[str]) -> _PreparedRuntimeActivation:
    return _PreparedRuntimeActivation(
        cast(Any, _ActivationController(events)), object(), 2, ()
    )


def test_prepared_activation_publishes_only_after_persistence_scope_succeeds() -> None:
    events: list[str] = []

    with _activation(events):
        events.append("persistence-commit")

    assert events == [
        "candidate-prepare",
        "persistence-commit",
        "candidate-start",
        "predecessor-drain",
        "predecessor-close",
    ]


def test_prepared_activation_closes_candidate_when_persistence_fails() -> None:
    events: list[str] = []

    try:
        with _activation(events):
            raise RuntimeError("synthetic persistence failure")
    except RuntimeError:
        pass
    else:
        raise AssertionError("synthetic persistence failure must escape")

    assert events == ["candidate-prepare", "candidate-close"]


def test_request_keeps_its_captured_generation_through_cutover() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []

    async def first(scope: object, receive: object, send: object) -> None:
        events.append("first-start")
        entered.set()
        await release.wait()
        events.append("first-finish")

    async def second(scope: object, receive: object, send: object) -> None:
        events.append("second")

    first_generation = RuntimeGeneration(
        first,
        begin_drain=lambda: events.append("drain"),
        close=lambda: events.append("close"),
    )
    dispatcher = MutableRuntimeDispatcher(first_generation)

    async def exercise() -> None:
        request = asyncio.create_task(dispatcher({}, _receive, _send))
        await entered.wait()
        dispatcher.replace(RuntimeGeneration(second))
        await dispatcher({}, _receive, _send)
        assert events == ["first-start", "drain", "second"]
        release.set()
        await request

    asyncio.run(exercise())
    assert events == ["first-start", "drain", "second", "first-finish", "close"]


def test_post_publication_start_fault_never_reopens_the_retired_generation() -> None:
    events: list[str] = []

    async def current(scope: object, receive: object, send: object) -> None:
        events.append("current")

    async def candidate(scope: object, receive: object, send: object) -> None:
        events.append("candidate")

    dispatcher = MutableRuntimeDispatcher(RuntimeGeneration(current))
    try:
        dispatcher.replace(
            RuntimeGeneration(
                candidate, start=lambda: (_ for _ in ()).throw(ValueError())
            )
        )
    except ValueError:
        pass
    else:
        raise AssertionError("candidate start must fail")

    asyncio.run(dispatcher({}, _receive, _send))
    assert events == ["candidate"]


def test_listener_lifespan_starts_and_closes_the_owned_generation_once() -> None:
    events: list[str] = []

    async def app(scope: object, receive: object, send: object) -> None:
        raise AssertionError("lifespan must not enter a generation target")

    dispatcher = MutableRuntimeDispatcher(
        RuntimeGeneration(
            app,
            start=lambda: events.append("start"),
            begin_drain=lambda: events.append("drain"),
            close=lambda: events.append("close"),
        )
    )
    dispatcher.start()

    async def lifespan() -> list[dict[str, object]]:
        requests = iter(({"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}))
        responses: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            return next(requests)

        async def send(message: dict[str, object]) -> None:
            responses.append(message)

        await dispatcher({"type": "lifespan"}, receive, send)
        return responses

    assert asyncio.run(lifespan()) == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]
    assert events == ["start", "drain", "close"]


async def _receive() -> dict[str, object]:
    return {"type": "http.disconnect"}


async def _send(_: dict[str, object]) -> None:
    return None
