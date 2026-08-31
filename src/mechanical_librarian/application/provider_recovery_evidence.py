"""Turn published report evidence into provider-recovery observations and work.

These four decisions have always been application logic that happened to live in
the composition root: which terminal provider statuses become durable health
observations, which lanes become bounded retry members, and how a raised failure
is classified for backoff.  None of them names an adapter, a client, or a
credential, so each is stated here beside its consumers and unit tested directly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Protocol

from mechanical_librarian.application.metadata_report import (
    PROVIDER_RECOVERY_ELIGIBLE_STATUSES,
    CredentialError,
    CurrentnessError,
    TargetNotFoundError,
)
from mechanical_librarian.application.provider_recovery_policy import (
    provider_recovery_decision,
)
from mechanical_librarian.application.provider_retry_successor import (
    ProviderRetryDisabled,
    ProviderRetrySharedFailure,
    ProviderRetrySuccessorError,
)
from mechanical_librarian.application.review_pipeline import ReviewPipelineError
from mechanical_librarian.domain.metadata_report import MetadataReport, ProviderStatus
from mechanical_librarian.domain.provider_recovery import ProviderRecoveryJob

if TYPE_CHECKING:
    from mechanical_librarian.application.configuration import ApplicationConfiguration

__all__ = [
    "ProviderFailureObserver",
    "TargetConnectionHeld",
    "classify_provider_recovery_failure",
    "provider_recovery_jobs",
    "provider_statuses",
    "record_provider_recovery_observations",
]


class ProviderFailureObserver(Protocol):
    """The one durable write ``record_provider_recovery_observations`` performs.

    This is a parameter annotation, not a facade: the composition root passes the
    concrete recovery store itself, so no capability the store carries is hidden
    from anything it is subsequently handed to.
    """

    def observe_failure(
        self,
        *,
        connection: str,
        adapter: str,
        scope: str,
        generation: int,
        failure: str,
        now: int,
        retry_after: int | None = None,
    ) -> None: ...


class TargetConnectionHeld(CurrentnessError):
    """Private runner signal: retain provider recovery unchanged for a target hold."""


def classify_provider_recovery_failure(error: Exception) -> tuple[str, int | None]:
    if isinstance(error, TargetConnectionHeld):
        return "target_held", None
    if isinstance(error, CredentialError) or (
        isinstance(error, ProviderRetrySuccessorError)
        and isinstance(error.__cause__, CredentialError)
    ):
        return "authentication", None
    if isinstance(error, ProviderRetryDisabled):
        return "disabled", None
    if isinstance(error, ProviderRetrySharedFailure):
        failure = (
            "transient"
            if error.failure in {"unavailable", "incomplete"}
            else error.failure
        )
        return failure, error.retry_after
    if isinstance(error, ProviderRetrySuccessorError) and "configuration" in str(error):
        return "configuration", None
    if isinstance(error, ProviderRetrySuccessorError):
        return "terminal", None
    if isinstance(error, (CurrentnessError, TargetNotFoundError)):
        return "stale", None
    return "transient", None


def provider_statuses(report: MetadataReport) -> tuple[ProviderStatus, ...]:
    return tuple(
        status
        for provider in report.providers
        for status in (
            provider.status,
            *(lane.status for lane in provider.lanes),
        )
    )


def record_provider_recovery_observations(
    store: ProviderFailureObserver,
    report: MetadataReport,
    *,
    adapters: dict[str, str],
    scope: str,
    generation: int,
    now: int,
) -> None:
    """Persist only terminal F22 provider classifications; never retry them."""
    for observation in report.providers:
        decision = provider_recovery_decision(observation, now=now)
        if decision is not None:
            failure, retry_after = decision
            adapter = adapters.get(observation.provider)
            if adapter is None:
                raise ReviewPipelineError("configuration")
            store.observe_failure(
                connection=observation.provider,
                adapter=adapter,
                scope=scope,
                generation=generation,
                failure=failure,
                now=now,
                retry_after=retry_after,
            )


def provider_recovery_jobs(
    report: MetadataReport,
    *,
    package: tuple[str | None, str, str | None, int | None, str],
    configuration: ApplicationConfiguration,
    scope: str,
) -> tuple[ProviderRecoveryJob, ...]:
    """Create bounded retry work only from a published terminal package."""
    adapters = {provider.name: provider.adapter for provider in configuration.providers}
    jobs = []
    for observation in report.providers:
        lane_ids = tuple(
            sorted(
                lane.identity
                for lane in observation.lanes
                if lane.status in PROVIDER_RECOVERY_ELIGIBLE_STATUSES
            )
        )
        if not lane_ids:
            continue
        adapter = adapters.get(observation.provider)
        if adapter is None:
            raise ReviewPipelineError("configuration")
        jobs.append(
            ProviderRecoveryJob(
                observation.provider,
                adapter,
                scope,
                configuration.provider_recovery_generation,
                report.report_id,
                report.target.connection,
                report.target.library,
                report.target.item,
                report.file.identity,
                report.report_id,
                *package,
                json.dumps(lane_ids, separators=(",", ":")),
            )
        )
    return tuple(
        sorted(
            jobs,
            key=lambda job: (
                job.connection,
                job.adapter,
                job.scope,
                job.generation,
                job.member_id,
            ),
        )
    )
