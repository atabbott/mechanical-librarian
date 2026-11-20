"""Clean-schema SQLite current catalogue with a database-scoped busy gate."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Iterator, cast
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.binding import (
    _LinuxDirectoryChain,
)
from mechanical_librarian.adapters.persistence.schema import (
    _check_constraint_violation,
)
from mechanical_librarian.adapters.persistence.transactions import sqlite_busy
from mechanical_librarian.application.batch import CatalogueSnapshot
from mechanical_librarian.application.catalogue import (
    CatalogueBusyError,
    CatalogueEntry,
    CatalogueError,
    CatalogueStaleError,
)

CATALOGUE_SCHEMA: Final = "catalogue-v4"

# How long an open or read waits on another connection's lock before it is
# busy. A refresh never waits: `acquire_refresh` drops it to zero (#2024).
_BUSY_TIMEOUT_SECONDS = 5.0

_SQL = (
    f"CREATE TABLE catalogue_schema (version TEXT PRIMARY KEY CHECK (version = '{CATALOGUE_SCHEMA}'));",
    "CREATE TABLE catalogue_generations (generation INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64), current INTEGER NOT NULL CHECK(current IN (0,1)), CHECK(current = 0 OR generation > 0));",
    "CREATE UNIQUE INDEX catalogue_one_current ON catalogue_generations(current) WHERE current = 1;",
    "CREATE TABLE catalogue_entries (generation INTEGER NOT NULL REFERENCES catalogue_generations(generation), connection TEXT NOT NULL, library TEXT NOT NULL, library_id TEXT NOT NULL, item_id TEXT NOT NULL, updated_at INTEGER NOT NULL, title TEXT NOT NULL, authors TEXT NOT NULL, has_ebook INTEGER NOT NULL CHECK(has_ebook IN (0,1)), has_audio INTEGER NOT NULL CHECK(has_audio IN (0,1)), classification TEXT NOT NULL CHECK(classification IN ('none','singular-supported','needs-attention')), file_identity TEXT, service_path TEXT, format TEXT, mount TEXT, source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64 AND source_fingerprint NOT GLOB '*[^0-9a-f]*'), item_path TEXT CHECK(item_path IS NULL OR length(item_path)>0), unread TEXT CHECK(unread IS NULL OR (classification='needs-attention' AND unread IN ('no_media','unreadable','oversized','changed'))), PRIMARY KEY(generation, connection, library, item_id));",
)
_ENTRY_COLUMNS: Final = (
    "connection,library,library_id,item_id,updated_at,title,authors,has_ebook,"
    "has_audio,classification,file_identity,service_path,format,mount,"
    "source_fingerprint,item_path,unread"
)


def _entry(value: tuple[Any, ...]) -> CatalogueEntry:
    """One stored entry, in `_ENTRY_COLUMNS` order."""
    return CatalogueEntry(
        value[0],
        value[1],
        value[2],
        value[3],
        value[4],
        value[5],
        tuple(json.loads(value[6])),
        bool(value[7]),
        bool(value[8]),
        value[9],
        value[10],
        value[11],
        value[12],
        value[13],
        value[14],
        value[15],
        value[16],
    )


def _require_bound_name(
    path: Path, chain: _LinuxDirectoryChain | None, expected: os.stat_result
) -> None:
    if chain is None:
        actual = path.lstat()
        if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
            expected.st_dev,
            expected.st_ino,
        ):
            raise CatalogueError("catalogue database pathname changed while opening")
        return
    current = chain.open_current_parent()
    try:
        actual = os.stat(path.name, dir_fd=current, follow_symlinks=False)
    finally:
        os.close(current)
    if not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (
        expected.st_dev,
        expected.st_ino,
    ):
        raise CatalogueError("catalogue database pathname changed while opening")


def _validate_schema(
    connection: sqlite3.Connection,
    rows: list[tuple[str, str, str | None]] | None = None,
    *,
    read_only: bool = False,
    full: bool = True,
) -> None:
    rows = (
        rows
        if rows is not None
        else connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
    )
    expected = sorted(
        (
            ("index", "catalogue_one_current", _SQL[2]),
            ("index", "sqlite_autoindex_catalogue_entries_1", None),
            ("index", "sqlite_autoindex_catalogue_schema_1", None),
            ("table", "catalogue_entries", _SQL[3]),
            ("table", "catalogue_generations", _SQL[1]),
            ("table", "catalogue_schema", _SQL[0]),
        )
    )

    def canonical(value: str) -> str:
        return " ".join(value.replace(";", "").split()).lower()

    if (
        len(rows) != len(expected)
        or any(
            actual[:2] != expected_item[:2]
            or (actual[2] is None) != (expected_item[2] is None)
            or actual[2] is not None
            and canonical(actual[2]) != canonical(cast(str, expected_item[2]))
            for actual, expected_item in zip(rows, expected, strict=True)
        )
        or connection.execute("SELECT version FROM catalogue_schema").fetchall()
        != [(CATALOGUE_SCHEMA,)]
        or full
        and (
            connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or connection.execute("PRAGMA foreign_key_check").fetchall()
        )
    ):
        raise CatalogueError("catalogue schema is unsupported")
    # A `mode=ro` integrity check skips CHECK constraints (#1401).
    if read_only and (table := _check_constraint_violation(connection, expected)):
        raise CatalogueError(
            f"catalogue schema is unsupported: CHECK constraint failed in {table}"
        )


def _storage_failure(error: sqlite3.Error) -> CatalogueError:
    """Translate a SQLite failure: busy or locked is retryable, the rest is not."""
    if sqlite_busy(error):
        return CatalogueBusyError("catalogue database is busy")
    return CatalogueError("catalogue database is unavailable")


_WalKey = tuple[int, int, int, int, int]
_AdmissionKey = tuple[int, int, int, _WalKey | None]


class CatalogueSchemaAdmission:
    """Remember the catalogue file this process last validated in full.

    The key is the main file's device, inode and change time, together with
    the write-ahead log's device, inode, size and times while the log holds
    any frames. It is taken before the checks and recorded only after every
    one passed. A later open of the same unchanged files skips the read-only
    pass and the integrity and foreign-key checks, but still compares the
    exact schema rows and version. A commit, which reaches the log before the
    main file, a checkpoint or a replacement changes the key and forces a
    full validation again (#2024).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: _AdmissionKey | None = None

    @staticmethod
    def key_of(
        information: os.stat_result, wal: os.stat_result | None
    ) -> _AdmissionKey:
        return (
            information.st_dev,
            information.st_ino,
            information.st_ctime_ns,
            # An empty log holds no frames, so it reads as no log at all.
            None
            if wal is None or wal.st_size == 0
            else (
                wal.st_dev,
                wal.st_ino,
                wal.st_size,
                wal.st_mtime_ns,
                wal.st_ctime_ns,
            ),
        )

    def admits(self, key: _AdmissionKey | None) -> bool:
        with self._lock:
            return key is not None and self._key == key

    def admit(self, key: _AdmissionKey | None) -> None:
        with self._lock:
            self._key = key


class SQLiteCatalogueRepository:
    def __init__(
        self,
        path: Path,
        *,
        create: bool = False,
        admission: CatalogueSchemaAdmission | None = None,
    ) -> None:
        self._path = path.absolute()
        if not self._path.is_absolute() or not self._path.name:
            raise CatalogueError("catalogue database is unavailable")
        self._chain = (
            _LinuxDirectoryChain.open(self._path.parent)
            if sys.platform.startswith("linux")
            else None
        )
        parent = (
            os.fstat(self._chain.parent_descriptor)
            if self._chain is not None
            else self._path.parent.stat()
        )
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
            if self._chain is not None:
                self._chain.close()
            raise CatalogueError("catalogue database parent is unsafe")
        descriptor = -1
        try:
            flags = (
                os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            try:
                descriptor = os.open(
                    self._path if self._chain is None else self._path.name,
                    flags,
                    **(
                        {}
                        if self._chain is None
                        else {"dir_fd": self._chain.parent_descriptor}
                    ),
                )
                exists = True
            except FileNotFoundError:
                if not create:
                    raise CatalogueStaleError("catalogue requires refresh") from None
                descriptor = os.open(
                    self._path if self._chain is None else self._path.name,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    **(
                        {}
                        if self._chain is None
                        else {"dir_fd": self._chain.parent_descriptor}
                    ),
                )
                exists = False
            information = os.fstat(descriptor)
            if (
                not stat.S_ISREG(information.st_mode)
                or information.st_uid != os.geteuid()
                or information.st_mode & 0o022
            ):
                raise CatalogueError("catalogue database is unsafe")
            self._identity = information
            key = None if admission is None else self._admission_key(information)
            admitted = admission is not None and admission.admits(key)
            if exists and not admitted:
                self._validate_existing_readonly()
            self._require_bound_name(information)
            try:
                self._connection = sqlite3.connect(
                    f"file:{quote(str(self._path))}?mode=rw",
                    uri=True,
                    timeout=_BUSY_TIMEOUT_SECONDS,
                    isolation_level=None,
                )
            except sqlite3.OperationalError as error:
                if not create:
                    raise CatalogueStaleError("catalogue requires refresh") from error
                raise CatalogueError("catalogue database is unavailable") from error
            self._require_bound_name(information)
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._verify_or_create(full=not admitted)
            self._connection.execute("PRAGMA journal_mode = WAL")
            if exists and admission is not None:
                # Keyed before the checks: a write while they ran changes it.
                admission.admit(key)
        except Exception as error:
            if hasattr(self, "_connection"):
                self._connection.close()
            if self._chain is not None:
                self._chain.close()
            if isinstance(error, sqlite3.Error):
                raise _storage_failure(error) from error
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _require_bound_name(self, expected: os.stat_result) -> None:
        _require_bound_name(self._path, self._chain, expected)

    def _admission_key(self, information: os.stat_result) -> _AdmissionKey | None:
        """Key the main file and its log; None when the log is not a file."""
        name = f"{self._path.name}-wal"
        try:
            wal: os.stat_result | None = (
                os.stat(self._path.with_name(name), follow_symlinks=False)
                if self._chain is None
                else os.stat(
                    name, dir_fd=self._chain.parent_descriptor, follow_symlinks=False
                )
            )
        except FileNotFoundError:
            wal = None
        except OSError:
            return None
        if wal is not None and not stat.S_ISREG(wal.st_mode):
            return None
        return CatalogueSchemaAdmission.key_of(information, wal)

    def _validate_existing_readonly(self) -> None:
        connection = sqlite3.connect(
            f"file:{quote(str(self._path))}?mode=ro",
            uri=True,
            timeout=_BUSY_TIMEOUT_SECONDS,
            isolation_level=None,
        )
        try:
            _validate_schema(connection, read_only=True)
        finally:
            connection.close()

    def _verify_or_create(self, *, full: bool = True) -> None:
        rows = self._connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        if not rows:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in _SQL:
                    self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO catalogue_schema VALUES (?)", (CATALOGUE_SCHEMA,)
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
            return
        _validate_schema(self._connection, rows, full=full)

    def acquire_refresh(self) -> None:
        try:
            self._require_bound_name(self._identity)
            # A second refresh is refused at once rather than queued (#2024).
            self._connection.execute("PRAGMA busy_timeout = 0")
            try:
                self._connection.execute("BEGIN IMMEDIATE")
            finally:
                self._connection.execute(
                    f"PRAGMA busy_timeout = {int(_BUSY_TIMEOUT_SECONDS * 1000)}"
                )
        except sqlite3.OperationalError as error:
            raise CatalogueBusyError("catalogue refresh is busy") from error

    def publish(self, fingerprint: str, entries: tuple[CatalogueEntry, ...]) -> int:
        try:
            self._require_bound_name(self._identity)
            generation: int = self._connection.execute(
                "SELECT COALESCE(MAX(generation),0)+1 FROM catalogue_generations"
            ).fetchone()[0]
            self._connection.execute(
                "UPDATE catalogue_generations SET current=0 WHERE current=1"
            )
            self._connection.execute(
                "INSERT INTO catalogue_generations(generation,fingerprint,current) VALUES(?,?,1)",
                (generation, fingerprint),
            )
            self._connection.executemany(
                "INSERT INTO catalogue_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        generation,
                        item.connection,
                        item.library,
                        item.library_id,
                        item.item_id,
                        item.updated_at,
                        item.title,
                        json.dumps(
                            item.authors, ensure_ascii=False, separators=(",", ":")
                        ),
                        int(item.has_ebook),
                        int(item.has_audio),
                        item.classification,
                        item.file_identity,
                        item.service_path,
                        item.format,
                        item.mount,
                        item.source_fingerprint,
                        item.item_path,
                        item.unread,
                    )
                    for item in entries
                ],
            )
            self._connection.execute(
                "DELETE FROM catalogue_entries WHERE generation <> ?", (generation,)
            )
            self._connection.execute(
                "DELETE FROM catalogue_generations WHERE generation <> ?", (generation,)
            )
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
        row = self._connection.execute(
            "SELECT generation, fingerprint FROM catalogue_generations WHERE current=1"
        ).fetchone()
        if row is None:
            raise CatalogueStaleError("catalogue requires refresh")
        if row[1] != fingerprint:
            raise CatalogueStaleError("catalogue requires refresh")
        values = self._connection.execute(
            f"SELECT {_ENTRY_COLUMNS} FROM catalogue_entries WHERE generation=?",  # noqa: S608 - fixed column list
            (row[0],),
        ).fetchall()
        try:
            return tuple(_entry(value) for value in values)
        except (TypeError, ValueError):
            raise CatalogueError("catalogue state is invalid") from None

    def snapshot(self, fingerprint: str) -> CatalogueSnapshot:
        self._connection.execute("BEGIN")
        try:
            row = self._connection.execute(
                "SELECT generation, fingerprint FROM catalogue_generations WHERE current=1"
            ).fetchone()
            if row is None or row[1] != fingerprint:
                raise CatalogueStaleError("catalogue requires refresh")
            entries = self.inspect(fingerprint)
            self._connection.execute("COMMIT")
            return CatalogueSnapshot(row[0], row[1], entries)
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def current_entry(
        self, generation: int, fingerprint: str, key: tuple[str, str, str, str]
    ) -> CatalogueEntry | None:
        self._connection.execute("BEGIN")
        try:
            row = self._connection.execute(
                "SELECT generation FROM catalogue_generations WHERE current=1 AND generation=? AND fingerprint=?",
                (generation, fingerprint),
            ).fetchone()
            if row is None:
                raise CatalogueStaleError("catalogue requires refresh")
            entries = self._connection.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM catalogue_entries WHERE generation=? "  # noqa: S608 - fixed column list
                "AND connection=? AND library=? AND library_id=? AND item_id=?",
                (generation, *key),
            ).fetchall()
            self._connection.execute("COMMIT")
            if len(entries) != 1:
                return None
            return _entry(entries[0])
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def latest_entry(
        self, fingerprint: str, key: tuple[str, str, str, str]
    ) -> CatalogueEntry | None:
        try:
            self._connection.execute("BEGIN")
            try:
                current = self._connection.execute(
                    "SELECT generation FROM catalogue_generations "
                    "WHERE current=1 AND fingerprint=?",
                    (fingerprint,),
                ).fetchone()
                if current is None:
                    raise CatalogueStaleError("catalogue requires refresh")
                entries = self._connection.execute(
                    f"SELECT {_ENTRY_COLUMNS} "  # noqa: S608 - fixed column list
                    "FROM catalogue_entries WHERE generation=? AND connection=? "
                    "AND library=? AND library_id=? AND item_id=?",
                    (current[0], *key),
                ).fetchall()
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        except sqlite3.Error as error:
            raise _storage_failure(error) from error
        if len(entries) != 1:
            return None
        return _entry(entries[0])

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            if self._chain is not None:
                self._chain.close()


