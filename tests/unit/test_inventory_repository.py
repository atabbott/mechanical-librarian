from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.persistence import inventory as inventory_adapter
from mechanical_librarian.adapters.library_services.synthetic import (
    SyntheticLibraryReader,
    synthetic_capabilities,
)
from mechanical_librarian.adapters.persistence.inventory import (
    SQLiteInventoryRepository,
)
from mechanical_librarian.application.job_heartbeat import RepositoryOutcome
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    LibraryListRequest,
    LibraryReadRequest,
    OperationContext,
    PageRequest,
    ResourceBounds,
    SnapshotInspectionRequest,
    SnapshotStoreRequest,
    TimeProbe,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    ExternalItemId,
    Fingerprint,
    IdempotencyKey,
    LibraryId,
    SnapshotId,
)
from mechanical_librarian.domain.inventory import (
    FieldName,
    ItemSnapshot,
    LibraryConnection,
    MetadataField,
    NormalizedMetadata,
    SecretReference,
    ServiceEndpoint,
)
from mechanical_librarian.infrastructure.persistence import sqlite
from mechanical_librarian.infrastructure.persistence.sqlite import (
    Migration,
    SchemaError,
)


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _context(*, max_items: int = 1, max_bytes: int = 524_288) -> OperationContext:
    return OperationContext(
        Deadline(_NOW + timedelta(seconds=30)),
        CancellationProbe(CancellationId("inventory-test"), lambda: False),
        ResourceBounds(max_items, max_bytes),
        TimeProbe(lambda: _NOW),
    )


def _fixture(library: LibraryId | None = None):
    connection = LibraryConnection(
        ConnectionId("synthetic-connection"),
        AdapterId("synthetic"),
        ServiceEndpoint("https://synthetic.invalid"),
        SecretReference("unused-reference"),
        synthetic_capabilities(),
    )
    return SyntheticLibraryReader(
        connection, library or LibraryId("synthetic-library")
    ).record.snapshot


class SQLiteInventoryRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite"
        self.connection = sqlite.connect(self.path)
        self.addCleanup(lambda: self.connection.close())
        sqlite.migrate(self.connection)
        self.snapshot = _fixture()
        self.key = IdempotencyKey.from_canonical_input(
            operation="snapshot-store",
            target=self.snapshot.target,
            value={
                "fingerprint": self.snapshot.fingerprint.digest,
                "schema": self.snapshot.fingerprint.schema.value,
                "snapshot": self.snapshot.id.value,
            },
        )

    def request(self) -> SnapshotStoreRequest:
        return SnapshotStoreRequest(self.snapshot, self.key, None, _context())

    def request_for(
        self,
        snapshot: ItemSnapshot,
        expected_previous: Fingerprint | None = None,
        *,
        key: IdempotencyKey | None = None,
        context: OperationContext | None = None,
    ) -> SnapshotStoreRequest:
        selected_key = key or IdempotencyKey.from_canonical_input(
            operation="snapshot-store",
            target=snapshot.target,
            value={
                "fingerprint": snapshot.fingerprint.digest,
                "schema": snapshot.fingerprint.schema.value,
                "snapshot": snapshot.id.value,
            },
        )
        return SnapshotStoreRequest(
            snapshot, selected_key, expected_previous, context or _context()
        )

    def test_uuid_library_identity_round_trips_through_canonical_storage(self) -> None:
        library = LibraryId("01234567-89ab-4def-8123-456789abcdef")
        snapshot = _fixture(library)
        repository = SQLiteInventoryRepository(self.connection)

        stored = repository.store(self.request_for(snapshot))
        read = repository.read(LibraryReadRequest(snapshot.target, _context()))

        self.assertEqual(stored.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(read.value, snapshot)
        self.assertEqual(read.value.target.library, library)
        self.assertIn(library.value, snapshot.canonical_input.serialized)

    def large_snapshot(self) -> ItemSnapshot:
        return ItemSnapshot.create(
            id=SnapshotId("synthetic-snapshot-large"),
            target=self.snapshot.target,
            media_type=self.snapshot.media_type,
            lifecycle=self.snapshot.lifecycle,
            metadata=NormalizedMetadata(
                (MetadataField(FieldName.DESCRIPTION, ("\x01" * 90_000,)),)
            ),
            files=self.snapshot.files,
            cover=self.snapshot.cover,
            provenance=self.snapshot.provenance,
            service_release=self.snapshot.service_release,
            concurrency_token=self.snapshot.concurrency_token,
            capabilities=self.snapshot.capabilities,
        )

    def next_snapshot(self) -> ItemSnapshot:
        return ItemSnapshot.create(
            id=SnapshotId("synthetic-snapshot-two"),
            target=self.snapshot.target,
            media_type=self.snapshot.media_type,
            lifecycle=self.snapshot.lifecycle,
            metadata=self.snapshot.metadata,
            files=self.snapshot.files,
            cover=self.snapshot.cover,
            provenance=replace(
                self.snapshot.provenance,
                observed_at=self.snapshot.provenance.observed_at + timedelta(seconds=1),
                raw_observation=replace(
                    self.snapshot.provenance.raw_observation,
                    payload_fingerprint=Fingerprint.from_canonical_value(
                        {"state": "advanced"},
                        schema=self.snapshot.provenance.raw_observation.payload_fingerprint.schema,
                    ),
                ),
            ),
            service_release=self.snapshot.service_release,
            concurrency_token=self.snapshot.concurrency_token,
            capabilities=self.snapshot.capabilities,
        )

    def snapshot_for_item(self, external_item: str) -> ItemSnapshot:
        target = replace(
            self.snapshot.target, external_item=ExternalItemId(external_item)
        )
        return ItemSnapshot.create(
            id=SnapshotId(f"snapshot-{external_item}"),
            target=target,
            media_type=self.snapshot.media_type,
            lifecycle=self.snapshot.lifecycle,
            metadata=self.snapshot.metadata,
            files=self.snapshot.files,
            cover=self.snapshot.cover,
            provenance=self.snapshot.provenance,
            service_release=self.snapshot.service_release,
            concurrency_token=self.snapshot.concurrency_token,
            capabilities=self.snapshot.capabilities,
        )

    def domain_connection(self) -> LibraryConnection:
        return LibraryConnection(
            self.snapshot.target.connection,
            self.snapshot.target.adapter,
            ServiceEndpoint("https://synthetic.invalid"),
            SecretReference("unused-reference"),
            self.snapshot.capabilities,
        )

    def test_store_duplicate_restart_and_scoped_read_preserve_exact_cardinality(
        self,
    ) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        first = repository.store(self.request())
        second = repository.store(self.request())

        self.assertEqual(first.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(second.outcome, RepositoryOutcome.REUSED)
        self.assertEqual(first.value, self.snapshot)
        self.assertEqual(second.value, self.snapshot)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM current_inventory_observations"
            ).fetchone(),
            (1,),
        )

        self.connection.close()
        self.connection = sqlite.connect_existing(self.path)
        sqlite.migrate(self.connection)
        restarted = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            restarted.store(self.request()).outcome, RepositoryOutcome.REUSED
        )
        self.assertEqual(
            restarted.read(LibraryReadRequest(self.snapshot.target, _context())).value,
            self.snapshot,
        )

    def test_large_domain_snapshot_obeys_exact_context_and_restarts_reused(
        self,
    ) -> None:
        snapshot = self.large_snapshot()
        size = len(snapshot.canonical_input.serialized.encode("utf-8"))
        self.assertGreater(size, 524_288)
        self.assertLessEqual(size, 1_048_576)
        repository = SQLiteInventoryRepository(self.connection)

        too_small = repository.store(
            self.request_for(snapshot, context=_context(max_bytes=size - 1))
        )
        self.assertEqual(too_small.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(
            (
                self.connection.execute(
                    "SELECT count(*) FROM inventory_snapshots"
                ).fetchone(),
                self.connection.execute(
                    "SELECT count(*) FROM current_inventory_observations"
                ).fetchone(),
            ),
            ((0,), (0,)),
        )

        context = _context(max_bytes=size)
        request = self.request_for(snapshot, context=context)
        self.assertEqual(repository.store(request).outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(
            repository.read(LibraryReadRequest(snapshot.target, context)).value,
            snapshot,
        )
        connection = LibraryConnection(
            snapshot.target.connection,
            snapshot.target.adapter,
            ServiceEndpoint("https://synthetic.invalid"),
            SecretReference("unused-reference"),
            snapshot.capabilities,
        )
        listed = repository.list(
            LibraryListRequest(connection, PageRequest(None, 1), context)
        )
        self.assertEqual(listed.value.items, (snapshot,))
        self.assertEqual(repository.store(request).outcome, RepositoryOutcome.REUSED)

        self.connection.close()
        self.connection = sqlite.connect_existing(self.path)
        sqlite.migrate(self.connection)
        restarted = SQLiteInventoryRepository(self.connection)
        self.assertEqual(restarted.store(request).outcome, RepositoryOutcome.REUSED)
        self.assertEqual(
            restarted.read(LibraryReadRequest(snapshot.target, context)).value,
            snapshot,
        )
        self.assertEqual(
            (
                self.connection.execute(
                    "SELECT count(*) FROM inventory_snapshots"
                ).fetchone(),
                self.connection.execute(
                    "SELECT count(*) FROM current_inventory_observations"
                ).fetchone(),
            ),
            ((1,), (1,)),
        )

    def test_read_measures_durable_bytes_before_hydrating_canonical_input(self) -> None:
        snapshot = self.large_snapshot()
        size = len(snapshot.canonical_input.serialized.encode("utf-8"))
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(
                self.request_for(snapshot, context=_context(max_bytes=size))
            ).outcome,
            RepositoryOutcome.ACCEPTED,
        )

        with patch.object(
            inventory_adapter,
            "_snapshot_from_row",
            wraps=inventory_adapter._snapshot_from_row,
        ) as hydrate:
            result = repository.read(
                LibraryReadRequest(snapshot.target, _context(max_bytes=size - 1))
            )

        self.assertEqual(result.outcome, RepositoryOutcome.UNAVAILABLE)
        hydrate.assert_not_called()
        self.assertFalse(self.connection.in_transaction)

        cancellation = {"requested": False}
        context = OperationContext(
            Deadline(_NOW + timedelta(seconds=30)),
            CancellationProbe(
                CancellationId("inspection-cancel"),
                lambda: cancellation["requested"],
            ),
            ResourceBounds(1, size),
            TimeProbe(lambda: _NOW),
        )
        original_hydrate = inventory_adapter._snapshot_from_row

        def cancel_after_hydration(*args):
            hydrated = original_hydrate(*args)
            cancellation["requested"] = True
            return hydrated

        with patch.object(
            inventory_adapter,
            "_snapshot_from_row",
            side_effect=cancel_after_hydration,
        ):
            cancelled = repository.inspect(
                SnapshotInspectionRequest(
                    snapshot.target,
                    snapshot.id,
                    snapshot.fingerprint,
                    context,
                )
            )
        self.assertEqual(cancelled.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertIsNone(cancelled.value)
        self.assertFalse(self.connection.in_transaction)
        exact = repository.read(
            LibraryReadRequest(snapshot.target, _context(max_bytes=size))
        )
        self.assertEqual(exact.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(exact.value, snapshot)

    def test_inspect_reports_exact_target_history_and_rejects_stale_expectation(
        self,
    ) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        missing = repository.inspect(
            SnapshotInspectionRequest(
                self.snapshot.target,
                self.snapshot.id,
                self.snapshot.fingerprint,
                _context(),
            )
        )
        self.assertEqual(missing.outcome, RepositoryOutcome.NOT_FOUND)
        self.assertIsNone(missing.value)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        changes_before_inspect = self.connection.total_changes
        first = repository.inspect(
            SnapshotInspectionRequest(
                self.snapshot.target,
                self.snapshot.id,
                self.snapshot.fingerprint,
                _context(),
            )
        )

        self.assertEqual(first.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(first.value.snapshot, self.snapshot)
        self.assertEqual(
            (
                first.value.item_count,
                first.value.history_count,
                first.value.current_count,
            ),
            (1, 1, 1),
        )
        self.assertEqual(self.connection.total_changes, changes_before_inspect)

        advanced = self.next_snapshot()
        self.assertEqual(
            repository.store(
                self.request_for(advanced, self.snapshot.fingerprint)
            ).outcome,
            RepositoryOutcome.ACCEPTED,
        )
        other = self.snapshot_for_item("other-item")
        self.assertEqual(
            repository.store(self.request_for(other)).outcome,
            RepositoryOutcome.ACCEPTED,
        )
        current = repository.inspect(
            SnapshotInspectionRequest(
                advanced.target, advanced.id, advanced.fingerprint, _context()
            )
        )
        stale = repository.inspect(
            SnapshotInspectionRequest(
                self.snapshot.target,
                self.snapshot.id,
                self.snapshot.fingerprint,
                _context(),
            )
        )

        self.assertEqual(current.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(current.value.snapshot, advanced)
        self.assertEqual(
            (
                current.value.item_count,
                current.value.history_count,
                current.value.current_count,
            ),
            (1, 2, 1),
        )
        self.assertEqual(stale.outcome, RepositoryOutcome.REJECTED)
        self.assertIsNone(stale.value)
        self.assertFalse(self.connection.in_transaction)

    def test_inspect_preflights_current_bytes_before_hydration(self) -> None:
        snapshot = self.large_snapshot()
        size = len(snapshot.canonical_input.serialized.encode("utf-8"))
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(
                self.request_for(snapshot, context=_context(max_bytes=size))
            ).outcome,
            RepositoryOutcome.ACCEPTED,
        )

        with patch.object(
            inventory_adapter,
            "_snapshot_from_row",
            wraps=inventory_adapter._snapshot_from_row,
        ) as hydrate:
            result = repository.inspect(
                SnapshotInspectionRequest(
                    snapshot.target,
                    snapshot.id,
                    snapshot.fingerprint,
                    _context(max_bytes=size - 1),
                )
            )

        self.assertEqual(result.outcome, RepositoryOutcome.UNAVAILABLE)
        hydrate.assert_not_called()
        self.assertFalse(self.connection.in_transaction)

    def test_inspect_uses_one_wal_snapshot_for_measurement_and_detail(self) -> None:
        writer = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            writer.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        reader_connection = sqlite.connect_existing(self.path)
        self.addCleanup(reader_connection.close)
        reader = SQLiteInventoryRepository(reader_connection)
        advanced = self.next_snapshot()
        measurement = reader._inspection_measurement

        def advance_after_measurement(target):
            measured = measurement(target)
            self.assertEqual(
                writer.store(
                    self.request_for(advanced, self.snapshot.fingerprint)
                ).outcome,
                RepositoryOutcome.ACCEPTED,
            )
            return measured

        with patch.object(
            reader, "_inspection_measurement", side_effect=advance_after_measurement
        ):
            old = reader.inspect(
                SnapshotInspectionRequest(
                    self.snapshot.target,
                    self.snapshot.id,
                    self.snapshot.fingerprint,
                    _context(),
                )
            )

        self.assertEqual(old.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(old.value.snapshot, self.snapshot)
        self.assertEqual(old.value.history_count, 1)
        latest = reader.inspect(
            SnapshotInspectionRequest(
                advanced.target, advanced.id, advanced.fingerprint, _context()
            )
        )
        self.assertEqual(latest.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(latest.value.snapshot, advanced)
        self.assertEqual(latest.value.history_count, 2)
        self.assertFalse(reader_connection.in_transaction)

    def test_list_enforces_page_and_context_bounds_before_hydration(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        snapshots = (self.snapshot_for_item("item-a"), self.snapshot_for_item("item-b"))
        for snapshot in snapshots:
            self.assertEqual(
                repository.store(self.request_for(snapshot)).outcome,
                RepositoryOutcome.ACCEPTED,
            )
        total_size = sum(
            len(snapshot.canonical_input.serialized.encode("utf-8"))
            for snapshot in snapshots
        )
        connection = self.domain_connection()

        cases = (
            (
                "page-overflow",
                LibraryListRequest(connection, PageRequest(None, 1), _context()),
                RepositoryOutcome.REJECTED,
            ),
            (
                "item-bound",
                LibraryListRequest(connection, PageRequest(None, 2), _context()),
                RepositoryOutcome.UNAVAILABLE,
            ),
            (
                "byte-bound",
                LibraryListRequest(
                    connection,
                    PageRequest(None, 2),
                    _context(max_items=2, max_bytes=total_size - 1),
                ),
                RepositoryOutcome.UNAVAILABLE,
            ),
        )
        for name, request, expected in cases:
            with (
                self.subTest(name=name),
                patch.object(
                    inventory_adapter,
                    "_snapshot_from_selected_row",
                    wraps=inventory_adapter._snapshot_from_selected_row,
                ) as hydrate,
            ):
                result = repository.list(request)
                self.assertEqual(result.outcome, expected)
                hydrate.assert_not_called()
                self.assertFalse(self.connection.in_transaction)

        exact = repository.list(
            LibraryListRequest(
                connection,
                PageRequest(None, 2),
                _context(max_items=2, max_bytes=total_size),
            )
        )
        self.assertEqual(exact.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(exact.value.items, snapshots)

    def test_store_measures_existing_durable_bytes_before_hydration(self) -> None:
        current = self.large_snapshot()
        current_size = len(current.canonical_input.serialized.encode("utf-8"))
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(
                self.request_for(current, context=_context(max_bytes=current_size))
            ).outcome,
            RepositoryOutcome.ACCEPTED,
        )
        incoming = self.next_snapshot()
        incoming_size = len(incoming.canonical_input.serialized.encode("utf-8"))

        with patch.object(
            inventory_adapter,
            "_snapshot_from_row",
            wraps=inventory_adapter._snapshot_from_row,
        ) as hydrate:
            result = repository.store(
                self.request_for(
                    incoming,
                    current.fingerprint,
                    context=_context(max_bytes=incoming_size),
                )
            )

        self.assertEqual(result.outcome, RepositoryOutcome.UNAVAILABLE)
        hydrate.assert_not_called()
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT snapshot_id FROM current_inventory_observations"
            ).fetchone(),
            (current.id.value,),
        )

    def test_snapshot_store_operation_is_validated_and_defended_at_repository_boundary(
        self,
    ) -> None:
        wrong_key = IdempotencyKey.from_canonical_input(
            operation="other-store",
            target=self.snapshot.target,
            value={"snapshot": self.snapshot.id.value},
        )
        with self.assertRaises(ValueError):
            SnapshotStoreRequest(self.snapshot, wrong_key, None, _context())

        forged = object.__new__(SnapshotStoreRequest)
        object.__setattr__(forged, "snapshot", self.snapshot)
        object.__setattr__(forged, "idempotency_key", wrong_key)
        object.__setattr__(forged, "expected_previous", None)
        object.__setattr__(forged, "context", _context())
        result = SQLiteInventoryRepository(self.connection).store(forged)

        self.assertEqual(result.outcome, RepositoryOutcome.REJECTED)
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (0,),
        )

    def test_read_snapshot_remains_stable_while_writer_advances_wal_state(self) -> None:
        writer = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            writer.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        reader_connection = sqlite.connect_existing(self.path)
        self.addCleanup(reader_connection.close)
        reader = SQLiteInventoryRepository(reader_connection)
        advanced = self.next_snapshot()

        with inventory_adapter._read_transaction(reader_connection):
            self.assertIsNotNone(reader._current_header(self.snapshot.target))
            self.assertEqual(
                writer.store(
                    self.request_for(advanced, self.snapshot.fingerprint)
                ).outcome,
                RepositoryOutcome.ACCEPTED,
            )
            self.assertEqual(
                reader._read_current_row(self.snapshot.target), self.snapshot
            )

        self.assertFalse(reader_connection.in_transaction)
        self.assertEqual(
            reader.read(LibraryReadRequest(self.snapshot.target, _context())).value,
            advanced,
        )

    def test_interruption_between_history_and_current_rolls_back_then_retries_cleanly(
        self,
    ) -> None:
        repository = SQLiteInventoryRepository(
            self.connection,
            after_history_insert=lambda: (_ for _ in ()).throw(
                sqlite3.OperationalError("injected interruption")
            ),
        )
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.UNAVAILABLE
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM current_inventory_observations"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            SQLiteInventoryRepository(self.connection).store(self.request()).outcome,
            RepositoryOutcome.ACCEPTED,
        )

    def test_asserted_predecessor_cannot_initialize_absent_target(self) -> None:
        result = SQLiteInventoryRepository(self.connection).store(
            self.request_for(self.snapshot, self.snapshot.fingerprint)
        )

        self.assertEqual(result.outcome, RepositoryOutcome.REJECTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM current_inventory_observations"
            ).fetchone(),
            (0,),
        )

    def test_retry_of_superseded_snapshot_is_rejected_without_changing_state(
        self,
    ) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        next_snapshot = self.next_snapshot()

        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        self.assertEqual(
            repository.store(
                self.request_for(next_snapshot, self.snapshot.fingerprint)
            ).outcome,
            RepositoryOutcome.ACCEPTED,
        )
        retry = repository.store(self.request())

        self.assertEqual(retry.outcome, RepositoryOutcome.REJECTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (2,),
        )
        self.assertEqual(
            repository.read(LibraryReadRequest(self.snapshot.target, _context())).value,
            next_snapshot,
        )

    def test_exact_history_with_changed_snapshot_or_key_is_corrupt(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        changed_key = IdempotencyKey.from_canonical_input(
            operation="snapshot-store",
            target=self.snapshot.target,
            value={"changed": True},
        )

        changed_snapshot = replace(self.next_snapshot(), id=self.snapshot.id)
        cases = (
            ("changed-key", self.request_for(self.snapshot, key=changed_key)),
            (
                "changed-snapshot",
                self.request_for(changed_snapshot, self.snapshot.fingerprint),
            ),
        )
        for name, request in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    repository.store(request).outcome, RepositoryOutcome.CORRUPT
                )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (1,),
        )

    def test_exact_current_retry_does_not_materialize_unrelated_history(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        target = self.snapshot.target
        self.connection.execute(
            "INSERT INTO inventory_snapshots("
            "adapter_id, connection_id, library_id, external_item, snapshot_id, "
            "idempotency_operation, idempotency_digest, fingerprint_algorithm, "
            "fingerprint_schema, fingerprint_digest, canonical_input, observed_at"
            ") VALUES (?, ?, ?, ?, 'unrelated-history', 'snapshot-store', ?, "
            "'sha256', 'snapshot-v1', ?, '{}', '2026-01-01T00:00:00.000000Z')",
            (
                target.adapter.value,
                target.connection.value,
                target.library.value,
                target.external_item.value,
                "c" * 64,
                "d" * 64,
            ),
        )

        result = repository.store(self.request())

        self.assertEqual(result.outcome, RepositoryOutcome.REUSED)
        self.assertEqual(result.value, self.snapshot)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (2,),
        )

    def test_exact_history_without_current_pointer_is_corrupt(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        self.connection.execute("DELETE FROM current_inventory_observations")

        result = repository.store(self.request())

        self.assertEqual(result.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM current_inventory_observations"
            ).fetchone(),
            (0,),
        )

    def test_identity_current_predecessor_matrix_preserves_cardinality(self) -> None:
        """Cover snapshot-id/fingerprint/key/current/predecessor reconciliation as a table."""
        repository = SQLiteInventoryRepository(self.connection)
        current = self.snapshot
        same_canonical_fresh_id = replace(
            current, id=SnapshotId("synthetic-snapshot-fresh")
        )
        changed = self.next_snapshot()

        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        cases = (
            ("exact-current-matching-key", self.request(), RepositoryOutcome.REUSED),
            (
                "fresh-id-same-fingerprint-no-predecessor",
                self.request_for(same_canonical_fresh_id),
                RepositoryOutcome.REJECTED,
            ),
            (
                "fresh-id-same-fingerprint-wrong-predecessor",
                self.request_for(same_canonical_fresh_id, changed.fingerprint),
                RepositoryOutcome.REJECTED,
            ),
            (
                "fresh-id-same-fingerprint-matching-predecessor",
                self.request_for(same_canonical_fresh_id, current.fingerprint),
                RepositoryOutcome.REUSED,
            ),
            (
                "new-state-missing-predecessor",
                self.request_for(changed),
                RepositoryOutcome.REJECTED,
            ),
            (
                "new-state-matching-predecessor",
                self.request_for(changed, current.fingerprint),
                RepositoryOutcome.ACCEPTED,
            ),
        )
        for name, request, expected in cases:
            with self.subTest(name=name):
                result = repository.store(request)
                self.assertEqual(result.outcome, expected)
                expected_rows = 2 if name == "new-state-matching-predecessor" else 1
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM inventory_snapshots"
                    ).fetchone(),
                    (expected_rows,),
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM current_inventory_observations"
                    ).fetchone(),
                    (1,),
                )

    def test_fresh_id_unchanged_state_reuses_durable_current_row(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        fresh = replace(self.snapshot, id=SnapshotId("synthetic-snapshot-fresh"))

        result = repository.store(self.request_for(fresh, self.snapshot.fingerprint))

        self.assertEqual(result.outcome, RepositoryOutcome.REUSED)
        self.assertEqual(result.value, self.snapshot)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM current_inventory_observations"
            ).fetchone(),
            (1,),
        )

    def test_historical_fingerprint_never_repoints_current(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        advanced = self.next_snapshot()
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        self.assertEqual(
            repository.store(
                self.request_for(advanced, self.snapshot.fingerprint)
            ).outcome,
            RepositoryOutcome.ACCEPTED,
        )
        fresh_historical = replace(
            self.snapshot, id=SnapshotId("synthetic-snapshot-historical-fresh")
        )

        result = repository.store(
            self.request_for(fresh_historical, advanced.fingerprint)
        )

        self.assertEqual(result.outcome, RepositoryOutcome.REJECTED)
        self.assertEqual(
            repository.read(LibraryReadRequest(self.snapshot.target, _context())).value,
            advanced,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (2,),
        )

    def test_idempotency_identity_bound_to_another_snapshot_is_corrupt(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )

        result = repository.store(
            self.request_for(
                self.next_snapshot(), self.snapshot.fingerprint, key=self.key
            )
        )

        self.assertEqual(result.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM inventory_snapshots"
            ).fetchone(),
            (1,),
        )

    def test_orphan_history_matrix_is_corrupt_and_never_mutates(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        self.connection.execute("DELETE FROM current_inventory_observations")
        fresh = replace(self.snapshot, id=SnapshotId("synthetic-snapshot-fresh"))
        changed = self.next_snapshot()
        cases = (
            ("exact-id", self.request()),
            ("fresh-id-same-fingerprint-no-predecessor", self.request_for(fresh)),
            (
                "fresh-id-same-fingerprint-with-predecessor",
                self.request_for(fresh, self.snapshot.fingerprint),
            ),
            ("fresh-state-no-predecessor", self.request_for(changed)),
            (
                "fresh-state-with-predecessor",
                self.request_for(changed, self.snapshot.fingerprint),
            ),
        )
        for name, request in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    repository.store(request).outcome, RepositoryOutcome.CORRUPT
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM inventory_snapshots"
                    ).fetchone(),
                    (1,),
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM current_inventory_observations"
                    ).fetchone(),
                    (0,),
                )

    def test_read_and_list_report_orphan_only_in_exact_connection_scope(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        self.connection.execute("DELETE FROM current_inventory_observations")
        target = self.snapshot.target

        self.assertEqual(
            repository.read(LibraryReadRequest(target, _context())).outcome,
            RepositoryOutcome.CORRUPT,
        )
        inspected = repository.inspect(
            SnapshotInspectionRequest(
                target, self.snapshot.id, self.snapshot.fingerprint, _context()
            )
        )
        self.assertEqual(inspected.outcome, RepositoryOutcome.CORRUPT)
        self.assertIsNone(inspected.value)
        own_connection = LibraryConnection(
            target.connection,
            target.adapter,
            ServiceEndpoint("https://synthetic.invalid"),
            SecretReference("unused-reference"),
            self.snapshot.capabilities,
        )
        self.assertEqual(
            repository.list(
                LibraryListRequest(own_connection, PageRequest(None, 1), _context())
            ).outcome,
            RepositoryOutcome.CORRUPT,
        )
        other_connection = replace(own_connection, id=ConnectionId("other-connection"))
        listed = repository.list(
            LibraryListRequest(other_connection, PageRequest(None, 1), _context())
        )
        self.assertEqual(listed.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(listed.value.items, ())

    def test_inverse_orphan_pointer_matrix_is_corrupt_and_connection_scoped(
        self,
    ) -> None:
        target = self.snapshot.target
        values = (
            target.adapter.value,
            target.connection.value,
            target.library.value,
            target.external_item.value,
        )
        self.connection.commit()
        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.execute(
                "INSERT INTO current_inventory_observations("
                "adapter_id, connection_id, library_id, external_item, snapshot_id"
                ") VALUES (?, ?, ?, ?, 'missing-history')",
                values,
            )
            self.connection.commit()
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_keys").fetchone(), (1,)
        )

        repository = SQLiteInventoryRepository(self.connection)
        fresh = replace(self.snapshot, id=SnapshotId("synthetic-snapshot-fresh"))
        changed = self.next_snapshot()
        cases = (
            ("incoming-exact-no-predecessor", self.request()),
            (
                "incoming-exact-with-predecessor",
                self.request_for(self.snapshot, self.snapshot.fingerprint),
            ),
            ("fresh-id-same-state", self.request_for(fresh)),
            (
                "fresh-id-same-state-with-predecessor",
                self.request_for(fresh, self.snapshot.fingerprint),
            ),
            ("fresh-state", self.request_for(changed)),
        )
        for name, request in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    repository.store(request).outcome, RepositoryOutcome.CORRUPT
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM inventory_snapshots"
                    ).fetchone(),
                    (0,),
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM current_inventory_observations"
                    ).fetchone(),
                    (1,),
                )

        self.assertEqual(
            repository.read(LibraryReadRequest(target, _context())).outcome,
            RepositoryOutcome.CORRUPT,
        )
        changes_before_inspect = self.connection.total_changes
        inspected = repository.inspect(
            SnapshotInspectionRequest(
                target, self.snapshot.id, self.snapshot.fingerprint, _context()
            )
        )
        self.assertEqual(inspected.outcome, RepositoryOutcome.CORRUPT)
        self.assertIsNone(inspected.value)
        self.assertEqual(self.connection.total_changes, changes_before_inspect)
        self.assertFalse(self.connection.in_transaction)
        own_connection = LibraryConnection(
            target.connection,
            target.adapter,
            ServiceEndpoint("https://synthetic.invalid"),
            SecretReference("unused-reference"),
            self.snapshot.capabilities,
        )
        self.assertEqual(
            repository.list(
                LibraryListRequest(own_connection, PageRequest(None, 1), _context())
            ).outcome,
            RepositoryOutcome.CORRUPT,
        )
        other_connection = replace(own_connection, id=ConnectionId("other-connection"))
        other = repository.list(
            LibraryListRequest(other_connection, PageRequest(None, 1), _context())
        )
        self.assertEqual(other.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(other.value.items, ())
        self.assertEqual(
            (
                self.connection.execute(
                    "SELECT count(*) FROM inventory_snapshots"
                ).fetchone(),
                self.connection.execute(
                    "SELECT count(*) FROM current_inventory_observations"
                ).fetchone(),
            ),
            ((0,), (1,)),
        )

    def test_history_is_append_only_and_cross_scope_reads_do_not_match(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(
            repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE inventory_snapshots SET canonical_input = '{}'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM inventory_snapshots")

        other = _fixture().target
        other = type(other)(
            other.adapter,
            other.connection,
            LibraryId("other-library"),
            other.external_item,
        )
        self.assertEqual(
            repository.read(LibraryReadRequest(other, _context())).outcome,
            RepositoryOutcome.NOT_FOUND,
        )

    def test_missing_nested_and_deep_canonical_rows_return_typed_corrupt(self) -> None:
        for serialized in ("{}", '{"target":[]}', "[" * 1_000):
            with (
                self.subTest(serialized=serialized[:20]),
                tempfile.TemporaryDirectory() as directory,
            ):
                connection = sqlite.connect(Path(directory) / "state.sqlite")
                try:
                    sqlite.migrate(connection)
                    target = self.snapshot.target
                    values = (
                        target.adapter.value,
                        target.connection.value,
                        target.library.value,
                        target.external_item.value,
                    )
                    connection.execute(
                        "INSERT INTO inventory_snapshots("
                        "adapter_id, connection_id, library_id, external_item, snapshot_id, "
                        "idempotency_operation, idempotency_digest, fingerprint_algorithm, "
                        "fingerprint_schema, fingerprint_digest, canonical_input, observed_at"
                        ") VALUES (?, ?, ?, ?, 'corrupt-snapshot', 'snapshot-store', ?, "
                        "'sha256', 'snapshot-v1', ?, ?, '2026-01-01T00:00:00.000000Z')",
                        (*values, "a" * 64, "b" * 64, serialized),
                    )
                    connection.execute(
                        "INSERT INTO current_inventory_observations("
                        "adapter_id, connection_id, library_id, external_item, snapshot_id"
                        ") VALUES (?, ?, ?, ?, 'corrupt-snapshot')",
                        values,
                    )
                    repository = SQLiteInventoryRepository(connection)
                    result = repository.read(LibraryReadRequest(target, _context()))
                    self.assertEqual(result.outcome, RepositoryOutcome.CORRUPT)
                    self.assertIsNone(result.value)
                    changes_before_inspect = connection.total_changes
                    inspected = repository.inspect(
                        SnapshotInspectionRequest(
                            target,
                            self.snapshot.id,
                            self.snapshot.fingerprint,
                            _context(),
                        )
                    )
                    self.assertEqual(inspected.outcome, RepositoryOutcome.CORRUPT)
                    self.assertIsNone(inspected.value)
                    self.assertEqual(connection.total_changes, changes_before_inspect)
                    self.assertFalse(connection.in_transaction)
                    domain_connection = LibraryConnection(
                        target.connection,
                        target.adapter,
                        ServiceEndpoint("https://synthetic.invalid"),
                        SecretReference("unused-reference"),
                        self.snapshot.capabilities,
                    )
                    listed = repository.list(
                        LibraryListRequest(
                            domain_connection, PageRequest(None, 1), _context()
                        )
                    )
                    self.assertEqual(listed.outcome, RepositoryOutcome.CORRUPT)
                    stored = repository.store(self.request())
                    self.assertEqual(stored.outcome, RepositoryOutcome.CORRUPT)
                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) FROM inventory_snapshots"
                        ).fetchone(),
                        (1,),
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) FROM current_inventory_observations"
                        ).fetchone(),
                        (1,),
                    )
                finally:
                    connection.close()

    def test_oversized_tampered_durable_input_is_corrupt_without_mutation(self) -> None:
        target = self.snapshot.target
        values = (
            target.adapter.value,
            target.connection.value,
            target.library.value,
            target.external_item.value,
        )
        self.connection.execute("PRAGMA ignore_check_constraints = ON")
        try:
            self.connection.execute(
                "INSERT INTO inventory_snapshots("
                "adapter_id, connection_id, library_id, external_item, snapshot_id, "
                "idempotency_operation, idempotency_digest, fingerprint_algorithm, "
                "fingerprint_schema, fingerprint_digest, canonical_input, observed_at"
                ") VALUES (?, ?, ?, ?, 'tampered-large', 'snapshot-store', ?, "
                "'sha256', 'snapshot-v1', ?, ?, '2026-01-01T00:00:00.000000Z')",
                (*values, "e" * 64, "f" * 64, "x" * 1_048_577),
            )
            self.connection.execute(
                "INSERT INTO current_inventory_observations("
                "adapter_id, connection_id, library_id, external_item, snapshot_id"
                ") VALUES (?, ?, ?, ?, 'tampered-large')",
                values,
            )
        finally:
            self.connection.execute("PRAGMA ignore_check_constraints = OFF")
        repository = SQLiteInventoryRepository(self.connection)
        context = _context(max_bytes=1_048_576)

        read = repository.read(LibraryReadRequest(target, context))
        self.assertEqual(read.outcome, RepositoryOutcome.CORRUPT)
        self.assertIsNone(read.value)
        changes_before_inspect = self.connection.total_changes
        inspected = repository.inspect(
            SnapshotInspectionRequest(
                target, self.snapshot.id, self.snapshot.fingerprint, context
            )
        )
        self.assertEqual(inspected.outcome, RepositoryOutcome.CORRUPT)
        self.assertIsNone(inspected.value)
        self.assertEqual(self.connection.total_changes, changes_before_inspect)
        self.assertFalse(self.connection.in_transaction)
        connection = LibraryConnection(
            target.connection,
            target.adapter,
            ServiceEndpoint("https://synthetic.invalid"),
            SecretReference("unused-reference"),
            self.snapshot.capabilities,
        )
        listed = repository.list(
            LibraryListRequest(connection, PageRequest(None, 1), context)
        )
        self.assertEqual(listed.outcome, RepositoryOutcome.CORRUPT)
        self.assertIsNone(listed.value)
        stored = repository.store(self.request_for(self.snapshot, context=context))
        self.assertEqual(stored.outcome, RepositoryOutcome.CORRUPT)
        self.assertIsNone(stored.value)
        self.assertEqual(
            (
                self.connection.execute(
                    "SELECT count(*) FROM inventory_snapshots"
                ).fetchone(),
                self.connection.execute(
                    "SELECT count(*) FROM current_inventory_observations"
                ).fetchone(),
            ),
            ((1,), (1,)),
        )

    def test_tampered_durable_operation_is_corrupt_before_hydration(self) -> None:
        target = self.snapshot.target
        values = (
            target.adapter.value,
            target.connection.value,
            target.library.value,
            target.external_item.value,
        )
        self.connection.execute("PRAGMA ignore_check_constraints = ON")
        try:
            self.connection.execute(
                "INSERT INTO inventory_snapshots("
                "adapter_id, connection_id, library_id, external_item, snapshot_id, "
                "idempotency_operation, idempotency_digest, fingerprint_algorithm, "
                "fingerprint_schema, fingerprint_digest, canonical_input, observed_at"
                ") VALUES (?, ?, ?, ?, ?, 'other-store', ?, ?, ?, ?, ?, ?)",
                (
                    *values,
                    self.snapshot.id.value,
                    self.key.digest,
                    self.snapshot.fingerprint.algorithm,
                    self.snapshot.fingerprint.schema.value,
                    self.snapshot.fingerprint.digest,
                    self.snapshot.canonical_input.serialized,
                    "2026-01-01T00:00:00.000000Z",
                ),
            )
            self.connection.execute(
                "INSERT INTO current_inventory_observations("
                "adapter_id, connection_id, library_id, external_item, snapshot_id"
                ") VALUES (?, ?, ?, ?, ?)",
                (*values, self.snapshot.id.value),
            )
        finally:
            self.connection.execute("PRAGMA ignore_check_constraints = OFF")
        repository = SQLiteInventoryRepository(self.connection)

        with (
            patch.object(
                inventory_adapter,
                "_snapshot_from_row",
                wraps=inventory_adapter._snapshot_from_row,
            ) as hydrate_row,
            patch.object(
                inventory_adapter,
                "_snapshot_from_selected_row",
                wraps=inventory_adapter._snapshot_from_selected_row,
            ) as hydrate_selected,
        ):
            read = repository.read(
                LibraryReadRequest(target, _context(max_bytes=1_048_576))
            )
            listed = repository.list(
                LibraryListRequest(
                    self.domain_connection(),
                    PageRequest(None, 1),
                    _context(max_bytes=1_048_576),
                )
            )
            stored = repository.store(self.request())
            inspected = repository.inspect(
                SnapshotInspectionRequest(
                    target,
                    self.snapshot.id,
                    self.snapshot.fingerprint,
                    _context(max_bytes=1_048_576),
                )
            )

        self.assertEqual(read.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(listed.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(stored.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(inspected.outcome, RepositoryOutcome.CORRUPT)
        self.assertIsNone(inspected.value)
        hydrate_row.assert_not_called()
        hydrate_selected.assert_not_called()
        self.assertFalse(self.connection.in_transaction)


class InventoryMigrationTests(unittest.TestCase):
    def test_inventory_canonical_sql_constraint_uses_utf8_bytes_and_text_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite.connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            sqlite.migrate(connection)

            def insert(sequence: int, canonical: object) -> None:
                digest = f"{sequence:064x}"
                connection.execute(
                    "INSERT INTO inventory_snapshots("
                    "adapter_id, connection_id, library_id, external_item, snapshot_id, "
                    "idempotency_operation, idempotency_digest, fingerprint_algorithm, "
                    "fingerprint_schema, fingerprint_digest, canonical_input, observed_at"
                    ") VALUES ('synthetic', 'boundary', 'library', ?, ?, "
                    "'snapshot-store', ?, 'sha256', 'snapshot-v1', ?, ?, "
                    "'2026-01-01T00:00:00.000000Z')",
                    (
                        f"item-{sequence}",
                        f"snapshot-{sequence}",
                        digest,
                        digest,
                        canonical,
                    ),
                )

            insert(1, "x" * 1_048_576)
            with self.assertRaises(sqlite3.IntegrityError):
                insert(2, "x" * 1_048_577)
            with self.assertRaises(sqlite3.IntegrityError):
                insert(3, sqlite3.Binary(b"{}"))
            insert(4, "é" * 524_288)
            with self.assertRaises(sqlite3.IntegrityError):
                insert(5, "é" * 524_289)

            self.assertEqual(
                connection.execute(
                    "SELECT typeof(canonical_input), length(CAST(canonical_input AS BLOB)) "
                    "FROM inventory_snapshots ORDER BY snapshot_id"
                ).fetchall(),
                [("text", 1_048_576), ("text", 1_048_576)],
            )
            definition = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'inventory_snapshots'"
            ).fetchone()[0]
            self.assertIn("length(CAST(canonical_input AS BLOB))", definition)
            self.assertIn("1048576", definition)

    def test_old_unreleased_f03_bound_fails_closed_against_corrected_checksum(
        self,
    ) -> None:
        current = sqlite.MIGRATIONS[2]
        old_statements = tuple(
            statement.replace(
                "length(CAST(canonical_input AS BLOB)) BETWEEN 2 AND 1048576",
                "length(canonical_input) BETWEEN 2 AND 524288",
            )
            for statement in current.statements
        )
        old = replace(current, statements=old_statements)
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite.connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", (*sqlite.MIGRATIONS[:2], old)):
                self.assertEqual(sqlite.migrate(connection), 3)
            with self.assertRaises(SchemaError):
                sqlite.migrate(connection)

    def test_genuine_f02_state_survives_f03_migration_and_idempotent_reopen(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            connection = sqlite.connect(path)
            with patch.object(sqlite, "MIGRATIONS", sqlite.MIGRATIONS[:2]):
                self.assertEqual(sqlite.migrate(connection), 2)
            connection.execute(
                "INSERT INTO instance_metadata(key, value) VALUES ('f02-sentinel', 'preserved')"
            )
            connection.execute(
                "INSERT INTO workflow_runs(run_id, definition_version, graph_digest, state, "
                "cancellation_requested, created_at, updated_at) VALUES "
                "('f02-sentinel', 'heartbeat-v1', ?, 'waiting', 0, ?, ?)",
                (
                    "a" * 64,
                    "2026-01-01T00:00:00.000000Z",
                    "2026-01-01T00:00:00.000000Z",
                ),
            )
            connection.execute(
                "INSERT INTO workflow_stages(run_id, stage_id, position, kind, version, "
                "expected_outcome, state) VALUES "
                "('f02-sentinel', 'root', 0, 'root', 'stage-v1', 'completed', 'ready')"
            )
            connection.execute(
                "INSERT INTO jobs(job_id, dedup_key, run_id, stage_id, kind, version, state, "
                "created_at, updated_at) VALUES "
                "('f02-job', 'f02-dedup', 'f02-sentinel', 'root', 'root', 'stage-v1', "
                "'pending', '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z')"
            )
            connection.execute(
                "INSERT INTO audit_events(id, kind, created_at, summary) VALUES "
                "('f02-audit', 'discovery', '2026-01-01T00:00:00.000000Z', 'discovered item')"
            )

            self.assertEqual(sqlite.migrate(connection), 4)
            self.assertEqual(sqlite.migrate(connection), 4)
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM instance_metadata WHERE key = 'f02-sentinel'"
                ).fetchone(),
                ("preserved",),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT state FROM workflow_runs WHERE run_id = 'f02-sentinel'"
                ).fetchone(),
                ("waiting",),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT state FROM jobs WHERE job_id = 'f02-job'"
                ).fetchone(),
                ("pending",),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT summary FROM audit_events WHERE id = 'f02-audit'"
                ).fetchone(),
                ("discovered item",),
            )
            connection.close()

    def test_interrupted_f03_ddl_leaves_exact_f02_state_then_clean_retry_succeeds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            connection = sqlite.connect(path)
            with patch.object(sqlite, "MIGRATIONS", sqlite.MIGRATIONS[:2]):
                sqlite.migrate(connection)
            connection.execute(
                "INSERT INTO instance_metadata(key, value) VALUES ('f02-sentinel', 'preserved')"
            )
            failed = Migration(
                3,
                (sqlite._INVENTORY_SNAPSHOTS_TABLE_SQL, "CREATE TABLE broken ("),
                frozenset({"inventory_snapshots", "broken"}),
            )
            with patch.object(sqlite, "MIGRATIONS", (*sqlite.MIGRATIONS[:2], failed)):
                with self.assertRaises(SchemaError):
                    sqlite.migrate(connection)
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                [(1,), (2,)],
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'inventory_snapshots'"
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM instance_metadata WHERE key = 'f02-sentinel'"
                ).fetchone(),
                ("preserved",),
            )
            self.assertEqual(sqlite.migrate(connection), 4)
            snapshot = _fixture()
            key = IdempotencyKey.from_canonical_input(
                operation="snapshot-store",
                target=snapshot.target,
                value={
                    "fingerprint": snapshot.fingerprint.digest,
                    "schema": snapshot.fingerprint.schema.value,
                    "snapshot": snapshot.id.value,
                },
            )
            request = SnapshotStoreRequest(snapshot, key, None, _context())
            repository = SQLiteInventoryRepository(connection)
            self.assertEqual(
                repository.store(request).outcome, RepositoryOutcome.ACCEPTED
            )
            self.assertEqual(
                repository.store(request).outcome, RepositoryOutcome.REUSED
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM inventory_snapshots"
                ).fetchone(),
                (1,),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM current_inventory_observations"
                ).fetchone(),
                (1,),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM instance_metadata WHERE key = 'f02-sentinel'"
                ).fetchone(),
                ("preserved",),
            )
            connection.close()

    def test_interrupted_f05a_migration_rolls_back_and_preserves_v1_readability(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = sqlite.connect(Path(directory) / "state.sqlite")
            with patch.object(sqlite, "MIGRATIONS", sqlite.MIGRATIONS[:3]):
                self.assertEqual(sqlite.migrate(connection), 3)
            snapshot = _fixture()
            key = IdempotencyKey.from_canonical_input(
                operation="snapshot-store",
                target=snapshot.target,
                value={
                    "fingerprint": snapshot.fingerprint.digest,
                    "schema": snapshot.fingerprint.schema.value,
                    "snapshot": snapshot.id.value,
                },
            )
            repository = SQLiteInventoryRepository(connection)
            self.assertEqual(
                repository.store(
                    SnapshotStoreRequest(snapshot, key, None, _context())
                ).outcome,
                RepositoryOutcome.ACCEPTED,
            )
            failed = Migration(
                4,
                (sqlite.MIGRATIONS[3].statements[0], "CREATE TABLE broken ("),
                frozenset({"inventory_v2_snapshots", "broken"}),
            )
            with patch.object(sqlite, "MIGRATIONS", (*sqlite.MIGRATIONS[:3], failed)):
                with self.assertRaises(SchemaError):
                    sqlite.migrate(connection)
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                [(1,), (2,), (3,)],
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='inventory_v2_snapshots'"
                ).fetchone()
            )
            self.assertEqual(
                repository.read(LibraryReadRequest(snapshot.target, _context())).value,
                snapshot,
            )
            self.assertEqual(sqlite.migrate(connection), 4)
            self.assertEqual(sqlite.migrate(connection), 4)
            self.assertEqual(
                repository.read(LibraryReadRequest(snapshot.target, _context())).value,
                snapshot,
            )
            connection.close()


if __name__ == "__main__":
    unittest.main()
