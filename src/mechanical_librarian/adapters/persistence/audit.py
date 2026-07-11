"""SQLite implementation of the application-owned audit repository."""

from __future__ import annotations

import sqlite3

from mechanical_librarian.application.audit import AuditAppendOutcome, AuditAppendResult, AuditEvent, AuditQueryOutcome, AuditQueryResult, AuditRepository


class SQLiteAuditRepository(AuditRepository):
    """Append-only audit adapter over a bootstrap-supplied SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append(self, event: AuditEvent) -> AuditAppendResult:
        row = event.storage_row
        try:
            self._connection.execute(
                "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)", row
            )
        except sqlite3.IntegrityError:
            return self._duplicate_outcome(row)
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            return AuditAppendResult(AuditAppendOutcome.UNAVAILABLE)
        except sqlite3.DatabaseError:
            return AuditAppendResult(AuditAppendOutcome.INTEGRITY_FAILURE)
        return AuditAppendResult(AuditAppendOutcome.APPENDED)

    def _duplicate_outcome(self, row: tuple[str, str, str, str]) -> AuditAppendResult:
        try:
            stored = self._connection.execute(
                "SELECT id, kind, created_at, summary FROM audit_events WHERE id = ?", (row[0],)
            ).fetchone()
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            return AuditAppendResult(AuditAppendOutcome.UNAVAILABLE)
        except sqlite3.DatabaseError:
            return AuditAppendResult(AuditAppendOutcome.INTEGRITY_FAILURE)
        if stored is None:
            return AuditAppendResult(AuditAppendOutcome.INTEGRITY_FAILURE)
        return AuditAppendResult(AuditAppendOutcome.DUPLICATE if tuple(stored) == row else AuditAppendOutcome.CONFLICT)

    def recent(self, limit: int) -> AuditQueryResult:
        if not 1 <= limit <= 100:
            return AuditQueryResult(AuditQueryOutcome.INVALID)
        try:
            rows = self._connection.execute(
                "SELECT id, kind, created_at, summary FROM audit_events ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            )
            events = tuple(AuditEvent.from_storage(*row) for row in rows)
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            return AuditQueryResult(AuditQueryOutcome.UNAVAILABLE)
        except (sqlite3.DatabaseError, ValueError, TypeError):
            return AuditQueryResult(AuditQueryOutcome.CORRUPT)
        return AuditQueryResult(AuditQueryOutcome.OK, events)
