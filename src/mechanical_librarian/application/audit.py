"""Typed, safe append-only audit contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from mechanical_librarian.domain.identifiers import AuditEventId, Identifier


_FORBIDDEN_SUMMARY = re.compile(r"(?:https?://|/[^ ]|\\|\b(?:token|secret|password|authorization)\b)", re.IGNORECASE)


class AuditEventKind(Identifier):
    """Bounded logical event category; it cannot carry a provider payload."""

    kind = "audit event kind"


@dataclass(frozen=True, repr=False)
class SafeAuditSummary:
    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 512 or self.value.strip() != self.value:
            raise ValueError("audit summaries must be trimmed and at most 512 characters")
        if any(ord(character) < 32 or ord(character) == 127 for character in self.value):
            raise ValueError("audit summaries cannot contain control characters")
        if self.value.startswith(("{", "[")) or _FORBIDDEN_SUMMARY.search(self.value):
            raise ValueError("audit summaries cannot contain private paths, endpoints, credentials, or payloads")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "SafeAuditSummary(<redacted>)"


@dataclass(frozen=True)
class AuditEvent:
    id: AuditEventId
    kind: AuditEventKind
    created_at: datetime
    summary: SafeAuditSummary

    def __post_init__(self) -> None:
        if not isinstance(self.id, AuditEventId) or not isinstance(self.kind, AuditEventKind) or not isinstance(self.summary, SafeAuditSummary):
            raise ValueError("audit events require typed safe identifiers, kinds, and summaries")
        if not isinstance(self.created_at, datetime):
            raise ValueError("audit events require a typed timestamp")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("audit timestamps must be timezone-aware")

    @property
    def storage_row(self) -> tuple[str, str, str, str]:
        timestamp = self.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return str(self.id), str(self.kind), timestamp, str(self.summary)

    @classmethod
    def from_storage(cls, event_id: object, kind: object, created_at: object, summary: object) -> "AuditEvent":
        if not all(isinstance(value, str) for value in (event_id, kind, created_at, summary)):
            raise ValueError("stored audit event has invalid value types")
        timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return cls(AuditEventId(event_id), AuditEventKind(kind), timestamp, SafeAuditSummary(summary))


class AuditAppendOutcome(StrEnum):
    APPENDED = "appended"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    INTEGRITY_FAILURE = "integrity_failure"


class AuditQueryOutcome(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    CORRUPT = "corrupt"


@dataclass(frozen=True)
class AuditAppendResult:
    outcome: AuditAppendOutcome

    @property
    def accepted(self) -> bool:
        return self.outcome in {AuditAppendOutcome.APPENDED, AuditAppendOutcome.DUPLICATE}


@dataclass(frozen=True)
class AuditQueryResult:
    outcome: AuditQueryOutcome
    events: tuple[AuditEvent, ...] = ()


class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> AuditAppendResult: ...
    def recent(self, limit: int) -> AuditQueryResult: ...
