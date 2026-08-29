"""Bounded canonical facts used by the review browse projections.

Canonical write boundaries perform complete semantic validation. Repository
browse queries use these bounded facts to avoid rebuilding the domain graph
several times, while the application still performs one complete parse of each
visible row before returning its DTO.
"""

from __future__ import annotations

import hashlib
import json

from mechanical_librarian.domain.identifiers import canonical_json
from mechanical_librarian.domain.metadata_report import (
    MAX_REPORT_BYTES,
    REPORT_SCHEMA,
)
from mechanical_librarian.domain.review_case import (
    MAX_REVIEW_CASE_BYTES,
    REVIEW_CASE_SCHEMA,
)
from mechanical_librarian.domain.validation import closed_document, literal


class ReviewBrowseValueError(ValueError):
    """A browse document cannot supply its bounded canonical facts."""


_REPORT = closed_document(
    "audio",
    "comparison_version",
    "covers",
    "field_results",
    "file",
    "identifier_evidence",
    "local",
    "providers",
    "report_id",
    "target",
    schema_version=literal(REPORT_SCHEMA),
)
_CASE = closed_document(
    "disposition",
    "proposal_set_id",
    "report_id",
    "target",
    "target_fingerprint",
    "file_identity",
    "results",
    "relationships",
    "proposal_document",
    schema_version=literal(REVIEW_CASE_SCHEMA),
)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _object(serialized: str, maximum: int) -> dict[str, object]:
    try:
        if (
            type(serialized) is not str
            or not serialized
            or len(serialized.encode("utf-8")) > maximum
        ):
            raise ValueError
        value = json.loads(
            serialized,
            object_pairs_hook=_unique_object,
            parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant)),
        )
        if type(value) is not dict:
            raise ValueError
        return value
    except (
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
    ) as error:
        raise ReviewBrowseValueError("review browse document is invalid") from error


def browse_report(serialized: str) -> dict[str, object]:
    """Read the closed report envelope without repeating semantic evaluation."""
    value = _object(serialized, MAX_REPORT_BYTES)
    if not _REPORT(value):
        raise ReviewBrowseValueError("review browse report is invalid")
    return value


def browse_case(serialized: str) -> dict[str, object]:
    """Read the closed case envelope without reconstructing its proposal set."""
    value = _object(serialized, MAX_REVIEW_CASE_BYTES)
    if not _CASE(value):
        raise ReviewBrowseValueError("review browse case is invalid")
    return value


def browse_case_and_report(
    case_document: str, report_document: str, admitted_proposal_set_id: str
) -> tuple[dict[str, object], dict[str, object]]:
    """Prove one case still binds its admitted proposal and source report."""
    case = browse_case(case_document)
    report = browse_report(report_document)
    target = report["target"]
    file = report["file"]
    if (
        type(target) is not dict
        or type(file) is not dict
        or type(admitted_proposal_set_id) is not str
        or case["proposal_set_id"] != admitted_proposal_set_id
        or case["report_id"] != report["report_id"]
        or case["target"] != target
        or case["target_fingerprint"]
        != hashlib.sha256(canonical_json(target).encode("utf-8")).hexdigest()
        or case["file_identity"] != file.get("identity")
    ):
        raise ReviewBrowseValueError("review browse lineage disagrees")
    return case, report


def browse_case_with_id(case_document: str) -> tuple[dict[str, object], str]:
    """Read one bounded case envelope and derive its content identity once."""
    return browse_case(case_document), "review_case_" + hashlib.sha256(
        case_document.encode("utf-8")
    ).hexdigest()


def browse_review_case_id(case_document: str) -> str:
    """Return the stable ID of a canonical case envelope."""
    return browse_case_with_id(case_document)[1]


__all__ = [
    "ReviewBrowseValueError",
    "browse_case",
    "browse_case_with_id",
    "browse_case_and_report",
    "browse_report",
    "browse_review_case_id",
]
