from __future__ import annotations

import dataclasses
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mechanical_librarian.adapters.library_services.synthetic import (
    SyntheticLibraryReader,
    synthetic_capabilities,
)
from mechanical_librarian.adapters.persistence.inventory import SQLiteInventoryRepository
from mechanical_librarian.application.job_heartbeat import (
    RepositoryOutcome,
    RepositoryResult,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    LibraryListRequest,
    LibraryReadRequest,
    OperationContext,
    Page,
    PageRequest,
    PortResult,
    ResourceBounds,
    SnapshotStoreRequest,
    TimeProbe,
)
from mechanical_librarian.application.synthetic_inventory import (
    InspectSyntheticInventory,
    SyntheticInventoryOutcome,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    LibraryId,
    SnapshotId,
    TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    FieldName,
    LibraryConnection,
    MediaType,
    SecretReference,
    ServiceEndpoint,
)
from mechanical_librarian.infrastructure.persistence import sqlite


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _context(
    *, cancelled: bool = False, expired: bool = False, max_bytes: int = 64 * 1024
) -> OperationContext:
    return OperationContext(
        Deadline(_NOW - timedelta(seconds=1) if expired else _NOW + timedelta(seconds=30)),
        CancellationProbe(CancellationId("synthetic-run"), lambda: cancelled),
        ResourceBounds(1, max_bytes),
        TimeProbe(lambda: _NOW),
    )


def _connection(
    *, capabilities: AdapterCapabilities | None = None
) -> LibraryConnection:
    return LibraryConnection(
        id=ConnectionId("synthetic-connection"),
        adapter=AdapterId("synthetic"),
        endpoint=ServiceEndpoint("https://synthetic.invalid"),
        secret=SecretReference("synthetic-secret-reference"),
        capabilities=capabilities or synthetic_capabilities(),
    )


class MemorySnapshotRepository:
    def __init__(self) -> None:
        self.snapshot = None
        self.keys = []
        self.requests = []
        self.read_requests = []

    def store(self, request: SnapshotStoreRequest) -> RepositoryResult:
        self.keys.append(request.idempotency_key)
        self.requests.append(request)
        if self.snapshot is None:
            self.snapshot = request.snapshot
            return RepositoryResult(RepositoryOutcome.ACCEPTED, request.snapshot)
        return RepositoryResult(RepositoryOutcome.REUSED, self.snapshot)

    def read(self, request: LibraryReadRequest) -> RepositoryResult:
        self.read_requests.append(request)
        if self.snapshot is None or self.snapshot.target != request.target:
            return RepositoryResult(RepositoryOutcome.NOT_FOUND)
        return RepositoryResult(RepositoryOutcome.ACCEPTED, self.snapshot)

    def list(self, request: LibraryListRequest) -> RepositoryResult:
        items = () if self.snapshot is None else (self.snapshot,)
        return RepositoryResult(RepositoryOutcome.ACCEPTED, Page(items, None))


class ReaderOverride:
    def __init__(self, reader: SyntheticLibraryReader) -> None:
        self.reader = reader
        self.capabilities_result: object | None = None
        self.list_result: object | None = None

    def discover_capabilities(self, connection, context):
        if self.capabilities_result is not None:
            return self.capabilities_result
        return self.reader.discover_capabilities(connection, context)

    def list_snapshots(self, request):
        if self.list_result is not None:
            return self.list_result
        return self.reader.list_snapshots(request)

    def read_snapshot(self, request):
        return self.reader.read_snapshot(request)


class RepositoryOverride(MemorySnapshotRepository):
    def __init__(
        self,
        store_result: object | None = None,
        read_result: object | None = None,
        read_results: list[object] | None = None,
    ):
        super().__init__()
        self.store_result = store_result
        self.read_result = read_result
        self.read_results = list(read_results or [])

    def store(self, request):
        if self.store_result is not None:
            return self.store_result
        return super().store(request)

    def read(self, request):
        if self.read_results:
            return self.read_results.pop(0)
        if self.read_result is not None:
            return self.read_result
        return super().read(request)


