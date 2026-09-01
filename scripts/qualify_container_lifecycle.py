#!/usr/bin/env python3
"""Repeatable exact-head qualification for the supported container lifecycle.

The unchanged file-managed sentinel and exact-empty one-listener journey run
against the same exact image.
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import io
import json
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from argon2 import PasswordHasher

ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_LABEL = "org.mechanical-librarian.qualification-run"
GRACEFUL_SHUTDOWN_SECONDS = 5
_FAKE_AUDIOBOOKSHELF = r"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

library = {
    "id": "books",
    "name": "Synthetic Books & Essays",
    "mediaType": "book",
    "folders": [{"fullPath": "/library/books"}],
}
summary = {"id": "item-1", "libraryId": "books", "updatedAt": 1}
detail = {
    **summary,
    "isInvalid": False,
    "isMissing": False,
    "mediaType": "book",
    "media": {
        "metadata": {"title": "Synthetic Book", "authors": [{"name": "Test Author"}]},
        "ebookFile": {
            "ebookFormat": "epub",
            "ino": "synthetic-inode",
            "metadata": {"relPath": "item-1.epub", "size": 1, "mtimeMs": 1},
        },
    },
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer synthetic-target-token":
            self.send_response(403)
            self.end_headers()
            return
        route = urlsplit(self.path).path
        if route == "/api/libraries":
            value = {"libraries": [library]}
        elif route == "/api/libraries/books/items":
            value = {"total": 1, "results": [summary]}
        elif route == "/api/items/item-1":
            value = detail
        else:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass

ThreadingHTTPServer(("0.0.0.0", 18080), Handler).serve_forever()
"""


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
        ["git", "show", f"{source_sha}:{path}"],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0 or len(completed.stdout) > 1_048_576:
        raise QualificationError("source_snapshot_failed")
    return completed.stdout


def _owned(kind: str, name: str, run_id: str) -> bool:
    command = {
        "container": [
            "docker",
            "inspect",
            name,
            "--format",
            '{{ index .Config.Labels "' + OWNERSHIP_LABEL + '" }}',
        ],
        "volume": [
            "docker",
            "volume",
            "inspect",
            name,
            "--format",
            '{{ index .Labels "' + OWNERSHIP_LABEL + '" }}',
        ],
        "image": [
            "docker",
            "image",
            "inspect",
            name,
            "--format",
            '{{ index .Config.Labels "' + OWNERSHIP_LABEL + '" }}',
        ],
    }[kind]
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
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
        command,
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _verified_context(certificate_pem: str) -> ssl.SSLContext:
    """A client context pinned to the synthetic certificate this run seeded.

    `verify_mode` stays `CERT_REQUIRED` and that certificate is the only trust
    anchor loaded, so the chain must terminate in exactly it. Hostname checking
    is the single part that cannot apply: the qualifier reaches the container
    through a published loopback mapping, by address and on a port `docker
    port` only reveals after start, while the certificate carries
    `DNS:review.test`. Pinning is what carries the assurance instead, and it is
    a real one -- the unverified context this replaced accepted any certificate
    at all, including one the run had not issued.

    The certificate arrives as PEM text rather than a path because the seed
    directory holding it is deleted before the qualified container starts.
    """

    context = ssl.create_default_context(cadata=certificate_pem)
    context.check_hostname = False
    return context


def _health(port: int, route: str, deadline: float, context: ssl.SSLContext) -> bool:
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


def _declared_health(container: str, deadline: float) -> str:
    """The container's own `HEALTHCHECK` verdict, waited out to `deadline`.

    `docker inspect` has no `.State.Health` at all on an image that declares no
    healthcheck, so the template below fails the run rather than reporting a
    blank -- the declaration is part of the contract being qualified.
    """

    status = "starting"
    while time.monotonic() < deadline:
        status = _run(
            ["docker", "inspect", container, "--format", "{{.State.Health.Status}}"]
        )
        if status != "starting":
            return status
        time.sleep(1)
    return status


