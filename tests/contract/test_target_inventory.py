from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from mechanical_librarian.adapters.library_services.audiobookshelf_inventory import (
    AudiobookshelfInventoryReader,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryConnectionVerification,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    FailureKind,
    OperationContext,
    PortFailure,
    PortResult,
    ResourceBounds,
    RetryAdvice,
    SafeSummary,
    TimeProbe,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    AdapterVersion,
    ConnectionId,
    ExternalItemId,
    LibraryId,
    SchemaVersion,
    ServiceVersion,
    TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    ItemLifecycle,
    LibraryConnection,
    MediaType,
    SecretReference,
    ServiceEndpoint,
)
from mechanical_librarian.domain.inventory_v2 import (
    FieldObservation,
    InventoryObservation,
    InventoryPass,
    InventorySnapshotV2,
    ObservationField,
    ObservationState,
    ScanBounds,
    ScanUsage,
)
from tests.contract.target_inventory import (
    TargetInventoryContract,
    TargetInventoryContractFixture,
)


NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
LIBRARY = LibraryId("01234567-89ab-4def-8123-456789abcdef")
FIXTURES = Path(__file__).parents[1] / "fixtures" / "audiobookshelf" / "2.35.1"


def _context() -> OperationContext:
    return OperationContext(
        Deadline(NOW + timedelta(minutes=10)),
        CancellationProbe(CancellationId("target-inventory-contract"), lambda: False),
        ResourceBounds(100, 1_000_000),
        TimeProbe(lambda: NOW),
    )


def _capabilities() -> AdapterCapabilities:
    return AdapterCapabilities(
        inventory_read=True,
        metadata_read=True,
        media_file_read=False,
        notifications=False,
        pagination=True,
        concurrency_tokens=False,
        cover_read=True,
        writable_fields=frozenset(),
        cover_write=False,
        supported_media_types=frozenset({MediaType.AUDIOBOOK, MediaType.EBOOK}),
    )


def _connection(adapter: str) -> LibraryConnection:
    return LibraryConnection(
        ConnectionId(f"{adapter}-contract"),
        AdapterId(adapter),
        ServiceEndpoint("https://target.invalid"),
        SecretReference("synthetic-secret-reference"),
        _capabilities(),
    )


def _response(value: object) -> httpx.Response:
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    return httpx.Response(
        200,
        stream=httpx.ByteStream(payload),
        headers={"content-type": "application/json", "content-length": str(len(payload))},
    )


def _abs_reader() -> AudiobookshelfInventoryReader:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/libraries":
            name = "libraries.json"
        elif request.url.path.endswith("/items"):
            name = "library-items-page-0.json"
        elif request.url.path.endswith("11234567-89ab-4def-8123-456789abcdef"):
            name = "item-audiobook-expanded.json"
        else:
            name = "item-ebook-expanded.json"
        return _response(json.loads((FIXTURES / name).read_text(encoding="utf-8")))

    return AudiobookshelfInventoryReader(
        client_factory=lambda: PortResult.success(
            httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        )
    )


class _CwaShapedInventoryFake:
    """Contract fixture only; deliberately not a production target adapter."""

    def verify_connection(
        self,
        connection: LibraryConnection,
        libraries: tuple[LibraryId, ...],
        bounds: ScanBounds,
        context: OperationContext,
    ) -> InventoryConnectionVerification:
        del libraries, bounds, context
        if connection.adapter != AdapterId("cwa-shaped"):
            return InventoryConnectionVerification(
                ScanUsage(), FailureKind.INVALID_REQUEST
            )
        return InventoryConnectionVerification(ScanUsage())

    def verify_library(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        context: OperationContext,
    ) -> PortResult[ScanUsage]:
        del library, bounds, context
        if connection.adapter != AdapterId("cwa-shaped"):
            return _invalid_request()
        return PortResult.success(ScanUsage(response_bytes=64))

    def read_pass(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        context: OperationContext,
    ) -> PortResult[InventoryPass]:
        if connection.adapter != AdapterId("cwa-shaped"):
            return _invalid_request()
        observation = InventoryObservation.create(
            target=TargetId(
                connection.adapter,
                connection.id,
                library,
                ExternalItemId("synthetic-cwa-item"),
            ),
            media_type=MediaType.EBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
            fields=(
                FieldObservation(
                    ObservationField.TITLE,
                    ObservationState.PRESENT,
                    ("Synthetic contract title",),
                    ('"Synthetic contract title"',),
                ),
            ),
            file_identities=(hashlib.sha256(b"synthetic-file").hexdigest(),),
            cover_identity=None,
            adapter_version=AdapterVersion("cwa-shaped-contract-v1"),
            normalizer_version=SchemaVersion("cwa-shaped-normalizer-v1"),
            service_version=ServiceVersion("fixture-1"),
            warnings=(),
            observed_at=context.observed_at,
        )
        snapshot = InventorySnapshotV2.create(observation)
        return PortResult.success(
            InventoryPass((snapshot,), ScanUsage(pages=1, items=1, response_bytes=128))
        )


def _invalid_request() -> PortResult:
    return PortResult.failed(
        PortFailure(
            FailureKind.INVALID_REQUEST,
            RetryAdvice(False),
            SafeSummary("inventory request invalid"),
        )
    )


class TargetInventoryReaderContractTests(unittest.TestCase):
    def test_audiobookshelf_and_cwa_shaped_readers_share_canonical_contract(
        self,
    ) -> None:
        cases = (
            (_abs_reader(), _connection("audiobookshelf")),
            (_CwaShapedInventoryFake(), _connection("cwa-shaped")),
        )
        bounds = ScanBounds(2, 4, 10, 1_000_000, 300, 0)
        for reader, connection in cases:
            with self.subTest(adapter=connection.adapter.value):
                TargetInventoryContract(self).require(
                    TargetInventoryContractFixture(
                        reader,
                        connection,
                        _connection("wrong-adapter"),
                        LIBRARY,
                        bounds,
                        _context(),
                    )
                )


if __name__ == "__main__":
    unittest.main()
