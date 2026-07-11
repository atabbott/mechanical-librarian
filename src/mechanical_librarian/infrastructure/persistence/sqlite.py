"""SQLite connection, migration, and transaction foundation."""

from __future__ import annotations

import hashlib
import re
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from mechanical_librarian.domain.audit_values import SAFE_AUDIT_SUMMARIES


class SchemaError(RuntimeError):
    """Database schema is missing, incompatible, or has been modified."""


@dataclass(frozen=True)
class Migration:
    version: int
    statements: tuple[str, ...]
    expected_tables: frozenset[str]
    checksum_source: str | None = None
    expected_table_definitions: Mapping[str, str] | None = None

    @property
    def checksum(self) -> str:
        source = self.checksum_source if self.checksum_source is not None else "\n".join(self.statements)
        return hashlib.sha256(source.encode("utf-8")).hexdigest()


_SAFE_AUDIT_SUMMARIES_SQL = ", ".join(
    f"'{summary}'" for summary in sorted(SAFE_AUDIT_SUMMARIES)
)
_AUDIT_EVENTS_TABLE_SQL = (
    "CREATE TABLE audit_events ("
    "id TEXT PRIMARY KEY CHECK (typeof(id) = 'text' AND length(id) BETWEEN 1 AND 64 "
    "AND id GLOB '[a-z]*' AND id NOT GLOB '*[^a-z0-9_-]*'), "
    "kind TEXT NOT NULL CHECK (typeof(kind) = 'text' AND length(kind) BETWEEN 1 AND 64 "
    "AND kind GLOB '[a-z]*' AND kind NOT GLOB '*[^a-z0-9_-]*'), "
    "created_at TEXT NOT NULL CHECK (typeof(created_at) = 'text' AND length(created_at) = 27 "
    "AND created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9][0-9][0-9][0-9]Z' "
    "AND strftime('%Y-%m-%d', created_at) IS NOT NULL "
    "AND strftime('%Y-%m-%d', created_at) = substr(created_at, 1, 10) "
    "AND strftime('%H:%M:%S', created_at) IS NOT NULL "
    "AND strftime('%H:%M:%S', created_at) = substr(created_at, 12, 8) "
    "AND CAST(substr(created_at, 1, 4) AS INTEGER) BETWEEN 1 AND 9999 "
    "AND CAST(substr(created_at, 12, 2) AS INTEGER) BETWEEN 0 AND 23 "
    "AND CAST(substr(created_at, 15, 2) AS INTEGER) BETWEEN 0 AND 59 "
    "AND CAST(substr(created_at, 18, 2) AS INTEGER) BETWEEN 0 AND 59), "
    "summary TEXT NOT NULL CHECK (typeof(summary) = 'text' AND summary IN ("
    f"{_SAFE_AUDIT_SUMMARIES_SQL})))"
)


MIGRATIONS = (
    Migration(
        1,
        (
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL)",
            "CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            _AUDIT_EVENTS_TABLE_SQL,
            "CREATE INDEX audit_events_by_created_at ON audit_events(created_at DESC, id DESC)",
        ),
        frozenset({"schema_migrations", "instance_metadata", "audit_events"}),
    ),
)


def connect(path: Path) -> sqlite3.Connection:
    """Open one application-owned connection without changing durable state."""

    connection: sqlite3.Connection | None = None
    try:
        if path == Path(":memory:"):
            raise SchemaError("durable state requires a filesystem database")
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=True)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except SchemaError:
        if connection is not None:
            connection.close()
        raise
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
    for version in sorted(versions):
        migration = known[version]
        objects.update(("table", table) for table in migration.expected_tables)
        for statement in migration.statements:
            declaration = _schema_object_statement(statement)
            if declaration is None:
                continue
            operation, object_key = declaration
            if operation == "CREATE":
                objects.add(object_key)
            else:
                objects.discard(object_key)
    return objects


