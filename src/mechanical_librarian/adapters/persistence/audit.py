"""SQLite implementation of the application-owned audit repository."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from mechanical_librarian.application.audit import (
    AuditAppendOutcome,
    AuditAppendResult,
    AuditEvent,
    AuditEventKind,
    AuditQueryOutcome,
    AuditQueryResult,
    AuditRepository,
    SafeAuditSummary,
)
from mechanical_librarian.domain.identifiers import AuditEventId
from mechanical_librarian.domain.timestamps import canonical_utc_timestamp


_AuditStorageRow = tuple[str, str, str, str]


def _serialize_event(event: AuditEvent) -> _AuditStorageRow:
    if (
        type(event) is not AuditEvent
        or type(event.id) is not AuditEventId
        or type(event.kind) is not AuditEventKind
        or type(event.summary) is not SafeAuditSummary
        or type(event.created_at) is not datetime
    ):
        raise ValueError("audit persistence requires exact event value types")
    try:
        utc_timestamp = event.created_at.astimezone(timezone.utc)
    except Exception:
        raise ValueError("audit timestamp could not be converted to UTC") from None
    validated = AuditEvent(
        AuditEventId(event.id.value),
        AuditEventKind(event.kind.value),
        utc_timestamp,
        SafeAuditSummary(event.summary.value),
    )
    timestamp = (
        validated.created_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    return (
        validated.id.value,
        validated.kind.value,
        timestamp,
        validated.summary.value,
    )


def _deserialize_event(row: tuple[object, ...]) -> AuditEvent:
    if len(row) != 4 or not all(type(value) is str for value in row):
        raise ValueError("stored audit event has invalid value types")
    event_id, kind, created_at, summary = row
    if canonical_utc_timestamp(created_at) != created_at:
        raise ValueError("stored audit event has an invalid timestamp")
    timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return AuditEvent(
        AuditEventId(event_id),
        AuditEventKind(kind),
        timestamp,
        SafeAuditSummary(summary),
    )


class SQLiteAuditRepository(AuditRepository):
    """Append-only audit adapter over a bootstrap-supplied SQLite connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append(self, event: AuditEvent) -> AuditAppendResult:
        if type(event) is not AuditEvent:
            return AuditAppendResult(AuditAppendOutcome.INVALID)
        try:
            row = _serialize_event(event)
        except (AttributeError, OverflowError, TypeError, ValueError):
            return AuditAppendResult(AuditAppendOutcome.INVALID)
        try:
            self._connection.execute(
                "INSERT INTO audit_events(id, kind, created_at, summary) "
                "VALUES (?, ?, ?, ?)",
                row,
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
                "SELECT id, kind, created_at, summary FROM audit_events WHERE id = ?",
                (row[0],),
            ).fetchone()
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            return AuditAppendResult(AuditAppendOutcome.UNAVAILABLE)
        except sqlite3.DatabaseError:
            return AuditAppendResult(AuditAppendOutcome.INTEGRITY_FAILURE)
        if stored is None:
            return AuditAppendResult(AuditAppendOutcome.INTEGRITY_FAILURE)
        try:
            stored_row = _serialize_event(_deserialize_event(stored))
        except (OverflowError, TypeError, ValueError):
            return AuditAppendResult(AuditAppendOutcome.INTEGRITY_FAILURE)
        outcome = (
            AuditAppendOutcome.DUPLICATE
            if stored_row == row
            else AuditAppendOutcome.CONFLICT
        )
        return AuditAppendResult(outcome)

    def recent(self, limit: int) -> AuditQueryResult:
        if type(limit) is not int or not 1 <= limit <= 100:
            return AuditQueryResult(AuditQueryOutcome.INVALID)
        try:
            rows = self._connection.execute(
                "SELECT id, kind, created_at, summary FROM audit_events "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            )
            events = tuple(_deserialize_event(row) for row in rows)
        except (sqlite3.OperationalError, sqlite3.ProgrammingError):
            return AuditQueryResult(AuditQueryOutcome.UNAVAILABLE)
        except (sqlite3.DatabaseError, ValueError, TypeError):
            return AuditQueryResult(AuditQueryOutcome.CORRUPT)
        return AuditQueryResult(AuditQueryOutcome.OK, events)
