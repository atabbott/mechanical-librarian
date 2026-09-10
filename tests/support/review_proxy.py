"""Disposable TLS edge for real browser-to-HTTP-backend transport proof."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from tests.support.ports import ReservedPort


@contextmanager
def review_tls_proxy(
    backend: str, origin: str, port: ReservedPort, certificate: Path, key: Path
) -> Iterator[None]:
    """Forward the exact fixture edge identity and stream responses unchanged."""

    async def forward(request: Request) -> StreamingResponse:
        client = httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=None
        )
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower()
            not in {
                "host",
                "connection",
                "x-forwarded-host",
                "x-forwarded-proto",
                "forwarded",
            }
        }
        headers.update(
            {
                "host": urlsplit(origin).netloc,
                "x-forwarded-host": urlsplit(origin).netloc,
                "x-forwarded-proto": "https",
            }
        )
        target = client.build_request(
            request.method,
            backend
            + request.url.path
            + ("?" + request.url.query if request.url.query else ""),
            headers=headers,
            content=await request.body(),
        )
        response = await client.send(target, stream=True)

        async def close() -> None:
            await response.aclose()
            await client.aclose()

        result = StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            background=BackgroundTask(close),
        )
        result.raw_headers = [
            (name, value)
            for name, value in response.headers.raw
            if name.lower() not in {b"connection", b"transfer-encoding"}
        ]
        return result

    app = Starlette(
        routes=[
            Route(
                "/{path:path}",
                forward,
                methods=["GET", "POST", "PATCH", "DELETE", "HEAD", "OPTIONS"],
            )
        ]
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port.number,
            ssl_certfile=str(certificate),
            ssl_keyfile=str(key),
            proxy_headers=False,
            access_log=False,
            log_level="error",
            timeout_graceful_shutdown=1,
        )
    )
    port.release()
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "synthetic TLS edge did not start"
    try:
        yield
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "synthetic TLS edge did not stop"
