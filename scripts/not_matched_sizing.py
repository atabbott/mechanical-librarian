"""Aggregate-only sizing of the works Library holds as Not matched (#1477 S32).

A workbench instrument, not part of the installed product. It opens a COPY of
an application database read-only and immutable, so nothing in the snapshot
directory is written, and prints one JSON document to stdout.

Every current, non-tombstoned work's report is given its provider match state
by the product's own `_report_provider_match_state_sql`, and Library's Not
matched lens predicate (`_NOT_MATCHED_REASON_SQL`) gives the works the lens
holds and the reason each one shows. Both are counted by medium only. The
output holds closed medium, state and reason words and counts: never a
connection, library, title, author, path or identifier.

Usage:
    uv run python scripts/not_matched_sizing.py --database /copy/application.sqlite3
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.review_sql import (
    _NOT_MATCHED_REASON_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _report_provider_match_state_sql,
)

NOT_MATCHED_SIZING_SCHEMA = "not-matched-sizing-v1"
MEDIA = ("ebook", "audiobook")
MATCH_STATES = ("matched", "refused", "unavailable", "adapter_fault", "no_match")

# One row per current work: its medium, its report's provider match state and
# its Not matched reason (NULL when the lens does not hold it). The match state
# is derived from each report, not read from a stored browse fact, so a
# snapshot that predates the fact still answers.
_CURRENT_WORKS_SQL = (
    "SELECT CASE WHEN json_extract(mr.document,'$.audio') IS NULL "
    "THEN 'ebook' ELSE 'audiobook' END AS medium,"
    "facts.provider_match_state AS provider_match_state,"
    + _NOT_MATCHED_REASON_SQL
    + " AS not_matched_reason FROM current_review_packages ml "
    "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
    "JOIN (SELECT report_id,"
    + _report_provider_match_state_sql("document")
    + " AS provider_match_state FROM metadata_reports) facts "
    "ON facts.report_id=ml.report_id WHERE ml.outcome<>'tombstoned'"
)
_SIZING_SQL = (
    "SELECT medium,provider_match_state,not_matched_reason,count(*) FROM ("
    + _CURRENT_WORKS_SQL
    + ") GROUP BY 1,2,3 ORDER BY 1,2,3"
)


class SizingError(Exception):
    """The database could not be read as an application database."""


def open_read_only(path: Path) -> sqlite3.Connection:
    """Open a database copy that SQLite may neither write nor lock."""

    if not path.is_file():
        raise SizingError("database is not a file")
    absolute = quote(str(path.absolute()), safe="/")
    return sqlite3.connect(f"file:{absolute}?mode=ro&immutable=1", uri=True)


def _count(table: dict[str, dict[str, int]], medium: str, key: str, n: int) -> None:
    row = table.setdefault(medium, {})
    row[key] = row.get(key, 0) + n


def not_matched_sizing(connection: sqlite3.Connection) -> dict[str, object]:
    """Counts of current works by medium: match state and Not matched reason."""

    try:
        rows = connection.execute(_SIZING_SQL).fetchall()
    except sqlite3.DatabaseError as error:
        raise SizingError("database is not a readable application database") from error
    works: dict[str, int] = {medium: 0 for medium in MEDIA}
    states: dict[str, dict[str, int]] = {medium: {} for medium in MEDIA}
    reasons: dict[str, dict[str, int]] = {medium: {} for medium in MEDIA}
    for medium, state, reason, count in rows:
        if medium not in MEDIA or state not in MATCH_STATES:
            raise SizingError("an unexpected medium or match state was derived")
        if type(count) is not int:
            raise SizingError("a count is not an integer")
        works[medium] += count
        _count(states, medium, state, count)
        if reason is not None:
            if reason not in MATCH_STATES or reason == "matched":
                raise SizingError("an unexpected Not matched reason was derived")
            _count(reasons, medium, reason, count)
    return {
        "schema_version": NOT_MATCHED_SIZING_SCHEMA,
        "current_works": works,
        "provider_match_state": {m: dict(sorted(v.items())) for m, v in states.items()},
        "not_matched": {
            medium: {
                "total": sum(reasons[medium].values()),
                **dict(sorted(reasons[medium].items())),
            }
            for medium in MEDIA
        },
    }


def main(argv: Sequence[str] | None = None, *, stdout: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        connection = open_read_only(arguments.database)
        try:
            document = not_matched_sizing(connection)
        finally:
            connection.close()
    except SizingError as error:
        print(f"not_matched_sizing: {error}", file=sys.stderr)
        return 2
    json.dump(document, stdout, indent=2, sort_keys=True)
    stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
