"""The ledger's migrations (ADR-0108, "Ledger format and migrations").

Each step migrates the ledger from exactly one `ledger_format_version` to the
next, in code: no generic SQL, no configured step, no downgrade and no
import. Only `regenerate-derived` runs the chain, once on admission's private
scratch copy and once inside its one transaction, straight after the derived
drop, so a step reads only its connection and is deterministic.

A step declares the earlier ledger objects it drops or alters (`replaced`)
and the ones it creates or alters (`created`), so the ledger objects of an
earlier format are derived backwards from this build's, and admission checks
an earlier ledger at its own format.

The chain keeps what the owner decided: `human_decisions` and its order, the
record (`owner_field_answers`, its question halves included) and Resume's
history (`owner_field_events`) leave it exactly as they entered, over the
columns they entered with, and no authority or consent appears, disappears or
becomes current. Authority a migration invalidates becomes history through
the revocation that follows it in the same transaction, and a rewritten
resolution lapses that library's consents because each consent binds the
resolution it was granted under.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from mechanical_librarian.application.metadata_report import MetadataReportError

LedgerObject = tuple[str, str, str, str | None]  # type, name, owning table, sql

_INCOMPATIBLE = "database is unavailable or incompatible"
# Each table the chain must leave exactly as it was, in stored order.
_PRESERVED = (
    ("human_decisions", "rowid"),
    ("owner_field_answers", "connection_name,library_name,item_id,destination"),
    ("owner_field_events", "rowid"),
)
# Each authority table, by its key, whose rows the chain never adds, removes
# or makes current.
_AUTHORITY = (
    ("field_effect_authorities", "decision_id"),
    ("field_effect_consents", "consent_id"),
)


@dataclass(frozen=True, slots=True)
class LedgerMigration:
    """One step, from ledger format `source` to exactly `source + 1`."""

    source: int
    migrate: Callable[[sqlite3.Connection], None]
    # The source format's ledger objects this step drops or alters, as stored.
    replaced: tuple[LedgerObject, ...] = ()
    # The (type, name) of each target-format object it creates or alters.
    created: frozenset[tuple[str, str]] = frozenset()


# This build's chain, in order of `source`.
LEDGER_MIGRATIONS: tuple[LedgerMigration, ...] = ()


def steps_from(version: int, current: int) -> tuple[LedgerMigration, ...] | None:
    """The steps from `version` to `current` in order, or None when
    `version` is not earlier than `current` or the chain does not reach it."""
    if version >= current:
        return None
    by_source = {step.source: step for step in LEDGER_MIGRATIONS}
    steps = [by_source.get(source) for source in range(version, current)]
    if any(step is None for step in steps):
        return None
    return tuple(step for step in steps if step is not None)


def ledger_objects_at(
    version: int, current: int, build_objects: tuple[LedgerObject, ...]
) -> tuple[LedgerObject, ...]:
    """The ledger objects of format `version`, worked backwards from this
    build's: each step from the newest down takes away what it created and
    puts back what it replaced, in the replaced object's place."""
    if version == current:
        return build_objects
    steps = steps_from(version, current)
    if steps is None:
        raise MetadataReportError(_INCOMPATIBLE)
    objects = list(build_objects)
    for step in reversed(steps):
        replaced = {(value[0], value[1]): value for value in step.replaced}
        earlier: list[LedgerObject] = []
        for value in objects:
            key = (value[0], value[1])
            if key not in step.created:
                earlier.append(value)
            elif key in replaced:
                earlier.append(replaced.pop(key))
        objects = earlier + list(replaced.values())
    return tuple(objects)


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        '"' + str(row[1]).replace('"', '""') + '"'
        for row in connection.execute(f"PRAGMA table_info({table})")
    ]


def _preserved(
    connection: sqlite3.Connection, columns: dict[str, list[str]]
) -> list[list[tuple[object, ...]]]:
    return [
        connection.execute(
            f"SELECT {','.join(columns[table])} FROM {table} ORDER BY {order}"
        ).fetchall()
        for table, order in _PRESERVED
    ]


def _currentness(connection: sqlite3.Connection) -> list[dict[object, object]]:
    return [
        dict(connection.execute(f"SELECT {key},current FROM {table}").fetchall())
        for table, key in _AUTHORITY
    ]


def migrate_ledger(connection: sqlite3.Connection, version: int, current: int) -> None:
    """Run each step from `version` to `current` in the caller's transaction,
    naming each step's target format in `ledger_meta` after it runs; refuse
    when the chain does not reach `version` or changed what it must keep."""
    if version == current:
        return
    steps = steps_from(version, current)
    if steps is None:
        raise MetadataReportError(_INCOMPATIBLE)
    columns = {table: _columns(connection, table) for table, _ in _PRESERVED}
    kept = _preserved(connection, columns)
    authority = _currentness(connection)
    for step in steps:
        if connection.execute(
            "SELECT ledger_format_version FROM ledger_meta WHERE singleton=1"
        ).fetchone() != (step.source,):
            raise MetadataReportError(_INCOMPATIBLE)
        try:
            step.migrate(connection)
        except (sqlite3.Error, MetadataReportError):
            raise
        except Exception as error:
            # A step's own failure is the run's one value-free refusal, on
            # admission's dry run as in the transaction, never internal text.
            raise MetadataReportError(_INCOMPATIBLE) from error
        connection.execute(
            "UPDATE ledger_meta SET ledger_format_version=? WHERE singleton=1",
            (step.source + 1,),
        )
    after = _currentness(connection)
    if _preserved(connection, columns) != kept or any(
        then.keys() != now.keys() or any(now[key] and not then[key] for key in now)
        for then, now in zip(authority, after, strict=True)
    ):
        raise MetadataReportError(_INCOMPATIBLE)


__all__ = [
    "LEDGER_MIGRATIONS",
    "LedgerMigration",
    "LedgerObject",
    "ledger_objects_at",
    "migrate_ledger",
    "steps_from",
]
