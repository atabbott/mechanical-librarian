from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, cast

import pytest
from pydantic import ValidationError

from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
    ReviewerAccessStoreError,
)
from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexDeviceAuthState,
)
from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterAccountStatus
from mechanical_librarian.application.review_pipeline import ReviewPipelineError
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessApplicationError,
    ReviewerAccessOperations,
    ReviewerAccessView,
)
from mechanical_librarian.application.reviewer_contracts import (
    DeviceAuthFailure,
    DeviceAuthStatus,
    OpenRouterFailure,
    OpenRouterReviewerError,
    ReviewerProvisioningStatus,
)
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.reviewer_access import OpenRouterTaskSelection


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

    def passive_status(self):
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
            observed_at=1,
        )

    def run(self, *_args):
        self.calls.append(("run", self.model))
        if self.fail:
            raise RuntimeError("typed provider failure")
        return "{}"

    def probe_description(self):
        from dataclasses import replace

        return replace(
            self.probe(),
            observed_at=1,
            prompt_price="0.000001",
            completion_price="0.000002",
            request_price="0",
        )

    def run_description(self, *args):
        return self.run(*args)

    def close(self):
        self.calls.append(("close", self.model))


def _operations(
    *, auth_state: DeviceAuthStatus = "ready", openrouter_fail: bool = False
) -> tuple[ReviewerAccessOperations, _Auth, list[tuple[str, str]], list[str]]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    from mechanical_librarian.adapters.persistence.schema import _create_schema

    _create_schema(connection)
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
        codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
            True, None, model="gpt-5.6-sol"
        ),
        codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
        codex_identity=lambda: ("gpt-5.4", b"codex-profile"),
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
    initial = operations.read()
    assert initial.mapping is None
    assert initial.recommendations == ("openai/gpt-6-luna",)
    assert tuple(candidate.model for candidate in initial.candidates) == (
        "dots-studio/dots-3-note-preview:free",
    )
    operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    )
    assert operations.read().mapping is not None
    assert auth.status_calls == 0
    assert auth.current_calls == 0
    assert host_calls == ["host", "host", "host"]
    assert codex == []


def test_generic_read_reports_an_existing_flow_without_credential_work() -> None:
    operations, auth, _openrouter, codex = _operations(auth_state="waiting")

    view = operations.read()

    assert view.mapping is None
    assert view.codex.state == "waiting"
    assert auth.status_calls == 0
    assert auth.current_calls == 0
    assert codex == []


@pytest.mark.parametrize(
    ("flow", "failure", "host_available", "state", "reason"),
    (
        ("failed", "permission_or_profile", True, "failed", "permission_or_profile"),
        ("failed", "permission_or_process", True, "failed", "permission_or_process"),
        ("failed", "protocol_or_process", True, "failed", "protocol_or_process"),
        ("expired", None, True, "expired", None),
        ("cancelled", None, True, "cancelled", None),
        (
            "interrupted",
            "restart_interrupted",
            True,
            "interrupted",
            "restart_interrupted",
        ),
        ("usable", None, True, "usable", None),
        ("ready", None, True, "ready", None),
        ("configured", None, True, "ready", None),
        (
            "failed",
            "permission_or_profile",
            False,
            "host_unsupported",
            "headroom_below_1_gib",
        ),
    ),
)
def test_generic_read_reports_terminal_flow_without_credential_work(
    flow: DeviceAuthStatus,
    failure: DeviceAuthFailure | None,
    host_available: bool,
    state: str,
    reason: str | None,
) -> None:
    operations, auth, _openrouter, codex = _operations()
    auth.passive_status = lambda: CodexDeviceAuthState(flow, failure=failure)  # type: ignore[method-assign]
    if not host_available:
        operations._codex_host = lambda: ReviewerHostStatus(
            False, "headroom_below_1_gib", None
        )

    view = operations.read()

    assert view.mapping is None
    assert view.codex.state == state
    assert view.codex.reason == reason
    assert auth.status_calls == 0
    assert auth.current_calls == 0
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


