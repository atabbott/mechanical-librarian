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

    def lookup(self, query: object, context: OperationContext) -> object:
        self.starts += 1
        self.deadlines.append(context.deadline.at)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _LateProvider(_Provider):
    def lookup(self, query: object, context: OperationContext) -> object:
        self.clock.now = context.deadline.at
        return super().lookup(query, context)


class _PublishAtProvider(_Provider):
    def __init__(
        self, result: object, clock: _Clock, published_at: datetime
    ) -> None:
        super().__init__(result, clock)
        self.published_at = published_at

    def lookup(self, query: object, context: OperationContext) -> object:
        self.clock.now = self.published_at
        return super().lookup(query, context)


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

    def request(self, label: str) -> HardcoverIdentityExecutionRequest:
        return HardcoverIdentityExecutionRequest(
            hashlib.sha256(label.encode("ascii")).hexdigest(),
            "synthetic-worker",
            self.query,
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

    def test_one_attempt_matches_and_releases_gate(self) -> None:
        provider = _Provider(self.matched(), self.clock)
        report = self.execute(self.request("matched"), provider)
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(provider.starts, 1)
        contender = self.gate.try_acquire()
        self.assertIsNotNone(contender)
        contender.release()

    def test_retryable_attempt_publishes_retry_wait_without_retrying(self) -> None:
        request = self.request("unavailable")
        provider = _Provider(
            IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE),
            self.clock,
        )
        report = self.execute(request, provider)
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(provider.starts, 1)
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

    def test_stale_current_is_reserved_before_inspection_and_attempt(self) -> None:
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
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(provider.starts, 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_generations WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (2,),
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
                timedelta(seconds=24),
                HardcoverIdentityOutcome.IN_PROGRESS,
                "retry_wait",
            ),
            (
                timedelta(seconds=24, microseconds=1),
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
