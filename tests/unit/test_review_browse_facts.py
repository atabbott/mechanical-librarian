"""Bounded browse facts against the canonical parsers they stand in for (#1191)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from mechanical_librarian.application import review_web
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
    ReviewWebError,
    _case_and_report,
    _library_item,
)
from mechanical_librarian.domain.identifiers import canonical_json
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderLaneObservation,
    ReportValueError,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_browse import (
    CaseBrowseFacts,
    LibraryReportFacts,
    ProviderLaneFacts,
    ReviewBrowseValueError,
    browse_case_facts_and_report,
    browse_report,
    library_report_facts,
    provider_lane_facts,
)
from mechanical_librarian.domain.review_case import ReviewCase, ReviewCaseValueError
from tests.support.audiobook_reports import corroborated_audiobook_report
from tests.support.owner_responsiveness import OwnerResponsivenessFixture
from tests.support.proposal_evidence import (
    _real_tigers_corroboration_report,
    retained_decoration_report,
)
from tests.support.review_cases import _report
from tests.unit.test_review_library_persistence import _terminal_document

Pair = tuple[dict[str, Any], dict[str, Any]]


# The canonical oracle: what Library and Attention projected from before #1191.
# Both documents are reconstructed completely, the four lineage facts are
# compared on the typed values, and the rendered facts are read off the typed
# objects, the provider lanes by the loop `_provider_outcomes` used to run.
def _canonical_report_facts(report: MetadataReport) -> LibraryReportFacts:
    return LibraryReportFacts(
        report.report_id,
        report.target,
        report.file,
        tuple(
            ProviderLaneFacts(
                provider.authority,
                provider.request_accounting.logical_requests,
                (
                    cast(ProviderLaneObservation, lane).kind
                    if provider.lanes
                    else provider.query_kind
                ).value,
                lane.status.value,
            )
            for provider in report.providers
            for lane in provider.lanes or (provider,)
        ),
    )


def _canonical_pair(
    case_document: str, report_document: str
) -> tuple[CaseBrowseFacts, LibraryReportFacts]:
    try:
        case = ReviewCase.from_json(case_document)
        report = MetadataReport.from_json(report_document)
    except (ReviewCaseValueError, ReportValueError) as error:
        raise ReviewWebError("unavailable") from error
    if (
        case.report_id != report.report_id
        or case.target != report.target.to_data()
        or case.target_fingerprint != report.target.fingerprint
        or case.file_identity != report.file.identity
    ):
        raise ReviewWebError("unavailable")
    return (
        CaseBrowseFacts(
            case.review_case_id,
            case.report_id,
            case.target,
            case.target_fingerprint,
            case.file_identity,
            case.proposal_document,
            tuple(
                {key: value for key, value in proposal.items() if key != "proposal_id"}
                for proposal in case.proposals
            ),
        ),
        _canonical_report_facts(report),
    )


def _canonical_report(report_document: str) -> LibraryReportFacts:
    try:
        return _canonical_report_facts(MetadataReport.from_json(report_document))
    except ReportValueError as error:
        raise ReviewWebError("unavailable") from error


def _documents(report: MetadataReport) -> tuple[str, str]:
    return ReviewCase.create(evaluate_report(report), report).to_json(), (
        report.to_json()
    )


_REPORTS: dict[str, Callable[[], MetadataReport]] = {
    "lanes": _real_tigers_corroboration_report,
    "audiobook": corroborated_audiobook_report,
    "decoration": lambda: retained_decoration_report("subtitle"),
    "plain": _report,
}


def _library_rows(case_document: str | None, report_document: str):
    terminal = _terminal_document("unreadable_media", "track_3")
    return (
        (case_document, report_document, (), (), (), (), None, 2),
        (case_document, report_document, (), ("title",), ("cover",), (), None, 0),
        (case_document, report_document, (), (), (), ("authors",), terminal, 1),
        (None, report_document, (), (), (), (), None, 0),
        (None, terminal, (), (), (), (), None, 0),
    )


def _attention_rows(case_document: str, report_document: str):
    return tuple(
        (case_document, report_document, destination.value, 3, outcome, cause, flag)
        for destination in TargetDestination
        for outcome, cause, flag in (
            (None, None, True),
            ("abstention", "automatic_safety_suspended", False),
        )
    )


def _pages(case_document: str, report_document: str) -> str:
    repository = Mock()
    library_rows = _library_rows(case_document, report_document)
    attention_rows = _attention_rows(case_document, report_document)
    repository.read_review_library.return_value = ReviewLibrarySnapshot(
        library_rows,
        len(library_rows),
        1,
    )
    repository.read_review_attention.return_value = ReviewAttentionSnapshot(
        attention_rows,
        len(attention_rows),
    )
    browse = BrowseReviewLibrary(repository)
    pages = {
        "library": browse.library(lens="all", search=None, limit=50, cursor=None),
        "attention": browse.attention(search=None, limit=50, cursor=None),
        "counted": _library_item(
            case_document, report_document, (), attention_count=None
        ),
    }
    return json.dumps(pages, sort_keys=True)


def _canonical_pages(case_document: str, report_document: str) -> str:
    with (
        patch.object(review_web, "_case_and_report", _canonical_pair),
        patch.object(review_web, "_report_facts", _canonical_report),
    ):
        return _pages(case_document, report_document)


@pytest.mark.parametrize("name", _REPORTS)
def test_narrow_pages_equal_the_canonical_pages_byte_for_byte(name: str) -> None:
    case_document, report_document = _documents(_REPORTS[name]())
    narrow = _pages(case_document, report_document)
    assert narrow == _canonical_pages(case_document, report_document)
    page = json.loads(narrow)
    assert len(page["library"]["items"]) == 5
    assert page["attention"]["items"]


def test_provider_lane_facts_match_the_canonical_lane_loop() -> None:
    for build in _REPORTS.values():
        report = build()
        expected = _canonical_report_facts(report)
        assert provider_lane_facts(report) == expected.providers
        assert library_report_facts(browse_report(report.to_json())) == expected
    lanes = provider_lane_facts(_real_tigers_corroboration_report())
    assert {lane.kind for lane in lanes} - {"none", "discovery"}


def _redigest(pair: Pair) -> Pair:
    case, report = pair
    case["target"] = report["target"]
    case["target_fingerprint"] = hashlib.sha256(
        canonical_json(report["target"]).encode("utf-8")
    ).hexdigest()
    return case, report


def _provider(report: dict[str, object]) -> dict[str, object]:
    return report["providers"][0]


def _first_lane(report: dict[str, object]) -> dict[str, object]:
    return next(lane for provider in report["providers"] for lane in provider["lanes"])


def _alternative(case: dict[str, object]) -> dict[str, object]:
    return next(
        alternative
        for result in case["results"]
        for alternative in result["alternatives"]
    )


def _title(pair: Pair) -> Pair:
    pair[1]["target"]["fields"][0]["value"] = 42
    return _redigest(pair)


def _accounting(pair: Pair) -> Pair:
    _provider(pair[1])["request_accounting"]["logical_requests"] += 1
    return pair


def _set(path: Callable[[Pair], dict[str, Any]], key: str, value: object):
    def change(pair: Pair) -> Pair:
        path(pair)[key] = value
        return pair

    return change


# Each mutation breaks one rendered value; each document stays canonical JSON
# inside its closed envelope, so only the field rule can refuse it.
_RENDERED: dict[str, tuple[str, Callable[[Pair], Pair]]] = {
    "target title": ("plain", _title),
    "provider status": (
        "decoration",
        _set(lambda pair: _provider(pair[1]), "status", "x"),
    ),
    "provider authority": (
        "decoration",
        _set(lambda pair: _provider(pair[1]), "authority", "elsewhere"),
    ),
    "provider query kind": (
        "decoration",
        _set(lambda pair: _provider(pair[1]), "query_kind", "x"),
    ),
    "provider accounting": ("decoration", _accounting),
    "lane kind": ("lanes", _set(lambda pair: _first_lane(pair[1]), "kind", "none")),
    "lane status": ("lanes", _set(lambda pair: _first_lane(pair[1]), "status", "x")),
    "file format": ("plain", _set(lambda pair: pair[1]["file"], "format", "pdf")),
    "file index": (
        "plain",
        _set(lambda pair: pair[1]["file"], "selected_index", True),
    ),
    "result outcome": (
        "plain",
        _set(lambda pair: pair[0]["results"][0], "outcome", "x"),
    ),
    "result reasons": (
        "plain",
        _set(lambda pair: pair[0]["results"][0], "reasons", ["Not A Reason"]),
    ),
    "evidence reference": (
        "decoration",
        _set(lambda pair: _alternative(pair[0]), "evidence_references", ["x"]),
    ),
}


def _mutated(name: str, change: Callable[[Pair], Pair]) -> tuple[str, str]:
    case_document, report_document = _documents(_REPORTS[name]())
    case, report = change((json.loads(case_document), json.loads(report_document)))
    return canonical_json(case), canonical_json(report)


@pytest.mark.parametrize("field", _RENDERED)
def test_a_malformed_rendered_value_refuses_exactly_as_canonical(field: str) -> None:
    case_document, report_document = _mutated(*_RENDERED[field])
    for read in (_case_and_report, _canonical_pair):
        with pytest.raises(ReviewWebError) as refused:
            read(case_document, report_document)
        assert refused.value.code == "unavailable"
    with pytest.raises(ReviewBrowseValueError) as bounded:
        browse_case_facts_and_report(case_document, report_document)
    # The field rule refused it, not the envelope or the lineage check.
    assert str(bounded.value) in {
        "review browse case is invalid",
        "review browse report is invalid",
    }
    for pages in (_pages, _canonical_pages):
        with pytest.raises(ReviewWebError) as refused:
            pages(case_document, report_document)
        assert refused.value.code == "unavailable"


def test_a_malformed_unrendered_value_is_the_admitted_difference() -> None:
    case_document, report_document = _documents(_REPORTS["audiobook"]())
    report = json.loads(report_document)
    candidate = _provider(report)["candidates"][0]
    candidate["identity"] = "not-an-identity"
    corrupted = canonical_json(report)
    with pytest.raises(ReviewWebError):
        _canonical_pair(case_document, corrupted)
    # The narrow read never opens the candidate, so the row renders exactly what
    # the canonical read rendered before the corruption.
    assert _pages(case_document, corrupted) == _canonical_pages(
        case_document, report_document
    )


def test_lineage_disagreement_still_refuses() -> None:
    case_document, report_document = _documents(_report())
    _, other = _documents(_report(title="Other"))
    for read in (_case_and_report, _canonical_pair):
        with pytest.raises(ReviewWebError, match="unavailable"):
            read(case_document, other)


def test_a_detail_read_parses_its_report_five_times(tmp_path: Path) -> None:
    fixture = OwnerResponsivenessFixture(tmp_path, 10, mixed=True)
    try:
        review_case_id = fixture.cases[2].review_case_id
        fixture.application.case_detail(review_case_id)
        original = MetadataReport.from_json.__func__
        callers: list[str] = []

        def counted(cls: type[MetadataReport], serialized: str) -> MetadataReport:
            import sys

            callers.append(sys._getframe(1).f_code.co_name)
            return original(cls, serialized)

        with patch.object(MetadataReport, "from_json", classmethod(counted)):
            fixture.application.case_detail(review_case_id)
    finally:
        fixture.close()
    # Down from 13 before #1191. What remains, by caller chain:
    # - `get_review_case`, through `_validated_report_row`: once for the
    #   snapshot's case and once more for `get_review_case_decisions`;
    # - `get_review_case_source_report`, through `_validated_report_row`, whose
    #   typed report the snapshot now carries to `review_web._read`;
    # - `current_cover_consideration_document`, in
    #   `compact_cover_consideration`;
    # - `project_for_review`, once, in `_validated_cover_consideration`.
    assert Counter(callers) == {
        "_validated_report_row": 3,
        "compact_cover_consideration": 1,
        "_validated_cover_consideration": 1,
    }
