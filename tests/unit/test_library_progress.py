"""Library progress: Library's counts and Attention's fields per library (#1335).

The progress read counts works over the same candidate relation as the Library
page, with each lens's own condition, and fields over Attention's own count
statement, so its totals are Library's `counts` and its fields are Attention's
rows on the same state.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
from collections import Counter
from collections.abc import Callable
from functools import partial
from itertools import repeat
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence import review_cases
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import (
    BrowseReviewLibrary,
    ReviewWebError,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.owner_responsiveness import OwnerResponsivenessFixture
from tests.support.proposal_evidence import _claim as _proposal_claim
from tests.support.proposal_evidence import _fields as _proposal_fields
from tests.support.proposal_evidence import _provider as _proposal_provider
from tests.support.proposal_evidence import _report as _proposal_report
from tests.support.review_cases import _report
from tests.unit import test_review_read_indexes as representative
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
)
from tests.unit.test_bibliographic_bundles import (
    _source as _bibliographic_source,
)

_LENSES = (
    "all",
    "needs_attention",
    "no_attention_needed",
    "not_matched",
    "preparation_failed",
)


def _at(source: MetadataReport, library: str, item: str) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            source.target.connection, library, item, source.target.fields
        ),
        file=source.file,
        local_claims=source.local_claims,
        local_reasons=source.local_reasons,
        providers=source.providers,
        cover_sources=source.cover_sources,
    )


def _admit(repository: SQLiteMetadataReportRepository, report: MetadataReport) -> None:
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)


def _narrator_conflict() -> MetadataReport:
    """An EPUB's conflicting narrators: excluded from attention (`review_sql`)."""
    return _proposal_report(
        title="Narrator conflict",
        fields=_proposal_fields(
            narrators=TargetField(TargetDestination.NARRATORS, TargetFieldState.MISSING)
        ),
        providers=tuple(
            _proposal_provider(
                provider,
                edition,
                (
                    _proposal_claim(
                        provider,
                        edition,
                        ClaimKind.CONTRIBUTOR,
                        narrator,
                        scope=ClaimScope.EDITION,
                        role="narrator",
                    ),
                ),
            )
            for provider, edition, narrator in (
                ("catalogue-a", "edition-a", "Nora Narrator"),
                ("catalogue-b", "edition-b", "Pia Narrator"),
            )
        ),
    )


