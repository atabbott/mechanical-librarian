"""The owner-link store: append-only rows, closed values, one current link (#1477)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkChanged,
    OwnerLinkItem,
    OwnerLinkStoreError,
    current_owner_link,
    record_owner_link,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    preparation_work_id,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.owner_link import (
    OwnerLinkAction,
    OwnerLinkScope,
    OwnerLinkTarget,
)

ITEM = OwnerLinkItem("abs", "Books", "item-1")
WORK = OwnerLinkTarget(OwnerLinkScope.WORK, "open_library", "/works/OL1W", "A Title")
EDITION = OwnerLinkTarget(
    OwnerLinkScope.EDITION, "google_books", "9780306406157", "A Title", ("Ann Author",)
)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    path = tmp_path / "state.sqlite"
    open_repository(path).close()
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        yield connection
    finally:
        connection.close()


def _record(
    connection: sqlite3.Connection,
    action: OwnerLinkAction,
    expected: str | None,
    target: OwnerLinkTarget | None,
    request: str,
    *,
    item: OwnerLinkItem = ITEM,
    medium: str = "ebook",
) -> str:
    return record_owner_link(
        connection,
        item=item,
        action=action,
        expected_current=expected,
        target=target,
        source="search",
        medium=medium,
        actor="owner",
        occurred_at=1_700_000_000,
        request_id=request,
        request_sha256="a" * 64,
    ).link_row_id


def _in_transaction(connection: sqlite3.Connection, body) -> object:
    connection.execute("BEGIN IMMEDIATE")
    try:
        value = body()
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")
    return value


def _insert_work(connection: sqlite3.Connection) -> str:
    work_id = preparation_work_id(ITEM.connection_name, ITEM.library_name, ITEM.item_id)
    connection.execute(
        "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,connection_name,"
        "library_name,library_id,item_id,file_identity,source_revision,source_fingerprint,"
        "producer_fingerprint,catalogue_generation,catalogue_fingerprint,stage,state,updated_at) "
        "VALUES(?,?,0,?,?,'lib',?,?,0,?,?,1,?,'complete','failed',0)",
        (
            work_id,
            "scan",
            ITEM.connection_name,
            ITEM.library_name,
            ITEM.item_id,
            "b" * 64,
            "c" * 64,
            "d" * 64,
            "e" * 64,
        ),
    )
    return work_id


def _fresh(connection: sqlite3.Connection, work_id: str) -> int:
    return connection.execute(
        "SELECT refresh_requested FROM review_preparation_work WHERE work_id=?",
        (work_id,),
    ).fetchone()[0]


def test_link_replace_withdraw_follow_one_current_link(
    database: sqlite3.Connection,
) -> None:
    first = _in_transaction(
        database,
        lambda: _record(database, OwnerLinkAction.LINK, None, WORK, "request-00000001"),
    )
    current = current_owner_link(database, "abs", "Books", "item-1")
    assert current is not None and current.row_id == first
    assert current.scope is OwnerLinkScope.WORK
    second = _in_transaction(
        database,
        lambda: _record(
            database, OwnerLinkAction.REPLACE, first, EDITION, "request-00000002"
        ),
    )
    current = current_owner_link(database, "abs", "Books", "item-1")
    assert current is not None and current.row_id == second
    assert current.identity == "9780306406157"
    _in_transaction(
        database,
        lambda: _record(
            database, OwnerLinkAction.WITHDRAW, second, None, "request-00000003"
        ),
    )
    assert current_owner_link(database, "abs", "Books", "item-1") is None
    sequences = database.execute(
        "SELECT sequence,action FROM owner_links ORDER BY sequence"
    ).fetchall()
    assert sequences == [(1, "link"), (2, "replace"), (3, "withdraw")]


@pytest.mark.parametrize(
    ("expected", "action"),
    [
        pytest.param("stale", OwnerLinkAction.REPLACE, id="stale-current"),
        pytest.param(None, OwnerLinkAction.LINK, id="link-over-current"),
    ],
)
def test_a_changed_current_link_is_refused(
    database: sqlite3.Connection, expected: str | None, action: OwnerLinkAction
) -> None:
    _in_transaction(
        database,
        lambda: _record(database, OwnerLinkAction.LINK, None, WORK, "request-00000001"),
    )
    with pytest.raises(OwnerLinkChanged):
        _in_transaction(
            database,
            lambda: _record(database, action, expected, EDITION, "request-00000002"),
        )
    assert database.execute("SELECT count(*) FROM owner_links").fetchone() == (1,)


def test_many_links_record_in_one_caller_transaction(
    database: sqlite3.Connection,
) -> None:
    items = [OwnerLinkItem("abs", "Books", f"item-{index}") for index in range(5)]

    def body() -> None:
        for index, item in enumerate(items):
            _record(
                database,
                OwnerLinkAction.LINK,
                None,
                WORK,
                f"request-bulk-{index:04d}",
                item=item,
            )

    _in_transaction(database, body)
    assert all(
        current_owner_link(
            database, item.connection_name, item.library_name, item.item_id
        )
        is not None
        for item in items
    )


def test_record_needs_the_callers_transaction(database: sqlite3.Connection) -> None:
    with pytest.raises(OwnerLinkStoreError):
        _record(database, OwnerLinkAction.LINK, None, WORK, "request-00000001")


def test_rollback_leaves_neither_row_nor_fresh_mark(
    database: sqlite3.Connection,
) -> None:
    work_id = _insert_work(database)
    with pytest.raises(RuntimeError):

        def body() -> None:
            _record(database, OwnerLinkAction.LINK, None, WORK, "request-00000001")
            assert _fresh(database, work_id) == 1
            raise RuntimeError("caller failed after the write")

        _in_transaction(database, body)
    assert database.execute("SELECT count(*) FROM owner_links").fetchone() == (0,)
    assert _fresh(database, work_id) == 0
    _in_transaction(
        database,
        lambda: _record(database, OwnerLinkAction.LINK, None, WORK, "request-00000001"),
    )
    assert _fresh(database, work_id) == 1


@pytest.mark.parametrize(
    "statement",
    [
        pytest.param("UPDATE owner_links SET actor='other'", id="update"),
        pytest.param("DELETE FROM owner_links", id="delete"),
    ],
)
def test_rows_refuse_update_and_delete(
    database: sqlite3.Connection, statement: str
) -> None:
    _in_transaction(
        database,
        lambda: _record(database, OwnerLinkAction.LINK, None, WORK, "request-00000001"),
    )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        database.execute(statement)


_ROW = {
    "link_row_id": "owner_link_" + "1" * 64,
    "connection_name": "abs",
    "library_name": "Books",
    "item_id": "item-9",
    "sequence": 1,
    "action": "link",
    "source": "search",
    "medium": "ebook",
    "scope": "work",
    "provider": "open_library",
    "record_identity": "/works/OL1W",
    "display_title": "A Title",
    "display_creators": "[]",
    "actor": "owner",
    "occurred_at": 1,
    "request_id": "request-raw-0001",
    "request_sha256": "a" * 64,
    "link_digest": "b" * 64,
}


def _raw_insert(connection: sqlite3.Connection, **changes: object) -> None:
    row = {**_ROW, **changes}
    connection.execute(
        f"INSERT INTO owner_links({','.join(row)}) VALUES({','.join('?' * len(row))})",
        tuple(row.values()),
    )


def test_a_valid_raw_row_is_admitted(database: sqlite3.Connection) -> None:
    _raw_insert(database)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"action": "relink"}, id="action"),
        pytest.param({"source": "file"}, id="source"),
        pytest.param({"medium": "comic"}, id="medium"),
        pytest.param({"scope": "series"}, id="scope"),
        pytest.param({"provider": "itunes_search"}, id="provider"),
        pytest.param({"record_identity": "/works/OL01W"}, id="identity-grammar"),
        pytest.param({"record_identity": "OL1W"}, id="identity-shape"),
        pytest.param(
            {
                "scope": "recording",
                "provider": "audible_catalog",
                "record_identity": "B000000001",
            },
            id="recording-on-ebook",
        ),
        pytest.param({"display_title": ""}, id="empty-title"),
        pytest.param(
            {"display_creators": "[" + ",".join(['"a"'] * 11) + "]"}, id="creators"
        ),
        pytest.param({"display_creators": None}, id="link-without-display"),
        pytest.param({"action": "withdraw", "sequence": 1}, id="withdraw-with-target"),
        pytest.param({"link_row_id": "owner_link_x"}, id="row-id"),
        pytest.param({"request_id": "short"}, id="request-id"),
        pytest.param({"sequence": 2}, id="sequence-gap"),
    ],
)
def test_closed_values_are_refused(
    database: sqlite3.Connection, changes: dict[str, object]
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(database, **changes)


def test_withdraw_without_a_current_link_is_refused(
    database: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="current link"):
        _raw_insert(
            database,
            action="withdraw",
            scope=None,
            provider=None,
            record_identity=None,
            display_title=None,
            display_creators=None,
            link_digest=None,
        )


def test_schema_admission_refuses_a_database_without_the_link_table(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state.sqlite"
    open_repository(path).close()
    connection = sqlite3.connect(path)
    connection.execute("DROP VIEW current_owner_links")
    connection.execute("DROP TABLE owner_links")
    connection.commit()
    connection.close()
    with pytest.raises(MetadataReportError):
        open_repository(path)


def test_a_reused_request_is_refused_as_a_row_not_as_a_changed_link(
    database: sqlite3.Connection,
) -> None:
    # Only the sequence trigger means another link landed first; a reused
    # request id is a refused row and never `link_changed`.
    _in_transaction(
        database,
        lambda: _record(database, OwnerLinkAction.LINK, None, WORK, "request-00000001"),
    )
    other = OwnerLinkItem("abs", "Books", "item-2")
    with pytest.raises(OwnerLinkStoreError) as refused:
        _in_transaction(
            database,
            lambda: _record(
                database,
                OwnerLinkAction.LINK,
                None,
                WORK,
                "request-00000001",
                item=other,
            ),
        )
    assert type(refused.value) is OwnerLinkStoreError


def test_a_link_landing_first_is_refused_by_the_trigger_as_link_changed(
    database: sqlite3.Connection,
) -> None:
    from unittest import mock

    from mechanical_librarian.adapters.persistence import owner_links

    _in_transaction(
        database,
        lambda: _record(database, OwnerLinkAction.LINK, None, WORK, "request-00000001"),
    )
    # A reader that saw no link, as a racing writer would have.
    with (
        mock.patch.object(owner_links, "_latest", return_value=None),
        pytest.raises(OwnerLinkChanged),
    ):
        _in_transaction(
            database,
            lambda: _record(
                database, OwnerLinkAction.LINK, None, EDITION, "request-00000002"
            ),
        )


@pytest.mark.parametrize(
    ("item", "medium", "target"),
    [
        pytest.param(OwnerLinkItem("", "Books", "item-1"), "ebook", WORK, id="empty"),
        pytest.param(
            OwnerLinkItem("abs", "Books", "i" * 513), "ebook", WORK, id="long-item"
        ),
        pytest.param(
            ITEM,
            "audiobook",
            WORK.__class__(OwnerLinkScope.WORK, "inventaire", "wd:Q1", "A Title"),
            id="source-not-for-medium",
        ),
    ],
)
def test_a_row_the_table_would_refuse_is_refused_before_insert(
    database: sqlite3.Connection,
    item: OwnerLinkItem,
    medium: str,
    target: OwnerLinkTarget,
) -> None:
    with pytest.raises(OwnerLinkStoreError) as refused:
        _in_transaction(
            database,
            lambda: _record(
                database,
                OwnerLinkAction.LINK,
                None,
                target,
                "request-00000003",
                item=item,
                medium=medium,
            ),
        )
    assert type(refused.value) is OwnerLinkStoreError
    assert database.execute("SELECT count(*) FROM owner_links").fetchone() == (0,)
