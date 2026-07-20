from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.persistence import hardcover_identity_work
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
    IdentityWorkReadiness,
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
MATCHED = IdentityWorkObservation(
    IdentityMatchOutcome.MATCHED,
    2,
    1001,
    2001,
    "Synthetic Ebook Alpha",
)


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

    def accept(self, work_key: str, now: datetime, *, egress_limit: int = 3):
        return self.accept_result

    def inspect(self, reference, now):
        claim = self.claim_result
        if type(claim) is not IdentityWorkClaim:
            raise AssertionError("test collaborator claim is malformed")
        return IdentityWorkReadiness(
            reference,
            IdentityWorkState.CLAIMED,
            ORIGIN + timedelta(seconds=30),
            active_claim=claim,
        )

    def claim(self, reference, owner, now, *, gate_owned):
        return self.claim_result

    def renew_and_start(self, claim, owner, now, *, gate_owned, egress_limit=3):
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
        self,
        claim,
        owner,
        now,
        *,
        retryable,
        retry_delay_seconds=0,
        gate_owned,
        fail_before_commit=False,
    ):
        return IdentityWorkStatus(
            IdentityWorkOutcome.PERSISTENCE_FAILURE, claim.reference
        )

    def cancel_available(self, reference, now):
        return self.cancel_result

    def cancel(self, claim, owner, now, *, gate_owned):
        return self.cancel_result


class HardcoverIdentityWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.sqlite"
        self.connection = connect(self.path)
        self.addCleanup(self.connection.close)
        self.assertEqual(migrate(self.connection), 11)
        self.repository = SQLiteHardcoverIdentityWorkRepository(self.connection)

    def accept(self, *, key: str = WORK_KEY, when: datetime = ORIGIN):
        return self.repository.accept(key, when)

    def test_transaction_cleanup_failure_never_masks_control_flow(self) -> None:
        class RollbackFailureConnection:
            in_transaction = False

            def execute(self, statement: str) -> None:
                self.assert_begin(statement)
                self.in_transaction = True

            def assert_begin(self, statement: str) -> None:
                self_test.assertEqual(statement, "BEGIN IMMEDIATE")

            def commit(self) -> None:
                raise AssertionError("commit must not run")

            def rollback(self) -> None:
                raise OSError("private cleanup failure")

        self_test = self
        connection = RollbackFailureConnection()
        with self.assertRaises(KeyboardInterrupt) as caught:
            with hardcover_identity_work._transaction(connection):  # type: ignore[arg-type]
                raise KeyboardInterrupt
        self.assertEqual(
            caught.exception.__notes__,
            ["identity work transaction rollback failed"],
        )

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
        canonical = (
            '{"book_id":2001,"edition_id":1001,"outcome":"matched",'
            '"provider_title":"Synthetic Ebook Alpha","row_count":2}'
        )
        self.assertEqual(MATCHED.to_canonical_json(), canonical)
        self.assertEqual(IdentityWorkObservation.from_canonical_json(canonical), MATCHED)
        legacy = IdentityWorkObservation.from_canonical_json(
            '{"outcome":"matched","row_count":2}'
        )
        self.assertFalse(legacy.reusable)
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
        self.assertIn(
            (IdentityWorkState.PENDING, IdentityWorkState.CANCELLED),
            LEGAL_TRANSITIONS,
        )
        self.assertIn(
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

    def test_inspect_projects_each_terminal_state_without_mutation(self) -> None:
        for index, terminal in enumerate(("matched", "failed", "cancelled"), 3):
            with self.subTest(terminal=terminal):
                key = str(index) * 64
                accepted = self.repository.accept(key, ORIGIN)
                claim = self.repository.claim(
                    accepted.reference,
                    "worker-a",
                    ORIGIN + timedelta(seconds=1),
                    gate_owned=True,
                )
                if terminal == "cancelled":
                    self.repository.cancel(
                        claim,
                        "worker-a",
                        ORIGIN + timedelta(seconds=2),
                        gate_owned=True,
                    )
                    expected = IdentityWorkState.CANCELLED
                else:
                    started = self.repository.renew_and_start(
                        claim,
                        "worker-a",
                        ORIGIN + timedelta(seconds=2),
                        gate_owned=True,
                    )
                    if terminal == "matched":
                        self.repository.publish_matched(
                            started,
                            "worker-a",
                            ORIGIN + timedelta(seconds=3),
                            MATCHED,
                            gate_owned=True,
                        )
                        expected = IdentityWorkState.SUCCEEDED
                    else:
                        self.repository.publish_failed(
                            started,
                            "worker-a",
                            ORIGIN + timedelta(seconds=3),
                            retryable=False,
                            gate_owned=True,
                        )
                        expected = IdentityWorkState.FAILED

                changes = self.connection.total_changes
                projection = self.repository.inspect(
                    accepted.reference, ORIGIN + timedelta(seconds=4)
                )

                self.assertEqual(
                    projection,
                    IdentityWorkReadiness(
                        accepted.reference,
                        expected,
                        ORIGIN + timedelta(seconds=30),
                        observation=MATCHED if terminal == "matched" else None,
                    ),
                )
                self.assertEqual(self.connection.total_changes, changes)

    def test_inspect_requires_a_still_current_succeeded_generation(self) -> None:
        accepted, started = self.started_claim()
        published_at = ORIGIN + timedelta(seconds=3)
        self.repository.publish_matched(
            started,
            "worker-a",
            published_at,
            MATCHED,
            gate_owned=True,
        )
        expires_at = published_at + timedelta(hours=24)
        changes = self.connection.total_changes

        projection = self.repository.inspect(accepted.reference, expires_at)

        self.assertEqual(projection.state, IdentityWorkState.SUCCEEDED)
        self.assertEqual(self.connection.total_changes, changes)
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "inconsistent"):
            self.repository.inspect(
                accepted.reference, expires_at + timedelta(microseconds=1)
            )
        self.assertEqual(self.connection.total_changes, changes)

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

    def test_keyboard_interrupt_rolls_back_partial_accept_and_allows_next_mutation(self) -> None:
        with (
            patch.object(self.repository, "_event", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.accept()
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(
            self.connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM hardcover_identity_generations),"
                "(SELECT COUNT(*) FROM hardcover_identity_candidates),"
                "(SELECT COUNT(*) FROM hardcover_identity_events)"
            ).fetchone(),
            (0, 0, 0),
        )

        accepted = self.accept()
        self.assertEqual((accepted.reference.generation, accepted.candidate), (1, 1))
        self.assertFalse(self.connection.in_transaction)

    def test_keyboard_interrupt_rolls_back_partial_publication_and_allows_retry(self) -> None:
        _, started = self.started_claim()
        before_dump = tuple(self.connection.iterdump())
        with (
            patch.object(self.repository, "_event", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            self.repository.publish_matched(
                started,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                MATCHED,
                gate_owned=True,
            )
        self.assertFalse(self.connection.in_transaction)
        self.assertEqual(tuple(self.connection.iterdump()), before_dump)

        result = self.repository.publish_matched(
            started,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            MATCHED,
            gate_owned=True,
        )
        self.assertEqual(result.outcome, IdentityWorkOutcome.MATCHED)
        self.assertFalse(self.connection.in_transaction)

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

    def test_start_replay_returns_the_exact_started_claim_without_mutation(self) -> None:
        accepted = self.accept()
        claimed = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        started = self.repository.renew_and_start(
            claimed,
            "worker-a",
            ORIGIN + timedelta(seconds=2),
            gate_owned=True,
        )
        before_dump = tuple(self.connection.iterdump())
        changes = self.connection.total_changes

        replayed_from_request = self.repository.renew_and_start(
            claimed,
            "worker-a",
            ORIGIN + timedelta(seconds=2, microseconds=1),
            gate_owned=True,
        )
        replayed_from_result = self.repository.renew_and_start(
            started,
            "worker-a",
            ORIGIN + timedelta(seconds=2, microseconds=2),
            gate_owned=True,
        )

        self.assertEqual(replayed_from_request, started)
        self.assertEqual(replayed_from_result, started)
        self.assertEqual(self.connection.total_changes, changes)
        self.assertEqual(tuple(self.connection.iterdump()), before_dump)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_attempts"
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_events WHERE kind='started'"
            ).fetchone(),
            (1,),
        )

    def test_start_replay_survives_restart_without_duplicate_rows_or_events(self) -> None:
        accepted = self.accept()
        claimed = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        started = self.repository.renew_and_start(
            claimed,
            "worker-a",
            ORIGIN + timedelta(seconds=2),
            gate_owned=True,
        )
        before_dump = tuple(self.connection.iterdump())
        restarted_connection = connect(self.path)
        self.addCleanup(restarted_connection.close)
        restarted = SQLiteHardcoverIdentityWorkRepository(restarted_connection)
        changes = restarted_connection.total_changes

        replayed = restarted.renew_and_start(
            started,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            gate_owned=True,
        )

        self.assertEqual(replayed, started)
        self.assertEqual(restarted_connection.total_changes, changes)
        self.assertEqual(tuple(restarted_connection.iterdump()), before_dump)
        self.assertEqual(
            restarted_connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM hardcover_identity_attempts),"
                "(SELECT COUNT(*) FROM hardcover_identity_events WHERE kind='started')"
            ).fetchone(),
            (1, 1),
        )

    def test_first_start_rejects_in_between_generation_timestamp_value_free(self) -> None:
        accepted = self.accept()
        claimed = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        self.connection.execute(
            "UPDATE hardcover_identity_generations SET updated_at=? WHERE work_key=?",
            ("2026-01-01T00:00:01.500000Z", WORK_KEY),
        )
        before_dump = tuple(self.connection.iterdump())
        changes = self.connection.total_changes

        with self.assertRaises(StaleIdentityWorkFence):
            self.repository.renew_and_start(
                claimed,
                "worker-a",
                ORIGIN + timedelta(seconds=2),
                gate_owned=True,
            )

        self.assertEqual(self.connection.total_changes, changes)
        self.assertEqual(tuple(self.connection.iterdump()), before_dump)
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_attempts"
            ).fetchone(),
            (0,),
        )

    def test_start_replay_rejects_in_between_generation_timestamp_value_free(self) -> None:
        _, started = self.started_claim()
        self.connection.execute(
            "UPDATE hardcover_identity_generations SET updated_at=? WHERE work_key=?",
            ("2026-01-01T00:00:02.500000Z", WORK_KEY),
        )
        before_dump = tuple(self.connection.iterdump())
        changes = self.connection.total_changes

        with self.assertRaises(StaleIdentityWorkFence):
            self.repository.renew_and_start(
                started,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                gate_owned=True,
            )

        self.assertEqual(self.connection.total_changes, changes)
        self.assertEqual(tuple(self.connection.iterdump()), before_dump)
        self.assertEqual(
            self.connection.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM hardcover_identity_attempts),"
                "(SELECT COUNT(*) FROM hardcover_identity_events WHERE kind='started')"
            ).fetchone(),
            (1, 1),
        )

    def test_first_and_retry_start_reject_corrupt_claim_provenance_value_free(self) -> None:
        for claim_kind in ("first", "retry"):
            for corruption in (
                "backdated",
                "wrong_initial_lease",
                "missing_event",
                "extra_event",
                "mismatched_event",
            ):
                with (
                    self.subTest(claim_kind=claim_kind, corruption=corruption),
                    tempfile.TemporaryDirectory() as directory,
                ):
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
                        owner = "worker-a"
                        started_at = ORIGIN + timedelta(seconds=2)
                        if claim_kind == "retry":
                            started = repository.renew_and_start(
                                claim,
                                owner,
                                started_at,
                                gate_owned=True,
                            )
                            repository.publish_failed(
                                started,
                                owner,
                                ORIGIN + timedelta(seconds=3),
                                retryable=True,
                                gate_owned=True,
                            )
                            owner = "worker-b"
                            claim = repository.claim(
                                accepted.reference,
                                owner,
                                ORIGIN + timedelta(seconds=4),
                                gate_owned=True,
                            )
                            started_at = ORIGIN + timedelta(seconds=5)

                        if corruption in {"backdated", "wrong_initial_lease"}:
                            connection.execute(
                                "DROP TRIGGER hardcover_identity_claims_no_update"
                            )
                        if corruption == "backdated":
                            backdated = (
                                "2026-01-01T00:00:00.500000Z"
                                if claim_kind == "first"
                                else "2026-01-01T00:00:03.500000Z"
                            )
                            connection.execute(
                                "UPDATE hardcover_identity_claims SET claimed_at=? "
                                "WHERE work_key=? AND generation=? AND fence=?",
                                (
                                    backdated,
                                    WORK_KEY,
                                    claim.reference.generation,
                                    claim.fence,
                                ),
                            )
                            connection.execute(
                                "UPDATE hardcover_identity_generations SET updated_at=? "
                                "WHERE work_key=? AND generation=?",
                                (backdated, WORK_KEY, claim.reference.generation),
                            )
                        elif corruption == "wrong_initial_lease":
                            connection.execute(
                                "UPDATE hardcover_identity_claims SET lease_expires_at=? "
                                "WHERE work_key=? AND generation=? AND fence=?",
                                (
                                    hardcover_identity_work._time(
                                        claim.lease_expires_at + timedelta(seconds=1)
                                    ),
                                    WORK_KEY,
                                    claim.reference.generation,
                                    claim.fence,
                                ),
                            )
                        elif corruption == "missing_event":
                            connection.execute(
                                "DROP TRIGGER hardcover_identity_events_no_delete"
                            )
                            connection.execute(
                                "DELETE FROM hardcover_identity_events WHERE work_key=? "
                                "AND generation=? AND fence=? AND kind='claimed'",
                                (WORK_KEY, claim.reference.generation, claim.fence),
                            )
                        elif corruption == "extra_event":
                            sequence = connection.execute(
                                "SELECT MAX(sequence)+1 FROM hardcover_identity_events "
                                "WHERE work_key=? AND generation=?",
                                (WORK_KEY, claim.reference.generation),
                            ).fetchone()[0]
                            connection.execute(
                                "INSERT INTO hardcover_identity_events"
                                "(work_key,generation,sequence,kind,from_state,to_state,fence,occurred_at) "
                                "SELECT work_key,generation,?,'claimed',from_state,to_state,fence,occurred_at "
                                "FROM hardcover_identity_events WHERE work_key=? AND generation=? "
                                "AND fence=? AND kind='claimed'",
                                (
                                    sequence,
                                    WORK_KEY,
                                    claim.reference.generation,
                                    claim.fence,
                                ),
                            )
                        else:
                            connection.execute(
                                "DROP TRIGGER hardcover_identity_events_no_update"
                            )
                            connection.execute(
                                "UPDATE hardcover_identity_events SET occurred_at=? "
                                "WHERE work_key=? AND generation=? AND fence=? AND kind='claimed'",
                                (
                                    hardcover_identity_work._time(
                                        started_at - timedelta(microseconds=1)
                                    ),
                                    WORK_KEY,
                                    claim.reference.generation,
                                    claim.fence,
                                ),
                            )

                        before_dump = tuple(connection.iterdump())
                        changes = connection.total_changes
                        with self.assertRaises(StaleIdentityWorkFence) as caught:
                            repository.renew_and_start(
                                claim,
                                owner,
                                started_at,
                                gate_owned=True,
                            )
                        self.assertEqual(str(caught.exception), "identity work fence is stale")
                        self.assertEqual(connection.total_changes, changes)
                        self.assertEqual(tuple(connection.iterdump()), before_dump)
                    finally:
                        connection.close()

    def test_claim_provenance_is_checked_before_every_post_claim_mutation(self) -> None:
        for operation in ("recover", "publish", "fail", "cancel"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
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
                    if operation in {"publish", "fail", "cancel"}:
                        claim = repository.renew_and_start(
                            claim,
                            "worker-a",
                            ORIGIN + timedelta(seconds=2),
                            gate_owned=True,
                        )
                    connection.execute(
                        "DROP TRIGGER hardcover_identity_events_no_delete"
                    )
                    connection.execute(
                        "DELETE FROM hardcover_identity_events WHERE work_key=? "
                        "AND generation=? AND fence=? AND kind='claimed'",
                        (WORK_KEY, claim.reference.generation, claim.fence),
                    )
                    before_dump = tuple(connection.iterdump())
                    changes = connection.total_changes

                    with self.assertRaises(StaleIdentityWorkFence) as caught:
                        if operation == "recover":
                            repository.recover_interrupted(
                                claim,
                                ORIGIN + timedelta(seconds=17),
                                gate_owned=True,
                            )
                        elif operation == "publish":
                            repository.publish_matched(
                                claim,
                                "worker-a",
                                ORIGIN + timedelta(seconds=3),
                                MATCHED,
                                gate_owned=True,
                            )
                        elif operation == "fail":
                            repository.publish_failed(
                                claim,
                                "worker-a",
                                ORIGIN + timedelta(seconds=3),
                                retryable=True,
                                gate_owned=True,
                            )
                        else:
                            repository.cancel(
                                claim,
                                "worker-a",
                                ORIGIN + timedelta(seconds=3),
                                gate_owned=True,
                            )

                    self.assertEqual(str(caught.exception), "identity work fence is stale")
                    self.assertEqual(connection.total_changes, changes)
                    self.assertEqual(tuple(connection.iterdump()), before_dump)
                finally:
                    connection.close()

    def test_start_replay_rejects_different_stale_and_corrupt_claims_value_free(self) -> None:
        for case in ("different", "stale", "corrupt"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                connection = connect(Path(directory) / "state.sqlite")
                try:
                    migrate(connection)
                    repository = SQLiteHardcoverIdentityWorkRepository(connection)
                    accepted = repository.accept(WORK_KEY, ORIGIN)
                    claimed = repository.claim(
                        accepted.reference,
                        "worker-a",
                        ORIGIN + timedelta(seconds=1),
                        gate_owned=True,
                    )
                    started = repository.renew_and_start(
                        claimed,
                        "worker-a",
                        ORIGIN + timedelta(seconds=2),
                        gate_owned=True,
                    )
                    owner = "worker-a"
                    replayed_at = ORIGIN + timedelta(seconds=3)
                    if case == "different":
                        owner = "worker-b"
                    elif case == "stale":
                        replayed_at = started.lease_expires_at + timedelta(
                            microseconds=1
                        )
                    else:
                        connection.execute(
                            "DROP TRIGGER hardcover_identity_events_no_update"
                        )
                        connection.execute(
                            "UPDATE hardcover_identity_events SET occurred_at=? "
                            "WHERE work_key=? AND generation=1 AND kind='started'",
                            (
                                "2026-01-01T00:00:02.000001Z",
                                WORK_KEY,
                            ),
                        )
                    before_dump = tuple(connection.iterdump())
                    changes = connection.total_changes
                    with self.assertRaises(StaleIdentityWorkFence):
                        repository.renew_and_start(
                            started,
                            owner,
                            replayed_at,
                            gate_owned=True,
                        )
                    self.assertEqual(connection.total_changes, changes)
                    self.assertEqual(tuple(connection.iterdump()), before_dump)
                finally:
                    connection.close()

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
        active = self.repository.accept(
            WORK_KEY, published_at + timedelta(hours=24, microseconds=3)
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
            (active.outcome, active.reference.generation, active.candidate),
            (IdentityWorkOutcome.IN_PROGRESS, 2, None),
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
        before_dump = tuple(self.connection.iterdump())
        changes = self.connection.total_changes
        with self.assertRaises(StaleIdentityWorkFence):
            self.repository.cancel(
                old, "worker-a", ORIGIN + timedelta(seconds=18), gate_owned=True
            )
        self.assertEqual(self.connection.total_changes, changes)
        self.assertEqual(tuple(self.connection.iterdump()), before_dump)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence,lease_owner FROM hardcover_identity_generations"
            ).fetchone(),
            ("claimed", 2, "worker-b"),
        )
        self.repository.cancel(
            current, "worker-b", ORIGIN + timedelta(seconds=18), gate_owned=True
        )

    def test_cancel_requires_a_strictly_live_exact_lease_boundary(self) -> None:
        for offset, cancelled in (
            (timedelta(microseconds=-1), True),
            (timedelta(), False),
            (timedelta(microseconds=1), False),
        ):
            with self.subTest(offset=offset):
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
                        before_dump = tuple(connection.iterdump())
                        changes = connection.total_changes
                        if cancelled:
                            repository.cancel(
                                claim,
                                "worker-a",
                                claim.lease_expires_at + offset,
                                gate_owned=True,
                            )
                            self.assertEqual(
                                connection.execute(
                                    "SELECT state FROM hardcover_identity_generations"
                                ).fetchone(),
                                ("cancelled",),
                            )
                        else:
                            with self.assertRaises(StaleIdentityWorkFence):
                                repository.cancel(
                                    claim,
                                    "worker-a",
                                    claim.lease_expires_at + offset,
                                    gate_owned=True,
                                )
                            self.assertEqual(connection.total_changes, changes)
                            self.assertEqual(tuple(connection.iterdump()), before_dump)
                    finally:
                        connection.close()

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

    def test_exact_retry_due_is_persisted_inspected_and_enforced(self) -> None:
        accepted, started = self.started_claim()
        published_at = ORIGIN + timedelta(seconds=3)
        status = self.repository.publish_failed(
            started,
            "worker-a",
            published_at,
            retryable=True,
            retry_delay_seconds=7,
            gate_owned=True,
        )
        self.assertEqual(status.outcome, IdentityWorkOutcome.IN_PROGRESS)
        due = published_at + timedelta(seconds=7)
        readiness = self.repository.inspect(accepted.reference, published_at)
        self.assertEqual(
            (readiness.state, readiness.retry_due_at, readiness.active_claim),
            (IdentityWorkState.RETRY_WAIT, due, None),
        )
        self.assertEqual(
            (
                readiness.retry_token.reference,
                readiness.retry_token.candidate,
                readiness.retry_token.fence,
                readiness.retry_token.due_at,
            ),
            (accepted.reference, 2, 2, due),
        )
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "not due"):
            self.repository.claim(
                accepted.reference,
                "worker-b",
                due - timedelta(microseconds=1),
                gate_owned=True,
            )
        claimed = self.repository.claim(
            accepted.reference,
            "worker-b",
            due,
            gate_owned=True,
        )
        self.assertEqual((claimed.candidate, claimed.fence), (2, 2))

    def test_egress_limit_is_atomic_immutable_and_restart_bound(self) -> None:
        accepted = self.accept()
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
            egress_limit=1,
        )
        self.assertIsInstance(started, IdentityWorkClaim)
        self.connection.close()
        self.connection = connect(self.path)
        self.addCleanup(self.connection.close)
        self.assertEqual(migrate(self.connection), 11)
        self.repository = SQLiteHardcoverIdentityWorkRepository(self.connection)
        with self.assertRaisesRegex(
            IdentityWorkPersistenceError, "egress authority changed"
        ):
            self.repository.renew_and_start(
                started,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                gate_owned=True,
                egress_limit=3,
            )
        exhausted = self.repository.publish_failed(
            started,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            retryable=True,
            retry_delay_seconds=1,
            gate_owned=True,
        )
        self.assertEqual(exhausted.outcome, IdentityWorkOutcome.RETRY_EXHAUSTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT max_starts FROM hardcover_identity_egress_policies"
            ).fetchall(),
            [(1,)],
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT attempt_count,state FROM hardcover_identity_generations"
            ).fetchone(),
            (1, "failed"),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_retry_schedules"
            ).fetchone(),
            (0,),
        )
        contender_connection = connect(self.path)
        self.addCleanup(contender_connection.close)
        self.assertEqual(migrate(contender_connection), 11)
        contender = SQLiteHardcoverIdentityWorkRepository(contender_connection)
        direct = self.repository.accept(WORK_KEY, ORIGIN + timedelta(seconds=4))
        managed = ManageHardcoverIdentityWork(contender).accept(
            WORK_KEY, ORIGIN + timedelta(seconds=4)
        )
        self.assertEqual(
            (direct.outcome, managed.outcome, direct.reference, managed.reference),
            (
                IdentityWorkOutcome.RETRY_EXHAUSTED,
                IdentityWorkOutcome.RETRY_EXHAUSTED,
                accepted.reference,
                accepted.reference,
            ),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_generations WHERE work_key=?",
                (WORK_KEY,),
            ).fetchone(),
            (1,),
        )

    def test_one_shot_publication_rollback_restarts_terminal_without_retry(self) -> None:
        accepted = self.accept()
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
            egress_limit=1,
        )
        self.assertIsInstance(started, IdentityWorkClaim)
        with self.assertRaisesRegex(
            IdentityWorkPersistenceError, "injected identity failure publication"
        ):
            self.repository.publish_failed(
                started,
                "worker-a",
                ORIGIN + timedelta(seconds=3),
                retryable=True,
                retry_delay_seconds=1,
                gate_owned=True,
                fail_before_commit=True,
            )

        self.connection.close()
        self.connection = connect(self.path)
        self.addCleanup(self.connection.close)
        self.repository = SQLiteHardcoverIdentityWorkRepository(self.connection)
        exhausted = self.repository.recover_interrupted(
            started,
            ORIGIN + timedelta(seconds=18),
            gate_owned=True,
        )

        self.assertEqual(exhausted.outcome, IdentityWorkOutcome.RETRY_EXHAUSTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT state,attempt_count FROM hardcover_identity_generations"
            ).fetchone(),
            ("failed", 1),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT outcome FROM hardcover_identity_attempts"
            ).fetchone(),
            ("interrupted",),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_retry_schedules"
            ).fetchone(),
            (0,),
        )

    def test_successful_one_shot_refresh_requires_explicit_normal_authority(
        self,
    ) -> None:
        accepted = self.accept()
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
            egress_limit=1,
        )
        self.repository.publish_matched(
            started,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            MATCHED,
            gate_owned=True,
        )
        exact = self.repository.accept(
            WORK_KEY, ORIGIN + timedelta(hours=24, seconds=3)
        )
        exhausted = self.repository.accept(
            WORK_KEY,
            ORIGIN + timedelta(hours=24, seconds=3, microseconds=1),
            egress_limit=1,
        )
        stale = self.repository.accept(
            WORK_KEY,
            ORIGIN + timedelta(hours=24, seconds=3, microseconds=2),
            egress_limit=3,
        )
        repeated = self.repository.accept(
            WORK_KEY,
            ORIGIN + timedelta(hours=24, seconds=3, microseconds=3),
            egress_limit=1,
        )
        reserved = self.repository.accept(
            WORK_KEY,
            ORIGIN + timedelta(hours=24, seconds=3, microseconds=4),
            egress_limit=3,
        )

        self.assertEqual(exact.outcome, IdentityWorkOutcome.MATCHED)
        self.assertEqual(
            (exhausted.outcome, exhausted.reference.generation),
            (IdentityWorkOutcome.RETRY_EXHAUSTED, 1),
        )
        self.assertEqual(
            (stale.outcome, stale.reference.generation, stale.candidate),
            (IdentityWorkOutcome.STALE, 2, None),
        )
        self.assertEqual(
            (repeated.outcome, repeated.reference.generation),
            (IdentityWorkOutcome.RETRY_EXHAUSTED, 1),
        )
        self.assertEqual(
            (reserved.outcome, reserved.reference.generation, reserved.candidate),
            (IdentityWorkOutcome.STALE, 2, 1),
        )

    def test_egress_policy_and_start_roll_back_as_one_atomic_change(self) -> None:
        accepted = self.accept()
        claim = self.repository.claim(
            accepted.reference,
            "worker-a",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        self.connection.execute(
            "CREATE TRIGGER reject_identity_start BEFORE INSERT ON "
            "hardcover_identity_attempts BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with self.assertRaisesRegex(IdentityWorkPersistenceError, "could not be started"):
            self.repository.renew_and_start(
                claim,
                "worker-a",
                ORIGIN + timedelta(seconds=2),
                gate_owned=True,
                egress_limit=1,
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT (SELECT COUNT(*) FROM hardcover_identity_egress_policies),"
                "(SELECT COUNT(*) FROM hardcover_identity_attempts),"
                "(SELECT attempt_count FROM hardcover_identity_generations)"
            ).fetchone(),
            (0, 0, 0),
        )
        self.connection.execute("DROP TRIGGER reject_identity_start")
        started = self.repository.renew_and_start(
            claim,
            "worker-a",
            ORIGIN + timedelta(seconds=2),
            gate_owned=True,
            egress_limit=1,
        )
        self.assertIsInstance(started, IdentityWorkClaim)
        self.assertEqual(
            self.connection.execute(
                "SELECT (SELECT max_starts FROM hardcover_identity_egress_policies),"
                "(SELECT COUNT(*) FROM hardcover_identity_attempts)"
            ).fetchone(),
            (1, 1),
        )

    def test_available_cancellation_is_atomic_and_lifecycle_scoped(self) -> None:
        pending = self.accept()
        self.repository.cancel_available(
            pending.reference, ORIGIN + timedelta(seconds=1)
        )
        self.assertEqual(
            self.repository.inspect(
                pending.reference, ORIGIN + timedelta(seconds=1)
            ).state,
            IdentityWorkState.CANCELLED,
        )

        retry = self.repository.accept(OTHER_WORK_KEY, ORIGIN)
        claim = self.repository.claim(
            retry.reference,
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
        self.repository.publish_failed(
            started,
            "worker-a",
            ORIGIN + timedelta(seconds=3),
            retryable=True,
            retry_delay_seconds=2,
            gate_owned=True,
        )
        self.repository.cancel_available(
            retry.reference, ORIGIN + timedelta(seconds=4)
        )
        self.assertEqual(
            self.repository.inspect(
                retry.reference, ORIGIN + timedelta(seconds=4)
            ).state,
            IdentityWorkState.CANCELLED,
        )

        claimed = self.repository.accept("3" * 64, ORIGIN)
        self.repository.claim(
            claimed.reference,
            "worker-b",
            ORIGIN + timedelta(seconds=1),
            gate_owned=True,
        )
        with self.assertRaisesRegex(
            IdentityWorkPersistenceError, "not directly cancellable"
        ):
            self.repository.cancel_available(
                claimed.reference, ORIGIN + timedelta(seconds=2)
            )
        self.assertEqual(
            self.repository.inspect(
                claimed.reference, ORIGIN + timedelta(seconds=2)
            ).state,
            IdentityWorkState.CLAIMED,
        )

    def test_available_cancellation_rolls_back_terminal_state_and_result(self) -> None:
        accepted = self.accept()
        self.connection.execute(
            "CREATE TRIGGER reject_identity_cancel BEFORE INSERT ON "
            "hardcover_identity_results BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
        with self.assertRaisesRegex(
            IdentityWorkPersistenceError, "could not be cancelled"
        ):
            self.repository.cancel_available(
                accepted.reference, ORIGIN + timedelta(seconds=1)
            )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence FROM hardcover_identity_generations"
            ).fetchone(),
            ("pending", 0),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT COUNT(*) FROM hardcover_identity_results"
            ).fetchone(),
            (0,),
        )
        self.connection.execute("DROP TRIGGER reject_identity_cancel")
        self.repository.cancel_available(
            accepted.reference, ORIGIN + timedelta(seconds=1)
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT state,fence FROM hardcover_identity_generations"
            ).fetchone(),
            ("cancelled", 1),
        )


