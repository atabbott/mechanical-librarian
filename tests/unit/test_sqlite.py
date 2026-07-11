from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.infrastructure.persistence import sqlite
from mechanical_librarian.infrastructure.persistence.sqlite import MIGRATIONS, Migration, SchemaError, connect, migrate, transaction


class SqliteFoundationTests(unittest.TestCase):
    def test_initial_migration_checksum_remains_compatible(self) -> None:
        legacy_source = """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL);
        CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE audit_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL);
    """
        self.assertEqual(MIGRATIONS[0].checksum, hashlib.sha256(legacy_source.encode("utf-8")).hexdigest())

    def test_migration_is_idempotent_and_records_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate(connection), 1)
            self.assertEqual(migrate(connection), 1)
            self.assertEqual(connection.execute("SELECT checksum FROM schema_migrations").fetchone()[0], MIGRATIONS[0].checksum)

    def test_transaction_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with self.assertRaises(RuntimeError):
                with transaction(connection) as active:
                    active.execute("INSERT INTO instance_metadata VALUES ('key', 'value')")
                    raise RuntimeError("fail")
            self.assertIsNone(connection.execute("SELECT 1 FROM instance_metadata").fetchone())

    def test_tampered_checksum_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            connection.execute("UPDATE schema_migrations SET checksum = 'tampered'")
            with self.assertRaises(SchemaError):
                migrate(connection)

    def test_failed_migration_rolls_back_all_schema_objects_and_records(self) -> None:
        failed = Migration(
            1,
            (
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
                "CREATE TABLE incomplete_state (id INTEGER PRIMARY KEY)",
                "CREATE TABLE",
            ),
            frozenset({"schema_migrations", "incomplete_state"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", (failed,)):
                with self.assertRaisesRegex(SchemaError, "rolled back"):
                    migrate(connection)
            self.assertEqual(
                connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(),
                [],
            )

    def test_failed_migration_can_restart_from_unchanged_schema(self) -> None:
        failed = Migration(
            1,
            (
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
                "INSERT INTO schema_migrations(version, checksum) VALUES (1, 'private-value')",
                "THIS IS NOT VALID SQL",
            ),
            frozenset({"schema_migrations"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", (failed,)):
                with self.assertRaises(SchemaError) as raised:
                    migrate(connection)
            self.assertNotIn("private-value", str(raised.exception))
            self.assertEqual(migrate(connection), 1)

    def test_constraint_failure_rolls_back_preceding_statements(self) -> None:
        failed = Migration(
            1,
            (
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
                "CREATE TABLE constrained_state (id INTEGER PRIMARY KEY, value TEXT UNIQUE NOT NULL)",
                "INSERT INTO constrained_state(id, value) VALUES (1, 'one')",
                "INSERT INTO constrained_state(id, value) VALUES (2, 'one')",
            ),
            frozenset({"schema_migrations", "constrained_state"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", (failed,)):
                with self.assertRaisesRegex(SchemaError, "rolled back"):
                    migrate(connection)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(), [])

    def test_multi_step_upgrade_is_atomic_and_idempotent(self) -> None:
        upgrade = Migration(
            2,
            ("ALTER TABLE instance_metadata ADD COLUMN source TEXT",),
            frozenset(),
            expected_table_definitions={
                "instance_metadata": "CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL, source TEXT)"
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate(connection), 1)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, upgrade)):
                self.assertEqual(migrate(connection), 2)
                self.assertEqual(migrate(connection), 2)
            self.assertEqual(connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall(), [(1,), (2,)])
            self.assertIn("source", {row[1] for row in connection.execute("PRAGMA table_info(instance_metadata)")})

    def test_migration_can_declare_and_validate_an_index(self) -> None:
        index = Migration(
            2,
            ("CREATE INDEX IF NOT EXISTS audit_events_by_created_at ON audit_events(created_at)",),
            frozenset(),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate(connection), 1)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, index)):
                self.assertEqual(migrate(connection), 2)
                self.assertEqual(migrate(connection), 2)
                self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'audit_events_by_created_at'").fetchone())

    def test_tampered_index_definition_fails_closed(self) -> None:
        index = Migration(2, ("CREATE UNIQUE INDEX audit_events_by_created_at ON audit_events(created_at)",), frozenset())
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, index)):
                migrate(connection)
                connection.execute("DROP INDEX audit_events_by_created_at")
                connection.execute("CREATE INDEX audit_events_by_created_at ON audit_events(id)")
                with self.assertRaisesRegex(SchemaError, "object definition"):
                    migrate(connection)

    def test_quoted_index_definition_fails_closed(self) -> None:
        index = Migration(2, ('CREATE INDEX "audit-events-by-created-at" ON audit_events(created_at)',), frozenset())
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, index)):
                migrate(connection)
                connection.execute('DROP INDEX "audit-events-by-created-at"')
                connection.execute('CREATE INDEX "audit-events-by-created-at" ON audit_events(id)')
                with self.assertRaisesRegex(SchemaError, "object definition"):
                    migrate(connection)

    def test_view_definition_preserves_if_not_exists_in_a_literal(self) -> None:
        view = Migration(2, ("CREATE VIEW audit_label AS SELECT 'a if not exists b' AS label",), frozenset())
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, view)):
                migrate(connection)
                connection.execute("DROP VIEW audit_label")
                connection.execute("CREATE VIEW audit_label AS SELECT 'a b' AS label")
                with self.assertRaisesRegex(SchemaError, "object definition"):
                    migrate(connection)

    def test_migration_can_drop_a_previously_declared_index(self) -> None:
        index = Migration(2, ("CREATE INDEX audit_events_by_created_at ON audit_events(created_at)",), frozenset())
        remove_index = Migration(3, ("DROP INDEX IF EXISTS audit_events_by_created_at",), frozenset())
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, index, remove_index)):
                self.assertEqual(migrate(connection), 3)
                self.assertEqual(migrate(connection), 3)
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'audit_events_by_created_at'").fetchone())

    def test_partial_schema_is_rejected_without_modification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)")
            connection.execute("CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            with self.assertRaisesRegex(SchemaError, "partially applied"):
                migrate(connection)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE name = 'instance_metadata'").fetchone(), ("instance_metadata",))

    def test_newer_schema_and_unrecognized_objects_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            connection.execute("INSERT INTO schema_migrations(version, checksum) VALUES (99, 'unknown')")
            with self.assertRaisesRegex(SchemaError, "newer"):
                migrate(connection)
            connection.execute("DELETE FROM schema_migrations WHERE version = 99")
            connection.execute("CREATE TABLE unknown_data (value TEXT)")
            with self.assertRaisesRegex(SchemaError, "unrecognized"):
                migrate(connection)

    def test_tampered_table_definition_and_view_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            connection.execute("DROP TABLE audit_events")
            connection.execute("CREATE TABLE audit_events (id TEXT PRIMARY KEY, altered TEXT NOT NULL)")
            with self.assertRaisesRegex(SchemaError, "definition"):
                migrate(connection)
            connection.execute("DROP TABLE audit_events")
            connection.execute(
                "CREATE TABLE audit_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL)"
            )
            connection.execute("CREATE VIEW unexpected_view AS SELECT id FROM audit_events")
            with self.assertRaisesRegex(SchemaError, "unrecognized"):
                migrate(connection)

    def test_transaction_control_statements_are_rejected(self) -> None:
        invalid = Migration(
            1,
            ("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)", "COMMIT"),
            frozenset({"schema_migrations"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", (invalid,)):
                with self.assertRaisesRegex(SchemaError, "transaction-control"):
                    migrate(connection)
            self.assertEqual(connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(), [])

    def test_connection_error_redacts_database_path(self) -> None:
        with self.assertRaises(SchemaError) as raised:
            connect(Path("/dev/null/private-state.sqlite"))
        self.assertNotIn("/dev/null", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
