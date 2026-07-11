"""Pure, explicit transitions for the authoritative workflow lifecycles."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

from .identifiers import DomainValueError


class JobState(StrEnum):
    PENDING = "pending"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"


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


class Disposition(StrEnum):
    AUTO_APPLY = "auto_apply"
    NEEDS_REVIEW = "needs_review"
    CLOSED_REJECTED = "closed_rejected"
    UNSUPPORTED = "unsupported"


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


@dataclass(frozen=True)
class AuthorizationResult:
    state: AuthorizationState
    event: AuthorizationEvent
    reason: str
    retryable: bool
    terminal: bool
    idempotent_replay: bool

    @classmethod
    def authorized(cls, event: AuthorizationEvent = AuthorizationEvent.IDEMPOTENT_REPLAY) -> "AuthorizationResult":
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
        return cls(AuthorizationState.REJECTED, AuthorizationEvent.HUMAN_DECISION, reason, False, True, True)

    @classmethod
    def stale(cls, reason: str) -> "AuthorizationResult":
        return cls(AuthorizationState.STALE, AuthorizationEvent.STALE_INPUT, reason, False, True, False)

    @classmethod
    def superseded(cls, reason: str) -> "AuthorizationResult":
        return cls(AuthorizationState.SUPERSEDED, AuthorizationEvent.STALE_INPUT, reason, False, True, False)

    @classmethod
    def operational_failure(cls, reason: str) -> "AuthorizationResult":
        return cls(AuthorizationState.OPERATIONAL_FAILURE, AuthorizationEvent.OPERATIONAL_FAILURE, reason, True, False, False)


S = TypeVar("S", bound=StrEnum)


@dataclass(frozen=True)
class Transition(Generic[S]):
    from_state: S
    to_state: S
    actor: str
    reason: str
    reproducibility_context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.actor.strip() or not self.reason.strip():
            raise DomainValueError("lifecycle transitions require a non-empty actor and reason")
        if len(self.reproducibility_context) > 8:
            raise DomainValueError("lifecycle transitions permit at most eight context entries")
        if len({key for key, _ in self.reproducibility_context}) != len(self.reproducibility_context):
            raise DomainValueError("lifecycle transition context keys must be unique")
        if any(not key or not value or len(key) > 64 or len(value) > 128 for key, value in self.reproducibility_context):
            raise DomainValueError("lifecycle transition context must be bounded non-empty text")


class InvalidTransition(DomainValueError):
    """Raised when a caller attempts a transition outside its state machine."""


_JOB: dict[JobState, frozenset[JobState]] = {
    JobState.PENDING: frozenset({JobState.LEASED, JobState.CANCELLED}),
    JobState.LEASED: frozenset({JobState.RUNNING, JobState.PENDING, JobState.CANCELLED}),
    JobState.RUNNING: frozenset({JobState.SUCCEEDED, JobState.RETRY_WAIT, JobState.FAILED_TERMINAL, JobState.CANCELLED, JobState.PENDING}),
    JobState.RETRY_WAIT: frozenset({JobState.PENDING, JobState.CANCELLED}),
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


def transition(
    current: S, target: S, *, actor: str, reason: str, reproducibility_context: tuple[tuple[str, str], ...] = ()
) -> Transition[S]:
    """Validate and describe one state change without mutating any entity."""

    table = _table_for(current)
    if target not in table.get(current, frozenset()):
        raise InvalidTransition(f"{type(current).__name__} cannot transition from {current} to {target}")
    return Transition(current, target, actor, reason, reproducibility_context)


def can_authorize_apply(
    *, disposition: Disposition, human_decision_current: bool, destructive: bool
) -> bool:
    """Encode the authorization boundary without constructing an apply plan."""

    if destructive:
        return human_decision_current
    return disposition is Disposition.AUTO_APPLY or (
        disposition is Disposition.NEEDS_REVIEW and human_decision_current
    )


def _table_for(state: S) -> dict[S, frozenset[S]]:
    tables: dict[type[StrEnum], object] = {
        JobState: _JOB,
        ReviewCaseState: _REVIEW,
        ProposalDecisionState: _PROPOSAL,
        BulkDecisionBatchState: _BATCH,
        ApplyPlanState: _APPLY,
        CircuitBreakerState: _BREAKER,
    }
    return tables[type(state)]  # type: ignore[return-value,index]
