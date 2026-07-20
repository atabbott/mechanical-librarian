"""Inert, proposal-free Hardcover identity reporting."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from mechanical_librarian.application.hardcover_safe_executor import (
        HardcoverIdentityExecutionRequest,
    )

from mechanical_librarian.application.configuration import (
    HardcoverProviderConfiguration,
)
from mechanical_librarian.application.ports import OperationContext
from mechanical_librarian.domain.hardcover_identity import (
    IdentityDecisionOutcome,
    PrivateIdentityQuery,
    ProviderIdentityObservation,
    TitleComparison,
    decide_book_identity,
    isbn_identity,
    validate_private_query,
)
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityMatchOutcome,
    IdentityWorkClaim,
    IdentityWorkReadiness,
    IdentityWorkObservation,
    IdentityWorkOutcome,
    IdentityWorkReference,
    IdentityWorkStatus,
)


MAX_IDENTITY_OBSERVATIONS = 4


class HardcoverIdentityOutcome(StrEnum):
    DISABLED = "disabled"
    INVALID_INPUT = "invalid_input"
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    MATCHED = "matched"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    EMPTY = "empty"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    INCOMPATIBLE = "incompatible"
    STALE = "stale"
    CANCELLED = "cancelled"
    IN_PROGRESS = "in_progress"
    PERSISTENCE_FAILURE = "persistence_failure"
    RETRY_EXHAUSTED = "retry_exhausted"


class IdentityAttemptFailureKind(StrEnum):
    CREDENTIAL_UNAVAILABLE = "credential_unavailable"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    INVALID_RETRY_AFTER = "invalid_retry_after"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    EMPTY = "empty"
    INCOMPATIBLE = "incompatible"


class IdentityReportError(RuntimeError):
    """A value-free failure while running one identity attempt."""

    def __init__(
        self,
        message: str,
        kind: IdentityAttemptFailureKind | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, repr=False, slots=True)
class IsbnIdentityQuery:
    canonical_isbn_13: str
    equivalent_isbn_10: str | None

    def __init__(
        self,
        canonical_isbn_13: str,
        equivalent_isbn_10: str | None,
    ) -> None:
        parsed = _isbn_identity_or_none(canonical_isbn_13)
        valid = (
            parsed is not None
            and parsed.canonical_isbn_13 == canonical_isbn_13
            and parsed.equivalent_isbn_10 == equivalent_isbn_10
        )
        if not valid:
            canonical_isbn_13 = ""
            equivalent_isbn_10 = None
            parsed = None
            raise ValueError("ISBN identity query is malformed")
        object.__setattr__(self, "canonical_isbn_13", parsed.canonical_isbn_13)
        object.__setattr__(self, "equivalent_isbn_10", parsed.equivalent_isbn_10)

    @classmethod
    def from_private(cls, query: PrivateIdentityQuery) -> IsbnIdentityQuery:
        if type(query) is not PrivateIdentityQuery:
            query = None
            raise TypeError("private identity query is malformed")
        parsed = _isbn_identity_or_none(query.canonical_isbn)
        if parsed is None:
            query = None
            raise ValueError("private identity query is malformed")
        return cls(parsed.canonical_isbn_13, parsed.equivalent_isbn_10)

    def __repr__(self) -> str:
        return "IsbnIdentityQuery(<redacted>)"


def _isbn_identity_or_none(value: object):
    try:
        return isbn_identity(value)
    except Exception:
        return None


@dataclass(frozen=True, slots=True)
class IdentityAttemptFailure:
    kind: IdentityAttemptFailureKind
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not IdentityAttemptFailureKind
            or (
                self.retry_after_seconds is not None
                and (
                    self.kind is not IdentityAttemptFailureKind.RATE_LIMITED
                    or type(self.retry_after_seconds) is not int
                    or not 0 <= self.retry_after_seconds <= 30
                )
            )
        ):
            raise ValueError("identity attempt failure is malformed")


@dataclass(frozen=True, repr=False, slots=True)
class IdentityAttemptResult:
    observations: tuple[ProviderIdentityObservation, ...] | None = None
    failure: IdentityAttemptFailure | None = None

    def __post_init__(self) -> None:
        if (self.observations is None) == (self.failure is None):
            raise ValueError("identity attempt result must contain exactly one result")
        if self.observations is not None:
            if type(self.observations) is not tuple:
                raise ValueError("identity attempt observations are malformed")
            if len(self.observations) > MAX_IDENTITY_OBSERVATIONS:
                raise ValueError("identity attempt observations exceed the resource limit")
            if any(
                type(row) is not ProviderIdentityObservation for row in self.observations
            ):
                raise ValueError("identity attempt observations are malformed")
        if self.failure is not None and type(self.failure) is not IdentityAttemptFailure:
            raise ValueError("identity attempt failure is malformed")

    @classmethod
    def success(
        cls, observations: tuple[ProviderIdentityObservation, ...]
    ) -> IdentityAttemptResult:
        return cls(observations=observations)

    @classmethod
    def failed(
        cls,
        kind: IdentityAttemptFailureKind,
        *,
        retry_after_seconds: int | None = None,
    ) -> IdentityAttemptResult:
        return cls(
            failure=IdentityAttemptFailure(kind, retry_after_seconds)
        )

    def __repr__(self) -> str:
        return "IdentityAttemptResult(<redacted>)"


def allow_identity_send() -> None:
    """Default send guard for already-structural provider calls."""


class BookIdentityProvider(Protocol):
    def lookup(
        self,
        query: IsbnIdentityQuery,
        context: OperationContext,
        *,
        before_exchange: Callable[[], None] = allow_identity_send,
    ) -> IdentityAttemptResult: ...


class FixedAdapterOutcome(StrEnum):
    MATCHED = "matched"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    EMPTY = "empty"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    INCOMPATIBLE = "incompatible"


class AddressPolicyOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class FixedAdapterPreviewReport:
    outcome: FixedAdapterOutcome
    row_count: int
    peer_equal: bool
    actual_network: bool
    address_policy: AddressPolicyOutcome

    def validate(self) -> None:
        types_invalid = (
            type(self.outcome) is not FixedAdapterOutcome
            or type(self.row_count) is not int
            or type(self.peer_equal) is not bool
            or type(self.actual_network) is not bool
            or type(self.address_policy) is not AddressPolicyOutcome
        )
        if types_invalid:
            raise TypeError("Hardcover fixed adapter preview report is malformed")
        rejected = (
            self.address_policy is AddressPolicyOutcome.REJECTED
            and self.outcome is FixedAdapterOutcome.INCOMPATIBLE
            and self.row_count == 0
            and self.peer_equal is False
            and self.actual_network is False
        )
        accepted = (
            self.address_policy is AddressPolicyOutcome.ACCEPTED
            and self.peer_equal is True
            and self.actual_network is False
            and {
                FixedAdapterOutcome.MATCHED: 1 <= self.row_count <= 3,
                FixedAdapterOutcome.AUTHENTICATION: self.row_count == 0,
                FixedAdapterOutcome.AUTHORIZATION: self.row_count == 0,
                FixedAdapterOutcome.MALFORMED: 0 <= self.row_count <= 4,
                FixedAdapterOutcome.OVERSIZED: self.row_count == 0,
                FixedAdapterOutcome.EMPTY: self.row_count == 0,
                FixedAdapterOutcome.NO_MATCH: self.row_count == 0,
                FixedAdapterOutcome.AMBIGUOUS: 2 <= self.row_count <= 4,
                FixedAdapterOutcome.INCOMPATIBLE: 0 <= self.row_count <= 4,
            }[self.outcome]
        )
        if not (rejected or accepted):
            raise TypeError("Hardcover fixed adapter preview report is malformed")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(
            {
                "schema": "hardcover-synthetic-output-v5",
                "scenario": "fixed-adapter-v3",
                "result": {
                    "outcome": self.outcome.value,
                    "row_count": self.row_count,
                    "peer_equal": self.peer_equal,
                    "actual_network": self.actual_network,
                    "address_policy": self.address_policy.value,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class DurableWorkPreviewReport:
    outcome: str
    schema_from: int
    schema_to: int
    migration_rollback_verified: bool
    migration_idempotence_verified: bool
    publication_rollback_verified: bool
    interrupted_recovery_gate_owned: bool

    def validate(self) -> None:
        if (
            self.outcome not in {"matched", "stale", "persistence_failure", "in_progress"}
            or type(self.outcome) is not str
            or type(self.schema_from) is not int
            or self.schema_from != 8
            or type(self.schema_to) is not int
            or self.schema_to != 10
            or self.migration_rollback_verified is not True
            or self.migration_idempotence_verified is not True
            or self.publication_rollback_verified is not True
            or self.interrupted_recovery_gate_owned is not True
        ):
            raise TypeError("Hardcover durable work preview report is malformed")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(
            {
                "schema": "hardcover-synthetic-output-v5",
                "scenario": "durable-work-v3",
                "result": {
                    "outcome": self.outcome,
                    "schema_from": self.schema_from,
                    "schema_to": self.schema_to,
                    "migration_rollback_verified": self.migration_rollback_verified,
                    "migration_idempotence_verified": self.migration_idempotence_verified,
                    "publication_rollback_verified": self.publication_rollback_verified,
                    "interrupted_recovery_gate_owned": self.interrupted_recovery_gate_owned,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class SafeExecutorPreviewReport:
    outcome: str
    cases_executed: int
    attempts_started: int
    gate_order_verified: bool
    deadline_before_gate_verified: bool
    caller_deadline_verified: bool
    late_result_rejected: bool
    closed_mapping_verified: bool
    contributor_labels_private: bool

    def validate(self) -> None:
        if (
            self.outcome != "all_cases_passed"
            or type(self.outcome) is not str
            or type(self.cases_executed) is not int
            or not 1 <= self.cases_executed <= 16
            or type(self.attempts_started) is not int
            or not 0 <= self.attempts_started <= self.cases_executed
            or self.gate_order_verified is not True
            or self.deadline_before_gate_verified is not True
            or self.caller_deadline_verified is not True
            or self.late_result_rejected is not True
            or self.closed_mapping_verified is not True
            or self.contributor_labels_private is not True
        ):
            raise TypeError("Hardcover safe executor preview report is malformed")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(
            {
                "schema": "hardcover-synthetic-output-v5",
                "scenario": "safe-executor-v3",
                "result": {
                    "outcome": self.outcome,
                    "cases_executed": self.cases_executed,
                    "attempts_started": self.attempts_started,
                    "gate_order_verified": self.gate_order_verified,
                    "deadline_before_gate_verified": self.deadline_before_gate_verified,
                    "caller_deadline_verified": self.caller_deadline_verified,
                    "late_result_rejected": self.late_result_rejected,
                    "closed_mapping_verified": self.closed_mapping_verified,
                    "contributor_labels_private": self.contributor_labels_private,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class IdentityLifecyclePreviewReport:
    outcome: str
    cases_executed: int
    attempts_started: int
    retry_schedule_verified: bool
    release_before_wait_verified: bool
    restart_verified: bool
    death_recovery_gate_owned: bool
    cancellation_verified: bool
    signal_handlers_restored: bool
    one_shot_egress_count: int
    fourth_start_forbidden: bool

    def validate(self) -> None:
        if (
            self.outcome != "all_cases_passed"
            or type(self.outcome) is not str
            or type(self.cases_executed) is not int
            or self.cases_executed != 6
            or type(self.attempts_started) is not int
            or not 1 <= self.attempts_started <= 16
            or self.retry_schedule_verified is not True
            or self.release_before_wait_verified is not True
            or self.restart_verified is not True
            or self.death_recovery_gate_owned is not True
            or self.cancellation_verified is not True
            or self.signal_handlers_restored is not True
            or type(self.one_shot_egress_count) is not int
            or self.one_shot_egress_count != 1
            or self.fourth_start_forbidden is not True
        ):
            raise TypeError("Hardcover lifecycle preview report is malformed")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(
            {
                "schema": "hardcover-synthetic-output-v6",
                "scenario": "lifecycle-orchestration-v3",
                "result": {
                    "outcome": self.outcome,
                    "cases_executed": self.cases_executed,
                    "attempts_started": self.attempts_started,
                    "retry_schedule_verified": self.retry_schedule_verified,
                    "release_before_wait_verified": self.release_before_wait_verified,
                    "restart_verified": self.restart_verified,
                    "death_recovery_gate_owned": self.death_recovery_gate_owned,
                    "cancellation_verified": self.cancellation_verified,
                    "signal_handlers_restored": self.signal_handlers_restored,
                    "one_shot_egress_count": self.one_shot_egress_count,
                    "fourth_start_forbidden": self.fourth_start_forbidden,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


def fixed_adapter_preview_report(
    result: IdentityAttemptResult,
    query: IsbnIdentityQuery,
    *,
    peer_equal: bool,
    address_policy: AddressPolicyOutcome,
) -> FixedAdapterPreviewReport:
    """Map one adapter attempt to the fixed public structural preview."""

    if (
        type(result) is not IdentityAttemptResult
        or type(query) is not IsbnIdentityQuery
        or type(peer_equal) is not bool
        or type(address_policy) is not AddressPolicyOutcome
    ):
        raise TypeError("Hardcover fixed adapter preview input is malformed")
    row_count = 0
    if result.failure is not None:
        public = {
            IdentityAttemptFailureKind.AUTHENTICATION: FixedAdapterOutcome.AUTHENTICATION,
            IdentityAttemptFailureKind.AUTHORIZATION: FixedAdapterOutcome.AUTHORIZATION,
            IdentityAttemptFailureKind.MALFORMED: FixedAdapterOutcome.MALFORMED,
            IdentityAttemptFailureKind.OVERSIZED: FixedAdapterOutcome.OVERSIZED,
            IdentityAttemptFailureKind.EMPTY: FixedAdapterOutcome.EMPTY,
            IdentityAttemptFailureKind.INCOMPATIBLE: FixedAdapterOutcome.INCOMPATIBLE,
        }.get(result.failure.kind)
        if public is None:
            raise IdentityReportError(
                "Hardcover fixed adapter attempt is internal-only"
            )
    else:
        observations = result.observations
        if observations is None:
            raise IdentityReportError("Hardcover fixed adapter result is malformed")
        row_count = len(observations)
        private_query = validate_private_query(
            {
                "isbn": query.canonical_isbn_13,
                "title": "Structural Preview",
            }
        )
        public = FixedAdapterOutcome(
            decide_book_identity(private_query, observations).outcome.value
        )
    report = FixedAdapterPreviewReport(
        public,
        row_count,
        peer_equal,
        False,
        address_policy,
    )
    report.validate()
    return report


@dataclass(frozen=True, slots=True)
class FixedAdapterAttemptEvidence:
    result: IdentityAttemptResult
    query: IsbnIdentityQuery
    peer_equal: bool
    address_policy: AddressPolicyOutcome

    def __post_init__(self) -> None:
        if (
            type(self.result) is not IdentityAttemptResult
            or type(self.query) is not IsbnIdentityQuery
            or type(self.peer_equal) is not bool
            or type(self.address_policy) is not AddressPolicyOutcome
        ):
            raise ValueError("Hardcover fixed adapter evidence is malformed")


class FixedAdapterStructuralPreview(Protocol):
    def run(self, source: Mapping[str, object]) -> FixedAdapterAttemptEvidence: ...


class RunHardcoverFixedAdapterPreview:
    """Delegate contributor decoding to the adapter and map only public results."""

    def run(
        self,
        preview: FixedAdapterStructuralPreview,
        source: Mapping[str, object],
    ) -> FixedAdapterPreviewReport:
        if not isinstance(source, Mapping):
            preview = None
            source = None
            raise TypeError("Hardcover structural preview input is malformed")
        invoke = getattr(preview, "run", None)
        if not callable(invoke):
            preview = None
            source = {}
            invoke = None
            raise TypeError("Hardcover structural preview is malformed")
        evidence, failure = _attempt_call(invoke, source)
        if failure is not None:
            preview = None
            source = {}
            invoke = None
            evidence = None
            _raise_attempt_boundary("Hardcover structural preview", failure)
        if type(evidence) is not FixedAdapterAttemptEvidence:
            preview = None
            source = {}
            invoke = None
            evidence = None
            raise IdentityReportError("Hardcover structural preview is malformed")
        report = fixed_adapter_preview_report(
            evidence.result,
            evidence.query,
            peer_equal=evidence.peer_equal,
            address_policy=evidence.address_policy,
        )
        return report


@dataclass(frozen=True)
class HardcoverIdentityReport:
    outcome: HardcoverIdentityOutcome

    def to_json(self) -> str:
        return json.dumps(
            {"schema": "hardcover-identity-report-v5", "outcome": self.outcome.value},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


class HardcoverIdentityExecutor(Protocol):
    """One purpose seam shared by one-attempt and lifecycle execution."""

    def run(
        self,
        request: HardcoverIdentityExecutionRequest,
        context: OperationContext,
    ) -> HardcoverIdentityReport: ...


@dataclass(frozen=True, slots=True)
class InertPreviewReport:
    outcome: HardcoverIdentityOutcome
    private_file_opened: bool = False
    database_opened: bool = False
    secret_opened: bool = False
    network_started: bool = False

    def validate(self) -> None:
        if (
            type(self.outcome) is not HardcoverIdentityOutcome
            or self.outcome
            not in {
                HardcoverIdentityOutcome.DISABLED,
                HardcoverIdentityOutcome.INVALID_INPUT,
            }
            or type(self.private_file_opened) is not bool
            or self.private_file_opened is not False
            or type(self.database_opened) is not bool
            or self.database_opened is not False
            or type(self.secret_opened) is not bool
            or self.secret_opened is not False
            or type(self.network_started) is not bool
            or self.network_started is not False
        ):
            raise TypeError("Hardcover inert preview report is malformed")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(
            {
                "schema": "hardcover-synthetic-output-v5",
                "scenario": "inert-report-v3",
                "result": {
                    "outcome": self.outcome.value,
                    "private_file_opened": self.private_file_opened,
                    "database_opened": self.database_opened,
                    "secret_opened": self.secret_opened,
                    "network_started": self.network_started,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class IdentityDecisionPreviewReport:
    outcome: IdentityDecisionOutcome
    rows_validated: int
    aliases_collapsed: int
    comparison: TitleComparison

    def validate(self) -> None:
        types_and_ranges_valid = (
            type(self.outcome) is IdentityDecisionOutcome
            and type(self.rows_validated) is int
            and 0 <= self.rows_validated <= 4
            and type(self.aliases_collapsed) is int
            and 0 <= self.aliases_collapsed <= 3
            and type(self.comparison) is TitleComparison
        )
        if not types_and_ranges_valid:
            raise TypeError("Hardcover identity decision preview report is malformed")
        outcome_valid = {
            IdentityDecisionOutcome.MATCHED: (
                1 <= self.rows_validated <= 3
                and self.aliases_collapsed == self.rows_validated - 1
                and self.comparison
                in {TitleComparison.EQUAL, TitleComparison.DIFFERENT}
            ),
            IdentityDecisionOutcome.NO_MATCH: (
                self.rows_validated == 0
                and self.aliases_collapsed == 0
                and self.comparison is TitleComparison.NOT_COMPARED
            ),
            IdentityDecisionOutcome.AMBIGUOUS: (
                2 <= self.rows_validated <= 4
                and self.aliases_collapsed == 0
                and self.comparison is TitleComparison.NOT_COMPARED
            ),
            IdentityDecisionOutcome.INCOMPATIBLE: (
                self.aliases_collapsed == 0
                and self.comparison is TitleComparison.NOT_COMPARED
            ),
            IdentityDecisionOutcome.MALFORMED: (
                self.aliases_collapsed == 0
                and self.comparison is TitleComparison.NOT_COMPARED
            ),
        }[self.outcome]
        if not outcome_valid:
            raise TypeError("Hardcover identity decision preview report is malformed")

    def to_json(self) -> str:
        self.validate()
        return json.dumps(
            {
                "schema": "hardcover-synthetic-output-v5",
                "scenario": "identity-decision-v3",
                "result": {
                    "outcome": self.outcome.value,
                    "rows_validated": self.rows_validated,
                    "aliases_collapsed": self.aliases_collapsed,
                    "comparison": self.comparison.value,
                },
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


class RunHardcoverIdentityReport:
    """Own the safe durable lifecycle or run the isolated decision seam."""

    def __init__(self, safe_executor: HardcoverIdentityExecutor | None = None) -> None:
        if safe_executor is not None and not callable(
            getattr(safe_executor, "run", None)
        ):
            raise TypeError("Hardcover safe executor is malformed")
        self._safe_executor = safe_executor

    def run(
        self,
        provider: BookIdentityProvider | object,
        query: PrivateIdentityQuery | OperationContext,
        context: OperationContext | None = None,
        *,
        privacy_observer: Callable[[str, tuple[object, ...]], None] | None = None,
    ) -> IdentityDecisionPreviewReport | HardcoverIdentityReport:
        if context is None:
            from mechanical_librarian.application.hardcover_safe_executor import (
                HardcoverIdentityExecutionRequest,
            )

            if (
                self._safe_executor is None
                or type(provider) is not HardcoverIdentityExecutionRequest
                or type(query) is not OperationContext
                or privacy_observer is not None
            ):
                raise TypeError("Hardcover identity execution input is malformed")
            report = self._safe_executor.run(provider, query)
            if type(report) is not HardcoverIdentityReport:
                raise IdentityReportError(
                    "Hardcover identity execution result is malformed"
                )
            return report
        if type(query) is not PrivateIdentityQuery or type(context) is not OperationContext:
            provider = None
            query = None
            context = None
            privacy_observer = None
            raise TypeError("identity report input is malformed")
        if privacy_observer is not None and not callable(privacy_observer):
            provider = None
            query = None
            context = None
            privacy_observer = None
            raise TypeError("Hardcover privacy observer is malformed")

        lookup = getattr(provider, "lookup", None)
        if not callable(lookup):
            provider = None
            query = None
            context = None
            privacy_observer = None
            lookup = None
            raise TypeError("book identity provider is malformed")
        typed_query, failure = _attempt_call(IsbnIdentityQuery.from_private, query)
        if failure is not None or type(typed_query) is not IsbnIdentityQuery:
            provider = None
            query = None
            context = None
            privacy_observer = None
            lookup = None
            typed_query = None
            _raise_attempt_boundary(
                "identity query",
                failure or IdentityAttemptFailureKind.MALFORMED,
            )
        result, failure = _attempt_call(lookup, typed_query, context)
        typed_query = None
        if failure is not None:
            provider = None
            query = None
            context = None
            privacy_observer = None
            lookup = None
            result = None
            _raise_attempt_boundary("identity provider attempt", failure)
        if type(result) is not IdentityAttemptResult:
            provider = None
            query = None
            context = None
            privacy_observer = None
            lookup = None
            result = None
            raise IdentityReportError("identity provider returned a malformed result")
        if result.failure is not None:
            outcome = _failure_outcome(result.failure.kind)
            if outcome is None:
                failure_kind = result.failure.kind
                provider = None
                query = None
                context = None
                privacy_observer = None
                lookup = None
                result = None
                _raise_no_decision(failure_kind)
            decision_report = IdentityDecisionPreviewReport(
                outcome, 0, 0, TitleComparison.NOT_COMPARED
            )
        else:
            decision = decide_book_identity(query, result.observations)
            decision_report = IdentityDecisionPreviewReport(
                decision.outcome,
                decision.rows_validated,
                decision.aliases_collapsed,
                decision.comparison,
            )
        decision_report.validate()
        if privacy_observer is not None:
            observations = (
                (
                    "traces",
                    ("hardcover.synthetic.identity_decision", "identity-decision-v3"),
                ),
                ("spans", ("lookup-and-decide", decision_report.outcome.value)),
                (
                    "metrics",
                    (
                        "hardcover_identity_preview_total",
                        (("outcome", decision_report.outcome.value),),
                        1,
                        None,
                    ),
                ),
            )
            for observer_channel, observer_values in observations:
                failure = _notify_observer(
                    privacy_observer, observer_channel, observer_values
                )
                if failure is not None:
                    provider = None
                    query = None
                    context = None
                    privacy_observer = None
                    lookup = None
                    result = None
                    observations = ()
                    observer_values = ()
                    _raise_attempt_boundary("Hardcover privacy observer", failure)
        return decision_report


def _failure_outcome(
    kind: IdentityAttemptFailureKind,
) -> IdentityDecisionOutcome | None:
    if kind is IdentityAttemptFailureKind.INCOMPATIBLE:
        return IdentityDecisionOutcome.INCOMPATIBLE
    if kind in {
        IdentityAttemptFailureKind.MALFORMED,
        IdentityAttemptFailureKind.OVERSIZED,
    }:
        return IdentityDecisionOutcome.MALFORMED
    return None


def _attempt_error(
    label: str,
    kind: IdentityAttemptFailureKind,
) -> IdentityReportError:
    suffix = (
        "cancelled"
        if kind is IdentityAttemptFailureKind.CANCELLED
        else "unavailable"
    )
    return IdentityReportError(f"{label} is {suffix}", kind)


def _raise_attempt_boundary(
    label: str,
    kind: IdentityAttemptFailureKind | None,
) -> None:
    if kind is IdentityAttemptFailureKind.MALFORMED or kind is None:
        raise TypeError(f"{label} is malformed")
    raise _attempt_error(label, kind)


def _raise_no_decision(kind: IdentityAttemptFailureKind) -> None:
    raise IdentityReportError(
        "identity provider attempt did not produce a decision", kind
    )


def _attempt_call(
    invoke: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> tuple[object | None, IdentityAttemptFailureKind | None]:
    try:
        return invoke(*args, **kwargs), None
    except Exception:
        return None, IdentityAttemptFailureKind.UNAVAILABLE


def _notify_observer(
    observer: Callable[[str, tuple[object, ...]], None],
    channel: str,
    values: tuple[object, ...],
) -> IdentityAttemptFailureKind | None:
    try:
        observer(channel, values)
    except Exception:
        return IdentityAttemptFailureKind.UNAVAILABLE
    return None


def inert_preview(
    configuration: HardcoverProviderConfiguration,
    query: PrivateIdentityQuery,
    *,
    privacy_observer: Callable[[str, tuple[object, ...]], None] | None = None,
) -> InertPreviewReport:
    """Exercise real validation seams while retaining no private projection."""

    if privacy_observer is not None and not callable(privacy_observer):
        configuration = None
        query = None
        privacy_observer = None
        raise TypeError("Hardcover privacy observer is malformed")
    if (
        type(configuration) is not HardcoverProviderConfiguration
        or configuration.enabled
        or type(query) is not PrivateIdentityQuery
    ):
        report = InertPreviewReport(HardcoverIdentityOutcome.INVALID_INPUT)
    else:
        report = InertPreviewReport(HardcoverIdentityOutcome.DISABLED)
    if privacy_observer is not None:
        observations = (
            (
                "traces",
                ("hardcover.synthetic.inert_preview", "inert-report-v3"),
            ),
            ("spans", ("validate-and-report", report.outcome.value)),
            (
                "metrics",
                (
                    "hardcover_identity_preview_total",
                    (("outcome", report.outcome.value),),
                    1,
                    None,
                ),
            ),
        )
        for observer_channel, observer_values in observations:
            failure = _notify_observer(
                privacy_observer, observer_channel, observer_values
            )
            if failure is not None:
                configuration = None
                query = None
                privacy_observer = None
                observations = ()
                observer_values = ()
                _raise_attempt_boundary("Hardcover privacy observer", failure)
    return report


def render_report_text(report: HardcoverIdentityReport) -> str:
    if (
        type(report) is not HardcoverIdentityReport
        or type(report.outcome) is not HardcoverIdentityOutcome
    ):
        raise TypeError("Hardcover identity report is malformed")
    return (
        "Hardcover identity report\n"
        "schema=hardcover-identity-report-v5\n"
        f"outcome={report.outcome.value}\n"
        "proposal=false write=false\n"
    )


@runtime_checkable
class HardcoverIdentityWorkPersistence(Protocol):
    """Application-owned durable identity-work transaction contract."""

    def accept(
        self,
        work_key: str,
        now: datetime,
        *,
        egress_limit: int = 3,
    ) -> IdentityWorkStatus: ...

    def inspect(
        self, reference: IdentityWorkReference, now: datetime
    ) -> IdentityWorkReadiness: ...

    def claim(
        self,
        reference: IdentityWorkReference,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> IdentityWorkClaim: ...

    def renew_and_start(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
        egress_limit: int = 3,
    ) -> IdentityWorkClaim | IdentityWorkStatus: ...

    def recover_interrupted(
        self, claim: IdentityWorkClaim, now: datetime, *, gate_owned: bool
    ) -> int | IdentityWorkStatus: ...

    def publish_matched(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        observation: IdentityWorkObservation,
        *,
        gate_owned: bool,
        fail_before_commit: bool = False,
    ) -> IdentityWorkStatus: ...

    def publish_failed(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        retryable: bool,
        retry_delay_seconds: int | None = 0,
        gate_owned: bool,
        fail_before_commit: bool = False,
    ) -> IdentityWorkStatus: ...

    def cancel_available(
        self,
        reference: IdentityWorkReference,
        now: datetime,
    ) -> None: ...

    def cancel(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> None: ...


class IdentityWorkPersistenceError(RuntimeError):
    """Value-free application boundary for durable identity-work failures."""


class StaleIdentityWorkFence(IdentityWorkPersistenceError):
    """The caller no longer owns the claimed generation."""


@dataclass(frozen=True, slots=True)
class DurableWorkPreviewCheckpoint:
    accepted: IdentityWorkStatus
    interrupted: IdentityWorkClaim

    def __post_init__(self) -> None:
        if (
            type(self.accepted) is not IdentityWorkStatus
            or type(self.interrupted) is not IdentityWorkClaim
        ):
            raise ValueError("Hardcover durable work checkpoint is malformed")


class ManageHardcoverIdentityWork:
    """Own the application workflow over one provider-neutral persistence port."""

    def __init__(self, persistence: HardcoverIdentityWorkPersistence) -> None:
        if not isinstance(persistence, HardcoverIdentityWorkPersistence):
            raise TypeError("Hardcover identity work persistence is malformed")
        self._persistence = persistence

    def accept(
        self,
        work_key: str,
        now: datetime,
        *,
        egress_limit: int = 3,
    ) -> IdentityWorkStatus:
        if type(egress_limit) is not int or egress_limit not in {1, 3}:
            raise ValueError("Hardcover identity work egress limit is malformed")
        result = self._persistence.accept(
            work_key,
            now,
            egress_limit=egress_limit,
        )
        if (
            type(result) is not IdentityWorkStatus
            or result.reference.work_key != work_key
            or result.outcome
            not in {
                IdentityWorkOutcome.MATCHED,
                IdentityWorkOutcome.STALE,
                IdentityWorkOutcome.IN_PROGRESS,
                IdentityWorkOutcome.PERSISTENCE_FAILURE,
                IdentityWorkOutcome.RETRY_EXHAUSTED,
            }
        ):
            raise TypeError("Hardcover identity work acceptance is malformed")
        return result

    def inspect(
        self, reference: IdentityWorkReference, now: datetime
    ) -> IdentityWorkReadiness:
        result = self._persistence.inspect(reference, now)
        if type(result) is not IdentityWorkReadiness or result.reference != reference:
            raise TypeError("Hardcover identity work readiness is malformed")
        return result

    def claim(
        self,
        reference: IdentityWorkReference,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> IdentityWorkClaim:
        result = self._persistence.claim(reference, owner, now, gate_owned=gate_owned)
        if type(result) is not IdentityWorkClaim or result.reference != reference:
            raise TypeError("Hardcover identity work claim is malformed")
        return result

    def start(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
        egress_limit: int = 3,
    ) -> IdentityWorkClaim | IdentityWorkStatus:
        result = self._persistence.renew_and_start(
            claim,
            owner,
            now,
            gate_owned=gate_owned,
            egress_limit=egress_limit,
        )
        if type(result) is IdentityWorkClaim and (
            result.reference == claim.reference
            and result.candidate == claim.candidate
            and result.fence == claim.fence
        ):
            return result
        if (
            type(result) is IdentityWorkStatus
            and result.outcome is IdentityWorkOutcome.RETRY_EXHAUSTED
            and result.reference == claim.reference
            and result.candidate == claim.candidate
        ):
            return result
        raise TypeError("Hardcover identity work start result is malformed")

    def recover(
        self, claim: IdentityWorkClaim, now: datetime, *, gate_owned: bool
    ) -> int | IdentityWorkStatus:
        result = self._persistence.recover_interrupted(
            claim, now, gate_owned=gate_owned
        )
        if type(result) is int and result > claim.fence:
            return result
        if (
            type(result) is IdentityWorkStatus
            and result.outcome is IdentityWorkOutcome.RETRY_EXHAUSTED
            and result.reference == claim.reference
            and result.candidate == claim.candidate
        ):
            return result
        raise TypeError("Hardcover identity work recovery result is malformed")

    def publish_matched(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        observation: IdentityWorkObservation,
        *,
        gate_owned: bool,
        fail_before_commit: bool = False,
    ) -> IdentityWorkStatus:
        result = self._persistence.publish_matched(
            claim,
            owner,
            now,
            observation,
            gate_owned=gate_owned,
            fail_before_commit=fail_before_commit,
        )
        if (
            type(result) is not IdentityWorkStatus
            or result.reference != claim.reference
            or result.outcome
            not in {IdentityWorkOutcome.MATCHED, IdentityWorkOutcome.PERSISTENCE_FAILURE}
            or (
                result.outcome is IdentityWorkOutcome.MATCHED
                and result.candidate != claim.candidate
            )
        ):
            raise TypeError("Hardcover identity work publication is malformed")
        return result

    def publish_failed(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        retryable: bool,
        retry_delay_seconds: int | None = 0,
        gate_owned: bool,
        fail_before_commit: bool = False,
    ) -> IdentityWorkStatus:
        result = self._persistence.publish_failed(
            claim,
            owner,
            now,
            retryable=retryable,
            retry_delay_seconds=retry_delay_seconds,
            gate_owned=gate_owned,
            fail_before_commit=fail_before_commit,
        )
        if (
            type(result) is not IdentityWorkStatus
            or result.reference != claim.reference
            or result.outcome
            not in {
                IdentityWorkOutcome.IN_PROGRESS,
                IdentityWorkOutcome.PERSISTENCE_FAILURE,
                IdentityWorkOutcome.RETRY_EXHAUSTED,
            }
        ):
            raise TypeError("Hardcover identity work failure result is malformed")
        return result

    def cancel_available(
        self,
        reference: IdentityWorkReference,
        now: datetime,
    ) -> None:
        result = self._persistence.cancel_available(reference, now)
        if result is not None:
            raise TypeError("Hardcover identity work cancellation is malformed")

    def cancel(
        self,
        claim: IdentityWorkClaim,
        owner: str,
        now: datetime,
        *,
        gate_owned: bool,
    ) -> None:
        result = self._persistence.cancel(claim, owner, now, gate_owned=gate_owned)
        if result is not None:
            raise TypeError("Hardcover identity work cancellation is malformed")

    def begin_preview(
        self, work_key: str, origin: datetime
    ) -> DurableWorkPreviewCheckpoint:
        """Execute the pre-restart durable-work script through the owned port."""

        at = lambda microseconds: origin + timedelta(microseconds=microseconds)
        accepted = self.accept(work_key, at(500_000))
        claim = self.claim(
            accepted.reference, "worker-a", at(1_600_000), gate_owned=True
        )
        started = self.start(claim, "worker-a", at(1_700_000), gate_owned=True)
        if type(started) is not IdentityWorkClaim:
            raise IdentityWorkPersistenceError("identity work preview start was exhausted")
        claim = started
        try:
            self.publish_matched(
                claim,
                "worker-a",
                at(2_800_000),
                IdentityWorkObservation(IdentityMatchOutcome.MATCHED, 2),
                gate_owned=True,
                fail_before_commit=True,
            )
        except IdentityWorkPersistenceError:
            pass
        else:
            raise IdentityWorkPersistenceError(
                "identity publication rollback was not exercised"
            )
        return DurableWorkPreviewCheckpoint(accepted, claim)

    def resume_preview(
        self,
        checkpoint: DurableWorkPreviewCheckpoint,
        origin: datetime,
    ) -> tuple[IdentityWorkStatus, IdentityWorkStatus]:
        """Execute the post-restart recovery and currentness script."""

        if type(checkpoint) is not DurableWorkPreviewCheckpoint:
            raise TypeError("Hardcover durable work checkpoint is malformed")
        at = lambda microseconds: origin + timedelta(microseconds=microseconds)
        if self.recover(
            checkpoint.interrupted, at(16_900_000), gate_owned=True
        ) != 2:
            raise IdentityWorkPersistenceError("identity recovery fence is inconsistent")
        try:
            self.publish_matched(
                checkpoint.interrupted,
                "worker-a",
                at(17_000_000),
                IdentityWorkObservation(IdentityMatchOutcome.MATCHED, 1),
                gate_owned=True,
            )
        except StaleIdentityWorkFence:
            pass
        else:
            raise IdentityWorkPersistenceError("stale identity fence was accepted")
        retry = self.claim(
            checkpoint.accepted.reference,
            "worker-b",
            at(17_000_000),
            gate_owned=True,
        )
        started = self.start(retry, "worker-b", at(17_100_000), gate_owned=True)
        if type(started) is not IdentityWorkClaim:
            raise IdentityWorkPersistenceError("identity work preview retry was exhausted")
        retry = started
        self.publish_matched(
            retry,
            "worker-b",
            at(18_200_000),
            IdentityWorkObservation(IdentityMatchOutcome.MATCHED, 2),
            gate_owned=True,
        )
        exact = self.accept(checkpoint.accepted.reference.work_key, at(86_418_200_000))
        stale = self.accept(checkpoint.accepted.reference.work_key, at(86_418_200_001))
        return exact, stale


__all__ = [
    "AddressPolicyOutcome",
    "BookIdentityProvider",
    "DurableWorkPreviewReport",
    "DurableWorkPreviewCheckpoint",
    "FixedAdapterOutcome",
    "FixedAdapterAttemptEvidence",
    "FixedAdapterPreviewReport",
    "FixedAdapterStructuralPreview",
    "HardcoverIdentityOutcome",
    "HardcoverIdentityExecutor",
    "HardcoverIdentityReport",
    "IdentityAttemptFailure",
    "IdentityAttemptFailureKind",
    "IdentityAttemptResult",
    "IdentityDecisionPreviewReport",
    "IdentityLifecyclePreviewReport",
    "IdentityReportError",
    "IdentityWorkPersistenceError",
    "InertPreviewReport",
    "IsbnIdentityQuery",
    "HardcoverIdentityWorkPersistence",
    "ManageHardcoverIdentityWork",
    "RunHardcoverIdentityReport",
    "RunHardcoverFixedAdapterPreview",
    "SafeExecutorPreviewReport",
    "StaleIdentityWorkFence",
    "allow_identity_send",
    "fixed_adapter_preview_report",
    "inert_preview",
    "render_report_text",
]
