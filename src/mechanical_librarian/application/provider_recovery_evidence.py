"""Turn published report evidence into provider-recovery observations and work.

These four decisions have always been application logic that happened to live in
the composition root: which terminal provider statuses become durable health
observations, which lanes become bounded retry members, and how a raised failure
is classified for backoff.  None of them names an adapter, a client, or a
credential, so each is stated here beside its consumers and unit tested directly.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable, Protocol

from mechanical_librarian.application.metadata_report import (
    PROVIDER_RECOVERY_ELIGIBLE_STATUSES,
    CredentialError,
    CurrentnessError,
    ProviderObservationFailure,
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
from mechanical_librarian.domain.proposal_set import ProposalOutcome, ProposalSet
from mechanical_librarian.domain.provider_recovery import ProviderRecoveryJob

if TYPE_CHECKING:
    from mechanical_librarian.application.configuration import ApplicationConfiguration

__all__ = [
    "ProviderFailureObserver",
    "TargetConnectionHeld",
    "classify_provider_recovery_failure",
    "provider_budget_dispatch_reason",
    "provider_recovery_jobs",
    "provider_statuses",
    "record_provider_recovery_observations",
]


def provider_budget_dispatch_reason(
    report: MetadataReport, proposal: ProposalSet | None, observation: object
) -> tuple[str, int]:
    """Classify one published recovery package without provider-specific rules.

    The result is stored with the recovery member because the SQLite claim is
    the atomic dispatch boundary.  It deliberately uses only exact published
    report/proposal facts: a live policy or a later package cannot relabel a
    member after its cohort has started.
    """
    if proposal is not None and type(proposal) is not ProposalSet:
        raise ReviewPipelineError("projection")
    if proposal is not None and proposal.report_id != report.report_id:
        raise ReviewPipelineError("projection")
    references = {
        getattr(claim, "reference", None)
        for candidate in getattr(observation, "candidates", ())
        for claim in getattr(candidate, "claims", ())
    } | {
        getattr(candidate, "reference", None)
        for lane in getattr(observation, "lanes", ())
        for candidate in getattr(lane, "candidates", ())
    }
    references.discard(None)
    unresolved = () if proposal is None else proposal.results

    def can_add_independence(result: object) -> bool:
        alternatives = getattr(result, "alternatives", ())
        return bool(alternatives) and not references.intersection(
            {
                reference
                for alternative in alternatives
                for reference in alternative.evidence_references
            }
        )

    if any(
        result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
        and result.reasons == ("insufficient_independent_corroboration",)
        and can_add_independence(result)
        for result in unresolved
    ):
        return "missing_corroboration", 0
    if not any(
        getattr(provider, "candidates", ())
        or any(
            getattr(lane, "candidates", ()) for lane in getattr(provider, "lanes", ())
        )
        for provider in report.providers
    ):
        return "zero_provider_evidence", 1
    if any(
        result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
        and result.reasons == ("competing_evidence",)
        and can_add_independence(result)
        for result in unresolved
    ):
        return "settleable_disagreement", 2
    return "blanket_scan", 3


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
    cause = error.__cause__ if isinstance(error, ProviderRetrySuccessorError) else None
    if isinstance(cause, ProviderObservationFailure):
        cause = cause.__cause__
    if isinstance(error, CredentialError) or isinstance(cause, CredentialError):
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
    google_burst_remaining: Callable[[], int | None] | None = None,
) -> None:
    """Persist only terminal F22 provider classifications; never retry them."""
    for observation in report.providers:
        decision = provider_recovery_decision(
            observation, now=now, google_burst_remaining=google_burst_remaining
        )
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
    proposal: ProposalSet | None = None,
    configuration: ApplicationConfiguration,
    scope: str,
) -> tuple[ProviderRecoveryJob, ...]:
    """Create bounded retry work only from a published terminal package."""
    adapters = {provider.name: provider.adapter for provider in configuration.providers}
    if proposal is not None and proposal.proposal_set_id != package[0]:
        raise ReviewPipelineError("projection")
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
        reason, rank = provider_budget_dispatch_reason(report, proposal, observation)
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
                dispatch_reason=reason,
                dispatch_rank=rank,
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
