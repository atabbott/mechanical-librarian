from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.infrastructure.persistence.sqlite import MIGRATIONS, SchemaError, connect, migrate, transaction


class SqliteFoundationTests(unittest.TestCase):
    def test_migration_is_idempotent_and_records_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.assertEqual(migrate(connection), 1)
            self.assertEqual(migrate(connection), 1)
            self.assertEqual(connection.execute("SELECT checksum FROM schema_migrations").fetchone()[0], MIGRATIONS[0].checksum)

    def test_transaction_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            migrate(connection)
            with self.assertRaises(RuntimeError):
                with transaction(connection) as active:
                    active.execute("INSERT INTO instance_metadata VALUES ('key', 'value')")
                    raise RuntimeError("fail")
            self.assertIsNone(connection.execute("SELECT 1 FROM instance_metadata").fetchone())

    def test_tampered_checksum_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            migrate(connection)
            connection.execute("UPDATE schema_migrations SET checksum = 'tampered'")
            with self.assertRaises(SchemaError):
                migrate(connection)


if __name__ == "__main__":
    unittest.main()
