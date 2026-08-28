"""Bounded background device authentication for the pinned contained Codex CLI."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Lock, Thread

from mechanical_librarian.application.reviewer_contracts import CodexDeviceAuthState

_DEVICE_URL = "https://auth.openai.com/codex/device"
_CODE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
_EXPIRY_LINE = "Enter this one-time code (expires in 15 minutes)"
_TIMEOUT_LINE = "device auth timed out after 15 minutes"
_MAX_OUTPUT_BYTES = 16_384
_MAX_AUTH_BYTES = 65_536


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
RunFactory = Callable[..., subprocess.CompletedProcess[bytes]]


class CodexDeviceAuthManager:
    """Own one exact child, staging directory, and atomic auth-store installation."""

    def __init__(
        self,
        auth_store: Path,
        command_factory: CommandFactory,
        *,
        popen: PopenFactory = subprocess.Popen,
        run: RunFactory = subprocess.run,
    ) -> None:
        self._auth_store = auth_store
        self._command_factory = command_factory
        self._popen = popen
        self._run = run
        self._lock = Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._thread: Thread | None = None
        self._cancelled = False
        self._state = self._recover_interrupted_staging()

    def start(self) -> CodexDeviceAuthState:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._state
            self._cancelled = False
            self._state = CodexDeviceAuthState("starting")
            thread = Thread(target=self._flow, name="reviewer-device-auth", daemon=True)
            self._thread = thread
            thread.start()
            return self._state

    def status(self) -> CodexDeviceAuthState:
        with self._lock:
            return self._state

    def cancel(self) -> CodexDeviceAuthState:
        with self._lock:
            process = self._process
            self._cancelled = True
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            self._state = CodexDeviceAuthState("cancelled")
            return self._state

    def clear(self) -> CodexDeviceAuthState:
        self.cancel()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=35)
            if thread.is_alive():
                raise CodexDeviceAuthError("codex auth is unavailable")
        directory = -1
        try:
            directory = _private_directory_descriptor(self._auth_store, create=False)
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
            _private_directory_descriptor(self._auth_store, create=False, close=True)
            _validated_auth(target)
            return CodexDeviceAuthState("configured")
        except (OSError, CodexDeviceAuthError):
            return CodexDeviceAuthState("failed", failure="permission_or_profile")

    def recheck_credential_status(self) -> CodexDeviceAuthState:
        current = self.current_credential_status()
        if current.state != "configured":
            return current
        try:
            prepared = _prepared(self._command_factory(self._auth_store, "status"))
            try:
                completed = self._run(
                    prepared.command,
                    env=dict(prepared.environment),
                    pass_fds=prepared.pass_fds,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                )
            finally:
                prepared.close()
        except (OSError, subprocess.SubprocessError, CodexDeviceAuthError):
            return CodexDeviceAuthState("failed", failure="permission_or_profile")
        if (
            completed.returncode == 0
            and completed.stdout == b"Logged in using ChatGPT\n"
            and completed.stderr == b""
        ):
            return CodexDeviceAuthState("usable")
        return CodexDeviceAuthState("failed", failure="permission_or_profile")

    def _flow(self) -> None:
        parent = self._auth_store.parent
        staging: Path | None = None
        prepared: CodexAuthCommand | None = None
        output = bytearray()
        try:
            _private_directory_descriptor(parent, create=True, close=True)
            staging = Path(tempfile.mkdtemp(prefix="reviewer-auth-", dir=parent))
            os.chmod(staging, 0o700)
            _private_directory_descriptor(staging, create=False, close=True)
            prepared = _prepared(self._command_factory(staging, "start"))
            process = self._popen(
                prepared.command,
                env=dict(prepared.environment),
                pass_fds=prepared.pass_fds,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with self._lock:
                self._process = process
            if process.stdout is None:
                raise CodexDeviceAuthError("process")
            lines: list[str] = []
            for raw_line in iter(process.stdout.readline, b""):
                output.extend(raw_line)
                if len(output) > _MAX_OUTPUT_BYTES:
                    raise CodexDeviceAuthError("protocol")
                try:
                    line = raw_line.decode("utf-8", "strict").rstrip("\n")
                except UnicodeError as error:
                    raise CodexDeviceAuthError("protocol") from error
                if "\r" in line or any(ord(character) < 32 for character in line):
                    raise CodexDeviceAuthError("protocol")
                lines.append(line)
                issued = _issued_state(lines)
                if issued is not None:
                    with self._lock:
                        if not self._cancelled:
                            self._state = issued
            returncode = process.wait()
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
            prepared.close()
            prepared = None
            self._set_state(CodexDeviceAuthState("validating"))
            auth_path = staging / "auth.json"
            _validated_auth(auth_path)
            prepared = _prepared(self._command_factory(staging, "status"))
            completed = self._run(
                prepared.command,
                env=dict(prepared.environment),
                pass_fds=prepared.pass_fds,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                check=False,
            )
            if not (
                completed.returncode == 0
                and completed.stdout == b"Logged in using ChatGPT\n"
                and completed.stderr == b""
            ):
                raise CodexDeviceAuthError("permission")
            with self._lock:
                if self._cancelled:
                    return
            _install_auth(auth_path, self._auth_store)
            self._set_state(CodexDeviceAuthState("usable"))
        except (OSError, subprocess.SubprocessError, CodexDeviceAuthError):
            with self._lock:
                if not self._cancelled:
                    self._state = CodexDeviceAuthState(
                        "failed", failure="protocol_or_process"
                    )
        finally:
            if prepared is not None:
                prepared.close()
            with self._lock:
                self._process = None
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

    def _recover_interrupted_staging(self) -> CodexDeviceAuthState:
        parent = self._auth_store.parent
        try:
            try:
                os.lstat(parent)
            except FileNotFoundError:
                return CodexDeviceAuthState("ready")
            _private_directory_descriptor(parent, create=False, close=True)
            interrupted = False
            for candidate in parent.iterdir():
                if not candidate.name.startswith("reviewer-auth-"):
                    continue
                information = candidate.lstat()
                if (
                    not stat.S_ISDIR(information.st_mode)
                    or information.st_uid != os.geteuid()
                    or stat.S_IMODE(information.st_mode) != 0o700
                ):
                    raise CodexDeviceAuthError("staging")
                shutil.rmtree(candidate)
                interrupted = True
            return CodexDeviceAuthState(
                "interrupted" if interrupted else "ready",
                failure="restart_interrupted" if interrupted else None,
            )
        except (OSError, CodexDeviceAuthError):
            return CodexDeviceAuthState("failed", failure="protocol_or_process")

    def _set_state(self, state: CodexDeviceAuthState) -> None:
        with self._lock:
            if not self._cancelled:
                self._state = state


def _issued_state(lines: list[str]) -> CodexDeviceAuthState | None:
    expiry = [index for index, line in enumerate(lines) if line == _EXPIRY_LINE]
    urls = [index for index, line in enumerate(lines) if line == _DEVICE_URL]
    codes = [(index, line) for index, line in enumerate(lines) if _CODE.fullmatch(line)]
    if not expiry and not urls and not codes:
        return None
    if len(expiry) != 1 or len(urls) != 1 or len(codes) != 1:
        return None
    code_index, code = codes[0]
    if not (urls[0] < expiry[0] < code_index):
        return None
    return CodexDeviceAuthState(
        "waiting",
        verification_url=_DEVICE_URL,
        user_code=code,
        expires_in_seconds=900,
    )


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
