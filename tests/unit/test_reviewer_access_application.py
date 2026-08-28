from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pytest

from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
)
from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexDeviceAuthState,
)
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterAccountStatus
from mechanical_librarian.application.reviewer_contracts import OpenRouterReviewerError
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus


class _Auth:
    def __init__(self, state: str = "ready") -> None:
        self.state = state
        self.current_calls = 0

    def start(self):
        self.state = "waiting"
        return CodexDeviceAuthState("waiting")

    def status(self):
        return CodexDeviceAuthState(self.state)

    def cancel(self):
        self.state = "cancelled"
        return CodexDeviceAuthState("cancelled")

    def clear(self):
        self.state = "ready"
        return CodexDeviceAuthState("ready")

    def current_credential_status(self):
        self.current_calls += 1
        return CodexDeviceAuthState(self.state)


@dataclass
class _OpenRouter:
    key: str
    model: str
    calls: list[tuple[str, str]]
    fail: bool = False

    def probe(self):
        self.calls.append(("probe", self.model))
        if self.fail:
            raise RuntimeError("typed provider failure")
        return OpenRouterAccountStatus(
            tier="free",
            model=self.model,
            input_modalities=("text", "image"),
            output_modalities=("text",),
            structured_outputs=True,
        )

    def run(self, *_args):
        self.calls.append(("run", self.model))
        if self.fail:
            raise RuntimeError("typed provider failure")
        return "{}"

    def close(self):
        self.calls.append(("close", self.model))


def _operations(
    *, auth_state: str = "ready", openrouter_fail: bool = False
) -> tuple[ReviewerAccessOperations, _Auth, list[tuple[str, str]], list[str]]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute(
        "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,document TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE reviewer_access_secrets (field_id TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    auth = _Auth(auth_state)
    openrouter_calls: list[tuple[str, str]] = []
    codex_calls: list[str] = []

    def codex_runner(*_args: object) -> str:
        codex_calls.append("run")
        return "{}"

    def codex_binding():
        codex_calls.append("binding")
        return "gpt-5.4", b"codex-profile", codex_runner

    operations = ReviewerAccessOperations(
        ReviewerAccessStore(connection),
        codex_auth=auth,
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_binding=codex_binding,
        openrouter_factory=lambda key, model: _OpenRouter(
            key, model, openrouter_calls, openrouter_fail
        ),
    )
    return operations, auth, openrouter_calls, codex_calls


def test_unconfigured_dispatch_constructs_neither_engine() -> None:
    operations, auth, openrouter, codex = _operations()
    with pytest.raises(ReviewerAccessApplicationError, match="not configured"):
        operations.dispatch_cover_observation()
    assert auth.current_calls == 0
    assert openrouter == []
    assert codex == []


def test_openrouter_configuration_validates_then_atomically_maps_without_disclosure() -> (
    None
):
    operations, _auth, calls, _codex = _operations()
    view = operations.configure_openrouter(
        expected_revision=1,
        api_key="secret-key",
        model="dots-studio/dots-3-note-preview:free",
    )
    assert view.mapping is not None
    assert view.openrouter_key_configured is True
    assert "secret-key" not in view.model_dump_json()
    assert calls.count(("probe", "dots-studio/dots-3-note-preview:free")) == 1


def test_ordinary_read_does_not_probe_but_explicit_refresh_maps_typed_failure() -> None:
    operations, _auth, calls, _codex = _operations()
    operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    )
    calls.clear()
    assert operations.read().openrouter.state == "configured"
    assert calls == []

    class _RateLimited(_OpenRouter):
        def probe(self):
            raise OpenRouterReviewerError("rate_limited")

    operations._openrouter_factory = lambda key, model: _RateLimited(key, model, calls)
    refreshed = operations.refresh_openrouter()
    assert refreshed.openrouter.state == "rate_limited"
    assert refreshed.openrouter.reason == "rate_limited"


def test_failed_openrouter_dispatch_never_falls_back_to_codex() -> None:
    operations, _auth, _openrouter, codex = _operations()
    operations.configure_openrouter(
        expected_revision=1,
        api_key="secret-key",
        model="example/vision",
    )
    operations._openrouter_factory = lambda key, model: _OpenRouter(
        key, model, [], True
    )
    dispatch = operations.dispatch_cover_observation()
    with pytest.raises(RuntimeError, match="provider failure"):
        dispatch.runner(b"profile", b"instruction", b"packet", b"schema", ())
    assert codex == []


def test_codex_selection_requires_current_host_and_credential_then_binds_lazily() -> (
    None
):
    operations, auth, openrouter, codex = _operations(auth_state="usable")
    view = operations.select_codex(expected_revision=1)
    assert view.mapping is not None and view.mapping.engine == "codex"
    assert openrouter == []
    dispatch = operations.dispatch_cover_observation()
    assert dispatch.engine == "codex"
    assert dispatch.model == "gpt-5.4"
    assert codex == ["binding"]
    assert auth.current_calls >= 2


def test_clearing_openrouter_is_immediate_and_removes_its_mapping() -> None:
    operations, _auth, _openrouter, _codex = _operations()
    operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    )
    view = operations.clear_openrouter(expected_revision=2)
    assert view.mapping is None
    assert view.openrouter_key_configured is False
    with pytest.raises(ReviewerAccessApplicationError):
        operations.dispatch_cover_observation()


def test_failed_codex_deactivation_does_not_clear_credential() -> None:
    operations, auth, _openrouter, _codex = _operations(auth_state="usable")
    operations.select_codex(expected_revision=1)
    original = operations._repository.replace

    def fail(*_args, **_kwargs):
        raise RuntimeError("stale")

    operations._repository.replace = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="stale"):
        operations.clear_codex(expected_revision=2)
    assert auth.state == "usable"
    operations._repository.replace = original  # type: ignore[method-assign]
