"""Synthetic adapter-produced synopsis alternatives and an independent tag action."""

from dataclasses import replace

from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from tests.support.audiobook_reports import synthetic_audiobook_product_lookup_report


def synopsis_report(
    current: str | None = None,
    summaries: tuple[str, ...] = (
        "A voyage reveals a hidden city.",
        "An explorer crosses the sea and discovers an ancient city.",
    ),
) -> MetadataReport:
    report = synthetic_audiobook_product_lookup_report(
        tuple(
            {
                "asin": f"B00000000{index}",
                "title": "Synthetic Journey",
                "authors": [{"name": "Example Author"}],
                "narrators": [{"name": "Example Narrator"}],
                "summary": summary,
                "genres": [{"type": "tag", "name": "Exploration"}],
            }
            for index, summary in enumerate(summaries, 1)
        )
    )
    return MetadataReport.create(
        target=replace(
            report.target,
            fields=tuple(
                TargetField(
                    field.destination,
                    TargetFieldState.MISSING
                    if current is None
                    else TargetFieldState.VALUE,
                    current,
                )
                if field.destination is TargetDestination.DESCRIPTION
                else field
                for field in report.target.fields
            ),
        ),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
        audio=report.audio,
    )
