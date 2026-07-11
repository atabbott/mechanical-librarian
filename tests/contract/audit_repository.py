"""Reusable behavioral contract for audit repository adapters."""

from __future__ import annotations

from datetime import datetime

from mechanical_librarian.application.audit import (
    AuditAppendOutcome,
    AuditEvent,
    AuditEventKind,
    AuditQueryOutcome,
    AuditRepository,
    AuditReadOutcome,
    SafeAuditSummary,
)
from mechanical_librarian.domain.identifiers import AuditEventId


def contract_event(
    identifier: str,
    timestamp: str = "2026-07-10T10:00:00Z",
    summary: str = "discovered item",
) -> AuditEvent:
    return AuditEvent(
        AuditEventId(identifier),
        AuditEventKind("discovery"),
        datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
        SafeAuditSummary(summary),
    )


class AuditRepositoryContract:
    """Mixin every concrete repository test case can run unchanged."""

    repository: AuditRepository

    def test_contract_append_replay_conflict_and_recent_order(self) -> None:
        first = contract_event("contract-first")
        second = contract_event("contract-second", "2026-07-11T10:00:00Z")
        self.assertEqual(self.repository.append(first).outcome, AuditAppendOutcome.APPENDED)  # type: ignore[attr-defined]
        self.assertEqual(self.repository.append(first).outcome, AuditAppendOutcome.DUPLICATE)  # type: ignore[attr-defined]
        conflict = contract_event("contract-first", summary="item changed")
        self.assertEqual(self.repository.append(conflict).outcome, AuditAppendOutcome.CONFLICT)  # type: ignore[attr-defined]
        self.assertEqual(self.repository.append(second).outcome, AuditAppendOutcome.APPENDED)  # type: ignore[attr-defined]
        result = self.repository.recent(1)
        self.assertEqual(result.outcome, AuditQueryOutcome.OK)  # type: ignore[attr-defined]
        self.assertEqual(result.events, (second,))  # type: ignore[attr-defined]

    def test_contract_exact_read_found_not_found_and_invalid(self) -> None:
        expected = contract_event("contract-exact")
        self.assertEqual(
            self.repository.read(expected.id).outcome, AuditReadOutcome.NOT_FOUND
        )  # type: ignore[attr-defined]
        self.assertEqual(
            self.repository.append(expected).outcome, AuditAppendOutcome.APPENDED
        )  # type: ignore[attr-defined]
        found = self.repository.read(expected.id)
        self.assertEqual(found.outcome, AuditReadOutcome.FOUND)  # type: ignore[attr-defined]
        self.assertEqual(found.event, expected)  # type: ignore[attr-defined]
        for invalid in ("contract-exact", object(), None):
            with self.subTest(invalid=invalid):  # type: ignore[attr-defined]
                result = self.repository.read(invalid)  # type: ignore[arg-type]
                self.assertEqual(result.outcome, AuditReadOutcome.INVALID)  # type: ignore[attr-defined]

    def test_contract_rejects_invalid_limits_without_querying(self) -> None:
        for invalid in (0, 101, True, 1.0, "1", None):
            with self.subTest(invalid=invalid):  # type: ignore[attr-defined]
                result = self.repository.recent(invalid)  # type: ignore[arg-type]
                self.assertEqual(result.outcome, AuditQueryOutcome.INVALID)  # type: ignore[attr-defined]
                self.assertEqual(result.events, ())  # type: ignore[attr-defined]
