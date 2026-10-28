from __future__ import annotations

import asyncio
from typing import cast

import pytest

from mechanical_librarian.application.review_pipeline import (
    ReviewPreparationPipeline,
    ReviewPreparationSupervisor,
)
from mechanical_librarian.domain.preparation_presentation import PreparationTrigger
from mechanical_librarian.interfaces.web.runtime_dispatch import (
    MutableRuntimeDispatcher,
    RuntimeGeneration,
)


async def _app(scope: object, receive: object, send: object) -> None:
    return None


async def _receive() -> dict[str, object]:
    return {"type": "http.disconnect"}


async def _send(_: dict[str, object]) -> None:
    return None


def test_transition_orders_dormant_start_commit_publish_admit_and_drain() -> None:
    events: list[str] = []
    old = RuntimeGeneration(
        _app,
        start_dormant=lambda: events.append("old.start_dormant"),
        stop_admission=lambda: events.append("old.stop_admission"),
        admit=lambda: events.append("old.admit"),
        begin_drain=lambda: events.append("old.begin_drain"),
        close=lambda: events.append("old.close"),
    )
    new = RuntimeGeneration(
        _app,
        start_dormant=lambda: events.append("new.start_dormant"),
        admit=lambda: events.append("new.admit"),
    )
    dispatcher = MutableRuntimeDispatcher(old)
    dispatcher.start()
    dispatcher.prepare(new)
    dispatcher.transition(old, new, lambda: events.append("authority.commit"))
    assert dispatcher.current() is new
    assert events == [
        "old.start_dormant",
        "old.admit",
        "new.start_dormant",
        "old.stop_admission",
        "authority.commit",
        "new.admit",
        "old.begin_drain",
        "old.close",
    ]


def test_dormant_failure_leaves_current_and_authority_unchanged() -> None:
    old = RuntimeGeneration(_app)
    closed: list[str] = []
    new = RuntimeGeneration(
        _app,
        start_dormant=lambda: (_ for _ in ()).throw(ValueError("start")),
        close=lambda: closed.append("close"),
    )
    dispatcher = MutableRuntimeDispatcher(old)
    dispatcher.start()
    with pytest.raises(ValueError, match="start"):
        dispatcher.prepare(new)
    assert dispatcher.current() is old
    assert closed == ["close"]


def test_commit_failure_restores_predecessor_and_closes_successor() -> None:
    events: list[str] = []
    old = RuntimeGeneration(
        _app,
        stop_admission=lambda: events.append("stop"),
        resume_admission=lambda: events.append("resume"),
    )
    new = RuntimeGeneration(_app, close=lambda: events.append("close"))
    dispatcher = MutableRuntimeDispatcher(old)
    dispatcher.start()
    dispatcher.prepare(new)
    with pytest.raises(ValueError, match="commit"):
        dispatcher.transition(
            old, new, lambda: (_ for _ in ()).throw(ValueError("commit"))
        )
    assert dispatcher.current() is old
    assert old.admitting
    assert events == ["stop", "resume", "close"]


def test_partial_predecessor_stop_failure_fails_closed_without_committing() -> None:
    shutdown: list[str] = []
    resources: list[str] = []

    def partial_stop() -> None:
        resources.append("old.stop")
        raise ValueError("partial stop")

    old = RuntimeGeneration(
        _app,
        stop_admission=partial_stop,
        begin_drain=lambda: resources.append("old.drain"),
        close=lambda: resources.append("old.close"),
    )
    new = RuntimeGeneration(
        _app,
        begin_drain=lambda: resources.append("new.drain"),
        close=lambda: resources.append("new.close"),
    )
    dispatcher = MutableRuntimeDispatcher(
        old, request_shutdown=lambda: shutdown.append("shutdown")
    )
    dispatcher.start()
    dispatcher.prepare(new)
    committed: list[str] = []
    with pytest.raises(ValueError, match="partial stop"):
        dispatcher.transition(old, new, lambda: committed.append("commit"))
    assert committed == []
    assert shutdown == ["shutdown"]
    assert resources.count("old.stop") >= 1
    assert resources[-4:] == ["old.drain", "old.close", "new.drain", "new.close"]
    assert not old.admitting and not new.admitting


