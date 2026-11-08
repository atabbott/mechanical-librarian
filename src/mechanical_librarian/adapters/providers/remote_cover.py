"""Closed, credential-free acquisition of one externally declared cover URL."""

from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import cast
from urllib.parse import SplitResult, urlsplit

from mechanical_librarian.application.metadata_report import CoverRedirects

# One ``socket.getaddrinfo`` row: (family, kind, protocol, canonical name,
# socket address).  Only the address element is read here.
_AddressInfo = tuple[
    socket.AddressFamily,
    socket.SocketKind,
    int,
    str,
    tuple[str, int] | tuple[str, int, int, int],
]

MAX_BYTES = 8 * 1024 * 1024
_MAX_HEADER_BYTES = 65_536
_MAX_CHUNK_LINE_BYTES = 1_024
_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_STATUS = re.compile(r"^HTTP/1\.[01] ([0-9]{3}) [\x20-\x7e]*$")
# Open Library's closed archive route (security.md › Provider cover admission).
# Every pattern is applied with ``fullmatch`` to the exact header string, so a
# trailing newline or any normalisation difference cannot pass.
_OPEN_LIBRARY_COVER = re.compile(
    r"https://covers\.openlibrary\.org/b/id/([1-9][0-9]{0,9})-L\.jpg\?default=false"
)
_ARCHIVE_SEGMENT = r"([A-Za-z0-9_.-]{1,64})"
_ARCHIVE_DOWNLOAD = re.compile(
    rf"https://archive\.org(?::443)?/download/{_ARCHIVE_SEGMENT}/{_ARCHIVE_SEGMENT}"
    rf"/{_ARCHIVE_SEGMENT}"
)
_ARCHIVE_DATANODE = re.compile(
    r"https://ia[0-9]{1,6}\.us\.archive\.org(?::443)?/view_archive\.php\?([^#%]*)"
)
_ARCHIVE_ITEM_PATH = re.compile(r"/[0-9]{1,4}/items/(.*)")
_UNSAFE_REDIRECT = "unsafe remote redirect"


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
        return type(self)(
            self.aggregate_deadline, min(self.aggregate_deadline, started + 20.0)
        )

    def remaining_phase(self, *, now: float | None = None) -> float:
        current = time.monotonic() if now is None else now
        value = min(
            5.0, self.aggregate_deadline - current, self.attempt_deadline - current
        )
        if value <= 0:
            raise RemoteCoverTimeout("aggregate deadline elapsed")
        return value


def _pinned_address(
    url: str, deadline: float | CoverDeadline | None = None
) -> tuple[str, str, str]:
    _validate_url(url)
    parts = _parts(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.port not in (None, 443)
        or parts.username
        or parts.password
        or parts.fragment
    ):
        raise RemoteCoverError("unsafe remote cover")
    host = parts.hostname
    addresses = _resolve(host, deadline)
    if not addresses or any(
        not ipaddress.ip_address(item[4][0]).is_global for item in addresses
    ):
        raise RemoteCoverError("unsafe remote cover")
    selected = addresses[0][4][0]
    return host, selected, _parts(url).path or "/"


def _resolve(host: str, deadline: float | CoverDeadline | None) -> list[_AddressInfo]:
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
    if (
        type(url) is not str
        or not url
        or any(ord(value) < 0x21 or ord(value) > 0x7E for value in url)
        or "\\" in url
    ):
        raise RemoteCoverError("unsafe remote cover")


def _parts(url: str) -> SplitResult:
    try:
        parts = urlsplit(url)
        _ = parts.hostname, parts.port
        return parts
    except ValueError as error:
        raise RemoteCoverError("unsafe remote cover") from error


def _remaining(deadline: float | CoverDeadline | None) -> float:
    if type(deadline) is CoverDeadline:
        return deadline.remaining_phase()
    # The ``CoverDeadline`` form already returned above, so the remaining
    # non-``None`` form is the plain float deadline.
    value = (
        5.0 if deadline is None else min(5.0, cast(float, deadline) - time.monotonic())
    )
    if value <= 0:
        raise RemoteCoverTimeout("aggregate deadline elapsed")
    return value


