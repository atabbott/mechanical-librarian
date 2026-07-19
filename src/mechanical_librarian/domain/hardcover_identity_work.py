"""Closed state and value types for durable Hardcover identity work."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum


class IdentityWorkState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IdentityWorkOutcome(StrEnum):
    MATCHED = "matched"
    STALE = "stale"
    PERSISTENCE_FAILURE = "persistence_failure"
    IN_PROGRESS = "in_progress"
    RETRY_EXHAUSTED = "retry_exhausted"
    CANCELLED = "cancelled"


class IdentityMatchOutcome(StrEnum):
    MATCHED = "matched"


ACTIVE_STATES = frozenset(
    {IdentityWorkState.PENDING, IdentityWorkState.CLAIMED, IdentityWorkState.RETRY_WAIT}
)
TERMINAL_STATES = frozenset(
    {IdentityWorkState.SUCCEEDED, IdentityWorkState.FAILED, IdentityWorkState.CANCELLED}
)

LEGAL_TRANSITIONS = frozenset(
    {
        (IdentityWorkState.PENDING, IdentityWorkState.CLAIMED),
        (IdentityWorkState.RETRY_WAIT, IdentityWorkState.CLAIMED),
        (IdentityWorkState.CLAIMED, IdentityWorkState.RETRY_WAIT),
        (IdentityWorkState.CLAIMED, IdentityWorkState.SUCCEEDED),
        (IdentityWorkState.CLAIMED, IdentityWorkState.FAILED),
        (IdentityWorkState.CLAIMED, IdentityWorkState.CANCELLED),
        (IdentityWorkState.PENDING, IdentityWorkState.FAILED),
        (IdentityWorkState.RETRY_WAIT, IdentityWorkState.FAILED),
        (IdentityWorkState.PENDING, IdentityWorkState.CANCELLED),
        (IdentityWorkState.RETRY_WAIT, IdentityWorkState.CANCELLED),
    }
)


def validate_transition(before: IdentityWorkState, after: IdentityWorkState) -> None:
    """Reject undeclared transitions instead of relying on persistence convention."""

    if type(before) is not IdentityWorkState or type(after) is not IdentityWorkState:
        raise TypeError("identity work transition is malformed")
    if (before, after) not in LEGAL_TRANSITIONS:
        raise ValueError("identity work transition is not allowed")


@dataclass(frozen=True, slots=True)
class IdentityWorkReference:
    work_key: str
    generation: int

    def __post_init__(self) -> None:
        if not _digest(self.work_key) or type(self.generation) is not int or self.generation < 1:
            raise ValueError("identity work reference is malformed")


@dataclass(frozen=True, slots=True)
class IdentityWorkClaim:
    reference: IdentityWorkReference
    candidate: int
    fence: int
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        if type(self.reference) is not IdentityWorkReference:
            raise ValueError("identity work claim is malformed")
        if type(self.candidate) is not int or not 1 <= self.candidate <= 3:
            raise ValueError("identity work claim is malformed")
        if type(self.fence) is not int or self.fence < 1:
            raise ValueError("identity work claim is malformed")
        _require_time(self.lease_expires_at)


@dataclass(frozen=True, slots=True)
class IdentityWorkInspection:
    """The minimum durable state needed to resume one active generation."""

    reference: IdentityWorkReference
    state: IdentityWorkState
    candidate: int
    fence: int
    aggregate_deadline: datetime
    retry_due_at: datetime | None = None
    claim: IdentityWorkClaim | None = None

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not IdentityWorkReference
            or type(self.state) is not IdentityWorkState
            or self.state not in ACTIVE_STATES
            or type(self.candidate) is not int
            or not 1 <= self.candidate <= 3
            or type(self.fence) is not int
            or self.fence < 0
        ):
            raise ValueError("identity work inspection is malformed")
        _require_time(self.aggregate_deadline)
        if self.retry_due_at is not None:
            _require_time(self.retry_due_at)
        if self.claim is not None and type(self.claim) is not IdentityWorkClaim:
            raise ValueError("identity work inspection is malformed")
        valid = {
            IdentityWorkState.PENDING: (
                self.candidate == 1
                and self.fence == 0
                and self.retry_due_at is None
                and self.claim is None
            ),
            IdentityWorkState.RETRY_WAIT: (
                2 <= self.candidate <= 3
                and self.fence >= 2
                and self.retry_due_at is not None
                and self.claim is None
                and self.retry_due_at <= self.aggregate_deadline
            ),
            IdentityWorkState.CLAIMED: (
                self.fence >= 1
                and self.retry_due_at is None
                and self.claim is not None
                and self.claim.reference == self.reference
                and self.claim.candidate == self.candidate
                and self.claim.fence == self.fence
            ),
        }[self.state]
        if not valid:
            raise ValueError("identity work inspection is malformed")


@dataclass(frozen=True, slots=True)
class IdentityWorkStatus:
    outcome: IdentityWorkOutcome
    reference: IdentityWorkReference
    candidate: int | None = None

    def __post_init__(self) -> None:
        if type(self.outcome) is not IdentityWorkOutcome:
            raise ValueError("identity work status is malformed")
        if type(self.reference) is not IdentityWorkReference:
            raise ValueError("identity work status is malformed")
        if self.candidate is not None and (
            type(self.candidate) is not int or not 1 <= self.candidate <= 3
        ):
            raise ValueError("identity work status is malformed")


@dataclass(frozen=True, slots=True)
class IdentityWorkObservation:
    """Closed provider-neutral structural result safe for durable persistence."""

    outcome: IdentityMatchOutcome
    row_count: int

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not IdentityMatchOutcome
            or type(self.row_count) is not int
            or not 1 <= self.row_count <= 4
        ):
            raise ValueError("identity work observation is malformed")

    def to_canonical_json(self) -> str:
        return json.dumps(
            {"outcome": self.outcome.value, "row_count": self.row_count},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_canonical_json(cls, value: object) -> IdentityWorkObservation:
        if type(value) is not str or not 2 <= len(value.encode("utf-8")) <= 128:
            raise ValueError("identity work observation is malformed")
        try:
            source = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            raise ValueError("identity work observation is malformed") from None
        if type(source) is not dict or set(source) != {"outcome", "row_count"}:
            raise ValueError("identity work observation is malformed")
        try:
            observation = cls(
                IdentityMatchOutcome(source["outcome"]), source["row_count"]
            )
        except (TypeError, ValueError):
            raise ValueError("identity work observation is malformed") from None
        if observation.to_canonical_json() != value:
            raise ValueError("identity work observation is malformed")
        return observation


def _digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and not set(value) - set("0123456789abcdef")


def _require_time(value: object) -> None:
    if (
        type(value) is not datetime
        or value.tzinfo is None
        or value.utcoffset() is None
        or not datetime.min.replace(tzinfo=timezone.utc)
        <= value.astimezone(timezone.utc)
        <= datetime.max.replace(tzinfo=timezone.utc)
    ):
        raise ValueError("identity work timestamp is malformed")


__all__ = [
    "ACTIVE_STATES",
    "IdentityWorkClaim",
    "IdentityWorkInspection",
    "IdentityMatchOutcome",
    "IdentityWorkObservation",
    "IdentityWorkOutcome",
    "IdentityWorkReference",
    "IdentityWorkState",
    "IdentityWorkStatus",
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "validate_transition",
]
