"""Restart-safe orchestration around the bounded Hardcover identity attempt."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from mechanical_librarian.application.hardcover_identity import (
    HardcoverIdentityOutcome,
    HardcoverIdentityReport,
    IdentityWorkPersistenceError,
    ManageHardcoverIdentityWork,
)
from mechanical_librarian.application.hardcover_safe_executor import (
    HardcoverIdentityExecutionRequest,
    IdentityDeploymentGate,
    IdentityDeploymentGateLease,
    RunSafeHardcoverIdentity,
)
from mechanical_librarian.application.ports import OperationContext
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityWorkOutcome,
    IdentityWorkReadiness,
    IdentityWorkState,
)


class RetryWait(Protocol):
    """Interruptible foreground wait; threading.Event implements this seam."""

    def wait(self, timeout: float) -> bool: ...


class RunHardcoverIdentityLifecycle:
    """Hide durable retry, recovery, deadline, and cancellation coordination."""

    def __init__(
        self,
        work: ManageHardcoverIdentityWork,
        attempt: RunSafeHardcoverIdentity,
        gate: IdentityDeploymentGate,
        retry_wait: RetryWait,
    ) -> None:
        if type(work) is not ManageHardcoverIdentityWork:
            raise TypeError("Hardcover identity work manager is malformed")
        if type(attempt) is not RunSafeHardcoverIdentity:
            raise TypeError("Hardcover identity attempt executor is malformed")
        if not callable(getattr(gate, "try_acquire", None)):
            raise TypeError("Hardcover deployment gate is malformed")
        if not callable(getattr(retry_wait, "wait", None)):
            raise TypeError("Hardcover retry wait is malformed")
        self._work = work
        self._attempt = attempt
        self._gate = gate
        self._retry_wait = retry_wait

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

        wait_for_retry = False
        for _ in range(8):
            now = context.observed_at
            try:
                accepted = self._work.accept(request.work_key, now)
                if accepted.outcome is IdentityWorkOutcome.MATCHED:
                    return _report(HardcoverIdentityOutcome.MATCHED)
                if accepted.outcome is IdentityWorkOutcome.PERSISTENCE_FAILURE:
                    return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
                if accepted.outcome is IdentityWorkOutcome.RETRY_EXHAUSTED:
                    return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
                if (
                    accepted.outcome is IdentityWorkOutcome.STALE
                    and accepted.candidate is None
                ):
                    accepted = self._work.accept(request.work_key, now)
                active = self._work.inspect(accepted.reference, now)
            except (IdentityWorkPersistenceError, TypeError, ValueError):
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            except Exception:
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)

            terminal = _terminal_report(active)
            if terminal is not None:
                return terminal
            if now >= active.aggregate_deadline or context.deadline.expired_at(now):
                return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)

            if active.state is IdentityWorkState.CLAIMED:
                recovered = self._recover_claim(active, context)
                if recovered is not None:
                    return recovered
                wait_for_retry = True
                continue

            if context.cancelled:
                try:
                    self._work.cancel_available(active.reference, now)
                except IdentityWorkPersistenceError:
                    return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
                return _report(HardcoverIdentityOutcome.CANCELLED)

            if active.state is IdentityWorkState.RETRY_WAIT:
                due = active.retry_due_at
                if due is None:
                    return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
                if now < due:
                    if not wait_for_retry:
                        return _report(HardcoverIdentityOutcome.IN_PROGRESS)
                    waited = self._wait_until(due, now, context)
                    if waited is not None:
                        return waited
                    wait_for_retry = False
                    continue

            report = self._attempt.run(request, context)
            if type(report) is not HardcoverIdentityReport:
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            if report.outcome is not HardcoverIdentityOutcome.IN_PROGRESS:
                return report
            try:
                refreshed = self._work.inspect(
                    active.reference, context.observed_at
                )
            except (IdentityWorkPersistenceError, TypeError, ValueError):
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            except Exception:
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            if (
                refreshed.state is IdentityWorkState.RETRY_WAIT
                and refreshed.retry_due_at is not None
                and context.observed_at < refreshed.retry_due_at
            ):
                wait_for_retry = True
                continue
            return report

        return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)

    def _wait_until(
        self,
        due: datetime,
        before: datetime,
        context: OperationContext,
    ) -> HardcoverIdentityReport | None:
        try:
            interrupted = self._retry_wait.wait((due - before).total_seconds())
            if type(interrupted) is not bool:
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            after = context.observed_at
        except (TypeError, ValueError):
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        except Exception:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        if after < before:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        if context.cancelled:
            return None
        if context.deadline.expired_at(after):
            return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
        if after < due:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        return None

    def _recover_claim(
        self,
        active: IdentityWorkReadiness,
        context: OperationContext,
    ) -> HardcoverIdentityReport | None:
        claim = active.active_claim
        if claim is None:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        now = context.observed_at
        if now <= claim.lease_expires_at:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        try:
            lease = self._gate.try_acquire()
        except Exception:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        if lease is None:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        return self._recover_under_gate(active, context, lease)

    def _recover_under_gate(
        self,
        active: IdentityWorkReadiness,
        context: OperationContext,
        lease: IdentityDeploymentGateLease,
    ) -> HardcoverIdentityReport | None:
        result: HardcoverIdentityReport | None = None
        failure: BaseException | None = None
        try:
            now = context.observed_at
            refreshed = self._work.inspect(active.reference, now)
            if refreshed.state is not IdentityWorkState.CLAIMED:
                return None
            claim = refreshed.active_claim
            if claim is None:
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            if now <= claim.lease_expires_at:
                return _report(HardcoverIdentityOutcome.IN_PROGRESS)
            recovered = self._work.recover(claim, now, gate_owned=True)
            if type(recovered) is not int:
                result = _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
            elif context.cancelled:
                self._work.cancel_available(active.reference, context.observed_at)
                result = _report(HardcoverIdentityOutcome.CANCELLED)
        except (IdentityWorkPersistenceError, TypeError, ValueError):
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
        return result


def _terminal_report(active: IdentityWorkReadiness) -> HardcoverIdentityReport | None:
    if active.state is IdentityWorkState.SUCCEEDED:
        return _report(HardcoverIdentityOutcome.MATCHED)
    if active.state is IdentityWorkState.CANCELLED:
        return _report(HardcoverIdentityOutcome.CANCELLED)
    if active.state is IdentityWorkState.FAILED:
        return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
    return None


def _report(outcome: HardcoverIdentityOutcome) -> HardcoverIdentityReport:
    return HardcoverIdentityReport(outcome)


__all__ = ["RetryWait", "RunHardcoverIdentityLifecycle"]
