"""Inert, proposal-free Hardcover identity reporting."""

from __future__ import annotations

import json
from collections.abc import Callable
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
)


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
    INCOMPATIBLE = "incompatible"


class IdentityReportError(RuntimeError):
    """A value-free failure while running one identity attempt."""


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
        if self.observations is not None and (
            type(self.observations) is not tuple
            or any(
                type(row) is not ProviderIdentityObservation for row in self.observations
            )
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
        if (
            type(self.outcome) is not IdentityDecisionOutcome
            or type(self.rows_validated) is not int
            or not 0 <= self.rows_validated <= 4
            or type(self.aliases_collapsed) is not int
            or not 0 <= self.aliases_collapsed <= 3
            or type(self.comparison) is not TitleComparison
            or (
                self.outcome is not IdentityDecisionOutcome.MATCHED
                and (
                    self.aliases_collapsed != 0
                    or self.comparison is not TitleComparison.NOT_COMPARED
                )
            )
        ):
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
        lookup = getattr(provider, "lookup", None)
        if not callable(lookup):
            raise TypeError("book identity provider is malformed")
        if type(query) is not PrivateIdentityQuery or type(context) is not OperationContext:
            raise TypeError("identity report input is malformed")
        if privacy_observer is not None and not callable(privacy_observer):
            raise TypeError("Hardcover privacy observer is malformed")

        result = lookup(IsbnIdentityQuery.from_private(query), context)
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
            privacy_observer(
                "traces",
                ("hardcover.synthetic.identity_decision", "identity-decision-v3"),
            )
            privacy_observer(
                "spans",
                ("lookup-and-decide", decision_report.outcome.value),
            )
            privacy_observer(
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
    raise IdentityReportError("identity provider attempt did not produce a decision")


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
        privacy_observer(
            "traces",
            ("hardcover.synthetic.inert_preview", "inert-report-v3"),
        )
        privacy_observer(
            "spans",
            ("validate-and-report", report.outcome.value),
        )
        privacy_observer(
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
    "BookIdentityProvider",
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
    "inert_preview",
    "preactivation_report",
    "render_report_text",
]
