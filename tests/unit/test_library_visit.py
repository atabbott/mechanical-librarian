"""The owner's Library visit: the mark, the gap and the first baseline (#1351).

The pure transition is tested on its own, and the stored row through the real
repository with an injected clock: reads never write, the boundary flips at
the gap without a post, and a work stops being new only at the visit after
the New lens showed it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import PreparationTrigger
from mechanical_librarian.domain.library_visit import (
    VISIT_GAP_SECONDS,
    LibraryVisit,
    LibraryVisitValueError,
    ListingPositions,
    record_library_use,
)
from tests.support.review_cases import _report
from tests.unit.test_library_progress import _admit, _at
from tests.unit.test_review_pipeline import (
    _PRODUCER,
    _entry,
    _install_settings_authority,
    _publish_proposal_set,
)

_P = ListingPositions


def _visit(seen: int, shown: int, active_at: int) -> LibraryVisit:
    return LibraryVisit(_P(seen, seen, seen), _P(shown, shown, shown), active_at)


def test_a_use_inside_the_gap_keeps_the_boundary_and_one_after_it_moves_it() -> None:
    visit = _visit(3, 7, 1_000)
    end = _P(9, 9, 9)
    inside = record_library_use(
        visit,
        end=end,
        listing=None,
        now=1_000 + VISIT_GAP_SECONDS - 1,
        baseline_ready=True,
    )
    assert inside == _visit(3, 7, 1_000 + VISIT_GAP_SECONDS - 1)
    after = record_library_use(
        visit,
        end=end,
        listing=None,
        now=1_000 + VISIT_GAP_SECONDS,
        baseline_ready=True,
    )
    # What the last visit showed is seen; nothing new was shown by this use.
    assert after == _visit(7, 7, 1_000 + VISIT_GAP_SECONDS)


def test_a_clock_set_back_reads_as_no_gap() -> None:
    visit = _visit(3, 7, 10_000)
    assert record_library_use(
        visit, end=_P(9, 9, 9), listing=None, now=5, baseline_ready=True
    ) == _visit(3, 7, 5)


def test_shown_only_rises_and_never_passes_the_current_end() -> None:
    visit = LibraryVisit(_P(3, 3, 3), _P(5, 6, 7), 1_000)
    recorded = record_library_use(
        visit,
        end=_P(8, 4, 9),
        # Further than the end in one table, behind `shown` in another.
        listing=_P(50, 50, 2),
        now=1_001,
        baseline_ready=True,
    )
    assert recorded == LibraryVisit(_P(3, 3, 3), _P(8, 6, 7), 1_001)


def test_a_use_after_the_gap_takes_the_old_shown_before_the_new_listing() -> None:
    recorded = record_library_use(
        _visit(3, 7, 1_000),
        end=_P(9, 9, 9),
        listing=_P(9, 9, 9),
        now=1_000 + VISIT_GAP_SECONDS,
        baseline_ready=True,
    )
    # Works 8 and 9 are shown by this visit, so they stay new during it.
    assert recorded == _visit(7, 9, 1_000 + VISIT_GAP_SECONDS)


def test_the_first_mark_is_the_current_end_whatever_was_shown() -> None:
    end = _P(4, 5, 6)
    for listing in (None, _P(0, 0, 0), _P(99, 99, 99)):
        assert record_library_use(
            None, end=end, listing=listing, now=7, baseline_ready=True
        ) == LibraryVisit(end, end, 7)
        assert (
            record_library_use(
                None, end=end, listing=listing, now=7, baseline_ready=False
            )
            is None
        )


@pytest.mark.parametrize(
    "build",
    (
        lambda: _P(-1, 0, 0),
        lambda: _P(True, 0, 0),  # type: ignore[arg-type]
        lambda: _P(0, 0, "1"),  # type: ignore[arg-type]
        lambda: LibraryVisit(_P(2, 2, 2), _P(2, 1, 2), 0),
        lambda: LibraryVisit(_P(0, 0, 0), _P(0, 0, 0), -1),
        lambda: record_library_use(
            None, end=_P(0, 0, 0), listing=None, now=-1, baseline_ready=True
        ),
        lambda: record_library_use(
            None, end=_P(0, 0, 0), listing=None, now=True, baseline_ready=True
        ),
    ),
)
def test_invalid_positions_visits_and_clocks_are_refused(build) -> None:
    with pytest.raises(LibraryVisitValueError):
        build()


def _finished_preparation(repository: SQLiteMetadataReportRepository) -> None:
    """One completed scan and no run: what the first mark waits for."""
    repository._connection.execute(
        "INSERT INTO review_preparation_scans(scan_id,connection_name,library_name,"
        "library_id,producer_fingerprint,state,requested_at,updated_at,"
        "catalogue_generation,catalogue_fingerprint,qualification_epoch,total) "
        "VALUES(?,'primary','other','lib-x',?,'completed',0,0,1,?,0,0)",
        ("review_scan_" + "f" * 64, "c" * 64, "d" * 64),
    )
    repository._connection.commit()


def _row(repository: SQLiteMetadataReportRepository) -> tuple[int, ...] | None:
    row = repository._connection.execute(
        "SELECT seen_packages,seen_works,seen_reports,shown_packages,"
        "shown_works,shown_reports,active_at FROM library_visit"
    ).fetchone()
    return None if row is None else tuple(row)


def _new_items(repository: SQLiteMetadataReportRepository, now: int) -> list[str]:
    page = repository.read_current_library_page(None, 50, 0, "new", (), now)
    assert page.new_total == len(page.rows)
    return sorted(json.loads(row[1])["target"]["item"] for row in page.rows)


def _library(repository: SQLiteMetadataReportRepository) -> None:
    for name, digest in (("old-a", "1"), ("old-b", "2")):
        _admit(repository, _at(_report(name, digest, digest), "ebooks", name))


def test_with_no_visit_nothing_is_new_and_no_read_creates_one(tmp_path: Path) -> None:
    """The NULL trap (AC12): no row is "nothing new", never "no boundary"."""
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        _library(repository)
        for now in (0, 1_000, 10**9):
            page = repository.read_current_library_page(None, 50, 0, "new", (), now)
            assert (page.new_total, page.rows, page.total) == (0, (), 2)
            everything = repository.read_current_library_page(
                None, 50, 0, None, (), now
            )
            assert (everything.new_total, len(everything.rows)) == (0, 2)
            assert [
                row[7]
                for row in repository.read_current_library_progress((), now).libraries
            ] == [0]
        assert _row(repository) is None
    finally:
        repository.close()


def test_the_first_mark_waits_for_a_finished_preparation_then_takes_the_end(
    tmp_path: Path,
) -> None:
    """D7 (AC15) on a real first run, through its dismissal and a restart."""
    path = tmp_path / "application.sqlite3"
    repository = open_repository(path)
    try:
        _install_settings_authority(repository)
        listing = ListingPositions(0, 0, 0)

        def refused(now: int) -> None:
            assert repository.record_library_visit(listing, now) is False
            assert _row(repository) is None

        refused(5)
        repository.request_review_scan(
            "primary",
            "ebooks",
            "lib-1",
            _PRODUCER,
            10,
            trigger=PreparationTrigger.INITIAL_SETUP,
        )
        refused(10)
        discovery = repository.claim_review_pipeline_action(11)
        assert discovery is not None
        refused(11)
        entry = replace(_entry("item-1", "b" * 64), library="ebooks")
        repository.complete_review_discovery(discovery, 1, "c" * 64, (entry,), 12)
        proposal_set_id = _publish_proposal_set(repository._connection, "6")
        work = repository.claim_review_pipeline_action(14)
        while work is not None:
            refused(14)
            repository.advance_review_pipeline_action(
                work, proposal_set_id if work.stage == "proposal" else None, 15
            )
            work = repository.claim_review_pipeline_action(16)
        presentation = repository.preparation_presentation(17)
        assert presentation is not None and presentation.dismissible
        # The summary is dismissed before Library is ever used: its run rows
        # are gone, and the completed scan still says a preparation finished.
        repository.dismiss_preparation_presentation(presentation.run_id, 18)
    finally:
        repository.close()
    repository = open_repository(path, create=False)
    try:
        _library(repository)
        end = tuple(
            repository._connection.execute(
                "SELECT (SELECT max(rowid) FROM current_review_packages),"
                "(SELECT max(rowid) FROM review_preparation_work),"
                "(SELECT max(rowid) FROM metadata_reports)"
            ).fetchone()
        )
        assert min(end) >= 1
        # Not a change: nothing was new before the first mark, nor after.
        assert repository.record_library_visit(listing, 20) is False
        assert _row(repository) == (*end, *end, 20)
        assert _new_items(repository, 20) == []
    finally:
        repository.close()


def test_an_unfinished_run_holds_the_first_mark_back(tmp_path: Path) -> None:
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        _install_settings_authority(repository)
        _finished_preparation(repository)
        repository.request_review_scan(
            "primary",
            "ebooks",
            "lib-1",
            _PRODUCER,
            10,
            trigger=PreparationTrigger.MANUAL_SCAN,
        )
        assert repository.record_library_visit(None, 11) is False
        assert _row(repository) is None
    finally:
        repository.close()


def test_reads_never_write_and_the_boundary_flips_at_the_gap_without_a_post(
    tmp_path: Path,
) -> None:
    """AC4 and AC14 on the real repository with an injected clock."""
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        _library(repository)
        _finished_preparation(repository)
        assert repository.record_library_visit(None, 100) is False
        baseline = _row(repository)
        assert baseline is not None and baseline[6] == 100
        for name in ("new-c", "new-d"):
            _admit(repository, _at(_report(name, name[-1], name[-1]), "ebooks", name))
        assert _new_items(repository, 110) == ["new-c", "new-d"]

        # Library is used, on any lens but New: the visit stays alive and
        # nothing was shown, so a gap later both works are still new.
        assert repository.record_library_visit(None, 200) is False
        assert _row(repository) == (*baseline[:6], 200)
        assert _new_items(repository, 200 + VISIT_GAP_SECONDS) == ["new-c", "new-d"]
        assert repository.record_library_visit(None, 200 + VISIT_GAP_SECONDS) is False
        start = 200 + VISIT_GAP_SECONDS

        # New is opened while it lists only the first work's listing.
        shown = repository.read_current_library_page(
            None, 50, 0, "new", (), start
        ).listing
        _admit(repository, _at(_report("new-e", "e", "e"), "ebooks", "new-e"))
        assert repository.record_library_visit(shown, start + 1) is False
        opened = _row(repository)
        assert opened is not None
        assert opened[:3] == baseline[:3] and opened[3:6] == (
            shown.packages,
            shown.works,
            shown.reports,
        )
        # Reads at any clock leave the row as the post left it.
        for now in (start + 1, start + VISIT_GAP_SECONDS, start + 10**6):
            repository.read_current_library_page(None, 50, 0, "new", (), now)
            repository.read_current_library_page(None, 50, 0, None, (), now)
            repository.read_current_library_progress((), now)
            assert _row(repository) == opened
        # One second short of the gap all three are new; at the gap, with no
        # post, the two the New lens showed are not, and the one that arrived
        # after that listing still is.
        last = start + 1 + VISIT_GAP_SECONDS
        assert _new_items(repository, last - 1) == ["new-c", "new-d", "new-e"]
        assert _new_items(repository, last) == ["new-e"]
        # The next visit's first use makes that the stored boundary: a change.
        assert repository.record_library_visit(None, last) is True
        assert _row(repository) == (*opened[3:6], *opened[3:6], last)
        assert _new_items(repository, last) == ["new-e"]
        assert repository.record_library_visit(None, last + 1) is False
    finally:
        repository.close()


def test_a_second_connection_reads_the_same_mark_after_a_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.sqlite3"
    repository = open_repository(path)
    _library(repository)
    _finished_preparation(repository)
    repository.record_library_visit(None, 100)
    _admit(repository, _at(_report("new-c", "c", "c"), "ebooks", "new-c"))
    stored = _row(repository)
    repository.close()
    repository = open_repository(path, create=False)
    try:
        assert _row(repository) == stored
        assert _new_items(repository, 150) == ["new-c"]
    finally:
        repository.close()


def test_an_invalid_stored_visit_refuses_the_write_and_changes_nothing(
    tmp_path: Path,
) -> None:
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        _finished_preparation(repository)
        repository._connection.execute(
            "INSERT INTO library_visit VALUES(1,0,0,0,0,0,0,'soon')"
        )
        repository._connection.commit()
        with pytest.raises(MetadataReportError, match="library visit is unavailable"):
            repository.record_library_visit(None, 5)
        assert repository._connection.execute(
            "SELECT active_at FROM library_visit"
        ).fetchone() == ("soon",)
    finally:
        repository.close()


@pytest.mark.parametrize(
    "statement",
    (
        "INSERT INTO library_visit VALUES(2,0,0,0,0,0,0,0)",
        "INSERT INTO library_visit VALUES(1,-1,0,0,0,0,0,0)",
        "INSERT INTO library_visit VALUES(1,2,0,0,1,0,0,0)",
        "INSERT INTO library_visit VALUES(1,0,2,0,0,1,0,0)",
        "INSERT INTO library_visit VALUES(1,0,0,2,0,0,1,0)",
        "INSERT INTO library_visit VALUES(1,0,0,0,0,0,0,-1)",
        "INSERT INTO library_visit VALUES(1,0,0,0,0,0,0,NULL)",
    ),
)
def test_the_table_refuses_a_row_its_checks_forbid(
    tmp_path: Path, statement: str
) -> None:
    repository = open_repository(tmp_path / "application.sqlite3")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            repository._connection.execute(statement)
    finally:
        repository.close()


def test_a_database_without_the_visit_table_is_refused(tmp_path: Path) -> None:
    """AC11: the exact-schema check refuses the predecessor (ADR-0013)."""
    path = tmp_path / "application.sqlite3"
    open_repository(path).close()
    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE library_visit")
    connection.commit()
    connection.close()
    with pytest.raises(MetadataReportError, match="unavailable or incompatible"):
        open_repository(path, create=False)
