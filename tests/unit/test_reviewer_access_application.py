from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, cast

import pytest

from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
)
from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexDeviceAuthState,
)
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterAccountStatus
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
)
from mechanical_librarian.application.reviewer_contracts import (
    DeviceAuthStatus,
    OpenRouterFailure,
    OpenRouterReviewerError,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus


class _Auth:
    def __init__(self, state: DeviceAuthStatus = "ready") -> None:
        self.state = state
        self.current_calls = 0
        self.status_calls = 0
        self.start_calls = 0
        self.recheck_state: DeviceAuthStatus | None = None

    def start(self):
        self.start_calls += 1
        self.state = "waiting"
        return CodexDeviceAuthState("waiting")

    def activate_lease(self) -> None:
        return None

    def status(self):
        self.status_calls += 1
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

    def recheck_credential_status(self):
        self.current_calls += 1
        if self.recheck_state is not None:
            return CodexDeviceAuthState(self.recheck_state)
        return CodexDeviceAuthState(
            "usable" if self.state == "configured" else self.state
        )

    def close(self) -> None:
        return None


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
    *, auth_state: DeviceAuthStatus = "ready", openrouter_fail: bool = False
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
    with pytest.raises(ReviewerAccessApplicationError, match="not_configured"):
        operations.dispatch_cover_observation()
    assert auth.current_calls == 0
    assert openrouter == []
    assert codex == []


def test_generic_reads_only_use_cheap_codex_host_evidence() -> None:
    operations, auth, _openrouter, codex = _operations()
    host_calls: list[str] = []

    def codex_host() -> ReviewerHostStatus:
        host_calls.append("host")
        return ReviewerHostStatus(True, None, 2 * 1024**3)

    operations._codex_host = codex_host
    assert operations.read().mapping is None
    operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    )
    assert operations.read().mapping is not None
    assert auth.status_calls == 0
    assert auth.current_calls == 0
    assert host_calls == ["host", "host", "host"]
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


@pytest.mark.parametrize(
    ("provider_code", "public_code"),
    (
        ("invalid_key", "invalid_key"),
        ("quota_exhausted", "quota_exhausted"),
        ("rate_limited", "rate_limited"),
        ("model_unavailable", "model_unavailable"),
        ("model_text_only", "model_text_only"),
        ("malformed_response", "malformed_response"),
        ("unreachable", "unreachable"),
        ("transport", "unreachable"),
    ),
)
def test_configuration_preserves_closed_value_free_provider_failure(
    provider_code: str, public_code: str
) -> None:
    operations, _auth, calls, _codex = _operations()

    class _Refused(_OpenRouter):
        def probe(self):
            raise OpenRouterReviewerError(cast("OpenRouterFailure", provider_code))

    operations._openrouter_factory = lambda key, model: _Refused(key, model, calls)
    with pytest.raises(ReviewerAccessApplicationError) as caught:
        operations.configure_openrouter(
            expected_revision=1, api_key="secret-key", model="example/vision"
        )
    assert caught.value.code == public_code
    assert "secret-key" not in str(caught.value)


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


def test_codex_selection_response_uses_exact_recheck_not_prior_terminal() -> None:
    operations, auth, _openrouter, _codex = _operations(auth_state="failed")

    auth.recheck_state = "usable"
    selected = operations.select_codex(expected_revision=1)
    assert selected.mapping is not None and selected.mapping.engine == "codex"
    assert selected.codex.state == "usable"


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


def test_failed_codex_deactivation_after_clear_remains_fail_closed() -> None:
    operations, auth, _openrouter, _codex = _operations(auth_state="usable")
    operations.select_codex(expected_revision=1)
    original = operations._repository.replace

    def fail(*_args, **_kwargs):
        raise RuntimeError("stale")

    operations._repository.replace = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="stale"):
        operations.clear_codex(expected_revision=2)
    assert auth.state == "ready"
    assert operations._repository.read()[0].cover_observation is not None
    operations._repository.replace = original  # type: ignore[method-assign]


