import socket
import time
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.providers import remote_cover
from mechanical_librarian.adapters.providers.remote_cover import (
    RemoteCoverError,
    RemoteCoverTimeout,
    _pinned_address,
    fetch_remote_cover,
)


class _FakeSocket:
    def __init__(self, headers: bytes, body: bytes = b"") -> None:
        self.headers = bytearray(headers)
        self.body = bytearray(body)
        self.body_bytes_read = 0
        self.maximum_recv = 0
        self.sent = bytearray()

    def __enter__(self) -> "_FakeSocket":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def close(self) -> None:
        return None

    def getpeername(self) -> tuple[str, int]:
        return ("8.8.8.8", 443)

    def recv(self, maximum: int) -> bytes:
        self.maximum_recv = max(self.maximum_recv, maximum)
        source = self.headers if self.headers else self.body
        if not source:
            return b""
        size = min(maximum, len(source))
        value = bytes(source[:size])
        del source[:size]
        if source is self.body:
            self.body_bytes_read += size
        return value

    def sendall(self, value: bytes) -> None:
        self.sent.extend(value)

    def settimeout(self, _value: float) -> None:
        return None


class _PinnedPeerSocket:
    """Expose one connected local test peer as the pinned TLS transport."""

    def __init__(self, value: socket.socket) -> None:
        self._value = value

    def __enter__(self) -> "_PinnedPeerSocket":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def close(self) -> None:
        self._value.close()

    def getpeername(self) -> tuple[str, int]:
        return ("8.8.8.8", 443)

    def recv(self, maximum: int) -> bytes:
        return self._value.recv(maximum)

    def sendall(self, value: bytes) -> None:
        self._value.sendall(value)

    def settimeout(self, value: float) -> None:
        self._value.settimeout(value)


class _FakeContext:
    def wrap_socket(self, raw: _FakeSocket, *, server_hostname: str) -> _FakeSocket:
        if server_hostname != "example.com":
            raise AssertionError(server_hostname)
        return raw


def test_rejects_non_https_and_private_address() -> None:
    with pytest.raises(RemoteCoverError):
        _pinned_address("http://example.com/a.jpg")
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
        with pytest.raises(RemoteCoverError):
            _pinned_address("https://example.com/a.jpg")


def test_accepts_public_https_443_only() -> None:
    with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
        assert ("example.com", "8.8.8.8", "/a.jpg") == _pinned_address(
            "https://example.com/a.jpg"
        )
    with pytest.raises(RemoteCoverError):
        _pinned_address("https://example.com:444/a.jpg")


def test_rejects_mixed_and_reserved_dns_answers_and_url_smuggling() -> None:
    with patch(
        "socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("192.168.1.1", 443)),
        ],
    ):
        with pytest.raises(RemoteCoverError):
            _pinned_address("https://example.com/a.jpg")
    for value in (
        "https://user@example.com/a.jpg",
        "https://example.com/a.jpg#fragment",
    ):
        with pytest.raises(RemoteCoverError):
            _pinned_address(value)


@pytest.mark.parametrize(
    "unresolvable",
    ("https://example.com:99999/cover.png", "https://[::1/cover.png"),
    ids=("port_out_of_range", "malformed_ipv6"),
)
def test_invalid_port_and_malformed_ipv6_fail_before_dns(unresolvable: str) -> None:
    with patch("socket.getaddrinfo") as lookup:
        with pytest.raises(RemoteCoverError, match="unsafe remote cover"):
            _pinned_address(unresolvable)
        lookup.assert_not_called()
    for value in (
        "https://example.com/a b.jpg",
        "https://example.com/a\x00b.jpg",
        "https://example.com/café.jpg",
        "https://example.com/a\\b.jpg",
    ):
        with pytest.raises(RemoteCoverError):
            _pinned_address(value)


def test_fetch_uses_only_explicit_host_and_identity_headers() -> None:
    secured = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n",
        b"cover",
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        value = fetch_remote_cover("https://example.com/cover.png")
    assert b"cover" == value.content
    request = bytes(secured.sent).decode("ascii")
    assert "Host: example.com\r\n" in request
    assert "Accept-Encoding: identity\r\n" in request
    assert "Authorization:" not in request
    assert "Cookie:" not in request
    assert "Referer:" not in request
    assert "User-Agent:" not in request


@pytest.mark.parametrize(
    "rejected_agent",
    ("", "unsafe\r\nAuthorization: value", "é"),
    ids=("empty", "header_injection", "non_ascii"),
)
def test_fetch_can_use_one_validated_identified_user_agent(
    rejected_agent: str,
) -> None:
    secured = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n",
        b"cover",
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        fetch_remote_cover(
            "https://example.com/cover.png",
            user_agent="Mechanical-Librarian (operator@example.invalid)",
        )
    assert "User-Agent: Mechanical-Librarian (operator@example.invalid)\r\n" in bytes(
        secured.sent
    ).decode("ascii")
    with pytest.raises(RemoteCoverError):
        fetch_remote_cover(
            "https://example.com/cover.png",
            user_agent=rejected_agent,
        )


def test_rejects_content_encoding_and_redirect_limit() -> None:
    encoded = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Encoding: gzip\r\nContent-Length: 1\r\n\r\n",
        b"x",
    )
    redirects = [
        _FakeSocket(
            b"HTTP/1.1 302 Found\r\nLocation: /again\r\nContent-Length: 0\r\n\r\n"
        )
        for _ in range(4)
    ]
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        with (
            patch("socket.create_connection", return_value=encoded),
            pytest.raises(RemoteCoverError),
        ):
            fetch_remote_cover("https://example.com/cover.png")
        with (
            patch("socket.create_connection", side_effect=redirects),
            pytest.raises(RemoteCoverError),
        ):
            fetch_remote_cover("https://example.com/cover.png")