def _receive(
    secured: ssl.SSLSocket, maximum: int, deadline: float | CoverDeadline | None
) -> bytes:
    secured.settimeout(_remaining(deadline))
    value = secured.recv(maximum)
    return value


def _response_headers(
    secured: ssl.SSLSocket,
    deadline: float | CoverDeadline | None,
) -> tuple[int, dict[str, str]]:
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        if len(buffer) >= _MAX_HEADER_BYTES:
            raise RemoteCoverError("remote cover headers are oversized")
        chunk = _receive(secured, 1, deadline)
        if not chunk:
            raise RemoteCoverError("remote cover response is invalid")
        buffer.extend(chunk)
    raw_headers = bytes(buffer[:-4])
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
        if (
            not separator
            or not name
            or lowered in headers
            or any(
                character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                for character in lowered
            )
            or value != value.strip(" \t")
        ):
            raise RemoteCoverError("remote cover response is invalid")
        headers[lowered] = value
    return int(match.group(1)), headers


def _response_body(
    secured: ssl.SSLSocket,
    headers: dict[str, str],
    deadline: float | CoverDeadline | None,
) -> bytes:
    body: bytes | bytearray
    transfer = headers.get("transfer-encoding")
    length_text = headers.get("content-length")
    if transfer is not None:
        if transfer.lower() != "chunked" or length_text is not None:
            raise RemoteCoverError("remote cover response is invalid")
        body = _chunked_body(secured, deadline)
    elif length_text is not None:
        if (
            not length_text.isascii()
            or not length_text.isdigit()
            or len(length_text) > 10
        ):
            raise RemoteCoverError("remote cover response is invalid")
        length = int(length_text)
        if length > MAX_BYTES:
            raise RemoteCoverError("remote cover is oversized")
        body = bytearray()
        while len(body) < length:
            chunk = _receive(secured, min(65_536, length - len(body)), deadline)
            if not chunk:
                raise RemoteCoverError("remote cover response is invalid")
            body.extend(chunk)
        body = bytes(body)
    else:
        # Close-delimited bodies have no bytes buffered by header parsing.
        # Stream from the TLS socket until peer close under the same caps.
        body = bytearray()
        while True:
            if len(body) > MAX_BYTES:
                raise RemoteCoverError("remote cover is oversized")
            chunk = _receive(secured, min(65_536, MAX_BYTES + 1 - len(body)), deadline)
            if not chunk:
                break
            body.extend(chunk)
        body = bytes(body)
    return body


def _framing_line(
    secured: ssl.SSLSocket,
    buffer: bytearray,
    deadline: float | CoverDeadline | None,
) -> bytes:
    while b"\r\n" not in buffer:
        remaining = _MAX_CHUNK_LINE_BYTES - len(buffer)
        if remaining <= 0:
            raise RemoteCoverError("remote cover response is invalid")
        chunk = _receive(secured, min(remaining, 4096), deadline)
        if not chunk:
            raise RemoteCoverError("remote cover response is invalid")
        buffer.extend(chunk)
    line, remainder = bytes(buffer).split(b"\r\n", 1)
    buffer[:] = remainder
    return line


def _chunked_body(
    secured: ssl.SSLSocket, deadline: float | CoverDeadline | None
) -> bytes:
    buffer = bytearray()
    output = bytearray()
    while True:
        line = _framing_line(secured, buffer, deadline)
        size_text = line.split(b";", 1)[0]
        if (
            not size_text
            or len(size_text) > 8
            or any(value not in b"0123456789abcdefABCDEF" for value in size_text)
        ):
            raise RemoteCoverError("remote cover response is invalid")
        size = int(size_text, 16)
        if size == 0:
            if _framing_line(secured, buffer, deadline):
                raise RemoteCoverError("remote cover response is invalid")
            return bytes(output)
        if len(output) + size > MAX_BYTES:
            raise RemoteCoverError("remote cover is oversized")
        while len(buffer) < size + 2:
            chunk = _receive(secured, min(65_536, size + 2 - len(buffer)), deadline)
            if not chunk:
                raise RemoteCoverError("remote cover response is invalid")
            buffer.extend(chunk)
        if buffer[size : size + 2] != b"\r\n":
            raise RemoteCoverError("remote cover response is invalid")
        output.extend(buffer[:size])
        del buffer[: size + 2]


