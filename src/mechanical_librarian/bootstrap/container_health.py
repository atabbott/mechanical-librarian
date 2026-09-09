"""Credential-free readiness probe for the supported single container.

`container.py` binds the review server to `0.0.0.0:8443` inside the image so a
published port mapping reaches it. This probe is that same container asking its
own server, over loopback, whether it is still serving, and it is what the
Dockerfile `HEALTHCHECK` and the Compose `healthcheck` block both invoke.

It reads `/health/ready`, which the trust boundary in `interfaces/web/app.py`
exempts from origin checking and which answers `ready` / 200 or `draining` /
503. So the probe needs no session, no owner password, and no weakening of any
authenticated surface, and the endpoint it reads returns two fixed words rather
than anything drawn from the library.

The validated listener identity selects transport: HTTP and proxied HTTPS use
loopback HTTP; direct HTTPS pins the configured certificate and checks the
canonical hostname. The probe never reads a credential or disables TLS checks.
"""

from __future__ import annotations

import socket
import ssl
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlsplit

from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.settings_authority import load_review_bootstrap

# Kept equal to the paths and port `container.py` fixes, without importing it:
# that module reaches the composition root and so loads uvicorn and every
# adapter, 2.2s of import the probe would pay on every interval to read two
# constants. `tests/unit/test_container_health.py` fails if the two drift.
CONFIG = Path("/data/config.json")
BOOTSTRAP = Path("/run/secrets/review-bootstrap.json")
HOST = "127.0.0.1"
PORT = 8443
ROUTE = "/health/ready"
TIMEOUT_SECONDS = 5.0
HEALTHY = "ready"

# The response is a status line, a handful of headers and one short word. The
# bound is what keeps a wedged or hostile responder from growing the read.
_RESPONSE_LIMIT_BYTES = 4096


def _status(payload: bytes) -> int | None:
    """The status code of an HTTP response, or `None` if it is not one."""

    line = payload.partition(b"\r\n")[0]
    fields = line.split(b" ")
    if len(fields) < 2 or not fields[0].startswith(b"HTTP/"):
        return None
    try:
        return int(fields[1])
    except ValueError:
        return None


def check(
    config_path: Path = CONFIG,
    *,
    bootstrap_path: Path = BOOTSTRAP,
    host: str = HOST,
    port: int = PORT,
    timeout: float = TIMEOUT_SECONDS,
) -> str:
    """Return `HEALTHY`, or a reason code naming why the probe failed."""

    try:
        configuration = load_configuration_file(config_path)
        review = configuration.review
        if review is None:
            return "review_surface_unconfigured"
        canonical_origin = review.canonical_origin
        tls_certificate_file = review.tls_certificate_file
    except FileNotFoundError:
        try:
            bootstrap = load_review_bootstrap(bootstrap_path)
        except ValueError:
            return "configuration_unavailable"
        canonical_origin = bootstrap.canonical_origin
        tls_certificate_file = bootstrap.tls_certificate_file
    except ValueError:
        # `ConfigurationError` is a `ValueError`; an unreadable or invalid
        # document is reported as one either way.
        return "configuration_unavailable"
    origin = urlsplit(canonical_origin)
    if origin.hostname is None:
        return "canonical_origin_unusable"
    try:
        context = (
            ssl.create_default_context(cafile=tls_certificate_file)
            if tls_certificate_file is not None
            else None
        )
    except (OSError, ValueError):
        return "tls_identity_unavailable"
    request = (
        f"GET {ROUTE} HTTP/1.1\r\n"
        f"Host: {origin.netloc}\r\n"
        "Connection: close\r\n"
        "Accept: text/plain\r\n"
        "\r\n"
    ).encode("ascii")
    payload = b""
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with (
                context.wrap_socket(raw, server_hostname=origin.hostname)
                if context is not None
                else nullcontext(raw)
            ) as stream:
                stream.sendall(request)
                while len(payload) < _RESPONSE_LIMIT_BYTES:
                    block = stream.recv(_RESPONSE_LIMIT_BYTES - len(payload))
                    if not block:
                        break
                    payload += block
    except (OSError, ValueError):
        # `ssl.SSLError` and `socket.timeout` are both `OSError`, so a refused
        # connection, an expired or unpinned certificate, and a name the
        # certificate does not carry all land here.
        return "unreachable"
    status = _status(payload)
    if status is None:
        return "malformed_response"
    return HEALTHY if status == 200 else f"not_ready_{status}"


def main() -> None:
    reason = check()
    if reason != HEALTHY:
        # Docker retains the last output beside the health status, so this is
        # what `docker inspect` shows an operator. It is a fixed reason code.
        print(reason)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
