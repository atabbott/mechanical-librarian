"""One bounded Hardcover identity attempt under durable send authority."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from mechanical_librarian.application.hardcover_identity import (
    BookIdentityProvider,
    HardcoverIdentityOutcome,
    HardcoverIdentityReport,
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    IdentityExchangeGuardError,
    IdentityReportError,
    IdentityWorkPersistenceError,
    IsbnIdentityQuery,
    ManageHardcoverIdentityWork,
)
from mechanical_librarian.application.ports import Deadline, OperationContext
from mechanical_librarian.domain.hardcover_identity import (
    IdentityDecisionOutcome,
    PrivateIdentityQuery,
    compare_identity_titles,
    decide_book_identity,
)
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityMatchOutcome,
    IdentityWorkClaim,
    IdentityWorkObservation,
    IdentityWorkOutcome,
    IdentityWorkReference,
    IdentityWorkRetryToken,
    IdentityWorkState,
    IdentityWorkStatus,
)
from mechanical_librarian.domain.identifiers import canonical_json


TRANSPORT_WINDOW = timedelta(seconds=5)
_WORK_KEY_CONTRACT = {
    "auth-contract-sha256": (
        "404a55fb404e9dc0ddfbe3ef7fd416862ee99627d10eeeeeb7d2402f39042b7b"
    ),
    "compatibility-contract-sha256": (
        "1a824e7610c4e46ed096131f077275f3ec152ab8fbb0ab8e0fca492da2c6d6dd"
    ),
    "config-contract-sha256": (
        "5d21d05239ba3d83e03d06a9e0ac1949adee5e0333b9311fc3c7fef67194db89"
    ),
    "deployment-gate-sha256": (
        "bd57e213f41d711d8ac5f0a5fa71a629e87a0496bc0c90aa9781d14acfaabb31"
    ),
    "identity-policy-version": "hardcover-identity-decision-v1",
    "lifecycle-contract-sha256": (
        "30f1aea2880ddf0bc81a2ad937076b996efe46248d9bdb97ac5dfd06d7a9474e"
    ),
    "operation-contract-sha256": (
        "218aedb44bab603697bdb51dfab9ede9f6530a6031af6d992f9f8b10b99bf2ce"
    ),
    "parser-limits-sha256": (
        "08d70bdc87eeac7b1c716ab160a40305af47b600c22fc91e4cf173353e7a0011"
    ),
    "private-query-schema-sha256": (
        "05637dcf584f527468f96c59244bf371b0b8ab09379accf54b78af124cd2f765"
    ),
    "provider-contract-version": "hardcover-isbn-identity-v1",
    "query-file-contract-sha256": (
        "7a04acca26307529d5e9caeaa5b66f438214046d5418614f89b2e78595cfcd6c"
    ),
    "query-sha256": (
        "309af66157939f2d817680cd8f10b58d27c58f3b3a4f3da7b6b0ee66c61d706b"
    ),
    "status-map-sha256": (
        "8b1add0470b53fa57b17cd43750351f0788a5607fc53cc45d9fd0d05636d4971"
    ),
    "unicode-comparison-version": "unicode-title-comparison-v1",
    "upstream-commit": "2b11c689e4bd5b91e8039ffbb6d4285248569ff3",
    "upstream-schema-sha256": (
        "a6a23ab56873e54946f265440a7e4b7199757b8116534793e2b141b7e3829dca"
    ),
}


def hardcover_identity_work_key(
    query: PrivateIdentityQuery, authority_revision: int
) -> str:
    """Derive the private-value-free durable identity for one ISBN authority."""

    if (
        type(query) is not PrivateIdentityQuery
        or type(authority_revision) is not int
        or not 1 <= authority_revision <= 2_147_483_647
    ):
        raise ValueError("Hardcover identity work-key input is malformed")
    source = {
        **_WORK_KEY_CONTRACT,
        "authority-revision": authority_revision,
        "canonical-isbn13": query.canonical_isbn,
    }
    encoded = canonical_json(source).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


@dataclass(frozen=True, slots=True)
class HardcoverIdentityInvocationResult:
    """Public report plus retry authority owned by this invocation."""

    report: HardcoverIdentityReport
    retry_token: IdentityWorkRetryToken | None = None

    def __post_init__(self) -> None:
        if (
            type(self.report) is not HardcoverIdentityReport
            or (
                self.retry_token is not None
                and type(self.retry_token) is not IdentityWorkRetryToken
            )
            or (
                self.retry_token is not None
                and self.report.outcome is not HardcoverIdentityOutcome.IN_PROGRESS
            )
        ):
            raise ValueError("Hardcover identity invocation result is malformed")


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
        if not callable(getattr(provider, "prepare", None)):
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
        return self.run_invocation(request, context).report

    def run_invocation(
        self,
        request: HardcoverIdentityExecutionRequest,
        context: OperationContext,
        *,
        accepted: IdentityWorkStatus | None = None,
    ) -> HardcoverIdentityInvocationResult:
        if (
            type(request) is not HardcoverIdentityExecutionRequest
            or type(context) is not OperationContext
            or (
                accepted is not None
                and (
                    type(accepted) is not IdentityWorkStatus
                    or accepted.reference.work_key != request.work_key
                )
            )
        ):
            raise TypeError("Hardcover identity execution input is malformed")
        accepted_at = context.observed_at
        try:
            if accepted is None:
                accepted = self._work.accept(
                    request.work_key,
                    accepted_at,
                    egress_limit=request.egress_limit,
                )
            if accepted.outcome is IdentityWorkOutcome.MATCHED:
                return _matched_report(request.query, accepted.observation)
            if accepted.outcome is IdentityWorkOutcome.PERSISTENCE_FAILURE:
                return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
            if accepted.outcome is IdentityWorkOutcome.RETRY_EXHAUSTED:
                return _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
            if (
                accepted.outcome is IdentityWorkOutcome.STALE
                and accepted.candidate is None
            ):
                return _report(HardcoverIdentityOutcome.STALE)
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

        result: HardcoverIdentityInvocationResult | None = None
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
    ) -> HardcoverIdentityInvocationResult:
        now = context.observed_at
        active = self._work.inspect(reference, now)
        if active.state is IdentityWorkState.CLAIMED:
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        if active.state is IdentityWorkState.SUCCEEDED:
            return _matched_report(request.query, active.observation)
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
        if (
            active.state is IdentityWorkState.RETRY_WAIT
            and active.retry_due_at is not None
            and now < active.retry_due_at
        ):
            return _report(HardcoverIdentityOutcome.IN_PROGRESS)
        query = IsbnIdentityQuery.from_private(request.query)
        preparation_context = OperationContext(
            Deadline(min(context.deadline.at, aggregate_deadline)),
            context.cancellation,
            context.resources,
            context.time,
        )
        try:
            prepared = self._provider.prepare(query, preparation_context)
        except Exception:
            prepared = IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.UNAVAILABLE
            )
        if type(prepared) is IdentityAttemptResult:
            query = None
            return self._preparation_result(reference, prepared, context)
        exchange = getattr(prepared, "exchange", None)
        if not callable(exchange):
            raise IdentityReportError(
                "identity provider returned a malformed preparation"
            )

        started: IdentityWorkClaim | None = None
        attempt_deadline: datetime | None = None
        callback_used = False
        rejected: HardcoverIdentityInvocationResult | None = None

        def begin_transport() -> OperationContext:
            nonlocal started, attempt_deadline, callback_used, rejected
            if callback_used:
                raise IdentityExchangeGuardError(
                    "Hardcover exchange authority is unavailable"
                )
            callback_used = True
            started_at = context.observed_at
            if (
                context.cancelled
                or started_at >= aggregate_deadline
                or context.deadline.expired_at(started_at)
            ):
                if context.cancelled:
                    self._work.cancel_available(reference, started_at)
                    rejected = _report(HardcoverIdentityOutcome.CANCELLED)
                else:
                    rejected = _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
                raise IdentityExchangeGuardError(
                    "Hardcover exchange authority is unavailable"
                )
            try:
                lease.validate_for_send()
            except Exception:
                raise IdentityExchangeGuardError(
                    "Hardcover exchange authority is unavailable"
                ) from None
            claim = self._work.claim(
                reference, request.owner, started_at, gate_owned=True
            )
            started_at = context.observed_at
            if (
                context.cancelled
                or started_at >= aggregate_deadline
                or context.deadline.expired_at(started_at)
            ):
                if context.cancelled:
                    self._work.cancel(
                        claim,
                        request.owner,
                        started_at,
                        gate_owned=True,
                    )
                    rejected = _report(HardcoverIdentityOutcome.CANCELLED)
                else:
                    rejected = _report(HardcoverIdentityOutcome.IN_PROGRESS)
                raise IdentityExchangeGuardError(
                    "Hardcover exchange authority is unavailable"
                )
            started_result = self._work.start(
                claim,
                request.owner,
                started_at,
                gate_owned=True,
                egress_limit=request.egress_limit,
            )
            if type(started_result) is not IdentityWorkClaim:
                rejected = _report(HardcoverIdentityOutcome.RETRY_EXHAUSTED)
                raise IdentityExchangeGuardError(
                    "Hardcover exchange authority is unavailable"
                )
            try:
                lease.validate_for_send()
            except Exception:
                raise IdentityExchangeGuardError(
                    "Hardcover exchange authority is unavailable"
                ) from None
            started = started_result
            attempt_deadline = min(
                context.deadline.at,
                aggregate_deadline,
                started_at + TRANSPORT_WINDOW,
            )
            return OperationContext(
                Deadline(attempt_deadline),
                context.cancellation,
                context.resources,
                context.time,
            )

        try:
            attempt = exchange(
                preparation_context,
                begin_transport=begin_transport,
            )
        except IdentityExchangeGuardError:
            query = None
            return rejected or _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        except Exception:
            attempt = IdentityAttemptResult.failed(
                IdentityAttemptFailureKind.UNAVAILABLE
            )
        query = None
        if started is None or attempt_deadline is None:
            if type(attempt) is IdentityAttemptResult:
                return self._preparation_result(reference, attempt, context)
            raise IdentityReportError("identity provider did not start transport")
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
        lease.validate_for_send()
        return self._publish_attempt(request, started, attempt, published_at)

    def _preparation_result(
        self,
        reference: IdentityWorkReference,
        attempt: IdentityAttemptResult,
        context: OperationContext,
    ) -> HardcoverIdentityInvocationResult:
        if attempt.failure is None:
            return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
        failure = attempt.failure.kind
        if failure is IdentityAttemptFailureKind.CANCELLED or context.cancelled:
            self._work.cancel_available(reference, context.observed_at)
            return _report(HardcoverIdentityOutcome.CANCELLED)
        outcome = {
            IdentityAttemptFailureKind.CREDENTIAL_UNAVAILABLE: (
                HardcoverIdentityOutcome.CREDENTIAL_UNAVAILABLE
            ),
            IdentityAttemptFailureKind.MALFORMED: HardcoverIdentityOutcome.MALFORMED,
            IdentityAttemptFailureKind.OVERSIZED: HardcoverIdentityOutcome.OVERSIZED,
            IdentityAttemptFailureKind.EMPTY: HardcoverIdentityOutcome.EMPTY,
            IdentityAttemptFailureKind.INCOMPATIBLE: HardcoverIdentityOutcome.INCOMPATIBLE,
        }.get(failure, HardcoverIdentityOutcome.IN_PROGRESS)
        return _report(outcome)

    def _publish_attempt(
        self,
        request: HardcoverIdentityExecutionRequest,
        claim: IdentityWorkClaim,
        attempt: IdentityAttemptResult,
        now: datetime,
    ) -> HardcoverIdentityInvocationResult:
        if attempt.failure is None:
            decision = decide_book_identity(request.query, attempt.observations)
            if decision.outcome is IdentityDecisionOutcome.MATCHED:
                status = self._work.publish_matched(
                    claim,
                    request.owner,
                    now,
                    IdentityWorkObservation(
                        IdentityMatchOutcome.MATCHED,
                        decision.rows_validated,
                        decision.identity.edition_id,
                        decision.identity.book_id,
                        decision.identity.provider_title,
                    ),
                    gate_owned=True,
                )
                if status.outcome is not IdentityWorkOutcome.MATCHED:
                    return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
                return _matched_report(request.query, status.observation)
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
            retry_token = None
            if status.outcome is IdentityWorkOutcome.IN_PROGRESS:
                if status.candidate is None:
                    raise IdentityReportError(
                        "identity retry publication returned no candidate"
                    )
                retry_token = IdentityWorkRetryToken(
                    claim.reference,
                    status.candidate,
                    claim.fence + 1,
                    now + timedelta(seconds=retry_delay),
                )
            return _report(
                outcome,
                retry_token=retry_token,
            )

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
            IdentityAttemptFailureKind.CREDENTIAL_UNAVAILABLE: (
                HardcoverIdentityOutcome.CREDENTIAL_UNAVAILABLE
            ),
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


def _report(
    outcome: HardcoverIdentityOutcome,
    *,
    retry_token: IdentityWorkRetryToken | None = None,
) -> HardcoverIdentityInvocationResult:
    return HardcoverIdentityInvocationResult(
        HardcoverIdentityReport(outcome),
        retry_token,
    )


def _matched_report(
    query: PrivateIdentityQuery,
    observation: IdentityWorkObservation | None,
) -> HardcoverIdentityInvocationResult:
    if (
        type(query) is not PrivateIdentityQuery
        or type(observation) is not IdentityWorkObservation
        or not observation.reusable
        or observation.edition_id is None
        or observation.book_id is None
        or observation.provider_title is None
    ):
        return _report(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
    return HardcoverIdentityInvocationResult(
        HardcoverIdentityReport(
            HardcoverIdentityOutcome.MATCHED,
            observation.edition_id,
            observation.book_id,
            compare_identity_titles(query.title, observation.provider_title),
        )
    )


__all__ = [
    "HardcoverIdentityExecutionRequest",
    "HardcoverIdentityInvocationResult",
    "IdentityDeploymentGate",
    "IdentityDeploymentGateLease",
    "RunSafeHardcoverIdentity",
    "TRANSPORT_WINDOW",
    "hardcover_identity_work_key",
]