@pytest.mark.parametrize(
    "code",
    (
        "rate_limited",
        "response_too_large",
        "timed_out",
        "data_policy_refused",
        "image_limit_exceeded",
    ),
)
def test_ordinary_read_does_not_probe_but_explicit_refresh_maps_typed_failure(
    code: str,
) -> None:
    operations, _auth, calls, _codex = _operations()
    operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    )
    calls.clear()
    assert operations.read().openrouter.state == "configured"
    assert calls == []

    class _Refused(_OpenRouter):
        def probe(self):
            raise OpenRouterReviewerError(cast("OpenRouterFailure", code))

    operations._openrouter_factory = lambda key, model: _Refused(key, model, calls)
    refreshed = operations.refresh_openrouter()
    assert refreshed.openrouter.state == code
    assert refreshed.openrouter.reason == code


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
        ("response_too_large", "response_too_large"),
        ("timed_out", "timed_out"),
        ("data_policy_refused", "data_policy_refused"),
        ("image_limit_exceeded", "image_limit_exceeded"),
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
    # Every probe failure, `unreachable` and `timed_out` included, is refused
    # before the write, so the browser may report it as nothing saved.
    document, key = operations._repository.snapshot()
    assert (document.revision, key) == (1, None)


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


def test_runner_construction_failure_preserves_selected_identity() -> None:
    operations, _auth, _openrouter, _codex = _operations()
    operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/model"
    )
    operations._openrouter_factory = lambda _key, _model: (_ for _ in ()).throw(
        RuntimeError("private provider detail")
    )

    with pytest.raises(ReviewerAccessApplicationError) as caught:
        operations.dispatch_cover_observation()

    assert caught.value.code == "unreachable"
    assert caught.value.identity is not None
    assert caught.value.identity[:3] == (2, "openrouter", "example/model")
    assert "private provider detail" not in str(caught.value)


def test_contained_description_disclosure_is_explicit_and_has_no_dispatch():
    import hashlib

    operations, auth, openrouter, codex = _operations()
    disclosure = operations.describe_contained_description()
    assert disclosure.model == "gpt-5.4"
    assert disclosure.profile_sha256 == hashlib.sha256(b"codex-profile").hexdigest()
    assert operations.read().description_mapping is None
    assert auth.current_calls == 0
    assert openrouter == []
    assert codex == []


def test_description_and_cover_models_preserve_siblings_and_reject_old_shared_snapshot():
    operations, _, calls, _ = _operations()
    cover = operations.configure_openrouter(
        expected_revision=1, api_key="first-secret", model="example/cover-a"
    )
    disclosed = operations.test_description_model("first-secret", "example/text-b")
    assert disclosed.model == "example/text-b"
    assert operations.read().revision == cover.revision
    both = operations.configure_openrouter(
        expected_revision=cover.revision,
        api_key="first-secret",
        model="example/text-b",
        task="description_rewrite",
    )
    assert isinstance(both.mapping, OpenRouterTaskSelection)
    assert both.mapping.model == "example/cover-a"
    assert isinstance(both.description_mapping, OpenRouterTaskSelection)
    assert both.description_mapping.model == "example/text-b"
    original = operations.current_description_rewrite_identity()
    assert original is not None
    dispatch = operations.dispatch_description_rewrite()
    assert dispatch.model == "example/text-b"
    dispatch.close()
    changed = operations.configure_openrouter(
        expected_revision=both.revision,
        api_key="replacement-secret",
        model="example/cover-c",
    )
    assert changed.description_mapping == both.description_mapping
    assert changed.description_metadata == both.description_metadata
    assert operations.current_description_rewrite_identity() != original
    assert not operations._repository.revision_is_current(original[0])
    restored = operations.configure_openrouter(
        expected_revision=changed.revision,
        api_key="first-secret",
        model="example/cover-a",
    )
    assert restored.description_mapping == both.description_mapping
    assert operations.current_description_rewrite_identity() != original
    cleared = operations.clear_openrouter(expected_revision=restored.revision)
    assert cleared.mapping is None
    assert cleared.description_mapping is None
    assert cleared.description_metadata is None
    assert not any(kind == "run" for kind, _ in calls)


