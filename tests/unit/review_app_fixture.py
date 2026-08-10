"""Compose the real application boundary for transport-contract tests only."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

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
    )
    return create_review_app(application, configuration)


__all__ = ["create_transport_test_app"]
