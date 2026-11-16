"""The `library_visit` row: the owner's one Library visit (#1351, ADR-0103).

A read never writes it. Library's candidate relation reads the boundary
through `LIBRARY_VISIT_BOUNDARY_SQL`, chosen from the clock the read binds;
`record_library_visit` is the one writer, run inside its caller's transaction.
"""

from __future__ import annotations

import sqlite3

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.library_visit import (
    VISIT_GAP_SECONDS,
    LibraryVisit,
    LibraryVisitValueError,
    ListingPositions,
    record_library_use,
)

# The boundary a work must lie past to be new, as one row or none. Once the
# gap has passed since the last recorded use, what that visit showed is
# already seen, so a device opened later needs no write to read the next
# visit. It binds one parameter, the server's clock. With no row the relation
# is empty, and a scalar read of it is NULL: the caller must read that as
# "nothing is new", never as "no boundary".
LIBRARY_VISIT_BOUNDARY_SQL = (
    "visit AS (SELECT "
    + ",".join(
        f"CASE WHEN clock.now-active_at>={VISIT_GAP_SECONDS} THEN shown_{name} "
        f"ELSE seen_{name} END AS {name}"
        for name in ("packages", "works", "reports")
    )
    + " FROM library_visit,(SELECT ? AS now) clock)"
)

# How far the listing has got: the last row of the listed works, of the works
# being prepared and of the reports. `metadata_reports` is append-only, so its
# end never falls; the other two can, when their last row is deleted.
LIBRARY_LISTING_END_SQL = (
    "(SELECT COALESCE(max(rowid),0) FROM current_review_packages) "
    "AS listing_packages,"
    "(SELECT COALESCE(max(rowid),0) FROM review_preparation_work) "
    "AS listing_works,"
    "(SELECT COALESCE(max(rowid),0) FROM metadata_reports) AS listing_reports"
)

# The first mark waits until no preparation run is unfinished and one scan
# has completed. The run rows are deleted when the owner dismisses the
# summary, so the scans say that a preparation has finished at all.
_BASELINE_READY_SQL = (
    "SELECT NOT EXISTS(SELECT 1 FROM review_preparation_presentation "
    "WHERE row_id='run' AND state IN ('starting','running','blocked')) "
    "AND EXISTS(SELECT 1 FROM review_preparation_scans "
    "WHERE state IN ('completed','completed_with_failures'))"
)


def _positions(values: tuple[object, ...]) -> ListingPositions:
    packages, works, reports = values
    if type(packages) is not int or type(works) is not int or type(reports) is not int:
        raise LibraryVisitValueError("library listing positions are invalid")
    return ListingPositions(packages, works, reports)


def record_library_visit(
    connection: sqlite3.Connection, listing: ListingPositions | None, now: int
) -> bool:
    """Record one use of Library; True when an existing boundary changed value.

    The caller owns the transaction. The first mark is not a change: nothing
    was new before it, and nothing is after it.
    """
    try:
        rows = connection.execute(
            "SELECT seen_packages,seen_works,seen_reports,shown_packages,"
            "shown_works,shown_reports,active_at FROM library_visit"
        ).fetchall()
        if len(rows) > 1 or (rows and type(rows[0][6]) is not int):
            raise LibraryVisitValueError("library visit is invalid")
        visit = (
            LibraryVisit(_positions(rows[0][:3]), _positions(rows[0][3:6]), rows[0][6])
            if rows
            else None
        )
        end = _positions(
            connection.execute("SELECT " + LIBRARY_LISTING_END_SQL).fetchone()
        )
        recorded = record_library_use(
            visit,
            end=end,
            listing=listing,
            now=now,
            baseline_ready=visit is not None
            or connection.execute(_BASELINE_READY_SQL).fetchone() == (1,),
        )
        if recorded is None:
            return False
        connection.execute(
            "INSERT INTO library_visit(singleton,seen_packages,seen_works,"
            "seen_reports,shown_packages,shown_works,shown_reports,active_at) "
            "VALUES(1,?,?,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
            "seen_packages=excluded.seen_packages,seen_works=excluded.seen_works,"
            "seen_reports=excluded.seen_reports,"
            "shown_packages=excluded.shown_packages,"
            "shown_works=excluded.shown_works,shown_reports=excluded.shown_reports,"
            "active_at=excluded.active_at",
            (
                recorded.seen.packages,
                recorded.seen.works,
                recorded.seen.reports,
                recorded.shown.packages,
                recorded.shown.works,
                recorded.shown.reports,
                recorded.active_at,
            ),
        )
        return visit is not None and recorded.seen != visit.seen
    except (sqlite3.Error, LibraryVisitValueError) as error:
        raise MetadataReportError("library visit is unavailable") from error


__all__ = [
    "LIBRARY_LISTING_END_SQL",
    "LIBRARY_VISIT_BOUNDARY_SQL",
    "record_library_visit",
]