def _admitted_media_type(headers: dict[str, str]) -> str:
    get = headers.get
    encoding = get("content-encoding")
    if encoding is not None and encoding.lower() != "identity":
        raise RemoteCoverError("remote cover encoding is unsupported")
    media_type = get("content-type")
    if media_type is None or media_type.lower() not in _IMAGE_MEDIA_TYPES:
        raise RemoteCoverError("remote cover format is unsupported")
    return media_type.lower()


def _raw_archive_location(location: str) -> None:
    """Refuse anything but printable, absolute, lowercase ``https://`` text.

    This runs on the exact header value before any join, split or decoding:
    ``urljoin`` and ``urlsplit`` silently drop a tab or line feed, so checking
    their output would check a different string from the one the server sent.
    """
    if (
        any(ord(character) < 0x21 or ord(character) > 0x7E for character in location)
        or "\\" in location
        or "@" in location
        or not location.startswith("https://")
    ):
        raise RemoteCoverError(_UNSAFE_REDIRECT)


def _archive_download(location: str, names: tuple[str, str]) -> tuple[str, str, str]:
    """Admit hop 1, ``archive.org/download/<item>/<zip>/<file>``, or refuse."""
    _raw_archive_location(location)
    match = _ARCHIVE_DOWNLOAD.fullmatch(location)
    if match is None:
        raise RemoteCoverError(_UNSAFE_REDIRECT)
    item, archive, file = match.groups()
    if {item, archive, file} & {".", ".."} or file not in names:
        raise RemoteCoverError(_UNSAFE_REDIRECT)
    return item, archive, file


def _archive_datanode(location: str, download: tuple[str, str, str]) -> None:
    """Admit hop 2, a datanode's ``view_archive.php`` bound to hop 1, or refuse.

    The query is split on ``&`` only, never with ``parse_qs``: no ``;``
    separator, no ``+`` to space and no percent-decoding.
    """
    _raw_archive_location(location)
    match = _ARCHIVE_DATANODE.fullmatch(location)
    if match is None:
        raise RemoteCoverError(_UNSAFE_REDIRECT)
    values: dict[str, str] = {}
    for pair in match.group(1).split("&"):
        key, separator, value = pair.partition("=")
        if (
            not separator
            or "=" in value
            or key not in {"archive", "file"}
            or key in values
        ):
            raise RemoteCoverError(_UNSAFE_REDIRECT)
        values[key] = value
    item, archive, file = download
    path = _ARCHIVE_ITEM_PATH.fullmatch(values.get("archive", ""))
    if (
        len(values) != 2
        or path is None
        or path.group(1) != f"{item}/{archive}"
        or values["file"] != file
    ):
        raise RemoteCoverError(_UNSAFE_REDIRECT)


def _open_library_archive_cover(
    url: str, deadline: float | CoverDeadline | None, user_agent: str | None
) -> RemoteCover:
    """Follow only Open Library's fixed route, each hop bound to the cover id.

    Position 0 is the constructed ``covers.openlibrary.org`` URL, position 1
    ``archive.org`` and position 2 an ``ia<N>.us.archive.org`` datanode. Only
    positions 0 and 1 may redirect, each to the next position. Every refusal
    happens before any DNS lookup or socket for the refused target, and only
    position 0 carries the User-Agent, which holds the owner's contact email.
    """
    match = _OPEN_LIBRARY_COVER.fullmatch(url) if type(url) is str else None
    if match is None or int(match.group(1)) > 2_147_483_647:
        raise RemoteCoverError("unsafe remote cover")
    if type(deadline) is not CoverDeadline:
        raise RemoteCoverError("remote cover request is invalid")
    requested = int(match.group(1))
    names = (f"{requested}-L.jpg", f"{requested:010d}-L.jpg")
    current = url
    download: tuple[str, str, str] | None = None
    for position in range(3):
        deadline.remaining_phase()
        status, headers, content = _pinned_fetch(
            current,
            deadline,
            user_agent if position == 0 else None,
        )
        if status in _REDIRECT_STATUSES:
            if position == 2:
                raise RemoteCoverError(_UNSAFE_REDIRECT)
            location = headers.get("location")
            if not location:
                raise RemoteCoverError("remote cover unavailable")
            if download is None:
                download = _archive_download(location, names)
            else:
                _archive_datanode(location, download)
            current = location
            continue
        if status != 200:
            raise RemoteCoverError("remote cover unavailable")
        media_type = _admitted_media_type(headers)
        if not content:
            raise RemoteCoverError("remote cover unavailable")
        return RemoteCover(current, media_type, content)
    raise RemoteCoverError(_UNSAFE_REDIRECT)