class MutableClock:
    def __init__(self, now: datetime = _NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class ExpiringRepository:
    def __init__(self, delegate: SQLiteInventoryRepository, clock: MutableClock, phase: str):
        self.delegate = delegate
        self.clock = clock
        self.phase = phase
        self.reads = 0
        self.delayed = False
        self.delegate_outcomes = []

    def _expire(self, phase: str) -> None:
        if not self.delayed and self.phase == phase:
            self.clock.now = _NOW + timedelta(seconds=30)
            self.delayed = True

    def read(self, request):
        self.reads += 1
        phase = "pre-read" if self.reads == 1 else "readback"
        self._expire(phase)
        result = self.delegate.read(request)
        self.delegate_outcomes.append((phase, result.outcome))
        return result

    def store(self, request):
        result = self.delegate.store(request)
        self.delegate_outcomes.append(("store", result.outcome))
        self._expire("store")
        return result

    def list(self, request):
        return self.delegate.list(request)


class SyntheticLibraryReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = _connection()
        self.library = LibraryId("synthetic-library")
        self.reader = SyntheticLibraryReader(self.connection, self.library)

    def test_returns_exactly_one_canonical_fully_scoped_record(self) -> None:
        record = self.reader.record
        listed = self.reader.list_snapshots(
            LibraryListRequest(self.connection, PageRequest(None, 1), _context())
        )

        self.assertIs(type(record.connection), LibraryConnection)
        self.assertEqual(listed, PortResult.success(Page((record.snapshot,), None)))
        self.assertEqual(record.item.target.adapter, self.connection.adapter)
        self.assertEqual(record.item.target.connection, self.connection.id)
        self.assertEqual(record.item.target.library, self.library)
        self.assertEqual(record.snapshot.target, record.item.target)
        self.assertNotIn("synthetic-item-one", record.item.target.external_item.value)
        self.assertNotIn("synthetic-item-one", record.snapshot.canonical_input.serialized)
        self.assertFalse(record.snapshot.capabilities.write_supported)
        self.assertEqual(record.snapshot.metadata.fields, ())
        self.assertEqual(record.snapshot.files, ())

    def test_read_is_exact_scope_and_obeys_cancellation_deadline_and_bounds(self) -> None:
        target = self.reader.record.item.target
        self.assertEqual(
            self.reader.read_snapshot(LibraryReadRequest(target, _context())).value,
            self.reader.record.snapshot,
        )
        wrong_target = TargetId(
            target.adapter,
            target.connection,
            LibraryId("other-library"),
            target.external_item,
        )
        self.assertEqual(
            self.reader.read_snapshot(LibraryReadRequest(wrong_target, _context())).failure.kind,
            "not_found",
        )
        cases = (
            (_context(cancelled=True), "cancelled"),
            (_context(expired=True), "timeout"),
            (_context(max_bytes=1), "resource_exhausted"),
        )
        for context, expected in cases:
            with self.subTest(expected=expected):
                result = self.reader.list_snapshots(
                    LibraryListRequest(self.connection, PageRequest(None, 1), context)
                )
                self.assertEqual(result.failure.kind, expected)

    def test_exposes_no_write_network_credential_or_vendor_methods(self) -> None:
        forbidden = (
            "apply",
            "write",
            "delete",
            "resolve",
            "request",
            "lookup",
            "authenticate",
            "credentials",
        )
        for name in forbidden:
            with self.subTest(name=name):
                self.assertFalse(callable(getattr(self.reader, name, None)))
        self.assertNotIn("vendor", vars(self.reader))
        self.assertNotIn("secret", vars(self.reader))


class InspectSyntheticInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = _connection()
        self.library = LibraryId("synthetic-library")
        self.reader = SyntheticLibraryReader(self.connection, self.library)

    def test_success_persists_reads_back_and_returns_safe_immutable_inspection(self) -> None:
        repository = MemorySnapshotRepository()
        result = InspectSyntheticInventory(self.reader, repository).run(
            self.connection, self.library, _context()
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.outcome, SyntheticInventoryOutcome.READY)
        self.assertEqual(result.inspection.snapshot, SnapshotId("synthetic-snapshot-one"))
        self.assertEqual(result.inspection.library, self.library)
        self.assertEqual(result.inspection.repository_outcome, RepositoryOutcome.ACCEPTED)
        self.assertFalse(hasattr(result.inspection, "external_item"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.inspection.library = LibraryId("other-library")

    def test_duplicate_run_reuses_exact_snapshot_and_idempotency_key(self) -> None:
        repository = MemorySnapshotRepository()
        use_case = InspectSyntheticInventory(self.reader, repository)

        first = use_case.run(self.connection, self.library, _context())
        second = use_case.run(self.connection, self.library, _context())

        self.assertEqual(first.inspection.repository_outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(second.inspection.repository_outcome, RepositoryOutcome.REUSED)
        self.assertEqual(repository.keys[0], repository.keys[1])
        self.assertEqual(repository.snapshot, self.reader.record.snapshot)

    def test_fresh_id_unchanged_rediscovery_reuses_persisted_durable_id(self) -> None:
        repository = MemorySnapshotRepository()
        original = self.reader.record.snapshot
        repository.snapshot = original
        fresh = dataclasses.replace(
            original, id=SnapshotId("synthetic-snapshot-fresh")
        )
        rediscovery = ReaderOverride(self.reader)
        rediscovery.list_result = PortResult.success(Page((fresh,), None))

        result = InspectSyntheticInventory(rediscovery, repository).run(
            self.connection, self.library, _context()
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(result.inspection.snapshot, original.id)
        self.assertEqual(result.inspection.repository_outcome, RepositoryOutcome.REUSED)
        self.assertEqual(repository.requests[0].expected_previous, original.fingerprint)
        self.assertEqual(repository.snapshot, original)

    def test_pre_read_corruption_stops_before_store(self) -> None:
        repository = RepositoryOverride(
            read_result=RepositoryResult(RepositoryOutcome.CORRUPT)
        )

        result = InspectSyntheticInventory(self.reader, repository).run(
            self.connection, self.library, _context()
        )

        self.assertEqual(result.outcome, SyntheticInventoryOutcome.PERSISTENCE_FAILURE)
        self.assertEqual(repository.requests, [])

    def test_rejects_malformed_cross_scope_and_write_capable_reader_responses(self) -> None:
        malformed = ReaderOverride(self.reader)
        malformed.list_result = "raw vendor response"
        self.assertEqual(
            InspectSyntheticInventory(malformed, MemorySnapshotRepository())
            .run(self.connection, self.library, _context())
            .outcome,
            SyntheticInventoryOutcome.INVALID_RESPONSE,
        )

        cross_scope = ReaderOverride(self.reader)
        snapshot = SyntheticLibraryReader(
            self.connection, LibraryId("other-library")
        ).record.snapshot
        cross_scope.list_result = PortResult.success(Page((snapshot,), None))
        self.assertEqual(
            InspectSyntheticInventory(cross_scope, MemorySnapshotRepository())
            .run(self.connection, self.library, _context())
            .outcome,
            SyntheticInventoryOutcome.INVALID_RESPONSE,
        )

        writable = AdapterCapabilities(
            inventory_read=True,
            metadata_read=True,
            media_file_read=False,
            notifications=False,
            pagination=False,
            concurrency_tokens=False,
            cover_read=False,
            writable_fields=frozenset({FieldName.TITLE}),
            cover_write=False,
            supported_media_types=frozenset({MediaType.EBOOK}),
        )
        write_reader = ReaderOverride(self.reader)
        write_reader.capabilities_result = PortResult.success(writable)
        self.assertEqual(
            InspectSyntheticInventory(write_reader, MemorySnapshotRepository())
            .run(self.connection, self.library, _context())
            .outcome,
            SyntheticInventoryOutcome.INVALID_RESPONSE,
        )

    def test_cancellation_deadline_and_resource_bounds_fail_before_persistence(self) -> None:
        cases = (
            (_context(cancelled=True), SyntheticInventoryOutcome.CANCELLED),
            (_context(expired=True), SyntheticInventoryOutcome.DEADLINE_EXPIRED),
            (_context(max_bytes=1), SyntheticInventoryOutcome.RESOURCE_EXHAUSTED),
        )
        for context, expected in cases:
            with self.subTest(expected=expected):
                repository = MemorySnapshotRepository()
                result = InspectSyntheticInventory(self.reader, repository).run(
                    self.connection, self.library, context
                )
                self.assertEqual(result.outcome, expected)
                self.assertEqual(repository.keys, [])
                self.assertEqual(repository.read_requests, [])

    def test_deadline_equality_and_simultaneous_cancellation_precedence(self) -> None:
        for cancelled, expected in (
            (False, SyntheticInventoryOutcome.DEADLINE_EXPIRED),
            (True, SyntheticInventoryOutcome.CANCELLED),
        ):
            with self.subTest(cancelled=cancelled):
                context = OperationContext(
                    Deadline(_NOW),
                    CancellationProbe(
                        CancellationId("synthetic-equality"), lambda: cancelled
                    ),
                    ResourceBounds(1, 64 * 1024),
                    TimeProbe(lambda: _NOW),
                )
                repository = MemorySnapshotRepository()
                result = InspectSyntheticInventory(self.reader, repository).run(
                    self.connection, self.library, context
                )
                self.assertEqual(result.outcome, expected)
                self.assertIsNone(result.inspection)
                self.assertEqual(repository.read_requests, [])

    def test_repository_boundary_expiry_phase_matrix_and_fresh_retry(self) -> None:
        for phase, expected_initial_counts in (
            ("pre-read", ((0,), (0,))),
            ("store", ((1,), (1,))),
            ("readback", ((1,), (1,))),
        ):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as directory:
                state = sqlite.connect(Path(directory) / "state.sqlite")
                try:
                    sqlite.migrate(state)
                    durable = SQLiteInventoryRepository(state)
                    clock = MutableClock()
                    repository = ExpiringRepository(durable, clock, phase)
                    context = OperationContext(
                        Deadline(_NOW + timedelta(seconds=30)),
                        CancellationProbe(
                            CancellationId(f"synthetic-{phase}"), lambda: False
                        ),
                        ResourceBounds(1, 64 * 1024),
                        TimeProbe(clock),
                    )
                    use_case = InspectSyntheticInventory(self.reader, repository)

                    expired = use_case.run(self.connection, self.library, context)

                    self.assertEqual(
                        expired.outcome, SyntheticInventoryOutcome.DEADLINE_EXPIRED
                    )
                    self.assertIsNone(expired.inspection)
                    counts = (
                        state.execute(
                            "SELECT count(*) FROM inventory_snapshots"
                        ).fetchone(),
                        state.execute(
                            "SELECT count(*) FROM current_inventory_observations"
                        ).fetchone(),
                    )
                    self.assertEqual(counts, expected_initial_counts)
                    boundary_outcomes = dict(repository.delegate_outcomes)
                    if phase in {"pre-read", "readback"}:
                        self.assertEqual(
                            boundary_outcomes[phase], RepositoryOutcome.UNAVAILABLE
                        )
                    else:
                        self.assertEqual(
                            boundary_outcomes["store"], RepositoryOutcome.ACCEPTED
                        )

                    clock.now = _NOW
                    retry = use_case.run(self.connection, self.library, context)

                    self.assertTrue(retry.succeeded)
                    self.assertEqual(
                        retry.inspection.repository_outcome,
                        RepositoryOutcome.ACCEPTED
                        if phase == "pre-read"
                        else RepositoryOutcome.REUSED,
                    )
                    self.assertEqual(
                        durable.read(
                            LibraryReadRequest(
                                self.reader.record.snapshot.target, context
                            )
                        ).value,
                        self.reader.record.snapshot,
                    )
                    self.assertEqual(
                        (
                            state.execute(
                                "SELECT count(*) FROM inventory_snapshots"
                            ).fetchone(),
                            state.execute(
                                "SELECT count(*) FROM current_inventory_observations"
                            ).fetchone(),
                        ),
                        ((1,), (1,)),
                    )
                finally:
                    state.close()

    def test_rejects_reused_or_readback_results_with_wrong_shape_or_value(self) -> None:
        other = SyntheticLibraryReader(
            self.connection, LibraryId("other-library")
        ).record.snapshot
        store_cases = (
            RepositoryResult(RepositoryOutcome.REUSED),
            RepositoryResult(RepositoryOutcome.REUSED, other),
            RepositoryResult(RepositoryOutcome.REJECTED, self.reader.record.snapshot),
            "raw repository response",
        )
        for stored in store_cases:
            with self.subTest(stored=stored):
                result = InspectSyntheticInventory(
                    self.reader, RepositoryOverride(store_result=stored)
                ).run(self.connection, self.library, _context())
                self.assertEqual(result.outcome, SyntheticInventoryOutcome.PERSISTENCE_FAILURE)

        original = self.reader.record.snapshot
        equivalent_but_not_current = dataclasses.replace(
            original, id=SnapshotId("different-durable-snapshot")
        )
        wrong_reused = RepositoryOverride(
            store_result=RepositoryResult(
                RepositoryOutcome.REUSED, equivalent_but_not_current
            ),
            read_result=RepositoryResult(RepositoryOutcome.ACCEPTED, original),
        )
        self.assertEqual(
            InspectSyntheticInventory(self.reader, wrong_reused)
            .run(self.connection, self.library, _context())
            .outcome,
            SyntheticInventoryOutcome.PERSISTENCE_FAILURE,
        )

        repository = RepositoryOverride(
            store_result=RepositoryResult(
                RepositoryOutcome.ACCEPTED, self.reader.record.snapshot
            ),
            read_results=[
                RepositoryResult(RepositoryOutcome.NOT_FOUND),
                RepositoryResult(RepositoryOutcome.REUSED, other),
            ],
        )
        result = InspectSyntheticInventory(self.reader, repository).run(
            self.connection, self.library, _context()
        )
        self.assertEqual(result.outcome, SyntheticInventoryOutcome.READBACK_MISMATCH)


if __name__ == "__main__":
    unittest.main()
