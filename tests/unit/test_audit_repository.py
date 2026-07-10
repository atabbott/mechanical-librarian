from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.application.audit import AuditEvent
from mechanical_librarian.infrastructure.persistence.audit import SQLiteAuditRepository
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate, transaction


class AuditRepositoryTests(unittest.TestCase):
    def test_append_is_idempotent_and_recent_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            first = AuditEvent("event-one", "discovery", "2026-07-10T10:00:00Z", "discovered item")
            self.assertTrue(repository.append(first))
            self.assertFalse(repository.append(first))
            self.assertEqual(repository.recent(1), (first,))

    def test_transaction_rollback_leaves_no_audit_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            with self.assertRaises(RuntimeError):
                with transaction(connection):
                    repository.append(AuditEvent("event-one", "discovery", "2026-07-10T10:00:00Z", "discovered item"))
                    raise RuntimeError("fail")
            self.assertEqual(repository.recent(1), ())


if __name__ == "__main__":
    unittest.main()