@pytest.mark.parametrize("next_engine", [None, "codex"])
def test_description_deselection_discards_prices_but_preserves_cover(next_engine):
    operations, _, _, _ = _operations(auth_state="usable")
    cover = operations.configure_openrouter(
        expected_revision=1, api_key="secret", model="example/a"
    )
    both = operations.configure_openrouter(
        expected_revision=cover.revision,
        api_key="secret",
        model="example/b",
        task="description_rewrite",
    )
    saved = (
        operations.select_none(
            expected_revision=both.revision, task="description_rewrite"
        )
        if next_engine is None
        else operations.select_codex(
            expected_revision=both.revision, task="description_rewrite"
        )
    )
    assert saved.mapping == cover.mapping
    assert saved.description_metadata is None
    cleared = operations.clear_openrouter(expected_revision=saved.revision)
    assert cleared.mapping is None
    assert cleared.description_mapping == saved.description_mapping
    assert cleared.description_metadata is None


@pytest.mark.parametrize("missing_price", ["prompt_price", "completion_price"])
def test_missing_description_prices_do_not_disable_cover_and_refresh_recovers(
    missing_price,
):
    from dataclasses import replace

    operations, _, calls, _ = _operations()
    cover = operations.configure_openrouter(
        expected_revision=1, api_key="secret", model="example/a"
    )
    both = operations.configure_openrouter(
        expected_revision=cover.revision,
        api_key="secret",
        model="example/b",
        task="description_rewrite",
    )
    factory = operations._openrouter_factory

    class MissingPrices(_OpenRouter):
        def probe_description(self):
            return replace(super().probe_description(), **{missing_price: None})

    operations._openrouter_factory = lambda key, model: MissingPrices(key, model, calls)
    with pytest.raises(ReviewerAccessApplicationError, match="model_unavailable"):
        operations.configure_openrouter(
            expected_revision=both.revision,
            api_key="replacement-secret",
            model="example/b",
            task="description_rewrite",
        )
    assert operations.read().revision == both.revision
    assert operations.read().description_metadata == both.description_metadata
    assert operations._repository.openrouter_key() == "secret"
    unavailable = operations.refresh_openrouter("description_rewrite")
    assert unavailable.mapping == both.mapping
    assert unavailable.description_mapping == both.description_mapping
    assert unavailable.description_metadata is None
    assert unavailable.description is not None
    assert unavailable.description.state == "model_unavailable"
    assert unavailable.model_dump()["description"]["reason"] == "model_unavailable"
    current = operations.read()
    assert current.description is not None
    assert current.description.state == "model_unavailable"
    assert current.model_dump()["description"]["reason"] == "model_unavailable"
    with pytest.raises(ReviewerAccessApplicationError, match="model_unavailable"):
        operations.dispatch_description_rewrite()
    cover_dispatch = operations.dispatch_cover_observation()
    assert cover_dispatch.model == "example/a"
    cover_dispatch.close()
    operations._openrouter_factory = factory
    recovered = operations.refresh_openrouter("description_rewrite")
    assert recovered.description is not None
    assert recovered.description.state == "usable"
    assert recovered.description_metadata is not None
    assert recovered.description_metadata.model == "example/b"
    assert recovered.mapping == both.mapping
    assert not any(kind == "run" for kind, _ in calls)


def test_description_setup_can_reuse_saved_shared_key_without_resubmitting_it():
    operations, _, _, _ = _operations()
    cover = operations.configure_openrouter(
        expected_revision=1, api_key="saved-secret", model="example/a"
    )
    assert operations.test_description_model("", "example/b").model == "example/b"
    both = operations.configure_openrouter(
        expected_revision=cover.revision,
        api_key="",
        model="example/b",
        task="description_rewrite",
    )
    assert both.mapping == cover.mapping
    assert isinstance(both.description_mapping, OpenRouterTaskSelection)
    assert both.description_mapping.model == "example/b"
    assert operations._repository.openrouter_key() == "saved-secret"


