"""Deterministic, read-only synthetic library adapter for the F03 demonstration."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from mechanical_librarian.application.ports import (
    FailureKind,
    LibraryListRequest,
    LibraryReadRequest,
    OperationContext,
    Page,
    PortFailure,
    PortResult,
    RetryAdvice,
    SafeSummary,
)
from mechanical_librarian.domain.identifiers import (
    AdapterVersion,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    ServiceVersion,
    SnapshotId,
    TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    AdapterRelease,
    InventoryRecord,
    ItemLifecycle,
    ItemSnapshot,
    LibraryConnection,
    LibraryItem,
    MediaType,
    NormalizedMetadata,
    ObservationProvenance,
    RawObservation,
    ServiceRelease,
)


_SYNTHETIC_EXTERNAL_VALUE = "synthetic-item-one"
_OBSERVED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_PAYLOAD_SCHEMA = SchemaVersion("synthetic-inventory-v1")


class SyntheticLibraryReader:
    """Return one canonical observation without network or mutation authority."""

    def __init__(self, connection: LibraryConnection, library: LibraryId) -> None:
        if type(connection) is not LibraryConnection or type(library) is not LibraryId:
            raise ValueError("synthetic reader requires an exact connection and library")
        if connection.capabilities != synthetic_capabilities():
            raise ValueError("synthetic reader requires exact read-only capabilities")
        self._connection = connection
        self._library = library
        self._record = _build_record(connection, library)

    @property
    def record(self) -> InventoryRecord:
        """Expose the immutable canonical fixture, never the external source value."""

        return self._record

    def discover_capabilities(
        self,
        connection: LibraryConnection,
        context: OperationContext,
    ) -> PortResult[AdapterCapabilities]:
        if type(connection) is not LibraryConnection or type(context) is not OperationContext:
            return PortResult.failed(_invalid_response("synthetic request shape invalid"))
        preflight = context.preflight_result(item_count=1, serialized_bytes=0)
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        if connection != self._connection:
            return PortResult.failed(_invalid_response("synthetic connection scope invalid"))
        return PortResult.success(self._record.snapshot.capabilities)

    def list_snapshots(
        self, request: LibraryListRequest
    ) -> PortResult[Page[ItemSnapshot]]:
        if type(request) is not LibraryListRequest:
            return PortResult.failed(_invalid_response("synthetic list request invalid"))
        size = len(self._record.snapshot.canonical_input.serialized.encode("utf-8"))
        preflight = request.context.preflight_result(item_count=1, serialized_bytes=size)
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        if request.connection != self._connection or request.page.cursor is not None:
            return PortResult.failed(_invalid_response("synthetic list scope invalid"))
        return PortResult.success(Page((self._record.snapshot,), None))

    def read_snapshot(self, request: LibraryReadRequest) -> PortResult[ItemSnapshot]:
        if type(request) is not LibraryReadRequest:
            return PortResult.failed(_invalid_response("synthetic read request invalid"))
        size = len(self._record.snapshot.canonical_input.serialized.encode("utf-8"))
        preflight = request.context.preflight_result(item_count=1, serialized_bytes=size)
        if preflight.failure is not None:
            return PortResult.failed(preflight.failure)
        if request.target != self._record.item.target:
            return PortResult.failed(
                PortFailure(
                    FailureKind.NOT_FOUND,
                    RetryAdvice(False),
                    SafeSummary("synthetic item not found"),
                )
            )
        return PortResult.success(self._record.snapshot)


def synthetic_capabilities() -> AdapterCapabilities:
    """Declare the exact authority of the synthetic reader."""

    return AdapterCapabilities(
        inventory_read=True,
        metadata_read=False,
        media_file_read=False,
        notifications=False,
        pagination=False,
        concurrency_tokens=False,
        cover_read=False,
        writable_fields=frozenset(),
        cover_write=False,
        supported_media_types=frozenset({MediaType.EBOOK}),
    )


def _build_record(connection: LibraryConnection, library: LibraryId) -> InventoryRecord:
    external_identity = hashlib.sha256(
        "\x00".join(
            (
                connection.adapter.value,
                connection.id.value,
                library.value,
                _SYNTHETIC_EXTERNAL_VALUE,
            )
        ).encode("utf-8")
    ).hexdigest()
    target = TargetId(
        adapter=connection.adapter,
        connection=connection.id,
        library=library,
        external_item=ExternalItemId(external_identity),
    )
    capabilities = synthetic_capabilities()
    provenance = ObservationProvenance(
        adapter_release=AdapterRelease(connection.adapter, AdapterVersion("synthetic-v1")),
        observed_at=_OBSERVED_AT,
        raw_observation=RawObservation(
            adapter=connection.adapter,
            payload_fingerprint=Fingerprint.from_canonical_value(
                {"item": external_identity, "state": "active"}, schema=_PAYLOAD_SCHEMA
            ),
        ),
    )
    snapshot = ItemSnapshot.create(
        id=SnapshotId("synthetic-snapshot-one"),
        target=target,
        media_type=MediaType.EBOOK,
        lifecycle=ItemLifecycle.ACTIVE,
        metadata=NormalizedMetadata(()),
        files=(),
        cover=None,
        provenance=provenance,
        service_release=ServiceRelease(connection.adapter, ServiceVersion("synthetic-v1")),
        concurrency_token=None,
        capabilities=capabilities,
    )
    item = LibraryItem(
        target=target,
        media_type=snapshot.media_type,
        lifecycle=snapshot.lifecycle,
        snapshot_ids=(snapshot.id,),
    )
    return InventoryRecord(connection=connection, item=item, snapshot=snapshot)


def _invalid_response(summary: str) -> PortFailure:
    return PortFailure(
        FailureKind.INVALID_RESPONSE,
        RetryAdvice(False),
        SafeSummary(summary),
    )


__all__ = ["SyntheticLibraryReader", "synthetic_capabilities"]