class HardcoverIdentityMigrationTests(unittest.TestCase):
    def test_schema_nine_started_work_backfills_normal_egress_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate_to_version(connection, 9), 9)
            connection.execute(
                "INSERT INTO hardcover_identity_generations("
                "work_key,generation,state,fence,attempt_count,aggregate_started_at,"
                "aggregate_deadline,lease_owner,lease_expires_at,created_at,updated_at) "
                "VALUES(?,1,'claimed',1,1,?,?,?,?,?,?)",
                (
                    WORK_KEY,
                    "2026-01-01T00:00:00.000000Z",
                    "2026-01-01T00:00:30.000000Z",
                    "worker-a",
                    "2026-01-01T00:00:17.000000Z",
                    "2026-01-01T00:00:00.000000Z",
                    "2026-01-01T00:00:02.000000Z",
                ),
            )
            connection.execute(
                "INSERT INTO hardcover_identity_candidates("
                "work_key,generation,candidate,created_at) VALUES(?,1,1,?)",
                (WORK_KEY, "2026-01-01T00:00:00.000000Z"),
            )
            connection.execute(
                "INSERT INTO hardcover_identity_claims("
                "work_key,generation,fence,candidate,owner,claimed_at,lease_expires_at) "
                "VALUES(?,1,1,1,'worker-a',?,?)",
                (
                    WORK_KEY,
                    "2026-01-01T00:00:01.000000Z",
                    "2026-01-01T00:00:17.000000Z",
                ),
            )
            timestamp = "2026-01-01T00:00:02.000000Z"
            connection.execute(
                "INSERT INTO hardcover_identity_attempts("
                "work_key,generation,attempt,fence,candidate,started_at) "
                "VALUES(?,?,?,?,?,?)",
                (WORK_KEY, 1, 1, 1, 1, timestamp),
            )
            self.assertEqual(migrate(connection), 11)
            self.assertEqual(
                connection.execute(
                    "SELECT work_key,generation,max_starts,created_at "
                    "FROM hardcover_identity_egress_policies"
                ).fetchall(),
                [(WORK_KEY, 1, 3, timestamp)],
            )
            self.assertEqual(migrate(connection), 11)

    def test_schema_eight_upgrades_additively_and_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            connection = connect(Path(directory) / "state.sqlite")
            self.addCleanup(connection.close)
            self.assertEqual(migrate_to_version(connection, 8), 8)
            connection.execute(
                "INSERT INTO instance_metadata(key,value) VALUES('schema-eight-sentinel','preserved')"
            )
            self.assertEqual(migrate(connection), 11)
            self.assertEqual(migrate(connection), 11)
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
                        self.assertEqual(migrate(connection), 11)
                    finally:
                        connection.close()


if __name__ == "__main__":
    unittest.main()
