"""SQLite connection, migration, and transaction foundation."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping


class SchemaError(RuntimeError):
    """Database schema is missing, incompatible, or has been modified."""


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]
    expected_tables: frozenset[str]
    checksum_source: str | None = None
    expected_table_definitions: Mapping[str, str] | None = None
    expected_objects: frozenset[tuple[str, str]] = frozenset()

    @property
    def checksum(self) -> str:
        source = self.checksum_source if self.checksum_source is not None else "\n".join(self.statements)
        return hashlib.sha256(source.encode("utf-8")).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        (
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
            "CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "CREATE TABLE audit_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL)",
        ),
        frozenset({"schema_migrations", "instance_metadata", "audit_events"}),
        """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL);
        CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE audit_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL);
    """,
    ),
)


def connect(path: Path) -> sqlite3.Connection:
    """Open one application-owned connection with the required safety settings."""

    connection: sqlite3.Connection | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=True)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except (OSError, sqlite3.Error):
        if connection is not None:
            connection.close()
        raise SchemaError("database connection could not be opened") from None


def _user_schema_objects(connection: sqlite3.Connection) -> set[tuple[str, str]]:
    return {
        (kind, name)
        for kind, name in connection.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table', 'index', 'trigger', 'view') AND name NOT LIKE 'sqlite_%'"
        )
    }


def _known_migrations() -> dict[int, Migration]:
    known = {migration.version: migration for migration in MIGRATIONS}
    if len(known) != len(MIGRATIONS) or tuple(sorted(known)) != tuple(range(1, len(known) + 1)):
        raise SchemaError("application migrations are not a contiguous ordered sequence")
    return known


def _expected_tables(known: dict[int, Migration], versions: set[int]) -> set[str]:
    return set().union(*(known[version].expected_tables for version in versions)) if versions else set()


def _expected_schema_objects(known: dict[int, Migration], versions: set[int]) -> set[tuple[str, str]]:
    objects: set[tuple[str, str]] = set()
    for version in versions:
        migration = known[version]
        objects.update(("table", table) for table in migration.expected_tables)
        objects.update(migration.expected_objects)
        for statement in migration.statements:
            match = re.match(
                r"\s*CREATE\s+(?:UNIQUE\s+)?(INDEX|TRIGGER|VIEW)\s+([A-Za-z_][A-Za-z0-9_]*)\b",
                statement,
                re.IGNORECASE,
            )
            if match is not None:
                objects.add((match.group(1).lower(), match.group(2)))
    return objects


def _normalise_schema_sql(sql: str) -> str:
    return " ".join(sql.split()).casefold().rstrip(";")


def _expected_table_definitions(known: dict[int, Migration], versions: set[int]) -> dict[str, str]:
    definitions: dict[str, str] = {}
    for version in sorted(versions):
        migration = known[version]
        declared = migration.expected_table_definitions
        if declared is not None:
            definitions.update({name: _normalise_schema_sql(sql) for name, sql in declared.items()})
            continue
        for statement in migration.statements:
            match = re.match(r"\s*CREATE\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\b", statement, re.IGNORECASE)
            if match is not None:
                definitions[match.group(1)] = _normalise_schema_sql(statement)
    if set(definitions) != _expected_tables(known, versions):
        raise SchemaError("application migration does not declare every managed table")
    return definitions


def _validate_statement(statement: str) -> None:
    operation = statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else ""
    if operation not in {"ALTER", "CREATE", "DELETE", "DROP", "INSERT", "UPDATE", "WITH"}:
        raise SchemaError("migration contains a transaction-control or unsupported statement")


def _validate_existing_schema(connection: sqlite3.Connection, known: dict[int, Migration]) -> dict[int, str]:
    objects = _user_schema_objects(connection)
    tables = {name for kind, name in objects if kind == "table"}
    known_objects = _expected_schema_objects(known, set(known))
    if "schema_migrations" not in tables:
        if objects:
            if objects & known_objects:
                raise SchemaError("database schema is partially applied; restore from a verified backup")
            raise SchemaError("database schema contains unrecognized objects; inspect it before retrying")
        return {}

    applied = dict(connection.execute("SELECT version, checksum FROM schema_migrations"))
    versions = set(applied)
    if versions - set(known):
        raise SchemaError("database schema is newer than this application")
    if versions != set(range(1, len(versions) + 1)):
        raise SchemaError("database schema is partially applied; restore from a verified backup")
    if not versions and objects:
        raise SchemaError("database schema is partially applied; restore from a verified backup")
    for version, checksum in applied.items():
        if known[version].checksum != checksum:
            raise SchemaError("database migration checksum does not match")

    expected = _expected_schema_objects(known, versions)
    if not expected.issubset(objects):
        raise SchemaError("database schema is partially applied; restore from a verified backup")
    if objects - expected:
        raise SchemaError("database schema contains unrecognized objects; inspect it before retrying")
    expected_definitions = _expected_table_definitions(known, versions)
    actual_definitions = {
        name: _normalise_schema_sql(sql)
        for name, sql in connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'")
        if name in expected_definitions and sql is not None
    }
    if actual_definitions != expected_definitions:
        raise SchemaError("database schema definition does not match its recorded migration")
    return applied


def migrate(connection: sqlite3.Connection) -> int:
    """Apply ordered migrations atomically, rejecting tampered or newer schemas."""

    try:
        connection.execute("BEGIN EXCLUSIVE")
        known = _known_migrations()
        applied = _validate_existing_schema(connection, known)
        for migration in MIGRATIONS:
            if migration.version not in applied:
                for statement in migration.statements:
                    _validate_statement(statement)
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)", (migration.version, migration.checksum))
                applied[migration.version] = migration.checksum
                _validate_existing_schema(connection, known)
        connection.commit()
    except SchemaError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except (sqlite3.Error, OSError):
        if connection.in_transaction:
            connection.rollback()
        raise SchemaError("migration failed and was rolled back") from None
    return max(known)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Provide exactly one explicit transaction; nested ownership is rejected."""

    if connection.in_transaction:
        raise RuntimeError("nested transactions are not supported")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
