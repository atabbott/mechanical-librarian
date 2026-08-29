from __future__ import annotations

import io
import json
import os
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from threading import Event, Thread, current_thread
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from mechanical_librarian.adapters.reviewers import codex_device_auth as auth_module
from mechanical_librarian.adapters.reviewers import process_group as process_groups
from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexAuthCommand,
    CodexDeviceAuthError,
    CodexDeviceAuthManager,
    CodexDeviceAuthState,
    _bounded_status,
    _install_auth,
    _issued_state,
    _stop_process,
)
from mechanical_librarian.bootstrap.composition import (
    _CodexAuthLeaseCoordinator,
    _LazyCodexAuth,
)

_PROMPT = (
    b"Open this URL in your browser\n"
    b"https://auth.openai.com/codex/device\n"
    b"Enter this one-time code (expires in 15 minutes)\n"
    b"ABCD-EFGH\n"
)


def _auth_document() -> bytes:
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": "id",
                "access_token": "access",
                "refresh_token": "refresh",
                "account_id": "account",
            },
            "last_refresh": "2026-08-28T00:00:00Z",
        },
        separators=(",", ":"),
    ).encode()


class _Process:
    def __init__(self, output: bytes, returncode: int) -> None:
        self.stdout = io.BytesIO(output)
        self.returncode = returncode
        self.pid = 123456789

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return 0 if self.returncode is None else self.returncode

    def poll(self) -> int | None:
        return self.returncode