def test_description_pending_cancellation_preserves_configured_cover_task(tmp_path):
    from tests.support.description_advice import _prepared

    captured = []
    repository, store, binding, _, _ = _prepared(
        tmp_path,
        pipeline_probe=lambda _repository, _report, _decision, access, calls: (
            captured.append((access, calls))
        ),
    )
    access, calls = captured[0]
    cover = access.select_codex(expected_revision=2, task="cover_observation")
    disabled = access.select_none(
        expected_revision=cover.revision, task="description_rewrite"
    )
    assert disabled.mapping == cover.mapping
    store.consume_pending_if_unconfigured(binding.review_case_id)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_preparation_pending"
    ).fetchone() == (0,)
    assert access.read().mapping == cover.mapping
    assert len(calls) == 1
    repository.close()


@pytest.mark.parametrize("task", ("cover_observation", "description_rewrite"))
def test_held_reviewer_observation_allows_foreground_and_refuses_obsolete_result(
    tmp_path, task
):
    import threading

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.adapters.persistence.reviewer_access import (
        ReviewerAccessStoreError,
    )
    from mechanical_librarian.application.operation_admission import OperationAdmission

    admission = OperationAdmission()
    repository = open_repository(
        tmp_path / "review.sqlite", cross_thread=True, admission=admission
    )
    operations, _auth, calls, _codex = _operations()
    operations._repository._connection.close()
    operations._repository = repository.reviewer_access_store()

    def local(call):
        with admission.enter(), repository.operation_lock:
            return call()

    operations.attach_serialization(local)
    entered = threading.Event()
    release = threading.Event()
    failures = []
    results = []
    thread = None
    try:
        original = operations._openrouter_factory
        assert (
            operations.configure_openrouter(
                expected_revision=1,
                api_key="synthetic-old",
                model="example/old",
                task=task,
            ).revision
            == 2
        )

        class Held(_OpenRouter):
            def probe(self):
                assert not admission.held
                assert not repository._connection.in_transaction
                entered.set()
                assert release.wait(5)
                return super().probe()

        operations._openrouter_factory = lambda key, model: (
            Held(key, model, calls) if model == "example/old" else original(key, model)
        )

        def refresh():
            try:
                with admission.classify("background"):
                    results.append(operations.refresh_openrouter(task))
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=refresh)
        thread.start()
        assert entered.wait(5)
        changed = operations.configure_openrouter(
            expected_revision=2, api_key="synthetic-new", model="example/new", task=task
        )
        assert changed.revision == 3
        repository.set_review_pipeline_paused(True, 100)
        assert repository.review_pipeline_paused()
        assert not release.is_set()
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert results == [] and len(failures) == 1
        if task == "description_rewrite":
            assert isinstance(failures[0], ReviewerAccessStoreError)
            assert str(failures[0]) == "reviewer access is stale"
        else:
            assert isinstance(failures[0], ReviewerAccessApplicationError)
            assert failures[0].code == "access_changed"
        document, key = repository.reviewer_access_store().snapshot()
        assert document.revision == 3 and key == "synthetic-new"
        assert getattr(document, task).model == "example/new"
    finally:
        release.set()
        if thread is not None:
            thread.join(5)
        repository.close()


