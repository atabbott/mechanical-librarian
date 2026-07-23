import unittest
import time
import socket
from unittest.mock import patch

import httpx

from mechanical_librarian.adapters.providers import remote_cover
from mechanical_librarian.adapters.providers.remote_cover import RemoteCoverError, _public_https, fetch_remote_cover
from mechanical_librarian.adapters.providers.remote_cover import RemoteCoverTimeout


class _FakeSocket:
    def __init__(self, headers: bytes, body: bytes = b"") -> None:
        self.headers = bytearray(headers)
        self.body = bytearray(body)
        self.body_bytes_read = 0
        self.maximum_recv = 0

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

    def sendall(self, _value: bytes) -> None:
        return None

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


class RemoteCoverTests(unittest.TestCase):
    def test_rejects_non_https_and_private_address(self) -> None:
        with self.assertRaises(RemoteCoverError):
            _public_https("http://example.com/a.jpg")
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(RemoteCoverError):
                _public_https("https://example.com/a.jpg")

    def test_accepts_public_https_443_only(self) -> None:
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            self.assertEqual(("example.com", 443), _public_https("https://example.com/a.jpg"))
        with self.assertRaises(RemoteCoverError):
            _public_https("https://example.com:444/a.jpg")

    def test_rejects_mixed_and_reserved_dns_answers_and_url_smuggling(self) -> None:
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", ("192.168.1.1", 443))]):
            with self.assertRaises(RemoteCoverError):
                _public_https("https://example.com/a.jpg")
        for value in ("https://user@example.com/a.jpg", "https://example.com/a.jpg#fragment"):
            with self.assertRaises(RemoteCoverError):
                _public_https(value)

    def test_invalid_port_and_malformed_ipv6_fail_before_dns(self) -> None:
        for value in ("https://example.com:99999/cover.png", "https://[::1/cover.png"):
            with self.subTest(value=value), patch("socket.getaddrinfo") as lookup:
                with self.assertRaisesRegex(RemoteCoverError, "unsafe remote cover"):
                    _public_https(value)
                lookup.assert_not_called()
        for value in (
            "https://example.com/a b.jpg",
            "https://example.com/a\x00b.jpg",
            "https://example.com/café.jpg",
            "https://example.com/a\\b.jpg",
        ):
            with self.assertRaises(RemoteCoverError):
                _public_https(value)

    def test_fetch_uses_only_explicit_host_and_identity_headers(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"cover")

        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            value = fetch_remote_cover("https://example.com/cover.png", client=httpx.Client(headers={"User-Agent": ""}, transport=httpx.MockTransport(handler)))
        self.assertEqual(b"cover", value.content)
        self.assertEqual("example.com", seen[0].headers["host"])
        self.assertEqual("identity", seen[0].headers["accept-encoding"])
        self.assertNotIn("authorization", seen[0].headers)
        self.assertNotIn("cookie", seen[0].headers)
        self.assertNotIn("referer", seen[0].headers)
        self.assertFalse(seen[0].headers.get("user-agent"))

    def test_rejects_content_encoding_and_redirect_limit(self) -> None:
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]):
            with self.assertRaises(RemoteCoverError):
                fetch_remote_cover("https://example.com/cover.png", client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "image/png", "content-encoding": "gzip"}, content=b"x"))))
            with self.assertRaises(RemoteCoverError):
                fetch_remote_cover("https://example.com/cover.png", client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "/again"}))))

    def test_elapsed_aggregate_deadline_permits_no_dns_or_request(self) -> None:
        with patch("socket.getaddrinfo") as lookup:
            with self.assertRaises(RemoteCoverTimeout):
                fetch_remote_cover("https://example.com/cover.png", deadline=time.monotonic() - 1)
        lookup.assert_not_called()

    def test_production_path_connects_to_one_validated_address_without_reresolving(self) -> None:
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]) as lookup, patch("socket.create_connection", side_effect=OSError):
            with self.assertRaises(RemoteCoverError):
                fetch_remote_cover("https://example.com/cover.png")
        lookup.assert_called_once_with("example.com", 443, type=socket.SOCK_STREAM)

    def test_production_path_gates_headers_before_reading_rejected_bodies(self) -> None:
        cases = (
            (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 4\r\n\r\n", b"html"),
            (b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\n", b"missing"),
            (b"HTTP/1.1 200 OK\r\nContent-Type: image/png; charset=binary\r\nContent-Length: 6\r\n\r\n", b"params"),
            (b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Encoding: gzip\r\nContent-Length: 4\r\n\r\n", b"gzip"),
            (b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Encoding:\r\nContent-Length: 5\r\n\r\n", b"empty"),
            (b"HTTP/1.1 404 Not Found\r\nContent-Type: image/png\r\nContent-Length: 5\r\n\r\n", b"error"),
        )
        for headers, body in cases:
            with self.subTest(headers=headers):
                secured = _FakeSocket(headers, body)
                with (
                    patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
                    patch("socket.create_connection", return_value=secured),
                    patch("ssl.create_default_context", return_value=_FakeContext()),
                    self.assertRaises(RemoteCoverError),
                ):
                    fetch_remote_cover("https://example.com/cover.png")
                self.assertEqual(0, secured.body_bytes_read)

    def test_production_path_redirect_does_not_read_body_and_valid_response_does(self) -> None:
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
        self.assertEqual(b"cover", result.content)
        self.assertEqual(0, redirect.body_bytes_read)
        self.assertEqual(5, valid.body_bytes_read)

    def test_production_path_reads_close_delimited_image_body(self) -> None:
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
        self.assertEqual(b"close-delimited-cover", result.content)
        self.assertEqual(len(result.content), secured.body_bytes_read)

    def test_pinned_fetch_times_out_when_a_peer_stalls_mid_body(self) -> None:
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
                self.assertRaises(RemoteCoverTimeout),
            ):
                fetch_remote_cover("https://example.com/cover.png", deadline=time.monotonic() + 0.1)
        finally:
            peer.close()

    def test_close_delimited_body_obeys_cap_and_deadline(self) -> None:
        oversized = _FakeSocket(
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\n\r\n",
            b"x" * (remote_cover.MAX_BYTES + 1),
        )
        with self.assertRaises(RemoteCoverError):
            remote_cover._response_body(oversized, {}, time.monotonic() + 1)
        with self.assertRaises(RemoteCoverTimeout):
            remote_cover._response_body(_FakeSocket(b"", b"cover"), {}, time.monotonic() - 1)

    def test_native_socket_timeout_is_timeout_disposition(self) -> None:
        with patch.object(remote_cover, "_pinned_fetch", side_effect=socket.timeout()):
            with self.assertRaises(RemoteCoverTimeout):
                fetch_remote_cover("https://example.com/cover.png")

    def test_production_path_bounds_unterminated_chunk_framing(self) -> None:
        secured = _FakeSocket(
            b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"A" * (remote_cover._MAX_CHUNK_LINE_BYTES + 64),
        )
        with (
            patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))]),
            patch("socket.create_connection", return_value=secured),
            patch("ssl.create_default_context", return_value=_FakeContext()),
            self.assertRaises(RemoteCoverError),
        ):
            fetch_remote_cover("https://example.com/cover.png")
        self.assertEqual(remote_cover._MAX_CHUNK_LINE_BYTES, secured.body_bytes_read)
        self.assertLessEqual(secured.maximum_recv, remote_cover._MAX_CHUNK_LINE_BYTES)