def _expected_non_table_definitions(known: dict[int, Migration], versions: set[int]) -> dict[tuple[str, str], str]:
    definitions: dict[tuple[str, str], str] = {}
    for version in sorted(versions):
        for statement in known[version].statements:
            declaration = _schema_object_statement(statement)
            if declaration is None:
                continue
            operation, object_key = declaration
            if operation == "CREATE":
                definitions[object_key] = _normalise_schema_sql(statement)
            else:
                definitions.pop(object_key, None)
    return definitions


def _schema_object_statement(statement: str) -> tuple[str, tuple[str, str]] | None:
    quoted_name = r'(?:[A-Za-z_][A-Za-z0-9_]*|"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])'
    match = re.match(
        rf"\s*(CREATE|DROP)\s+(?:UNIQUE\s+)?(INDEX|TRIGGER|VIEW)\s+"
        rf"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?({quoted_name})(?=\s|\(|;|$)",
        statement,
        re.IGNORECASE,
    )
    if match is None:
        return None
    name = match.group(3)
    if name.startswith('"'):
        name = name[1:-1].replace('""', '"')
    elif name.startswith("`"):
        name = name[1:-1].replace("``", "`")
    elif name.startswith("["):
        name = name[1:-1]
    return match.group(1).upper(), (match.group(2).lower(), name)


def _normalise_schema_sql(sql: str) -> str:
    without_idempotency_clause = re.sub(
        r"^(\s*CREATE\s+(?:UNIQUE\s+)?(?:INDEX|TRIGGER|VIEW))\s+IF\s+NOT\s+EXISTS\s+",
        r"\1 ",
        sql,
        flags=re.IGNORECASE,
    )
    canonical: list[str] = []
    pending_space = False
    position = 0
    while position < len(without_idempotency_clause):
        character = without_idempotency_clause[position]
        if character.isspace():
            pending_space = bool(canonical)
            position += 1
            continue
        if pending_space:
            canonical.append(" ")
            pending_space = False
        if character in {"'", '"', "`", "["}:
            closing = "]" if character == "[" else character
            canonical.append(character)
            position += 1
            while position < len(without_idempotency_clause):
                quoted_character = without_idempotency_clause[position]
                canonical.append(quoted_character)
                position += 1
                if quoted_character != closing:
                    continue
                if closing != "]" and position < len(without_idempotency_clause) and without_idempotency_clause[position] == closing:
                    canonical.append(closing)
                    position += 1
                    continue
                break
            continue
        canonical.append(character.casefold())
        position += 1
    return "".join(canonical).rstrip().removesuffix(";").rstrip()


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


def _contains_sql_comment(statement: str) -> bool:
    position = 0
    while position < len(statement):
        character = statement[position]
        if character in {"'", '"', "`", "["}:
            closing = "]" if character == "[" else character
            position += 1
            while position < len(statement):
                quoted_character = statement[position]
                position += 1
                if quoted_character != closing:
                    continue
                if (
                    closing != "]"
                    and position < len(statement)
                    and statement[position] == closing
                ):
                    position += 1
                    continue
                break
            continue
        if statement.startswith("--", position) or statement.startswith("/*", position):
            return True
        position += 1
    return False


def _validate_statement(statement: str) -> None:
    if _contains_sql_comment(statement):
        raise SchemaError("migration contains unsupported SQL comments")
    operation = statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else ""
    if operation not in {"ALTER", "CREATE", "DELETE", "DROP", "INSERT", "UPDATE", "WITH"}:
        raise SchemaError("migration contains a transaction-control or unsupported statement")
    if re.match(r"\s*CREATE\s+(?:TEMP|TEMPORARY)\b", statement, re.IGNORECASE):
        raise SchemaError("migration contains an unsupported temporary schema declaration")
    quoted_name = r'(?:[A-Za-z_][A-Za-z0-9_]*|"(?:[^"]|"")*"|`(?:[^`]|``)*`|\[[^\]]*\])'
    qualified = re.match(
        rf"\s*(?:CREATE|DROP)\s+(?:UNIQUE\s+)?(?:INDEX|TRIGGER|VIEW)\s+"
        rf"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?{quoted_name}\s*\.",
        statement,
        re.IGNORECASE,
    )
    if qualified is not None:
        raise SchemaError("migration contains an unsupported schema-qualified declaration")


