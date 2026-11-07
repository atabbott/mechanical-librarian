"""The offline Hardcover dispatch replay (#1380) over synthetic stores."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest
from scripts.hardcover_dispatch_replay import SCHEMA, main, replay

_TITLE = "Synthetic Replay Title"
_ISBN = "9780306406157"


def _claim(kind: str, canonical: str, basis: str, role: str | None = None):
    return {
        "kind": kind,
        "basis": basis,
        "role": role,
        "state": "valid",
        "canonical": canonical,
        "reference": f"local_{kind}_{canonical}",
    }


def _lane(
    kind: str, status: str, *editions: str, work: str = "hardcover-work-1"
) -> dict[str, object]:
    return {
        "kind": kind,
        "status": status,
        "candidates": [
            {
                "reference": f"lane_candidate_{edition}",
                "edition_identity": edition,
                "work_identity": work,
            }
            for edition in editions
        ],
    }


def _document(
    *,
    authors: list[str] | None = None,
    claims: list[dict[str, object]] | None = None,
    lanes: list[dict[str, object]] | None = None,
    initial: int = 1,
    candidates: list[dict[str, object]] | None = None,
    evidence: list[dict[str, object]] | None = None,
    media: str = "epub",
) -> dict[str, object]:
    return {
        "file": {"format": media},
        "target": {
            "fields": [
                {"destination": "title", "state": "value", "value": _TITLE},
                (
                    {"destination": "authors", "state": "value", "value": authors}
                    if authors
                    else {"destination": "authors", "state": "empty", "value": None}
                ),
            ]
        },
        "local": {
            "claims": [
                _claim("title", _TITLE, "target_metadata"),
                *(claims or ()),
            ]
        },
        "providers": [
            {
                "authority": "hardcover",
                "provider": "hardcover-main",
                "status": "candidates",
                "edition_enrichment_overflow": None,
                "request_accounting": {
                    "initial_dispatches": initial,
                    "undispatched_incomplete": 0,
                },
                "lanes": lanes if lanes is not None else [_lane("work", "no_match")],
                "candidates": candidates or [],
            }
        ],
        "identifier_evidence": evidence or [],
    }


def _store(*documents: dict[str, object]) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    _fill(connection, documents)
    return connection


def _fill(
    connection: sqlite3.Connection, documents: tuple[dict[str, object], ...]
) -> None:
    connection.execute(
        "CREATE TABLE metadata_reports (report_id TEXT PRIMARY KEY, document TEXT)"
    )
    connection.execute("CREATE TABLE current_metadata_reports (report_id TEXT)")
    for index, document in enumerate(documents):
        connection.execute(
            "INSERT INTO metadata_reports VALUES (?, ?)",
            (f"report_{index}", json.dumps(document)),
        )
        connection.execute(
            "INSERT INTO current_metadata_reports VALUES (?)", (f"report_{index}",)
        )
    connection.commit()


def _ebook(result: dict[str, object]) -> dict[str, object]:
    return cast("dict[str, object]", result["ebook"])


def test_the_output_is_aggregate_only() -> None:
    result = replay(
        _store(
            _document(
                authors=["Ada Example"],
                claims=[_claim("isbn", _ISBN, "opening_matter")],
                lanes=[
                    _lane("isbn", "candidates", "hardcover-edition-1"),
                    _lane("work", "no_match"),
                ],
                initial=2,
            )
        )
    )
    text = json.dumps(result)

    assert SCHEMA == result["schema"]
    assert {"read": 1, "unreadable": 0, "ebooks": 1, "audiobooks": 0} == result[
        "reports"
    ]
    assert {"total": 2, "distribution": {"2": 1}} == _ebook(result)[
        "planned_initial_dispatches"
    ]
    for private in (_TITLE, "Ada Example", _ISBN, "hardcover-edition-1", "report_0"):
        assert private not in text


def test_a_report_missing_a_key_path_is_unreadable_not_converted() -> None:
    broken = _document(authors=["Ada Example"])
    del cast("dict[str, object]", broken["local"])["claims"]

    result = replay(_store(broken, _document(authors=["Ada Example"])))

    assert {"read": 2, "unreadable": 1, "ebooks": 1, "audiobooks": 0} == result[
        "reports"
    ]


def test_a_recorded_work_lane_removed_counts_differently() -> None:
    claims = [_claim("isbn", _ISBN, "opening_matter")]
    answered = _lane("isbn", "candidates", "hardcover-edition-1")
    kept = replay(
        _store(
            _document(
                authors=["Ada Example"],
                claims=claims,
                lanes=[answered, _lane("work", "no_match")],
                initial=2,
            )
        )
    )
    removed = replay(
        _store(
            _document(
                authors=["Ada Example"], claims=claims, lanes=[answered], initial=2
            )
        )
    )

    assert (
        2 == cast("dict[str, int]", _ebook(kept)["planned_initial_dispatches"])["total"]
    )
    assert (
        1
        == cast("dict[str, int]", _ebook(removed)["planned_initial_dispatches"])[
            "total"
        ]
    )
    assert 1 == _ebook(removed)["work_lanes_suppressed_as_recorded"]


def _exit(tmp_path: Path, *documents: dict[str, object]) -> tuple[int, dict]:
    database = tmp_path / "synthetic.sqlite3"
    connection = sqlite3.connect(database)
    _fill(connection, documents)
    connection.close()
    output = io.StringIO()
    status = main(["--database", str(database)], output)
    return status, json.loads(output.getvalue())


def test_an_ebook_planning_more_than_its_baseline_breaches_the_bound(
    tmp_path: Path,
) -> None:
    status, result = _exit(tmp_path, _document(authors=["Ada Example"], initial=0))

    assert 1 == status
    assert False is _ebook(result)["within_bound"]
    assert {"epub_fallback": 0, "other": 1} == _ebook(result)["increased_ebooks"]


def test_an_epub_fallback_ebook_may_plan_more_than_its_baseline(
    tmp_path: Path,
) -> None:
    fallback = _document(
        claims=[
            _claim("creator", "Ada Example", "package_metadata", "aut"),
            _claim("creator", "Cy Example", "package_metadata", "trl"),
        ],
        lanes=[],
        initial=0,
    )
    status, result = _exit(tmp_path, fallback, _document(authors=["Bea"], initial=2))

    assert 0 == status
    assert {"epub_fallback": 1, "other": 0} == _ebook(result)["increased_ebooks"]
    assert {"ebooks": 1, "added_initial_dispatches": 1} == _ebook(result)[
        "epub_fallback"
    ]


@pytest.mark.parametrize(
    ("authors", "removed"),
    ((["Ada Example"], 1), (["Example, Ada"], 0), (["Ada Example", "Bea"], 0)),
    ids=("single", "inverted_single", "two_authors"),
)
def test_a_retry_is_removed_only_when_it_would_resend_the_same_list(
    authors: list[str], removed: int
) -> None:
    result = replay(
        _store(
            _document(
                authors=authors, lanes=[_lane("work", "candidate_limit_exceeded")]
            )
        )
    )

    assert {
        "lower_bound": removed,
        "recorded_candidate_limit_work_lanes": 1,
    } == _ebook(result)["narrowing_retries_removed"]


def test_witnesses_edition_claims_and_q5_are_counted() -> None:
    candidates: list[dict[str, object]] = [
        {
            "identity": "hardcover-edition-2",
            "claims": [{"scope": "edition"}, {"scope": "work"}],
        }
    ]
    evidence: list[dict[str, object]] = [
        {"provider": "hardcover-main", "candidate_identity": "hardcover-edition-2"},
        {"provider": None, "candidate_identity": None},
    ]
    result = replay(
        _store(
            _document(
                authors=["Ada Example"],
                claims=[_claim("isbn", _ISBN, "opening_matter")],
                lanes=[
                    _lane("isbn", "candidate_limit_exceeded"),
                    _lane("work", "candidates", "hardcover-edition-2"),
                ],
                initial=2,
                candidates=candidates,
                evidence=evidence,
            )
        )
    )
    ebook = _ebook(result)

    assert 1 == ebook["isbn_lane_overflow_witnesses"]
    assert 1 == ebook["ebooks_losing_work_lane_edition_claim"]
    assert 1 == ebook["q5_reports_losing_work_lane_neutral_isbn_evidence"]
    assert ebook["q6_work_lane_refusals_only_by_edition_title_variant_veto"] is None
    assert {"observations_with_accepted_book": 1, "maximum_added_requests": 4} == (
        ebook["edition_fetch_ceiling"]
    )


def test_an_audiobook_overflow_removes_no_narrowing_retry() -> None:
    result = replay(
        _store(
            _document(
                authors=["Ada Example"],
                lanes=[_lane("work", "candidate_limit_exceeded")],
                media="m4b",
            )
        )
    )

    assert {
        "plan_rebuilt": False,
        "recorded_work_lanes": 1,
        "narrowing_retries_removed": {
            "lower_bound": 0,
            "recorded_candidate_limit_work_lanes": 1,
        },
    } == result["audiobook"]
