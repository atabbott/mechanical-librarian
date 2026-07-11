"""Application-owned diagnostic for durable state and audit readback."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from mechanical_librarian.application.audit import (
    AuditAppendOutcome,
    AuditEvent,
    AuditEventKind,
    AuditReadOutcome,
    AuditRepository,
    SafeAuditSummary,
)
from mechanical_librarian.domain.identifiers import AuditEventId


_STATE_CHECK_EVENT = AuditEvent(
    id=AuditEventId("state-check-v1"),
    kind=AuditEventKind("state-check"),
    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    summary=SafeAuditSummary("verification succeeded"),
)
class StateCheckOutcome(StrEnum):
    """Closed, value-free outcomes safe for an executable interface."""

    READY = "ready"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"
    INTEGRITY_FAILURE = "integrity_failure"
    CORRUPT = "corrupt"
    READBACK_MISMATCH = "readback_mismatch"
    SETUP_FAILURE = "setup_failure"
    INTERNAL_FAILURE = "internal_failure"


@dataclass(frozen=True)
class StateCheckResult:
    outcome: StateCheckOutcome

    def __post_init__(self) -> None:
        if type(self.outcome) is not StateCheckOutcome:
            raise ValueError("state check results require a typed outcome")

    @property
    def succeeded(self) -> bool:
        return self.outcome is StateCheckOutcome.READY


class CheckDurableState:
    """Record and read back one stable, bounded, code-owned audit event."""

    def __init__(self, audit_repository: AuditRepository) -> None:
        self._audit_repository = audit_repository

    def run(self) -> StateCheckResult:
        appended = self._audit_repository.append(_STATE_CHECK_EVENT)
        if appended.outcome not in {
            AuditAppendOutcome.APPENDED,
            AuditAppendOutcome.DUPLICATE,
        }:
            return StateCheckResult(_append_failure(appended.outcome))

        readback = self._audit_repository.read(_STATE_CHECK_EVENT.id)
        if readback.outcome is not AuditReadOutcome.FOUND:
            return StateCheckResult(_read_failure(readback.outcome))
        if type(readback.event) is not AuditEvent or readback.event != _STATE_CHECK_EVENT:
            return StateCheckResult(StateCheckOutcome.READBACK_MISMATCH)
        return StateCheckResult(StateCheckOutcome.READY)


def _append_failure(outcome: AuditAppendOutcome) -> StateCheckOutcome:
    return {
        AuditAppendOutcome.CONFLICT: StateCheckOutcome.CORRUPT,
        AuditAppendOutcome.INVALID: StateCheckOutcome.INVALID,
        AuditAppendOutcome.UNAVAILABLE: StateCheckOutcome.UNAVAILABLE,
        AuditAppendOutcome.INTEGRITY_FAILURE: StateCheckOutcome.INTEGRITY_FAILURE,
    }.get(outcome, StateCheckOutcome.INTERNAL_FAILURE)


def _read_failure(outcome: AuditReadOutcome) -> StateCheckOutcome:
    return {
        AuditReadOutcome.NOT_FOUND: StateCheckOutcome.READBACK_MISMATCH,
        AuditReadOutcome.INVALID: StateCheckOutcome.INVALID,
        AuditReadOutcome.UNAVAILABLE: StateCheckOutcome.UNAVAILABLE,
        AuditReadOutcome.CORRUPT: StateCheckOutcome.CORRUPT,
    }.get(outcome, StateCheckOutcome.INTERNAL_FAILURE)


__all__ = ["CheckDurableState", "StateCheckOutcome", "StateCheckResult"]
