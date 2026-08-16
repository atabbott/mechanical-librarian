from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import mechanical_librarian.adapters.persistence.provider_recovery as recovery_persistence
from mechanical_librarian.adapters.persistence.provider_recovery import (
    ProviderRecoveryJob,
    ProviderRecoveryStoreError,
    SQLiteProviderRecoveryStore,
)
from mechanical_librarian.application.metadata_report import CredentialError
from mechanical_librarian.application.provider_recovery_runner import (
    ScopedProviderRecoveryRunner,
)
from mechanical_librarian.application.provider_retry_successor import (
    ProviderRetrySuccessorError,
)
from mechanical_librarian.bootstrap.composition import (
    _provider_recovery_jobs,
    _record_provider_recovery_observations,
)
from mechanical_librarian.domain.metadata_report import ProviderStatus
from mechanical_librarian.domain.provider_recovery import (
    MAX_TIMESTAMP,
    ProviderRecovery,
    ProviderRecoveryError,
)


class ProviderRecoveryTests(unittest.TestCase):
    @staticmethod
    def _job(member_id: str) -> ProviderRecoveryJob:
        return ProviderRecoveryJob("google", "google_books", "books", 1, member_id, "primary", "books", member_id, f"file-{member_id}", f"report-{member_id}", "proposal", f"report-{member_id}", "case", 1, "admitted", '["work"]')

    def test_job_preserves_each_canonical_current_review_package_shape(self) -> None:
        common = ("google", "google_books", "books", 1, "member", "primary", "books", "item", "file", "report")
        for package in (
            (None, "report", None, None, "report_only"),
            ("proposal", "report", "case", 1, "admitted"),
            ("proposal", "report", "case", 1, "superseded"),
            ("proposal", "report", None, 1, "no_case"),
            ("proposal", "report", None, 1, "retired"),
        ):
            with self.subTest(package=package):
                self.assertEqual(package, (
                    ProviderRecoveryJob(*common, *cast(tuple[str | None, str, str | None, int | None, str], package), '["work"]').package_proposal_id,
                    ProviderRecoveryJob(*common, *cast(tuple[str | None, str, str | None, int | None, str], package), '["work"]').package_report_id,
                    ProviderRecoveryJob(*common, *cast(tuple[str | None, str, str | None, int | None, str], package), '["work"]').package_case_id,
                    ProviderRecoveryJob(*common, *cast(tuple[str | None, str, str | None, int | None, str], package), '["work"]').package_checkpoint,
                    ProviderRecoveryJob(*common, *cast(tuple[str | None, str, str | None, int | None, str], package), '["work"]').package_outcome,
                ))
        with self.assertRaises(ProviderRecoveryError):
            ProviderRecoveryJob(*common, None, "report", "case", 1, "report_only", '["work"]')

    def test_activation_runner_claims_one_canary_then_fans_out_current_members_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            calls: list[str] = []
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda _, member, __: calls.append(member.source_report_id),
                classify_failure=lambda _: ("transient", None),
            )
            runner.enqueue((self._job("canary"), self._job("sibling-a"), self._job("sibling-b")))
            result = runner.run_one(now=1, token="owner")
            self.assertEqual(("recovered", 2), (result.state, result.fanout))
            self.assertEqual("idle", runner.run_one(now=1, token="other").state)
            # The second invocation is idle: the first scope was atomically closed.
            self.assertEqual(["report-canary", "report-sibling-a", "report-sibling-b"], calls)
            self.assertEqual("recovered", store.snapshot()[0].state)
            store.close()

    def test_due_health_without_an_exact_member_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(
                Path(directory) / "recovery.sqlite"
            )
            store.record(
                ProviderRecovery.failed(
                    connection="google", adapter="google_books", scope="books",
                    generation=1, failure="transient", now=0, retry_after=1,
                ),
                occurred_at=0,
            )
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda *_args: self.fail("provider must not run"),
                classify_failure=lambda _: ("transient", None),
            )

            self.assertEqual("exhausted", runner.run_one(now=1, token="owner").state)
            self.assertEqual("exhausted", store.snapshot()[0].state)
            self.assertNotIn(
                "recovered",
                [row[0] for row in store._connection.execute(
                    "SELECT state FROM provider_recovery_events"
                )],
            )
            store.close()

    def test_interrupted_fanout_does_not_repeat_the_completed_canary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            calls: list[str] = []
            def retry(_, member, __):
                calls.append(member.member_id)
                if member.member_id == "sibling-a":
                    raise KeyboardInterrupt()
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1, retry=retry,
                classify_failure=lambda _: ("transient", None),
            )
            runner.enqueue((self._job("canary"), self._job("sibling-a"), self._job("sibling-b")))
            with self.assertRaises(KeyboardInterrupt):
                runner.run_one(now=1, token="owner")
            # The canary job is durably complete while the scope lease remains
            # in fanout state.  A new claimant waits for that scope lease to
            # expire, then continues without recontacting the canary.
            resumed: list[str] = []
            next_runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda _, member, __: resumed.append(member.member_id),
                classify_failure=lambda _: ("transient", None),
            )
            self.assertEqual("idle", next_runner.run_one(now=62, token="next").state)
            self.assertEqual("recovered", next_runner.run_one(now=302, token="next-later").state)
            self.assertEqual(["sibling-a", "sibling-b"], resumed)
            self.assertEqual(["canary", "sibling-a"], calls)
            store.close()

    def test_activation_runner_failure_has_zero_fanout_and_schedules_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            calls: list[str] = []
            def retry(_, member, __):
                calls.append(member.source_report_id)
                raise RuntimeError("unavailable")
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=retry, classify_failure=lambda _: ("transient", None),
            )
            runner.enqueue((self._job("canary"), self._job("sibling")))
            self.assertEqual("waiting", runner.run_one(now=1, token="owner").state)
            self.assertEqual(["report-canary"], calls)
            current = store.snapshot()[0]
            self.assertEqual(("waiting", 2, 14_401), (current.state, current.attempt, current.next_eligible_at))
            store.close()

    def test_target_hold_canary_restores_exact_health_job_and_event_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            store.enqueue_jobs((self._job("canary"),))
            before = tuple(store._connection.execute("SELECT * FROM provider_recovery_health")), tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")), tuple(store._connection.execute("SELECT * FROM provider_recovery_events"))
            samples = 0
            def blocked_targets() -> frozenset[str]:
                nonlocal samples
                samples += 1
                return frozenset() if samples == 1 else frozenset(("primary",))
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda *_: (_ for _ in ()).throw(RuntimeError("target held")),
                classify_failure=lambda _: ("target_held", None),
                blocked_targets=blocked_targets,
            )
            self.assertEqual("target_held", runner.run_one(now=1, token="owner").state)
            after = tuple(store._connection.execute("SELECT * FROM provider_recovery_health")), tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")), tuple(store._connection.execute("SELECT * FROM provider_recovery_events"))
            self.assertEqual(before, after)
            self.assertEqual(1, samples)
            self.assertIsNotNone(store._connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='provider_recovery_events_no_delete'").fetchone())
            store.close()

    def test_target_hold_fanout_restores_exact_health_job_and_event_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            store.enqueue_jobs((self._job("canary"), self._job("sibling")))
            claimed = store.claim_one(generation=1, now=1, token="owner")
            assert claimed is not None
            canary = store.claim_job(recovery=claimed, token="owner", now=1)
            assert canary is not None
            store.authorize_fanout(job=canary, token="owner", recovery=claimed, now=1)
            store._connection.execute("UPDATE provider_recovery_health SET claim_token='expired',claim_expires_at=1")
            before = tuple(store._connection.execute("SELECT * FROM provider_recovery_health")), tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")), tuple(store._connection.execute("SELECT * FROM provider_recovery_events"))
            samples = 0
            def blocked_targets() -> frozenset[str]:
                nonlocal samples
                samples += 1
                return frozenset() if samples == 1 else frozenset(("primary",))
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda *_: (_ for _ in ()).throw(RuntimeError("target held")),
                classify_failure=lambda _: ("target_held", None),
                blocked_targets=blocked_targets,
            )
            self.assertEqual("target_held", runner.run_one(now=2, token="owner").state)
            after = tuple(store._connection.execute("SELECT * FROM provider_recovery_health")), tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")), tuple(store._connection.execute("SELECT * FROM provider_recovery_events"))
            self.assertEqual(before, after)
            self.assertEqual(1, samples)
            store.close()

    def test_non_target_outcomes_append_checking_before_the_terminal_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            store.enqueue_jobs((self._job("canary"),))
            success = ScopedProviderRecoveryRunner(
                store=store, generation=1, retry=lambda *_: None,
                classify_failure=lambda _: ("transient", None),
            )
            self.assertEqual("recovered", success.run_one(now=1, token="success").state)
            states = [row[0] for row in store._connection.execute("SELECT state FROM provider_recovery_events ORDER BY sequence")]
            self.assertEqual(["waiting", "checking", "fanout", "recovered"], states)
            store.close()

        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            store.enqueue_jobs((self._job("canary"),))
            failure = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda *_: (_ for _ in ()).throw(RuntimeError("transient")),
                classify_failure=lambda _: ("transient", None),
            )
            self.assertEqual("waiting", failure.run_one(now=1, token="failure").state)
            states = [row[0] for row in store._connection.execute("SELECT state FROM provider_recovery_events ORDER BY sequence")]
            self.assertEqual(["waiting", "checking", "waiting"], states)
            store.close()

    def test_blocked_target_emits_nothing_then_recovers_on_a_later_tick(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            store.enqueue_jobs((self._job("canary"),))
            blocked = frozenset(("primary",))
            calls: list[str] = []
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda _, job, __: calls.append(job.member_id),
                classify_failure=lambda _: ("transient", None),
                blocked_targets=lambda: blocked,
            )
            before = tuple(store._connection.execute("SELECT * FROM provider_recovery_health")), tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")), tuple(store._connection.execute("SELECT * FROM provider_recovery_events"))
            self.assertEqual("idle", runner.run_one(now=1, token="blocked").state)
            after = tuple(store._connection.execute("SELECT * FROM provider_recovery_health")), tuple(store._connection.execute("SELECT * FROM provider_recovery_jobs")), tuple(store._connection.execute("SELECT * FROM provider_recovery_events"))
            self.assertEqual(before, after)
            self.assertEqual([], calls)
            blocked = frozenset()
            self.assertEqual("recovered", runner.run_one(now=2, token="recovered").state)
            self.assertEqual(["canary"], calls)
            store.close()

    def test_blocked_active_member_releases_mixed_scope_after_open_member_exhausts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(connection="google", adapter="google_books", scope="books", generation=1, failure="transient", now=0, retry_after=1), occurred_at=0)
            blocked = self._job("blocked")
            exhausted = replace(self._job("open"), target_connection="secondary", attempt=5)
            store.enqueue_jobs((blocked, exhausted))
            held = frozenset(("primary",))
            calls: list[str] = []
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda _, job, __: calls.append(job.member_id),
                classify_failure=lambda _: ("transient", None),
                blocked_targets=lambda: held,
            )
            self.assertEqual("idle", runner.run_one(now=1, token="mixed").state)
            self.assertEqual(("waiting", 1, 1), (
                store.snapshot()[0].state, store.snapshot()[0].attempt,
                store.snapshot()[0].next_eligible_at,
            ))
            self.assertEqual(
                [("blocked", "queued", 0), ("open", "failed", 5)],
                store._connection.execute("SELECT member_id,state,attempt FROM provider_recovery_jobs ORDER BY member_id").fetchall(),
            )
            self.assertEqual(["waiting"], [
                row[0] for row in store._connection.execute("SELECT state FROM provider_recovery_events ORDER BY sequence")
            ])
            held = frozenset()
            self.assertEqual("exhausted", runner.run_one(now=2, token="released").state)
            self.assertEqual(["blocked"], calls)
            self.assertEqual(["waiting", "checking", "fanout", "exhausted"], [
                row[0] for row in store._connection.execute("SELECT state FROM provider_recovery_events ORDER BY sequence")
            ])
            store.close()

    def test_attempt_five_job_is_never_persisted_as_attempt_six(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            store.enqueue_jobs((replace(self._job("canary"), attempt=5),))
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda *_: self.fail("exhausted member must not dispatch"),
                classify_failure=lambda _: ("transient", None),
            )
            self.assertEqual("exhausted", runner.run_one(now=1, token="owner").state)
            self.assertEqual(
                [("failed", 5)],
                store._connection.execute(
                    "SELECT state,attempt FROM provider_recovery_jobs"
                ).fetchall(),
            )
            self.assertEqual("exhausted", store.snapshot()[0].state)
            self.assertEqual("idle", runner.run_one(now=302, token="later").state)
            self.assertEqual(
                ["waiting", "checking", "exhausted"],
                [row[0] for row in store._connection.execute("SELECT state FROM provider_recovery_events ORDER BY sequence")],
            )
            store.close()

    def test_credential_failure_is_dormant_not_requeued(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            store.enqueue_jobs((self._job("canary"),))
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda *_: (_ for _ in ()).throw(CredentialError("credential")),
                classify_failure=lambda error: (
                    "authentication" if isinstance(error, CredentialError) else "transient", None
                ),
            )
            self.assertEqual("waiting", runner.run_one(now=1, token="owner").state)
            self.assertEqual("dormant", store.snapshot()[0].state)
            self.assertIsNone(store.claim_one(generation=1, now=100_000, token="later"))
            store.close()

    def test_terminal_successor_failure_exhausts_without_requeue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            store.enqueue_jobs((self._job("canary"), self._job("sibling")))
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda *_: (_ for _ in ()).throw(ProviderRetrySuccessorError("terminal")),
                classify_failure=lambda error: (
                    "terminal" if isinstance(error, ProviderRetrySuccessorError) else "transient", None
                ),
            )
            self.assertEqual("exhausted", runner.run_one(now=1, token="owner").state)
            self.assertEqual("exhausted", store.snapshot()[0].state)
            self.assertEqual(
                {"failed"},
                {row[0] for row in store._connection.execute(
                    "SELECT state FROM provider_recovery_jobs"
                )},
            )
            store.close()

    def test_reenqueue_refreshes_current_package_and_invalidates_running_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            original = self._job("member")
            current = replace(
                original, file_identity="current-file", source_report_id="current-report",
                package_report_id="current-package", lane_ids='["current"]',
            )
            self.assertEqual(1, store.enqueue_jobs((original,)))
            claimed = store.claim_one(generation=1, now=1, token="owner")
            assert claimed is not None
            running = store.claim_job(recovery=claimed, token="owner", now=1)
            assert running is not None
            self.assertEqual(0, store.enqueue_jobs((original,)))
            self.assertEqual(
                ("running", 1, "owner"),
                store._connection.execute(
                    "SELECT state,attempt,claim_token FROM provider_recovery_jobs"
                ).fetchone(),
            )
            self.assertEqual(1, store.enqueue_jobs((current,)))
            self.assertEqual(
                ("current-file", "current-report", "current-package", '["current"]', "queued", 0),
                store._connection.execute(
                    "SELECT file_identity,source_report_id,package_report_id,lane_ids,state,attempt "
                    "FROM provider_recovery_jobs"
                ).fetchone(),
            )
            with self.assertRaises(ProviderRecoveryStoreError):
                store.complete_job(job=running, token="owner", now=1, success=True)
            refreshed = store.claim_job(recovery=claimed, token="owner", now=1)
            assert refreshed is not None
            store.complete_job(job=refreshed, token="owner", now=1, success=True)
            self.assertEqual(0, store.enqueue_jobs((current,)))
            self.assertEqual(
                ("complete", 1, None),
                store._connection.execute(
                    "SELECT state,attempt,claim_token FROM provider_recovery_jobs"
                ).fetchone(),
            )
            store.close()

    def test_rejected_scope_claim_rolls_back_before_valid_claimant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            store.enqueue_jobs((self._job("canary"),))
            claimed = store.claim_one(generation=1, now=1, token="owner")
            assert claimed is not None
            with self.assertRaises(ProviderRecoveryStoreError):
                store.claim_job(recovery=claimed, token="other", now=1)
            self.assertFalse(store._connection.in_transaction)
            self.assertIsNotNone(store.claim_job(recovery=claimed, token="owner", now=1))
            store.close()

    def test_generation_reconciliation_keeps_current_duplicate_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            stale = self._job("member")
            current = replace(
                stale, generation=2, source_report_id="current-report",
                package_report_id="current-package",
            )
            store.enqueue_jobs((stale,))
            store.enqueue_jobs((current,))
            self.assertEqual(1, store.reconcile_generation(generation=2, now=2))
            self.assertEqual(
                [(2, "current-report", "current-package")],
                store._connection.execute(
                    "SELECT generation,source_report_id,package_report_id "
                    "FROM provider_recovery_jobs"
                ).fetchall(),
            )
            self.assertEqual("waiting", store.snapshot()[0].state)
            store.close()

    def test_shared_failure_during_fanout_stops_uncontacted_siblings_and_reopens_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            calls: list[str] = []
            def retry(_, member, __):
                calls.append(member.member_id)
                if member.member_id == "sibling-a":
                    raise RuntimeError("rate limited")
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1, retry=retry,
                classify_failure=lambda _: ("rate_limited", 19),
            )
            runner.enqueue((self._job("canary"), self._job("sibling-a"), self._job("sibling-b")))
            result = runner.run_one(now=1, token="owner")
            self.assertEqual(("waiting", 0), (result.state, result.fanout))
            self.assertEqual(["canary", "sibling-a"], calls)
            health = store.snapshot()[0]
            self.assertEqual(("waiting", 2, 20), (
                health.state, health.attempt, health.next_eligible_at,
            ))
            states = dict(store._connection.execute(
                "SELECT member_id,state FROM provider_recovery_jobs"
            ).fetchall())
            self.assertEqual(
                {"canary": "complete", "sibling-a": "queued", "sibling-b": "queued"},
                states,
            )
            store.close()

    def test_nonretryable_canary_exhausts_scope_without_sibling_egress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            calls: list[str] = []
            def retry(_, member, __):
                calls.append(member.member_id)
                raise RuntimeError("disabled")
            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1, retry=retry,
                classify_failure=lambda _: ("disabled", None),
            )
            runner.enqueue((self._job("canary"), self._job("sibling")))
            self.assertEqual("exhausted", runner.run_one(now=1, token="owner").state)
            self.assertEqual(["canary"], calls)
            self.assertEqual("exhausted", store.snapshot()[0].state)
            self.assertEqual({"failed"}, {
                row[0] for row in store._connection.execute(
                    "SELECT state FROM provider_recovery_jobs"
                ).fetchall()
            })
            store.close()
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
            worker.start()
            worker.join()
            self.assertEqual([], failures)
            store.reconcile_generation(generation=2, now=1)
            self.assertIsNone(store.claim_one(generation=2, now=1, token="owner"))
            self.assertEqual("dormant", store.snapshot()[0].state)
            store.close()
            with self.assertRaises(ProviderRecoveryStoreError):
                store.activity_snapshot()

    def test_authentication_and_authorization_jobs_wake_only_with_generation(self) -> None:
        for failure in ("authentication", "authorization"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                store = SQLiteProviderRecoveryStore.open(
                    Path(directory) / "recovery.sqlite"
                )
                store.observe_failure(
                    connection="google", adapter="google_books", scope="books",
                    generation=1, failure=failure, now=0,
                )
                store.enqueue_jobs((self._job("canary"),))
                self.assertIsNone(
                    store.claim_one(generation=1, now=10_000, token="too-soon")
                )
                self.assertEqual(1, store.reconcile_generation(generation=2, now=20))
                calls: list[str] = []
                runner = ScopedProviderRecoveryRunner(
                    store=store, generation=2,
                    retry=lambda _, member, __: calls.append(member.member_id),  # noqa: B023 - closure is created and consumed within this iteration
                    classify_failure=lambda _: ("transient", None),
                )
                self.assertEqual("recovered", runner.run_one(now=20).state)
                self.assertEqual(["canary"], calls)
                self.assertEqual(
                    [(2, "complete")],
                    store._connection.execute(
                        "SELECT generation,state FROM provider_recovery_jobs"
                    ).fetchall(),
                )
                store.close()

    def test_terminal_authentication_and_authorization_lanes_create_exact_jobs(self) -> None:
        configuration = SimpleNamespace(
            providers=(SimpleNamespace(name="google", adapter="google_books"),),
            provider_recovery_generation=1,
        )
        for status in (
            ProviderStatus.AUTHENTICATION, ProviderStatus.AUTHORIZATION,
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                report = SimpleNamespace(
                    report_id="report-auth",
                    target=SimpleNamespace(
                        connection="primary", library="books", item="item-1"
                    ),
                    file=SimpleNamespace(identity="file-1"),
                    providers=(SimpleNamespace(
                        provider="google",
                        lanes=(SimpleNamespace(identity="isbn-lane", status=status),),
                    ),),
                )
                jobs = _provider_recovery_jobs(
                    report,
                    package=(None, "report-auth", None, None, "report_only"),
                    configuration=configuration,
                    scope="books",
                )
                self.assertEqual(1, len(jobs))
                self.assertEqual('["isbn-lane"]', jobs[0].lane_ids)
                store = SQLiteProviderRecoveryStore.open(
                    Path(directory) / "recovery.sqlite"
                )
                store.observe_failure(
                    connection="google", adapter="google_books", scope="books",
                    generation=1, failure=status.value, now=0,
                )
                self.assertEqual(1, store.enqueue_jobs(jobs))
                self.assertEqual(
                    [("report-auth", '["isbn-lane"]', "queued")],
                    store._connection.execute(
                        "SELECT source_report_id,lane_ids,state "
                        "FROM provider_recovery_jobs"
                    ).fetchall(),
                )
                store.close()

    def test_stale_canary_retires_only_itself_before_current_canary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(
                Path(directory) / "recovery.sqlite"
            )
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            calls: list[str] = []

            def retry(_, member, __):
                calls.append(member.member_id)
                if member.member_id == "a-stale":
                    raise RuntimeError("stale")

            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1, retry=retry,
                classify_failure=lambda error: (
                    "stale" if str(error) == "stale" else "transient", None
                ),
            )
            runner.enqueue((self._job("a-stale"), self._job("b-current")))
            result = runner.run_one(now=1, token="owner")
            self.assertEqual(("recovered", 0), (result.state, result.fanout))
            self.assertEqual(["a-stale", "b-current"], calls)
            self.assertEqual(
                [("a-stale", "stale"), ("b-current", "complete")],
                store._connection.execute(
                    "SELECT member_id,state FROM provider_recovery_jobs ORDER BY member_id"
                ).fetchall(),
            )
            store.close()

    def test_stage_renewal_prevents_manual_reclaim_beyond_initial_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(
                Path(directory) / "recovery.sqlite"
            )
            store.record(ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=1, failure="transient", now=0, retry_after=1,
            ), occurred_at=0)
            store.enqueue_jobs((self._job("canary"),))
            clock = [1]
            competing: list[str] = []
            competitor = ScopedProviderRecoveryRunner(
                store=store, generation=1,
                retry=lambda _, member, __: competing.append(member.member_id),
                classify_failure=lambda _: ("transient", None),
                clock=lambda: clock[0],
            )

            def retry(_, member, renew):
                self.assertEqual("canary", member.member_id)
                clock[0] = 250
                renew()
                clock[0] = 301
                self.assertEqual("idle", competitor.run_one(now=301).state)
                clock[0] = 400
                renew()

            runner = ScopedProviderRecoveryRunner(
                store=store, generation=1, retry=retry,
                classify_failure=lambda _: ("transient", None),
                clock=lambda: clock[0],
            )
            self.assertEqual("recovered", runner.run_one(now=1, token="owner").state)
            self.assertEqual([], competing)
            store.close()

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
            self.assertIsNone(store.claim_one(generation=1, now=61, token="b"))
            self.assertIsNotNone(store.claim_one(generation=1, now=301, token="b"))
            store.close()
            reopened = SQLiteProviderRecoveryStore.open(path)
            self.assertEqual("checking", reopened.snapshot()[0].state)
            reopened.close()
            connection = sqlite3.connect(path)
            connection.execute("DROP TRIGGER provider_recovery_events_no_delete")
            connection.close()
            with self.assertRaises(ProviderRecoveryStoreError):
                SQLiteProviderRecoveryStore.open(path)

    def test_mixed_lane_health_preserves_provider_connection_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            report = SimpleNamespace(providers=(
                SimpleNamespace(
                    provider="catalogue-a", status=ProviderStatus.CANDIDATES,
                    lanes=(SimpleNamespace(status=ProviderStatus.AUTHENTICATION),),
                ),
                SimpleNamespace(
                    provider="catalogue-b", status=ProviderStatus.UNAVAILABLE,
                    lanes=(),
                ),
                SimpleNamespace(
                    provider="excluded", status=ProviderStatus.DISABLED, lanes=(),
                ),
            ))
            _record_provider_recovery_observations(
                store, report,
                adapters={
                    "catalogue-a": "hardcover", "catalogue-b": "hardcover",
                    "excluded": "open_library",
                },
                scope="books", generation=1, now=0,
            )
            self.assertEqual(
                {
                    ("catalogue-a", "hardcover", "books", "dormant"),
                    ("catalogue-b", "hardcover", "books", "waiting"),
                },
                {
                    (item.connection, item.adapter, item.scope, item.state)
                    for item in store.snapshot(limit=2)
                },
            )
            store.close()

    def test_owner_completion_and_backoff_transition_are_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            first = ProviderRecovery.failed(connection="primary", adapter="hardcover", scope="books", generation=1, failure="transient", now=0, retry_after=2)
            store.record(first, occurred_at=0)
            claimed = store.claim_one(generation=1, now=2, token="owner")
            self.assertIsNotNone(claimed)
            with self.assertRaises(TypeError):
                store.record(ProviderRecovery("primary", "hardcover", "books", 1, "waiting", 2, 6), occurred_at=2, owner_token="owner")  # type: ignore[call-arg]  # reason: the test proves `record` rejects an unknown `owner_token` keyword at runtime with TypeError.
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
            store.record(dormant, occurred_at=0)
            store.record(waiting, occurred_at=0)
            store.record(checking, occurred_at=0)
            self.assertIsNotNone(store.claim_one(generation=1, now=1, token="owner"))
            self.assertEqual(3, store.reconcile_generation(generation=2, now=9))
            rows = {item.connection: item for item in store.snapshot(limit=3)}
            self.assertEqual((2, "dormant", 0, None), (rows["primary"].generation, rows["primary"].state, rows["primary"].attempt, rows["primary"].next_eligible_at))
            self.assertEqual((2, "dormant", 1, None), (rows["other"].generation, rows["other"].state, rows["other"].attempt, rows["other"].next_eligible_at))
            self.assertEqual((2, "dormant", 1, None), (rows["aaa"].generation, rows["aaa"].state, rows["aaa"].attempt, rows["aaa"].next_eligible_at))
            self.assertEqual(
                [("aaa", 2, "dormant", 1, None), ("other", 2, "dormant", 1, None), ("primary", 2, "dormant", 0, None)],
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

    def test_activity_aggregates_128_distinct_deadlines_without_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteProviderRecoveryStore.open(Path(directory) / "recovery.sqlite")
            for number in range(128):
                store.record(
                    ProviderRecovery.failed(
                        connection=f"waiting-{number}", adapter="hardcover",
                        scope="books", generation=1, failure="transient", now=0,
                        retry_after=number + 1,
                    ),
                    occurred_at=0,
                )
            store.observe_failure(
                connection="dormant", adapter="hardcover", scope="books",
                generation=1, failure="authentication", now=0,
            )
            self.assertIsNotNone(store.claim_one(generation=1, now=1, token="owner"))
            self.assertEqual(
                (
                    {"state": "checking", "next_eligible_at": None, "affected_count": 1},
                    {"state": "dormant", "next_eligible_at": None, "affected_count": 1},
                    {"state": "waiting", "next_eligible_at": 2, "affected_count": 127},
                ),
                store.activity_snapshot(),
            )
            store.close()

    def test_orphan_shape_valid_event_rejects_store_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.sqlite"
            store = SQLiteProviderRecoveryStore.open(path)
            store.observe_failure(
                connection="known", adapter="hardcover", scope="books",
                generation=1, failure="authentication", now=0,
            )
            store.close()
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "INSERT INTO provider_recovery_events(connection,adapter,scope,generation,state,attempt,next_eligible_at,occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                ("orphan", "hardcover", "books", 1, "dormant", 0, None, 0),
            )
            connection.commit()
            connection.close()
            with self.assertRaises(ProviderRecoveryStoreError):
                SQLiteProviderRecoveryStore.open(path)


