#!/usr/bin/env python3
"""Build, install, and exercise one source-bound wheel without uv cache reuse."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Protocol
from zipfile import BadZipFile, ZipFile, ZipInfo


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_WHEEL_BYTES = 67_108_864
_MAX_WHEEL_MEMBERS = 4_096
_MAX_INSTALLED_BYTES = 134_217_728
_COMMAND_TIMEOUT_SECONDS = 180


class QualificationFailure(RuntimeError):
    """A fixed, path-free exact-artifact qualification failure."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _Digest(Protocol):
    def update(self, data: bytes) -> object: ...


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

    actual_root = _run(
        ("git", "-C", str(source_root), "rev-parse", "--show-toplevel"),
        reason="source_repository_invalid",
    ).stdout.decode("utf-8", "strict").strip()
    if Path(actual_root).resolve() != source_root:
        raise QualificationFailure("source_repository_invalid")
    actual_sha = _run(
        ("git", "-C", str(source_root), "rev-parse", "HEAD"),
        reason="source_identity_unavailable",
    ).stdout.decode("ascii", "strict").strip()
    if actual_sha != expected_source_sha:
        raise QualificationFailure("source_identity_mismatch")
    if _run(
        (
            "git",
            "-C",
            str(source_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
        reason="source_cleanliness_unavailable",
    ).stdout:
        raise QualificationFailure("source_not_clean")

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_NO_CACHE"] = "1"
    if (wheel_output is None) != (environment_path is None):
        raise QualificationFailure("retained_workspace_invalid")

    with tempfile.TemporaryDirectory(
        prefix="mechanical-librarian-exact-artifact-"
    ) as temporary:
        root = Path(temporary)
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

        _run(
            (
                "uv",
                "build",
                "--wheel",
                "--out-dir",
                str(output),
            ),
            cwd=source_root,
            env=environment,
            reason="wheel_build_failed",
        )
        wheels = tuple(output.glob("*.whl"))
        if len(wheels) != 1:
            raise QualificationFailure("wheel_selection_invalid")
        wheel = wheels[0].resolve()
        wheel_sha256 = _file_sha256(wheel)

        _run(
            (
                "uv",
                "venv",
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
                "uv",
                "pip",
                "install",
                "--no-cache",
                "--reinstall",
                "--python",
                str(environment_python),
                str(wheel),
            ),
            cwd=run_directory,
            env=environment,
            reason="wheel_install_failed",
        )

        purelib_text = _run(
            (
                str(environment_python),
                "-I",
                "-c",
                "import sysconfig; print(sysconfig.get_paths()['purelib'])",
            ),
            cwd=run_directory,
            env=environment,
            reason="installed_environment_invalid",
        ).stdout.decode("utf-8", "strict").strip()
        purelib = Path(purelib_text).resolve()
        (
            wheel_payload_sha256,
            installed_payload_sha256,
            distribution_version,
            dist_info,
        ) = _verify_installed_payload(wheel, purelib, distribution)
        _verify_installed_origin(purelib / dist_info / "direct_url.json", wheel)
        _verify_import_origin(
            environment_python,
            import_name,
            purelib,
            run_directory,
            environment,
        )

        console = _environment_bin(qualified_environment) / console_script
        if (
            not console.is_file()
            or console.is_symlink()
            or not os.access(console, os.X_OK)
        ):
            raise QualificationFailure("installed_console_invalid")
        if run_directory.resolve().is_relative_to(source_root):
            raise QualificationFailure("outside_checkout_invalid")
        completed = _run(
            (str(console), *console_args),
            cwd=run_directory,
            env=environment,
            reason="installed_console_failed",
        )
        console_stdout_sha256 = hashlib.sha256(completed.stdout).hexdigest()

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
        retained_wheel=retained,
        retained_environment=retained,
    )


def _run(
    command: tuple[str, ...],
    *,
    reason: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise QualificationFailure(reason) from None
    if completed.returncode != 0:
        raise QualificationFailure(reason)
    return completed


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


def _verify_installed_payload(
    wheel: Path,
    purelib: Path,
    distribution: str,
) -> tuple[str, str, str, str]:
    wheel_digest = hashlib.sha256()
    installed_digest = hashlib.sha256()
    total = 0
    try:
        with ZipFile(wheel) as archive:
            members = tuple(
                member
                for member in archive.infolist()
                if not member.is_dir() and not member.filename.endswith("/RECORD")
            )
            if not 1 <= len(members) <= _MAX_WHEEL_MEMBERS:
                raise QualificationFailure("wheel_invalid")
            metadata_members = tuple(
                member
                for member in members
                if member.filename.endswith(".dist-info/METADATA")
            )
            if len(metadata_members) != 1:
                raise QualificationFailure("distribution_identity_mismatch")
            metadata = BytesParser().parsebytes(archive.read(metadata_members[0]))
            if _canonical_distribution(metadata.get("Name", "")) != _canonical_distribution(
                distribution
            ):
                raise QualificationFailure("distribution_identity_mismatch")
            distribution_version = metadata.get("Version", "")
            if not distribution_version or len(distribution_version) > 128:
                raise QualificationFailure("distribution_identity_mismatch")
            dist_info = metadata_members[0].filename.split("/", 1)[0]

            for member in sorted(members, key=lambda value: value.filename):
                relative = _wheel_member_path(member)
                wheel_bytes = archive.read(member)
                total += len(wheel_bytes)
                if total > _MAX_INSTALLED_BYTES:
                    raise QualificationFailure("wheel_invalid")
                installed = purelib.joinpath(*relative.parts)
                if (
                    not installed.is_file()
                    or installed.is_symlink()
                    or not installed.resolve().is_relative_to(purelib)
                ):
                    raise QualificationFailure("installed_payload_mismatch")
                installed_bytes = installed.read_bytes()
                if installed_bytes != wheel_bytes:
                    raise QualificationFailure("installed_payload_mismatch")
                _digest_member(wheel_digest, member.filename, wheel_bytes)
                _digest_member(installed_digest, member.filename, installed_bytes)
    except QualificationFailure:
        raise
    except (BadZipFile, KeyError, OSError, ValueError):
        raise QualificationFailure("wheel_invalid") from None
    return (
        wheel_digest.hexdigest(),
        installed_digest.hexdigest(),
        distribution_version,
        dist_info,
    )


def _wheel_member_path(member: ZipInfo) -> PurePosixPath:
    path = PurePosixPath(member.filename)
    mode = (member.external_attr >> 16) & 0o170000
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or mode == stat.S_IFLNK
    ):
        raise QualificationFailure("wheel_invalid")
    return path


def _digest_member(digest: _Digest, name: str, content: bytes) -> None:
    encoded = name.encode("utf-8", "strict")
    digest.update(len(encoded).to_bytes(4, "big"))
    digest.update(encoded)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)


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
    origin = _run(
        (str(python), "-I", "-c", statement),
        cwd=cwd,
        env=environment,
        reason="installed_import_invalid",
    ).stdout.decode("utf-8", "strict").strip()
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
