"""The review edge closes a request that does not arrive in time.

Each test runs a real uvicorn server with `BoundedReviewH11Protocol` on a
loopback socket and speaks raw HTTP/1.1 to it, so what is proven is what a
client on the network sees. The deadlines are shortened for the test; their
shipped values are asserted separately.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from mechanical_librarian.infrastructure.security import review_protocol
from mechanical_librarian.infrastructure.security.review_protocol import (
    REVIEW_BODY_TIMEOUT_SECONDS,
    REVIEW_HEADER_TIMEOUT_SECONDS,
    BoundedReviewH11Protocol,
)
from mechanical_librarian.interfaces.web.app import (
    _marking_aborted,
    _withhold_aborted_end,
)

_DEADLINE = 0.5
# Long enough that a closed connection is told apart from a slow one on a
# loaded runner, short enough that a hung test fails quickly.
_PATIENCE = 5.0


async def _echo_length(scope: Scope, receive: Receive, send: Send) -> None:
    """Read the whole body, as `BodyLimitMiddleware` does, then answer."""
    if scope["type"] != "http":
        return
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        size += len(message.get("body", b""))
        if not message.get("more_body", False):
            break
    body = str(size).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _answer_at_once(scope: Scope, receive: Receive, send: Send) -> None:
    """Answer before reading any body, as an early refusal does."""
    if scope["type"] != "http":
        return
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-length", b"0")],
        }
    )
    await send({"type": "http.response.body", "body": b""})


class _Edge:
    def __init__(self, app: object) -> None:
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self.port = int(self._listener.getsockname()[1])
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,  # type: ignore[arg-type]  # reason: a bare ASGI callable, which uvicorn accepts at runtime
                http=BoundedReviewH11Protocol,
                log_config=None,
                access_log=False,
                lifespan="off",
                # Far above `_PATIENCE`, so only the protocol's own deadlines
                # can close a connection inside a test.
                timeout_keep_alive=60,
            )
        )
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self._server.serve(sockets=[self._listener])),
            daemon=True,
        )

    def __enter__(self) -> _Edge:
        self._thread.start()
        deadline = time.monotonic() + _PATIENCE
        while not self._server.started:
            if time.monotonic() > deadline:
                raise AssertionError("the test edge did not start")
            time.sleep(0.01)
        return self

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(_PATIENCE)
        self._listener.close()

    def connect(self) -> socket.socket:
        client = socket.create_connection(("127.0.0.1", self.port), timeout=_PATIENCE)
        return client


def _read_response(client: socket.socket) -> bytes:
    """One whole response: its head and the body its length names."""
    received = b""
    while b"\r\n\r\n" not in received:
        chunk = client.recv(65_536)
        assert chunk, "the edge closed before answering"
        received += chunk
    head, _, body = received.partition(b"\r\n\r\n")
    length = next(
        int(line.split(b":", 1)[1])
        for line in head.split(b"\r\n")
        if line.lower().startswith(b"content-length:")
    )
    while len(body) < length:
        chunk = client.recv(65_536)
        assert chunk, "the edge closed mid-response"
        body += chunk
    assert len(body) == length
    return head + b"\r\n\r\n" + body


def _read_until_closed(client: socket.socket) -> tuple[bytes, float]:
    """Everything the server sends, and how long it took to close."""
    started = time.monotonic()
    received = b""
    while True:
        try:
            chunk = client.recv(65_536)
        except TimeoutError:
            pytest.fail("the edge kept a stalled request open")
        except ConnectionResetError:
            break
        if not chunk:
            break
        received += chunk
    return received, time.monotonic() - started


@pytest.fixture
def short_deadlines(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(review_protocol, "REVIEW_HEADER_TIMEOUT_SECONDS", _DEADLINE)
    monkeypatch.setattr(review_protocol, "REVIEW_BODY_TIMEOUT_SECONDS", _DEADLINE)
    yield


def test_shipped_deadlines() -> None:
    assert REVIEW_HEADER_TIMEOUT_SECONDS == 10.0
    assert REVIEW_BODY_TIMEOUT_SECONDS == 30.0


def test_a_body_that_stops_arriving_is_closed(short_deadlines: None) -> None:
    with _Edge(_echo_length) as edge, edge.connect() as client:
        client.sendall(
            b"POST /api/v1/activity/consent HTTP/1.1\r\nHost: edge\r\n"
            b"Content-Length: 1000\r\n\r\n" + b"x" * 10
        )
        received, waited = _read_until_closed(client)
    assert received == b""
    assert waited < _PATIENCE


def test_a_trickling_body_is_closed_at_the_deadline(short_deadlines: None) -> None:
    with _Edge(_echo_length) as edge, edge.connect() as client:
        client.sendall(
            b"POST /api/v1/settings HTTP/1.1\r\nHost: edge\r\n"
            b"Content-Length: 1000\r\n\r\n"
        )
        started = time.monotonic()
        closed = False
        # More bytes never extend the deadline.
        while time.monotonic() - started < _PATIENCE:
            try:
                client.sendall(b"x")
            except (BrokenPipeError, ConnectionResetError):
                closed = True
                break
            time.sleep(0.05)
        if not closed:
            pytest.fail("a trickling body held the connection past its deadline")
        assert time.monotonic() - started < _PATIENCE


def test_a_body_still_arriving_after_an_early_answer_is_closed(
    short_deadlines: None,
) -> None:
    with _Edge(_answer_at_once) as edge, edge.connect() as client:
        client.sendall(
            b"POST /api/v1/activity/consent HTTP/1.1\r\nHost: edge\r\n"
            b"Content-Length: 1000\r\n\r\n" + b"x" * 10
        )
        answer = _read_response(client)
        assert answer.startswith(b"HTTP/1.1 413 ")
        started = time.monotonic()
        # The body keeps trickling after the answer; only the body deadline
        # can close the connection, since each byte is fresh activity.
        while time.monotonic() - started < _PATIENCE:
            try:
                client.sendall(b"x")
            except (BrokenPipeError, ConnectionResetError):
                break
            time.sleep(0.05)
        else:
            pytest.fail("a body trickling after an early answer held the connection")


def test_a_complete_request_is_answered_and_kept_alive(short_deadlines: None) -> None:
    with _Edge(_echo_length) as edge, edge.connect() as client:
        request = (
            b"POST /api/v1/activity/consent HTTP/1.1\r\nHost: edge\r\n"
            b"Content-Length: 20\r\n\r\n"
        )
        client.sendall(request + b"y" * 10)
        time.sleep(_DEADLINE / 5)
        client.sendall(b"y" * 10)
        first = _read_response(client)
        assert first.startswith(b"HTTP/1.1 200 ")
        assert first.endswith(b"\r\n\r\n20")
        # A second request on the same connection gets its own deadline.
        client.sendall(request + b"z" * 20)
        second = _read_response(client)
        assert second.startswith(b"HTTP/1.1 200 ")
        # Idle after a response, the connection closes on the header deadline.
        received, waited = _read_until_closed(client)
    assert received == b""
    assert waited < _PATIENCE


def test_incomplete_headers_are_still_closed(short_deadlines: None) -> None:
    with _Edge(_echo_length) as edge, edge.connect() as client:
        client.sendall(b"POST /api/v1/settings HTTP/1.1\r\nHost:")
        received, waited = _read_until_closed(client)
    assert received == b""
    assert waited < _PATIENCE


async def _stream_slowly(scope: Scope, receive: Receive, send: Send) -> None:
    """Answer a bodiless GET with a body sent over several deadlines (#1336)."""
    if scope["type"] != "http":
        return
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/csv")],
        }
    )
    for index in range(8):
        await asyncio.sleep(_DEADLINE / 2)
        await send(
            {
                "type": "http.response.body",
                "body": f"row-{index}\n".encode(),
                "more_body": True,
            }
        )
    await send({"type": "http.response.body", "body": b""})


