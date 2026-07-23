"""Closed, credential-free acquisition of one externally declared cover URL."""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

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


def fetch_remote_cover(url: str, *, deadline: float | None = None, client: httpx.Client | None = None) -> RemoteCover:
    """One no-retry request chain; every redirect is revalidated before use."""
    own = client is None
    active = client or httpx.Client(trust_env=False, follow_redirects=False, headers={})
    current = url
    try:
        for _ in range(4):
            if deadline is not None and time.monotonic() >= deadline:
                raise RemoteCoverTimeout("aggregate deadline elapsed")
            host, _ = _public_https(current)
            timeout = httpx.Timeout(5.0, connect=5.0, read=5.0, write=5.0, pool=5.0)
            with active.stream("GET", current, headers={"Host": host, "Accept-Encoding": "identity"}, timeout=timeout) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise RemoteCoverError("remote redirect is invalid")
                    current = str(response.url.join(location)); continue
                if response.status_code != 200:
                    raise RemoteCoverError("remote cover unavailable")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].lower().strip()
                if media_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise RemoteCoverError("remote cover format is unsupported")
                chunks: list[bytes] = []; size = 0
                for chunk in response.iter_bytes():
                    if deadline is not None and time.monotonic() >= deadline: raise RemoteCoverTimeout("aggregate deadline elapsed")
                    size += len(chunk)
                    if size > MAX_BYTES: raise RemoteCoverError("remote cover is oversized")
                    chunks.append(chunk)
                if not chunks: raise RemoteCoverError("remote cover unavailable")
                return RemoteCover(current, media_type, b"".join(chunks))
        raise RemoteCoverError("remote redirect limit exceeded")
    except httpx.TimeoutException as error:
        raise RemoteCoverTimeout("remote cover timed out") from error
    except httpx.HTTPError as error:
        raise RemoteCoverError("remote cover unavailable") from error
    finally:
        if own: active.close()


__all__ = ["RemoteCover", "RemoteCoverError", "RemoteCoverTimeout", "fetch_remote_cover"]
