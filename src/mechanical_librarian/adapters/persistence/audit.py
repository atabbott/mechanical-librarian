"""SQLite implementation of the application-owned audit repository."""

from __future__ import annotations

import sqlite3

from mechanical_librarian.application.audit import AuditEvent, AuditRepository


class SQLiteAuditRepository(AuditRepository):
    """Append-only audit adapter over a bootstrap-supplied SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append(self, event: AuditEvent) -> bool:
        try:
            self._connection.execute(
                "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)",
                (event.id, event.kind, event.created_at, event.summary),
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def recent(self, limit: int) -> tuple[AuditEvent, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("audit query limit must be between 1 and 100")
        rows = self._connection.execute(
            "SELECT id, kind, created_at, summary FROM audit_events ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        )
        return tuple(AuditEvent(*row) for row in rows)
