from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from mechanical_librarian.adapters.persistence.provider_recovery import ProviderRecoveryStoreError, SQLiteProviderRecoveryStore
import mechanical_librarian.adapters.persistence.provider_recovery as recovery_persistence
from mechanical_librarian.domain.provider_recovery import MAX_TIMESTAMP, ProviderRecovery, ProviderRecoveryError


class ProviderRecoveryTests(unittest.TestCase):
    def test_bootstrap_preserves_unrelated_objects_and_rejects_partial_or_rolled_back_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE unrelated(value TEXT) STRICT")
            connection.close()
            store = SQLiteProviderRecoveryStore.open(path)
            self.assertEqual(("unrelated",), store._connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='unrelated'").fetchone())
            store.close()

            partial = Path(directory) / "partial.sqlite"
            sqlite3.connect(partial).execute("CREATE TABLE provider_recovery_health(value TEXT)").connection.close()
            with self.assertRaises(ProviderRecoveryStoreError):
                SQLiteProviderRecoveryStore.open(partial)

            rolled_back = Path(directory) / "rolled-back.sqlite"
            with patch.object(recovery_persistence, "_CREATE", recovery_persistence._CREATE + ("INVALID SQL",)):
                with self.assertRaises(ProviderRecoveryStoreError):
                    SQLiteProviderRecoveryStore.open(rolled_back)
            connection = sqlite3.connect(rolled_back)
            self.assertEqual([], connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'provider_recovery_%'").fetchall())
            connection.close()

    def test_shared_store_serializes_worker_observation_and_closes_after_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            failures: list[Exception] = []
            def observe() -> None:
                try:
                    store.observe_failure(connection="primary", adapter="hardcover", scope="books", generation=1, failure="authentication", now=0)
                    store.activity_snapshot()
                except Exception as error:
                    failures.append(error)
            worker = threading.Thread(target=observe)
            worker.start(); worker.join()
            self.assertEqual([], failures)
            store.reconcile_generation(generation=2, now=1)
            self.assertIsNotNone(store.claim_one(generation=2, now=1, token="owner"))
            self.assertEqual("checking", store.snapshot()[0].state)
            store.close()
            with self.assertRaises(ProviderRecoveryStoreError):
                store.activity_snapshot()

    def test_transient_retry_and_generation_wake_are_closed(self) -> None:
        recovery = ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="transient", now=10)
        self.assertEqual(("waiting", 1, 7210), (recovery.state, recovery.attempt, recovery.next_eligible_at))
        self.assertEqual(14410, recovery.retry(now=10).next_eligible_at)
        sequence = ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 0, 0)
        delays = []
        for _ in range(5):
            sequence = sequence.retry(now=0)
            delays.append(sequence.next_eligible_at)
        self.assertEqual([7_200, 14_400, 28_800, 57_600, 86_400], delays)
        dormant = ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="authentication", now=10)
        self.assertEqual("dormant", dormant.state)
        self.assertEqual("waiting", dormant.with_generation(2).state)
        for value in (0, 86401, True):
            with self.subTest(value=value), self.assertRaises(ProviderRecoveryError):
                ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="rate_limited", now=10, retry_after=value)

    def test_atomic_claim_restart_and_corruption_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.sqlite"
            store = SQLiteProviderRecoveryStore.open(path)
            store.record(ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            self.assertIsNone(store.claim_one(generation=1, now=0, token="a"))
            self.assertIsNotNone(store.claim_one(generation=1, now=1, token="a"))
            self.assertIsNone(store.claim_one(generation=1, now=1, token="b"))
            with self.assertRaises(ProviderRecoveryStoreError):
                store.record(ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="authentication", now=2), occurred_at=2)
            # An interrupted worker can only be reclaimed after its bounded
            # lease; the claim operation itself never invokes a provider.
            self.assertIsNotNone(store.claim_one(generation=1, now=61, token="b"))
            store.close()
            reopened = SQLiteProviderRecoveryStore.open(path)
            self.assertEqual("checking", reopened.snapshot()[0].state)
            reopened.close()
            connection = sqlite3.connect(path)
            connection.execute("DROP TRIGGER provider_recovery_events_no_delete")
            connection.close()
            with self.assertRaises(ProviderRecoveryStoreError):
                SQLiteProviderRecoveryStore.open(path)

    def test_owner_completion_and_backoff_transition_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            first = ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="transient", now=0, retry_after=2)
            store.record(first, occurred_at=0)
            claimed = store.claim_one(generation=1, now=2, token="owner")
            self.assertIsNotNone(claimed)
            with self.assertRaises(TypeError):
                store.record(ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 2, 6), occurred_at=2, owner_token="owner")
            with self.assertRaises(ProviderRecoveryStoreError):
                store.record(ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 2, 6), occurred_at=2)
            waiting = ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 2, 6)
            with self.assertRaises(ProviderRecoveryStoreError):
                store.complete(token="other", recovery=claimed, now=2, failure="transient", retry_after=4)
            self.assertEqual(waiting, store.complete(token="owner", recovery=claimed, now=2, failure="transient", retry_after=4))
            self.assertEqual(("waiting", 2, 6), (store.snapshot()[0].state, store.snapshot()[0].attempt, store.snapshot()[0].next_eligible_at))
            # An idempotent same state does not make another event.
            store.record(waiting, occurred_at=2)
            self.assertEqual(3, store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0])
            store.close()

    def test_membership_mismatch_fails_before_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.sqlite"
            store = SQLiteProviderRecoveryStore.open(path)
            store.record(ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="authentication", now=0), occurred_at=0)
            store.close()
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DELETE FROM provider_recovery_members")
            connection.commit()
            connection.close()
            with self.assertRaises(ProviderRecoveryStoreError):
                SQLiteProviderRecoveryStore.open(path)

    def test_timestamp_bound_accepts_exact_maximum_and_rejects_one_over(self) -> None:
        self.assertEqual(MAX_TIMESTAMP, ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 1, MAX_TIMESTAMP).next_eligible_at)
        with self.assertRaises(ProviderRecoveryError):
            ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 1, MAX_TIMESTAMP + 1)

    def test_startup_generation_reconciles_every_older_state_and_rejects_newer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            dormant = ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="authentication", now=0)
            waiting = ProviderRecovery.failed(connection="other", adapter="hardcover", scope="books", generation=1, failure="transient", now=0, retry_after=1)
            checking = ProviderRecovery.failed(connection="aaa", adapter="hardcover", scope="books", generation=1, failure="transient", now=0, retry_after=1)
            store.record(dormant, occurred_at=0); store.record(waiting, occurred_at=0); store.record(checking, occurred_at=0)
            self.assertIsNotNone(store.claim_one(generation=1, now=1, token="owner"))
            self.assertEqual(3, store.reconcile_generation(generation=2, now=9))
            rows = {item.connection: item for item in store.snapshot(limit=3)}
            self.assertEqual((2, "waiting", 0, 9), (rows["primary"].generation, rows["primary"].state, rows["primary"].attempt, rows["primary"].next_eligible_at))
            self.assertEqual((2, "waiting", 1, 1), (rows["other"].generation, rows["other"].state, rows["other"].attempt, rows["other"].next_eligible_at))
            self.assertEqual((2, "checking", 1, None), (rows["aaa"].generation, rows["aaa"].state, rows["aaa"].attempt, rows["aaa"].next_eligible_at))
            self.assertEqual(
                [("aaa", 2, "checking", 1, None), ("other", 2, "waiting", 1, 1), ("primary", 2, "waiting", 0, 9)],
                store._connection.execute("SELECT connection,generation,state,attempt,next_eligible_at FROM provider_recovery_events WHERE generation=2 ORDER BY connection").fetchall(),
            )
            before = (store.snapshot(limit=3), store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0])
            with self.assertRaises(ProviderRecoveryStoreError):
                store.reconcile_generation(generation=1, now=10)
            self.assertEqual(before, (store.snapshot(limit=3), store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0]))
            store.close()

    def test_health_observation_is_idempotent_and_never_completes_a_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            kwargs = {"connection": "primary", "adapter": "hardcover", "scope": "books", "generation": 1}
            store.observe_failure(**kwargs, failure="transient", now=0)
            first = store.snapshot()[0]
            store.observe_failure(**kwargs, failure="rate_limited", now=10)
            self.assertEqual(first, store.snapshot()[0])
            self.assertEqual(1, store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0])
            store.observe_failure(**kwargs, failure="authentication", now=10)
            dormant = store.snapshot()[0]
            store.observe_failure(**kwargs, failure="authorization", now=11)
            self.assertEqual(dormant, store.snapshot()[0])
            self.assertEqual(2, store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0])

            checking = ProviderRecovery.failed(connection="other", adapter="hardcover", scope="books", generation=1, failure="transient", now=0, retry_after=1)
            store.record(checking, occurred_at=0)
            claimed = store.claim_one(generation=1, now=1, token="owner")
            self.assertIsNotNone(claimed)
            events = store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0]
            store.observe_failure(connection="other", adapter="hardcover", scope="books", generation=1, failure="authentication", now=1)
            self.assertEqual("checking", {item.connection: item for item in store.snapshot(limit=2)}["other"].state)
            self.assertEqual(events, store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0])
            store.close()

    def test_completion_rejects_exact_expiry_and_excluded_failure_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            recovery = ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="transient", now=0, retry_after=1)
            store.record(recovery, occurred_at=0)
            claimed = store.claim_one(generation=1, now=1, token="owner", lease_seconds=1)
            assert claimed is not None
            before = (store.snapshot(), store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0])
            with self.assertRaises(ProviderRecoveryStoreError):
                store.complete(token="owner", recovery=claimed, now=2, failure="transient")
            self.assertEqual(before, (store.snapshot(), store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0]))
            with self.assertRaises(ProviderRecoveryStoreError):
                store.complete(token="owner", recovery=claimed, now=1, failure="no_match")
            self.assertEqual(before, (store.snapshot(), store._connection.execute("SELECT count(*) FROM provider_recovery_events").fetchone()[0]))
            store.close()

    def test_completion_caps_retry_storm_and_activity_aggregates_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            for number in range(33):
                recovery = ProviderRecovery.failed(connection=f"c{number}", adapter="hardcover", scope="books", generation=1, failure="authentication", now=0)
                store.record(recovery, occurred_at=0)
            self.assertEqual(1, len(store.activity_snapshot()))
            store.record(ProviderRecovery.failed(connection="storm", adapter="hardcover", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            current = store.claim_one(generation=1, now=1, token="owner")
            self.assertIsNotNone(current)
            assert current is not None
            for attempt in range(5):
                current = store.complete(token="owner", recovery=current, now=10 + attempt, failure="transient")
                if attempt < 4:
                    current = store.claim_one(generation=1, now=current.next_eligible_at or 0, token="owner")
                    assert current is not None
            self.assertEqual(("waiting", 5, 10 + 4 + 86_400), (current.state, current.attempt, current.next_eligible_at))
