"""Bounded, immutable workflow definitions for durable synthetic execution."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.domain.identifiers import (
    DomainValueError,
    Identifier,
    Version,
    canonical_json,
)
from mechanical_librarian.domain.lifecycle import (
    JobState,
    RunState,
    StageState,
    Transition,
    transition,
)


MAX_WORKFLOW_STAGES = 32
MAX_WORKFLOW_DEPENDENCIES = 64


class RunId(Identifier):
    kind = "run ID"


class StageId(Identifier):
    kind = "stage ID"


class WorkerId(Identifier):
    kind = "worker ID"


class WorkflowVersion(Version):
    kind = "workflow version"


class StageVersion(Version):
    kind = "stage version"


class OutcomeKind(StrEnum):
    COMPLETED = "completed"


class FailureKind(StrEnum):
    SYNTHETIC_RETRYABLE = "synthetic_retryable"
    SYNTHETIC_TERMINAL = "synthetic_terminal"
    LEASE_EXPIRED = "lease_expired"
    DEPENDENCY_FAILED = "dependency_failed"
    DEPENDENCY_CANCELLED = "dependency_cancelled"
    INVALID_DEPENDENCY_OUTCOME = "invalid_dependency_outcome"


def validate_run_transition(current: RunState, target: RunState) -> Transition[RunState]:
    """Reject a run-state edge outside the authoritative workflow lifecycle."""

    if type(current) is not RunState or type(target) is not RunState:
        raise DomainValueError("run transitions require exact typed states")
    if current is target:
        raise DomainValueError("run lifecycle persistence must not record a no-op")
    return transition(
        current,
        target,
        actor="job repository",
        reason="persist durable run transition",
    )


def validate_stage_transition(current: StageState, target: StageState) -> Transition[StageState]:
    """Reject a stage-state edge outside the authoritative workflow lifecycle."""

    if type(current) is not StageState or type(target) is not StageState:
        raise DomainValueError("stage transitions require exact typed states")
    if current is target:
        raise DomainValueError("stage lifecycle persistence must not record a no-op")
    return transition(
        current,
        target,
        actor="job repository",
        reason="persist durable stage transition",
    )


@dataclass(frozen=True)
class StageDefinition:
    id: StageId
    kind: str
    version: StageVersion
    expected_outcome: OutcomeKind = OutcomeKind.COMPLETED

    def __post_init__(self) -> None:
        if type(self.id) is not StageId or type(self.version) is not StageVersion:
            raise DomainValueError("workflow stages require typed identity and version")
        if self.kind not in {"root", "branch", "join"}:
            raise DomainValueError("workflow stage kind is unsupported")
        if type(self.expected_outcome) is not OutcomeKind:
            raise DomainValueError("workflow stages require a typed expected outcome")


@dataclass(frozen=True)
class Dependency:
    prerequisite: StageId
    dependent: StageId

    def __post_init__(self) -> None:
        if type(self.prerequisite) is not StageId or type(self.dependent) is not StageId:
            raise DomainValueError("workflow dependencies require typed stage identities")
        if self.prerequisite == self.dependent:
            raise DomainValueError("workflow dependencies cannot be self-referential")


@dataclass(frozen=True)
class WorkflowDefinition:
    version: WorkflowVersion
    stages: tuple[StageDefinition, ...]
    dependencies: tuple[Dependency, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not WorkflowVersion:
            raise DomainValueError("workflow definitions require a typed version")
        if type(self.stages) is not tuple or not 1 <= len(self.stages) <= MAX_WORKFLOW_STAGES:
            raise DomainValueError("workflow definitions require a bounded stage tuple")
        if type(self.dependencies) is not tuple or len(self.dependencies) > MAX_WORKFLOW_DEPENDENCIES:
            raise DomainValueError("workflow definitions require a bounded dependency tuple")
        if any(type(stage) is not StageDefinition for stage in self.stages):
            raise DomainValueError("workflow definitions require exact stage definitions")
        if any(type(edge) is not Dependency for edge in self.dependencies):
            raise DomainValueError("workflow definitions require exact dependencies")

        stage_ids = tuple(stage.id for stage in self.stages)
        if len(set(stage_ids)) != len(stage_ids):
            raise DomainValueError("workflow stage identities must be unique")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise DomainValueError("workflow dependencies must be unique")
        known = set(stage_ids)
        if any(edge.prerequisite not in known or edge.dependent not in known for edge in self.dependencies):
            raise DomainValueError("workflow dependencies must reference declared stages")
        self.topological_order()

    @property
    def digest(self) -> str:
        value = {
            "schema": "workflow-graph-v1",
            "version": self.version.value,
            "stages": [
                {
                    "id": stage.id.value,
                    "kind": stage.kind,
                    "version": stage.version.value,
                    "expected_outcome": stage.expected_outcome.value,
                }
                for stage in sorted(self.stages, key=lambda value: value.id.value)
            ],
            "dependencies": [
                {
                    "prerequisite": edge.prerequisite.value,
                    "dependent": edge.dependent.value,
                    "required": True,
                }
                for edge in sorted(
                    self.dependencies,
                    key=lambda value: (value.prerequisite.value, value.dependent.value),
                )
            ],
        }
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    def topological_order(self) -> tuple[StageId, ...]:
        declared = {stage.id for stage in self.stages}
        incoming = {stage_id: 0 for stage_id in declared}
        outgoing: dict[StageId, set[StageId]] = {stage_id: set() for stage_id in declared}
        for edge in self.dependencies:
            incoming[edge.dependent] += 1
            outgoing[edge.prerequisite].add(edge.dependent)
        ready = sorted(
            (stage_id for stage_id, count in incoming.items() if count == 0),
            key=lambda value: value.value,
        )
        ordered: list[StageId] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for dependent in sorted(outgoing[current], key=lambda value: value.value):
                incoming[dependent] -= 1
                if incoming[dependent] == 0:
                    ready.append(dependent)
                    ready.sort(key=lambda value: value.value)
        if len(ordered) != len(declared):
            raise DomainValueError("workflow definitions must be acyclic")
        return tuple(ordered)

    def descendants(self, changed: frozenset[StageId]) -> frozenset[StageId]:
        known = {stage.id for stage in self.stages}
        if not changed <= known:
            raise DomainValueError("changed workflow stages must be declared")
        outgoing: dict[StageId, set[StageId]] = {stage_id: set() for stage_id in known}
        for edge in self.dependencies:
            outgoing[edge.prerequisite].add(edge.dependent)
        affected = set(changed)
        pending = list(changed)
        while pending:
            current = pending.pop()
            for dependent in outgoing[current]:
                if dependent not in affected:
                    affected.add(dependent)
                    pending.append(dependent)
        return frozenset(affected)

    def stage(self, stage_id: StageId) -> StageDefinition:
        for stage in self.stages:
            if stage.id == stage_id:
                return stage
        raise DomainValueError("workflow stage is not declared")


def synthetic_heartbeat_definition(*, incompatible: bool = False) -> WorkflowDefinition:
    """Return the fixed, report-only F02 fork-and-join graph."""

    branch_version = "stage-v2" if incompatible else "stage-v1"
    return WorkflowDefinition(
        version=WorkflowVersion("heartbeat-v2" if incompatible else "heartbeat-v1"),
        stages=(
            StageDefinition(StageId("root"), "root", StageVersion("stage-v1")),
            StageDefinition(StageId("left"), "branch", StageVersion(branch_version)),
            StageDefinition(StageId("right"), "branch", StageVersion("stage-v1")),
            StageDefinition(StageId("join"), "join", StageVersion("stage-v1")),
        ),
        dependencies=(
            Dependency(StageId("root"), StageId("left")),
            Dependency(StageId("root"), StageId("right")),
            Dependency(StageId("left"), StageId("join")),
            Dependency(StageId("right"), StageId("join")),
        ),
    )


__all__ = [
    "Dependency",
    "FailureKind",
    "JobState",
    "MAX_WORKFLOW_DEPENDENCIES",
    "MAX_WORKFLOW_STAGES",
    "OutcomeKind",
    "RunId",
    "RunState",
    "StageDefinition",
    "StageId",
    "StageState",
    "StageVersion",
    "WorkerId",
    "WorkflowDefinition",
    "WorkflowVersion",
    "synthetic_heartbeat_definition",
    "validate_run_transition",
    "validate_stage_transition",
]
