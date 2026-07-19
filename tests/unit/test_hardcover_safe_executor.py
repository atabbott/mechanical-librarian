from __future__ import annotations

import os
import signal
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
from mechanical_librarian.infrastructure.cancellation_signals import (
    temporary_cancellation_signals,
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


class _Waiter:
    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.due: list[datetime] = []

    def wait_until(self, due_at: datetime, context: OperationContext) -> None:
        self.due.append(due_at)
        if not context.cancelled:
            self.clock.now = due_at


class _ScriptedProvider:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.starts: list[datetime] = []

    def lookup(self, query: object, context: OperationContext) -> object:
        self.starts.append(context.observed_at)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


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
        self.waiter = _Waiter(self.clock)
        self.gate = DescriptorHardcoverIdentityGate(self.database)
        self.query = validate_private_query(
            {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
        )

    def context(self) -> OperationContext:
        return OperationContext(
            Deadline(ORIGIN + timedelta(minutes=5)),
            CancellationProbe(
                CancellationId("hardcover-safe-test"), self.cancelled.is_set
            ),
            ResourceBounds(4, 65_536),
            TimeProbe(lambda: self.clock.now),
        )

    def request(self, label: str, *, one_shot: bool = False):
        import hashlib

        return HardcoverIdentityExecutionRequest(
            hashlib.sha256(label.encode("ascii")).hexdigest(),
            "worker-a",
            self.query,
            one_shot,
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

    def executor(self, provider: _ScriptedProvider) -> RunHardcoverIdentityReport:
        safe = RunSafeHardcoverIdentity(self.work, provider, self.gate, self.waiter)
        return RunHardcoverIdentityReport(safe)

    def test_retry_releases_gate_waits_exactly_and_matches_on_second_start(self) -> None:
        provider = _ScriptedProvider(
            IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE),
            self.matched(),
        )
        report = self.executor(provider).run(self.request("retry-match"), self.context())
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(len(provider.starts), 2)
        self.assertEqual(provider.starts[1] - provider.starts[0], timedelta(seconds=1))
        self.assertEqual(self.waiter.due, [ORIGIN + timedelta(seconds=1)])
        contender = self.gate.try_acquire()
        self.assertIsNotNone(contender)
        contender.release()

    def test_retry_after_uses_later_delay_and_third_failure_has_no_fourth_send(self) -> None:
        provider = _ScriptedProvider(
            IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.RATE_LIMITED,
                retry_after_seconds=3,
            ),
            IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE),
            IdentityAttemptResult.failed(IdentityAttemptFailureKind.TIMEOUT),
        )
        report = self.executor(provider).run(self.request("three-starts"), self.context())
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        self.assertEqual(len(provider.starts), 3)
        self.assertEqual(provider.starts[1] - provider.starts[0], timedelta(seconds=3))
        self.assertEqual(provider.starts[2] - provider.starts[1], timedelta(seconds=2))

    def test_exact_aggregate_deadline_finishes_before_gate_attempt(self) -> None:
        request = self.request("exact-deadline")
        accepted = self.work.accept(request.work_key, self.clock.now)
        self.clock.now += timedelta(seconds=30)
        with patch.object(self.gate, "try_acquire") as acquire:
            report = self.executor(_ScriptedProvider()).run(request, self.context())
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        acquire.assert_not_called()
        row = self.connection.execute(
            "SELECT state FROM hardcover_identity_generations WHERE work_key=? AND generation=?",
            (request.work_key, accepted.reference.generation),
        ).fetchone()
        self.assertEqual(row, ("failed",))

    def test_busy_gate_does_not_claim_or_start(self) -> None:
        held = self.gate.try_acquire()
        self.assertIsNotNone(held)
        try:
            request = self.request("busy")
            report = self.executor(_ScriptedProvider()).run(request, self.context())
        finally:
            held.release()
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.IN_PROGRESS)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_claims WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (0,),
        )

    def test_one_shot_consumes_one_start_and_never_waits(self) -> None:
        provider = _ScriptedProvider(
            IdentityAttemptResult.failed(IdentityAttemptFailureKind.UNAVAILABLE)
        )
        report = self.executor(provider).run(
            self.request("one-shot", one_shot=True), self.context()
        )
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        self.assertEqual(len(provider.starts), 1)
        self.assertEqual(self.waiter.due, [])

    def test_provider_cancellation_is_durable_and_gate_is_released(self) -> None:
        class CancellingProvider(_ScriptedProvider):
            def lookup(inner, query: object, context: OperationContext) -> object:
                self.cancelled.set()
                return IdentityAttemptResult.failed(
                    IdentityAttemptFailureKind.CANCELLED
                )

        report = self.executor(CancellingProvider()).run(
            self.request("cancelled"), self.context()
        )
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.CANCELLED)
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM hardcover_identity_generations WHERE work_key=?",
                (self.request("cancelled").work_key,),
            ).fetchone(),
            ("cancelled",),
        )
        contender = self.gate.try_acquire()
        self.assertIsNotNone(contender)
        contender.release()

    def test_post_start_death_consumes_attempt_and_later_owner_recovers(self) -> None:
        request = self.request("death")
        with self.assertRaises(_ProcessDeath):
            self.executor(_ScriptedProvider(_ProcessDeath())).run(
                request, self.context()
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            ("claimed", 1),
        )
        self.clock.now += timedelta(seconds=15, microseconds=1)
        provider = _ScriptedProvider(self.matched())
        report = self.executor(provider).run(request, self.context())
        self.assertEqual(report.outcome, HardcoverIdentityOutcome.MATCHED)
        self.assertEqual(len(provider.starts), 1)
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt_count,state FROM hardcover_identity_generations WHERE work_key=?",
                (request.work_key,),
            ).fetchone(),
            (2, "succeeded"),
        )


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


class TemporaryCancellationSignalTests(unittest.TestCase):
    def test_handlers_set_event_and_restore_exact_values_on_all_exits(self) -> None:
        event = threading.Event()
        before = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        with self.assertRaises(KeyboardInterrupt):
            with temporary_cancellation_signals(event):
                current = signal.getsignal(signal.SIGINT)
                current(signal.SIGINT, None)
                self.assertTrue(event.is_set())
                raise KeyboardInterrupt
        self.assertEqual(
            {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)},
            before,
        )

    def test_partial_install_restores_and_non_main_thread_is_rejected(self) -> None:
        event = threading.Event()
        original = signal.getsignal(signal.SIGINT)
        real_signal = signal.signal

        def fail_second(number: signal.Signals, handler: object):
            if number == signal.SIGTERM:
                raise OSError("synthetic")
            return real_signal(number, handler)

        with patch("signal.signal", side_effect=fail_second):
            with self.assertRaises(OSError):
                with temporary_cancellation_signals(event):
                    pass
        self.assertEqual(signal.getsignal(signal.SIGINT), original)

        failures: list[BaseException] = []

        def run() -> None:
            try:
                with temporary_cancellation_signals(event):
                    pass
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeError)


if __name__ == "__main__":
    unittest.main()
