"""Closed, credential-free acquisition of one externally declared cover URL."""

from __future__ import annotations

import ipaddress
import socket
import ssl
import time
import threading
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urljoin

import httpx


MAX_BYTES = 8 * 1024 * 1024
_MAX_HEADER_BYTES = 65_536
_STATUS = re.compile(r"^HTTP/1\.[01] ([0-9]{3}) [\x20-\x7e]*$")


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
    _validate_url(url)
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


def _pinned_address(url: str, deadline: float | CoverDeadline | None = None) -> tuple[str, str, str]:
    _validate_url(url)
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443) or parts.username or parts.password or parts.fragment:
        raise RemoteCoverError("unsafe remote cover")
    host = parts.hostname
    addresses = _resolve(host, deadline)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise RemoteCoverError("unsafe remote cover")
    selected = addresses[0][4][0]
    return host, selected, urlsplit(url).path or "/"


def _resolve(host: str, deadline: float | CoverDeadline | None) -> list[tuple[object, ...]]:
    result: list[object] = []
    def resolve() -> None:
        try:
            result.append(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except BaseException as error:
            result.append(error)
    worker = threading.Thread(target=resolve, daemon=True)
    worker.start()
    worker.join(_remaining(deadline))
    if worker.is_alive():
        raise RemoteCoverTimeout("remote cover timed out")
    if not result or isinstance(result[0], BaseException):
        raise RemoteCoverError("remote cover unavailable")
    return result[0]  # type: ignore[return-value]


def _validate_url(url: str) -> None:
    if type(url) is not str or not url or any(ord(value) < 0x21 or ord(value) > 0x7E for value in url) or "\\" in url:
        raise RemoteCoverError("unsafe remote cover")


def _remaining(deadline: float | CoverDeadline | None) -> float:
    if type(deadline) is CoverDeadline:
        return deadline.remaining_phase()
    value = 5.0 if deadline is None else min(5.0, deadline - time.monotonic())
    if value <= 0:
        raise RemoteCoverTimeout("aggregate deadline elapsed")
    return value


def _receive(secured: ssl.SSLSocket, maximum: int, deadline: float | CoverDeadline | None) -> bytes:
    secured.settimeout(_remaining(deadline))
    value = secured.recv(maximum)
    return value


def _response(secured: ssl.SSLSocket, deadline: float | CoverDeadline | None) -> tuple[int, dict[str, str], bytes]:
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        if len(buffer) >= _MAX_HEADER_BYTES:
            raise RemoteCoverError("remote cover headers are oversized")
        chunk = _receive(secured, min(4096, _MAX_HEADER_BYTES + 1 - len(buffer)), deadline)
        if not chunk:
            raise RemoteCoverError("remote cover response is invalid")
        buffer.extend(chunk)
    raw_headers, body_start = bytes(buffer).split(b"\r\n\r\n", 1)
    try:
        lines = raw_headers.decode("ascii", "strict").split("\r\n")
    except UnicodeError as error:
        raise RemoteCoverError("remote cover response is invalid") from error
    match = _STATUS.fullmatch(lines[0])
    if match is None:
        raise RemoteCoverError("remote cover response is invalid")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        lowered = name.lower()
        if not separator or not name or lowered in headers or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in lowered) or value != value.strip(" \t"):
            raise RemoteCoverError("remote cover response is invalid")
        headers[lowered] = value
    transfer = headers.get("transfer-encoding")
    length_text = headers.get("content-length")
    if transfer is not None:
        if transfer.lower() != "chunked" or length_text is not None:
            raise RemoteCoverError("remote cover response is invalid")
        body = _chunked_body(secured, body_start, deadline)
    elif length_text is not None:
        if not length_text.isascii() or not length_text.isdigit() or len(length_text) > 10:
            raise RemoteCoverError("remote cover response is invalid")
        length = int(length_text)
        if length > MAX_BYTES or len(body_start) > length:
            raise RemoteCoverError("remote cover is oversized")
        body = bytearray(body_start)
        while len(body) < length:
            chunk = _receive(secured, min(65_536, length - len(body)), deadline)
            if not chunk:
                raise RemoteCoverError("remote cover response is invalid")
            body.extend(chunk)
        body = bytes(body)
    else:
        body = bytearray(body_start)
        while True:
            if len(body) > MAX_BYTES:
                raise RemoteCoverError("remote cover is oversized")
            chunk = _receive(secured, min(65_536, MAX_BYTES + 1 - len(body)), deadline)
            if not chunk:
                break
            body.extend(chunk)
        body = bytes(body)
    return int(match.group(1)), headers, body


def _chunked_body(secured: ssl.SSLSocket, initial: bytes, deadline: float | CoverDeadline | None) -> bytes:
    buffer = bytearray(initial); output = bytearray()
    def fill(marker: bytes) -> None:
        while marker not in buffer:
            chunk = _receive(secured, 4096, deadline)
            if not chunk: raise RemoteCoverError("remote cover response is invalid")
            buffer.extend(chunk)
    while True:
        fill(b"\r\n")
        line, remainder = bytes(buffer).split(b"\r\n", 1); buffer[:] = remainder
        size_text = line.split(b";", 1)[0]
        if not size_text or len(size_text) > 8 or any(value not in b"0123456789abcdefABCDEF" for value in size_text):
            raise RemoteCoverError("remote cover response is invalid")
        size = int(size_text, 16)
        if size == 0:
            fill(b"\r\n")
            if bytes(buffer).split(b"\r\n", 1)[0]: raise RemoteCoverError("remote cover response is invalid")
            return bytes(output)
        if len(output) + size > MAX_BYTES: raise RemoteCoverError("remote cover is oversized")
        while len(buffer) < size + 2:
            chunk = _receive(secured, min(65_536, size + 2 - len(buffer)), deadline)
            if not chunk: raise RemoteCoverError("remote cover response is invalid")
            buffer.extend(chunk)
        if buffer[size:size + 2] != b"\r\n": raise RemoteCoverError("remote cover response is invalid")
        output.extend(buffer[:size]); del buffer[:size + 2]


def _pinned_fetch(url: str, deadline: float | CoverDeadline | None) -> tuple[int, dict[str, str], bytes]:
    host, address, path = _pinned_address(url, deadline)
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
            return _response(secured, deadline)
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
    except (httpx.HTTPError, OSError, ssl.SSLError, UnicodeError) as error:
        raise RemoteCoverError("remote cover unavailable") from error
    finally:
        if own: active.close()


__all__ = ["RemoteCover", "RemoteCoverError", "RemoteCoverTimeout", "fetch_remote_cover"]
