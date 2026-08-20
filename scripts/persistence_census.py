"""Aggregate-only persistence health census over database copies (#743).

Run post-measurement against COPIES of the application and catalogue
databases, never the live files. Emits JSON: per-table row counts, the
complete index inventory, and ``EXPLAIN QUERY PLAN`` over the hot query
shapes the cold-start report and the attention surface execute, flagging
full-table scans so the index-vs-simplify persistence decision starts from
evidence. Read-only; counts and plan text only — no titles, paths, or
values.

Usage:
    uv run python scripts/persistence_census.py \
        --database /copy/application.sqlite3 \
        [--catalogue /copy/catalogue.sqlite3]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_REVIEW_FROM_SQL,
    _REVIEW_FIELD_ATTENTION_SQL,
    _REVIEW_SEARCH_SQL,
)

# The report statements are imported rather than copied so the census always
# explains what the report actually runs. Direct execution puts scripts/ on
# sys.path; the test import resolves it as the scripts namespace package.
try:
    from scripts import cold_start_report as report_sql
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import cold_start_report as report_sql  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

_HOT_STATEMENTS: dict[str, str] = {
    "attention_field_count": (
        "SELECT count(*) "
        + _CURRENT_REVIEW_FROM_SQL
        + " JOIN review_case_field_facts result "
        "ON result.review_case_id=rc.review_case_id WHERE "
        + _REVIEW_FIELD_ATTENTION_SQL
        + _REVIEW_SEARCH_SQL
    ),
    "library_work_counts": report_sql._LIBRARY_WORK_COUNTS_SQL,
    "attention_field_rows": report_sql._ATTENTION_FIELD_ROWS_SQL,
    "provider_rows": report_sql._PROVIDER_ROWS_SQL,
    "unmatched_works": report_sql._UNMATCHED_WORKS_SQL,
    "duplicate_groups": report_sql._DUPLICATE_GROUPS_SQL,
    "automatic_agreement": report_sql._AUTOMATIC_AGREEMENT_SQL,
    "latest_scans": report_sql._SCAN_ROWS_SQL,
    "work_state_counts": report_sql._WORK_STATE_COUNTS_SQL,
    "current_package_lookup": (
        "SELECT review_case_id,report_id,outcome FROM current_review_packages "
        "WHERE connection_name=? AND library_name=? AND item_id=?"
    ),
    "work_ledger_by_scan": (
        "SELECT state,count(*) FROM review_preparation_work "
        "WHERE scan_id=? GROUP BY state"
    ),
    "pending_decision_join": (
        "SELECT decision.review_case_id,decision.destination "
        "FROM automatic_decisions decision "
        "JOIN current_review_case_lineages lineage "
        "ON lineage.review_case_id=decision.review_case_id "
        "WHERE json_extract(decision.document,'$.outcome')='candidate'"
    ),
}


def _open_read_only(path: Path) -> sqlite3.Connection:
    absolute = str(path.absolute())
    connection = sqlite3.connect(f"file:{quote(absolute, safe='/')}?mode=ro", uri=True)
    connection.row_factory = None
    # The planner only needs the function to exist to prepare statements that
    # name it; census plans never evaluate rows through it.
    connection.create_function(
        "ml_casefold", 1, lambda value: value, deterministic=True
    )
    return connection


def _table_rows(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = connection.execute(
            f'SELECT count(*) FROM "{table}"'  # noqa: S608 - names from sqlite_master
        ).fetchone()[0]
    return counts


def _indexes(connection: sqlite3.Connection) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' ORDER BY name"
    ).fetchall()
    return [
        {
            "name": name,
            "table": table,
            "implicit": sql is None,
        }
        for name, table, sql in rows
    ]


def _plans(connection: sqlite3.Connection) -> dict[str, dict[str, object]]:
    plans: dict[str, dict[str, object]] = {}
    for label, statement in _HOT_STATEMENTS.items():
        try:
            # Placeholders are bound to NULL: the planner needs a complete
            # binding set, and the plan shape does not depend on the values.
            rows = connection.execute(
                "EXPLAIN QUERY PLAN " + statement,
                (None,) * statement.count("?"),
            ).fetchall()
        except sqlite3.Error as error:
            plans[label] = {"error": type(error).__name__, "detail": str(error)}
            continue
        details = [str(row[-1]) for row in rows]
        plans[label] = {
            "steps": details,
            "full_scans": [
                step
                for step in details
                if step.startswith("SCAN") and "USING" not in step
            ],
        }
    return plans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--catalogue", type=Path)
    arguments = parser.parse_args(argv)

    application = _open_read_only(arguments.database)
    try:
        census: dict[str, object] = {
            "schema_version": "persistence-census-v1",
            "application": {
                "table_rows": _table_rows(application),
                "indexes": _indexes(application),
                "plans": _plans(application),
            },
        }
    finally:
        application.close()

    if arguments.catalogue is not None:
        catalogue = _open_read_only(arguments.catalogue)
        try:
            census["catalogue"] = {
                "table_rows": _table_rows(catalogue),
                "indexes": _indexes(catalogue),
            }
        finally:
            catalogue.close()

    json.dump(census, sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
