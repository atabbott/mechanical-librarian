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

    def __init__(self, repository: InventoryPollingRepository) -> None:
        self._repository = repository

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
        if inspection.state is PollState.SUCCEEDED:
            return InventoryPollResult(InventoryPollOutcome.SUCCEEDED, inspection)
        if inspection.state is PollState.BLOCKED:
            return InventoryPollResult(InventoryPollOutcome.BLOCKED, inspection)
        if inspection.state in {PollState.FAILED_TERMINAL, PollState.CANCELLED}:
            return InventoryPollResult(InventoryPollOutcome.FAILED, inspection)
        claimed = self._repository.claim(inspection.poll_id, worker_id, now)
        if not claimed.accepted or type(claimed.value) is not PollClaim:
            inspected = self._repository.inspect(inspection.poll_id)
            return InventoryPollResult(
                InventoryPollOutcome.DEFERRED,
                inspected.value if inspected.accepted else inspection,
            )
        claim = claimed.value
        try:
            inventory = operation(claim)
        except Exception:
            inventory = InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())
        ended = finished_at()
        usage = _usage(inventory)
        if inventory.succeeded:
            completed = self._repository.complete(claim, usage, ended)
            if completed.accepted and type(completed.value) is PollInspection:
                return InventoryPollResult(
                    InventoryPollOutcome.SUCCEEDED, completed.value, inventory
                )
            inspected = self._repository.inspect(claim.poll_id)
            return InventoryPollResult(
                InventoryPollOutcome.FAILED,
                inspected.value if inspected.accepted else None,
                inventory,
            )
        kind = _failure_kind(inventory)
        failure = PortFailure(
            kind,
            RetryAdvice(kind in {
                FailureKind.UNAVAILABLE,
                FailureKind.RATE_LIMITED,
                FailureKind.TIMEOUT,
            }),
            SafeSummary("inventory poll attempt failed safely"),
        )
        compatibility = (
            "incompatible"
            if kind is FailureKind.INCOMPATIBLE
            else "contract_drift"
            if kind is FailureKind.INVALID_RESPONSE
            else "supported"
        )
        failed = self._repository.fail(
            claim, failure, usage, ended, compatibility=compatibility
        )
        if not failed.accepted or type(failed.value) is not PollInspection:
            return InventoryPollResult(InventoryPollOutcome.FAILED, None, inventory)
        outcome = {
            PollState.RETRY_WAIT: InventoryPollOutcome.DEFERRED,
            PollState.BLOCKED: InventoryPollOutcome.BLOCKED,
            PollState.CANCELLED: InventoryPollOutcome.FAILED,
            PollState.FAILED_TERMINAL: InventoryPollOutcome.FAILED,
        }.get(failed.value.state, InventoryPollOutcome.FAILED)
        return InventoryPollResult(outcome, failed.value, inventory)


def _usage(result: InventoryRunResult) -> ScanUsage:
    return sum(
        (
            item.usage
            for item in result.libraries
        ),
        ScanUsage(),
    )


def _failure_kind(result: InventoryRunResult) -> FailureKind:
    values = [
        item.inspection.failure_kind
        for item in result.libraries
        if item.inspection is not None and item.inspection.failure_kind is not None
    ]
    priority = (
        FailureKind.CANCELLED,
        FailureKind.AUTHENTICATION,
        FailureKind.AUTHORIZATION,
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


__all__ = [
    "InventoryPollOutcome",
    "InventoryPollResult",
    "InventoryPollingRepository",
    "RunInventoryPoll",
]