def test_after_commit_fault_serves_unavailable_and_requests_shutdown() -> None:
    shutdown: list[str] = []
    events: list[str] = []
    old = RuntimeGeneration(
        _app,
        resume_admission=lambda: events.append("old.resume"),
        begin_drain=lambda: events.append("old.drain"),
        close=lambda: events.append("old.close"),
    )
    new = RuntimeGeneration(
        _app,
        admit=lambda: events.append("new.admit"),
        begin_drain=lambda: events.append("new.drain"),
        close=lambda: events.append("new.close"),
    )
    dispatcher = MutableRuntimeDispatcher(
        old, request_shutdown=lambda: shutdown.append("shutdown")
    )
    dispatcher.start()
    dispatcher.prepare(new)

    def cutover() -> None:
        events.append("cutover")
        raise ValueError("cutover")

    with pytest.raises(ValueError, match="cutover"):
        dispatcher.transition(
            old, new, lambda: events.append("commit"), after_commit=cutover
        )
    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(dispatcher({"type": "http"}, _receive, send))
    assert shutdown == ["shutdown"]
    assert events == [
        "commit",
        "cutover",
        "old.drain",
        "old.close",
        "new.drain",
        "new.close",
    ]
    assert not old.admitting and not new.admitting
    assert sent[0]["status"] == 503


def test_post_commit_admit_fault_serves_unavailable_and_requests_shutdown() -> None:
    shutdown: list[str] = []
    resources: list[str] = []
    old = RuntimeGeneration(
        _app,
        begin_drain=lambda: resources.append("old.drain"),
        close=lambda: resources.append("old.close"),
    )
    new = RuntimeGeneration(
        _app,
        admit=lambda: (_ for _ in ()).throw(ValueError("admit")),
        begin_drain=lambda: resources.append("new.drain"),
        close=lambda: resources.append("new.close"),
    )
    dispatcher = MutableRuntimeDispatcher(
        old, request_shutdown=lambda: shutdown.append("shutdown")
    )
    dispatcher.start()
    dispatcher.prepare(new)
    committed: list[str] = []
    with pytest.raises(ValueError, match="admit"):
        dispatcher.transition(old, new, lambda: committed.append("commit"))
    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(dispatcher({"type": "http"}, _receive, send))
    assert committed == ["commit"]
    assert shutdown == ["shutdown"]
    assert resources == ["old.drain", "old.close", "new.drain", "new.close"]
    assert not old.admitting and not new.admitting
    assert sent[0]["status"] == 503


def test_post_commit_paused_library_addition_keeps_successor_available_until_resume() -> (
    None
):
    class PausedPipeline:
        recovery_required = False
        libraries = (("primary", "added", "lib-2"),)

        def __init__(self) -> None:
            self.paused = True
            self.requests: list[tuple[str, str, PreparationTrigger | None]] = []
            self.deferred: tuple[tuple[str, str], ...] = ()

        def request_library_addition(
            self,
            connection: str,
            library: str,
        ) -> object | None:
            if self.paused:
                self.deferred = ((connection, library),)
                return None
            self.requests.append(
                (connection, library, PreparationTrigger.LIBRARY_ADDED)
            )
            self.deferred = ()
            return object()

        def deferred_library_additions(self) -> tuple[tuple[str, str], ...]:
            return self.deferred

        def set_paused(self, paused: bool) -> None:
            self.paused = paused
            if not paused:
                self.requests.extend(
                    (connection, library, PreparationTrigger.LIBRARY_ADDED)
                    for connection, library in self.deferred
                )
                self.deferred = ()

    pipeline = cast(ReviewPreparationPipeline, PausedPipeline())
    supervisor = ReviewPreparationSupervisor(pipeline)
    shutdown: list[str] = []
    settings_commits: list[str] = []
    available: list[str] = []

    async def successor_app(scope: object, receive: object, send: object) -> None:
        available.append("served")

    predecessor = RuntimeGeneration(_app)

    def admit_successor() -> None:
        supervisor.request_library_addition("primary", "added")

    successor = RuntimeGeneration(successor_app, admit=admit_successor)
    dispatcher = MutableRuntimeDispatcher(
        predecessor, request_shutdown=lambda: shutdown.append("shutdown")
    )
    dispatcher.start()
    dispatcher.prepare(successor)
    dispatcher.transition(
        predecessor,
        successor,
        lambda: settings_commits.append("library-added"),
    )
    asyncio.run(dispatcher({"type": "http"}, _receive, _send))

    assert settings_commits == ["library-added"]
    assert dispatcher.current() is successor
    assert shutdown == []
    assert available == ["served"]
    assert pipeline.requests == []

    supervisor.set_paused(False)
    supervisor.set_paused(False)
    assert pipeline.requests == [("primary", "added", PreparationTrigger.LIBRARY_ADDED)]


