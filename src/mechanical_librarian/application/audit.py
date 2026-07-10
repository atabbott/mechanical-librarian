"""Application-owned append-only audit contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AuditEvent:
    id: str
    kind: str
    created_at: str
    summary: str

    def __post_init__(self) -> None:
        if not self.id or not self.kind or not self.created_at or not self.summary:
            raise ValueError("audit events require non-empty safe fields")
        if len(self.summary) > 512 or "\n" in self.summary:
            raise ValueError("audit summaries must be single-line and at most 512 characters")


class AuditRepository(Protocol):
    def append(self, event: AuditEvent) -> bool: ...
    def recent(self, limit: int) -> tuple[AuditEvent, ...]: ...
