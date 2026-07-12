"""SQLite durable-job repository for the bounded F02 synthetic workflow."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from mechanical_librarian.application.job_heartbeat import (
    JobClaim,
    JobHeartbeatRepository,
    JobSnapshot,
    RepositoryOutcome,
    RepositoryResult,
    RunSnapshot,
    StageSnapshot,
)
from mechanical_librarian.domain.identifiers import AuditEventId, JobId
from mechanical_librarian.domain.lifecycle import JobState, RunState, StageState, Transition, transition
from mechanical_librarian.domain.workflow import (
    FailureKind,
    Dependency,
    OutcomeKind,
    RunId,
    StageId,
    StageDefinition,
    StageVersion,
    WorkerId,
    WorkflowDefinition,
    WorkflowVersion,
    validate_run_transition,
    validate_stage_transition,
)


_TERMINAL_STAGE_STATES = {
    StageState.SUCCEEDED.value,
    StageState.FAILED_TERMINAL.value,
    StageState.CANCELLED.value,
    StageState.DEPENDENCY_FAILED.value,
    StageState.DEPENDENCY_CANCELLED.value,
}
_TERMINAL_JOB_STATES = {
    JobState.SUCCEEDED.value,
    JobState.FAILED_TERMINAL.value,
    JobState.CANCELLED.value,
}
def _timestamp(value: datetime) -> str:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("job timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )

def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


class SQLiteJobHeartbeatRepository(JobHeartbeatRepository):
    """One-connection adapter with explicit operation-owned transactions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def _record_edge(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        entity_kind: str,
        entity_id: str,
        current: JobState | RunState | StageState,
        target: JobState | RunState | StageState,
        timestamp: str,
    ) -> None:
        if current is target:
            return
        if type(current) is JobState and type(target) is JobState:
            record: Transition = transition(
                current,
                target,
                actor="job repository",
                reason="persist durable job transition",
            )
        elif type(current) is RunState and type(target) is RunState:
            record = validate_run_transition(current, target)
        elif type(current) is StageState and type(target) is StageState:
            record = validate_stage_transition(current, target)
        else:
            raise ValueError("workflow transition types do not match")
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM workflow_transitions WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
        )
        transition_id = _stable_id(
            "transition",
            run_id,
            entity_kind,
            entity_id,
            record.from_state.value,
            record.to_state.value,
            timestamp,
            str(sequence),
        )
        connection.execute(
            "INSERT INTO workflow_transitions(transition_id, run_id, sequence, entity_kind, entity_id, "
            "from_state, to_state, actor, reason, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                transition_id,
                run_id,
                sequence,
                entity_kind,
                entity_id,
                record.from_state.value,
                record.to_state.value,
                record.actor,
                record.reason,
                timestamp,
            ),
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        if self._connection.in_transaction:
            raise RuntimeError("nested job transactions are not supported")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def create_or_resume(
        self,
        run_id: RunId,
        definition: WorkflowDefinition,
        *,
        changed_stages: frozenset[StageId],
        now: datetime,
    ) -> RepositoryResult[RunSnapshot]:
        if (
            type(run_id) is not RunId
            or type(definition) is not WorkflowDefinition
            or type(changed_stages) is not frozenset
            or any(type(stage_id) is not StageId for stage_id in changed_stages)
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            with self._transaction() as connection:
                stored = connection.execute(
                    "SELECT definition_version, graph_digest, superseded_by "
                    "FROM workflow_runs WHERE run_id = ?",
                    (run_id.value,),
                ).fetchone()
                if stored is None:
                    if changed_stages:
                        return RepositoryResult(RepositoryOutcome.REJECTED)
                    self._insert_run(connection, run_id, definition, timestamp)
                    selected = run_id
                    outcome = RepositoryOutcome.ACCEPTED
                elif stored[0] == definition.version.value and stored[1] == definition.digest:
                    if changed_stages:
                        return RepositoryResult(RepositoryOutcome.REJECTED)
                    selected = run_id
                    outcome = RepositoryOutcome.REUSED
                else:
                    source_definition = self._stored_definition(connection, run_id)
                    if source_definition is None or not self.validate_run(
                        run_id, source_definition
                    ).accepted:
                        return RepositoryResult(RepositoryOutcome.CORRUPT)
                    derived_changes = self._derive_changed_stages(
                        connection, run_id, definition
                    )
                    if changed_stages != derived_changes:
                        return RepositoryResult(RepositoryOutcome.REJECTED)
                    selected = self._replan(
                        connection,
                        run_id,
                        definition,
                        derived_changes,
                        timestamp,
                        stored[2],
                    )
                    outcome = RepositoryOutcome.ACCEPTED
            inspected = self.inspect(selected)
            if not inspected.accepted:
                return inspected
            validated = self.validate_run(selected, definition)
            if not validated.accepted:
                return RepositoryResult(validated.outcome)
            return RepositoryResult(outcome, inspected.value)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def validate_run(
        self, run_id: RunId, definition: WorkflowDefinition
    ) -> RepositoryResult[None]:
        """Validate the complete persisted definition and checkpoint provenance."""

        if type(run_id) is not RunId or type(definition) is not WorkflowDefinition:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            run = self._connection.execute(
                "SELECT definition_version, graph_digest FROM workflow_runs WHERE run_id = ?",
                (run_id.value,),
            ).fetchone()
            if run is None:
                return RepositoryResult(RepositoryOutcome.NOT_FOUND)
            if run != (definition.version.value, definition.digest):
                return RepositoryResult(RepositoryOutcome.CORRUPT)
            positions = {
                stage_id: position
                for position, stage_id in enumerate(definition.topological_order())
            }
            expected_stages = {
                (
                    stage.id.value,
                    positions[stage.id],
                    stage.kind,
                    stage.version.value,
                    stage.expected_outcome.value,
                )
                for stage in definition.stages
            }
            stored_stages = set(
                self._connection.execute(
                    "SELECT stage_id, position, kind, version, expected_outcome "
                    "FROM workflow_stages WHERE run_id = ?",
                    (run_id.value,),
                )
            )
            expected_edges = {
                (edge.dependent.value, edge.prerequisite.value, 1)
                for edge in definition.dependencies
            }
            stored_edges = set(
                self._connection.execute(
                    "SELECT dependent_stage_id, prerequisite_stage_id, required "
                    "FROM workflow_dependencies WHERE run_id = ?",
                    (run_id.value,),
                )
            )
            if stored_stages != expected_stages or stored_edges != expected_edges:
                return RepositoryResult(RepositoryOutcome.CORRUPT)
            checkpoints = self._connection.execute(
                "SELECT stage_id, source_run_id, source_stage_id, accepting_job_id, "
                "accepting_fence, outcome FROM stage_checkpoints WHERE run_id = ?",
                (run_id.value,),
            ).fetchall()
            for stage_id, source_run, source_stage, job_id, fence, outcome in checkpoints:
                if source_run is not None:
                    source = self._connection.execute(
                        "SELECT outcome FROM stage_checkpoints WHERE run_id = ? AND stage_id = ?",
                        (source_run, source_stage),
                    ).fetchone()
                    if source_stage != stage_id or source != (outcome,):
                        return RepositoryResult(RepositoryOutcome.CORRUPT)
                    continue
                accepted = self._connection.execute(
                    "SELECT j.run_id, j.stage_id, j.state, j.fence, a.outcome, a.finished_at "
                    "FROM jobs j JOIN job_attempts a ON a.job_id = j.job_id AND a.fence = ? "
                    "WHERE j.job_id = ?",
                    (fence, job_id),
                ).fetchone()
                if (
                    accepted is None
                    or accepted[0] != run_id.value
                    or accepted[1] != stage_id
                    or accepted[2] != JobState.SUCCEEDED.value
                    or accepted[3] != fence
                    or accepted[4] != outcome
                    or accepted[5] is None
                ):
                    return RepositoryResult(RepositoryOutcome.CORRUPT)
            current_run_state = self._connection.execute(
                "SELECT state FROM workflow_runs WHERE run_id = ?", (run_id.value,)
            ).fetchone()[0]
            durable_states: dict[tuple[str, str], JobState | RunState | StageState] = {
                ("run", run_id.value): RunState(current_run_state)
            }
            durable_states.update(
                {
                    ("stage", row[0]): StageState(row[1])
                    for row in self._connection.execute(
                        "SELECT stage_id, state FROM workflow_stages WHERE run_id = ?",
                        (run_id.value,),
                    )
                }
            )
            durable_states.update(
                {
                    ("job", row[0]): JobState(row[1])
                    for row in self._connection.execute(
                        "SELECT job_id, state FROM jobs WHERE run_id = ?",
                        (run_id.value,),
                    )
                }
            )
            chain_states: dict[tuple[str, str], JobState | RunState | StageState] = {
                key: (
                    RunState.WAITING
                    if key[0] == "run"
                    else StageState.WAITING
                    if key[0] == "stage"
                    else JobState.PENDING
                )
                for key in durable_states
            }
            transitions = self._connection.execute(
                "SELECT sequence, entity_kind, entity_id, from_state, to_state, actor, reason "
                "FROM workflow_transitions WHERE run_id = ? ORDER BY sequence",
                (run_id.value,),
            ).fetchall()
            if [row[0] for row in transitions] != list(range(1, len(transitions) + 1)):
                return RepositoryResult(RepositoryOutcome.CORRUPT)
            for _sequence, entity_kind, entity_id, from_state, to_state, actor, reason in transitions:
                key = (entity_kind, entity_id)
                if key not in chain_states:
                    return RepositoryResult(RepositoryOutcome.CORRUPT)
                if entity_kind == "job":
                    current = chain_states[key]
                    if current != JobState(from_state):
                        return RepositoryResult(RepositoryOutcome.CORRUPT)
                    record = transition(
                        current,
                        JobState(to_state),
                        actor=actor,
                        reason=reason,
                    )
                elif entity_kind == "stage":
                    current = chain_states[key]
                    if current != StageState(from_state):
                        return RepositoryResult(RepositoryOutcome.CORRUPT)
                    record = validate_stage_transition(
                        current, StageState(to_state)
                    )
                elif entity_kind == "run" and entity_id == run_id.value:
                    current = chain_states[key]
                    if current != RunState(from_state):
                        return RepositoryResult(RepositoryOutcome.CORRUPT)
                    record = validate_run_transition(
                        current, RunState(to_state)
                    )
                else:
                    return RepositoryResult(RepositoryOutcome.CORRUPT)
                if record.actor != actor or record.reason != reason:
                    return RepositoryResult(RepositoryOutcome.CORRUPT)
                chain_states[key] = record.to_state
            if chain_states != durable_states:
                return RepositoryResult(RepositoryOutcome.CORRUPT)
            return RepositoryResult(RepositoryOutcome.ACCEPTED)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.CORRUPT)

    @staticmethod
    def _stored_definition(
        connection: sqlite3.Connection, run_id: RunId
    ) -> WorkflowDefinition | None:
        run = connection.execute(
            "SELECT definition_version FROM workflow_runs WHERE run_id = ?",
            (run_id.value,),
        ).fetchone()
        if run is None:
            return None
        stages = tuple(
            StageDefinition(
                StageId(row[0]),
                str(row[1]),
                StageVersion(row[2]),
                OutcomeKind(row[3]),
            )
            for row in connection.execute(
                "SELECT stage_id, kind, version, expected_outcome "
                "FROM workflow_stages WHERE run_id = ? ORDER BY position",
                (run_id.value,),
            )
        )
        dependencies = tuple(
            Dependency(StageId(row[0]), StageId(row[1]))
            for row in connection.execute(
                "SELECT prerequisite_stage_id, dependent_stage_id "
                "FROM workflow_dependencies WHERE run_id = ? "
                "ORDER BY prerequisite_stage_id, dependent_stage_id",
                (run_id.value,),
            )
        )
        return WorkflowDefinition(WorkflowVersion(run[0]), stages, dependencies)

    @staticmethod
    def _derive_changed_stages(
        connection: sqlite3.Connection,
        source: RunId,
        definition: WorkflowDefinition,
    ) -> frozenset[StageId]:
        stored_stages = {
            row[0]: (row[1], row[2], row[3])
            for row in connection.execute(
                "SELECT stage_id, kind, version, expected_outcome FROM workflow_stages "
                "WHERE run_id = ?",
                (source.value,),
            )
        }
        requested_stages = {
            stage.id.value: (
                stage.kind,
                stage.version.value,
                stage.expected_outcome.value,
            )
            for stage in definition.stages
        }
        if set(stored_stages) != set(requested_stages):
            raise ValueError("workflow replan cannot change the bounded stage identity set")
        changed = {
            StageId(stage_id)
            for stage_id, requested in requested_stages.items()
            if stored_stages[stage_id] != requested
        }
        stored_edges = {
            (row[0], row[1])
            for row in connection.execute(
                "SELECT prerequisite_stage_id, dependent_stage_id "
                "FROM workflow_dependencies WHERE run_id = ?",
                (source.value,),
            )
        }
        requested_edges = {
            (edge.prerequisite.value, edge.dependent.value)
            for edge in definition.dependencies
        }
        for _prerequisite, dependent in stored_edges ^ requested_edges:
            changed.add(StageId(dependent))
        return frozenset(changed)

    def _insert_run(
        self,
        connection: sqlite3.Connection,
        run_id: RunId,
        definition: WorkflowDefinition,
        timestamp: str,
        *,
        replanned_from: RunId | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO workflow_runs(run_id, definition_version, graph_digest, state, "
            "cancellation_requested, replanned_from, superseded_by, created_at, updated_at) "
            "VALUES (?, ?, ?, 'waiting', 0, ?, NULL, ?, ?)",
            (
                run_id.value,
                definition.version.value,
                definition.digest,
                replanned_from.value if replanned_from is not None else None,
                timestamp,
                timestamp,
            ),
        )
        positions = {stage_id: index for index, stage_id in enumerate(definition.topological_order())}
        for stage in definition.stages:
            connection.execute(
                "INSERT INTO workflow_stages(run_id, stage_id, position, kind, version, expected_outcome, state) "
                "VALUES (?, ?, ?, ?, ?, ?, 'waiting')",
                (
                    run_id.value,
                    stage.id.value,
                    positions[stage.id],
                    stage.kind,
                    stage.version.value,
                    stage.expected_outcome.value,
                ),
            )
        for edge in definition.dependencies:
            connection.execute(
                "INSERT INTO workflow_dependencies(run_id, dependent_stage_id, prerequisite_stage_id, required) "
                "VALUES (?, ?, ?, 1)",
                (run_id.value, edge.dependent.value, edge.prerequisite.value),
            )

    def _replan(
        self,
        connection: sqlite3.Connection,
        source: RunId,
        definition: WorkflowDefinition,
        changed_stages: frozenset[StageId],
        timestamp: str,
        stored_replacement: object,
    ) -> RunId:
        replacement = RunId(_stable_id("replan", source.value, definition.version.value, definition.digest))
        if stored_replacement is not None:
            if stored_replacement != replacement.value:
                raise ValueError("workflow run already has a different replacement")
            row = connection.execute(
                "SELECT definition_version, graph_digest FROM workflow_runs WHERE run_id = ?",
                (replacement.value,),
            ).fetchone()
            if row != (definition.version.value, definition.digest):
                raise ValueError("stored workflow replacement is incompatible")
            self._refresh_run_state(connection, replacement, timestamp)
            return replacement

        self._insert_run(
            connection,
            replacement,
            definition,
            timestamp,
            replanned_from=source,
        )
        source_checkpoint_rows = {
            row[0]: (row[1], bool(row[2]), bool(row[3]))
            for row in connection.execute(
                "SELECT stage_id, outcome, reuse_eligible, current_applicable "
                "FROM stage_checkpoints WHERE run_id = ?",
                (source.value,),
            )
        }
        inherited_invalid = frozenset(
            StageId(stage_id)
            for stage_id, (_outcome, reuse_eligible, current_applicable)
            in source_checkpoint_rows.items()
            if not reuse_eligible or not current_applicable
        )
        affected = definition.descendants(changed_stages | inherited_invalid)
        source_checkpoints = {
            stage_id: row[0] for stage_id, row in source_checkpoint_rows.items()
        }
        for stage in definition.stages:
            outcome = source_checkpoints.get(stage.id.value)
            if outcome is None:
                continue
            eligible = stage.id not in affected
            connection.execute(
                "INSERT INTO stage_checkpoints(run_id, stage_id, source_run_id, source_stage_id, "
                "accepting_job_id, accepting_fence, outcome, reuse_eligible, current_applicable, accepted_at) "
                "VALUES (?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)",
                (
                    replacement.value,
                    stage.id.value,
                    source.value,
                    stage.id.value,
                    outcome,
                    int(eligible),
                    int(eligible),
                    timestamp,
                ),
            )
            if eligible:
                self._record_edge(
                    connection,
                    replacement.value,
                    "stage",
                    stage.id.value,
                    StageState.WAITING,
                    StageState.SUCCEEDED,
                    timestamp,
                )
                connection.execute(
                    "UPDATE workflow_stages SET state = 'succeeded' WHERE run_id = ? AND stage_id = ?",
                    (replacement.value, stage.id.value),
                )
        source_state = RunState(
            connection.execute(
                "SELECT state FROM workflow_runs WHERE run_id = ?", (source.value,)
            ).fetchone()[0]
        )
        self._record_edge(
            connection,
            source.value,
            "run",
            source.value,
            source_state,
            RunState.SUPERSEDED,
            timestamp,
        )
        connection.execute(
            "UPDATE workflow_runs SET state = 'superseded', superseded_by = ?, updated_at = ? "
            "WHERE run_id = ? AND superseded_by IS NULL",
            (replacement.value, timestamp, source.value),
        )
        self._refresh_run_state(connection, replacement, timestamp)
        return replacement

    def enqueue_eligible(self, run_id: RunId, *, now: datetime) -> RepositoryResult[int]:
        if type(run_id) is not RunId:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            created = 0
            with self._transaction() as connection:
                run = connection.execute(
                    "SELECT state, cancellation_requested FROM workflow_runs WHERE run_id = ?",
                    (run_id.value,),
                ).fetchone()
                if run is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if run[0] in {RunState.CANCELLED.value, RunState.SUPERSEDED.value, RunState.FAILED_TERMINAL.value} or run[1] != 0:
                    return RepositoryResult(RepositoryOutcome.REUSED, 0)
                stages = connection.execute(
                    "SELECT stage_id, kind, version, state FROM workflow_stages "
                    "WHERE run_id = ? ORDER BY position",
                    (run_id.value,),
                ).fetchall()
                for stage_id, kind, version, state in stages:
                    if state in _TERMINAL_STAGE_STATES:
                        continue
                    exists = connection.execute(
                        "SELECT 1 FROM jobs WHERE run_id = ? AND stage_id = ?",
                        (run_id.value, stage_id),
                    ).fetchone()
                    if exists is not None:
                        continue
                    prerequisite_rows = connection.execute(
                        "SELECT d.prerequisite_stage_id, s.state, c.outcome, c.current_applicable "
                        "FROM workflow_dependencies d "
                        "JOIN workflow_stages s ON s.run_id = d.run_id AND s.stage_id = d.prerequisite_stage_id "
                        "LEFT JOIN stage_checkpoints c ON c.run_id = d.run_id AND c.stage_id = d.prerequisite_stage_id "
                        "WHERE d.run_id = ? AND d.dependent_stage_id = ?",
                        (run_id.value, stage_id),
                    ).fetchall()
                    terminal = self._dependency_terminal_state(prerequisite_rows)
                    if terminal is not None:
                        self._record_edge(
                            connection, run_id.value, "stage", stage_id,
                            StageState(state), terminal, timestamp
                        )
                        connection.execute(
                            "UPDATE workflow_stages SET state = ? WHERE run_id = ? AND stage_id = ?",
                            (terminal.value, run_id.value, stage_id),
                        )
                        continue
                    if prerequisite_rows and any(
                        row[1] == StageState.SUCCEEDED.value
                        and not (
                            row[2] == OutcomeKind.COMPLETED.value and row[3] == 1
                        )
                        for row in prerequisite_rows
                    ):
                        self._record_edge(
                            connection, run_id.value, "stage", stage_id,
                            StageState(state), StageState.DEPENDENCY_FAILED, timestamp
                        )
                        connection.execute(
                            "UPDATE workflow_stages SET state = 'dependency_failed' "
                            "WHERE run_id = ? AND stage_id = ?",
                            (run_id.value, stage_id),
                        )
                        self._propagate_terminal(
                            connection, run_id, StageId(stage_id), False, timestamp
                        )
                        self._terminalize_remaining(
                            connection,
                            run_id,
                            FailureKind.INVALID_DEPENDENCY_OUTCOME,
                            timestamp,
                        )
                        continue
                    if prerequisite_rows and not all(
                        row[1] == StageState.SUCCEEDED.value
                        and row[2] == OutcomeKind.COMPLETED.value
                        and row[3] == 1
                        for row in prerequisite_rows
                    ):
                        continue
                    job_id = JobId(_stable_id("job", run_id.value, stage_id))
                    dedup_key = _stable_id("dedup", run_id.value, stage_id, version)
                    self._record_edge(
                        connection, run_id.value, "stage", stage_id,
                        StageState(state), StageState.READY, timestamp
                    )
                    connection.execute(
                        "INSERT INTO jobs(job_id, dedup_key, run_id, stage_id, kind, version, state, "
                        "attempt, max_attempts, fence, cancellation_requested, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, 2, 0, 0, ?, ?)",
                        (job_id.value, dedup_key, run_id.value, stage_id, kind, version, timestamp, timestamp),
                    )
                    connection.execute(
                        "UPDATE workflow_stages SET state = 'ready' WHERE run_id = ? AND stage_id = ?",
                        (run_id.value, stage_id),
                    )
                    created += 1
                self._refresh_run_state(connection, run_id, timestamp)
            return RepositoryResult(
                RepositoryOutcome.ACCEPTED if created else RepositoryOutcome.REUSED,
                created,
            )
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    @staticmethod
    def _dependency_terminal_state(rows: list[tuple[object, ...]]) -> StageState | None:
        states = {row[1] for row in rows}
        if states & {StageState.CANCELLED.value, StageState.DEPENDENCY_CANCELLED.value}:
            return StageState.DEPENDENCY_CANCELLED
        if states & {StageState.FAILED_TERMINAL.value, StageState.DEPENDENCY_FAILED.value}:
            return StageState.DEPENDENCY_FAILED
        return None

    def claim(
        self,
        worker_id: WorkerId,
        *,
        capacity: int,
        now: datetime,
        lease_duration: timedelta,
    ) -> RepositoryResult[tuple[JobClaim, ...]]:
        if (
            type(worker_id) is not WorkerId
            or type(capacity) is not int
            or not 1 <= capacity <= 32
            or type(lease_duration) is not timedelta
            or not timedelta(seconds=1) <= lease_duration <= timedelta(hours=1)
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            expiry = _timestamp(now + lease_duration)
            claims: list[JobClaim] = []
            with self._transaction() as connection:
                self._recover_expired(connection, now, timestamp)
                retry_rows = connection.execute(
                    "SELECT job_id, run_id FROM jobs WHERE state = 'retry_wait' "
                    "AND cancellation_requested = 0 AND next_eligible_at IS NOT NULL "
                    "AND next_eligible_at <= ?",
                    (timestamp,),
                ).fetchall()
                for retry_job_id, retry_run_id in retry_rows:
                    self._record_edge(
                        connection, retry_run_id, "job", retry_job_id,
                        JobState.RETRY_WAIT, JobState.PENDING, timestamp
                    )
                connection.execute(
                    "UPDATE jobs SET state = 'pending', next_eligible_at = NULL, updated_at = ? "
                    "WHERE state = 'retry_wait' AND cancellation_requested = 0 "
                    "AND next_eligible_at IS NOT NULL AND next_eligible_at <= ?",
                    (timestamp, timestamp),
                )
                rows = connection.execute(
                    "SELECT j.job_id, j.run_id, j.stage_id, j.fence "
                    "FROM jobs j JOIN workflow_runs r ON r.run_id = j.run_id "
                    "JOIN workflow_stages s ON s.run_id = j.run_id AND s.stage_id = j.stage_id "
                    "WHERE j.state = 'pending' AND j.cancellation_requested = 0 "
                    "AND r.cancellation_requested = 0 AND r.state NOT IN ('cancelled','superseded','failed_terminal','succeeded') "
                    "AND s.state = 'ready' ORDER BY j.created_at, j.job_id LIMIT ?",
                    (capacity,),
                ).fetchall()
                for job_id, run_id, stage_id, fence in rows:
                    self._record_edge(
                        connection, run_id, "job", job_id,
                        JobState.PENDING, JobState.LEASED, timestamp
                    )
                    new_fence = int(fence) + 1
                    updated = connection.execute(
                        "UPDATE jobs SET state = 'leased', lease_owner = ?, lease_expires_at = ?, "
                        "fence = ?, updated_at = ? WHERE job_id = ? AND state = 'pending' AND fence = ?",
                        (worker_id.value, expiry, new_fence, timestamp, job_id, fence),
                    ).rowcount
                    if updated != 1:
                        raise sqlite3.OperationalError("claim contention changed the selected job")
                    claims.append(
                        JobClaim(
                            JobId(job_id),
                            RunId(run_id),
                            StageId(stage_id),
                            worker_id,
                            new_fence,
                            now + lease_duration,
                        )
                    )
            return RepositoryResult(RepositoryOutcome.ACCEPTED, tuple(claims))
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def _recover_expired(
        self, connection: sqlite3.Connection, now: datetime, timestamp: str
    ) -> None:
        terminal_runs: set[str] = set()
        rows = connection.execute(
            "SELECT j.job_id, j.run_id, j.stage_id, j.state, j.attempt, "
            "j.cancellation_requested, j.terminal_failure, r.state, r.cancellation_requested, s.state "
            "FROM jobs j JOIN workflow_runs r ON r.run_id = j.run_id "
            "JOIN workflow_stages s ON s.run_id = j.run_id AND s.stage_id = j.stage_id "
            "WHERE j.state IN ('leased','running') AND j.lease_expires_at IS NOT NULL "
            "AND j.lease_expires_at <= ?",
            (timestamp,),
        ).fetchall()
        for (
            job_id,
            run_id,
            stage_id,
            state,
            attempt,
            cancellation_requested,
            terminal_failure,
            run_state,
            run_cancelled,
            stage_state,
        ) in rows:
            job_state = JobState(state)
            if cancellation_requested or run_cancelled or run_state in {
                RunState.CANCELLED.value,
                RunState.FAILED_TERMINAL.value,
                RunState.SUPERSEDED.value,
            }:
                self._record_edge(
                    connection, run_id, "job", job_id,
                    job_state, JobState.CANCELLED, timestamp
                )
                if stage_state not in _TERMINAL_STAGE_STATES:
                    self._record_edge(
                        connection, run_id, "stage", stage_id,
                        StageState(stage_state), StageState.CANCELLED, timestamp
                    )
                failure = terminal_failure or FailureKind.DEPENDENCY_CANCELLED.value
                if state == JobState.RUNNING.value:
                    connection.execute(
                        "UPDATE job_attempts SET finished_at = ?, failure = ? "
                        "WHERE job_id = ? AND attempt = ? AND finished_at IS NULL",
                        (timestamp, failure, job_id, attempt),
                    )
                connection.execute(
                    "UPDATE jobs SET state = 'cancelled', terminal_failure = ?, "
                    "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
                    "WHERE job_id = ?",
                    (failure, timestamp, job_id),
                )
                connection.execute(
                    "UPDATE workflow_stages SET state = 'cancelled' WHERE run_id = ? "
                    "AND stage_id = ? AND state NOT IN ('succeeded','failed_terminal','dependency_failed','dependency_cancelled')",
                    (run_id, stage_id),
                )
                continue
            if state == JobState.RUNNING.value:
                connection.execute(
                    "UPDATE job_attempts SET finished_at = ?, failure = 'lease_expired' "
                    "WHERE job_id = ? AND attempt = ? AND finished_at IS NULL",
                    (timestamp, job_id, attempt),
                )
            if state == JobState.RUNNING.value and attempt >= 2:
                self._record_edge(
                    connection, run_id, "job", job_id,
                    job_state, JobState.FAILED_TERMINAL, timestamp
                )
                self._record_edge(
                    connection, run_id, "stage", stage_id,
                    StageState(stage_state), StageState.FAILED_TERMINAL, timestamp
                )
                connection.execute(
                    "UPDATE jobs SET state = 'failed_terminal', terminal_failure = 'lease_expired', "
                    "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                    (timestamp, job_id),
                )
                connection.execute(
                    "UPDATE workflow_stages SET state = 'failed_terminal' WHERE run_id = ? AND stage_id = ?",
                    (run_id, stage_id),
                )
                self._propagate_terminal(
                    connection, RunId(run_id), StageId(stage_id), False, timestamp
                )
                terminal_runs.add(run_id)
            else:
                self._record_edge(
                    connection, run_id, "job", job_id,
                    job_state, JobState.PENDING, timestamp
                )
                self._record_edge(
                    connection, run_id, "stage", stage_id,
                    StageState(stage_state), StageState.READY, timestamp
                )
                connection.execute(
                    "UPDATE jobs SET state = 'pending', lease_owner = NULL, lease_expires_at = NULL, "
                    "terminal_failure = CASE WHEN ? = 'running' THEN 'lease_expired' ELSE terminal_failure END, "
                    "updated_at = ? WHERE job_id = ?",
                    (state, timestamp, job_id),
                )
                connection.execute(
                    "UPDATE workflow_stages SET state = 'ready' WHERE run_id = ? AND stage_id = ?",
                    (run_id, stage_id),
                )
        for run_id in sorted(terminal_runs):
            typed_run_id = RunId(run_id)
            self._terminalize_remaining(
                connection,
                typed_run_id,
                FailureKind.DEPENDENCY_FAILED,
                timestamp,
            )
            self._refresh_run_state(connection, typed_run_id, timestamp)

    def start(self, claim: JobClaim, *, now: datetime) -> RepositoryResult[None]:
        if type(claim) is not JobClaim:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            with self._transaction() as connection:
                row = self._current_claim(connection, claim, timestamp, JobState.LEASED)
                if row is None or row[0] >= row[1]:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                attempt = int(row[0]) + 1
                stage_state = StageState(
                    connection.execute(
                        "SELECT state FROM workflow_stages WHERE run_id = ? AND stage_id = ?",
                        (claim.run_id.value, claim.stage_id.value),
                    ).fetchone()[0]
                )
                self._record_edge(
                    connection, claim.run_id.value, "job", claim.job_id.value,
                    JobState.LEASED, JobState.RUNNING, timestamp
                )
                self._record_edge(
                    connection, claim.run_id.value, "stage", claim.stage_id.value,
                    stage_state, StageState.RUNNING, timestamp
                )
                connection.execute(
                    "UPDATE jobs SET state = 'running', attempt = ?, updated_at = ? WHERE job_id = ?",
                    (attempt, timestamp, claim.job_id.value),
                )
                connection.execute(
                    "INSERT INTO job_attempts(job_id, attempt, fence, worker_id, started_at) VALUES (?, ?, ?, ?, ?)",
                    (claim.job_id.value, attempt, claim.fence, claim.worker_id.value, timestamp),
                )
                connection.execute(
                    "UPDATE workflow_stages SET state = 'running' WHERE run_id = ? AND stage_id = ?",
                    (claim.run_id.value, claim.stage_id.value),
                )
                run_state = RunState(
                    connection.execute(
                        "SELECT state FROM workflow_runs WHERE run_id = ?",
                        (claim.run_id.value,),
                    ).fetchone()[0]
                )
                if run_state is RunState.WAITING:
                    self._record_edge(
                        connection, claim.run_id.value, "run", claim.run_id.value,
                        run_state, RunState.RUNNING, timestamp
                    )
                connection.execute(
                    "UPDATE workflow_runs SET state = 'running', updated_at = ? WHERE run_id = ? AND state = 'waiting'",
                    (timestamp, claim.run_id.value),
                )
            return RepositoryResult(RepositoryOutcome.ACCEPTED)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def renew(
        self, claim: JobClaim, *, now: datetime, lease_duration: timedelta
    ) -> RepositoryResult[JobClaim]:
        if (
            type(claim) is not JobClaim
            or type(lease_duration) is not timedelta
            or not timedelta(seconds=1) <= lease_duration <= timedelta(hours=1)
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            expiry_time = now + lease_duration
            expiry = _timestamp(expiry_time)
            with self._transaction() as connection:
                row = connection.execute(
                    "SELECT j.state FROM jobs j "
                    "JOIN workflow_runs r ON r.run_id = j.run_id "
                    "JOIN workflow_stages s ON s.run_id = j.run_id AND s.stage_id = j.stage_id "
                    "WHERE j.job_id = ? AND j.run_id = ? AND j.stage_id = ? "
                    "AND j.lease_owner = ? AND j.fence = ? "
                    "AND j.lease_expires_at > ? AND j.state IN ('leased','running') "
                    "AND j.cancellation_requested = 0 AND r.cancellation_requested = 0 "
                    "AND r.state NOT IN ('cancelled','superseded','failed_terminal','succeeded') "
                    "AND s.state IN ('ready','running')",
                    (
                        claim.job_id.value,
                        claim.run_id.value,
                        claim.stage_id.value,
                        claim.worker_id.value,
                        claim.fence,
                        timestamp,
                    ),
                ).fetchone()
                if row is None:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                connection.execute(
                    "UPDATE jobs SET lease_expires_at = ?, updated_at = ? WHERE job_id = ?",
                    (expiry, timestamp, claim.job_id.value),
                )
            return RepositoryResult(
                RepositoryOutcome.ACCEPTED,
                JobClaim(
                    claim.job_id,
                    claim.run_id,
                    claim.stage_id,
                    claim.worker_id,
                    claim.fence,
                    expiry_time,
                ),
            )
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def complete(
        self, claim: JobClaim, outcome: OutcomeKind, *, now: datetime
    ) -> RepositoryResult[None]:
        if type(claim) is not JobClaim or type(outcome) is not OutcomeKind:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            with self._transaction() as connection:
                row = self._current_claim(connection, claim, timestamp, JobState.RUNNING)
                if row is None:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                expected = connection.execute(
                    "SELECT expected_outcome FROM workflow_stages WHERE run_id = ? AND stage_id = ?",
                    (claim.run_id.value, claim.stage_id.value),
                ).fetchone()
                if expected != (outcome.value,):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                attempt = int(row[0])
                stage_state = StageState(
                    connection.execute(
                        "SELECT state FROM workflow_stages WHERE run_id = ? AND stage_id = ?",
                        (claim.run_id.value, claim.stage_id.value),
                    ).fetchone()[0]
                )
                self._record_edge(
                    connection, claim.run_id.value, "job", claim.job_id.value,
                    JobState.RUNNING, JobState.SUCCEEDED, timestamp
                )
                self._record_edge(
                    connection, claim.run_id.value, "stage", claim.stage_id.value,
                    stage_state, StageState.SUCCEEDED, timestamp
                )
                connection.execute(
                    "UPDATE job_attempts SET finished_at = ?, outcome = ? WHERE job_id = ? AND attempt = ? "
                    "AND fence = ? AND finished_at IS NULL",
                    (timestamp, outcome.value, claim.job_id.value, attempt, claim.fence),
                )
                connection.execute(
                    "UPDATE jobs SET state = 'succeeded', terminal_outcome = ?, terminal_failure = NULL, "
                    "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                    (outcome.value, timestamp, claim.job_id.value),
                )
                connection.execute(
                    "UPDATE workflow_stages SET state = 'succeeded' WHERE run_id = ? AND stage_id = ?",
                    (claim.run_id.value, claim.stage_id.value),
                )
                connection.execute(
                    "INSERT INTO stage_checkpoints(run_id, stage_id, source_run_id, source_stage_id, "
                    "accepting_job_id, accepting_fence, outcome, reuse_eligible, current_applicable, accepted_at) "
                    "VALUES (?, ?, NULL, NULL, ?, ?, ?, 1, 1, ?) "
                    "ON CONFLICT(run_id, stage_id) DO UPDATE SET "
                    "source_run_id = NULL, source_stage_id = NULL, accepting_job_id = excluded.accepting_job_id, "
                    "accepting_fence = excluded.accepting_fence, outcome = excluded.outcome, "
                    "reuse_eligible = 1, current_applicable = 1, accepted_at = excluded.accepted_at "
                    "WHERE stage_checkpoints.current_applicable = 0",
                    (
                        claim.run_id.value,
                        claim.stage_id.value,
                        claim.job_id.value,
                        claim.fence,
                        outcome.value,
                        timestamp,
                    ),
                )
                audit_id = AuditEventId(
                    _stable_id("job-complete", claim.job_id.value, str(claim.fence))
                )
                connection.execute(
                    "INSERT INTO audit_events(id, kind, created_at, summary) VALUES (?, 'job', ?, 'job completed')",
                    (audit_id.value, timestamp),
                )
                self._refresh_run_state(connection, claim.run_id, timestamp)
            return RepositoryResult(RepositoryOutcome.ACCEPTED)
        except sqlite3.IntegrityError:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def fail(
        self, claim: JobClaim, failure: FailureKind, *, now: datetime
    ) -> RepositoryResult[None]:
        if type(claim) is not JobClaim or type(failure) is not FailureKind:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            with self._transaction() as connection:
                row = self._current_claim(connection, claim, timestamp, JobState.RUNNING)
                if row is None:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                attempt = int(row[0])
                connection.execute(
                    "UPDATE job_attempts SET finished_at = ?, failure = ? WHERE job_id = ? "
                    "AND attempt = ? AND fence = ? AND finished_at IS NULL",
                    (timestamp, failure.value, claim.job_id.value, attempt, claim.fence),
                )
                retryable = failure is FailureKind.SYNTHETIC_RETRYABLE and attempt < 2
                stage_state = StageState(
                    connection.execute(
                        "SELECT state FROM workflow_stages WHERE run_id = ? AND stage_id = ?",
                        (claim.run_id.value, claim.stage_id.value),
                    ).fetchone()[0]
                )
                if retryable:
                    self._record_edge(
                        connection, claim.run_id.value, "job", claim.job_id.value,
                        JobState.RUNNING, JobState.RETRY_WAIT, timestamp
                    )
                    self._record_edge(
                        connection, claim.run_id.value, "stage", claim.stage_id.value,
                        stage_state, StageState.READY, timestamp
                    )
                    eligible = _timestamp(now + timedelta(seconds=5))
                    connection.execute(
                        "UPDATE jobs SET state = 'retry_wait', next_eligible_at = ?, terminal_failure = ?, "
                        "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                        (eligible, failure.value, timestamp, claim.job_id.value),
                    )
                    connection.execute(
                        "UPDATE workflow_stages SET state = 'ready' WHERE run_id = ? AND stage_id = ?",
                        (claim.run_id.value, claim.stage_id.value),
                    )
                else:
                    self._record_edge(
                        connection, claim.run_id.value, "job", claim.job_id.value,
                        JobState.RUNNING, JobState.FAILED_TERMINAL, timestamp
                    )
                    self._record_edge(
                        connection, claim.run_id.value, "stage", claim.stage_id.value,
                        stage_state, StageState.FAILED_TERMINAL, timestamp
                    )
                    connection.execute(
                        "UPDATE jobs SET state = 'failed_terminal', terminal_failure = ?, "
                        "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                        (failure.value, timestamp, claim.job_id.value),
                    )
                    connection.execute(
                        "UPDATE workflow_stages SET state = 'failed_terminal' WHERE run_id = ? AND stage_id = ?",
                        (claim.run_id.value, claim.stage_id.value),
                    )
                    self._propagate_terminal(
                        connection, claim.run_id, claim.stage_id, False, timestamp
                    )
                    self._terminalize_remaining(
                        connection,
                        claim.run_id,
                        FailureKind.DEPENDENCY_FAILED,
                        timestamp,
                    )
                self._refresh_run_state(connection, claim.run_id, timestamp)
            return RepositoryResult(RepositoryOutcome.ACCEPTED)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def cancel_run(self, run_id: RunId, *, now: datetime) -> RepositoryResult[None]:
        if type(run_id) is not RunId:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            with self._transaction() as connection:
                row = connection.execute(
                    "SELECT state FROM workflow_runs WHERE run_id = ?", (run_id.value,)
                ).fetchone()
                if row is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if row[0] in {RunState.SUCCEEDED.value, RunState.FAILED_TERMINAL.value, RunState.SUPERSEDED.value}:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                self._record_edge(
                    connection, run_id.value, "run", run_id.value,
                    RunState(row[0]), RunState.CANCELLED, timestamp
                )
                for job_id, job_state in connection.execute(
                    "SELECT job_id, state FROM jobs WHERE run_id = ? "
                    "AND state IN ('pending','leased','retry_wait')",
                    (run_id.value,),
                ):
                    self._record_edge(
                        connection, run_id.value, "job", job_id,
                        JobState(job_state), JobState.CANCELLED, timestamp
                    )
                for stage_id, stage_state in connection.execute(
                    "SELECT stage_id, state FROM workflow_stages WHERE run_id = ? "
                    "AND state NOT IN ('succeeded','failed_terminal','cancelled','dependency_failed','dependency_cancelled')",
                    (run_id.value,),
                ):
                    self._record_edge(
                        connection, run_id.value, "stage", stage_id,
                        StageState(stage_state), StageState.CANCELLED, timestamp
                    )
                connection.execute(
                    "UPDATE workflow_runs SET state = 'cancelled', cancellation_requested = 1, updated_at = ? "
                    "WHERE run_id = ?",
                    (timestamp, run_id.value),
                )
                connection.execute(
                    "UPDATE jobs SET cancellation_requested = 1, "
                    "state = CASE WHEN state IN ('pending','leased','retry_wait') THEN 'cancelled' ELSE state END, "
                    "terminal_failure = CASE WHEN state IN ('pending','leased','running','retry_wait') THEN 'dependency_cancelled' ELSE terminal_failure END, "
                    "lease_owner = CASE WHEN state IN ('pending','leased','retry_wait') THEN NULL ELSE lease_owner END, "
                    "lease_expires_at = CASE WHEN state IN ('pending','leased','retry_wait') THEN NULL ELSE lease_expires_at END, "
                    "updated_at = ? WHERE run_id = ? AND state NOT IN ('succeeded','failed_terminal','cancelled')",
                    (timestamp, run_id.value),
                )
                connection.execute(
                    "UPDATE workflow_stages SET state = 'cancelled' WHERE run_id = ? "
                    "AND state NOT IN ('succeeded','failed_terminal','cancelled','dependency_failed','dependency_cancelled')",
                    (run_id.value,),
                )
            return RepositoryResult(RepositoryOutcome.ACCEPTED)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def cancel_stage(
        self, run_id: RunId, stage_id: StageId, *, now: datetime
    ) -> RepositoryResult[None]:
        if type(run_id) is not RunId or type(stage_id) is not StageId:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            with self._transaction() as connection:
                run = connection.execute(
                    "SELECT state, cancellation_requested FROM workflow_runs WHERE run_id = ?",
                    (run_id.value,),
                ).fetchone()
                stage = connection.execute(
                    "SELECT state FROM workflow_stages WHERE run_id = ? AND stage_id = ?",
                    (run_id.value, stage_id.value),
                ).fetchone()
                if run is None or stage is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if run[1] != 0 or run[0] in {
                    RunState.SUCCEEDED.value,
                    RunState.FAILED_TERMINAL.value,
                    RunState.CANCELLED.value,
                    RunState.SUPERSEDED.value,
                }:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                if stage[0] in _TERMINAL_STAGE_STATES:
                    return RepositoryResult(RepositoryOutcome.REUSED)
                self._record_edge(
                    connection, run_id.value, "stage", stage_id.value,
                    StageState(stage[0]), StageState.CANCELLED, timestamp
                )
                job = connection.execute(
                    "SELECT state FROM jobs WHERE run_id = ? AND stage_id = ?",
                    (run_id.value, stage_id.value),
                ).fetchone()
                if job is not None and job[0] not in _TERMINAL_JOB_STATES:
                    if job[0] == JobState.RUNNING.value:
                        connection.execute(
                            "UPDATE jobs SET cancellation_requested = 1, "
                            "terminal_failure = 'dependency_cancelled', updated_at = ? "
                            "WHERE run_id = ? AND stage_id = ?",
                            (timestamp, run_id.value, stage_id.value),
                        )
                    else:
                        job_id = connection.execute(
                            "SELECT job_id FROM jobs WHERE run_id = ? AND stage_id = ?",
                            (run_id.value, stage_id.value),
                        ).fetchone()[0]
                        self._record_edge(
                            connection, run_id.value, "job", job_id,
                            JobState(job[0]), JobState.CANCELLED, timestamp
                        )
                        connection.execute(
                            "UPDATE jobs SET state = 'cancelled', cancellation_requested = 1, "
                            "terminal_failure = 'dependency_cancelled', lease_owner = NULL, "
                            "lease_expires_at = NULL, updated_at = ? "
                            "WHERE run_id = ? AND stage_id = ?",
                            (timestamp, run_id.value, stage_id.value),
                        )
                connection.execute(
                    "UPDATE workflow_stages SET state = 'cancelled' "
                    "WHERE run_id = ? AND stage_id = ?",
                    (run_id.value, stage_id.value),
                )
                self._propagate_terminal(
                    connection, run_id, stage_id, True, timestamp
                )
                self._terminalize_remaining(
                    connection,
                    run_id,
                    FailureKind.DEPENDENCY_CANCELLED,
                    timestamp,
                )
                self._refresh_run_state(connection, run_id, timestamp)
            return RepositoryResult(RepositoryOutcome.ACCEPTED)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def acknowledge_cancellation(
        self, claim: JobClaim, *, now: datetime
    ) -> RepositoryResult[None]:
        if type(claim) is not JobClaim:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            timestamp = _timestamp(now)
            with self._transaction() as connection:
                row = connection.execute(
                    "SELECT attempt, terminal_failure FROM jobs WHERE job_id = ? AND run_id = ? AND stage_id = ? "
                    "AND state = 'running' AND lease_owner = ? AND fence = ? "
                    "AND lease_expires_at > ? AND cancellation_requested = 1",
                    (
                        claim.job_id.value,
                        claim.run_id.value,
                        claim.stage_id.value,
                        claim.worker_id.value,
                        claim.fence,
                        timestamp,
                    ),
                ).fetchone()
                if row is None:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                failure = (
                    row[1]
                    if row[1]
                    in {
                        FailureKind.DEPENDENCY_CANCELLED.value,
                        FailureKind.DEPENDENCY_FAILED.value,
                    }
                    else FailureKind.DEPENDENCY_CANCELLED.value
                )
                self._record_edge(
                    connection, claim.run_id.value, "job", claim.job_id.value,
                    JobState.RUNNING, JobState.CANCELLED, timestamp
                )
                connection.execute(
                    "UPDATE job_attempts SET finished_at = ?, failure = ? "
                    "WHERE job_id = ? AND attempt = ? AND fence = ? AND finished_at IS NULL",
                    (timestamp, failure, claim.job_id.value, row[0], claim.fence),
                )
                connection.execute(
                    "UPDATE jobs SET state = 'cancelled', terminal_failure = ?, "
                    "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                    (failure, timestamp, claim.job_id.value),
                )
            return RepositoryResult(RepositoryOutcome.ACCEPTED)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def _current_claim(
        self,
        connection: sqlite3.Connection,
        claim: JobClaim,
        timestamp: str,
        state: JobState,
    ) -> tuple[int, int] | None:
        row = connection.execute(
            "SELECT j.attempt, j.max_attempts FROM jobs j "
            "JOIN workflow_runs r ON r.run_id = j.run_id "
            "WHERE j.job_id = ? AND j.run_id = ? AND j.stage_id = ? AND j.state = ? "
            "AND j.lease_owner = ? AND j.fence = ? AND j.lease_expires_at > ? "
            "AND j.cancellation_requested = 0 AND r.cancellation_requested = 0 "
            "AND r.state NOT IN ('cancelled','superseded','failed_terminal','succeeded')",
            (
                claim.job_id.value,
                claim.run_id.value,
                claim.stage_id.value,
                state.value,
                claim.worker_id.value,
                claim.fence,
                timestamp,
            ),
        ).fetchone()
        if row is None:
            return None
        return int(row[0]), int(row[1])

    def _propagate_terminal(
        self,
        connection: sqlite3.Connection,
        run_id: RunId,
        stage_id: StageId,
        cancelled: bool,
        timestamp: str,
    ) -> None:
        terminal = (
            StageState.DEPENDENCY_CANCELLED.value
            if cancelled
            else StageState.DEPENDENCY_FAILED.value
        )
        pending = [stage_id.value]
        seen: set[str] = set()
        while pending:
            prerequisite = pending.pop()
            dependents = [
                row[0]
                for row in connection.execute(
                    "SELECT dependent_stage_id FROM workflow_dependencies "
                    "WHERE run_id = ? AND prerequisite_stage_id = ?",
                    (run_id.value, prerequisite),
                )
            ]
            for dependent in dependents:
                if dependent in seen:
                    continue
                seen.add(dependent)
                stage_row = connection.execute(
                    "SELECT state FROM workflow_stages WHERE run_id = ? AND stage_id = ?",
                    (run_id.value, dependent),
                ).fetchone()
                if stage_row is not None and stage_row[0] not in _TERMINAL_STAGE_STATES:
                    self._record_edge(
                        connection, run_id.value, "stage", dependent,
                        StageState(stage_row[0]), StageState(terminal),
                        timestamp,
                    )
                job_row = connection.execute(
                    "SELECT state FROM jobs WHERE run_id = ? AND stage_id = ?",
                    (run_id.value, dependent),
                ).fetchone()
                if job_row is not None and job_row[0] not in _TERMINAL_JOB_STATES:
                    job_id = connection.execute(
                        "SELECT job_id FROM jobs WHERE run_id = ? AND stage_id = ?",
                        (run_id.value, dependent),
                    ).fetchone()[0]
                    self._record_edge(
                        connection, run_id.value, "job", job_id,
                        JobState(job_row[0]),
                        JobState.CANCELLED if cancelled else JobState.FAILED_TERMINAL,
                        timestamp,
                    )
                connection.execute(
                    "UPDATE workflow_stages SET state = ? WHERE run_id = ? AND stage_id = ? "
                    "AND state NOT IN ('succeeded','failed_terminal','cancelled','dependency_failed','dependency_cancelled')",
                    (terminal, run_id.value, dependent),
                )
                connection.execute(
                    "UPDATE jobs SET state = ?, terminal_failure = ?, lease_owner = NULL, lease_expires_at = NULL "
                    "WHERE run_id = ? AND stage_id = ? AND state NOT IN ('succeeded','failed_terminal','cancelled')",
                    (
                        JobState.CANCELLED.value if cancelled else JobState.FAILED_TERMINAL.value,
                        FailureKind.DEPENDENCY_CANCELLED.value
                        if cancelled
                        else FailureKind.DEPENDENCY_FAILED.value,
                        run_id.value,
                        dependent,
                    ),
                )
                pending.append(dependent)

    def _terminalize_remaining(
        self,
        connection: sqlite3.Connection,
        run_id: RunId,
        failure: FailureKind,
        timestamp: str,
    ) -> None:
        for job_id, job_state in connection.execute(
            "SELECT job_id, state FROM jobs WHERE run_id = ? "
            "AND state IN ('pending','leased','retry_wait')",
            (run_id.value,),
        ):
            self._record_edge(
                connection, run_id.value, "job", job_id,
                JobState(job_state), JobState.CANCELLED, timestamp
            )
        for stage_id, stage_state in connection.execute(
            "SELECT stage_id, state FROM workflow_stages WHERE run_id = ? "
            "AND state IN ('waiting','ready','running')",
            (run_id.value,),
        ):
            self._record_edge(
                connection, run_id.value, "stage", stage_id,
                StageState(stage_state), StageState.CANCELLED, timestamp
            )
        connection.execute(
            "UPDATE jobs SET cancellation_requested = 1, terminal_failure = ?, "
            "state = CASE WHEN state IN ('pending','leased','retry_wait') THEN 'cancelled' ELSE state END, "
            "lease_owner = CASE WHEN state IN ('pending','leased','retry_wait') THEN NULL ELSE lease_owner END, "
            "lease_expires_at = CASE WHEN state IN ('pending','leased','retry_wait') THEN NULL ELSE lease_expires_at END, "
            "updated_at = ? WHERE run_id = ? AND state NOT IN ('succeeded','failed_terminal','cancelled')",
            (failure.value, timestamp, run_id.value),
        )
        connection.execute(
            "UPDATE workflow_stages SET state = 'cancelled' WHERE run_id = ? "
            "AND state IN ('waiting','ready','running')",
            (run_id.value,),
        )

    def _refresh_run_state(
        self, connection: sqlite3.Connection, run_id: RunId, timestamp: str
    ) -> None:
        row = connection.execute(
            "SELECT state, cancellation_requested FROM workflow_runs WHERE run_id = ?",
            (run_id.value,),
        ).fetchone()
        if row is None or row[0] in {RunState.CANCELLED.value, RunState.SUPERSEDED.value}:
            return
        states = {
            item[0]
            for item in connection.execute(
                "SELECT state FROM workflow_stages WHERE run_id = ?", (run_id.value,)
            )
        }
        if states and states <= {StageState.SUCCEEDED.value}:
            state = RunState.SUCCEEDED.value
        elif states & {
            StageState.FAILED_TERMINAL.value,
            StageState.DEPENDENCY_FAILED.value,
            StageState.DEPENDENCY_CANCELLED.value,
        }:
            state = RunState.FAILED_TERMINAL.value
        elif states & {StageState.RUNNING.value, StageState.READY.value}:
            state = RunState.RUNNING.value
        else:
            state = RunState.WAITING.value
        self._record_edge(
            connection, run_id.value, "run", run_id.value,
            RunState(row[0]), RunState(state), timestamp
        )
        connection.execute(
            "UPDATE workflow_runs SET state = ?, updated_at = ? WHERE run_id = ?",
            (state, timestamp, run_id.value),
        )

    def inspect(self, run_id: RunId) -> RepositoryResult[RunSnapshot]:
        if type(run_id) is not RunId:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            run = self._connection.execute(
                "SELECT definition_version, graph_digest, state, cancellation_requested, "
                "replanned_from, superseded_by FROM workflow_runs WHERE run_id = ?",
                (run_id.value,),
            ).fetchone()
            if run is None:
                return RepositoryResult(RepositoryOutcome.NOT_FOUND)
            stages = tuple(
                self._stage_snapshot(row)
                for row in self._connection.execute(
                    "SELECT s.stage_id, s.state, c.outcome, c.reuse_eligible, c.current_applicable "
                    "FROM workflow_stages s LEFT JOIN stage_checkpoints c "
                    "ON c.run_id = s.run_id AND c.stage_id = s.stage_id "
                    "WHERE s.run_id = ? ORDER BY s.position",
                    (run_id.value,),
                )
            )
            jobs = tuple(
                JobSnapshot(
                    JobId(row[0]),
                    StageId(row[1]),
                    JobState(row[2]),
                    int(row[3]),
                    int(row[4]),
                    datetime.fromisoformat(str(row[5]).replace("Z", "+00:00"))
                    if row[5] is not None
                    else None,
                    datetime.fromisoformat(str(row[6]).replace("Z", "+00:00"))
                    if row[6] is not None
                    else None,
                )
                for row in self._connection.execute(
                    "SELECT job_id, stage_id, state, attempt, fence, lease_expires_at, next_eligible_at FROM jobs "
                    "WHERE run_id = ? ORDER BY created_at, job_id",
                    (run_id.value,),
                )
            )
            checkpoint_count = int(
                self._connection.execute(
                    "SELECT count(*) FROM stage_checkpoints WHERE run_id = ?",
                    (run_id.value,),
                ).fetchone()[0]
            )
            attempt_count = int(
                self._connection.execute(
                    "SELECT count(*) FROM job_attempts a JOIN jobs j ON j.job_id = a.job_id "
                    "WHERE j.run_id = ?",
                    (run_id.value,),
                ).fetchone()[0]
            )
            audit_completion_count = 0
            for job in jobs:
                if job.state is not JobState.SUCCEEDED:
                    continue
                audit_id = _stable_id("job-complete", job.id.value, str(job.fence))
                if self._connection.execute(
                    "SELECT 1 FROM audit_events WHERE id = ? AND kind = 'job' "
                    "AND summary = 'job completed'",
                    (audit_id,),
                ).fetchone() is not None:
                    audit_completion_count += 1
            snapshot = RunSnapshot(
                run_id,
                str(run[0]),
                str(run[1]),
                RunState(run[2]),
                bool(run[3]),
                RunId(run[4]) if run[4] is not None else None,
                RunId(run[5]) if run[5] is not None else None,
                stages,
                jobs,
                checkpoint_count,
                attempt_count,
                audit_completion_count,
            )
            return RepositoryResult(RepositoryOutcome.ACCEPTED, snapshot)
        except (sqlite3.Error, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.CORRUPT)

    @staticmethod
    def _stage_snapshot(row: tuple[object, ...]) -> StageSnapshot:
        stage_id, state, outcome, reuse, applicable = row
        return StageSnapshot(
            StageId(str(stage_id)),
            StageState(state),
            OutcomeKind(outcome) if outcome is not None else None,
            bool(reuse) if reuse is not None else None,
            bool(applicable) if applicable is not None else None,
        )


__all__ = ["SQLiteJobHeartbeatRepository"]
