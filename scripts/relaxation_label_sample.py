"""Library links to the fields an ADR-0092 relaxation would fill (Decision 5).

A workbench instrument, deliberately not part of the installed product: run it
from the checkout an install was built from, against a COPY of that install's
application database. It opens the copy read-only, writes nothing and prints,
for each of the five closed relaxations of ADR-0092, one Library link per
field it would admit that the owner has not decided, so the owner can label
them. Each decision is one label; the admission-rule ledger's
``--a6-abstention`` reading then counts them (``scripts/admission_rule_a6.py``).

The fields are the reading's own: a child process replays every current case's
decision, and every decided case's, under this checkout
(``admission_rule_a6.child_a6``), and ``admission_rule_a6.admitted_fields``
names each field a relaxation admits. A field is listed when its case is
current and the owner's latest decision on that library item's field, by the
ledger's rule (``admission_rule_ledger._owner_decisions``), does not exist or
was withdrawn. "Already decided" counts the admitted fields an owner decision
labels, on current cases and on decided cases that are no longer current,
which is the reading's pooled count. A field whose writes are suspended is
still listed: a decision on it is still recorded. Links are listed in case
order.

It refuses, with one fixed line each and in this order: a missing file, a
copy with changes still in its write-ahead log, a file this checkout cannot
open, a store built by another decision-policy version, a checkout whose
decision rules the A6 mirror does not cover, and a replay that fails.

The output holds links and counts only: never a title, author, value, item
identifier, library name or path. A review case identifier is an opaque
content hash.

Usage:
    uv run python scripts/relaxation_label_sample.py \\
        --database /copy/application.sqlite3 \\
        [--origin http://localhost:8080] [--limit 50]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from mechanical_librarian.domain import shadow_decision

# Direct execution puts scripts/ on sys.path; the test import resolves the
# scripts namespace package.
try:
    from scripts import admission_rule_a6 as a6
    from scripts import admission_rule_ledger as ledger
    from scripts import hardcover_series_sample as series_sample
except ImportError:  # pragma: no cover - direct `python scripts/...` execution
    import admission_rule_a6 as a6  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    import admission_rule_ledger as ledger  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]
    import hardcover_series_sample as series_sample  # type: ignore[no-redef,import-not-found]  # reason: same module under both execution styles; only resolvable when scripts/ is sys.path[0]

ROOT = Path(__file__).resolve().parents[1]

_DESCRIPTION = (
    "List Library links to the fields each ADR-0092 relaxation would fill that "
    "you have not decided. Reads a copy of the application database and "
    "writes nothing."
)
_WAL = (
    "The copy has changes that are still in application.sqlite3-wal. "
    "Stop the container, then copy application.sqlite3 again."
)
_ENGINE = (
    "That database was built by a different version of Mechanical Librarian. "
    "Run this from the checkout you built the install from."
)
_MIRROR = "This tool does not cover this checkout's decision rules yet."
# The owner's name for each relaxation; docs/operations/owner-labels.md maps
# each name to its rule id.
NAMES = {
    "authors_one_correlation": "Authors with one agreeing source",
    "title_two_correlations_without_local": (
        "Titles with two agreeing sources and no match in the book file"
    ),
    "publisher_two_correlations_without_local": (
        "Publishers with two agreeing sources and no match in the book file"
    ),
    "language_two_correlations_without_local": (
        "Languages with two agreeing sources and no match in the book file"
    ),
    "published_date_day_one_correlation": (
        "Publication dates to the day with one agreeing source"
    ),
}
_OTHER_VERSION_SQL = (
    "SELECT 1 FROM shadow_decision_outcomes "
    "WHERE json_extract(document,'$.decision_policy_version') IS NOT ? LIMIT 1"
)


@dataclass(frozen=True, slots=True)
class Sample:
    """Per relaxation, the undecided fields as (case, destination), in case
    order, and the labelled ones' count; and the cases the replay lost."""

    fields: dict[str, list[tuple[str, str]]]
    decided: Counter[str]
    failed: int


