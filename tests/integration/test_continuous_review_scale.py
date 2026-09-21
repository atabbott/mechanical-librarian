"""Installed-wheel continuous-review qualification at a representative cohort."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from mechanical_librarian.application.review_pipeline import PipelineAction
from tests.support import continuous_review_scale
from tests.support.continuous_review_scale import (
    _InstalledPreparationRun,
    _InstalledRepositoryTopology,
    _route_failure_diagnostic,
    _wait_for_https,
)


class _ClosingObserver:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def observe(self, _: str, __: str) -> None:
        return None

    def close(self) -> None:
        self._events.append("observer")


class _JoiningThread(threading.Thread):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events
        self._alive = True

    def join(self, timeout: float | None = None) -> None:
        self._events.append(f"join:{timeout}")
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive


def _admission_action(item_id: str) -> PipelineAction:
    return PipelineAction(
        "action",
        "scan",
        "admission",
        1,
        "primary",
        "fiction",
        "fiction",
        "a" * 64,
        catalogue_generation=1,
        catalogue_fingerprint="b" * 64,
        item_id=item_id,
        file_identity="c" * 64,
        source_revision=1,
        report_id="report",
        proposal_set_id="proposal",
        source_fingerprint="d" * 64,
    )


class _Response:
    def __init__(
        self, status_code: int, payload: dict[str, object], text: str = "safe"
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class _TerminalClient:
    def __enter__(self) -> _TerminalClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def post(self, *_: object, **__: object) -> _Response:
        return _Response(200, {})

    def get(self, *_: object, **__: object) -> _Response:
        return _Response(
            200,
            {
                "active": False,
                "libraries": [
                    {"state": "completed", "total": 1, "checked": 1, "blocked": 0},
                    {"state": "completed", "total": 1, "checked": 1, "blocked": 0},
                    {"state": "completed", "total": 1, "checked": 1, "blocked": 0},
                ],
            },
        )


class _PendingClient(_TerminalClient):
    def get(self, *_: object, **__: object) -> _Response:
        return _Response(
            200,
            {
                "active": True,
                "libraries": [
                    {
                        "library": "fiction",
                        "state": "extracting",
                        "total": 2,
                        "checked": 1,
                        "blocked": 0,
                    }
                ],
            },
        )


class _DecisionClient:
    def __init__(self, detail_statuses: list[int] | None = None) -> None:
        self.payload: dict[str, object] | None = None
        self.detail_requests = 0
        self._detail_statuses = detail_statuses or []

    def __enter__(self) -> _DecisionClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def post(self, url: str, **kwargs: object) -> _Response:
        if url.endswith("/session/login"):
            return _Response(200, {})
        self.payload = kwargs["json"]  # type: ignore[assignment]
        return _Response(
            200,
            {
                "actor": "owner",
                "decision_id": "human_decision_" + "a" * 64,
                "outcome": "accept",
                "proposal_id": "review_proposal_" + "b" * 64,
                "target_fingerprint": "c" * 64,
            },
        )

    def get(self, url: str, **_: object) -> _Response:
        if url.endswith("/library"):
            return _Response(
                200,
                {},
                (
                    'data-csrf-token="'
                    + "a" * 32
                    + '" data-build-identity="'
                    + "b" * 64
                    + '"'
                ),
            )
        self.detail_requests += 1
        status = self._detail_statuses.pop(0) if self._detail_statuses else 200
        return _Response(
            status,
            {
                "writes_consented": False,
                "fields": [
                    {
                        "destination": "title",
                        "proposal_id": "review_proposal_" + "b" * 64,
                        "target_fingerprint": "c" * 64,
                        "current": {"value": "current"},
                        "proposed_values": [
                            {"kind": "text", "value": "Exact evaluator candidate"}
                        ],
                    }
                ],
            },
        )


class _ActivityClient:
    def __init__(self, history: dict[str, object]) -> None:
        self._history = history

    def __enter__(self) -> _ActivityClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def post(self, *_: object, **__: object) -> _Response:
        return _Response(200, {})

    def get(self, url: str, **_: object) -> _Response:
        if url.endswith("/history"):
            return _Response(200, self._history)
        if "/activity" in url:
            return _Response(200, {"events": []})
        return _Response(
            200,
            {
                "fields": [
                    {
                        "destination": "title",
                        "human_decision": {
                            "decision_id": "human_decision_" + "a" * 64,
                            "outcome": "accept",
                        },
                    }
                ]
            },
        )


def test_exited_installed_server_reports_its_exit_code() -> None:
    process = subprocess.Popen(
        (sys.executable, "-c", "raise SystemExit(2)"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    process.wait(timeout=10)

    with pytest.raises(
        AssertionError,
        match=r"^installed review server exited before serving, exit code 2$",
    ):
        _wait_for_https(process, "https://127.0.0.1:1")


def test_installed_repository_topology_keeps_web_and_pipeline_owners_independent() -> (
    None
):
    from mechanical_librarian.application.configuration import ReviewWebConfiguration
    from mechanical_librarian.bootstrap import composition

    with tempfile.TemporaryDirectory() as directory:
        topology = _InstalledRepositoryTopology.open(Path(directory) / "state.sqlite3")
        review = ReviewWebConfiguration(
            "http://127.0.0.1:18444", "/synthetic/owner.hash"
        )
        composition._compose_cover_considerations(
            topology.web_state_owner,
            Path(directory) / "artifacts",
            review,
            composition._compose_reviewer_access(topology.web_state_owner, review),
        )
        assert topology.web_state_owner._capture_cover_read is not None
        observation = topology.open_foreground_observation()
        first_action = topology.open_action()
        second_action = topology.open_action()
        handles = (
            topology.web_state_owner,
            observation,
            first_action,
            second_action,
        )
        assert len({id(handle) for handle in handles}) == 4
        assert len({id(handle._connection) for handle in handles}) == 4
        assert all(
            handle.operation_admission is topology.web_state_owner.operation_admission
            for handle in handles
        )
        if sys.platform.startswith("linux"):
            assert len({id(handle.authority) for handle in handles}) == 4
        try:
            for handle in handles:
                handle.operational_snapshot()
        finally:
            observation.close()
            first_action.close()
            second_action.close()
            topology.web_state_owner.close()


def test_unresponsive_installed_server_reports_the_deadline_it_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The branch a live-but-slow server takes, which no test had covered.

    It is the branch every recorded sighting is suspected of, and it used to
    raise the same string as the death above, so neither the message nor a
    test could tell them apart (#730).
    """

    monkeypatch.setattr(continuous_review_scale, "_REVIEW_SERVER_START_SECONDS", 0.5)
    process = subprocess.Popen(
        (sys.executable, "-c", "import time; time.sleep(30)"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(
            AssertionError,
            match=(
                r"^installed review server stayed alive without serving "
                r"for 0\.5 seconds$"
            ),
        ):
            _wait_for_https(process, "https://127.0.0.1:1")
        assert process.poll() is None
    finally:
        process.kill()
        process.wait(timeout=10)


def test_route_failure_diagnostic_keeps_only_the_stable_error_category() -> None:
    response = continuous_review_scale.httpx.Response(
        503,
        json={"error": {"code": "persistence", "detail": "private value"}},
    )
    assert _route_failure_diagnostic("early", "library_default", response) == {
        "phase": "early",
        "route": "library_default",
        "status": 503,
        "category": "persistence",
    }


@pytest.mark.parametrize("drain_fails", [False, True])
def test_installed_preparation_closes_browser_observer_before_server(
    drain_fails: bool,
) -> None:
    """The private lifecycle boundary owns this ordering across callback failure."""

    events: list[str] = []
    run = object.__new__(_InstalledPreparationRun)
    run._phase_observer = _ClosingObserver(events)
    run._fixture = type("Fixture", (), {"release_discovery": lambda _: None})()
    run._fixture_case_hold = threading.Event()
    run._fixture_case_held = threading.Event()
    run._fixture_case_release = threading.Event()
    run._server = type("Server", (), {"should_exit": False, "force_exit": False})()
    run._server_thread = _JoiningThread(events)
    run._recovery = type(
        "Recovery", (), {"close": lambda _: events.append("recovery")}
    )()
    run._session_store = type(
        "Sessions", (), {"close": lambda _: events.append("sessions")}
    )()

    def close_application():
        events.append("application")
        if drain_fails:
            raise RuntimeError("browse drain deadline")

    run._close_application = close_application
    if drain_fails:
        with pytest.raises(RuntimeError, match="browse drain deadline"):
            run.close()
        assert events == ["observer", "join:30", "application"]
    else:
        run.close()
        assert events == ["observer", "join:30", "application", "recovery", "sessions"]

    assert run._fixture_case_release.is_set()


def test_installed_decision_activity_wrappers_keep_browser_out_of_http_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    activity = continuous_review_scale._InstalledActivityEvidence(
        event_id="event",
        category="owner",
        outcome="accept",
        history_events=1,
        unfiltered_events=1,
        filtered_events=1,
    )
    run = type(
        "Run", (), {"assert_owner_choice_browser": lambda _: calls.append("browser")}
    )()

    @contextmanager
    def lifecycle(*_: object, **__: object):
        yield run, activity

    monkeypatch.setattr(
        continuous_review_scale, "_running_installed_decision_activity", lifecycle
    )
    assert (
        continuous_review_scale.qualify_installed_decision_activity_http(
            {}, expected_total=1
        )
        == activity
    )
    assert calls == []
    assert (
        continuous_review_scale.qualify_installed_decision_activity(
            {}, expected_total=1
        )
        == activity
    )
    assert calls == ["browser"]


def test_installed_decision_releases_the_post_advance_hold_before_reading_current(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    decision = continuous_review_scale._InstalledDecisionEvidence(
        review_case_id="case",
        decision_id="decision",
        proposal_id="proposal",
        target_fingerprint="fingerprint",
        response_status=200,
    )
    activity = continuous_review_scale._InstalledActivityEvidence(
        event_id="event",
        category="owner",
        outcome="accept",
        history_events=1,
        unfiltered_events=1,
        filtered_events=1,
    )

    class Run:
        def hold_fixture_case(self) -> None:
            events.append("arm")

        def release_discovery(self) -> None:
            events.append("discover")

        def wait_for_fixture_case(self) -> str:
            events.append("case")
            return "case"

        def wait_for_fixture_case_hold(self) -> None:
            events.append("held")

        def assert_fixture_case_active(self) -> None:
            events.append("active")

        def release_fixture_case(self) -> None:
            events.append("release")

        def wait_for_fixture_case_release(self) -> None:
            events.append("released")

        def accept_fixture_title_proposal(self) -> object:
            assert events[-1] == "released"
            events.append("decision")
            return decision

        def wait_until_terminal(self) -> dict[str, object]:
            events.append("terminal")
            return {"libraries": [{"total": 1, "checked": 1, "blocked": 0}]}

        def assert_decision_activity(self, received: object) -> object:
            assert received is decision
            events.append("activity")
            return activity

    class Environment:
        def ensure_new_fiction(self) -> None:
            events.append("fiction")

        @contextmanager
        def running_preparation(self):
            yield Run()

    @contextmanager
    def environment(*_: object, **__: object):
        yield Environment()

    monkeypatch.setattr(
        continuous_review_scale, "_installed_qualification_environment", environment
    )

    with continuous_review_scale._running_installed_decision_activity(
        {}, expected_total=1
    ) as (_, observed):
        assert observed is activity

    assert events == [
        "fiction",
        "arm",
        "discover",
        "case",
        "held",
        "active",
        "release",
        "released",
        "decision",
        "terminal",
        "activity",
    ]


def test_installed_preparation_exposes_only_the_fixture_admitted_case() -> None:
    """Other workers cannot signal or replace the fixture-owned admission."""

    run = object.__new__(_InstalledPreparationRun)
    run._case_id = None
    run._case_id_lock = threading.Lock()
    run._case_admitted = threading.Event()

    run._capture_fixture_case("history-0000", "case-other")

    assert not run._case_admitted.is_set()
    assert run._case_id is None

    run._capture_fixture_case("fiction-0000", "case-1")
    run._capture_fixture_case("fiction-0000", "case-replaced")

    assert run.wait_for_fixture_case() == "case-1"


def test_fixture_capture_is_nonblocking_and_post_advance_hold_releases() -> None:
    run = object.__new__(_InstalledPreparationRun)
    run._case_id = None
    run._case_id_lock = threading.Lock()
    run._case_admitted = threading.Event()
    run._fixture_case_hold = threading.Event()
    run._fixture_case_held = threading.Event()
    run._fixture_case_release = threading.Event()
    run._fixture_case_released = threading.Event()
    run.hold_fixture_case()
    run._capture_fixture_case("fiction-0000", "case-1")
    assert run.wait_for_fixture_case() == "case-1"
    worker = threading.Thread(
        target=run._hold_fixture_case_after_advance,
        args=(_admission_action("fiction-0000"), "case-1"),
    )
    worker.start()
    assert run._fixture_case_held.wait(timeout=1)
    assert worker.is_alive()
    run.release_fixture_case()
    run.wait_for_fixture_case_release()
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_nonfixture_or_duplicate_post_advance_callbacks_do_not_hold() -> None:
    run = object.__new__(_InstalledPreparationRun)
    run._case_id = None
    run._case_id_lock = threading.Lock()
    run._case_admitted = threading.Event()
    run._fixture_case_hold = threading.Event()
    run._fixture_case_held = threading.Event()
    run._fixture_case_release = threading.Event()
    run._fixture_case_released = threading.Event()
    run.hold_fixture_case()
    run._capture_fixture_case("history-0000", "case-other")
    assert not run._case_admitted.is_set()
    run._hold_fixture_case_after_advance(
        _admission_action("history-0000"), "case-other"
    )
    assert not run._fixture_case_held.is_set()

    run._capture_fixture_case("fiction-0000", "case-1")
    worker = threading.Thread(
        target=run._hold_fixture_case_after_advance,
        args=(_admission_action("fiction-0000"), "case-1"),
    )
    worker.start()
    assert run._fixture_case_held.wait(timeout=1)
    run._hold_fixture_case_after_advance(_admission_action("fiction-0000"), "case-1")
    run.release_fixture_case()
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_post_advance_hold_leaves_the_web_owner_readable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        topology = _InstalledRepositoryTopology.open(Path(directory) / "state.sqlite3")
        run = object.__new__(_InstalledPreparationRun)
        run._case_id = "case-1"
        run._case_id_lock = threading.Lock()
        run._case_admitted = threading.Event()
        run._fixture_case_hold = threading.Event()
        run._fixture_case_held = threading.Event()
        run._fixture_case_release = threading.Event()
        run._fixture_case_released = threading.Event()
        run.hold_fixture_case()
        worker = threading.Thread(
            target=run._hold_fixture_case_after_advance,
            args=(_admission_action("fiction-0000"), "case-1"),
        )
        worker.start()
        assert run._fixture_case_held.wait(timeout=1)
        observation = topology.open_foreground_observation()
        try:
            observation.operational_snapshot()
        finally:
            observation.close()
            run.release_fixture_case()
            worker.join(timeout=1)
            topology.web_state_owner.close()
        assert not worker.is_alive()


def test_installed_preparation_accepts_only_the_projected_fixture_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public case projection, not fixture internals, binds the decision."""

    client = _DecisionClient()
    run = object.__new__(_InstalledPreparationRun)
    run._origin = "https://127.0.0.1:1"
    run._case_id = "review_case_" + "d" * 64
    run._case_id_lock = threading.Lock()
    run._case_admitted = threading.Event()
    run._case_admitted.set()
    monkeypatch.setattr(continuous_review_scale.httpx, "Client", lambda **_: client)

    evidence = run.accept_fixture_title_proposal()

    assert evidence.response_status == 200
    assert client.payload == {
        "destination": "title",
        "outcome": "accept",
        "proposal_id": "review_proposal_" + "b" * 64,
        "selected_value": {"kind": "text", "value": "Exact evaluator candidate"},
        "target_fingerprint": "c" * 64,
    }


def test_installed_preparation_waits_for_the_admission_current_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-admission callback may release before its current pointer commits."""

    client = _DecisionClient([409, 200])
    run = object.__new__(_InstalledPreparationRun)
    run._origin = "https://127.0.0.1:1"
    run._case_id = "review_case_" + "d" * 64
    run._case_id_lock = threading.Lock()
    run._case_admitted = threading.Event()
    run._case_admitted.set()
    monkeypatch.setattr(continuous_review_scale.httpx, "Client", lambda **_: client)
    monkeypatch.setattr(continuous_review_scale.time, "sleep", lambda _: None)

    evidence = run.accept_fixture_title_proposal()

    assert evidence.response_status == 200
    assert client.detail_requests == 2


@pytest.mark.parametrize(
    "history",
    [
        {"events": []},
        {"items": [{"kind": "human_decision_recorded", "decision_id": "wrong"}]},
    ],
)
def test_installed_activity_requires_the_authoritative_history_items(
    monkeypatch: pytest.MonkeyPatch, history: dict[str, object]
) -> None:
    """A stale history alias or unrelated record cannot satisfy the Activity chain."""

    run = object.__new__(_InstalledPreparationRun)
    run._origin = "https://127.0.0.1:1"
    decision = continuous_review_scale._InstalledDecisionEvidence(
        review_case_id="review_case_" + "d" * 64,
        decision_id="human_decision_" + "a" * 64,
        proposal_id="review_proposal_" + "b" * 64,
        target_fingerprint="c" * 64,
        response_status=200,
    )
    monkeypatch.setattr(
        continuous_review_scale.httpx, "Client", lambda **_: _ActivityClient(history)
    )

    with pytest.raises(AssertionError):
        run.assert_decision_activity(decision)


def test_terminal_wait_has_no_phase_fixture_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared lifecycle does not inherit scale profiling behavior."""

    run = object.__new__(_InstalledPreparationRun)
    run._origin = "https://127.0.0.1:1"
    run._expected_total = 3
    run._server_thread = _JoiningThread([])
    run._fixture = type(
        "Fixture",
        (),
        {
            "hold_next_detail": lambda _: pytest.fail("phase sampling reached"),
            "release_next_detail": lambda _: pytest.fail("phase sampling reached"),
        },
    )()
    monkeypatch.setattr(
        continuous_review_scale.httpx, "Client", lambda **_: _TerminalClient()
    )

    projection = run.wait_until_terminal()

    assert projection["active"] is False


def test_terminal_wait_uses_count_deadline_and_safe_aggregate_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = object.__new__(_InstalledPreparationRun)
    run._origin = "https://127.0.0.1:1"
    run._expected_total = 2
    run._server_thread = _JoiningThread([])
    values = iter((0.0, 0.0, 2.0, 2.0))
    # Replace this qualification module's clock reference, not the process-wide
    # stdlib module that live worker threads also use during the complete tier.
    monkeypatch.setattr(
        continuous_review_scale,
        "time",
        SimpleNamespace(monotonic=lambda: next(values), sleep=lambda _: None),
    )
    monkeypatch.setattr(
        continuous_review_scale, "_MINIMUM_INSTALLED_RECONCILIATION_WORKS_PER_SECOND", 1
    )
    monkeypatch.setattr(continuous_review_scale, "_REVIEW_SERVER_START_SECONDS", 1)
    monkeypatch.setattr(
        continuous_review_scale.httpx, "Client", lambda **_: _PendingClient()
    )

    with pytest.raises(pytest.fail.Exception, match="elapsed_seconds") as error:
        run.wait_until_terminal()

    assert "fiction" in str(error.value)
    assert "Exact evaluator candidate" not in str(error.value)
