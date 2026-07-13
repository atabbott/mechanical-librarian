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
    PortResult,
)
from mechanical_librarian.domain.identifiers import LibraryId, ScanId, canonical_json
from mechanical_librarian.domain.inventory import LibraryConnection
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
    ) -> RepositoryResult[ScanStart]: ...

    def finish(
        self,
        scan_id: ScanId,
        state: ScanState,
        failure_kind: str,
        usage: ScanUsage,
        finished_at: datetime,
        context: OperationContext,
    ) -> RepositoryResult[InventoryInspection]: ...

    def publish_complete(
        self,
        start: ScanStart,
        snapshots: tuple[InventorySnapshotV2, ...],
        usage: ScanUsage,
        finished_at: datetime,
        context: OperationContext,
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
class InventoryLibraryResult:
    library: LibraryId
    outcome: InventoryOutcome
    inspection: InventoryInspection | None

    def __post_init__(self) -> None:
        if (
            type(self.library) is not LibraryId
            or type(self.outcome) is not InventoryOutcome
            or (
                self.inspection is not None
                and type(self.inspection) is not InventoryInspection
            )
        ):
            raise ValueError("inventory library result is malformed")
        if self.outcome is InventoryOutcome.READY and self.inspection is None:
            raise ValueError("ready inventory result requires inspection")


@dataclass(frozen=True)
class InventoryRunResult:
    outcome: InventoryOutcome
    libraries: tuple[InventoryLibraryResult, ...]

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
    ) -> InventoryRunResult:
        if (
            type(connection) is not LibraryConnection
            or type(libraries) is not tuple
            or not libraries
            or any(type(value) is not LibraryId for value in libraries)
            or len(set(libraries)) != len(libraries)
            or type(bounds) is not ScanBounds
            or type(context) is not OperationContext
        ):
            return InventoryRunResult(InventoryOutcome.INVALID_REQUEST, ())
        recovered = self._repository.recover_interrupted(context.time.now(), context)
        if (
            not _accepted(recovered)
            or type(recovered.value) is not int
            or recovered.value < 0
        ):
            return InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())

        cohort_id = _cohort_id(connection, libraries, context.time.now())
        results = tuple(
            self._run_library(
                connection,
                library,
                bounds,
                context,
                cohort_id,
                len(libraries),
            )
            for library in libraries
        )
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
        return InventoryRunResult(outcome, results)

    def _run_library(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        context: OperationContext,
        cohort_id: str,
        cohort_size: int,
    ) -> InventoryLibraryResult:
        started_at = context.time.now()
        scan_id = _scan_id(connection, library, started_at)
        begun = self._repository.begin(
            scan_id,
            connection,
            library,
            bounds,
            started_at,
            context,
            cohort_id=cohort_id,
            cohort_size=cohort_size,
        )
        if not _accepted(begun) or type(begun.value) is not ScanStart:
            return InventoryLibraryResult(
                library, InventoryOutcome.PERSISTENCE_FAILURE, None
            )
        start = begun.value
        usage = ScanUsage()

        try:
            verified = self._source.verify_library(connection, library, bounds, context)
        except Exception:
            return self._terminal_failure(
                library,
                start,
                usage,
                FailureKind.INVALID_RESPONSE,
                context,
            )
        if verified.failure is not None or type(verified.value) is not ScanUsage:
            return self._terminal_failure(
                library,
                start,
                usage,
                verified.failure.kind
                if verified.failure is not None
                else FailureKind.INVALID_RESPONSE,
                context,
            )
        usage += verified.value

        try:
            first = self._read_pass(connection, library, bounds, usage, context)
        except Exception:
            return self._terminal_failure(
                library,
                start,
                usage,
                FailureKind.INVALID_RESPONSE,
                context,
            )
        if first.failure is not None or type(first.value) is not InventoryPass:
            return self._terminal_failure(
                library,
                start,
                usage,
                first.failure.kind
                if first.failure is not None
                else FailureKind.INVALID_RESPONSE,
                context,
            )
        usage += first.value.usage

        try:
            second = self._read_pass(connection, library, bounds, usage, context)
        except Exception:
            return self._terminal_failure(
                library,
                start,
                usage,
                FailureKind.INVALID_RESPONSE,
                context,
            )
        if second.failure is not None or type(second.value) is not InventoryPass:
            return self._terminal_failure(
                library,
                start,
                usage,
                second.failure.kind
                if second.failure is not None
                else FailureKind.INVALID_RESPONSE,
                context,
            )
        usage += second.value.usage

        if (
            first.value.digest != second.value.digest
            or first.value.recent_item
            or second.value.recent_item
        ):
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
        if inspection is None or inspection.state is not ScanState.COMPLETE:
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
            )
        return InventoryLibraryResult(
            library,
            (
                InventoryOutcome.READY
                if inspection is not None
                else InventoryOutcome.PERSISTENCE_FAILURE
            ),
            inspection,
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
        kind: FailureKind,
        context: OperationContext,
    ) -> InventoryLibraryResult:
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
        )


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


__all__ = [
    "InventoryLibraryResult",
    "InventoryOutcome",
    "InventoryRunResult",
    "InventoryScanRepository",
    "InventorySource",
    "ReconcileInventory",
]
