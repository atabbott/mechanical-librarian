"""Lossless SQLite representation of an expanded v4 cover consideration.

Reports are immutable canonical evidence owned by metadata_reports. Readers bind
both documents in one SQLite snapshot; application hashes always cover expansion.
"""

from __future__ import annotations

import hashlib
import json

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import MetadataReport


def compact_cover_consideration(document: str, report_document: str) -> str:
    """Replace only an exact canonical report, inside the publication transaction."""
    try:
        value = json.loads(document)
        report = MetadataReport.from_json(report_document)
        if (
            type(value) is not dict
            or value.get("schema_version") != "cover-consideration-v4"
            or value.get("report_document") != report_document
            or value.get("report_id") != report.report_id
            or "report_document_digest" in value
            or _canonical_json(value) != document
            or report.to_json() != report_document
            or not 2 <= len(document.encode("utf-8")) <= 12_582_912
        ):
            raise ValueError
        del value["report_document"]
        value["report_document_digest"] = hashlib.sha256(
            report_document.encode("utf-8")
        ).hexdigest()
        return _canonical_json(value)
    except (KeyError, TypeError, ValueError, RecursionError) as error:
        raise MetadataReportError("cover consideration is invalid") from error


def expand_cover_consideration(
    document: str,
    report_document: str,
    report_document_digest: str,
    document_digest: str,
    report_id: str,
) -> str:
    """Refuse missing or mismatched joins; never turn corrupt evidence into absence."""
    try:
        if (
            type(document) is not str
            or not 2 <= len(document.encode("utf-8")) <= 12_582_912
            or type(report_document) is not str
            or not 2 <= len(report_document.encode("utf-8")) <= 2_097_152
        ):
            raise ValueError
        value = json.loads(document)
        if (
            type(value) is not dict
            or "report_document" in value
            or value.get("schema_version") != "cover-consideration-v4"
            or value.get("report_id") != report_id
            or value.get("report_document_digest") != report_document_digest
            or hashlib.sha256(report_document.encode("utf-8")).hexdigest()
            != report_document_digest
            or _canonical_json(value) != document
        ):
            raise ValueError
        del value["report_document_digest"]
        value["report_document"] = report_document
        expanded = _canonical_json(value)
        if (
            compact_cover_consideration(expanded, report_document) != document
            or hashlib.sha256(expanded.encode("utf-8")).hexdigest() != document_digest
        ):
            raise ValueError
        return expanded
    except (
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
        MetadataReportError,
    ) as error:
        raise MetadataReportError("cover consideration is unavailable") from error