def _three_libraries(root: Path) -> SQLiteMetadataReportRepository:
    """The Library-counts fixture (#1284), plus a second library of cases.

    `ebooks` holds a work needing attention and one no provider matched,
    `fiction` a book whose preparation stopped before its report, and `shelf`
    an EPUB whose only conflict is its narrators, which needs no attention,
    beside a work whose series does.
    """
    repository = open_repository(root / "review.sqlite")
    for report in (
        _at(_report("Needle attention work", actionable=True), "ebooks", "attention"),
        _at(_report("Settled work", "c", "d", actionable=False), "ebooks", "settled"),
        _at(_narrator_conflict(), "shelf", "narrator-conflict"),
        _at(
            _bibliographic_report(
                current_title="Mistborn Secret History",
                providers=(
                    _bibliographic_source(
                        "catalogue", "edition-1", titles=(), series="Mistborn"
                    ),
                ),
            ),
            "shelf",
            "series-attention",
        ),
    ):
        _admit(repository, report)
    catalogue = open_catalogue(root / "catalogue.sqlite", create=True)
    entry = CatalogueEntry(
        "primary",
        "fiction",
        "lib-1",
        "terminal-item",
        1,
        "Stopped terminal title",
        ("Terminal Writer",),
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
    repository.bind_library_catalogue(root / "catalogue.sqlite", "a" * 64)
    repository.request_review_scan("primary", "fiction", "lib-1", "f" * 64, 10)
    discovery = repository.claim_review_pipeline_action(11)
    assert discovery is not None
    repository.complete_review_discovery(discovery, generation, "a" * 64, (entry,), 12)
    action = repository.claim_review_pipeline_action(13)
    assert action is not None
    repository.fail_review_pipeline_action(
        action, "unreadable_media", False, 14, "track_1"
    )
    return repository


def test_progress_totals_are_library_counts_and_its_fields_attention_rows(
    tmp_path: Path,
) -> None:
    repository = _three_libraries(tmp_path)
    try:
        browser = BrowseReviewLibrary(repository)
        labels = (
            ("primary", "shelf", "Second shelf"),
            ("primary", "ebooks", "E-books"),
        )
        progress = browser.progress(labels)
        totals = progress["totals"]
        assert totals == {
            "all": 5,
            "needs_attention": 2,
            "no_attention_needed": 1,
            "not_matched": 1,
            "preparation_failed": 1,
            "flagged": 0,
            "new": 0,
        }
        # Equal to every Library answer's counts, whatever its lens and page.
        for lens in _LENSES:
            for limit in (1, 25):
                assert (
                    browser.library(lens=lens, search=None, limit=limit, cursor=None)[
                        "counts"
                    ]
                    == totals
                ), (lens, limit)
        # Settings order, then the stable name no setting configures; a library
        # without a display name reads as its stable name.
        libraries = cast(list[dict[str, object]], progress["libraries"])
        assert [
            (row["library"], row["library_display_name"], row["counts"])
            for row in libraries
        ] == [
            (
                "shelf",
                "Second shelf",
                {
                    "all": 2,
                    "needs_attention": 1,
                    "no_attention_needed": 1,
                    "not_matched": 0,
                    "preparation_failed": 0,
                    "flagged": 0,
                    "new": 0,
                },
            ),
            (
                "ebooks",
                "E-books",
                {
                    "all": 2,
                    "needs_attention": 1,
                    "no_attention_needed": 0,
                    "not_matched": 1,
                    "preparation_failed": 0,
                    "flagged": 0,
                    "new": 0,
                },
            ),
            (
                "fiction",
                "fiction",
                {
                    "all": 1,
                    "needs_attention": 0,
                    "no_attention_needed": 0,
                    "not_matched": 0,
                    "preparation_failed": 1,
                    "flagged": 0,
                    "new": 0,
                },
            ),
        ]
        for lens in _LENSES:
            assert (
                sum(cast(dict[str, int], row["counts"])[lens] for row in libraries)
                == cast(dict[str, int], totals)[lens]
            )
        # Each field counts the works Attention lists under it; the EPUB's
        # conflicting narrators are in neither.
        attention = browser.attention(search=None, limit=50, cursor=None)
        listed = Counter(
            str(item["destination"])
            for item in cast(list[dict[str, object]], attention["items"])
        )
        assert progress["fields"] == [
            {"destination": "series", "need_you": 1},
            {"destination": "title", "need_you": 1},
        ]
        assert listed == {"series": 1, "title": 1}
        # Nothing names a work: counts and library names only.
        text = json.dumps(progress)
        for private in (
            "settled",
            "narrator-conflict",
            "series-attention",
            "terminal-item",
            "Needle",
            "Settled",
            "Stopped",
            "Mistborn",
            "lib-1",
            "review_case",
            "report",
        ):
            assert private not in text, private
    finally:
        repository.close()


def test_progress_refuses_counts_whose_lenses_do_not_partition_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stopped book counted as settled too would make the lenses overlap."""
    repository = _three_libraries(tmp_path)
    try:
        assert repository.read_review_library_progress(()).libraries
        monkeypatch.setitem(
            review_cases._LIBRARY_LENS_CONDITIONS,
            "no_attention_needed",
            "attention=0 AND match_reason IS NULL",
        )
        with pytest.raises(MetadataReportError, match="review library is unavailable"):
            repository.read_review_library_progress(())
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("corruption", "statement"),
    (
        ("negative", "library"),
        ("boolean", "library"),
        ("over_total_flagged", "library"),
        ("over_total_new", "library"),
        ("over_attention", "field"),
        ("unknown_library", "field"),
    ),
)
def test_progress_refuses_an_invalid_row(
    tmp_path: Path, corruption: str, statement: str
) -> None:
    repository = _three_libraries(tmp_path)
    reached = False

    def corrupt(cursor: sqlite3.Cursor, row: tuple[object, ...]) -> tuple[object, ...]:
        nonlocal reached
        width = 9 if statement == "library" else 4
        if len(cursor.description) != width:
            return row
        reached = True
        values = list(row)
        if corruption == "negative":
            values[4] = -1
        elif corruption == "boolean":
            values[2] = True
        elif corruption == "over_attention":
            values[3] = 2
        elif corruption == "over_total_flagged":
            values[7] = cast(int, values[2]) + 1
        elif corruption == "over_total_new":
            values[8] = cast(int, values[2]) + 1
        else:
            values[1] = "elsewhere"
        return tuple(values)

    repository._connection.row_factory = corrupt
    try:
        with pytest.raises(MetadataReportError, match="review library is unavailable"):
            repository.read_review_library_progress(())
        assert reached
    finally:
        repository._connection.row_factory = None
        repository.close()


# The Library page statement as it was before #1335 extracted its candidate
# relation, plus #1334's Looks wrong column and total and #1351's visit
# boundary, New column, New total and listing end: the trace of each read
# below, as its SHA-256.
_PAGE_STATEMENT_DIGESTS = {
    (False, None): "84a9ebb376eda35e180f30f28f22ded9ee0c2971d91eddfa093fa817b61809e3",
    (False, "needs_attention"): (
        "b6a6a4a52c0adc3442194aba1583f83ff9613fca760b2044ec386ae7054ddff6"
    ),
    (True, None): "4e17e6b3cda48ee1233cb8ace6809ef85bc00c1b3c0f5271390359f720e6ec62",
    (True, "needs_attention"): (
        "252cc84dddf21823548d91f92989ddcb7403e949178f18f9b1b1b743590ecdad"
    ),
}


def _traced(
    repository: SQLiteMetadataReportRepository, read: Callable[[], object]
) -> tuple[str, ...]:
    statements: list[str] = []
    repository._connection.set_trace_callback(statements.append)
    try:
        read()
    finally:
        repository._connection.set_trace_callback(None)
    return tuple(statement for statement in statements if statement.startswith("WITH "))


def test_the_library_page_statement_is_unchanged_and_shares_its_candidates(
    tmp_path: Path,
) -> None:
    repository = open_repository(tmp_path / "review.sqlite")
    try:
        for attached in (False, True):
            if attached:
                catalogue = open_catalogue(tmp_path / "catalogue.sqlite", create=True)
                catalogue.acquire_refresh()
                catalogue.publish("a" * 64, ())
                repository.bind_library_catalogue(
                    tmp_path / "catalogue.sqlite", "a" * 64
                )
            for lens in (None, "needs_attention"):
                (page,) = _traced(
                    repository,
                    partial(repository.read_review_library, None, 7, 3, lens),
                )
                assert (
                    hashlib.sha256(page.encode()).hexdigest()
                    == _PAGE_STATEMENT_DIGESTS[(attached, lens)]
                ), (attached, lens)
            (progress,) = _traced(
                repository, lambda: repository.read_review_library_progress(())
            )
            # The progress statement selects from the page's own relation.
            shared = page.index(",totals AS (")
            assert progress.startswith(page[:shared] + " SELECT connection_name,")
    finally:
        repository.close()


def test_field_counts_are_attention_rows_on_the_representative_store() -> None:
    with tempfile.TemporaryDirectory() as directory:
        connection = representative._representative_database(
            Path(directory) / "application.sqlite3", works=24
        )
        repository = SQLiteMetadataReportRepository(connection)
        try:
            progress = repository.read_review_library_progress(())
            fields = Counter[str]()
            for _connection, _library, destination, works in progress.fields:
                fields[destination] += works
            rows = repository.read_current_attention_page(None, 10_000, 0).rows
            assert fields == Counter(row[2] for row in rows)
            assert fields.total() == repository.count_current_attention_fields(None)
            assert fields.total() > 0
            # Library's own attention lens agrees with the work counts.
            library = BrowseReviewLibrary(repository).library(
                lens="all", search=None, limit=5, cursor=None
            )
            assert (
                BrowseReviewLibrary(repository).progress()["totals"]
                == (library["counts"])
            )
        finally:
            repository.close()


def test_a_cover_field_counts_only_while_its_live_authority_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Attention's live cover term decides a cover field, as on Attention."""
    # Held covers ask; suggested ones would not be attention (#1363 S9).
    from mechanical_librarian.application import cover_policy
    from tests.support.cover_producers import held_measured_stage

    monkeypatch.setattr(cover_policy, "elect_measured_covers", held_measured_stage)
    fixture = OwnerResponsivenessFixture(tmp_path, 30, mixed=True)
    try:
        repository = fixture.repository
        policy, replay = repository._cover_read_authority()
        assert policy is not None and replay is not None
        another = "cover_policy_" + hashlib.sha256(b"another policy").hexdigest()

        def fields() -> Counter[str]:
            counted = Counter[str]()
            for (
                *_library,
                destination,
                works,
            ) in repository.read_current_library_progress(()).fields:
                counted[destination] += works
            listed = Counter(
                row[2]
                for row in repository.read_current_attention_page(None, 10_000, 0).rows
            )
            assert counted == listed
            assert counted.total() == repository.count_current_attention_fields(None)
            return counted

        repository.bind_cover_read_observation(
            repeat(((policy, replay), frozenset())).__next__
        )
        with repository.browse_observation():
            assert fields()["cover"] > 0
        repository.bind_cover_read_observation(
            repeat(((another, replay), frozenset())).__next__
        )
        with repository.browse_observation():
            assert fields()["cover"] == 0
            # Without the live term every stored cover fact would count, more
            # than the works Library has in attention, and the read refuses.
            monkeypatch.setattr(
                review_cases, "_ATTENTION_FACT_CURRENT_SQL", "(1 OR ?=?)"
            )
            with pytest.raises(MetadataReportError):
                repository.read_current_library_progress(())
    finally:
        fixture.close()


def test_progress_reads_its_two_statements_on_one_snapshot() -> None:
    """A decision committed between the work and field statements is unseen."""
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "application.sqlite3"
        reader = open_repository(database)
        report = _report()
        reader.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        reader.publish_proposal(proposal_set)
        case_id = json.loads(reader.admit_review_case(proposal_set))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(reader.get_review_case(case_id))["case"]
        )
        proposal = next(
            value
            for value in case.proposals
            if value["result"]["outcome"] == "unresolved_choice"
        )
        decision = HumanDecision.create(
            case,
            proposal_id=cast(str, proposal["proposal_id"]),
            target_fingerprint=case.target_fingerprint,
            destination=cast(str, proposal["destination"]),
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="progress-snapshot-decision",
            occurred_at=1_000,
        )
        committed = threading.Event()
        errors: list[BaseException] = []

        def write_decision() -> None:
            writer = None
            try:
                connection = sqlite3.connect(
                    database if reader._binding is None else reader._binding.alias_path,
                    isolation_level=None,
                )
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=5000")
                writer = SQLiteMetadataReportRepository(connection)
                writer.record_human_decision(decision)
            except BaseException as error:
                errors.append(error)
            finally:
                if writer is not None:
                    writer.close()
                committed.set()

        started = False

        def between(statement: str) -> None:
            # The field statement is about to run: the work statement has read.
            nonlocal started
            if started or not statement.startswith("SELECT c.connection_name"):
                return
            started = True
            threading.Thread(target=write_decision, daemon=True).start()
            assert committed.wait(5)

        reader._connection.set_trace_callback(between)
        try:
            before = reader.read_review_library_progress(())
        finally:
            reader._connection.set_trace_callback(None)
        assert started and errors == []
        assert before.libraries == (("primary", "ebooks", 1, 1, 0, 0, 0, 0),)
        assert before.fields == (("primary", "ebooks", "title", 1),)
        after = reader.read_review_library_progress(())
        assert after.libraries == (("primary", "ebooks", 1, 0, 0, 0, 0, 0),)
        assert after.fields == ()
        reader.close()


