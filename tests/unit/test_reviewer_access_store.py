from __future__ import annotations

import sqlite3

import pytest

from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
    ReviewerAccessStoreError,
)
from mechanical_librarian.domain.reviewer_access import ReviewerAccessDocument


def _document(revision: int) -> ReviewerAccessDocument:
    return ReviewerAccessDocument.model_validate(
        {
            "schema_version": "reviewer-access-v1",
            "revision": revision,
            "cover_observation": {"engine": "openrouter", "model": "example/vision"},
        }
    )


def _store() -> ReviewerAccessStore:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute(
        "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,document TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE reviewer_access_secrets (field_id TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO reviewer_access(singleton,revision,document) VALUES(1,1,?)",
        (
            '{"cover_observation":null,"revision":1,"schema_version":"reviewer-access-v1"}',
        ),
    )
    return ReviewerAccessStore(connection)


def test_atomic_replace_and_clear_never_expose_the_openrouter_secret() -> None:
    store = _store()
    saved, configured = store.replace(_document(2), 1, openrouter_key="secret-key")
    assert saved.revision == 2
    assert configured is True
    assert store.read() == (saved, True)
    cleared, configured = store.replace(_document(3), 2, clear_openrouter_key=True)
    assert cleared.revision == 3
    assert configured is False


def test_compare_and_swap_refuses_a_lost_writer() -> None:
    store = _store()
    store.replace(_document(2), 1)
    with pytest.raises(ReviewerAccessStoreError, match="stale"):
        store.replace(_document(2), 1)
