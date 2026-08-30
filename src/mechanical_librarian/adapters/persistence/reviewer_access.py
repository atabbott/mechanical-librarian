"""Exact-current SQLite authority for owner-managed reviewer access."""

from __future__ import annotations

import sqlite3
from threading import RLock

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessRepositoryError,
)
from mechanical_librarian.domain.reviewer_access import ReviewerAccessDocument
from mechanical_librarian.domain.validation import credential

_OPENROUTER_SECRET = "openrouter.api_key"


class ReviewerAccessStoreError(ReviewerAccessRepositoryError):
    """Value-free reviewer-access persistence failure."""


class ReviewerAccessStore(ConnectionScoped):
    """One current document plus one write-only OpenRouter secret."""

    def __init__(
        self, connection: sqlite3.Connection, lock: RLock | None = None
    ) -> None:
        super().__init__(connection, serialization_lock=lock)

    def read(self) -> tuple[ReviewerAccessDocument, bool]:
        def read() -> tuple[ReviewerAccessDocument, bool]:
            document, key = self._snapshot()
            return document, key is not None

        return self._serialized(read)

    def snapshot(self) -> tuple[ReviewerAccessDocument, str | None]:
        """Capture selection and its required secret in one SQLite read boundary."""

        return self._serialized(self._snapshot)

    def revision_is_current(self, revision: int) -> bool:
        def check() -> bool:
            try:
                return self._connection.execute(
                    "SELECT 1 FROM reviewer_access WHERE singleton=1 AND revision=?",
                    (revision,),
                ).fetchone() is not None or (
                    revision == 1
                    and self._connection.execute(
                        "SELECT 1 FROM reviewer_access"
                    ).fetchone()
                    is None
                )
            except sqlite3.Error as error:
                raise ReviewerAccessStoreError(
                    "reviewer access is unavailable"
                ) from error

        return self._serialized(check)

    def openrouter_key(self) -> str:
        """Snapshot the write-only key without holding the store lock afterwards."""

        def snapshot() -> str:
            try:
                return self._openrouter_key()
            except sqlite3.Error as error:
                raise ReviewerAccessStoreError(
                    "reviewer access is unavailable"
                ) from error

        return self._serialized(snapshot)

    def _openrouter_key(self) -> str:
        rows = self._secret_rows()
        if (
            len(rows) != 1
            or rows[0][0] != _OPENROUTER_SECRET
            or type(rows[0][1]) is not str
            or not credential(rows[0][1])
        ):
            raise ReviewerAccessStoreError("reviewer access is unavailable")
        return rows[0][1]

    def replace(
        self,
        document: ReviewerAccessDocument,
        expected_revision: int,
        openrouter_key: str | None = None,
        clear_openrouter_key: bool = False,
    ) -> tuple[ReviewerAccessDocument, bool]:
        if (
            type(expected_revision) is not int
            or document.revision != expected_revision + 1
            or type(clear_openrouter_key) is not bool
            or (
                openrouter_key is not None
                and (type(openrouter_key) is not str or not credential(openrouter_key))
            )
            or (openrouter_key is not None and clear_openrouter_key)
        ):
            raise ReviewerAccessStoreError("reviewer access is invalid")

        transaction = self._transaction("replace_reviewer_access")
        try:
            transaction.begin()
            current, _ = self._read()
            if current.revision != expected_revision:
                raise ReviewerAccessStoreError("reviewer access is stale")
            cursor = self._connection.execute(
                "UPDATE reviewer_access SET revision=?,document=? WHERE singleton=1 AND revision=?",
                (
                    document.revision,
                    _canonical_json(document.model_dump()),
                    expected_revision,
                ),
            )
            if cursor.rowcount == 0:
                if (
                    expected_revision != 1
                    or self._connection.execute(
                        "SELECT 1 FROM reviewer_access WHERE singleton=1"
                    ).fetchone()
                    is not None
                ):
                    raise ReviewerAccessStoreError("reviewer access is stale")
                self._connection.execute(
                    "INSERT INTO reviewer_access(singleton,revision,document) VALUES(1,?,?)",
                    (document.revision, _canonical_json(document.model_dump())),
                )
            if clear_openrouter_key:
                self._connection.execute(
                    "DELETE FROM reviewer_access_secrets WHERE field_id=?",
                    (_OPENROUTER_SECRET,),
                )
            elif openrouter_key is not None:
                self._connection.execute(
                    "INSERT INTO reviewer_access_secrets(field_id,value) VALUES(?,?) "
                    "ON CONFLICT(field_id) DO UPDATE SET value=excluded.value",
                    (_OPENROUTER_SECRET, openrouter_key),
                )
            configured = self._configured()
            transaction.commit()
            return document, configured
        except ReviewerAccessStoreError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise ReviewerAccessStoreError("reviewer access is unavailable") from error
        except BaseException as error:
            transaction.rollback()
            raise error from None

    def _read(self) -> tuple[ReviewerAccessDocument, bool]:
        document, key = self._snapshot()
        return document, key is not None

    def _snapshot(self) -> tuple[ReviewerAccessDocument, str | None]:
        """Read document and every secret row from one SQLite snapshot statement."""
        try:
            rows = self._connection.execute(
                "SELECT r.revision,r.document,s.field_id,s.value,"
                "(SELECT COUNT(*) FROM reviewer_access) "
                "FROM (SELECT 1) AS anchor "
                "LEFT JOIN reviewer_access AS r ON r.singleton=1 "
                "LEFT JOIN reviewer_access_secrets AS s ON 1=1 "
                "ORDER BY s.field_id"
            ).fetchall()
            if not rows:
                raise ReviewerAccessStoreError("reviewer access is unavailable")
            revision, payload = rows[0][0], rows[0][1]
            reviewer_rows = rows[0][4]
            secret_rows = [(row[2], row[3]) for row in rows if row[2] is not None]
            if type(reviewer_rows) is not int or reviewer_rows not in {0, 1}:
                raise ReviewerAccessStoreError("reviewer access is unavailable")
            if revision is None and payload is None:
                if reviewer_rows != 0 or secret_rows:
                    raise ReviewerAccessStoreError("reviewer access is unavailable")
                return (
                    ReviewerAccessDocument(
                        schema_version="reviewer-access-v1", revision=1
                    ),
                    None,
                )
            if (
                type(revision) is not int
                or type(payload) is not str
                or len(secret_rows) > 1
            ):
                raise ReviewerAccessStoreError("reviewer access is unavailable")
            document = ReviewerAccessDocument.model_validate_json(payload)
            if (
                document.revision != revision
                or _canonical_json(document.model_dump()) != payload
            ):
                raise ReviewerAccessStoreError("reviewer access is unavailable")
            if not secret_rows:
                return document, None
            field_id, key = secret_rows[0]
            if (
                field_id != _OPENROUTER_SECRET
                or type(key) is not str
                or not credential(key)
            ):
                raise ReviewerAccessStoreError("reviewer access is unavailable")
            return document, key
        except (sqlite3.Error, ValueError) as error:
            if isinstance(error, ReviewerAccessStoreError):
                raise
            raise ReviewerAccessStoreError("reviewer access is unavailable") from error

    def _configured(self) -> bool:
        rows = self._secret_rows()
        if len(rows) > 1 or (
            rows
            and (
                rows[0][0] != _OPENROUTER_SECRET
                or type(rows[0][1]) is not str
                or not credential(rows[0][1])
            )
        ):
            raise ReviewerAccessStoreError("reviewer access is unavailable")
        return len(rows) == 1

    def _secret_rows(self) -> list[tuple[object, object]]:
        return self._connection.execute(
            "SELECT field_id,value FROM reviewer_access_secrets ORDER BY field_id"
        ).fetchall()


__all__ = ["ReviewerAccessStore", "ReviewerAccessStoreError"]
