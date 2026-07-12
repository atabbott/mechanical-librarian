from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.library_services.synthetic import (
    SyntheticLibraryReader,
    synthetic_capabilities,
)
from mechanical_librarian.adapters.persistence.inventory import SQLiteInventoryRepository
from mechanical_librarian.application.job_heartbeat import RepositoryOutcome
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    LibraryReadRequest,
    OperationContext,
    ResourceBounds,
    SnapshotStoreRequest,
    TimeProbe,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    ConnectionId,
    IdempotencyKey,
    LibraryId,
)
from mechanical_librarian.domain.inventory import (
    LibraryConnection,
    SecretReference,
    ServiceEndpoint,
)
from mechanical_librarian.infrastructure.persistence import sqlite
from mechanical_librarian.infrastructure.persistence.sqlite import Migration, SchemaError


_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _context() -> OperationContext:
    return OperationContext(
        Deadline(_NOW + timedelta(seconds=30)),
        CancellationProbe(CancellationId("inventory-test"), lambda: False),
        ResourceBounds(1, 524_288),
        TimeProbe(lambda: _NOW),
    )


def _fixture():
    connection = LibraryConnection(
        ConnectionId("synthetic-connection"),
        AdapterId("synthetic"),
        ServiceEndpoint("https://synthetic.invalid"),
        SecretReference("unused-reference"),
        synthetic_capabilities(),
    )
    return SyntheticLibraryReader(connection, LibraryId("synthetic-library")).record.snapshot


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

    def test_store_duplicate_restart_and_scoped_read_preserve_exact_cardinality(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        first = repository.store(self.request())
        second = repository.store(self.request())

        self.assertEqual(first.outcome, RepositoryOutcome.ACCEPTED)
        self.assertEqual(second.outcome, RepositoryOutcome.REUSED)
        self.assertEqual(first.value, self.snapshot)
        self.assertEqual(second.value, self.snapshot)
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM inventory_snapshots").fetchone(),
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
        self.assertEqual(restarted.store(self.request()).outcome, RepositoryOutcome.REUSED)
        self.assertEqual(
            restarted.read(LibraryReadRequest(self.snapshot.target, _context())).value,
            self.snapshot,
        )

    def test_interruption_between_history_and_current_rolls_back_then_retries_cleanly(self) -> None:
        repository = SQLiteInventoryRepository(
            self.connection,
            after_history_insert=lambda: (_ for _ in ()).throw(
                sqlite3.OperationalError("injected interruption")
            ),
        )
        self.assertEqual(repository.store(self.request()).outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM inventory_snapshots").fetchone(),
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

    def test_history_is_append_only_and_cross_scope_reads_do_not_match(self) -> None:
        repository = SQLiteInventoryRepository(self.connection)
        self.assertEqual(repository.store(self.request()).outcome, RepositoryOutcome.ACCEPTED)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE inventory_snapshots SET canonical_input = '{}'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM inventory_snapshots")

        other = _fixture().target
        other = type(other)(other.adapter, other.connection, LibraryId("other-library"), other.external_item)
        self.assertEqual(
            repository.read(LibraryReadRequest(other, _context())).outcome,
            RepositoryOutcome.NOT_FOUND,
        )

    def test_missing_nested_and_deep_canonical_rows_return_typed_corrupt(self) -> None:
        for serialized in ("{}", '{"target":[]}', "[" * 1_000):
            with self.subTest(serialized=serialized[:20]), tempfile.TemporaryDirectory() as directory:
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
                    result = SQLiteInventoryRepository(connection).read(
                        LibraryReadRequest(target, _context())
                    )
                    self.assertEqual(result.outcome, RepositoryOutcome.CORRUPT)
                    self.assertIsNone(result.value)
                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) FROM current_inventory_observations"
                        ).fetchone(),
                        (1,),
                    )
                finally:
                    connection.close()


class InventoryMigrationTests(unittest.TestCase):
    def test_genuine_f02_state_survives_f03_migration_and_idempotent_reopen(self) -> None:
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
                ("a" * 64, "2026-01-01T00:00:00.000000Z", "2026-01-01T00:00:00.000000Z"),
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

            self.assertEqual(sqlite.migrate(connection), 3)
            self.assertEqual(sqlite.migrate(connection), 3)
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

    def test_interrupted_f03_ddl_leaves_exact_f02_state_then_clean_retry_succeeds(self) -> None:
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
            request = SnapshotStoreRequest(snapshot, key, None, _context())
            repository = SQLiteInventoryRepository(connection)
            self.assertEqual(repository.store(request).outcome, RepositoryOutcome.ACCEPTED)
            self.assertEqual(repository.store(request).outcome, RepositoryOutcome.REUSED)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM inventory_snapshots").fetchone(),
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


if __name__ == "__main__":
    unittest.main()
