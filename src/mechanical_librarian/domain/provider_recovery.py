"""Closed, non-executing provider recovery state transitions.

This module intentionally decides eligibility only.  It neither imports a
provider adapter nor exposes a replay operation; activation owns execution.
"""

from __future__ import annotations

from dataclasses import dataclass


class ProviderRecoveryError(ValueError):
    """A value-free provider recovery boundary failure."""


_TRANSIENT = {"transient", "rate_limited"}
_DORMANT = {"authentication", "authorization", "configuration"}
_BACKOFF = (7_200, 14_400, 28_800, 57_600, 86_400)


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
                or self.state not in {"waiting", "dormant", "checking"}
                or type(self.attempt) is not int or not 0 <= self.attempt <= len(_BACKOFF)
                or (self.next_eligible_at is not None and (type(self.next_eligible_at) is not int or self.next_eligible_at < 0))):
            raise ProviderRecoveryError("provider recovery is invalid")
        if (self.state == "waiting") != (self.next_eligible_at is not None):
            raise ProviderRecoveryError("provider recovery is invalid")

    @classmethod
    def failed(
        cls, *, connection: str, adapter: str, scope: str, generation: int,
        failure: str, now: int, retry_after: int | None = None,
    ) -> "ProviderRecovery":
        if type(now) is not int or now < 0 or failure not in _TRANSIENT | _DORMANT:
            raise ProviderRecoveryError("provider recovery failure is invalid")
        if failure in _DORMANT:
            return cls(connection, adapter, scope, generation, "dormant", 0, None)
        if retry_after is not None and (type(retry_after) is not int or not 1 <= retry_after <= 86_400):
            raise ProviderRecoveryError("provider retry delay is invalid")
        return cls(connection, adapter, scope, generation, "waiting", 1, now + (retry_after or _BACKOFF[0]))

    def retry(self, *, now: int, retry_after: int | None = None) -> "ProviderRecovery":
        if self.state != "waiting" or type(now) is not int or now < 0:
            raise ProviderRecoveryError("provider recovery retry is invalid")
        if retry_after is not None and (type(retry_after) is not int or not 1 <= retry_after <= 86_400):
            raise ProviderRecoveryError("provider retry delay is invalid")
        attempt = min(self.attempt + 1, len(_BACKOFF))
        return ProviderRecovery(self.connection, self.adapter, self.scope, self.generation,
                                "waiting", attempt, now + (retry_after or _BACKOFF[attempt - 1]))

    def with_generation(self, generation: int) -> "ProviderRecovery":
        if type(generation) is not int or generation < self.generation:
            raise ProviderRecoveryError("provider recovery generation is invalid")
        if self.state == "dormant" and generation > self.generation:
            return ProviderRecovery(self.connection, self.adapter, self.scope, generation, "waiting", 0, 0)
        return ProviderRecovery(self.connection, self.adapter, self.scope, generation, self.state, self.attempt, self.next_eligible_at)


__all__ = ["ProviderRecovery", "ProviderRecoveryError"]