def _pinned_fetch(
    url: str,
    deadline: float | CoverDeadline | None,
    user_agent: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    host, address, path = _pinned_address(url, deadline)
    query = _parts(url).query
    if query:
        path += "?" + query
    raw = socket.create_connection((address, 443), timeout=_remaining(deadline))
    try:
        if raw.getpeername()[0] != address:
            raise RemoteCoverError("remote cover unavailable")
        context = ssl.create_default_context()
        with context.wrap_socket(raw, server_hostname=host) as secured:
            secured.settimeout(_remaining(deadline))
            agent = "" if user_agent is None else f"User-Agent: {user_agent}\r\n"
            request = f"GET {path} HTTP/1.1\r\nHost: {host}\r\n{agent}Accept-Encoding: identity\r\nConnection: close\r\n\r\n".encode(
                "ascii"
            )
            secured.sendall(request)
            status, headers = _response_headers(secured, deadline)
            if status != 200:
                return status, headers, b""
            _admitted_media_type(headers)
            return status, headers, _response_body(secured, headers, deadline)
    finally:
        try:
            raw.close()
        except OSError:
            pass


def fetch_remote_cover(
    url: str,
    *,
    deadline: float | CoverDeadline | None = None,
    redirects: CoverRedirects = CoverRedirects.NONE,
    user_agent: str | None = None,
) -> RemoteCover:
    """One no-retry request; only Open Library's fixed route follows redirects.

    `docs/architecture/security.md` › Provider cover admission: every other
    provider's cover is one pinned request, and any redirect refuses it.
    """
    if type(redirects) is not CoverRedirects or (
        user_agent is not None
        and (
            type(user_agent) is not str
            or not 1 <= len(user_agent) <= 512
            or user_agent.strip() != user_agent
            or any(
                ord(character) < 0x20 or ord(character) > 0x7E
                for character in user_agent
            )
        )
    ):
        raise RemoteCoverError("remote cover request is invalid")
    try:
        if redirects is CoverRedirects.OPEN_LIBRARY_ARCHIVE:
            return _open_library_archive_cover(url, deadline, user_agent)
        if type(deadline) is CoverDeadline and deadline.remaining_phase() <= 0:
            raise RemoteCoverTimeout("aggregate deadline elapsed")
        if type(deadline) is float and time.monotonic() >= deadline:
            raise RemoteCoverTimeout("aggregate deadline elapsed")
        status, headers, content = _pinned_fetch(url, deadline, user_agent)
        if status != 200:
            raise RemoteCoverError("remote cover unavailable")
        media_type = _admitted_media_type(headers)
        if not content:
            raise RemoteCoverError("remote cover unavailable")
        return RemoteCover(url, media_type, content)
    except (TimeoutError, socket.timeout) as error:
        raise RemoteCoverTimeout("remote cover timed out") from error
    except (OSError, ssl.SSLError, UnicodeError) as error:
        raise RemoteCoverError("remote cover unavailable") from error


__all__ = [
    "RemoteCover",
    "RemoteCoverError",
    "RemoteCoverTimeout",
    "fetch_remote_cover",
]