def test_a_streamed_answer_to_a_complete_get_outlives_both_deadlines(
    short_deadlines: None,
) -> None:
    """Neither deadline runs while the server, not the client, is sending.

    The write plan file streams for as long as it takes to make (#1336); the
    body deadline covers only a request body still owed by the client.
    """
    with _Edge(_stream_slowly) as edge, edge.connect() as client:
        client.sendall(
            b"GET /api/v1/activity/write-plan?format=csv HTTP/1.1\r\nHost: edge\r\n\r\n"
        )
        received = b""
        started = time.monotonic()
        while not received.endswith(b"0\r\n\r\n"):
            chunk = client.recv(65_536)
            assert chunk, "the edge closed mid-stream"
            received += chunk
        elapsed = time.monotonic() - started
    assert elapsed > 2 * _DEADLINE
    assert received.startswith(b"HTTP/1.1 200 ")
    assert all(f"row-{index}\n".encode() in received for index in range(8))


def _aborting_app() -> FastAPI:
    """The app's middleware shape with a route whose body fails after its head."""
    app = FastAPI()

    @app.middleware("http")
    async def relay(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        _withhold_aborted_end(request.scope, response)
        return response

    @app.get("/api/v1/activity/write-plan")
    def plan(request: Request) -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b'"Library"\r\n"Secret Title"\r\n'
            await asyncio.sleep(_DEADLINE / 2)
            raise RuntimeError("Secret Title /books/secret.epub")

        return StreamingResponse(
            _marking_aborted(request.scope, body()), media_type="text/csv"
        )

    return app


def test_an_aborted_stream_is_closed_without_its_chunked_terminator(
    short_deadlines: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A body that fails after its head never reads as complete on the wire (#1336).

    The relayed body raises before the middleware's final frame, so the server
    closes the connection instead of sending the terminator, and logs only the
    original error's type.
    """
    with (
        caplog.at_level(logging.DEBUG),
        _Edge(_aborting_app()) as edge,
        edge.connect() as client,
    ):
        client.sendall(
            b"GET /api/v1/activity/write-plan HTTP/1.1\r\nHost: edge\r\n\r\n"
        )
        received, elapsed = _read_until_closed(client)
    assert elapsed < _PATIENCE
    assert received.startswith(b"HTTP/1.1 200 ")
    assert b"transfer-encoding: chunked" in received.lower()
    assert b'"Secret Title"' in received
    assert not received.endswith(b"0\r\n\r\n")
    logged = "\n".join(
        record.getMessage()
        + "\n"
        + (
            logging.Formatter().formatException(record.exc_info)
            if record.exc_info
            else ""
        )
        for record in caplog.records
    )
    assert "response body aborted by RuntimeError" in logged
    assert "Secret Title" not in logged
    assert "/books/" not in logged
