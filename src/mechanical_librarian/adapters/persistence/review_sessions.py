"""The review-session aggregate on its own dedicated SQLite database.

Owns the `review_sessions` table and the `review_login_failures` table that
rate-limits admission to it: who is currently signed in to the review surface,
which CSRF token that session carries, and how many failed attempts each
client, and all clients together, have made inside the current window. A
client is stored only as a verifier-keyed HMAC of its resolved identity, never
as an address (#1384).

The two tables move together because every method touches both -- a login
attempt reads and prunes failures, a granted session clears them, and expiry
prunes sessions -- so splitting them would put one transaction across two
modules for no gain.

Nothing here reads another aggregate: the session is authority over the review
surface, not over any report, case or policy it later reaches. #912 turned
that independence physical. Session state lives in its own database file with
its own connection and lock, so owner sign-in never queues behind a library
scan's transaction on the application database -- neither on the in-process
transaction lock nor on SQLite's rollback-journal write lock.

The restart contract mirrors the application database's posture under
ADR-0013: an absent file is created with the exact schema, the exact current
schema is accepted, and anything else is rejected clearly without mutation.
Losing this file costs one owner re-login; it holds only token digests,
expiries, keyed client digests and throttle timestamps, never a password,
token, client address or review document.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import stat
import threading
from pathlib import Path
from typing import cast

from mechanical_librarian.adapters.persistence.binding import (
    _connect_bound,
    _unlink_created,
    _validate_delete_mode_header,
)
from mechanical_librarian.application.metadata_report import MetadataReportError

_REVIEW_SESSIONS_SQL = (
    "CREATE TABLE review_sessions ("
    "session_digest TEXT PRIMARY KEY NOT NULL CHECK(length(session_digest)=64 AND session_digest NOT GLOB '*[^0-9a-f]*'), "
    "csrf_digest TEXT NOT NULL CHECK(length(csrf_digest)=64 AND csrf_digest NOT GLOB '*[^0-9a-f]*'), "
    "actor TEXT NOT NULL CHECK(actor='owner'), created_at INTEGER NOT NULL CHECK(created_at>=0), "
    "expires_at INTEGER NOT NULL CHECK(expires_at>created_at), revoked_at INTEGER CHECK(revoked_at IS NULL OR revoked_at>=created_at))"
)
_REVIEW_LOGIN_FAILURES_SQL = (
    "CREATE TABLE review_login_failures ("
    "failure_id TEXT PRIMARY KEY NOT NULL CHECK(length(failure_id)=64 AND failure_id NOT GLOB '*[^0-9a-f]*'), "
    "client_digest TEXT NOT NULL CHECK(length(client_digest)=64 AND client_digest NOT GLOB '*[^0-9a-f]*'), "
    "occurred_at INTEGER NOT NULL CHECK(occurred_at>=0))"
)
_EXPECTED_OBJECTS = (
    ("index", "sqlite_autoindex_review_login_failures_1", None),
    ("index", "sqlite_autoindex_review_sessions_1", None),
    ("table", "review_login_failures", _REVIEW_LOGIN_FAILURES_SQL),
    ("table", "review_sessions", _REVIEW_SESSIONS_SQL),
)


class SQLiteReviewSessionRepository:
    """Admit, authenticate, rotate and revoke the review surface's sessions."""

    def __init__(
        self, connection: sqlite3.Connection, lock: threading.RLock | None = None
    ) -> None:
        self._connection = connection
        self._lock = lock if lock is not None else threading.RLock()

    @classmethod
    def open_ephemeral(cls) -> SQLiteReviewSessionRepository:
        """Open the process-local store used by owned test and qualification runs."""
        connection = sqlite3.connect(
            ":memory:", isolation_level=None, check_same_thread=False
        )
        connection.execute(_REVIEW_SESSIONS_SQL)
        connection.execute(_REVIEW_LOGIN_FAILURES_SQL)
        return cls(connection)

    @classmethod
    def open(cls, path: Path) -> SQLiteReviewSessionRepository:
        """Open or create the exact-current session database at ``path``.

        The parent directory is the composition root's owner-only private
        root; this opener owns only the database file's schema contract.
        """
        connection: sqlite3.Connection | None = None
        descriptor = -1
        created_identity: tuple[int, int] | None = None
        try:
            nofollow = getattr(os, "O_NOFOLLOW", 0)
            if not nofollow:
                raise MetadataReportError(
                    "review session database no-follow access is unavailable"
                )
            flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | nofollow
            if os.path.lexists(path):
                descriptor = os.open(path, flags)
                information = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(information.st_mode)
                    or information.st_uid != os.geteuid()
                    or information.st_mode & 0o077
                ):
                    raise MetadataReportError(
                        "review session database is unavailable or incompatible"
                    )
                _validate_delete_mode_header(descriptor)
            else:
                descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                information = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(information.st_mode)
                    or information.st_uid != os.geteuid()
                    or information.st_mode & 0o077
                ):
                    raise MetadataReportError(
                        "review session database is unavailable or incompatible"
                    )
                created_identity = (information.st_dev, information.st_ino)
            connection = _connect_bound(
                path,
                descriptor,
                cross_thread=True,
                # Only an admitted existing database can own a rollback journal;
                # a new database must reject every orphan sidecar.
                allow_rollback_journal=created_identity is None,
            )
            if connection.execute("PRAGMA journal_mode").fetchone() != ("delete",):
                raise MetadataReportError(
                    "review session database is unavailable or incompatible"
                )
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=5000")
            objects = connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
            if created_identity is not None:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    connection.execute(_REVIEW_SESSIONS_SQL)
                    connection.execute(_REVIEW_LOGIN_FAILURES_SQL)
                    connection.execute("COMMIT")
                except sqlite3.Error:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise
                objects = connection.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                _validate_session_schema(connection, objects)
            else:
                _validate_session_schema(connection, objects)
            return cls(connection)
        except MetadataReportError:
            if connection is not None:
                connection.close()
            if created_identity is not None:
                _unlink_created(path, created_identity)
            raise
        except (OSError, sqlite3.Error) as error:
            if connection is not None:
                connection.close()
            if created_identity is not None:
                _unlink_created(path, created_identity)
            raise MetadataReportError(
                "review session database is unavailable or incompatible"
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def begin_login(
        self,
        now: int,
        client_digest: str,
        window_seconds: int,
        client_maximum: int,
        global_maximum: int,
    ) -> bool:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "DELETE FROM review_login_failures WHERE occurred_at<?",
                    (now - window_seconds,),
                )
                self._connection.execute(
                    "DELETE FROM review_sessions WHERE expires_at<=?", (now,)
                )
                counts = self._connection.execute(
                    "SELECT count(*),count(*) FILTER (WHERE client_digest=?) "
                    "FROM review_login_failures",
                    (client_digest,),
                ).fetchone()
                if counts is None:
                    raise MetadataReportError("review login is unavailable")
                permitted: bool = (
                    counts[0] < global_maximum and counts[1] < client_maximum
                )
                if permitted:
                    identity = hashlib.sha256(
                        f"review-login-attempt-v1:{now}:{secrets.token_hex(16)}".encode()
                    ).hexdigest()
                    self._connection.execute(
                        "INSERT INTO review_login_failures"
                        "(failure_id,client_digest,occurred_at) VALUES(?,?,?)",
                        (identity, client_digest, now),
                    )
                self._connection.execute("COMMIT")
                return permitted
            except MetadataReportError:
                self._rollback()
                raise
            except sqlite3.Error as error:
                self._rollback()
                raise MetadataReportError("review login is unavailable") from error

    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
        client_digest: str,
    ) -> None:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "INSERT INTO review_sessions(session_digest,csrf_digest,actor,created_at,expires_at) VALUES(?,?,'owner',?,?)",
                    (session_digest, csrf_digest, created_at, expires_at),
                )
                # Only the signing-in client's debt is settled, so another
                # client's failures survive the owner's sign-in (#1384).
                self._connection.execute(
                    "DELETE FROM review_login_failures WHERE client_digest=?",
                    (client_digest,),
                )
                self._connection.execute("COMMIT")
            except sqlite3.Error as error:
                self._rollback()
                raise MetadataReportError("review session is unavailable") from error

    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]:
        with self._lock:
            try:
                row = self._connection.execute(
                    "SELECT actor,csrf_digest,expires_at FROM review_sessions WHERE session_digest=? AND revoked_at IS NULL AND expires_at>?",
                    (session_digest, now),
                ).fetchone()
                if (
                    row is None
                    or len(row) != 3
                    or row[0] != "owner"
                    or type(row[1]) is not str
                    or type(row[2]) is not int
                ):
                    raise MetadataReportError("review session is unavailable")
                # The guard above proves the exact ("owner", str, int) shape.
                return cast("tuple[str, str, int]", row)
            except sqlite3.Error as error:
                raise MetadataReportError("review session is unavailable") from error

    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._connection.execute(
                    "UPDATE review_sessions SET csrf_digest=? "
                    "WHERE session_digest=? AND revoked_at IS NULL AND expires_at>? "
                    "RETURNING actor,expires_at",
                    (csrf_digest, session_digest, now),
                ).fetchone()
                if row is None:
                    self._rollback()
                    return "unauthorized"
                if len(row) != 2 or row[0] != "owner" or type(row[1]) is not int:
                    raise MetadataReportError("review session is unavailable")
                self._connection.execute("COMMIT")
                return "rotated"
            except MetadataReportError:
                self._rollback()
                raise
            except sqlite3.Error as error:
                self._rollback()
                raise MetadataReportError("review session is unavailable") from error

    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                cursor = self._connection.execute(
                    "UPDATE review_sessions SET revoked_at=? WHERE session_digest=? AND csrf_digest=? AND revoked_at IS NULL AND expires_at>?",
                    (now, session_digest, csrf_digest, now),
                )
                if cursor.rowcount != 1:
                    active = self._connection.execute(
                        "SELECT 1 FROM review_sessions WHERE session_digest=? AND revoked_at IS NULL AND expires_at>?",
                        (session_digest, now),
                    ).fetchone()
                    self._rollback()
                    return "csrf" if active is not None else "unauthorized"
                self._connection.execute("COMMIT")
                return "revoked"
            except sqlite3.Error as error:
                self._rollback()
                raise MetadataReportError("review session is unavailable") from error

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


def _validate_session_schema(
    connection: sqlite3.Connection, objects: list[tuple[str, str, str | None]]
) -> None:
    if tuple(tuple(value) for value in objects) != _EXPECTED_OBJECTS:
        raise MetadataReportError(
            "review session database is unavailable or incompatible"
        )
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise MetadataReportError(
            "review session database is unavailable or incompatible"
        )
