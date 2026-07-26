"""Pinned, tool-free Codex CLI qualification adapter."""

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
from dataclasses import dataclass
from pathlib import Path

from mechanical_librarian.application.shadow_reviews import ShadowReviewError
from mechanical_librarian.domain.shadow_review import (
    QUALIFICATION_MODE,
    RESULT_SCHEMA,
    ReviewerProfileIdentity,
    ShadowReviewRequest,
    ShadowReviewResult,
    ShadowReviewValueError,
    canonical_json,
)

PROFILE_SCHEMA = "reviewer-qualification-profile-v1"
MAX_PROFILE_BYTES = 65_536
_FAILURES = frozenset(
    {"runtime", "setup", "auth", "profile", "timeout", "cancel", "output"}
)
MAX_CITATIONS = 256
MAX_PROBLEMS = 32


class ShadowReviewerError(ShadowReviewError):
    """Typed, value-free failure at the reviewer trust boundary."""

    def __init__(self, code: str, *, count: int = 0, digest: str | None = None):
        if code not in _FAILURES:
            raise ValueError("invalid reviewer failure code")
        self.code = code
        self.count = min(max(count, 0), 2_147_483_647)
        self.digest = digest
        super().__init__(code, count=self.count, digest=self.digest)


@dataclass(frozen=True, slots=True)
class QualificationProfile:
    codex_path: Path
    sandbox_path: Path
    auth_store_path: Path
    job_root_path: Path
    identity: ReviewerProfileIdentity
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
            or type(self.identity) is not ReviewerProfileIdentity
            or self.identity.mode != QUALIFICATION_MODE
        ):
            raise ShadowReviewerError("profile")