class _Chunked(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
        return super().read(7 if size is None else min(size, 7))


class _BlockingStream:
    def __init__(self, entered: Event, release: Event) -> None:
        self._entered = entered
        self._release = release

    def read(self, _size: int) -> bytes:
        self._entered.set()
        self._release.wait(timeout=2)
        return b""


def _manager(
    tmp_path: Path,
    *,
    output: bytes = _PROMPT,
    returncode: int = 0,
    status: subprocess.CompletedProcess[bytes] | None = None,
    lease_current: Callable[[], bool] = lambda: True,
    lease_guard: Callable[[], AbstractContextManager[None]] = nullcontext,
    activity_guard: Callable[[], AbstractContextManager[None]] = nullcontext,
    quarantine_activity: Callable[[], None] = lambda: None,
) -> CodexDeviceAuthManager:
    def capture(
        process: subprocess.Popen[bytes],
    ) -> process_groups.ReviewerProcessGroup:
        return process_groups.ReviewerProcessGroup(process.pid, process.pid, "1")

    def finish(
        _process: subprocess.Popen[bytes],
        _identity: process_groups.ReviewerProcessGroup,
    ) -> None:
        return

    def terminate(
        process: subprocess.Popen[bytes],
        identity: process_groups.ReviewerProcessGroup,
    ) -> None:
        if process.poll() is not None:
            return
        os.killpg(identity.pgid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(identity.pgid, signal.SIGKILL)
            process.wait(timeout=5)

    def command(staging: Path, action: str):
        return ([action, str(staging)], {"CODEX_HOME": str(staging)})

    def popen(command: list[str], **_kwargs: Any):
        action = command[-2]
        staging = Path(command[-1])
        if action == "status":
            completed = status or subprocess.CompletedProcess(
                ("status",), 0, b"Logged in using ChatGPT\n", b""
            )
            return _Process(completed.stdout + completed.stderr, completed.returncode)
        if returncode == 0:
            auth = staging / "auth.json"
            auth.write_bytes(_auth_document())
            auth.chmod(0o600)
        return _Process(output, returncode)

    return CodexDeviceAuthManager(
        tmp_path / "profile",
        command,
        popen=popen,
        lease_current=lease_current,
        lease_guard=lease_guard,
        activity_guard=activity_guard,
        quarantine_activity=quarantine_activity,
        capture_process_group=capture,
        finish_process_group=finish,
        terminate_process_group=terminate,
    )


def test_device_flow_owns_one_whole_activity_guard(tmp_path: Path) -> None:
    events: list[str] = []

    @contextmanager
    def activity() -> Iterator[None]:
        events.append("entered")
        try:
            yield
        finally:
            events.append("exited")

    manager = _manager(tmp_path, activity_guard=activity)
    with patch.object(manager, "_run_flow", side_effect=lambda: events.append("flow")):
        manager._flow()
    assert events == ["entered", "flow", "exited"]


def test_clear_and_recheck_own_the_activity_guard(tmp_path: Path) -> None:
    events: list[str] = []

    @contextmanager
    def activity() -> Iterator[None]:
        events.append("entered")
        try:
            yield
        finally:
            events.append("exited")

    manager = _manager(tmp_path, activity_guard=activity)
    _finish(manager)
    events.clear()

    assert manager.recheck_credential_status().state == "usable"
    assert events == ["entered", "exited"]
    events.clear()

    assert manager.clear().state == "ready"
    assert events == ["entered", "exited"]


def _finish(manager: CodexDeviceAuthManager) -> None:
    manager.start()
    assert manager._thread is not None
    manager._thread.join(timeout=2)
    assert not manager._thread.is_alive()


def test_issued_prompt_validates_status_and_installs_private_auth_atomically(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    _finish(manager)
    assert manager.status().state == "usable"
    installed = tmp_path / "profile" / "auth.json"
    assert json.loads(installed.read_text())["auth_mode"] == "chatgpt"
    assert stat.S_IMODE(installed.stat().st_mode) == 0o600
    assert stat.S_IMODE(installed.parent.stat().st_mode) == 0o700
    assert not list(tmp_path.glob("reviewer-auth-*"))


def test_exact_prompt_enters_explicit_issued_state_before_waiting() -> None:
    state = _issued_state(_PROMPT.decode().splitlines())
    assert state is not None
    assert state.state == "issued"
    assert state.verification_url == "https://auth.openai.com/codex/device"
    assert state.user_code == "ABCD-EFGH"
    assert state.expires_in_seconds == 900


def test_ordinary_credential_status_does_not_launch_cli(tmp_path: Path) -> None:
    calls: list[str] = []
    manager = _manager(tmp_path)
    _finish(manager)

    def unexpected_popen(*_args: Any, **_kwargs: Any) -> subprocess.Popen[bytes]:
        calls.append("popen")
        raise AssertionError("ordinary status launched the CLI")

    manager._popen = unexpected_popen
    assert manager.current_credential_status().state == "configured"
    assert calls == []


def test_exact_timeout_is_expired_and_permission_failure_is_failed(
    tmp_path: Path,
) -> None:
    expired = _manager(
        tmp_path / "expired",
        output=_PROMPT + b"device auth timed out after 15 minutes\n",
        returncode=1,
    )
    _finish(expired)
    assert expired.status().state == "expired"

    failed = _manager(tmp_path / "failed", returncode=1)
    _finish(failed)
    assert failed.status().state == "failed"
    assert failed.status().failure == "permission_or_process"


def test_staging_removal_refusal_after_install_never_publishes_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    original_unlink = os.unlink

    def refuse_auth(path: str, *, dir_fd: int | None = None) -> None:
        if path == "auth.json" and dir_fd is not None:
            raise OSError("synthetic staging removal refusal")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", refuse_auth)
    _finish(manager)

    assert manager.status() == CodexDeviceAuthState(
        "failed", failure="protocol_or_process"
    )
    assert (tmp_path / "profile" / "auth.json").exists()
    assert any(path.name.startswith("reviewer-auth-") for path in tmp_path.iterdir())
    manager._popen = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("trust failure must not spawn")
    )
    assert manager.current_credential_status().state == "failed"
    assert manager.recheck_credential_status().state == "failed"
    assert manager.start().state == "failed"
    with pytest.raises(CodexDeviceAuthError, match="staging"):
        manager.clear()


def test_staging_removal_refusal_after_validation_failure_remains_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(
        tmp_path,
        status=subprocess.CompletedProcess(("status",), 1, b"", b""),
    )
    original_unlink = os.unlink

    def refuse_auth(path: str, *, dir_fd: int | None = None) -> None:
        if path == "auth.json" and dir_fd is not None:
            raise OSError("synthetic staging removal refusal")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", refuse_auth)

    _finish(manager)

    assert manager.status() == CodexDeviceAuthState(
        "failed", failure="protocol_or_process"
    )
    assert not (tmp_path / "profile" / "auth.json").exists()
    assert any(path.name.startswith("reviewer-auth-") for path in tmp_path.iterdir())


def test_flow_retains_activity_and_process_until_cleanup_is_proven(
    tmp_path: Path,
) -> None:
    entered = Event()
    exited = Event()
    cleanup_blocked = Event()
    allow_cleanup = Event()

    @contextmanager
    def activity() -> Iterator[None]:
        entered.set()
        try:
            yield
        finally:
            exited.set()

    manager = _manager(tmp_path, output=b"malformed\n", activity_guard=activity)
    original = manager._terminate_process_group

    def blocked_cleanup(
        process: subprocess.Popen[bytes],
        identity: process_groups.ReviewerProcessGroup,
    ) -> None:
        cleanup_blocked.set()
        if not allow_cleanup.is_set():
            raise process_groups.ReviewerProcessGroupError("runtime")
        original(process, identity)

    manager._terminate_process_group = blocked_cleanup
    manager.start()
    assert entered.wait(timeout=2)
    assert cleanup_blocked.wait(timeout=2)
    assert not exited.is_set()
    assert manager._process is not None
    assert manager.status().state == "failed"

    allow_cleanup.set()
    assert manager._thread is not None
    manager._thread.join(timeout=2)
    assert not manager._thread.is_alive()
    assert exited.is_set()
    assert manager._process is None


def test_permanent_cleanup_failure_stops_and_quarantines_shared_admission(
    tmp_path: Path,
) -> None:
    coordinator = _CodexAuthLeaseCoordinator()
    holder: dict[str, CodexDeviceAuthManager] = {}

    def manager_factory(
        lease_current: Callable[[], bool],
        lease_guard: Callable[[], AbstractContextManager[None]],
    ) -> CodexDeviceAuthManager:
        manager = _manager(
            tmp_path,
            output=b"malformed\n",
            lease_current=lease_current,
            lease_guard=lease_guard,
            activity_guard=coordinator.activity,
            quarantine_activity=coordinator.quarantine,
        )
        holder["manager"] = manager
        return manager

    owner = _LazyCodexAuth(manager_factory, coordinator)
    successor = _LazyCodexAuth(manager_factory, coordinator)
    owner.activate_lease()
    assert owner.status().state == "ready"
    manager = holder["manager"]
    cleanup_attempts = 0

    def refuse_cleanup(
        _process: subprocess.Popen[bytes],
        _identity: process_groups.ReviewerProcessGroup,
    ) -> None:
        nonlocal cleanup_attempts
        cleanup_attempts += 1
        raise process_groups.ReviewerProcessGroupError("runtime")

    manager._terminate_process_group = refuse_cleanup
    manager.start()
    assert manager._thread is not None
    manager._thread.join(timeout=2)

    assert not manager._thread.is_alive()
    assert cleanup_attempts == 1 + len(auth_module._CLEANUP_RETRY_DELAYS)
    assert manager._process is not None
    assert manager._process_group is not None
    assert manager._state == CodexDeviceAuthState(
        "failed", failure="protocol_or_process"
    )
    assert owner.start() == CodexDeviceAuthState(
        "failed", failure="permission_or_profile"
    )
    with pytest.raises(CodexDeviceAuthError):
        owner.clear()
    with pytest.raises(CodexDeviceAuthError):
        with coordinator.activity():
            pass
    with pytest.raises(CodexDeviceAuthError):
        successor.activate_lease()
    assert cleanup_attempts == 1 + len(auth_module._CLEANUP_RETRY_DELAYS)


def test_foreground_recheck_cleanup_failure_quarantines_every_codex_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    credential = profile / "auth.json"
    credential.write_bytes(_auth_document())
    credential.chmod(0o600)
    coordinator = _CodexAuthLeaseCoordinator()
    holder: dict[str, CodexDeviceAuthManager] = {}
    spawns = 0

    def manager_factory(
        lease_current: Callable[[], bool],
        lease_guard: Callable[[], AbstractContextManager[None]],
    ) -> CodexDeviceAuthManager:
        manager = _manager(
            tmp_path,
            lease_current=lease_current,
            lease_guard=lease_guard,
            activity_guard=coordinator.activity,
            quarantine_activity=coordinator.quarantine,
        )
        original_popen = manager._popen

        def recording_popen(command: list[str], **kwargs: Any):
            nonlocal spawns
            spawns += 1
            return original_popen(command, **kwargs)

        manager._popen = recording_popen
        holder["manager"] = manager
        return manager

    owner = _LazyCodexAuth(manager_factory, coordinator)
    successor = _LazyCodexAuth(manager_factory, coordinator)
    owner.activate_lease()
    assert owner.current_credential_status().state == "configured"
    manager = holder["manager"]
    cleanup_attempts = 0
    delays: list[float] = []

    manager._finish_process_group = lambda _process, _identity: (_ for _ in ()).throw(
        process_groups.ReviewerProcessGroupError("runtime")
    )

    def refuse_cleanup(
        _process: subprocess.Popen[bytes],
        _identity: process_groups.ReviewerProcessGroup,
    ) -> None:
        nonlocal cleanup_attempts
        cleanup_attempts += 1
        raise process_groups.ReviewerProcessGroupError("runtime")

    manager._terminate_process_group = refuse_cleanup
    monkeypatch.setattr(auth_module.time, "sleep", delays.append)

    assert owner.recheck_credential_status() == CodexDeviceAuthState(
        "failed", failure="protocol_or_process"
    )

    retained_process = manager._process
    retained_group = manager._process_group
    assert retained_process is not None
    assert retained_group == process_groups.ReviewerProcessGroup(
        retained_process.pid, retained_process.pid, "1"
    )
    assert cleanup_attempts == 1 + len(auth_module._CLEANUP_RETRY_DELAYS)
    assert delays == list(auth_module._CLEANUP_RETRY_DELAYS)
    assert manager._quarantined
    assert coordinator._quarantined
    assert owner.start().state == "failed"
    assert owner.recheck_credential_status().state == "failed"
    with pytest.raises(CodexDeviceAuthError):
        owner.clear()
    with pytest.raises(CodexDeviceAuthError):
        with coordinator.activity():
            pass
    with pytest.raises(CodexDeviceAuthError):
        with owner.review_admission():
            pass
    with pytest.raises(CodexDeviceAuthError):
        successor.activate_lease()
    assert spawns == 1
    assert manager._process is retained_process
    assert manager._process_group == retained_group


def test_manager_identity_capture_failure_reaps_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    reaped: list[int] = []
    manager._capture_process_group = lambda _process: (_ for _ in ()).throw(
        process_groups.ReviewerProcessGroupError("runtime")
    )
    monkeypatch.setattr(
        process_groups,
        "kill_spawned_process_group",
        lambda process: reaped.append(process.pid),
    )

    _finish(manager)

    assert reaped == [123456789]
    assert manager.status() == CodexDeviceAuthState(
        "failed", failure="protocol_or_process"
    )
    assert manager._process is None


def test_cancel_cleanup_failure_is_failed_and_retains_exact_owner(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    assert manager.status().state == "ready"
    process = _Process(b"", 0)
    process.returncode = None  # type: ignore[assignment]
    identity = process_groups.ReviewerProcessGroup(process.pid, process.pid, "1")
    manager._process = process  # type: ignore[assignment]
    manager._process_group = identity
    cleanup_attempts = 0

    def refuse_cleanup(
        _process: subprocess.Popen[bytes],
        _identity: process_groups.ReviewerProcessGroup,
    ) -> None:
        nonlocal cleanup_attempts
        cleanup_attempts += 1
        raise process_groups.ReviewerProcessGroupError("runtime")

    manager._terminate_process_group = refuse_cleanup

    with pytest.raises(CodexDeviceAuthError, match="process"):
        manager.cancel()

    assert manager.status() == CodexDeviceAuthState(
        "failed", failure="protocol_or_process"
    )
    assert cast("object", manager._process) is process
    assert manager._process_group == identity
    assert manager._quarantined
    assert cleanup_attempts == 1 + len(auth_module._CLEANUP_RETRY_DELAYS)
    with pytest.raises(CodexDeviceAuthError, match="process"):
        manager.close()
    assert cleanup_attempts == 1 + len(auth_module._CLEANUP_RETRY_DELAYS)


@pytest.mark.parametrize(
    "output",
    (
        b"https://evil.test/device\nABCD-EFGH\n",
        _PROMPT.replace(b"15 minutes", b"10 minutes"),
        _PROMPT.replace(b"ABCD-EFGH", b"../../etc/passwd"),
        _PROMPT + b"ABCD-EFGH\n",
        b"\xff\n",
    ),
)
def test_malformed_or_hostile_stdout_fails_without_install(
    tmp_path: Path, output: bytes
) -> None:
    manager = _manager(tmp_path, output=output)
    _finish(manager)
    assert manager.status().state == "failed"
    assert not (tmp_path / "profile" / "auth.json").exists()


def test_status_must_be_the_exact_chatgpt_success(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        status=subprocess.CompletedProcess(("status",), 0, b"Logged in\n", b""),
    )
    _finish(manager)
    assert manager.status().state == "failed"
    assert not (tmp_path / "profile" / "auth.json").exists()


def test_oversized_status_output_fails_before_install(tmp_path: Path) -> None:
    manager = _manager(
        tmp_path,
        status=subprocess.CompletedProcess(("status",), 0, b"x" * 16_385, b""),
    )
    _finish(manager)
    assert manager.status().state == "failed"
    assert not (tmp_path / "profile" / "auth.json").exists()


def test_chunked_oversized_status_recheck_fails_at_the_stream_bound(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    _finish(manager)
    process = _Process(b"", 0)
    process.stdout = _Chunked(b"x" * 16_385)
    manager._popen = lambda *_args, **_kwargs: process  # type: ignore[assignment]

    assert manager.recheck_credential_status().state == "failed"
    assert process.stdout.tell() == 16_385


def test_status_eof_with_live_child_is_killed_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    _finish(manager)
    process = _Process(b"", 0)
    process.returncode = None  # type: ignore[assignment]
    waits = 0

    def wait(timeout: float | None = None) -> int:
        nonlocal waits
        waits += 1
        if waits == 1:
            raise subprocess.TimeoutExpired(("status",), timeout or 0)
        process.returncode = -9
        return -9

    process.wait = wait  # type: ignore[method-assign]
    manager._popen = lambda *_args, **_kwargs: process  # type: ignore[assignment]
    monkeypatch.setattr(os, "killpg", lambda _pid, _signal: None)

    assert manager.recheck_credential_status().state == "failed"
    assert process.poll() == -9


def test_status_deadline_stops_child_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(b"Logged in using ChatGPT\n", 0)
    process.returncode = None  # type: ignore[assignment]

    def wait(timeout: float | None = None) -> int:
        del timeout
        process.returncode = -15
        return -15

    process.wait = wait  # type: ignore[method-assign]
    monkeypatch.setattr(os, "killpg", lambda _pid, _signal: None)
    prepared = CodexAuthCommand(("status",), {})

    with pytest.raises(subprocess.TimeoutExpired):
        _bounded_status(
            cast("subprocess.Popen[bytes]", process),
            prepared,
            time.monotonic() - 1,
            stop_process=lambda: setattr(process, "returncode", -15),
        )
    assert process.poll() == -15


def test_clear_removes_installed_authority_immediately(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _finish(manager)
    assert (tmp_path / "profile" / "auth.json").exists()
    assert manager.clear().state == "ready"
    assert not (tmp_path / "profile" / "auth.json").exists()


def test_generation_shutdown_preserves_valid_installed_credential(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    _finish(manager)
    installed = tmp_path / "profile" / "auth.json"
    before = installed.read_bytes()
    manager.close()
    assert installed.read_bytes() == before


def test_cancel_targets_only_the_exact_active_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    process = _Process(b"", 0)
    process.returncode = None  # type: ignore[assignment]
    manager._process = process  # type: ignore[assignment]
    manager._process_group = process_groups.ReviewerProcessGroup(
        process.pid, process.pid, "1"
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    assert manager.cancel().state == "cancelled"
    assert killed == [(123456789, 15)]


def test_shutdown_escalates_sigterm_resistant_child_and_proves_death(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(b"", 0)
    process.returncode = None  # type: ignore[assignment]
    killed: list[int] = []

    def kill_group(_pid: int, sent: int) -> None:
        killed.append(sent)
        if sent == signal.SIGKILL:
            process.returncode = -signal.SIGKILL

    monkeypatch.setattr(os, "killpg", kill_group)
    identity = process_groups.ReviewerProcessGroup(process.pid, process.pid, "1")
    monkeypatch.setattr(process_groups, "require_signal_identity", lambda _value: True)
    reaps = 0

    def reap(_pgid: int, _timeout: float) -> None:
        nonlocal reaps
        reaps += 1
        if reaps == 1:
            raise process_groups.ReviewerProcessGroupError("runtime")

    monkeypatch.setattr(process_groups, "reap_process_group", reap)
    monkeypatch.setattr(process_groups, "wait_for_exit", lambda _pid, _timeout: 0)
    monkeypatch.setattr(process_groups, "process_identity_alive", lambda _value: False)
    _stop_process(cast("subprocess.Popen[bytes]", process), identity)
    assert killed == [signal.SIGTERM, signal.SIGKILL]
    assert process.poll() == -9


def test_shutdown_does_not_release_when_broker_exits_before_its_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _Process(b"", 0)
    process.returncode = None  # type: ignore[assignment]
    signals: list[int] = []

    def kill_group(_pid: int, sent: int) -> None:
        signals.append(sent)
        if sent == signal.SIGTERM:
            process.returncode = -signal.SIGTERM

    monkeypatch.setattr(os, "killpg", kill_group)
    identity = process_groups.ReviewerProcessGroup(process.pid, process.pid, "1")
    monkeypatch.setattr(process_groups, "require_signal_identity", lambda _value: True)
    reaps = 0

    def reap(_pgid: int, _timeout: float) -> None:
        nonlocal reaps
        reaps += 1
        if reaps == 1:
            raise process_groups.ReviewerProcessGroupError("runtime")

    monkeypatch.setattr(process_groups, "reap_process_group", reap)
    monkeypatch.setattr(process_groups, "wait_for_exit", lambda _pid, _timeout: 0)
    monkeypatch.setattr(process_groups, "process_identity_alive", lambda _value: False)
    _stop_process(cast("subprocess.Popen[bytes]", process), identity)

    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_eof_from_still_running_child_uses_bounded_wait_then_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path, output=b"")
    process = _Process(b"", 0)
    process.returncode = None  # type: ignore[assignment]
    waits = 0

    def wait(timeout: float | None = None) -> int:
        nonlocal waits
        waits += 1
        if waits == 1:
            raise subprocess.TimeoutExpired(("device",), timeout or 0)
        process.returncode = -9
        return -9

    process.wait = wait  # type: ignore[method-assign]
    manager._popen = lambda *_args, **_kwargs: process  # type: ignore[assignment]
    monkeypatch.setattr(os, "killpg", lambda _pid, _signal: None)
    _finish(manager)
    assert manager.status().state == "expired"
    assert process.poll() == -9


def test_repeated_start_cannot_spawn_a_second_flow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    release = Event()

    def wait_for_release() -> None:
        release.wait(timeout=2)

    monkeypatch.setattr(manager, "_flow", wait_for_release)
    manager.start()
    thread = manager._thread
    manager.start()
    assert manager._thread is thread
    release.set()
    assert thread is not None
    thread.join(timeout=2)


def test_cancel_between_status_validation_and_install_wins_without_install(
    tmp_path: Path,
) -> None:
    manager = _manager(tmp_path)
    validating = Event()
    release = Event()

    original = manager._popen

    def blocked_status(command: list[str], **kwargs: Any):
        if command[-2] == "status":
            validating.set()
            release.wait(timeout=2)
        return original(command, **kwargs)

    manager._popen = blocked_status
    manager.start()
    assert validating.wait(timeout=2)
    assert manager.cancel().state == "cancelled"
    release.set()
    assert manager._thread is not None
    manager._thread.join(timeout=2)
    assert not manager._thread.is_alive()
    assert manager.status().state == "cancelled"
    assert not (tmp_path / "profile" / "auth.json").exists()


def test_terminal_install_uses_lease_before_manager_lock_and_handoff_completes(
    tmp_path: Path,
) -> None:
    coordinator = _CodexAuthLeaseCoordinator()
    validation_complete = Event()
    release_validation = Event()
    foreground_has_lease = Event()
    release_foreground = Event()
    terminal_waiting_for_lease = Event()
    holder: dict[str, CodexDeviceAuthManager] = {}

    def predecessor_factory(
        lease_current: Callable[[], bool],
        lease_guard: Callable[[], AbstractContextManager[None]],
    ) -> CodexDeviceAuthManager:
        @contextmanager
        def ordered_guard() -> Iterator[None]:
            if (
                current_thread().name == "reviewer-device-auth"
                and validation_complete.is_set()
            ):
                terminal_waiting_for_lease.set()
            with lease_guard():
                if current_thread().name == "foreground-status":
                    foreground_has_lease.set()
                    assert release_foreground.wait(timeout=2)
                yield

        manager = _manager(
            tmp_path, lease_current=lease_current, lease_guard=ordered_guard
        )
        original_status = manager._status_command

        def blocked_status(
            prepared: CodexAuthCommand, deadline: float, *, flow_owned: bool
        ) -> tuple[bytes, int]:
            result = original_status(prepared, deadline, flow_owned=flow_owned)
            validation_complete.set()
            assert release_validation.wait(timeout=2)
            return result

        manager._status_command = blocked_status  # type: ignore[method-assign]
        holder["manager"] = manager
        return manager

    predecessor = _LazyCodexAuth(predecessor_factory, coordinator)
    successor = _LazyCodexAuth(
        lambda lease, guard: _manager(tmp_path, lease_current=lease, lease_guard=guard),
        coordinator,
    )
    predecessor.activate_lease()
    predecessor.start()
    assert validation_complete.wait(timeout=2)
    manager = holder["manager"]
    foreground_result: list[CodexDeviceAuthState] = []
    foreground = Thread(
        target=lambda: foreground_result.append(manager.status()),
        name="foreground-status",
        daemon=True,
    )
    foreground.start()
    assert foreground_has_lease.wait(timeout=2)
    release_validation.set()
    assert terminal_waiting_for_lease.wait(timeout=2)
    release_foreground.set()

    foreground.join(timeout=2)
    assert manager._thread is not None
    manager._thread.join(timeout=2)
    assert not foreground.is_alive()
    assert not manager._thread.is_alive()
    assert foreground_result == [CodexDeviceAuthState("validating")]
    assert manager.status().state == "usable"

    activated = Event()

    def activate_successor() -> None:
        successor.activate_lease()
        activated.set()

    handoff = Thread(target=activate_successor, daemon=True)
    handoff.start()
    handoff.join(timeout=2)
    assert not handoff.is_alive()
    assert activated.is_set()


def test_cancel_owns_and_stops_spawned_validation_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    entered = Event()
    release = Event()
    validation = _Process(b"", 0)
    validation.returncode = None  # type: ignore[assignment]
    validation.stdout = _BlockingStream(entered, release)  # type: ignore[assignment]
    original = manager._popen

    def popen(command: list[str], **kwargs: Any):
        return validation if command[-2] == "status" else original(command, **kwargs)

    manager._popen = popen

    def terminate(_pid: int, _signal: int) -> None:
        validation.returncode = -15

    monkeypatch.setattr(os, "killpg", terminate)
    manager.start()
    assert entered.wait(timeout=2)
    assert cast("object", manager._process) is validation
    assert manager.cancel().state == "cancelled"
    release.set()
    assert manager._thread is not None
    manager._thread.join(timeout=2)
    assert not manager._thread.is_alive()
    assert validation.poll() == -15
    assert not (tmp_path / "profile" / "auth.json").exists()


def test_active_device_flow_excludes_external_recheck_and_selection_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    entered = Event()
    release = Event()
    child = _Process(b"", 0)
    child.returncode = None  # type: ignore[assignment]
    child.stdout = _BlockingStream(entered, release)  # type: ignore[assignment]
    spawn_count = 0

    def popen(_command: list[str], **_kwargs: Any):
        nonlocal spawn_count
        spawn_count += 1
        return child

    manager._popen = popen

    def terminate(_pid: int, _signal: int) -> None:
        child.returncode = -15

    monkeypatch.setattr(os, "killpg", terminate)
    assert manager.start().state == "starting"
    assert entered.wait(timeout=2)
    assert manager.recheck_credential_status().state == "starting"
    assert manager.current_credential_status().state == "starting"
    assert spawn_count == 1
    assert manager.cancel().state == "cancelled"
    release.set()
    manager.close()
    assert spawn_count == 1
    assert child.poll() == -15


def test_successor_lease_revokes_flow_before_stale_install(tmp_path: Path) -> None:
    coordinator = _CodexAuthLeaseCoordinator()
    validating = Event()
    validation_finishing = Event()
    release_validation = Event()
    holder: dict[str, CodexDeviceAuthManager] = {}

    def predecessor_factory(
        lease_current: Callable[[], bool],
        lease_guard: Callable[[], AbstractContextManager[None]],
    ) -> CodexDeviceAuthManager:
        manager = _manager(
            tmp_path, lease_current=lease_current, lease_guard=lease_guard
        )
        original = manager._popen

        def blocked_status(command: list[str], **kwargs: Any):
            if command[-2] == "status":
                validating.set()
            return original(command, **kwargs)

        manager._popen = blocked_status
        original_finish = manager._finish_process_group

        def blocked_finish(
            process: subprocess.Popen[bytes],
            identity: process_groups.ReviewerProcessGroup,
        ) -> None:
            if validating.is_set():
                validation_finishing.set()
                release_validation.wait(timeout=2)
            original_finish(process, identity)

        manager._finish_process_group = blocked_finish
        holder["manager"] = manager
        return manager

    predecessor = _LazyCodexAuth(predecessor_factory, coordinator)
    successor = _LazyCodexAuth(
        lambda lease, _guard: _manager(tmp_path, lease_current=lease), coordinator
    )
    predecessor.activate_lease()
    predecessor.start()
    assert validation_finishing.wait(timeout=2)
    activated = Event()
    cancelled = Event()
    manager = holder["manager"]
    original_cancel = manager.cancel

    def observed_cancel() -> CodexDeviceAuthState:
        result = original_cancel()
        cancelled.set()
        return result

    def activate_successor() -> None:
        successor.activate_lease()
        activated.set()

    with patch.object(manager, "cancel", side_effect=observed_cancel):
        cutover = Thread(target=activate_successor)
        cutover.start()
        assert cancelled.wait(timeout=2)
        release_validation.set()
        cutover.join(timeout=3)
    assert activated.is_set()
    assert manager._thread is not None
    manager._thread.join(timeout=2)
    assert not (tmp_path / "profile" / "auth.json").exists()
    assert not any(
        path.name.startswith("reviewer-auth-") for path in tmp_path.iterdir()
    )


def test_handoff_winning_before_start_admission_touches_nothing(tmp_path: Path) -> None:
    coordinator = _CodexAuthLeaseCoordinator()
    approaching_guard = Event()
    resume_start = Event()
    popen_calls = 0
    holder: dict[str, CodexDeviceAuthManager] = {}

    def predecessor_factory(
        lease_current: Callable[[], bool],
        lease_guard: Callable[[], AbstractContextManager[None]],
    ) -> CodexDeviceAuthManager:
        @contextmanager
        def delayed_guard() -> Iterator[None]:
            approaching_guard.set()
            assert resume_start.wait(timeout=2)
            with lease_guard():
                yield

        manager = _manager(
            tmp_path, lease_current=lease_current, lease_guard=delayed_guard
        )
        original = manager._popen

        def counted_popen(command: list[str], **kwargs: Any):
            nonlocal popen_calls
            popen_calls += 1
            return original(command, **kwargs)

        manager._popen = counted_popen
        holder["manager"] = manager
        return manager

    predecessor = _LazyCodexAuth(predecessor_factory, coordinator)
    successor = _LazyCodexAuth(
        lambda lease, guard: _manager(tmp_path, lease_current=lease, lease_guard=guard),
        coordinator,
    )
    predecessor.activate_lease()
    result: list[CodexDeviceAuthState] = []
    starter = Thread(target=lambda: result.append(predecessor.start()))
    starter.start()
    assert approaching_guard.wait(timeout=2)

    successor.activate_lease()
    resume_start.set()
    starter.join(timeout=2)

    assert not starter.is_alive()
    assert result == [CodexDeviceAuthState("failed", failure="permission_or_profile")]
    assert popen_calls == 0
    assert not (tmp_path / "profile").exists()
    assert not any(
        path.name.startswith("reviewer-auth-") for path in tmp_path.iterdir()
    )
    holder["manager"].close()


def test_failed_predecessor_drain_admits_no_successor_owner() -> None:
    coordinator = _CodexAuthLeaseCoordinator()
    manager = Mock(spec=CodexDeviceAuthManager)
    manager.close.side_effect = CodexDeviceAuthError("process")
    predecessor = _LazyCodexAuth(lambda _lease, _guard: manager, coordinator)
    successor = _LazyCodexAuth(lambda _lease, _guard: manager, coordinator)
    predecessor.activate_lease()
    predecessor._manager = manager

    with pytest.raises(CodexDeviceAuthError):
        successor.activate_lease()

    assert successor._lease is None
    assert not coordinator.current(predecessor, cast("int", predecessor._lease))


def test_device_child_uses_the_installed_resource_exec_wrapper(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    captured: dict[str, object] = {}
    commands: list[list[str]] = []
    original = manager._popen

    def capturing_popen(command: list[str], **kwargs: Any):
        captured.update(kwargs)
        commands.append(command)
        return original(command, **kwargs)

    manager._popen = capturing_popen
    _finish(manager)
    assert "preexec_fn" not in captured
    assert tuple(commands[0][1:3]) == (
        "-m",
        "mechanical_librarian.adapters.reviewers.resource_exec",
    )
    assert commands[0][-2] == "start"


def test_clear_failures_never_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    _finish(manager)
    installed = tmp_path / "profile" / "auth.json"
    original_unlink = os.unlink

    def refused_unlink(path: str, *, dir_fd: int | None = None) -> None:
        if path == "auth.json":
            raise OSError("synthetic unlink refusal")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", refused_unlink)
    with pytest.raises(CodexDeviceAuthError):
        manager.clear()
    assert installed.exists()


def test_lazy_production_wrapper_propagates_clear_failure() -> None:
    manager = Mock(spec=CodexDeviceAuthManager)
    manager.clear.side_effect = OSError("private unlink detail")
    lazy = _LazyCodexAuth(lambda _lease, _guard: manager, _CodexAuthLeaseCoordinator())
    lazy.activate_lease()

    with pytest.raises(OSError, match="private unlink detail"):
        lazy.clear()


def test_restart_cleans_private_staging_and_reports_interruption(
    tmp_path: Path,
) -> None:
    stale = tmp_path / "reviewer-auth-stale"
    stale.mkdir(mode=0o700)
    manager = _manager(tmp_path)
    assert manager.status().state == "interrupted"
    assert manager.status().failure == "restart_interrupted"
    assert not stale.exists()


def test_staging_entry_limit_is_enforced_while_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    staging = tmp_path / "reviewer-auth-oversized"
    staging.mkdir(mode=0o700)
    (staging / "first").write_bytes(b"")
    (staging / "second").write_bytes(b"")
    original_scandir = os.scandir
    yielded = 0

    class CountingEntries:
        def __init__(self, directory: int) -> None:
            self._entries = original_scandir(directory)

        def __enter__(self) -> CountingEntries:
            self._entries.__enter__()
            return self

        def __exit__(self, *arguments: object) -> None:
            self._entries.__exit__(*arguments)

        def __iter__(self) -> CountingEntries:
            return self

        def __next__(self) -> os.DirEntry[str]:
            nonlocal yielded
            yielded += 1
            if yielded > 2:
                raise AssertionError("entry traversal exceeded the bounded refusal")
            return next(self._entries)

    monkeypatch.setattr(auth_module, "_MAX_STAGING_ENTRIES", 1)
    monkeypatch.setattr(os, "scandir", CountingEntries)

    with pytest.raises(CodexDeviceAuthError, match="staging"):
        auth_module._remove_staging(staging)

    assert yielded == 2
    assert staging.exists()


def test_restart_staging_root_count_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for suffix in ("one", "two"):
        (tmp_path / f"reviewer-auth-{suffix}").mkdir(mode=0o700)
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"preserved")
    monkeypatch.setattr(auth_module, "_MAX_STAGING_ROOTS", 1)

    state = _manager(tmp_path).status()

    assert state == CodexDeviceAuthState("failed", failure="protocol_or_process")
    assert unrelated.read_bytes() == b"preserved"
    assert any(path.name.startswith("reviewer-auth-") for path in tmp_path.iterdir())


def test_restart_uses_shared_no_follow_staging_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / "reviewer-auth-stale"
    stale.mkdir(mode=0o700)
    calls: list[str] = []
    original = auth_module._remove_staging_at

    def recording_remove(parent: int, name: str) -> None:
        calls.append(name)
        original(parent, name)

    monkeypatch.setattr(auth_module, "_remove_staging_at", recording_remove)
    state = _manager(tmp_path).status()

    assert state.failure == "restart_interrupted"
    assert calls == ["reviewer-auth-stale"]

    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    protected = outside / "auth.json"
    protected.write_bytes(b"preserved")
    (tmp_path / "reviewer-auth-link").symlink_to(outside, target_is_directory=True)
    state = _manager(tmp_path).status()
    assert state == CodexDeviceAuthState("failed", failure="protocol_or_process")
    assert protected.read_bytes() == b"preserved"


def test_restart_rejects_staging_root_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale = tmp_path / "reviewer-auth-replaced"
    stale.mkdir(mode=0o700)
    original_stat = os.stat

    def changed_identity(
        path: str | bytes | int | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        result = original_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == "reviewer-auth-replaced" and dir_fd is not None:
            values = list(result)
            values[1] += 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(os, "stat", changed_identity)

    assert _manager(tmp_path).status() == CodexDeviceAuthState(
        "failed", failure="protocol_or_process"
    )
    assert stale.exists()


def test_auth_store_symlink_and_hardlinked_credential_fail_closed(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    credential = real / "auth.json"
    credential.write_bytes(_auth_document())
    credential.chmod(0o600)
    (tmp_path / "profile").symlink_to(real, target_is_directory=True)
    manager = _manager(tmp_path)
    assert manager.current_credential_status().state == "failed"
    with pytest.raises(CodexDeviceAuthError):
        manager.clear()

    (tmp_path / "profile").unlink()
    (tmp_path / "profile").mkdir(mode=0o700)
    installed = tmp_path / "profile" / "auth.json"
    installed.write_bytes(_auth_document())
    installed.chmod(0o600)
    os.link(installed, tmp_path / "linked-auth.json")
    assert manager.current_credential_status().state == "failed"


def test_atomic_install_loops_short_writes_and_cleans_failed_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "auth.json"
    source.write_bytes(_auth_document())
    source.chmod(0o600)
    original_write = os.write

    def short_write(descriptor: int, payload: bytes | memoryview) -> int:
        return original_write(descriptor, bytes(payload[:3]))

    monkeypatch.setattr(os, "write", short_write)
    target = tmp_path / "target"
    _install_auth(source, target)
    assert (target / "auth.json").read_bytes() == source.read_bytes()

    monkeypatch.setattr(
        os,
        "rename",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace")),
    )
    with pytest.raises(OSError, match="replace"):
        _install_auth(source, target)
    assert not list(target.glob(".auth-*"))
