from __future__ import annotations

import socket
import ssl
import unittest

import httpx

from mechanical_librarian.adapters.providers.retry_safety import (
    bounded_attempt_elapsed,
    bounded_retry_after,
    transient_transport_failure,
)


def _caused(
    error: httpx.HTTPError, cause: BaseException
) -> httpx.HTTPError:
    error.__cause__ = cause
    return error


class ProviderRetrySafetyTests(unittest.TestCase):
    def test_attempt_elapsed_is_finite_and_bounded(self) -> None:
        self.assertEqual(0.0, bounded_attempt_elapsed(2.0, 1.0))
        self.assertEqual(0.0, bounded_attempt_elapsed(0.0, float("inf")))
        self.assertEqual(60.0, bounded_attempt_elapsed(0.0, 600.0))
        self.assertEqual(1.25, bounded_attempt_elapsed(2.0, 3.25))

    def test_accepts_only_narrow_typed_transient_transport_failures(
        self,
    ) -> None:
        accepted = (
            (httpx.ConnectTimeout("redacted"), "transport_connect_timeout"),
            (httpx.ReadTimeout("redacted"), "transport_read_timeout"),
            (
                _caused(
                    httpx.ConnectError("redacted"),
                    ConnectionResetError(),
                ),
                "transport_connect_error",
            ),
            (
                _caused(httpx.ReadError("redacted"), TimeoutError()),
                "transport_read_error",
            ),
            (
                _caused(httpx.WriteError("redacted"), BrokenPipeError()),
                "transport_write_error",
            ),
            (
                _caused(
                    httpx.RemoteProtocolError("redacted"),
                    ConnectionAbortedError(),
                ),
                "transport_remote_protocol_error",
            ),
            (
                _caused(
                    httpx.ConnectError("redacted"),
                    socket.gaierror(socket.EAI_AGAIN, "redacted"),
                ),
                "transport_connect_error",
            ),
        )
        for error, expected in accepted:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(
                    expected, transient_transport_failure(error)
                )

    def test_rejects_unsafe_transport_types_causes_and_unknowns(self) -> None:
        rejected = (
            httpx.PoolTimeout("redacted"),
            httpx.WriteTimeout("redacted"),
            httpx.ProxyError("redacted"),
            httpx.LocalProtocolError("redacted"),
            httpx.DecodingError("redacted"),
            httpx.InvalidURL("redacted"),
            httpx.ConnectError("connection reset by peer"),
            _caused(httpx.ConnectError("redacted"), ssl.SSLError()),
            _caused(
                httpx.ConnectError("redacted"),
                socket.gaierror(socket.EAI_NONAME, "redacted"),
            ),
        )
        for error in rejected:
            with self.subTest(error=type(error).__name__):
                self.assertIsNone(transient_transport_failure(error))

    def test_retry_after_accepts_bounded_canonical_values_only(self) -> None:
        now = 1_784_553_600.0
        self.assertEqual(0.0, bounded_retry_after("0", now=now))
        self.assertEqual(60.0, bounded_retry_after("60", now=now))
        self.assertEqual(
            30.0,
            bounded_retry_after(
                "Mon, 20 Jul 2026 13:20:30 GMT", now=now
            ),
        )
        for value in (
            "61",
            "-1",
            "3, 4",
            "Mon, 20 Jul 2026 13:20:30 GMT, 4",
            "Mon, 20 Jul 2026 14:20:30 GMT",
            "Monday, 20-Jul-26 13:20:30 GMT",
            "redacted",
        ):
            with self.subTest(value=value):
                self.assertIsNone(bounded_retry_after(value, now=now))


if __name__ == "__main__":
    unittest.main()