def _validate_existing_schema(connection: sqlite3.Connection, known: dict[int, Migration]) -> dict[int, str]:
    attached_schemas = {
        name
        for _, name, _ in connection.execute("PRAGMA database_list")
        if name not in {"main", "temp"}
    }
    if attached_schemas:
        raise SchemaError("migration connection contains an unsupported attached schema")
    temporary_objects = connection.execute(
        "SELECT 1 FROM sqlite_temp_master "
        "WHERE type IN ('table', 'index', 'trigger', 'view') "
        "AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone()
    if temporary_objects is not None:
        raise SchemaError("database schema contains an unsupported temporary object")
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
    expected_non_table = _expected_non_table_definitions(known, versions)
    actual_non_table = {
        (kind, name): _normalise_schema_sql(sql)
        for kind, name, sql in connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE type IN ('index', 'trigger', 'view')"
        )
        if (kind, name) in expected_non_table and sql is not None
    }
    if actual_non_table != expected_non_table:
        raise SchemaError("database schema object definition does not match its recorded migration")
    return applied


_STATE_SUFFIXES = ("", "-journal", "-wal", "-shm")
_RECOVERY_COPY_SUFFIXES = ("", "-journal", "-wal")


def _state_snapshot(path: Path) -> dict[str, tuple[int, int, int, int]]:
    snapshot: dict[str, tuple[int, int, int, int]] = {}
    for suffix in _STATE_SUFFIXES:
        candidate = Path(f"{path}{suffix}")
        if not candidate.exists():
            continue
        status = candidate.stat()
        snapshot[suffix] = (
            status.st_dev,
            status.st_ino,
            status.st_size,
            status.st_mtime_ns,
        )
    return snapshot


def preflight(path: Path) -> None:
    """Validate a stable copy before SQLite can recover or rewrite existing state."""

    if path == Path(":memory:"):
        raise SchemaError("durable state requires a filesystem database")
    if not path.exists():
        return

    before = _state_snapshot(path)
    probe: sqlite3.Connection | None = None
    try:
        with tempfile.TemporaryDirectory() as directory:
            copied_path = Path(directory) / "state.sqlite"
            for suffix in _RECOVERY_COPY_SUFFIXES:
                source = Path(f"{path}{suffix}")
                if source.exists():
                    shutil.copyfile(source, Path(f"{copied_path}{suffix}"))
            if before != _state_snapshot(path):
                raise SchemaError("database changed during validation")
            probe = sqlite3.connect(
                copied_path,
                isolation_level=None,
                check_same_thread=True,
            )
            try:
                applied = _validate_existing_schema(probe, _known_migrations())
            finally:
                probe.close()
                probe = None
            if not applied:
                raise SchemaError("existing database has no recorded application schema")
    except SchemaError:
        raise
    except (OSError, sqlite3.Error):
        raise SchemaError("existing database could not be validated") from None
    finally:
        if probe is not None:
            probe.close()


def _validated_migrations(connection: sqlite3.Connection) -> dict[int, Migration]:
    try:
        connection.execute("BEGIN EXCLUSIVE")
        known = _known_migrations()
        _validate_existing_schema(connection, known)
        connection.commit()
    except SchemaError:
        if connection.in_transaction:
            connection.rollback()
        raise
    except (sqlite3.Error, OSError):
        if connection.in_transaction:
            connection.rollback()
        raise SchemaError("database schema could not be validated") from None
    return known


def _enable_wal(connection: sqlite3.Connection) -> None:
    try:
        journal_mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    except sqlite3.Error:
        raise SchemaError("database journal mode could not be enabled") from None
    if journal_mode != ("wal",):
        raise SchemaError("database journal mode could not be enabled")


def migrate(connection: sqlite3.Connection) -> int:
    """Apply ordered migrations atomically, rejecting tampered or newer schemas."""

    known = _validated_migrations(connection)
    _enable_wal(connection)
    try:
        connection.execute("BEGIN EXCLUSIVE")
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
