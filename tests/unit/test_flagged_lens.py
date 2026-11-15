"""The Looks wrong lens over stored value flags (#1334).

Report insert derives each report's flags once, through the domain rules, and
the Library filters and counts them in SQL. The row's own findings come from
the same rules over the same report, and open refuses stored flags that
disagree with them.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import (
    REVIEW_LIBRARY_SCHEMA,
    BrowseReviewLibrary,
    ReviewWebError,
)
from mechanical_librarian.domain import value_flags as rules
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.review_cases import _report
from tests.support.review_web_doubles import (
    FakeReviewRepository,
    ReviewWebContext,
    review_web_context,
)

_PARTITION = (
    "needs_attention",
    "no_attention_needed",
    "not_matched",
    "preparation_failed",
)


def _titled_provider(index: int, title: str) -> ProviderObservation:
    """A provider candidate that states `title`, as D8 reads it."""
    candidate = f"edition-{index}"
    return ProviderObservation(
        "hardcover_primary",
        ProviderStatus.CANDIDATES,
        (),
        (
            ProviderCandidate(
                candidate,
                f"{candidate}-work",
                (
                    ProviderClaim.create(
                        provider="hardcover_primary",
                        candidate=candidate,
                        kind=ClaimKind.TITLE,
                        value=title,
                        state=FieldState.VALID,
                        scope=ClaimScope.WORK,
                        applicability="not_applicable",
                        raw_order=0,
                    ),
                ),
                (),
            ),
        ),
        authority="hardcover",
    )


def _work(
    index: int,
    title: str,
    *,
    authors: tuple[str, ...] = (),
    narrators: tuple[str, ...] = (),
    source_title: str | None = None,
) -> MetadataReport:
    """A synthetic ebook; with `source_title`, a provider states that title."""
    values = {
        TargetDestination.AUTHORS: authors,
        TargetDestination.NARRATORS: narrators,
    }
    fields = tuple(
        replace(field, state=TargetFieldState.VALUE, value=values[field.destination])
        if values.get(field.destination)
        else field
        for field in default_target_fields(title)
    )
    source = _report(actionable=False)
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", f"work-{index:04d}", fields),
        file=FileFacts(0, f"{index + 1:064x}", f"{index + 7001:064x}"),
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=(
            (ProviderObservation(f"provider-{index}", ProviderStatus.NO_MATCH),)
            if source_title is None
            else (_titled_provider(index, source_title),)
        ),
    )


# Each work's title, authors, narrators, source title, and expected findings.
_WORKS = (
    (0, "THE EXAMPLE STORY", (), (), None, (("title", "title_all_capitals"),)),
    (1, "the example story", (), (), "the example story", ()),
    (2, "a quiet example", (), (), None, (("title", "title_all_lower_case"),)),
    (
        3,
        "Example Story",
        ("Example Author",),
        ("Narrated by Example Reader",),
        None,
        (("narrators", "credit_label"),),
    ),
    (
        4,
        "Example Author",
        ("Example Author",),
        (),
        None,
        (("title", "title_is_author"),),
    ),
    (5, "Example Author", ("Example Author",), (), "Example Author", ()),
    (6, "Disc 01", (), (), None, (("title", "title_part_number"),)),
    (7, "Ordinary Example", (), (), None, ()),
    (8, "Example Story (CD 2)", (), (), None, (("title", "title_disc_number"),)),
)


def _publish(
    repository: SQLiteMetadataReportRepository, report: MetadataReport
) -> None:
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)


def _library(
    root: Path,
) -> tuple[SQLiteMetadataReportRepository, dict[str, MetadataReport]]:
    repository = open_repository(root / "application.sqlite3")
    reports: dict[str, MetadataReport] = {}
    for index, title, authors, narrators, source, _expected in _WORKS:
        report = _work(
            index, title, authors=authors, narrators=narrators, source_title=source
        )
        _publish(repository, report)
        reports[report.target.item] = report
    return repository, reports


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", page["items"])


def _stored(connection: sqlite3.Connection) -> dict[str, list[tuple[str, str]]]:
    rows: dict[str, list[tuple[str, str]]] = {}
    for item, destination, rule in connection.execute(
        "SELECT mr.item_id,vf.destination,vf.rule FROM metadata_report_value_flags vf "
        "JOIN metadata_reports mr ON mr.report_id=vf.report_id"
    ):
        rows.setdefault(item, []).append((destination, rule))
    return rows


def test_insert_writes_each_report_flags_and_the_row_names_the_same(
    tmp_path: Path,
) -> None:
    repository, _reports = _library(tmp_path)
    try:
        expected = {
            f"work-{index:04d}": list(flags) for index, *_rest, flags in _WORKS if flags
        }
        stored = _stored(repository._connection)
        order = {rule: position for position, rule in enumerate(rules.VALUE_FLAG_RULES)}
        assert {
            item: sorted(flags, key=lambda flag: order[flag[1]])
            for item, flags in stored.items()
        } == expected
        page = BrowseReviewLibrary(repository).library(
            lens="all", search=None, limit=50, cursor=None
        )
        rows = {
            cast(str, item["item"]): [
                (flag["destination"], flag["rule"])
                for flag in cast("list[dict[str, str]]", item["value_flags"])
            ]
            for item in _items(page)
        }
        assert {item: flags for item, flags in rows.items() if flags} == expected
        assert all(rows[item] == [] for item in rows if item not in expected)
        assert page["schema_version"] == REVIEW_LIBRARY_SCHEMA
        # A flag names a rule and a field, never a value or an identity.
        assert all(
            set(flag) == {"destination", "rule"}
            for item in _items(page)
            for flag in cast("list[dict[str, str]]", item["value_flags"])
        )
    finally:
        repository.close()


def test_the_lens_lists_flagged_works_in_all_order_and_every_answer_counts_it(
    tmp_path: Path,
) -> None:
    repository, _reports = _library(tmp_path)
    try:
        browse = BrowseReviewLibrary(repository)
        everything = browse.library(lens="all", search=None, limit=50, cursor=None)
        flagged = browse.library(lens="flagged", search=None, limit=50, cursor=None)
        assert flagged["lens"] == "flagged"
        assert [item["item"] for item in _items(flagged)] == [
            item["item"] for item in _items(everything) if item["value_flags"]
        ]
        assert len(_items(flagged)) == 6
        counts = cast(dict[str, int], everything["counts"])
        assert counts["flagged"] == 6
        # The four lenses still partition All, and every answer counts alike.
        assert counts["all"] == sum(counts[lens] for lens in _PARTITION)
        for lens in ("all", "flagged", *_PARTITION):
            assert (
                browse.library(lens=lens, search=None, limit=1, cursor=None)["counts"]
                == counts
            ), lens
        seen: set[str] = set()
        for lens in _PARTITION:
            items = {
                cast(str, item["item"])
                for item in _items(
                    browse.library(lens=lens, search=None, limit=50, cursor=None)
                )
            }
            assert not items & seen
            seen |= items
        assert seen == {cast(str, item["item"]) for item in _items(everything)}
        assert browse.progress()["totals"] == counts
    finally:
        repository.close()


def test_a_cursor_is_bound_to_the_flagged_lens_and_search_narrows_it(
    tmp_path: Path,
) -> None:
    repository, _reports = _library(tmp_path)
    try:
        browse = BrowseReviewLibrary(repository)
        first = browse.library(lens="flagged", search=None, limit=4, cursor=None)
        cursor = first["next_cursor"]
        assert type(cursor) is str
        second = browse.library(lens="flagged", search=None, limit=4, cursor=cursor)
        assert len(_items(first)) + len(_items(second)) == 6
        assert not {item["item"] for item in _items(first)} & {
            item["item"] for item in _items(second)
        }
        for lens in ("all", "needs_attention"):
            with pytest.raises(ReviewWebError):
                browse.library(lens=lens, search=None, limit=4, cursor=cursor)
        searched = browse.library(
            lens="flagged", search="example", limit=50, cursor=None
        )
        assert {item["item"] for item in _items(searched)} == {
            "work-0000",
            "work-0002",
            "work-0003",
            "work-0004",
            "work-0008",
        }
        assert cast(dict[str, int], searched["counts"])["flagged"] == 5
    finally:
        repository.close()


def test_a_work_that_could_not_finish_has_no_report_and_no_flag(
    tmp_path: Path,
) -> None:
    (tmp_path / "catalogue").mkdir(mode=0o700)
    catalogue = open_catalogue(tmp_path / "catalogue" / "catalogue.sqlite", create=True)
    entry = CatalogueEntry(
        "primary",
        "fiction",
        "lib-1",
        "terminal-item",
        1,
        "STOPPED TERMINAL TITLE",
        ("Narrated by Example Reader",),
        True,
        False,
        "singular-supported",
        "1" * 64,
        "/synthetic.epub",
        "epub",
        "/media",
    )
    catalogue.acquire_refresh()
    generation = catalogue.publish("a" * 64, (entry,))
    repository = open_repository(tmp_path / "review.sqlite")
    try:
        repository.bind_library_catalogue(
            tmp_path / "catalogue" / "catalogue.sqlite", "a" * 64
        )
        repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        repository.complete_review_discovery(
            discovery, generation, "a" * 64, (entry,), 12
        )
        action = repository.claim_review_pipeline_action(13)
        assert action is not None
        repository.fail_review_pipeline_action(
            action, "unreadable_media", False, 14, "track_1"
        )
        browse = BrowseReviewLibrary(repository)
        page = browse.library(lens="all", search=None, limit=50, cursor=None)
        (item,) = _items(page)
        assert item["attention_state"] == "preparation_failed"
        assert item["value_flags"] == []
        assert cast(dict[str, int], page["counts"])["flagged"] == 0
        assert (
            _items(browse.library(lens="flagged", search=None, limit=50, cursor=None))
            == []
        )
    finally:
        repository.close()
        catalogue.close()


@pytest.mark.parametrize(
    "tamper",
    (
        "DELETE FROM metadata_report_value_flags WHERE rule='title_all_capitals'",
        "UPDATE metadata_report_value_flags SET rule='title_announcement' "
        "WHERE rule='title_all_capitals'",
        "INSERT INTO metadata_report_value_flags(report_id,destination,rule) "
        "SELECT report_id,'title','title_announcement' FROM metadata_reports "
        "WHERE item_id='work-0007'",
    ),
)
def test_open_refuses_a_flag_row_that_disagrees_with_the_rules(
    tmp_path: Path, tamper: str
) -> None:
    repository, _reports = _library(tmp_path)
    repository.close()
    database = tmp_path / "application.sqlite3"
    connection = sqlite3.connect(database)
    assert connection.execute(tamper).rowcount == 1
    connection.commit()
    connection.close()
    with pytest.raises(
        MetadataReportError, match="^database is unavailable or incompatible$"
    ):
        open_repository(database, create=False)


def test_open_refuses_flags_derived_by_another_rule_list(tmp_path: Path) -> None:
    repository, _reports = _library(tmp_path)
    repository.close()
    database = tmp_path / "application.sqlite3"
    connection = sqlite3.connect(database)
    assert connection.execute(
        "SELECT value_flag_rules FROM browse_fact_identity"
    ).fetchall() == [("value-flag-rules-v1",)]
    connection.execute(
        "UPDATE browse_fact_identity SET value_flag_rules='value-flag-rules-v0'"
    )
    connection.commit()
    connection.close()
    before = database.read_bytes()
    with pytest.raises(
        MetadataReportError,
        match="^database value flags were derived under a different rule list; "
        "a clean rebuild is required$",
    ):
        open_repository(database, create=False)
    assert database.read_bytes() == before


def test_a_changed_rule_makes_the_row_differ_from_the_stored_flags_and_open_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _reports = _library(tmp_path)
    repository.close()
    database = tmp_path / "application.sqlite3"
    with monkeypatch.context() as patched:
        # Capitals stop counting, in-process, under the same version.
        patched.setattr(rules, "single_case", lambda _value: None)
        with pytest.raises(
            MetadataReportError, match="^database is unavailable or incompatible$"
        ):
            open_repository(database, create=False)
    open_repository(database, create=False).close()


def test_a_changed_rule_names_different_row_findings_than_the_stored_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _reports = _library(tmp_path)
    try:
        stored = _stored(repository._connection)
        assert stored["work-0000"] == [("title", "title_all_capitals")]
        with monkeypatch.context() as patched:
            patched.setattr(rules, "single_case", lambda _value: None)
            page = BrowseReviewLibrary(repository).library(
                lens="flagged", search=None, limit=50, cursor=None
            )
        row = next(item for item in _items(page) if item["item"] == "work-0000")
        # The lens still selects by the stored row; the row's own finding,
        # recomputed under the patched rule, no longer agrees with it.
        assert row["value_flags"] == []
    finally:
        repository.close()


def test_reselecting_a_report_refuses_flag_rows_that_disagree(tmp_path: Path) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database)
    historical = _work(0, "THE EXAMPLE STORY")
    successor = _work(0, "The Example Story")
    _publish(repository, historical)
    repository.publish(successor, lambda: True, historical.report_id)
    proposal = evaluate_report(successor)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    repository.close()
    connection = sqlite3.connect(database)
    assert (
        connection.execute(
            "DELETE FROM metadata_report_value_flags WHERE report_id=?",
            (historical.report_id,),
        ).rowcount
        == 1
    )
    connection.commit()
    connection.close()
    # The history is unselected, so open accepts it ...
    repository = open_repository(database, create=False)
    try:
        # ... until a publication selects it again.
        with pytest.raises(
            MetadataReportError, match="^database is unavailable or incompatible$"
        ):
            repository.publish(historical, lambda: True, successor.report_id)
    finally:
        repository.close()


def test_a_connection_without_the_flag_function_writes_and_validates_nothing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    open_repository(database).close()
    report = _work(0, "THE EXAMPLE STORY")
    document = report.to_json()
    row = (
        report.report_id,
        report.target.connection,
        report.target.library,
        report.target.item,
        report.file.identity,
        report.file.content_digest,
        report.schema_version,
        hashlib.sha256(document.encode()).hexdigest(),
        document,
    )
    insert = (
        "INSERT INTO metadata_reports(report_id,connection_name,library_name,item_id,"
        "file_identity,content_digest,schema_version,document_digest,document) "
        "VALUES(?,?,?,?,?,?,?,?,?)"
    )
    connection = sqlite3.connect(database)
    try:
        # The fold alone is not enough: the trigger also needs the flags.
        connection.create_function(
            "ml_casefold", 1, schema._review_casefold, deterministic=True
        )
        with pytest.raises(sqlite3.OperationalError, match="ml_value_flags"):
            connection.execute(insert, row)
        assert connection.execute(
            "SELECT (SELECT count(*) FROM metadata_reports),"
            "(SELECT count(*) FROM metadata_report_browse_facts),"
            "(SELECT count(*) FROM metadata_report_value_flags)"
        ).fetchone() == (0, 0, 0)
        connection.rollback()
        schema._register_casefold(connection)
        connection.execute(insert, row)
        connection.commit()
        assert connection.execute(
            "SELECT destination,rule FROM metadata_report_value_flags"
        ).fetchall() == [("title", "title_all_capitals")]
    finally:
        connection.close()
    unflagged = sqlite3.connect(database)
    try:
        unflagged.create_function(
            "ml_casefold", 1, schema._review_casefold, deterministic=True
        )
        with pytest.raises(sqlite3.OperationalError, match="ml_value_flags"):
            schema._validate_review_field_facts(unflagged)
    finally:
        unflagged.close()


@pytest.mark.parametrize("argument", (None, 1, b"[]"))
def test_the_flag_function_refuses_anything_but_text(argument: object) -> None:
    with pytest.raises(ValueError):
        schema._review_value_flags(argument, "[]")
    with pytest.raises(ValueError):
        schema._review_value_flags("{}", argument)


def test_the_table_refuses_a_credit_label_on_the_title(tmp_path: Path) -> None:
    repository, _reports = _library(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            repository._connection.execute(
                "INSERT INTO metadata_report_value_flags(report_id,destination,rule) "
                "SELECT report_id,'title','credit_label' FROM metadata_reports LIMIT 1"
            )
        with pytest.raises(sqlite3.IntegrityError):
            repository._connection.execute(
                "INSERT INTO metadata_report_value_flags(report_id,destination,rule) "
                "SELECT report_id,'subtitle','title_all_capitals' FROM metadata_reports "
                "LIMIT 1"
            )
    finally:
        repository._connection.rollback()
        repository.close()


@pytest.fixture
def web() -> Iterator[ReviewWebContext[FakeReviewRepository]]:
    yield from review_web_context(FakeReviewRepository)


def test_the_served_lens_agrees_with_the_store_double(
    web: ReviewWebContext[FakeReviewRepository],
) -> None:
    """The HTTP surface serves Looks wrong through its closed models."""
    for index, title, authors, narrators, source, _expected in _WORKS:
        web.repository.browse_without_cases.append(
            _work(
                index, title, authors=authors, narrators=narrators, source_title=source
            )
        )
    web.login()
    served = web.client.get("/api/v1/library?lens=flagged")
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["schema_version"] == REVIEW_LIBRARY_SCHEMA
    assert body["lens"] == "flagged"
    expected = {f"work-{index:04d}": flags for index, *_rest, flags in _WORKS if flags}
    assert {
        item["item"]: tuple(
            (flag["destination"], flag["rule"]) for flag in item["value_flags"]
        )
        for item in body["items"]
    } == expected
    assert body["counts"]["flagged"] == len(expected)
    everything = web.client.get("/api/v1/library?lens=all").json()
    assert everything["counts"] == body["counts"]
    assert web.client.get("/api/v1/library?lens=looks_wrong").status_code == 400
