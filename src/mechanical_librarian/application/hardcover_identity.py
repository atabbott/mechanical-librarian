"""Inert, proposal-free Hardcover identity reporting."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

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


MAX_IDENTITY_OBSERVATIONS = 4


class HardcoverIdentityOutcome(StrEnum):
    DISABLED = "disabled"
    PROVIDER_NOT_ACTIVATED = "provider_not_activated"
    INVALID_INPUT = "invalid_input"


class IdentityAttemptFailureKind(StrEnum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
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

    def __post_init__(self) -> None:
        try:
            parsed = isbn_identity(self.canonical_isbn_13)
        except ValueError:
            raise ValueError("ISBN identity query is malformed") from None
        if (
            parsed.canonical_isbn_13 != self.canonical_isbn_13
            or parsed.equivalent_isbn_10 != self.equivalent_isbn_10
        ):
            raise ValueError("ISBN identity query is malformed")

    @classmethod
    def from_private(cls, query: PrivateIdentityQuery) -> IsbnIdentityQuery:
        if type(query) is not PrivateIdentityQuery:
            raise TypeError("private identity query is malformed")
        parsed = isbn_identity(query.canonical_isbn)
        return cls(parsed.canonical_isbn_13, parsed.equivalent_isbn_10)

    def __repr__(self) -> str:
        return "IsbnIdentityQuery(<redacted>)"


@dataclass(frozen=True, slots=True)
class IdentityAttemptFailure:
    kind: IdentityAttemptFailureKind

    def __post_init__(self) -> None:
        if type(self.kind) is not IdentityAttemptFailureKind:
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
    def failed(cls, kind: IdentityAttemptFailureKind) -> IdentityAttemptResult:
        return cls(failure=IdentityAttemptFailure(kind))

    def __repr__(self) -> str:
        return "IdentityAttemptResult(<redacted>)"


class BookIdentityProvider(Protocol):
    def lookup(
        self, query: IsbnIdentityQuery, context: OperationContext
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
            raise TypeError("Hardcover structural preview input is malformed")
        invoke = _attempt_method(preview, "run", "Hardcover structural preview")
        try:
            evidence = invoke(source)
        except KeyboardInterrupt:
            raise _attempt_error(
                "Hardcover structural preview", IdentityAttemptFailureKind.CANCELLED
            ) from None
        except BaseException:
            raise _attempt_error(
                "Hardcover structural preview", IdentityAttemptFailureKind.UNAVAILABLE
            ) from None
        if type(evidence) is not FixedAdapterAttemptEvidence:
            raise IdentityReportError("Hardcover structural preview is malformed")
        return fixed_adapter_preview_report(
            evidence.result,
            evidence.query,
            peer_equal=evidence.peer_equal,
            address_policy=evidence.address_policy,
        )


@dataclass(frozen=True)
class HardcoverIdentityReport:
    outcome: HardcoverIdentityOutcome

    def to_json(self) -> str:
        return json.dumps(
            {"schema": "hardcover-identity-report-v1", "outcome": self.outcome.value},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


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
    """Run exactly one provider attempt and discard all private observations."""

    def run(
        self,
        provider: BookIdentityProvider,
        query: PrivateIdentityQuery,
        context: OperationContext,
        *,
        privacy_observer: Callable[[str, tuple[object, ...]], None] | None = None,
    ) -> IdentityDecisionPreviewReport:
        if type(query) is not PrivateIdentityQuery or type(context) is not OperationContext:
            raise TypeError("identity report input is malformed")
        if privacy_observer is not None and not callable(privacy_observer):
            raise TypeError("Hardcover privacy observer is malformed")

        lookup = _attempt_method(provider, "lookup", "book identity provider")
        try:
            result = lookup(IsbnIdentityQuery.from_private(query), context)
        except KeyboardInterrupt:
            raise _attempt_error(
                "identity provider attempt", IdentityAttemptFailureKind.CANCELLED
            ) from None
        except BaseException:
            raise _attempt_error(
                "identity provider attempt", IdentityAttemptFailureKind.UNAVAILABLE
            ) from None
        if type(result) is not IdentityAttemptResult:
            raise IdentityReportError("identity provider returned a malformed result")
        if result.failure is not None:
            outcome = _failure_outcome(result.failure.kind)
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
            _notify_observer(privacy_observer,
                "traces",
                ("hardcover.synthetic.identity_decision", "identity-decision-v3"),
            )
            _notify_observer(privacy_observer,
                "spans",
                ("lookup-and-decide", decision_report.outcome.value),
            )
            _notify_observer(privacy_observer,
                "metrics",
                (
                    "hardcover_identity_preview_total",
                    (("outcome", decision_report.outcome.value),),
                    1,
                    None,
                ),
            )
        return decision_report


def _failure_outcome(kind: IdentityAttemptFailureKind) -> IdentityDecisionOutcome:
    if kind is IdentityAttemptFailureKind.INCOMPATIBLE:
        return IdentityDecisionOutcome.INCOMPATIBLE
    if kind in {
        IdentityAttemptFailureKind.MALFORMED,
        IdentityAttemptFailureKind.OVERSIZED,
    }:
        return IdentityDecisionOutcome.MALFORMED
    raise IdentityReportError(
        "identity provider attempt did not produce a decision", kind
    )


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


def _attempt_method(owner: object, name: str, label: str) -> Callable[..., object]:
    try:
        method = getattr(owner, name, None)
    except KeyboardInterrupt:
        raise _attempt_error(label, IdentityAttemptFailureKind.CANCELLED) from None
    except BaseException:
        raise _attempt_error(label, IdentityAttemptFailureKind.UNAVAILABLE) from None
    if not callable(method):
        raise TypeError(f"{label} is malformed")
    return method


def _notify_observer(
    observer: Callable[[str, tuple[object, ...]], None],
    channel: str,
    values: tuple[object, ...],
) -> None:
    try:
        observer(channel, values)
    except KeyboardInterrupt:
        raise _attempt_error(
            "Hardcover privacy observer", IdentityAttemptFailureKind.CANCELLED
        ) from None
    except BaseException:
        raise _attempt_error(
            "Hardcover privacy observer", IdentityAttemptFailureKind.UNAVAILABLE
        ) from None


def preactivation_report(
    configuration: HardcoverProviderConfiguration,
) -> HardcoverIdentityReport:
    """Return before private query, database, credential, or network authority."""

    if type(configuration) is not HardcoverProviderConfiguration:
        raise TypeError("Hardcover provider configuration is malformed")
    return HardcoverIdentityReport(
        HardcoverIdentityOutcome.PROVIDER_NOT_ACTIVATED
        if configuration.enabled
        else HardcoverIdentityOutcome.DISABLED
    )


def inert_preview(
    configuration: HardcoverProviderConfiguration,
    query: PrivateIdentityQuery,
    *,
    privacy_observer: Callable[[str, tuple[object, ...]], None] | None = None,
) -> InertPreviewReport:
    """Exercise real validation seams while retaining no private projection."""

    if privacy_observer is not None and not callable(privacy_observer):
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
        _notify_observer(privacy_observer,
            "traces",
            ("hardcover.synthetic.inert_preview", "inert-report-v3"),
        )
        _notify_observer(privacy_observer,
            "spans",
            ("validate-and-report", report.outcome.value),
        )
        _notify_observer(privacy_observer,
            "metrics",
            (
                "hardcover_identity_preview_total",
                (("outcome", report.outcome.value),),
                1,
                None,
            ),
        )
    return report


def render_report_text(report: HardcoverIdentityReport) -> str:
    if (
        type(report) is not HardcoverIdentityReport
        or type(report.outcome) is not HardcoverIdentityOutcome
    ):
        raise TypeError("Hardcover identity report is malformed")
    return (
        "Hardcover identity report\n"
        "schema=hardcover-identity-report-v1\n"
        f"outcome={report.outcome.value}\n"
        "proposal=false write=false\n"
    )


__all__ = [
    "AddressPolicyOutcome",
    "BookIdentityProvider",
    "FixedAdapterOutcome",
    "FixedAdapterAttemptEvidence",
    "FixedAdapterPreviewReport",
    "FixedAdapterStructuralPreview",
    "HardcoverIdentityOutcome",
    "HardcoverIdentityReport",
    "IdentityAttemptFailure",
    "IdentityAttemptFailureKind",
    "IdentityAttemptResult",
    "IdentityDecisionPreviewReport",
    "IdentityReportError",
    "InertPreviewReport",
    "IsbnIdentityQuery",
    "RunHardcoverIdentityReport",
    "RunHardcoverFixedAdapterPreview",
    "fixed_adapter_preview_report",
    "inert_preview",
    "preactivation_report",
    "render_report_text",
]