def _start_visit(repository: SQLiteMetadataReportRepository, now: int) -> None:
    """Finish the fixture's scan, then take the first mark (#1351)."""
    assert repository.claim_review_pipeline_action(now) is None
    assert repository.record_library_visit(None, now) is False


def _new_works(
    browser: BrowseReviewLibrary, now: int, search: str | None = None
) -> tuple[int, list[str]]:
    page = browser.library(lens="new", search=search, limit=50, cursor=None, now=now)
    counts = cast(dict[str, int], page["counts"])
    items = cast(list[dict[str, object]], page["items"])
    return counts["new"], sorted(str(item["title"]) for item in items)


def _stop_before_report(
    repository: SQLiteMetadataReportRepository, library: str, item: str, now: int
) -> None:
    """Discover one item in a library of its own and fail it for good."""
    entry = CatalogueEntry(
        "primary",
        library,
        "lib-" + library,
        item,
        1,
        "Stopped later",
        (),
        True,
        False,
        "singular-supported",
        "2" * 64,
        "/synthetic-later.epub",
        "epub",
        "/media",
    )
    repository.request_review_scan("primary", library, "lib-" + library, "f" * 64, now)
    discovery = repository.claim_review_pipeline_action(now + 1)
    assert discovery is not None
    repository.complete_review_discovery(discovery, 2, "b" * 64, (entry,), now + 2)
    action = repository.claim_review_pipeline_action(now + 3)
    assert action is not None
    repository.fail_review_pipeline_action(
        action, "unreadable_media", False, now + 4, "track_1"
    )
    assert repository.claim_review_pipeline_action(now + 5) is None


