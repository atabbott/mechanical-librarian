"""Closed state and value types for durable Hardcover identity work."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from mechanical_librarian.domain.hardcover_identity import valid_identity_title


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
        (IdentityWorkState.PENDING, IdentityWorkState.CANCELLED),
        (IdentityWorkState.RETRY_WAIT, IdentityWorkState.CLAIMED),
        (IdentityWorkState.RETRY_WAIT, IdentityWorkState.CANCELLED),
        (IdentityWorkState.CLAIMED, IdentityWorkState.RETRY_WAIT),
        (IdentityWorkState.CLAIMED, IdentityWorkState.SUCCEEDED),
        (IdentityWorkState.CLAIMED, IdentityWorkState.FAILED),
        (IdentityWorkState.CLAIMED, IdentityWorkState.CANCELLED),
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
class IdentityWorkRetryToken:
    """Exact immutable durable retry-schedule identity."""

    reference: IdentityWorkReference
    candidate: int
    fence: int
    due_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not IdentityWorkReference
            or type(self.candidate) is not int
            or not 1 <= self.candidate <= 3
            or type(self.fence) is not int
            or self.fence < 2
        ):
            raise ValueError("identity work retry token is malformed")
        _require_time(self.due_at)


@dataclass(frozen=True, slots=True)
class IdentityWorkReadiness:
    """Exact-reference lifecycle facts required around one attempt."""

    reference: IdentityWorkReference
    state: IdentityWorkState
    aggregate_deadline: datetime
    retry_token: IdentityWorkRetryToken | None = None
    active_claim: IdentityWorkClaim | None = None
    observation: IdentityWorkObservation | None = None

    def __post_init__(self) -> None:
        if (
            type(self.reference) is not IdentityWorkReference
            or type(self.state) is not IdentityWorkState
        ):
            raise ValueError("identity work readiness is malformed")
        _require_time(self.aggregate_deadline)
        if (self.retry_token is not None) != (
            self.state is IdentityWorkState.RETRY_WAIT
        ):
            raise ValueError("identity work readiness is malformed")
        if self.retry_token is not None:
            if (
                type(self.retry_token) is not IdentityWorkRetryToken
                or self.retry_token.reference != self.reference
                or self.retry_token.due_at > self.aggregate_deadline
            ):
                raise ValueError("identity work readiness is malformed")
        if (self.active_claim is not None) != (
            self.state is IdentityWorkState.CLAIMED
        ):
            raise ValueError("identity work readiness is malformed")
        if (
            self.active_claim is not None
            and self.active_claim.reference != self.reference
        ):
            raise ValueError("identity work readiness is malformed")
        if self.observation is not None and type(self.observation) is not IdentityWorkObservation:
            raise ValueError("identity work readiness is malformed")
        if (self.observation is not None) != (
            self.state is IdentityWorkState.SUCCEEDED
        ):
            raise ValueError("identity work readiness is malformed")

    @property
    def retry_due_at(self) -> datetime | None:
        return None if self.retry_token is None else self.retry_token.due_at


@dataclass(frozen=True, slots=True)
class IdentityWorkStatus:
    outcome: IdentityWorkOutcome
    reference: IdentityWorkReference
    candidate: int | None = None
    observation: IdentityWorkObservation | None = None

    def __post_init__(self) -> None:
        if type(self.outcome) is not IdentityWorkOutcome:
            raise ValueError("identity work status is malformed")
        if type(self.reference) is not IdentityWorkReference:
            raise ValueError("identity work status is malformed")
        if self.candidate is not None and (
            type(self.candidate) is not int or not 1 <= self.candidate <= 3
        ):
            raise ValueError("identity work status is malformed")
        if self.observation is not None and type(self.observation) is not IdentityWorkObservation:
            raise ValueError("identity work status is malformed")
        if (self.observation is not None) != (
            self.outcome is IdentityWorkOutcome.MATCHED
        ):
            raise ValueError("identity work status is malformed")


@dataclass(frozen=True, repr=False, slots=True)
class IdentityWorkObservation:
    """Private matched identity needed for truthful current-match reuse."""

    outcome: IdentityMatchOutcome
    row_count: int
    edition_id: int | None = None
    book_id: int | None = None
    provider_title: str | None = None

    def __post_init__(self) -> None:
        legacy = (
            self.edition_id is None
            and self.book_id is None
            and self.provider_title is None
        )
        current = (
            _positive_int32(self.edition_id)
            and _positive_int32(self.book_id)
            and _valid_provider_title(self.provider_title)
        )
        if (
            type(self.outcome) is not IdentityMatchOutcome
            or type(self.row_count) is not int
            or not 1 <= self.row_count <= 4
            or not (legacy or current)
        ):
            raise ValueError("identity work observation is malformed")

    @property
    def reusable(self) -> bool:
        return self.edition_id is not None

    def to_canonical_json(self) -> str:
        source: dict[str, object] = {
            "outcome": self.outcome.value,
            "row_count": self.row_count,
        }
        if self.reusable:
            source.update(
                edition_id=self.edition_id,
                book_id=self.book_id,
                provider_title=self.provider_title,
            )
        return json.dumps(
            source,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_canonical_json(cls, value: object) -> IdentityWorkObservation:
        if type(value) is not str or not 2 <= len(value.encode("utf-8")) <= 2_304:
            raise ValueError("identity work observation is malformed")
        try:
            source = json.loads(value)
        except (json.JSONDecodeError, RecursionError):
            raise ValueError("identity work observation is malformed") from None
        if type(source) is not dict or set(source) not in {
            frozenset({"outcome", "row_count"}),
            frozenset(
                {"outcome", "row_count", "edition_id", "book_id", "provider_title"}
            ),
        }:
            raise ValueError("identity work observation is malformed")
        try:
            observation = cls(
                IdentityMatchOutcome(source["outcome"]),
                source["row_count"],
                source.get("edition_id"),
                source.get("book_id"),
                source.get("provider_title"),
            )
        except (TypeError, ValueError):
            raise ValueError("identity work observation is malformed") from None
        if observation.to_canonical_json() != value:
            raise ValueError("identity work observation is malformed")
        return observation

    def __repr__(self) -> str:
        return "IdentityWorkObservation(<redacted>)"


def _positive_int32(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2_147_483_647


def _valid_provider_title(value: object) -> bool:
    return (
        type(value) is str
        and valid_identity_title(value)
        and value.strip() == value
        and unicodedata.normalize("NFC", value) == value
    )


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
    "IdentityWorkReadiness",
    "IdentityMatchOutcome",
    "IdentityWorkObservation",
    "IdentityWorkOutcome",
    "IdentityWorkReference",
    "IdentityWorkRetryToken",
    "IdentityWorkState",
    "IdentityWorkStatus",
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "validate_transition",
]
