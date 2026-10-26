"""Bounded canonical facts used by the review browse projections.

Canonical write boundaries and the work detail perform complete semantic
validation. Library and Attention rows are projected from these bounded facts,
which verify each document's content address and read every field a row renders
under that field's canonical rule (#1191). With the content addresses verified,
every rendered value is byte-identical to what the canonical writer validated at
write time. What a row no longer re-checks is the internal-consistency rules
the canonical writer enforces (format/audio section agreement, provider
status/candidate agreement, inactive-provider inputs), non-canonical report and
proposal text, and sub-documents the row never renders: those can only diverge
through a canonical-writer defect or an out-of-band storage edit, and detail
and every write path still parse canonically. Consecutive Attention fields
with exactly equal document strings share that read within one repository page
only.
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
    _provider_lane_dispatch_from_data,
    _request_accounting,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import (
    MAX_PROPOSAL_BYTES,
    MEDIA_KINDS,
    PROPOSAL_SCHEMA,
    _result_from_data,
    proposal_set_content_address,
)
from mechanical_librarian.domain.review_case import (
    MAX_REVIEW_CASE_BYTES,
    REVIEW_CASE_SCHEMA,
    proposal_manual_requirements,
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
_PROPOSAL = closed_document(
    "media_kind",
    "policy_version",
    "relationships",
    "report",
    "resolved_policy_id",
    "results",
    schema_version=literal(PROPOSAL_SCHEMA),
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


def require_admitted_lineage(
    case: dict[str, object], report: dict[str, object], admitted_proposal_set_id: str
) -> None:
    """Prove read envelopes still bind the admitted proposal and source report."""
    _require_lineage(case, report)
    if (
        type(admitted_proposal_set_id) is not str
        or case["proposal_set_id"] != admitted_proposal_set_id
    ):
        raise ReviewBrowseValueError("review browse lineage disagrees")


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
    # How an audiobook lane's dispatch ended, None where none was recorded, so
    # a lane work-identity admission refused can say so (#1407).
    dispatch: str | None


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
    proposals: tuple[dict[str, object], ...]


def provider_lane_facts(report: MetadataReport) -> tuple[ProviderLaneFacts, ...]:
    """State a parsed report's provider lanes in the browse vocabulary."""
    return tuple(
        ProviderLaneFacts(
            provider.authority,
            provider.request_accounting.logical_requests,
            kind.value,
            status.value,
            None if dispatch is None else dispatch.outcome.value,
        )
        for provider in report.providers
        for kind, status, dispatch in (
            tuple((lane.kind, lane.status, lane.dispatch) for lane in provider.lanes)
            or ((provider.query_kind, provider.status, None),)
        )
    )


def library_report_facts(report: dict[str, object]) -> LibraryReportFacts:
    """Read what a browse row renders from a report, and nothing else.

    The report must carry its own content address, so every value read here is
    the one the canonical writer validated. The target and file are then
    reconstructed by their own canonical readers, and each provider lane's
    authority, kinds, statuses, dispatch outcome and request count are checked
    against the enums and bounds the canonical provider reader applies to them;
    its candidates, evidence and diagnostics are not rendered and not read.
    """
    try:
        identity = {key: value for key, value in report.items() if key != "report_id"}
        if report["report_id"] != report_content_address(identity):
            raise ValueError
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
                (
                    ProviderQueryKind(lane["kind"]),
                    ProviderStatus(lane["status"]),
                    None
                    if lane["dispatch"] is None
                    else _provider_lane_dispatch_from_data(
                        lane["dispatch"]
                    ).outcome.value,
                )
                for lane in provider["lanes"]
            ]
            if any(lane_kind not in PROVIDER_LANE_KINDS for lane_kind, _, _ in pairs):
                raise ValueError
            lanes.extend(
                ProviderLaneFacts(
                    authority,
                    requests.logical_requests,
                    value.value,
                    state.value,
                    dispatch,
                )
                for value, state, dispatch in pairs or [(kind, status, None)]
            )
        return LibraryReportFacts(report["report_id"], target, file, tuple(lanes))
    except (KeyError, TypeError, ValueError) as error:
        raise ReviewBrowseValueError("review browse report is invalid") from error


def case_browse_facts(case: dict[str, object], review_case_id: str) -> CaseBrowseFacts:
    """Project a case's proposals from its own results, as `review_proposals` does.

    The case must name its proposal document by that document's content
    address, the case must be undecided, and the document must carry the
    case's report binding, relationships and exactly its results, so every
    value read here is the one the canonical writer validated. Every result is
    read by the canonical result reader, so each rendered outcome, reason and
    evidence reference is refused exactly as there. The proposal set is not
    reconstructed and no proposal identity is computed: no browse row renders
    one.
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
        proposal = _object(cast(str, case["proposal_document"]), MAX_PROPOSAL_BYTES)
        if (
            not _PROPOSAL(proposal)
            or case["disposition"] != "undecided"
            or case["proposal_set_id"] != proposal_set_content_address(proposal)
            or proposal["report"]
            != {
                "report_id": case["report_id"],
                "target_fingerprint": case["target_fingerprint"],
            }
            or proposal["results"] != results
            or proposal["relationships"] != case["relationships"]
            or proposal["media_kind"] not in MEDIA_KINDS
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
    if canonical_json(case) != case_document:
        raise ReviewBrowseValueError("review browse case is invalid")
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
    "browse_report",
    "browse_review_case_id",
    "case_browse_facts",
    "library_report_facts",
    "provider_lane_facts",
    "require_admitted_lineage",
]