def test_new_is_a_work_first_listed_after_the_boundary_and_nothing_else(
    tmp_path: Path,
) -> None:
    """AC1: an arrival is new; a re-search or a decision makes nothing new."""
    repository = _three_libraries(tmp_path)
    try:
        browser = BrowseReviewLibrary(repository)
        _start_visit(repository, 100)
        assert _new_works(browser, 100) == (0, [])
        positions = dict(
            repository._connection.execute(
                "SELECT item_id,rowid FROM current_review_packages"
            )
        )

        _admit(repository, _at(_report("Arrived work", "5", "6"), "ebooks", "arrived"))
        assert _new_works(browser, 110) == (1, ["Arrived work"])

        # A later report for a work already listed: the package row is
        # written in place, so the work keeps its position.
        reports = repository._connection.execute(
            "SELECT count(*) FROM metadata_reports"
        ).fetchone()[0]
        _admit(
            repository,
            _at(
                _report("Needle attention work", "7", "b", actionable=True),
                "ebooks",
                "attention",
            ),
        )
        assert (
            repository._connection.execute(
                "SELECT count(*) FROM metadata_reports"
            ).fetchone()[0]
            == reports + 1
        )
        assert (
            dict(
                repository._connection.execute(
                    "SELECT item_id,rowid FROM current_review_packages"
                )
            )["attention"]
            == positions["attention"]
        )
        # An owner decision on a listed work.
        case_id = repository._connection.execute(
            "SELECT review_case_id FROM current_review_packages "
            "WHERE item_id='series-attention'"
        ).fetchone()[0]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(
            value for value in case.proposals if value["destination"] == "series"
        )
        repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=cast(str, proposal["proposal_id"]),
                target_fingerprint=case.target_fingerprint,
                destination="series",
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="new-lens-decision",
                occurred_at=120,
            )
        )
        assert _new_works(browser, 130) == (1, ["Arrived work"])

        # A work that could not be read has no package: it is new by the
        # order it was discovered in. The one found before the mark is not.
        _stop_before_report(repository, "drama", "stopped-later", 140)
        assert _new_works(browser, 150) == (
            2,
            ["Arrived work", "Preparation unavailable"],
        )

        # The negative control: once a later visit has seen them, none is new.
        shown = repository.read_current_library_page(None, 1, 0, "new", (), 160)
        assert repository.record_library_visit(shown.listing, 160) is False
        assert _new_works(browser, 160)[0] == 2
        assert repository.record_library_visit(None, 160 + 1_800) is True
        assert _new_works(browser, 160 + 1_800) == (0, [])
    finally:
        repository.close()


