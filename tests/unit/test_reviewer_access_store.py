from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
    ReviewerAccessStoreError,
)
from mechanical_librarian.domain.reviewer_access import (
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
)


def _document(revision: int, model: str = "example/vision") -> ReviewerAccessDocument:
    return ReviewerAccessDocument.model_validate(
        {
            "schema_version": "reviewer-access-v1",
            "revision": revision,
            "cover_observation": {"engine": "openrouter", "model": model},
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


@contextmanager
def _connection_store() -> Iterator[tuple[sqlite3.Connection, ReviewerAccessStore]]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute(
        "CREATE TABLE reviewer_access (singleton INTEGER,revision INTEGER,document TEXT)"
    )
    connection.execute(
        "CREATE TABLE reviewer_access_secrets (field_id TEXT,value TEXT)"
    )
    connection.execute(
        "INSERT INTO reviewer_access(singleton,revision,document) VALUES(1,1,?)",
        (
            '{"cover_observation":null,"revision":1,"schema_version":"reviewer-access-v1"}',
        ),
    )
    try:
        yield connection, ReviewerAccessStore(connection)
    finally:
        connection.close()


def test_absent_current_document_reads_as_no_reviewer_and_is_created_by_first_write() -> (
    None
):
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute(
        "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,document TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE reviewer_access_secrets (field_id TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    store = ReviewerAccessStore(connection)
    assert store.read()[0].cover_observation is None
    assert store.replace(_document(2), 1)[0].revision == 2


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


@pytest.mark.parametrize(
    "mutation",
    (
        "DELETE FROM reviewer_access",
        "UPDATE reviewer_access SET revision=x'31'",
        "UPDATE reviewer_access SET document='{}'",
        'INSERT INTO reviewer_access VALUES(1,1,\'{"cover_observation":null,"revision":1,"schema_version":"reviewer-access-v1"}\')',
    ),
)
def test_corrupt_duplicate_or_missing_singleton_state_fails_closed(
    mutation: str,
) -> None:
    with _connection_store() as (connection, store):
        connection.execute(mutation)
        if mutation == "DELETE FROM reviewer_access":
            connection.execute(
                "INSERT INTO reviewer_access_secrets VALUES('openrouter.api_key','secret-key')"
            )
        with pytest.raises(ReviewerAccessStoreError, match="unavailable"):
            store.read()


@pytest.mark.parametrize(
    "rows",
    (
        (("unexpected", "secret-key"),),
        (("openrouter.api_key", ""),),
        (("openrouter.api_key", "secret-key"), ("openrouter.api_key", "other")),
    ),
)
def test_corrupt_or_duplicate_secret_rows_fail_closed(
    rows: tuple[tuple[str, str], ...],
) -> None:
    with _connection_store() as (connection, store):
        connection.executemany("INSERT INTO reviewer_access_secrets VALUES(?,?)", rows)
        with pytest.raises(ReviewerAccessStoreError, match="unavailable"):
            store.read()


def test_stale_compare_and_swap_rolls_back_secret_replacement() -> None:
    with _connection_store() as (connection, store):
        connection.execute(
            "INSERT INTO reviewer_access_secrets VALUES('openrouter.api_key','original')"
        )
        with pytest.raises(ReviewerAccessStoreError, match="stale"):
            store.replace(_document(3), 2, openrouter_key="replacement")
        assert connection.execute(
            "SELECT value FROM reviewer_access_secrets"
        ).fetchall() == [("original",)]


def test_secret_write_failure_rolls_back_document_replacement() -> None:
    store = _store()
    connection = store._connection
    connection.execute(
        "CREATE TRIGGER reject_reviewer_secret BEFORE INSERT ON reviewer_access_secrets "
        "BEGIN SELECT RAISE(ABORT,'refused'); END"
    )
    with pytest.raises(ReviewerAccessStoreError, match="unavailable"):
        store.replace(_document(2), 1, openrouter_key="secret-key")
    document, configured = store.read()
    assert document.revision == 1
    assert configured is False


def test_public_projection_never_contains_the_secret_value() -> None:
    store = _store()
    saved, configured = store.replace(_document(2), 1, openrouter_key="secret-key")
    assert configured is True
    assert "secret-key" not in saved.model_dump_json()
    assert "secret-key" not in repr(store.read())


def test_independent_connections_never_mix_document_and_secret_snapshots(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reviewer.sqlite3"
    writer_connection = sqlite3.connect(
        path, isolation_level=None, check_same_thread=False
    )
    writer_connection.execute(
        "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,document TEXT NOT NULL)"
    )
    writer_connection.execute(
        "CREATE TABLE reviewer_access_secrets (field_id TEXT PRIMARY KEY,value TEXT NOT NULL)"
    )
    reader_connection = sqlite3.connect(path, isolation_level=None)
    writer = ReviewerAccessStore(writer_connection)
    reader = ReviewerAccessStore(reader_connection)
    writer.replace(_document(2, "example/model-a"), 1, openrouter_key="key-a")
    finished = threading.Event()

    def replace_repeatedly() -> None:
        revision = 2
        for index in range(80):
            suffix = "b" if index % 2 == 0 else "a"
            writer.replace(
                _document(revision + 1, f"example/model-{suffix}"),
                revision,
                openrouter_key=f"key-{suffix}",
            )
            revision += 1
        finished.set()

    thread = threading.Thread(target=replace_repeatedly)
    thread.start()
    while not finished.is_set():
        document, key = reader.snapshot()
        assert isinstance(document.cover_observation, OpenRouterTaskSelection)
        assert key is not None
        assert (
            document.cover_observation.model.rsplit("-", 1)[1] == key.rsplit("-", 1)[1]
        )
    thread.join(timeout=2)
    assert not thread.is_alive()
