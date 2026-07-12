"""Application contracts and supported synthetic durable-job demonstration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Generic, Protocol, TypeVar, cast

from mechanical_librarian.domain.identifiers import JobId
from mechanical_librarian.domain.workflow import (
    FailureKind,
    JobState,
    OutcomeKind,
    RunId,
    RunState,
    StageId,
    StageState,
    WorkerId,
    WorkflowDefinition,
    WorkflowVersion,
    synthetic_heartbeat_definition,
)


class RepositoryOutcome(StrEnum):
    ACCEPTED = "accepted"
    REUSED = "reused"
    REJECTED = "rejected"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    CORRUPT = "corrupt"


T = TypeVar("T")


@dataclass(frozen=True)
class RepositoryResult(Generic[T]):
    outcome: RepositoryOutcome
    value: T | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome in {RepositoryOutcome.ACCEPTED, RepositoryOutcome.REUSED}


@dataclass(frozen=True)
class JobClaim:
    job_id: JobId
    run_id: RunId
    stage_id: StageId
    worker_id: WorkerId
    fence: int
    lease_expires_at: datetime


@dataclass(frozen=True)
class StageSnapshot:
    id: StageId
    state: StageState
    checkpoint_outcome: OutcomeKind | None
    reuse_eligible: bool | None = None
    current_applicable: bool | None = None


@dataclass(frozen=True)
class JobSnapshot:
    id: JobId
    stage_id: StageId
    state: JobState
    attempt: int
    fence: int
    lease_expires_at: datetime | None = None
    next_eligible_at: datetime | None = None


@dataclass(frozen=True)
class RunSnapshot:
    id: RunId
    definition_version: str
    graph_digest: str
    state: RunState
    cancellation_requested: bool
    replanned_from: RunId | None
    superseded_by: RunId | None
    stages: tuple[StageSnapshot, ...]
    jobs: tuple[JobSnapshot, ...]
    checkpoint_count: int
    attempt_count: int
    audit_completion_count: int


class JobHeartbeatRepository(Protocol):
    def validate_run(
        self, run_id: RunId, definition: WorkflowDefinition
    ) -> RepositoryResult[None]: ...

    def create_or_resume(
        self,
        run_id: RunId,
        definition: WorkflowDefinition,
        *,
        changed_stages: frozenset[StageId],
        now: datetime,
    ) -> RepositoryResult[RunSnapshot]: ...

    def enqueue_eligible(self, run_id: RunId, *, now: datetime) -> RepositoryResult[int]: ...

    def claim(
        self,
        worker_id: WorkerId,
        *,
        capacity: int,
        now: datetime,
        lease_duration: timedelta,
    ) -> RepositoryResult[tuple[JobClaim, ...]]: ...

    def start(self, claim: JobClaim, *, now: datetime) -> RepositoryResult[None]: ...

    def renew(
        self, claim: JobClaim, *, now: datetime, lease_duration: timedelta
    ) -> RepositoryResult[JobClaim]: ...

    def complete(
        self, claim: JobClaim, outcome: OutcomeKind, *, now: datetime
    ) -> RepositoryResult[None]: ...

    def fail(
        self, claim: JobClaim, failure: FailureKind, *, now: datetime
    ) -> RepositoryResult[None]: ...

    def cancel_run(self, run_id: RunId, *, now: datetime) -> RepositoryResult[None]: ...

    def cancel_stage(
        self, run_id: RunId, stage_id: StageId, *, now: datetime
    ) -> RepositoryResult[None]: ...

    def acknowledge_cancellation(
        self, claim: JobClaim, *, now: datetime
    ) -> RepositoryResult[None]: ...

    def inspect(self, run_id: RunId) -> RepositoryResult[RunSnapshot]: ...


class HeartbeatOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


_HEARTBEAT_CHECKS = (
    "complete-set",
    "lease-recovery",
    "stale-rejected",
    "fan-in",
    "negative-fan-in",
    "retry-bounded",
    "cancellation",
    "dependency-cancellation",
    "compatible-resume",
    "incompatible-replan",
    "transitive-invalidation",
)


@dataclass(frozen=True)
class HeartbeatResult:
    outcome: HeartbeatOutcome
    checks: tuple[str, ...] = ()
    inspection: "HeartbeatInspection | None" = None

    def __post_init__(self) -> None:
        if type(self.outcome) is not HeartbeatOutcome or type(self.checks) is not tuple:
            raise ValueError("heartbeat results require typed bounded values")
        if any(type(check) is not str or check not in _HEARTBEAT_CHECKS for check in self.checks):
            raise ValueError("heartbeat result contains an unknown check")
        if len(set(self.checks)) != len(self.checks):
            raise ValueError("heartbeat result checks must be unique")
        if self.inspection is not None and type(self.inspection) is not HeartbeatInspection:
            raise ValueError("heartbeat result inspection is invalid")

    @property
    def succeeded(self) -> bool:
        return self.outcome is HeartbeatOutcome.PASSED


@dataclass(frozen=True)
class HeartbeatInspection:
    source_id: RunId
    source_state: RunState
    source_version: str
    source_digest: str
    replacement_id: RunId
    replacement_state: RunState
    replacement_version: str
    replacement_digest: str
    retry_state: RunState
    retry_attempts: int
    cancellation_state: RunState
    cancelled_job_state: JobState
    required_cancellation_state: RunState
    required_stage_states: tuple[tuple[StageId, StageState], ...]
    negative_state: RunState

    def __post_init__(self) -> None:
        if (
            type(self.source_id) is not RunId
            or type(self.replacement_id) is not RunId
            or type(self.source_state) is not RunState
            or type(self.replacement_state) is not RunState
            or type(self.retry_state) is not RunState
            or type(self.cancellation_state) is not RunState
            or type(self.required_cancellation_state) is not RunState
            or type(self.negative_state) is not RunState
            or type(self.cancelled_job_state) is not JobState
        ):
            raise ValueError("heartbeat inspection requires exact typed states")
        WorkflowVersion(self.source_version)
        WorkflowVersion(self.replacement_version)
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_digest) or not re.fullmatch(
            r"[0-9a-f]{64}", self.replacement_digest
        ):
            raise ValueError("heartbeat inspection requires canonical graph digests")
        if type(self.retry_attempts) is not int or not 0 <= self.retry_attempts <= 2:
            raise ValueError("heartbeat retry attempts are out of bounds")
        if type(self.required_stage_states) is not tuple or not 1 <= len(
            self.required_stage_states
        ) <= 32:
            raise ValueError("heartbeat stage inspection is out of bounds")
        if any(
            type(entry) is not tuple
            or len(entry) != 2
            or type(entry[0]) is not StageId
            or type(entry[1]) is not StageState
            for entry in self.required_stage_states
        ):
            raise ValueError("heartbeat stage inspection requires typed values")


class RunSyntheticJobHeartbeat:
    """Execute the fixed F02 report-only scenario using only repository authority."""

    _START = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _LEASE = timedelta(seconds=5)

    def __init__(self, repository: JobHeartbeatRepository) -> None:
        self._repository = repository

    def run(self) -> HeartbeatResult:
        try:
            checks = self._qualified_existing() or self._run_scenarios()
            inspection = self._inspection()
        except Exception:
            return HeartbeatResult(HeartbeatOutcome.FAILED)
        expected = _HEARTBEAT_CHECKS
        if checks != expected or inspection is None:
            return HeartbeatResult(HeartbeatOutcome.FAILED)
        return HeartbeatResult(HeartbeatOutcome.PASSED, checks, inspection)

    def _qualified_existing(self) -> tuple[str, ...] | None:
        definition = synthetic_heartbeat_definition()
        replacement_definition = synthetic_heartbeat_definition(incompatible=True)
        source = self._repository.inspect(RunId("heartbeat-main"))
        if not source.accepted or source.value is None:
            return None
        if source.value.superseded_by is None:
            return None
        replacement = self._repository.inspect(source.value.superseded_by)
        retry = self._repository.inspect(RunId("heartbeat-retry"))
        cancelled = self._repository.inspect(RunId("heartbeat-cancel"))
        required_cancelled = self._repository.inspect(
            RunId("heartbeat-required-cancel")
        )
        negative = self._repository.inspect(RunId("heartbeat-negative"))
        validations = (
            self._repository.validate_run(RunId("heartbeat-main"), definition),
            self._repository.validate_run(source.value.superseded_by, replacement_definition),
            self._repository.validate_run(RunId("heartbeat-retry"), definition),
            self._repository.validate_run(RunId("heartbeat-cancel"), definition),
            self._repository.validate_run(RunId("heartbeat-required-cancel"), definition),
            self._repository.validate_run(RunId("heartbeat-negative"), definition),
        )
        if (
            not all(result.accepted for result in validations)
            or not replacement.accepted
            or replacement.value is None
            or not retry.accepted
            or retry.value is None
            or not cancelled.accepted
            or cancelled.value is None
            or not required_cancelled.accepted
            or required_cancelled.value is None
            or not negative.accepted
            or negative.value is None
            or retry.value.state is not RunState.FAILED_TERMINAL
            or cancelled.value.state is not RunState.CANCELLED
            or required_cancelled.value.state is not RunState.FAILED_TERMINAL
            or negative.value.state is not RunState.FAILED_TERMINAL
            or source.value.replanned_from is not None
            or replacement.value.replanned_from != source.value.id
            or replacement.value.superseded_by is not None
        ):
            return None
        if (
            source.value.state is not RunState.SUPERSEDED
            or len(source.value.jobs) != 4
            or any(job.state is not JobState.SUCCEEDED for job in source.value.jobs)
            or source.value.checkpoint_count != 4
            or not 5 <= source.value.attempt_count <= 8
            or source.value.audit_completion_count != 4
            or replacement.value.checkpoint_count != 4
            or replacement.value.attempt_count != 0
            or replacement.value.audit_completion_count != 0
            or retry.value.checkpoint_count != 0
            or retry.value.attempt_count != 2
            or retry.value.audit_completion_count != 0
            or len(retry.value.jobs) != 1
            or retry.value.jobs[0].state is not JobState.FAILED_TERMINAL
            or len(cancelled.value.jobs) != 1
            or cancelled.value.jobs[0].state is not JobState.CANCELLED
            or not 1 <= cancelled.value.attempt_count <= 2
            or len(required_cancelled.value.jobs) != 1
            or required_cancelled.value.jobs[0].state is not JobState.CANCELLED
            or not 1 <= required_cancelled.value.attempt_count <= 2
            or negative.value.checkpoint_count != 2
            or not 3 <= negative.value.attempt_count <= 5
            or negative.value.audit_completion_count != 2
        ):
            return None
        required_states = {
            stage.id.value: stage.state for stage in required_cancelled.value.stages
        }
        if (
            required_states.get("root") is not StageState.CANCELLED
            or required_states.get("left") is not StageState.DEPENDENCY_CANCELLED
            or required_states.get("right") is not StageState.DEPENDENCY_CANCELLED
            or required_states.get("join") is not StageState.DEPENDENCY_CANCELLED
        ):
            return None
        flags = {
            stage.id.value: (stage.reuse_eligible, stage.current_applicable)
            for stage in replacement.value.stages
        }
        if (
            flags.get("root") != (True, True)
            or flags.get("right") != (True, True)
            or flags.get("left") != (False, False)
            or flags.get("join") != (False, False)
        ):
            return None
        return (
            "complete-set",
            "lease-recovery",
            "stale-rejected",
            "fan-in",
            "negative-fan-in",
            "retry-bounded",
            "cancellation",
            "dependency-cancellation",
            "compatible-resume",
            "incompatible-replan",
            "transitive-invalidation",
        )

    def _inspection(self) -> HeartbeatInspection | None:
        source = self._snapshot(RunId("heartbeat-main"))
        if source.superseded_by is None:
            return None
        replacement = self._snapshot(source.superseded_by)
        retry = self._snapshot(RunId("heartbeat-retry"))
        cancelled = self._snapshot(RunId("heartbeat-cancel"))
        required = self._snapshot(RunId("heartbeat-required-cancel"))
        negative = self._snapshot(RunId("heartbeat-negative"))
        if not cancelled.jobs:
            return None
        return HeartbeatInspection(
            source.id,
            source.state,
            source.definition_version,
            source.graph_digest,
            replacement.id,
            replacement.state,
            replacement.definition_version,
            replacement.graph_digest,
            retry.state,
            retry.attempt_count,
            cancelled.state,
            cancelled.jobs[0].state,
            required.state,
            tuple((stage.id, stage.state) for stage in required.stages),
            negative.state,
        )

    def _run_scenarios(self) -> tuple[str, ...]:
        checks: list[str] = []
        definition = synthetic_heartbeat_definition()
        main = RunId("heartbeat-main")
        created = self._repository.create_or_resume(
            main, definition, changed_stages=frozenset(), now=self._START
        )
        self._require(created.accepted)
        self._require(self._repository.enqueue_eligible(main, now=self._START).accepted)
        initial = self._snapshot(main)
        self._require("root" in self._job_stages(initial))

        recovered_at = self._START + timedelta(seconds=6)
        root_job = next(job for job in initial.jobs if job.stage_id == StageId("root"))
        if root_job.state is not JobState.SUCCEEDED:
            if root_job.state is JobState.PENDING:
                old_claim = self._one_claim(WorkerId("worker-old"), self._START)
                self._require(self._repository.start(old_claim, now=self._START).accepted)
                recovered_claim = self._one_claim(WorkerId("worker-new"), recovered_at)
                self._require(recovered_claim.job_id == old_claim.job_id)
                self._require(recovered_claim.fence > old_claim.fence)
                self._require(
                    not self._repository.complete(
                        old_claim, OutcomeKind.COMPLETED, now=recovered_at
                    ).accepted
                )
            elif root_job.state in {JobState.LEASED, JobState.RUNNING}:
                recovered_claim = self._one_claim(
                    WorkerId("worker-new"), recovered_at
                )
                self._require(recovered_claim.fence > root_job.fence)
            else:
                self._require(False)
            self._require(
                self._repository.start(recovered_claim, now=recovered_at).accepted
            )
            self._require(
                self._repository.complete(
                    recovered_claim, OutcomeKind.COMPLETED, now=recovered_at
                ).accepted
            )
        checks.extend(("complete-set", "lease-recovery", "stale-rejected"))

        main_now = self._recovery_time(self._snapshot(main), recovered_at)
        self._require(self._repository.enqueue_eligible(main, now=main_now).accepted)
        branch_snapshot = self._snapshot(main)
        unfinished_branches = {
            job.stage_id
            for job in branch_snapshot.jobs
            if job.stage_id in {StageId("left"), StageId("right")}
            and job.state is not JobState.SUCCEEDED
        }
        if unfinished_branches:
            main_now = self._recovery_time(branch_snapshot, main_now)
            branch_claims = self._claims(
                WorkerId("worker-branch"), main_now, len(unfinished_branches)
            )
            self._require({claim.stage_id for claim in branch_claims} == unfinished_branches)
            for claim in branch_claims:
                self._require(self._repository.start(claim, now=main_now).accepted)
                self._require(
                    self._repository.complete(
                        claim, OutcomeKind.COMPLETED, now=main_now
                    ).accepted
                )
        self._require(self._repository.enqueue_eligible(main, now=main_now).accepted)
        join_snapshot = self._snapshot(main)
        join_job = next(
            (job for job in join_snapshot.jobs if job.stage_id == StageId("join")),
            None,
        )
        self._require(join_job is not None)
        if join_job is not None and join_job.state is not JobState.SUCCEEDED:
            main_now = self._recovery_time(join_snapshot, main_now)
            join = self._one_claim(WorkerId("worker-join"), main_now)
            self._require(join.stage_id == StageId("join"))
            self._require(self._repository.start(join, now=main_now).accepted)
            self._require(
                self._repository.complete(
                    join, OutcomeKind.COMPLETED, now=main_now
                ).accepted
            )
        self._require(self._snapshot(main).state is RunState.SUCCEEDED)
        checks.append("fan-in")

        negative = RunId("heartbeat-negative")
        self._require(
            self._repository.create_or_resume(
                negative, definition, changed_stages=frozenset(), now=self._START
            ).accepted
        )
        negative_snapshot = self._snapshot(negative)
        if negative_snapshot.state is not RunState.FAILED_TERMINAL:
            self._require(
                self._repository.enqueue_eligible(negative, now=self._START).accepted
            )
            negative_snapshot = self._snapshot(negative)
        negative_root_job = next(
            job for job in negative_snapshot.jobs if job.stage_id == StageId("root")
        )
        negative_now = self._START
        if negative_root_job.state is not JobState.SUCCEEDED:
            negative_root, negative_now = self._resume_claim(
                negative,
                StageId("root"),
                WorkerId("worker-negative"),
                negative_now,
            )
            self._require(
                not self._repository.complete(
                    negative_root, cast(OutcomeKind, object()), now=negative_now
                ).accepted
            )
            self._require(
                self._repository.complete(
                    negative_root, OutcomeKind.COMPLETED, now=negative_now
                ).accepted
            )
        self._require(self._repository.enqueue_eligible(negative, now=negative_now).accepted)
        negative_snapshot = self._snapshot(negative)
        unfinished_negative = {
            job.stage_id
            for job in negative_snapshot.jobs
            if job.stage_id in {StageId("left"), StageId("right")}
            and job.state not in {JobState.SUCCEEDED, JobState.FAILED_TERMINAL, JobState.CANCELLED}
        }
        if unfinished_negative:
            negative_now = self._recovery_time(negative_snapshot, negative_now)
            negative_branches = self._claims(
                WorkerId("worker-negative"), negative_now, len(unfinished_negative)
            )
            self._require(
                {claim.stage_id for claim in negative_branches} == unfinished_negative
            )
            for claim in negative_branches:
                self._require(self._repository.start(claim, now=negative_now).accepted)
            negative_left = next(
                (claim for claim in negative_branches if claim.stage_id == StageId("left")),
                None,
            )
            negative_right = next(
                (claim for claim in negative_branches if claim.stage_id == StageId("right")),
                None,
            )
            if negative_left is not None:
                self._require(
                    self._repository.complete(
                        negative_left, OutcomeKind.COMPLETED, now=negative_now
                    ).accepted
                )
            if negative_right is not None:
                self._require(self._repository.enqueue_eligible(negative, now=negative_now).accepted)
                negative_before = self._snapshot(negative)
                self._require(
                    all(job.stage_id != StageId("join") for job in negative_before.jobs)
                )
                self._require(
                    self._repository.fail(
                        negative_right,
                        FailureKind.SYNTHETIC_TERMINAL,
                        now=negative_now,
                    ).accepted
                )
        self._require(self._snapshot(negative).state is RunState.FAILED_TERMINAL)
        checks.append("negative-fan-in")

        retry_run = RunId("heartbeat-retry")
        self._require(
            self._repository.create_or_resume(
                retry_run, definition, changed_stages=frozenset(), now=self._START
            ).accepted
        )
        retry_snapshot = self._snapshot(retry_run)
        if retry_snapshot.state is not RunState.FAILED_TERMINAL:
            self._require(
                self._repository.enqueue_eligible(retry_run, now=self._START).accepted
            )
            retry_snapshot = self._snapshot(retry_run)
        retry_job = retry_snapshot.jobs[0]
        retry_now = self._START
        if retry_job.state is not JobState.FAILED_TERMINAL and retry_job.attempt == 0:
            first, retry_now = self._resume_claim(
                retry_run,
                StageId("root"),
                WorkerId("worker-retry"),
                retry_now,
            )
            self._require(
                self._repository.fail(
                    first, FailureKind.SYNTHETIC_RETRYABLE, now=retry_now
                ).accepted
            )
            early = self._repository.claim(
                WorkerId("worker-early"),
                capacity=1,
                now=retry_now + timedelta(seconds=4),
                lease_duration=self._LEASE,
            )
            self._require(early.accepted and early.value == ())
        retry_snapshot = self._snapshot(retry_run)
        retry_job = retry_snapshot.jobs[0]
        if retry_job.state is not JobState.FAILED_TERMINAL:
            second, retry_now = self._resume_claim(
                retry_run,
                StageId("root"),
                WorkerId("worker-retry"),
                retry_now + timedelta(seconds=5),
            )
            self._require(
                self._repository.fail(
                    second, FailureKind.SYNTHETIC_RETRYABLE, now=retry_now
                ).accepted
            )
        self._require(self._snapshot(retry_run).state is RunState.FAILED_TERMINAL)
        checks.append("retry-bounded")

        cancelled = RunId("heartbeat-cancel")
        self._require(
            self._repository.create_or_resume(
                cancelled, definition, changed_stages=frozenset(), now=self._START
            ).accepted
        )
        cancelled_snapshot = self._snapshot(cancelled)
        if cancelled_snapshot.state is not RunState.CANCELLED:
            self._require(
                self._repository.enqueue_eligible(cancelled, now=self._START).accepted
            )
            cancelled_snapshot = self._snapshot(cancelled)
        if cancelled_snapshot.state is not RunState.CANCELLED:
            cancelling_claim, cancel_now = self._resume_claim(
                cancelled,
                StageId("root"),
                WorkerId("worker-cancel"),
                self._START,
            )
            self._require(self._repository.cancel_run(cancelled, now=cancel_now).accepted)
            self._require(
                not self._repository.complete(
                    cancelling_claim, OutcomeKind.COMPLETED, now=cancel_now
                ).accepted
            )
            self._require(
                self._repository.acknowledge_cancellation(
                    cancelling_claim, now=cancel_now
                ).accepted
            )
        else:
            self._repository.claim(
                WorkerId("worker-cancel-recovery"),
                capacity=1,
                now=self._recovery_time(cancelled_snapshot, self._START),
                lease_duration=self._LEASE,
            )
        self._require(self._snapshot(cancelled).state is RunState.CANCELLED)
        checks.append("cancellation")

        required_cancelled = RunId("heartbeat-required-cancel")
        self._require(
            self._repository.create_or_resume(
                required_cancelled,
                definition,
                changed_stages=frozenset(),
                now=self._START,
            ).accepted
        )
        required_snapshot = self._snapshot(required_cancelled)
        if required_snapshot.state is not RunState.FAILED_TERMINAL:
            self._require(
                self._repository.enqueue_eligible(
                    required_cancelled, now=self._START
                ).accepted
            )
            required_snapshot = self._snapshot(required_cancelled)
        if required_snapshot.state is not RunState.FAILED_TERMINAL:
            required_claim, required_now = self._resume_claim(
                required_cancelled,
                StageId("root"),
                WorkerId("worker-required-cancel"),
                self._START,
            )
            self._require(
                self._repository.cancel_stage(
                    required_cancelled, StageId("root"), now=required_now
                ).accepted
            )
            self._require(
                not self._repository.complete(
                    required_claim, OutcomeKind.COMPLETED, now=required_now
                ).accepted
            )
            self._require(
                self._repository.acknowledge_cancellation(
                    required_claim, now=required_now
                ).accepted
            )
        else:
            self._repository.claim(
                WorkerId("worker-required-recovery"),
                capacity=1,
                now=self._recovery_time(required_snapshot, self._START),
                lease_duration=self._LEASE,
            )
        required_snapshot = self._snapshot(required_cancelled)
        required_states = {
            stage.id.value: stage.state for stage in required_snapshot.stages
        }
        self._require(required_states["root"] is StageState.CANCELLED)
        self._require(
            required_states["left"] is StageState.DEPENDENCY_CANCELLED
            and required_states["right"] is StageState.DEPENDENCY_CANCELLED
            and required_states["join"] is StageState.DEPENDENCY_CANCELLED
        )
        checks.append("dependency-cancellation")

        resumed = self._repository.create_or_resume(
            main, definition, changed_stages=frozenset(), now=recovered_at
        )
        self._require(resumed.outcome is RepositoryOutcome.REUSED)
        checks.append("compatible-resume")

        replanned = self._repository.create_or_resume(
            main,
            synthetic_heartbeat_definition(incompatible=True),
            changed_stages=frozenset({StageId("left")}),
            now=recovered_at,
        )
        self._require(replanned.accepted and replanned.value is not None)
        replacement = replanned.value
        self._require(replacement.replanned_from == main)
        source = self._snapshot(main)
        self._require(source.superseded_by == replacement.id)
        checks.append("incompatible-replan")
        flags = {
            stage.id.value: (stage.reuse_eligible, stage.current_applicable)
            for stage in replacement.stages
        }
        self._require(flags["root"] == (True, True))
        self._require(flags["right"] == (True, True))
        self._require(flags["left"] == (False, False))
        self._require(flags["join"] == (False, False))
        checks.append("transitive-invalidation")
        return tuple(checks)

    def _snapshot(self, run_id: RunId) -> RunSnapshot:
        result = self._repository.inspect(run_id)
        self._require(result.accepted and result.value is not None)
        return result.value

    def _one_claim(self, worker: WorkerId, now: datetime) -> JobClaim:
        claims = self._claims(worker, now, 1)
        self._require(len(claims) == 1)
        return claims[0]

    def _claims(
        self, worker: WorkerId, now: datetime, capacity: int
    ) -> tuple[JobClaim, ...]:
        result = self._repository.claim(
            worker,
            capacity=capacity,
            now=now,
            lease_duration=self._LEASE,
        )
        self._require(result.accepted and result.value is not None)
        return result.value

    def _resume_claim(
        self,
        run_id: RunId,
        stage_id: StageId,
        worker: WorkerId,
        minimum: datetime,
    ) -> tuple[JobClaim, datetime]:
        snapshot = self._snapshot(run_id)
        job = next((item for item in snapshot.jobs if item.stage_id == stage_id), None)
        self._require(job is not None)
        now = self._recovery_time(snapshot, minimum)
        claim = self._one_claim(worker, now)
        self._require(claim.run_id == run_id and claim.stage_id == stage_id)
        self._require(self._repository.start(claim, now=now).accepted)
        return claim, now

    @staticmethod
    def _job_stages(snapshot: RunSnapshot) -> set[str]:
        return {job.stage_id.value for job in snapshot.jobs}

    @staticmethod
    def _recovery_time(snapshot: RunSnapshot, minimum: datetime) -> datetime:
        expiries = tuple(
            instant
            for job in snapshot.jobs
            for instant in (job.lease_expires_at, job.next_eligible_at)
            if instant is not None
        )
        if not expiries:
            return minimum
        return max(minimum, max(expiries) + timedelta(seconds=1))

    @staticmethod
    def _require(condition: bool) -> None:
        if not condition:
            raise RuntimeError("synthetic heartbeat criterion failed")


__all__ = [
    "HeartbeatInspection",
    "HeartbeatOutcome",
    "HeartbeatResult",
    "JobClaim",
    "JobHeartbeatRepository",
    "JobSnapshot",
    "RepositoryOutcome",
    "RepositoryResult",
    "RunSnapshot",
    "RunSyntheticJobHeartbeat",
    "StageSnapshot",
]
