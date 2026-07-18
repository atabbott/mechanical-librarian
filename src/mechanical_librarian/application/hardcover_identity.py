"""Inert, proposal-free Hardcover identity reporting."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.application.configuration import (
    HardcoverProviderConfiguration,
)
from mechanical_librarian.domain.hardcover_identity import PrivateIdentityQuery


class HardcoverIdentityOutcome(StrEnum):
    DISABLED = "disabled"
    PROVIDER_NOT_ACTIVATED = "provider_not_activated"
    INVALID_INPUT = "invalid_input"


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


@dataclass(frozen=True)
class InertPreviewReport:
    outcome: HardcoverIdentityOutcome
    private_file_opened: bool = False
    database_opened: bool = False
    secret_opened: bool = False
    network_started: bool = False

    def to_json(self) -> str:
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
    "HardcoverIdentityOutcome",
    "HardcoverIdentityReport",
    "InertPreviewReport",
    "inert_preview",
    "preactivation_report",
    "render_report_text",
]
