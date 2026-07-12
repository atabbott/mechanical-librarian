"""Pure, explicit transitions for the authoritative workflow lifecycles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

from .evidence import Disposition
from .identifiers import DomainValueError


class JobState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"


class RunState(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class StageState(StrEnum):
    WAITING = "waiting"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"
    DEPENDENCY_FAILED = "dependency_failed"
    DEPENDENCY_CANCELLED = "dependency_cancelled"


class ReviewCaseState(StrEnum):
    OPEN = "open"
    AUTO_RESOLVED = "auto_resolved"
    NEEDS_REVIEW = "needs_review"
    HUMAN_RESOLVED = "human_resolved"
    UNSUPPORTED = "unsupported"
    SUPERSEDED = "superseded"


class ProposalDecisionState(StrEnum):
    PROPOSED = "proposed"
    AI_APPROVED = "ai_approved"
    AI_REJECTED = "ai_rejected"
    AI_MANUAL = "ai_manual"
    DETERMINISTIC_REJECTED = "deterministic_rejected"
    UNSUPPORTED = "unsupported"


class BulkDecisionBatchState(StrEnum):
    PREVIEWED = "previewed"
    COMMITTED = "committed"
    STALE = "stale"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApplyPlanState(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    BACKUP_READY = "backup_ready"
    QUEUED = "queued"
    APPLYING = "applying"
    VERIFIED = "verified"
    ROLLBACK_PENDING = "rollback_pending"
    FAILED = "failed"
    STALE = "stale"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"


class CircuitBreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class AuthorizationState(StrEnum):
    AUTHORIZED = "authorized"
    REJECTED = "rejected"
    STALE = "stale"
    SUPERSEDED = "superseded"
    OPERATIONAL_FAILURE = "operational_failure"


class AuthorizationEvent(StrEnum):
    AUTOMATIC_POLICY = "automatic_policy"
    HUMAN_DECISION = "human_decision"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    STALE_INPUT = "stale_input"
    OPERATIONAL_FAILURE = "operational_failure"


class HumanDecisionApplicability(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


_AUTHORIZATION_REASONS_BY_STATE = {
    AuthorizationState.AUTHORIZED: frozenset({"authorization is current"}),
    AuthorizationState.REJECTED: frozenset(
        {
            "authorization requires an applicable disposition",
            "automatic authorization cannot carry a human decision",
            "automatic authorization requires policy qualification",
            "destructive work has no automatic authorization path",
            "human authorization cannot carry automatic policy evidence",
            "invalid authorization",
        }
    ),
    AuthorizationState.STALE: frozenset(
        {
            "automatic disposition is no longer resolved",
            "automatic policy qualification is no longer current",
            "human decision is no longer current",
            "review case evidence changed",
        }
    ),
    AuthorizationState.SUPERSEDED: frozenset({"review case was replaced"}),
    AuthorizationState.OPERATIONAL_FAILURE: frozenset(
        {"temporary policy source unavailable"}
    ),
}
@dataclass(frozen=True)
class AuthorizationResult:
    state: AuthorizationState
    event: AuthorizationEvent
    reason: str
    retryable: bool
    terminal: bool
    idempotent_replay: bool

    def __post_init__(self) -> None:
        if (
            type(self.state) is not AuthorizationState
            or type(self.event) is not AuthorizationEvent
        ):
            raise DomainValueError("authorization results require exact state and event types")
        if (
            type(self.reason) is not str
            or self.reason not in _AUTHORIZATION_REASONS_BY_STATE[self.state]
        ):
            raise DomainValueError(
                "authorization reason is inconsistent with its result state"
            )
        if any(
            type(value) is not bool
            for value in (self.retryable, self.terminal, self.idempotent_replay)
        ):
            raise DomainValueError("authorization result flags must be exact booleans")
        legal = {
            AuthorizationState.AUTHORIZED: (
                {
                    AuthorizationEvent.AUTOMATIC_POLICY,
                    AuthorizationEvent.HUMAN_DECISION,
                    AuthorizationEvent.IDEMPOTENT_REPLAY,
                },
                False,
                False,
            ),
            AuthorizationState.REJECTED: ({AuthorizationEvent.HUMAN_DECISION}, False, True),
            AuthorizationState.STALE: ({AuthorizationEvent.STALE_INPUT}, False, True),
            AuthorizationState.SUPERSEDED: ({AuthorizationEvent.STALE_INPUT}, False, True),
            AuthorizationState.OPERATIONAL_FAILURE: (
                {AuthorizationEvent.OPERATIONAL_FAILURE},
                True,
                False,
            ),
        }
        events, retryable, terminal = legal[self.state]
        if (
            self.event not in events
            or self.retryable is not retryable
            or self.terminal is not terminal
        ):
            raise DomainValueError("authorization result state, event, and flags are inconsistent")
        if self.idempotent_replay is not (self.event is AuthorizationEvent.IDEMPOTENT_REPLAY):
            raise DomainValueError("only idempotent replay events may be marked as replayed")

    @classmethod
    def authorized(
        cls, event: AuthorizationEvent = AuthorizationEvent.IDEMPOTENT_REPLAY
    ) -> "AuthorizationResult":
        return cls(
            AuthorizationState.AUTHORIZED,
            event,
            "authorization is current",
            False,
            False,
            event is AuthorizationEvent.IDEMPOTENT_REPLAY,
        )

    @classmethod
    def rejected(cls, reason: str) -> "AuthorizationResult":
        return cls(
            AuthorizationState.REJECTED,
            AuthorizationEvent.HUMAN_DECISION,
            reason,
            False,
            True,
            False,
        )

    @classmethod
    def stale(cls, reason: str) -> "AuthorizationResult":
        return cls(
            AuthorizationState.STALE,
            AuthorizationEvent.STALE_INPUT,
            reason,
            False,
            True,
            False,
        )

    @classmethod
    def superseded(cls, reason: str) -> "AuthorizationResult":
        return cls(
            AuthorizationState.SUPERSEDED,
            AuthorizationEvent.STALE_INPUT,
            reason,
            False,
            True,
            False,
        )

    @classmethod
    def operational_failure(cls, reason: str) -> "AuthorizationResult":
        return cls(
            AuthorizationState.OPERATIONAL_FAILURE,
            AuthorizationEvent.OPERATIONAL_FAILURE,
            reason,
            True,
            False,
            False,
        )


S = TypeVar("S", bound=StrEnum)


@dataclass(frozen=True)
class Transition(Generic[S]):
    from_state: S
    to_state: S
    actor: str
    reason: str
    reproducibility_context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.from_state) is not type(self.to_state)
            or type(self.from_state) not in _STATE_TABLES
        ):
            raise InvalidTransition("lifecycle transitions require one exact supported state type")
        if self.to_state not in _table_for(self.from_state).get(self.from_state, frozenset()):
            raise InvalidTransition("lifecycle transition edge is not permitted")
        _validate_text(self.actor, "transition actor", 128)
        _validate_text(self.reason, "transition reason", 256)
        if type(self.reproducibility_context) is not tuple:
            raise DomainValueError("lifecycle transition context must be an immutable tuple")
        if len(self.reproducibility_context) > 8:
            raise DomainValueError("lifecycle transitions permit at most eight context entries")
        if any(
            type(entry) is not tuple or len(entry) != 2
            for entry in self.reproducibility_context
        ):
            raise DomainValueError("lifecycle transition context entries must be exact pairs")
        if len({key for key, _ in self.reproducibility_context}) != len(
            self.reproducibility_context
        ):
            raise DomainValueError("lifecycle transition context keys must be unique")
        for key, value in self.reproducibility_context:
            _validate_text(key, "transition context key", 64)
            _validate_text(value, "transition context value", 128)


class InvalidTransition(DomainValueError):
    """Raised when a caller attempts a transition outside its state machine."""


_JOB: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset({JobState.LEASED, JobState.FAILED_TERMINAL, JobState.CANCELLED}),
    JobState.LEASED: frozenset({JobState.RUNNING, JobState.PENDING, JobState.FAILED_TERMINAL, JobState.CANCELLED}),
    JobState.RUNNING: frozenset({JobState.SUCCEEDED, JobState.RETRY_WAIT, JobState.FAILED_TERMINAL, JobState.CANCELLED, JobState.PENDING}),
    JobState.RETRY_WAIT: frozenset({JobState.PENDING, JobState.FAILED_TERMINAL, JobState.CANCELLED}),
}
_RUN: dict[RunState, frozenset[RunState]] = {
    RunState.WAITING: frozenset(
        {RunState.RUNNING, RunState.SUCCEEDED, RunState.FAILED_TERMINAL, RunState.CANCELLED, RunState.SUPERSEDED}
    ),
    RunState.RUNNING: frozenset(
        {RunState.WAITING, RunState.SUCCEEDED, RunState.FAILED_TERMINAL, RunState.CANCELLED, RunState.SUPERSEDED}
    ),
    RunState.SUCCEEDED: frozenset({RunState.SUPERSEDED}),
}
_STAGE: dict[StageState, frozenset[StageState]] = {
    StageState.WAITING: frozenset(
        {StageState.READY, StageState.SUCCEEDED, StageState.CANCELLED, StageState.DEPENDENCY_FAILED, StageState.DEPENDENCY_CANCELLED}
    ),
    StageState.READY: frozenset(
        {StageState.RUNNING, StageState.CANCELLED, StageState.FAILED_TERMINAL, StageState.DEPENDENCY_FAILED, StageState.DEPENDENCY_CANCELLED}
    ),
    StageState.RUNNING: frozenset(
        {StageState.READY, StageState.SUCCEEDED, StageState.CANCELLED, StageState.FAILED_TERMINAL, StageState.DEPENDENCY_FAILED, StageState.DEPENDENCY_CANCELLED}
    ),
}
_REVIEW: dict[ReviewCaseState, frozenset[ReviewCaseState]] = {
    ReviewCaseState.OPEN: frozenset({ReviewCaseState.AUTO_RESOLVED, ReviewCaseState.NEEDS_REVIEW, ReviewCaseState.UNSUPPORTED, ReviewCaseState.SUPERSEDED}),
    ReviewCaseState.NEEDS_REVIEW: frozenset({ReviewCaseState.HUMAN_RESOLVED, ReviewCaseState.SUPERSEDED}),
}
_PROPOSAL: dict[ProposalDecisionState, frozenset[ProposalDecisionState]] = {
    ProposalDecisionState.PROPOSED: frozenset(set(ProposalDecisionState) - {ProposalDecisionState.PROPOSED}),
}
_BATCH: dict[BulkDecisionBatchState, frozenset[BulkDecisionBatchState]] = {
    BulkDecisionBatchState.PREVIEWED: frozenset({BulkDecisionBatchState.COMMITTED, BulkDecisionBatchState.STALE, BulkDecisionBatchState.EXPIRED, BulkDecisionBatchState.CANCELLED}),
}
_APPLY: dict[ApplyPlanState, frozenset[ApplyPlanState]] = {
    ApplyPlanState.DRAFT: frozenset({ApplyPlanState.VALIDATED, ApplyPlanState.STALE, ApplyPlanState.CANCELLED}),
    ApplyPlanState.VALIDATED: frozenset({ApplyPlanState.BACKUP_READY, ApplyPlanState.STALE, ApplyPlanState.CANCELLED}),
    ApplyPlanState.BACKUP_READY: frozenset({ApplyPlanState.QUEUED, ApplyPlanState.STALE, ApplyPlanState.CANCELLED}),
    ApplyPlanState.QUEUED: frozenset({ApplyPlanState.APPLYING, ApplyPlanState.STALE, ApplyPlanState.CANCELLED}),
    ApplyPlanState.APPLYING: frozenset({ApplyPlanState.VERIFIED, ApplyPlanState.ROLLBACK_PENDING, ApplyPlanState.FAILED}),
    ApplyPlanState.ROLLBACK_PENDING: frozenset({ApplyPlanState.ROLLED_BACK, ApplyPlanState.ROLLBACK_FAILED}),
}
_BREAKER: dict[CircuitBreakerState, frozenset[CircuitBreakerState]] = {
    CircuitBreakerState.CLOSED: frozenset({CircuitBreakerState.OPEN}),
    CircuitBreakerState.OPEN: frozenset({CircuitBreakerState.HALF_OPEN}),
    CircuitBreakerState.HALF_OPEN: frozenset({CircuitBreakerState.CLOSED, CircuitBreakerState.OPEN}),
}
_STATE_TABLES: dict[type[StrEnum], object] = {
    JobState: _JOB,
    RunState: _RUN,
    StageState: _STAGE,
    ReviewCaseState: _REVIEW,
    ProposalDecisionState: _PROPOSAL,
    BulkDecisionBatchState: _BATCH,
    ApplyPlanState: _APPLY,
    CircuitBreakerState: _BREAKER,
}


def transition(
    current: S, target: S, *, actor: str, reason: str, reproducibility_context: tuple[tuple[str, str], ...] = ()
) -> Transition[S]:
    """Validate and describe one state change without mutating any entity."""

    return Transition(current, target, actor, reason, reproducibility_context)


def _table_for(state: S) -> dict[S, frozenset[S]]:
    return _STATE_TABLES[type(state)]  # type: ignore[return-value,index]


def _validate_text(value: object, label: str, maximum: int) -> None:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value) > maximum
        or not value.isprintable()
    ):
        raise DomainValueError(f"{label} must be bounded trimmed printable text")
