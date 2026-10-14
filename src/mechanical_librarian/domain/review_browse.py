"""Bounded canonical facts used by the review browse projections.

Canonical write boundaries and the work detail perform complete semantic
validation. Library and Attention rows are projected from these bounded facts,
which read every field a row renders under that field's canonical rule and read
nothing a row does not render (#1191). Consecutive Attention fields with exactly
equal document strings share that read within one repository page only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

from mechanical_librarian.domain.identifiers import canonical_json
from mechanical_librarian.domain.metadata_report import (
    MAX_REPORT_BYTES,
    PROVIDER_AUTHORITIES,
    PROVIDER_LANE_KINDS,
    REPORT_SCHEMA,
    FileFacts,
    MetadataReport,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    _request_accounting,
)
from mechanical_librarian.domain.proposal_set import _result_from_data
from mechanical_librarian.domain.review_case import (
    MAX_REVIEW_CASE_BYTES,
    REVIEW_CASE_SCHEMA,
    proposal_manual_requirements,
    proposal_media_kind,
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
    _require_lineage(case, report)
    if (
        type(admitted_proposal_set_id) is not str
        or case["proposal_set_id"] != admitted_proposal_set_id
    ):
        raise ReviewBrowseValueError("review browse lineage disagrees")
    return case, report


def _require_lineage(case: dict[str, object], report: dict[str, object]) -> None:
    """Refuse a case that no longer names the report it is read beside."""
    target = report["target"]
    file = report["file"]
    if (
        type(target) is not dict
        or type(file) is not dict
        or case["report_id"] != report["report_id"]
        or case["target"] != target
        or case["target_fingerprint"]
        != hashlib.sha256(canonical_json(target).encode("utf-8")).hexdigest()
        or case["file_identity"] != file.get("identity")
    ):
        raise ReviewBrowseValueError("review browse lineage disagrees")


@dataclass(frozen=True, slots=True)
class ProviderLaneFacts:
    """One provider lane as Library and the report detail state it."""

    authority: str | None
    logical_requests: int
    kind: str
    status: str


@dataclass(frozen=True, slots=True)
class LibraryReportFacts:
    """The report fields a browse row renders, each under its canonical rule."""

    report_id: str
    target: TargetFacts
    file: FileFacts
    providers: tuple[ProviderLaneFacts, ...]


@dataclass(frozen=True, slots=True)
class CaseBrowseFacts:
    """The case fields a browse row renders, without its proposal set."""

    review_case_id: str
    report_id: str
    target: dict[str, object]
    target_fingerprint: str
    file_identity: str
    proposal_document: str
    proposals: tuple[dict[str, object], ...]

    @property
    def media_kind(self) -> str:
        return proposal_media_kind(self.proposal_document)


def provider_lane_facts(report: MetadataReport) -> tuple[ProviderLaneFacts, ...]:
    """State a parsed report's provider lanes in the browse vocabulary."""
    return tuple(
        ProviderLaneFacts(
            provider.authority,
            provider.request_accounting.logical_requests,
            kind.value,
            status.value,
        )
        for provider in report.providers
        for kind, status in (
            tuple((lane.kind, lane.status) for lane in provider.lanes)
            or ((provider.query_kind, provider.status),)
        )
    )


def library_report_facts(report: dict[str, object]) -> LibraryReportFacts:
    """Read what a browse row renders from a report, and nothing else.

    The target and file are reconstructed by their own canonical readers. Each
    provider lane's authority, kinds, statuses and request count are checked
    against the enums and bounds the canonical provider reader applies to them;
    its candidates, evidence and diagnostics are not rendered and not read.
    """
    try:
        target = TargetFacts.from_data(report["target"])
        file = FileFacts(**cast("dict[str, Any]", report["file"]))
        providers = report["providers"]
        if (
            target.to_data() != report["target"]
            or file.to_data() != report["file"]
            or type(report["report_id"]) is not str
            or type(providers) is not list
        ):
            raise ValueError
        lanes: list[ProviderLaneFacts] = []
        for provider in providers:
            authority = provider["authority"]
            kind = ProviderQueryKind(provider["query_kind"])
            status = ProviderStatus(provider["status"])
            requests = _request_accounting(provider["request_accounting"])
            if (
                authority not in PROVIDER_AUTHORITIES
                or type(provider["lanes"]) is not list
            ):
                raise ValueError
            pairs = [
                (ProviderQueryKind(lane["kind"]), ProviderStatus(lane["status"]))
                for lane in provider["lanes"]
            ]
            if any(lane_kind not in PROVIDER_LANE_KINDS for lane_kind, _ in pairs):
                raise ValueError
            lanes.extend(
                ProviderLaneFacts(
                    authority, requests.logical_requests, value.value, state.value
                )
                for value, state in pairs or [(kind, status)]
            )
        return LibraryReportFacts(report["report_id"], target, file, tuple(lanes))
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewBrowseValueError("review browse report is invalid") from error


def case_browse_facts(case: dict[str, object], review_case_id: str) -> CaseBrowseFacts:
    """Project a case's proposals from its own results, as `review_proposals` does.

    Every result is read by the canonical result reader, so each rendered
    outcome, reason and evidence reference is refused exactly as there. The
    proposal document is not reconstructed and no proposal identity is
    computed: no browse row renders one.
    """
    try:
        results = case["results"]
        target = case["target"]
        strings = (
            review_case_id,
            case["report_id"],
            case["target_fingerprint"],
            case["file_identity"],
            case["proposal_document"],
        )
        if (
            type(results) is not list
            or type(target) is not dict
            or any(type(value) is not str for value in strings)
        ):
            raise ValueError
        parsed = tuple(_result_from_data(result) for result in results)
        if tuple(value.destination for value in parsed) != tuple(
            TargetDestination
        ) or any(
            value.to_data() != result
            for value, result in zip(parsed, results, strict=True)
        ):
            raise ValueError
        return CaseBrowseFacts(
            review_case_id,
            cast(str, case["report_id"]),
            target,
            cast(str, case["target_fingerprint"]),
            cast(str, case["file_identity"]),
            cast(str, case["proposal_document"]),
            tuple(
                {
                    "destination": result["destination"],
                    "manual_required": manual_required,
                    "result": result,
                }
                for result, manual_required in zip(
                    results, proposal_manual_requirements(results), strict=True
                )
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewBrowseValueError("review browse case is invalid") from error


def browse_case_facts_and_report(
    case_document: str, report_document: str
) -> tuple[CaseBrowseFacts, LibraryReportFacts]:
    """Read one case and its report for a browse row, in agreement."""
    case, review_case_id = browse_case_with_id(case_document)
    report = browse_report(report_document)
    _require_lineage(case, report)
    return case_browse_facts(case, review_case_id), library_report_facts(report)


def browse_case_with_id(case_document: str) -> tuple[dict[str, object], str]:
    """Read one bounded case envelope and derive its content identity once."""
    return browse_case(case_document), "review_case_" + hashlib.sha256(
        case_document.encode("utf-8")
    ).hexdigest()


def browse_review_case_id(case_document: str) -> str:
    """Return the stable ID of a canonical case envelope."""
    return browse_case_with_id(case_document)[1]


__all__ = [
    "CaseBrowseFacts",
    "LibraryReportFacts",
    "ProviderLaneFacts",
    "ReviewBrowseValueError",
    "browse_case",
    "browse_case_facts_and_report",
    "browse_case_with_id",
    "browse_case_and_report",
    "browse_report",
    "browse_review_case_id",
    "case_browse_facts",
    "library_report_facts",
    "provider_lane_facts",
]
