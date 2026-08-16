"""The review-session aggregate.

Owns the `review_sessions` table and the `review_login_failures` table that
rate-limits admission to it: who is currently signed in to the review surface,
which CSRF token that session carries, and how many failed attempts have
happened inside the current window.

The two tables move together because every method touches both -- a login
attempt reads and prunes failures, a granted session clears them, and expiry
prunes sessions -- so splitting them would put one transaction across two
modules for no gain.

Nothing here reads another aggregate: the session is authority over the review
surface, not over any report, case or policy it later reaches.

`revoke_review_session` belongs here and is deliberately still on the composing
repository. `test_sqlite_logout_classification_is_atomic_across_connections`
proves the CSRF-versus-unauthorized classification is atomic by substituting
`_connection` on the repository and starting a competing logout from inside the
substituted `execute`. An aggregate holds its own reference to the one shared
connection, so moving the method would leave that substitution addressing an
object the statement never reaches. It moves once the composition root owns the
connection every holder sees.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import cast

from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.metadata_report import MetadataReportError


class SQLiteReviewSessionRepository(ConnectionScoped):
    """Admit, authenticate, rotate and revoke the review surface's sessions."""

    def begin_login(
        self, now: int, window_seconds: int, maximum_failures: int
    ) -> bool:
        transaction = self._transaction("begin_login")
        try:
            transaction.begin()
            self._connection.execute(
                "DELETE FROM review_login_failures WHERE occurred_at<?",
                (now - window_seconds,),
            )
            self._connection.execute(
                "DELETE FROM review_sessions WHERE expires_at<=?", (now,)
            )
            count = self._connection.execute(
                "SELECT count(*) FROM review_login_failures"
            ).fetchone()
            if count is None:
                raise MetadataReportError("review login is unavailable")
            permitted: bool = count[0] < maximum_failures
            if permitted:
                identity = hashlib.sha256(
                    f"review-login-attempt-v1:{now}:{secrets.token_hex(16)}".encode()
                ).hexdigest()
                self._connection.execute(
                    "INSERT INTO review_login_failures(failure_id,occurred_at) "
                    "VALUES(?,?)",
                    (identity, now),
                )
            transaction.commit()
            return permitted
        except MetadataReportError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("review login is unavailable") from error

    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
    ) -> None:
        transaction = self._transaction("create_review_session")
        try:
            transaction.begin()
            self._connection.execute(
                "INSERT INTO review_sessions(session_digest,csrf_digest,actor,created_at,expires_at) VALUES(?,?,'owner',?,?)",
                (session_digest, csrf_digest, created_at, expires_at),
            )
            self._connection.execute("DELETE FROM review_login_failures")
            transaction.commit()
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("review session is unavailable") from error

    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]:
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
        transaction = self._transaction("rotate_review_session_csrf")
        try:
            transaction.begin()
            row = self._connection.execute(
                "UPDATE review_sessions SET csrf_digest=? "
                "WHERE session_digest=? AND revoked_at IS NULL AND expires_at>? "
                "RETURNING actor,expires_at",
                (csrf_digest, session_digest, now),
            ).fetchone()
            if row is None:
                transaction.rollback()
                return "unauthorized"
            if (
                len(row) != 2
                or row[0] != "owner"
                or type(row[1]) is not int
            ):
                raise MetadataReportError("review session is unavailable")
            transaction.commit()
            return "rotated"
        except MetadataReportError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("review session is unavailable") from error
