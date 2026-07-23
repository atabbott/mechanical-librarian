import unittest
from unittest.mock import patch

from mechanical_librarian.adapters.providers.remote_cover import RemoteCoverError, _public_https


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
