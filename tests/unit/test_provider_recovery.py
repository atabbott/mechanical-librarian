from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.provider_recovery import ProviderRecoveryStoreError, SQLiteProviderRecoveryStore
from mechanical_librarian.domain.provider_recovery import ProviderRecovery, ProviderRecoveryError


class ProviderRecoveryTests(unittest.TestCase):
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
            waiting = ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 2, 6)
            with self.assertRaises(ProviderRecoveryStoreError):
                store.record(waiting, occurred_at=2, owner_token="other")
            store.record(waiting, occurred_at=2, owner_token="owner")
            self.assertEqual(("waiting", 2, 6), (store.snapshot()[0].state, store.snapshot()[0].attempt, store.snapshot()[0].next_eligible_at))
            # A duplicate completion is idempotent and does not make another event.
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
