#!/usr/bin/env python3
"""Prove the pinned contained Codex request exposes no model-callable tools."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mechanical_librarian.adapters.reviewers.contained_reviewer import (
    _CLOSED_MODEL_CATALOG,
    _exact_config,
)
from mechanical_librarian.application.contained_reviewer_contract import COVER_MODEL

_MAX_REQUEST_BYTES = 1_048_576
_VERSION_TIMEOUT_SECONDS = 10


def _probe_version(codex: Path, version_home: str) -> str:
    """Return the exact bounded version without exposing child diagnostics."""
    try:
        completed = subprocess.run(
            (str(codex), "--version"),
            capture_output=True,
            check=False,
            text=True,
            timeout=_VERSION_TIMEOUT_SECONDS,
            env={
                "CODEX_HOME": version_home,
                "HOME": version_home,
                "LANG": "C.UTF-8",
                "PATH": os.defpath,
            },
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("contained Codex version probe failed") from error
    if completed.returncode != 0:
        raise RuntimeError("contained Codex version probe failed")
    return completed.stdout.strip()


def _validate_requests(requests: list[dict[str, object]]) -> None:
    """Judge every captured request; retries of one identical request are fine (#1377).

    Kept apart from ``main`` so the decision is tested directly.
    """
    if not requests:
        raise RuntimeError("contained Codex emitted no fixture request")
    for document in requests:
        if document.get("model") != COVER_MODEL or document.get("tools") not in (
            None,
            [],
        ):
            raise RuntimeError("contained Codex exposed a model-callable tool")
    if any(document != requests[0] for document in requests[1:]):
        raise RuntimeError("contained Codex emitted an ambiguous fixture request")


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if len(values) != 1:
        raise SystemExit("usage: verify_contained_reviewer_tool_inventory.py CODEX")
    codex = Path(values[0])
    with tempfile.TemporaryDirectory(prefix="reviewer-version-proof-") as version_home:
        version = _probe_version(codex, version_home)
    if version != "codex-cli 0.147.0":
        raise RuntimeError("unexpected contained Codex version")

    request: list[dict[str, object]] = []
    received = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = _MAX_REQUEST_BYTES + 1
            if not 1 <= size <= _MAX_REQUEST_BYTES:
                self.send_error(413)
                return
            payload = self.rfile.read(size)
            document = json.loads(payload)
            if type(document) is not dict:
                self.send_error(400)
                return
            request.append(document)
            received.set()
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format: str, *_arguments: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    process: subprocess.Popen[str] | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="reviewer-tool-proof-") as temporary:
            root = Path(temporary)
            catalog = root / "models.json"
            catalog.write_bytes(_CLOSED_MODEL_CATALOG)
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            config = tuple(
                (
                    "model_catalog_json=" + json.dumps(str(catalog), ensure_ascii=False)
                    if value.startswith("model_catalog_json=")
                    else value
                )
                for value in _exact_config("medium")
            )
            provider = json.dumps(endpoint, ensure_ascii=False)
            command = [
                str(codex),
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--ephemeral",
                "--strict-config",
                "--model",
                COVER_MODEL,
                "--sandbox",
                "danger-full-access",
                "--json",
            ]
            for value in (
                *config,
                'model_provider="fixture"',
                'model_providers.fixture={name="fixture",base_url='
                + provider
                + ',env_key="REVIEWER_FIXTURE_KEY",wire_api="responses"}',
            ):
                command.extend(("-c", value))
            command.append("-")
            environment = {
                "CODEX_HOME": str(root),
                "HOME": str(root),
                "LANG": "C.UTF-8",
                "PATH": os.defpath,
                "REVIEWER_FIXTURE_KEY": "synthetic-build-proof",
            }
            try:
                process = subprocess.Popen(
                    command,
                    cwd=root,
                    env=environment,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                assert process.stdin is not None
                process.stdin.write("Return an empty JSON object.\n")
                process.stdin.close()
                if not received.wait(timeout=30):
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                    assert process.stderr is not None
                    diagnostics = process.stderr.read()[-4_096:].replace(
                        environment["REVIEWER_FIXTURE_KEY"], "[redacted]"
                    )
                    process = None
                    raise RuntimeError(
                        "contained Codex emitted no fixture request: " + diagnostics
                    )
            finally:
                # The child is stopped while its home still exists (#1377).
                if process is not None and process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    _validate_requests(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
