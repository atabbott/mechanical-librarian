import unittest
from unittest.mock import patch

import httpx

from mechanical_librarian.adapters.providers.remote_cover import RemoteCoverError, _public_https, fetch_remote_cover


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
