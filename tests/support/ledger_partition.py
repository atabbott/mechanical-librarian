"""Reopen a store whose derived partition was regenerated (ADR-0108).

`regenerate-derived` drops derived state and recreates it empty, and
preparation rebuilds it. This module does that to a copy of a store: it
deletes every derived row, puts back the setup rows a new schema writes into
derived tables, reopens the copy through the product's own open validation and
decodes every ledger document. A caller whose store reached a kind of ledger
row so proves that row stands without the derived rows it was made against.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceCandidate,
)
from mechanical_librarian.domain.field_effect import (
    FieldEffectNoChange,
    FieldEffectOperation,
)
from mechanical_librarian.domain.human_decision import (
    HUMAN_DECISION_EVENT_SCHEMA,
    HumanDecision,
)

# Ledger tables that still reference a derived row, so a regeneration takes
# their rows too. #2038 C2b deletes the manual holds; the record replaces them.
UNTIL_C2B = frozenset({"field_effect_manual_holds"})
_COPIES = {
    "human_decisions": ("display_title", "target_field_document", "selected_form"),
    "field_effects": ("display_title",),
    "field_effect_no_changes": ("display_title",),
    "owner_authored_descriptions": ("candidate_document",),
    "owner_cover_selections": (
        "transport_sha256",
        "width",
        "height",
        "offered_arts",
        "report_format",
    ),
}


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        name
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }


def _ledger_rows(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {
        table: sorted(connection.execute(f"SELECT * FROM {table}"), key=repr)
        for table in sorted(schema.LEDGER_TABLES - UNTIL_C2B)
    }


def _derived_setup() -> dict[str, list[tuple[object, ...]]]:
    """Each derived table and the rows a new schema writes into it."""
    fresh = sqlite3.connect(":memory:")
    try:
        schema._create_schema(fresh)
        return {
            table: fresh.execute(f"SELECT * FROM {table}").fetchall()
            for table in sorted(_tables(fresh) - schema.LEDGER_TABLES)
        }
    finally:
        fresh.close()


def _without_triggers[T](path: Path, change: Callable[[sqlite3.Connection], T]) -> T:
    """Run ``change`` on the store at ``path`` with every trigger set aside,
    as a hostile edit or a table drop would, then restore each trigger by its
    exact text, so the schema still compares equal; return what it returns."""
    connection = sqlite3.connect(path)
    try:
        triggers = connection.execute(
            "SELECT name,sql FROM sqlite_schema WHERE type='trigger' ORDER BY name"
        ).fetchall()
        connection.execute("PRAGMA foreign_keys=OFF")
        for name, _ in triggers:
            connection.execute(f'DROP TRIGGER "{name}"')
        result = change(connection)
        for _, sql in triggers:
            connection.execute(sql)
        connection.commit()
    finally:
        connection.close()
    return result


def regenerate(path: Path) -> None:
    """Delete every derived row and put back what a new schema sets up."""
    setup = _derived_setup()

    def change(connection: sqlite3.Connection) -> None:
        for table in (*setup, *sorted(UNTIL_C2B)):
            connection.execute(f"DELETE FROM {table}")
        for table, rows in setup.items():
            for row in rows:
                connection.execute(
                    f"INSERT INTO {table} VALUES({','.join('?' * len(row))})", row
                )

    _without_triggers(path, change)


def rewrite(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> int:
    """Change stored rows behind every guard, as a hostile edit would, and
    return how many rows changed."""
    return _without_triggers(
        path, lambda connection: connection.execute(sql, parameters).rowcount
    )


def rewrite_no_change(path: Path, column: str, value: str) -> None:
    """Change one column of the store's single no-change row and give the row
    the event id that change implies, so only a check against another row can
    see it."""

    def change(connection: sqlite3.Connection) -> None:
        names = (
            "decision_id",
            "review_case_id",
            "report_id",
            "connection_name",
            "library_name",
            "work_id",
            "destination",
            "target_fingerprint",
            "target_version",
            "occurred_at",
            "origin",
        )
        (row,) = connection.execute(
            f"SELECT {','.join(names)} FROM field_effect_no_changes"
        ).fetchall()
        values = {**dict(zip(names, row, strict=True)), column: value}
        record = FieldEffectNoChange.create(
            decision_id=values["decision_id"],
            review_case_id=values["review_case_id"],
            report_id=values["report_id"],
            connection=values["connection_name"],
            library=values["library_name"],
            work_id=values["work_id"],
            destination=values["destination"],
            target_fingerprint=values["target_fingerprint"],
            target_version=values["target_version"],
            occurred_at=values["occurred_at"],
            origin=values["origin"],
        )
        connection.execute(
            f"UPDATE field_effect_no_changes SET {column}=?,event_id=?",
            (value, record.event_id),
        )

    _without_triggers(path, change)


def copied_store(repository: SQLiteMetadataReportRepository, root: Path) -> Path:
    """A copy of the store under ``root``, which this creates."""
    root.mkdir(mode=0o700)
    path = root / "copy.sqlite3"
    copy = sqlite3.connect(path)
    try:
        repository._connection.backup(copy)
        copy.execute("PRAGMA journal_mode=DELETE")
    finally:
        copy.close()
    return path


def opens(path: Path) -> bool:
    """Whether the product's open validation admits the store."""
    try:
        open_repository(path, create=False).close()
    except MetadataReportError:
        return False
    return True