@pytest.mark.parametrize("separate_generation", (False, True))
def test_credential_clear_serializes_same_generation_but_preserves_lost_cas(
    tmp_path, separate_generation, monkeypatch
):
    import threading

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.adapters.persistence.reviewer_access import (
        ReviewerAccessStoreError,
    )
    from mechanical_librarian.application.operation_admission import OperationAdmission

    admission = OperationAdmission()
    repository = open_repository(
        tmp_path / "review.sqlite", cross_thread=True, admission=admission
    )
    operations, auth, _calls, _codex = _operations(auth_state="usable")
    operations._repository._connection.close()
    operations._repository = repository.reviewer_access_store()
    assert operations.select_codex(expected_revision=1).revision == 2
    successor_repository = None
    competing = operations
    if separate_generation:
        successor_repository = open_repository(
            tmp_path / "review.sqlite",
            create=False,
            cross_thread=True,
            authority=repository.authority,
            admission=admission,
        )
        competing, _auth, _calls, _codex = _operations()
        competing._repository._connection.close()
        competing._repository = successor_repository.reviewer_access_store()
    entered = threading.Event()
    release = threading.Event()
    competing_started = threading.Event()
    outcomes: dict[str, ReviewerAccessView | BaseException] = {}
    threads = []
    clear_calls = 0
    original_clear = auth.clear

    def held_clear():
        nonlocal clear_calls
        assert not admission.held
        clear_calls += 1
        result = original_clear()
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(auth, "clear", held_clear)

    def clear():
        try:
            outcomes["clear"] = operations.clear_codex(expected_revision=2)
        except BaseException as error:
            outcomes["clear"] = error

    def replace_mapping():
        competing_started.set()
        try:
            outcomes["mapping"] = competing.configure_openrouter(
                expected_revision=2, api_key="synthetic-new", model="example/new"
            )
        except BaseException as error:
            outcomes["mapping"] = error

    try:
        first = threading.Thread(target=clear)
        threads.append(first)
        first.start()
        assert entered.wait(5)
        second = threading.Thread(target=replace_mapping)
        threads.append(second)
        second.start()
        assert competing_started.wait(5)
        # An unrelated owner operation still enters while credential I/O is held.
        repository.set_review_pipeline_paused(True, 100)
        assert repository.review_pipeline_paused()
        if separate_generation:
            second.join(5)
            assert not second.is_alive()
            mapping = outcomes["mapping"]
            assert isinstance(mapping, ReviewerAccessView)
            assert mapping.revision == 3
        else:
            assert not operations._command_lock.acquire(blocking=False)
        assert not release.is_set()
        release.set()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
        rejected = outcomes["clear" if separate_generation else "mapping"]
        assert isinstance(rejected, ReviewerAccessStoreError)
        assert str(rejected) == "reviewer access is stale"
        accepted = outcomes["mapping" if separate_generation else "clear"]
        assert isinstance(accepted, ReviewerAccessView)
        assert accepted.revision == 3
        document, key = repository.reviewer_access_store().snapshot()
        assert document.revision == 3 and auth.state == "ready" and clear_calls == 1
        if separate_generation:
            assert isinstance(document.cover_observation, OpenRouterTaskSelection)
            assert document.cover_observation.model == "example/new"
            assert key == "synthetic-new"
        else:
            assert document.cover_observation is None and key is None
        assert operations._command_lock.acquire(blocking=False)
        operations._command_lock.release()
    finally:
        release.set()
        for thread in threads:
            thread.join(5)
        if successor_repository is not None:
            successor_repository.close()
        repository.close()


@pytest.mark.parametrize("task", ["cover_observation", "description_rewrite"])
def test_provisioning_drift_refuses_both_tasks_but_retains_recovery(task):
    operations, auth, _router_calls, codex_calls = _operations(auth_state="usable")
    selected = operations.select_codex(expected_revision=1, task=task)
    operations._codex_provisioning = lambda _refresh: ReviewerProvisioningStatus(
        False, "profile_contract_mismatch"
    )
    operations._codex_host = lambda: pytest.fail("provisioning must precede host")
    assert operations.read().codex.state == "not_provisioned"
    for action in (
        operations.describe_contained_description,
        operations.recheck_codex,
        operations.dispatch_cover_observation
        if task == "cover_observation"
        else operations.dispatch_description_rewrite,
        lambda: operations.select_codex(expected_revision=selected.revision, task=task),
        lambda: operations.start_codex(expected_revision=selected.revision),
    ):
        with pytest.raises(ReviewerAccessApplicationError) as error:
            action()
        assert error.value.code == "not_provisioned"
    assert not codex_calls and auth.start_calls == 0
    assert operations.cancel_codex().state == "cancelled"
    cleared = operations.clear_codex(expected_revision=selected.revision)
    assert cleared.mapping is None and cleared.description_mapping is None
    assert cleared.codex.state == "not_provisioned"


