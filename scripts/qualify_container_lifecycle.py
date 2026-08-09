#!/usr/bin/env python3
"""Repeatable exact-head qualification for the supported container lifecycle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from argon2 import PasswordHasher


ROOT = Path(__file__).resolve().parents[1]


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
            "graceful_shutdown_seconds": 5,
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

    suffix = source_sha[:12]
    image = f"mechanical-librarian-qualification:{suffix}"
    volume = f"mechanical-librarian-qualification-{suffix}"
    container = f"mechanical-librarian-qualification-{suffix}"
    created_image = created_volume = created_container = False
    try:
        _run([
            "docker", "build", "--pull=false",
            "--label", f"org.opencontainers.image.revision={source_sha}",
            "-t", image, ".",
        ], capture=False)
        created_image = True
        image_id = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
        _run(["docker", "volume", "create", volume])
        created_volume = True
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
            _run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(seed / "tls.key"), "-out", str(seed / "tls.crt"),
                "-days", "1", "-subj", "/CN=review.test",
            ])
            _run([
                "docker", "run", "--rm", "--user", "0:0",
                "--mount", f"type=volume,src={volume},dst=/data",
                "--mount", f"type=bind,src={seed},dst=/seed,readonly",
                "--entrypoint", "/bin/sh", image, "-c",
                "cp /seed/config.json /seed/owner.argon2 /seed/target.secret "
                "/seed/tls.crt /seed/tls.key /data/ && "
                "chown -R 65532:65532 /data && chmod 700 /data && chmod 600 /data/*",
            ])

        container_id = _run([
            "docker", "create", "--name", container, "--user", "65532:65532",
            "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "--mount", f"type=volume,src={volume},dst=/data",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=0700,uid=65532,gid=65532",
            "-p", "127.0.0.1::8443", image,
        ])
        created_container = True
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

        started = time.monotonic()
        _run(["docker", "stop", "--time", "8", container])
        shutdown_ms = int((time.monotonic() - started) * 1000)
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
            _run(["docker", "stop", "--time", "8", container])
            _run(["docker", "cp", f"{container}:/data/application.sqlite3", str(second)])
            second_hash = _sha256(second)
        if first_hash != second_hash:
            raise QualificationError("container_restart_state_changed")
        print(json.dumps({
            "application_sha256": first_hash,
            "container_id": container_id,
            "exact_current_restart": True,
            "graceful_shutdown_ms": shutdown_ms,
            "image_id": image_id,
            "liveness": True,
            "non_root": True,
            "read_only_root": True,
            "readiness": True,
            "schema": "container-lifecycle-evidence-v1",
            "single_data_volume": True,
            "source_sha": source_sha,
            "status": "passed",
        }, sort_keys=True, separators=(",", ":")))
    finally:
        if created_container:
            subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
        if created_volume:
            subprocess.run(["docker", "volume", "rm", volume], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
        if created_image:
            subprocess.run(["docker", "image", "rm", image], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
