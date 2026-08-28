from __future__ import annotations

import io
import json
import os
import stat
import subprocess
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from mechanical_librarian.adapters.reviewers.codex_device_auth import (
    CodexDeviceAuthError,
    CodexDeviceAuthManager,
    _install_auth,
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

    def wait(self) -> int:
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode


def _manager(
    tmp_path: Path,
    *,
    output: bytes = _PROMPT,
    returncode: int = 0,
    status: subprocess.CompletedProcess[bytes] | None = None,
) -> CodexDeviceAuthManager:
    def command(staging: Path, action: str):
        return ([action, str(staging)], {"CODEX_HOME": str(staging)})

    def popen(command: list[str], **_kwargs: Any):
        staging = Path(command[1])
        if returncode == 0:
            auth = staging / "auth.json"
            auth.write_bytes(_auth_document())
            auth.chmod(0o600)
        return _Process(output, returncode)

    return CodexDeviceAuthManager(
        tmp_path / "profile",
        command,
        popen=popen,
        run=lambda *_args, **_kwargs: (
            status
            or subprocess.CompletedProcess(
                ("status",), 0, b"Logged in using ChatGPT\n", b""
            )
        ),
    )


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


def test_ordinary_credential_status_does_not_launch_cli(tmp_path: Path) -> None:
    calls: list[str] = []
    manager = _manager(tmp_path)
    _finish(manager)
    manager._run = lambda *_args, **_kwargs: calls.append("run")  # type: ignore[method-assign]
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


def test_clear_removes_installed_authority_immediately(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _finish(manager)
    assert (tmp_path / "profile" / "auth.json").exists()
    assert manager.clear().state == "ready"
    assert not (tmp_path / "profile" / "auth.json").exists()


def test_cancel_targets_only_the_exact_active_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    process = _Process(b"", 0)
    process.returncode = None  # type: ignore[assignment]
    manager._process = process  # type: ignore[assignment]
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    assert manager.cancel().state == "cancelled"
    assert killed == [(123456789, 15)]


def test_repeated_start_cannot_spawn_a_second_flow(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    release = Event()
    manager._flow = lambda: release.wait(timeout=2)  # type: ignore[method-assign]
    manager.start()
    thread = manager._thread
    manager.start()
    assert manager._thread is thread
    release.set()
    assert thread is not None
    thread.join(timeout=2)


def test_restart_cleans_private_staging_and_reports_interruption(
    tmp_path: Path,
) -> None:
    stale = tmp_path / "reviewer-auth-stale"
    stale.mkdir(mode=0o700)
    manager = _manager(tmp_path)
    assert manager.status().state == "interrupted"
    assert manager.status().failure == "restart_interrupted"
    assert not stale.exists()


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
