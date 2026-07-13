"""Bounded complete-only inventory reconciliation orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from mechanical_librarian.application.job_heartbeat import (
    RepositoryOutcome,
    RepositoryResult,
)
from mechanical_librarian.application.ports import (
    FailureKind,
    OperationContext,
    PortFailure,
    PortResult,
    RetryAdvice,
    SafeSummary,
)
from mechanical_librarian.domain.identifiers import LibraryId, ScanId, canonical_json
from mechanical_librarian.domain.inventory import LibraryConnection
from mechanical_librarian.domain.inventory_polling import PollClaim
from mechanical_librarian.domain.inventory_v2 import (
    InventoryInspection,
    InventoryPass,
    InventorySnapshotV2,
    ScanBounds,
    ScanStart,
    ScanState,
    ScanUsage,
)


class InventorySource(Protocol):
    """Read-only canonical inventory source with no mutation methods."""

    def verify_connection(
        self,
        connection: LibraryConnection,
        libraries: tuple[LibraryId, ...],
        bounds: ScanBounds,
        context: OperationContext,
    ) -> InventoryConnectionVerification: ...

    def verify_library(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        context: OperationContext,
    ) -> PortResult[ScanUsage]: ...

    def read_pass(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        context: OperationContext,
    ) -> PortResult[InventoryPass]: ...


class InventoryScanRepository(Protocol):
    """Durable scan and v2 snapshot persistence owned by the application."""

    def recover_interrupted(
        self, finished_at: datetime, context: OperationContext
    ) -> RepositoryResult[int]: ...

    def begin(
        self,
        scan_id: ScanId,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        started_at: datetime,
        context: OperationContext,
        *,
        cohort_id: str | None = None,
        cohort_size: int = 1,
        claim: PollClaim | None = None,
    ) -> RepositoryResult[ScanStart]: ...

    def finish(
        self,
        scan_id: ScanId,
        state: ScanState,
        failure_kind: str,
        usage: ScanUsage,
        finished_at: datetime,
        context: OperationContext,
        *,
        claim: PollClaim | None = None,
    ) -> RepositoryResult[InventoryInspection]: ...

    def checkpoint(
        self,
        scan_id: ScanId,
        usage: ScanUsage,
        observed_at: datetime,
        context: OperationContext,
        *,
        claim: PollClaim | None = None,
    ) -> RepositoryResult[InventoryInspection]: ...

    def publish_complete(
        self,
        start: ScanStart,
        snapshots: tuple[InventorySnapshotV2, ...],
        usage: ScanUsage,
        finished_at: datetime,
        context: OperationContext,
        *,
        claim: PollClaim | None = None,
    ) -> RepositoryResult[InventoryInspection]: ...

    def inspect(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        context: OperationContext,
    ) -> RepositoryResult[InventoryInspection]: ...

    def inspect_scan(
        self,
        scan_id: ScanId,
        context: OperationContext,
    ) -> RepositoryResult[InventoryInspection]: ...


class InventoryOutcome(StrEnum):
    READY = "ready"
    INCOMPLETE = "incomplete"
    UNSTABLE = "unstable"
    PERSISTENCE_FAILURE = "persistence_failure"
    INVALID_REQUEST = "invalid_request"


@dataclass(frozen=True)
class InventoryConnectionVerification:
    """Run-scoped compatibility evidence gathered after durable cohort start."""

    usage: ScanUsage
    failure: FailureKind | None = None
    compatibility: str = "unknown"

    def __post_init__(self) -> None:
        if (
            type(self.usage) is not ScanUsage
            or (self.failure is not None and type(self.failure) is not FailureKind)
            or self.compatibility
            not in {"supported", "incompatible", "unknown", "contract_drift"}
        ):
            raise ValueError("inventory connection verification is malformed")


@dataclass(frozen=True)
class InventoryLibraryResult:
    library: LibraryId
    outcome: InventoryOutcome
    inspection: InventoryInspection | None
    usage: ScanUsage = ScanUsage()
    failure: PortFailure | None = None

    def __post_init__(self) -> None:
        if (
            type(self.library) is not LibraryId
            or type(self.outcome) is not InventoryOutcome
            or (
                self.inspection is not None
                and type(self.inspection) is not InventoryInspection
            )
            or type(self.usage) is not ScanUsage
            or (self.failure is not None and type(self.failure) is not PortFailure)
        ):
            raise ValueError("inventory library result is malformed")
        if self.outcome is InventoryOutcome.READY and self.inspection is None:
            raise ValueError("ready inventory result requires inspection")


@dataclass(frozen=True)
class InventoryRunResult:
    outcome: InventoryOutcome
    libraries: tuple[InventoryLibraryResult, ...]
    compatibility: str = "unknown"

    def __post_init__(self) -> None:
        if self.compatibility not in {
            "supported",
            "incompatible",
            "unknown",
            "contract_drift",
        }:
            raise ValueError("inventory compatibility state is malformed")

    @property
    def succeeded(self) -> bool:
        return self.outcome is InventoryOutcome.READY


class ReconcileInventory:
    """Prove two bounded passes equal before publishing absence conclusions."""

    def __init__(
        self,
        source: InventorySource,
        repository: InventoryScanRepository,
    ) -> None:
        self._source = source
        self._repository = repository

    def run(
        self,
        connection: LibraryConnection,
        libraries: tuple[LibraryId, ...],
        bounds: ScanBounds,
        context: OperationContext,
        *,
        claim: PollClaim | None = None,
    ) -> InventoryRunResult:
        if (
            type(connection) is not LibraryConnection
            or type(libraries) is not tuple
            or not libraries
            or any(type(value) is not LibraryId for value in libraries)
            or len(set(libraries)) != len(libraries)
            or type(bounds) is not ScanBounds
            or type(context) is not OperationContext
            or (claim is not None and type(claim) is not PollClaim)
        ):
            return InventoryRunResult(InventoryOutcome.INVALID_REQUEST, ())
        if claim is not None:
            return ReconcileInventory(
                self._source,
                _ClaimBoundInventoryRepository(self._repository, claim),
            ).run(connection, libraries, bounds, context)
        recovered = self._repository.recover_interrupted(context.time.now(), context)
        if (
            not _accepted(recovered)
            or type(recovered.value) is not int
            or recovered.value < 0
        ):
            return InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())

        cohort_id = _cohort_id(connection, libraries, context.time.now())
        starts: dict[LibraryId, ScanStart] = {}
        for library in libraries:
            started_at = context.time.now()
            begun = self._repository.begin(
                _scan_id(connection, library, started_at),
                connection,
                library,
                bounds,
                started_at,
                context,
                cohort_id=cohort_id,
                cohort_size=len(libraries),
            )
            if not _accepted(begun) or type(begun.value) is not ScanStart:
                live = context.preflight_result(item_count=0, serialized_bytes=0)
                if live.failure is not None and live.failure.kind in {
                    FailureKind.CANCELLED,
                    FailureKind.TIMEOUT,
                }:
                    failure = live.failure.kind
                else:
                    failure = (
                        FailureKind.CONFLICT
                        if type(begun) is RepositoryResult
                        and begun.outcome is RepositoryOutcome.REJECTED
                        else FailureKind.UNAVAILABLE
                    )
                failed: list[InventoryLibraryResult] = []
                for candidate in libraries:
                    start = starts.get(candidate)
                    failed.append(
                        self._terminal_failure(
                            candidate, start, ScanUsage(), failure, context
                        )
                        if start is not None
                        else InventoryLibraryResult(
                            candidate, InventoryOutcome.PERSISTENCE_FAILURE, None
                        )
                    )
                return InventoryRunResult(
                    InventoryOutcome.PERSISTENCE_FAILURE, tuple(failed)
                )
            starts[library] = begun.value

        try:
            connection_verification = self._source.verify_connection(
                connection, libraries, bounds, context
            )
        except Exception:
            connection_verification = InventoryConnectionVerification(
                ScanUsage(), FailureKind.INVALID_RESPONSE
            )
        if type(connection_verification) is not InventoryConnectionVerification:
            connection_verification = InventoryConnectionVerification(
                ScanUsage(), FailureKind.INVALID_RESPONSE
            )
        live_failure = _liveness_failure(context)
        if live_failure is not None:
            connection_verification = InventoryConnectionVerification(
                connection_verification.usage,
                live_failure,
                connection_verification.compatibility,
            )
        if connection_verification.failure is not None:
            results = tuple(
                self._terminal_failure(
                    library,
                    starts[library],
                    connection_verification.usage,
                    connection_verification.failure,
                    context,
                )
                for library in libraries
            )
        else:
            checkpoint = context.preflight_result(
                item_count=connection_verification.usage.items,
                serialized_bytes=connection_verification.usage.response_bytes,
            )
            failure = (
                checkpoint.failure.kind
                if checkpoint.failure is not None
                else (
                    FailureKind.RESOURCE_EXHAUSTED
                    if connection_verification.usage.response_bytes > bounds.max_bytes
                    else None
                )
            )
            if failure is not None:
                results = tuple(
                    self._terminal_failure(
                        library,
                        starts[library],
                        connection_verification.usage,
                        failure,
                        context,
                    )
                    for library in libraries
                )
            else:
                results = tuple(
                    self._run_library(
                        connection,
                        library,
                        bounds,
                        context,
                        starts[library],
                        connection_verification.usage,
                    )
                    for library in libraries
                )
        if not all(result.outcome is InventoryOutcome.READY for result in results):
            discarded: list[InventoryLibraryResult] = []
            for result in results:
                inspection = result.inspection
                if (
                    result.outcome is InventoryOutcome.READY
                    and inspection is not None
                    and inspection.state is ScanState.RUNNING
                ):
                    discarded.append(
                        self._terminal_failure(
                            result.library,
                            starts[result.library],
                            ScanUsage(
                                inspection.page_count,
                                inspection.item_count,
                                inspection.response_bytes,
                            ),
                            FailureKind.CONFLICT,
                            context,
                        )
                    )
                else:
                    discarded.append(result)
            results = tuple(discarded)
        refreshed: list[InventoryLibraryResult] = []
        for result in results:
            if result.outcome is not InventoryOutcome.READY:
                refreshed.append(result)
                continue
            inspected = self._repository.inspect_scan(
                result.inspection.scan_id, context
            )
            inspection = _inspection(inspected)
            refreshed.append(
                InventoryLibraryResult(
                    result.library,
                    (
                        InventoryOutcome.READY
                        if inspection is not None
                        and inspection.state is ScanState.COMPLETE
                        else InventoryOutcome.PERSISTENCE_FAILURE
                    ),
                    inspection,
                    result.usage,
                    result.failure,
                )
            )
        results = tuple(refreshed)
        if all(result.outcome is InventoryOutcome.READY for result in results):
            outcome = InventoryOutcome.READY
        elif any(
            result.outcome is InventoryOutcome.PERSISTENCE_FAILURE for result in results
        ):
            outcome = InventoryOutcome.PERSISTENCE_FAILURE
        elif any(result.outcome is InventoryOutcome.UNSTABLE for result in results):
            outcome = InventoryOutcome.UNSTABLE
        else:
            outcome = InventoryOutcome.INCOMPLETE
        return InventoryRunResult(
            outcome, results, connection_verification.compatibility
        )

    def _run_library(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        context: OperationContext,
        start: ScanStart,
        initial_usage: ScanUsage,
    ) -> InventoryLibraryResult:
        usage = initial_usage

        if bounds.max_bytes - usage.response_bytes < 1_024:
            return self._terminal_failure(
                library,
                start,
                usage,
                FailureKind.RESOURCE_EXHAUSTED,
                context,
            )
        verification_bounds = ScanBounds(
            bounds.page_size,
            bounds.max_pages,
            bounds.max_items,
            bounds.max_bytes - usage.response_bytes,
            bounds.max_seconds,
            bounds.stability_delay_seconds,
        )

        verified = None
        try:
            verified = self._source.verify_library(
                connection, library, verification_bounds, context
            )
        except Exception:
            pass
        verified_usage = (
            verified.value
            if verified is not None
            and verified.failure is None
            and type(verified.value) is ScanUsage
            else None
        )
        if verified_usage is not None:
            usage += verified_usage
            if not self._checkpoint(start, usage, context):
                return self._terminal_failure(
                    library, start, usage, FailureKind.UNAVAILABLE, context
                )
        live_failure = _liveness_failure(context)
        if live_failure is not None:
            return self._terminal_failure(
                library,
                start,
                usage,
                live_failure,
                context,
            )
        if verified is None:
            return self._terminal_failure(
                library, start, usage, FailureKind.INVALID_RESPONSE, context
            )
        if verified.failure is not None or type(verified.value) is not ScanUsage:
            if verified.failure is not None:
                usage += _failure_usage(verified.failure)
            return self._terminal_failure(
                library,
                start,
                usage,
                verified.failure
                if verified.failure is not None
                else FailureKind.INVALID_RESPONSE,
                context,
            )

        first = None
        try:
            first = self._read_pass(connection, library, bounds, usage, context)
        except Exception:
            pass
        first_usage = (
            first.value.usage
            if first is not None
            and first.failure is None
            and type(first.value) is InventoryPass
            else None
        )
        if first_usage is not None:
            usage += first_usage
            if not self._checkpoint(start, usage, context):
                return self._terminal_failure(
                    library, start, usage, FailureKind.UNAVAILABLE, context
                )
        live_failure = _liveness_failure(context)
        if live_failure is not None:
            return self._terminal_failure(
                library,
                start,
                usage,
                live_failure,
                context,
            )
        if first is None:
            return self._terminal_failure(
                library, start, usage, FailureKind.INVALID_RESPONSE, context
            )
        if first.failure is not None or type(first.value) is not InventoryPass:
            if first.failure is not None:
                usage += _failure_usage(first.failure)
            return self._terminal_failure(
                library,
                start,
                usage,
                first.failure
                if first.failure is not None
                else FailureKind.INVALID_RESPONSE,
                context,
            )

        second = None
        try:
            second = self._read_pass(connection, library, bounds, usage, context)
        except Exception:
            pass
        second_usage = (
            second.value.usage
            if second is not None
            and second.failure is None
            and type(second.value) is InventoryPass
            else None
        )
        if second_usage is not None:
            usage += second_usage
            if not self._checkpoint(start, usage, context):
                return self._terminal_failure(
                    library, start, usage, FailureKind.UNAVAILABLE, context
                )
        live_failure = _liveness_failure(context)
        if live_failure is not None:
            return self._terminal_failure(
                library,
                start,
                usage,
                live_failure,
                context,
            )
        if second is None:
            return self._terminal_failure(
                library, start, usage, FailureKind.INVALID_RESPONSE, context
            )
        if second.failure is not None or type(second.value) is not InventoryPass:
            if second.failure is not None:
                usage += _failure_usage(second.failure)
            return self._terminal_failure(
                library,
                start,
                usage,
                second.failure
                if second.failure is not None
                else FailureKind.INVALID_RESPONSE,
                context,
            )

        if (
            first.value.digest != second.value.digest
            or first.value.recent_item
            or second.value.recent_item
        ):
            live_failure = _liveness_failure(context)
            if live_failure is not None:
                return self._terminal_failure(
                    library, start, usage, live_failure, context
                )
            finished = self._repository.finish(
                start.scan_id,
                ScanState.UNSTABLE,
                FailureKind.CONFLICT.value,
                usage,
                context.time.now(),
                context,
            )
            return InventoryLibraryResult(
                library,
                (
                    InventoryOutcome.UNSTABLE
                    if _inspection(finished) is not None
                    else InventoryOutcome.PERSISTENCE_FAILURE
                ),
                _inspection(finished),
                usage,
            )

        seen = {snapshot.observation.target for snapshot in second.value.snapshots}
        removals = tuple(
            snapshot.removed(observed_at=context.time.now())
            for snapshot in start.current
            if snapshot.observation.target not in seen
            and snapshot.observation.lifecycle.value != "removed"
        )
        retained_removals = tuple(
            snapshot
            for snapshot in start.current
            if snapshot.observation.target not in seen
            and snapshot.observation.lifecycle.value == "removed"
        )
        final_snapshots = (
            *second.value.snapshots,
            *removals,
            *retained_removals,
        )
        final_bytes = sum(
            len(snapshot.observation.canonical.encode("utf-8"))
            for snapshot in final_snapshots
        )
        checkpoint = context.preflight_result(
            item_count=len(final_snapshots),
            serialized_bytes=final_bytes,
        )
        if checkpoint.failure is not None:
            return self._terminal_failure(
                library,
                start,
                usage,
                checkpoint.failure.kind,
                context,
            )

        published = self._repository.publish_complete(
            start,
            final_snapshots,
            usage,
            context.time.now(),
            context,
        )
        inspection = _inspection(published)
        if inspection is None or inspection.state not in {
            ScanState.RUNNING,
            ScanState.COMPLETE,
        }:
            checkpoint = context.preflight_result(
                item_count=len(final_snapshots),
                serialized_bytes=final_bytes,
            )
            rejected = (
                type(published) is RepositoryResult
                and published.outcome is RepositoryOutcome.REJECTED
            )
            persistence_failure = not rejected
            if checkpoint.failure is not None:
                failure_kind = checkpoint.failure.kind
                if failure_kind in {FailureKind.CANCELLED, FailureKind.TIMEOUT}:
                    persistence_failure = False
            elif rejected:
                failure_kind = FailureKind.CONFLICT
            else:
                failure_kind = FailureKind.UNAVAILABLE
            terminal_state = (
                ScanState.UNSTABLE
                if failure_kind is FailureKind.CONFLICT
                else ScanState.INCOMPLETE
            )
            conflicted = self._repository.finish(
                start.scan_id,
                terminal_state,
                failure_kind.value,
                usage,
                context.time.now(),
                context,
            )
            conflicted_inspection = _inspection(conflicted)
            return InventoryLibraryResult(
                library,
                (
                    InventoryOutcome.PERSISTENCE_FAILURE
                    if persistence_failure or conflicted_inspection is None
                    else (
                        InventoryOutcome.UNSTABLE
                        if terminal_state is ScanState.UNSTABLE
                        else InventoryOutcome.INCOMPLETE
                    )
                ),
                conflicted_inspection,
                usage,
            )
        return InventoryLibraryResult(
            library,
            (
                InventoryOutcome.READY
                if inspection is not None
                else InventoryOutcome.PERSISTENCE_FAILURE
            ),
            inspection,
            usage,
        )


    def _read_pass(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        usage: ScanUsage,
        context: OperationContext,
    ) -> PortResult[InventoryPass]:
        if (
            usage.pages >= bounds.max_pages
            or usage.items >= bounds.max_items
            or bounds.max_bytes - usage.response_bytes < 1_024
        ):
            from mechanical_librarian.application.ports import (
                PortFailure,
                RetryAdvice,
                SafeSummary,
            )

            return PortResult.failed(
                PortFailure(
                    FailureKind.RESOURCE_EXHAUSTED,
                    RetryAdvice(False),
                    SafeSummary("inventory scan resource bound exhausted"),
                )
            )
        remaining = ScanBounds(
            bounds.page_size,
            bounds.max_pages - usage.pages,
            bounds.max_items - usage.items,
            bounds.max_bytes - usage.response_bytes,
            bounds.max_seconds,
            bounds.stability_delay_seconds,
        )
        return self._source.read_pass(connection, library, remaining, context)

    def _terminal_failure(
        self,
        library: LibraryId,
        start: ScanStart,
        usage: ScanUsage,
        failure: FailureKind | PortFailure,
        context: OperationContext,
    ) -> InventoryLibraryResult:
        port_failure = (
            failure
            if type(failure) is PortFailure
            else PortFailure(
                failure,
                RetryAdvice(
                    failure in {
                        FailureKind.UNAVAILABLE,
                        FailureKind.RATE_LIMITED,
                        FailureKind.TIMEOUT,
                    }
                ),
                SafeSummary("inventory reconciliation failed safely"),
            )
        )
        kind = port_failure.kind
        state = (
            ScanState.UNSTABLE if kind is FailureKind.CONFLICT else ScanState.INCOMPLETE
        )
        finished = self._repository.finish(
            start.scan_id,
            state,
            kind.value,
            usage,
            context.time.now(),
            context,
        )
        inspection = _inspection(finished)
        return InventoryLibraryResult(
            library,
            (
                InventoryOutcome.PERSISTENCE_FAILURE
                if inspection is None
                else (
                    InventoryOutcome.UNSTABLE
                    if state is ScanState.UNSTABLE
                    else InventoryOutcome.INCOMPLETE
                )
            ),
            inspection,
            usage,
            port_failure,
        )

    def _checkpoint(
        self, start: ScanStart, usage: ScanUsage, context: OperationContext
    ) -> bool:
        if _liveness_failure(context) is not None:
            return True
        checkpoint = getattr(self._repository, "checkpoint", None)
        if checkpoint is None:
            return True
        result = checkpoint(
            start.scan_id,
            usage,
            context.time.now(),
            context,
        )
        return _accepted(result) and _inspection(result) is not None


class _ClaimBoundInventoryRepository:
    """Bind every scan mutation to one durable poll claim without global state."""

    def __init__(self, repository: InventoryScanRepository, claim: PollClaim) -> None:
        self._repository = repository
        self._claim = claim

    def recover_interrupted(
        self, _finished_at: datetime, _context: OperationContext
    ) -> RepositoryResult[int]:
        return RepositoryResult(RepositoryOutcome.ACCEPTED, 0)

    def begin(self, *args, **kwargs):
        return self._repository.begin(*args, **kwargs, claim=self._claim)

    def finish(self, *args, **kwargs):
        return self._repository.finish(*args, **kwargs, claim=self._claim)

    def checkpoint(self, *args, **kwargs):
        return self._repository.checkpoint(*args, **kwargs, claim=self._claim)

    def publish_complete(self, *args, **kwargs):
        return self._repository.publish_complete(*args, **kwargs, claim=self._claim)

    def inspect(self, *args, **kwargs):
        return self._repository.inspect(*args, **kwargs)

    def inspect_scan(self, *args, **kwargs):
        return self._repository.inspect_scan(*args, **kwargs)


def _scan_id(
    connection: LibraryConnection, library: LibraryId, started_at: datetime
) -> ScanId:
    digest = hashlib.sha256(
        canonical_json(
            {
                "adapter": connection.adapter.value,
                "connection": connection.id.value,
                "library": library.value,
                "started_at": started_at.isoformat(),
            }
        ).encode("utf-8")
    ).hexdigest()
    return ScanId("scan-" + digest[:32])


def _failure_usage(failure) -> ScanUsage:
    return ScanUsage(
        failure.usage.pages,
        failure.usage.items,
        failure.usage.response_bytes,
    )


def _cohort_id(
    connection: LibraryConnection,
    libraries: tuple[LibraryId, ...],
    started_at: datetime,
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "adapter": connection.adapter.value,
                "connection": connection.id.value,
                "libraries": sorted(library.value for library in libraries),
                "started_at": started_at.isoformat(),
            }
        ).encode("utf-8")
    ).hexdigest()
    return "cohort-" + digest[:32]


def _accepted(result: object) -> bool:
    return type(result) is RepositoryResult and result.outcome in {
        RepositoryOutcome.ACCEPTED,
        RepositoryOutcome.REUSED,
    }


def _inspection(result: object) -> InventoryInspection | None:
    if not _accepted(result) or type(result.value) is not InventoryInspection:
        return None
    return result.value


def _liveness_failure(context: OperationContext) -> FailureKind | None:
    checkpoint = context.preflight_result(item_count=0, serialized_bytes=0)
    if checkpoint.failure is not None and checkpoint.failure.kind in {
        FailureKind.CANCELLED,
        FailureKind.TIMEOUT,
    }:
        return checkpoint.failure.kind
    return None


__all__ = [
    "InventoryConnectionVerification",
    "InventoryLibraryResult",
    "InventoryOutcome",
    "InventoryRunResult",
    "InventoryScanRepository",
    "InventorySource",
    "ReconcileInventory",
]