def test_reauthentication_deselects_codex_before_starting_replacement() -> None:
    operations, auth, _openrouter, _codex = _operations(auth_state="usable")
    assert operations.select_codex(expected_revision=1).revision == 2
    access, started = operations.start_codex(expected_revision=2)
    assert started.state == "waiting"
    assert access.revision == 3
    assert access.mapping is None
    document, _configured = operations._repository.read()
    assert document.revision == 3
    assert document.cover_observation is None
    assert operations.codex_status().state == "waiting"


def test_explicit_codex_recheck_preserves_closed_result() -> None:
    operations, auth, _openrouter, _codex = _operations(auth_state="configured")
    assert operations.recheck_codex().state == "usable"
    assert auth.current_calls == 1

    auth.state = "failed"
    assert operations.recheck_codex().state == "failed"


@pytest.mark.parametrize(
    "reason",
    ("host_evidence_unavailable", "cgroup_limit_below_1_gib", "headroom_below_1_gib"),
)
def test_explicit_codex_recheck_rejects_host_before_auth(
    reason: Literal[
        "host_evidence_unavailable",
        "cgroup_limit_below_1_gib",
        "headroom_below_1_gib",
    ],
) -> None:
    operations, auth, _openrouter, _codex = _operations(auth_state="configured")
    operations._codex_host = lambda: ReviewerHostStatus(False, reason, None)

    with pytest.raises(ReviewerAccessApplicationError) as caught:
        operations.recheck_codex()

    assert caught.value.code == "host_unsupported"
    assert auth.current_calls == 0
    assert auth.status_calls == 0


def test_host_gate_precedes_every_codex_status_selection_and_start_side_effect() -> (
    None
):
    operations, auth, _openrouter, _codex = _operations(auth_state="usable")
    selected = operations.select_codex(expected_revision=1)
    assert selected.revision == 2
    prior_current_calls = auth.current_calls
    operations._codex_host = lambda: ReviewerHostStatus(
        False, "headroom_below_1_gib", None
    )

    with pytest.raises(ReviewerAccessApplicationError) as selection:
        operations.select_codex(expected_revision=2)
    with pytest.raises(ReviewerAccessApplicationError) as start:
        operations.start_codex(expected_revision=2)

    assert selection.value.code == "host_unsupported"
    assert start.value.code == "host_unsupported"
    assert auth.status_calls == 0
    assert auth.current_calls == prior_current_calls
    assert auth.start_calls == 0
    document, _configured = operations._repository.read()
    assert document.revision == 2
    assert document.cover_observation is not None
    assert document.cover_observation.engine == "codex"


def test_clear_failure_keeps_selected_authority_and_returns_closed_error() -> None:
    operations, auth, _openrouter, _codex = _operations(auth_state="usable")
    operations.select_codex(expected_revision=1)

    def fail_clear() -> CodexDeviceAuthState:
        raise RuntimeError("private path")

    auth.clear = fail_clear  # type: ignore[method-assign]
    with pytest.raises(ReviewerAccessApplicationError) as caught:
        operations.clear_codex(expected_revision=2)
    assert caught.value.code == "credential_clear_failed"
    assert "private path" not in str(caught.value)
    assert operations._repository.read()[0].cover_observation is not None


def test_a_b_a_replacement_cannot_make_captured_dispatch_current_again() -> None:
    operations, _auth, _openrouter, _codex = _operations()
    operations.configure_openrouter(
        expected_revision=1, api_key="key-a", model="example/model-a"
    )
    captured = operations.dispatch_cover_observation()
    assert captured.access_revision == 2
    operations.configure_openrouter(
        expected_revision=2, api_key="key-b", model="example/model-b"
    )
    operations.configure_openrouter(
        expected_revision=3, api_key="key-a-new", model="example/model-a"
    )
    assert captured.model == "example/model-a"
    assert operations.revision_is_current(captured.access_revision) is False
    assert operations.current_cover_observation_identity()[:3] == (
        4,
        "openrouter",
        "example/model-a",
    )