def read_sample(connection: sqlite3.Connection, probe: a6.A6Probe) -> Sample:
    """Name each admitted field, listed unless the owner decided it."""
    owner = ledger._owner_decisions(connection)
    scope = ledger._CURRENT_SQL
    if owner is not None:
        scope += f" OR {ledger._DECIDED_SQL}"
    items = ledger._case_items(connection, scope)
    decided_items = {
        (*items.get(case_id, (case_id,)), destination)
        for case_id, labels in (owner or {}).items()
        for destination in labels
    }
    fields: dict[str, list[tuple[str, str]]] = {
        rule.rule: [] for rule in a6.RELAXATIONS
    }
    decided: Counter[str] = Counter()
    for case_id, current, case, shadow in a6.owner_cases(connection, owner):
        replayed = probe.cases.get(case_id)
        for item, rule, candidate in a6.admitted_fields(
            case, shadow, probe.scalar, replayed
        ):
            if a6.owner_label(case, item.destination, candidate) is not None:
                decided[rule.rule] += 1
            elif (
                current
                and (*items.get(case_id, (case_id,)), item.destination)
                not in decided_items
            ):
                fields[rule.rule].append((case_id, item.destination))
    return Sample(fields, decided, probe.failed)


def render(sample: Sample, origin: str, limit: int) -> str:
    lines: list[str] = []
    for rule in a6.RELAXATIONS:
        fields = sample.fields[rule.rule]
        if lines:
            lines.append("")
        lines.append(
            f"{NAMES[rule.rule]}: {len(fields)} to decide, "
            f"{sample.decided[rule.rule]} already decided."
        )
        shown = fields[:limit]
        lines.extend(
            f"{origin}/library?lens=all&preview={case_id}&field={destination}"
            for case_id, destination in shown
        )
        if len(fields) > limit:
            lines.append(f"Showing {len(shown)} of {len(fields)}.")
    if sample.failed == 1:
        lines += ["", "1 work could not be checked and is left out."]
    elif sample.failed > 1:
        lines += [
            "",
            f"{sample.failed} works could not be checked and are left out.",
        ]
    return "\n".join(lines) + "\n"


def _sample(
    connection: sqlite3.Connection, database: Path, replay: a6.A6Reader
) -> Sample:
    """Refuse a store or checkout the reading cannot score, then read it."""
    version = shadow_decision.DECISION_POLICY_VERSION
    try:
        foreign = connection.execute(_OTHER_VERSION_SQL, (version,)).fetchone()
    except sqlite3.Error as error:
        raise SystemExit(series_sample._UNREADABLE) from error
    if foreign is not None:
        raise SystemExit(_ENGINE)
    if a6.table_for(version) is None:
        raise SystemExit(_MIRROR)
    try:
        probe = replay(ROOT, database)
        if probe.version != version:
            raise SystemExit(series_sample._UNREADABLE)
        return read_sample(connection, probe)
    except (ledger.Refusal, sqlite3.Error) as error:
        raise SystemExit(series_sample._UNREADABLE) from error


def main(
    argv: list[str] | None = None,
    stdout: TextIO | None = None,
    replay: a6.A6Reader | None = None,
) -> int:
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
        help="how many links to print for each relaxation (default 50)",
    )
    arguments = parser.parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    database: Path = arguments.database
    if not database.is_file():
        raise SystemExit(series_sample._MISSING)
    wal = database.with_name(database.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise SystemExit(_WAL)
    try:
        connection = ledger._open_snapshot(database)
    except (ledger.Refusal, sqlite3.Error) as error:
        raise SystemExit(series_sample._UNREADABLE) from error
    try:
        sample = _sample(connection, database, replay or a6.child_a6)
    finally:
        connection.close()
    output.write(render(sample, arguments.origin, arguments.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
