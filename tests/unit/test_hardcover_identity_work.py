from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mechanical_librarian.adapters.persistence.hardcover_identity_work import (
    IdentityWorkPersistenceError,
    SQLiteHardcoverIdentityWorkRepository,
    StaleIdentityWorkFence,
)
from mechanical_librarian.application.hardcover_identity import (
    HardcoverIdentityWorkPersistence,
    ManageHardcoverIdentityWork,
)
from mechanical_librarian.domain.hardcover_identity_work import (
    LEGAL_TRANSITIONS,
    IdentityWorkOutcome,
    IdentityWorkReference,
    IdentityWorkState,
    validate_transition,
)
from mechanical_librarian.infrastructure.persistence.sqlite import (
    MIGRATIONS,
    SchemaError,
    connect,
    migrate,
    migrate_to_version,
)


WORK_KEY = "1" * 64
OTHER_WORK_KEY = "2" * 64
ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


class HardcoverIdentityWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite"
        self.connection = connect(self.path)
        self.addCleanup(self.connection.close)
        self.assertEqual(migrate(self.connection), 9)
        self.repository = SQLiteHardcoverIdentityWorkRepository(self.connection)

    def accept(self, *, key: str = WORK_KEY, when: datetime = ORIGIN):
        return self.repository.accept(key, when)

    def started_claim(self, *, key: str = WORK_KEY):
        accepted = self.accept(key=key)
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        started = self.repository.renew_and_start(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=2),
            gate_owned=True,
        )
        return accepted, started

    def test_domain_transition_graph_is_closed(self) -> None:
        for before in IdentityWorkState:
            for after in IdentityWorkState:
                with self.subTest(before=before, after=after):
                    if (before, after) in LEGAL_TRANSITIONS:
                        validate_transition(before, after)
                    else:
                        with self.assertRaisesRegex(ValueError, "not allowed"):
                            validate_transition(before, after)
        with self.assertRaises(TypeError):
            validate_transition("pending", IdentityWorkState.CLAIMED)  # type: ignore[arg-type]

    def test_accept_commits_generation_candidate_and_deadline_atomically(self) -> None:
        status = self.accept()
        self.assertEqual(status.outcome, IdentityWorkOutcome.IN_PROGRESS)
        self.assertEqual(status.reference, IdentityWorkReference(WORK_KEY, 1))
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence,attempt_count,aggregate_started_at,aggregate_deadline FROM hardcover_identity_generations"
            ).fetchone(),
            (
                "pending",
                0,
                0,
                "2026-01-01T00:00:00.000000Z",
                "2026-01-01T00:00:30.000000Z",
            ),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT candidate FROM hardcover_identity_candidates"
            ).fetchall(),
            [(1,)],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT kind,from_state,to_state FROM hardcover_identity_events"
            ).fetchall(),
            [("accepted", None, "pending")],
        )

    def test_accept_failure_rolls_back_every_first_generation_record(self) -> None:
        self.connection.execute(
            "CREATE TRIGGER test_candidate_failure BEFORE INSERT ON hardcover_identity_candidates BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "accepted"):
            self.accept()
        for table in (
            "hardcover_identity_generations",
            "hardcover_identity_candidates",
            "hardcover_identity_events",
        ):
            self.assertEqual(
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone(),
                (0,),
            )

    def test_claim_reserves_and_only_start_consumes_one_attempt(self) -> None:
        accepted = self.accept()
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        self.assertEqual((claim.fence, claim.candidate), (1, 1))
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt_count FROM hardcover_identity_generations"
            ).fetchone(),
            (0,),
        )
        started = self.repository.renew_and_start(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=10),
            gate_owned=True,
        )
        self.assertEqual(
            started.lease_expires_at, ORIGIN + timedelta(seconds=25)
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt,fence,candidate,outcome FROM hardcover_identity_attempts"
            ).fetchall(),
            [(1, 1, 1, None)],
        )

    def test_start_rejects_less_than_six_seconds_and_requires_gate(self) -> None:
        accepted = self.accept()
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "gate"):
            self.repository.claim(
                accepted.reference,
                "worker-a",
                ORIGIN,
                gate_owned=False,
            )
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        started = self.repository.renew_and_start(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=10, microseconds=1),
            gate_owned=True,
        )
        self.assertEqual(started.lease_expires_at, ORIGIN + timedelta(seconds=25, microseconds=1))

        late = self.accept(key=OTHER_WORK_KEY)
        late_claim = self.repository.claim(
            late.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=20),
            gate_owned=True,
        )
        exhausted = self.repository.renew_and_start(
            late_claim,
            "worker-a",
            ORIGIN + timedelta(seconds=24, microseconds=1),
            gate_owned=True,
        )
        self.assertEqual(exhausted.outcome, IdentityWorkOutcome.RETRY_EXHAUSTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt_count,state FROM hardcover_identity_generations WHERE work_key=?",
                (OTHER_WORK_KEY,),
            ).fetchone(),
            (0, "failed"),
        )

    def test_publication_failure_rolls_back_attempt_result_observation_and_pointer(self) -> None:
        _, claim = self.started_claim()
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "injected"):
            self.repository.publish_matched(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                {"outcome": "matched", "rows": 2},
                gate_owned=True,
                fail_before_commit=True,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM hardcover_identity_generations"
            ).fetchone(),
            ("claimed",),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT resolved_at,outcome FROM hardcover_identity_attempts"
            ).fetchone(),
            (None, None),
        )
        for table in (
            "hardcover_identity_results",
            "hardcover_identity_observations",
            "current_hardcover_identity",
        ):
            self.assertEqual(
                self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone(),
                (0,),
            )

    def test_restart_recovery_advances_fence_and_rejects_aba_owner(self) -> None:
        accepted, old_claim = self.started_claim()
        self.connection.close()
        self.connection = connect(self.path)
        self.addCleanup(self.connection.close)
        self.repository = SQLiteHardcoverIdentityWorkRepository(self.connection)
        self.assertEqual(
            self.repository.recover_interrupted(
                old_claim, ORIGIN + timedelta(seconds=18), gate_owned=True
            ),
            2,
        )
        with self.assertRaises(StaleIdentityWorkFence):
            self.repository.publish_matched(
                old_claim,
                "worker-a",
                ORIGIN + timedelta(seconds=19),
                {"outcome": "matched"},
                gate_owned=True,
            )
        retry = self.repository.claim(
            accepted.reference,
            "worker-b",
            ORIGIN + timedelta(seconds=19),
            gate_owned=True,
        )
        self.assertEqual((retry.fence, retry.candidate), (2, 2))
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt,fence,outcome FROM hardcover_identity_attempts"
            ).fetchall(),
            [(1, 1, "interrupted")],
        )

    def test_recovery_rejects_live_lease_and_reclaims_prestart_candidate(self) -> None:
        accepted = self.accept()
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "not expired"):
            self.repository.recover_interrupted(
                claim, ORIGIN + timedelta(seconds=2), gate_owned=True
            )
        self.assertEqual(
            self.repository.recover_interrupted(
                claim, ORIGIN + timedelta(seconds=17), gate_owned=True
            ),
            2,
        )
        reclaimed = self.repository.claim(
            accepted.reference,
            "worker-b",
            ORIGIN + timedelta(seconds=17),
            gate_owned=True,
        )
        self.assertEqual((reclaimed.candidate, reclaimed.fence), (1, 2))

    def test_matched_currentness_includes_exact_24_hour_boundary(self) -> None:
        _, claim = self.started_claim()
        published_at = ORIGIN + timedelta(seconds=3)
        self.repository.publish_matched(
            claim,
            "worker-a",
            published_at,
            {"outcome": "matched", "rows": 2},
            gate_owned=True,
        )
        exact = self.repository.accept(
            WORK_KEY, published_at + timedelta(hours=24)
        )
        self.assertEqual(
            (exact.outcome, exact.reference.generation),
            (IdentityWorkOutcome.MATCHED, 1),
        )
        stale = self.repository.accept(
            WORK_KEY, published_at + timedelta(hours=24, microseconds=1)
        )
        repeated = self.repository.accept(
            WORK_KEY, published_at + timedelta(hours=24, microseconds=2)
        )
        self.assertEqual(
            (stale.outcome, stale.reference.generation, stale.candidate),
            (IdentityWorkOutcome.STALE, 2, None),
        )
        self.assertEqual(
            (repeated.outcome, repeated.reference.generation, repeated.candidate),
            (IdentityWorkOutcome.STALE, 2, 1),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT generation,status FROM current_hardcover_identity"
            ).fetchone(),
            (1, "expired"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*),MIN(aggregate_started_at),MAX(aggregate_deadline) FROM hardcover_identity_generations WHERE state='pending'"
            ).fetchone(),
            (
                1,
                "2026-01-02T00:00:03.000002Z",
                "2026-01-02T00:00:33.000002Z",
            ),
        )

    def test_append_only_records_and_finalized_attempts_cannot_be_rewritten(self) -> None:
        _, claim = self.started_claim()
        self.repository.publish_matched(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            {"outcome": "matched"},
            gate_owned=True,
        )
        for statement in (
            "UPDATE hardcover_identity_candidates SET candidate=2",
            "DELETE FROM hardcover_identity_events",
            "UPDATE hardcover_identity_results SET outcome='failed'",
            "UPDATE hardcover_identity_observations SET canonical_observation='{}'",
            "UPDATE hardcover_identity_attempts SET outcome='failed'",
        ):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(statement)

    def test_cancel_is_terminal_and_preserves_no_current_pointer(self) -> None:
        accepted = self.accept()
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(microseconds=1),
            gate_owned=True,
        )
        self.repository.cancel(
            claim, "worker-a", ORIGIN + timedelta(seconds=1), gate_owned=True
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM hardcover_identity_generations"
            ).fetchone(),
            ("cancelled",),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT outcome FROM hardcover_identity_results"
            ).fetchone(),
            ("cancelled",),
        )
        with self.assertRaises(ValueError):
            self.repository.cancel(
                claim, "worker-a", ORIGIN + timedelta(seconds=2), gate_owned=True
            )

    def test_cancel_finalizes_a_started_attempt_once(self) -> None:
        accepted, claim = self.started_claim()
        self.repository.cancel(
            claim, "worker-a", ORIGIN + timedelta(seconds=3), gate_owned=True
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT resolved_at,outcome FROM hardcover_identity_attempts WHERE fence=?",
                (claim.fence,),
            ).fetchone(),
            ("2026-01-01T00:00:03.000000Z", "cancelled"),
        )

    def test_equal_observations_are_scoped_to_work_and_generation(self) -> None:
        _, first = self.started_claim()
        _, second = self.started_claim(key=OTHER_WORK_KEY)
        for claim in (first, second):
            self.repository.publish_matched(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                {"outcome": "matched"},
                gate_owned=True,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(DISTINCT observation_id),COUNT(*) FROM hardcover_identity_observations"
            ).fetchone(),
            (2, 2),
        )

    def test_backward_wall_clock_is_rejected_without_state_change(self) -> None:
        accepted = self.accept(when=ORIGIN + timedelta(seconds=10))
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "chronology"):
            self.repository.claim(
                accepted.reference,
                "worker-a",
                ORIGIN + timedelta(seconds=9),
                gate_owned=True,
            )
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "chronology"):
            self.repository.accept(WORK_KEY, ORIGIN + timedelta(seconds=9))
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence,attempt_count FROM hardcover_identity_generations"
            ).fetchone(),
            ("pending", 0, 0),
        )

    def test_corrupt_generation_state_is_rejected_value_free(self) -> None:
        accepted = self.accept()
        self.connection.execute("PRAGMA ignore_check_constraints=ON")
        self.connection.execute(
            "UPDATE hardcover_identity_generations SET state='unknown' WHERE work_key=?",
            (WORK_KEY,),
        )
        self.connection.execute("PRAGMA ignore_check_constraints=OFF")
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "corrupt"):
            self.repository.claim(
                accepted.reference,
                "worker-a",
                ORIGIN + timedelta(seconds=1),
                gate_owned=True,
            )

    def test_two_acceptors_reuse_one_active_generation(self) -> None:
        other_connection = connect(self.path)
        self.addCleanup(other_connection.close)
        other = SQLiteHardcoverIdentityWorkRepository(other_connection)
        first = self.accept()
        second = other.accept(WORK_KEY, ORIGIN + timedelta(microseconds=1))
        self.assertEqual(first.reference, second.reference)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_generations"
            ).fetchone(),
            (1,),
        )

    def test_stale_claim_cannot_cancel_a_newer_owner_fence(self) -> None:
        accepted = self.accept()
        old = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        self.repository.recover_interrupted(
            old, ORIGIN + timedelta(seconds=17), gate_owned=True
        )
        current = self.repository.claim(
            accepted.reference,
            "worker-b",
            ORIGIN + timedelta(seconds=17),
            gate_owned=True,
        )
        with self.assertRaises(StaleIdentityWorkFence):
            self.repository.cancel(
                old, "worker-a", ORIGIN + timedelta(seconds=18), gate_owned=True
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence,lease_owner FROM hardcover_identity_generations"
            ).fetchone(),
            ("claimed", 2, "worker-b"),
        )
        self.repository.cancel(
            current, "worker-b", ORIGIN + timedelta(seconds=18), gate_owned=True
        )

    def test_retry_claim_requires_the_unique_current_due_schedule(self) -> None:
        accepted = self.accept()
        self.connection.execute(
            "UPDATE hardcover_identity_generations SET state='retry_wait',fence=1 WHERE work_key=?",
            (WORK_KEY,),
        )
        before = self.connection.total_changes
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "schedule"):
            self.repository.claim(
                accepted.reference,
                "worker-a",
                ORIGIN + timedelta(seconds=1),
                gate_owned=True,
            )
        self.assertEqual(self.connection.total_changes, before)
        self.connection.execute(
            "INSERT INTO hardcover_identity_retry_schedules(work_key,generation,fence,candidate,due_at,reason,created_at) VALUES(?,?,1,1,?,'retryable',?)",
            (
                WORK_KEY,
                1,
                "2026-01-01T00:00:10.000000Z",
                "2026-01-01T00:00:01.000000Z",
            ),
        )
        before = self.connection.total_changes
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "not due"):
            self.repository.claim(
                accepted.reference,
                "worker-a",
                ORIGIN + timedelta(seconds=9, microseconds=999_999),
                gate_owned=True,
            )
        self.assertEqual(self.connection.total_changes, before)
        due = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=10),
            gate_owned=True,
        )
        self.assertEqual((due.candidate, due.fence), (1, 1))

    def test_retryable_failure_publication_rolls_back_or_schedules_exact_next(self) -> None:
        accepted, claim = self.started_claim()
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "injected"):
            self.repository.publish_failed(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                retryable=True,
                gate_owned=True,
                fail_before_commit=True,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence,attempt_count FROM hardcover_identity_generations"
            ).fetchone(),
            ("claimed", 1, 1),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT resolved_at,outcome FROM hardcover_identity_attempts"
            ).fetchone(),
            (None, None),
        )
        status = self.repository.publish_failed(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            retryable=True,
            gate_owned=True,
        )
        self.assertEqual(
            (status.outcome, status.candidate),
            (IdentityWorkOutcome.IN_PROGRESS, 2),
        )
        retry = self.repository.claim(
            accepted.reference,
            "worker-b",
            ORIGIN + timedelta(seconds=3),
            gate_owned=True,
        )
        self.assertEqual((retry.candidate, retry.fence), (2, 2))

    def test_third_interrupted_attempt_commits_terminal_failed_result(self) -> None:
        accepted, first = self.started_claim()
        self.repository.publish_failed(
            first,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            retryable=True,
            gate_owned=True,
        )
        second = self.repository.claim(
            accepted.reference,
            "worker-b",
            ORIGIN + timedelta(seconds=4),
            gate_owned=True,
        )
        second = self.repository.renew_and_start(
            second,
            "worker-b",
            ORIGIN + timedelta(seconds=5),
            gate_owned=True,
        )
        self.repository.publish_failed(
            second,
            "worker-b",
            ORIGIN + timedelta(seconds=6),
            retryable=True,
            gate_owned=True,
        )
        third = self.repository.claim(
            accepted.reference,
            "worker-c",
            ORIGIN + timedelta(seconds=7),
            gate_owned=True,
        )
        third = self.repository.renew_and_start(
            third,
            "worker-c",
            ORIGIN + timedelta(seconds=8),
            gate_owned=True,
        )
        exhausted = self.repository.recover_interrupted(
            third, ORIGIN + timedelta(seconds=24), gate_owned=True
        )
        self.assertEqual(exhausted.outcome, IdentityWorkOutcome.RETRY_EXHAUSTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence,attempt_count FROM hardcover_identity_generations"
            ).fetchone(),
            ("failed", 3, 3),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT outcome FROM hardcover_identity_results"
            ).fetchone(),
            ("failed",),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT outcome FROM hardcover_identity_attempts WHERE attempt=3"
            ).fetchone(),
            ("interrupted",),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_retry_schedules WHERE candidate=3"
            ).fetchone(),
            (1,),
        )

    def test_publication_after_aggregate_deadline_fails_terminally(self) -> None:
        accepted = self.accept()
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=20),
            gate_owned=True,
        )
        claim = self.repository.renew_and_start(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=20),
            gate_owned=True,
        )
        result = self.repository.publish_matched(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=30, microseconds=1),
            {"outcome": "matched"},
            gate_owned=True,
        )
        self.assertEqual(result.outcome, IdentityWorkOutcome.PERSISTENCE_FAILURE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM hardcover_identity_generations"
            ).fetchone(),
            ("failed",),
        )

    def test_application_service_uses_the_application_owned_protocol(self) -> None:
        self.assertIsInstance(self.repository, HardcoverIdentityWorkPersistence)
        service = ManageHardcoverIdentityWork(self.repository)
        status = service.accept(WORK_KEY, ORIGIN)
        claim = service.claim(
            status.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        self.assertEqual((claim.candidate, claim.fence), (1, 1))


class HardcoverIdentityMigrationTests(unittest.TestCase):
    def test_schema_eight_upgrades_additively_and_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate_to_version(connection, 8), 8)
            connection.execute(
                "INSERT INTO instance_metadata(key,value) VALUES('schema-eight-sentinel','preserved')"
            )
            self.assertEqual(migrate(connection), 9)
            self.assertEqual(migrate(connection), 9)
            self.assertEqual(
                connection.execute(
                    "SELECT value FROM instance_metadata WHERE key='schema-eight-sentinel'"
                ).fetchone(),
                ("preserved",),
            )
            self.assertEqual(
                connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version=9"
                ).fetchone(),
                (MIGRATIONS[8].checksum,),
            )

    def test_interrupted_schema_nine_migration_rolls_back_and_retries(self) -> None:
        class FailingConnection:
            def __init__(self, connection: sqlite3.Connection, fail_at: str) -> None:
                self.connection = connection
                self.fail_at = fail_at
                self.failed = False

            def execute(self, statement: str, parameters=()):
                if statement == self.fail_at and not self.failed:
                    self.failed = True
                    raise sqlite3.OperationalError("injected schema-nine interruption")
                return self.connection.execute(statement, parameters)

            def __getattr__(self, name: str):
                return getattr(self.connection, name)

        migration = MIGRATIONS[8]
        points = (0, len(migration.statements) // 2, len(migration.statements) - 1)
        with tempfile.TemporaryDirectory() as directory:
            for point in points:
                with self.subTest(point=point):
                    path = Path(directory) / f"failure-{point}.sqlite"
                    connection = connect(path)
                    try:
                        self.assertEqual(migrate_to_version(connection, 8), 8)
                        with self.assertRaisesRegex(SchemaError, "rolled back"):
                            migrate(FailingConnection(connection, migration.statements[point]))  # type: ignore[arg-type]
                        self.assertEqual(
                            connection.execute(
                                "SELECT COUNT(*) FROM schema_migrations WHERE version=9"
                            ).fetchone(),
                            (0,),
                        )
                        self.assertEqual(
                            connection.execute(
                                "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'hardcover_identity_%' OR name='current_hardcover_identity'"
                            ).fetchone(),
                            (0,),
                        )
                        self.assertEqual(migrate(connection), 9)
                    finally:
                        connection.close()


if __name__ == "__main__":
    unittest.main()
