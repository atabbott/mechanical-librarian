from __future__ import annotations

import sqlite3
import threading
from _thread import RLock
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Never, cast

import pytest

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
    ReviewerAccessStoreError,
)
from mechanical_librarian.adapters.persistence.schema import _create_schema
from mechanical_librarian.adapters.persistence.transactions import TransactionScope
from mechanical_librarian.domain.reviewer_access import (
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
)


def _document(revision: int, model: str = "example/vision") -> ReviewerAccessDocument:
    return ReviewerAccessDocument.model_validate(
        {
            "schema_version": "reviewer-access-v3",
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
            '{"cover_metadata":null,"cover_observation":null,"description_metadata":null,"description_rewrite":null,"revision":1,"schema_version":"reviewer-access-v3"}',
        ),
    )
    return ReviewerAccessStore(connection)


class _ReleaseInterruptionLock:
    def __init__(
        self, fault: BaseException, *, before_effect: bool, fault_depth: int
    ) -> None:
        self.lock = RLock()
        self.fault = fault
        self.before_effect = before_effect
        self.fault_depth = fault_depth
        self.armed = False

    def acquire(self) -> None:
        self.lock.acquire()

    def release(self) -> None:
        if (
            self.armed
            and self.before_effect
            and self.lock._recursion_count() == self.fault_depth
        ):
            self.armed = False
            raise self.fault
        self.lock.release()
        if self.armed and self.lock._recursion_count() == self.fault_depth - 1:
            self.armed = False
            raise self.fault

    def _recursion_count(self) -> int:
        return self.lock._recursion_count()


def _interrupt(fault: BaseException) -> Never:
    raise fault


def _assert_lock_available(lock: _ReleaseInterruptionLock) -> None:
    acquired = threading.Event()

    def contend() -> None:
        with lock.lock:
            acquired.set()

    contender = threading.Thread(target=contend)
    contender.start()
    assert acquired.wait(1)
    contender.join(timeout=1)
    assert not contender.is_alive()


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
            '{"cover_metadata":null,"cover_observation":null,"description_metadata":null,"description_rewrite":null,"revision":1,"schema_version":"reviewer-access-v3"}',
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


@pytest.mark.parametrize("fault_type", (KeyboardInterrupt, SystemExit))
def test_reviewer_replace_body_process_control_rolls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault_type: type[BaseException],
) -> None:
    database = tmp_path / "reviewer.sqlite3"
    connection = sqlite3.connect(database, isolation_level=None)
    _create_schema(connection)
    lock = _ReleaseInterruptionLock(
        SystemExit("unused"), before_effect=False, fault_depth=1
    )
    store = ReviewerAccessStore(connection, cast(RLock, lock))
    fault = fault_type("private reviewer body")
    before = list(connection.iterdump())
    monkeypatch.setattr(store, "_read", lambda: _interrupt(fault))

    with pytest.raises(fault_type) as raised:
        store.replace(_document(2), 1, openrouter_key="secret-key")

    assert raised.value is fault
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert not connection.in_transaction
    assert list(connection.iterdump()) == before
    assert lock._recursion_count() == 0
    _assert_lock_available(lock)
    second = sqlite3.connect(database, isolation_level=None, timeout=0)
    second.execute("BEGIN IMMEDIATE")
    second.rollback()
    second.close()
    connection.close()


@pytest.mark.parametrize("fault_type", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("before_effect", (False, True))
def test_reviewer_replace_commit_release_propagates_process_control(
    fault_type: type[BaseException], before_effect: bool
) -> None:
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
            '{"cover_metadata":null,"cover_observation":null,"description_metadata":null,"description_rewrite":null,"revision":1,"schema_version":"reviewer-access-v3"}',
        ),
    )
    fault = fault_type("private reviewer commit release")
    lock = _ReleaseInterruptionLock(fault, before_effect=before_effect, fault_depth=1)
    store = ReviewerAccessStore(connection, cast(RLock, lock))
    lock.armed = True

    with pytest.raises(fault_type) as raised:
        store.replace(_document(2), 1, openrouter_key="secret-key")

    assert raised.value is fault
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert not connection.in_transaction
    assert store.read() == (_document(2), True)
    assert lock._recursion_count() == 0
    _assert_lock_available(lock)
    connection.close()