def test_a_newcomer_after_the_last_row_left_and_one_in_flight_are_new(
    tmp_path: Path,
) -> None:
    """AC2: a newcomer after the last row was deleted never takes its place."""
    repository = _three_libraries(tmp_path)
    try:
        browser = BrowseReviewLibrary(repository)
        # Discovered before the mark, and still being prepared at it.
        in_flight = CatalogueEntry(
            "primary",
            "drama",
            "lib-drama",
            "in-flight",
            1,
            "In flight",
            (),
            True,
            False,
            "singular-supported",
            "3" * 64,
            "/synthetic-flight.epub",
            "epub",
            "/media",
        )
        assert repository.claim_review_pipeline_action(20) is None
        repository.request_review_scan("primary", "drama", "lib-drama", "f" * 64, 21)
        discovery = repository.claim_review_pipeline_action(22)
        assert discovery is not None
        repository.complete_review_discovery(discovery, 2, "b" * 64, (in_flight,), 23)
        assert repository.record_library_visit(None, 100) is False
        boundary = repository._connection.execute(
            "SELECT seen_packages,seen_works,seen_reports FROM library_visit"
        ).fetchone()
        assert _new_works(browser, 100) == (0, [])

        # The last listed work leaves the library, and its row with it.
        last = repository._connection.execute(
            "SELECT item_id,rowid FROM current_review_packages "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert last[1] == boundary[0]
        repository._connection.execute(
            "DELETE FROM current_review_packages WHERE rowid=?", (last[1],)
        )
        repository._connection.commit()
        _admit(repository, _at(_report("Late newcomer", "8", "9"), "ebooks", "reuser"))
        assert repository._connection.execute(
            "SELECT rowid>? FROM current_review_packages WHERE item_id='reuser'",
            (boundary[0],),
        ).fetchone() == (1,)
        assert _new_works(browser, 110) == (1, ["Late newcomer"])

        _admit(repository, _at(_report("In flight", "a", "c"), "drama", "in-flight"))
        assert _new_works(browser, 120) == (2, ["In flight", "Late newcomer"])
    finally:
        repository.close()


def test_a_work_that_returns_after_the_last_row_left_is_new(
    tmp_path: Path,
) -> None:
    """A seen work that leaves and returns counts as new, even as the last row.

    Its first report stays in `metadata_reports`, so only a package position
    that is never given out twice can tell its return from a work that stayed.
    """
    repository = _three_libraries(tmp_path)
    try:
        browser = BrowseReviewLibrary(repository)
        _start_visit(repository, 100)
        seen = repository._connection.execute(
            "SELECT seen_packages FROM library_visit"
        ).fetchone()[0]
        library, item, position = repository._connection.execute(
            "SELECT library_name,item_id,rowid FROM current_review_packages "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert position == seen
        repository._connection.execute(
            "DELETE FROM current_review_packages WHERE rowid=?", (position,)
        )
        repository._connection.commit()
        _admit(repository, _at(_report("Returned work", "8", "9"), library, item))
        assert repository._connection.execute(
            "SELECT rowid>? FROM current_review_packages WHERE item_id=?",
            (seen, item),
        ).fetchone() == (1,)
        assert _new_works(browser, 110) == (1, ["Returned work"])
    finally:
        repository.close()


def test_the_new_lens_lists_counts_and_pages_across_the_partition(
    tmp_path: Path,
) -> None:
    """AC5: New cuts across the four lenses, whose counts it does not change."""
    repository = _three_libraries(tmp_path)
    try:
        browser = BrowseReviewLibrary(repository)
        _start_visit(repository, 100)
        for index, (title, actionable) in enumerate(
            (("Arrival one", True), ("Arrival two", False), ("Arrival three", False))
        ):
            _admit(
                repository,
                _at(
                    _report(
                        title, f"{index + 4}", f"{index + 7}", actionable=actionable
                    ),
                    "ebooks" if index else "shelf",
                    f"arrival-{index}",
                ),
            )
        everything = browser.library(
            lens="all", search=None, limit=50, cursor=None, now=110
        )
        counts = cast(dict[str, int], everything["counts"])
        assert counts["new"] == 3 <= counts["all"] == 8
        assert sum(counts[lens] for lens in _LENSES[1:]) == counts["all"]
        # The same counts on every lens, and what a clockless read gives the
        # partition: New changes none of them.
        clockless = cast(
            dict[str, int],
            browser.library(lens="all", search=None, limit=50, cursor=None)["counts"],
        )
        assert clockless == counts
        for lens in (*_LENSES, "new", "flagged"):
            assert (
                browser.library(lens=lens, search=None, limit=1, cursor=None, now=110)[
                    "counts"
                ]
                == counts
            ), lens

        first = browser.library(lens="new", search=None, limit=2, cursor=None, now=110)
        assert (first["lens"], first["page"], first["previous_cursor"]) == (
            "new",
            1,
            None,
        )
        second = browser.library(
            lens="new",
            search=None,
            limit=2,
            cursor=cast(str, first["next_cursor"]),
            now=110,
        )
        assert second["next_cursor"] is None and second["page"] == 2
        listed = [
            str(item["title"])
            for page in (first, second)
            for item in cast(list[dict[str, object]], page["items"])
        ]
        # Library's own order: the work needing attention first, then by title.
        assert listed == ["Arrival one", "Arrival three", "Arrival two"]
        # A cursor is bound to its lens.
        with pytest.raises(ReviewWebError):
            browser.library(
                lens="all",
                search=None,
                limit=2,
                cursor=cast(str, first["next_cursor"]),
                now=110,
            )
        # Search composes under New.
        assert _new_works(browser, 110, "three") == (1, ["Arrival three"])
        assert _new_works(browser, 110, "Needle") == (0, [])
        # The listing every page hands back is the end of the three tables.
        assert first["listing"] == everything["listing"]
        assert cast(dict[str, int], first["listing"]) == dict(
            zip(
                ("packages", "works", "reports"),
                repository._connection.execute(
                    "SELECT (SELECT max(rowid) FROM current_review_packages),"
                    "(SELECT max(rowid) FROM review_preparation_work),"
                    "(SELECT max(rowid) FROM metadata_reports)"
                ).fetchone(),
                strict=True,
            )
        )

        progress = browser.progress((), 110)
        assert progress["totals"] == counts
        assert {
            str(row["library"]): cast(dict[str, int], row["counts"])["new"]
            for row in cast(list[dict[str, object]], progress["libraries"])
        } == {"ebooks": 2, "fiction": 0, "shelf": 1}
    finally:
        repository.close()
