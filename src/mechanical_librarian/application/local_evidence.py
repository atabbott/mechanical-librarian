"""Framework-independent orchestration and rendering for local evidence."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from mechanical_librarian.application.ports import LocalEvidenceExtractor
from mechanical_librarian.domain.local_evidence import (
    EpubPackageLimits,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
    LocalEvidenceRequest,
)


EpubPackageReportRunner = Callable[[Path, str, str], LocalEvidenceReport]


def extract_local_evidence(
    extractor: LocalEvidenceExtractor, request: LocalEvidenceRequest
) -> LocalEvidenceReport:
    """Invoke one least-authority extractor and reject a changed report context."""

    if type(request) is not LocalEvidenceRequest or not hasattr(extractor, "extract"):
        raise ValueError("local evidence extraction requires typed inputs")
    report = extractor.extract(request)
    if type(report) is not LocalEvidenceReport:
        raise ValueError("local evidence extractor returned an invalid report")
    if report.capability is not request.capability or report.limits != request.limits:
        raise ValueError("local evidence extractor changed its declared context")
    if report.source_fingerprint is not None and report.source_fingerprint != request.expected_source:
        raise ValueError("local evidence extractor changed source identity")
    return report


def internal_failure_report() -> LocalEvidenceReport:
    """Create the fixed envelope used when a CLI runner cannot return a report."""

    return LocalEvidenceReport(
        outcome=LocalEvidenceOutcome.INTERNAL_FAILURE,
        capability=LocalEvidenceCapability.PACKAGE,
        limits=EpubPackageLimits(),
    )


def render_local_evidence_text(report: LocalEvidenceReport) -> str:
    """Render the same bounded canonical projection as stable line-oriented text."""

    if type(report) is not LocalEvidenceReport:
        raise ValueError("local evidence text rendering requires a typed report")
    fingerprint = (
        "none"
        if report.source_fingerprint is None
        else f"{report.source_fingerprint.schema.value}:{report.source_fingerprint.digest}"
    )
    lines = [
        f"schema={report.schema_version.value}",
        f"outcome={report.outcome.value}",
        f"capability={report.capability.value}",
        f"limit_profile={report.limits.profile}",
        f"source_fingerprint={fingerprint}",
        "artifact=none" if report.artifact is None else f"artifact={report.artifact.kind}:{report.artifact.identity}",
        f"observations={len(report.observations)} declarations={len(report.declarations)} warnings={len(report.warnings)}",
    ]
    for observation in report.normalized_observations:
        normalized = observation.normalized_value or "none"
        lines.append(
            "observation="
            f"{observation.field.value} applicability={observation.applicability.value} "
            f"ordinal={observation.citation.source_ordinal} normalized={normalized}"
        )
    for declaration in report.declarations:
        lines.append(
            f"declaration={declaration.kind.value} ordinal={declaration.source_ordinal}"
        )
    for warning in report.warnings:
        ordinal = "none" if warning.citation is None else str(warning.citation.source_ordinal)
        lines.append(f"warning={warning.code} ordinal={ordinal}")
    lines.append(
        "usage="
        + ",".join(f"{key}:{value}" for key, value in report.usage.to_data().items())
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "EpubPackageReportRunner",
    "extract_local_evidence",
    "internal_failure_report",
    "render_local_evidence_text",
]
