#!/usr/bin/env python3
"""Repeatable exact-head qualification for the supported container lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

from argon2 import PasswordHasher


ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_LABEL = "org.mechanical-librarian.qualification-run"
GRACEFUL_SHUTDOWN_SECONDS = 5


class QualificationError(RuntimeError):
    pass


def _run(arguments: list[str], *, capture: bool = True) -> str:
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if result.returncode != 0:
        raise QualificationError("container_qualification_failed")
    return "" if result.stdout is None else result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot(source_sha: str, destination: Path) -> None:
    completed = subprocess.run(
        ["git", "archive", "--format=tar", source_sha],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise QualificationError("source_snapshot_failed")
    try:
        with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
            archive.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as error:
        raise QualificationError("source_snapshot_failed") from error


def _git_blob(source_sha: str, path: str) -> bytes:
    completed = subprocess.run(
        ["git", "show", f"{source_sha}:{path}"], cwd=ROOT, check=False,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0 or len(completed.stdout) > 1_048_576:
        raise QualificationError("source_snapshot_failed")
    return completed.stdout


def _owned(kind: str, name: str, run_id: str) -> bool:
    command = {
        "container": ["docker", "inspect", name, "--format",
                      '{{ index .Config.Labels "' + OWNERSHIP_LABEL + '" }}'],
        "volume": ["docker", "volume", "inspect", name, "--format",
                   '{{ index .Labels "' + OWNERSHIP_LABEL + '" }}'],
        "image": ["docker", "image", "inspect", name, "--format",
                  '{{ index .Config.Labels "' + OWNERSHIP_LABEL + '" }}'],
    }[kind]
    result = subprocess.run(
        command, cwd=ROOT, check=False, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == run_id


def _remove_owned(kind: str, name: str, run_id: str) -> None:
    if not _owned(kind, name, run_id):
        return
    command = {
        "container": ["docker", "rm", "-f", name],
        "volume": ["docker", "volume", "rm", name],
        "image": ["docker", "image", "rm", name],
    }[kind]
    subprocess.run(
        command, cwd=ROOT, check=False, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _health(port: int, route: str, deadline: float) -> bool:
    context = ssl._create_unverified_context()
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"https://127.0.0.1:{port}{route}",
                context=context,
                timeout=1,
            ) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def _clean_stop(container: str) -> int:
    started = time.monotonic()
    _run([
        "docker", "stop", "--time", str(GRACEFUL_SHUTDOWN_SECONDS), container
    ])
    shutdown_ms = int((time.monotonic() - started) * 1000)
    state = json.loads(_run([
        "docker", "inspect", container, "--format", "{{json .State}}"
    ]))
    if (
        shutdown_ms > GRACEFUL_SHUTDOWN_SECONDS * 1_000
        or state.get("Running") is not False
        or state.get("ExitCode") != 0
        or state.get("OOMKilled") is not False
        or state.get("Dead") is not False
        or state.get("Error") != ""
    ):
        raise QualificationError("container_graceful_shutdown_failed")
    return shutdown_ms


def _configuration() -> dict[str, object]:
    return {
        "schema_version": "config-v5",
        "connections": [{
            "name": "primary",
            "adapter": "audiobookshelf",
            "endpoint": "https://127.0.0.1:9",
            "secret_file": "/data/target.secret",
        }],
        "libraries": [{
            "name": "books",
            "connection": "primary",
            "library_id": "01234567-89ab-4def-8123-456789abcdef",
            "mount": "books",
            "provider_connections": [],
        }],
        "mounts": [{
            "name": "books",
            "container_path": "/media/books",
            "service_path": "/library/books",
            "read_only": True,
        }],
        "providers": [],
        "review": {
            "canonical_origin": "https://review.test",
            "owner_password_hash_file": "/data/owner.argon2",
            "tls_certificate_file": "/data/tls.crt",
            "tls_private_key_file": "/data/tls.key",
            "session_lifetime_seconds": 600,
            "library_polling_interval_seconds": 60,
            "graceful_shutdown_seconds": GRACEFUL_SHUTDOWN_SECONDS,
            "diagnostic_timings_enabled": True,
            "trusted_proxy_ips": [],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    arguments = parser.parse_args()
    source_sha = _run(["git", "rev-parse", "HEAD"])
    if source_sha != arguments.source_sha or len(source_sha) != 40:
        raise QualificationError("source_head_mismatch")
    if _run(["git", "status", "--porcelain"]):
        raise QualificationError("source_not_clean")
    if shutil.which("docker") is None or shutil.which("openssl") is None:
        raise QualificationError("qualification_dependency_unavailable")

    script_digest = _sha256(Path(__file__))
    run_id = uuid.uuid4().hex
    suffix = f"{source_sha[:12]}-{run_id[:12]}"
    image = f"mechanical-librarian-qualification:{suffix}"
    volume = f"mechanical-librarian-qualification-{suffix}"
    container = f"mechanical-librarian-qualification-{suffix}"
    seed_container = f"{container}-seed"
    probe_container = f"{container}-probe"
    try:
        with tempfile.TemporaryDirectory(
            prefix="mechanical-librarian-container-source-"
        ) as temporary:
            snapshot = Path(temporary)
            _snapshot(source_sha, snapshot)
            _run([
                "docker", "build", "--pull=false",
                "--label", f"org.opencontainers.image.revision={source_sha}",
                "--label", f"{OWNERSHIP_LABEL}={run_id}",
                "-t", image, str(snapshot),
            ], capture=False)
        if not _owned("image", image, run_id):
            raise QualificationError("image_ownership_failed")
        image_id = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
        _run([
            "docker", "run", "--rm", "--user", "65532:65532", "--read-only",
            "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--entrypoint", "/opt/mechanical-librarian/bin/python", image,
            "-I", "-c",
            "import importlib.util; raise SystemExit(0 if "
            "importlib.util.find_spec('mechanical_librarian_workbench') is None else 1)",
        ])
        _run(["docker", "volume", "create", "--label",
              f"{OWNERSHIP_LABEL}={run_id}", volume])
        if not _owned("volume", volume, run_id):
            raise QualificationError("volume_ownership_failed")
        with tempfile.TemporaryDirectory() as temporary:
            seed = Path(temporary)
            (seed / "config.json").write_text(
                json.dumps(_configuration(), sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            (seed / "owner.argon2").write_text(
                PasswordHasher().hash("synthetic qualification owner"), encoding="utf-8"
            )
            (seed / "target.secret").write_text("synthetic-target-token", encoding="ascii")
            (seed / "guarded-checkpoint-probe.py").write_bytes(
                _git_blob(source_sha, "scripts/guarded_checkpoint_probe.py")
            )
            _run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(seed / "tls.key"), "-out", str(seed / "tls.crt"),
                "-days", "1", "-subj", "/CN=review.test",
            ])
            _run([
                "docker", "run", "--rm", "--name", seed_container,
                "--label", f"{OWNERSHIP_LABEL}={run_id}", "--user", "0:0",
                "--mount", f"type=volume,src={volume},dst=/data",
                "--mount", f"type=bind,src={seed},dst=/seed,readonly",
                "--entrypoint", "/bin/sh", image, "-c",
                "cp /seed/config.json /seed/owner.argon2 /seed/target.secret "
                "/seed/guarded-checkpoint-probe.py "
                "/seed/tls.crt /seed/tls.key /data/ && "
                "chown -R 65532:65532 /data && chmod 700 /data && chmod 600 /data/*",
            ])

        probe_command = [
            "docker", "run", "--rm", "--name", probe_container,
            "--label", f"{OWNERSHIP_LABEL}={run_id}",
            "--user", "65532:65532", "--read-only", "--network", "none",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--mount", f"type=volume,src={volume},dst=/data",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=65532,gid=65532",
            "--entrypoint", "/opt/mechanical-librarian/bin/python", image,
            "-I", "/data/guarded-checkpoint-probe.py",
        ]
        _run([*probe_command, "prepare"])
        checkpoint_evidence = json.loads(_run([*probe_command, "recover"]))
        if checkpoint_evidence.get("status") != "passed":
            raise QualificationError("guarded_checkpoint_restart_failed")

        container_id = _run([
            "docker", "create", "--name", container, "--label",
            f"{OWNERSHIP_LABEL}={run_id}", "--user", "65532:65532",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--mount", f"type=volume,src={volume},dst=/data",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=0700,uid=65532,gid=65532",
            "-p", "127.0.0.1::8443", image,
        ])
        if not _owned("container", container, run_id):
            raise QualificationError("container_ownership_failed")
        _run(["docker", "start", container])
        port = int(_run([
            "docker", "port", container, "8443/tcp"
        ]).rsplit(":", 1)[1])
        if not _health(port, "/health/live", time.monotonic() + 20):
            raise QualificationError("container_liveness_failed")
        if not _health(port, "/health/ready", time.monotonic() + 20):
            raise QualificationError("container_readiness_failed")
        inspection = json.loads(_run(["docker", "inspect", container]))[0]
        if (
            inspection["Config"]["User"] != "65532:65532"
            or inspection["HostConfig"]["ReadonlyRootfs"] is not True
            or inspection["HostConfig"]["CapDrop"] != ["ALL"]
            or len(inspection["Mounts"]) != 1
        ):
            raise QualificationError("container_shape_failed")

        shutdown_ms = _clean_stop(container)
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.sqlite3"
            second = Path(temporary) / "second.sqlite3"
            _run(["docker", "cp", f"{container}:/data/application.sqlite3", str(first)])
            first_hash = _sha256(first)
            _run(["docker", "start", container])
            port = int(_run([
                "docker", "port", container, "8443/tcp"
            ]).rsplit(":", 1)[1])
            if not _health(port, "/health/ready", time.monotonic() + 20):
                raise QualificationError("container_restart_failed")
            shutdown_ms = max(shutdown_ms, _clean_stop(container))
            _run(["docker", "cp", f"{container}:/data/application.sqlite3", str(second)])
            second_hash = _sha256(second)
        if first_hash != second_hash:
            raise QualificationError("container_restart_state_changed")
        if (
            _run(["git", "rev-parse", "HEAD"]) != source_sha
            or _run(["git", "status", "--porcelain"])
            or _sha256(Path(__file__)) != script_digest
        ):
            raise QualificationError("source_changed_during_qualification")
        print(json.dumps({
            "application_sha256": first_hash,
            "container_id": container_id,
            "exact_current_restart": True,
            "graceful_shutdown_ms": shutdown_ms,
            "graceful_shutdown_seconds": GRACEFUL_SHUTDOWN_SECONDS,
            "graceful_terminal_state": True,
            "guarded_checkpoint_restart": True,
            "image_id": image_id,
            "liveness": True,
            "non_root": True,
            "read_only_root": True,
            "readiness": True,
            "schema": "container-lifecycle-evidence-v2",
            "snapshot_build": True,
            "workbench_excluded": True,
            "single_data_volume": True,
            "source_sha": source_sha,
            "status": "passed",
        }, sort_keys=True, separators=(",", ":")))
    finally:
        _remove_owned("container", probe_container, run_id)
        _remove_owned("container", seed_container, run_id)
        _remove_owned("container", container, run_id)
        _remove_owned("volume", volume, run_id)
        _remove_owned("image", image, run_id)


if __name__ == "__main__":
    main()
