"""Durable, bounded inventory polling values with no target-service authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from .inventory_v2 import ScanUsage


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class PollState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED_TERMINAL = "failed_terminal"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class PollPolicy:
    max_attempts: int
    max_elapsed_seconds: int
    lease_seconds: int
    initial_backoff_seconds: int
    max_backoff_seconds: int
    max_pages: int
    max_items: int
    max_bytes: int

    def __post_init__(self) -> None:
        if (
            type(self.max_attempts) is not int
            or not 1 <= self.max_attempts <= 16
            or type(self.max_elapsed_seconds) is not int
            or not 1 <= self.max_elapsed_seconds <= 86_400
            or type(self.lease_seconds) is not int
            or not 1 <= self.lease_seconds <= 3_600
            or type(self.initial_backoff_seconds) is not int
            or not 1 <= self.initial_backoff_seconds <= 3_600
            or type(self.max_backoff_seconds) is not int
            or not self.initial_backoff_seconds <= self.max_backoff_seconds <= 86_400
            or type(self.max_pages) is not int
            or not 1 <= self.max_pages <= 10_240_000
            or type(self.max_items) is not int
            or not 1 <= self.max_items <= 10_240_000
            or type(self.max_bytes) is not int
            or not 1_024 <= self.max_bytes <= 68_719_476_736
        ):
            raise ValueError("inventory poll policy is invalid")


@dataclass(frozen=True)
class PollRequest:
    poll_id: str
    trigger_id: str
    scope_digest: str
    governing_digest: str
    policy: PollPolicy

    def __post_init__(self) -> None:
        if (
            type(self.poll_id) is not str
            or _IDENTIFIER.fullmatch(self.poll_id) is None
            or type(self.trigger_id) is not str
            or _IDENTIFIER.fullmatch(self.trigger_id) is None
            or type(self.scope_digest) is not str
            or _DIGEST.fullmatch(self.scope_digest) is None
            or type(self.governing_digest) is not str
            or _DIGEST.fullmatch(self.governing_digest) is None
            or type(self.policy) is not PollPolicy
        ):
            raise ValueError("inventory poll request is invalid")


@dataclass(frozen=True)
class PollClaim:
    poll_id: str
    attempt: int
    fence: int
    worker_id: str
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        if (
            type(self.poll_id) is not str
            or _IDENTIFIER.fullmatch(self.poll_id) is None
            or type(self.attempt) is not int
            or self.attempt < 1
            or type(self.fence) is not int
            or self.fence < 1
            or type(self.worker_id) is not str
            or _IDENTIFIER.fullmatch(self.worker_id) is None
            or self.lease_expires_at.tzinfo is None
            or self.lease_expires_at.utcoffset() is None
        ):
            raise ValueError("inventory poll claim is invalid")


@dataclass(frozen=True)
class PollInspection:
    poll_id: str
    state: PollState
    attempt: int
    max_attempts: int
    fence: int
    usage: ScanUsage
    next_eligible_at: datetime | None
    failure_kind: str | None
    compatibility: str | None
    cancellation_requested: bool

    def __post_init__(self) -> None:
        if (
            type(self.poll_id) is not str
            or _IDENTIFIER.fullmatch(self.poll_id) is None
            or type(self.state) is not PollState
            or type(self.attempt) is not int
            or not 0 <= self.attempt <= self.max_attempts
            or type(self.max_attempts) is not int
            or not 1 <= self.max_attempts <= 16
            or type(self.fence) is not int
            or self.fence < 0
            or type(self.usage) is not ScanUsage
            or (
                self.next_eligible_at is not None
                and (
                    self.next_eligible_at.tzinfo is None
                    or self.next_eligible_at.utcoffset() is None
                )
            )
            or (self.failure_kind is not None and not self.failure_kind)
            or (self.compatibility is not None and not self.compatibility)
            or type(self.cancellation_requested) is not bool
        ):
            raise ValueError("inventory poll inspection is invalid")


def retry_delay(policy: PollPolicy, attempt: int) -> timedelta:
    """Return bounded exponential backoff from frozen durable policy."""

    seconds = min(
        policy.max_backoff_seconds,
        policy.initial_backoff_seconds * (2 ** max(0, attempt - 1)),
    )
    return timedelta(seconds=seconds)


__all__ = [
    "PollClaim",
    "PollInspection",
    "PollPolicy",
    "PollRequest",
    "PollState",
    "retry_delay",
]
