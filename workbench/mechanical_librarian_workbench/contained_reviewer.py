"""Pinned, tool-free contained reviewer for source-mode workbenches."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, cast

from mechanical_librarian_workbench.contained_reviewer_contract import (
    ContainedReviewRequest,
    ContainedReviewResult,
    ContainedReviewerError,
    ContainedReviewerIdentity,
    canonical_json,
)

PROFILE_SCHEMA = "contained-reviewer-profile-v1"
MAX_PROFILE_BYTES = 65_536
MAX_FAILURE_SIGNAL_BYTES = 65_536
_LIVE_SCHEMA_REJECTION_PREFIX = (
    "Invalid schema for response_format 'codex_output_schema': "
)
_FAILURES = frozenset(
    {
        "runtime", "setup", "auth", "profile", "timeout", "cancel",
        "response_format", "malformed_output",
    }
)
class ContainedReviewerProcessError(ContainedReviewerError):
    """Typed, value-free failure at the reviewer trust boundary."""

    def __init__(self, code: str, *, count: int = 0, digest: str | None = None):
        if code not in _FAILURES:
            raise ValueError("invalid reviewer failure code")
        self.code = code
        self.count = min(max(count, 0), 2_147_483_647)
        self.digest = digest
        super().__init__(code, count=self.count, digest=self.digest)


@dataclass(frozen=True, slots=True)
class _SchemaBinding:
    serialized: bytes
    digest: str
    schema: dict[str, object]


@dataclass(frozen=True, slots=True)
class ContainedReviewerProfile:
    codex_path: Path
    sandbox_path: Path
    auth_store_path: Path
    job_root_path: Path
    identity: ContainedReviewerIdentity
    schema_version: str = PROFILE_SCHEMA

    def __post_init__(self) -> None:
        if (
            self.schema_version != PROFILE_SCHEMA
            or any(
                not isinstance(value, Path) or not value.is_absolute()
                for value in (
                    self.codex_path,
                    self.sandbox_path,
                    self.auth_store_path,
                    self.job_root_path,
                )
            )
            or type(self.identity) is not ContainedReviewerIdentity
        ):
            raise ContainedReviewerProcessError("profile")


def load_contained_reviewer_profile(path: Path) -> ContainedReviewerProfile:
    """Read only canonical profile bytes; do not touch declared artifacts."""
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
            or not 1 <= information.st_size <= MAX_PROFILE_BYTES
        ):
            raise ContainedReviewerProcessError("profile")
        chunks: list[bytes] = []
        remaining = information.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65_536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != information.st_size:
            raise ContainedReviewerProcessError("profile")
        return load_contained_reviewer_profile_bytes(payload)
    except ContainedReviewerProcessError:
        raise
    except (OSError, UnicodeError) as error:
        raise ContainedReviewerProcessError("profile") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_contained_reviewer_profile_bytes(payload: bytes) -> ContainedReviewerProfile:
    """Parse the exact profile bytes already admitted by the workbench authority."""
    try:
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_PROFILE_BYTES:
            raise ContainedReviewerProcessError("profile")
        serialized = payload.decode("utf-8", errors="strict")
        raw = json.loads(serialized, object_pairs_hook=_unique_object)
        if (
            type(raw) is not dict
            or canonical_json(raw) != serialized
            or set(raw)
            != {
                "auth_store_path",
                "codex",
                "job_root_path",
                "limits",
                "model",
                "reasoning",
                "sandbox",
                "schema_version",
            }
            or raw["schema_version"] != PROFILE_SCHEMA
            or type(raw["codex"]) is not dict
            or set(raw["codex"]) != {"path", "sha256", "version"}
            or type(raw["sandbox"]) is not dict
            or set(raw["sandbox"]) != {"path", "sha256", "version"}
            or type(raw["limits"]) is not dict
            or set(raw["limits"])
            != {
                "file_bytes",
                "memory_bytes",
                "output_bytes",
                "processes",
                "timeout_seconds",
            }
        ):
            raise ContainedReviewerProcessError("profile")
        identity = ContainedReviewerIdentity(
            raw["codex"]["sha256"],
            raw["codex"]["version"],
            raw["sandbox"]["sha256"],
            raw["sandbox"]["version"],
            raw["model"],
            raw["reasoning"],
            raw["limits"]["timeout_seconds"],
            raw["limits"]["output_bytes"],
            raw["limits"]["memory_bytes"],
            raw["limits"]["processes"],
            raw["limits"]["file_bytes"],
        )
        return ContainedReviewerProfile(
            Path(raw["codex"]["path"]),
            Path(raw["sandbox"]["path"]),
            Path(raw["auth_store_path"]),
            Path(raw["job_root_path"]),
            identity,
        )
    except ContainedReviewerProcessError:
        raise
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
        ContainedReviewerError,
    ) as error:
        raise ContainedReviewerProcessError("profile") from error


class ContainedReviewerAdapter:
    def __init__(self, profile: ContainedReviewerProfile) -> None:
        self._profile = profile

    def auth(self, action: str) -> str:
        if action not in {"start", "status", "logout"}:
            raise ContainedReviewerProcessError("profile")
        with _bound_artifact(
            self._profile.codex_path,
            self._profile.identity.executable_sha256,
        ) as codex, _bound_artifact(
            self._profile.sandbox_path,
            self._profile.identity.sandbox_sha256,
        ) as sandbox:
            with self._auth_lock():
                with self._empty_auth_config() as empty_config:
                    self._verify_codex(codex, sandbox)
                    command = self._auth_command(codex, sandbox, empty_config)
                    if action == "start":
                        completed = self._run_auth(
                            [*command, "login", "--device-auth"],
                            codex,
                            sandbox,
                            empty_config,
                            interactive=True,
                        )
                        if completed.returncode != 0:
                            raise ContainedReviewerProcessError("auth")
                        return canonical_json(
                            {
                                "mode": "contained",
                                "schema_version": "reviewer-auth-v1",
                                "status": "started",
                            }
                        )
                    if action == "logout":
                        self._require_chatgpt_auth(
                            command, codex, sandbox, empty_config
                        )
                        completed = self._run_auth(
                            [*command, "logout"], codex, sandbox, empty_config
                        )
                        if completed.returncode != 0:
                            raise ContainedReviewerProcessError("auth")
                        return canonical_json(
                            {
                                "mode": "contained",
                                "schema_version": "reviewer-auth-v1",
                                "status": "logged_out",
                            }
                        )
                    self._require_chatgpt_auth(
                        command, codex, sandbox, empty_config
                    )
                    return canonical_json(
                        {
                            "mode": "contained",
                            "schema_version": "reviewer-auth-v1",
                            "status": "chatgpt",
                        }
                    )

    def run(
        self,
        request: ContainedReviewRequest,
        dispatched: Callable[[int, int, str], None],
    ) -> ContainedReviewResult:
        if not sys.platform.startswith("linux"):
            raise ContainedReviewerProcessError("setup")
        job_path: Path | None = None
        self._require_private_directory(self._profile.auth_store_path)
        self._require_private_directory(self._profile.job_root_path)
        trust_failure_path = (
            self._profile.job_root_path / ".reviewer-trust-failure"
        )
        credential_path = self._profile.auth_store_path / "auth.json"
        with (
            _bound_artifact(
                self._profile.codex_path,
                self._profile.identity.executable_sha256,
            ) as codex,
            _bound_artifact(
                self._profile.sandbox_path,
                self._profile.identity.sandbox_sha256,
            ) as sandbox,
        ):
            with self._auth_lock():
                if _path_entry_exists(trust_failure_path):
                    raise ContainedReviewerProcessError("setup")
                with _bound_credential(credential_path) as credential:
                    try:
                        if any(
                            path.name.startswith("contained-reviewer-")
                            for path in self._profile.job_root_path.iterdir()
                        ):
                            raise ContainedReviewerProcessError("setup")
                        job_path = Path(
                            tempfile.mkdtemp(
                                prefix="contained-reviewer-",
                                dir=self._profile.job_root_path,
                            )
                        )
                        os.chmod(job_path, 0o700)
                        provider_state_path = job_path / "provider-state"
                        provider_state_path.mkdir(mode=0o700)
                        with self._empty_auth_config() as empty_config:
                            self._verify_codex(codex, sandbox)
                            self._require_chatgpt_auth(
                                self._auth_command(
                                    codex,
                                    sandbox,
                                    empty_config,
                                    provider_state_path=provider_state_path,
                                    credential_path=credential.invocation_path,
                                ),
                                codex,
                                sandbox,
                                empty_config,
                                credential,
                            )
                        schema_path = job_path / "output-schema.json"
                        result_path = job_path / "result.json"
                        prompt_path = job_path / "prompt.bin"
                        binding = _schema_binding(request)
                        schema_path.write_bytes(binding.serialized)
                        prompt_path.write_bytes(
                            _prompt(
                                request, schema_digest=binding.digest
                            ).encode("utf-8")
                        )
                        os.chmod(schema_path, 0o600)
                        os.chmod(prompt_path, 0o600)
                        stdout_path = job_path / "stdout.bin"
                        stderr_path = job_path / "stderr.bin"
                        with (
                            prompt_path.open("rb") as prompt_file,
                            stdout_path.open("w+b") as stdout_file,
                            stderr_path.open("w+b") as stderr_file,
                        ):
                            returncode = self._run_gated(
                                self._sandbox_command(
                                    job_path,
                                    result_path,
                                    provider_state_path,
                                    codex.invocation_path,
                                    sandbox.invocation_path,
                                    credential.invocation_path,
                                ),
                                codex,
                                sandbox,
                                prompt_file.fileno(),
                                stdout_file.fileno(),
                                stderr_file.fileno(),
                                dispatched,
                                credential_descriptor=credential.descriptor,
                            )
                            if returncode != 0:
                                code, count, digest = _nonzero_failure(
                                    stdout_file,
                                    stderr_file,
                                    returncode=returncode,
                                )
                                raise ContainedReviewerProcessError(
                                    code, count=count, digest=digest
                                )
                        try:
                            result_bytes = result_path.read_bytes()
                        except OSError as error:
                            raise ContainedReviewerProcessError(
                                "malformed_output"
                            ) from error
                        return _validated_result(
                            result_bytes,
                            maximum=self._profile.identity.output_bytes,
                            schema=binding.schema,
                        )
                    finally:
                        cleanup_error: OSError | None = None
                        if job_path is not None:
                            try:
                                shutil.rmtree(job_path)
                            except OSError as error:
                                cleanup_error = error
                        credential_drifted = not _credential_unchanged(
                            credential_path, credential
                        )
                        if credential_drifted or cleanup_error is not None:
                            _record_trust_failure(trust_failure_path)
                        if credential_drifted:
                            raise ContainedReviewerProcessError("auth")
                        if cleanup_error is not None:
                            raise ContainedReviewerProcessError("runtime") from cleanup_error

    def _verify_codex(
        self, codex: "_BoundArtifact", sandbox: "_BoundArtifact"
    ) -> None:
        command = [
            sandbox.invocation_path,
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--unshare-net",
            "--clearenv",
            "--dir",
            "/runtime",
            "--dir",
            "/auth",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for system_path in ("/usr", "/lib", "/lib64"):
            if Path(system_path).exists():
                command.extend(("--ro-bind", system_path, system_path))
        command.extend(
            (
                "--ro-bind",
                codex.invocation_path,
                "/runtime/codex",
                "--chdir",
                "/auth",
                "--setenv",
                "HOME",
                "/auth",
                "--setenv",
                "CODEX_HOME",
                "/auth",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "PATH",
                "/runtime:/usr/bin:/bin",
                "/runtime/codex",
                "--version",
            )
        )
        try:
            completed = subprocess.run(
                command,
                env={},
                capture_output=True,
                pass_fds=(codex.descriptor, sandbox.descriptor),
                timeout=min(self._profile.identity.timeout_seconds, 30),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ContainedReviewerProcessError("setup") from error
        expected = self._profile.identity.executable_version.encode("utf-8")
        if (
            completed.returncode != 0
            or completed.stderr
            or completed.stdout.rstrip(b"\r\n") != expected
        ):
            raise ContainedReviewerProcessError("setup")

    def _require_chatgpt_auth(
        self,
        command: list[str],
        codex: "_BoundArtifact",
        sandbox: "_BoundArtifact",
        empty_config: int,
        credential: "_BoundCredential | None" = None,
    ) -> None:
        completed = self._run_auth(
            [*command, "login", "status"],
            codex,
            sandbox,
            empty_config,
            credential_descriptor=(
                credential.descriptor if credential is not None else None
            ),
            timeout=30,
        )
        status = b"Logged in using ChatGPT"
        canonical = {status, status + b"\n", status + b"\r\n"}
        if (
            completed.returncode != 0
            or not (
                (completed.stdout in canonical and not completed.stderr)
                or (completed.stderr in canonical and not completed.stdout)
            )
        ):
            raise ContainedReviewerProcessError("auth")

    def _auth_command(
        self,
        codex: "_BoundArtifact",
        sandbox: "_BoundArtifact",
        empty_config: int,
        *,
        provider_state_path: Path | None = None,
        credential_path: str | None = None,
    ) -> list[str]:
        if (provider_state_path is None) != (credential_path is None):
            raise ContainedReviewerProcessError("setup")
        command = [
            sandbox.invocation_path,
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--clearenv",
            "--dir",
            "/runtime",
            "--dir",
            "/auth",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for system_path in (
            "/usr",
            "/lib",
            "/lib64",
            "/etc/ssl/certs",
            "/etc/ca-certificates",
            "/etc/resolv.conf",
            "/etc/hosts",
            "/etc/nsswitch.conf",
        ):
            if Path(system_path).exists():
                command.extend(("--ro-bind", system_path, system_path))
        auth_mount = (
            "--bind",
            str(self._profile.auth_store_path),
            "/auth",
        )
        if provider_state_path is not None:
            auth_mount = (
                "--bind",
                str(provider_state_path),
                "/auth",
                "--ro-bind",
                cast(str, credential_path),
                "/auth/auth.json",
            )
        command.extend(
            (
                "--ro-bind",
                codex.invocation_path,
                "/runtime/codex",
                *auth_mount,
                "--perms",
                "0400",
                "--ro-bind-data",
                str(empty_config),
                "/auth/config.toml",
                "--chdir",
                "/auth",
                "--setenv",
                "HOME",
                "/auth",
                "--setenv",
                "CODEX_HOME",
                "/auth",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "PATH",
                "/runtime:/usr/bin:/bin",
                "/runtime/codex",
            )
        )
        for value in _auth_config():
            command.extend(("-c", value))
        return command

    def _run_auth(
        self,
        command: list[str],
        codex: "_BoundArtifact",
        sandbox: "_BoundArtifact",
        empty_config: int,
        *,
        interactive: bool = False,
        timeout: int | None = None,
        credential_descriptor: int | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        pass_fds = (codex.descriptor, sandbox.descriptor, empty_config)
        if credential_descriptor is not None:
            pass_fds = (*pass_fds, credential_descriptor)
        try:
            return subprocess.run(
                command,
                env=self._auth_environment(),
                stdin=None,
                stdout=None if interactive else subprocess.PIPE,
                stderr=None if interactive else subprocess.PIPE,
                pass_fds=pass_fds,
                timeout=min(
                    self._profile.identity.timeout_seconds,
                    timeout or self._profile.identity.timeout_seconds,
                ),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ContainedReviewerProcessError("timeout") from error
        except KeyboardInterrupt as error:
            raise ContainedReviewerProcessError("cancel") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise ContainedReviewerProcessError("auth") from error

    @contextmanager
    def _empty_auth_config(self) -> Iterator[int]:
        read_descriptor = -1
        write_descriptor = -1
        try:
            read_descriptor, write_descriptor = os.pipe()
            os.close(write_descriptor)
            write_descriptor = -1
            yield read_descriptor
        except OSError as error:
            raise ContainedReviewerProcessError("setup") from error
        finally:
            if write_descriptor >= 0:
                os.close(write_descriptor)
            if read_descriptor >= 0:
                os.close(read_descriptor)

    def _auth_environment(self) -> dict[str, str]:
        return {
            "CODEX_HOME": str(self._profile.auth_store_path),
            "HOME": str(self._profile.auth_store_path),
            "LANG": "C.UTF-8",
            "PATH": os.defpath,
        }

    def _auth_lock(self):
        self._require_private_directory(self._profile.auth_store_path)
        lock_path = self._profile.auth_store_path / ".reviewer-auth.lock"
        descriptor = os.open(
            lock_path,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
        ):
            os.close(descriptor)
            raise ContainedReviewerProcessError("auth")

        class _Lock:
            def __enter__(self):
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                return self

            def __exit__(self, *args: object) -> None:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

        return _Lock()

    @staticmethod
    def _require_private_directory(path: Path) -> None:
        try:
            information = path.stat()
        except OSError as error:
            raise ContainedReviewerProcessError("setup") from error
        if (
            not stat.S_ISDIR(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
        ):
            raise ContainedReviewerProcessError("setup")

    def _sandbox_command(
        self,
        job_path: Path,
        result_path: Path,
        provider_state_path: Path,
        codex_path: str,
        sandbox_path: str,
        credential_path: str,
    ) -> list[str]:
        command = [
            sandbox_path,
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--clearenv",
            "--dir",
            "/runtime",
            "--dir",
            "/auth",
            "--dir",
            "/job",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
        for system_path in (
            "/usr",
            "/lib",
            "/lib64",
            "/etc/ssl/certs",
            "/etc/ca-certificates",
            "/etc/resolv.conf",
            "/etc/hosts",
            "/etc/nsswitch.conf",
        ):
            if Path(system_path).exists():
                command.extend(("--ro-bind", system_path, system_path))
        command.extend(
            (
                "--ro-bind",
                codex_path,
                "/runtime/codex",
                "--bind",
                str(provider_state_path),
                "/auth",
                "--ro-bind",
                credential_path,
                "/auth/auth.json",
                "--bind",
                str(job_path),
                "/job",
                "--chdir",
                "/job",
                "--setenv",
                "HOME",
                "/job",
                "--setenv",
                "CODEX_HOME",
                "/auth",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "PATH",
                "/runtime:/usr/bin:/bin",
                "/runtime/codex",
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--ephemeral",
                "--strict-config",
                "--model",
                self._profile.identity.model,
                "--sandbox",
                "read-only",
                "--json",
                "--output-schema",
                "/job/output-schema.json",
                "--output-last-message",
                "/job/" + result_path.name,
            )
        )
        for value in _exact_config(self._profile.identity.reasoning):
            command.extend(("-c", value))
        command.append("-")
        return command

    def _run_gated(
        self,
        command: list[str],
        codex: "_BoundArtifact",
        sandbox: "_BoundArtifact",
        stdin_descriptor: int,
        stdout_descriptor: int,
        stderr_descriptor: int,
        dispatched: Callable[[int, int, str], None],
        *,
        credential_descriptor: int,
    ) -> int:
        release_read, release_write = os.pipe()
        ready_read, ready_write = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(release_write)
                os.close(ready_read)
                os.setsid()
                os.write(ready_write, b"R")
                os.close(ready_write)
                if os.read(release_read, 1) != b"G":
                    os._exit(125)
                os.close(release_read)
                os.dup2(stdin_descriptor, 0)
                os.dup2(stdout_descriptor, 1)
                os.dup2(stderr_descriptor, 2)
                os.set_inheritable(codex.descriptor, True)
                os.set_inheritable(sandbox.descriptor, True)
                os.set_inheritable(credential_descriptor, True)
                self._limit_child()
                os.execve(sandbox.invocation_path, command, {})
            except BaseException:
                os._exit(126)
        os.close(release_read)
        os.close(ready_write)
        released = False
        try:
            if os.read(ready_read, 1) != b"R":
                raise ContainedReviewerProcessError("runtime")
            pgid = pid
            token = _process_start_token(pid)
            try:
                dispatched(pid, pgid, token)
            except Exception:
                os.close(release_write)
                release_write = -1
                _wait_for_exit(pid, 5)
                if _process_identity_alive(pid, pgid, token):
                    _kill_and_prove_dead(pid, pgid, token)
                raise
            os.write(release_write, b"G")
            released = True
            os.close(release_write)
            release_write = -1
            return _wait_for_exit(
                pid, self._profile.identity.timeout_seconds
            )
        except TimeoutError as error:
            _kill_and_prove_dead(pid, pid, _safe_start_token(pid))
            raise ContainedReviewerProcessError("timeout") from error
        except KeyboardInterrupt as error:
            _kill_and_prove_dead(pid, pid, _safe_start_token(pid))
            raise ContainedReviewerProcessError("cancel") from error
        finally:
            os.close(ready_read)
            if release_write >= 0:
                os.close(release_write)
            if not released:
                try:
                    _wait_for_exit(pid, 5)
                except TimeoutError:
                    _kill_and_prove_dead(
                        pid, pid, _safe_start_token(pid)
                    )

    def _limit_child(self) -> None:
        resource.setrlimit(
            resource.RLIMIT_AS,
            (self._profile.identity.memory_bytes, self._profile.identity.memory_bytes),
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (self._profile.identity.file_bytes, self._profile.identity.file_bytes),
        )
        resource.setrlimit(
            resource.RLIMIT_NPROC,
            (self._profile.identity.process_limit, self._profile.identity.process_limit),
        )
        resource.setrlimit(
            resource.RLIMIT_NOFILE,
            (64, 64),
        )


def _process_identity_alive(pid: int, pgid: int, start_token: str) -> bool:
    if (
        type(pid) is not int
        or type(pgid) is not int
        or pid < 2
        or pgid < 2
        or type(start_token) is not str
        or not start_token.isascii()
        or not start_token.isdigit()
    ):
        raise ContainedReviewerProcessError("runtime")
    try:
        if os.getpgid(pid) == pgid and _process_start_token(pid) == start_token:
            return True
    except (OSError, ContainedReviewerProcessError):
        pass
    try:
        names = os.listdir("/proc")
    except OSError as error:
        raise ContainedReviewerProcessError("runtime") from error
    for name in names:
        if not name.isascii() or not name.isdigit():
            continue
        try:
            raw = Path("/proc", name, "stat").read_text(encoding="ascii")
            _, member_group = _proc_identity(raw)
        except (OSError, UnicodeError, ValueError):
            continue
        if member_group == pgid:
            return True
    return False


@dataclass(frozen=True, slots=True)
class _BoundCredential:
    descriptor: int
    invocation_path: str
    fingerprint: tuple[int, int, int, int, str]


def _descriptor_fingerprint(
    descriptor: int,
) -> tuple[int, int, int, int, str]:
    information = os.fstat(descriptor)
    if (
        not stat.S_ISREG(information.st_mode)
        or information.st_uid != os.geteuid()
        or information.st_mode & 0o077
        or not 1 <= information.st_size <= MAX_PROFILE_BYTES
    ):
        raise ContainedReviewerProcessError("auth")
    before = (
        information.st_dev,
        information.st_ino,
        information.st_size,
        stat.S_IMODE(information.st_mode),
    )
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 65_536):
        digest.update(chunk)
    after_information = os.fstat(descriptor)
    after = (
        after_information.st_dev,
        after_information.st_ino,
        after_information.st_size,
        stat.S_IMODE(after_information.st_mode),
    )
    if before != after:
        raise ContainedReviewerProcessError("auth")
    return (*before, digest.hexdigest())


@contextmanager
def _bound_credential(path: Path) -> Iterator[_BoundCredential]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as error:
        raise ContainedReviewerProcessError("auth") from error
    try:
        try:
            bound = _BoundCredential(
                descriptor,
                f"/proc/self/fd/{descriptor}",
                _descriptor_fingerprint(descriptor),
            )
        except OSError as error:
            raise ContainedReviewerProcessError("auth") from error
        yield bound
    finally:
        os.close(descriptor)


def _credential_unchanged(
    path: Path, credential: _BoundCredential
) -> bool:
    try:
        path_information = os.lstat(path)
        if (
            not stat.S_ISREG(path_information.st_mode)
            or path_information.st_uid != os.geteuid()
            or path_information.st_mode & 0o077
            or (
                path_information.st_dev,
                path_information.st_ino,
                path_information.st_size,
                stat.S_IMODE(path_information.st_mode),
            )
            != credential.fingerprint[:4]
        ):
            return False
        return (
            _descriptor_fingerprint(credential.descriptor)
            == credential.fingerprint
        )
    except (OSError, ContainedReviewerProcessError):
        return False


def _path_entry_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as error:
        raise ContainedReviewerProcessError("setup") from error
    return True


def _record_trust_failure(path: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.write(descriptor, b"reviewer-trust-failure-v1\n")
    except FileExistsError:
        return
    except OSError as error:
        raise ContainedReviewerProcessError("setup") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class _BoundArtifact:
    descriptor: int
    invocation_path: str


@contextmanager
def _bound_artifact(
    path: Path, expected_digest: str
) -> Iterator[_BoundArtifact]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o022
            or not information.st_mode & stat.S_IXUSR
        ):
            raise ContainedReviewerProcessError("setup")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1_048_576):
            digest.update(chunk)
        if digest.hexdigest() != expected_digest:
            raise ContainedReviewerProcessError("setup")
        os.lseek(descriptor, 0, os.SEEK_SET)
        invocation_root = "/proc/self/fd" if sys.platform.startswith("linux") else "/dev/fd"
        yield _BoundArtifact(
            descriptor, f"{invocation_root}/{descriptor}"
        )
    except ContainedReviewerProcessError:
        raise
    except OSError as error:
        raise ContainedReviewerProcessError("setup") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _process_start_token(pid: int) -> str:
    try:
        token, _ = _proc_identity(
            Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ContainedReviewerProcessError("runtime") from error
    return token


def _proc_identity(raw: str) -> tuple[str, int]:
    try:
        tail = raw[raw.rindex(")") + 2 :].split()
        process_group = int(tail[2], 10)
        start_token = tail[19]
    except (IndexError, ValueError) as error:
        raise ValueError("invalid proc stat") from error
    if (
        not start_token.isascii()
        or not start_token.isdigit()
        or process_group < 1
    ):
        raise ValueError("invalid proc stat")
    return start_token, process_group


def _safe_start_token(pid: int) -> str:
    try:
        return _process_start_token(pid)
    except ContainedReviewerProcessError:
        return "0"


def _wait_for_exit(pid: int, timeout_seconds: int) -> int:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            waited, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return 0
        if waited == pid:
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            if os.WIFSIGNALED(status):
                return 128 + os.WTERMSIG(status)
            return 126
        if time.monotonic() >= deadline:
            raise TimeoutError
        time.sleep(0.01)


def _kill_and_prove_dead(pid: int, pgid: int, start_token: str) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        _wait_for_exit(pid, 5)
    except TimeoutError as error:
        raise ContainedReviewerProcessError("runtime") from error
    if _process_identity_alive(pid, pgid, start_token):
        raise ContainedReviewerProcessError("runtime")


def _diagnostic(stdout_file, stderr_file) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    for value in (stdout_file, stderr_file):
        value.flush()
        value.seek(0)
        while chunk := value.read(65_536):
            count += len(chunk)
            digest.update(chunk)
    return count, digest.hexdigest()


def _nonzero_failure(
    stdout_file,
    stderr_file,
    *,
    returncode: int,
) -> tuple[str, int, str]:
    count, digest = _diagnostic(stdout_file, stderr_file)
    code = "runtime"
    if returncode == 1 and _is_live_schema_rejection(stdout_file, stderr_file):
        code = "response_format"
    return code, count, digest


def _is_live_schema_rejection(stdout_file, stderr_file) -> bool:
    """Recognize only the pinned Codex JSONL response-schema rejection event."""
    try:
        stdout_file.flush()
        stderr_file.flush()
        stdout_size = os.fstat(stdout_file.fileno()).st_size
        stderr_size = os.fstat(stderr_file.fileno()).st_size
        if (
            stderr_size != 0
            or not 1 <= stdout_size <= MAX_FAILURE_SIGNAL_BYTES
        ):
            return False
        stdout_file.seek(0)
        payload = stdout_file.read(MAX_FAILURE_SIGNAL_BYTES + 1)
        if len(payload) != stdout_size or not payload.endswith(b"\n"):
            return False
        serialized = payload.decode("utf-8", errors="strict")
        lines = serialized[:-1].split("\n")
        if not 1 <= len(lines) <= 64 or any(not line for line in lines):
            return False
        events: list[dict[str, object]] = []
        for line in lines:
            event = json.loads(
                line,
                object_pairs_hook=_unique_failure_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(value)
                ),
            )
            if (
                type(event) is not dict
                or type(event.get("type")) is not str
            ):
                return False
            events.append(event)
        raw = events[-1]
        if type(raw) is not dict or set(raw) != {"error", "type"}:
            return False
        error = raw["error"]
        if (
            raw["type"] != "turn.failed"
            or type(error) is not dict
            or set(error) != {"message"}
            or type(error["message"]) is not str
        ):
            return False
        message = cast(str, error["message"])
        if message.startswith(_LIVE_SCHEMA_REJECTION_PREFIX):
            return len(message) > len(_LIVE_SCHEMA_REJECTION_PREFIX)
        service = json.loads(
            message,
            object_pairs_hook=_unique_failure_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(value)
            ),
        )
        if (
            type(service) is not dict
            or set(service) != {"error", "status", "type"}
            or service["type"] != "error"
            or type(service["status"]) is not int
            or service["status"] != 400
        ):
            return False
        service_error = service["error"]
        if (
            type(service_error) is not dict
            or set(service_error) != {"code", "message", "param", "type"}
            or service_error["type"] != "invalid_request_error"
            or service_error["code"] != "invalid_json_schema"
            or service_error["param"] != "text.format.schema"
            or type(service_error["message"]) is not str
        ):
            return False
        service_message = cast(str, service_error["message"])
        return service_message.startswith(
            _LIVE_SCHEMA_REJECTION_PREFIX
        ) and len(service_message) > len(_LIVE_SCHEMA_REJECTION_PREFIX)
    except (
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
    ):
        return False


def _unique_failure_object(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate failure event key")
        result[key] = value
    return result


def _validated_result(
    result_bytes: bytes,
    *,
    maximum: int,
    schema: dict[str, object],
) -> ContainedReviewResult:
    digest = hashlib.sha256(result_bytes).hexdigest()
    if len(result_bytes) > maximum:
        raise ContainedReviewerProcessError(
            "malformed_output", count=len(result_bytes), digest=digest
        )
    try:
        serialized = result_bytes.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ContainedReviewerProcessError(
            "malformed_output", count=len(result_bytes), digest=digest
        ) from error
    try:
        raw = json.loads(
            serialized,
            object_pairs_hook=_unique_output_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(value)
            ),
        )
        if (
            type(raw) is not dict
            or canonical_json(raw) != serialized
            or not _matches_output_schema(raw, schema)
        ):
            raise ValueError
    except (
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
    ) as error:
        raise ContainedReviewerProcessError(
            "malformed_output", count=len(result_bytes), digest=digest
        ) from error
    return ContainedReviewResult(serialized, digest)


def _prompt(request: ContainedReviewRequest, *, schema_digest: str) -> str:
    return (
        "Follow the bounded instruction using only the canonical input. "
        "Return exactly one canonical JSON object matching the supplied schema.\n"
        "Instruction: " + request.instruction + "\n"
        "Schema digest: " + schema_digest + "\n"
        "Input: " + request.input_document
    )


def _output_schema(request: ContainedReviewRequest) -> dict[str, object]:
    raw = json.loads(
        request.output_schema,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    if type(raw) is not dict:
        raise ContainedReviewerProcessError("response_format")
    return raw


def _schema_binding(request: ContainedReviewRequest) -> _SchemaBinding:
    schema = _output_schema(request)
    _validate_output_schema(schema)
    serialized = request.output_schema.encode("utf-8")
    return _SchemaBinding(
        serialized, hashlib.sha256(serialized).hexdigest(), schema
    )


def _validate_output_schema(schema: object) -> None:
    """Permit only the pinned structured-output subset and typed bindings."""
    allowed = {
        "$schema",
        "additionalProperties",
        "anyOf",
        "const",
        "enum",
        "items",
        "maxItems",
        "maxLength",
        "minItems",
        "minLength",
        "properties",
        "required",
        "type",
    }
    json_types = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }
    primitive_types = set(json_types.values())
    all_types = primitive_types | {"object", "array"}

    def visit(value: object, *, root: bool = False) -> None:
        if type(value) is not dict or not value or not set(value) <= allowed:
            raise ContainedReviewerProcessError("response_format")
        if root:
            if value.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
                raise ContainedReviewerProcessError("response_format")
        elif "$schema" in value:
            raise ContainedReviewerProcessError("response_format")
        primitive = value.get("type")
        if primitive is not None and (
            type(primitive) is not str or primitive not in all_types
        ):
            raise ContainedReviewerProcessError("response_format")
        if "const" in value or "enum" in value:
            enum = value.get("enum", [])
            if type(enum) is not list:
                raise ContainedReviewerProcessError("response_format")
            values = ([value["const"]] if "const" in value else []) + enum
            if (
                primitive not in primitive_types
                or not values
                or any(json_types.get(type(item)) != primitive for item in values)
                or (
                    "enum" in value
                    and len({canonical_json(item) for item in enum}) != len(enum)
                )
            ):
                raise ContainedReviewerProcessError("response_format")
        if (
            "const" in value
            and "enum" in value
            and value["const"] not in value["enum"]
        ):
            raise ContainedReviewerProcessError("response_format")
        if "items" in value:
            if primitive != "array":
                raise ContainedReviewerProcessError("response_format")
            visit(value["items"])
        for key in ("maxItems", "minItems"):
            if key in value and (
                primitive != "array"
                or type(value[key]) is not int
                or value[key] < 0
            ):
                raise ContainedReviewerProcessError("response_format")
        for key in ("maxLength", "minLength"):
            if key in value and (
                primitive != "string"
                or type(value[key]) is not int
                or value[key] < 0
            ):
                raise ContainedReviewerProcessError("response_format")
        if "required" in value and (
            primitive != "object"
            or type(value["required"]) is not list
            or not value["required"]
            or any(type(item) is not str for item in value["required"])
            or len(set(value["required"])) != len(value["required"])
        ):
            raise ContainedReviewerProcessError("response_format")
        if "additionalProperties" in value and (
            primitive != "object" or value["additionalProperties"] is not False
        ):
            raise ContainedReviewerProcessError("response_format")
        if "anyOf" in value:
            if (
                set(value) != {"anyOf"}
                or type(value["anyOf"]) is not list
                or not value["anyOf"]
            ):
                raise ContainedReviewerProcessError("response_format")
            for child in value["anyOf"]:
                visit(child)
        if "properties" in value:
            if (
                primitive != "object"
                or type(value["properties"]) is not dict
                or not value["properties"]
                or type(value.get("required")) is not list
                or set(value["required"]) != set(value["properties"])
                or value.get("additionalProperties") is not False
            ):
                raise ContainedReviewerProcessError("response_format")
            for child in value["properties"].values():
                visit(child)
        if primitive == "array" and "items" not in value:
            raise ContainedReviewerProcessError("response_format")
        if primitive == "object" and "properties" not in value:
            raise ContainedReviewerProcessError("response_format")
        if "minItems" in value and "maxItems" in value and value["minItems"] > value["maxItems"]:
            raise ContainedReviewerProcessError("response_format")
        if "minLength" in value and "maxLength" in value and value["minLength"] > value["maxLength"]:
            raise ContainedReviewerProcessError("response_format")

    visit(schema, root=True)


def _matches_output_schema(value: object, schema: dict[str, object]) -> bool:
    """Validate one result against the exact locally admitted schema subset."""
    if "anyOf" in schema:
        alternatives = cast(list[dict[str, object]], schema["anyOf"])
        return any(
            _matches_output_schema(value, alternative)
            for alternative in alternatives
        )
    expected = schema.get("type")
    type_matches = {
        "null": value is None,
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "string": type(value) is str,
        "array": type(value) is list,
        "object": type(value) is dict,
    }
    if not type_matches.get(cast(str, expected), False):
        return False
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in cast(list[object], schema["enum"]):
        return False
    if type(value) is str:
        minimum = cast(int, schema.get("minLength", 0))
        maximum = cast(int, schema.get("maxLength", len(value)))
        return minimum <= len(value) <= maximum
    if type(value) is list:
        minimum = cast(int, schema.get("minItems", 0))
        maximum = cast(int, schema.get("maxItems", len(value)))
        item_schema = cast(dict[str, object], schema["items"])
        return (
            minimum <= len(value) <= maximum
            and all(_matches_output_schema(item, item_schema) for item in value)
        )
    if type(value) is dict:
        properties = cast(dict[str, dict[str, object]], schema["properties"])
        return set(value) == set(properties) and all(
            _matches_output_schema(item, properties[key])
            for key, item in value.items()
        )
    return True


def _exact_config(reasoning: str) -> tuple[str, ...]:
    return (
        f'model_reasoning_effort="{reasoning}"',
        "features.shell_tool=false",
        "features.unified_exec=false",
        "agents.enabled=false",
        "features.multi_agent=false",
        "features.apps=false",
        "features.plugins=false",
        "features.browser_use=false",
        "features.computer_use=false",
        "features.image_generation=false",
        "features.skill_search=false",
        "features.tool_call_mcp_elicitation=false",
        "apps._default.enabled=false",
        'web_search="disabled"',
        "features.hooks=false",
        "features.memories=false",
        "features.remote_plugin=false",
        'approval_policy="never"',
        'history.persistence="none"',
        'forced_login_method="chatgpt"',
        'cli_auth_credentials_store="file"',
        "check_for_update_on_startup=false",
        "feedback.enabled=false",
        "analytics.enabled=false",
        'shell_environment_policy.inherit="none"',
    )


def _auth_config() -> tuple[str, ...]:
    return (
        'forced_login_method="chatgpt"',
        'cli_auth_credentials_store="file"',
        "check_for_update_on_startup=false",
        "feedback.enabled=false",
        "analytics.enabled=false",
        'history.persistence="none"',
        "features.hooks=false",
        "features.memories=false",
        "features.remote_plugin=false",
        "features.plugins=false",
        "features.apps=false",
        "apps._default.enabled=false",
    )


def _unique_output_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate result key")
        result[key] = value
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContainedReviewerProcessError("profile")
        result[key] = value
    return result


__all__ = [
    "ContainedReviewerAdapter",
    "PROFILE_SCHEMA",
    "ContainedReviewerProfile",
    "ContainedReviewerProcessError",
    "load_contained_reviewer_profile",
    "load_contained_reviewer_profile_bytes",
]