def test_initial_budget_failure_closes_dormant_generation() -> None:
    events: list[str] = []
    generation = RuntimeGeneration(
        _app,
        start_dormant=lambda: events.append("start"),
        close=lambda: events.append("close"),
    )
    dispatcher = MutableRuntimeDispatcher(
        generation,
        apply_shutdown_budget=lambda _seconds: (_ for _ in ()).throw(
            ValueError("budget")
        ),
    )
    with pytest.raises(ValueError, match="budget"):
        dispatcher.start()
    assert events == ["start", "close"]


def test_capture_during_shutdown_uses_unavailable_target() -> None:
    called: list[str] = []

    async def application(scope: object, receive: object, send: object) -> None:
        called.append("application")

    generation = RuntimeGeneration(application)
    dispatcher = MutableRuntimeDispatcher(generation)
    dispatcher.start()
    generation._admitting = False
    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(dispatcher({"type": "http"}, _receive, send))
    assert called == []
    assert sent[0]["status"] == 503


def test_captured_predecessor_closes_only_after_request_release() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []

    async def first(scope: object, receive: object, send: object) -> None:
        events.append("first.start")
        entered.set()
        await release.wait()
        events.append("first.finish")

    async def second(scope: object, receive: object, send: object) -> None:
        events.append("second")

    old = RuntimeGeneration(
        first,
        begin_drain=lambda: events.append("drain"),
        close=lambda: events.append("close"),
    )
    new = RuntimeGeneration(second)
    dispatcher = MutableRuntimeDispatcher(old)
    dispatcher.start()
    dispatcher.prepare(new)

    async def exercise() -> None:
        request = asyncio.create_task(dispatcher({}, _receive, _send))
        await entered.wait()
        dispatcher.transition(old, new, lambda: None)
        await dispatcher({}, _receive, _send)
        release.set()
        await request

    asyncio.run(exercise())
    assert events == ["first.start", "drain", "second", "first.finish", "close"]


def test_real_activity_generations_keep_root_and_successor_usable(tmp_path):
    import threading
    import time

    from mechanical_librarian.adapters.persistence.activity_facts import (
        ActivityFactStore,
    )
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.adapters.persistence.review_sessions import (
        SQLiteReviewSessionRepository,
    )
    from mechanical_librarian.application.operation_admission import OperationAdmission
    from tests.support.owner_responsiveness import activity_generation

    root_admission = OperationAdmission()
    root = open_repository(
        tmp_path / "application.sqlite3",
        cross_thread=True,
        admission=root_admission,
    )
    sessions = SQLiteReviewSessionRepository.open(tmp_path / "sessions.sqlite3")
    facts = ActivityFactStore()
    try:
        with (
            activity_generation(
                root, tmp_path / "application.sqlite3", sessions, facts=facts
            ) as old,
            activity_generation(
                root, tmp_path / "application.sqlite3", sessions, facts=facts
            ) as successor,
        ):
            # Only the background projector publishes before a read, and it
            # starts with the generation, so its first step can come after
            # composition returns (seen under loaded parallel runs).
            deadline = time.monotonic() + 10
            while facts.snapshot() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert facts.snapshot() is not None
            projection = old._activity_projection
            assert projection is not None
            local = projection._operation_admission
            assert local is not None
            with old.classify_request("background"):
                assert not root_admission.held and not local.held
                with root_admission.enter(), local.enter():
                    root_ticket = root_admission._active
                    local_ticket = local._active
                    assert root_ticket is not None and local_ticket is not None
                    assert root_ticket.work_class == "background"
                    assert local_ticket.work_class == "background"
                    with old.classify_request("foreground"):
                        assert root_ticket.work_class == "background"
                        assert local_ticket.work_class == "background"
            with old.classify_request("foreground"), local.enter():
                foreground_ticket = local._active
                assert foreground_ticket is not None
                assert foreground_ticket.work_class == "foreground"
            results = []
            errors = []

            def read():
                try:
                    results.append(old.activity())
                except BaseException as error:
                    errors.append(error)

            with root_admission.enter():
                reader = threading.Thread(target=read)
                reader.start()
                reader.join(3)
                assert not reader.is_alive()
            assert not errors and results[0]["events"] == []
            old.begin_drain()
            old.stop()
            assert successor.activity()["events"] == []
            assert successor.activity_transitions("event-1")["events"] == []
            assert not root_admission._closed
            assert root._connection.execute("SELECT 1").fetchone() == (1,)
    finally:
        sessions.close()
        root.close()
