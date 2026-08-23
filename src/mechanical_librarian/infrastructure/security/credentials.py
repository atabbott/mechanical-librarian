"""Private-file credentials and least-authority HTTP clients."""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from collections.abc import Generator
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from mechanical_librarian.application.metadata_report import CredentialError


def read_secret(path_value: str) -> str:
    try:
        path = Path(path_value)
        if not path.is_absolute() or path.name in {"", ".", ".."}:
            raise CredentialError("credential unavailable")
        directory_flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        parent = os.open(path.parent, directory_flags)
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent,
            )
            try:
                before = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != os.geteuid()
                    or before.st_nlink != 1
                    or before.st_mode & 0o077
                    or not 1 <= before.st_size <= 2048
                ):
                    raise CredentialError("credential unavailable")
                payload = os.read(descriptor, before.st_size + 1)
                after = os.fstat(descriptor)
                if len(payload) != before.st_size or (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                    raise CredentialError("credential unavailable")
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
        text = payload.decode("ascii", "strict")
        if (
            text.strip() != text
            or not text
            or text.lower().startswith("bearer")
            or any(not 0x21 <= ord(value) <= 0x7E for value in text)
        ):
            raise CredentialError("credential unavailable")
        return text
    except CredentialError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise CredentialError("credential unavailable") from None


def target_client(
    endpoint: str,
    secret_file: str,
    timeout_seconds: int,
    *,
    plain_http: bool = False,
) -> httpx.Client:
    return target_client_token(
        endpoint, read_secret(secret_file), timeout_seconds, plain_http=plain_http
    )


def target_client_token(
    endpoint: str,
    token: str,
    timeout_seconds: int,
    *,
    plain_http: bool = False,
) -> httpx.Client:
    if not _target_origin_allowed(endpoint, plain_http=plain_http):
        raise CredentialError("target endpoint is invalid")
    return httpx.Client(
        base_url=endpoint.rstrip("/"),
        auth=_TargetAuth(endpoint.rstrip("/"), token),
        follow_redirects=False,
        trust_env=False,
        verify=True,
        timeout=httpx.Timeout(float(timeout_seconds)),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    )


def hardcover_client(
    secret_file: str,
    timeout_seconds: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    return hardcover_client_token(
        read_secret(secret_file), timeout_seconds, transport=transport
    )


def hardcover_client_token(
    token: str, timeout_seconds: int, *, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        auth=_HardcoverAuth(token),
        follow_redirects=False,
        trust_env=False,
        verify=True,
        timeout=httpx.Timeout(float(timeout_seconds)),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        transport=transport,
    )


def open_library_client(
    contact_email: str,
    timeout_seconds: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    if (
        type(contact_email) is not str
        or not 1 <= len(contact_email) <= 254
        or any(not 0x21 <= ord(value) <= 0x7E for value in contact_email)
    ):
        raise CredentialError("provider contact is invalid")
    return httpx.Client(
        headers={"User-Agent": f"Mechanical-Librarian ({contact_email})"},
        follow_redirects=False,
        trust_env=False,
        verify=True,
        timeout=httpx.Timeout(float(timeout_seconds)),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        transport=transport,
    )


def google_books_client(
    timeout_seconds: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """Construct a credential-free client confined by the adapter's fixed URL."""
    return httpx.Client(
        follow_redirects=False,
        trust_env=False,
        verify=True,
        timeout=httpx.Timeout(float(timeout_seconds)),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
        transport=transport,
    )


class _TargetAuth(httpx.Auth):
    def __init__(self, origin: str, token: str) -> None:
        self._origin = httpx.URL(origin)
        self._token = token

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        same_origin = (
            request.url.scheme == self._origin.scheme
            and request.url.host == self._origin.host
            and request.url.port == self._origin.port
        )
        item = r"/api/items/[^/]+"
        readable = (
            request.url.path in {"/api/libraries", "/api/me"}
            or re.fullmatch(r"/api/libraries/[^/]+/items", request.url.path)
            or re.fullmatch(item, request.url.path)
            or re.fullmatch(item + r"/cover", request.url.path)
        )
        writable = (
            request.method == "PATCH"
            and re.fullmatch(item + r"/media", request.url.path)
        ) or (
            request.method == "POST"
            and re.fullmatch(item + r"/cover", request.url.path)
        )
        if same_origin and ((request.method == "GET" and readable) or writable):
            request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


class _HardcoverAuth(httpx.Auth):
    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        if request.method == "POST" and request.url == httpx.URL(
            "https://api.hardcover.app/v1/graphql"
        ):
            request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


def _target_origin_allowed(value: str, *, plain_http: bool = False) -> bool:
    try:
        parsed = urlsplit(value)
        if (
            parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
            or parsed.hostname is None
        ):
            return False
        if parsed.scheme == "https":
            return True
        if parsed.scheme != "http":
            return False
        # ``plain_http`` is the connection's validated explicit opt-in for a
        # cleartext local endpoint; without it, plain HTTP stays confined to
        # loopback where the transport never leaves the host.
        return plain_http or (
            parsed.hostname == "localhost"
            or ipaddress.ip_address(parsed.hostname).is_loopback
        )
    except ValueError:
        return False


__all__ = [
    "hardcover_client",
    "hardcover_client_token",
    "google_books_client",
    "open_library_client",
    "read_secret",
    "target_client",
    "target_client_token",
]
