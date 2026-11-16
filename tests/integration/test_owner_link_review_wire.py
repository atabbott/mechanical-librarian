"""A linked report's sources reach the review wire (#1477, increment 2).

`GenerateMetadataReport` with real adapters over synthetic answers prepares a
work the owner linked, then the report detail and the case evidence ledger
project its lanes through `_provider_outcomes`. The `link` lane kind and the
anchored WORK lane validate against the response models, and each lane says
what the link's read found. Dry proof D5 showed the same report refused at
c2b7a54; the unlinked control validated there and still does.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from mechanical_librarian.application.metadata_report import ProviderBinding
from mechanical_librarian.application.review_web import (
    REVIEW_CASE_EVIDENCE_SCHEMA,
    _provider_outcomes,
    _report_detail,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.review_browse import (
    library_report_facts,
    provider_lane_facts,
)
from mechanical_librarian.interfaces.web.app import (
    _EvidenceResponse,
    _ReportDetailResponse,
)
from tests.integration import test_owner_link_preparation as prep
from tests.support.hardcover_evidence import books_envelope

_ANCHORED = "Returned possible matches for the linked record's title and authors."


def _sources(report: MetadataReport) -> list[dict[str, object]]:
    """The lanes as the report detail and the evidence ledger state them."""
    detail = _report_detail(report.to_data(), report)
    _ReportDetailResponse.model_validate(detail)
    ledger = _provider_outcomes(provider_lane_facts(report))
    _EvidenceResponse.model_validate(
        {
            "items": [],
            "missing_lineage_count": 0,
            "next_cursor": None,
            "review_case_id": "review_case_" + "c" * 64,
            "schema_version": REVIEW_CASE_EVIDENCE_SCHEMA,
            "source_ledger": ledger,
        }
    )
    assert detail["provider_sources"] == ledger
    # The stored report's browse reader states the same lanes as the parsed
    # report's, so Library says what the detail says.
    assert library_report_facts(report.to_data()).providers == provider_lane_facts(
        report
    )
    return ledger


def _lanes(report: MetadataReport) -> set[tuple[object, ...]]:
    return {
        (value["provider"], value["lane_kind"], value["anchored"], value["reason"])
        for value in _sources(report)
    }


def test_a_work_link_and_its_anchored_search_reach_the_wire(tmp_path: Path) -> None:
    report, _, _ = prep._work_link_report(
        tmp_path,
        lambda: prep._json(books_envelope(prep._linked_book())),
        lambda _: prep._volume(prep.LINKED_TITLE, prep.LINKED_AUTHOR),
    )
    assert _lanes(report) == {
        ("Hardcover", "link", False, "Read the linked record."),
        ("Google Books", "work", True, _ANCHORED),
    }


def test_an_unlinked_work_s_search_is_not_anchored(tmp_path: Path) -> None:
    """Control: the same answer to an unlinked work reads as it always did."""
    path = prep._store(tmp_path)
    report = prep._prepare_ebook(
        path,
        (
            ProviderBinding(
                "google_books",
                True,
                lambda: prep._google(
                    [], lambda _: prep._volume(prep.LOCAL_TITLE, prep.LOCAL_AUTHOR)
                ),
                authority="google_books",
            ),
        ),
    )
    assert report.owner_link is None
    assert _lanes(report) == {
        ("Google Books", "work", False, "Returned possible matches to compare."),
    }


@pytest.mark.parametrize(
    ("books", "reason"),
    [
        pytest.param([], "No longer has the linked record.", id="missing"),
        pytest.param(
            [{"id": 4321, "title": "Old", "canonical_id": 99}],
            "Merged the linked record into another one.",
            id="merged",
        ),
    ],
)
def test_a_link_the_source_no_longer_has_says_so(
    tmp_path: Path, books: list[dict[str, object]], reason: str
) -> None:
    report, _, _ = prep._work_link_report(
        tmp_path, lambda: prep._json(books_envelope(books))
    )
    # No anchor was read, so no other source was asked.
    assert _lanes(report) == {
        ("Hardcover", "link", False, reason),
        (
            "Google Books",
            "none",
            False,
            "This work lacks the identifiers this source needs.",
        ),
    }


def test_a_link_the_source_did_not_answer_keeps_the_failure_reason(
    tmp_path: Path,
) -> None:
    report, _, _ = prep._work_link_report(tmp_path, lambda: httpx.Response(502))
    ((kind, anchored, reason),) = {
        lane[1:] for lane in _lanes(report) if lane[0] == "Hardcover"
    }
    assert (kind, anchored, reason) == ("link", False, "Did not answer.")


@pytest.mark.parametrize(
    ("physical_format", "reason"),
    [
        pytest.param("eBook", "Read the linked record.", id="ebook-edition"),
        pytest.param(
            "Hardcover",
            "The linked record no longer matches this work's edition.",
            id="print-edition",
        ),
    ],
)
def test_an_edition_link_s_isbn_lane_says_what_the_re_check_found(
    tmp_path: Path, physical_format: str, reason: str
) -> None:
    path = prep._store(tmp_path)
    prep._link(path, "ebooks", "ebook", prep.EDITION_LINK)
    report = prep._prepare_ebook(
        path,
        (
            ProviderBinding(
                "open_library",
                True,
                lambda: prep._open_library([], physical_format),
                authority="open_library",
            ),
        ),
    )
    lanes = _lanes(report)
    assert ("Open Library", "isbn", False, reason) in lanes
