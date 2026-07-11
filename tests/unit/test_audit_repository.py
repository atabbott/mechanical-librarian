from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from mechanical_librarian.adapters.persistence.audit import SQLiteAuditRepository
from mechanical_librarian.application.audit import AuditAppendOutcome, AuditEvent, AuditEventKind, AuditEventId, AuditQueryOutcome, SafeAuditSummary
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate, transaction


def event(identifier: str, timestamp: str = "2026-07-10T10:00:00Z", summary: str = "discovered item") -> AuditEvent:
    return AuditEvent(AuditEventId(identifier), AuditEventKind("discovery"), datetime.fromisoformat(timestamp.replace("Z", "+00:00")), SafeAuditSummary(summary))


class AuditRepositoryTests(unittest.TestCase):
    def test_append_replay_conflict_and_bounded_ordering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            first = event("event-one")
            self.assertEqual(repository.append(first).outcome, AuditAppendOutcome.APPENDED)
            self.assertEqual(repository.append(first).outcome, AuditAppendOutcome.DUPLICATE)
            self.assertEqual(repository.append(event("event-one", summary="different summary")).outcome, AuditAppendOutcome.CONFLICT)
            repository.append(event("event-two", "2026-07-11T10:00:00Z"))
            result = repository.recent(1)
            self.assertEqual(result.outcome, AuditQueryOutcome.OK)
            self.assertEqual(result.events, (event("event-two", "2026-07-11T10:00:00Z"),))

    def test_invalid_and_hostile_values_are_rejected_at_boundary(self) -> None:
        with self.assertRaises(ValueError):
            SafeAuditSummary("https://private.example/token")
        with self.assertRaises(ValueError):
            SafeAuditSummary("/private/library")
        with self.assertRaises(ValueError):
            SafeAuditSummary('{"provider": "payload"}')
        with self.assertRaises(ValueError):
            AuditEvent(AuditEventId("event-one"), AuditEventKind("discovery"), datetime.now(), SafeAuditSummary("safe"))

    def test_unavailable_corrupt_and_invalid_query_return_typed_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            self.assertEqual(repository.recent(0).outcome, AuditQueryOutcome.INVALID)
            connection.execute("INSERT INTO audit_events(id, kind, created_at, summary) VALUES ('event-one', 'discovery', 'bad-time', 'safe')")
            self.assertEqual(repository.recent(1).outcome, AuditQueryOutcome.CORRUPT)
            connection.close()
            self.assertEqual(repository.append(event("event-two")).outcome, AuditAppendOutcome.UNAVAILABLE)

    def test_transaction_rollback_leaves_no_audit_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            migrate(connection)
            repository = SQLiteAuditRepository(connection)
            with self.assertRaises(RuntimeError):
                with transaction(connection):
                    self.assertEqual(repository.append(event("event-one")).outcome, AuditAppendOutcome.APPENDED)
                    raise RuntimeError("fail")
            self.assertEqual(repository.recent(1).events, ())


if __name__ == "__main__":
    unittest.main()