class _CommitFailingConnection:
    """Delegate to a real connection while refusing every ``COMMIT``.

    The store's connection is the seam the transaction epilogue must cross, so
    substituting it at construction keeps the failure forced from outside the
    store rather than by replacing one of its own members.
    """

    def __init__(self, connection: sqlite3.Connection, *, fail_commit: bool) -> None:
        self._connection = connection
        self._fail_commit = fail_commit
        self.statements: list[str] = []

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    def execute(
        self, statement: str, parameters: tuple[object, ...] = ()
    ) -> sqlite3.Cursor:
        self.statements.append(statement)
        if self._fail_commit and statement == "COMMIT":
            raise sqlite3.OperationalError("disk I/O error")
        return self._connection.execute(statement, parameters)

    def close(self) -> None:
        self._connection.close()


class TransactionEpilogueTests(unittest.TestCase):
    """Pin the shared commit/rollback epilogue the six write paths reuse."""

    @staticmethod
    def _store_over(
        directory: str, *, generation: int, fail_commit: bool
    ) -> tuple[SQLiteProviderRecoveryStore, _CommitFailingConnection]:
        path = Path(directory) / "recovery.sqlite"
        prepared = SQLiteProviderRecoveryStore.open(path)
        prepared.record(
            ProviderRecovery.failed(
                connection="google", adapter="google_books", scope="books",
                generation=generation, failure="transient", now=0, retry_after=1,
            ),
            occurred_at=0,
        )
        prepared.close()
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        connection.execute("PRAGMA foreign_keys=ON")
        proxy = _CommitFailingConnection(connection, fail_commit=fail_commit)
        return SQLiteProviderRecoveryStore(cast(sqlite3.Connection, proxy)), proxy

    def test_commit_failure_rolls_back_and_reports_persistence_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, proxy = self._store_over(directory, generation=1, fail_commit=True)
            with self.assertRaises(ProviderRecoveryStoreError) as raised:
                store.reconcile_generation(generation=2, now=10)
            self.assertEqual(
                "provider recovery persistence is unavailable", str(raised.exception)
            )
            self.assertIsInstance(raised.exception.__cause__, sqlite3.OperationalError)
            self.assertIn("ROLLBACK", proxy.statements)
            self.assertFalse(proxy.in_transaction)
            store.close()

    def test_store_failure_rolls_back_and_keeps_its_own_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store, proxy = self._store_over(directory, generation=2, fail_commit=False)
            with self.assertRaises(ProviderRecoveryStoreError) as raised:
                store.reconcile_generation(generation=1, now=10)
            self.assertEqual(
                "provider recovery generation is stale", str(raised.exception)
            )
            self.assertIsNone(raised.exception.__cause__)
            self.assertIn("ROLLBACK", proxy.statements)
            self.assertNotIn("COMMIT", proxy.statements)
            self.assertFalse(proxy.in_transaction)
            store.close()