def _workspace(port: int, context: ssl.SSLContext) -> bool:
    """Exercise authenticated direct entry and one manifest-admitted asset."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    common = {"Host": "review.test", "Origin": "https://review.test"}
    try:
        login = urllib.request.Request(
            f"https://127.0.0.1:{port}/api/v1/session/login",
            data=json.dumps({"password": "synthetic qualification owner"}).encode(),
            headers={**common, "Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(login, timeout=3) as response:
            if response.status != 200:
                return False
        page_request = urllib.request.Request(
            f"https://127.0.0.1:{port}/library",
            headers={"Host": "review.test"},
        )
        with opener.open(page_request, timeout=3) as response:
            body = response.read(1_048_577)
            if (
                response.status != 200
                or response.headers.get("Cache-Control") != "no-store, private"
                or len(body) > 1_048_576
            ):
                return False
        text = body.decode("utf-8", "strict")
        if re.search(r'data-csrf-token="[A-Za-z0-9_-]{32,128}"', text) is None:
            return False
        if re.search(r'data-build-identity="[0-9a-f]{64}"', text) is None:
            return False
        script = re.search(r'<script[^>]+src="(/assets/review-app/[^"]+\.js)"', text)
        if script is None:
            return False
        asset_request = urllib.request.Request(
            f"https://127.0.0.1:{port}{script.group(1)}",
            headers={"Host": "review.test"},
        )
        with opener.open(asset_request, timeout=3) as response:
            payload = response.read(4_194_305)
            return (
                response.status == 200
                and "javascript" in response.headers.get("Content-Type", "")
                and 0 < len(payload) <= 4_194_304
            )
    except (OSError, UnicodeError, urllib.error.URLError, ValueError):
        return False


def _clean_stop(container: str) -> int:
    started = time.monotonic()
    _run(["docker", "stop", "--time", str(GRACEFUL_SHUTDOWN_SECONDS), container])
    shutdown_ms = int((time.monotonic() - started) * 1000)
    state = json.loads(
        _run(["docker", "inspect", container, "--format", "{{json .State}}"])
    )
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


def _json_request(
    opener: urllib.request.OpenerDirector,
    port: int,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, object]:
    request = urllib.request.Request(
        f"https://127.0.0.1:{port}{path}",
        data=(
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ),
        headers={
            "Host": "review.test",
            **({"Content-Type": "application/json"} if payload is not None else {}),
            **(headers or {}),
        },
        method=method,
    )
    try:
        with opener.open(request, timeout=5) as response:
            body = response.read(1_048_577)
            if len(body) > 1_048_576:
                raise QualificationError("first_run_response_oversize")
            return response.status, json.loads(body.decode("utf-8", "strict"))
    except urllib.error.HTTPError as error:
        body = error.read(1_048_577)
        if len(body) > 1_048_576:
            raise QualificationError("first_run_response_oversize") from error
        return error.code, json.loads(body.decode("utf-8", "strict"))


def _qualify_first_run(
    image: str,
    run_id: str,
    suffix: str,
) -> dict[str, object]:
    data_volume = f"mechanical-librarian-first-run-data-{suffix}"
    secrets_volume = f"mechanical-librarian-first-run-secrets-{suffix}"
    media_volume = f"mechanical-librarian-first-run-media-{suffix}"
    seed = f"mechanical-librarian-first-run-seed-{suffix}"
    target = f"mechanical-librarian-first-run-target-{suffix}"
    review = f"mechanical-librarian-first-run-review-{suffix}"
    try:
        for volume in (data_volume, secrets_volume, media_volume):
            _run(
                [
                    "docker",
                    "volume",
                    "create",
                    "--label",
                    f"{OWNERSHIP_LABEL}={run_id}",
                    volume,
                ]
            )
            if not _owned("volume", volume, run_id):
                raise QualificationError("first_run_volume_ownership_failed")
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)
            _run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(source / "tls.key"),
                    "-out",
                    str(source / "tls.crt"),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=review.test",
                    "-addext",
                    "subjectAltName=DNS:review.test",
                ]
            )
            certificate_pem = (source / "tls.crt").read_text(encoding="ascii")
            (source / "review-bootstrap.json").write_text(
                json.dumps(
                    {
                        "schema_version": "review-bootstrap-v1",
                        "canonical_origin": "https://review.test",
                        "tls_certificate_file": "/run/secrets/tls.crt",
                        "tls_private_key_file": "/run/secrets/tls.key",
                        "trusted_proxy_ips": [],
                        "mounts": [
                            {
                                "name": "books",
                                "container_path": "/media/books",
                                "service_path": "/library/books",
                                "read_only": True,
                            }
                        ],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    seed,
                    "--label",
                    f"{OWNERSHIP_LABEL}={run_id}",
                    "--user",
                    "0:0",
                    "--mount",
                    f"type=volume,src={secrets_volume},dst=/secrets",
                    "--mount",
                    f"type=volume,src={media_volume},dst=/media",
                    "--mount",
                    f"type=bind,src={source},dst=/seed,readonly",
                    "--entrypoint",
                    "/bin/sh",
                    image,
                    "-c",
                    "cp /seed/review-bootstrap.json /seed/tls.crt /seed/tls.key /secrets/ && "
                    "mkdir -p /media/books && printf x >/media/books/item-1.epub && "
                    "chown -R 65532:65532 /secrets /media && chmod 700 /secrets /media /media/books && chmod 600 /secrets/* /media/books/item-1.epub",
                ]
            )

        _run(
            [
                "docker",
                "create",
                "--name",
                target,
                "--label",
                f"{OWNERSHIP_LABEL}={run_id}",
                "--user",
                "65532:65532",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=65532,gid=65532",
                "-p",
                "127.0.0.1::8443",
                "--entrypoint",
                "/opt/mechanical-librarian/bin/python",
                image,
                "-I",
                "-c",
                _FAKE_AUDIOBOOKSHELF,
            ]
        )
        _run(["docker", "start", target])
        _run(
            [
                "docker",
                "create",
                "--init",
                "--name",
                review,
                "--label",
                f"{OWNERSHIP_LABEL}={run_id}",
                "--user",
                "65532:65532",
                "--read-only",
                "--network",
                f"container:{target}",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--mount",
                f"type=volume,src={data_volume},dst=/data",
                "--mount",
                f"type=volume,src={secrets_volume},dst=/run/secrets,readonly",
                "--mount",
                f"type=volume,src={media_volume},dst=/media,readonly",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=0700,uid=65532,gid=65532",
                image,
            ]
        )
        if not _owned("container", review, run_id):
            raise QualificationError("first_run_container_ownership_failed")
        _run(["docker", "start", review])
        port = int(_run(["docker", "port", target, "8443/tcp"]).rsplit(":", 1)[1])
        context = _verified_context(certificate_pem)
        if not _health(port, "/health/ready", time.monotonic() + 30, context):
            raise QualificationError("first_run_readiness_failed")
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        status, claim = _json_request(opener, port, "/api/setup/claim")
        if (
            status != 200
            or not isinstance(claim, dict)
            or claim.get("claimed") is not False
        ):
            raise QualificationError("first_run_claim_failed")
        password = "synthetic qualification owner"
        status, session = _json_request(
            opener,
            port,
            "/api/setup/session",
            method="POST",
            payload={
                "password": password,
                "confirmation": password,
                "expected_revision": claim["revision"],
            },
            headers={
                "Origin": "https://review.test",
                "X-Setup-Nonce": str(claim["nonce"]),
            },
        )
        if status != 200 or not isinstance(session, dict):
            raise QualificationError("first_run_password_failed")
        csrf = str(session["csrf_token"])
        mutation_headers = {
            "Origin": "https://review.test",
            "X-CSRF-Token": csrf,
        }
        steps = (
            (
                "/api/setup/connect",
                {
                    "expected_revision": 2,
                    "name": "primary",
                    "endpoint": "http://127.0.0.1:18080",
                    "key": "synthetic-target-token",
                },
                3,
            ),
            ("/api/setup/providers", {"expected_revision": 3, "providers": []}, 4),
        )
        for path, payload, revision in steps:
            status, result = _json_request(
                opener,
                port,
                path,
                method="POST",
                payload=payload,
                headers=mutation_headers,
            )
            if (
                status != 200
                or not isinstance(result, dict)
                or result.get("revision") != revision
            ):
                raise QualificationError("first_run_step_failed")
        status, discovery = _json_request(
            opener, port, "/api/setup/libraries?expected_revision=4"
        )
        if (
            status != 200
            or not isinstance(discovery, dict)
            or not isinstance(discovery.get("libraries"), list)
            or len(discovery["libraries"]) != 1
        ):
            raise QualificationError("first_run_discovery_failed")
        status, result = _json_request(
            opener,
            port,
            "/api/setup/libraries",
            method="POST",
            payload={
                "expected_revision": 4,
                "libraries": [
                    {
                        "name": "synthetic-books-essays",
                        "display_name": "Synthetic Books & Essays",
                        "library_id": "books",
                        "media_kind": "book",
                        "provider_connections": [],
                    }
                ],
            },
            headers=mutation_headers,
        )
        if status != 200 or not isinstance(result, dict) or result.get("revision") != 5:
            raise QualificationError("first_run_library_selection_failed")
        status, mounts = _json_request(
            opener,
            port,
            "/api/setup/mounts",
            method="POST",
            payload={
                "expected_revision": 5,
                "service_paths": {"synthetic-books-essays": "/library/books"},
            },
            headers=mutation_headers,
        )
        if status != 200 or not isinstance(mounts, dict) or mounts.get("revision") != 6:
            raise QualificationError("first_run_mount_failed")
        status, _ = _json_request(
            opener,
            port,
            "/api/setup/kick-off",
            method="POST",
            payload={"expected_revision": 6},
            headers=mutation_headers,
        )
        if status != 202:
            raise QualificationError("first_run_promotion_failed")
        status, _ = _json_request(opener, port, "/api/setup/state")
        if status != 404:
            raise QualificationError("first_run_setup_routes_open")
        workspace = urllib.request.Request(
            f"https://127.0.0.1:{port}/library", headers={"Host": "review.test"}
        )
        with opener.open(workspace, timeout=5) as response:
            if response.status != 200 or b"review-app" not in response.read(1_048_577):
                raise QualificationError("first_run_session_handoff_failed")
        status, settings = _json_request(opener, port, "/api/v1/settings")
        if (
            status != 200
            or not isinstance(settings, dict)
            or settings.get("authority") != "store"
        ):
            raise QualificationError("first_run_settings_failed")
        preparation_advanced = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status, preparation = _json_request(
                opener, port, "/api/v1/review-preparation"
            )
            if status == 200 and isinstance(preparation, dict):
                libraries = preparation.get("libraries")
                if isinstance(libraries, list) and any(
                    isinstance(item, dict)
                    and isinstance(item.get("total"), int)
                    and item["total"] >= 1
                    and isinstance(item.get("checked"), int)
                    and item["checked"] >= 1
                    for item in libraries
                ):
                    preparation_advanced = True
                    break
            time.sleep(0.2)
        if not preparation_advanced:
            raise QualificationError("first_run_preparation_did_not_advance")
        _clean_stop(review)
        _run(["docker", "start", review])
        if not _health(port, "/health/ready", time.monotonic() + 30, context):
            raise QualificationError("first_run_restart_failed")
        restarted = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        status, _ = _json_request(
            restarted,
            port,
            "/api/v1/session/login",
            method="POST",
            payload={"password": password},
            headers={"Origin": "https://review.test"},
        )
        if (
            status != 200
            or _json_request(restarted, port, "/api/v1/settings")[0] != 200
        ):
            raise QualificationError("first_run_durable_restart_failed")
        _clean_stop(review)
        return {
            "durable_restart": True,
            "empty_volume": True,
            "ordinary_session_continuity": True,
            "preparation_advanced": True,
            "same_listener_promotion": True,
            "setup_routes_closed": True,
        }
    finally:
        _remove_owned("container", review, run_id)
        _remove_owned("container", target, run_id)
        _remove_owned("container", seed, run_id)
        _remove_owned("volume", media_volume, run_id)
        _remove_owned("volume", secrets_volume, run_id)
        _remove_owned("volume", data_volume, run_id)


def _configuration() -> dict[str, object]:
    return {
        "schema_version": "config-v8",
        "provider_recovery_generation": 1,
        "connections": [
            {
                "name": "primary",
                "adapter": "audiobookshelf",
                "endpoint": "https://127.0.0.1:9",
                "secret_file": "/data/target.secret",
            }
        ],
        "libraries": [
            {
                "name": "books",
                "connection": "primary",
                "library_id": "01234567-89ab-4def-8123-456789abcdef",
                "mount": "books",
                "provider_connections": [],
            }
        ],
        "mounts": [
            {
                "name": "books",
                "container_path": "/media/books",
                "service_path": "/library/books",
                "read_only": True,
            }
        ],
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
            _run(
                [
                    "docker",
                    "build",
                    "--pull=false",
                    "--label",
                    f"org.opencontainers.image.revision={source_sha}",
                    "--label",
                    f"{OWNERSHIP_LABEL}={run_id}",
                    "-t",
                    image,
                    str(snapshot),
                ],
                capture=False,
            )
        if not _owned("image", image, run_id):
            raise QualificationError("image_ownership_failed")
        image_id = _run(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
        image_revision = _run(
            [
                "docker",
                "image",
                "inspect",
                image,
                "--format",
                '{{ index .Config.Labels "org.opencontainers.image.revision" }}',
            ]
        )
        if image_revision != source_sha:
            raise QualificationError("image_revision_mismatch")
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "65532:65532",
                "--read-only",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--entrypoint",
                "/opt/mechanical-librarian/bin/python",
                image,
                "-I",
                "-c",
                "import importlib.util; raise SystemExit(0 if "
                "importlib.util.find_spec('mechanical_librarian_workbench') is None else 1)",
            ]
        )
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "65532:65532",
                "--read-only",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--entrypoint",
                "/opt/mechanical-librarian/bin/python",
                image,
                "-I",
                "-c",
                "from mechanical_librarian.interfaces.web.react_assets import "
                "ReactAssetBundle,ReactAssetLoader; value=ReactAssetLoader().load(); "
                "raise SystemExit(0 if isinstance(value,ReactAssetBundle) and "
                "value.entry == 'index.html' and any(a.kind == 'script' for a in value.assets) "
                "and any(a.kind == 'style' for a in value.assets) else 1)",
            ]
        )
        _run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "65532:65532",
                "--read-only",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--entrypoint",
                "/bin/sh",
                image,
                "-eu",
                "-c",
                'test -z "$(command -v node || true)"; '
                'test -z "$(command -v npm || true)"; '
                'test -z "$(find /opt/mechanical-librarian -type d '
                "\\( -name node_modules -o -name frontend -o -name generated -o -name .vite \\) "
                '-print -quit)"; '
                'test -z "$(find /opt/mechanical-librarian -type f '
                "\\( -name '*.map' -o -name package.json -o -name package-lock.json \\) "
                '-print -quit)"',
            ]
        )
        _run(
            [
                "docker",
                "volume",
                "create",
                "--label",
                f"{OWNERSHIP_LABEL}={run_id}",
                volume,
            ]
        )
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
            (seed / "target.secret").write_text(
                "synthetic-target-token", encoding="ascii"
            )
            (seed / "guarded-checkpoint-probe.py").write_bytes(
                _git_blob(source_sha, "scripts/guarded_checkpoint_probe.py")
            )
            _run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(seed / "tls.key"),
                    "-out",
                    str(seed / "tls.crt"),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=review.test",
                    # A real certificate carries its names in a SAN, and the
                    # container health probe verifies the hostname it connects
                    # under. Without this the qualification identity would rest
                    # on OpenSSL's subject-CN fallback, which no operator's
                    # certificate relies on and no verifier promises to keep.
                    "-addext",
                    "subjectAltName=DNS:review.test",
                ]
            )
            # Read before this directory is removed: it is the trust anchor for
            # every HTTPS request the qualifier makes to the started container,
            # which happens after the `with` block below closes.
            certificate_pem = (seed / "tls.crt").read_text(encoding="ascii")
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--name",
                    seed_container,
                    "--label",
                    f"{OWNERSHIP_LABEL}={run_id}",
                    "--user",
                    "0:0",
                    "--mount",
                    f"type=volume,src={volume},dst=/data",
                    "--mount",
                    f"type=bind,src={seed},dst=/seed,readonly",
                    "--entrypoint",
                    "/bin/sh",
                    image,
                    "-c",
                    "cp /seed/config.json /seed/owner.argon2 /seed/target.secret "
                    "/seed/guarded-checkpoint-probe.py "
                    "/seed/tls.crt /seed/tls.key /data/ && "
                    "chown -R 65532:65532 /data && chmod 700 /data && chmod 600 /data/*",
                ]
            )

        probe_command = [
            "docker",
            "run",
            "--rm",
            "--name",
            probe_container,
            "--label",
            f"{OWNERSHIP_LABEL}={run_id}",
            "--user",
            "65532:65532",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--mount",
            f"type=volume,src={volume},dst=/data",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=0700,uid=65532,gid=65532",
            "--entrypoint",
            "/opt/mechanical-librarian/bin/python",
            image,
            "-I",
            "/data/guarded-checkpoint-probe.py",
        ]
        _run([*probe_command, "prepare"])
        checkpoint_evidence = json.loads(_run([*probe_command, "recover"]))
        if checkpoint_evidence.get("status") != "passed":
            raise QualificationError("guarded_checkpoint_restart_failed")

        container_id = _run(
            [
                "docker",
                "create",
                "--init",
                "--name",
                container,
                "--label",
                f"{OWNERSHIP_LABEL}={run_id}",
                "--user",
                "65532:65532",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--mount",
                f"type=volume,src={volume},dst=/data",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=0700,uid=65532,gid=65532",
                "-p",
                "127.0.0.1::8443",
                image,
            ]
        )
        if not _owned("container", container, run_id):
            raise QualificationError("container_ownership_failed")
        _run(["docker", "start", container])
        port = int(_run(["docker", "port", container, "8443/tcp"]).rsplit(":", 1)[1])
        pinned = _verified_context(certificate_pem)
        if not _health(port, "/health/live", time.monotonic() + 20, pinned):
            raise QualificationError("container_liveness_failed")
        if not _health(port, "/health/ready", time.monotonic() + 20, pinned):
            raise QualificationError("container_readiness_failed")
        if not _workspace(port, pinned):
            raise QualificationError("container_react_workspace_failed")
        if _declared_health(container, time.monotonic() + 90) != "healthy":
            raise QualificationError("container_declared_health_failed")
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
            port = int(
                _run(["docker", "port", container, "8443/tcp"]).rsplit(":", 1)[1]
            )
            if not _health(port, "/health/ready", time.monotonic() + 20, pinned):
                raise QualificationError("container_restart_failed")
            shutdown_ms = max(shutdown_ms, _clean_stop(container))
            _run(
                ["docker", "cp", f"{container}:/data/application.sqlite3", str(second)]
            )
            second_hash = _sha256(second)
        if first_hash != second_hash:
            raise QualificationError("container_restart_state_changed")
        first_run = _qualify_first_run(image, run_id, suffix)
        if (
            _run(["git", "rev-parse", "HEAD"]) != source_sha
            or _run(["git", "status", "--porcelain"])
            or _sha256(Path(__file__)) != script_digest
        ):
            raise QualificationError("source_changed_during_qualification")
        print(
            json.dumps(
                {
                    "application_sha256": first_hash,
                    "container_id": container_id,
                    "declared_health": "healthy",
                    "exact_current_restart": True,
                    "graceful_shutdown_ms": shutdown_ms,
                    "graceful_shutdown_seconds": GRACEFUL_SHUTDOWN_SECONDS,
                    "graceful_terminal_state": True,
                    "guarded_checkpoint_restart": True,
                    "frontend_build_tools_excluded": True,
                    "image_id": image_id,
                    "image_revision": image_revision,
                    "installed_artifact": "container-image",
                    "first_run": first_run,
                    "liveness": True,
                    "non_root": True,
                    "read_only_root": True,
                    "readiness": True,
                    "react_assets_validated": True,
                    "react_workspace_activated": True,
                    "schema": "container-lifecycle-evidence-v5",
                    "snapshot_build": True,
                    "workbench_excluded": True,
                    "single_data_volume": True,
                    "source_sha": source_sha,
                    "status": "passed",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    finally:
        _remove_owned("container", probe_container, run_id)
        _remove_owned("container", seed_container, run_id)
        _remove_owned("container", container, run_id)
        _remove_owned("volume", volume, run_id)
        _remove_owned("image", image, run_id)


if __name__ == "__main__":
    main()