def test_redirects_can_be_rejected_after_one_pinned_request() -> None:
    redirect = _FakeSocket(
        b"HTTP/1.1 302 Found\r\nLocation: /again\r\nContent-Length: 0\r\n\r\n"
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=redirect) as connect,
        patch("ssl.create_default_context", return_value=_FakeContext()),
        pytest.raises(RemoteCoverError),
    ):
        fetch_remote_cover(
            "https://example.com/cover.png",
            allow_redirects=False,
        )
    connect.assert_called_once()


def test_elapsed_aggregate_deadline_permits_no_dns_or_request() -> None:
    with patch("socket.getaddrinfo") as lookup:
        with pytest.raises(RemoteCoverTimeout):
            fetch_remote_cover(
                "https://example.com/cover.png", deadline=time.monotonic() - 1
            )
    lookup.assert_not_called()


def test_production_path_connects_to_one_validated_address_without_reresolving() -> (
    None
):
    with (
        patch(
            "socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]
        ) as lookup,
        patch("socket.create_connection", side_effect=OSError),
    ):
        with pytest.raises(RemoteCoverError):
            fetch_remote_cover("https://example.com/cover.png")
    lookup.assert_called_once_with("example.com", 443, type=socket.SOCK_STREAM)


@pytest.mark.parametrize(
    ("headers", "body"),
    (
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 4\r\n\r\n",
            b"html",
        ),
        (b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\n", b"missing"),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png; charset=binary\r\nContent-Length: 6\r\n\r\n",
            b"params",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Encoding: gzip\r\nContent-Length: 4\r\n\r\n",
            b"gzip",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Encoding:\r\nContent-Length: 5\r\n\r\n",
            b"empty",
        ),
        (
            b"HTTP/1.1 404 Not Found\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n",
            b"error",
        ),
    ),
    ids=(
        "html_content_type",
        "absent_content_type",
        "parameterized_content_type",
        "gzip_content_encoding",
        "empty_content_encoding",
        "not_found_status",
    ),
)
def test_production_path_gates_headers_before_reading_rejected_bodies(
    headers: bytes, body: bytes
) -> None:
    secured = _FakeSocket(headers, body)
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
        pytest.raises(RemoteCoverError),
    ):
        fetch_remote_cover("https://example.com/cover.png")
    assert 0 == secured.body_bytes_read


def test_production_path_redirect_does_not_read_body_and_valid_response_does() -> None:
    redirect = _FakeSocket(
        b"HTTP/1.1 302 Found\r\nLocation: /final.png\r\nContent-Length: 8\r\n\r\n",
        b"redirect",
    )
    valid = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n",
        b"cover",
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", side_effect=[redirect, valid]),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        result = fetch_remote_cover("https://example.com/cover.png")
    assert b"cover" == result.content
    assert 0 == redirect.body_bytes_read
    assert 5 == valid.body_bytes_read


def test_production_path_reads_close_delimited_image_body() -> None:
    secured = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n",
        b"close-delimited-cover",
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
    ):
        result = fetch_remote_cover("https://example.com/cover.png")
    assert b"close-delimited-cover" == result.content
    assert len(result.content) == secured.body_bytes_read


def test_pinned_fetch_times_out_when_a_peer_stalls_mid_body() -> None:
    client, peer = socket.socketpair()
    secured = _PinnedPeerSocket(client)
    try:
        peer.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 9\r\n\r\ncover"
        )
        with (
            patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
            patch("socket.create_connection", return_value=secured),
            patch("ssl.create_default_context", return_value=_FakeContext()),
            pytest.raises(RemoteCoverTimeout),
        ):
            fetch_remote_cover(
                "https://example.com/cover.png", deadline=time.monotonic() + 0.1
            )
    finally:
        peer.close()


def test_close_delimited_body_obeys_cap_and_deadline() -> None:
    oversized = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n",
        b"x" * (remote_cover.MAX_BYTES + 1),
    )
    with pytest.raises(RemoteCoverError):
        remote_cover._response_body(oversized, {}, time.monotonic() + 1)
    with pytest.raises(RemoteCoverTimeout):
        remote_cover._response_body(
            _FakeSocket(b"", b"cover"), {}, time.monotonic() - 1
        )


def test_native_socket_timeout_is_timeout_disposition() -> None:
    with patch.object(remote_cover, "_pinned_fetch", side_effect=socket.timeout()):
        with pytest.raises(RemoteCoverTimeout):
            fetch_remote_cover("https://example.com/cover.png")


def test_production_path_bounds_unterminated_chunk_framing() -> None:
    secured = _FakeSocket(
        b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"A" * (remote_cover._MAX_CHUNK_LINE_BYTES + 64),
    )
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
        patch("socket.create_connection", return_value=secured),
        patch("ssl.create_default_context", return_value=_FakeContext()),
        pytest.raises(RemoteCoverError),
    ):
        fetch_remote_cover("https://example.com/cover.png")
    assert remote_cover._MAX_CHUNK_LINE_BYTES == secured.body_bytes_read
    assert secured.maximum_recv <= remote_cover._MAX_CHUNK_LINE_BYTES
