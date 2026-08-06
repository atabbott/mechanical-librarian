from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from uvicorn.config import Config, create_ssl_context
from uvicorn.server import ServerState
from argon2 import PasswordHasher

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import (
    ConfigurationError,
    ReviewWebConfiguration,
)
from mechanical_librarian.bootstrap.composition import run_review_server
from mechanical_librarian.infrastructure.security.review_tls import (
    REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
    open_review_tls_identity,
)
from mechanical_librarian.infrastructure.security.review_protocol import (
    BoundedReviewH11Protocol,
    MAX_REVIEW_CONNECTIONS,
    REVIEW_HEADER_TIMEOUT_SECONDS,
    REVIEW_TLS_CIPHERS,
)
from tests.support.tls import synthetic_tls_identity


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _encrypted_private_key(
    source: Path, destination: Path, password: str
) -> Path:
    subprocess.run(
        [
            "openssl",
            "pkey",
            "-in",
            str(source),
            "-aes256",
            "-passout",
            "pass:" + password,
            "-out",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(destination, 0o600)
    return destination


class ReviewTLSIdentityTests(unittest.TestCase):
    def test_matching_owner_only_identity_is_descriptor_bound_and_reopenable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            certificate, private_key = synthetic_tls_identity(Path(directory))
            for _ in range(2):
                with open_review_tls_identity(
                    str(certificate), str(private_key)
                ) as identity:
                    self.assertTrue(Path(identity.certificate_file).exists())
                    self.assertTrue(Path(identity.private_key_file).exists())
                self.assertFalse(Path(identity.certificate_file).exists())
                self.assertFalse(Path(identity.private_key_file).exists())

    def test_incomplete_mismatched_linked_or_exposed_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            certificate, private_key = synthetic_tls_identity(root, "first")
            _, other_key = synthetic_tls_identity(root, "second")
            linked = root / "linked.key"
            linked.symlink_to(private_key)
            exposed = root / "exposed.key"
            exposed.write_bytes(private_key.read_bytes())
            os.chmod(exposed, 0o644)
            encrypted = _encrypted_private_key(
                private_key,
                root / "encrypted.key",
                "synthetic-test-password",
            )
            empty_encrypted = _encrypted_private_key(
                private_key, root / "empty-encrypted.key", ""
            )
            self.assertTrue(
                empty_encrypted.read_bytes().startswith(
                    b"-----BEGIN ENCRYPTED PRIVATE KEY-----"
                )
            )
            for candidate_certificate, candidate_key in (
                (None, str(private_key)),
                (str(certificate), None),
                (str(certificate), str(other_key)),
                (str(certificate), str(linked)),
                (str(certificate), str(exposed)),
                (str(certificate), str(encrypted)),
                (str(certificate), str(empty_encrypted)),
            ):
                with self.subTest(key=candidate_key), self.assertRaises(
                    ConfigurationError
                ):
                    with open_review_tls_identity(
                        candidate_certificate, candidate_key
                    ):
                        self.fail("invalid TLS identity was admitted")

    def test_uvicorn_second_load_rejects_encrypted_keys_noninteractively(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            certificate, private_key = synthetic_tls_identity(root)
            encrypted = _encrypted_private_key(
                private_key,
                root / "encrypted.key",
                "synthetic-test-password",
            )
            arguments = {
                "password": REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
                "ssl_version": ssl.PROTOCOL_TLS_SERVER,
                "cert_reqs": ssl.CERT_NONE,
                "ca_certs": None,
                "ciphers": "TLSv1",
            }
            create_ssl_context(
                str(certificate), str(private_key), **arguments
            )
            with self.assertRaises(ssl.SSLError):
                create_ssl_context(str(certificate), str(encrypted), **arguments)


class ReviewProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()

        async def app(scope, receive, send):
            return None

        self.config = Config(app, log_config=None)
        self.config.load()

    def tearDown(self) -> None:
        self.loop.close()

    def _protocol(self, state: ServerState | None = None):
        protocol = BoundedReviewH11Protocol(
            self.config, state or ServerState(), {}, _loop=self.loop
        )
        transport = Mock()
        transport.get_extra_info.side_effect = lambda name: {
            "sockname": ("127.0.0.1", 8443),
            "peername": ("127.0.0.1", 50000),
            "sslcontext": object(),
        }.get(name)
        transport.is_closing.return_value = False
        return protocol, transport

    def test_incomplete_headers_are_closed_on_the_fixed_timeout(self) -> None:
        protocol, transport = self._protocol()
        protocol.connection_made(transport)
        self.assertEqual(REVIEW_HEADER_TIMEOUT_SECONDS, 10.0)
        protocol.data_received(b"GET /login HTTP/1.1\r\nHost:")
        self.assertIsNotNone(protocol._review_header_timeout)
        protocol._expire_review_header()
        transport.close.assert_called_once_with()
        protocol.connection_lost(None)

    def test_excess_connections_are_closed_before_request_processing(self) -> None:
        state = ServerState()
        state.connections.update(Mock() for _ in range(MAX_REVIEW_CONNECTIONS))
        protocol, transport = self._protocol(state)
        protocol.connection_made(transport)
        transport.close.assert_called_once_with()
        self.assertIsNone(protocol._review_header_timeout)
        protocol.connection_lost(None)


class ReviewServerTLSCompositionTests(unittest.TestCase):
    def test_installed_command_serves_only_the_canonical_https_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            certificate, private_key = synthetic_tls_identity(root)
            owner_hash = root / "owner.hash"
            owner_hash.write_text(
                PasswordHasher().hash("synthetic password"), encoding="utf-8"
            )
            os.chmod(owner_hash, 0o600)
            database = root / "application.sqlite3"
            open_repository(database).close()
            port = _unused_port()
            origin = f"https://127.0.0.1:{port}"
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "schema_version": "config-v5",
                        "connections": [
                            {
                                "name": "target",
                                "adapter": "audiobookshelf",
                                "endpoint": "https://target.invalid",
                                "secret_file": str(root / "unused.secret"),
                            }
                        ],
                        "libraries": [
                            {
                                "name": "books",
                                "connection": "target",
                                "library_id": "synthetic-library",
                                "mount": None,
                                "provider_connections": [],
                            }
                        ],
                        "mounts": [],
                        "providers": [],
                        "review": {
                            "canonical_origin": origin,
                            "owner_password_hash_file": str(owner_hash),
                            "tls_certificate_file": str(certificate),
                            "tls_private_key_file": str(private_key),
                        },
                    }
                ),
                encoding="utf-8",
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "mechanical_librarian",
                    "review-serve",
                    "--config",
                    str(config),
                    "--database",
                    str(database),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 10
                with httpx.Client(verify=False, trust_env=False) as client:
                    while True:
                        if process.poll() is not None:
                            _, error = process.communicate()
                            self.fail(
                                "review server exited before HTTPS qualification: "
                                + error.decode("utf-8", "replace")
                            )
                        try:
                            health = client.get(origin + "/health/live")
                            break
                        except httpx.TransportError:
                            if time.monotonic() >= deadline:
                                self.fail("review server did not start")
                            time.sleep(0.02)
                    self.assertEqual(health.status_code, 200)
                    self.assertIn(
                        "max-age=", health.headers["strict-transport-security"]
                    )
                    hostile_host = client.get(
                        origin + "/login", headers={"Host": "hostile.invalid"}
                    )
                    self.assertEqual(hostile_host.status_code, 400)
                    forwarded_host = client.get(
                        origin + "/login",
                        headers={"X-Forwarded-Host": "127.0.0.1"},
                    )
                    self.assertEqual(forwarded_host.status_code, 400)
                    hostile_origin = client.post(
                        origin + "/api/v1/session/login",
                        headers={"Origin": "https://hostile.invalid"},
                        json={"password": "synthetic password"},
                    )
                    self.assertEqual(hostile_origin.status_code, 403)
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)

    def test_server_uses_validated_tls_before_opening_the_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            certificate, private_key = synthetic_tls_identity(root)
            review = ReviewWebConfiguration(
                canonical_origin="https://127.0.0.1:8443",
                owner_password_hash_file=str(root / "owner.hash"),
                tls_certificate_file=str(certificate),
                tls_private_key_file=str(private_key),
            )
            repository = Mock()

            def qualified_run(app, **arguments):
                self.assertTrue(Path(arguments["ssl_certfile"]).exists())
                self.assertTrue(Path(arguments["ssl_keyfile"]).exists())
                self.assertEqual(
                    arguments["ssl_keyfile_password"],
                    REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
                )
                self.assertFalse(arguments["proxy_headers"])
                self.assertIs(arguments["http"], BoundedReviewH11Protocol)
                self.assertEqual(
                    arguments["limit_concurrency"], MAX_REVIEW_CONNECTIONS
                )
                self.assertEqual(arguments["backlog"], MAX_REVIEW_CONNECTIONS)
                self.assertEqual(
                    arguments["h11_max_incomplete_event_size"], 16_384
                )
                self.assertEqual(arguments["ssl_ciphers"], REVIEW_TLS_CIPHERS)
                self.assertEqual(arguments["host"], "127.0.0.1")
                self.assertEqual(arguments["port"], 8443)
                certificate.write_text("replaced certificate", encoding="ascii")
                private_key.write_text("replaced private key", encoding="ascii")
                context = create_ssl_context(
                    arguments["ssl_certfile"],
                    arguments["ssl_keyfile"],
                    password=arguments["ssl_keyfile_password"],
                    ssl_version=ssl.PROTOCOL_TLS_SERVER,
                    cert_reqs=ssl.CERT_NONE,
                    ca_certs=None,
                    ciphers=arguments["ssl_ciphers"],
                )
                tls12 = [
                    cipher["name"]
                    for cipher in context.get_ciphers()
                    if cipher["protocol"] == "TLSv1.2"
                ]
                self.assertTrue(tls12)
                self.assertTrue(
                    all("GCM" in name or "CHACHA20" in name for name in tls12)
                )

            with (
                patch(
                    "mechanical_librarian.bootstrap.composition.load_configuration_file",
                    return_value=SimpleNamespace(review=review),
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.open_repository",
                    return_value=repository,
                ) as opened,
                patch(
                    "mechanical_librarian.bootstrap.composition.create_review_app",
                    return_value=object(),
                ),
                patch(
                    "mechanical_librarian.bootstrap.composition.uvicorn.run",
                    side_effect=qualified_run,
                ),
            ):
                run_review_server(
                    Path("/config.json"),
                    Path("/application.sqlite3"),
                    "127.0.0.1",
                    8443,
                )
            opened.assert_called_once_with(
                Path("/application.sqlite3"), create=False, cross_thread=True
            )

    def test_missing_or_invalid_tls_fails_before_database_or_server(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            certificate = root / "invalid.crt"
            private_key = root / "invalid.key"
            certificate.write_text("not a certificate", encoding="ascii")
            private_key.write_text("not a private key", encoding="ascii")
            os.chmod(certificate, 0o600)
            os.chmod(private_key, 0o600)
            valid_certificate, valid_key = synthetic_tls_identity(root, "valid")
            encrypted_key = _encrypted_private_key(
                valid_key,
                root / "encrypted.key",
                "synthetic-test-password",
            )
            empty_encrypted_key = _encrypted_private_key(
                valid_key, root / "empty-encrypted.key", ""
            )
            configurations = (
                ReviewWebConfiguration(
                    canonical_origin="https://review.test",
                    owner_password_hash_file=str(root / "owner.hash"),
                ),
                ReviewWebConfiguration(
                    canonical_origin="https://review.test",
                    owner_password_hash_file=str(root / "owner.hash"),
                    tls_certificate_file=str(certificate),
                    tls_private_key_file=str(private_key),
                ),
                ReviewWebConfiguration(
                    canonical_origin="https://review.test",
                    owner_password_hash_file=str(root / "owner.hash"),
                    tls_certificate_file=str(valid_certificate),
                    tls_private_key_file=str(encrypted_key),
                ),
                ReviewWebConfiguration(
                    canonical_origin="https://review.test",
                    owner_password_hash_file=str(root / "owner.hash"),
                    tls_certificate_file=str(valid_certificate),
                    tls_private_key_file=str(empty_encrypted_key),
                ),
            )
            for review in configurations:
                with (
                    self.subTest(review=review),
                    patch(
                        "mechanical_librarian.bootstrap.composition.load_configuration_file",
                        return_value=SimpleNamespace(review=review),
                    ),
                    patch(
                        "mechanical_librarian.bootstrap.composition.open_repository"
                    ) as opened,
                    patch(
                        "mechanical_librarian.bootstrap.composition.uvicorn.run"
                    ) as served,
                    self.assertRaises(ConfigurationError),
                ):
                    run_review_server(
                        Path("/config.json"),
                        Path("/application.sqlite3"),
                        "127.0.0.1",
                        8443,
                    )
                opened.assert_not_called()
                served.assert_not_called()
