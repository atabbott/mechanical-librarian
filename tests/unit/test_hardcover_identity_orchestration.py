from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mechanical_librarian.adapters.persistence.hardcover_identity_work import (
    SQLiteHardcoverIdentityWorkRepository,
)
from mechanical_librarian.application.hardcover_identity import (
    HardcoverIdentityOutcome,
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    IdentityReportError,
    ManageHardcoverIdentityWork,
    RunHardcoverIdentityReport,
)
from mechanical_librarian.application.hardcover_identity_orchestration import (
    RunHardcoverIdentityLifecycle,
)
from mechanical_librarian.application.hardcover_safe_executor import (
    HardcoverIdentityExecutionRequest,
    RunSafeHardcoverIdentity,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    OperationContext,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.domain.hardcover_identity import (
    ProviderBookObservation,
    ProviderIdentityObservation,
    validate_private_query,
)
from mechanical_librarian.infrastructure.hardcover_identity_gate import (
    DescriptorHardcoverIdentityGate,
)
from mechanical_librarian.infrastructure.persistence.sqlite import migrate


ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Clock:
    def __init__(self) -> None:
        self.now = ORIGIN


class _Wait:
    def __init__(
        self,
        clock: _Clock,
        *,
        cancellation: threading.Event | None = None,
        advance: bool = True,
        regression: bool = False,
    ) -> None:
        self.clock = clock
        self.cancellation = cancellation
        self.advance = advance
        self.regression = regression
        self.timeouts: list[float] = []

    def wait(self, timeout: float) -> bool:
        self.timeouts.append(timeout)
        if self.regression:
            self.clock.now -= timedelta(seconds=1)
        elif self.advance:
            self.clock.now += timedelta(seconds=timeout)
        if self.cancellation is not None:
            self.cancellation.set()
        return self.cancellation is not None


class _SequenceProvider:
    def __init__(self, *results: IdentityAttemptResult) -> None:
        self.results = list(results)
        self.starts = 0

    def lookup(self, query: object, context: OperationContext) -> IdentityAttemptResult:
        self.starts += 1
        return self.results.pop(0)


class HardcoverIdentityOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "state.sqlite"
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        migrate(self.connection)
        self.work = ManageHardcoverIdentityWork(
            SQLiteHardcoverIdentityWorkRepository(self.connection)
        )
        self.clock = _Clock()
        self.cancelled = threading.Event()
        self.gate = DescriptorHardcoverIdentityGate(self.database)
        self.query = validate_private_query(
            {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
        )

    def context(self, *, deadline: datetime | None = None) -> OperationContext:
        return OperationContext(
            Deadline(deadline or ORIGIN + timedelta(seconds=29)),
            CancellationProbe(
                CancellationId("hardcover-lifecycle-test"), self.cancelled.is_set
            ),
            ResourceBounds(4, 65_536),
            TimeProbe(lambda: self.clock.now),
        )

    def request(
        self, label: str, *, egress_limit: int = 3
    ) -> HardcoverIdentityExecutionRequest:
        return HardcoverIdentityExecutionRequest(
            hashlib.sha256(label.encode("ascii")).hexdigest(),
            "synthetic-worker",
            self.query,
            egress_limit,
        )

    def matched(self) -> IdentityAttemptResult:
        return IdentityAttemptResult.success(
            (
                ProviderIdentityObservation(
                    id=1001,
                    book_id=2001,
                    canonical_id=None,
                    isbn_10="0000000019",
                    isbn_10_valid=True,
                    isbn_13="9780000000019",
                    isbn_13_valid=True,
                    isbns_match=True,
                    reading_format_id=4,
                    state="normalized",
                    compilation=False,
                    book=ProviderBookObservation(
                        id=2001,
                        canonical_id=None,
                        book_status_id=1,
                        state="processed",
                        title="Synthetic Ebook Alpha",
                        parent_book_id=None,
                        compilation=False,
                        is_partial_book=False,
                    ),
                ),
            )
        )

    def lifecycle(
        self, provider: object, retry_wait: object
    ) -> RunHardcoverIdentityLifecycle:
        attempt = RunSafeHardcoverIdentity(self.work, provider, self.gate)
        return RunHardcoverIdentityLifecycle(
            self.work, attempt, self.gate, retry_wait
        )

    def unavailable(self) -> IdentityAttemptResult:
        return IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE)

    def test_one_invocation_waits_exact_backoff_and_matches_on_third_start(
        self,
    ) -> None:
        provider = _SequenceProvider(
            self.unavailable(), self.unavailable(), self.matched()
        )
        retry_wait = _Wait(self.clock)

        report = self.lifecycle(provider, retry_wait).run(
            self.request("three-attempt-match"), self.context()
        )

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(provider.starts, 3)
        self.assertEqual(retry_wait.timeouts, [1.0, 2.0])

    def test_three_retryable_failures_exhaust_without_fourth_start(self) -> None:
        provider = _SequenceProvider(
            self.unavailable(), self.unavailable(), self.unavailable()
        )
        retry_wait = _Wait(self.clock)

        report = self.lifecycle(provider, retry_wait).run(
            self.request("three-attempt-exhausted"), self.context()
        )

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        self.assertEqual(provider.starts, 3)
        self.assertEqual(retry_wait.timeouts, [1.0, 2.0])

    def test_one_shot_never_waits_or_starts_twice(self) -> None:
        provider = _SequenceProvider(self.unavailable(), self.matched())
        retry_wait = _Wait(self.clock)
        request = self.request("one-shot", egress_limit=1)
        lifecycle = self.lifecycle(provider, retry_wait)

        first = lifecycle.run(request, self.context())
        second = lifecycle.run(request, self.context())

        self.assertEqual(first.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        self.assertEqual(second.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        self.assertEqual(provider.starts, 1)
        self.assertEqual(retry_wait.timeouts, [])

    def test_restart_before_due_returns_without_waiting_or_sending(self) -> None:
        provider = _SequenceProvider(self.unavailable(), self.matched())
        request = self.request("restart-before-due")
        first = RunSafeHardcoverIdentity(self.work, provider, self.gate).run(
            request, self.context()
        )
        retry_wait = _Wait(self.clock)

        restarted = self.lifecycle(provider, retry_wait).run(request, self.context())

        self.assertEqual(first.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(restarted.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(provider.starts, 1)
        self.assertEqual(retry_wait.timeouts, [])

    def test_cancellation_during_wait_cancels_available_retry(self) -> None:
        provider = _SequenceProvider(self.unavailable())
        retry_wait = _Wait(self.clock, cancellation=self.cancelled)
        request = self.request("cancel-during-wait")

        report = self.lifecycle(provider, retry_wait).run(request, self.context())

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.CANCELLED)
        self.assertEqual(provider.starts, 1)
        self.assertEqual(retry_wait.timeouts, [1.0])
        state = self.connection.execute(
            "SELECT state FROM hardcover_identity_generations WHERE work_key=?",
            (request.work_key,),
        ).fetchone()
        self.assertEqual(state, ("cancelled",))

    def test_deadline_and_wall_regression_stop_after_first_attempt(self) -> None:
        for label, retry_wait, deadline, expected in (
            (
                "deadline-during-wait",
                _Wait(self.clock),
                ORIGIN + timedelta(milliseconds=500),
                HardcoverIdentityOutcome.RETRY_EXHAUSTED,
            ),
            (
                "wall-regression",
                _Wait(self.clock, regression=True),
                ORIGIN + timedelta(seconds=29),
                HardcoverIdentityOutcome.PERSISTENCE_FAILURE,
            ),
        ):
            with self.subTest(label=label):
                self.clock.now = ORIGIN
                provider = _SequenceProvider(self.unavailable())
                report = self.lifecycle(provider, retry_wait).run(
                    self.request(label), self.context(deadline=deadline)
                )
                self.assertEqual(report.outcome, expected)
                self.assertEqual(provider.starts, 1)

    def test_expired_started_claim_is_recovered_then_retried(self) -> None:
        request = self.request("expired-claim")
        accepted = self.work.accept(request.work_key, self.clock.now)
        claim = self.work.claim(
            accepted.reference, request.owner, self.clock.now, gate_owned=True
        )
        started = self.work.start(
            claim, request.owner, self.clock.now, gate_owned=True
        )
        self.clock.now = started.lease_expires_at + timedelta(microseconds=1)
        provider = _SequenceProvider(self.matched())

        report = self.lifecycle(provider, _Wait(self.clock)).run(
            request, self.context()
        )

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(provider.starts, 1)
        attempts = self.connection.execute(
            "SELECT attempt,outcome FROM hardcover_identity_attempts "
            "WHERE work_key=? ORDER BY attempt",
            (request.work_key,),
        ).fetchall()
        self.assertEqual(attempts, [(1, "interrupted"), (2, "matched")])

    def test_expired_claim_gate_contention_returns_without_recovery(self) -> None:
        request = self.request("recovery-contention")
        accepted = self.work.accept(request.work_key, self.clock.now)
        claim = self.work.claim(
            accepted.reference, request.owner, self.clock.now, gate_owned=True
        )
        started = self.work.start(
            claim, request.owner, self.clock.now, gate_owned=True
        )
        self.clock.now = started.lease_expires_at + timedelta(microseconds=1)
        holder = self.gate.try_acquire()
        self.addCleanup(holder.release)
        provider = _SequenceProvider(self.matched())

        report = self.lifecycle(provider, _Wait(self.clock)).run(
            request, self.context()
        )

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(provider.starts, 0)
        state = self.connection.execute(
            "SELECT state FROM hardcover_identity_generations WHERE work_key=?",
            (request.work_key,),
        ).fetchone()
        self.assertEqual(state, ("claimed",))

    def test_report_boundary_accepts_both_executors_and_rejects_bad_result(
        self,
    ) -> None:
        one_provider = _SequenceProvider(self.matched())
        one_attempt = RunSafeHardcoverIdentity(self.work, one_provider, self.gate)
        one = RunHardcoverIdentityReport(one_attempt).run(
            self.request("shared-one-attempt"), self.context()
        )
        lifecycle_provider = _SequenceProvider(self.matched())
        lifecycle = self.lifecycle(lifecycle_provider, _Wait(self.clock))
        whole = RunHardcoverIdentityReport(lifecycle).run(
            self.request("shared-lifecycle"), self.context()
        )

        class MalformedExecutor:
            def run(self, request: object, context: object) -> object:
                return object()

        with self.assertRaisesRegex(
            IdentityReportError, "execution result is malformed"
        ):
            RunHardcoverIdentityReport(MalformedExecutor()).run(
                self.request("shared-malformed"), self.context()
            )
        self.assertEqual(one.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(whole.outcome, HardcoverIdentityOutcome.MATCHED)


if __name__ == "__main__":
    unittest.main()
