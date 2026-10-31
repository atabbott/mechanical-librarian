"""Bounded background device authentication for the pinned contained Codex CLI."""

from __future__ import annotations

import json
import os
import re
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread

from mechanical_librarian.adapters.reviewers import process_group as process_groups
from mechanical_librarian.application.reviewer_contracts import CodexDeviceAuthState

_DEVICE_URL = "https://auth.openai.com/codex/device"
_CODE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
_CODE_ANYWHERE = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{4}")
_URL_ANYWHERE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
# The pinned CLI colours its prompt even with stdout piped, NO_COLOR or
# TERM=dumb. Only complete SGR sequences are removed; any other escape stays.
_SGR = re.compile(r"\x1b\[[0-9;]*m")
_STEP_PREFIX = re.compile(r"^[0-9]+\. ")
_EXPIRY_LINE = "Enter this one-time code (expires in 15 minutes)"
_TIMEOUT_LINE = "device auth timed out after 15 minutes"
_MAX_OUTPUT_BYTES = 16_384
_MAX_AUTH_BYTES = 65_536
_DEVICE_DEADLINE_SECONDS = 900
_SHUTDOWN_GRACE_SECONDS = 5
_MAX_STAGING_ENTRIES = 4096
_MAX_STAGING_DEPTH = 16
_MAX_STAGING_ROOTS = 64
_MAX_STAGING_PARENT_ENTRIES = 4096
_CLEANUP_RETRY_DELAYS = (0.05, 0.1, 0.2)
_RESOURCE_EXEC_MODULE = "mechanical_librarian.adapters.reviewers.resource_exec"


class CodexDeviceAuthError(RuntimeError):
    """Value-free device authentication failure."""


@dataclass(frozen=True, slots=True)
class CodexAuthCommand:
    command: Sequence[str]
    environment: Mapping[str, str]
    pass_fds: tuple[int, ...] = ()
    close: Callable[[], None] = lambda: None


CommandFactory = Callable[
    [Path, str], CodexAuthCommand | tuple[Sequence[str], Mapping[str, str]]
]
PopenFactory = Callable[..., subprocess.Popen[bytes]]
CaptureProcessGroup = Callable[
    [subprocess.Popen[bytes]], process_groups.ReviewerProcessGroup
]
ProcessGroupAction = Callable[
    [subprocess.Popen[bytes], process_groups.ReviewerProcessGroup], None
]


