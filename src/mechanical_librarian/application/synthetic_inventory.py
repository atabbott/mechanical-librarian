"""Application orchestration for the bounded F03 synthetic inventory round trip."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.application.job_heartbeat import (
    RepositoryOutcome,
    RepositoryResult,
)
from mechanical_librarian.application.ports import (
    FailureKind,
    LibraryListRequest,
    LibraryReadRequest,
    LibraryReader,
    OperationContext,
    Page,
    PageRequest,
    PortResult,
    SnapshotRepository,
    SnapshotStoreRequest,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    Fingerprint,
    IdempotencyKey,
    LibraryId,
    SnapshotId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    ItemLifecycle,
    ItemSnapshot,
    LibraryConnection,
    MediaType,
)


class SyntheticInventoryOutcome(StrEnum):
    READY = "ready"
    CANCELLED = "cancelled"
    DEADLINE_EXPIRED = "deadline_expired"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    READER_FAILURE = "reader_failure"
    INVALID_RESPONSE = "invalid_response"
    PERSISTENCE_FAILURE = "persistence_failure"
    READBACK_MISMATCH = "readback_mismatch"
    CONFIGURATION_FAILURE = "configuration_failure"
    SETUP_FAILURE = "setup_failure"
    INTERNAL_FAILURE = "internal_failure"


@dataclass(frozen=True)
class SyntheticInventoryInspection:
    """Public-safe immutable inspection of one persisted canonical snapshot."""

    adapter: AdapterId
    connection: ConnectionId
    library: LibraryId
    snapshot: SnapshotId
    media_type: MediaType
    lifecycle: ItemLifecycle
    fingerprint: Fingerprint
    repository_outcome: RepositoryOutcome
    item_count: int = 1
    history_count: int = 1
    current_count: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.adapter) is not AdapterId
            or type(self.connection) is not ConnectionId
            or type(self.library) is not LibraryId
            or type(self.snapshot) is not SnapshotId
            or type(self.media_type) is not MediaType
            or type(self.lifecycle) is not ItemLifecycle
            or type(self.fingerprint) is not Fingerprint
            or self.repository_outcome
            not in {RepositoryOutcome.ACCEPTED, RepositoryOutcome.REUSED}
            or (self.item_count, self.history_count, self.current_count) != (1, 1, 1)
        ):
            raise ValueError("synthetic inventory inspection is invalid")


@dataclass(frozen=True)
class SyntheticInventoryResult:
    outcome: SyntheticInventoryOutcome
    inspection: SyntheticInventoryInspection | None = None

    def __post_init__(self) -> None:
        if type(self.outcome) is not SyntheticInventoryOutcome:
            raise ValueError("synthetic inventory result requires a typed outcome")
        if self.outcome is SyntheticInventoryOutcome.READY:
            if type(self.inspection) is not SyntheticInventoryInspection:
                raise ValueError("successful inventory results require an inspection")
        elif self.inspection is not None:
            raise ValueError("failed inventory results cannot expose partial inspection")

    @property
    def succeeded(self) -> bool:
        return self.outcome is SyntheticInventoryOutcome.READY


class InspectSyntheticInventory:
    """Read, persist, and independently read back one exact synthetic snapshot."""

    def __init__(self, reader: LibraryReader, repository: SnapshotRepository) -> None:
        self._reader = reader
        self._repository = repository

    def run(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        context: OperationContext,
    ) -> SyntheticInventoryResult:
        if (
            type(connection) is not LibraryConnection
            or type(library) is not LibraryId
            or type(context) is not OperationContext
        ):
            return _failed(SyntheticInventoryOutcome.INVALID_RESPONSE)

        preflight = context.preflight_result(item_count=1, serialized_bytes=0)
        if preflight.failure is not None:
            return _failed(_context_outcome(preflight.failure.kind))

        capabilities_result = self._reader.discover_capabilities(connection, context)
        capabilities = _port_value(capabilities_result, AdapterCapabilities)
        if capabilities is None:
            return _failed(_port_outcome(capabilities_result))
        if not _valid_capabilities(connection, capabilities):
            return _failed(SyntheticInventoryOutcome.INVALID_RESPONSE)

        listed_result = self._reader.list_snapshots(
            LibraryListRequest(connection, PageRequest(None, 1), context)
        )
        page = _port_value(listed_result, Page)
        if page is None:
            return _failed(_port_outcome(listed_result))
        if len(page.items) != 1 or page.next_cursor is not None:
            return _failed(SyntheticInventoryOutcome.INVALID_RESPONSE)
        snapshot = page.items[0]
        if type(snapshot) is not ItemSnapshot or not _valid_snapshot_scope(
            snapshot, connection, library, capabilities
        ):
            return _failed(SyntheticInventoryOutcome.INVALID_RESPONSE)

        size = len(snapshot.canonical_input.serialized.encode("utf-8"))
        bounded = context.preflight_result(item_count=1, serialized_bytes=size)
        if bounded.failure is not None:
            return _failed(_context_outcome(bounded.failure.kind))

        key = IdempotencyKey.from_canonical_input(
            operation="snapshot-store",
            target=snapshot.target,
            value={
                "fingerprint": snapshot.fingerprint.digest,
                "schema": snapshot.fingerprint.schema.value,
                "snapshot": snapshot.id.value,
            },
        )
        stored = self._repository.store(
            SnapshotStoreRequest(snapshot, key, None, context)
        )
        if not _valid_repository_snapshot(stored, snapshot):
            return _failed(SyntheticInventoryOutcome.PERSISTENCE_FAILURE)

        readback = self._repository.read(LibraryReadRequest(snapshot.target, context))
        if not _valid_readback(readback, snapshot):
            return _failed(SyntheticInventoryOutcome.READBACK_MISMATCH)

        return SyntheticInventoryResult(
            SyntheticInventoryOutcome.READY,
            SyntheticInventoryInspection(
                adapter=snapshot.target.adapter,
                connection=snapshot.target.connection,
                library=snapshot.target.library,
                snapshot=snapshot.id,
                media_type=snapshot.media_type,
                lifecycle=snapshot.lifecycle,
                fingerprint=snapshot.fingerprint,
                repository_outcome=stored.outcome,
            ),
        )


def _valid_capabilities(
    connection: LibraryConnection, capabilities: AdapterCapabilities
) -> bool:
    return (
        capabilities == connection.capabilities
        and capabilities.inventory_read
        and not capabilities.metadata_read
        and not capabilities.media_file_read
        and not capabilities.notifications
        and not capabilities.pagination
        and not capabilities.concurrency_tokens
        and not capabilities.cover_read
        and not capabilities.write_supported
    )


def _valid_snapshot_scope(
    snapshot: ItemSnapshot,
    connection: LibraryConnection,
    library: LibraryId,
    capabilities: AdapterCapabilities,
) -> bool:
    return (
        snapshot.target.adapter == connection.adapter
        and snapshot.target.connection == connection.id
        and snapshot.target.library == library
        and snapshot.capabilities == capabilities
        and snapshot.media_type in capabilities.supported_media_types
    )


def _valid_repository_snapshot(
    result: object, expected: ItemSnapshot
) -> bool:
    return (
        type(result) is RepositoryResult
        and result.outcome in {RepositoryOutcome.ACCEPTED, RepositoryOutcome.REUSED}
        and type(result.value) is ItemSnapshot
        and result.value == expected
    )


def _valid_readback(result: object, expected: ItemSnapshot) -> bool:
    return (
        type(result) is RepositoryResult
        and result.outcome in {RepositoryOutcome.ACCEPTED, RepositoryOutcome.REUSED}
        and type(result.value) is ItemSnapshot
        and result.value == expected
    )


def _port_value(result: object, expected_type: type[object]) -> object | None:
    if (
        type(result) is not PortResult
        or result.failure is not None
        or type(result.value) is not expected_type
    ):
        return None
    return result.value


def _port_outcome(result: object) -> SyntheticInventoryOutcome:
    if type(result) is not PortResult or result.failure is None:
        return SyntheticInventoryOutcome.INVALID_RESPONSE
    return _context_outcome(result.failure.kind, default=SyntheticInventoryOutcome.READER_FAILURE)


def _context_outcome(
    kind: FailureKind,
    *,
    default: SyntheticInventoryOutcome = SyntheticInventoryOutcome.INVALID_RESPONSE,
) -> SyntheticInventoryOutcome:
    return {
        FailureKind.CANCELLED: SyntheticInventoryOutcome.CANCELLED,
        FailureKind.TIMEOUT: SyntheticInventoryOutcome.DEADLINE_EXPIRED,
        FailureKind.RESOURCE_EXHAUSTED: SyntheticInventoryOutcome.RESOURCE_EXHAUSTED,
    }.get(kind, default)


def _failed(outcome: SyntheticInventoryOutcome) -> SyntheticInventoryResult:
    return SyntheticInventoryResult(outcome)


__all__ = [
    "InspectSyntheticInventory",
    "SyntheticInventoryInspection",
    "SyntheticInventoryOutcome",
    "SyntheticInventoryResult",
]
