"""Closed, credential-free acquisition of one externally declared cover URL."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import http.client
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urljoin

import httpx


MAX_BYTES = 8 * 1024 * 1024


class RemoteCoverError(RuntimeError):
    pass


class RemoteCoverTimeout(RemoteCoverError):
    pass


@dataclass(frozen=True, slots=True)
class RemoteCover:
    url: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class CoverDeadline:
    """One aggregate cover window plus a fresh bounded attempt window."""

    aggregate_deadline: float
    attempt_deadline: float

    @classmethod
    def start(cls, *, now: float | None = None) -> "CoverDeadline":
        started = time.monotonic() if now is None else now
        return cls(started + 60.0, started + 20.0)

    def next_attempt(self, *, now: float | None = None) -> "CoverDeadline":
        started = time.monotonic() if now is None else now
        return type(self)(self.aggregate_deadline, min(self.aggregate_deadline, started + 20.0))

    def remaining_phase(self, *, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        value = min(5.0, self.aggregate_deadline - current, self.attempt_deadline - current)
        if value <= 0:
            raise RemoteCoverTimeout("aggregate deadline elapsed")
        return value


def _public_https(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443) or parts.username or parts.password or parts.fragment:
        raise RemoteCoverError("unsafe remote cover")
    try:
        addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise RemoteCoverError("remote cover unavailable") from error
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise RemoteCoverError("unsafe remote cover")
    return parts.hostname, 443


def _pinned_address(url: str) -> tuple[str, str, str]:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443) or parts.username or parts.password or parts.fragment:
        raise RemoteCoverError("unsafe remote cover")
    host = parts.hostname
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise RemoteCoverError("unsafe remote cover")
    selected = addresses[0][4][0]
    return host, selected, urlsplit(url).path or "/"


def _remaining(deadline: float | CoverDeadline | None) -> float:
    if type(deadline) is CoverDeadline:
        return deadline.remaining_phase()
    value = 5.0 if deadline is None else min(5.0, deadline - time.monotonic())
    if value <= 0:
        raise RemoteCoverTimeout("aggregate deadline elapsed")
    return value


def _pinned_fetch(url: str, deadline: float | CoverDeadline | None) -> tuple[int, dict[str, str], bytes]:
    host, address, path = _pinned_address(url)
    query = urlsplit(url).query
    if query:
        path += "?" + query
    raw = socket.create_connection((address, 443), timeout=_remaining(deadline))
    try:
        if raw.getpeername()[0] != address:
            raise RemoteCoverError("remote cover unavailable")
        context = ssl.create_default_context()
        with context.wrap_socket(raw, server_hostname=host) as secured:
            secured.settimeout(_remaining(deadline))
            request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n".encode("ascii")
            secured.sendall(request)
            response = http.client.HTTPResponse(secured)
            response.begin()
            headers = {key.lower(): value for key, value in response.getheaders()}
            chunks: list[bytes] = []; size = 0
            while True:
                secured.settimeout(_remaining(deadline))
                chunk = response.read(min(65_536, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_BYTES:
                    raise RemoteCoverError("remote cover is oversized")
                chunks.append(chunk)
            return response.status, headers, b"".join(chunks)
    finally:
        try:
            raw.close()
        except OSError:
            pass


def fetch_remote_cover(url: str, *, deadline: float | CoverDeadline | None = None, client: httpx.Client | None = None) -> RemoteCover:
    """One no-retry request chain; every redirect is revalidated before use."""
    own = client is None
    active = client or httpx.Client(trust_env=False, follow_redirects=False, headers={"User-Agent": ""})
    current = url
    try:
        for _ in range(4):
            if type(deadline) is CoverDeadline and deadline.remaining_phase() <= 0:
                raise RemoteCoverTimeout("aggregate deadline elapsed")
            if type(deadline) is float and time.monotonic() >= deadline:
                raise RemoteCoverTimeout("aggregate deadline elapsed")
            if client is not None:
                host, _ = _public_https(current)
            remaining = _remaining(deadline)
            if client is None:
                status, headers, content = _pinned_fetch(current, deadline)
                if status in {301, 302, 303, 307, 308}:
                    location = headers.get("location")
                    if not location: raise RemoteCoverError("remote redirect is invalid")
                    current = urljoin(current, location); continue
                if status != 200: raise RemoteCoverError("remote cover unavailable")
                if headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}: raise RemoteCoverError("remote cover encoding is unsupported")
                media_type = headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if media_type not in {"image/jpeg", "image/png", "image/webp"}: raise RemoteCoverError("remote cover format is unsupported")
                if not content: raise RemoteCoverError("remote cover unavailable")
                return RemoteCover(current, media_type, content)
            timeout = httpx.Timeout(remaining, connect=remaining, read=remaining, write=remaining, pool=remaining)
            with active.stream("GET", current, headers={"Host": host, "Accept-Encoding": "identity"}, timeout=timeout) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise RemoteCoverError("remote redirect is invalid")
                    current = str(response.url.join(location)); continue
                if response.status_code != 200:
                    raise RemoteCoverError("remote cover unavailable")
                if response.headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
                    raise RemoteCoverError("remote cover encoding is unsupported")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if media_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise RemoteCoverError("remote cover format is unsupported")
                chunks: list[bytes] = []; size = 0
                for chunk in response.iter_bytes():
                    _remaining(deadline)
                    size += len(chunk)
                    if size > MAX_BYTES: raise RemoteCoverError("remote cover is oversized")
                    chunks.append(chunk)
                if not chunks: raise RemoteCoverError("remote cover unavailable")
                return RemoteCover(current, media_type, b"".join(chunks))
        raise RemoteCoverError("remote redirect limit exceeded")
    except httpx.TimeoutException as error:
        raise RemoteCoverTimeout("remote cover timed out") from error
    except (httpx.HTTPError, OSError, ssl.SSLError, http.client.HTTPException) as error:
        raise RemoteCoverError("remote cover unavailable") from error
    finally:
        if own: active.close()


__all__ = ["RemoteCover", "RemoteCoverError", "RemoteCoverTimeout", "fetch_remote_cover"]
