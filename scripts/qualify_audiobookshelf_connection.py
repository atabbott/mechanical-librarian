#!/usr/bin/env python3
"""Qualify F04 against a disposable digest-pinned Audiobookshelf container."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


IMAGE = (
    "ghcr.io/advplyr/audiobookshelf@"
    "sha256:1eef6716183c52abafe5405e7d6be8390248ecd59c7488c44af871757ac8fc4d"
)
ROOT = Path(__file__).parents[1]


def request(url: str, method: str = "GET", payload=None, token=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    operation = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(operation, timeout=10) as response:
        body = response.read()
        if not body:
            return response.status, None
        try:
            value = json.loads(body)
        except (UnicodeError, ValueError):
            value = None
        return response.status, value


def wait_ready(base: str) -> dict:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            status, value = request(base + "/status")
            if status == 200 and isinstance(value, dict):
                return value
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError("disposable Audiobookshelf did not become ready")


class TraceProxy(BaseHTTPRequestHandler):
    upstream = ""
    trace: list[tuple[str, str, bool, bool]] = []

    def _forward(self) -> None:
        type(self).trace.append(
            (
                self.command,
                self.path,
                bool(self.headers.get("Authorization")),
                bool(self.headers.get("Cookie")),
            )
        )
        headers = {"Accept": "application/json"}
        authorization = self.headers.get("Authorization")
        if authorization:
            headers["Authorization"] = authorization
        upstream = urllib.request.Request(
            type(self).upstream + self.path,
            data=b"" if self.command == "POST" else None,
            headers=headers,
            method=self.command,
        )
        try:
            with urllib.request.urlopen(upstream, timeout=10) as response:
                payload = response.read()
                self.send_response(response.status)
                self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as error:
            payload = error.read()
            self.send_response(error.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    do_GET = _forward
    do_POST = _forward

    def log_message(self, _format, *_args) -> None:
        return


def main() -> int:
    name = "ml-f04-" + uuid.uuid4().hex[:10]
    proxy = None
    with tempfile.TemporaryDirectory(prefix="ml-f04-") as directory:
        root = Path(directory)
        (root / "config").mkdir()
        (root / "metadata").mkdir()
        try:
            subprocess.run(
                [
                    "docker", "run", "-d", "--rm", "--name", name,
                    "-p", "127.0.0.1::80",
                    "-v", f"{root / 'config'}:/config",
                    "-v", f"{root / 'metadata'}:/metadata",
                    IMAGE,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            published = subprocess.run(
                ["docker", "port", name, "80/tcp"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            base = "http://127.0.0.1:" + published.rsplit(":", 1)[1]
            status = wait_ready(base)
            if status.get("serverVersion") != "2.35.1":
                raise RuntimeError("disposable Audiobookshelf version mismatch")

            request(
                base + "/init",
                "POST",
                {"newRoot": {"username": "fixture-root", "password": "fixture-password"}},
            )
            _, login = request(
                base + "/login",
                "POST",
                {"username": "fixture-root", "password": "fixture-password"},
            )
            access_token = login["user"]["accessToken"]
            user_id = login["user"]["id"]
            _, created = request(
                base + "/api/api-keys",
                "POST",
                {"name": "f04-disposable", "userId": user_id, "isActive": True},
                access_token,
            )
            api_key = created["apiKey"]["apiKey"]

            TraceProxy.upstream = base
            TraceProxy.trace = []
            proxy = ThreadingHTTPServer(("127.0.0.1", 0), TraceProxy)
            thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            thread.start()
            secret = root / "api-key"
            secret.write_text(api_key + "\n", encoding="utf-8")
            secret.chmod(0o600)
            configuration = root / "config-v1.json"
            configuration.write_text(
                json.dumps(
                    {
                        "schema_version": "config-v1",
                        "instance_name": "f04-disposable",
                        "connections": [{
                            "id": "abs-disposable",
                            "adapter": "audiobookshelf",
                            "endpoint": f"http://127.0.0.1:{proxy.server_port}",
                            "secret_reference": str(secret),
                            "enabled_libraries": [
                                "01234567-89ab-4def-8123-456789abcdef"
                            ],
                            "capability_overrides": [],
                        }],
                    }
                ),
                encoding="utf-8",
            )
            from mechanical_librarian.bootstrap import _run_connection_probe

            diagnostic = _run_connection_probe(configuration)
            if not diagnostic.succeeded:
                raise RuntimeError(
                    f"F04 disposable probe outcome={diagnostic.outcome.value} "
                    f"trace={TraceProxy.trace}"
                )
            TraceProxy.trace = []
            completed = subprocess.run(
                [
                    str(ROOT / ".venv" / "bin" / "mechanical-librarian"),
                    "connection-check", "--config", str(configuration),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    "F04 disposable probe failed "
                    f"return={completed.returncode} trace={TraceProxy.trace} "
                    f"stderr={completed.stderr.strip()}"
                )
            if TraceProxy.trace != [
                ("GET", "/status", False, False),
                ("POST", "/api/authorize", True, False),
            ]:
                raise RuntimeError("F04 probe exceeded its exact route/method contract")
            if api_key in completed.stdout + completed.stderr:
                raise RuntimeError("F04 probe exposed credential material")
            print(completed.stdout, end="")
            print(
                "trace=GET:/status:auth-none:cookie-none,"
                "POST:/api/authorize:auth-bearer:cookie-none"
            )
            return 0
        finally:
            if proxy is not None:
                proxy.shutdown()
                proxy.server_close()
            subprocess.run(
                ["docker", "rm", "-f", name],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    raise SystemExit(main())
