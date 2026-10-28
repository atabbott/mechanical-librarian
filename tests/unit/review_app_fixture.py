"""Compose the real application boundary for transport-contract tests only."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import patch

from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
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
from mechanical_librarian.application.owner_settings import OwnerSettingsOperations
from mechanical_librarian.application.review_application import (
    compose_review_application,
)
from mechanical_librarian.application.review_pipeline import ReviewPreparationSupervisor
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
from mechanical_librarian.interfaces.web import (
    create_review_app,
    load_owner_password_binding,
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
    def __init__(self, fonts: dict[str, bytes] | None = None) -> None:
        values = {
            "index.html": (
                b'<!doctype html><html><body><div id="root"></div>'
                b'<script src="/assets/review-app/assets/index-deadbeef.js"></script>'
                b"</body></html>"
            ),
            "assets/index-deadbeef.js": b"window.reviewReady=true;",
            "assets/index-deadbeef.css": b":root{color-scheme:light}",
            **(fonts or {}),
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
                    "shell"
                    if path == "index.html"
                    else "font"
                    if path.endswith(".woff2")
                    else ("style" if path.endswith(".css") else "script"),
                )
                for path, payload in sorted(values.items())
            ),
            root,
        )

    def load(self) -> ReactAssetBundle:
        return self._bundle


def font_asset_loader(fonts: dict[str, bytes]) -> ReactAssetLoader:
    """A synthetic workspace build that also carries these font files."""
    return cast(ReactAssetLoader, _TestReactAssetLoader(fonts))


def create_transport_test_app(
    repository,
    configuration: ReviewWebConfiguration,
    *,
    session_store=None,
    clock: Callable[[], int] = lambda: int(time.time()),
    close_repository: bool = False,
    review_preparation: ReviewPreparationSupervisor | None = None,
    policy_libraries: tuple[tuple[str, dict[str, object] | None], ...] = (),
    field_application: GuardedFieldApplication
    | ContinuousGuardedApplication
    | None = None,
    activity_projection: ContinuousGuardedApplication | None = None,
    operational_timings: TimingRepository | None = None,
    operational_support: SupportExporter | None = None,
    operational_timer: StageTimer | None = None,
    react_asset_loader: ReactAssetLoader | None = None,
    cover_considerations=None,
    settings: OwnerSettingsOperations | None = None,
    reviewer_access: ReviewerAccessOperations | None = None,
    first_boot: bool = False,
    acknowledge_first_boot: Callable[[], None] | None = None,
    provider_throttling: Callable[[], dict[str, object]] | None = None,
):
    """Wire synthetic store capabilities before creating the HTTP adapter."""
    password_binding = load_owner_password_binding(
        Path(configuration.owner_password_hash_file)
    )
    if session_store is None:
        # A double that still implements the session protocol serves as its
        # own store; a real repository no longer does (#912), so compose a
        # fresh in-memory session store beside it.
        if hasattr(repository, "begin_login"):
            session_store = repository
        else:
            session_store = SQLiteReviewSessionRepository.open_ephemeral()
    application = compose_review_application(
        state_owner=repository,
        session_store=session_store,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=password_binding[0],
        session_digest=password_binding[1],
        client_digest=password_binding[2],
        clock=clock,
        session_lifetime_seconds=configuration.session_lifetime_seconds,
        graceful_shutdown_seconds=configuration.graceful_shutdown_seconds,
        close_state_owner=close_repository,
        preparation=review_preparation,
        policy_libraries=policy_libraries,
        field_application=field_application,
        activity_projection=activity_projection,
        operational_timings=operational_timings,
        operational_support=operational_support,
        operational_timer=operational_timer,
        cover_considerations=cover_considerations,
        settings=settings,
        reviewer_access=reviewer_access,
        first_boot=first_boot,
        acknowledge_first_boot=acknowledge_first_boot,
        provider_throttling=provider_throttling,
    )
    with patch(
        "mechanical_librarian.interfaces.web.app.ReactAssetLoader",
        return_value=react_asset_loader or _TestReactAssetLoader(),
    ):
        app = create_review_app(application, configuration)
    # The composed boundary, kept reachable for the cohorts that assert on what
    # it announced rather than on what it answered. Nothing in the served
    # composition reads this.
    app.state.review_application = application
    return app


__all__ = ["TEST_REACT_BUILD_IDENTITY", "create_transport_test_app"]