def decode_ledger(connection: sqlite3.Connection) -> Counter[str]:
    """Decode every ledger document open validation does not already read.

    Return how many rows each ledger table holds, and per copy column how
    many rows carry it, so a caller can show what its store reached.
    """
    counted: Counter[str] = Counter()
    for table in schema.LEDGER_TABLES:
        (counted[table],) = connection.execute(
            f"SELECT count(*) FROM {table}"
        ).fetchone()
    for table, columns in _COPIES.items():
        for column in columns:
            (counted[f"{table}.{column}"],) = connection.execute(
                f"SELECT count({column}) FROM {table}"
            ).fetchone()
    keys: dict[str, tuple[str, str, str]] = {}
    for decision_id, document, item, target_field in connection.execute(
        "SELECT decision_id,document,json_array(connection_name,library_name,"
        "item_id),target_field_document FROM human_decisions"
    ):
        decision = HumanDecision.from_json(document)
        assert decision.decision_id == decision_id
        assert type(json.loads(target_field)) in {dict, type(None)}
        keys[decision_id] = tuple(json.loads(item))
    for decision_id, document, item in connection.execute(
        "SELECT decision_id,document,json_array(connection_name,library_name,"
        "item_id) FROM human_decision_events"
    ):
        event = json.loads(document)
        assert event["schema_version"] == HUMAN_DECISION_EVENT_SCHEMA
        assert event["decision_id"] == decision_id
        assert keys[decision_id] == tuple(json.loads(item))
    for decision_id, document in connection.execute(
        "SELECT decision_id,document FROM owner_cover_selections"
    ):
        chosen = HumanDecision.from_json(json.loads(document)["decision_document"])
        assert chosen.decision_id == decision_id
    for decision_id, candidate_id, document, candidate_document in connection.execute(
        "SELECT decision_id,candidate_id,document,candidate_document "
        "FROM owner_authored_descriptions"
    ):
        authored = AuthoredDescriptionDecision.from_json(document)
        candidate = DescriptionAdviceCandidate.from_json(candidate_document)
        assert (authored.decision_id, candidate.candidate_id) == (
            decision_id,
            candidate_id,
        )
    for plan_id, document in connection.execute(
        "SELECT plan_id,document FROM field_effects"
    ):
        assert FieldEffectOperation.from_json(document).plan.plan_id == plan_id
    for row in connection.execute(
        "SELECT decision_id,review_case_id,report_id,connection_name,library_name,"
        "work_id,destination,target_fingerprint,target_version,occurred_at,origin,"
        "event_id FROM field_effect_no_changes"
    ):
        record = FieldEffectNoChange.create(
            decision_id=row[0],
            review_case_id=row[1],
            report_id=row[2],
            connection=row[3],
            library=row[4],
            work_id=row[5],
            destination=row[6],
            target_fingerprint=row[7],
            target_version=row[8],
            occurred_at=row[9],
            origin=row[10],
        )
        assert record.event_id == row[11]
    return counted


def reopen_without_derived_rows(
    repository: SQLiteMetadataReportRepository, root: Path
) -> Counter[str]:
    """Copy the store under ``root``, regenerate its derived partition, and
    reopen it: the ledger must open unchanged and decode."""
    path = copied_store(repository, root)
    connection = sqlite3.connect(path)
    try:
        before = _ledger_rows(connection)
    finally:
        connection.close()
    regenerate(path)
    reopened = open_repository(path, create=False)
    try:
        connection = reopened._connection
        assert _ledger_rows(connection) == before
        assert {
            table: connection.execute(f"SELECT * FROM {table}").fetchall()
            for table in _derived_setup()
        } == _derived_setup()
        return decode_ledger(connection)
    finally:
        reopened.close()
