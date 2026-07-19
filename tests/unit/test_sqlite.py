from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mechanical_librarian.domain import audit_values
from mechanical_librarian.infrastructure.persistence import sqlite
from mechanical_librarian.infrastructure.persistence.sqlite import MIGRATIONS, Migration, SchemaError, connect, migrate, transaction
from mechanical_librarian.domain.title_proposals import TitleProposalResult, proposal_set_from_manifest
from tests.unit.test_title_proposals import manifest as title_manifest


CURRENT_VERSION = len(MIGRATIONS)
NEXT_VERSION = CURRENT_VERSION + 1
CURRENT_VERSION_ROWS = [(version,) for version in range(1, CURRENT_VERSION + 1)]


class SqliteFoundationTests(unittest.TestCase):
    def test_f10a_migration_is_additive_from_genuine_schema_six_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", MIGRATIONS[:6]):
                self.assertEqual(migrate(connection), 6)
            proposal = proposal_set_from_manifest(
                manifest=title_manifest(),
                result=TitleProposalResult.NEEDS_REVIEW,
                proposed_value="A Synthetic Book",
            )
            connection.execute(
                "INSERT INTO synthetic_title_proposal_sets("
                "set_id, identity_digest, lineage, lineage_revision, previous_set_id, mode, canonical_set"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal.set_id,
                    proposal.identity_digest,
                    proposal.lineage,
                    proposal.lineage_revision,
                    proposal.previous_set_id,
                    proposal.mode.value,
                    proposal.to_json(),
                ),
            )
            connection.execute(
                "INSERT INTO current_synthetic_title_proposals(lineage, set_id) VALUES (?, ?)",
                (proposal.lineage, proposal.set_id),
            )

            self.assertEqual(migrate(connection), CURRENT_VERSION)
            self.assertEqual(migrate(connection), CURRENT_VERSION)
            self.assertEqual(
                connection.execute(
                    "SELECT canonical_set FROM synthetic_title_proposal_sets WHERE set_id = ?",
                    (proposal.set_id,),
                ).fetchone(),
                (proposal.to_json(),),
            )
            objects = {
                (kind, name)
                for kind, name in connection.execute(
                    "SELECT type, name FROM sqlite_master WHERE name LIKE '%synthetic_title_review%'"
                )
            }
            self.assertIn(("table", "synthetic_title_review_cases"), objects)
            self.assertIn(("table", "synthetic_title_review_case_transitions"), objects)
            self.assertIn(("table", "current_synthetic_title_review_cases"), objects)
            self.assertIn(("trigger", "synthetic_title_review_cases_no_update"), objects)
            self.assertIn(("trigger", "synthetic_title_review_case_transitions_no_delete"), objects)

    def test_every_supported_schema_prefix_upgrades_to_schema_eight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for version in range(1, 8):
                with self.subTest(version=version):
                    path = Path(directory) / f"schema-{version}.sqlite"
                    connection = connect(path)
                    try:
                        with patch.object(sqlite, "MIGRATIONS", MIGRATIONS[:version]):
                            self.assertEqual(migrate(connection), version)
                        connection.execute(
                            "INSERT INTO instance_metadata(key, value) VALUES (?, ?)",
                            (f"schema-{version}-sentinel", "preserved"),
                        )
                        self.assertEqual(migrate(connection), CURRENT_VERSION)
                        self.assertEqual(
                            connection.execute(
                                "SELECT value FROM instance_metadata WHERE key = ?",
                                (f"schema-{version}-sentinel",),
                            ).fetchone(),
                            ("preserved",),
                        )
                    finally:
                        connection.close()

    def test_interrupted_schema_seven_migration_rolls_back_and_retries_cleanly(self) -> None:
        class FailingConnection:
            def __init__(self, connection: sqlite3.Connection, statement: str) -> None:
                self._connection = connection
                self._statement = statement
                self._failed = False

            def execute(self, statement: str, parameters: tuple[object, ...] = ()):
                if statement == self._statement and not self._failed:
                    self._failed = True
                    raise sqlite3.OperationalError("injected full/interruption")
                return self._connection.execute(statement, parameters)

            def __getattr__(self, name: str):
                return getattr(self._connection, name)

        migration = MIGRATIONS[6]
        indexes = (0, len(migration.statements) // 2, len(migration.statements) - 1)
        with tempfile.TemporaryDirectory() as directory:
            for index in indexes:
                with self.subTest(index=index):
                    connection = connect(Path(directory) / f"failure-{index}.sqlite")
                    try:
                        with patch.object(sqlite, "MIGRATIONS", MIGRATIONS[:6]):
                            self.assertEqual(migrate(connection), 6)
                        with self.assertRaisesRegex(SchemaError, "rolled back"):
                            migrate(FailingConnection(connection, migration.statements[index]))  # type: ignore[arg-type]
                        self.assertEqual(
                            connection.execute(
                                "SELECT version FROM schema_migrations ORDER BY version"
                            ).fetchall(),
                            [(version,) for version in range(1, 7)],
                        )
                        self.assertEqual(
                            connection.execute(
                                "SELECT name FROM sqlite_master WHERE name LIKE '%synthetic_title_review%'"
                            ).fetchall(),
                            [],
                        )
                        self.assertEqual(migrate(connection), CURRENT_VERSION)
                    finally:
                        connection.close()

    def test_interrupted_schema_eight_migration_rolls_back_and_retries_cleanly(self) -> None:
        class FailingConnection:
            def __init__(self, connection: sqlite3.Connection, statement: str) -> None:
                self._connection = connection
                self._statement = statement
                self._failed = False

            def execute(self, statement: str, parameters: tuple[object, ...] = ()):
                if statement == self._statement and not self._failed:
                    self._failed = True
                    raise sqlite3.OperationalError("injected schema-eight interruption")
                return self._connection.execute(statement, parameters)

            def __getattr__(self, name: str):
                return getattr(self._connection, name)

        migration = MIGRATIONS[7]
        indexes = (0, len(migration.statements) // 2, len(migration.statements) - 1)
        with tempfile.TemporaryDirectory() as directory:
            for index in indexes:
                with self.subTest(index=index):
                    connection = connect(Path(directory) / f"schema-eight-{index}.sqlite")
                    try:
                        with patch.object(sqlite, "MIGRATIONS", MIGRATIONS[:7]):
                            self.assertEqual(migrate(connection), 7)
                        connection.execute(
                            "INSERT INTO instance_metadata(key,value) VALUES "
                            "('schema-eight-sentinel','preserved')"
                        )
                        with self.assertRaisesRegex(SchemaError, "rolled back"):
                            migrate(  # type: ignore[arg-type]
                                FailingConnection(
                                    connection, migration.statements[index]
                                )
                            )
                        self.assertEqual(
                            connection.execute(
                                "SELECT version FROM schema_migrations ORDER BY version"
                            ).fetchall(),
                            [(version,) for version in range(1, 8)],
                        )
                        self.assertEqual(
                            connection.execute(
                                "SELECT name FROM sqlite_master WHERE name IN "
                                "('inventory_snapshot_file_bindings','epub_evidence_jobs',"
                                "'epub_opening_evidence')"
                            ).fetchall(),
                            [],
                        )
                        self.assertEqual(migrate(connection), CURRENT_VERSION)
                        self.assertEqual(
                            connection.execute(
                                "SELECT value FROM instance_metadata WHERE key="
                                "'schema-eight-sentinel'"
                            ).fetchone(),
                            ("preserved",),
                        )
                    finally:
                        connection.close()

    def test_f09a_migration_is_additive_from_genuine_version_five_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", MIGRATIONS[:5]):
                self.assertEqual(migrate(connection), 5)
            connection.execute(
                "INSERT INTO instance_metadata(key, value) VALUES ('f09a-sentinel', 'preserved')"
            )

            self.assertEqual(migrate(connection), CURRENT_VERSION)
            self.assertEqual(migrate(connection), CURRENT_VERSION)
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM instance_metadata WHERE key = 'f09a-sentinel'"
                ).fetchone(),
                ("preserved",),
            )
            objects = {
                (kind, name)
                for kind, name in connection.execute(
                    "SELECT type, name FROM sqlite_master WHERE name LIKE '%synthetic_title_proposal%'"
                )
            }
            self.assertIn(("table", "synthetic_title_proposal_sets"), objects)
            self.assertIn(("table", "current_synthetic_title_proposals"), objects)
            self.assertIn(("trigger", "synthetic_title_proposal_sets_no_update"), objects)
            self.assertIn(("trigger", "synthetic_title_proposal_sets_no_delete"), objects)
            self.assertIn(("trigger", "current_synthetic_title_proposals_no_delete"), objects)

    def test_integrity_verification_requires_one_exact_ok_row(self) -> None:
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = [("ok",), ("extra",)]

        with self.assertRaisesRegex(SchemaError, "integrity"):
            sqlite._verify_integrity(connection)

    def test_safe_copy_rejects_fifo_replacement_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFO replacement requires POSIX mkfifo")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "state.sqlite"
            destination = Path(directory) / "copied.sqlite"
            source.write_bytes(b"regular state")
            expected = sqlite._state_snapshot(source)[""]
            source.unlink()
            os.mkfifo(source)

            with self.assertRaisesRegex(SchemaError, "changed"):
                sqlite._copy_regular_state(source, destination, expected)
            self.assertFalse(destination.exists())

    def test_safe_copy_rejects_source_change_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "state.sqlite"
            destination = Path(directory) / "copied.sqlite"
            source.write_bytes(b"stable state")
            expected = sqlite._state_snapshot(source)[""]
            real_copy = sqlite.shutil.copyfileobj

            def copy_then_change(input_file, output_file) -> None:
                real_copy(input_file, output_file)
                source.write_bytes(b"changed state")

            with (
                patch.object(
                    sqlite.shutil,
                    "copyfileobj",
                    side_effect=copy_then_change,
                ),
                self.assertRaisesRegex(SchemaError, "changed"),
            ):
                sqlite._copy_regular_state(source, destination, expected)

    def test_competing_publication_timeout_is_a_typed_safe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staged = root / "staged.sqlite"
            winner_staged = root / "winner.sqlite"
            target = root / "state.sqlite"
            staged.write_bytes(b"complete staged state")
            staged.chmod(0o600)
            winner_staged.write_bytes(b"complete winner state")
            os.link(winner_staged, target)

            with (
                patch.object(sqlite, "_state_snapshot", return_value={}),
                patch.object(sqlite.time, "monotonic", side_effect=(0.0, 6.0)),
                self.assertRaisesRegex(SchemaError, "did not settle"),
            ):
                sqlite.publish_staged_state(staged, target)

            self.assertEqual(target.read_bytes(), b"complete winner state")
            self.assertEqual(target.stat().st_nlink, 2)
            self.assertEqual(staged.read_bytes(), b"complete staged state")

    def test_competing_publication_disappearance_is_a_typed_safe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "state.sqlite"
            target.write_bytes(b"competing state")
            real_link = sqlite.os.link
            conflict_observed = False
            staged_path: Path | None = None

            def remove_after_conflict(
                source: Path,
                destination: Path,
                **kwargs: object,
            ) -> None:
                nonlocal conflict_observed
                try:
                    real_link(source, destination, **kwargs)
                except FileExistsError:
                    conflict_observed = True
                    target.unlink()
                    raise

            with sqlite.staged_state(target) as staged:
                staged_path = staged
                staged.write_bytes(b"complete staged state")
                with (
                    patch.object(sqlite, "_state_snapshot", return_value={}),
                    patch.object(sqlite.os, "link", side_effect=remove_after_conflict),
                    self.assertRaisesRegex(SchemaError, "is unavailable"),
                ):
                    sqlite.publish_staged_state(staged, target)

                self.assertTrue(conflict_observed)
                self.assertFalse(target.exists())
                self.assertEqual(staged.read_bytes(), b"complete staged state")

            self.assertIsNotNone(staged_path)
            self.assertFalse(staged_path.exists())
            self.assertFalse(target.exists())

    def test_competing_non_regular_publication_is_a_typed_safe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "state.sqlite"
            target.write_bytes(b"competing state")
            real_link = sqlite.os.link
            conflict_observed = False
            staged_path: Path | None = None

            def replace_with_directory_after_conflict(
                source: Path,
                destination: Path,
                **kwargs: object,
            ) -> None:
                nonlocal conflict_observed
                try:
                    real_link(source, destination, **kwargs)
                except FileExistsError:
                    conflict_observed = True
                    target.unlink()
                    target.mkdir()
                    raise

            with sqlite.staged_state(target) as staged:
                staged_path = staged
                staged.write_bytes(b"complete staged state")
                with (
                    patch.object(sqlite, "_state_snapshot", return_value={}),
                    patch.object(
                        sqlite.os,
                        "link",
                        side_effect=replace_with_directory_after_conflict,
                    ),
                    self.assertRaisesRegex(SchemaError, "not a regular file"),
                ):
                    sqlite.publish_staged_state(staged, target)

                self.assertTrue(conflict_observed)
                self.assertTrue(target.is_dir())
                self.assertEqual(staged.read_bytes(), b"complete staged state")

            self.assertIsNotNone(staged_path)
            self.assertFalse(staged_path.exists())
            self.assertTrue(target.is_dir())

    def test_in_memory_database_is_not_a_durable_connection(self) -> None:
        with self.assertRaisesRegex(SchemaError, "filesystem database"):
            connect(Path(":memory:"))

    def test_wal_failure_precedes_every_migration_write(self) -> None:
        class WalFailureConnection:
            def __init__(self, connection: sqlite3.Connection) -> None:
                self._connection = connection

            def execute(self, statement: str, parameters: tuple[object, ...] = ()):
                if statement == "PRAGMA journal_mode = WAL":
                    raise sqlite3.OperationalError("injected WAL failure")
                return self._connection.execute(statement, parameters)

            def __getattr__(self, name: str):
                return getattr(self._connection, name)

        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)

            with self.assertRaisesRegex(SchemaError, "journal mode"):
                migrate(WalFailureConnection(connection))  # type: ignore[arg-type]

            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall(),
                [],
            )

    def test_initial_migration_checksum_remains_stable(self) -> None:
        self.assertEqual(
            MIGRATIONS[0].checksum,
            "9634bb21b101030bb214f62f59e28a2baecbf91f82d0aa127a7a140507d06fea",
        )

    def test_migration_is_idempotent_and_records_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate(connection), CURRENT_VERSION)
            self.assertEqual(migrate(connection), CURRENT_VERSION)
            self.assertEqual(connection.execute("SELECT checksum FROM schema_migrations").fetchone()[0], MIGRATIONS[0].checksum)

    def test_f02_migration_is_additive_from_genuine_f01_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            connection = connect(path)
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", (MIGRATIONS[0],)):
                self.assertEqual(migrate(connection), 1)
            connection.execute(
                "INSERT INTO instance_metadata(key, value) VALUES ('sentinel', 'preserved')"
            )

            self.assertEqual(migrate(connection), CURRENT_VERSION)
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM instance_metadata WHERE key = 'sentinel'"
                ).fetchone(),
                ("preserved",),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                CURRENT_VERSION_ROWS,
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
                ).fetchone()
            )

    def test_f02_schema_rejects_invalid_digest_timestamp_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            valid = (
                "run-one",
                "heartbeat-v1",
                "a" * 64,
                "waiting",
                "2026-01-01T00:00:00.000000Z",
                "2026-01-01T00:00:00.000000Z",
            )
            for index, invalid in (
                (2, "not-a-digest"),
                (3, "unknown"),
                (4, "2026-99-99T00:00:00.000000Z"),
            ):
                values = list(valid)
                values[index] = invalid
                with self.subTest(invalid=invalid), self.assertRaises(
                    sqlite3.IntegrityError
                ):
                    connection.execute(
                        "INSERT INTO workflow_runs(run_id, definition_version, graph_digest, state, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        values,
                    )

    def test_plain_sqlite_integrity_check_supports_initial_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            connection = connect(path)
            migrate(connection)
            connection.close()

            plain = sqlite3.connect(path)
            self.addCleanup(plain.close)
            self.assertEqual(plain.execute("PRAGMA integrity_check").fetchone(), ("ok",))

    def test_python_vocabulary_change_cannot_mutate_recorded_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            recorded_checksum = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version = 1"
            ).fetchone()[0]
            expanded = frozenset({*audit_values.SAFE_AUDIT_SUMMARIES, "new phrase"})
            with patch.object(audit_values, "SAFE_AUDIT_SUMMARIES", expanded):
                self.assertEqual(
                    audit_values.validated_safe_audit_summary("new phrase"),
                    "new phrase",
                )
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute(
                        "INSERT INTO audit_events(id, kind, created_at, summary) "
                        "VALUES ('event-one', 'discovery', "
                        "'2026-07-10T10:00:00.000000Z', 'new phrase')"
                    )
            self.assertEqual(MIGRATIONS[0].checksum, recorded_checksum)

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
            self.assertEqual(migrate(connection), CURRENT_VERSION)

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
            NEXT_VERSION,
            ("ALTER TABLE instance_metadata ADD COLUMN source TEXT",),
            frozenset(),
            expected_table_definitions={
                "instance_metadata": "CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL, source TEXT)"
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate(connection), CURRENT_VERSION)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, upgrade)):
                self.assertEqual(migrate(connection), NEXT_VERSION)
                self.assertEqual(migrate(connection), NEXT_VERSION)
            self.assertEqual(connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall(), [*CURRENT_VERSION_ROWS, (NEXT_VERSION,)])
            self.assertIn("source", {row[1] for row in connection.execute("PRAGMA table_info(instance_metadata)")})

    def test_migration_can_declare_and_validate_an_index(self) -> None:
        index = Migration(
            NEXT_VERSION,
            ("CREATE INDEX IF NOT EXISTS audit_events_by_kind_test ON audit_events(kind)",),
            frozenset(),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate(connection), CURRENT_VERSION)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, index)):
                self.assertEqual(migrate(connection), NEXT_VERSION)
                self.assertEqual(migrate(connection), NEXT_VERSION)
                self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'audit_events_by_kind_test'").fetchone())

    def test_tampered_index_definition_fails_closed(self) -> None:
        index = Migration(NEXT_VERSION, ("CREATE UNIQUE INDEX audit_events_by_kind_test ON audit_events(kind)",), frozenset())
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, index)):
                migrate(connection)
                connection.execute("DROP INDEX audit_events_by_kind_test")
                connection.execute("CREATE INDEX audit_events_by_kind_test ON audit_events(id)")
                with self.assertRaisesRegex(SchemaError, "object definition"):
                    migrate(connection)

    def test_quoted_index_definition_fails_closed(self) -> None:
        index = Migration(NEXT_VERSION, ('CREATE INDEX "audit-events-by-kind-test" ON audit_events(kind)',), frozenset())
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, index)):
                migrate(connection)
                connection.execute('DROP INDEX "audit-events-by-kind-test"')
                connection.execute('CREATE INDEX "audit-events-by-kind-test" ON audit_events(id)')
                with self.assertRaisesRegex(SchemaError, "object definition"):
                    migrate(connection)

    def test_view_definition_preserves_if_not_exists_in_a_literal(self) -> None:
        view = Migration(NEXT_VERSION, ("CREATE VIEW audit_label AS SELECT 'a if not exists b' AS label",), frozenset())
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

    def test_schema_sql_normalisation_preserves_quoted_contents(self) -> None:
        baseline = (
            "CREATE VIEW quoted_values AS SELECT 'Alpha  Beta', 'it''s', "
            "'-- literal', '/* literal */', "
            '"Mixed -- Name", `Tick /* Name */`, [Bracket -- Name], X\'A0 B1\''
        )
        formatting_only = (
            "create\n\tview   quoted_values as\tselect 'Alpha  Beta', 'it''s', "
            "'-- literal', '/* literal */', "
            '"Mixed -- Name", `Tick /* Name */`, [Bracket -- Name], x\'A0 B1\';'
        )
        self.assertEqual(
            sqlite._normalise_schema_sql(baseline),
            sqlite._normalise_schema_sql(formatting_only),
        )
        tampered_values = (
            baseline.replace("Alpha  Beta", "alpha  beta"),
            baseline.replace("Alpha  Beta", "Alpha Beta"),
            baseline.replace("it''s", "IT''S"),
            baseline.replace("Mixed -- Name", "mixed -- name"),
            baseline.replace("Tick /* Name */", "Tick  /* Name */"),
            baseline.replace("Bracket -- Name", "Bracket\t-- Name"),
            baseline.replace("A0 B1", "a0 b1"),
        )
        for tampered in tampered_values:
            with self.subTest(tampered=tampered):
                self.assertNotEqual(
                    sqlite._normalise_schema_sql(baseline),
                    sqlite._normalise_schema_sql(tampered),
                )

    def test_literal_tampering_in_view_trigger_and_table_fails_closed(self) -> None:
        cases = (
            (
                "view",
                "CREATE VIEW literal_object AS SELECT 'Alpha  Beta' AS value",
                "DROP VIEW literal_object",
                "CREATE VIEW literal_object AS SELECT 'alpha  beta' AS value",
            ),
            (
                "trigger",
                "CREATE TRIGGER literal_object AFTER INSERT ON audit_events "
                "BEGIN SELECT 'it''s  Safe'; END",
                "DROP TRIGGER literal_object",
                "CREATE TRIGGER literal_object AFTER INSERT ON audit_events "
                "BEGIN SELECT 'it''''s  Safe'; END",
            ),
            (
                "view with literal tab and newline",
                "CREATE VIEW literal_object AS SELECT 'Alpha\tBeta\nGamma' AS value",
                "DROP VIEW literal_object",
                "CREATE VIEW literal_object AS SELECT 'Alpha Beta Gamma' AS value",
            ),
        )
        for kind, declaration, drop, tampered in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                connection = connect(Path(directory) / "state.sqlite")
                self.addCleanup(connection.close)
                migrate(connection)
                extension = Migration(NEXT_VERSION, (declaration,), frozenset())
                with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                    migrate(connection)
                    connection.execute(drop)
                    connection.execute(tampered)
                    with self.assertRaisesRegex(SchemaError, "object definition"):
                        migrate(connection)

        table_migration = Migration(
            1,
            (
                "CREATE TABLE schema_migrations "
                "(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
                "CREATE TABLE literal_table "
                "(value TEXT DEFAULT 'Alpha  Beta' CHECK(value != 'it''s'))",
            ),
            frozenset({"schema_migrations", "literal_table"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            with patch.object(sqlite, "MIGRATIONS", (table_migration,)):
                migrate(connection)
                connection.execute("DROP TABLE literal_table")
                connection.execute(
                    "CREATE TABLE literal_table "
                    "(value TEXT DEFAULT 'Alpha Beta' CHECK(value != 'it''s'))"
                )
                with self.assertRaisesRegex(SchemaError, "definition"):
                    migrate(connection)

    def test_supported_non_table_objects_survive_restart_and_validate_exactly(self) -> None:
        extension = Migration(
            NEXT_VERSION,
            (
                "CREATE INDEX IF NOT EXISTS audit_events_by_kind ON audit_events(kind)",
                "CREATE VIEW IF NOT EXISTS audit_kinds AS SELECT kind FROM audit_events",
                "CREATE TRIGGER IF NOT EXISTS audit_inserted AFTER INSERT ON audit_events "
                "BEGIN INSERT OR REPLACE INTO instance_metadata(key, value) "
                "VALUES ('last audit action', 'inserted'); END",
            ),
            frozenset(),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            connection = connect(path)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                self.assertEqual(migrate(connection), NEXT_VERSION)
                self.assertEqual(migrate(connection), NEXT_VERSION)
            connection.close()

            reopened = connect(path)
            self.addCleanup(reopened.close)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                self.assertEqual(migrate(reopened), NEXT_VERSION)
                objects = set(
                    reopened.execute(
                        "SELECT type, name FROM sqlite_master "
                        "WHERE name IN ('audit_events_by_kind', 'audit_kinds', 'audit_inserted')"
                    )
                )
                self.assertEqual(
                    objects,
                    {
                        ("index", "audit_events_by_kind"),
                        ("view", "audit_kinds"),
                        ("trigger", "audit_inserted"),
                    },
                )

    def test_temporary_and_schema_qualified_declarations_are_rejected_durably(self) -> None:
        declarations = (
            "CREATE TEMP VIEW transient_object AS SELECT 1",
            "CREATE TEMPORARY TABLE transient_object(value TEXT)",
            "CREATE INDEX temp.transient_object ON audit_events(kind)",
            "CREATE VIEW main.transient_object AS SELECT 1",
            "CREATE TRIGGER other.transient_object AFTER INSERT ON audit_events BEGIN SELECT 1; END",
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.sqlite"
                connection = connect(path)
                migrate(connection)
                extension = Migration(NEXT_VERSION, (declaration,), frozenset())
                with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                    with self.assertRaisesRegex(SchemaError, "temporary|schema-qualified"):
                        migrate(connection)
                self.assertEqual(
                    connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall(),
                    CURRENT_VERSION_ROWS,
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_temp_master WHERE name = 'transient_object'"
                    ).fetchone()
                )
                connection.close()

                reopened = connect(path)
                self.addCleanup(reopened.close)
                self.assertIsNone(
                    reopened.execute(
                        "SELECT 1 FROM sqlite_master WHERE name = 'transient_object'"
                    ).fetchone()
                )
                self.assertIsNone(
                    reopened.execute(
                        "SELECT 1 FROM sqlite_temp_master WHERE name = 'transient_object'"
                    ).fetchone()
                )
                self.assertEqual(
                    reopened.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall(),
                    CURRENT_VERSION_ROWS,
                )

    def test_comment_obscured_temporary_declarations_are_rejected_durably(self) -> None:
        declarations = (
            "CREATE /* comment */ TEMP VIEW transient_object AS SELECT 1",
            "CREATE -- comment\n TEMPORARY TABLE transient_object(value TEXT)",
            "CREATE VIEW temp /* comment */ . transient_object AS SELECT 1",
            "CREATE TRIGGER temp -- comment\n . transient_object "
            "AFTER INSERT ON audit_events BEGIN SELECT 1; END",
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.sqlite"
                connection = connect(path)
                migrate(connection)
                extension = Migration(NEXT_VERSION, (declaration,), frozenset())
                with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                    with self.assertRaisesRegex(SchemaError, "comments"):
                        migrate(connection)
                self.assertEqual(
                    connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall(),
                    CURRENT_VERSION_ROWS,
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_temp_master WHERE name = 'transient_object'"
                    ).fetchone()
                )
                connection.close()

                reopened = connect(path)
                self.addCleanup(reopened.close)
                self.assertIsNone(
                    reopened.execute(
                        "SELECT 1 FROM sqlite_master WHERE name = 'transient_object'"
                    ).fetchone()
                )
                self.assertIsNone(
                    reopened.execute(
                        "SELECT 1 FROM sqlite_temp_master WHERE name = 'transient_object'"
                    ).fetchone()
                )
                self.assertEqual(
                    reopened.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall(),
                    CURRENT_VERSION_ROWS,
                )

    def test_sql_comments_outside_quotes_are_rejected_durably(self) -> None:
        declarations = (
            "CREATE/**/TABLE comment_object(value TEXT)",
            "CREATE -- comment\n TABLE comment_object(value TEXT)",
            "CREATE VIEW comment_object AS /* comment */ SELECT 1",
            "CREATE TRIGGER comment_object /* comment */ "
            "AFTER INSERT ON audit_events BEGIN SELECT 1; END",
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "state.sqlite"
                connection = connect(path)
                migrate(connection)
                extension = Migration(NEXT_VERSION, (declaration,), frozenset())
                with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                    with self.assertRaisesRegex(SchemaError, "comments"):
                        migrate(connection)
                self.assertEqual(
                    connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall(),
                    CURRENT_VERSION_ROWS,
                )
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name = 'comment_object'"
                    ).fetchone()
                )
                connection.close()

                reopened = connect(path)
                self.addCleanup(reopened.close)
                self.assertEqual(migrate(reopened), CURRENT_VERSION)
                self.assertIsNone(
                    reopened.execute(
                        "SELECT 1 FROM sqlite_master WHERE name = 'comment_object'"
                    ).fetchone()
                )

    def test_comment_markers_inside_quoted_content_are_allowed(self) -> None:
        extension = Migration(
            NEXT_VERSION,
            (
                "CREATE VIEW quoted_comment_markers AS SELECT "
                "'-- literal' AS line_value, '/* literal */' AS block_value, "
                '1 AS "-- identifier", 2 AS `/* identifier */`, 3 AS [-- bracket]',
            ),
            frozenset(),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.sqlite"
            connection = connect(path)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                self.assertEqual(migrate(connection), NEXT_VERSION)
                self.assertEqual(migrate(connection), NEXT_VERSION)
            connection.close()

            reopened = connect(path)
            self.addCleanup(reopened.close)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                self.assertEqual(migrate(reopened), NEXT_VERSION)

    def test_attached_schema_declaration_cannot_be_recorded(self) -> None:
        extension = Migration(
            NEXT_VERSION,
            ("CREATE VIEW auxiliary /* comment */ . external_object AS SELECT 1",),
            frozenset(),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            connection = connect(path / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            connection.execute(
                "ATTACH DATABASE ? AS auxiliary",
                (str(path / "auxiliary.sqlite"),),
            )
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, extension)):
                with self.assertRaisesRegex(SchemaError, "attached schema"):
                    migrate(connection)
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                CURRENT_VERSION_ROWS,
            )
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM auxiliary.sqlite_master WHERE name = 'external_object'"
                ).fetchone()
            )
            connection.execute("DETACH DATABASE auxiliary")
            connection.close()

            reopened = connect(path / "state.sqlite")
            self.addCleanup(reopened.close)
            self.assertEqual(migrate(reopened), CURRENT_VERSION)
            self.assertEqual(
                reopened.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                CURRENT_VERSION_ROWS,
            )

    def test_preexisting_temporary_object_is_rejected_independently_of_sql_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            connection.execute("CREATE TEMP VIEW external_temp_object AS SELECT 1")
            with self.assertRaisesRegex(SchemaError, "temporary object"):
                migrate(connection)
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                CURRENT_VERSION_ROWS,
            )
            connection.execute("DROP VIEW external_temp_object")
            self.assertEqual(migrate(connection), CURRENT_VERSION)

    def test_failure_after_non_table_ddl_rolls_back_objects_and_checksum(self) -> None:
        failed = Migration(
            NEXT_VERSION,
            (
                "CREATE INDEX rollback_index ON audit_events(kind)",
                "CREATE VIEW rollback_view AS SELECT kind FROM audit_events",
                "CREATE TRIGGER rollback_trigger AFTER INSERT ON audit_events BEGIN SELECT 1; END",
                "CREATE TABLE",
            ),
            frozenset(),
        )
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, failed)):
                with self.assertRaisesRegex(SchemaError, "rolled back"):
                    migrate(connection)
            self.assertEqual(
                connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
                CURRENT_VERSION_ROWS,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE name IN ('rollback_index', 'rollback_view', 'rollback_trigger')"
                ).fetchall(),
                [],
            )

    def test_migration_can_drop_a_previously_declared_index(self) -> None:
        index = Migration(NEXT_VERSION, ("CREATE INDEX removable_audit_index ON audit_events(kind)",), frozenset())
        remove_index = Migration(NEXT_VERSION + 1, ("DROP INDEX IF EXISTS removable_audit_index",), frozenset())
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            with patch.object(sqlite, "MIGRATIONS", (*MIGRATIONS, index, remove_index)):
                self.assertEqual(migrate(connection), NEXT_VERSION + 1)
                self.assertEqual(migrate(connection), NEXT_VERSION + 1)
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'removable_audit_index'").fetchone())

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
            connection.execute(
                "CREATE TABLE audit_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, "
                "created_at TEXT NOT NULL, summary TEXT NOT NULL, altered TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX audit_events_by_created_at "
                "ON audit_events(created_at DESC, id DESC)"
            )
            with self.assertRaisesRegex(SchemaError, "definition"):
                migrate(connection)
            connection.execute("DROP TABLE audit_events")
            connection.execute(
                "CREATE TABLE audit_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE INDEX audit_events_by_created_at "
                "ON audit_events(created_at DESC, id DESC)"
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
