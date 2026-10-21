"""One bounded activation loop for provider recovery.

The runner deliberately owns no clock, thread, queue, or provider protocol. A
composition calls :meth:`run_one` from the existing review-preparation wake
cycle.  The recovery store supplies the only durable lease; therefore a manual
caller and the background caller cannot fan out concurrently.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol, cast
from uuid import uuid4

from mechanical_librarian.application.failure_diagnostics import (
    emit_provider_events,
    provider_recovery_job_events,
)
from mechanical_librarian.application.provider_recovery_evidence import (
    settled_recovery_evidence,
)
from mechanical_librarian.domain.provider_recovery import (
    RECOVERY_FAILURES,
    ProviderRecovery,
    ProviderRecoveryJob,
    RecoveryAttemptEvidence,
)


class ProviderRecoveryStore(Protocol):
    def enqueue_jobs(
        self, jobs: tuple[ProviderRecoveryJob, ...], *, enqueued_at: int
    ) -> int: ...
    def claim_one(
        self, *, generation: int, now: int, token: str, blocked_targets: frozenset[str]
    ) -> ProviderRecovery | None: ...
    def claim_job(
        self,
        *,
        recovery: ProviderRecovery,
        token: str,
        now: int,
        blocked_targets: frozenset[str],
    ) -> ProviderRecoveryJob | None: ...
    def renew_job(self, *, job: ProviderRecoveryJob, token: str, now: int) -> None: ...
    def complete_job(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        now: int,
        success: bool,
        recovery: ProviderRecovery,
        evidence: RecoveryAttemptEvidence | None = None,
    ) -> None: ...
    def complete_stale_job(
        self, *, job: ProviderRecoveryJob, token: str, now: int
    ) -> None: ...
    def release_target_hold(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
    ) -> None: ...
    def complete_exhausted_scope(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        evidence: RecoveryAttemptEvidence | None = None,
    ) -> ProviderRecovery: ...
    def complete_canary_failure(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        evidence: RecoveryAttemptEvidence | None = None,
        failure: str,
        retry_after: int | None,
    ) -> ProviderRecovery: ...
    def authorize_fanout(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        evidence: RecoveryAttemptEvidence | None = None,
    ) -> ProviderRecovery: ...
    def finish_fanout(
        self, *, recovery: ProviderRecovery, token: str, now: int
    ) -> ProviderRecovery: ...
    def interrupt_fanout(
        self,
        *,
        job: ProviderRecoveryJob,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        evidence: RecoveryAttemptEvidence | None = None,
        failure: str,
        retry_after: int | None,
    ) -> ProviderRecovery: ...
    def resume_fanout(
        self, *, generation: int, now: int, token: str, blocked_targets: frozenset[str]
    ) -> ProviderRecovery | None: ...
    def fanout_pending(self, *, recovery: ProviderRecovery) -> bool: ...
    def complete_success(
        self, *, token: str, recovery: ProviderRecovery, now: int
    ) -> ProviderRecovery: ...
    def complete_missing_jobs(
        self,
        *,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        blocked_targets: frozenset[str],
    ) -> ProviderRecovery | None: ...
    def complete(
        self,
        *,
        token: str,
        recovery: ProviderRecovery,
        now: int,
        failure: str,
        retry_after: int | None,
    ) -> ProviderRecovery: ...


@dataclass(frozen=True, slots=True)
class RecoveryMember:
    """Exact-current retry input, retained by the activation composition."""

    report_id: str
    lane_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.report_id, str)
            or not self.report_id
            or not self.lane_identities
            or tuple(sorted(set(self.lane_identities))) != self.lane_identities
        ):
            raise ValueError("provider recovery member is invalid")


@dataclass(frozen=True, slots=True)
class RecoveryRun:
    state: str
    fanout: int = 0


class ScopedProviderRecoveryRunner:
    """Claim one deterministic canary and atomically close or back off its scope."""

    def __init__(
        self,
        *,
        store: ProviderRecoveryStore,
        generation: int,
        retry: Callable[
            [ProviderRecovery, ProviderRecoveryJob, Callable[[], None]],
            RecoveryAttemptEvidence | None,
        ],
        classify_failure: Callable[[Exception], tuple[str, int | None]],
        clock: Callable[[], int] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
        blocked_targets: Callable[[], frozenset[str]] = lambda: frozenset(),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store, self._generation = store, generation
        self._retry, self._classify_failure = retry, classify_failure
        self._clock, self._cancelled, self._blocked_targets = (
            clock,
            cancelled,
            blocked_targets,
        )
        self._monotonic = monotonic

    def _job_event(
        self,
        started: float,
        job: ProviderRecoveryJob,
        transition: str,
        evidence: RecoveryAttemptEvidence | None = None,
    ) -> None:
        """Name one job transition the store has already committed (#1299).

        Called only after the store call returns, so an event never describes
        a settlement that did not happen; the clock is read inside the guard.
        """
        emit_provider_events(
            lambda: provider_recovery_job_events(
                job=job,
                transition=transition,
                evidence=evidence,
                elapsed_seconds=self._monotonic() - started,
            )
        )

    def _current(self, floor: int) -> int:
        return floor if self._clock is None else max(floor, self._clock())

    def enqueue(
        self, jobs: tuple[ProviderRecoveryJob, ...], *, enqueued_at: int
    ) -> int:
        """Persist the complete eligible set before it becomes claimable."""
        return self._store.enqueue_jobs(jobs, enqueued_at=enqueued_at)

    def run_one(self, *, now: int, token: str | None = None) -> RecoveryRun:
        started = self._monotonic()
        token = token or uuid4().hex
        current = self._current(now)
        blocked = self._blocked_targets()
        fanout_state = self._store.resume_fanout(
            generation=self._generation,
            now=current,
            token=token,
            blocked_targets=blocked,
        )
        claimed = (
            None
            if fanout_state is not None
            else self._store.claim_one(
                generation=self._generation,
                now=current,
                token=token,
                blocked_targets=blocked,
            )
        )
        if fanout_state is None and claimed is None:
            return RecoveryRun("idle")
        if fanout_state is not None:
            canary = None
        else:
            assert claimed is not None
            canary = self._store.claim_job(
                recovery=claimed, token=token, now=current, blocked_targets=blocked
            )
            if canary is not None:
                self._job_event(started, canary, "started")
        if fanout_state is not None:
            pass
        elif canary is None:
            # Due health without an exact member is incomplete durable state,
            # never evidence that the provider recovered.
            missing = self._store.complete_missing_jobs(
                # This branch is only reachable while ``fanout_state`` is None,
                # which is exactly where ``claimed`` was already asserted above.
                token=token,
                recovery=cast("ProviderRecovery", claimed),
                now=self._current(current),
                blocked_targets=blocked,
            )
            return RecoveryRun(missing.state if missing is not None else "idle")
        if fanout_state is None:
            assert claimed is not None
            while canary is not None:

                def renew_canary(
                    member: ProviderRecoveryJob = canary,
                    claim_token: str = token,
                ) -> None:
                    self._store.renew_job(
                        job=member,
                        token=claim_token,
                        now=self._current(current),  # noqa: B023 - `current` is deliberately read late: the renewal must use the newest clock reading, and `self._retry` invokes the renewal before `current` is next reassigned
                    )

                try:
                    evidence = self._retry(claimed, canary, renew_canary)
                except Exception as error:
                    # ``_classify_failure`` is the narrowing: it inspects the
                    # error and names the transaction outcome, so breadth here
                    # hands it every failure rather than pre-filtering by a type
                    # the injected retry seam never declared. A failure allowed
                    # to escape instead would leave the canary job claimed and
                    # its target hold unreleased.
                    failure, retry_after = self._classify_failure(error)
                    evidence = settled_recovery_evidence(error, failure)
                    current = self._current(current)
                    if failure == "target_held":
                        self._store.release_target_hold(
                            job=canary,
                            token=token,
                            recovery=claimed,
                            now=current,
                        )
                        self._job_event(started, canary, "held")
                        return RecoveryRun("target_held")
                    if failure == "stale":
                        self._store.complete_stale_job(
                            job=canary,
                            token=token,
                            now=current,
                        )
                        self._job_event(started, canary, "stale")
                        canary = self._store.claim_job(
                            recovery=claimed,
                            token=token,
                            now=current,
                            blocked_targets=blocked,
                        )
                        if canary is not None:
                            self._job_event(started, canary, "started")
                        continue
                    if failure not in RECOVERY_FAILURES:
                        exhausted = self._store.complete_exhausted_scope(
                            job=canary,
                            token=token,
                            recovery=claimed,
                            now=current,
                            evidence=evidence,
                        )
                        self._job_event(started, canary, "failed", evidence)
                        return RecoveryRun(exhausted.state)
                    self._store.complete_canary_failure(
                        job=canary,
                        token=token,
                        recovery=claimed,
                        now=current,
                        evidence=evidence,
                        failure=failure,
                        retry_after=retry_after,
                    )
                    self._job_event(started, canary, "failed", evidence)
                    return RecoveryRun("waiting")
                current = self._current(current)
                fanout_state = self._store.authorize_fanout(
                    job=canary,
                    token=token,
                    recovery=claimed,
                    now=current,
                    evidence=evidence,
                )
                self._job_event(
                    started,
                    canary,
                    "settled",
                    evidence or RecoveryAttemptEvidence("completed"),
                )
                break
            if canary is None:
                completed = self._store.complete_success(
                    token=token,
                    recovery=claimed,
                    now=self._current(current),
                )
                return RecoveryRun(completed.state)
        assert fanout_state is not None
        fanout = 0
        while not self._cancelled():
            current = self._current(current)
            member = self._store.claim_job(
                recovery=fanout_state,
                token=token,
                now=current,
                blocked_targets=blocked,
            )
            if member is None:
                break
            self._job_event(started, member, "started")
            completed_evidence = None
            try:

                def renew_member(
                    active: ProviderRecoveryJob = member,
                    claim_token: str = token,
                ) -> None:
                    self._store.renew_job(
                        job=active,
                        token=claim_token,
                        now=self._current(current),  # noqa: B023 - `current` is deliberately read late: the renewal must use the newest clock reading, and `self._retry` invokes the renewal before `current` is next reassigned
                    )

                evidence = self._retry(fanout_state, member, renew_member)
                completed_evidence = evidence or RecoveryAttemptEvidence("completed")
                current = self._current(current)
                self._store.complete_job(
                    job=member,
                    token=token,
                    now=current,
                    success=True,
                    recovery=fanout_state,
                    evidence=evidence,
                )
                self._job_event(started, member, "settled", completed_evidence)
                fanout += 1
            except Exception as error:
                # ``_classify_failure`` does the naming here as it does in the
                # canary lane, but this block is wider than that one: it also
                # covers the clock reading and the store write that completes a
                # member that succeeded, so a completion that fails is settled
                # through the same path as a retry that fails. That is the
                # invariant the breadth serves -- every claimed member reaches
                # ``_classify_failure`` -- because a member whose failure
                # escaped would be left claimed with its target hold unreleased.
                current = self._current(current)
                failure, retry_after = self._classify_failure(error)
                evidence = (
                    replace(completed_evidence, outcome="terminal_failure")
                    if completed_evidence is not None
                    else settled_recovery_evidence(error, failure)
                )
                if failure == "target_held":
                    self._store.release_target_hold(
                        job=member,
                        token=token,
                        recovery=fanout_state,
                        now=current,
                    )
                    self._job_event(started, member, "held")
                    return RecoveryRun("target_held", fanout)
                if failure == "stale":
                    self._store.complete_stale_job(
                        job=member,
                        token=token,
                        now=current,
                    )
                    self._job_event(started, member, "stale")
                elif failure in RECOVERY_FAILURES:
                    self._store.interrupt_fanout(
                        job=member,
                        token=token,
                        recovery=fanout_state,
                        now=current,
                        evidence=evidence,
                        failure=failure,
                        retry_after=retry_after,
                    )
                    self._job_event(started, member, "failed", evidence)
                    return RecoveryRun("waiting", fanout)
                else:
                    self._store.complete_job(
                        job=member,
                        token=token,
                        now=current,
                        success=False,
                        recovery=fanout_state,
                        evidence=evidence,
                    )
                    self._job_event(started, member, "failed", evidence)
        if self._cancelled():
            return RecoveryRun("fanout", fanout)
        if self._store.fanout_pending(recovery=fanout_state):
            return RecoveryRun("fanout", fanout)
        completed = self._store.finish_fanout(
            recovery=fanout_state,
            token=token,
            now=self._current(current),
        )
        return RecoveryRun(completed.state, fanout)


__all__ = [
    "ProviderRecoveryStore",
    "RecoveryMember",
    "RecoveryRun",
    "ScopedProviderRecoveryRunner",
]
