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
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from argon2 import PasswordHasher
from uvicorn.config import Config, create_ssl_context
from uvicorn.server import ServerState

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.configuration import (
    ConfigurationError,
    ReviewWebConfiguration,
)
from mechanical_librarian.bootstrap.composition import run_review_server
from mechanical_librarian.infrastructure.security.review_protocol import (
    MAX_REVIEW_CONNECTIONS,
    REVIEW_HEADER_TIMEOUT_SECONDS,
    REVIEW_TLS_CIPHERS,
    BoundedReviewH11Protocol,
)
from mechanical_librarian.infrastructure.security.review_tls import (
    REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
    open_review_tls_identity,
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


def test_matching_owner_only_identity_is_descriptor_bound_and_reopenable() -> None:
    with tempfile.TemporaryDirectory() as directory:
        certificate, private_key = synthetic_tls_identity(Path(directory))
        for _ in range(2):
            with open_review_tls_identity(
                str(certificate), str(private_key)
            ) as identity:
                assert Path(identity.certificate_file).exists()
                assert Path(identity.private_key_file).exists()
            assert not Path(identity.certificate_file).exists()
            assert not Path(identity.private_key_file).exists()


def test_incomplete_mismatched_linked_or_exposed_identity_fails_closed() -> None:
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
        assert empty_encrypted.read_bytes().startswith(
                b"-----BEGIN ENCRYPTED PRIVATE KEY-----"
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
            with pytest.raises(ConfigurationError):
                with open_review_tls_identity(
                    candidate_certificate, candidate_key
                ):
                    pytest.fail("invalid TLS identity was admitted")


def test_uvicorn_second_load_rejects_encrypted_keys_noninteractively() -> None:
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
        with pytest.raises(ssl.SSLError):
            create_ssl_context(str(certificate), str(encrypted), **arguments)


def test_encrypted_envelopes_fail_before_ssl_parsing() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        certificate, private_key = synthetic_tls_identity(root)
        encrypted = (
            _encrypted_private_key(
                private_key,
                root / ("empty.key" if password == "" else "protected.key"),
                password,
            )
            for password in ("", "synthetic-test-password")
        )
        with patch(
            "mechanical_librarian.infrastructure.security.review_tls.ssl.SSLContext"
        ) as context:
            for candidate in encrypted:
                with pytest.raises(ConfigurationError):
                    with open_review_tls_identity(
                        str(certificate), str(candidate)
                    ):
                        pytest.fail("encrypted key reached SSL parsing")
        context.assert_not_called()


@pytest.fixture
def protocol_environment() -> Iterator[tuple[Config, asyncio.AbstractEventLoop]]:
    """One loaded uvicorn config over its own event loop, closed after use."""

    loop = asyncio.new_event_loop()

    async def app(scope, receive, send):
        return None

    config = Config(app, log_config=None)
    config.load()
    yield config, loop
    loop.close()


def _protocol(
    environment: tuple[Config, asyncio.AbstractEventLoop],
    state: ServerState | None = None,
) -> tuple[BoundedReviewH11Protocol, Mock]:
    config, loop = environment
    protocol = BoundedReviewH11Protocol(
        config, state or ServerState(), {}, _loop=loop
    )
    transport = Mock()
    transport.get_extra_info.side_effect = lambda name: {
        "sockname": ("127.0.0.1", 8443),
        "peername": ("127.0.0.1", 50000),
        "sslcontext": object(),
    }.get(name)
    transport.is_closing.return_value = False
    return protocol, transport


def test_incomplete_headers_are_closed_on_the_fixed_timeout(
    protocol_environment: tuple[Config, asyncio.AbstractEventLoop],
) -> None:
    protocol, transport = _protocol(protocol_environment)
    protocol.connection_made(transport)
    assert REVIEW_HEADER_TIMEOUT_SECONDS == 10.0
    protocol.data_received(b"GET /login HTTP/1.1\r\nHost:")
    assert protocol._review_header_timeout is not None
    protocol._expire_review_header()
    transport.close.assert_called_once_with()
    protocol.connection_lost(None)


def test_excess_connections_are_closed_before_request_processing(
    protocol_environment: tuple[Config, asyncio.AbstractEventLoop],
) -> None:
    state = ServerState()
    state.connections.update(Mock() for _ in range(MAX_REVIEW_CONNECTIONS))
    protocol, transport = _protocol(protocol_environment, state)
    protocol.connection_made(transport)
    transport.close.assert_called_once_with()
    assert protocol._review_header_timeout is None
    protocol.connection_lost(None)


def test_installed_command_serves_only_the_canonical_https_boundary() -> None:
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
                    "schema_version": "config-v6",
                    "provider_recovery_generation": 1,
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
                "--catalogue",
                str(root / "catalogue.sqlite3"),
                "--artifacts",
                str(root / "artifacts"),
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
                        pytest.fail(
                            "review server exited before HTTPS qualification: "
                            + error.decode("utf-8", "replace")
                        )
                    try:
                        health = client.get(origin + "/health/live")
                        break
                    except httpx.TransportError:
                        if time.monotonic() >= deadline:
                            pytest.fail("review server did not start")
                        time.sleep(0.02)
                assert health.status_code == 200
                assert "max-age=" in health.headers["strict-transport-security"]
                hostile_host = client.get(
                    origin + "/login", headers={"Host": "hostile.invalid"}
                )
                assert hostile_host.status_code == 400
                forwarded_host = client.get(
                    origin + "/login",
                    headers={"X-Forwarded-Host": "127.0.0.1"},
                )
                assert forwarded_host.status_code == 400
                hostile_origin = client.post(
                    origin + "/api/v1/session/login",
                    headers={"Origin": "https://hostile.invalid"},
                    json={"password": "synthetic password"},
                )
                assert hostile_origin.status_code == 403
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def test_server_uses_validated_tls_before_opening_the_database() -> None:
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
        database = root / "application.sqlite3"

        def qualified_run(app, **arguments):
            assert Path(arguments["ssl_certfile"]).exists()
            assert Path(arguments["ssl_keyfile"]).exists()
            assert arguments["ssl_keyfile_password"] == REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD
            assert not arguments["proxy_headers"]
            assert arguments["http"] is BoundedReviewH11Protocol
            assert arguments["limit_concurrency"] == MAX_REVIEW_CONNECTIONS
            assert arguments["backlog"] == MAX_REVIEW_CONNECTIONS
            assert arguments["h11_max_incomplete_event_size"] == 16_384
            assert arguments["ssl_ciphers"] == REVIEW_TLS_CIPHERS
            assert arguments["host"] == "127.0.0.1"
            assert arguments["port"] == 8443
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
            assert tls12
            assert all("GCM" in name or "CHACHA20" in name for name in tls12)

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
                "mechanical_librarian.bootstrap.composition.compose_review_application",
                return_value=object(),
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.load_owner_password_verifier",
                return_value=lambda _: False,
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.create_review_pipeline",
                return_value=Mock(),
            ),
            patch(
                "mechanical_librarian.bootstrap.composition.uvicorn.run",
                side_effect=qualified_run,
            ),
        ):
            run_review_server(
                Path("/config.json"),
                database,
                Path("/catalogue.sqlite3"),
                Path("/artifacts"),
                "127.0.0.1",
                8443,
            )
        opened.assert_called_once_with(
            database, create=False, cross_thread=True
        )


def test_missing_or_invalid_tls_fails_before_database_or_server() -> None:
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
            with patch(
                    "mechanical_librarian.bootstrap.composition.load_configuration_file",
                    return_value=SimpleNamespace(review=review),
                ), patch(
                    "mechanical_librarian.bootstrap.composition.open_repository"
                ) as opened, patch(
                    "mechanical_librarian.bootstrap.composition.uvicorn.run"
                ) as served, pytest.raises(ConfigurationError):
                run_review_server(
                    Path("/config.json"),
                    Path("/application.sqlite3"),
                    Path("/catalogue.sqlite3"),
                    Path("/artifacts"),
                    "127.0.0.1",
                    8443,
                )
            opened.assert_not_called()
            served.assert_not_called()