def test_profile_replacement_between_identity_and_binding_cannot_dispatch():
    operations, _auth, _router_calls, calls = _operations(auth_state="usable")
    operations.select_codex(expected_revision=1)
    operations._codex_binding = lambda: (
        "gpt-5.4",
        b"different-profile",
        lambda *_: "{}",
    )
    with pytest.raises(ReviewerAccessApplicationError) as caught:
        operations.dispatch_cover_observation()
    assert caught.value.code == "access_changed"
    assert not calls


@pytest.mark.parametrize("next_engine", [None, "codex", "clear"])
def test_cover_prices_persist_refresh_and_clear_without_changing_description(
    next_engine,
):
    from dataclasses import replace

    operations, _, calls, _ = _operations(auth_state="usable")

    class Priced(_OpenRouter):
        def probe(self):
            return replace(
                super().probe(),
                prompt_price="0.000001",
                image_price="0",
                observed_at=123,
            )

    operations._openrouter_factory = lambda key, model: Priced(key, model, calls)
    cover = operations.configure_openrouter(
        expected_revision=1, api_key="secret", model="example/cover"
    )
    assert cover.cover_metadata is not None
    assert cover.cover_metadata.model == "example/cover"
    assert operations.read().cover_metadata == cover.cover_metadata
    both = operations.configure_openrouter(
        expected_revision=cover.revision,
        api_key="secret",
        model="example/text",
        task="description_rewrite",
    )
    refreshed = operations.refresh_openrouter()
    assert refreshed.revision == both.revision + 1
    assert refreshed.cover_metadata is not None
    assert refreshed.cover_metadata.observed_at == 123
    assert refreshed.cover_metadata.prompt_price == "0.000001"
    assert refreshed.cover_metadata.image_price == "0"
    assert refreshed.description_metadata == both.description_metadata

    class Refused(_OpenRouter):
        def probe(self):
            raise OpenRouterReviewerError("unreachable")

    operations._openrouter_factory = lambda key, model: Refused(key, model, calls)
    failed = operations.refresh_openrouter()
    assert failed.openrouter.state == "unreachable"
    assert failed.cover_metadata == refreshed.cover_metadata
    assert failed.revision == refreshed.revision
    result = (
        operations.clear_openrouter(expected_revision=failed.revision)
        if next_engine == "clear"
        else operations.select_codex(expected_revision=failed.revision)
        if next_engine == "codex"
        else operations.select_none(expected_revision=failed.revision)
    )
    assert result.cover_metadata is None
    if next_engine != "clear":
        assert result.description_metadata == both.description_metadata
        assert result.description_mapping == both.description_mapping


def test_description_missing_required_price_precedes_stale_revision(monkeypatch):
    from dataclasses import replace

    operations, _, calls, _ = _operations()
    cover = operations.configure_openrouter(
        expected_revision=1, api_key="secret", model="example/a"
    )

    class MissingPrice(_OpenRouter):
        def probe_description(self):
            return replace(super().probe_description(), prompt_price=None)

    class MissingFee(_OpenRouter):
        def probe_description(self):
            return replace(super().probe_description(), request_price=None)

    operations._openrouter_factory = lambda key, model: MissingPrice(key, model, calls)
    with monkeypatch.context() as stale:
        stale.setattr(
            operations._repository, "revision_is_current", lambda _revision: False
        )
        with pytest.raises(ReviewerAccessApplicationError, match="model_unavailable"):
            operations.test_description_model("", "example/b")
        operations._openrouter_factory = lambda key, model: MissingFee(
            key, model, calls
        )
        with pytest.raises(ReviewerAccessApplicationError, match="access_changed"):
            operations.test_description_model("", "example/b")
    assert operations.read().mapping == cover.mapping
    assert operations.read().description_mapping is None
    assert operations._repository.openrouter_key() == "secret"
    assert not any(kind == "run" for kind, _ in calls)


