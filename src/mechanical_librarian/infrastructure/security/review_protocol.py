"""Bounded HTTP/1.1 protocol for the direct authenticated review edge."""

from __future__ import annotations

import asyncio

from uvicorn.protocols.http import h11_impl


MAX_REVIEW_CONNECTIONS = 100
REVIEW_HEADER_TIMEOUT_SECONDS = 10.0
REVIEW_TLS_CIPHERS = "ECDHE+AESGCM:ECDHE+CHACHA20"


class BoundedReviewH11Protocol(h11_impl.H11Protocol):
    """Close excess connections and incomplete request headers."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._review_header_timeout: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.Transport) -> None:
        super().connection_made(transport)
        if len(self.connections) > MAX_REVIEW_CONNECTIONS:
            transport.close()
            return
        self._set_review_header_timeout()

    def data_received(self, data: bytes) -> None:
        self._set_review_header_timeout()
        super().data_received(data)
        if self.conn.their_state is not h11_impl.h11.IDLE:
            self._unset_review_header_timeout()

    def on_response_complete(self) -> None:
        super().on_response_complete()
        if (
            not self.transport.is_closing()
            and self.conn.their_state is h11_impl.h11.IDLE
        ):
            self._set_review_header_timeout()

    def connection_lost(self, exc: Exception | None) -> None:
        self._unset_review_header_timeout()
        super().connection_lost(exc)

    def _set_review_header_timeout(self) -> None:
        if self._review_header_timeout is None:
            self._review_header_timeout = self.loop.call_later(
                REVIEW_HEADER_TIMEOUT_SECONDS, self._expire_review_header
            )

    def _unset_review_header_timeout(self) -> None:
        if self._review_header_timeout is not None:
            self._review_header_timeout.cancel()
            self._review_header_timeout = None

    def _expire_review_header(self) -> None:
        self._review_header_timeout = None
        if self.transport is not None and not self.transport.is_closing():
            self.transport.close()


__all__ = [
    "BoundedReviewH11Protocol",
    "MAX_REVIEW_CONNECTIONS",
    "REVIEW_HEADER_TIMEOUT_SECONDS",
    "REVIEW_TLS_CIPHERS",
]
