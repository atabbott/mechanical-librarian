"""Application orchestration for durable bounded inventory polls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Callable, Protocol

from mechanical_librarian.application.inventory_reconciliation import (
    InventoryOutcome,
    InventoryRunResult,
)
from mechanical_librarian.application.job_heartbeat import RepositoryResult
from mechanical_librarian.application.ports import (
    FailureKind,
    PortFailure,
    RetryAdvice,
    SafeSummary,
)
from mechanical_librarian.domain.inventory_polling import (
    PollClaim,
    PollInspection,
    PollRequest,
    PollState,
)
from mechanical_librarian.domain.identifiers import AdapterId
from mechanical_librarian.domain.inventory_v2 import ScanUsage


class InventoryPollingRepository(Protocol):
    def schedule(
        self, request: PollRequest, now: datetime
    ) -> RepositoryResult[PollInspection]: ...

    def claim(
        self, poll_id: str, worker_id: str, now: datetime
    ) -> RepositoryResult[PollClaim]: ...

    def fail(
        self,
        claim: PollClaim,
        failure: PortFailure,
        usage: ScanUsage,
        now: datetime,
        *,
        compatibility: str | None = None,
    ) -> RepositoryResult[PollInspection]: ...

    def complete(
        self,
        claim: PollClaim,
        usage: ScanUsage,
        now: datetime,
        *,
        compatibility: str = "supported",
    ) -> RepositoryResult[PollInspection]: ...

    def inspect(self, poll_id: str) -> RepositoryResult[PollInspection]: ...


class InventoryPollOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    FAILED = "failed"
    INVALID = "invalid"


class InventoryPollStage(StrEnum):
    SCHEDULED = "scheduled"
    CLAIMED = "claimed"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


_SCHEDULER_FAILURES = frozenset(
    {
        "attempt_budget_exhausted",
        "elapsed_budget_exhausted",
        "lease_expired",
    }
)


@dataclass(frozen=True)
class InventoryPollEvent:
    adapter: AdapterId
    poll_id: str
    stage: InventoryPollStage
    attempt: int
    usage: ScanUsage = ScanUsage()
    failure: FailureKind | str | None = None
    compatibility: str = "unknown"

    def __post_init__(self) -> None:
        if (
            type(self.adapter) is not AdapterId
            or type(self.poll_id) is not str
            or not self.poll_id
            or len(self.poll_id) > 96
            or type(self.stage) is not InventoryPollStage
            or type(self.attempt) is not int
            or self.attempt < 0
            or type(self.usage) is not ScanUsage
            or (
                self.failure is not None
                and type(self.failure) is not FailureKind
                and self.failure not in _SCHEDULER_FAILURES
            )
            or self.compatibility
            not in {"supported", "incompatible", "unknown", "contract_drift"}
        ):
            raise ValueError("inventory poll event is malformed")


class InventoryPollTelemetry(Protocol):
    def record(self, event: InventoryPollEvent) -> None: ...


class _NullInventoryPollTelemetry:
    def record(self, _event: InventoryPollEvent) -> None:
        return None


@dataclass(frozen=True)
class InventoryPollResult:
    outcome: InventoryPollOutcome
    inspection: PollInspection | None
    inventory: InventoryRunResult | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome is InventoryPollOutcome.SUCCEEDED


class RunInventoryPoll:
    """Persist intent before I/O and record exactly one bounded attempt."""

    def __init__(
        self,
        repository: InventoryPollingRepository,
        *,
        telemetry: InventoryPollTelemetry | None = None,
        adapter: AdapterId | None = None,
    ) -> None:
        self._repository = repository
        self._telemetry = _NullInventoryPollTelemetry() if telemetry is None else telemetry
        self._adapter = adapter

    def run(
        self,
        request: PollRequest,
        worker_id: str,
        now: datetime,
        operation: Callable[[PollClaim], InventoryRunResult],
        finished_at: Callable[[], datetime],
    ) -> InventoryPollResult:
        if (
            type(request) is not PollRequest
            or type(worker_id) is not str
            or type(now) is not datetime
            or not callable(operation)
            or not callable(finished_at)
        ):
            return InventoryPollResult(InventoryPollOutcome.INVALID, None)
        scheduled = self._repository.schedule(request, now)
        if not scheduled.accepted or type(scheduled.value) is not PollInspection:
            return InventoryPollResult(InventoryPollOutcome.FAILED, None)
        inspection = scheduled.value
        self._record(inspection, InventoryPollStage.SCHEDULED)
        projected_outcome, projected_stage = _inspection_projection(inspection)
        if projected_outcome is not InventoryPollOutcome.DEFERRED:
            self._record(inspection, projected_stage)
            return InventoryPollResult(projected_outcome, inspection)
        claimed = self._repository.claim(inspection.poll_id, worker_id, now)
        if not claimed.accepted or type(claimed.value) is not PollClaim:
            inspected = self._repository.inspect(inspection.poll_id)
            refreshed = inspected.value if inspected.accepted else inspection
            projected_outcome, projected_stage = _inspection_projection(refreshed)
            self._record(refreshed, projected_stage)
            return InventoryPollResult(
                projected_outcome,
                refreshed,
            )
        claim = claimed.value
        self._record_claim(claim)
        try:
            inventory = operation(claim)
        except Exception:
            inventory = InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())
        ended = finished_at()
        usage = _usage(inventory)
        if inventory.succeeded:
            completed = self._repository.complete(
                claim, usage, ended, compatibility="supported"
            )
            if completed.accepted and type(completed.value) is PollInspection:
                self._record(completed.value, InventoryPollStage.SUCCEEDED)
                return InventoryPollResult(
                    InventoryPollOutcome.SUCCEEDED, completed.value, inventory
                )
            inspected = self._repository.inspect(claim.poll_id)
            return InventoryPollResult(
                InventoryPollOutcome.FAILED,
                inspected.value if inspected.accepted else None,
                inventory,
            )
        failure = _poll_failure(inventory)
        kind = failure.kind
        compatibility = (
            "contract_drift"
            if kind is FailureKind.INVALID_RESPONSE
            else inventory.compatibility
        )
        failed = self._repository.fail(
            claim,
            failure,
            usage,
            ended,
            compatibility=compatibility,
        )
        if not failed.accepted or type(failed.value) is not PollInspection:
            return InventoryPollResult(InventoryPollOutcome.FAILED, None, inventory)
        outcome = {
            PollState.RETRY_WAIT: InventoryPollOutcome.DEFERRED,
            PollState.BLOCKED: InventoryPollOutcome.BLOCKED,
            PollState.CANCELLED: InventoryPollOutcome.FAILED,
            PollState.FAILED_TERMINAL: InventoryPollOutcome.FAILED,
        }.get(failed.value.state, InventoryPollOutcome.FAILED)
        stage = {
            InventoryPollOutcome.DEFERRED: InventoryPollStage.DEFERRED,
            InventoryPollOutcome.BLOCKED: InventoryPollStage.BLOCKED,
        }.get(outcome, InventoryPollStage.FAILED)
        self._record(failed.value, stage)
        return InventoryPollResult(outcome, failed.value, inventory)

    def _record_claim(self, claim: PollClaim) -> None:
        if self._adapter is None:
            return
        try:
            self._telemetry.record(
                InventoryPollEvent(
                    self._adapter,
                    claim.poll_id,
                    InventoryPollStage.CLAIMED,
                    claim.attempt,
                )
            )
        except Exception:
            return

    def _record(self, inspection: PollInspection, stage: InventoryPollStage) -> None:
        if self._adapter is None:
            return
        try:
            self._telemetry.record(
                InventoryPollEvent(
                    self._adapter,
                    inspection.poll_id,
                    stage,
                    inspection.attempt,
                    inspection.usage,
                    _event_failure(inspection.failure_kind),
                    inspection.compatibility or "unknown",
                )
            )
        except Exception:
            return


def _usage(result: InventoryRunResult) -> ScanUsage:
    return sum(
        (
            item.usage
            for item in result.libraries
        ),
        ScanUsage(),
    )


def _inspection_projection(
    inspection: PollInspection,
) -> tuple[InventoryPollOutcome, InventoryPollStage]:
    return {
        PollState.SUCCEEDED: (
            InventoryPollOutcome.SUCCEEDED,
            InventoryPollStage.SUCCEEDED,
        ),
        PollState.BLOCKED: (
            InventoryPollOutcome.BLOCKED,
            InventoryPollStage.BLOCKED,
        ),
        PollState.FAILED_TERMINAL: (
            InventoryPollOutcome.FAILED,
            InventoryPollStage.FAILED,
        ),
        PollState.CANCELLED: (
            InventoryPollOutcome.FAILED,
            InventoryPollStage.FAILED,
        ),
        PollState.PENDING: (
            InventoryPollOutcome.DEFERRED,
            InventoryPollStage.DEFERRED,
        ),
        PollState.RUNNING: (
            InventoryPollOutcome.DEFERRED,
            InventoryPollStage.DEFERRED,
        ),
        PollState.RETRY_WAIT: (
            InventoryPollOutcome.DEFERRED,
            InventoryPollStage.DEFERRED,
        ),
    }[inspection.state]


def _event_failure(value: str | None) -> FailureKind | str | None:
    if value is None:
        return None
    try:
        return FailureKind(value)
    except ValueError:
        return value if value in _SCHEDULER_FAILURES else None


def _failure_kind(result: InventoryRunResult) -> FailureKind:
    values = [
        item.inspection.failure_kind
        for item in result.libraries
        if item.inspection is not None and item.inspection.failure_kind is not None
    ]
    values.extend(
        item.failure.kind.value
        for item in result.libraries
        if item.failure is not None
    )
    priority = (
        FailureKind.CANCELLED,
        FailureKind.AUTHENTICATION,
        FailureKind.AUTHORIZATION,
        FailureKind.UNSUPPORTED,
        FailureKind.INCOMPATIBLE,
        FailureKind.NOT_FOUND,
        FailureKind.INVALID_REQUEST,
        FailureKind.INVALID_RESPONSE,
        FailureKind.RESOURCE_EXHAUSTED,
        FailureKind.TIMEOUT,
        FailureKind.RATE_LIMITED,
        FailureKind.UNAVAILABLE,
        FailureKind.CONFLICT,
    )
    for kind in priority:
        if kind.value in values:
            return kind
    return (
        FailureKind.INVALID_REQUEST
        if result.outcome is InventoryOutcome.INVALID_REQUEST
        else FailureKind.UNAVAILABLE
    )


def _poll_failure(result: InventoryRunResult) -> PortFailure:
    kind = _failure_kind(result)
    selected = None
    for item in result.libraries:
        if item.failure is not None and item.failure.kind is kind:
            selected = item.failure
            break
    if selected is not None:
        advised = tuple(
            item.failure.retry.retry_after
            for item in result.libraries
            if item.failure is not None
            and item.failure.retry.retry_after is not None
        )
        return PortFailure(
            selected.kind,
            RetryAdvice(
                selected.retry.retryable,
                max(advised) if advised and selected.retry.retryable else None,
            ),
            selected.safe_summary,
            selected.usage,
        )
    return PortFailure(
        kind,
        RetryAdvice(kind in {
            FailureKind.UNAVAILABLE,
            FailureKind.RATE_LIMITED,
            FailureKind.TIMEOUT,
        }),
        SafeSummary("inventory poll attempt failed safely"),
    )


__all__ = [
    "InventoryPollOutcome",
    "InventoryPollEvent",
    "InventoryPollResult",
    "InventoryPollStage",
    "InventoryPollTelemetry",
    "InventoryPollingRepository",
    "RunInventoryPoll",
]
