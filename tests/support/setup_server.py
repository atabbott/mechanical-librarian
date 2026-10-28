"""The real first-run setup generation on a temporary volume, served over TLS.

Two suites drive the same server: the browser suite through the shipped page,
and a transport test through HTTP alone, which still runs where the browser
cannot launch.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from mechanical_librarian.bootstrap import composition
from mechanical_librarian.infrastructure.settings_authority import (
    BootstrapMount,
    ReviewBootstrap,
)
from mechanical_librarian.interfaces.web.runtime_dispatch import (
    MutableRuntimeDispatcher,
)
from tests.support.ports import ReservedPort
from tests.support.tls import synthetic_tls_identity


@contextmanager
def real_setup_server(
    root: Path,
    trusted_proxy_ips: tuple[str, ...] = (),
    mounts: tuple[BootstrapMount, ...] = (),
) -> Iterator[tuple[str, Path, Path]]:
    """Yield the origin, the minted token file and the trusted certificate."""
    certificate, private_key = synthetic_tls_identity(root, "setup")
    data = root / "data"
    data.mkdir(mode=0o700)
    database = data / "application.sqlite3"
    port = ReservedPort()
    origin = f"https://127.0.0.1:{port.number}"
    bootstrap = ReviewBootstrap(
        schema_version="review-bootstrap-v2",
        canonical_origin=origin,
        tls_certificate_file=str(certificate),
        tls_private_key_file=str(private_key),
        trusted_proxy_ips=list(trusted_proxy_ips),
        mounts=list(mounts),
    )
    repository = composition.open_repository(database, create=True, cross_thread=True)
    session_store = composition._open_review_session_store(database)
    activation = None
    try:
        runtime, activation = composition._compose_setup_runtime_generation(
            bootstrap,
            database,
            data / "catalogue.sqlite3",
            data / "artifacts",
            repository,
            session_store,
            initialize=True,
        )
        dispatcher = MutableRuntimeDispatcher(runtime)
        activation.bind(dispatcher)
        server = uvicorn.Server(
            uvicorn.Config(
                dispatcher,
                host="127.0.0.1",
                port=port.number,
                log_level="error",
                access_log=False,
                ssl_certfile=str(certificate),
                ssl_keyfile=str(private_key),
            )
        )
        dispatcher.start()
        port.release()
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            if not server.started:
                raise RuntimeError("real setup server did not start")
            yield origin, data / "private" / "setup-claim-token", certificate
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            runtime.close()
    finally:
        port.release()
        if activation is not None:
            activation.close()
        session_store.close()
        repository.close()
