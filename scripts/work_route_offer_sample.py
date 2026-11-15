"""Library links to the work-route offers the owner has not decided (#1642).

A workbench instrument, deliberately not part of the installed product: run it
from the checkout an install was built from, against a COPY of that install's
application database. It opens the copy read-only, writes nothing and prints
one Library link per field, so the owner can label the fields
``scripts/work_route_measurement.py`` measures from his decisions.

A field is listed when it is the description, genres or narrators of a current
audiobook with a review case and no admitted recording, the work route offers
a value for it while it is strictly empty (the measurement's ``_qualifies``),
and Hardcover or Open Library, through an exact work source naming one work,
offers that value (``offered_fields``). A field whose latest qualifying owner
decision (``latest_labels``) is anything but a withdrawal, a pin or an unpin
is left out and counted as already decided, which is the measurement's
``labels.labelled``.
Each link names the providers that offer the value. Links are listed in the
measurement's item order, and every count is read inside one read
transaction.

The output holds links and counts only: never a title, author, value, item
identifier, library name or path. A review case identifier is an opaque
content hash.

Usage:
    uv run python scripts/work_route_offer_sample.py \\
        --database /copy/application.sqlite3 \\
        [--origin http://localhost:8080] [--limit 50]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import TextIO, cast

from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
)

# Direct execution puts scripts/ on sys.path; the test import resolves the
# scripts namespace package.
try:
    from scripts import hardcover_series_sample as series_sample
    from scripts import work_route_measurement as measurement
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import hardcover_series_sample as series_sample  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    import work_route_measurement as measurement  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

_DESCRIPTION = (
    "List Library links to the empty description, genres and narrators fields "
    "where Hardcover or Open Library offers a value for an audiobook Audible "
    "cannot match. Reads a copy of the application database and writes nothing."
)
_FIELD_NAMES = {
    "description": "Description",
    "genres": "Genres",
    "narrators": "Narrators",
}
_PROVIDER_NAMES = {"hardcover": "Hardcover", "open_library": "Open Library"}

Offer = tuple[str, tuple[str, ...]]


def read_offers(
    connection: sqlite3.Connection,
) -> tuple[dict[str, list[Offer]], Counter[str]]:
    """Per field, each undecided offer as (case, providers), and the decided count.

    The offers and the decisions read one snapshot inside one read
    transaction.
    """
    connection.execute("BEGIN")
    try:
        rows = connection.execute(measurement._REPORTS_SQL).fetchall()
        latest, _outside = measurement.latest_labels(connection)
    finally:
        connection.execute("ROLLBACK")
    settled = {
        key
        for key, value in latest.items()
        if value[0] != "withdraw" and value[0] not in measurement.STANDING_OUTCOMES
    }
    decided = Counter(cast("str", key[3]) for key in settled)
    offers: dict[str, list[Offer]] = {
        name: [] for name in measurement.OWNER_LABELLED_FIELDS
    }
    for connection_name, library, item, case_id, report_text, proposal_text in rows:
        if case_id is None:
            continue
        report = measurement._load_report(report_text)
        if admitted_audiobook_identity(report):
            continue
        sources, _duplicates = measurement.exact_sources(report)
        offered = measurement.offered_fields(
            report, measurement._load_json(proposal_text), sources
        )
        for name in measurement.OWNER_LABELLED_FIELDS:
            providers = tuple(
                authority
                for authority in measurement.PROVIDERS
                if name in offered.get(authority, ())
            )
            if (
                providers
                and measurement._qualifies(name, report)
                and (connection_name, library, item, name) not in settled
            ):
                offers[name].append((cast("str", case_id), providers))
    return offers, decided


def render(
    offers: dict[str, list[Offer]], decided: Counter[str], origin: str, limit: int
) -> str:
    lines: list[str] = []
    for name in measurement.OWNER_LABELLED_FIELDS:
        listed = offers[name]
        if lines:
            lines.append("")
        lines.append(
            f"{_FIELD_NAMES[name]}: {len(listed)} to decide, "
            f"{decided[name]} already decided."
        )
        shown = listed[:limit]
        lines.extend(
            f"{origin}/library?lens=all&preview={case_id}&field={name}"
            "  From "
            + " and ".join(_PROVIDER_NAMES[provider] for provider in providers)
            + "."
            for case_id, providers in shown
        )
        if len(listed) > limit:
            lines.append(f"Showing {len(shown)} of {len(listed)}.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument(
        "--database", required=True, type=Path, help="a copy of application.sqlite3"
    )
    parser.add_argument(
        "--origin",
        default=series_sample.DEFAULT_ORIGIN,
        type=series_sample._origin,
        help=(
            "the address you open Mechanical Librarian at "
            "(default http://localhost:8080)"
        ),
    )
    parser.add_argument(
        "--limit",
        default=series_sample.DEFAULT_LIMIT,
        type=series_sample._limit,
        help="how many links to print for each field (default 50)",
    )
    arguments = parser.parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    database: Path = arguments.database
    if not database.is_file():
        raise SystemExit(series_sample._MISSING)
    try:
        connection = series_sample._open_read_only(database)
    except sqlite3.Error as error:
        raise SystemExit(series_sample._UNREADABLE) from error
    try:
        offers, decided = read_offers(connection)
    except (sqlite3.Error, ValueError) as error:
        raise SystemExit(series_sample._UNREADABLE) from error
    finally:
        connection.close()
    output.write(render(offers, decided, arguments.origin, arguments.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
