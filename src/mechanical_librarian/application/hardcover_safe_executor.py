"""Application-owned, bounded foreground lifecycle for Hardcover identity work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from mechanical_librarian.application.hardcover_identity import (
    BookIdentityProvider,
    HardcoverIdentityOutcome,
    HardcoverIdentityReport,
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    IdentityReportError,
    IdentityWorkPersistenceError,
    IsbnIdentityQuery,
    ManageHardcoverIdentityWork,
    StaleIdentityWorkFence,
)
from mechanical_librarian.application.ports import Deadline, OperationContext
from mechanical_librarian.domain.hardcover_identity import (
    IdentityDecisionOutcome,
    PrivateIdentityQuery,
    decide_book_identity,
)
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityMatchOutcome,
    IdentityWorkClaim,
    IdentityWorkInspection,
    IdentityWorkObservation,
    IdentityWorkOutcome,
    IdentityWorkState,
)


TRANSPORT_WINDOW = timedelta(seconds=5)
PUBLICATION_RESERVE = timedelta(seconds=1)
MINIMUM_START_WINDOW = TRANSPORT_WINDOW + PUBLICATION_RESERVE
MAX_STARTS = 3


@dataclass(frozen=True, repr=False, slots=True)
class HardcoverIdentityExecutionRequest:
    """Private request paired with a public-safe durable work digest."""

    work_key: str
    owner: str
    query: PrivateIdentityQuery
    one_shot: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.work_key) is not str
            or len(self.work_key) != 64
            or set(self.work_key) - set("0123456789abcdef")
            or type(self.owner) is not str
            or not 1 <= len(self.owner) <= 64
            or not self.owner[0].islower()
            or set(self.owner)
            - set("abcdefghijklmnopqrstuvwxyz0123456789_-")
            or type(self.query) is not PrivateIdentityQuery
            or type(self.one_shot) is not bool
        ):
            raise ValueError("Hardcover identity execution request is malformed")

    def __repr__(self) -> str:
        return "HardcoverIdentityExecutionRequest(<redacted>)"


class IdentityDeploymentGateLease(Protocol):
    """Held deployment-scoped send authority."""

    def validate_for_send(self) -> None: ...

    def release(self) -> None: ...


class IdentityDeploymentGate(Protocol):
    def try_acquire(self) -> IdentityDeploymentGateLease | None: ...


class IdentityWaiter(Protocol):
    """Injected wait edge; implementations must observe cancellation."""

    def wait_until(self, due_at: datetime, context: OperationContext) -> None: ...


class RunSafeHardcoverIdentity:
    """Own acceptance, fencing, send authority, retry, recovery, and publication."""

    def __init__(
        self,
        work: ManageHardcoverIdentityWork,
        provider: BookIdentityProvider,
        gate: IdentityDeploymentGate,
        waiter: IdentityWaiter,
    ) -> None:
        if type(work) is not ManageHardcoverIdentityWork:
            raise TypeError("Hardcover identity work manager is malformed")
        if not callable(getattr(provider, "lookup", None)):
            raise TypeError("book identity provider is malformed")
        if not callable(getattr(gate, "try_acquire", None)):
            raise TypeError("Hardcover deployment gate is malformed")
        if not callable(getattr(waiter, "wait_until", None)):
            raise TypeError("Hardcover identity waiter is malformed")
        self._work = work
        self._provider = provider
        self._gate = gate
        self._waiter = waiter

    def run(
        self,
        request: HardcoverIdentityExecutionRequest,
        context: OperationContext,
    ) -> HardcoverIdentityReport:
        if (
            type(request) is not HardcoverIdentityExecutionRequest
            or type(context) is not OperationContext
        ):
            raise TypeError("Hardcover identity execution input is malformed")
        try:
            accepted = self._work.accept(request.work_key, context.observed_at)
        except IdentityWorkPersistenceError:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        if accepted.outcome is IdentityWorkOutcome.MATCHED:
            return _report(HardcoverIdentityOutcome.MATCHED)
        reference = accepted.reference

        # At most three sends and one recovery transition can be observed by one
        # invocation. The bound also turns a non-advancing injected clock into a
        # public in-progress result rather than an application spin.
        for _ in range(8):
            now = context.observed_at
            try:
                active = self._work.inspect(reference, now)
            except IdentityWorkPersistenceError:
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)

            if context.cancelled:
                return self._cancel_waiting(active, now)
            if now >= active.aggregate_deadline:
                return self._finish_waiting(active, now)
            if (
                active.state is IdentityWorkState.RETRY_WAIT
                and active.retry_due_at is not None
                and now < active.retry_due_at
            ):
                self._waiter.wait_until(active.retry_due_at, context)
                if context.observed_at < active.retry_due_at:
                    return _report(HardcoverIdentityOutcome.IN_PROGRESS)
                continue
            if (
                active.state is IdentityWorkState.CLAIMED
                and active.claim is not None
                and now < active.claim.lease_expires_at
            ):
                self._waiter.wait_until(active.claim.lease_expires_at, context)
                if context.observed_at < active.claim.lease_expires_at:
                    return _report(HardcoverIdentityOutcome.IN_PROGRESS)
                continue

            lease = self._gate.try_acquire()
            if lease is None:
                return _report(HardcoverIdentityOutcome.IN_PROGRESS)
            wait_due: datetime | None = None
            gate_failure: BaseException | None = None
            try:
                result, wait_due = self._under_gate(
                    request, context, active, lease
                )
            except BaseException as error:
                gate_failure = error
                raise
            finally:
                try:
                    lease.release()
                except Exception:
                    if gate_failure is not None:
                        gate_failure.add_note("Hardcover deployment gate release failed")
                    else:
                        raise
            if result is not None:
                return result
            if wait_due is None:
                return _report(HardcoverIdentityOutcome.IN_PROGRESS)
            self._waiter.wait_until(wait_due, context)
            if context.observed_at < wait_due:
                return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        return _report(HardcoverIdentityOutcome.IN_PROGRESS)

    def _under_gate(
        self,
        request: HardcoverIdentityExecutionRequest,
        context: OperationContext,
        prior: IdentityWorkInspection,
        lease: IdentityDeploymentGateLease,
    ) -> tuple[HardcoverIdentityReport | None, datetime | None]:
        now = context.observed_at
        try:
            active = self._work.inspect(prior.reference, now)
            if context.cancelled:
                if active.state is IdentityWorkState.CLAIMED:
                    return self._cancel_claim(active.claim, request.owner, now), None
                return self._cancel_waiting(active, now), None
            if now >= active.aggregate_deadline:
                if active.state is IdentityWorkState.CLAIMED:
                    return _report(HardcoverIdentityOutcome.IN_PROGRESS), None
                return self._finish_waiting(active, now), None
            if active.state is IdentityWorkState.CLAIMED:
                claim = active.claim
                if claim is None:
                    raise IdentityWorkPersistenceError(
                        "identity work claim is unavailable"
                    )
                if now < claim.lease_expires_at:
                    return None, claim.lease_expires_at
                recovered = self._work.recover(claim, now, gate_owned=True)
                if type(recovered) is not int:
                    return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED), None
                active = self._work.inspect(active.reference, now)
            if (
                active.state is IdentityWorkState.RETRY_WAIT
                and active.retry_due_at is not None
                and now < active.retry_due_at
            ):
                return None, active.retry_due_at

            claim = self._work.claim(
                active.reference, request.owner, now, gate_owned=True
            )
            if context.cancelled:
                return self._cancel_claim(claim, request.owner, context.observed_at), None
            started_at = context.observed_at
            started = self._work.start(
                claim, request.owner, started_at, gate_owned=True
            )
            if type(started) is not IdentityWorkClaim:
                return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED), None
            claim = started
            if context.cancelled:
                return self._cancel_claim(claim, request.owner, context.observed_at), None
            lease.validate_for_send()
            query = IsbnIdentityQuery.from_private(request.query)
            attempt_context = OperationContext(
                Deadline(started_at + TRANSPORT_WINDOW),
                context.cancellation,
                context.resources,
                context.time,
            )
            try:
                attempt = self._provider.lookup(query, attempt_context)
            except Exception:
                attempt = IdentityAttemptResult.failed(
                    IdentityAttemptFailureKind.UNAVAILABLE
                )
            query = None
            if type(attempt) is not IdentityAttemptResult:
                attempt = None
                raise IdentityReportError(
                    "identity provider returned a malformed result"
                )
            published_at = context.observed_at
            if context.cancelled or (
                attempt.failure is not None
                and attempt.failure.kind is IdentityAttemptFailureKind.CANCELLED
            ):
                return self._cancel_claim(claim, request.owner, published_at), None
            return self._publish_attempt(
                request, claim, attempt, published_at
            )
        except StaleIdentityWorkFence:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS), None
        except IdentityWorkPersistenceError:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE), None

    def _publish_attempt(
        self,
        request: HardcoverIdentityExecutionRequest,
        claim: IdentityWorkClaim,
        attempt: IdentityAttemptResult,
        now: datetime,
    ) -> tuple[HardcoverIdentityReport | None, datetime | None]:
        if attempt.failure is None:
            decision = decide_book_identity(request.query, attempt.observations)
            if decision.outcome is IdentityDecisionOutcome.MATCHED:
                status = self._work.publish_matched(
                    claim,
                    request.owner,
                    now,
                    IdentityWorkObservation(
                        IdentityMatchOutcome.MATCHED, decision.rows_validated
                    ),
                    gate_owned=True,
                )
                outcome = (
                    HardcoverIdentityOutcome.MATCHED
                    if status.outcome is IdentityWorkOutcome.MATCHED
                    else HardcoverIdentityOutcome.PERSISTENCE_FAILURE
                )
                return _report(outcome), None
            terminal = {
                IdentityDecisionOutcome.NO_MATCH: HardcoverIdentityOutcome.NO_MATCH,
                IdentityDecisionOutcome.AMBIGUOUS: HardcoverIdentityOutcome.AMBIGUOUS,
                IdentityDecisionOutcome.INCOMPATIBLE: HardcoverIdentityOutcome.INCOMPATIBLE,
                IdentityDecisionOutcome.MALFORMED: HardcoverIdentityOutcome.MALFORMED,
            }[decision.outcome]
            self._work.publish_failed(
                claim,
                request.owner,
                now,
                retryable=False,
                gate_owned=True,
            )
            return _report(terminal), None

        failure = attempt.failure
        retryable = failure.kind in {
            IdentityAttemptFailureKind.RATE_LIMITED,
            IdentityAttemptFailureKind.TIMEOUT,
            IdentityAttemptFailureKind.UNAVAILABLE,
        }
        if retryable:
            retryable = not request.one_shot and claim.candidate < MAX_STARTS
            fixed = claim.candidate
            advised = failure.retry_after_seconds or 0
            retry_at = now + timedelta(seconds=max(fixed, advised))
            status = self._work.publish_failed(
                claim,
                request.owner,
                now,
                retryable=retryable,
                retry_at=retry_at if retryable else None,
                gate_owned=True,
            )
            if status.outcome is IdentityWorkOutcome.IN_PROGRESS:
                return None, retry_at
            return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED), None

        terminal = {
            IdentityAttemptFailureKind.AUTHENTICATION: HardcoverIdentityOutcome.AUTHENTICATION,
            IdentityAttemptFailureKind.AUTHORIZATION: HardcoverIdentityOutcome.AUTHORIZATION,
            IdentityAttemptFailureKind.MALFORMED: HardcoverIdentityOutcome.MALFORMED,
            IdentityAttemptFailureKind.OVERSIZED: HardcoverIdentityOutcome.OVERSIZED,
            IdentityAttemptFailureKind.EMPTY: HardcoverIdentityOutcome.EMPTY,
            IdentityAttemptFailureKind.INCOMPATIBLE: HardcoverIdentityOutcome.INCOMPATIBLE,
        }.get(failure.kind, HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        self._work.publish_failed(
            claim,
            request.owner,
            now,
            retryable=False,
            gate_owned=True,
        )
        return _report(terminal), None

    def _cancel_waiting(
        self, active: IdentityWorkInspection, now: datetime
    ) -> HardcoverIdentityReport:
        try:
            self._work.finish_waiting(active.reference, now, cancelled=True)
        except StaleIdentityWorkFence:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        except IdentityWorkPersistenceError:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        return _report(HardcoverIdentityOutcome.CANCELLED)

    def _finish_waiting(
        self, active: IdentityWorkInspection, now: datetime
    ) -> HardcoverIdentityReport:
        try:
            self._work.finish_waiting(active.reference, now, cancelled=False)
        except StaleIdentityWorkFence:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        except IdentityWorkPersistenceError:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)

    def _cancel_claim(
        self, claim: IdentityWorkClaim | None, owner: str, now: datetime
    ) -> HardcoverIdentityReport:
        if claim is None:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        try:
            self._work.cancel(claim, owner, now, gate_owned=True)
        except StaleIdentityWorkFence:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        except IdentityWorkPersistenceError:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        return _report(HardcoverIdentityOutcome.CANCELLED)


def _report(outcome: HardcoverIdentityOutcome) -> HardcoverIdentityReport:
    return HardcoverIdentityReport(outcome)


__all__ = [
    "HardcoverIdentityExecutionRequest",
    "IdentityDeploymentGate",
    "IdentityDeploymentGateLease",
    "IdentityWaiter",
    "MAX_STARTS",
    "MINIMUM_START_WINDOW",
    "PUBLICATION_RESERVE",
    "RunSafeHardcoverIdentity",
    "TRANSPORT_WINDOW",
]
