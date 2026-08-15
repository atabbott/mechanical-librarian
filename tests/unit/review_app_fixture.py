"""Compose the real application boundary for transport-contract tests only."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.operations import (
    StageTimer,
    SupportExporter,
    TimingRepository,
)
from mechanical_librarian.application.review_application import (
    compose_review_application,
)
from mechanical_librarian.application.review_pipeline import ReviewPreparationSupervisor
from mechanical_librarian.interfaces.web import (
    create_review_app,
    load_owner_password_verifier,
)
from mechanical_librarian.interfaces.web.react_assets import (
    ReactAsset,
    ReactAssetBundle,
    ReactAssetLoader,
)

TEST_REACT_BUILD_IDENTITY = "a" * 64


class _MemoryFile:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read_bytes(self) -> bytes:
        return self._payload


class _MemoryRoot:
    def __init__(self, values: dict[str, bytes]) -> None:
        self._values = values

    def joinpath(self, *descendants: str) -> _MemoryFile:
        return _MemoryFile(self._values["/".join(descendants)])


class _TestReactAssetLoader:
    def __init__(self) -> None:
        values = {
            "index.html": (
                b'<!doctype html><html><body><div id="root"></div>'
                b'<script src="/assets/review-app/assets/index-deadbeef.js"></script>'
                b"</body></html>"
            ),
            "assets/index-deadbeef.js": b"window.reviewReady=true;",
            "assets/index-deadbeef.css": b":root{color-scheme:light}",
        }
        root = _MemoryRoot(values)
        self._bundle = ReactAssetBundle(
            TEST_REACT_BUILD_IDENTITY,
            "index.html",
            tuple(
                ReactAsset(
                    path,
                    hashlib.sha256(payload).hexdigest(),
                    len(payload),
                    "shell" if path == "index.html" else (
                        "style" if path.endswith(".css") else "script"
                    ),
                )
                for path, payload in sorted(values.items())
            ),
            root,
        )

    def load(self) -> ReactAssetBundle:
        return self._bundle


def create_transport_test_app(
    repository,
    configuration: ReviewWebConfiguration,
    *,
    clock: Callable[[], int] = lambda: int(time.time()),
    close_repository: bool = False,
    review_preparation: ReviewPreparationSupervisor | None = None,
    policy_libraries: tuple[tuple[str, dict[str, object] | None], ...] = (),
    field_application: GuardedFieldApplication | ContinuousGuardedApplication | None = None,
    operational_timings: TimingRepository | None = None,
    operational_support: SupportExporter | None = None,
    operational_timer: StageTimer | None = None,
    react_asset_loader: ReactAssetLoader | None = None,
    cover_considerations=None,
):
    """Wire synthetic store capabilities before creating the HTTP adapter."""
    application = compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=load_owner_password_verifier(
            Path(configuration.owner_password_hash_file)
        ),
        clock=clock,
        session_lifetime_seconds=configuration.session_lifetime_seconds,
        graceful_shutdown_seconds=configuration.graceful_shutdown_seconds,
        close_state_owner=close_repository,
        preparation=review_preparation,
        policy_libraries=policy_libraries,
        field_application=field_application,
        operational_timings=operational_timings,
        operational_support=operational_support,
        operational_timer=operational_timer,
        cover_considerations=cover_considerations,
    )
    with patch(
        "mechanical_librarian.interfaces.web.app.ReactAssetLoader",
        return_value=react_asset_loader or _TestReactAssetLoader(),
    ):
        return create_review_app(application, configuration)


__all__ = ["TEST_REACT_BUILD_IDENTITY", "create_transport_test_app"]
