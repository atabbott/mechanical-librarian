"""Clean-schema SQLite current catalogue with a database-scoped busy gate."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path
from urllib.parse import quote

from mechanical_librarian.application.catalogue import (
    CatalogueBusyError,
    CatalogueEntry,
    CatalogueError,
    CatalogueStaleError,
)

_SCHEMA = "catalogue-v1"
_SQL = (
    "CREATE TABLE catalogue_schema (version TEXT PRIMARY KEY CHECK (version = 'catalogue-v1'));",
    "CREATE TABLE catalogue_generations (generation INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64), current INTEGER NOT NULL CHECK(current IN (0,1)), CHECK(current = 0 OR generation > 0));",
    "CREATE UNIQUE INDEX catalogue_one_current ON catalogue_generations(current) WHERE current = 1;",
    "CREATE TABLE catalogue_entries (generation INTEGER NOT NULL REFERENCES catalogue_generations(generation), connection TEXT NOT NULL, library TEXT NOT NULL, library_id TEXT NOT NULL, item_id TEXT NOT NULL, updated_at INTEGER NOT NULL, title TEXT NOT NULL, authors TEXT NOT NULL, has_ebook INTEGER NOT NULL CHECK(has_ebook IN (0,1)), has_audio INTEGER NOT NULL CHECK(has_audio IN (0,1)), classification TEXT NOT NULL CHECK(classification IN ('none','singular-supported','needs-attention')), file_identity TEXT, service_path TEXT, format TEXT, mount TEXT, PRIMARY KEY(generation, connection, library, item_id));",
)


class SQLiteCatalogueRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        if path.is_symlink() or path.parent.is_symlink() or not path.parent.is_dir():
            raise CatalogueError("catalogue database is unavailable")
        parent = path.parent.stat()
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            raise CatalogueError("catalogue database parent is unsafe")
        exists = path.exists()
        if exists:
            information = path.stat()
            if not stat.S_ISREG(information.st_mode) or information.st_uid != os.geteuid() or information.st_mode & 0o022:
                raise CatalogueError("catalogue database is unsafe")
            self._validate_existing_readonly()
        self._connection = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._verify_or_create()
            self._connection.execute("PRAGMA journal_mode = WAL")
        except Exception:
            self._connection.close()
            raise

    def _validate_existing_readonly(self) -> None:
        connection = sqlite3.connect(f"file:{quote(str(self._path))}?mode=ro", uri=True, timeout=0, isolation_level=None)
        try:
            self._validate_schema(connection)
        finally:
            connection.close()

    def _verify_or_create(self) -> None:
        rows = self._connection.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        if not rows:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SQL:
                    self._connection.execute(statement)
                self._connection.execute("INSERT INTO catalogue_schema VALUES (?)", (_SCHEMA,))
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            return
        self._validate_schema(self._connection, rows)

    def _validate_schema(self, connection: sqlite3.Connection, rows: list[tuple[str, str]] | None = None) -> None:
        rows = rows if rows is not None else connection.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        expected = {
            "catalogue_schema": _SQL[0], "catalogue_generations": _SQL[1],
            "catalogue_one_current": _SQL[2], "catalogue_entries": _SQL[3],
        }
        actual = {name: sql for name, sql in rows}
        canonical = lambda value: " ".join(value.replace(";", "").split()).lower()
        if actual.keys() != expected.keys() or any(type(actual[name]) is not str or canonical(actual[name]) != canonical(expected[name]) for name in expected) or connection.execute("SELECT version FROM catalogue_schema").fetchall() != [(_SCHEMA,)] or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise CatalogueError("catalogue schema is unsupported")

    def acquire_refresh(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise CatalogueBusyError("catalogue refresh is busy") from error

    def publish(self, fingerprint: str, entries: tuple[CatalogueEntry, ...]) -> int:
        try:
            generation = self._connection.execute("SELECT COALESCE(MAX(generation),0)+1 FROM catalogue_generations").fetchone()[0]
            self._connection.execute("UPDATE catalogue_generations SET current=0 WHERE current=1")
            self._connection.execute("INSERT INTO catalogue_generations(generation,fingerprint,current) VALUES(?,?,1)", (generation, fingerprint))
            self._connection.executemany(
                "INSERT INTO catalogue_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(generation, item.connection, item.library, item.library_id, item.item_id, item.updated_at, item.title, json.dumps(item.authors, ensure_ascii=False, separators=(",", ":")), int(item.has_ebook), int(item.has_audio), item.classification, item.file_identity, item.service_path, item.format, item.mount) for item in entries],
            )
            self._connection.execute("COMMIT")
            return generation
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def abort_refresh(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")

    def inspect(self, fingerprint: str) -> tuple[CatalogueEntry, ...]:
        row = self._connection.execute("SELECT generation, fingerprint FROM catalogue_generations WHERE current=1").fetchone()
        if row is None:
            raise CatalogueStaleError("catalogue requires refresh")
        if row[1] != fingerprint:
            raise CatalogueStaleError("catalogue requires refresh")
        values = self._connection.execute("SELECT connection,library,library_id,item_id,updated_at,title,authors,has_ebook,has_audio,classification,file_identity,service_path,format,mount FROM catalogue_entries WHERE generation=?", (row[0],)).fetchall()
        try:
            return tuple(CatalogueEntry(value[0], value[1], value[2], value[3], value[4], value[5], tuple(json.loads(value[6])), bool(value[7]), bool(value[8]), value[9], value[10], value[11], value[12], value[13]) for value in values)
        except (TypeError, ValueError):
            raise CatalogueError("catalogue state is invalid") from None

    def close(self) -> None:
        self._connection.close()


def open_catalogue(path: Path) -> SQLiteCatalogueRepository:
    return SQLiteCatalogueRepository(path)


__all__ = ["SQLiteCatalogueRepository", "open_catalogue"]
