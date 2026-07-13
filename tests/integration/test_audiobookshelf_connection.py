from __future__ import annotations

import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import mechanical_librarian.bootstrap as bootstrap


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "audiobookshelf" / "2.35.1"


class _FixtureHandler(BaseHTTPRequestHandler):
    requests = []

    def _respond(self, fixture: str) -> None:
        payload = (FIXTURES / fixture).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if fixture == "status.json":
            self.send_header("Set-Cookie", "hostile-session=private; Path=/")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        type(self).requests.append(("GET", self.path, dict(self.headers)))
        if self.path != "/status":
            self.send_error(404)
            return
        self._respond("status.json")

    def do_POST(self) -> None:
        type(self).requests.append(("POST", self.path, dict(self.headers)))
        if self.path != "/api/authorize":
            self.send_error(404)
            return
        self._respond("authorize.json")

    def log_message(self, _format, *_args) -> None:
        return


class _IncompleteProfileHandler(_FixtureHandler):
    def do_POST(self) -> None:
        type(self).requests.append(("POST", self.path, dict(self.headers)))
        if self.path != "/api/authorize":
            self.send_error(404)
            return
        self._respond("authorize-partial.json")


_MISSING_FLAG = object()


class _AmbiguousPermissionHandler(_FixtureHandler):
    access_all_libraries = _MISSING_FLAG

    def do_POST(self) -> None:
        type(self).requests.append(("POST", self.path, dict(self.headers)))
        if self.path != "/api/authorize":
            self.send_error(404)
            return
        payload = json.loads((FIXTURES / "authorize.json").read_text(encoding="utf-8"))
        permissions = payload["user"]["permissions"]
        if self.access_all_libraries is _MISSING_FLAG:
            del permissions["accessAllLibraries"]
        else:
            permissions["accessAllLibraries"] = self.access_all_libraries
        payload["user"]["librariesAccessible"] = ["synthetic-library"]
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _ScopedUuidHandler(_FixtureHandler):
    library_id = "01234567-89ab-4def-8123-456789abcdef"

    def do_POST(self) -> None:
        type(self).requests.append(("POST", self.path, dict(self.headers)))
        if self.path != "/api/authorize":
            self.send_error(404)
            return
        payload = json.loads((FIXTURES / "authorize.json").read_text(encoding="utf-8"))
        payload["user"]["permissions"]["accessAllLibraries"] = False
        payload["user"]["librariesAccessible"] = [self.library_id]
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class ComposedAudiobookshelfConnectionTests(unittest.TestCase):
    def test_scoped_uuid_library_is_configured_and_verified_end_to_end(self) -> None:
        _ScopedUuidHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ScopedUuidHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "private-api-key"
            secret.write_text("private-token-sentinel\n", encoding="utf-8")
            secret.chmod(0o600)
            configuration = root / "private-config.json"
            configuration.write_text(
                json.dumps(
                    {
                        "schema_version": "config-v1",
                        "instance_name": "scoped-uuid",
                        "connections": [
                            {
                                "id": "abs-synthetic",
                                "adapter": "audiobookshelf",
                                "endpoint": f"http://127.0.0.1:{server.server_port}",
                                "secret_reference": str(secret),
                                "enabled_libraries": [_ScopedUuidHandler.library_id],
                                "capability_overrides": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                result = bootstrap._run_connection_probe(configuration)
                exit_code = bootstrap.main(
                    ["connection-check", "--config", str(configuration)]
                )

        self.assertEqual(result.outcome.value, "ready")
        self.assertEqual(exit_code, 0, errors.getvalue())
        self.assertEqual(errors.getvalue(), "")
        self.assertIn("Connection check passed.\n", output.getvalue())
        self.assertNotIn(_ScopedUuidHandler.library_id, output.getvalue())

    def test_incomplete_authorization_profile_fails_cli_without_success_report(
        self,
    ) -> None:
        _IncompleteProfileHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _IncompleteProfileHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "private-api-key"
            secret.write_text("private-token-sentinel\n", encoding="utf-8")
            secret.chmod(0o600)
            configuration = root / "private-config.json"
            configuration.write_text(
                json.dumps(
                    {
                        "schema_version": "config-v1",
                        "instance_name": "incomplete-authorization",
                        "connections": [
                            {
                                "id": "abs-synthetic",
                                "adapter": "audiobookshelf",
                                "endpoint": f"http://127.0.0.1:{server.server_port}",
                                "secret_reference": str(secret),
                                "enabled_libraries": ["synthetic-library"],
                                "capability_overrides": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            errors = io.StringIO()
            with redirect_stdout(output), redirect_stderr(errors):
                result = bootstrap._run_connection_probe(configuration)
                exit_code = bootstrap.main(
                    ["connection-check", "--config", str(configuration)]
                )

        self.assertEqual(result.outcome.value, "authorization_failed")
        self.assertIsNone(result.inspection)
        self.assertEqual(exit_code, 1)
        self.assertNotIn("Connection check passed.", output.getvalue())
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            errors.getvalue(),
            "Connection check failed: configured target could not be verified.\n",
        )

    def test_ambiguous_scope_flag_fails_cli_with_covering_library_list(
        self,
    ) -> None:
        for name, flag in (("missing", _MISSING_FLAG), ("null", None)):
            with self.subTest(name=name):
                _AmbiguousPermissionHandler.access_all_libraries = flag
                _AmbiguousPermissionHandler.requests = []
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), _AmbiguousPermissionHandler
                )
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        secret = root / "private-api-key"
                        secret.write_text(
                            "private-token-sentinel\n", encoding="utf-8"
                        )
                        secret.chmod(0o600)
                        configuration = root / "private-config.json"
                        configuration.write_text(
                            json.dumps(
                                {
                                    "schema_version": "config-v1",
                                    "instance_name": "ambiguous-authorization",
                                    "connections": [
                                        {
                                            "id": "abs-synthetic",
                                            "adapter": "audiobookshelf",
                                            "endpoint": (
                                                "http://127.0.0.1:"
                                                f"{server.server_port}"
                                            ),
                                            "secret_reference": str(secret),
                                            "enabled_libraries": [
                                                "synthetic-library"
                                            ],
                                            "capability_overrides": [],
                                        }
                                    ],
                                }
                            ),
                            encoding="utf-8",
                        )
                        output = io.StringIO()
                        errors = io.StringIO()
                        with redirect_stdout(output), redirect_stderr(errors):
                            result = bootstrap._run_connection_probe(configuration)
                            exit_code = bootstrap.main(
                                [
                                    "connection-check",
                                    "--config",
                                    str(configuration),
                                ]
                            )
                finally:
                    server.shutdown()
                    server.server_close()

                self.assertEqual(result.outcome.value, "authorization_failed")
                self.assertIsNone(result.inspection)
                self.assertEqual(exit_code, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(
                    errors.getvalue(),
                    "Connection check failed: configured target could not be verified.\n",
                )

    def test_non_loopback_http_is_rejected_before_secret_or_http_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            configuration = Path(directory) / "config.json"
            configuration.write_text(
                json.dumps({
                    "schema_version": "config-v1",
                    "instance_name": "transport-policy",
                    "connections": [{
                        "id": "abs-synthetic",
                        "adapter": "audiobookshelf",
                        "endpoint": "http://example.invalid",
                        "secret_reference": "/private/never-read",
                        "enabled_libraries": ["synthetic-library"],
                        "capability_overrides": [],
                    }],
                }),
                encoding="utf-8",
            )
            with patch(
                "mechanical_librarian.infrastructure.security.target_secrets._read"
            ) as read, patch(
                "mechanical_librarian.infrastructure.security.target_secrets.httpx.Client"
            ) as client:
                result = bootstrap._run_connection_probe(configuration)
            self.assertEqual(result.outcome.value, "configuration_failure")
            read.assert_not_called()
            client.assert_not_called()

    def test_missing_secret_failure_is_fixed_and_opens_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = root / "private-config.json"
            missing = root / "private-secret-sentinel"
            configuration.write_text(
                json.dumps(
                    {
                        "schema_version": "config-v1",
                        "instance_name": "synthetic-connection-check",
                        "connections": [{
                            "id": "abs-synthetic",
                            "adapter": "audiobookshelf",
                            "endpoint": "https://private-target.invalid",
                            "secret_reference": str(missing),
                            "enabled_libraries": ["synthetic-library"],
                            "capability_overrides": [],
                        }],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            errors = io.StringIO()
            with (
                redirect_stdout(output),
                redirect_stderr(errors),
                patch("socket.socket", side_effect=AssertionError("network forbidden")),
            ):
                exit_code = bootstrap.main(
                    ["connection-check", "--config", str(configuration)]
                )
        self.assertEqual(exit_code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            errors.getvalue(),
            "Connection check failed: configured target could not be verified.\n",
        )
        self.assertNotIn("private", errors.getvalue())

    def test_disposable_target_contract_is_report_only_and_redacted(self) -> None:
        _FixtureHandler.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            secret = root / "private-api-key"
            secret.write_text("private-token-sentinel\n", encoding="utf-8")
            secret.chmod(0o600)
            configuration = root / "private-config.json"
            configuration.write_text(
                json.dumps(
                    {
                        "schema_version": "config-v1",
                        "instance_name": "synthetic-connection-check",
                        "connections": [
                            {
                                "id": "private-connection-sentinel",
                                "adapter": "audiobookshelf",
                                "endpoint": f"http://127.0.0.1:{server.server_port}",
                                "secret_reference": str(secret),
                                "enabled_libraries": ["synthetic-library"],
                                "capability_overrides": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()
            errors = io.StringIO()
            with (
                redirect_stdout(output),
                redirect_stderr(errors),
                patch.object(
                    bootstrap,
                    "SQLiteInventoryRepository",
                    side_effect=AssertionError("persistence forbidden"),
                ),
            ):
                exit_code = bootstrap.main(
                    ["connection-check", "--config", str(configuration)]
                )

        self.assertEqual(exit_code, 0, errors.getvalue())
        self.assertEqual(errors.getvalue(), "")
        report = output.getvalue()
        self.assertIn("Connection check passed.\n", report)
        self.assertIn("authority=target-connection-read-only\n", report)
        self.assertIn("adapter=audiobookshelf scope=configured-target\n", report)
        self.assertNotIn("private-connection-sentinel", report)
        self.assertIn("compatibility=supported version=2.35.1\n", report)
        self.assertIn("health=healthy\n", report)
        self.assertIn("lineage=abs-connection-v1/abs-2.35.1-fixture-v1\n", report)
        for private in ("private", "connection-sentinel", "token-sentinel", "127.0.0.1"):
            self.assertNotIn(private, report)
        self.assertEqual(
            [(method, path) for method, path, _headers in _FixtureHandler.requests],
            [("GET", "/status"), ("POST", "/api/authorize")],
        )
        self.assertNotIn("Authorization", _FixtureHandler.requests[0][2])
        self.assertEqual(
            _FixtureHandler.requests[1][2].get("Authorization"),
            "Bearer private-token-sentinel",
        )
        for _method, _path, headers in _FixtureHandler.requests:
            self.assertNotIn("Cookie", headers)
        self.assertFalse(
            any(
                marker in path
                for _method, path, _headers in _FixtureHandler.requests
                for marker in ("library", "item", "cover", "media", "upload", "update", "delete")
            )
        )
        for forbidden in ("SupportedWriter", "MetadataProvider", "Reviewer"):
            self.assertNotIn(forbidden, vars(bootstrap))


if __name__ == "__main__":
    unittest.main()
