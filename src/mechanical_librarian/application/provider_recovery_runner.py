"""One bounded activation loop for provider recovery.

The runner deliberately owns no clock, thread, queue, or provider protocol. A
composition calls :meth:`run_one` from the existing review-preparation wake
cycle.  The recovery store supplies the only durable lease; therefore a manual
caller and the background caller cannot fan out concurrently.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from mechanical_librarian.adapters.persistence.provider_recovery import ProviderRecoveryJob, SQLiteProviderRecoveryStore
from mechanical_librarian.domain.provider_recovery import ProviderRecovery


@dataclass(frozen=True, slots=True)
class RecoveryMember:
    """Exact-current retry input, retained by the activation composition."""

    report_id: str
    lane_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.report_id, str) or not self.report_id or not self.lane_identities or tuple(sorted(set(self.lane_identities))) != self.lane_identities:
            raise ValueError("provider recovery member is invalid")


@dataclass(frozen=True, slots=True)
class RecoveryRun:
    state: str
    fanout: int = 0


class ScopedProviderRecoveryRunner:
    """Claim one deterministic canary and atomically close or back off its scope."""

    def __init__(
        self, *, store: SQLiteProviderRecoveryStore, generation: int,
        retry: Callable[[ProviderRecovery, ProviderRecoveryJob], None],
        classify_failure: Callable[[Exception], tuple[str, int | None]],
    ) -> None:
        self._store, self._generation = store, generation
        self._retry, self._classify_failure = retry, classify_failure

    def enqueue(self, jobs: tuple[ProviderRecoveryJob, ...]) -> int:
        """Persist the complete eligible set before it becomes claimable."""
        return self._store.enqueue_jobs(jobs)

    def run_one(self, *, now: int, token: str | None = None) -> RecoveryRun:
        token = token or uuid4().hex
        claimed = self._store.claim_one(generation=self._generation, now=now, token=token)
        if claimed is None:
            return RecoveryRun("idle")
        canary = self._store.claim_job(recovery=claimed, token=token, now=now)
        if canary is None:
            # A stale package is not provider health. It closes safely and
            # cannot create a retry storm or invoke a provider.
            self._store.complete_success(token=token, recovery=claimed, now=now)
            return RecoveryRun("recovered")
        try:
            self._retry(claimed, canary)
        except Exception as error:
            failure, retry_after = self._classify_failure(error)
            if failure not in {"transient", "rate_limited", "authentication", "authorization", "configuration"}:
                self._store.complete_job(job=canary, token=token, now=now, success=False)
                self._store.complete_success(token=token, recovery=claimed, now=now)
                return RecoveryRun("nonretryable")
            self._store.complete_job(job=canary, token=token, now=now, success=False)
            self._store.complete(token=token, recovery=claimed, now=now, failure=failure, retry_after=retry_after)
            return RecoveryRun("waiting")
        self._store.complete_job(job=canary, token=token, now=now, success=True)
        self._store.complete_success(token=token, recovery=claimed, now=now)
        fanout = 0
        while (member := self._store.claim_job(recovery=claimed, token=token, now=now)) is not None:
            try:
                self._retry(claimed, member)
                self._store.complete_job(job=member, token=token, now=now, success=True)
                fanout += 1
            except Exception:
                self._store.complete_job(job=member, token=token, now=now, success=False)
        return RecoveryRun("recovered", fanout)


__all__ = ["RecoveryMember", "RecoveryRun", "ScopedProviderRecoveryRunner"]
