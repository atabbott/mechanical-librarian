"""One bounded Hardcover identity attempt under durable send authority."""

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
    IdentityWorkObservation,
    IdentityWorkOutcome,
    IdentityWorkReference,
    IdentityWorkState,
)


TRANSPORT_WINDOW = timedelta(seconds=5)


@dataclass(frozen=True, repr=False, slots=True)
class HardcoverIdentityExecutionRequest:
    """Private query paired with public-safe durable authority values."""

    work_key: str
    owner: str
    query: PrivateIdentityQuery
    egress_limit: int = 3

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
            or type(self.egress_limit) is not int
            or self.egress_limit not in {1, 3}
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


class RunSafeHardcoverIdentity:
    """Order acceptance, gate, durable start, one attempt, and publication."""

    def __init__(
        self,
        work: ManageHardcoverIdentityWork,
        provider: BookIdentityProvider,
        gate: IdentityDeploymentGate,
    ) -> None:
        if type(work) is not ManageHardcoverIdentityWork:
            raise TypeError("Hardcover identity work manager is malformed")
        if not callable(getattr(provider, "lookup", None)):
            raise TypeError("book identity provider is malformed")
        if not callable(getattr(gate, "try_acquire", None)):
            raise TypeError("Hardcover deployment gate is malformed")
        self._work = work
        self._provider = provider
        self._gate = gate

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
        accepted_at = context.observed_at
        try:
            accepted = self._work.accept(request.work_key, accepted_at)
            if accepted.outcome is IdentityWorkOutcome.MATCHED:
                return _report(HardcoverIdentityOutcome.MATCHED)
            if accepted.outcome is IdentityWorkOutcome.PERSISTENCE_FAILURE:
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            if accepted.outcome is IdentityWorkOutcome.RETRY_EXHAUSTED:
                return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
            if accepted.outcome is IdentityWorkOutcome.STALE and accepted.candidate is None:
                accepted = self._work.accept(request.work_key, accepted_at)
            active = self._work.inspect(accepted.reference, accepted_at)
            if active.state not in {
                IdentityWorkState.PENDING,
                IdentityWorkState.RETRY_WAIT,
            }:
                return _report(HardcoverIdentityOutcome.IN_PROGRESS)
            if (
                active.state is IdentityWorkState.RETRY_WAIT
                and active.retry_due_at is not None
                and accepted_at < active.retry_due_at
            ):
                return _report(HardcoverIdentityOutcome.IN_PROGRESS)
            if context.cancelled:
                self._work.cancel_available(accepted.reference, accepted_at)
                return _report(HardcoverIdentityOutcome.CANCELLED)
        except IdentityWorkPersistenceError:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)

        aggregate_deadline = active.aggregate_deadline
        now = context.observed_at
        if now >= aggregate_deadline or context.deadline.expired_at(now):
            return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        try:
            lease = self._gate.try_acquire()
        except Exception:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        if lease is None:
            if context.cancelled:
                try:
                    self._work.cancel_available(accepted.reference, context.observed_at)
                except IdentityWorkPersistenceError:
                    return _report(HardcoverIdentityOutcome.IN_PROGRESS)
                return _report(HardcoverIdentityOutcome.CANCELLED)
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)

        result: HardcoverIdentityReport | None = None
        failure: BaseException | None = None
        try:
            result = self._run_under_gate(
                request,
                context,
                accepted.reference,
                aggregate_deadline,
                lease,
            )
        except (IdentityWorkPersistenceError, IdentityReportError, ValueError, TypeError):
            result = _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        except Exception:
            result = _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        except BaseException as error:
            failure = error
            raise
        finally:
            try:
                lease.release()
            except Exception:
                if failure is not None:
                    failure.add_note("Hardcover deployment gate release failed")
                else:
                    result = _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        if result is None:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        return result

    def _run_under_gate(
        self,
        request: HardcoverIdentityExecutionRequest,
        context: OperationContext,
        reference: IdentityWorkReference,
        aggregate_deadline: datetime,
        lease: IdentityDeploymentGateLease,
    ) -> HardcoverIdentityReport:
        now = context.observed_at
        active = self._work.inspect(reference, now)
        if active.state is IdentityWorkState.CLAIMED:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        if (
            active.state is IdentityWorkState.RETRY_WAIT
            and active.retry_due_at is not None
            and now < active.retry_due_at
        ):
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        if active.state is IdentityWorkState.SUCCEEDED:
            return _report(HardcoverIdentityOutcome.MATCHED)
        if active.state in {
            IdentityWorkState.FAILED,
            IdentityWorkState.CANCELLED,
        }:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        aggregate_deadline = active.aggregate_deadline
        if now >= aggregate_deadline or context.deadline.expired_at(now):
            return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        if context.cancelled:
            self._work.cancel_available(reference, now)
            return _report(HardcoverIdentityOutcome.CANCELLED)
        claim = self._work.claim(
            reference, request.owner, now, gate_owned=True
        )
        started_at = context.observed_at
        if context.cancelled:
            self._work.cancel(
                claim,
                request.owner,
                started_at,
                gate_owned=True,
            )
            return _report(HardcoverIdentityOutcome.CANCELLED)
        if context.deadline.expired_at(started_at):
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        started = self._work.start(
            claim,
            request.owner,
            started_at,
            gate_owned=True,
            egress_limit=request.egress_limit,
        )
        if type(started) is not IdentityWorkClaim:
            return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        if context.cancelled:
            self._work.cancel(
                started,
                request.owner,
                context.observed_at,
                gate_owned=True,
            )
            return _report(HardcoverIdentityOutcome.CANCELLED)
        lease.validate_for_send()

        attempt_deadline = min(
            context.deadline.at,
            started_at + TRANSPORT_WINDOW,
        )
        attempt_context = OperationContext(
            Deadline(attempt_deadline),
            context.cancellation,
            context.resources,
            context.time,
        )
        query = IsbnIdentityQuery.from_private(request.query)
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
        if context.cancelled:
            attempt = IdentityAttemptResult.failed(IdentityAttemptFailureKind.CANCELLED)
        elif published_at >= attempt_deadline:
            attempt = IdentityAttemptResult.failed(IdentityAttemptFailureKind.TIMEOUT)
        return self._publish_attempt(request, started, attempt, published_at)

    def _publish_attempt(
        self,
        request: HardcoverIdentityExecutionRequest,
        claim: IdentityWorkClaim,
        attempt: IdentityAttemptResult,
        now: datetime,
    ) -> HardcoverIdentityReport:
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
                return _report(
                    HardcoverIdentityOutcome.MATCHED
                    if status.outcome is IdentityWorkOutcome.MATCHED
                    else HardcoverIdentityOutcome.PERSISTENCE_FAILURE
                )
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
            return _report(terminal)

        failure = attempt.failure.kind
        if failure in {
            IdentityAttemptFailureKind.RATE_LIMITED,
            IdentityAttemptFailureKind.TIMEOUT,
            IdentityAttemptFailureKind.UNAVAILABLE,
        }:
            retry_after = attempt.failure.retry_after_seconds
            retry_delay = max(
                1 if claim.candidate == 1 else 2,
                0 if retry_after is None else retry_after,
            )
            status = self._work.publish_failed(
                claim,
                request.owner,
                now,
                retryable=True,
                retry_delay_seconds=retry_delay,
                gate_owned=True,
            )
            outcome = {
                IdentityWorkOutcome.IN_PROGRESS: HardcoverIdentityOutcome.IN_PROGRESS,
                IdentityWorkOutcome.RETRY_EXHAUSTED: (
                    HardcoverIdentityOutcome.RETRY_EXHAUSTED
                ),
                IdentityWorkOutcome.PERSISTENCE_FAILURE: (
                    HardcoverIdentityOutcome.PERSISTENCE_FAILURE
                ),
            }.get(status.outcome, HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            return _report(outcome)

        if failure is IdentityAttemptFailureKind.INVALID_RETRY_AFTER:
            status = self._work.publish_failed(
                claim,
                request.owner,
                now,
                retryable=True,
                retry_delay_seconds=None,
                gate_owned=True,
            )
            return _report(
                HardcoverIdentityOutcome.RETRY_EXHAUSTED
                if status.outcome is IdentityWorkOutcome.RETRY_EXHAUSTED
                else HardcoverIdentityOutcome.PERSISTENCE_FAILURE
            )

        if failure is IdentityAttemptFailureKind.CANCELLED:
            self._work.cancel(
                claim,
                request.owner,
                now,
                gate_owned=True,
            )
            return _report(HardcoverIdentityOutcome.CANCELLED)

        terminal = {
            IdentityAttemptFailureKind.AUTHENTICATION: HardcoverIdentityOutcome.AUTHENTICATION,
            IdentityAttemptFailureKind.AUTHORIZATION: HardcoverIdentityOutcome.AUTHORIZATION,
            IdentityAttemptFailureKind.MALFORMED: HardcoverIdentityOutcome.MALFORMED,
            IdentityAttemptFailureKind.OVERSIZED: HardcoverIdentityOutcome.OVERSIZED,
            IdentityAttemptFailureKind.EMPTY: HardcoverIdentityOutcome.EMPTY,
            IdentityAttemptFailureKind.INCOMPATIBLE: HardcoverIdentityOutcome.INCOMPATIBLE,
        }.get(failure, HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        self._work.publish_failed(
            claim,
            request.owner,
            now,
            retryable=False,
            gate_owned=True,
        )
        return _report(terminal)


def _report(outcome: HardcoverIdentityOutcome) -> HardcoverIdentityReport:
    return HardcoverIdentityReport(outcome)


__all__ = [
    "HardcoverIdentityExecutionRequest",
    "IdentityDeploymentGate",
    "IdentityDeploymentGateLease",
    "RunSafeHardcoverIdentity",
    "TRANSPORT_WINDOW",
]
