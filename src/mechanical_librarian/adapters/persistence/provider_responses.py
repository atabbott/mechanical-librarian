"""Provider answers kept for reuse, on their own private SQLite database.

A metadata adapter asks this store before it asks a provider (#1064,
ADR-0101). Each row is one success body a provider returned and a clean
observation accepted, keyed by the adapter kind and a digest of the request
that produced it. The store holds no request text, header or credential:
only the compressed body, its SHA-256 and length, and the time it was fetched.

The file follows the private stores' rules: an absent file is created
owner-only with the exact schema, the exact current schema is accepted, and
anything else is refused without mutation. Unlike the other stores it runs no
integrity check at open, which would read every payload: each row proves its
own integrity when it is read, and a row that fails is deleted and treated as
never stored. Losing the file costs only requests, so nothing here ever fails
a preparation: every fault is a miss or a skipped commit and one value-free
event.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import threading
import zlib
from pathlib import Path

from mechanical_librarian.adapters.persistence.binding import (
    _connect_bound,
    _unlink_created,
    _validate_delete_mode_header,
)
from mechanical_librarian.application.failure_diagnostics import emit_provider_events
from mechanical_librarian.application.metadata_report import MetadataReportError

# The stored payload bytes the store keeps before it removes the answers
# fetched longest ago; a full 10,000-item library is estimated at about a
# quarter of it.
MAX_STORE_BYTES = 2 * 1024**3
# The largest body any adapter may accept (the configured per-response bound's
# ceiling).
MAX_PAYLOAD_BYTES = 262_144
# The last microsecond of 9999-12-31, the latest time a report can name.
_LATEST_FETCHED_AT_US = 253_402_300_799_999_999
_TABLE_SQL = (
    "CREATE TABLE provider_responses ("
    "adapter TEXT NOT NULL CHECK(adapter IN ('audible_catalog','audiosilo','audnexus',"
    "'google_books','hardcover','inventaire','itunes_search','open_library')), "
    "digest TEXT NOT NULL CHECK(length(digest)=64 AND digest NOT GLOB '*[^0-9a-f]*'), "
    "fetched_at_us INTEGER NOT NULL CHECK(fetched_at_us BETWEEN 0 AND 253402300799999999), "
    "payload_digest TEXT NOT NULL CHECK(length(payload_digest)=64 AND payload_digest NOT GLOB '*[^0-9a-f]*'), "
    "payload_bytes INTEGER NOT NULL CHECK(payload_bytes BETWEEN 1 AND 262144), "
    "payload BLOB NOT NULL, "
    "stored_bytes INTEGER NOT NULL CHECK(stored_bytes=length(payload)), "
    "PRIMARY KEY(adapter,digest)) STRICT, WITHOUT ROWID"
)
# Covering, so the size sum and the oldest-first removal never read a payload.
_INDEX_SQL = (
    "CREATE INDEX provider_responses_by_age "
    "ON provider_responses(fetched_at_us,stored_bytes)"
)
_EXPECTED_OBJECTS = (
    ("index", "provider_responses_by_age", _INDEX_SQL),
    ("table", "provider_responses", _TABLE_SQL),
)
_REFUSED = "provider response database is unavailable or incompatible"


def _fault(fault: str, adapter: str | None = None) -> None:
    emit_provider_events(
        lambda: (
            {
                "event": "provider_response_store",
                "level": "warning",
                "store_fault": fault,
                **({} if adapter is None else {"adapter": adapter}),
            },
        )
    )


class SQLiteProviderResponseStore:
    """Read and keep provider answers; never raise into a provider dispatch."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: Path) -> SQLiteProviderResponseStore:
        """Open or create the exact-current store at ``path``, or refuse it."""
        connection: sqlite3.Connection | None = None
        descriptor = -1
        created: tuple[int, int] | None = None
        try:
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            if not nofollow:
                raise MetadataReportError(_REFUSED)
            flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | nofollow
            exists = os.path.lexists(path)
            descriptor = (
                os.open(path, flags)
                if exists
                else os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            )
            information = os.fstat(descriptor)
            if not exists:
                created = (information.st_dev, information.st_ino)
            if (
                not stat.S_ISREG(information.st_mode)
                or information.st_uid != os.geteuid()
                or information.st_mode & 0o077
            ):
                raise MetadataReportError(_REFUSED)
            if exists:
                _validate_delete_mode_header(descriptor)
            connection = _connect_bound(
                path,
                descriptor,
                cross_thread=True,
                allow_rollback_journal=exists,
            )
            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise MetadataReportError(_REFUSED)
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=1000")
            if created is not None:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(_TABLE_SQL)
                    connection.execute(_INDEX_SQL)
                    connection.execute("COMMIT")
                except sqlite3.Error:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
            objects = connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
            if tuple(tuple(value) for value in objects) != _EXPECTED_OBJECTS:
                raise MetadataReportError(_REFUSED)
            return cls(connection)
        except (MetadataReportError, OSError, sqlite3.Error) as error:
            if connection is not None:
                connection.close()
            if created is not None:
                _unlink_created(path, created)
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(_REFUSED) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def get(self, adapter: str, digest: str) -> tuple[bytes, int] | None:
        """Return one verified body and its fetch time, or None for a miss."""
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT fetched_at_us,payload_digest,payload_bytes,payload "
                    "FROM provider_responses WHERE adapter=? AND digest=?",
                    (adapter, digest),
                ).fetchone()
                if row is None:
                    return None
                fetched_at_us, payload_digest, payload_bytes, stored = row
                body = _verified(payload_digest, payload_bytes, stored)
                if (
                    body is not None
                    and type(fetched_at_us) is int
                    and 0 <= fetched_at_us <= _LATEST_FETCHED_AT_US
                ):
                    return body, fetched_at_us
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "DELETE FROM provider_responses "
                    "WHERE adapter=? AND digest=? AND payload_digest=?",
                    (adapter, digest, payload_digest),
                )
                self._connection.execute("COMMIT")
        except (sqlite3.Error, ValueError, TypeError):
            self._rollback()
            _fault("read_failed", adapter)
            return None
        _fault("row_corrupt", adapter)
        return None

    def commit(self, adapter: str, rows: tuple[tuple[str, int, bytes], ...]) -> None:
        """Keep one clean observation's answers as `(digest, fetched_at_us, body)`.

        An answer replaces the one held for its request unless that one was
        fetched later, so an observation that finishes late cannot undo a
        newer one. The answers fetched longest ago are then removed until the
        store is under its ceiling.
        """
        if not rows:
            return
        values = []
        for digest, fetched_at_us, body in rows:
            stored = zlib.compress(body)
            values.append(
                (
                    adapter,
                    digest,
                    fetched_at_us,
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                    stored,
                    len(stored),
                )
            )
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.executemany(
                    "INSERT INTO provider_responses(adapter,digest,fetched_at_us,"
                    "payload_digest,payload_bytes,payload,stored_bytes) "
                    "VALUES(?,?,?,?,?,?,?) ON CONFLICT(adapter,digest) DO UPDATE SET "
                    "fetched_at_us=excluded.fetched_at_us,"
                    "payload_digest=excluded.payload_digest,"
                    "payload_bytes=excluded.payload_bytes,payload=excluded.payload,"
                    "stored_bytes=excluded.stored_bytes "
                    "WHERE excluded.fetched_at_us>=provider_responses.fetched_at_us",
                    values,
                )
                (total,) = self._connection.execute(
                    "SELECT coalesce(sum(stored_bytes),0) FROM provider_responses"
                ).fetchone()
                excess = total - MAX_STORE_BYTES
                while excess > 0:
                    oldest = self._connection.execute(
                        "SELECT adapter,digest,stored_bytes FROM provider_responses "
                        "ORDER BY fetched_at_us LIMIT 64"
                    ).fetchall()
                    for kind, key, size in oldest:
                        if excess <= 0:
                            break
                        self._connection.execute(
                            "DELETE FROM provider_responses WHERE adapter=? AND digest=?",
                            (kind, key),
                        )
                        excess -= size
                self._connection.execute("COMMIT")
        except sqlite3.Error:
            self._rollback()
            _fault("write_failed", adapter)

    def discard(self, adapter: str, digest: str) -> None:
        """Remove one held answer its adapter no longer reads."""
        try:
            with self._lock:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "DELETE FROM provider_responses WHERE adapter=? AND digest=?",
                    (adapter, digest),
                )
                self._connection.execute("COMMIT")
        except sqlite3.Error:
            self._rollback()
            _fault("write_failed", adapter)

    def _rollback(self) -> None:
        try:
            with self._lock:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass


def _verified(
    payload_digest: object, payload_bytes: object, stored: object
) -> bytes | None:
    """Inflate one stored body within its stated length, or refuse it."""
    if (
        type(stored) is not bytes
        or type(payload_bytes) is not int
        or not 1 <= payload_bytes <= MAX_PAYLOAD_BYTES
    ):
        return None
    inflater = zlib.decompressobj()
    try:
        body = inflater.decompress(stored, payload_bytes + 1)
    except zlib.error:
        return None
    if (
        not inflater.eof
        or inflater.unconsumed_tail
        or inflater.unused_data
        or len(body) != payload_bytes
        or hashlib.sha256(body).hexdigest() != payload_digest
    ):
        return None
    return body


def open_provider_response_store(path: Path) -> SQLiteProviderResponseStore | None:
    """Open the store, or degrade to live requests with one value-free event."""
    try:
        return SQLiteProviderResponseStore.open(path)
    except MetadataReportError:
        _fault("unavailable")
        return None


__all__ = [
    "MAX_PAYLOAD_BYTES",
    "MAX_STORE_BYTES",
    "SQLiteProviderResponseStore",
    "open_provider_response_store",
]
