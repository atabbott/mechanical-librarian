from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mechanical_librarian.adapters.persistence.jobs import (
    SQLiteJobHeartbeatRepository,
)
from mechanical_librarian.application.job_heartbeat import (
    RepositoryOutcome,
    RunSyntheticJobHeartbeat,
)
from mechanical_librarian.domain.workflow import (
    FailureKind,
    OutcomeKind,
    RunId,
    RunState,
    StageDefinition,
    StageId,
    StageState,
    StageVersion,
    WorkerId,
    WorkflowDefinition,
    WorkflowVersion,
    synthetic_heartbeat_definition,
)
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
LEASE = timedelta(seconds=5)


class SQLiteJobHeartbeatTests(unittest.TestCase):
    def test_cli_is_idempotent_across_process_restart(self) -> None:
        self.connection.close()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        for _ in range(2):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mechanical_librarian",
                    "job-heartbeat",
                    "--database",
                    str(self.path),
                ],
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("authority=synthetic-report-only", completed.stdout)
            self.assertNotIn(str(self.path), completed.stdout)
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "state.sqlite"
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary_directory.cleanup()

    def create_run(self, value: str = "run-one") -> RunId:
        run_id = RunId(value)
        result = self.repository.create_or_resume(
            run_id,
            synthetic_heartbeat_definition(),
            changed_stages=frozenset(),
            now=NOW,
        )
        self.assertTrue(result.accepted)
        return run_id

    def claim_one(self, worker: str, now: datetime = NOW):
        claimed = self.repository.claim(
            WorkerId(worker), capacity=1, now=now, lease_duration=LEASE
        )
        self.assertTrue(claimed.accepted)
        self.assertIsNotNone(claimed.value)
        self.assertEqual(len(claimed.value), 1)
        return claimed.value[0]

    def complete_run(self, run_id: RunId) -> None:
        while True:
            snapshot = self.repository.inspect(run_id).value
            if snapshot.state is RunState.SUCCEEDED:
                return
            self.repository.enqueue_eligible(run_id, now=NOW)
            claims = self.repository.claim(
                WorkerId("worker-complete"),
                capacity=4,
                now=NOW,
                lease_duration=LEASE,
            ).value
            self.assertTrue(claims)
            for claim in claims:
                self.repository.start(claim, now=NOW)
                self.repository.complete(claim, OutcomeKind.COMPLETED, now=NOW)

    def complete_heartbeat_main(self) -> RunId:
        run_id = self.create_run("heartbeat-main")
        self.repository.enqueue_eligible(run_id, now=NOW)
        old = self.claim_one("worker-main-old")
        self.repository.start(old, now=NOW)
        recovered = self.claim_one("worker-main-new", NOW + timedelta(seconds=6))
        self.repository.start(recovered, now=NOW + timedelta(seconds=6))
        self.repository.complete(
            recovered, OutcomeKind.COMPLETED, now=NOW + timedelta(seconds=6)
        )
        self.complete_run(run_id)
        return run_id

    def test_complete_eligible_set_is_idempotent_and_claim_is_bounded(self) -> None:
        run_id = self.create_run()
        first = self.repository.enqueue_eligible(run_id, now=NOW)
        repeated = self.repository.enqueue_eligible(run_id, now=NOW)
        self.assertEqual(first.value, 1)
        self.assertEqual(repeated.outcome, RepositoryOutcome.REUSED)
        claimed = self.repository.claim(
            WorkerId("worker-one"), capacity=1, now=NOW, lease_duration=LEASE
        )
        self.assertEqual(len(claimed.value or ()), 1)
        empty = self.repository.claim(
            WorkerId("worker-two"), capacity=2, now=NOW, lease_duration=LEASE
        )
        self.assertEqual(empty.value, ())

    def test_competing_schedulers_converge_on_one_job(self) -> None:
        run_id = self.create_run()

        def enqueue() -> RepositoryOutcome:
            connection = connect(self.path)
            try:
                migrate(connection)
                return SQLiteJobHeartbeatRepository(connection).enqueue_eligible(
                    run_id, now=NOW
                ).outcome
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(lambda _value: enqueue(), range(2)))
        self.assertTrue(
            all(
                outcome in {RepositoryOutcome.ACCEPTED, RepositoryOutcome.REUSED}
                for outcome in outcomes
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM jobs WHERE run_id = ?", (run_id.value,)
            ).fetchone(),
            (1,),
        )

    def test_competing_workers_claim_distinct_jobs_with_bounded_capacity(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-root")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        self.repository.enqueue_eligible(run_id, now=NOW)

        def claim(worker: str):
            connection = connect(self.path)
            try:
                migrate(connection)
                result = SQLiteJobHeartbeatRepository(connection).claim(
                    WorkerId(worker),
                    capacity=1,
                    now=NOW,
                    lease_duration=LEASE,
                )
                return result.value or ()
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed = tuple(executor.map(claim, ("worker-left", "worker-right")))
        self.assertEqual(tuple(len(batch) for batch in claimed), (1, 1))
        self.assertEqual(
            {batch[0].stage_id.value for batch in claimed}, {"left", "right"}
        )

    def test_failed_fanout_transaction_leaves_no_partial_sibling_set(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-root")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        self.connection.execute(
            "CREATE TEMP TRIGGER fail_right BEFORE INSERT ON jobs "
            "WHEN NEW.stage_id = 'right' BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        failed = self.repository.enqueue_eligible(run_id, now=NOW)
        self.assertEqual(failed.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(
            self.connection.execute(
                "SELECT stage_id FROM jobs WHERE run_id = ? ORDER BY stage_id",
                (run_id.value,),
            ).fetchall(),
            [("root",)],
        )
        self.connection.execute("DROP TRIGGER fail_right")
        retried = self.repository.enqueue_eligible(run_id, now=NOW)
        self.assertEqual(retried.value, 2)

    def test_expiry_reclaims_with_higher_fence_and_stale_publication_is_inert(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        old = self.claim_one("worker-old")
        self.assertTrue(self.repository.start(old, now=NOW).accepted)
        recovered_at = NOW + timedelta(seconds=6)
        new = self.claim_one("worker-new", recovered_at)
        self.assertGreater(new.fence, old.fence)
        before = self.connection.execute(
            "SELECT state, fence, terminal_outcome FROM jobs WHERE job_id = ?",
            (old.job_id.value,),
        ).fetchone()
        rejected = self.repository.complete(
            old, OutcomeKind.COMPLETED, now=recovered_at
        )
        after = self.connection.execute(
            "SELECT state, fence, terminal_outcome FROM jobs WHERE job_id = ?",
            (old.job_id.value,),
        ).fetchone()
        self.assertEqual(rejected.outcome, RepositoryOutcome.REJECTED)
        self.assertEqual(before, after)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM stage_checkpoints WHERE run_id = ?",
                (run_id.value,),
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM audit_events WHERE kind = 'job'"
            ).fetchone(),
            (0,),
        )
        self.assertEqual(
            self.repository.renew(old, now=recovered_at, lease_duration=LEASE).outcome,
            RepositoryOutcome.REJECTED,
        )
        self.assertTrue(self.repository.start(new, now=recovered_at).accepted)
        self.assertTrue(
            self.repository.complete(
                new, OutcomeKind.COMPLETED, now=recovered_at
            ).accepted
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM stage_checkpoints WHERE run_id = ?",
                (run_id.value,),
            ).fetchone(),
            (1,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM audit_events WHERE kind = 'job'"
            ).fetchone(),
            (1,),
        )

    def test_mixed_attempt_expiry_terminalizes_every_sibling_in_either_row_order(self) -> None:
        runs: list[tuple[RunId, StageId, StageId]] = []
        for exhausted_stage, sibling_stage in (
            (StageId("left"), StageId("right")),
            (StageId("right"), StageId("left")),
        ):
            run_id = self.create_run(f"mixed-expiry-{exhausted_stage.value}")
            self.repository.enqueue_eligible(run_id, now=NOW)
            root = self.claim_one(f"root-{exhausted_stage.value}")
            self.repository.start(root, now=NOW)
            self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
            self.repository.enqueue_eligible(run_id, now=NOW)
            first = self.repository.claim(
                WorkerId(f"first-{exhausted_stage.value}"),
                capacity=2,
                now=NOW,
                lease_duration=LEASE,
            ).value
            self.assertIsNotNone(first)
            exhausted = next(
                claim for claim in first if claim.stage_id == exhausted_stage
            )
            self.repository.start(exhausted, now=NOW)

            recovered_at = NOW + timedelta(seconds=6)
            second = self.repository.claim(
                WorkerId(f"second-{exhausted_stage.value}"),
                capacity=2,
                now=recovered_at,
                lease_duration=LEASE,
            ).value
            self.assertIsNotNone(second)
            self.assertEqual(len(second), 2)
            for claim in second:
                self.repository.start(claim, now=recovered_at)

            terminal_at = NOW + timedelta(seconds=12)
            recovered = self.repository.claim(
                WorkerId(f"terminal-{exhausted_stage.value}"),
                capacity=2,
                now=terminal_at,
                lease_duration=LEASE,
            )
            self.assertEqual(recovered.value, ())
            snapshot = self.repository.inspect(run_id).value
            self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)
            jobs = {job.stage_id: job for job in snapshot.jobs}
            self.assertEqual(jobs[exhausted_stage].state.value, "failed_terminal")
            self.assertEqual(jobs[exhausted_stage].attempt, 2)
            self.assertEqual(jobs[sibling_stage].state.value, "cancelled")
            self.assertEqual(jobs[sibling_stage].attempt, 1)
            stages = {stage.id: stage.state for stage in snapshot.stages}
            self.assertEqual(stages[exhausted_stage], StageState.FAILED_TERMINAL)
            self.assertEqual(stages[sibling_stage], StageState.CANCELLED)
            self.assertEqual(stages[StageId("join")], StageState.DEPENDENCY_FAILED)
            self.assertTrue(
                self.repository.validate_run(
                    run_id, synthetic_heartbeat_definition()
                ).accepted
            )
            runs.append((run_id, exhausted_stage, sibling_stage))

        self.connection.close()
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        for run_id, exhausted_stage, sibling_stage in runs:
            snapshot = self.repository.inspect(run_id).value
            jobs = {job.stage_id: job for job in snapshot.jobs}
            self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)
            self.assertEqual(jobs[exhausted_stage].state.value, "failed_terminal")
            self.assertEqual(jobs[sibling_stage].state.value, "cancelled")
            failures = dict(
                self.connection.execute(
                    "SELECT stage_id, terminal_failure FROM jobs WHERE run_id = ?",
                    (run_id.value,),
                )
            )
            self.assertEqual(
                failures[exhausted_stage.value], FailureKind.LEASE_EXPIRED.value
            )
            self.assertEqual(
                failures[sibling_stage.value], FailureKind.DEPENDENCY_FAILED.value
            )
            self.assertEqual(
                self.repository.claim(
                    WorkerId(f"after-restart-{exhausted_stage.value}"),
                    capacity=2,
                    now=NOW + timedelta(seconds=30),
                    lease_duration=LEASE,
                ).value,
                (),
            )

    def test_renewal_rejects_nonpositive_and_unbounded_durations(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        claim = self.claim_one("worker-renew")
        for duration in (
            timedelta(seconds=-1),
            timedelta(0),
            timedelta(hours=2),
        ):
            with self.subTest(duration=duration):
                self.assertEqual(
                    self.repository.renew(
                        claim, now=NOW, lease_duration=duration
                    ).outcome,
                    RepositoryOutcome.REJECTED,
                )

    def test_retry_deadline_budget_and_restart_are_durable(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        first = self.claim_one("worker-retry")
        self.repository.start(first, now=NOW)
        self.repository.fail(first, FailureKind.SYNTHETIC_RETRYABLE, now=NOW)
        self.connection.close()
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        early = self.repository.claim(
            WorkerId("worker-early"),
            capacity=1,
            now=NOW - timedelta(seconds=1),
            lease_duration=LEASE,
        )
        self.assertEqual(early.value, ())
        second = self.claim_one("worker-retry", NOW + timedelta(seconds=5))
        self.repository.start(second, now=NOW + timedelta(seconds=5))
        self.repository.fail(
            second,
            FailureKind.SYNTHETIC_RETRYABLE,
            now=NOW + timedelta(seconds=5),
        )
        snapshot = self.repository.inspect(run_id).value
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)
        self.assertEqual(snapshot.jobs[0].attempt, 2)

    def test_worker_process_death_recovers_under_a_new_fence(self) -> None:
        script = (
            "import os,sys; from datetime import datetime,timedelta,timezone; "
            "from pathlib import Path; "
            "from mechanical_librarian.infrastructure.persistence.sqlite import connect,migrate; "
            "from mechanical_librarian.adapters.persistence.jobs import SQLiteJobHeartbeatRepository; "
            "from mechanical_librarian.domain.workflow import RunId,WorkerId,synthetic_heartbeat_definition; "
            "c=connect(Path(sys.argv[1])); migrate(c); r=SQLiteJobHeartbeatRepository(c); "
            "now=datetime(2026,1,1,tzinfo=timezone.utc); run=RunId('worker-death'); "
            "r.create_or_resume(run,synthetic_heartbeat_definition(),changed_stages=frozenset(),now=now); "
            "r.enqueue_eligible(run,now=now); "
            "claim=r.claim(WorkerId('worker-dead'),capacity=1,now=now,lease_duration=timedelta(seconds=5)).value[0]; "
            "r.start(claim,now=now); os._exit(23)"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 23)
        recovered = self.repository.claim(
            WorkerId("worker-recovered"),
            capacity=1,
            now=NOW + timedelta(seconds=6),
            lease_duration=LEASE,
        )
        self.assertEqual(len(recovered.value or ()), 1)
        self.assertEqual(recovered.value[0].run_id, RunId("worker-death"))
        self.assertGreater(recovered.value[0].fence, 1)

    def test_scheduler_process_death_during_fanout_recovers_complete_set(self) -> None:
        run_id = self.create_run("scheduler-death")
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-root")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        script = (
            "import os,sys; from datetime import datetime,timezone; from pathlib import Path; "
            "from mechanical_librarian.infrastructure.persistence.sqlite import connect,migrate; "
            "from mechanical_librarian.adapters.persistence.jobs import SQLiteJobHeartbeatRepository; "
            "from mechanical_librarian.domain.workflow import RunId; "
            "c=connect(Path(sys.argv[1])); migrate(c); c.create_function('die',0,lambda: os._exit(25)); "
            "c.execute(\"CREATE TEMP TRIGGER die_scheduler BEFORE INSERT ON jobs "
            "WHEN NEW.stage_id = 'right' BEGIN SELECT die(); END\"); "
            "SQLiteJobHeartbeatRepository(c).enqueue_eligible(RunId('scheduler-death'),"
            "now=datetime(2026,1,1,tzinfo=timezone.utc))"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 25, completed.stderr)
        self.assertEqual(
            self.connection.execute(
                "SELECT stage_id FROM jobs WHERE run_id = ? ORDER BY stage_id",
                (run_id.value,),
            ).fetchall(),
            [("root",)],
        )
        recovered = self.repository.enqueue_eligible(run_id, now=NOW)
        self.assertEqual(recovered.value, 2)

    def test_whole_process_death_after_acknowledged_schedule_recovers(self) -> None:
        script = (
            "import os,sys; from datetime import datetime,timezone; from pathlib import Path; "
            "from mechanical_librarian.infrastructure.persistence.sqlite import connect,migrate; "
            "from mechanical_librarian.adapters.persistence.jobs import SQLiteJobHeartbeatRepository; "
            "from mechanical_librarian.domain.workflow import RunId,synthetic_heartbeat_definition; "
            "c=connect(Path(sys.argv[1])); migrate(c); r=SQLiteJobHeartbeatRepository(c); "
            "now=datetime(2026,1,1,tzinfo=timezone.utc); run=RunId('process-death'); "
            "r.create_or_resume(run,synthetic_heartbeat_definition(),changed_stages=frozenset(),now=now); "
            "r.enqueue_eligible(run,now=now); c.close(); os._exit(26)"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 26, completed.stderr)
        claim = self.repository.claim(
            WorkerId("worker-after-process-death"),
            capacity=1,
            now=NOW,
            lease_duration=LEASE,
        )
        self.assertEqual(len(claim.value or ()), 1)
        self.assertEqual(claim.value[0].run_id, RunId("process-death"))

    def test_required_failure_terminates_descendants_instead_of_waiting(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-root")
        self.repository.start(root, now=NOW)
        self.repository.fail(root, FailureKind.SYNTHETIC_TERMINAL, now=NOW)
        snapshot = self.repository.inspect(run_id).value
        self.assertIsNotNone(snapshot)
        states = {stage.id.value: stage.state for stage in snapshot.stages}
        self.assertEqual(states["root"], StageState.FAILED_TERMINAL)
        self.assertEqual(states["left"], StageState.DEPENDENCY_FAILED)
        self.assertEqual(states["right"], StageState.DEPENDENCY_FAILED)
        self.assertEqual(states["join"], StageState.DEPENDENCY_FAILED)
        self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)

    def test_missing_or_malformed_outcome_cannot_advance_fan_in(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-root")
        self.repository.start(root, now=NOW)
        malformed = self.repository.complete(root, object(), now=NOW)  # type: ignore[arg-type]
        self.assertEqual(malformed.outcome, RepositoryOutcome.REJECTED)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM stage_checkpoints WHERE run_id = ?",
                (run_id.value,),
            ).fetchone(),
            (0,),
        )
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        self.repository.enqueue_eligible(run_id, now=NOW)
        branches = self.repository.claim(
            WorkerId("worker-branches"), capacity=2, now=NOW, lease_duration=LEASE
        ).value
        self.assertEqual(len(branches), 2)
        left = next(claim for claim in branches if claim.stage_id == StageId("left"))
        self.repository.start(left, now=NOW)
        self.repository.complete(left, OutcomeKind.COMPLETED, now=NOW)
        self.repository.enqueue_eligible(run_id, now=NOW)
        self.assertIsNone(
            self.connection.execute(
                "SELECT 1 FROM jobs WHERE run_id = ? AND stage_id = 'join'",
                (run_id.value,),
            ).fetchone()
        )

    def test_cancellation_prevents_claim_and_survives_restart(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        self.assertTrue(self.repository.cancel_run(run_id, now=NOW).accepted)
        self.connection.close()
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        claims = self.repository.claim(
            WorkerId("worker-after-cancel"),
            capacity=4,
            now=NOW,
            lease_duration=LEASE,
        )
        self.assertEqual(claims.value, ())
        snapshot = self.repository.inspect(run_id).value
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.state, RunState.CANCELLED)
        self.assertTrue(snapshot.cancellation_requested)

    def test_required_stage_cancellation_propagates_and_retry_wait_cannot_reclaim(self) -> None:
        run_id = self.create_run("required-cancel")
        self.repository.enqueue_eligible(run_id, now=NOW)
        claim = self.claim_one("worker-required")
        self.repository.start(claim, now=NOW)
        self.repository.fail(claim, FailureKind.SYNTHETIC_RETRYABLE, now=NOW)
        self.assertTrue(
            self.repository.cancel_stage(run_id, StageId("root"), now=NOW).accepted
        )
        self.connection.close()
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        claims = self.repository.claim(
            WorkerId("worker-after-required-cancel"),
            capacity=4,
            now=NOW + timedelta(seconds=10),
            lease_duration=LEASE,
        )
        self.assertEqual(claims.value, ())
        snapshot = self.repository.inspect(run_id).value
        states = {stage.id.value: stage.state for stage in snapshot.stages}
        self.assertEqual(states["root"], StageState.CANCELLED)
        self.assertEqual(states["left"], StageState.DEPENDENCY_CANCELLED)
        self.assertEqual(states["right"], StageState.DEPENDENCY_CANCELLED)
        self.assertEqual(states["join"], StageState.DEPENDENCY_CANCELLED)
        self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)

    def test_ready_leaf_cancellation_terminalizes_run_and_survives_restart(self) -> None:
        run_id = self.create_run("leaf-cancel")
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-leaf-root")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        self.repository.enqueue_eligible(run_id, now=NOW)
        branches = self.repository.claim(
            WorkerId("worker-leaf-branches"),
            capacity=2,
            now=NOW,
            lease_duration=LEASE,
        ).value
        self.assertEqual(len(branches), 2)
        for claim in branches:
            self.repository.start(claim, now=NOW)
            self.repository.complete(claim, OutcomeKind.COMPLETED, now=NOW)
        self.repository.enqueue_eligible(run_id, now=NOW)
        before = self.repository.inspect(run_id).value
        join_stage = next(
            stage for stage in before.stages if stage.id == StageId("join")
        )
        join_job = next(
            job for job in before.jobs if job.stage_id == StageId("join")
        )
        self.assertEqual(join_stage.state, StageState.READY)
        self.assertEqual(join_job.state.value, "pending")

        self.assertTrue(
            self.repository.cancel_stage(
                run_id, StageId("join"), now=NOW
            ).accepted
        )
        snapshot = self.repository.inspect(run_id).value
        stages = {stage.id: stage.state for stage in snapshot.stages}
        jobs = {job.stage_id: job.state.value for job in snapshot.jobs}
        self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)
        self.assertEqual(stages[StageId("join")], StageState.CANCELLED)
        self.assertEqual(jobs[StageId("join")], "cancelled")
        self.assertTrue(
            all(
                state is StageState.SUCCEEDED
                for stage_id, state in stages.items()
                if stage_id != StageId("join")
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT terminal_failure FROM jobs WHERE run_id = ? AND stage_id = 'join'",
                (run_id.value,),
            ).fetchone(),
            (FailureKind.DEPENDENCY_CANCELLED.value,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT from_state, to_state FROM workflow_transitions "
                "WHERE run_id = ? AND entity_kind = 'run' "
                "ORDER BY sequence DESC LIMIT 1",
                (run_id.value,),
            ).fetchone(),
            (RunState.RUNNING.value, RunState.FAILED_TERMINAL.value),
        )
        self.assertTrue(
            self.repository.validate_run(
                run_id, synthetic_heartbeat_definition()
            ).accepted
        )

        self.connection.close()
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        restarted = self.repository.inspect(run_id).value
        self.assertEqual(restarted.state, RunState.FAILED_TERMINAL)
        self.assertEqual(
            self.repository.enqueue_eligible(run_id, now=NOW).value,
            0,
        )
        self.assertEqual(
            self.repository.claim(
                WorkerId("worker-after-leaf-cancel"),
                capacity=4,
                now=NOW + timedelta(seconds=10),
                lease_duration=LEASE,
            ).value,
            (),
        )
        self.assertTrue(
            self.repository.validate_run(
                run_id, synthetic_heartbeat_definition()
            ).accepted
        )

    def test_terminal_branch_failure_cancels_unrelated_running_sibling(self) -> None:
        run_id = self.create_run("branch-failure")
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-root")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        self.repository.enqueue_eligible(run_id, now=NOW)
        branches = self.repository.claim(
            WorkerId("worker-branches"), capacity=2, now=NOW, lease_duration=LEASE
        ).value
        for claim in branches:
            self.repository.start(claim, now=NOW)
        left = next(claim for claim in branches if claim.stage_id == StageId("left"))
        right = next(claim for claim in branches if claim.stage_id == StageId("right"))
        self.repository.fail(left, FailureKind.SYNTHETIC_TERMINAL, now=NOW)
        self.assertEqual(
            self.repository.complete(right, OutcomeKind.COMPLETED, now=NOW).outcome,
            RepositoryOutcome.REJECTED,
        )
        self.assertTrue(
            self.repository.acknowledge_cancellation(right, now=NOW).accepted
        )
        snapshot = self.repository.inspect(run_id).value
        jobs = {job.stage_id.value: job.state for job in snapshot.jobs}
        self.assertEqual(jobs["left"].value, "failed_terminal")
        self.assertEqual(jobs["right"].value, "cancelled")
        self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)

    def test_running_completion_cannot_revive_cancelled_run(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        claim = self.claim_one("worker-running")
        self.repository.start(claim, now=NOW)
        self.repository.cancel_run(run_id, now=NOW)
        rejected = self.repository.complete(claim, OutcomeKind.COMPLETED, now=NOW)
        self.assertEqual(rejected.outcome, RepositoryOutcome.REJECTED)
        self.assertTrue(
            self.repository.acknowledge_cancellation(claim, now=NOW).accepted
        )
        snapshot = self.repository.inspect(run_id).value
        self.assertEqual(snapshot.state, RunState.CANCELLED)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM stage_checkpoints WHERE run_id = ?",
                (run_id.value,),
            ).fetchone(),
            (0,),
        )
        self.assertEqual(snapshot.jobs[0].state.value, "cancelled")

    def test_replan_is_idempotent_and_only_invalidates_transitive_branch(self) -> None:
        result = RunSyntheticJobHeartbeat(self.repository).run()
        self.assertTrue(result.succeeded)
        source = self.repository.inspect(RunId("heartbeat-main")).value
        self.assertIsNotNone(source)
        self.assertIsNotNone(source.superseded_by)
        replacement = self.repository.inspect(source.superseded_by).value
        self.assertIsNotNone(replacement)
        flags = {
            stage.id.value: (stage.reuse_eligible, stage.current_applicable)
            for stage in replacement.stages
        }
        self.assertEqual(flags["root"], (True, True))
        self.assertEqual(flags["right"], (True, True))
        self.assertEqual(flags["left"], (False, False))
        self.assertEqual(flags["join"], (False, False))
        original_rows = self.connection.execute(
            "SELECT stage_id, state FROM workflow_stages WHERE run_id = ? ORDER BY stage_id",
            (source.id.value,),
        ).fetchall()
        replay = self.repository.create_or_resume(
            source.id,
            synthetic_heartbeat_definition(incompatible=True),
            changed_stages=frozenset({StageId("left")}),
            now=NOW + timedelta(minutes=1),
        )
        self.assertTrue(replay.accepted)
        self.assertEqual(replay.value.id, replacement.id)
        self.assertEqual(
            self.connection.execute(
                "SELECT stage_id, state FROM workflow_stages WHERE run_id = ? ORDER BY stage_id",
                (source.id.value,),
            ).fetchall(),
            original_rows,
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE replanned_from = ?",
                (source.id.value,),
            ).fetchone(),
            (1,),
        )
        self.repository.enqueue_eligible(replacement.id, now=NOW + timedelta(minutes=1))
        changed = self.claim_one("worker-replan", NOW + timedelta(minutes=1))
        self.assertEqual(changed.run_id, replacement.id)
        self.assertEqual(changed.stage_id, StageId("left"))
        self.repository.start(changed, now=NOW + timedelta(minutes=1))
        self.assertTrue(
            self.repository.complete(
                changed,
                OutcomeKind.COMPLETED,
                now=NOW + timedelta(minutes=1),
            ).accepted
        )
        self.repository.enqueue_eligible(replacement.id, now=NOW + timedelta(minutes=1))
        join = self.claim_one("worker-replan", NOW + timedelta(minutes=1))
        self.assertEqual(join.stage_id, StageId("join"))
        self.repository.start(join, now=NOW + timedelta(minutes=1))
        self.repository.complete(
            join, OutcomeKind.COMPLETED, now=NOW + timedelta(minutes=1)
        )
        completed_replacement = self.repository.inspect(replacement.id).value
        self.assertEqual(completed_replacement.state, RunState.SUCCEEDED)
        self.assertEqual(
            {
                stage.id.value: stage.current_applicable
                for stage in completed_replacement.stages
            },
            {"root": True, "left": True, "right": True, "join": True},
        )

    def test_replan_atomically_terminalizes_active_source_work(self) -> None:
        baseline = synthetic_heartbeat_definition()
        replacement_definition = synthetic_heartbeat_definition(incompatible=True)
        for source_state in ("pending", "leased", "running", "retry_wait"):
            with self.subTest(source_state=source_state):
                source = self.create_run(f"active-source-{source_state}")
                self.repository.enqueue_eligible(source, now=NOW)
                claim = None
                if source_state != "pending":
                    claim = self.claim_one(f"worker-{source_state}")
                if source_state in {"running", "retry_wait"}:
                    self.repository.start(claim, now=NOW)
                if source_state == "retry_wait":
                    self.repository.fail(
                        claim, FailureKind.SYNTHETIC_RETRYABLE, now=NOW
                    )

                replanned = self.repository.create_or_resume(
                    source,
                    replacement_definition,
                    changed_stages=frozenset({StageId("left")}),
                    now=NOW + timedelta(minutes=1),
                )
                self.assertTrue(replanned.accepted)
                snapshot = self.repository.inspect(source).value
                self.assertEqual(snapshot.state, RunState.SUPERSEDED)
                self.assertTrue(
                    all(job.state.value == "cancelled" for job in snapshot.jobs)
                )
                self.assertTrue(
                    all(stage.state.value == "cancelled" for stage in snapshot.stages)
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT count(*) FROM job_attempts a JOIN jobs j "
                        "ON j.job_id = a.job_id WHERE j.run_id = ? "
                        "AND a.finished_at IS NULL",
                        (source.value,),
                    ).fetchone(),
                    (0,),
                )
                if claim is not None:
                    self.assertEqual(
                        self.repository.complete(
                            claim,
                            OutcomeKind.COMPLETED,
                            now=NOW + timedelta(minutes=1),
                        ).outcome,
                        RepositoryOutcome.REJECTED,
                    )
                self.assertTrue(self.repository.validate_run(source, baseline).accepted)
                self.assertTrue(
                    self.repository.validate_run(
                        replanned.value.id, replacement_definition
                    ).accepted
                )
                before_recovery = tuple(self.connection.iterdump())
                claimed = self.repository.claim(
                    WorkerId("worker-after-supersession"),
                    capacity=4,
                    now=NOW + timedelta(minutes=2),
                    lease_duration=LEASE,
                )
                self.assertEqual(claimed.value, ())
                self.assertEqual(tuple(self.connection.iterdump()), before_recovery)
                self.assertEqual(
                    self.repository.enqueue_eligible(
                        replanned.value.id, now=NOW + timedelta(minutes=2)
                    ).value,
                    1,
                )
                self.assertTrue(
                    self.repository.cancel_run(
                        replanned.value.id, now=NOW + timedelta(minutes=3)
                    ).accepted
                )

        atomic = self.create_run("active-source-atomic")
        self.repository.enqueue_eligible(atomic, now=NOW)
        active_claim = self.claim_one("worker-active-atomic")
        self.repository.start(active_claim, now=NOW)
        before_failure = tuple(self.connection.iterdump())
        self.connection.execute(
            "CREATE TEMP TRIGGER fail_active_replan BEFORE UPDATE OF superseded_by "
            "ON workflow_runs WHEN OLD.run_id = 'active-source-atomic' "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        failed = self.repository.create_or_resume(
            atomic,
            replacement_definition,
            changed_stages=frozenset({StageId("left")}),
            now=NOW + timedelta(minutes=1),
        )
        self.connection.execute("DROP TRIGGER fail_active_replan")
        self.assertEqual(failed.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(tuple(self.connection.iterdump()), before_failure)

    def test_checkpoint_applicability_tampering_fails_closed(self) -> None:
        baseline = synthetic_heartbeat_definition()
        source = self.create_run("checkpoint-flags-source")
        self.complete_run(source)
        second_definition = synthetic_heartbeat_definition(incompatible=True)
        second = self.repository.create_or_resume(
            source,
            second_definition,
            changed_stages=frozenset({StageId("left")}),
            now=NOW + timedelta(minutes=1),
        ).value
        third_stages = tuple(
            StageDefinition(
                stage.id,
                stage.kind,
                StageVersion("stage-v3")
                if stage.id == StageId("right")
                else stage.version,
                stage.expected_outcome,
            )
            for stage in second_definition.stages
        )
        third_definition = WorkflowDefinition(
            WorkflowVersion("heartbeat-v3"),
            third_stages,
            second_definition.dependencies,
        )

        corruptions = (
            (second.id.value, "left", 1, 1),
            (second.id.value, "root", 0, 0),
            (source.value, "root", 0, 0),
        )
        for tampered_run, stage_id, reuse_eligible, current_applicable in corruptions:
            with self.subTest(
                tampered_run=tampered_run,
                stage_id=stage_id,
                reuse_eligible=reuse_eligible,
            ):
                original = self.connection.execute(
                    "SELECT reuse_eligible, current_applicable FROM stage_checkpoints "
                    "WHERE run_id = ? AND stage_id = ?",
                    (tampered_run, stage_id),
                ).fetchone()
                self.connection.execute(
                    "UPDATE stage_checkpoints SET reuse_eligible = ?, "
                    "current_applicable = ? WHERE run_id = ? AND stage_id = ?",
                    (reuse_eligible, current_applicable, tampered_run, stage_id),
                )
                definition = baseline if tampered_run == source.value else second_definition
                self.assertEqual(
                    self.repository.validate_run(RunId(tampered_run), definition).outcome,
                    RepositoryOutcome.CORRUPT,
                )
                before = tuple(self.connection.iterdump())
                compatible = self.repository.create_or_resume(
                    RunId(tampered_run),
                    definition,
                    changed_stages=frozenset(),
                    now=NOW + timedelta(minutes=2),
                )
                self.assertEqual(compatible.outcome, RepositoryOutcome.CORRUPT)
                if tampered_run == second.id.value:
                    chained = self.repository.create_or_resume(
                        second.id,
                        third_definition,
                        changed_stages=frozenset({StageId("right")}),
                        now=NOW + timedelta(minutes=2),
                    )
                    self.assertEqual(chained.outcome, RepositoryOutcome.CORRUPT)
                self.assertEqual(tuple(self.connection.iterdump()), before)
                self.connection.execute(
                    "UPDATE stage_checkpoints SET reuse_eligible = ?, "
                    "current_applicable = ? WHERE run_id = ? AND stage_id = ?",
                    (*original, tampered_run, stage_id),
                )
                self.assertTrue(
                    self.repository.validate_run(RunId(tampered_run), definition).accepted
                )

    def test_succeeded_stage_requires_current_checkpoint(self) -> None:
        baseline = synthetic_heartbeat_definition()
        source = self.create_run("missing-succeeded-checkpoint")
        self.complete_run(source)
        direct_checkpoint = self.connection.execute(
            "SELECT run_id, stage_id, source_run_id, source_stage_id, "
            "accepting_job_id, accepting_fence, outcome, reuse_eligible, "
            "current_applicable, accepted_at FROM stage_checkpoints "
            "WHERE run_id = ? AND stage_id = 'right'",
            (source.value,),
        ).fetchone()
        self.connection.execute(
            "DELETE FROM stage_checkpoints WHERE run_id = ? AND stage_id = 'right'",
            (source.value,),
        )
        before = tuple(self.connection.iterdump())
        self.assertEqual(
            self.repository.validate_run(source, baseline).outcome,
            RepositoryOutcome.CORRUPT,
        )
        compatible = self.repository.create_or_resume(
            source,
            baseline,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=1),
        )
        incompatible = self.repository.create_or_resume(
            source,
            synthetic_heartbeat_definition(incompatible=True),
            changed_stages=frozenset({StageId("left")}),
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(compatible.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(incompatible.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(tuple(self.connection.iterdump()), before)
        self.connection.execute(
            "INSERT INTO stage_checkpoints(run_id, stage_id, source_run_id, "
            "source_stage_id, accepting_job_id, accepting_fence, outcome, "
            "reuse_eligible, current_applicable, accepted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            direct_checkpoint,
        )
        self.assertTrue(self.repository.validate_run(source, baseline).accepted)

        second_definition = synthetic_heartbeat_definition(incompatible=True)
        second = self.repository.create_or_resume(
            source,
            second_definition,
            changed_stages=frozenset({StageId("left")}),
            now=NOW + timedelta(minutes=1),
        ).value
        inherited_checkpoint = self.connection.execute(
            "SELECT run_id, stage_id, source_run_id, source_stage_id, "
            "accepting_job_id, accepting_fence, outcome, reuse_eligible, "
            "current_applicable, accepted_at FROM stage_checkpoints "
            "WHERE run_id = ? AND stage_id = 'right'",
            (second.id.value,),
        ).fetchone()
        self.connection.execute(
            "DELETE FROM stage_checkpoints WHERE run_id = ? AND stage_id = 'right'",
            (second.id.value,),
        )
        before = tuple(self.connection.iterdump())
        third_definition = WorkflowDefinition(
            WorkflowVersion("heartbeat-v3"),
            second_definition.stages,
            second_definition.dependencies,
        )
        self.assertEqual(
            self.repository.validate_run(second.id, second_definition).outcome,
            RepositoryOutcome.CORRUPT,
        )
        compatible = self.repository.create_or_resume(
            second.id,
            second_definition,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=2),
        )
        chained = self.repository.create_or_resume(
            second.id,
            third_definition,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(compatible.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(chained.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(tuple(self.connection.iterdump()), before)
        self.connection.execute(
            "INSERT INTO stage_checkpoints(run_id, stage_id, source_run_id, "
            "source_stage_id, accepting_job_id, accepting_fence, outcome, "
            "reuse_eligible, current_applicable, accepted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            inherited_checkpoint,
        )
        self.assertTrue(
            self.repository.validate_run(second.id, second_definition).accepted
        )

    def test_repeated_qualification_fails_closed_when_durable_evidence_is_missing(self) -> None:
        self.assertTrue(RunSyntheticJobHeartbeat(self.repository).run().succeeded)
        self.connection.execute("DELETE FROM audit_events WHERE kind = 'job'")
        repeated = RunSyntheticJobHeartbeat(self.repository).run()
        self.assertFalse(repeated.succeeded)

    def test_replan_failure_is_atomic_and_retryable(self) -> None:
        self.assertTrue(RunSyntheticJobHeartbeat(self.repository).run().succeeded)
        source = RunId("heartbeat-main")
        replacement = self.repository.inspect(source).value.superseded_by
        self.assertIsNotNone(replacement)
        # Use a fresh completed source so a trigger can abort its first linkage.
        fresh = RunId("atomic-source")
        self.repository.create_or_resume(
            fresh,
            synthetic_heartbeat_definition(),
            changed_stages=frozenset(),
            now=NOW,
        )
        self.connection.execute(
            "CREATE TEMP TRIGGER fail_replan BEFORE UPDATE OF superseded_by ON workflow_runs "
            "WHEN OLD.run_id = 'atomic-source' BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        failed = self.repository.create_or_resume(
            fresh,
            synthetic_heartbeat_definition(incompatible=True),
            changed_stages=frozenset({StageId("left")}),
            now=NOW,
        )
        self.assertEqual(failed.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(
            self.connection.execute(
                "SELECT superseded_by FROM workflow_runs WHERE run_id = 'atomic-source'"
            ).fetchone(),
            (None,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE replanned_from = 'atomic-source'"
            ).fetchone(),
            (0,),
        )
        self.connection.execute("DROP TRIGGER fail_replan")
        retried = self.repository.create_or_resume(
            fresh,
            synthetic_heartbeat_definition(incompatible=True),
            changed_stages=frozenset({StageId("left")}),
            now=NOW,
        )
        self.assertTrue(retried.accepted)

    def test_replan_rejects_changed_set_mismatch_on_first_call_and_replay(self) -> None:
        source = self.create_run("change-mismatch")
        target = synthetic_heartbeat_definition(incompatible=True)
        self.assertEqual(
            self.repository.create_or_resume(
                source,
                target,
                changed_stages=frozenset({StageId("right")}),
                now=NOW,
            ).outcome,
            RepositoryOutcome.REJECTED,
        )
        accepted = self.repository.create_or_resume(
            source,
            target,
            changed_stages=frozenset({StageId("left")}),
            now=NOW,
        )
        self.assertTrue(accepted.accepted)
        self.assertEqual(
            self.repository.create_or_resume(
                source,
                target,
                changed_stages=frozenset({StageId("right")}),
                now=NOW,
            ).outcome,
            RepositoryOutcome.REJECTED,
        )

    def test_version_only_and_graph_only_incompatibility_each_replan(self) -> None:
        baseline = synthetic_heartbeat_definition()
        version_only = WorkflowDefinition(
            WorkflowVersion("heartbeat-v9"), baseline.stages, baseline.dependencies
        )
        version_source = self.create_run("version-only")
        version_result = self.repository.create_or_resume(
            version_source,
            version_only,
            changed_stages=frozenset(),
            now=NOW,
        )
        self.assertTrue(version_result.accepted)
        self.assertEqual(version_result.value.replanned_from, version_source)

        graph_source = self.create_run("graph-only")
        changed = synthetic_heartbeat_definition(incompatible=True)
        graph_only = WorkflowDefinition(
            baseline.version, changed.stages, changed.dependencies
        )
        graph_result = self.repository.create_or_resume(
            graph_source,
            graph_only,
            changed_stages=frozenset({StageId("left")}),
            now=NOW,
        )
        self.assertTrue(graph_result.accepted)
        self.assertEqual(graph_result.value.replanned_from, graph_source)

    def test_completed_version_only_replan_is_immediately_terminal_and_idempotent(self) -> None:
        baseline = synthetic_heartbeat_definition()
        source = self.create_run("completed-version-only")
        self.complete_run(source)
        version_only = WorkflowDefinition(
            WorkflowVersion("heartbeat-v9"), baseline.stages, baseline.dependencies
        )
        replanned = self.repository.create_or_resume(
            source,
            version_only,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=1),
        )
        self.assertTrue(replanned.accepted)
        self.assertEqual(replanned.value.state, RunState.SUCCEEDED)
        self.assertTrue(
            all(
                stage.state is StageState.SUCCEEDED
                and stage.reuse_eligible
                and stage.current_applicable
                for stage in replanned.value.stages
            )
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT from_state, to_state FROM workflow_transitions "
                "WHERE run_id = ? AND entity_kind = 'run'",
                (replanned.value.id.value,),
            ).fetchall(),
            [("waiting", "succeeded")],
        )

        self.connection.close()
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        resumed = self.repository.create_or_resume(
            replanned.value.id,
            version_only,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(resumed.outcome, RepositoryOutcome.REUSED)
        self.assertEqual(resumed.value.state, RunState.SUCCEEDED)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM workflow_transitions "
                "WHERE run_id = ? AND entity_kind = 'run'",
                (replanned.value.id.value,),
            ).fetchone(),
            (1,),
        )
        self.assertTrue(
            self.repository.validate_run(replanned.value.id, version_only).accepted
        )

    def test_chained_replan_preserves_inherited_checkpoint_invalidity(self) -> None:
        source = self.create_run("chained-replan")
        self.complete_run(source)
        second_definition = synthetic_heartbeat_definition(incompatible=True)
        second = self.repository.create_or_resume(
            source,
            second_definition,
            changed_stages=frozenset({StageId("left")}),
            now=NOW + timedelta(minutes=1),
        ).value
        second_flags = {
            stage.id: (stage.reuse_eligible, stage.current_applicable)
            for stage in second.stages
        }
        self.assertEqual(second_flags[StageId("left")], (False, False))
        self.assertEqual(second_flags[StageId("join")], (False, False))

        third_stages = tuple(
            StageDefinition(
                stage.id,
                stage.kind,
                StageVersion("stage-v3")
                if stage.id == StageId("right")
                else stage.version,
                stage.expected_outcome,
            )
            for stage in second_definition.stages
        )
        third_definition = WorkflowDefinition(
            WorkflowVersion("heartbeat-v3"),
            third_stages,
            second_definition.dependencies,
        )
        third = self.repository.create_or_resume(
            second.id,
            third_definition,
            changed_stages=frozenset({StageId("right")}),
            now=NOW + timedelta(minutes=2),
        ).value
        third_flags = {
            stage.id: (stage.reuse_eligible, stage.current_applicable)
            for stage in third.stages
        }
        self.assertEqual(third_flags[StageId("root")], (True, True))
        self.assertEqual(third_flags[StageId("left")], (False, False))
        self.assertEqual(third_flags[StageId("right")], (False, False))
        self.assertEqual(third_flags[StageId("join")], (False, False))
        self.assertTrue(
            self.repository.validate_run(third.id, third_definition).accepted
        )

        self.assertEqual(
            self.repository.enqueue_eligible(
                third.id, now=NOW + timedelta(minutes=2)
            ).value,
            2,
        )
        branches = self.repository.claim(
            WorkerId("worker-chained-replan"),
            capacity=2,
            now=NOW + timedelta(minutes=2),
            lease_duration=LEASE,
        ).value
        self.assertEqual(
            {claim.stage_id for claim in branches},
            {StageId("left"), StageId("right")},
        )
        for claim in branches:
            self.repository.start(claim, now=NOW + timedelta(minutes=2))
            self.repository.complete(
                claim,
                OutcomeKind.COMPLETED,
                now=NOW + timedelta(minutes=2),
            )
        self.assertEqual(
            self.repository.enqueue_eligible(
                third.id, now=NOW + timedelta(minutes=2)
            ).value,
            1,
        )
        join = self.claim_one("worker-chained-join", NOW + timedelta(minutes=2))
        self.assertEqual(join.stage_id, StageId("join"))
        self.repository.start(join, now=NOW + timedelta(minutes=2))
        self.repository.complete(
            join,
            OutcomeKind.COMPLETED,
            now=NOW + timedelta(minutes=2),
        )
        final = self.repository.inspect(third.id).value
        self.assertEqual(final.state, RunState.SUCCEEDED)
        self.assertTrue(all(stage.current_applicable for stage in final.stages))

    def test_replan_preserves_complete_original_history(self) -> None:
        source = self.create_run("history-source")
        self.complete_run(source)

        def original_history() -> tuple[object, ...]:
            return (
                self.connection.execute(
                    "SELECT * FROM workflow_stages WHERE run_id = ? ORDER BY stage_id",
                    (source.value,),
                ).fetchall(),
                self.connection.execute(
                    "SELECT * FROM workflow_dependencies WHERE run_id = ? "
                    "ORDER BY dependent_stage_id, prerequisite_stage_id",
                    (source.value,),
                ).fetchall(),
                self.connection.execute(
                    "SELECT * FROM jobs WHERE run_id = ? ORDER BY stage_id",
                    (source.value,),
                ).fetchall(),
                self.connection.execute(
                    "SELECT a.* FROM job_attempts a JOIN jobs j ON j.job_id = a.job_id "
                    "WHERE j.run_id = ? ORDER BY a.job_id, a.attempt",
                    (source.value,),
                ).fetchall(),
                self.connection.execute(
                    "SELECT * FROM stage_checkpoints WHERE run_id = ? ORDER BY stage_id",
                    (source.value,),
                ).fetchall(),
                self.connection.execute(
                    "SELECT * FROM audit_events WHERE kind = 'job' ORDER BY id"
                ).fetchall(),
            )

        before = original_history()
        replanned = self.repository.create_or_resume(
            source,
            synthetic_heartbeat_definition(incompatible=True),
            changed_stages=frozenset({StageId("left")}),
            now=NOW + timedelta(minutes=1),
        )
        self.assertTrue(replanned.accepted)
        self.connection.close()
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        self.assertEqual(original_history(), before)

    def test_process_death_during_replan_leaves_no_half_link(self) -> None:
        source = RunId("process-replan")
        self.repository.create_or_resume(
            source,
            synthetic_heartbeat_definition(),
            changed_stages=frozenset(),
            now=NOW,
        )
        script = (
            "import os,sys; from datetime import datetime,timezone; from pathlib import Path; "
            "from mechanical_librarian.infrastructure.persistence.sqlite import connect,migrate; "
            "from mechanical_librarian.adapters.persistence.jobs import SQLiteJobHeartbeatRepository; "
            "from mechanical_librarian.domain.workflow import RunId,StageId,synthetic_heartbeat_definition; "
            "c=connect(Path(sys.argv[1])); migrate(c); c.create_function('die',0,lambda: os._exit(24)); "
            "c.execute(\"CREATE TEMP TRIGGER die_replan BEFORE UPDATE OF superseded_by ON workflow_runs "
            "WHEN OLD.run_id = 'process-replan' BEGIN SELECT die(); END\"); "
            "SQLiteJobHeartbeatRepository(c).create_or_resume(RunId('process-replan'),"
            "synthetic_heartbeat_definition(incompatible=True),changed_stages=frozenset({StageId('left')}),"
            "now=datetime(2026,1,1,tzinfo=timezone.utc))"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 24, completed.stderr)
        self.assertEqual(
            self.connection.execute(
                "SELECT superseded_by FROM workflow_runs WHERE run_id = ?",
                (source.value,),
            ).fetchone(),
            (None,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE replanned_from = ?",
                (source.value,),
            ).fetchone(),
            (0,),
        )
        retried = self.repository.create_or_resume(
            source,
            synthetic_heartbeat_definition(incompatible=True),
            changed_stages=frozenset({StageId("left")}),
            now=NOW,
        )
        self.assertTrue(retried.accepted)

    def test_database_lock_fails_visibly_without_claim(self) -> None:
        run_id = self.create_run()
        self.repository.enqueue_eligible(run_id, now=NOW)
        contender = sqlite3.connect(self.path, isolation_level=None)
        contender.execute("PRAGMA busy_timeout = 1")
        contender.execute("BEGIN IMMEDIATE")
        try:
            connection = sqlite3.connect(self.path, isolation_level=None)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 1")
            repository = SQLiteJobHeartbeatRepository(connection)
            result = repository.claim(
                WorkerId("worker-locked"),
                capacity=1,
                now=NOW,
                lease_duration=LEASE,
            )
            connection.close()
        finally:
            contender.rollback()
            contender.close()
        self.assertEqual(result.outcome, RepositoryOutcome.UNAVAILABLE)
        self.assertEqual(
            self.connection.execute(
                "SELECT state FROM jobs WHERE run_id = ?", (run_id.value,)
            ).fetchone(),
            ("pending",),
        )

    def test_cancellation_revokes_renewal_without_mutation(self) -> None:
        run_id = self.create_run("renew-cancel")
        self.repository.enqueue_eligible(run_id, now=NOW)
        claim = self.claim_one("worker-renew-cancel")
        self.repository.start(claim, now=NOW)
        self.repository.cancel_run(run_id, now=NOW + timedelta(seconds=1))
        before = self.connection.execute(
            "SELECT lease_expires_at, updated_at FROM jobs WHERE job_id = ?",
            (claim.job_id.value,),
        ).fetchone()
        result = self.repository.renew(
            claim, now=NOW + timedelta(seconds=1), lease_duration=LEASE
        )
        after = self.connection.execute(
            "SELECT lease_expires_at, updated_at FROM jobs WHERE job_id = ?",
            (claim.job_id.value,),
        ).fetchone()
        self.assertEqual(result.outcome, RepositoryOutcome.REJECTED)
        self.assertEqual(after, before)

    def test_terminal_success_without_checkpoint_fails_closed(self) -> None:
        run_id = self.create_run("missing-terminal-outcome")
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-missing-terminal")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        checkpoint = self.connection.execute(
            "SELECT run_id, stage_id, source_run_id, source_stage_id, "
            "accepting_job_id, accepting_fence, outcome, reuse_eligible, "
            "current_applicable, accepted_at FROM stage_checkpoints "
            "WHERE run_id = ? AND stage_id = 'root'",
            (run_id.value,),
        ).fetchone()
        self.connection.execute(
            "DELETE FROM stage_checkpoints WHERE run_id = ? AND stage_id = 'root'",
            (run_id.value,),
        )
        self.assertTrue(self.repository.enqueue_eligible(run_id, now=NOW).accepted)
        snapshot = self.repository.inspect(run_id).value
        self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)
        states = {stage.id.value: stage.state for stage in snapshot.stages}
        self.assertEqual(states["left"], StageState.DEPENDENCY_FAILED)
        self.assertNotEqual(states["right"], StageState.WAITING)
        self.assertNotEqual(states["join"], StageState.WAITING)
        self.assertEqual(
            self.connection.execute(
                "SELECT entity_id, from_state, to_state FROM workflow_transitions "
                "WHERE run_id = ? AND entity_kind = 'stage' "
                "AND entity_id IN ('left', 'right', 'join') ORDER BY sequence",
                (run_id.value,),
            ).fetchall(),
            [
                ("left", "waiting", "dependency_failed"),
                ("join", "waiting", "dependency_failed"),
                ("right", "waiting", "cancelled"),
            ],
        )
        self.assertEqual(
            self.repository.claim(
                WorkerId("worker-after-invalid-dependency"),
                capacity=4,
                now=NOW,
                lease_duration=LEASE,
            ).value,
            (),
        )

        self.connection.execute(
            "INSERT INTO stage_checkpoints(run_id, stage_id, source_run_id, "
            "source_stage_id, accepting_job_id, accepting_fence, outcome, "
            "reuse_eligible, current_applicable, accepted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            checkpoint,
        )
        definition = synthetic_heartbeat_definition()
        self.assertTrue(self.repository.validate_run(run_id, definition).accepted)
        self.connection.close()
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        before = tuple(self.connection.iterdump())
        self.assertEqual(
            self.repository.enqueue_eligible(run_id, now=NOW).outcome,
            RepositoryOutcome.REUSED,
        )
        self.assertEqual(tuple(self.connection.iterdump()), before)
        self.assertTrue(self.repository.validate_run(run_id, definition).accepted)

    def test_maximum_domain_values_use_bounded_deduplication_key(self) -> None:
        run_id = RunId("r" * 64)
        definition = WorkflowDefinition(
            WorkflowVersion("v" * 64),
            (
                StageDefinition(
                    StageId("s" * 64), "root", StageVersion("x" * 64)
                ),
            ),
            (),
        )
        self.assertTrue(
            self.repository.create_or_resume(
                run_id, definition, changed_stages=frozenset(), now=NOW
            ).accepted
        )
        self.assertTrue(self.repository.enqueue_eligible(run_id, now=NOW).accepted)
        key = self.connection.execute(
            "SELECT dedup_key FROM jobs WHERE run_id = ?", (run_id.value,)
        ).fetchone()[0]
        self.assertLessEqual(len(key), 128)

    def test_checkpoint_provenance_columns_are_paired(self) -> None:
        run_id = self.create_run("checkpoint-pairs")
        timestamp = "2026-01-01T00:00:00.000000Z"
        invalid_values = (
            (None, None, None, 1),
            (run_id.value, None, None, None),
        )
        for source_run, source_stage, job_id, fence in invalid_values:
            with self.subTest(values=(source_run, source_stage, job_id, fence)):
                with self.assertRaises(sqlite3.IntegrityError):
                    self.connection.execute(
                        "INSERT INTO stage_checkpoints("
                        "run_id, stage_id, source_run_id, source_stage_id, "
                        "accepting_job_id, accepting_fence, outcome, reuse_eligible, "
                        "current_applicable, accepted_at) VALUES (?, 'root', ?, ?, ?, ?, "
                        "'completed', 1, 1, ?)",
                        (
                            run_id.value,
                            source_run,
                            source_stage,
                            job_id,
                            fence,
                            timestamp,
                        ),
                    )

    def test_state_changes_persist_authoritative_transition_facts(self) -> None:
        run_id = self.create_run("transition-facts")
        self.repository.enqueue_eligible(run_id, now=NOW)
        claim = self.claim_one("worker-transition")
        self.repository.start(claim, now=NOW)
        self.repository.complete(claim, OutcomeKind.COMPLETED, now=NOW)
        rows = self.connection.execute(
            "SELECT entity_kind, from_state, to_state, actor, reason "
            "FROM workflow_transitions WHERE run_id = ? ORDER BY rowid",
            (run_id.value,),
        ).fetchall()
        self.assertIn(
            (
                "job",
                "pending",
                "leased",
                "job repository",
                "persist durable job transition",
            ),
            rows,
        )
        self.assertIn(
            (
                "stage",
                "running",
                "succeeded",
                "job repository",
                "persist durable stage transition",
            ),
            rows,
        )
        self.assertIn(
            (
                "run",
                "waiting",
                "running",
                "job repository",
                "persist durable run transition",
            ),
            rows,
        )

    def test_transition_history_is_append_only_and_chain_validated(self) -> None:
        self.assertTrue(RunSyntheticJobHeartbeat(self.repository).run().succeeded)
        transition_id = self.connection.execute(
            "SELECT transition_id FROM workflow_transitions "
            "WHERE run_id = 'heartbeat-main' AND entity_kind = 'job' "
            "AND from_state = 'pending' AND to_state = 'leased' ORDER BY sequence LIMIT 1"
        ).fetchone()[0]
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "DELETE FROM workflow_transitions WHERE transition_id = ?",
                (transition_id,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                "UPDATE workflow_transitions SET to_state = 'cancelled' "
                "WHERE transition_id = ?",
                (transition_id,),
            )
        cases = (
            (
                "DELETE FROM workflow_transitions WHERE run_id = 'heartbeat-main'",
                (),
            ),
            (
                "UPDATE workflow_transitions SET to_state = 'cancelled' "
                "WHERE transition_id = ?",
                (transition_id,),
            ),
        )
        for statement, parameters in cases:
            with self.subTest(statement=statement):
                self.connection.execute("SAVEPOINT transition_tamper")
                self.connection.execute("DROP TRIGGER workflow_transitions_no_update")
                self.connection.execute("DROP TRIGGER workflow_transitions_no_delete")
                self.connection.execute(statement, parameters)
                self.assertFalse(RunSyntheticJobHeartbeat(self.repository).run().succeeded)
                self.connection.execute("ROLLBACK TO transition_tamper")
                self.connection.execute("RELEASE transition_tamper")

    def test_requalification_rejects_valid_shape_durable_tampering(self) -> None:
        self.assertTrue(RunSyntheticJobHeartbeat(self.repository).run().succeeded)
        cases = (
            (
                "UPDATE workflow_runs SET graph_digest = ? WHERE run_id = 'heartbeat-main'",
                ("0" * 64,),
            ),
            (
                "UPDATE workflow_stages SET version = 'stage-v9' "
                "WHERE run_id = 'heartbeat-main' AND stage_id = 'left'",
                (),
            ),
            (
                "UPDATE stage_checkpoints SET accepting_fence = accepting_fence + 1 "
                "WHERE run_id = 'heartbeat-main' AND stage_id = 'root'",
                (),
            ),
        )
        for statement, parameters in cases:
            with self.subTest(statement=statement):
                self.connection.execute("SAVEPOINT evidence_tamper")
                self.connection.execute(statement, parameters)
                self.assertFalse(RunSyntheticJobHeartbeat(self.repository).run().succeeded)
                self.connection.execute("ROLLBACK TO evidence_tamper")
                self.connection.execute("RELEASE evidence_tamper")

    def test_requalification_requires_durable_retry_attempt_evidence(self) -> None:
        first = RunSyntheticJobHeartbeat(self.repository).run()
        self.assertTrue(first.succeeded)
        self.assertEqual(first.inspection.retry_attempts, 2)
        attempts = self.connection.execute(
            "SELECT job_id, attempt, fence, worker_id, started_at, finished_at, "
            "outcome, failure FROM job_attempts WHERE job_id IN "
            "(SELECT job_id FROM jobs WHERE run_id = 'heartbeat-retry') "
            "ORDER BY attempt"
        ).fetchall()
        self.assertEqual(len(attempts), 2)
        self.connection.execute(
            "DELETE FROM job_attempts WHERE job_id IN "
            "(SELECT job_id FROM jobs WHERE run_id = 'heartbeat-retry')"
        )
        before = tuple(self.connection.iterdump())
        self.assertFalse(RunSyntheticJobHeartbeat(self.repository).run().succeeded)
        self.assertEqual(tuple(self.connection.iterdump()), before)

        self.connection.executemany(
            "INSERT INTO job_attempts(job_id, attempt, fence, worker_id, "
            "started_at, finished_at, outcome, failure) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            attempts,
        )
        restored = RunSyntheticJobHeartbeat(self.repository).run()
        self.assertTrue(restored.succeeded)
        self.assertEqual(restored.inspection.retry_attempts, 2)

    def test_supported_heartbeat_resumes_after_root_process_death(self) -> None:
        run_id = self.create_run("heartbeat-main")
        self.repository.enqueue_eligible(run_id, now=NOW)
        claim = self.claim_one("worker-interrupted-heartbeat")
        self.repository.start(claim, now=NOW)
        self.connection.close()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mechanical_librarian",
                "job-heartbeat",
                "--database",
                str(self.path),
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)

    def test_supported_heartbeat_resumes_after_reclaimed_root_process_death(self) -> None:
        run_id = self.create_run("heartbeat-main")
        self.repository.enqueue_eligible(run_id, now=NOW)
        old = self.claim_one("worker-root-old")
        self.repository.start(old, now=NOW)
        reclaimed = self.repository.claim(
            WorkerId("worker-root-reclaimed"),
            capacity=1,
            now=NOW + timedelta(seconds=6),
            lease_duration=LEASE,
        ).value[0]
        self.assertGreater(reclaimed.fence, old.fence)
        self.connection.close()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mechanical_librarian",
                "job-heartbeat",
                "--database",
                str(self.path),
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        result = RunSyntheticJobHeartbeat(self.repository).run()
        self.assertTrue(result.succeeded)
        source = self.repository.inspect(run_id).value
        root = next(job for job in source.jobs if job.stage_id == StageId("root"))
        self.assertEqual(root.state.value, "succeeded")
        self.assertGreater(root.fence, reclaimed.fence)

    def test_supported_heartbeat_resumes_after_branch_process_death(self) -> None:
        run_id = self.create_run("heartbeat-main")
        self.repository.enqueue_eligible(run_id, now=NOW)
        root = self.claim_one("worker-before-interruption")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        self.repository.enqueue_eligible(run_id, now=NOW)
        branches = self.repository.claim(
            WorkerId("worker-interrupted-branch"),
            capacity=2,
            now=NOW,
            lease_duration=LEASE,
        ).value
        self.repository.start(branches[0], now=NOW)
        self.connection.close()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mechanical_librarian",
                "job-heartbeat",
                "--database",
                str(self.path),
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)

    def test_replan_rejects_corrupt_source_checkpoint_provenance(self) -> None:
        source = self.create_run("corrupt-replan-source")
        self.complete_run(source)
        self.connection.execute(
            "UPDATE stage_checkpoints SET accepting_fence = accepting_fence + 1 "
            "WHERE run_id = ? AND stage_id = 'root'",
            (source.value,),
        )
        self.assertEqual(
            self.repository.validate_run(source, synthetic_heartbeat_definition()).outcome,
            RepositoryOutcome.CORRUPT,
        )
        result = self.repository.create_or_resume(
            source,
            synthetic_heartbeat_definition(incompatible=True),
            changed_stages=frozenset({StageId("left")}),
            now=NOW,
        )
        self.assertEqual(result.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE replanned_from = ?",
                (source.value,),
            ).fetchone(),
            (0,),
        )

    def test_direct_checkpoint_requires_stable_completion_audit(self) -> None:
        source = self.create_run("missing-completion-audit")
        self.repository.enqueue_eligible(source, now=NOW)
        root = self.claim_one("worker-audit-root")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        root_audit = self.connection.execute(
            "SELECT id, kind, created_at, summary FROM audit_events"
        ).fetchone()
        self.assertIsNotNone(root_audit)
        self.complete_run(source)
        definition = synthetic_heartbeat_definition()
        replacement_definition = synthetic_heartbeat_definition(incompatible=True)
        self.assertTrue(self.repository.validate_run(source, definition).accepted)

        corruptions = (
            "DELETE FROM audit_events WHERE id = ?",
            "UPDATE audit_events SET kind = 'other' WHERE id = ?",
            "UPDATE audit_events SET summary = 'job failed' WHERE id = ?",
        )
        for statement in corruptions:
            with self.subTest(statement=statement):
                self.connection.execute(statement, (root_audit[0],))
                self.assertEqual(
                    self.repository.validate_run(source, definition).outcome,
                    RepositoryOutcome.CORRUPT,
                )
                self.connection.execute(
                    "INSERT OR REPLACE INTO audit_events(id, kind, created_at, summary) "
                    "VALUES (?, ?, ?, ?)",
                    root_audit,
                )
                self.assertTrue(
                    self.repository.validate_run(source, definition).accepted
                )

        self.connection.execute(
            "DELETE FROM audit_events WHERE id = ?", (root_audit[0],)
        )
        before = tuple(self.connection.iterdump())
        compatible = self.repository.create_or_resume(
            source,
            definition,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=1),
        )
        incompatible = self.repository.create_or_resume(
            source,
            replacement_definition,
            changed_stages=frozenset({StageId("left")}),
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(compatible.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(incompatible.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(tuple(self.connection.iterdump()), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT superseded_by FROM workflow_runs WHERE run_id = ?",
                (source.value,),
            ).fetchone(),
            (None,),
        )
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE replanned_from = ?",
                (source.value,),
            ).fetchone(),
            (0,),
        )

    def test_inherited_checkpoint_requires_origin_completion_audit(self) -> None:
        source = self.create_run("inherited-origin-audit")
        self.complete_run(source)
        baseline = synthetic_heartbeat_definition()
        second_definition = WorkflowDefinition(
            WorkflowVersion("heartbeat-v2"), baseline.stages, baseline.dependencies
        )
        second = self.repository.create_or_resume(
            source,
            second_definition,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=1),
        ).value
        third_definition = WorkflowDefinition(
            WorkflowVersion("heartbeat-v3"), baseline.stages, baseline.dependencies
        )
        third = self.repository.create_or_resume(
            second.id,
            third_definition,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=2),
        ).value
        self.assertTrue(self.repository.validate_run(second.id, second_definition).accepted)
        self.assertTrue(self.repository.validate_run(third.id, third_definition).accepted)

        audits = self.connection.execute(
            "SELECT id, kind, created_at, summary FROM audit_events ORDER BY id"
        ).fetchall()
        self.connection.execute("DELETE FROM audit_events")
        before = tuple(self.connection.iterdump())
        self.assertEqual(
            self.repository.validate_run(second.id, second_definition).outcome,
            RepositoryOutcome.CORRUPT,
        )
        self.assertEqual(
            self.repository.validate_run(third.id, third_definition).outcome,
            RepositoryOutcome.CORRUPT,
        )
        compatible = self.repository.create_or_resume(
            third.id,
            third_definition,
            changed_stages=frozenset(),
            now=NOW + timedelta(minutes=3),
        )
        fourth_definition = WorkflowDefinition(
            WorkflowVersion("heartbeat-v4"),
            tuple(
                StageDefinition(
                    stage.id,
                    stage.kind,
                    StageVersion("stage-v4")
                    if stage.id == StageId("right")
                    else stage.version,
                    stage.expected_outcome,
                )
                for stage in baseline.stages
            ),
            baseline.dependencies,
        )
        incompatible = self.repository.create_or_resume(
            third.id,
            fourth_definition,
            changed_stages=frozenset({StageId("right")}),
            now=NOW + timedelta(minutes=3),
        )
        self.assertEqual(compatible.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(incompatible.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(tuple(self.connection.iterdump()), before)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE replanned_from = ?",
                (third.id.value,),
            ).fetchone(),
            (0,),
        )

        self.connection.executemany(
            "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, ?, ?, ?)",
            audits,
        )
        self.assertTrue(self.repository.validate_run(source, baseline).accepted)
        self.assertTrue(self.repository.validate_run(second.id, second_definition).accepted)
        self.assertTrue(self.repository.validate_run(third.id, third_definition).accepted)

    def test_reused_checkpoint_requires_immediate_replan_source(self) -> None:
        self.assertTrue(RunSyntheticJobHeartbeat(self.repository).run().succeeded)
        source = self.repository.inspect(RunId("heartbeat-main")).value
        self.assertIsNotNone(source.superseded_by)
        replacement = source.superseded_by
        replacement_definition = synthetic_heartbeat_definition(incompatible=True)
        unrelated = self.create_run("unrelated-checkpoint-source")
        self.complete_run(unrelated)
        original_source = self.connection.execute(
            "SELECT source_run_id FROM stage_checkpoints "
            "WHERE run_id = ? AND stage_id = 'root'",
            (replacement.value,),
        ).fetchone()
        self.assertEqual(original_source, (source.id.value,))

        for forged_source in (replacement, unrelated):
            with self.subTest(forged_source=forged_source.value):
                self.connection.execute(
                    "UPDATE stage_checkpoints SET source_run_id = ? "
                    "WHERE run_id = ? AND stage_id = 'root'",
                    (forged_source.value, replacement.value),
                )
                before = self.connection.execute(
                    "SELECT * FROM stage_checkpoints WHERE run_id = ? ORDER BY stage_id",
                    (replacement.value,),
                ).fetchall()
                self.assertEqual(
                    self.repository.validate_run(
                        replacement, replacement_definition
                    ).outcome,
                    RepositoryOutcome.CORRUPT,
                )
                self.assertFalse(
                    RunSyntheticJobHeartbeat(self.repository).run().succeeded
                )
                self.assertEqual(
                    self.connection.execute(
                        "SELECT * FROM stage_checkpoints "
                        "WHERE run_id = ? ORDER BY stage_id",
                        (replacement.value,),
                    ).fetchall(),
                    before,
                )
                self.connection.execute(
                    "UPDATE stage_checkpoints SET source_run_id = ? "
                    "WHERE run_id = ? AND stage_id = 'root'",
                    (source.id.value, replacement.value),
                )

        third_stages = tuple(
            StageDefinition(
                stage.id,
                stage.kind,
                StageVersion("stage-v3")
                if stage.id == StageId("right")
                else stage.version,
                stage.expected_outcome,
            )
            for stage in replacement_definition.stages
        )
        third_definition = WorkflowDefinition(
            WorkflowVersion("heartbeat-v3"),
            third_stages,
            replacement_definition.dependencies,
        )
        self.connection.execute(
            "UPDATE stage_checkpoints SET source_run_id = ? "
            "WHERE run_id = ? AND stage_id = 'root'",
            (unrelated.value, replacement.value),
        )
        rejected = self.repository.create_or_resume(
            replacement,
            third_definition,
            changed_stages=frozenset({StageId("right")}),
            now=NOW + timedelta(minutes=2),
        )
        self.assertEqual(rejected.outcome, RepositoryOutcome.CORRUPT)
        self.assertEqual(
            self.connection.execute(
                "SELECT count(*) FROM workflow_runs WHERE replanned_from = ?",
                (replacement.value,),
            ).fetchone(),
            (0,),
        )
        self.connection.execute(
            "UPDATE stage_checkpoints SET source_run_id = ? "
            "WHERE run_id = ? AND stage_id = 'root'",
            (source.id.value, replacement.value),
        )
        self.assertTrue(
            self.repository.validate_run(
                replacement, replacement_definition
            ).accepted
        )

    def test_supported_heartbeat_resumes_after_negative_phase_process_death(self) -> None:
        self.complete_heartbeat_main()
        negative = self.create_run("heartbeat-negative")
        self.repository.enqueue_eligible(negative, now=NOW)
        claim = self.claim_one("worker-interrupted-negative")
        self.repository.start(claim, now=NOW)
        self.connection.close()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mechanical_librarian",
                "job-heartbeat",
                "--database",
                str(self.path),
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)

    def test_supported_heartbeat_resumes_from_retry_wait(self) -> None:
        self.complete_heartbeat_main()
        negative = self.create_run("heartbeat-negative")
        self.repository.enqueue_eligible(negative, now=NOW)
        root = self.claim_one("worker-negative-setup")
        self.repository.start(root, now=NOW)
        self.repository.complete(root, OutcomeKind.COMPLETED, now=NOW)
        self.repository.enqueue_eligible(negative, now=NOW)
        branches = self.repository.claim(
            WorkerId("worker-negative-setup"), capacity=2, now=NOW, lease_duration=LEASE
        ).value
        for claim in branches:
            self.repository.start(claim, now=NOW)
        left = next(claim for claim in branches if claim.stage_id == StageId("left"))
        right = next(claim for claim in branches if claim.stage_id == StageId("right"))
        self.repository.complete(left, OutcomeKind.COMPLETED, now=NOW)
        self.repository.fail(right, FailureKind.SYNTHETIC_TERMINAL, now=NOW)
        retry = self.create_run("heartbeat-retry")
        self.repository.enqueue_eligible(retry, now=NOW)
        retry_claim = self.claim_one("worker-retry-setup")
        self.repository.start(retry_claim, now=NOW)
        self.repository.fail(retry_claim, FailureKind.SYNTHETIC_RETRYABLE, now=NOW)
        self.assertTrue(RunSyntheticJobHeartbeat(self.repository).run().succeeded)

    def test_supported_heartbeat_resumes_after_second_retry_process_death(self) -> None:
        retry = self.create_run("heartbeat-retry")
        self.repository.enqueue_eligible(retry, now=NOW)
        first = self.claim_one("worker-retry-first")
        self.repository.start(first, now=NOW)
        self.repository.fail(first, FailureKind.SYNTHETIC_RETRYABLE, now=NOW)
        second = self.claim_one("worker-retry-second", NOW + timedelta(seconds=5))
        self.repository.start(second, now=NOW + timedelta(seconds=5))
        self.connection.close()
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mechanical_librarian",
                "job-heartbeat",
                "--database",
                str(self.path),
            ],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.connection = connect(self.path)
        migrate(self.connection)
        self.repository = SQLiteJobHeartbeatRepository(self.connection)
        result = RunSyntheticJobHeartbeat(self.repository).run()
        self.assertTrue(result.succeeded)
        snapshot = self.repository.inspect(retry).value
        self.assertEqual(snapshot.state, RunState.FAILED_TERMINAL)
        self.assertEqual(snapshot.attempt_count, 2)
        self.assertEqual(snapshot.jobs[0].state.value, "failed_terminal")


if __name__ == "__main__":
    unittest.main()
