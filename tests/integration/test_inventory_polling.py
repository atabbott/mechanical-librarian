from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from mechanical_librarian.adapters.persistence.inventory_polling import (
    SQLiteInventoryPollingRepository,
)
from mechanical_librarian.application.ports import (
    FailureKind,
    PortFailure,
    RetryAdvice,
    SafeSummary,
)
from mechanical_librarian.application.inventory_polling import (
    InventoryPollEvent,
    InventoryPollStage,
    InventoryPollOutcome,
    RunInventoryPoll,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryLibraryResult,
    InventoryOutcome,
    InventoryRunResult,
)
from mechanical_librarian.domain.identifiers import AdapterId, LibraryId
from mechanical_librarian.domain.inventory_polling import (
    PollPolicy,
    PollRequest,
    PollState,
)
from mechanical_librarian.domain.inventory_v2 import ScanUsage
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate
from mechanical_librarian.infrastructure.observability.inventory_polling import (
    LoggingInventoryPollTelemetry,
)


NOW = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)


class InventoryPollingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.connection = connect(Path(self.directory.name) / "state.sqlite")
        self.addCleanup(self.connection.close)
        migrate(self.connection)
        self.repository = SQLiteInventoryPollingRepository(
            self.connection, now=lambda: NOW
        )

    def request(
        self,
        *,
        poll_id: str = "poll_a",
        trigger_id: str = "trigger_a",
        governing: str = "b" * 64,
        max_attempts: int = 3,
        max_pages: int = 30,
    ) -> PollRequest:
        return PollRequest(
            poll_id,
            trigger_id,
            "a" * 64,
            governing,
            PollPolicy(max_attempts, 300, 30, 5, 20, max_pages, 300, 30_000),
        )

    def failure(
        self,
        kind: FailureKind,
        *,
        retryable: bool,
        retry_after: timedelta | None = None,
    ) -> PortFailure:
        return PortFailure(
            kind,
            RetryAdvice(retryable, retry_after),
            SafeSummary("inventory poll test failure"),
        )

    def test_schedule_is_idempotent_and_persists_policy_before_claim(self) -> None:
        first = self.repository.schedule(self.request(), NOW)
        duplicate = self.repository.schedule(
            self.request(poll_id="poll_b"), NOW + timedelta(seconds=1)
        )

        self.assertTrue(first.accepted)
        self.assertTrue(duplicate.accepted)
        self.assertEqual(duplicate.value.poll_id, "poll_a")
        row = self.connection.execute(
            "SELECT state,attempt,max_attempts,max_pages FROM inventory_polls"
        ).fetchone()
        self.assertEqual(row, ("pending", 0, 3, 30))

    def test_retry_wait_survives_restart_and_does_not_reset_usage(self) -> None:
        self.repository.schedule(self.request(), NOW)
        claim = self.repository.claim("poll_a", "worker_a", NOW).value
        failed = self.repository.fail(
            claim,
            self.failure(FailureKind.UNAVAILABLE, retryable=True),
            ScanUsage(3, 7, 900),
            NOW + timedelta(seconds=1),
        )

        self.assertEqual(failed.value.state, PollState.RETRY_WAIT)
        self.assertEqual(failed.value.usage, ScanUsage(3, 7, 900))
        reopened = SQLiteInventoryPollingRepository(
            self.connection, now=lambda: NOW
        )
        self.assertFalse(
            reopened.claim("poll_a", "worker_b", NOW + timedelta(seconds=5)).accepted
        )
        second = reopened.claim("poll_a", "worker_b", NOW + timedelta(seconds=6))
        self.assertTrue(second.accepted)
        self.assertEqual(second.value.attempt, 2)
        self.assertGreater(second.value.fence, claim.fence)

    def test_retry_after_is_bounded_by_persisted_elapsed_window(self) -> None:
        self.repository.schedule(self.request(max_attempts=2), NOW)
        claim = self.repository.claim("poll_a", "worker_a", NOW).value
        result = self.repository.fail(
            claim,
            self.failure(
                FailureKind.RATE_LIMITED,
                retryable=True,
                retry_after=timedelta(days=1),
            ),
            ScanUsage(),
            NOW + timedelta(seconds=1),
        )
        self.assertEqual(result.value.state, PollState.FAILED_TERMINAL)
        self.assertIsNone(result.value.next_eligible_at)

    def test_non_retryable_failure_is_suppressed_until_governing_change(self) -> None:
        self.repository.schedule(self.request(), NOW)
        claim = self.repository.claim("poll_a", "worker_a", NOW).value
        blocked = self.repository.fail(
            claim,
            self.failure(FailureKind.AUTHENTICATION, retryable=False),
            ScanUsage(),
            NOW + timedelta(seconds=1),
            compatibility="supported",
        )
        self.assertEqual(blocked.value.state, PollState.BLOCKED)

        suppressed = self.repository.schedule(
            self.request(poll_id="poll_b", trigger_id="trigger_b"),
            NOW + timedelta(seconds=2),
        )
        changed = self.repository.schedule(
            self.request(
                poll_id="poll_c", trigger_id="trigger_c", governing="c" * 64
            ),
            NOW + timedelta(seconds=2),
        )
        self.assertEqual(suppressed.value.poll_id, "poll_a")
        self.assertEqual(suppressed.value.usage, ScanUsage())
        self.assertEqual(suppressed.value.compatibility, "supported")
        self.assertEqual(changed.value.poll_id, "poll_c")
        self.assertEqual(changed.value.state, PollState.PENDING)

    def test_expired_claim_is_fenced_and_reclaimed_once(self) -> None:
        self.repository.schedule(self.request(), NOW)
        first = self.repository.claim("poll_a", "worker_a", NOW).value
        second = self.repository.claim(
            "poll_a", "worker_b", NOW + timedelta(seconds=31)
        ).value

        stale = self.repository.complete(
            first, ScanUsage(1, 1, 10), NOW + timedelta(seconds=31)
        )
        current = self.repository.complete(
            second, ScanUsage(1, 1, 10), NOW + timedelta(seconds=32)
        )
        self.assertFalse(stale.accepted)
        self.assertTrue(current.accepted)
        self.assertEqual(current.value.state, PollState.SUCCEEDED)
        attempts = self.connection.execute(
            "SELECT attempt,state FROM inventory_poll_attempts ORDER BY attempt"
        ).fetchall()
        self.assertEqual(attempts, [(1, "lease_expired"), (2, "succeeded")])

    def test_cancellation_never_becomes_success(self) -> None:
        self.repository.schedule(self.request(), NOW)
        claim = self.repository.claim("poll_a", "worker_a", NOW).value
        cancelled = self.repository.cancel("poll_a", NOW + timedelta(seconds=1))
        completed = self.repository.complete(
            claim, ScanUsage(), NOW + timedelta(seconds=2)
        )
        self.assertTrue(cancelled.value.cancellation_requested)
        self.assertFalse(completed.accepted)

    def test_repository_clock_rejects_completion_after_real_lease_expiry(self) -> None:
        clock = [NOW]
        repository = SQLiteInventoryPollingRepository(
            self.connection, now=lambda: clock[0]
        )
        repository.schedule(self.request(), NOW)
        claim = repository.claim("poll_a", "worker_a", NOW).value

        clock[0] = NOW + timedelta(seconds=31)
        completed = repository.complete(
            claim, ScanUsage(1, 1, 10), NOW + timedelta(seconds=1)
        )

        self.assertFalse(completed.accepted)

    def test_cumulative_resource_exhaustion_is_terminal(self) -> None:
        self.repository.schedule(self.request(max_pages=4), NOW)
        claim = self.repository.claim("poll_a", "worker_a", NOW).value
        first = self.repository.fail(
            claim,
            self.failure(FailureKind.UNAVAILABLE, retryable=True),
            ScanUsage(3, 1, 10),
            NOW + timedelta(seconds=1),
        )
        self.assertEqual(first.value.state, PollState.RETRY_WAIT)
        claim = self.repository.claim(
            "poll_a", "worker_b", NOW + timedelta(seconds=6)
        ).value
        exhausted = self.repository.fail(
            claim,
            self.failure(FailureKind.UNAVAILABLE, retryable=True),
            ScanUsage(2, 1, 10),
            NOW + timedelta(seconds=7),
        )
        self.assertEqual(exhausted.value.state, PollState.FAILED_TERMINAL)
        self.assertEqual(exhausted.value.failure_kind, "resource_exhausted")
        self.assertEqual(exhausted.value.usage.pages, 4)

    def test_application_persists_before_io_and_duplicate_success_is_noop(self) -> None:
        calls = []

        def operation(claim):
            row = self.connection.execute(
                "SELECT state,attempt,fence FROM inventory_polls WHERE poll_id=?",
                (claim.poll_id,),
            ).fetchone()
            self.assertEqual(row, ("running", 1, claim.fence))
            calls.append(claim)
            return InventoryRunResult(InventoryOutcome.READY, ())

        service = RunInventoryPoll(self.repository)
        first = service.run(
            self.request(), "worker_a", NOW, operation, lambda: NOW + timedelta(seconds=1)
        )
        duplicate = service.run(
            self.request(poll_id="poll_b"),
            "worker_b",
            NOW + timedelta(seconds=2),
            lambda _claim: self.fail("duplicate delivery performed target I/O"),
            lambda: NOW + timedelta(seconds=3),
        )

        self.assertEqual(first.outcome, InventoryPollOutcome.SUCCEEDED)
        self.assertEqual(duplicate.outcome, InventoryPollOutcome.SUCCEEDED)
        self.assertEqual(len(calls), 1)

    def test_application_emits_only_bounded_poll_telemetry(self) -> None:
        events = []

        class Telemetry:
            def record(self, event):
                events.append(event)

        result = RunInventoryPoll(
            self.repository,
            telemetry=Telemetry(),
            adapter=AdapterId("fixture"),
        ).run(
            self.request(),
            "worker_a",
            NOW,
            lambda _claim: InventoryRunResult(
                InventoryOutcome.READY, (), "supported"
            ),
            lambda: NOW + timedelta(seconds=1),
        )

        self.assertEqual(result.outcome, InventoryPollOutcome.SUCCEEDED)
        self.assertEqual(
            [event.stage for event in events],
            [
                InventoryPollStage.SCHEDULED,
                InventoryPollStage.CLAIMED,
                InventoryPollStage.SUCCEEDED,
            ],
        )
        self.assertEqual(events[-1].compatibility, "supported")
        self.assertNotIn("private", repr(events))

    def test_logging_telemetry_records_fixed_fields_and_resource_counters(self) -> None:
        logger = Mock()
        telemetry = LoggingInventoryPollTelemetry(logger)
        telemetry.record(
            InventoryPollEvent(
                AdapterId("fixture"),
                "poll_fixture",
                InventoryPollStage.DEFERRED,
                2,
                ScanUsage(3, 4, 500),
                FailureKind.RATE_LIMITED,
                "supported",
            )
        )

        counters = telemetry.counters()
        self.assertEqual(counters[("fixture", "deferred", "rate_limited")], 1)
        self.assertEqual(counters[("fixture", "pages", "deferred")], 3)
        self.assertEqual(counters[("fixture", "items", "deferred")], 4)
        self.assertEqual(counters[("fixture", "bytes", "deferred")], 500)
        fields = logger.info.call_args.kwargs["extra"]["inventory_poll"]
        self.assertEqual(fields["failure_kind"], "rate_limited")
        self.assertNotIn("title", fields)
        self.assertNotIn("path", fields)

        telemetry.record(
            InventoryPollEvent(
                AdapterId("fixture"),
                "poll_fixture",
                InventoryPollStage.FAILED,
                3,
                failure="lease_expired",
            )
        )
        self.assertEqual(counters[("fixture", "deferred", "rate_limited")], 1)
        self.assertEqual(
            telemetry.counters()[("fixture", "failed", "lease_expired")], 1
        )

    def test_application_preserves_retry_after_from_inventory_failure(self) -> None:
        failure = self.failure(
            FailureKind.RATE_LIMITED,
            retryable=True,
            retry_after=timedelta(seconds=17),
        )
        inventory = InventoryRunResult(
            InventoryOutcome.INCOMPLETE,
            (
                InventoryLibraryResult(
                    LibraryId("01234567-89ab-4def-8123-456789abcdef"),
                    InventoryOutcome.INCOMPLETE,
                    None,
                    ScanUsage(1, 2, 300),
                    failure,
                ),
            ),
            "supported",
        )

        result = RunInventoryPoll(self.repository).run(
            self.request(),
            "worker_a",
            NOW,
            lambda _claim: inventory,
            lambda: NOW + timedelta(seconds=1),
        )

        self.assertEqual(result.outcome, InventoryPollOutcome.DEFERRED)
        self.assertEqual(
            result.inspection.next_eligible_at,
            NOW + timedelta(seconds=18),
        )
        self.assertEqual(result.inspection.usage, ScanUsage(1, 2, 300))
        self.assertEqual(result.inspection.compatibility, "supported")

    def test_application_uses_longest_retry_guidance_across_libraries(self) -> None:
        first = LibraryId("01234567-89ab-4def-8123-456789abcdef")
        second = LibraryId("11234567-89ab-4def-8123-456789abcdef")
        inventory = InventoryRunResult(
            InventoryOutcome.INCOMPLETE,
            (
                InventoryLibraryResult(
                    first,
                    InventoryOutcome.INCOMPLETE,
                    None,
                    failure=self.failure(
                        FailureKind.TIMEOUT,
                        retryable=True,
                        retry_after=timedelta(seconds=5),
                    ),
                ),
                InventoryLibraryResult(
                    second,
                    InventoryOutcome.INCOMPLETE,
                    None,
                    failure=self.failure(
                        FailureKind.RATE_LIMITED,
                        retryable=True,
                        retry_after=timedelta(seconds=17),
                    ),
                ),
            ),
            "supported",
        )

        result = RunInventoryPoll(self.repository).run(
            self.request(),
            "worker_a",
            NOW,
            lambda _claim: inventory,
            lambda: NOW + timedelta(seconds=1),
        )

        self.assertEqual(result.outcome, InventoryPollOutcome.DEFERRED)
        self.assertEqual(
            result.inspection.next_eligible_at,
            NOW + timedelta(seconds=18),
        )

    def test_invalid_inventory_response_records_contract_drift(self) -> None:
        failure = self.failure(FailureKind.INVALID_RESPONSE, retryable=False)
        inventory = InventoryRunResult(
            InventoryOutcome.INCOMPLETE,
            (
                InventoryLibraryResult(
                    LibraryId("01234567-89ab-4def-8123-456789abcdef"),
                    InventoryOutcome.INCOMPLETE,
                    None,
                    failure=failure,
                ),
            ),
            "supported",
        )

        result = RunInventoryPoll(self.repository).run(
            self.request(),
            "worker_a",
            NOW,
            lambda _claim: inventory,
            lambda: NOW + timedelta(seconds=1),
        )

        self.assertEqual(result.outcome, InventoryPollOutcome.BLOCKED)
        self.assertEqual(result.inspection.compatibility, "contract_drift")

    def test_unsupported_inventory_library_blocks_until_governing_change(
        self,
    ) -> None:
        inventory = InventoryRunResult(
            InventoryOutcome.INCOMPLETE,
            (
                InventoryLibraryResult(
                    LibraryId("01234567-89ab-4def-8123-456789abcdef"),
                    InventoryOutcome.INCOMPLETE,
                    None,
                    failure=self.failure(
                        FailureKind.UNSUPPORTED,
                        retryable=False,
                    ),
                ),
            ),
            "supported",
        )
        service = RunInventoryPoll(self.repository)

        result = service.run(
            self.request(),
            "worker_a",
            NOW,
            lambda _claim: inventory,
            lambda: NOW + timedelta(seconds=1),
        )
        duplicate = service.run(
            self.request(poll_id="poll_b", trigger_id="trigger_b"),
            "worker_b",
            NOW + timedelta(seconds=2),
            lambda _claim: self.fail("duplicate delivery performed target I/O"),
            lambda: NOW + timedelta(seconds=3),
        )

        self.assertEqual(result.outcome, InventoryPollOutcome.BLOCKED)
        self.assertEqual(result.inspection.failure_kind, "unsupported")
        self.assertIsNone(result.inspection.next_eligible_at)
        self.assertEqual(duplicate.outcome, InventoryPollOutcome.BLOCKED)
        self.assertEqual(duplicate.inspection.poll_id, "poll_a")


if __name__ == "__main__":
    unittest.main()
