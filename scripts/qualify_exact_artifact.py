#!/usr/bin/env python3
"""Build, install, and exercise one source-bound wheel without uv cache reuse."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol
from zipfile import BadZipFile, ZipFile, ZipInfo


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_WHEEL_BYTES = 67_108_864
_MAX_WHEEL_MEMBERS = 4_096
_MAX_WHEEL_MEMBER_BYTES = 16_777_216
_MAX_INSTALLED_BYTES = 134_217_728
_MAX_METADATA_BYTES = 1_048_576
_MAX_COMMAND_STREAM_BYTES = 1_048_576
_COMMAND_TIMEOUT_SECONDS = 180
_CANONICAL_TEST_ARGS = (
    "-m",
    "unittest",
    "discover",
    "-s",
    "tests",
    "-t",
    ".",
    "-p",
    "test_*.py",
)


class QualificationFailure(RuntimeError):
    """A fixed, path-free exact-artifact qualification failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _Digest(Protocol):
    def update(self, data: bytes) -> object: ...


@dataclass(frozen=True, slots=True)
class _WheelMember:
    name: str
    size: int
    record_hash: str


@dataclass(frozen=True, slots=True)
class _WheelManifest:
    distribution_version: str
    dist_info: str
    record_name: str
    members: tuple[_WheelMember, ...]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class ExactArtifactEvidence:
    schema: str
    status: str
    source_sha: str
    wheel_sha256: str
    wheel_payload_sha256: str
    installed_payload_sha256: str
    distribution: str
    distribution_version: str
    console_script: str
    console_stdout_sha256: str
    cache_disabled: bool
    isolated_environment: bool
    explicit_wheel_install: bool
    outside_checkout: bool
    locked_dependencies: bool
    full_suite_executed: bool
    wheel_record_validated: bool
    retained_wheel: bool
    retained_environment: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def qualify_exact_artifact(
    source_root: Path,
    expected_source_sha: str,
    *,
    distribution: str = "mechanical-librarian",
    import_name: str = "mechanical_librarian",
    console_script: str = "mechanical-librarian",
    console_args: tuple[str, ...] = ("metadata-report", "--help"),
    test_args: tuple[str, ...] | None = _CANONICAL_TEST_ARGS,
    python_executable: str = sys.executable,
    wheel_output: Path | None = None,
    environment_path: Path | None = None,
) -> ExactArtifactEvidence:
    """Qualify one clean Git source revision through its exact installed wheel."""

    source_root = source_root.resolve()
    if _SOURCE_SHA.fullmatch(expected_source_sha) is None:
        raise QualificationFailure("source_identity_invalid")
    if not source_root.is_dir() or not (source_root / "pyproject.toml").is_file():
        raise QualificationFailure("source_repository_invalid")

    actual_root = _decode_stdout(
        _run(
            ("git", "-C", str(source_root), "rev-parse", "--show-toplevel"),
            reason="source_repository_invalid",
        ),
        encoding="utf-8",
        reason="source_repository_invalid",
    ).strip()
    if Path(actual_root).resolve() != source_root:
        raise QualificationFailure("source_repository_invalid")
    actual_sha = _verify_source_checkout(source_root, expected_source_sha)

    environment = _qualification_environment()
    uv_executable = _required_executable("uv", environment)
    if (wheel_output is None) != (environment_path is None):
        raise QualificationFailure("retained_workspace_invalid")

    with tempfile.TemporaryDirectory(
        prefix="mechanical-librarian-exact-artifact-"
    ) as temporary:
        root = Path(temporary)
        build_source = root / "source"
        _run(
            (
                "git",
                "clone",
                "--no-local",
                "--no-checkout",
                "--quiet",
                str(source_root),
                str(build_source),
            ),
            reason="source_snapshot_failed",
        )
        _run(
            (
                "git",
                "-C",
                str(build_source),
                "checkout",
                "--detach",
                "--quiet",
                expected_source_sha,
            ),
            reason="source_snapshot_failed",
        )
        _verify_source_checkout(build_source, expected_source_sha)

        run_directory = root / "run"
        run_directory.mkdir()
        retained = wheel_output is not None
        if retained:
            output = _new_directory(wheel_output, "retained_workspace_invalid")
            qualified_environment = _new_path(
                environment_path, "retained_workspace_invalid"
            )
        else:
            output = root / "wheel"
            qualified_environment = root / "environment"
            output.mkdir()

        locked_requirements = root / "locked-requirements.txt"
        _run(
            (
                uv_executable,
                "export",
                "--no-config",
                "--frozen",
                "--all-groups",
                "--no-emit-project",
                "--format",
                "requirements.txt",
                "--output-file",
                str(locked_requirements),
            ),
            cwd=build_source,
            env=environment,
            reason="locked_dependencies_failed",
        )
        _run(
            (
                uv_executable,
                "build",
                "--no-config",
                "--build-constraints",
                str(locked_requirements),
                "--require-hashes",
                "--wheel",
                "--out-dir",
                str(output),
            ),
            cwd=build_source,
            env=environment,
            reason="wheel_build_failed",
        )
        _verify_source_checkout(build_source, expected_source_sha)
        wheels = tuple(output.glob("*.whl"))
        if len(wheels) != 1:
            raise QualificationFailure("wheel_selection_invalid")
        wheel = wheels[0].resolve()
        wheel_sha256 = _file_sha256(wheel)
        wheel_manifest = _validate_wheel(wheel, distribution)

        _run(
            (
                uv_executable,
                "venv",
                "--no-config",
                "--no-cache",
                "--python",
                python_executable,
                str(qualified_environment),
            ),
            cwd=run_directory,
            env=environment,
            reason="environment_creation_failed",
        )
        environment_python = _environment_python(qualified_environment)
        _run(
            (
                uv_executable,
                "pip",
                "install",
                "--no-config",
                "--no-cache",
                "--require-hashes",
                "--python",
                str(environment_python),
                "--requirements",
                str(locked_requirements),
            ),
            cwd=run_directory,
            env=environment,
            reason="locked_dependencies_failed",
        )
        _run(
            (
                uv_executable,
                "pip",
                "install",
                "--no-config",
                "--no-cache",
                "--no-deps",
                "--reinstall",
                "--python",
                str(environment_python),
                str(wheel),
            ),
            cwd=run_directory,
            env=environment,
            reason="wheel_install_failed",
        )
        if _file_sha256(wheel) != wheel_sha256:
            raise QualificationFailure("wheel_identity_mismatch")

        purelib_text = _decode_stdout(
            _run(
                (
                    str(environment_python),
                    "-I",
                    "-c",
                    "import sysconfig; print(sysconfig.get_paths()['purelib'])",
                ),
                cwd=run_directory,
                env=environment,
                reason="installed_environment_invalid",
            ),
            encoding="utf-8",
            reason="installed_environment_invalid",
        ).strip()
        purelib = Path(purelib_text).resolve()
        console = (
            _environment_bin(qualified_environment) / console_script
        ).resolve()
        runtime_environment = _installed_runtime_environment(
            environment,
            qualified_environment,
            python_executable,
        )
        (
            wheel_payload_sha256,
            installed_payload_sha256,
            distribution_version,
            dist_info,
        ) = _verify_installed_payload(
            wheel,
            purelib,
            distribution,
            manifest=wheel_manifest,
            console=console,
        )
        if _file_sha256(wheel) != wheel_sha256:
            raise QualificationFailure("wheel_identity_mismatch")
        _verify_installed_origin(purelib / dist_info / "direct_url.json", wheel)
        _verify_import_origin(
            environment_python,
            import_name,
            purelib,
            run_directory,
            runtime_environment,
        )

        if test_args is not None:
            _run(
                (str(environment_python), "-I", *test_args),
                cwd=build_source,
                env=runtime_environment,
                reason="installed_test_suite_failed",
            )
        if (
            not console.is_file()
            or console.is_symlink()
            or not os.access(console, os.X_OK)
        ):
            raise QualificationFailure("installed_console_invalid")
        if run_directory.resolve().is_relative_to(
            source_root
        ) or run_directory.resolve().is_relative_to(build_source):
            raise QualificationFailure("outside_checkout_invalid")
        completed = _run(
            (str(console), *console_args),
            cwd=run_directory,
            env=runtime_environment,
            reason="installed_console_failed",
        )
        console_stdout_sha256 = hashlib.sha256(completed.stdout).hexdigest()
        _verify_source_checkout(source_root, expected_source_sha)

    return ExactArtifactEvidence(
        schema="exact-artifact-evidence-v1",
        status="passed",
        source_sha=actual_sha,
        wheel_sha256=wheel_sha256,
        wheel_payload_sha256=wheel_payload_sha256,
        installed_payload_sha256=installed_payload_sha256,
        distribution=_canonical_distribution(distribution),
        distribution_version=distribution_version,
        console_script=console_script,
        console_stdout_sha256=console_stdout_sha256,
        cache_disabled=True,
        isolated_environment=True,
        explicit_wheel_install=True,
        outside_checkout=True,
        locked_dependencies=True,
        full_suite_executed=test_args == _CANONICAL_TEST_ARGS,
        wheel_record_validated=True,
        retained_wheel=retained,
        retained_environment=retained,
    )


