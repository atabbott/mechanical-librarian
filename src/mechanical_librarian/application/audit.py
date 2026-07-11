"""Typed, safe append-only audit contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol

from mechanical_librarian.domain.identifiers import AuditEventId, Identifier


_SAFE_AUDIT_SUMMARIES = frozenset(
    {
        "apply completed",
        "apply failed",
        "apply started",
        "apply verified",
        "authorization denied",
        "authorization granted",
        "authorization revoked",
        "backup failed",
        "backup verified",
        "decision recorded",
        "discovered item",
        "item changed",
        "item discovered",
        "item skipped",
        "item unresolved",
        "job completed",
        "job failed",
        "job queued",
        "job retrying",
        "job started",
        "plan created",
        "plan validated",
        "review approved",
        "review rejected",
        "review requested",
        "rollback completed",
        "rollback failed",
        "rollback started",
        "verification failed",
        "verification succeeded",
    }
)

class AuditEventKind(Identifier):
    """Bounded logical event category; it cannot carry a provider payload."""

    kind = "audit event kind"


@dataclass(frozen=True, repr=False)
class SafeAuditSummary:
    """One exact phrase from the closed, public-safe audit vocabulary."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise ValueError("audit summaries must be exact text values")
        if self.value not in _SAFE_AUDIT_SUMMARIES:
            raise ValueError("audit summary is not in the public-safe vocabulary")

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
        if (
            type(self.id) is not AuditEventId
            or type(self.kind) is not AuditEventKind
            or type(self.summary) is not SafeAuditSummary
        ):
            raise ValueError("audit events require typed safe identifiers, kinds, and summaries")
        AuditEventId(self.id.value)
        AuditEventKind(self.kind.value)
        SafeAuditSummary(self.summary.value)
        if type(self.created_at) is not datetime:
            raise ValueError("audit events require a typed timestamp")
        try:
            offset = self.created_at.utcoffset()
            self.created_at.astimezone(timezone.utc)
        except Exception:
            raise ValueError("audit timestamps must be representable in UTC") from None
        if self.created_at.tzinfo is None or offset is None:
            raise ValueError("audit timestamps must be timezone-aware")


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