class CodexDeviceAuthManager:
    """Own one exact child, staging directory, and atomic auth-store installation."""

    def __init__(
        self,
        auth_store: Path,
        command_factory: CommandFactory,
        *,
        popen: PopenFactory = subprocess.Popen,
        lease_current: Callable[[], bool] = lambda: True,
        lease_guard: Callable[[], AbstractContextManager[None]] = nullcontext,
        activity_guard: Callable[[], AbstractContextManager[None]] = nullcontext,
        capture_process_group: CaptureProcessGroup = process_groups.capture_process_group,
        finish_process_group: ProcessGroupAction = lambda _process, identity: (
            process_groups.finish_process_group(identity)
        ),
        terminate_process_group: ProcessGroupAction = lambda _process, identity: (
            process_groups.terminate_process_group(identity)
        ),
        quarantine_activity: Callable[[], None] = lambda: None,
    ) -> None:
        self._auth_store = auth_store
        self._command_factory = command_factory
        self._popen = popen
        self._lease_current = lease_current
        self._lease_guard = lease_guard
        self._activity_guard = activity_guard
        self._capture_process_group = capture_process_group
        self._finish_process_group = finish_process_group
        self._terminate_process_group = terminate_process_group
        self._quarantine_activity = quarantine_activity
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._process_group: process_groups.ReviewerProcessGroup | None = None
        self._thread: Thread | None = None
        self._cancelled = False
        self._spawning = False
        self._installing = False
        self._state = CodexDeviceAuthState("ready")
        self._recovered = False
        self._trust_failed = False
        self._quarantined = False

    def _require_lease(self) -> None:
        if not self._lease_current():
            raise CodexDeviceAuthError("lease")

    def _prepare_store(self) -> None:
        with self._lease_guard():
            self._require_lease()
            if self._recovered:
                return
            recovered = self._recover_interrupted_staging()
            with self._lock:
                self._state = recovered
                self._recovered = True
                self._trust_failed = recovered.state == "failed"

    def start(self) -> CodexDeviceAuthState:
        self._prepare_store()
        with self._lock:
            if self._quarantined:
                return self._state
            if self._trust_failed:
                return self._state
            if self._thread is not None and self._thread.is_alive():
                return self._state
            self._cancelled = False
            self._state = CodexDeviceAuthState("starting")
            thread = Thread(target=self._flow, name="reviewer-device-auth", daemon=True)
            self._thread = thread
        thread.start()
        return CodexDeviceAuthState("starting")

    def status(self) -> CodexDeviceAuthState:
        self._prepare_store()
        with self._lock:
            current = self._state
            if current.state == "issued":
                self._state = CodexDeviceAuthState(
                    "waiting",
                    verification_url=current.verification_url,
                    user_code=current.user_code,
                    expires_in_seconds=current.expires_in_seconds,
                )
            return current

    def cancel(self) -> CodexDeviceAuthState:
        with self._lock:
            if self._quarantined:
                raise CodexDeviceAuthError("process")
            if self._trust_failed:
                return self._state
            if self._installing:
                return self._state
            if self._state.state == "usable" and (
                self._process is None or self._process.poll() is not None
            ):
                return self._state
            process = self._process
            process_group = self._process_group
            self._cancelled = True
            self._state = CodexDeviceAuthState("cancelled")
        if process is not None or process_group is not None:
            self._release_retained_process_or_quarantine()
        return CodexDeviceAuthState("cancelled")

    def close(self) -> None:
        """Boundedly stop this generation without deleting installed credentials."""
        self.cancel()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_SHUTDOWN_GRACE_SECONDS * 2)
            if thread.is_alive():
                raise CodexDeviceAuthError("codex auth is unavailable")

    def clear(self) -> CodexDeviceAuthState:
        self._prepare_store()
        with self._lock:
            if self._quarantined or self._trust_failed:
                raise CodexDeviceAuthError("staging")
        self.close()
        with self._activity_guard(), self._lease_guard():
            self._require_lease()
            directory = -1
            try:
                directory = _private_directory_descriptor(
                    self._auth_store, create=False
                )
                try:
                    os.unlink("auth.json", dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise CodexDeviceAuthError("codex auth is unavailable") from error
            finally:
                if directory >= 0:
                    os.close(directory)
        with self._lock:
            self._state = CodexDeviceAuthState("ready")
            return self._state

    def current_credential_status(self) -> CodexDeviceAuthState:
        with self._lease_guard():
            self._prepare_store()
            with self._lock:
                if self._quarantined:
                    return self._state
                if self._trust_failed:
                    return self._state
                if self._thread is not None and self._thread.is_alive():
                    return self._state
            target = self._auth_store / "auth.json"
            try:
                os.lstat(target)
            except FileNotFoundError:
                current = self.status()
                return (
                    current
                    if current.state in {"interrupted", "failed"}
                    else CodexDeviceAuthState("ready")
                )
            except OSError:
                return CodexDeviceAuthState("failed", failure="permission_or_profile")
            try:
                _private_directory_descriptor(
                    self._auth_store, create=False, close=True
                )
                _validated_auth(target)
                return CodexDeviceAuthState("configured")
            except (OSError, CodexDeviceAuthError):
                return CodexDeviceAuthState("failed", failure="permission_or_profile")

    def recheck_credential_status(self) -> CodexDeviceAuthState:
        self._prepare_store()
        with self._lease_guard():
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    return self._state
        current = self.current_credential_status()
        if current.state != "configured":
            return current
        with self._activity_guard(), self._lease_guard():
            self._require_lease()
            with self._lock:
                if self._thread is not None and self._thread.is_alive():
                    return self._state
            current = self.current_credential_status()
            if current.state != "configured":
                return current
            return self._recheck_configured_credential_status()

    def _recheck_configured_credential_status(self) -> CodexDeviceAuthState:
        """Keep the process-bearing recheck directly bounded by the activity gate."""
        try:
            prepared = _prepared(self._command_factory(self._auth_store, "status"))
            try:
                output, returncode = self._status_command(
                    prepared, time.monotonic() + 30, flow_owned=False
                )
            finally:
                prepared.close()
        except (OSError, subprocess.SubprocessError, CodexDeviceAuthError):
            with self._lock:
                if self._quarantined:
                    return self._state
            return CodexDeviceAuthState("failed", failure="permission_or_profile")
        if returncode == 0 and output == b"Logged in using ChatGPT\n":
            self._require_lease()
            return CodexDeviceAuthState("usable")
        return CodexDeviceAuthState("failed", failure="permission_or_profile")

    def _flow(self) -> None:
        # The small wrapper makes the whole long-lived device flow one testable
        # activity boundary without hiding which operation owns the lock.
        with self._activity_guard():
            try:
                self._run_flow()
            finally:
                try:
                    self._release_retained_process_or_quarantine()
                except CodexDeviceAuthError:
                    pass

    def _run_flow(self) -> None:
        deadline = time.monotonic() + _DEVICE_DEADLINE_SECONDS
        parent = self._auth_store.parent
        staging: Path | None = None
        prepared: CodexAuthCommand | None = None
        process: subprocess.Popen[bytes] | None = None
        process_group: process_groups.ReviewerProcessGroup | None = None
        try:
            with self._lease_guard():
                self._require_lease()
                with self._lock:
                    if self._cancelled:
                        return
                _private_directory_descriptor(parent, create=True, close=True)
                staging = Path(tempfile.mkdtemp(prefix="reviewer-auth-", dir=parent))
                os.chmod(staging, 0o700)
                _private_directory_descriptor(staging, create=False, close=True)
                prepared = _prepared(self._command_factory(staging, "start"))
            process, process_group = self._spawn(prepared, flow_owned=True)
            with self._lock:
                cancelled = self._cancelled
            if cancelled:
                self._release_retained_process_or_quarantine()
                return
            if process.stdout is None:
                raise CodexDeviceAuthError("process")
            lines = _bounded_output_lines(
                process,
                process.stdout,
                deadline=deadline,
                on_lines=lambda value: self._publish_issued(value),
                stop_process=self._release_retained_process_or_quarantine,
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._release_retained_process_or_quarantine()
                lines.append(_TIMEOUT_LINE)
                returncode = -signal.SIGKILL
            else:
                try:
                    returncode = process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    self._release_retained_process_or_quarantine()
                    lines.append(_TIMEOUT_LINE)
                    returncode = -signal.SIGKILL
            with self._lock:
                cancelled = self._cancelled
            if cancelled:
                return
            if returncode != 0:
                terminal = lines[-1] if lines else ""
                if terminal == _TIMEOUT_LINE:
                    self._set_state(CodexDeviceAuthState("expired"))
                else:
                    self._set_state(
                        CodexDeviceAuthState("failed", failure="permission_or_process")
                    )
                return
            if _issued_state(lines) is None:
                raise CodexDeviceAuthError("protocol")
            self._finish_owned_process(process, process_group)
            self._release(process, process_group)
            process = None
            process_group = None
            prepared.close()
            prepared = None
            self._set_state(CodexDeviceAuthState("validating"))
            auth_path = staging / "auth.json"
            _validated_auth(auth_path)
            prepared = _prepared(self._command_factory(staging, "status"))
            output, returncode = self._status_command(
                prepared, deadline, flow_owned=True
            )
            if not (returncode == 0 and output == b"Logged in using ChatGPT\n"):
                raise CodexDeviceAuthError("permission")
            # Cancellation wins until this short terminal reservation. Once it
            # is reserved, installation wins and no manager lock covers I/O.
            with self._lease_guard():
                self._require_lease()
                with self._lock:
                    if self._cancelled:
                        return
                    self._installing = True
                try:
                    _install_auth(auth_path, self._auth_store)
                    _remove_staging(staging)
                    staging = None
                    self._require_lease()
                    with self._lock:
                        self._state = CodexDeviceAuthState("usable")
                finally:
                    with self._lock:
                        self._installing = False
        except (OSError, subprocess.SubprocessError, CodexDeviceAuthError):
            with self._lock:
                if not self._cancelled:
                    self._state = CodexDeviceAuthState(
                        "failed", failure="protocol_or_process"
                    )
        finally:
            if prepared is not None:
                prepared.close()
            if staging is not None:
                try:
                    _remove_staging(staging)
                except (OSError, CodexDeviceAuthError):
                    with self._lock:
                        self._trust_failed = True
                        self._state = CodexDeviceAuthState(
                            "failed", failure="protocol_or_process"
                        )

    def _spawn(
        self, prepared: CodexAuthCommand, *, flow_owned: bool
    ) -> tuple[subprocess.Popen[bytes], process_groups.ReviewerProcessGroup]:
        with self._lease_guard():
            self._require_lease()
            with self._lock:
                if self._quarantined or self._process is not None or self._spawning:
                    raise CodexDeviceAuthError("process")
                if (
                    not flow_owned
                    and self._thread is not None
                    and self._thread.is_alive()
                ):
                    raise CodexDeviceAuthError("process")
                self._spawning = True
            try:
                process = self._popen(
                    _resource_command(prepared.command),
                    env=dict(prepared.environment),
                    pass_fds=prepared.pass_fds,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except BaseException:
                with self._lock:
                    self._spawning = False
                raise
            try:
                process_group = self._capture_process_group(process)
            except process_groups.ReviewerProcessGroupError as error:
                try:
                    process_groups.kill_spawned_process_group(process)
                except process_groups.ReviewerProcessGroupError as cleanup_error:
                    raise CodexDeviceAuthError("process") from cleanup_error
                finally:
                    with self._lock:
                        self._spawning = False
                raise CodexDeviceAuthError("process") from error
            except BaseException:
                try:
                    process_groups.kill_spawned_process_group(process)
                finally:
                    with self._lock:
                        self._spawning = False
                raise
            with self._lock:
                self._spawning = False
                self._process = process
                self._process_group = process_group
                cancelled = self._cancelled
        if cancelled:
            self._release_retained_process_or_quarantine()
            raise CodexDeviceAuthError("cancelled")
        return process, process_group

    def _release(
        self,
        process: subprocess.Popen[bytes] | None,
        process_group: process_groups.ReviewerProcessGroup | None,
    ) -> None:
        with self._lock:
            if self._process is process and self._process_group == process_group:
                self._process = None
                self._process_group = None

    def _status_command(
        self, prepared: CodexAuthCommand, deadline: float, *, flow_owned: bool
    ) -> tuple[bytes, int]:
        process, process_group = self._spawn(prepared, flow_owned=flow_owned)
        try:
            result = _bounded_status(
                process,
                prepared,
                deadline,
                stop_process=self._release_retained_process_or_quarantine,
            )
            self._finish_owned_process(process, process_group)
            self._release(process, process_group)
            return result
        finally:
            with self._lock:
                retained = (
                    self._process is process and self._process_group == process_group
                )
            if retained:
                self._release_retained_process_or_quarantine()

    def _release_retained_process_or_quarantine(self) -> None:
        """Prove the exact retained group absent or poison all Codex admission."""
        with self._lock:
            if self._quarantined:
                raise CodexDeviceAuthError("process")
            process = self._process
            process_group = self._process_group
        if process is None and process_group is None:
            return
        if process is None or process_group is None:
            self._quarantine_retained_process()
            raise CodexDeviceAuthError("process")
        for delay in (0.0, *_CLEANUP_RETRY_DELAYS):
            if delay:
                time.sleep(delay)
            try:
                self._stop_owned_process(process, process_group)
            except CodexDeviceAuthError:
                continue
            self._release(process, process_group)
            return
        self._quarantine_retained_process()
        raise CodexDeviceAuthError("process")

    def _quarantine_retained_process(self) -> None:
        with self._lock:
            self._quarantined = True
            self._state = CodexDeviceAuthState("failed", failure="protocol_or_process")
        self._quarantine_activity()

    def _stop_owned_process(
        self,
        process: subprocess.Popen[bytes],
        identity: process_groups.ReviewerProcessGroup,
    ) -> None:
        try:
            self._terminate_process_group(process, identity)
        except process_groups.ReviewerProcessGroupError as error:
            raise CodexDeviceAuthError("process") from error

    def _finish_owned_process(
        self,
        process: subprocess.Popen[bytes],
        identity: process_groups.ReviewerProcessGroup,
    ) -> None:
        try:
            self._finish_process_group(process, identity)
        except process_groups.ReviewerProcessGroupError as error:
            raise CodexDeviceAuthError("process") from error

    def _publish_issued(self, lines: list[str]) -> None:
        issued = _issued_state(lines)
        if issued is not None:
            with self._lock:
                if not self._cancelled:
                    self._state = issued

    def _recover_interrupted_staging(self) -> CodexDeviceAuthState:
        parent = self._auth_store.parent
        descriptor = -1
        try:
            try:
                os.lstat(parent)
            except FileNotFoundError:
                return CodexDeviceAuthState("ready")
            descriptor = _private_directory_descriptor(parent, create=False)
            interrupted = False
            roots = 0
            parent_entries = 0
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    parent_entries += 1
                    if parent_entries > _MAX_STAGING_PARENT_ENTRIES:
                        raise CodexDeviceAuthError("staging")
                    if not entry.name.startswith("reviewer-auth-"):
                        continue
                    roots += 1
                    if roots > _MAX_STAGING_ROOTS:
                        raise CodexDeviceAuthError("staging")
                    _remove_staging_at(descriptor, entry.name)
                    interrupted = True
            if interrupted:
                os.fsync(descriptor)
            return CodexDeviceAuthState(
                "interrupted" if interrupted else "ready",
                failure="restart_interrupted" if interrupted else None,
            )
        except (OSError, CodexDeviceAuthError):
            return CodexDeviceAuthState("failed", failure="protocol_or_process")
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _set_state(self, state: CodexDeviceAuthState) -> None:
        with self._lock:
            if not self._cancelled:
                self._state = state


def _issued_state(lines: list[str]) -> CodexDeviceAuthState | None:
    prompt = [_prompt_text(line) for line in lines]
    expiry = [index for index, line in enumerate(prompt) if line == _EXPIRY_LINE]
    urls = [index for index, line in enumerate(prompt) if _URL_ANYWHERE.search(line)]
    codes = [
        (index, line)
        for index, line in enumerate(prompt)
        if _CODE_ANYWHERE.search(line)
    ]
    if not expiry and not urls and not codes:
        return None
    if len(expiry) != 1 or len(urls) != 1 or len(codes) != 1:
        return None
    url_index = urls[0]
    code_index, code = codes[0]
    if prompt[url_index] != _DEVICE_URL or not _CODE.fullmatch(code):
        return None
    if not (url_index < expiry[0] < code_index):
        return None
    return CodexDeviceAuthState(
        "issued",
        verification_url=_DEVICE_URL,
        user_code=code,
        expires_in_seconds=900,
    )


def _prompt_text(line: str) -> str:
    """Remove only surrounding whitespace and one optional "N. " step prefix."""
    return _STEP_PREFIX.sub("", line.strip(), count=1)


def _remove_staging(path: Path) -> None:
    """Remove only the exact private staging tree with bounded no-follow descent."""
    parent = _private_directory_descriptor(path.parent, create=False)
    try:
        _remove_staging_at(parent, path.name)
        os.fsync(parent)
    finally:
        os.close(parent)


def _remove_staging_at(parent: int, name: str) -> None:
    """Remove one exact staging child relative to its already-bound parent."""
    if not name.startswith("reviewer-auth-") or name in {
        "reviewer-auth-",
        ".",
        "..",
    }:
        raise CodexDeviceAuthError("staging")
    staging = os.open(
        name,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent,
    )
    try:
        information = os.fstat(staging)
        if (
            not stat.S_ISDIR(information.st_mode)
            or information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise CodexDeviceAuthError("staging")
        _remove_staging_contents(staging, depth=0, remaining=[_MAX_STAGING_ENTRIES])
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if current.st_dev != information.st_dev or current.st_ino != information.st_ino:
            raise CodexDeviceAuthError("staging")
    finally:
        os.close(staging)
    os.rmdir(name, dir_fd=parent)


def _remove_staging_contents(
    directory: int, *, depth: int, remaining: list[int]
) -> None:
    if depth > _MAX_STAGING_DEPTH:
        raise CodexDeviceAuthError("staging")
    with os.scandir(directory) as entries:
        for entry in entries:
            remaining[0] -= 1
            if remaining[0] < 0:
                raise CodexDeviceAuthError("staging")
            name = entry.name
            information = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(information.st_mode):
                child = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory,
                )
                try:
                    child_information = os.fstat(child)
                    if (
                        child_information.st_dev != information.st_dev
                        or child_information.st_ino != information.st_ino
                    ):
                        raise CodexDeviceAuthError("staging")
                    _remove_staging_contents(
                        child, depth=depth + 1, remaining=remaining
                    )
                finally:
                    os.close(child)
                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (
                    current.st_dev != information.st_dev
                    or current.st_ino != information.st_ino
                ):
                    raise CodexDeviceAuthError("staging")
                os.rmdir(name, dir_fd=directory)
            else:
                os.unlink(name, dir_fd=directory)


def _stop_process(
    process: subprocess.Popen[bytes], identity: process_groups.ReviewerProcessGroup
) -> None:
    try:
        process_groups.terminate_process_group(identity)
    except process_groups.ReviewerProcessGroupError as error:
        raise CodexDeviceAuthError("process") from error


def _resource_command(command: Sequence[str]) -> tuple[str, ...]:
    return (sys.executable, "-m", _RESOURCE_EXEC_MODULE, *command)


def _bounded_status(
    process: subprocess.Popen[bytes],
    prepared: CodexAuthCommand,
    deadline: float,
    *,
    stop_process: Callable[[], None],
) -> tuple[bytes, int]:
    """Run one status command with bounded merged output and one deadline."""
    if process.stdout is None:
        stop_process()
        raise CodexDeviceAuthError("process")
    output = bytearray()
    try:
        descriptor = process.stdout.fileno()
    except (AttributeError, OSError):
        descriptor = None
    selector: selectors.BaseSelector | None = None
    if descriptor is not None:
        selector = selectors.DefaultSelector()
        selector.register(descriptor, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop_process()
                raise subprocess.TimeoutExpired(prepared.command, 0)
            if selector is not None:
                if not selector.select(timeout=min(0.25, remaining)):
                    if process.poll() is None:
                        continue
                assert descriptor is not None
                chunk = os.read(
                    descriptor, min(4096, _MAX_OUTPUT_BYTES + 1 - len(output))
                )
            else:
                chunk = process.stdout.read(
                    min(4096, _MAX_OUTPUT_BYTES + 1 - len(output))
                )
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > _MAX_OUTPUT_BYTES:
                stop_process()
                raise CodexDeviceAuthError("protocol")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stop_process()
            raise subprocess.TimeoutExpired(prepared.command, 0)
        try:
            return bytes(output), process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            stop_process()
            raise
    finally:
        if selector is not None:
            selector.close()


def _bounded_output_lines(
    process: subprocess.Popen[bytes],
    stream: object,
    *,
    deadline: float,
    on_lines: Callable[[list[str]], None],
    stop_process: Callable[[], None],
) -> list[str]:
    """Read bounded chunks without permitting an unbounded pipe line allocation."""
    output = bytearray()
    lines: list[str] = []
    pending = bytearray()
    try:
        descriptor = stream.fileno()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        descriptor = None
    selector: selectors.BaseSelector | None = None
    if descriptor is not None:
        selector = selectors.DefaultSelector()
        selector.register(descriptor, selectors.EVENT_READ)
    try:
        while True:
            if time.monotonic() >= deadline:
                stop_process()
                return [*lines, _TIMEOUT_LINE]
            if selector is not None:
                if not selector.select(timeout=min(0.25, deadline - time.monotonic())):
                    if process.poll() is not None:
                        chunk = os.read(descriptor, 4096)
                        if not chunk:
                            break
                    else:
                        continue
                else:
                    chunk = os.read(descriptor, 4096)
            else:
                chunk = stream.read(4096)  # type: ignore[attr-defined]
            if not chunk:
                break
            output.extend(chunk)
            pending.extend(chunk)
            if len(output) > _MAX_OUTPUT_BYTES or len(pending) > _MAX_OUTPUT_BYTES:
                raise CodexDeviceAuthError("protocol")
            while b"\n" in pending:
                raw_line, _, remainder = pending.partition(b"\n")
                pending = bytearray(remainder)
                lines.append(_validated_output_line(bytes(raw_line)))
                on_lines(lines)
        if pending:
            lines.append(_validated_output_line(bytes(pending)))
            on_lines(lines)
        return lines
    finally:
        if selector is not None:
            selector.close()


def _validated_output_line(raw_line: bytes) -> str:
    try:
        line = raw_line.decode("utf-8", "strict")
    except UnicodeError as error:
        raise CodexDeviceAuthError("protocol") from error
    line = _SGR.sub("", line)
    if any(unicodedata.category(character) == "Cc" for character in line):
        raise CodexDeviceAuthError("protocol")
    return line


def _prepared(
    value: CodexAuthCommand | tuple[Sequence[str], Mapping[str, str]],
) -> CodexAuthCommand:
    if isinstance(value, CodexAuthCommand):
        return value
    command, environment = value
    return CodexAuthCommand(command, environment)


def _read_validated_auth(path: Path) -> tuple[dict[str, object], bytes]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_nlink != 1
            or information.st_size < 2
            or information.st_size > _MAX_AUTH_BYTES
            or stat.S_IMODE(information.st_mode) != 0o600
        ):
            raise CodexDeviceAuthError("credential")
        before = (
            information.st_dev,
            information.st_ino,
            information.st_size,
            information.st_mtime_ns,
        )
        payload = bytearray()
        while len(payload) < information.st_size:
            chunk = os.read(descriptor, information.st_size - len(payload))
            if not chunk:
                break
            payload.extend(chunk)
        after_information = os.fstat(descriptor)
        after = (
            after_information.st_dev,
            after_information.st_ino,
            after_information.st_size,
            after_information.st_mtime_ns,
        )
        if len(payload) != information.st_size or before != after:
            raise CodexDeviceAuthError("credential")
        value = json.loads(payload.decode("utf-8", "strict"), object_pairs_hook=_unique)
        tokens = value.get("tokens") if type(value) is dict else None
        if (
            type(value) is not dict
            or set(value) != {"auth_mode", "OPENAI_API_KEY", "tokens", "last_refresh"}
            or value["auth_mode"] != "chatgpt"
            or value["OPENAI_API_KEY"] is not None
            or type(value["last_refresh"]) is not str
            or not value["last_refresh"]
            or type(tokens) is not dict
            or set(tokens)
            != {"id_token", "access_token", "refresh_token", "account_id"}
            or any(type(item) is not str or not item for item in tokens.values())
        ):
            raise CodexDeviceAuthError("credential")
        return value, bytes(payload)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        if isinstance(error, CodexDeviceAuthError):
            raise
        raise CodexDeviceAuthError("credential") from error
    finally:
        if "descriptor" in locals():
            os.close(descriptor)


def _validated_auth(path: Path) -> dict[str, object]:
    return _read_validated_auth(path)[0]


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _install_auth(source: Path, target_directory: Path) -> None:
    _document, payload = _read_validated_auth(source)
    directory = _private_directory_descriptor(target_directory, create=True)
    temporary = ".auth-" + secrets.token_hex(16)
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory,
        )
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("credential write failed")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(temporary, "auth.json", src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(directory)


def _private_directory_descriptor(
    path: Path, *, create: bool, close: bool = False
) -> int:
    if create:
        try:
            path.mkdir(mode=0o700, parents=True)
        except FileExistsError:
            pass
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    information = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(information.st_mode)
        or information.st_uid != os.geteuid()
        or stat.S_IMODE(information.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise CodexDeviceAuthError("directory")
    if close:
        os.close(descriptor)
        return -1
    return descriptor


__all__ = [
    "CodexDeviceAuthError",
    "CodexAuthCommand",
    "CodexDeviceAuthManager",
    "CodexDeviceAuthState",
]
