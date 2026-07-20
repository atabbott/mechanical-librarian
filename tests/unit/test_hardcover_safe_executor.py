from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.persistence.hardcover_identity_work import (
    SQLiteHardcoverIdentityWorkRepository,
)
from mechanical_librarian.application.hardcover_identity import (
    HardcoverIdentityOutcome,
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    ManageHardcoverIdentityWork,
    RunHardcoverIdentityReport,
    SafeExecutorPreviewReport,
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
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityMatchOutcome,
    IdentityWorkObservation,
    IdentityWorkOutcome,
)
from mechanical_librarian.infrastructure.hardcover_identity_gate import (
    LOCK_NAME,
    DescriptorHardcoverIdentityGate,
    HardcoverIdentityGateError,
)
from mechanical_librarian.infrastructure.persistence.sqlite import migrate


ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


class _Clock:
    def __init__(self) -> None:
        self.now = ORIGIN


class _Provider:
    def __init__(self, result: object, clock: _Clock) -> None:
        self.result = result
        self.clock = clock
        self.starts = 0
        self.deadlines: list[datetime] = []

    def prepare(
        self,
        query: object,
        context: OperationContext,
    ) -> _Provider:
        return self

    def exchange(
        self,
        context: OperationContext,
        *,
        begin_transport,
    ) -> object:
        context = begin_transport()
        self.starts += 1
        self.deadlines.append(context.deadline.at)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _LateProvider(_Provider):
    def exchange(self, context: OperationContext, **kwargs) -> object:
        begin_transport = kwargs.pop("begin_transport")
        transport_context = begin_transport()
        self.clock.now = transport_context.deadline.at
        return super().exchange(
            transport_context,
            begin_transport=lambda: transport_context,
            **kwargs,
        )


class _PublishAtProvider(_Provider):
    def __init__(
        self, result: object, clock: _Clock, published_at: datetime
    ) -> None:
        super().__init__(result, clock)
        self.published_at = published_at

    def exchange(self, context: OperationContext, **kwargs) -> object:
        self.clock.now = self.published_at
        return super().exchange(context, **kwargs)


class _FailingGate:
    def try_acquire(self) -> object:
        raise HardcoverIdentityGateError("synthetic gate failure")


class _ReleaseFailureLease:
    def validate_for_send(self) -> None:
        pass

    def release(self) -> None:
        raise HardcoverIdentityGateError("synthetic release failure")


class _ReleaseFailureGate:
    def try_acquire(self) -> _ReleaseFailureLease:
        return _ReleaseFailureLease()


class _ProcessDeath(BaseException):
    pass


class HardcoverSafeExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o700)
        self.database = self.root / "state.sqlite"
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

    def context(self, deadline: datetime | None = None) -> OperationContext:
        return OperationContext(
            Deadline(deadline or self.clock.now + timedelta(minutes=5)),
            CancellationProbe(
                CancellationId("hardcover-safe-test"), self.cancelled.is_set
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
        row = ProviderIdentityObservation(
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
        )
        return IdentityAttemptResult.success((row,))

    def execute(
        self,
        request: HardcoverIdentityExecutionRequest,
        provider: object,
        *,
        context: OperationContext | None = None,
        gate: object | None = None,
    ):
        safe = RunSafeHardcoverIdentity(
            self.work,
            provider,
            self.gate if gate is None else gate,
        )
        return RunHardcoverIdentityReport(safe).run(
            request,
            self.context() if context is None else context,
        )

    def _work_counts(self, work_key: str) -> tuple[object, ...]:
        return self.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM hardcover_identity_generations WHERE work_key=?),"
            "(SELECT COUNT(*) FROM hardcover_identity_claims WHERE work_key=?),"
            "(SELECT COUNT(*) FROM hardcover_identity_attempts WHERE work_key=?),"
            "(SELECT state FROM hardcover_identity_generations WHERE work_key=? "
            "ORDER BY generation DESC LIMIT 1)",
            (work_key, work_key, work_key, work_key),
        ).fetchone()

    def test_one_attempt_matches_and_releases_gate(self) -> None:
        provider = _Provider(self.matched(), self.clock)
        report = self.execute(self.request("matched"), provider)
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(provider.starts, 1)
        contender = self.gate.try_acquire()
        self.assertIsNotNone(contender)
        contender.release()

    def test_held_gate_is_revalidated_at_the_provider_exchange_boundary(self) -> None:
        class Lease:
            def __init__(self) -> None:
                self.validations = 0
                self.released = False

            def validate_for_send(self) -> None:
                self.validations += 1

            def release(self) -> None:
                self.released = True

        lease = Lease()

        class Gate:
            def try_acquire(self):
                return lease

        provider = _Provider(self.matched(), self.clock)
        report = self.execute(self.request("send-revalidation"), provider, gate=Gate())
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(provider.starts, 1)
        self.assertEqual(lease.validations, 3)
        self.assertTrue(lease.released)

    def test_exchange_guard_failure_is_closed_without_retry_or_exchange(self) -> None:
        class Lease:
            def __init__(self) -> None:
                self.validations = 0
                self.released = False

            def validate_for_send(self) -> None:
                self.validations += 1
                if self.validations == 2:
                    raise HardcoverIdentityGateError("synthetic identity loss")

            def release(self) -> None:
                self.released = True

        lease = Lease()

        class Gate:
            def try_acquire(self):
                return lease

        request = self.request("exchange-guard-loss", egress_limit=1)
        provider = _Provider(self.matched(), self.clock)
        report = self.execute(request, provider, gate=Gate())

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        self.assertEqual(provider.starts, 0)
        self.assertEqual(lease.validations, 2)
        self.assertTrue(lease.released)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations "
                "WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("claimed", 1),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT outcome FROM hardcover_identity_attempts WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (None,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_retry_schedules "
                "WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (0,),
        )

        self.clock.now += timedelta(seconds=16)
        reference = self.work.accept(
            request.work_key,
            self.clock.now,
            egress_limit=1,
        ).reference
        active = self.work.inspect(reference, self.clock.now)
        self.assertIsNotNone(active.active_claim)
        recovered = self.work.recover(
            active.active_claim,
            self.clock.now,
            gate_owned=True,
        )
        self.assertEqual(recovered.outcome, IdentityWorkOutcome.RETRY_EXHAUSTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations "
                "WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("failed", 1),
        )

    def test_database_replacement_after_exchange_guard_fails_before_publication(
        self,
    ) -> None:
        request = self.request("post-exchange-database-loss", egress_limit=1)
        saved_database = self.root / "authorized-state.sqlite"
        replacement_database = self.root / "replacement-state.sqlite"
        replacement_connection = sqlite3.connect(replacement_database)
        migrate(replacement_connection)
        replacement_connection.close()

        class ReplacingProvider(_Provider):
            def exchange(
                provider_self,
                context: OperationContext,
                *,
                begin_transport,
            ) -> object:
                begin_transport()
                provider_self.starts += 1
                self.database.rename(saved_database)
                replacement_database.rename(self.database)
                return provider_self.result

        provider = ReplacingProvider(self.matched(), self.clock)
        report = self.execute(request, provider)

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        self.assertEqual(provider.starts, 1)
        original_identity = saved_database.stat()
        replacement_identity = self.database.stat()
        self.assertNotEqual(
            (original_identity.st_dev, original_identity.st_ino),
            (replacement_identity.st_dev, replacement_identity.st_ino),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations "
                "WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("claimed", 1),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_results WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM current_hardcover_identity WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (0,),
        )
        displaced_database = self.root / "rejected-state.sqlite"
        self.database.rename(displaced_database)
        saved_database.rename(self.database)
        visible_replacement = sqlite3.connect(displaced_database)
        try:
            self.assertEqual(
                visible_replacement.execute(
                    "SELECT COUNT(*) FROM hardcover_identity_generations"
                ).fetchone(),
                (0,),
            )
            self.assertEqual(
                visible_replacement.execute(
                    "SELECT COUNT(*) FROM current_hardcover_identity"
                ).fetchone(),
                (0,),
            )
        finally:
            visible_replacement.close()

        self.clock.now += timedelta(seconds=16)
        reference = self.work.accept(
            request.work_key,
            self.clock.now,
            egress_limit=1,
        ).reference
        active = self.work.inspect(reference, self.clock.now)
        self.assertIsNotNone(active.active_claim)
        lease = self.gate.try_acquire()
        self.assertIsNotNone(lease)
        try:
            recovered = self.work.recover(
                active.active_claim,
                self.clock.now,
                gate_owned=True,
            )
        finally:
            lease.release()
        self.assertEqual(recovered.outcome, IdentityWorkOutcome.RETRY_EXHAUSTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations "
                "WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("failed", 1),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT outcome FROM hardcover_identity_attempts WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("interrupted",),
        )

    def test_retryable_attempt_publishes_retry_wait_without_retrying(self) -> None:
        request = self.request("unavailable")
        provider = _Provider(
            IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE),
            self.clock,
        )
        invocation = RunSafeHardcoverIdentity(
            self.work,
            provider,
            self.gate,
        ).run_invocation(request, self.context())
        self.assertEqual(
            invocation.report.outcome,
            HardcoverIdentityOutcome.IN_PROGRESS,
        )
        self.assertEqual(provider.starts, 1)
        token = invocation.retry_token
        self.assertIsNotNone(token)
        readiness = self.work.inspect(token.reference, self.clock.now)
        self.assertEqual(token, readiness.retry_token)
        self.assertEqual(
            (
                token.candidate,
                token.fence,
                token.due_at,
            ),
            (2, 2, ORIGIN + timedelta(seconds=1)),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("retry_wait", 1),
        )

    def test_persisted_deadline_is_checked_before_gate(self) -> None:
        request = self.request("deadline")
        self.work.accept(request.work_key, self.clock.now)
        self.clock.now += timedelta(seconds=30)
        with patch.object(self.gate, "try_acquire") as acquire:
            report = self.execute(request, _Provider(self.matched(), self.clock))
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        acquire.assert_not_called()

    def test_busy_gate_does_not_claim_or_start(self) -> None:
        held = self.gate.try_acquire()
        self.assertIsNotNone(held)
        request = self.request("busy")
        provider = _Provider(self.matched(), self.clock)
        try:
            report = self.execute(request, provider)
        finally:
            held.release()
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(provider.starts, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_claims WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (0,),
        )

    def test_reconciles_post_gate_lifecycle_races_without_local_mutation(self) -> None:
        for transition, outcome, state, attempts in (
            (
                "matched",
                HardcoverIdentityOutcome.MATCHED,
                "succeeded",
                1,
            ),
            (
                "failed",
                HardcoverIdentityOutcome.PERSISTENCE_FAILURE,
                "failed",
                1,
            ),
            (
                "cancelled",
                HardcoverIdentityOutcome.PERSISTENCE_FAILURE,
                "cancelled",
                0,
            ),
        ):
            with self.subTest(transition=transition):
                request = self.request(f"post-gate-{transition}")
                provider = _Provider(self.matched(), self.clock)
                accepted = self.work.accept(request.work_key, self.clock.now)
                acquire = self.gate.try_acquire
                competing_snapshot = None

                def acquire_after_competing_transition():
                    nonlocal competing_snapshot
                    competing = self.work.claim(
                        accepted.reference,
                        "competing-worker",
                        self.clock.now,
                        gate_owned=True,
                    )
                    if transition == "cancelled":
                        self.work.cancel(
                            competing,
                            "competing-worker",
                            self.clock.now,
                            gate_owned=True,
                        )
                    else:
                        started = self.work.start(
                            competing,
                            "competing-worker",
                            self.clock.now,
                            gate_owned=True,
                        )
                        if transition == "matched":
                            self.work.publish_matched(
                                started,
                                "competing-worker",
                                self.clock.now,
                                IdentityWorkObservation(
                                    IdentityMatchOutcome.MATCHED,
                                    1,
                                    1001,
                                    2001,
                                    "Synthetic Ebook Alpha",
                                ),
                                gate_owned=True,
                            )
                        else:
                            self.work.publish_failed(
                                started,
                                "competing-worker",
                                self.clock.now,
                                retryable=transition == "retry_wait",
                                gate_owned=True,
                            )
                    competing_snapshot = self._work_counts(request.work_key)
                    return acquire()

                with patch.object(
                    self.gate,
                    "try_acquire",
                    side_effect=acquire_after_competing_transition,
                ):
                    report = self.execute(request, provider)

                self.assertEqual(report.outcome, outcome)
                self.assertEqual(provider.starts, 0)
                self.assertEqual(
                    self._work_counts(request.work_key), competing_snapshot
                )
                self.assertEqual(competing_snapshot, (1, 1, attempts, state))

    def test_post_gate_future_retry_wait_race_remains_in_progress(self) -> None:
        request = self.request("post-gate-future-retry")
        provider = _Provider(self.matched(), self.clock)
        accepted = self.work.accept(request.work_key, self.clock.now)
        acquire = self.gate.try_acquire
        competing_snapshot = None

        def acquire_after_retry_schedule():
            nonlocal competing_snapshot
            competing = self.work.claim(
                accepted.reference,
                "competing-worker",
                self.clock.now,
                gate_owned=True,
            )
            started = self.work.start(
                competing,
                "competing-worker",
                self.clock.now,
                gate_owned=True,
            )
            self.work.publish_failed(
                started,
                "competing-worker",
                self.clock.now,
                retryable=True,
                retry_delay_seconds=10,
                gate_owned=True,
            )
            competing_snapshot = self._work_counts(request.work_key)
            return acquire()

        with patch.object(
            self.gate,
            "try_acquire",
            side_effect=acquire_after_retry_schedule,
        ):
            report = self.execute(request, provider)

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(provider.starts, 0)
        self.assertEqual(self._work_counts(request.work_key), competing_snapshot)
        self.assertEqual(competing_snapshot, (1, 1, 1, "retry_wait"))

    def test_post_gate_cancellation_cancels_future_retry_wait(self) -> None:
        request = self.request("post-gate-cancel-future-retry")
        provider = _Provider(self.matched(), self.clock)
        accepted = self.work.accept(request.work_key, self.clock.now)
        acquire = self.gate.try_acquire

        def acquire_after_retry_schedule():
            competing = self.work.claim(
                accepted.reference,
                "competing-worker",
                self.clock.now,
                gate_owned=True,
            )
            started = self.work.start(
                competing,
                "competing-worker",
                self.clock.now,
                gate_owned=True,
            )
            self.work.publish_failed(
                started,
                "competing-worker",
                self.clock.now,
                retryable=True,
                retry_delay_seconds=10,
                gate_owned=True,
            )
            self.cancelled.set()
            return acquire()

        with patch.object(
            self.gate,
            "try_acquire",
            side_effect=acquire_after_retry_schedule,
        ):
            report = self.execute(request, provider)

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.CANCELLED)
        self.assertEqual(provider.starts, 0)
        self.assertEqual(self._work_counts(request.work_key), (1, 1, 1, "cancelled"))

    def test_post_gate_deadline_expiry_stops_future_retry_wait(self) -> None:
        request = self.request("post-gate-deadline-future-retry")
        provider = _Provider(self.matched(), self.clock)
        accepted = self.work.accept(request.work_key, self.clock.now)
        acquire = self.gate.try_acquire
        caller_deadline = ORIGIN + timedelta(seconds=5)

        def acquire_after_retry_schedule():
            competing = self.work.claim(
                accepted.reference,
                "competing-worker",
                self.clock.now,
                gate_owned=True,
            )
            started = self.work.start(
                competing,
                "competing-worker",
                self.clock.now,
                gate_owned=True,
            )
            self.work.publish_failed(
                started,
                "competing-worker",
                self.clock.now,
                retryable=True,
                retry_delay_seconds=10,
                gate_owned=True,
            )
            self.clock.now = caller_deadline
            return acquire()

        with patch.object(
            self.gate,
            "try_acquire",
            side_effect=acquire_after_retry_schedule,
        ):
            report = self.execute(
                request,
                provider,
                context=self.context(caller_deadline),
            )

        self.assertEqual(report.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        self.assertEqual(provider.starts, 0)
        self.assertEqual(self._work_counts(request.work_key), (1, 1, 1, "retry_wait"))

    def test_rejects_inconsistent_post_gate_current_match_without_local_mutation(
        self,
    ) -> None:
        for corruption in (
            "missing-current",
            "expired-current",
            "different-generation",
            "malformed-observation",
            "malformed-current-pointer",
        ):
            with self.subTest(corruption=corruption):
                self.clock.now = ORIGIN
                request = self.request(f"post-gate-current-{corruption}")
                provider = _Provider(self.matched(), self.clock)
                accepted = self.work.accept(request.work_key, self.clock.now)
                acquire = self.gate.try_acquire
                competing_snapshot = None

                def acquire_after_corrupting_current_match():
                    nonlocal competing_snapshot
                    competing = self.work.claim(
                        accepted.reference,
                        "competing-worker",
                        self.clock.now,
                        gate_owned=True,
                    )
                    started = self.work.start(
                        competing,
                        "competing-worker",
                        self.clock.now,
                        gate_owned=True,
                    )
                    self.work.publish_matched(
                        started,
                        "competing-worker",
                        self.clock.now,
                        IdentityWorkObservation(IdentityMatchOutcome.MATCHED, 1),
                        gate_owned=True,
                    )

                    if corruption == "missing-current":
                        self.connection.execute(
                            "DELETE FROM current_hardcover_identity WHERE work_key=?",
                            (request.work_key,),
                        )
                    elif corruption == "expired-current":
                        self.clock.now += timedelta(days=1, microseconds=1)
                    elif corruption == "different-generation":
                        self.connection.execute("PRAGMA foreign_keys=OFF")
                        self.connection.execute(
                            "UPDATE current_hardcover_identity SET generation=2 WHERE work_key=?",
                            (request.work_key,),
                        )
                    elif corruption == "malformed-observation":
                        self.connection.execute(
                            "DROP TRIGGER hardcover_identity_observations_no_update"
                        )
                        self.connection.execute(
                            "UPDATE hardcover_identity_observations "
                            "SET canonical_observation='{}' WHERE work_key=?",
                            (request.work_key,),
                        )
                    else:
                        self.connection.execute("PRAGMA foreign_keys=OFF")
                        self.connection.execute(
                            "UPDATE current_hardcover_identity "
                            "SET observation_id='malformed' WHERE work_key=?",
                            (request.work_key,),
                        )
                    if self.connection.in_transaction:
                        self.connection.commit()
                    self.connection.execute("PRAGMA foreign_keys=ON")
                    competing_snapshot = self._work_counts(request.work_key)
                    return acquire()

                with patch.object(
                    self.gate,
                    "try_acquire",
                    side_effect=acquire_after_corrupting_current_match,
                ):
                    report = self.execute(request, provider)

                self.assertEqual(
                    report.outcome, HardcoverIdentityOutcome.PERSISTENCE_FAILURE
                )
                self.assertEqual(provider.starts, 0)
                self.assertEqual(
                    self._work_counts(request.work_key), competing_snapshot
                )
                self.assertEqual(competing_snapshot, (1, 1, 1, "succeeded"))

    def test_stale_current_reports_before_a_later_refresh_attempt(self) -> None:
        request = self.request("stale")
        accepted = self.work.accept(request.work_key, self.clock.now)
        claim = self.work.claim(
            accepted.reference,
            request.owner,
            self.clock.now,
            gate_owned=True,
        )
        started = self.work.start(
            claim,
            request.owner,
            self.clock.now,
            gate_owned=True,
        )
        self.work.publish_matched(
            started,
            request.owner,
            self.clock.now,
            IdentityWorkObservation(IdentityMatchOutcome.MATCHED, 1),
            gate_owned=True,
        )
        self.clock.now += timedelta(hours=24, microseconds=1)
        provider = _Provider(self.matched(), self.clock)
        report = self.execute(request, provider)
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.STALE)
        self.assertEqual(provider.starts, 0)
        refreshed = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(request, refreshed).outcome,
            HardcoverIdentityOutcome.MATCHED,
        )
        self.assertEqual(refreshed.starts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_generations WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (2,),
        )

    def test_reserved_stale_refresh_resumes_after_gate_contention(self) -> None:
        request = self.request("stale-gate-contention")
        accepted = self.work.accept(request.work_key, self.clock.now)
        claim = self.work.claim(
            accepted.reference,
            request.owner,
            self.clock.now,
            gate_owned=True,
        )
        started = self.work.start(
            claim,
            request.owner,
            self.clock.now,
            gate_owned=True,
        )
        self.work.publish_matched(
            started,
            request.owner,
            self.clock.now,
            IdentityWorkObservation(IdentityMatchOutcome.MATCHED, 1),
            gate_owned=True,
        )
        self.clock.now += timedelta(hours=24, microseconds=1)
        provider = _Provider(self.matched(), self.clock)

        stale = self.execute(request, provider)
        held = self.gate.try_acquire()
        self.assertIsNotNone(held)
        try:
            busy = self.execute(request, provider)
        finally:
            held.release()
        refreshed = self.execute(request, provider)

        self.assertEqual(stale.outcome, HardcoverIdentityOutcome.STALE)
        self.assertEqual(busy.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(refreshed.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(provider.starts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT generation,state,attempt_count FROM "
                "hardcover_identity_generations WHERE work_key=? "
                "ORDER BY generation",
                (request.work_key,),
            ).fetchall(),
            [(1, "succeeded", 1), (2, "succeeded", 1)],
        )

    def test_caller_deadline_is_preserved_for_attempt(self) -> None:
        caller_deadline = ORIGIN + timedelta(seconds=2)
        provider = _Provider(self.matched(), self.clock)
        report = self.execute(
            self.request("caller-deadline"),
            provider,
            context=self.context(caller_deadline),
        )
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(provider.deadlines, [caller_deadline])

    def test_expired_caller_stops_before_gate(self) -> None:
        request = self.request("expired-caller")
        with patch.object(self.gate, "try_acquire") as acquire:
            report = self.execute(
                request,
                _Provider(self.matched(), self.clock),
                context=self.context(ORIGIN),
            )
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        acquire.assert_not_called()

    def test_deadline_race_after_claim_returns_truthful_in_progress(self) -> None:
        request = self.request("post-claim-deadline")
        provider = _Provider(self.matched(), self.clock)
        real_claim = self.work.claim

        def claim_then_expire(*args: object, **kwargs: object):
            claim = real_claim(*args, **kwargs)
            self.clock.now = ORIGIN + timedelta(seconds=2)
            return claim

        with patch.object(self.work, "claim", side_effect=claim_then_expire):
            report = self.execute(
                request,
                provider,
                context=self.context(ORIGIN + timedelta(seconds=1)),
            )
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(provider.starts, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("claimed", 0),
        )

    def test_late_match_is_published_as_retry_wait_not_success(self) -> None:
        request = self.request("late")
        provider = _LateProvider(self.matched(), self.clock)
        report = self.execute(request, provider)
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(provider.starts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM hardcover_identity_generations WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("retry_wait",),
        )

    def test_late_result_respects_exact_six_second_retry_reserve(self) -> None:
        for offset, outcome, state in (
            (
                timedelta(seconds=23),
                HardcoverIdentityOutcome.IN_PROGRESS,
                "retry_wait",
            ),
            (
                timedelta(seconds=23, microseconds=1),
                HardcoverIdentityOutcome.RETRY_EXHAUSTED,
                "failed",
            ),
        ):
            with self.subTest(offset=offset):
                request = self.request(f"late-reserve-{offset}")
                self.work.accept(request.work_key, ORIGIN)
                self.clock.now = ORIGIN + timedelta(seconds=20)
                provider = _PublishAtProvider(
                    IdentityAttemptResult.failed(
                        IdentityAttemptFailureKind.UNAVAILABLE
                    ),
                    self.clock,
                    ORIGIN + offset,
                )
                report = self.execute(request, provider)
                self.assertEqual(report.outcome, outcome)
                self.assertEqual(provider.starts, 1)
                self.assertEqual(
                    self.connection.execute(
                        "SELECT state FROM hardcover_identity_generations "
                        "WHERE work_key=?",
                        (request.work_key,),
                    ).fetchone(),
                    (state,),
                )
                self.clock.now = ORIGIN

    def test_due_retry_reuses_the_one_attempt_ordering_without_early_send(self) -> None:
        request = self.request("due-retry")
        first = _Provider(
            IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE),
            self.clock,
        )
        self.assertEqual(
            self.execute(request, first).outcome,
            HardcoverIdentityOutcome.IN_PROGRESS,
        )
        due = ORIGIN + timedelta(seconds=1)
        self.assertEqual(
            self.work.inspect(
                self.work.accept(request.work_key, ORIGIN).reference,
                ORIGIN,
            ).retry_due_at,
            due,
        )
        early = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(request, early).outcome,
            HardcoverIdentityOutcome.IN_PROGRESS,
        )
        self.assertEqual(early.starts, 0)
        self.clock.now = due
        retry = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(request, retry).outcome,
            HardcoverIdentityOutcome.MATCHED,
        )
        self.assertEqual(retry.starts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt_count,state FROM hardcover_identity_generations "
                "WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (2, "succeeded"),
        )

    def test_retry_after_controls_due_or_exhausts_without_another_send(self) -> None:
        valid = self.request("retry-after-valid")
        rate_limited = _Provider(
            IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.RATE_LIMITED,
                retry_after_seconds=10,
            ),
            self.clock,
        )
        self.assertEqual(
            self.execute(valid, rate_limited).outcome,
            HardcoverIdentityOutcome.IN_PROGRESS,
        )
        self.assertEqual(
            self.work.inspect(
                self.work.accept(valid.work_key, ORIGIN).reference,
                ORIGIN,
            ).retry_due_at,
            ORIGIN + timedelta(seconds=10),
        )

        invalid = self.request("retry-after-invalid")
        rejected = _Provider(
            IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.INVALID_RETRY_AFTER
            ),
            self.clock,
        )
        self.assertEqual(
            self.execute(invalid, rejected).outcome,
            HardcoverIdentityOutcome.RETRY_EXHAUSTED,
        )
        self.assertEqual(rejected.starts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM hardcover_identity_generations WHERE work_key=?",
                (invalid.work_key,),
            ).fetchone(),
            ("failed",),
        )
        second = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(invalid, second).outcome,
            HardcoverIdentityOutcome.MATCHED,
        )
        self.assertEqual(second.starts, 1)

    def test_cancellation_before_start_and_after_provider_return_is_durable(self) -> None:
        before = self.request("cancel-before")
        self.cancelled.set()
        provider = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(before, provider).outcome,
            HardcoverIdentityOutcome.CANCELLED,
        )
        self.assertEqual(provider.starts, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations "
                "WHERE work_key=?",
                (before.work_key,),
            ).fetchone(),
            ("cancelled", 0),
        )

        self.cancelled.clear()
        after = self.request("cancel-after")
        cancelled = self.cancelled

        class CancellingProvider(_Provider):
            def exchange(self, context: OperationContext, **kwargs) -> object:
                result = super().exchange(context, **kwargs)
                cancelled.set()
                return result

        returning = CancellingProvider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(after, returning).outcome,
            HardcoverIdentityOutcome.CANCELLED,
        )
        self.assertEqual(returning.starts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT g.state,g.attempt_count,a.outcome "
                "FROM hardcover_identity_generations AS g "
                "JOIN hardcover_identity_attempts AS a USING(work_key,generation) "
                "WHERE g.work_key=?",
                (after.work_key,),
            ).fetchone(),
            ("cancelled", 1, "cancelled"),
        )

    def test_limit_one_survives_restart_repetition_and_limit_switch(self) -> None:
        request = self.request("one-shot", egress_limit=1)
        first = _Provider(
            IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE),
            self.clock,
        )
        self.assertEqual(
            self.execute(request, first).outcome,
            HardcoverIdentityOutcome.RETRY_EXHAUSTED,
        )
        self.assertEqual(first.starts, 1)
        for repeated in (
            request,
            HardcoverIdentityExecutionRequest(
                request.work_key,
                request.owner,
                request.query,
                3,
            ),
        ):
            provider = _Provider(self.matched(), self.clock)
            self.assertEqual(
                self.execute(repeated, provider).outcome,
                HardcoverIdentityOutcome.RETRY_EXHAUSTED,
            )
            self.assertEqual(provider.starts, 0)

        self.connection.close()
        self.connection = sqlite3.connect(self.database)
        self.addCleanup(self.connection.close)
        migrate(self.connection)
        self.work = ManageHardcoverIdentityWork(
            SQLiteHardcoverIdentityWorkRepository(self.connection)
        )
        after_restart = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(request, after_restart).outcome,
            HardcoverIdentityOutcome.RETRY_EXHAUSTED,
        )
        self.assertEqual(after_restart.starts, 0)
        self.assertEqual(
            self.connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM hardcover_identity_generations WHERE work_key=?),"
                "(SELECT COUNT(*) FROM hardcover_identity_attempts WHERE work_key=?)",
                (request.work_key, request.work_key),
            ).fetchone(),
            (1, 1),
        )

    def test_successful_one_shot_refresh_requires_normal_request(self) -> None:
        one_shot = self.request("successful-one-shot-refresh", egress_limit=1)
        first = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(one_shot, first).outcome,
            HardcoverIdentityOutcome.MATCHED,
        )
        self.assertEqual(first.starts, 1)

        self.clock.now += timedelta(hours=24, microseconds=1)
        repeated = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(one_shot, repeated).outcome,
            HardcoverIdentityOutcome.RETRY_EXHAUSTED,
        )
        self.assertEqual(repeated.starts, 0)

        normal = HardcoverIdentityExecutionRequest(
            one_shot.work_key,
            one_shot.owner,
            one_shot.query,
            3,
        )
        refreshed = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(normal, refreshed).outcome,
            HardcoverIdentityOutcome.STALE,
        )
        self.assertEqual(refreshed.starts, 0)
        refresh_attempt = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(normal, refresh_attempt).outcome,
            HardcoverIdentityOutcome.MATCHED,
        )
        self.assertEqual(refresh_attempt.starts, 1)

        self.clock.now += timedelta(hours=24, microseconds=1)
        third = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(normal, third).outcome,
            HardcoverIdentityOutcome.STALE,
        )
        self.assertEqual(third.starts, 0)
        third_attempt = _Provider(self.matched(), self.clock)
        self.assertEqual(
            self.execute(normal, third_attempt).outcome,
            HardcoverIdentityOutcome.MATCHED,
        )
        self.assertEqual(third_attempt.starts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT generation,attempt_count,state FROM "
                "hardcover_identity_generations WHERE work_key=? "
                "ORDER BY generation",
                (one_shot.work_key,),
            ).fetchall(),
            [
                (1, 1, "succeeded"),
                (2, 1, "succeeded"),
                (3, 1, "succeeded"),
            ],
        )

    def test_gate_acquisition_and_release_fail_closed(self) -> None:
        for gate in (_FailingGate(), _ReleaseFailureGate()):
            with self.subTest(gate=type(gate).__name__):
                report = self.execute(
                    self.request(type(gate).__name__),
                    _Provider(self.matched(), self.clock),
                    gate=gate,
                )
                self.assertEqual(
                    report.outcome,
                    HardcoverIdentityOutcome.PERSISTENCE_FAILURE,
                )

    def test_preview_report_rejects_false_proof_flags(self) -> None:
        values = {
            "outcome": "all_cases_passed",
            "cases_executed": 10,
            "attempts_started": 6,
            "gate_order_verified": True,
            "deadline_before_gate_verified": True,
            "caller_deadline_verified": True,
            "late_result_rejected": True,
            "closed_mapping_verified": True,
            "contributor_labels_private": True,
        }
        for field in (
            "gate_order_verified",
            "deadline_before_gate_verified",
            "caller_deadline_verified",
            "late_result_rejected",
            "closed_mapping_verified",
        ):
            with self.subTest(field=field):
                invalid = dict(values)
                invalid[field] = False
                with self.assertRaisesRegex(
                    TypeError, "Hardcover safe executor preview report is malformed"
                ):
                    SafeExecutorPreviewReport(**invalid).validate()

    def test_control_flow_exception_propagates_after_gate_release(self) -> None:
        with self.assertRaises(_ProcessDeath):
            self.execute(
                self.request("control-flow"),
                _Provider(_ProcessDeath(), self.clock),
            )
        contender = self.gate.try_acquire()
        self.assertIsNotNone(contender)
        contender.release()


class HardcoverIdentityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.chmod(0o700)
        self.database = self.root / "state.sqlite"
        self.database.write_bytes(b"synthetic-state")
        self.database.chmod(0o600)

    def test_lock_is_stable_private_nonblocking_and_never_unlinked(self) -> None:
        gate = DescriptorHardcoverIdentityGate(self.database)
        first = gate.try_acquire()
        self.assertIsNotNone(first)
        lock = self.root / LOCK_NAME
        identity = (lock.stat().st_dev, lock.stat().st_ino)
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        self.assertIsNone(gate.try_acquire())
        first.release()
        second = gate.try_acquire()
        self.assertIsNotNone(second)
        second.release()
        self.assertEqual((lock.stat().st_dev, lock.stat().st_ino), identity)

    def test_parent_alias_converges_on_same_lock(self) -> None:
        alias = self.root.parent / f"{self.root.name}-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.addCleanup(alias.unlink)
        aliased_gate = DescriptorHardcoverIdentityGate(alias / self.database.name)
        first = DescriptorHardcoverIdentityGate(self.database).try_acquire()
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(aliased_gate.try_acquire())
        finally:
            first.release()
        with tempfile.TemporaryDirectory(dir=self.root.parent) as replacement_name:
            replacement = Path(replacement_name)
            replacement.chmod(0o700)
            alias.unlink()
            alias.symlink_to(replacement, target_is_directory=True)
            with self.assertRaises(HardcoverIdentityGateError):
                aliased_gate.try_acquire()

    def test_connection_binding_marks_exact_inode_and_cleans_before_authority(
        self,
    ) -> None:
        self.database.unlink()
        connection = sqlite3.connect(self.database, isolation_level=None)
        self.addCleanup(connection.close)
        migrate(connection)
        identity = self.database.stat()
        gate = DescriptorHardcoverIdentityGate(
            self.database,
            expected_database_identity=(identity.st_dev, identity.st_ino),
        )

        gate.bind_connection(connection)

        self.assertEqual(connection.execute("PRAGMA application_id").fetchone(), (0,))
        with self.database.open("rb") as database_file:
            database_file.seek(68)
            self.assertEqual(database_file.read(4), b"\x00\x00\x00\x00")

        connection.execute("PRAGMA application_id=123456")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        gate.bind_connection(connection)
        self.assertEqual(
            connection.execute("PRAGMA application_id").fetchone(), (123456,)
        )

    def test_connection_binding_serializes_marker_and_preserves_original_value(
        self,
    ) -> None:
        self.database.unlink()
        setup = sqlite3.connect(self.database, isolation_level=None)
        migrate(setup)
        setup.execute("PRAGMA application_id=123456")
        setup.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        setup.close()
        identity = self.database.stat()
        first_gate = DescriptorHardcoverIdentityGate(
            self.database,
            expected_database_identity=(identity.st_dev, identity.st_ino),
        )
        second_gate = DescriptorHardcoverIdentityGate(
            self.database,
            expected_database_identity=(identity.st_dev, identity.st_ino),
        )
        marker_installed = threading.Event()
        allow_restore = threading.Event()
        failures: list[BaseException] = []
        original_read = DescriptorHardcoverIdentityGate._read_application_id

        def pause_after_marker(descriptor: int) -> int:
            value = original_read(descriptor)
            if value == 0x4D4C4201 and not marker_installed.is_set():
                marker_installed.set()
                if not allow_restore.wait(timeout=5):
                    raise AssertionError("binding interleave was not released")
            return value

        def bind_first() -> None:
            connection = sqlite3.connect(self.database, isolation_level=None)
            try:
                first_gate.bind_connection(connection)
            except BaseException as error:
                failures.append(error)
            finally:
                connection.close()

        with patch.object(
            DescriptorHardcoverIdentityGate,
            "_read_application_id",
            side_effect=pause_after_marker,
        ):
            first = threading.Thread(target=bind_first)
            first.start()
            try:
                self.assertTrue(marker_installed.wait(timeout=5))
                contender = sqlite3.connect(self.database, isolation_level=None)
                try:
                    with self.assertRaisesRegex(
                        HardcoverIdentityGateError,
                        "deployment gate is busy",
                    ):
                        second_gate.bind_connection(contender)
                finally:
                    contender.close()
                descriptor = os.open(self.database, os.O_RDONLY)
                try:
                    self.assertEqual(original_read(descriptor), 0x4D4C4201)
                finally:
                    os.close(descriptor)
            finally:
                allow_restore.set()
                first.join(timeout=5)
            self.assertFalse(first.is_alive())
            self.assertEqual(failures, [])

        second = sqlite3.connect(self.database, isolation_level=None)
        try:
            self.assertEqual(second.execute("PRAGMA application_id").fetchone(), (123456,))
            second_gate.bind_connection(second)
            self.assertEqual(second.execute("PRAGMA application_id").fetchone(), (123456,))
        finally:
            second.close()

    def test_connection_binding_repairs_crash_marker_and_rejects_inode_aba(
        self,
    ) -> None:
        self.database.unlink()
        original_connection = sqlite3.connect(self.database, isolation_level=None)
        migrate(original_connection)
        original_connection.execute("PRAGMA application_id=1296843265")
        original_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        original_connection.close()
        identity = self.database.stat()
        gate = DescriptorHardcoverIdentityGate(
            self.database,
            expected_database_identity=(identity.st_dev, identity.st_ino),
        )
        repaired = sqlite3.connect(self.database, isolation_level=None)
        self.addCleanup(repaired.close)
        gate.bind_connection(repaired)
        self.assertEqual(repaired.execute("PRAGMA application_id").fetchone(), (0,))
        repaired.close()

        original = self.root / "original.sqlite"
        self.database.rename(original)
        replacement_connection = sqlite3.connect(
            self.database, isolation_level=None
        )
        self.addCleanup(replacement_connection.close)
        migrate(replacement_connection)
        replacement = self.root / "replacement.sqlite"
        self.database.rename(replacement)
        original.rename(self.database)

        with self.assertRaisesRegex(
            HardcoverIdentityGateError, "connection identity changed"
        ):
            gate.bind_connection(replacement_connection)
        self.assertEqual(
            replacement_connection.execute("PRAGMA application_id").fetchone(),
            (0,),
        )
        restored = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                restored.execute("PRAGMA application_id").fetchone(),
                (0,),
            )
        finally:
            restored.close()

    def test_final_symlink_hardlink_nonregular_and_lock_replacement_are_rejected(self) -> None:
        real = self.database
        symlink = self.root / "symlink.sqlite"
        symlink.symlink_to(real)
        with self.assertRaises(HardcoverIdentityGateError):
            DescriptorHardcoverIdentityGate(symlink).try_acquire()
        hardlink = self.root / "hardlink.sqlite"
        os.link(real, hardlink)
        with self.assertRaises(HardcoverIdentityGateError):
            DescriptorHardcoverIdentityGate(real).try_acquire()
        hardlink.unlink()
        fifo = self.root / "state.fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(HardcoverIdentityGateError):
            DescriptorHardcoverIdentityGate(fifo).try_acquire()
        fifo.unlink()

        gate = DescriptorHardcoverIdentityGate(real)
        lease = gate.try_acquire()
        self.assertIsNotNone(lease)
        lock = self.root / LOCK_NAME
        lock.rename(self.root / "old-lock")
        lock.write_bytes(b"")
        lock.chmod(0o600)
        with self.assertRaises(HardcoverIdentityGateError):
            lease.validate_for_send()
        lease.release()

    def test_dotdot_permissive_root_and_database_replacement_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DescriptorHardcoverIdentityGate(
                self.root / "nested" / ".." / self.database.name
            )
        self.root.chmod(0o755)
        with self.assertRaises(HardcoverIdentityGateError):
            DescriptorHardcoverIdentityGate(self.database).try_acquire()
        self.root.chmod(0o700)
        gate = DescriptorHardcoverIdentityGate(self.database)
        lease = gate.try_acquire()
        self.assertIsNotNone(lease)
        self.database.rename(self.root / "old-state")
        self.database.write_bytes(b"replacement")
        with self.assertRaises(HardcoverIdentityGateError):
            lease.validate_for_send()
        lease.release()


if __name__ == "__main__":
    unittest.main()
