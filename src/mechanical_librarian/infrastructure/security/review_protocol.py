"""Bounded HTTP/1.1 protocol for the direct authenticated review edge."""

from __future__ import annotations

import asyncio
from typing import Any

from uvicorn.protocols.http import h11_impl

MAX_REVIEW_CONNECTIONS = 100
REVIEW_HEADER_TIMEOUT_SECONDS = 10.0
REVIEW_BODY_TIMEOUT_SECONDS = 30.0
REVIEW_TLS_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20"

# Suppression reason for each `h11_impl.h11` below: h11_impl imports the h11
# module without re-exporting it, so mypy's no-implicit-reexport rejects the
# attribute even though it exists at runtime. h11 is not a declared direct
# dependency of this project, so it is deliberately not imported here.


class BoundedReviewH11Protocol(h11_impl.H11Protocol):
    """Close excess connections and requests that do not arrive in time.

    Headers must arrive within ``REVIEW_HEADER_TIMEOUT_SECONDS`` of the
    connection opening or the previous response ending. A request body must
    then arrive in full within ``REVIEW_BODY_TIMEOUT_SECONDS`` of its headers,
    whether or not the app has already answered. Neither deadline is reset by
    more bytes, so a slow trickle is closed as surely as a silent client.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._review_header_timeout: asyncio.TimerHandle | None = None
        self._review_body_timeout: asyncio.TimerHandle | None = None

    def connection_made(  # type: ignore[override]  # reason: narrows to asyncio.Transport exactly as uvicorn's own H11Protocol.connection_made does; uvicorn only ever serves TCP transports here.
        self, transport: asyncio.Transport
    ) -> None:
        super().connection_made(transport)
        if len(self.connections) > MAX_REVIEW_CONNECTIONS:
            transport.close()
            return
        self._set_review_header_timeout()

    def data_received(self, data: bytes) -> None:
        self._set_review_header_timeout()
        super().data_received(data)
        self._follow_request_state()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        if not self.transport.is_closing():
            self._follow_request_state()

    def connection_lost(self, exc: Exception | None) -> None:
        self._unset_review_header_timeout()
        self._unset_review_body_timeout()
        super().connection_lost(exc)

    def _follow_request_state(self) -> None:
        """Keep exactly the deadline for what the client still owes."""
        state = self.conn.their_state
        if state is h11_impl.h11.IDLE:  # type: ignore[attr-defined]  # reason: uvicorn re-exports h11 through h11_impl at runtime but does not declare it, and h11 is a transitive dependency this unit may not add to pyproject.toml
            # Still owed: the next request's headers.
            self._unset_review_body_timeout()
            self._set_review_header_timeout()
        elif state is h11_impl.h11.SEND_BODY:  # type: ignore[attr-defined]  # reason: uvicorn re-exports h11 through h11_impl at runtime but does not declare it, and h11 is a transitive dependency this unit may not add to pyproject.toml
            # Headers arrived; the body is still owed.
            self._unset_review_header_timeout()
            self._set_review_body_timeout()
        else:
            # The request has fully arrived, or the connection is closing.
            self._unset_review_header_timeout()
            self._unset_review_body_timeout()

    def _set_review_header_timeout(self) -> None:
        if self._review_header_timeout is None:
            self._review_header_timeout = self.loop.call_later(
                REVIEW_HEADER_TIMEOUT_SECONDS, self._expire_review_header
            )

    def _unset_review_header_timeout(self) -> None:
        if self._review_header_timeout is not None:
            self._review_header_timeout.cancel()
            self._review_header_timeout = None

    def _set_review_body_timeout(self) -> None:
        if self._review_body_timeout is None:
            self._review_body_timeout = self.loop.call_later(
                REVIEW_BODY_TIMEOUT_SECONDS, self._expire_review_body
            )

    def _unset_review_body_timeout(self) -> None:
        if self._review_body_timeout is not None:
            self._review_body_timeout.cancel()
            self._review_body_timeout = None

    def _expire_review_header(self) -> None:
        self._review_header_timeout = None
        self._close_incomplete_request()

    def _expire_review_body(self) -> None:
        self._review_body_timeout = None
        self._close_incomplete_request()

    def _close_incomplete_request(self) -> None:
        if self.transport is not None and not self.transport.is_closing():
            self.transport.close()


__all__ = [
    "BoundedReviewH11Protocol",
    "MAX_REVIEW_CONNECTIONS",
    "REVIEW_BODY_TIMEOUT_SECONDS",
    "REVIEW_HEADER_TIMEOUT_SECONDS",
    "REVIEW_TLS_CIPHERS",
]
