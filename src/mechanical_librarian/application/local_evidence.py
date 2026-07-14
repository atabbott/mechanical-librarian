"""Framework-independent orchestration and rendering for local evidence."""

from __future__ import annotations

import json
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
    lines: list[str] = []
    for key, value in report.to_data().items():
        if type(value) is list:
            if not value:
                lines.append(f"{key}=[]")
                continue
            for index, item in enumerate(value):
                lines.append(
                    f"{key}[{index}]="
                    + json.dumps(
                        item,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
            continue
        lines.append(
            f"{key}="
            + json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "EpubPackageReportRunner",
    "extract_local_evidence",
    "internal_failure_report",
    "render_local_evidence_text",
]
