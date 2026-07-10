"""SQLite connection, migration, and transaction foundation."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class SchemaError(RuntimeError):
    """Database schema is missing, incompatible, or has been modified."""


@dataclass(frozen=True)
class Migration:
    version: int
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


MIGRATIONS = (
    Migration(1, """
        CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL);
        CREATE TABLE instance_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE audit_events (id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL, summary TEXT NOT NULL);
    """),
)


def connect(path: Path) -> sqlite3.Connection:
    """Open one application-owned connection with the required safety settings."""

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None, check_same_thread=True)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def migrate(connection: sqlite3.Connection) -> int:
    """Apply ordered migrations atomically, rejecting tampered or newer schemas."""

    connection.execute("BEGIN EXCLUSIVE")
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        applied = {} if exists is None else dict(connection.execute("SELECT version, checksum FROM schema_migrations"))
        known = {migration.version: migration for migration in MIGRATIONS}
        if set(applied) - set(known):
            raise SchemaError("database schema is newer than this application")
        for version, checksum in applied.items():
            if known[version].checksum != checksum:
                raise SchemaError("database migration checksum does not match")
        for migration in MIGRATIONS:
            if migration.version not in applied:
                connection.executescript(migration.sql)
                connection.execute("INSERT INTO schema_migrations(version, checksum) VALUES (?, ?)", (migration.version, migration.checksum))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return MIGRATIONS[-1].version


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
