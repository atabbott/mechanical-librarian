from __future__ import annotations

import contextlib
import json
import socket
import ssl
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.bootstrap import container, container_health
from mechanical_librarian.bootstrap.container_health import HEALTHY, check, main
from tests.support.ports import ReservedPort
from tests.support.tls import synthetic_tls_identity

READY = (
    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 6\r\n\r\nready\n"
)
DRAINING = (
    b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain\r\n"
    b"Content-Length: 9\r\n\r\ndraining\n"
)
NOT_HTTP = b"nothing resembling a status line\r\n\r\n"


@contextlib.contextmanager
def _server(
    certificate: Path | None, private_key: Path | None, response: bytes
) -> Iterator[tuple[int, list[bytes]]]:
    """One TLS responder that answers a single request with fixed bytes."""

    received: list[bytes] = []
    context = None
    if certificate is not None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(certificate), str(private_key))
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = int(listener.getsockname()[1])

    def serve() -> None:
        try:
            raw, _ = listener.accept()
            with (
                context.wrap_socket(raw, server_side=True)
                if context is not None
                else raw
            ) as stream:
                received.append(stream.recv(4096))
                stream.sendall(response)
        except OSError:
            # A client that refuses the certificate never completes the
            # handshake; that is the assertion, not a harness failure.
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port, received
    finally:
        listener.close()
        thread.join(timeout=5)


def _configuration(
    root: Path,
    origin: str,
    certificate: Path | None = None,
    private_key: Path | None = None,
) -> Path:
    review: dict[str, object] | None = None
    if certificate is not None and private_key is not None:
        review = {
            "canonical_origin": origin,
            "owner_password_hash_file": str(root / "owner.hash"),
            "tls_certificate_file": str(certificate),
            "tls_private_key_file": str(private_key),
        }
    config = root / "config.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "config-v9",
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
                "review": review,
            }
        ),
        encoding="utf-8",
    )
    return config


def test_a_serving_container_reports_ready_over_verified_loopback_tls(
    tmp_path: Path,
) -> None:
    certificate, private_key = synthetic_tls_identity(tmp_path)
    with _server(certificate, private_key, READY) as (port, received):
        config = _configuration(
            tmp_path, f"https://127.0.0.1:{port}", certificate, private_key
        )
        assert check(config, port=port) == HEALTHY
    request = received[0].decode("ascii")
    assert request.startswith("GET /health/ready HTTP/1.1\r\n")
    assert f"Host: 127.0.0.1:{port}\r\n" in request
    # The readiness surface is credential-free by design; the probe must not
    # have acquired or presented one.
    assert "Cookie" not in request
    assert "Authorization" not in request


def test_a_draining_container_is_reported_by_its_status(tmp_path: Path) -> None:
    certificate, private_key = synthetic_tls_identity(tmp_path)
    with _server(certificate, private_key, DRAINING) as (port, _):
        config = _configuration(
            tmp_path, f"https://127.0.0.1:{port}", certificate, private_key
        )
        assert check(config, port=port) == "not_ready_503"


def test_a_certificate_other_than_the_configured_one_is_refused(
    tmp_path: Path,
) -> None:
    """The configured certificate is the only trust anchor the probe accepts."""

    certificate, private_key = synthetic_tls_identity(tmp_path, "configured")
    other_certificate, other_key = synthetic_tls_identity(tmp_path, "other")
    with _server(other_certificate, other_key, READY) as (port, _):
        config = _configuration(
            tmp_path, f"https://127.0.0.1:{port}", certificate, private_key
        )
        assert check(config, port=port) == "unreachable"


def test_a_name_the_certificate_does_not_carry_is_refused(tmp_path: Path) -> None:
    """Hostname verification stays on; loopback is not a reason to skip it."""

    certificate, private_key = synthetic_tls_identity(tmp_path)
    with _server(certificate, private_key, READY) as (port, _):
        config = _configuration(
            tmp_path, f"https://elsewhere.test:{port}", certificate, private_key
        )
        assert check(config, port=port) == "unreachable"


def test_an_unparseable_response_is_not_taken_for_readiness(tmp_path: Path) -> None:
    certificate, private_key = synthetic_tls_identity(tmp_path)
    with _server(certificate, private_key, NOT_HTTP) as (port, _):
        config = _configuration(
            tmp_path, f"https://127.0.0.1:{port}", certificate, private_key
        )
        assert check(config, port=port) == "malformed_response"


