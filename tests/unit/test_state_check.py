from __future__ import annotations

import unittest
from dataclasses import dataclass

from mechanical_librarian.application.audit import (
    AuditAppendOutcome,
    AuditAppendResult,
    AuditEvent,
    AuditReadOutcome,
    AuditReadResult,
)
from mechanical_librarian.application.state_check import (
    CheckDurableState,
    StateCheckOutcome,
)


@dataclass
class StubAuditRepository:
    append_outcome: AuditAppendOutcome = AuditAppendOutcome.APPENDED
    read_outcome: AuditReadOutcome = AuditReadOutcome.FOUND
    read_event: AuditEvent | None = None

    def append(self, event: AuditEvent) -> AuditAppendResult:
        self.appended_event = event
        return AuditAppendResult(self.append_outcome)

    def read(self, event_id: object) -> AuditReadResult:
        self.read_event_id = event_id
        return AuditReadResult(
            self.read_outcome,
            self.appended_event if self.read_event is None else self.read_event,
        )

    def recent(self, limit: int) -> object:
        raise AssertionError("state check must use exact-ID readback")


class StateCheckTests(unittest.TestCase):
    def test_appended_and_duplicate_events_are_read_back_by_exact_id(self) -> None:
        for append_outcome in (
            AuditAppendOutcome.APPENDED,
            AuditAppendOutcome.DUPLICATE,
        ):
            with self.subTest(append_outcome=append_outcome):
                repository = StubAuditRepository(append_outcome=append_outcome)
                result = CheckDurableState(repository).run()

                self.assertEqual(result.outcome, StateCheckOutcome.READY)
                self.assertEqual(repository.read_event_id, repository.appended_event.id)
                self.assertEqual(repository.appended_event.id.value, "state-check-v1")
                self.assertEqual(repository.appended_event.kind.value, "state-check")
                self.assertEqual(
                    repository.appended_event.summary.value,
                    "verification succeeded",
                )

    def test_every_append_failure_maps_to_a_safe_state_check_outcome(self) -> None:
        expected = {
            AuditAppendOutcome.CONFLICT: StateCheckOutcome.CORRUPT,
            AuditAppendOutcome.INVALID: StateCheckOutcome.INVALID,
            AuditAppendOutcome.UNAVAILABLE: StateCheckOutcome.UNAVAILABLE,
            AuditAppendOutcome.INTEGRITY_FAILURE: StateCheckOutcome.INTEGRITY_FAILURE,
        }
        for audit_outcome, state_outcome in expected.items():
            with self.subTest(audit_outcome=audit_outcome):
                result = CheckDurableState(
                    StubAuditRepository(append_outcome=audit_outcome)
                ).run()
                self.assertEqual(result.outcome, state_outcome)

    def test_every_read_failure_maps_to_a_safe_state_check_outcome(self) -> None:
        expected = {
            AuditReadOutcome.NOT_FOUND: StateCheckOutcome.READBACK_MISMATCH,
            AuditReadOutcome.INVALID: StateCheckOutcome.INVALID,
            AuditReadOutcome.UNAVAILABLE: StateCheckOutcome.UNAVAILABLE,
            AuditReadOutcome.CORRUPT: StateCheckOutcome.CORRUPT,
        }
        for audit_outcome, state_outcome in expected.items():
            with self.subTest(audit_outcome=audit_outcome):
                result = CheckDurableState(
                    StubAuditRepository(read_outcome=audit_outcome)
                ).run()
                self.assertEqual(result.outcome, state_outcome)

    def test_found_read_with_a_different_event_fails_readback(self) -> None:
        repository = StubAuditRepository()
        repository.read_event = object()  # type: ignore[assignment]

        result = CheckDurableState(repository).run()

        self.assertEqual(result.outcome, StateCheckOutcome.READBACK_MISMATCH)


if __name__ == "__main__":
    unittest.main()