def load_qualification_profile(path: Path) -> QualificationProfile:
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
            raise ShadowReviewerError("profile")
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
            raise ShadowReviewerError("profile")
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
                "mode",
                "model",
                "output_version",
                "packet_version",
                "policy_version",
                "profile_version",
                "prompt_version",
                "reasoning",
                "sandbox",
                "schema_version",
            }
            or raw["schema_version"] != PROFILE_SCHEMA
            or raw["mode"] != QUALIFICATION_MODE
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
            raise ShadowReviewerError("profile")
        identity = ReviewerProfileIdentity(
            raw["codex"]["sha256"],
            raw["codex"]["version"],
            raw["sandbox"]["sha256"],
            raw["sandbox"]["version"],
            raw["model"],
            raw["reasoning"],
            raw["prompt_version"],
            raw["profile_version"],
            raw["packet_version"],
            raw["output_version"],
            raw["policy_version"],
            raw["limits"]["timeout_seconds"],
            raw["limits"]["output_bytes"],
            raw["limits"]["memory_bytes"],
            raw["limits"]["processes"],
            raw["limits"]["file_bytes"],
        )
        return QualificationProfile(
            Path(raw["codex"]["path"]),
            Path(raw["sandbox"]["path"]),
            Path(raw["auth_store_path"]),
            Path(raw["job_root_path"]),
            identity,
        )
    except ShadowReviewerError:
        raise
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
        ShadowReviewValueError,
    ) as error:
        raise ShadowReviewerError("profile") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class CodexShadowReviewer:
    def __init__(self, profile: QualificationProfile) -> None:
        self._profile = profile

    def auth(self, action: str) -> str:
        if action not in {"start", "status", "logout"}:
            raise ShadowReviewerError("profile")
        with self._auth_lock():
            self._verify_codex()
            command = [str(self._profile.codex_path)]
            if action == "start":
                command.extend(("login", "--device-auth"))
                completed = subprocess.run(
                    command,
                    env=self._auth_environment(),
                    stdin=None,
                    stdout=None,
                    stderr=None,
                    timeout=self._profile.identity.timeout_seconds,
                    check=False,
                )
                if completed.returncode != 0:
                    raise ShadowReviewerError("auth")
                return canonical_json(
                    {
                        "mode": QUALIFICATION_MODE,
                        "schema_version": "reviewer-auth-v1",
                        "status": "started",
                    }
                )
            if action == "logout":
                self._require_chatgpt_auth(command)
                completed = subprocess.run(
                    [*command, "logout"],
                    env=self._auth_environment(),
                    capture_output=True,
                    timeout=self._profile.identity.timeout_seconds,
                    check=False,
                )
                if completed.returncode != 0:
                    raise ShadowReviewerError("auth")
                return canonical_json(
                    {
                        "mode": QUALIFICATION_MODE,
                        "schema_version": "reviewer-auth-v1",
                        "status": "logged_out",
                    }
                )
            self._require_chatgpt_auth(command)
            return canonical_json(
                {
                    "mode": QUALIFICATION_MODE,
                    "schema_version": "reviewer-auth-v1",
                    "status": "chatgpt",
                }
            )

    def run(
        self,
        request: ShadowReviewRequest,
        dispatched: Callable[[int, int, str], None],
    ) -> ShadowReviewResult:
        if not sys.platform.startswith("linux"):
            raise ShadowReviewerError("setup")
        self._verify_sandbox()
        job_path: Path | None = None
        with self._auth_lock():
            self._verify_codex()
            self._require_chatgpt_auth([str(self._profile.codex_path)])
            try:
                self._require_private_directory(self._profile.job_root_path)
                job_path = Path(
                    tempfile.mkdtemp(
                        prefix="shadow-review-", dir=self._profile.job_root_path
                    )
                )
                os.chmod(job_path, 0o700)
                schema_path = job_path / "output-schema.json"
                result_path = job_path / "result.json"
                schema_path.write_text(
                    canonical_json(_output_schema(request)), encoding="utf-8"
                )
                os.chmod(schema_path, 0o600)
                prompt = _prompt(request).encode("utf-8")
                stdout_path = job_path / "stdout.bin"
                stderr_path = job_path / "stderr.bin"
                with stdout_path.open("w+b") as stdout_file, stderr_path.open(
                    "w+b"
                ) as stderr_file:
                    process = subprocess.Popen(
                        self._sandbox_command(job_path, result_path),
                        stdin=subprocess.PIPE,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        env={},
                        start_new_session=True,
                        preexec_fn=self._limit_child,
                    )
                    pid = process.pid
                    pgid = os.getpgid(pid)
                    token = _process_start_token(pid)
                    try:
                        dispatched(pid, pgid, token)
                    except Exception:
                        _kill_and_prove_dead(process, pgid, token)
                        raise
                    try:
                        process.communicate(
                            prompt, timeout=self._profile.identity.timeout_seconds
                        )
                    except subprocess.TimeoutExpired as error:
                        _kill_and_prove_dead(process, pgid, token)
                        raise ShadowReviewerError("timeout") from error
                    except KeyboardInterrupt as error:
                        _kill_and_prove_dead(process, pgid, token)
                        raise ShadowReviewerError("cancel") from error
                    if process.returncode != 0:
                        count, digest = _diagnostic(stdout_file, stderr_file)
                        raise ShadowReviewerError(
                            "runtime", count=count, digest=digest
                        )
                try:
                    result_bytes = result_path.read_bytes()
                except OSError as error:
                    raise ShadowReviewerError("output") from error
                if len(result_bytes) > self._profile.identity.output_bytes:
                    raise ShadowReviewerError("output")
                try:
                    serialized = result_bytes.decode("utf-8", errors="strict")
                    return ShadowReviewResult.from_json(serialized, request)
                except (UnicodeError, ShadowReviewValueError) as error:
                    raise ShadowReviewerError(
                        "output",
                        count=len(result_bytes),
                        digest=hashlib.sha256(result_bytes).hexdigest(),
                    ) from error
            finally:
                if job_path is not None:
                    shutil.rmtree(job_path, ignore_errors=True)

    def _verify_codex(self) -> None:
        _verify_file(self._profile.codex_path, self._profile.identity.executable_sha256)
        try:
            completed = subprocess.run(
                [str(self._profile.codex_path), "--version"],
                env=self._auth_environment(),
                capture_output=True,
                timeout=min(self._profile.identity.timeout_seconds, 30),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ShadowReviewerError("setup") from error
        expected = self._profile.identity.executable_version.encode("utf-8")
        if (
            completed.returncode != 0
            or completed.stderr
            or completed.stdout.rstrip(b"\r\n") != expected
        ):
            raise ShadowReviewerError("setup")

    def _verify_sandbox(self) -> None:
        _verify_file(self._profile.sandbox_path, self._profile.identity.sandbox_sha256)
        self._require_private_directory(self._profile.auth_store_path)
        self._require_private_directory(self._profile.job_root_path)

    def _require_chatgpt_auth(self, command: list[str]) -> None:
        try:
            completed = subprocess.run(
                [*command, "login", "status"],
                env=self._auth_environment(),
                capture_output=True,
                timeout=min(self._profile.identity.timeout_seconds, 30),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ShadowReviewerError("auth") from error
        if (
            completed.returncode != 0
            or completed.stderr
            or completed.stdout.rstrip(b"\r\n") != b"Logged in using ChatGPT"
        ):
            raise ShadowReviewerError("auth")

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
            raise ShadowReviewerError("auth")

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
            raise ShadowReviewerError("setup") from error
        if (
            not stat.S_ISDIR(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
        ):
            raise ShadowReviewerError("setup")

    def _sandbox_command(self, job_path: Path, result_path: Path) -> list[str]:
        command = [
            str(self._profile.sandbox_path),
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
                str(self._profile.codex_path),
                "/runtime/codex",
                "--bind",
                str(self._profile.auth_store_path),
                "/auth",
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
                "--ephemeral",
                "--strict-config",
                "--model",
                self._profile.identity.model,
                "--sandbox",
                "read-only",
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


def process_identity_alive(pid: int, pgid: int, start_token: str) -> bool:
    if (
        type(pid) is not int
        or type(pgid) is not int
        or pid < 2
        or pgid < 2
        or type(start_token) is not str
        or not start_token.isascii()
        or not start_token.isdigit()
    ):
        raise ShadowReviewerError("runtime")
    try:
        if os.getpgid(pid) == pgid and _process_start_token(pid) == start_token:
            return True
    except (OSError, ShadowReviewerError):
        pass
    try:
        names = os.listdir("/proc")
    except OSError as error:
        raise ShadowReviewerError("runtime") from error
    for name in names:
        if not name.isascii() or not name.isdigit():
            continue
        try:
            raw = Path("/proc", name, "stat").read_text(encoding="ascii")
            tail = raw[raw.rindex(")") + 2 :].split()
            member_group = int(tail[2])
        except (IndexError, OSError, UnicodeError, ValueError):
            continue
        if member_group == pgid:
            return True
    return False


def _verify_file(path: Path, expected_digest: str) -> None:
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
            raise ShadowReviewerError("setup")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1_048_576):
            digest.update(chunk)
        if digest.hexdigest() != expected_digest:
            raise ShadowReviewerError("setup")
    except ShadowReviewerError:
        raise
    except OSError as error:
        raise ShadowReviewerError("setup") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _process_start_token(pid: int) -> str:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()
        token = fields[21]
    except (IndexError, OSError, UnicodeError) as error:
        raise ShadowReviewerError("runtime") from error
    if not token.isascii() or not token.isdigit():
        raise ShadowReviewerError("runtime")
    return token


def _kill_and_prove_dead(
    process: subprocess.Popen[bytes], pgid: int, start_token: str
) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    if process_identity_alive(process.pid, pgid, start_token):
        raise ShadowReviewerError("runtime")


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


def _prompt(request: ShadowReviewRequest) -> str:
    return (
        "Outcome: provide one bounded shadow recommendation for every proposal.\n"
        "Constraints: use only packet candidates and citations; do not infer missing "
        "facts; choose manual when evidence is insufficient; problems are adverse "
        "facts only.\nEvidence: the canonical packet follows.\nOutput: return only "
        "one object matching shadow-review-result-v1.\nStop: after every proposal "
        "is covered exactly once.\n"
        + request.packet.to_json()
    )


def _output_schema(request: ShadowReviewRequest) -> dict[str, object]:
    proposal_ids = [value["proposal_id"] for value in request.packet.proposals]
    citation_ids = [value["citation_id"] for value in request.packet.citations]
    candidate_ids = [
        candidate["candidate_id"]
        for proposal in request.packet.proposals
        for candidate in proposal["candidates"]
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": {
            "recommendations": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {
                            "anyOf": [{"enum": candidate_ids}, {"type": "null"}]
                        },
                        "citations": {
                            "items": {"enum": citation_ids},
                            "maxItems": MAX_CITATIONS,
                            "minItems": 1,
                            "type": "array",
                            "uniqueItems": True,
                        },
                        "problems": {
                            "items": {
                                "maxLength": 1_024,
                                "minLength": 1,
                                "type": "string",
                            },
                            "maxItems": MAX_PROBLEMS,
                            "type": "array",
                        },
                        "proposal_id": {"enum": proposal_ids},
                        "reasons": {
                            "items": {
                                "maxLength": 1_024,
                                "minLength": 1,
                                "type": "string",
                            },
                            "maxItems": 16,
                            "minItems": 1,
                            "type": "array",
                        },
                        "recommendation": {"enum": sorted(_recommendations())},
                    },
                    "required": [
                        "candidate_id",
                        "citations",
                        "problems",
                        "proposal_id",
                        "reasons",
                        "recommendation",
                    ],
                    "type": "object",
                },
                "maxItems": len(proposal_ids),
                "minItems": len(proposal_ids),
                "type": "array",
            },
            "review_request_id": {"const": request.review_request_id},
            "schema_version": {"const": RESULT_SCHEMA},
        },
        "required": ["recommendations", "review_request_id", "schema_version"],
        "type": "object",
    }


def _recommendations() -> set[str]:
    return {"recommend_candidate", "recommend_keep_current", "manual"}


def _exact_config(reasoning: str) -> tuple[str, ...]:
    return (
        f'model_reasoning_effort="{reasoning}"',
        "features.shell_tool=false",
        "features.unified_exec=false",
        "agents.enabled=false",
        "features.multi_agent=false",
        "features.apps=false",
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


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ShadowReviewerError("profile")
        result[key] = value
    return result


__all__ = [
    "CodexShadowReviewer",
    "PROFILE_SCHEMA",
    "QualificationProfile",
    "ShadowReviewerError",
    "load_qualification_profile",
    "process_identity_alive",
]