def _qualification_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
        and not key.startswith("UV_")
    }
    launcher_bin = Path(sys.executable).parent.resolve()
    environment["PATH"] = os.pathsep.join(
        entry
        for entry in environment.get("PATH", "").split(os.pathsep)
        if entry and Path(entry).resolve() != launcher_bin
    )
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_NO_CACHE"] = "1"
    return environment


def _required_executable(name: str, environment: dict[str, str]) -> str:
    resolved = shutil.which(name, path=environment.get("PATH"))
    if resolved is None:
        raise QualificationFailure("qualification_tool_unavailable")
    executable = Path(resolved).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise QualificationFailure("qualification_tool_unavailable")
    return str(executable)


def _verify_source_checkout(source_root: Path, expected_source_sha: str) -> str:
    actual_sha = _decode_stdout(
        _run(
            ("git", "-C", str(source_root), "rev-parse", "HEAD"),
            reason="source_identity_unavailable",
        ),
        encoding="ascii",
        reason="source_identity_unavailable",
    ).strip()
    if actual_sha != expected_source_sha:
        raise QualificationFailure("source_identity_mismatch")
    status = _run(
        (
            "git",
            "-C",
            str(source_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
        reason="source_cleanliness_unavailable",
    ).stdout
    if status:
        raise QualificationFailure("source_not_clean")
    return actual_sha


def _run(
    command: tuple[str, ...],
    *,
    reason: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    deadline = time.monotonic() + _COMMAND_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
    except OSError:
        raise QualificationFailure(reason) from None

    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    if process.stdout is None or process.stderr is None:
        process.kill()
        process.wait()
        raise QualificationFailure(reason)
    readers = (
        threading.Thread(
            target=_read_process_stream,
            args=(process, process.stdout, stdout, overflow),
            daemon=True,
        ),
        threading.Thread(
            target=_read_process_stream,
            args=(process, process.stderr, stderr, overflow),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_process_tree(process)
        try:
            returncode = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            returncode = -1

    for reader in readers:
        reader.join(timeout=min(0.25, max(0.0, deadline - time.monotonic())))
    descendants_terminated = False
    if any(reader.is_alive() for reader in readers):
        descendants_terminated = _terminate_process_tree(process)
        cleanup_deadline = time.monotonic() + 1
        for reader in readers:
            reader.join(
                timeout=max(0.0, cleanup_deadline - time.monotonic())
            )
    readers_stuck = any(reader.is_alive() for reader in readers)
    if not readers_stuck:
        process.stdout.close()
        process.stderr.close()

    if (
        timed_out
        or overflow.is_set()
        or descendants_terminated
        or readers_stuck
        or returncode != 0
    ):
        raise QualificationFailure(reason)
    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
    )


def _read_process_stream(
    process: subprocess.Popen[bytes],
    stream: BinaryIO,
    output: bytearray,
    overflow: threading.Event,
) -> None:
    try:
        while chunk := stream.read(65_536):
            if len(output) + len(chunk) > _MAX_COMMAND_STREAM_BYTES:
                overflow.set()
                _terminate_process_tree(process)
                return
            output.extend(chunk)
    except OSError:
        overflow.set()
        _terminate_process_tree(process)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> bool:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
        else:
            return False
    except OSError:
        return False
    return True


def _decode_stdout(
    completed: subprocess.CompletedProcess[bytes],
    *,
    encoding: str,
    reason: str,
) -> str:
    try:
        return completed.stdout.decode(encoding, "strict")
    except UnicodeError:
        raise QualificationFailure(reason) from None


def _file_sha256(path: Path) -> str:
    try:
        size = path.stat().st_size
        if not 1 <= size <= _MAX_WHEEL_BYTES or path.is_symlink():
            raise QualificationFailure("wheel_invalid")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1_048_576):
                digest.update(chunk)
        value = digest.hexdigest()
    except OSError:
        raise QualificationFailure("wheel_invalid") from None
    if _SHA256.fullmatch(value) is None:
        raise QualificationFailure("wheel_invalid")
    return value


def _validate_wheel(wheel: Path, distribution: str) -> _WheelManifest:
    payload_digest = hashlib.sha256()
    try:
        with ZipFile(wheel) as archive:
            entries = tuple(archive.infolist())
            if not 1 <= len(entries) <= _MAX_WHEEL_MEMBERS:
                raise QualificationFailure("wheel_invalid")
            if len({entry.filename for entry in entries}) != len(entries):
                raise QualificationFailure("wheel_invalid")
            for entry in entries:
                _wheel_member_path(entry)
                if entry.flag_bits & 0x1 or (entry.is_dir() and entry.file_size != 0):
                    raise QualificationFailure("wheel_invalid")

            files = tuple(entry for entry in entries if not entry.is_dir())
            if (
                sum(entry.file_size for entry in files) > _MAX_INSTALLED_BYTES
                or any(
                    entry.file_size < 0
                    or entry.file_size > _MAX_WHEEL_MEMBER_BYTES
                    for entry in files
                )
            ):
                raise QualificationFailure("wheel_invalid")
            metadata_members = tuple(
                entry
                for entry in files
                if entry.filename.endswith(".dist-info/METADATA")
            )
            if len(metadata_members) != 1:
                raise QualificationFailure("distribution_identity_mismatch")
            metadata_member = metadata_members[0]
            if metadata_member.file_size > _MAX_METADATA_BYTES:
                raise QualificationFailure("distribution_identity_mismatch")
            metadata = BytesParser().parsebytes(
                _read_archive_member(archive, metadata_member)
            )
            if _canonical_distribution(
                metadata.get("Name", "")
            ) != _canonical_distribution(distribution):
                raise QualificationFailure("distribution_identity_mismatch")
            distribution_version = metadata.get("Version", "")
            if not distribution_version or len(distribution_version) > 128:
                raise QualificationFailure("distribution_identity_mismatch")
            dist_info = metadata_member.filename.split("/", 1)[0]
            record_name = f"{dist_info}/RECORD"
            record_members = tuple(
                entry for entry in files if entry.filename == record_name
            )
            if (
                len(record_members) != 1
                or record_members[0].file_size > _MAX_METADATA_BYTES
            ):
                raise QualificationFailure("wheel_record_invalid")
            record_rows = _parse_record(
                _read_archive_member(archive, record_members[0]),
                reason="wheel_record_invalid",
            )
            if set(record_rows) != {entry.filename for entry in files}:
                raise QualificationFailure("wheel_record_invalid")

            manifest_members: list[_WheelMember] = []
            for member in sorted(files, key=lambda value: value.filename):
                record_hash, record_size = record_rows[member.filename]
                if member.filename == record_name:
                    if record_hash or record_size:
                        raise QualificationFailure("wheel_record_invalid")
                    continue
                digest = hashlib.sha256()
                _digest_member_header(
                    payload_digest,
                    member.filename,
                    member.file_size,
                )
                with archive.open(member) as stream:
                    observed_size = 0
                    while chunk := stream.read(65_536):
                        observed_size += len(chunk)
                        if observed_size > member.file_size:
                            raise QualificationFailure("wheel_invalid")
                        digest.update(chunk)
                        payload_digest.update(chunk)
                if observed_size != member.file_size:
                    raise QualificationFailure("wheel_invalid")
                expected_hash = _record_hash(digest.digest())
                if (
                    record_hash != expected_hash
                    or record_size != str(member.file_size)
                ):
                    raise QualificationFailure("wheel_record_invalid")
                manifest_members.append(
                    _WheelMember(
                        name=member.filename,
                        size=member.file_size,
                        record_hash=expected_hash,
                    )
                )
    except QualificationFailure:
        raise
    except (
        BadZipFile,
        csv.Error,
        EOFError,
        KeyError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        raise QualificationFailure("wheel_invalid") from None
    return _WheelManifest(
        distribution_version=distribution_version,
        dist_info=dist_info,
        record_name=record_name,
        members=tuple(manifest_members),
        payload_sha256=payload_digest.hexdigest(),
    )


def _verify_installed_payload(
    wheel: Path,
    purelib: Path,
    distribution: str,
    *,
    manifest: _WheelManifest | None = None,
    console: Path | None = None,
) -> tuple[str, str, str, str]:
    if manifest is None:
        manifest = _validate_wheel(wheel, distribution)
    installed_digest = hashlib.sha256()
    try:
        with ZipFile(wheel) as archive:
            archive_members = {
                member.filename: member
                for member in archive.infolist()
                if not member.is_dir()
            }
            for expected in manifest.members:
                member = archive_members.get(expected.name)
                if member is None or member.file_size != expected.size:
                    raise QualificationFailure("wheel_identity_mismatch")
                relative = _wheel_member_path(member)
                installed = purelib.joinpath(*relative.parts)
                if (
                    not installed.is_file()
                    or installed.is_symlink()
                    or not installed.resolve().is_relative_to(purelib)
                    or installed.stat().st_size != member.file_size
                ):
                    raise QualificationFailure("installed_payload_mismatch")
                _digest_member_header(
                    installed_digest,
                    member.filename,
                    member.file_size,
                )
                with archive.open(member) as wheel_stream, installed.open(
                    "rb"
                ) as installed_stream:
                    while True:
                        wheel_chunk = wheel_stream.read(65_536)
                        installed_chunk = installed_stream.read(65_536)
                        if wheel_chunk != installed_chunk:
                            raise QualificationFailure("installed_payload_mismatch")
                        if not wheel_chunk:
                            break
                        installed_digest.update(installed_chunk)
        if installed_digest.hexdigest() != manifest.payload_sha256:
            raise QualificationFailure("installed_payload_mismatch")
        _verify_installed_record(purelib, manifest, console)
    except QualificationFailure:
        raise
    except (
        BadZipFile,
        EOFError,
        KeyError,
        OSError,
        RuntimeError,
        UnicodeError,
        ValueError,
    ):
        raise QualificationFailure("wheel_invalid") from None
    return (
        manifest.payload_sha256,
        installed_digest.hexdigest(),
        manifest.distribution_version,
        manifest.dist_info,
    )


def _verify_installed_record(
    purelib: Path,
    manifest: _WheelManifest,
    console: Path | None,
) -> None:
    record_path = purelib / manifest.record_name
    rows = _parse_record(
        _read_bounded_file(record_path, _MAX_METADATA_BYTES),
        reason="installed_record_invalid",
    )
    expected = {
        member.name: (member.record_hash, str(member.size))
        for member in manifest.members
    }
    expected[manifest.record_name] = ("", "")
    if any(rows.get(name) != value for name, value in expected.items()):
        raise QualificationFailure("installed_record_payload_invalid")

    generated = set(rows) - set(expected)
    allowed = {
        f"{manifest.dist_info}/INSTALLER",
        f"{manifest.dist_info}/REQUESTED",
        f"{manifest.dist_info}/direct_url.json",
        f"{manifest.dist_info}/uv_cache.json",
    }
    installer_record = f"{manifest.dist_info}/INSTALLER"
    origin_record = f"{manifest.dist_info}/direct_url.json"
    console_record = None
    if console is not None:
        console_record = os.path.relpath(console, purelib).replace(os.sep, "/")
        allowed.add(console_record)
    if installer_record not in generated:
        raise QualificationFailure("installed_record_installer_missing")
    if origin_record not in generated:
        raise QualificationFailure("installed_record_origin_missing")
    if console_record is not None and console_record not in generated:
        raise QualificationFailure("installed_record_console_missing")
    if not generated.issubset(allowed):
        raise QualificationFailure("installed_record_generated_unexpected")

    for name in generated:
        target = (
            console
            if name == console_record
            else purelib.joinpath(*PurePosixPath(name).parts)
        )
        if target is None or not target.is_file() or target.is_symlink():
            raise QualificationFailure("installed_record_generated_invalid")
        if name != console_record and not target.resolve().is_relative_to(purelib):
            raise QualificationFailure("installed_record_generated_invalid")
        digest, size = _file_record_identity(target)
        if rows[name] != (digest, str(size)):
            raise QualificationFailure("installed_record_generated_invalid")


def _parse_record(
    content: bytes,
    *,
    reason: str,
) -> dict[str, tuple[str, str]]:
    try:
        text = content.decode("utf-8", "strict")
        parsed = tuple(csv.reader(io.StringIO(text, newline=""), strict=True))
    except (csv.Error, UnicodeError):
        raise QualificationFailure(reason) from None
    rows: dict[str, tuple[str, str]] = {}
    for row in parsed:
        if len(row) != 3 or not row[0] or row[0] in rows:
            raise QualificationFailure(reason)
        rows[row[0]] = (row[1], row[2])
    if not rows:
        raise QualificationFailure(reason)
    return rows


def _record_hash(digest: bytes) -> str:
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def _file_record_identity(path: Path) -> tuple[str, int]:
    try:
        size = path.stat().st_size
        if size < 0 or size > _MAX_WHEEL_MEMBER_BYTES:
            raise QualificationFailure("installed_record_invalid")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(65_536):
                digest.update(chunk)
    except OSError:
        raise QualificationFailure("installed_record_invalid") from None
    return _record_hash(digest.digest()), size


def _wheel_member_path(member: ZipInfo) -> PurePosixPath:
    path = PurePosixPath(member.filename)
    mode = (member.external_attr >> 16) & 0o170000
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or "\\" in member.filename
        or any(ord(character) < 32 or ord(character) == 127 for character in member.filename)
        or mode == stat.S_IFLNK
    ):
        raise QualificationFailure("wheel_invalid")
    return path


def _read_archive_member(archive: ZipFile, member: ZipInfo) -> bytes:
    content = bytearray()
    with archive.open(member) as stream:
        while chunk := stream.read(65_536):
            if len(content) + len(chunk) > member.file_size:
                raise QualificationFailure("wheel_invalid")
            content.extend(chunk)
    if len(content) != member.file_size:
        raise QualificationFailure("wheel_invalid")
    return bytes(content)


def _read_bounded_file(path: Path, limit: int) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise QualificationFailure("installed_record_invalid")
        content = bytearray()
        with path.open("rb") as stream:
            while chunk := stream.read(65_536):
                if len(content) + len(chunk) > limit:
                    raise QualificationFailure("installed_record_invalid")
                content.extend(chunk)
    except OSError:
        raise QualificationFailure("installed_record_invalid") from None
    return bytes(content)


def _digest_member_header(digest: _Digest, name: str, size: int) -> None:
    encoded = name.encode("utf-8", "strict")
    digest.update(len(encoded).to_bytes(4, "big"))
    digest.update(encoded)
    digest.update(size.to_bytes(8, "big"))


def _verify_installed_origin(path: Path, wheel: Path) -> None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        raise QualificationFailure("installed_origin_mismatch") from None
    if (
        type(value) is not dict
        or set(value) != {"archive_info", "url"}
        or value.get("url") != wheel.as_uri()
        or type(value.get("archive_info")) is not dict
    ):
        raise QualificationFailure("installed_origin_mismatch")


def _verify_import_origin(
    python: Path,
    import_name: str,
    purelib: Path,
    cwd: Path,
    environment: dict[str, str],
) -> None:
    statement = (
        "import importlib.util; "
        f"spec=importlib.util.find_spec({import_name!r}); "
        "print('' if spec is None or spec.origin is None else spec.origin)"
    )
    origin = _decode_stdout(
        _run(
            (str(python), "-I", "-c", statement),
            cwd=cwd,
            env=environment,
            reason="installed_import_invalid",
        ),
        encoding="utf-8",
        reason="installed_import_invalid",
    ).strip()
    if not origin:
        raise QualificationFailure("installed_import_invalid")
    try:
        resolved = Path(origin).resolve(strict=True)
    except OSError:
        raise QualificationFailure("installed_import_invalid") from None
    if not resolved.is_file() or not resolved.is_relative_to(purelib):
        raise QualificationFailure("installed_import_invalid")


def _environment_bin(environment: Path) -> Path:
    return environment / ("Scripts" if os.name == "nt" else "bin")


def _environment_python(environment: Path) -> Path:
    return _environment_bin(environment) / ("python.exe" if os.name == "nt" else "python")


def _installed_runtime_environment(
    environment: dict[str, str],
    qualified_environment: Path,
    launcher_python: str,
) -> dict[str, str]:
    value = dict(environment)
    value.pop("UV_NO_CACHE", None)
    qualified_bin = _environment_bin(qualified_environment).resolve()
    launcher_bin = Path(launcher_python).parent.resolve()
    path_entries = []
    for entry in environment.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except OSError:
            continue
        if resolved not in {qualified_bin, launcher_bin}:
            path_entries.append(entry)
    value["PATH"] = os.pathsep.join((str(qualified_bin), *path_entries))
    return value


def _new_directory(path: Path | None, reason: str) -> Path:
    value = _new_path(path, reason)
    try:
        value.mkdir()
    except OSError:
        raise QualificationFailure(reason) from None
    return value


def _new_path(path: Path | None, reason: str) -> Path:
    if path is None:
        raise QualificationFailure(reason)
    try:
        value = path.resolve()
        if value.exists() or not value.parent.is_dir() or value.parent.is_symlink():
            raise QualificationFailure(reason)
    except OSError:
        raise QualificationFailure(reason) from None
    return value


def _canonical_distribution(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Qualify the exact installed Mechanical Librarian wheel."
    )
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--wheel-output", type=Path)
    parser.add_argument("--environment", type=Path)
    arguments = parser.parse_args(argv)
    try:
        evidence = qualify_exact_artifact(
            Path(__file__).resolve().parents[1],
            arguments.source_sha,
            wheel_output=arguments.wheel_output,
            environment_path=arguments.environment,
        )
    except QualificationFailure as error:
        print(
            json.dumps(
                {
                    "reason": error.reason,
                    "schema": "exact-artifact-evidence-v1",
                    "status": "failed",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(evidence.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