def test_description_model_without_request_fee_validates_saves_refreshes_and_dispatches():
    from dataclasses import replace

    operations, _, calls, _ = _operations()
    cover = operations.configure_openrouter(
        expected_revision=1, api_key="secret", model="example/a"
    )

    class MissingFee(_OpenRouter):
        def probe_description(self):
            return replace(super().probe_description(), request_price=None)

    operations._openrouter_factory = lambda key, model: MissingFee(key, model, calls)
    disclosed = operations.test_description_model("", "example/b")
    assert disclosed.model == "example/b"
    assert disclosed.prompt_price == "0.000001"
    assert disclosed.completion_price == "0.000002"
    assert disclosed.request_price is None
    assert operations.read().revision == cover.revision
    saved = operations.configure_openrouter(
        expected_revision=cover.revision,
        api_key="",
        model="example/b",
        task="description_rewrite",
    )
    assert saved.description_metadata == disclosed
    assert saved.description is not None
    assert saved.description.state == "usable"
    assert saved.mapping == cover.mapping
    assert saved.cover_metadata == cover.cover_metadata
    refreshed = operations.refresh_openrouter("description_rewrite")
    assert refreshed.description is not None
    assert refreshed.description.state == "usable"
    assert refreshed.description_metadata == disclosed
    assert refreshed.mapping == cover.mapping
    assert refreshed.cover_metadata == cover.cover_metadata
    dispatch = operations.dispatch_description_rewrite()
    assert dispatch.engine == "openrouter"
    assert dispatch.model == "example/b"
    dispatch.close()
    assert not any(kind == "run" for kind, _ in calls)


@pytest.mark.parametrize(
    "request_price",
    ["-1", "abc", "1000001", ""],
    ids=["negative", "malformed", "over_bound", "empty"],
)
def test_present_invalid_description_request_fee_is_refused_without_mutation(
    request_price,
):
    from dataclasses import replace

    operations, _, calls, _ = _operations()
    cover = operations.configure_openrouter(
        expected_revision=1, api_key="secret", model="example/a"
    )
    before = operations.read()

    class InvalidFee(_OpenRouter):
        def probe_description(self):
            return replace(super().probe_description(), request_price=request_price)

    operations._openrouter_factory = lambda key, model: InvalidFee(key, model, calls)
    with pytest.raises(ValueError):
        operations.test_description_model("", "example/b")
    with pytest.raises(ValueError):
        operations.configure_openrouter(
            expected_revision=cover.revision,
            api_key="replacement-secret",
            model="example/b",
            task="description_rewrite",
        )
    after = operations.read()
    assert after.revision == before.revision
    assert after.description_metadata == before.description_metadata
    assert after.description_mapping is None
    assert operations._repository.openrouter_key() == "secret"
    assert not any(kind == "run" for kind, _ in calls)


def test_committed_openrouter_key_is_answered_when_the_later_revision_check_locks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations, _auth, _calls, _codex = _operations()
    store = cast("ReviewerAccessStore", operations._repository)
    real_replace = store.replace

    def locked(_revision: int) -> bool:
        raise ReviewerAccessStoreError("reviewer access is unavailable")

    def replace_then_lock(*args, **kwargs):
        result = real_replace(*args, **kwargs)
        monkeypatch.setattr(store, "revision_is_current", locked)
        return result

    monkeypatch.setattr(store, "replace", replace_then_lock)

    view = operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    )

    document, key = store.snapshot()
    assert (document.revision, key) == (2, "secret-key")
    assert view.revision == 2
    assert view.openrouter_key_configured is True
    assert view.openrouter.state == "usable"


def _refusing_hook(operations: ReviewerAccessOperations) -> list[str]:
    calls: list[str] = []

    def changed() -> None:
        calls.append("changed")
        raise ReviewPipelineError("preparation_restart_required")

    operations.attach_changes(changed)
    return calls


def test_committed_openrouter_key_is_answered_when_the_change_hook_refuses() -> None:
    operations, _auth, _calls, _codex = _operations()
    hook = _refusing_hook(operations)

    view = operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    )

    document, key = operations._repository.snapshot()
    assert (document.revision, key) == (2, "secret-key")
    assert view.revision == 2
    assert hook == ["changed"]


