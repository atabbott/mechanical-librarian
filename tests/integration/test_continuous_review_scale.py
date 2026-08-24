"""Installed-wheel continuous-review qualification at a representative cohort."""

from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from tests.support import continuous_review_scale
from tests.support.continuous_review_scale import (
    _InstalledPreparationRun,
    _route_failure_diagnostic,
    _wait_for_https,
    qualify_installed_pipeline,
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


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.text = "safe"
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


def test_installed_preparation_closes_browser_observer_before_server() -> None:
    """The private lifecycle boundary owns this ordering across callback failure."""

    events: list[str] = []
    run = object.__new__(_InstalledPreparationRun)
    run._phase_observer = _ClosingObserver(events)
    run._fixture = type("Fixture", (), {"release_discovery": lambda _: None})()
    run._server = type("Server", (), {"should_exit": False, "force_exit": False})()
    run._server_thread = _JoiningThread(events)
    run._recovery = type(
        "Recovery", (), {"close": lambda _: events.append("recovery")}
    )()

    run.close()

    assert events == ["observer", "join:30", "recovery"]


def test_installed_preparation_exposes_the_admitted_fixture_case() -> None:
    """Admission hand-off is locked because the pipeline worker is concurrent."""

    run = object.__new__(_InstalledPreparationRun)
    run._case_id = None
    run._case_id_lock = threading.Lock()
    run._case_admitted = threading.Event()

    run._capture_fixture_case("case-1")

    assert run.wait_for_fixture_case() == "case-1"


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


def test_installed_pipeline_reconciles_then_restarts_completed_cohort() -> None:
    qualify_installed_pipeline(
        {"fiction": 4, "history": 20, "science": 20},
        expected_total=45,
    )