@pytest.mark.parametrize("fault_type", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("before_effect", (False, True))
def test_nested_reviewer_read_release_preserves_outer_authority(
    fault_type: type[BaseException], before_effect: bool
) -> None:
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
            '{"cover_metadata":null,"cover_observation":null,"description_metadata":null,"description_rewrite":null,"revision":1,"schema_version":"reviewer-access-v3"}',
        ),
    )
    fault = fault_type("private nested reviewer read release")
    lock = _ReleaseInterruptionLock(fault, before_effect=before_effect, fault_depth=2)
    store = ReviewerAccessStore(connection, cast(RLock, lock))
    transaction = TransactionScope(
        connection, "nested_reviewer_read", serialization_lock=cast(RLock, lock)
    )
    transaction.begin()
    connection.execute(
        "INSERT INTO reviewer_access_secrets(field_id,value) VALUES('openrouter.api_key','secret-key')"
    )
    lock.armed = True

    with pytest.raises(fault_type) as raised:
        try:
            store.read()
        except BaseException as error:
            transaction.rollback()
            raise error from None

    assert raised.value is fault
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert not connection.in_transaction
    assert connection.execute("SELECT * FROM reviewer_access_secrets").fetchall() == []
    assert lock._recursion_count() == 0
    _assert_lock_available(lock)
    connection.close()


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
        'INSERT INTO reviewer_access VALUES(1,1,\'{"cover_metadata":null,"cover_observation":null,"description_metadata":null,"description_rewrite":null,"revision":1,"schema_version":"reviewer-access-v3"}\')',
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


def test_exact_predecessor_empty_reviewer_table_is_rejected_without_mutation():
    from mechanical_librarian.adapters.persistence.schema import (
        _register_casefold,
        _validate_schema,
    )
    from mechanical_librarian.application.metadata_report import MetadataReportError

    connection = sqlite3.connect(":memory:", isolation_level=None)
    _register_casefold(connection)
    _create_schema(connection)
    _validate_schema(connection)
    connection.execute("DROP TABLE reviewer_access")
    connection.execute(
        "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=1),document TEXT NOT NULL CHECK(typeof(document)='text' AND length(CAST(document AS BLOB)) BETWEEN 2 AND 262144))"
    )
    before = tuple(connection.iterdump())
    with pytest.raises(MetadataReportError):
        _validate_schema(connection)
    assert tuple(connection.iterdump()) == before
    connection.close()


def test_predecessor_description_price_document_reads_as_current() -> None:
    predecessor = (
        '{"cover_metadata":null,"cover_observation":null,"description_metadata":'
        '{"completion_price":"0.000002","model":"example/b","observed_at":1,'
        '"prompt_price":"0.000001","request_price":"0","structured_outputs":true,'
        '"text_input":true,"text_output":true},"description_rewrite":'
        '{"engine":"openrouter","model":"example/b"},"revision":2,'
        '"schema_version":"reviewer-access-v3"}'
    )
    unpriced = predecessor.replace('"request_price":"0"', '"request_price":null')
    keyless = predecessor.replace('"request_price":"0",', "")

    def stored(payload: str) -> ReviewerAccessStore:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.execute(
            "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,document TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE reviewer_access_secrets (field_id TEXT PRIMARY KEY,value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO reviewer_access(singleton,revision,document) VALUES(1,2,?)",
            (payload,),
        )
        return ReviewerAccessStore(connection)

    for payload, request_price in ((predecessor, "0"), (unpriced, None)):
        document, configured = stored(payload).read()
        assert document.description_metadata is not None
        assert document.description_metadata.request_price == request_price
        assert not configured
        assert _canonical_json(document.model_dump()) == payload
    with pytest.raises(ReviewerAccessStoreError, match="unavailable"):
        stored(keyless).read()
