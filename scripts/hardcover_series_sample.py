"""Library links to the ebook series fields only Hardcover disputes (#1811).

A workbench instrument, deliberately not part of the installed product: run it
from the checkout an install was built from, against a COPY of that install's
application database. It opens the copy read-only, writes nothing and prints
one Library link per field to stdout, so the owner can label the fields during
the qualification run (#1418, #1747).

A field is listed when the product's own single-source predicate holds it
(``_SINGLE_SOURCE_CHOICE_SQL``: one alternative, one source, nothing to
corroborate it), it is the series of a current EPUB case, and that one source
is Hardcover. On a series the predicate holds only a replacement of a series
the owner already has: an empty series with one source is proposed, not held.
Such a field is choosable on the work's page but not in Attention. A field
the owner already decided, or whose writes are suspended, is left out by the
owner-decision and suspension clauses of the queue's own
``_REVIEW_FIELD_ATTENTION_CLEAR_SQL``. A field Automatic kept stays listed and
is marked, because the owner's choice on it is still a label. Fields
whose one source is not Hardcover are counted, not listed. Links are listed
oldest admitted first.

The output holds links and counts only: never a title, author, series, item
identifier, library name or path. A review case identifier is an opaque
content hash.

Usage:
    uv run python scripts/hardcover_series_sample.py \
        --database /copy/application.sqlite3 \
        [--origin http://localhost:8080] [--limit 50]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import TextIO
from urllib.parse import quote, urlsplit

from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_REVIEW_FROM_SQL,
    _REVIEW_FIELD_ATTENTION_CLEAR_SQL,
    _active_generation_automatic_sql,
)
from mechanical_librarian.adapters.persistence.schema import _SINGLE_SOURCE_CHOICE_SQL
from mechanical_librarian.domain.reviewer_decision_summary import correlation_identity

DEFAULT_ORIGIN = "http://localhost:8080"
DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def _clear_clauses(start: str, stop: str | None) -> str:
    """The queue's clear fragment from `start` up to `stop`, sliced unchanged.

    Each marker must occur exactly once, so a reshaped fragment fails at
    import rather than composing a different exclusion.
    """
    clear = _REVIEW_FIELD_ATTENTION_CLEAR_SQL
    for marker in (start, stop):
        if marker is not None and clear.count(marker) != 1:
            raise RuntimeError("the review clear fragment changed shape")
    return clear[clear.index(start) : None if stop is None else clear.index(stop)]


# The clear fragment's owner-decision clause (the latest owner decision of the
# case's episode, unless a withdrawal) and its suspension clause. Its
# automatic-decision clause is left out, so a field Automatic kept stays
# listed; its description and cover clauses cannot fire on a series.
_OWNER_DECISION_CLEAR_SQL = _clear_clauses(
    " AND COALESCE((SELECT decision.outcome FROM human_decisions decision ",
    "AND NOT EXISTS (SELECT 1 FROM current_description_decisions ",
)
_SUSPENSION_CLEAR_SQL = _clear_clauses(
    "AND NOT EXISTS (SELECT 1 FROM field_effect_suspensions ", None
)

# Each row is a current EPUB case's series field the single-source predicate
# holds, with whether its one source is Hardcover, whether Automatic kept the
# owner's series in the active generation, and the case's admission order. The
# inner `review_case_field_facts result` shadows the outer `json_each ...
# result` inside its EXISTS, which is what the clear clauses'
# `result.destination` reads; `c` and `rc` stay the outer ones.
_FIELDS_SQL = (
    "SELECT rc.review_case_id,"
    "EXISTS (SELECT 1 FROM json_each(result.value,'$.alternatives') source "
    "WHERE json_extract(source.value,'$.assessment')<>'preserves_target' "
    "AND json_extract(source.value,'$.correlations')=json_array(?)),"
    "EXISTS (SELECT 1 FROM automatic_decisions automatic "
    "WHERE automatic.review_case_id=rc.review_case_id "
    "AND automatic.destination='series' "
    "AND json_extract(automatic.document,'$.outcome')='keep_current' AND "
    + _active_generation_automatic_sql("automatic")
    + "),"
    "(SELECT max(admission.rowid) FROM review_case_transitions admission "
    "WHERE admission.to_review_case_id=rc.review_case_id) "
    + _CURRENT_REVIEW_FROM_SQL
    + ",json_each(rc.document,'$.results') result "
    "WHERE json_extract(mr.document,'$.file.format')='epub' "
    "AND json_extract(result.value,'$.destination')='series' AND "
    + _SINGLE_SOURCE_CHOICE_SQL
    + " AND EXISTS (SELECT 1 FROM review_case_field_facts result "
    "WHERE result.review_case_id=rc.review_case_id AND result.destination='series'"
    + _OWNER_DECISION_CLEAR_SQL
    + _SUSPENSION_CLEAR_SQL
    + ")"
)

_DESCRIPTION = (
    "List Library links to the ebook series fields where only Hardcover names "
    "a series, and it differs from yours. Reads a copy of the application "
    "database and writes nothing."
)
_MISSING = "No file at the --database path. Give it a copy of application.sqlite3."
_AUTOMATIC_KEPT = "  Automatic kept your series."
_UNREADABLE = (
    "This checkout cannot read that database. "
    "Copy it from an install built from this checkout."
)


def _open_read_only(path: Path) -> sqlite3.Connection:
    absolute = str(path.absolute())
    connection = sqlite3.connect(f"file:{quote(absolute, safe='/')}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _origin(value: str) -> str:
    """An http or https origin: a host and an optional port, nothing more."""
    try:
        parts = urlsplit(value)
        port_valid = parts.port is None or parts.port > 0
    except ValueError:
        port_valid = False
    if (
        not port_valid
        or parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or "?" in value
        or "#" in value
        or any(character.isspace() for character in value)
        or parts.netloc.endswith(":")
    ):
        raise argparse.ArgumentTypeError(
            "give the address as http://host:port or https://host"
        )
    return f"{parts.scheme}://{parts.netloc}"


def _limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        limit = 0
    if not 1 <= limit <= MAX_LIMIT:
        raise argparse.ArgumentTypeError("give a whole number from 1 to 500")
    return limit


def read_fields(
    connection: sqlite3.Connection,
) -> tuple[list[tuple[str, bool]], int]:
    """Hardcover's fields oldest admitted first, and the other sources' count.

    Each field is its case id and whether Automatic kept the owner's series.

    The counts and the list read one snapshot inside one read transaction.
    """
    connection.execute("BEGIN")
    try:
        rows = connection.execute(
            _FIELDS_SQL, (correlation_identity("provider:hardcover"),)
        ).fetchall()
    finally:
        connection.execute("ROLLBACK")
    hardcover: list[tuple[int, str, bool]] = []
    other = 0
    for row in rows:
        case_id, from_hardcover, kept, admitted = row
        if (
            type(case_id) is not str
            or from_hardcover not in {0, 1}
            or kept not in {0, 1}
            or type(admitted) is not int
        ):
            raise ValueError("unexpected field row")
        if from_hardcover:
            hardcover.append((admitted, case_id, bool(kept)))
        else:
            other += 1
    return [(case_id, kept) for _, case_id, kept in sorted(hardcover)], other


def render(fields: list[tuple[str, bool]], other: int, origin: str, limit: int) -> str:
    total = len(fields)
    if total == 0:
        lines = ["No ebook has a series that only Hardcover disputes."]
    elif total == 1:
        lines = [
            "Only Hardcover disputes the series of 1 ebook. "
            "Choose its series in Library."
        ]
    else:
        lines = [
            f"Only Hardcover disputes the series of {total} ebooks. "
            "Choose each series in Library."
        ]
    shown = fields[:limit]
    lines.extend(
        f"{origin}/library?lens=all&preview={case_id}&field=series"
        + (_AUTOMATIC_KEPT if kept else "")
        for case_id, kept in shown
    )
    if total > limit:
        lines.append(f"Showing the oldest {len(shown)} of {total}.")
    if other == 1:
        lines.append(
            "One source other than Hardcover disputes the series of 1 more "
            "ebook. It is not listed."
        )
    elif other > 1:
        lines.append(
            "One source other than Hardcover disputes the series of "
            f"{other} more ebooks. They are not listed."
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument(
        "--database", required=True, type=Path, help="a copy of application.sqlite3"
    )
    parser.add_argument(
        "--origin",
        default=DEFAULT_ORIGIN,
        type=_origin,
        help=(
            "the address you open Mechanical Librarian at "
            "(default http://localhost:8080)"
        ),
    )
    parser.add_argument(
        "--limit",
        default=DEFAULT_LIMIT,
        type=_limit,
        help="how many links to print (default 50)",
    )
    arguments = parser.parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    database: Path = arguments.database
    if not database.is_file():
        raise SystemExit(_MISSING)
    try:
        connection = _open_read_only(database)
    except sqlite3.Error as error:
        raise SystemExit(_UNREADABLE) from error
    try:
        fields, other = read_fields(connection)
    except (sqlite3.Error, ValueError) as error:
        raise SystemExit(_UNREADABLE) from error
    finally:
        connection.close()
    output.write(render(fields, other, arguments.origin, arguments.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
