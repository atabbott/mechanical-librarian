"""Clean-schema SQLite current catalogue with a database-scoped busy gate."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.metadata_reports import _LinuxDirectoryChain
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
    def __init__(self, path: Path, *, create: bool = False) -> None:
        self._path = path.absolute()
        if not self._path.is_absolute() or not self._path.name:
            raise CatalogueError("catalogue database is unavailable")
        self._chain = _LinuxDirectoryChain.open(self._path.parent) if sys.platform.startswith("linux") else None
        parent = os.fstat(self._chain.parent_descriptor) if self._chain is not None else self._path.parent.stat()
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            if self._chain is not None:
                self._chain.close()
            raise CatalogueError("catalogue database parent is unsafe")
        descriptor = -1
        try:
            flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(self._path if self._chain is None else self._path.name, flags, **({} if self._chain is None else {"dir_fd": self._chain.parent_descriptor}))
                exists = True
            except FileNotFoundError:
                if not create:
                    raise CatalogueStaleError("catalogue requires refresh") from None
                descriptor = os.open(self._path if self._chain is None else self._path.name, flags | os.O_CREAT | os.O_EXCL, 0o600, **({} if self._chain is None else {"dir_fd": self._chain.parent_descriptor}))
                exists = False
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode) or information.st_uid != os.geteuid() or information.st_mode & 0o022:
                raise CatalogueError("catalogue database is unsafe")
            self._identity = information
            if exists:
                self._validate_existing_readonly()
            self._require_bound_name(information)
            try:
                self._connection = sqlite3.connect(f"file:{quote(str(self._path))}?mode=rw", uri=True, timeout=0, isolation_level=None)
            except sqlite3.OperationalError as error:
                if not create:
                    raise CatalogueStaleError("catalogue requires refresh") from error
                raise CatalogueError("catalogue database is unavailable") from error
            self._require_bound_name(information)
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._verify_or_create()
            self._connection.execute("PRAGMA journal_mode = WAL")
        except Exception:
            if hasattr(self, "_connection"):
                self._connection.close()
            if self._chain is not None:
                self._chain.close()
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _require_bound_name(self, expected: os.stat_result) -> None:
        if self._chain is None:
            actual = self._path.lstat()
            if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise CatalogueError("catalogue database pathname changed while opening")
            return
        current = self._chain.open_current_parent()
        try:
            actual = os.stat(self._path.name, dir_fd=current, follow_symlinks=False)
        finally:
            os.close(current)
        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise CatalogueError("catalogue database pathname changed while opening")

    def _validate_existing_readonly(self) -> None:
        connection = sqlite3.connect(f"file:{quote(str(self._path))}?mode=ro", uri=True, timeout=0, isolation_level=None)
        try:
            self._validate_schema(connection)
        finally:
            connection.close()

    def _verify_or_create(self) -> None:
        rows = self._connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
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

    def _validate_schema(self, connection: sqlite3.Connection, rows: list[tuple[str, str, str | None]] | None = None) -> None:
        rows = rows if rows is not None else connection.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
        expected = sorted((
            ("index", "catalogue_one_current", _SQL[2]),
            ("index", "sqlite_autoindex_catalogue_entries_1", None),
            ("index", "sqlite_autoindex_catalogue_schema_1", None),
            ("table", "catalogue_entries", _SQL[3]),
            ("table", "catalogue_generations", _SQL[1]),
            ("table", "catalogue_schema", _SQL[0]),
        ))
        canonical = lambda value: " ".join(value.replace(";", "").split()).lower()
        if len(rows) != len(expected) or any(actual[:2] != expected_item[:2] or (actual[2] is None) != (expected_item[2] is None) or actual[2] is not None and canonical(actual[2]) != canonical(expected_item[2]) for actual, expected_item in zip(rows, expected, strict=True)) or connection.execute("SELECT version FROM catalogue_schema").fetchall() != [(_SCHEMA,)] or connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise CatalogueError("catalogue schema is unsupported")

    def acquire_refresh(self) -> None:
        try:
            self._require_bound_name(self._identity)
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            raise CatalogueBusyError("catalogue refresh is busy") from error

    def publish(self, fingerprint: str, entries: tuple[CatalogueEntry, ...]) -> int:
        try:
            self._require_bound_name(self._identity)
            generation = self._connection.execute("SELECT COALESCE(MAX(generation),0)+1 FROM catalogue_generations").fetchone()[0]
            self._connection.execute("UPDATE catalogue_generations SET current=0 WHERE current=1")
            self._connection.execute("INSERT INTO catalogue_generations(generation,fingerprint,current) VALUES(?,?,1)", (generation, fingerprint))
            self._connection.executemany(
                "INSERT INTO catalogue_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(generation, item.connection, item.library, item.library_id, item.item_id, item.updated_at, item.title, json.dumps(item.authors, ensure_ascii=False, separators=(",", ":")), int(item.has_ebook), int(item.has_audio), item.classification, item.file_identity, item.service_path, item.format, item.mount) for item in entries],
            )
            self._connection.execute("DELETE FROM catalogue_entries WHERE generation <> ?", (generation,))
            self._connection.execute("DELETE FROM catalogue_generations WHERE generation <> ?", (generation,))
            self._require_bound_name(self._identity)
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
        try:
            self._connection.close()
        finally:
            if self._chain is not None:
                self._chain.close()


def open_catalogue(path: Path, *, create: bool = False) -> SQLiteCatalogueRepository:
    return SQLiteCatalogueRepository(path, create=create)


__all__ = ["SQLiteCatalogueRepository", "open_catalogue"]
