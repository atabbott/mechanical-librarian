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
    IdentityMatchOutcome,
    IdentityWorkClaim,
    IdentityWorkOutcome,
    IdentityWorkObservation,
    IdentityWorkReference,
    IdentityWorkState,
    IdentityWorkStatus,
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
MATCHED = IdentityWorkObservation(IdentityMatchOutcome.MATCHED, 2)


class ProtocolPersistenceCollaborator:
    def __init__(self) -> None:
        reference = IdentityWorkReference(WORK_KEY, 1)
        self.accept_result: object = IdentityWorkStatus(
            IdentityWorkOutcome.IN_PROGRESS, reference, 1
        )
        self.claim_result: object = IdentityWorkClaim(
            reference,
            1,
            1,
            ORIGIN + timedelta(seconds=15),
        )
        self.start_result: object = self.claim_result
        self.cancel_result: object = None

    def accept(self, work_key: str, now: datetime):
        return self.accept_result

    def claim(self, reference, owner, now, *, gate_owned):
        return self.claim_result

    def renew_and_start(self, claim, owner, now, *, gate_owned):
        return self.start_result

    def recover_interrupted(self, claim, now, *, gate_owned):
        return claim.fence + 1

    def publish_matched(
        self, claim, owner, now, observation, *, gate_owned, fail_before_commit=False
    ):
        return IdentityWorkStatus(
            IdentityWorkOutcome.MATCHED, claim.reference, claim.candidate
        )

    def publish_failed(
        self, claim, owner, now, *, retryable, gate_owned, fail_before_commit=False
    ):
        return IdentityWorkStatus(
            IdentityWorkOutcome.PERSISTENCE_FAILURE, claim.reference
        )

    def cancel(self, claim, owner, now, *, gate_owned):
        return self.cancel_result


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

    def retry_wait(self):
        accepted, claim = self.started_claim()
        self.repository.publish_failed(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            retryable=True,
            gate_owned=True,
        )
        return accepted

    def candidate_three_retry_wait(self):
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
        return accepted

    def same_timestamp_stale_candidate_three_retry_wait(self):
        accepted, first = self.started_claim()
        self.repository.publish_matched(
            first,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            MATCHED,
            gate_owned=True,
        )
        stale_time = ORIGIN + timedelta(days=1, seconds=4)
        stale = self.repository.accept(WORK_KEY, stale_time)
        self.assertIsNone(stale.candidate)
        reserved = self.repository.accept(WORK_KEY, stale_time)
        self.assertEqual((reserved.reference.generation, reserved.candidate), (2, 1))
        first_retry = self.repository.claim(
            reserved.reference,
            "worker-b",
            stale_time + timedelta(seconds=1),
            gate_owned=True,
        )
        first_retry = self.repository.renew_and_start(
            first_retry,
            "worker-b",
            stale_time + timedelta(seconds=2),
            gate_owned=True,
        )
        self.repository.publish_failed(
            first_retry,
            "worker-b",
            stale_time + timedelta(seconds=3),
            retryable=True,
            gate_owned=True,
        )
        second_retry = self.repository.claim(
            reserved.reference,
            "worker-c",
            stale_time + timedelta(seconds=4),
            gate_owned=True,
        )
        second_retry = self.repository.renew_and_start(
            second_retry,
            "worker-c",
            stale_time + timedelta(seconds=5),
            gate_owned=True,
        )
        self.repository.publish_failed(
            second_retry,
            "worker-c",
            stale_time + timedelta(seconds=6),
            retryable=True,
            gate_owned=True,
        )
        return reserved, stale_time

    def assert_candidate_three_history_rejected(
        self, reference, *, when: datetime = ORIGIN + timedelta(seconds=7)
    ) -> None:
        before = self.connection.execute(
            "SELECT state,fence,attempt_count,lease_owner,lease_expires_at,updated_at "
            "FROM hardcover_identity_generations WHERE work_key=? AND generation=?",
            (reference.work_key, reference.generation),
        ).fetchone()
        claim_count = self.connection.execute(
            "SELECT COUNT(*) FROM hardcover_identity_claims WHERE work_key=? AND generation=?",
            (reference.work_key, reference.generation),
        ).fetchone()
        changes = self.connection.total_changes
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "history"):
            self.repository.claim(
                reference,
                "worker-c",
                when,
                gate_owned=True,
            )
        self.assertEqual(self.connection.total_changes, changes)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence,attempt_count,lease_owner,lease_expires_at,updated_at "
                "FROM hardcover_identity_generations WHERE work_key=? AND generation=?",
                (reference.work_key, reference.generation),
            ).fetchone(),
            before,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_claims WHERE work_key=? AND generation=?",
                (reference.work_key, reference.generation),
            ).fetchone(),
            claim_count,
        )

    def test_closed_observation_has_one_exact_canonical_shape(self) -> None:
        canonical = '{"outcome":"matched","row_count":2}'
        self.assertEqual(MATCHED.to_canonical_json(), canonical)
        self.assertEqual(IdentityWorkObservation.from_canonical_json(canonical), MATCHED)
        for malformed in (
            '{}',
            '{"outcome":"matched","row_count":2,"title":"private"}',
            '{"outcome":"provider","row_count":2}',
            '{"outcome":"matched","row_count":"2"}',
            '{"row_count":2,"outcome":"matched"}',
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                IdentityWorkObservation.from_canonical_json(malformed)

    def test_repository_rejects_untyped_observation_without_publication(self) -> None:
        _, claim = self.started_claim()
        with self.assertRaises(TypeError):
            self.repository.publish_matched(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                {"outcome": "matched", "title": "private"},  # type: ignore[arg-type]
                gate_owned=True,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_results"
            ).fetchone(),
            (0,),
        )

    def test_domain_transition_graph_is_closed(self) -> None:
        self.assertNotIn(
            (IdentityWorkState.PENDING, IdentityWorkState.CANCELLED),
            LEGAL_TRANSITIONS,
        )
        self.assertNotIn(
            (IdentityWorkState.RETRY_WAIT, IdentityWorkState.CANCELLED),
            LEGAL_TRANSITIONS,
        )
        self.assertIn(
            (IdentityWorkState.CLAIMED, IdentityWorkState.CANCELLED),
            LEGAL_TRANSITIONS,
        )
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
                MATCHED,
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
                MATCHED,
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

    def test_candidate_substitution_rejects_every_current_claim_mutation(self) -> None:
        accepted = self.accept()
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        substituted = IdentityWorkClaim(
            claim.reference,
            2,
            claim.fence,
            claim.lease_expires_at,
        )
        before_generation = self.connection.execute(
            "SELECT state,fence,attempt_count,lease_owner,lease_expires_at,updated_at "
            "FROM hardcover_identity_generations"
        ).fetchone()
        before_counts = self.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM hardcover_identity_attempts),"
            "(SELECT COUNT(*) FROM hardcover_identity_results),"
            "(SELECT COUNT(*) FROM hardcover_identity_events)"
        ).fetchone()
        operations = (
            lambda: self.repository.renew_and_start(
                substituted,
                "worker-a",
                ORIGIN + timedelta(seconds=2),
                gate_owned=True,
            ),
            lambda: self.repository.recover_interrupted(
                substituted,
                ORIGIN + timedelta(seconds=17),
                gate_owned=True,
            ),
            lambda: self.repository.cancel(
                substituted,
                "worker-a",
                ORIGIN + timedelta(seconds=2),
                gate_owned=True,
            ),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(
                StaleIdentityWorkFence
            ):
                operation()
            self.assertEqual(
                self.connection.execute(
                    "SELECT state,fence,attempt_count,lease_owner,lease_expires_at,updated_at "
                    "FROM hardcover_identity_generations"
                ).fetchone(),
                before_generation,
            )
            self.assertEqual(
                self.connection.execute(
                    "SELECT "
                    "(SELECT COUNT(*) FROM hardcover_identity_attempts),"
                    "(SELECT COUNT(*) FROM hardcover_identity_results),"
                    "(SELECT COUNT(*) FROM hardcover_identity_events)"
                ).fetchone(),
                before_counts,
            )

    def test_attempt_candidate_must_match_its_immutable_claim(self) -> None:
        accepted = self.accept()
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        self.connection.execute(
            "INSERT INTO hardcover_identity_candidates(work_key,generation,candidate,created_at) "
            "VALUES(?,?,2,?)",
            (WORK_KEY, 1, "2026-01-01T00:00:01.000000Z"),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "INSERT INTO hardcover_identity_attempts("
                "work_key,generation,attempt,fence,candidate,started_at"
                ") VALUES(?,?,?,?,?,?)",
                (
                    WORK_KEY,
                    1,
                    1,
                    claim.fence,
                    2,
                    "2026-01-01T00:00:02.000000Z",
                ),
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_attempts"
            ).fetchone(),
            (0,),
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
            MATCHED,
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

    def test_matched_reuse_reconciles_result_and_closed_observation(self) -> None:
        _, first = self.started_claim()
        _, second = self.started_claim(key=OTHER_WORK_KEY)
        for claim in (first, second):
            self.repository.publish_matched(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                MATCHED,
                gate_owned=True,
            )
        before = self.connection.execute(
            "SELECT work_key,generation,status,current_since,expires_at "
            "FROM current_hardcover_identity ORDER BY work_key"
        ).fetchall()
        self.connection.execute("DROP TRIGGER hardcover_identity_results_no_update")
        self.connection.execute(
            "UPDATE hardcover_identity_results SET fence=2 WHERE work_key=?",
            (WORK_KEY,),
        )
        self.connection.execute(
            "DROP TRIGGER hardcover_identity_observations_no_update"
        )
        self.connection.execute(
            "UPDATE hardcover_identity_observations SET canonical_observation='{}' WHERE work_key=?",
            (OTHER_WORK_KEY,),
        )
        for key in (WORK_KEY, OTHER_WORK_KEY):
            with self.subTest(key=key), self.assertRaisesRegex(
                IdentityWorkPersistenceError, "inconsistent"
            ):
                self.repository.accept(key, ORIGIN + timedelta(seconds=4))
        self.assertEqual(
            self.connection.execute(
                "SELECT work_key,generation,status,current_since,expires_at "
                "FROM current_hardcover_identity ORDER BY work_key"
            ).fetchall(),
            before,
        )

    def test_matched_reuse_reconciles_terminal_generation_timestamp(self) -> None:
        _, claim = self.started_claim()
        self.repository.publish_matched(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            MATCHED,
            gate_owned=True,
        )
        before = self.connection.execute(
            "SELECT generation,status,current_since,expires_at "
            "FROM current_hardcover_identity"
        ).fetchone()
        self.connection.execute(
            "UPDATE hardcover_identity_generations SET updated_at=? WHERE work_key=?",
            ("2026-01-01T00:00:04.000000Z", WORK_KEY),
        )
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "inconsistent"):
            self.repository.accept(WORK_KEY, ORIGIN + timedelta(seconds=5))
        self.assertEqual(
            self.connection.execute(
                "SELECT generation,status,current_since,expires_at "
                "FROM current_hardcover_identity"
            ).fetchone(),
            before,
        )

    def test_append_only_records_and_finalized_attempts_cannot_be_rewritten(self) -> None:
        _, claim = self.started_claim()
        self.repository.publish_matched(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            MATCHED,
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
                MATCHED,
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
            "UPDATE hardcover_identity_generations SET state='pending',fence=0 WHERE work_key=?",
            (WORK_KEY,),
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
            ORIGIN + timedelta(seconds=2),
            gate_owned=True,
        )
        self.assertIsInstance(started, IdentityWorkClaim)
        self.repository.publish_failed(
            started,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            retryable=True,
            gate_owned=True,
        )
        self.connection.execute(
            "DROP TRIGGER hardcover_identity_retry_schedules_no_update"
        )
        self.connection.execute(
            "UPDATE hardcover_identity_retry_schedules SET due_at=? WHERE work_key=? AND generation=1 AND fence=2",
            ("2026-01-01T00:00:10.000000Z", WORK_KEY),
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
        self.assertEqual((due.candidate, due.fence), (2, 2))

    def test_retry_claim_rejects_multiple_current_or_future_schedules(self) -> None:
        accepted = self.retry_wait()
        self.connection.execute(
            "INSERT INTO hardcover_identity_retry_schedules(work_key,generation,fence,candidate,due_at,reason,created_at) "
            "VALUES(?,?,3,2,?,'retryable',?)",
            (
                WORK_KEY,
                1,
                "2026-01-01T00:00:03.000000Z",
                "2026-01-01T00:00:03.000000Z",
            ),
        )
        before = self.connection.execute(
            "SELECT state,fence,lease_owner FROM hardcover_identity_generations"
        ).fetchone()
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "schedule"):
            self.repository.claim(
                accepted.reference,
                "worker-b",
                ORIGIN + timedelta(seconds=4),
                gate_owned=True,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence,lease_owner FROM hardcover_identity_generations"
            ).fetchone(),
            before,
        )

    def test_retry_claim_rejects_noncanonical_reason_and_chronology(self) -> None:
        for corruption in ("reason", "chronology"):
            with self.subTest(corruption=corruption):
                with tempfile.TemporaryDirectory() as directory:
                    connection = connect(Path(directory) / "state.sqlite")
                    try:
                        migrate(connection)
                        repository = SQLiteHardcoverIdentityWorkRepository(connection)
                        accepted = repository.accept(WORK_KEY, ORIGIN)
                        claim = repository.claim(
                            accepted.reference,
                            "worker-a",
                            ORIGIN + timedelta(seconds=1),
                            gate_owned=True,
                        )
                        claim = repository.renew_and_start(
                            claim,
                            "worker-a",
                            ORIGIN + timedelta(seconds=2),
                            gate_owned=True,
                        )
                        repository.publish_failed(
                            claim,
                            "worker-a",
                            ORIGIN + timedelta(seconds=3),
                            retryable=True,
                            gate_owned=True,
                        )
                        connection.execute(
                            "DROP TRIGGER hardcover_identity_retry_schedules_no_update"
                        )
                        if corruption == "reason":
                            connection.execute("PRAGMA ignore_check_constraints=ON")
                            connection.execute(
                                "UPDATE hardcover_identity_retry_schedules SET reason='other'"
                            )
                        else:
                            connection.execute(
                                "UPDATE hardcover_identity_retry_schedules SET due_at=?",
                                ("2026-01-01T00:00:02.000000Z",),
                            )
                        with self.assertRaisesRegex(
                            IdentityWorkPersistenceError, "corrupt"
                        ):
                            repository.claim(
                                accepted.reference,
                                "worker-b",
                                ORIGIN + timedelta(seconds=4),
                                gate_owned=True,
                            )
                        self.assertEqual(
                            connection.execute(
                                "SELECT state,fence,lease_owner FROM hardcover_identity_generations"
                            ).fetchone(),
                            ("retry_wait", 2, None),
                        )
                    finally:
                        connection.close()

    def test_retry_claim_rejects_logically_impossible_candidate_history(self) -> None:
        accepted = self.retry_wait()
        self.connection.execute(
            "DROP TRIGGER hardcover_identity_retry_schedules_no_update"
        )
        self.connection.execute(
            "UPDATE hardcover_identity_retry_schedules SET reason='interrupted'"
        )
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "history"):
            self.repository.claim(
                accepted.reference,
                "worker-b",
                ORIGIN + timedelta(seconds=4),
                gate_owned=True,
            )

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

    def test_candidate_three_claim_reconciles_complete_valid_history(self) -> None:
        accepted = self.candidate_three_retry_wait()
        third = self.repository.claim(
            accepted.reference,
            "worker-c",
            ORIGIN + timedelta(seconds=7),
            gate_owned=True,
        )
        self.assertEqual((third.candidate, third.fence), (3, 3))
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt,fence,candidate,outcome FROM hardcover_identity_attempts ORDER BY attempt"
            ).fetchall(),
            [(1, 1, 1, "failed"), (2, 2, 2, "failed")],
        )

    def test_candidate_three_accepts_equal_time_deferred_reservation_prefix(
        self,
    ) -> None:
        reserved, stale_time = self.same_timestamp_stale_candidate_three_retry_wait()
        third = self.repository.claim(
            reserved.reference,
            "worker-d",
            stale_time + timedelta(seconds=7),
            gate_owned=True,
        )
        self.assertEqual(
            (third.reference.generation, third.candidate, third.fence), (2, 3, 3)
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT kind,from_state,to_state,fence,occurred_at "
                "FROM hardcover_identity_events WHERE work_key=? AND generation=2 "
                "ORDER BY sequence LIMIT 2",
                (WORK_KEY,),
            ).fetchall(),
            [
                ("expired", None, "pending", 0, "2026-01-02T00:00:04.000000Z"),
                (
                    "accepted",
                    "pending",
                    "pending",
                    0,
                    "2026-01-02T00:00:04.000000Z",
                ),
            ],
        )

    def test_candidate_three_rejects_mixed_equal_time_deferred_prefix(self) -> None:
        reserved, stale_time = self.same_timestamp_stale_candidate_three_retry_wait()
        self.connection.execute("DROP TRIGGER hardcover_identity_events_no_update")
        self.connection.execute(
            "UPDATE hardcover_identity_events SET from_state=NULL "
            "WHERE work_key=? AND generation=2 AND kind='accepted'",
            (WORK_KEY,),
        )
        self.assert_candidate_three_history_rejected(
            reserved.reference, when=stale_time + timedelta(seconds=7)
        )

    def test_candidate_three_rejects_duplicate_equal_time_deferred_prefix(self) -> None:
        reserved, stale_time = self.same_timestamp_stale_candidate_three_retry_wait()
        self.connection.execute("DROP TRIGGER hardcover_identity_events_no_update")
        self.connection.execute(
            "UPDATE hardcover_identity_events SET sequence=sequence+100 "
            "WHERE work_key=? AND generation=2",
            (WORK_KEY,),
        )
        self.connection.execute(
            "UPDATE hardcover_identity_events SET sequence=1 "
            "WHERE work_key=? AND generation=2 AND kind='expired'",
            (WORK_KEY,),
        )
        self.connection.execute(
            "UPDATE hardcover_identity_events SET sequence=sequence-99 "
            "WHERE work_key=? AND generation=2 AND sequence>=102",
            (WORK_KEY,),
        )
        self.connection.execute(
            "INSERT INTO hardcover_identity_events(work_key,generation,sequence,kind,from_state,to_state,fence,occurred_at) "
            "VALUES(?,2,2,'accepted','pending','pending',0,?)",
            (WORK_KEY, "2026-01-02T00:00:04.000000Z"),
        )
        self.assert_candidate_three_history_rejected(
            reserved.reference, when=stale_time + timedelta(seconds=7)
        )

    def test_candidate_three_claim_rejects_missing_accepted_event(self) -> None:
        accepted = self.candidate_three_retry_wait()
        self.connection.execute("DROP TRIGGER hardcover_identity_events_no_delete")
        self.connection.execute(
            "DELETE FROM hardcover_identity_events "
            "WHERE work_key=? AND generation=1 AND kind='accepted'",
            (WORK_KEY,),
        )
        self.assert_candidate_three_history_rejected(accepted.reference)

    def test_candidate_three_claim_rejects_corrupt_accepted_event(self) -> None:
        accepted = self.candidate_three_retry_wait()
        self.connection.execute("DROP TRIGGER hardcover_identity_events_no_update")
        self.connection.execute(
            "UPDATE hardcover_identity_events SET occurred_at=? "
            "WHERE work_key=? AND generation=1 AND kind='accepted'",
            ("2026-01-01T00:00:00.500000Z", WORK_KEY),
        )
        self.assert_candidate_three_history_rejected(accepted.reference)

    def test_candidate_three_claim_rejects_extra_failed_event(self) -> None:
        accepted = self.candidate_three_retry_wait()
        self.connection.execute(
            "INSERT INTO hardcover_identity_events(work_key,generation,sequence,kind,from_state,to_state,fence,occurred_at) "
            "VALUES(?,1,8,'failed','claimed','failed',1,?)",
            (WORK_KEY, "2026-01-01T00:00:06.000000Z"),
        )
        self.assert_candidate_three_history_rejected(accepted.reference)

    def test_candidate_three_claim_rejects_extra_published_event(self) -> None:
        accepted = self.candidate_three_retry_wait()
        self.connection.execute(
            "INSERT INTO hardcover_identity_events(work_key,generation,sequence,kind,from_state,to_state,fence,occurred_at) "
            "VALUES(?,1,8,'published','claimed','succeeded',1,?)",
            (WORK_KEY, "2026-01-01T00:00:06.000000Z"),
        )
        self.assert_candidate_three_history_rejected(accepted.reference)

    def test_candidate_three_claim_rejects_corrupt_first_attempt_fence(self) -> None:
        accepted = self.candidate_three_retry_wait()
        self.connection.execute(
            "DROP TRIGGER hardcover_identity_attempts_finalize_only"
        )
        self.connection.execute("PRAGMA foreign_keys=OFF")
        self.connection.execute(
            "UPDATE hardcover_identity_attempts SET fence=10 WHERE work_key=? AND generation=1 AND attempt=1",
            (WORK_KEY,),
        )
        self.assert_candidate_three_history_rejected(accepted.reference)

    def test_candidate_three_claim_rejects_corrupt_first_attempt_candidate(self) -> None:
        accepted = self.candidate_three_retry_wait()
        self.connection.execute(
            "DROP TRIGGER hardcover_identity_attempts_finalize_only"
        )
        self.connection.execute("PRAGMA foreign_keys=OFF")
        self.connection.execute(
            "UPDATE hardcover_identity_attempts SET candidate=2 WHERE work_key=? AND generation=1 AND attempt=1",
            (WORK_KEY,),
        )
        self.assert_candidate_three_history_rejected(accepted.reference)

    def test_candidate_three_claim_rejects_corrupt_first_attempt_chronology(self) -> None:
        accepted = self.candidate_three_retry_wait()
        self.connection.execute(
            "DROP TRIGGER hardcover_identity_attempts_finalize_only"
        )
        self.connection.execute(
            "UPDATE hardcover_identity_attempts SET started_at=? WHERE work_key=? AND generation=1 AND attempt=1",
            ("2025-12-31T23:59:59.000000Z", WORK_KEY),
        )
        self.assert_candidate_three_history_rejected(accepted.reference)

    def test_candidate_three_claim_rejects_corrupt_first_retry_history(self) -> None:
        accepted = self.candidate_three_retry_wait()
        self.connection.execute(
            "DROP TRIGGER hardcover_identity_events_no_update"
        )
        self.connection.execute(
            "UPDATE hardcover_identity_events SET occurred_at=? "
            "WHERE work_key=? AND generation=1 AND fence=1 AND kind='started'",
            ("2026-01-01T00:00:02.500000Z", WORK_KEY),
        )
        self.assert_candidate_three_history_rejected(accepted.reference)

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
            MATCHED,
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

    def test_application_service_narrows_protocol_collaborator_outcomes(self) -> None:
        collaborator = ProtocolPersistenceCollaborator()
        self.assertIsInstance(collaborator, HardcoverIdentityWorkPersistence)
        service = ManageHardcoverIdentityWork(collaborator)
        accepted = service.accept(WORK_KEY, ORIGIN)
        claim = service.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        collaborator.claim_result = IdentityWorkStatus(
            IdentityWorkOutcome.IN_PROGRESS, accepted.reference, 1
        )
        with self.assertRaisesRegex(TypeError, "claim"):
            service.claim(
                accepted.reference,
                "worker-a",
                ORIGIN + timedelta(seconds=1),
                gate_owned=True,
            )
        collaborator.start_result = IdentityWorkStatus(
            IdentityWorkOutcome.RETRY_EXHAUSTED,
            claim.reference,
            claim.candidate,
        )
        self.assertEqual(
            service.start(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=2),
                gate_owned=True,
            ).outcome,
            IdentityWorkOutcome.RETRY_EXHAUSTED,
        )
        collaborator.start_result = IdentityWorkStatus(
            IdentityWorkOutcome.IN_PROGRESS, claim.reference, claim.candidate
        )
        with self.assertRaisesRegex(TypeError, "start"):
            service.start(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=2),
                gate_owned=True,
            )
        collaborator.cancel_result = IdentityWorkStatus(
            IdentityWorkOutcome.PERSISTENCE_FAILURE, claim.reference
        )
        with self.assertRaisesRegex(TypeError, "cancellation"):
            service.cancel(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                gate_owned=True,
            )


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