def test_a_closed_port_is_reported_as_unreachable(tmp_path: Path) -> None:
    certificate, private_key = synthetic_tls_identity(tmp_path)
    # The reservation is held across the probe rather than dropped once the
    # number is read. Nothing binds this port on purpose, so holding it is
    # what keeps some other automatic allocation on the host from binding it
    # by accident and answering in place of the closed port under test; a
    # bound socket that never listens still refuses the connection, which is
    # the condition being asserted.
    with contextlib.closing(ReservedPort()) as reservation:
        config = _configuration(
            tmp_path,
            f"https://127.0.0.1:{reservation.number}",
            certificate,
            private_key,
        )
        assert check(config, port=reservation.number, timeout=1.0) == "unreachable"


def test_an_absent_or_invalid_configuration_is_reported(tmp_path: Path) -> None:
    assert check(tmp_path / "absent.json") == "configuration_unavailable"
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{not a configuration document", encoding="utf-8")
    assert check(invalid) == "configuration_unavailable"


def test_a_configuration_without_a_review_surface_is_reported(tmp_path: Path) -> None:
    config = _configuration(tmp_path, "https://review.test")
    assert check(config) == "review_surface_unconfigured"


def test_an_unusable_certificate_file_is_reported(tmp_path: Path) -> None:
    certificate = tmp_path / "broken.crt"
    certificate.write_text("not a certificate", encoding="ascii")
    private_key = tmp_path / "broken.key"
    private_key.write_text("not a private key", encoding="ascii")
    config = _configuration(
        tmp_path, "https://127.0.0.1:8443", certificate, private_key
    )
    assert check(config) == "tls_identity_unavailable"


def test_the_command_exits_zero_only_when_the_container_is_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(container_health, "check", lambda: HEALTHY)
    main()
    monkeypatch.setattr(container_health, "check", lambda: "not_ready_503")
    with pytest.raises(SystemExit) as failure:
        main()
    assert failure.value.code == 1
    # Docker keeps the probe's last output beside the health status, so the
    # reason an operator reads out of `docker inspect` is this line.
    assert capsys.readouterr().out.strip() == "not_ready_503"


def test_the_probe_targets_the_surface_the_entrypoint_actually_serves() -> None:
    """The probe restates the entrypoint's constants rather than importing it."""

    assert container_health.CONFIG == container.CONFIG
    assert container_health.PORT == container.PORT
    # The entrypoint binds every interface so a published mapping reaches it;
    # the probe is inside that container and stays on loopback.
    assert container.HOST == "0.0.0.0"
    assert container_health.HOST == "127.0.0.1"


@pytest.mark.parametrize("authority", ["file", "bootstrap"])
@pytest.mark.parametrize("mode", ["http", "direct", "proxy"])
@pytest.mark.parametrize(
    ("response", "expected"),
    [(READY, HEALTHY), (DRAINING, "not_ready_503"), (NOT_HTTP, "malformed_response")],
)
def test_readiness_selects_actual_listener_transport(
    tmp_path: Path, authority: str, mode: str, response: bytes, expected: str
) -> None:
    certificate, key = synthetic_tls_identity(tmp_path)
    direct = mode == "direct"
    with _server(
        certificate if direct else None, key if direct else None, response
    ) as (port, received):
        origin = f"{'http' if mode == 'http' else 'https'}://127.0.0.1:{port}"
        config = _configuration(tmp_path, origin, certificate, key)
        source = json.loads(config.read_bytes())
        review = source["review"]
        if not direct:
            review["tls_certificate_file"] = None
            review["tls_private_key_file"] = None
        review["trusted_proxy_ips"] = ["127.0.0.1"] if mode == "proxy" else []
        bootstrap = tmp_path / "bootstrap.json"
        if authority == "bootstrap":
            config.unlink()
            review.pop("owner_password_hash_file")
            bootstrap.write_text(
                json.dumps({"schema_version": "review-bootstrap-v2", **review})
            )
            bootstrap.chmod(0o600)
        else:
            config.write_text(json.dumps(source))
        assert check(config, bootstrap_path=bootstrap, port=port) == expected
    assert len(received) == 1
    assert b"Authorization" not in received[0] and b"Cookie" not in received[0]