class CatalogueReadAdmission:
    """One catalogue admission, with short readonly Library attachments.

    The retained descriptor prevents inode reuse; a replacement or schema change
    refuses until the owning review repository closes and is reopened.
    """

    def __init__(self, path: Path, fingerprint: str) -> None:
        self._path = path.absolute()
        self.fingerprint = fingerprint
        self.active = False
        self._chain = (
            _LinuxDirectoryChain.open(self._path.parent)
            if sys.platform.startswith("linux")
            else None
        )
        self._descriptor = -1
        self._identity: os.stat_result | None = None
        self._schema_version: int | None = None

    def close(self) -> None:
        try:
            if self._descriptor >= 0:
                os.close(self._descriptor)
                self._descriptor = -1
        finally:
            if self._chain is not None:
                self._chain.close()

    def _require_current(self, connection: sqlite3.Connection) -> None:
        assert self._identity is not None
        _require_bound_name(self._path, self._chain, self._identity)
        if connection.execute(
            "PRAGMA retained_catalogue.schema_version"
        ).fetchone() != (self._schema_version,):
            raise CatalogueError("catalogue schema changed after admission")

    @contextmanager
    def attach(self, connection: sqlite3.Connection) -> Iterator[None]:
        if connection.in_transaction or self.active:
            raise CatalogueError("catalogue attachment requires an idle connection")
        attached = False
        descriptor = -1
        try:
            parent = (
                os.fstat(self._chain.parent_descriptor)
                if self._chain is not None
                else self._path.parent.stat()
            )
            if parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
                raise CatalogueError("catalogue database parent is unsafe")
            try:
                descriptor = os.open(
                    self._path if self._chain is None else self._path.name,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    **(
                        {}
                        if self._chain is None
                        else {"dir_fd": self._chain.parent_descriptor}
                    ),
                )
            except FileNotFoundError as error:
                if self._identity is not None:
                    raise CatalogueError("catalogue pathname changed") from error
                yield
                return
            information = os.fstat(descriptor)
            if (
                not stat.S_ISREG(information.st_mode)
                or information.st_uid != os.geteuid()
                or information.st_mode & 0o022
            ):
                raise CatalogueError("catalogue database is unsafe")
            _require_bound_name(self._path, self._chain, self._identity or information)
            uri = f"file:{quote(str(self._path))}?mode=ro"
            if self._identity is None:
                validation = sqlite3.connect(
                    uri, uri=True, isolation_level=None, timeout=0
                )
                try:
                    validation.execute("BEGIN")
                    _validate_schema(validation, read_only=True)
                    self._schema_version = validation.execute(
                        "PRAGMA schema_version"
                    ).fetchone()[0]
                finally:
                    validation.close()
                _require_bound_name(self._path, self._chain, information)
                self._identity = information
                self._descriptor, descriptor = descriptor, -1
            connection.execute("ATTACH DATABASE ? AS retained_catalogue", (uri,))
            attached = True
            self._require_current(connection)
            self.active = True
            try:
                yield
            finally:
                self._require_current(connection)
        finally:
            self.active = False
            if descriptor >= 0:
                os.close(descriptor)
            if attached:
                connection.execute("DETACH DATABASE retained_catalogue")


def open_catalogue(
    path: Path,
    *,
    create: bool = False,
    admission: CatalogueSchemaAdmission | None = None,
) -> SQLiteCatalogueRepository:
    return SQLiteCatalogueRepository(path, create=create, admission=admission)


__all__ = [
    "CatalogueSchemaAdmission",
    "SQLiteCatalogueRepository",
    "open_catalogue",
]
