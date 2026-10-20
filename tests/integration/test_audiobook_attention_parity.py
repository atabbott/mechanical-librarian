"""A recording joins the attention queue under exactly an ebook's conditions.

#1249 removed the one audiobook-specific reason the shared attention predicate
held, so the persisted facts, the Library row, the Attention page and its count
are now medium-blind apart from the fields an EPUB never carries. A recording's
settled fill still stays choosable in its case, because only the queue moved.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
)
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    InspectCurrentReviewCase,
)
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    MetadataReport,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    corroborated_audiobook_report,
    publish_audiobook_preview,
)
from tests.support.proposal_evidence import _claim, _provider, _report
from tests.unit.test_browse_attention_statements import _shadow_outcome

_FACTS_SQL = (
    "SELECT connection_name,library_name,item_id,report_id,destination,"
    "cover_consideration_id,cover_policy_reference,cover_refresh_admits "
    "FROM review_attention_facts"
)
_EPUB_OMITTED = frozenset({"narrators", "explicit"})


def _facts(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    facts = sorted(connection.execute(_FACTS_SQL).fetchall(), key=repr)
    assert facts == sorted(
        connection.execute(_ATTENTION_FACT_SOURCE_SQL).fetchall(), key=repr
    )
    return facts


def _as_medium(
    repository: SQLiteMetadataReportRepository, case_id: str, medium: str
) -> None:
    """Restate the case's report browse format; the triggers refresh its facts."""
    assert (
        repository._connection.execute(
            "UPDATE metadata_report_browse_facts SET format=? WHERE report_id="
            "(SELECT report_id FROM review_cases WHERE review_case_id=?)",
            (medium, case_id),
        ).rowcount
        == 1
    )


def _field_facts(
    repository: SQLiteMetadataReportRepository,
    case_id: str,
    destinations: tuple[str, ...],
    assignment: str,
) -> None:
    for destination in destinations:
        assert (
            repository._connection.execute(
                f"UPDATE review_case_field_facts SET {assignment} "
                "WHERE review_case_id=? AND destination=?",
                (case_id, destination),
            ).rowcount
            == 1
        ), destination


def _safety_refusal(
    repository: SQLiteMetadataReportRepository, case_id: str, targets: tuple[str, ...]
) -> None:
    _shadow_outcome(
        repository,
        case_id,
        tuple((destination, "applied", "automatic") for destination in targets),
    )
    assert (
        repository._connection.execute(
            "UPDATE automatic_safety_state SET paused=1"
        ).rowcount
        == 1
    )


# Each condition, and the fields it asks about. The policy Ask check names
# series and never narrators or ASIN, so that condition asks about series.
_CONDITIONS = {
    "unresolved_choice": (
        ("asin", "narrators"),
        lambda repository, case_id, targets: _field_facts(
            repository, case_id, targets, "unresolved_choice=1"
        ),
    ),
    "policy_ask": (
        ("series",),
        lambda repository, case_id, targets: repository._connection.execute(
            "UPDATE metadata_policy_resolutions SET document=json_set(document,"
            "'$.destinations.series','ask')"
        ),
    ),
    "abstention": (
        ("asin", "narrators"),
        lambda repository, case_id, targets: _shadow_outcome(
            repository,
            case_id,
            tuple((destination, "abstention", "ask") for destination in targets),
        ),
    ),
    "safety_refusal": (("asin", "narrators"), _safety_refusal),
    "manual_bibliographic": (
        ("asin", "narrators"),
        lambda repository, case_id, targets: _field_facts(
            repository, case_id, targets, "manual_bibliographic_attention=1"
        ),
    ),
}


