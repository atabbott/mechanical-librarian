"""Exact-current SQLite authority for owner-managed reviewer access."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from threading import RLock
from typing import TypeVar

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.application.reviewer_access import (
    ReviewerAccessRepositoryError,
)
from mechanical_librarian.domain.reviewer_access import ReviewerAccessDocument
from mechanical_librarian.domain.validation import credential

_OPENROUTER_SECRET = "openrouter.api_key"
_T = TypeVar("_T")


class ReviewerAccessStoreError(ReviewerAccessRepositoryError):
    """Value-free reviewer-access persistence failure."""


class ReviewerAccessStore:
    """One current document plus one write-only OpenRouter secret."""

    def __init__(
        self, connection: sqlite3.Connection, lock: RLock | None = None
    ) -> None:
        self._connection = connection
        self._lock = lock

    def read(self) -> tuple[ReviewerAccessDocument, bool]:
        return self._with_lock(self._read)

    def snapshot(self) -> tuple[ReviewerAccessDocument, str | None]:
        """Capture selection and its required secret in one SQLite read boundary."""

        def capture() -> tuple[ReviewerAccessDocument, str | None]:
            document, configured = self._read()
            return document, self._openrouter_key() if configured else None

        return self._with_lock(capture)

    def revision_is_current(self, revision: int) -> bool:
        def check() -> bool:
            document, _configured = self._read()
            return document.revision == revision

        return self._with_lock(check)

    def openrouter_key(self) -> str:
        """Snapshot the write-only key without holding the store lock afterwards."""

        def snapshot() -> str:
            try:
                return self._openrouter_key()
            except sqlite3.Error as error:
                raise ReviewerAccessStoreError(
                    "reviewer access is unavailable"
                ) from error

        return self._with_lock(snapshot)

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

        def commit() -> tuple[ReviewerAccessDocument, bool]:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
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
                self._connection.commit()
                return document, self._configured()
            except (sqlite3.Error, ReviewerAccessStoreError) as error:
                self._connection.rollback()
                if isinstance(error, ReviewerAccessStoreError):
                    raise
                raise ReviewerAccessStoreError(
                    "reviewer access is unavailable"
                ) from error

        return self._with_lock(commit)

    def _read(self) -> tuple[ReviewerAccessDocument, bool]:
        try:
            rows = self._connection.execute(
                "SELECT revision,document FROM reviewer_access WHERE singleton=1"
            ).fetchall()
            if not rows:
                if self._secret_rows():
                    raise ReviewerAccessStoreError("reviewer access is unavailable")
                return (
                    ReviewerAccessDocument(
                        schema_version="reviewer-access-v1", revision=1
                    ),
                    self._configured(),
                )
            if (
                len(rows) != 1
                or type(rows[0][0]) is not int
                or type(rows[0][1]) is not str
            ):
                raise ReviewerAccessStoreError("reviewer access is unavailable")
            document = ReviewerAccessDocument.model_validate_json(rows[0][1])
            if (
                document.revision != rows[0][0]
                or _canonical_json(document.model_dump()) != rows[0][1]
            ):
                raise ReviewerAccessStoreError("reviewer access is unavailable")
            return document, self._configured()
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

    def _with_lock(self, operation: Callable[[], _T]) -> _T:
        if self._lock is None:
            return operation()
        with self._lock:
            return operation()


__all__ = ["ReviewerAccessStore", "ReviewerAccessStoreError"]
