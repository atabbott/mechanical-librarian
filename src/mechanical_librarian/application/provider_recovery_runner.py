"""One bounded activation loop for provider recovery.

The runner deliberately owns no clock, thread, queue, or provider protocol. A
composition calls :meth:`run_one` from the existing review-preparation wake
cycle.  The recovery store supplies the only durable lease; therefore a manual
caller and the background caller cannot fan out concurrently.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from mechanical_librarian.domain.provider_recovery import (
    ProviderRecovery,
    ProviderRecoveryJob,
)


class ProviderRecoveryStore(Protocol):
    def enqueue_jobs(self, jobs: tuple[ProviderRecoveryJob, ...]) -> int: ...
    def claim_one(self, *, generation: int, now: int, token: str, blocked_targets: frozenset[str]) -> ProviderRecovery | None: ...
    def claim_job(self, *, recovery: ProviderRecovery, token: str, now: int, blocked_targets: frozenset[str]) -> ProviderRecoveryJob | None: ...
    def renew_job(self, *, job: ProviderRecoveryJob, token: str, now: int) -> None: ...
    def complete_job(self, *, job: ProviderRecoveryJob, token: str, now: int, success: bool) -> None: ...
    def complete_stale_job(self, *, job: ProviderRecoveryJob, token: str, now: int) -> None: ...
    def release_target_hold(self, *, job: ProviderRecoveryJob, token: str, recovery: ProviderRecovery, now: int) -> None: ...
    def complete_exhausted_scope(self, *, job: ProviderRecoveryJob, token: str, recovery: ProviderRecovery, now: int) -> ProviderRecovery: ...
    def complete_canary_failure(self, *, job: ProviderRecoveryJob, token: str, recovery: ProviderRecovery, now: int, failure: str, retry_after: int | None) -> ProviderRecovery: ...
    def authorize_fanout(self, *, job: ProviderRecoveryJob, token: str, recovery: ProviderRecovery, now: int) -> ProviderRecovery: ...
    def finish_fanout(self, *, recovery: ProviderRecovery, token: str, now: int) -> ProviderRecovery: ...
    def interrupt_fanout(self, *, job: ProviderRecoveryJob, token: str, recovery: ProviderRecovery, now: int, failure: str, retry_after: int | None) -> ProviderRecovery: ...
    def resume_fanout(self, *, generation: int, now: int, token: str, blocked_targets: frozenset[str]) -> ProviderRecovery | None: ...
    def fanout_pending(self, *, recovery: ProviderRecovery) -> bool: ...
    def complete_success(self, *, token: str, recovery: ProviderRecovery, now: int) -> ProviderRecovery: ...
    def complete_missing_jobs(self, *, token: str, recovery: ProviderRecovery, now: int, blocked_targets: frozenset[str]) -> ProviderRecovery | None: ...
    def complete(self, *, token: str, recovery: ProviderRecovery, now: int, failure: str, retry_after: int | None) -> ProviderRecovery: ...


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
        self, *, store: ProviderRecoveryStore, generation: int,
        retry: Callable[[ProviderRecovery, ProviderRecoveryJob, Callable[[], None]], None],
        classify_failure: Callable[[Exception], tuple[str, int | None]],
        clock: Callable[[], int] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        blocked_targets: Callable[[], frozenset[str]] = lambda: frozenset(),
    ) -> None:
        self._store, self._generation = store, generation
        self._retry, self._classify_failure = retry, classify_failure
        self._clock, self._cancelled, self._blocked_targets = clock, cancelled, blocked_targets

    def _current(self, floor: int) -> int:
        return floor if self._clock is None else max(floor, self._clock())

    def enqueue(self, jobs: tuple[ProviderRecoveryJob, ...]) -> int:
        """Persist the complete eligible set before it becomes claimable."""
        return self._store.enqueue_jobs(jobs)

    def run_one(self, *, now: int, token: str | None = None) -> RecoveryRun:
        token = token or uuid4().hex
        current = self._current(now)
        blocked = self._blocked_targets()
        fanout_state = self._store.resume_fanout(
            generation=self._generation, now=current, token=token, blocked_targets=blocked,
        )
        claimed = None if fanout_state is not None else self._store.claim_one(
            generation=self._generation, now=current, token=token, blocked_targets=blocked,
        )
        if fanout_state is None and claimed is None:
            return RecoveryRun("idle")
        if fanout_state is not None:
            canary = None
        else:
            assert claimed is not None
            canary = self._store.claim_job(recovery=claimed, token=token, now=current, blocked_targets=blocked)
        if fanout_state is not None:
            pass
        elif canary is None:
            # Due health without an exact member is incomplete durable state,
            # never evidence that the provider recovered.
            missing = self._store.complete_missing_jobs(
                token=token, recovery=claimed, now=self._current(current), blocked_targets=blocked,
            )
            return RecoveryRun("exhausted" if missing is not None else "idle")
        if fanout_state is None:
            assert claimed is not None
            while canary is not None:
                def renew_canary(
                    member: ProviderRecoveryJob = canary,
                    claim_token: str = token,
                ) -> None:
                    self._store.renew_job(
                        job=member, token=claim_token,
                        now=self._current(current),
                    )

                try:
                    self._retry(claimed, canary, renew_canary)
                except Exception as error:
                    failure, retry_after = self._classify_failure(error)
                    current = self._current(current)
                    if failure == "target_held":
                        self._store.release_target_hold(
                            job=canary, token=token, recovery=claimed, now=current,
                        )
                        return RecoveryRun("target_held")
                    if failure == "stale":
                        self._store.complete_stale_job(
                            job=canary, token=token, now=current,
                        )
                        canary = self._store.claim_job(
                            recovery=claimed, token=token, now=current, blocked_targets=blocked,
                        )
                        continue
                    if failure not in {"transient", "rate_limited", "authentication", "authorization", "configuration"}:
                        self._store.complete_exhausted_scope(
                            job=canary, token=token, recovery=claimed, now=current,
                        )
                        return RecoveryRun("exhausted")
                    self._store.complete_canary_failure(
                        job=canary, token=token, recovery=claimed, now=current,
                        failure=failure, retry_after=retry_after,
                    )
                    return RecoveryRun("waiting")
                current = self._current(current)
                fanout_state = self._store.authorize_fanout(
                    job=canary, token=token, recovery=claimed, now=current,
                )
                break
            if canary is None:
                self._store.complete_success(
                    token=token, recovery=claimed, now=self._current(current),
                )
                return RecoveryRun("recovered")
        assert fanout_state is not None
        fanout = 0
        while not self._cancelled():
            current = self._current(current)
            member = self._store.claim_job(
                recovery=fanout_state, token=token, now=current, blocked_targets=blocked,
            )
            if member is None:
                break
            try:
                def renew_member(
                    active: ProviderRecoveryJob = member,
                    claim_token: str = token,
                ) -> None:
                    self._store.renew_job(
                        job=active, token=claim_token,
                        now=self._current(current),
                    )

                self._retry(fanout_state, member, renew_member)
                current = self._current(current)
                self._store.complete_job(
                    job=member, token=token, now=current, success=True,
                )
                fanout += 1
            except Exception as error:
                current = self._current(current)
                failure, retry_after = self._classify_failure(error)
                if failure == "target_held":
                    self._store.release_target_hold(
                        job=member, token=token, recovery=fanout_state, now=current,
                    )
                    return RecoveryRun("target_held", fanout)
                if failure == "stale":
                    self._store.complete_stale_job(
                        job=member, token=token, now=current,
                    )
                elif failure in {
                    "transient", "rate_limited", "authentication",
                    "authorization", "configuration",
                }:
                    self._store.interrupt_fanout(
                        job=member, token=token, recovery=fanout_state,
                        now=current, failure=failure, retry_after=retry_after,
                    )
                    return RecoveryRun("waiting", fanout)
                else:
                    self._store.complete_job(
                        job=member, token=token, now=current, success=False,
                    )
        if self._cancelled():
            return RecoveryRun("fanout", fanout)
        if self._store.fanout_pending(recovery=fanout_state):
            return RecoveryRun("fanout", fanout)
        completed = self._store.finish_fanout(
            recovery=fanout_state, token=token, now=self._current(current),
        )
        return RecoveryRun(completed.state, fanout)


__all__ = ["ProviderRecoveryStore", "RecoveryMember", "RecoveryRun", "ScopedProviderRecoveryRunner"]