def _condition_facts(
    directory: Path, medium: str, condition: str
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    """Facts before and after one condition, on one medium of one recording."""
    directory.mkdir(mode=0o700)
    with closing(open_repository(directory / "application.sqlite3")) as repository:
        # Series goes to Automatic so that nothing asks before the condition.
        case_id, _ = publish_audiobook_preview(
            repository, corroborated_audiobook_report(), ("series",)
        )
        connection = repository._connection
        # The conditions write the rows a real projection would, without the
        # parents a real write would also create; facts read none of them.
        connection.execute("PRAGMA foreign_keys=OFF")
        _as_medium(repository, case_id, medium)
        before = _facts(connection)
        targets, apply = _CONDITIONS[condition]
        apply(repository, case_id, targets)
        after = _facts(connection)
        connection.commit()
    return before, after


@pytest.mark.parametrize("condition", sorted(_CONDITIONS))
def test_each_attention_condition_holds_alike_on_both_media(
    tmp_path: Path, condition: str
) -> None:
    recording = _condition_facts(tmp_path / "m4b", "m4b", condition)
    ebook = _condition_facts(tmp_path / "epub", "epub", condition)
    targets, _ = _CONDITIONS[condition]

    assert recording[0] == ebook[0] == []
    assert {row[4] for row in recording[1]} == set(targets)
    assert ebook[1] == [row for row in recording[1] if row[4] not in _EPUB_OMITTED]
    assert {row[4] for row in ebook[1]} == set(targets) - _EPUB_OMITTED


def _ebook_asin_report() -> MetadataReport:
    """An ebook whose one provider states an ebook ASIN the target lacks."""
    return _report(
        providers=(
            _provider(
                "catalogue",
                "edition-1",
                (
                    _claim(
                        "catalogue",
                        "edition-1",
                        ClaimKind.ASIN,
                        "B012345678",
                        scope=ClaimScope.EDITION,
                        applicability="ebook",
                    ),
                ),
            ),
        )
    )


@pytest.mark.parametrize("medium", ("m4b", "epub"))
def test_a_settled_asin_fill_leaves_the_queue_and_stays_choosable(
    tmp_path: Path, medium: str
) -> None:
    """Under all-Ask a single-candidate ASIN fill asks for nothing.

    The queue reads alike on both media, yet a recording's case still offers
    the fill to the owner (#1249).
    """
    report = admitted_audiobook_report() if medium == "m4b" else _ebook_asin_report()
    with closing(open_repository(tmp_path / "application.sqlite3")) as repository:
        # Activation needs one Automatic option; isbn forms no field here.
        case_id, _ = publish_audiobook_preview(repository, report, ("isbn_form",))
        browser = BrowseReviewLibrary(repository)
        detail = InspectCurrentReviewCase(repository).run(case_id)

        assert report.file.format == medium
        assert "asin" not in {row[4] for row in _facts(repository._connection)}
        attention = browser.attention(search=None, limit=50, cursor=None)
        items = attention["items"]
        assert type(items) is list
        assert "asin" not in {item["destination"] for item in items}
        rows = browser.library(lens="all", search=None, limit=50, cursor=None)["items"]
        assert type(rows) is list
        (row,) = rows
        assert row["attention_count"] == attention["total"]
        assert attention["total"] == repository.count_current_attention_fields(None)
        assert attention["total"] == len(items)
        asin = next(f for f in detail["fields"] if f["destination"] == "asin")
        assert asin["deterministic_disposition"] == "propose_change"
        assert asin["decision_actions"] == ["keep_current", "accept"]
        assert attention["total"] == 0
        assert row["attention_state"] == "no_attention_needed"
        # The case page keeps a recording's undecided field the owner's: the
        # stated gap between that page and its Library row (#1249). An
        # ebook's waits for its policy exactly as before.
        assert asin["editable"] is (medium == "m4b")
        assert asin["needs_attention"] is (medium == "m4b")


def test_a_policy_decided_asin_fill_has_no_fact_on_either_medium(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        frozenset({audiobook_decision_qualification.F46_ASIN_IDENTITY}),
    )
    monkeypatch.setattr(
        audiobook_decision_qualification, "F46_QUALIFICATION_MANIFEST_SHA256", "7" * 64
    )
    with closing(open_repository(tmp_path / "application.sqlite3")) as repository:
        case_id, _ = publish_audiobook_preview(
            repository, admitted_audiobook_report(), ("asin",)
        )
        repository.persist_shadow_outcome(case_id, 4)
        connection = repository._connection
        assert [("asin",)] == connection.execute(
            "SELECT destination FROM automatic_decisions"
        ).fetchall()

        assert "asin" not in {row[4] for row in _facts(connection)}
        _as_medium(repository, case_id, "epub")
        assert "asin" not in {row[4] for row in _facts(connection)}
        connection.rollback()