def _full_store(directory: str) -> SQLiteProviderRecoveryStore:
    """Open a store whose database cannot grow by one more page.

    ``max_page_count`` produces the same ``SQLITE_FULL`` a full disk produces,
    and SQLite ends the transaction itself on that error rather than leaving it
    open.  That is the state the two inner handlers have to survive: by the
    time they run, the transaction they mean to abandon is already gone.  Tiny
    pages and maximum-length identities make the limit bite on the first write
    rather than on however much slack the last page happens to hold.
    """
    path = Path(directory) / "recovery.sqlite"
    prepared = sqlite3.connect(path, isolation_level=None)
    prepared.execute("PRAGMA page_size=512")
    prepared.execute("VACUUM")
    prepared.close()
    store = SQLiteProviderRecoveryStore.open(path)
    for index in range(60):
        store.record(
            ProviderRecovery("c" * 60 + f"{index:04d}", "a" * 64, "s" * 64, 1, "waiting", 0, 1),
            occurred_at=0,
        )
    pages = store._connection.execute("PRAGMA page_count").fetchone()[0]
    store._connection.execute(f"PRAGMA max_page_count={pages}")
    return store


class InnerTransactionEpilogueTests(unittest.TestCase):
    """Pin the two write paths that abandon their transaction inline.

    ``_claim_one`` and ``_record`` keep a nested handler instead of the shared
    epilogue, so they need the same guard the shared one carries.  Without it
    the ``ROLLBACK`` they issue against an already-closed transaction raises,
    and that failure -- not the one that ended the transaction -- becomes the
    ``__cause__`` the operator reads.
    """

    def test_a_full_database_reports_the_disk_failure_from_a_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = _full_store(directory)
            with self.assertRaises(ProviderRecoveryStoreError) as raised:
                store.claim_one(generation=1, now=10, token="t" * 128)
            self.assertIsInstance(raised.exception.__cause__, sqlite3.OperationalError)
            self.assertEqual("database or disk is full", str(raised.exception.__cause__))
            self.assertFalse(store._connection.in_transaction)
            store.close()

    def test_a_full_database_reports_the_disk_failure_from_a_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = _full_store(directory)
            with self.assertRaises(ProviderRecoveryStoreError) as raised:
                store.record(
                    ProviderRecovery("z" * 64, "a" * 64, "s" * 64, 1, "waiting", 0, 1),
                    occurred_at=1,
                )
            self.assertIsInstance(raised.exception.__cause__, sqlite3.OperationalError)
            self.assertEqual("database or disk is full", str(raised.exception.__cause__))
            self.assertFalse(store._connection.in_transaction)
            store.close()

    def test_a_rejected_claim_argument_still_abandons_its_open_transaction(self) -> None:
        """The other half of the guard: an open transaction is still rolled back.

        ``claim_one`` validates ``blocked_targets`` after ``BEGIN IMMEDIATE``,
        and the ``ProviderRecoveryStoreError`` it raises is caught by neither
        outer handler.  That makes the inner one load-bearing on exactly this
        path, so guarding it must not turn it off.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.sqlite"
            store = SQLiteProviderRecoveryStore.open(path)
            with self.assertRaises(ProviderRecoveryStoreError) as raised:
                store.claim_one(
                    generation=1, now=10, token="owner",
                    blocked_targets=frozenset({"x" * 65}),
                )
            self.assertEqual("provider recovery claim is invalid", str(raised.exception))
            self.assertFalse(store._connection.in_transaction)
            store.close()

    def test_a_committed_claim_survives_a_failure_building_its_return_value(self) -> None:
        """The recorded decision: the lease is kept, and it expires on its own."""
        def _reject(*_: object) -> ProviderRecovery:
            raise ProviderRecoveryError("row is not a valid recovery")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recovery.sqlite"
            store = SQLiteProviderRecoveryStore.open(path)
            store.record(
                ProviderRecovery("google", "google_books", "books", 1, "waiting", 0, 1),
                occurred_at=0,
            )
            with patch.object(recovery_persistence, "ProviderRecovery", _reject):
                with self.assertRaises(ProviderRecoveryStoreError) as raised:
                    store.claim_one(generation=1, now=10, token="owner")
            self.assertIsInstance(raised.exception.__cause__, ProviderRecoveryError)
            self.assertEqual(
                ("checking", "owner"),
                store._connection.execute(
                    "SELECT state,claim_token FROM provider_recovery_health"
                ).fetchone(),
            )
            self.assertFalse(store._connection.in_transaction)
            self.assertIsNotNone(store.claim_one(generation=1, now=1_000, token="later"))
            store.close()
