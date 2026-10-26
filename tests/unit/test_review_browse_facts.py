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
from mechanical_librarian.domain.human_decision import HumanDecision, decision_actions
from mechanical_librarian.domain.identifiers import canonical_json
from mechanical_librarian.domain.metadata_report import (
    PROVIDER_AUTHORITIES,
    MetadataReport,
    ProviderLaneObservation,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    report_content_address,
)
from mechanical_librarian.domain.proposal_set import (
    evaluate_report,
    proposal_set_content_address,
)
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
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
)
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
#
# The posture the equality below holds to: with the content addresses
# verified, every rendered value is byte-identical to what the canonical writer
# validated at write time. What browse no longer re-checks per row is the
# internal-consistency rules the canonical writer enforces (format/audio
# section agreement, provider status/candidate agreement, inactive-provider
# inputs), non-canonical report and proposal text, and sub-documents the row
# never renders: those can only diverge through a
# canonical-writer defect or an out-of-band storage edit, and detail and every
# write path still parse canonically.
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
                lane.dispatch.outcome.value
                if type(lane) is ProviderLaneObservation and lane.dispatch is not None
                else None,
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


def _human_decisions(case_document: str | None) -> tuple[str, ...]:
    try:
        case = ReviewCase.from_json(cast(str, case_document))
    except (ReviewCaseValueError, TypeError):
        return ()  # A refused case has no decision a writer could have bound.
    return tuple(
        HumanDecision.create(
            case,
            proposal_id=str(proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=str(proposal["destination"]),
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key=f"browse-equality-{index:04d}",
            occurred_at=100 + index,
        ).to_json()
        for index, proposal in enumerate(
            [
                value
                for value in case.proposals
                if "keep_current" in decision_actions(value)
            ][:2]
        )
    )


def _library_rows(case_document: str | None, report_document: str):
    terminal = _terminal_document("unreadable_media", "track_3")
    decided = _human_decisions(case_document)
    return (
        (case_document, report_document, (), (), (), (), None, 2),
        (case_document, report_document, decided, (), (), (), None, 1),
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
        0,
        (None,) * len(library_rows),
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
    assert len(page["library"]["items"]) == 6
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


def _reseal(pair: Pair) -> Pair:
    """Give a changed pair the content addresses a canonical writer would."""
    case, report = _redigest(pair)
    report["report_id"] = report_content_address(
        {key: value for key, value in report.items() if key != "report_id"}
    )
    proposal = json.loads(case["proposal_document"])
    proposal["report"] = {
        "report_id": report["report_id"],
        "target_fingerprint": case["target_fingerprint"],
    }
    proposal["results"] = case["results"]
    case["report_id"] = report["report_id"]
    case["proposal_document"] = canonical_json(proposal)
    case["proposal_set_id"] = proposal_set_content_address(proposal)
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
    return pair


def _accounting(pair: Pair) -> Pair:
    _provider(pair[1])["request_accounting"]["logical_requests"] += 1
    return pair


def _set(path: Callable[[Pair], dict[str, Any]], key: str, value: object):
    def change(pair: Pair) -> Pair:
        path(pair)[key] = value
        return pair

    return change


# Each mutation breaks one rendered value and is then resealed, so both
# documents stay canonical JSON inside their closed envelopes and every content
# address and lineage fact agrees: the narrow read can refuse it only on the
# rendered field's own rule.
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


def _mutated(
    name: str, change: Callable[[Pair], Pair], *, reseal: bool = True
) -> tuple[str, str]:
    case_document, report_document = _documents(_REPORTS[name]())
    case, report = change((json.loads(case_document), json.loads(report_document)))
    if reseal:
        case, report = _reseal((case, report))
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
    # A reader refused it, not the envelope or the lineage check.
    assert str(bounded.value) in {
        "review browse case is invalid",
        "review browse report is invalid",
    }
    for pages in (_pages, _canonical_pages):
        with pytest.raises(ReviewWebError) as refused:
            pages(case_document, report_document)
        assert refused.value.code == "unavailable"


def _retitle(pair: Pair) -> Pair:
    pair[1]["target"]["fields"][0]["value"] = "Another Title"
    return _redigest(pair)


def _other_status(current: str) -> str:
    return next(status.value for status in ProviderStatus if status.value != current)


def _flip_status(read: Callable[[Pair], dict[str, Any]]):
    def change(pair: Pair) -> Pair:
        read(pair)["status"] = _other_status(read(pair)["status"])
        return pair

    return change


def _swap_authority(pair: Pair) -> Pair:
    provider = _provider(pair[1])
    provider["authority"] = sorted(
        cast("set[str]", PROVIDER_AUTHORITIES - {None, provider["authority"]})
    )[0]
    return pair


def _flip_media_kind(pair: Pair) -> Pair:
    proposal = json.loads(pair[0]["proposal_document"])
    proposal["media_kind"] = (
        "audiobook" if proposal["media_kind"] == "ebook" else "ebook"
    )
    pair[0]["proposal_document"] = canonical_json(proposal)
    return pair


# Each probe changes a rendered value to another well-formed one without
# resealing, so each document is canonical JSON inside its closed envelope but
# no longer carries the content address its writer gave it.
_INCONSISTENT: dict[str, tuple[str, Callable[[Pair], Pair]]] = {
    "retitled target": ("plain", _retitle),
    "file format": ("plain", _set(lambda pair: pair[1]["file"], "format", "m4b")),
    "provider status": ("decoration", _flip_status(lambda pair: _provider(pair[1]))),
    "lane status": ("lanes", _flip_status(lambda pair: _first_lane(pair[1]))),
    "provider authority": ("decoration", _swap_authority),
    "proposal media kind": ("plain", _flip_media_kind),
    "result reasons": (
        "plain",
        _set(lambda pair: pair[0]["results"][0], "reasons", ["Not A Reason"]),
    ),
    "case disposition": ("plain", _set(lambda pair: pair[0], "disposition", "decided")),
    "case relationships": (
        "plain",
        _set(lambda pair: pair[0]["relationships"], "bibliographic_combinations", [{}]),
    ),
}


@pytest.mark.parametrize("probe", _INCONSISTENT)
def test_a_well_formed_inconsistent_value_refuses_as_canonical(probe: str) -> None:
    name, change = _INCONSISTENT[probe]
    case_document, report_document = _mutated(name, change, reseal=False)
    for read in (_case_and_report, _canonical_pair):
        with pytest.raises(ReviewWebError) as refused:
            read(case_document, report_document)
        assert refused.value.code == "unavailable"
    for pages in (_pages, _canonical_pages):
        with pytest.raises(ReviewWebError) as refused:
            pages(case_document, report_document)
        assert refused.value.code == "unavailable"


def test_a_non_canonical_case_text_refuses_as_canonical() -> None:
    case_document, report_document = _documents(_report())
    spaced = json.dumps(json.loads(case_document), sort_keys=True, ensure_ascii=False)
    for read in (_case_and_report, _canonical_pair):
        with pytest.raises(ReviewWebError, match="unavailable"):
            read(spaced, report_document)


def test_a_malformed_unrendered_value_is_the_admitted_difference() -> None:
    case_document, report_document = _documents(_REPORTS["audiobook"]())

    def corrupt(pair: Pair) -> Pair:
        _provider(pair[1])["candidates"][0]["identity"] = "not-an-identity"
        return pair

    corrupted_case, corrupted = _mutated("audiobook", corrupt)
    with pytest.raises(ReviewWebError):
        _canonical_pair(corrupted_case, corrupted)
    # Resealed, the corruption is one only a canonical-writer defect or an
    # out-of-band edit could store. The narrow read never opens the candidate,
    # so the row renders what the canonical read rendered before it, under the
    # resealed case's identity.
    old_id = browse_case_facts_and_report(case_document, report_document)[0]
    new_id = browse_case_facts_and_report(corrupted_case, corrupted)[0]
    assert _pages(corrupted_case, corrupted) == _canonical_pages(
        case_document, report_document
    ).replace(old_id.review_case_id, new_id.review_case_id)


def test_lineage_disagreement_still_refuses() -> None:
    case_document, report_document = _documents(_report())
    _, other = _documents(_report(title="Other"))
    for read in (_case_and_report, _canonical_pair):
        with pytest.raises(ReviewWebError, match="unavailable"):
            read(case_document, other)


def test_a_detail_read_parses_its_report_four_times(tmp_path: Path) -> None:
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
    # Down from 13 before #1191, and from 5 before #1202. What remains, by
    # caller chain:
    # - `get_review_case`, through `_validated_report_row`, once for the
    #   snapshot's case, whose document `get_review_case_decisions` reuses;
    # - `get_review_case_source_report`, through `_validated_report_row`, whose
    #   typed report the snapshot now carries to `review_web._read`;
    # - `current_cover_consideration_document`, in
    #   `compact_cover_consideration`;
    # - `project_for_review`, once, in `_validated_cover_consideration`.
    assert Counter(callers) == {
        "_validated_report_row": 2,
        "compact_cover_consideration": 1,
        "_validated_cover_consideration": 1,
    }


def test_library_and_attention_reads_parse_each_document_once_in_the_store(
    tmp_path: Path,
) -> None:
    import sys

    from mechanical_librarian.domain import review_browse
    from tests.unit.test_report_browse_facts import _resolve_policies

    fixture = OwnerResponsivenessFixture(tmp_path, 10, mixed=True)
    try:
        repository = fixture.repository
        # Asked policies give a work several attentive fields: adjacent rows.
        _resolve_policies(repository, "books-1")
        parsed: Counter[str] = Counter()
        original = review_browse._object

        def counted(serialized: str, maximum: int) -> dict[str, object]:
            parsed[sys._getframe(1).f_code.co_name] += 1
            return original(serialized, maximum)

        with patch.object(review_browse, "_object", counted):
            library = repository.read_review_library(None, 50, 0, None)
            library_parses = dict(parsed)
            parsed.clear()
            attention = repository.read_review_attention(None, 50, 0)
    finally:
        fixture.close()
    # Library: the page read parses each row's report and case once, and the
    # row projection reads those envelopes rather than the bytes again (#1202).
    cases = sum(case is not None for case, *_ in library.rows)
    assert library_parses == {"browse_report": len(library.rows), "browse_case": cases}
    # Attention: a work's adjacent fields share one parse of its documents.
    works = len({(row[0], row[1]) for row in attention.rows})
    assert works < len(attention.rows)
    assert dict(parsed) == {"browse_case": works, "browse_report": works}