@pytest.mark.parametrize("command", ("select_none", "clear_openrouter"))
def test_committed_reviewer_change_is_answered_when_the_change_hook_refuses(
    command: Literal["select_none", "clear_openrouter"],
) -> None:
    operations, _auth, _calls, _codex = _operations()
    operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    )
    hook = _refusing_hook(operations)

    view = getattr(operations, command)(expected_revision=2)

    document, key = operations._repository.snapshot()
    assert document.revision == view.revision == 3
    assert document.cover_observation is None
    assert view.mapping is None
    assert (key is None) is (command == "clear_openrouter")
    assert hook == ["changed"]


def _configured(operations: ReviewerAccessOperations) -> int:
    return operations.configure_openrouter(
        expected_revision=1, api_key="secret-key", model="example/vision"
    ).revision


def _codex_selected(operations: ReviewerAccessOperations) -> int:
    return operations.select_codex(expected_revision=1).revision


def _description_configured(operations: ReviewerAccessOperations) -> int:
    return operations.configure_openrouter(
        expected_revision=_configured(operations),
        api_key="",
        model="example/description",
        task="description_rewrite",
    ).revision


_COMMITTED_COMMANDS: dict[
    str,
    tuple[
        Callable[[ReviewerAccessOperations], int],
        Callable[[ReviewerAccessOperations, int], ReviewerAccessView],
    ],
] = {
    "select_none": (
        _configured,
        lambda access, revision: access.select_none(expected_revision=revision),
    ),
    "clear_openrouter": (
        _configured,
        lambda access, revision: access.clear_openrouter(expected_revision=revision),
    ),
    "select_codex": (
        lambda _access: 1,
        lambda access, revision: access.select_codex(expected_revision=revision),
    ),
    "clear_codex": (
        _codex_selected,
        lambda access, revision: access.clear_codex(expected_revision=revision),
    ),
    "start_codex_clearing_selection": (
        _codex_selected,
        lambda access, revision: access.start_codex(expected_revision=revision)[0],
    ),
    "refresh_openrouter": (
        _configured,
        lambda access, _revision: access.refresh_openrouter(),
    ),
    "refresh_description": (
        _description_configured,
        lambda access, _revision: access.refresh_openrouter("description_rewrite"),
    ),
}


@pytest.mark.parametrize("command", tuple(_COMMITTED_COMMANDS))
def test_committed_reviewer_change_is_answered_when_the_later_revision_check_locks(
    command: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    operations, _auth, _calls, _codex = _operations(auth_state="usable")
    prepare, run = _COMMITTED_COMMANDS[command]
    revision = prepare(operations)
    store = cast("ReviewerAccessStore", operations._repository)
    real_replace = store.replace

    def locked(_revision: int) -> bool:
        raise ReviewerAccessStoreError("reviewer access is unavailable")

    def replace_then_lock(*args, **kwargs):
        result = real_replace(*args, **kwargs)
        monkeypatch.setattr(store, "revision_is_current", locked)
        return result

    monkeypatch.setattr(store, "replace", replace_then_lock)

    view = run(operations, revision)

    document, _key = store.snapshot()
    assert document.revision == view.revision == revision + 1


def test_refresh_keeps_saved_description_metadata_when_the_committed_status_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from mechanical_librarian.application import reviewer_access as module

    operations, _auth, calls, _codex = _operations()
    revision = _description_configured(operations)
    before = operations.read().description_metadata
    assert before is not None

    class LaterObservation(_OpenRouter):
        def probe_description(self):
            return replace(super().probe_description(), observed_at=2)

    operations._openrouter_factory = lambda key, model: LaterObservation(
        key, model, calls
    )
    real_status = module.ReviewerEngineStatus

    def status_failing_when_usable(**fields: object) -> object:
        if fields.get("state") == "usable":
            # A real pydantic refusal, raised only by the post-commit status.
            return real_status.model_validate({**fields, "engine": "unknown"})
        return real_status.model_validate(fields)

    monkeypatch.setattr(module, "ReviewerEngineStatus", status_failing_when_usable)

    with pytest.raises(ValidationError):
        operations.refresh_openrouter("description_rewrite")

    document, key = operations._repository.snapshot()
    assert key == "secret-key"
    assert document.revision == revision + 1
    assert document.description_metadata is not None
    assert document.description_metadata.observed_at == 2
    assert document.description_metadata.model == before.model
