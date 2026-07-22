"""Application-facing EPUB opening-report contract and rendering."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.epub_inspection import EpubInspectionOutcome


EpubOpeningReportRunner = Callable[[Path, str, str], EpubOpeningReport]


def internal_opening_failure_report() -> EpubOpeningReport:
    return EpubOpeningReport(EpubInspectionOutcome.INTERNAL_FAILURE, EpubOpeningLimits())


def render_epub_opening_text(report: EpubOpeningReport) -> str:
    if type(report) is not EpubOpeningReport:
        raise ValueError("EPUB opening text rendering requires a typed report")
    lines: list[str] = []
    for key, value in report.to_data().items():
        if type(value) is list:
            if not value:
                lines.append(f"{key}=[]")
            else:
                lines.extend(
                    f"{key}[{index}]="
                    + json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                    for index, item in enumerate(value)
                )
        else:
            lines.append(
                f"{key}="
                + json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            )
    return "\n".join(lines) + "\n"


__all__ = [
    "EpubOpeningReportRunner",
    "internal_opening_failure_report",
    "render_epub_opening_text",
]
