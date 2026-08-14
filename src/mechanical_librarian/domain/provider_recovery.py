"""Closed, non-executing provider recovery state transitions.

This module intentionally decides eligibility only.  It neither imports a
provider adapter nor exposes a replay operation; activation owns execution.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderRecoveryError(ValueError):
    """A value-free provider recovery boundary failure."""


@dataclass(frozen=True, slots=True)
class ProviderRecoveryJob:
    """The immutable exact-current input for one scoped retry member."""

    connection: str; adapter: str; scope: str; generation: int; member_id: str
    target_connection: str; library: str; item: str; file_identity: str
    source_report_id: str; package_proposal_id: str | None; package_report_id: str
    package_case_id: str | None; package_checkpoint: int | None; package_outcome: str; lane_ids: str
    state: str = "queued"; attempt: int = 0

    def __post_init__(self) -> None:
        strings = (self.connection, self.adapter, self.scope, self.member_id, self.target_connection, self.library, self.item, self.file_identity, self.source_report_id, self.package_report_id, self.package_outcome, self.lane_ids)
        package = (self.package_proposal_id, self.package_case_id, self.package_checkpoint)
        valid_package = (
            (self.package_outcome == "report_only" and package == (None, None, None))
            or (self.package_outcome in {"admitted", "superseded"} and all(value is not None for value in package))
            or (self.package_outcome in {"no_case", "retired"} and self.package_proposal_id is not None and self.package_case_id is None and self.package_checkpoint is not None)
        )
        if (not all(type(value) is str and value for value in strings)
                or self.package_proposal_id is not None and (type(self.package_proposal_id) is not str or not self.package_proposal_id)
                or self.package_case_id is not None and (type(self.package_case_id) is not str or not self.package_case_id)
                or self.package_checkpoint is not None and (type(self.package_checkpoint) is not int or self.package_checkpoint < 0)
                or not valid_package or type(self.generation) is not int or self.generation < 1
                or self.state not in {"queued", "running", "complete", "failed", "stale"}
                or type(self.attempt) is not int or not 0 <= self.attempt <= 5):
            raise ProviderRecoveryError("provider recovery job is invalid")


_TRANSIENT = {"transient", "rate_limited"}
_DORMANT = {"authentication", "authorization", "configuration"}
_BACKOFF = (7_200, 14_400, 28_800, 57_600, 86_400)
MAX_TIMESTAMP = 8_640_000_000_000


@dataclass(frozen=True, slots=True)
class ProviderRecovery:
    connection: str
    adapter: str
    scope: str
    generation: int
    state: str
    attempt: int
    next_eligible_at: int | None

    def __post_init__(self) -> None:
        if (not all(type(value) is str and 1 <= len(value) <= 64 for value in (self.connection, self.adapter, self.scope))
                or type(self.generation) is not int or self.generation < 1
                or self.state not in {
                    "waiting", "dormant", "checking", "fanout",
                    "recovered", "exhausted",
                }
                or type(self.attempt) is not int or not 0 <= self.attempt <= len(_BACKOFF)
                or (self.next_eligible_at is not None and (type(self.next_eligible_at) is not int or not 0 <= self.next_eligible_at <= MAX_TIMESTAMP))):
            raise ProviderRecoveryError("provider recovery is invalid")
        if (self.state == "waiting") != (self.next_eligible_at is not None):
            raise ProviderRecoveryError("provider recovery is invalid")
        if self.state in {"fanout", "recovered", "exhausted"} and self.next_eligible_at is not None:
            raise ProviderRecoveryError("provider recovery is invalid")
        if self.state == "recovered" and self.attempt != 0:
            raise ProviderRecoveryError("provider recovery is invalid")

    @classmethod
    def failed(
        cls, *, connection: str, adapter: str, scope: str, generation: int,
        failure: str, now: int, retry_after: int | None = None,
    ) -> "ProviderRecovery":
        if type(now) is not int or not 0 <= now <= MAX_TIMESTAMP or failure not in _TRANSIENT | _DORMANT:
            raise ProviderRecoveryError("provider recovery failure is invalid")
        if failure in _DORMANT:
            return cls(connection, adapter, scope, generation, "dormant", 0, None)
        if retry_after is not None and (type(retry_after) is not int or not 1 <= retry_after <= 86_400):
            raise ProviderRecoveryError("provider retry delay is invalid")
        deadline = now + (retry_after or _BACKOFF[0])
        if deadline > MAX_TIMESTAMP:
            raise ProviderRecoveryError("provider recovery failure is invalid")
        return cls(connection, adapter, scope, generation, "waiting", 1, deadline)

    def retry(self, *, now: int, retry_after: int | None = None) -> "ProviderRecovery":
        if self.state != "waiting" or type(now) is not int or not 0 <= now <= MAX_TIMESTAMP:
            raise ProviderRecoveryError("provider recovery retry is invalid")
        if retry_after is not None and (type(retry_after) is not int or not 1 <= retry_after <= 86_400):
            raise ProviderRecoveryError("provider retry delay is invalid")
        attempt = min(self.attempt + 1, len(_BACKOFF))
        deadline = now + (retry_after or _BACKOFF[attempt - 1])
        if deadline > MAX_TIMESTAMP:
            raise ProviderRecoveryError("provider recovery retry is invalid")
        return ProviderRecovery(self.connection, self.adapter, self.scope, self.generation,
                                "waiting", attempt, deadline)

    def with_generation(self, generation: int, *, now: int = 0) -> "ProviderRecovery":
        if (type(generation) is not int or generation < self.generation
                or type(now) is not int or not 0 <= now <= MAX_TIMESTAMP):
            raise ProviderRecoveryError("provider recovery generation is invalid")
        if self.state == "dormant" and generation > self.generation:
            return ProviderRecovery(self.connection, self.adapter, self.scope, generation, "waiting", 0, now)
        return ProviderRecovery(self.connection, self.adapter, self.scope, generation, self.state, self.attempt, self.next_eligible_at)


__all__ = ["MAX_TIMESTAMP", "ProviderRecovery", "ProviderRecoveryError", "ProviderRecoveryJob"]
