"""The owner-link store: append-only rows, closed values, one current link (#1477)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkChanged,
    OwnerLinkIdempotencyConflict,
    OwnerLinkItem,
    OwnerLinkStoreError,
    choose_owner_link,
    current_owner_link,
    current_owner_link_items,
    current_owner_link_row,
    owner_link_by_request,
    owner_link_rows,
    record_owner_link,
    withdraw_owner_link,
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
        pytest.param({"source": "imported"}, id="source"),
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


# -- The owner's choose and withdraw writes (S16) -----------------------------


def _choose(
    connection: sqlite3.Connection,
    request: str,
    *,
    expected: str | None = None,
    target: OwnerLinkTarget = WORK,
    digest: str = "a" * 64,
):
    return choose_owner_link(
        connection,
        item=ITEM,
        expected_current=expected,
        target=target,
        medium="ebook",
        actor="owner",
        occurred_at=1_700_000_100,
        request_id=request,
        request_sha256=digest,
    )


def _withdraw(
    connection: sqlite3.Connection, request: str, expected: str, digest: str = "c" * 64
):
    return withdraw_owner_link(
        connection,
        item=ITEM,
        expected_current=expected,
        medium="ebook",
        actor="owner",
        occurred_at=1_700_000_200,
        request_id=request,
        request_sha256=digest,
    )


def _count(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT COUNT(*) FROM owner_links").fetchone()[0]


def test_choose_links_then_replaces_then_withdraw_ends_the_link(
    database: sqlite3.Connection,
) -> None:
    linked, replayed = _choose(database, "request-choose-0001")
    assert (linked.action, replayed, database.in_transaction) == (
        OwnerLinkAction.LINK,
        False,
        False,
    )
    replaced, _ = _choose(
        database, "request-choose-0002", expected=linked.link_row_id, target=EDITION
    )
    assert replaced.action is OwnerLinkAction.REPLACE
    current = current_owner_link_row(database, ITEM)
    assert current is not None and current.target == EDITION
    withdrawn, _ = _withdraw(database, "request-withdraw-01", replaced.link_row_id)
    assert withdrawn.action is OwnerLinkAction.WITHDRAW and withdrawn.target is None
    assert current_owner_link_row(database, ITEM) is None
    assert [row.action for row in owner_link_rows(database, ITEM)] == [
        OwnerLinkAction.LINK,
        OwnerLinkAction.REPLACE,
        OwnerLinkAction.WITHDRAW,
    ]


def test_the_same_request_answers_its_stored_row_and_writes_nothing(
    database: sqlite3.Connection,
) -> None:
    first, _ = _choose(database, "request-choose-0001")
    # The replay is read first: the link it made is now current, so a fresh
    # write would refuse as link_changed, and the replay must not.
    again, replayed = _choose(database, "request-choose-0001")
    assert (again, replayed, _count(database)) == (first, True, 1)
    assert owner_link_by_request(database, "request-choose-0001") == first
    assert owner_link_by_request(database, "request-unknown-01") is None


def test_the_same_request_with_another_body_is_a_conflict(
    database: sqlite3.Connection,
) -> None:
    _choose(database, "request-choose-0001")
    with pytest.raises(OwnerLinkIdempotencyConflict):
        _choose(database, "request-choose-0001", digest="f" * 64)
    assert (_count(database), database.in_transaction) == (1, False)


def test_a_stale_expected_link_rolls_back_and_writes_nothing(
    database: sqlite3.Connection,
) -> None:
    first, _ = _choose(database, "request-choose-0001")
    second, _ = _choose(
        database, "request-choose-0002", expected=first.link_row_id, target=EDITION
    )
    with pytest.raises(OwnerLinkChanged):
        _withdraw(database, "request-withdraw-01", first.link_row_id)
    with pytest.raises(OwnerLinkChanged):
        _choose(database, "request-choose-0003")
    assert (_count(database), database.in_transaction) == (2, False)
    current = current_owner_link_row(database, ITEM)
    assert current is not None and current.link_row_id == second.link_row_id


def test_a_row_outside_its_grammar_fails_every_read_closed(
    database: sqlite3.Connection,
) -> None:
    # The table admits this row; its digest is not the one its values make.
    _raw_insert(database)
    item = OwnerLinkItem("abs", "Books", "item-9")
    with pytest.raises(OwnerLinkStoreError, match="invalid"):
        owner_link_rows(database, item)
    with pytest.raises(OwnerLinkStoreError, match="invalid"):
        current_owner_link_row(database, item)
    with pytest.raises(OwnerLinkStoreError, match="invalid"):
        owner_link_by_request(database, "request-raw-0001")


# -- File links from an identifiers file import (#1478) -----------------------

IMPORT_ID = "identifier_import_" + "f" * 64
FILE_EDITION = OwnerLinkTarget(OwnerLinkScope.EDITION, None, "9780306406157", None)


def _insert_import(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO identifier_imports(import_id,connection_name,library_name,"
        "row_count,will_link,already_linked,no_item,ambiguous,invalid_identifier,"
        "wrong_medium,repeated,conflicting,unreadable,actor,created_at,request_id,"
        "request_sha256,preview_digest) "
        "VALUES(?,'abs','Books',1,1,0,0,0,0,0,0,0,0,'owner',1,?,?,?)",
        (IMPORT_ID, "import-request-0001", "a" * 64, "b" * 64),
    )


_FILE_ROW = {
    "source": "file",
    "scope": "edition",
    "provider": None,
    "record_identity": "9780306406157",
    "display_title": None,
    "display_creators": None,
    "import_id": IMPORT_ID,
}


def test_a_valid_raw_file_row_is_admitted(database: sqlite3.Connection) -> None:
    _insert_import(database)
    _raw_insert(database, **_FILE_ROW)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"provider": "google_books"}, id="file-with-provider"),
        pytest.param({"display_title": "A Title"}, id="file-with-title"),
        pytest.param({"display_creators": "[]"}, id="file-with-creators"),
        pytest.param({"import_id": None}, id="file-without-import"),
        pytest.param({"import_id": "identifier_import_x"}, id="import-id-shape"),
        pytest.param(
            {"scope": "work", "record_identity": "/works/OL1W"}, id="file-work"
        ),
        pytest.param({"action": "replace"}, id="file-replace"),
        pytest.param(
            {"import_id": "identifier_import_" + "e" * 64}, id="unknown-import"
        ),
        pytest.param(
            {"scope": "recording", "record_identity": "B000000001"},
            id="recording-on-ebook",
        ),
    ],
)
def test_file_rows_outside_their_grammar_are_refused(
    database: sqlite3.Connection, changes: dict[str, object]
) -> None:
    database.execute("PRAGMA foreign_keys=ON")
    _insert_import(database)
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(database, **{**_FILE_ROW, **changes})


def test_a_search_row_with_an_import_is_refused(database: sqlite3.Connection) -> None:
    _insert_import(database)
    with pytest.raises(sqlite3.IntegrityError):
        _raw_insert(database, import_id=IMPORT_ID)


def _record_file(
    connection: sqlite3.Connection,
    action: OwnerLinkAction,
    expected: str | None,
    target: OwnerLinkTarget | None,
    request: str,
    *,
    import_id: str | None = IMPORT_ID,
    source: str = "file",
):
    return record_owner_link(
        connection,
        item=ITEM,
        action=action,
        expected_current=expected,
        target=target,
        source=source,
        medium="ebook",
        actor="owner",
        occurred_at=1_700_000_000,
        request_id=request,
        request_sha256="a" * 64,
        import_id=import_id,
    )


def test_a_file_link_records_reads_back_and_is_replaced_by_a_search(
    database: sqlite3.Connection,
) -> None:
    def link() -> object:
        _insert_import(database)
        return _record_file(
            database, OwnerLinkAction.LINK, None, FILE_EDITION, "file-request-00001"
        )

    row = _in_transaction(database, link)
    current = current_owner_link(database, "abs", "Books", "item-1")
    assert current is not None and current.provider is None
    assert current.source == "file"
    (read,) = owner_link_rows(database, ITEM)
    assert read == row
    assert (read.source, read.import_id, read.target) == (
        "file",
        IMPORT_ID,
        FILE_EDITION,
    )
    replaced = _in_transaction(
        database,
        lambda: _record(
            database,
            OwnerLinkAction.REPLACE,
            read.link_row_id,
            EDITION,
            "request-00000009",
        ),
    )
    now = current_owner_link_row(database, ITEM)
    assert now is not None and now.link_row_id == replaced
    assert current_owner_link_items(database, "abs", "Books") == {"item-1"}
    assert current_owner_link_items(database, "abs", "Other") == frozenset()


@pytest.mark.parametrize(
    ("source", "target", "import_id"),
    [
        pytest.param("file", FILE_EDITION, None, id="file-without-import"),
        pytest.param("file", EDITION, IMPORT_ID, id="file-with-provider"),
        pytest.param("search", FILE_EDITION, None, id="search-without-provider"),
        pytest.param("search", EDITION, IMPORT_ID, id="search-with-import"),
        pytest.param("file", FILE_EDITION, "identifier_import_x", id="import-shape"),
    ],
)
def test_a_file_row_out_of_grammar_is_refused_before_insert(
    database: sqlite3.Connection,
    source: str,
    target: OwnerLinkTarget,
    import_id: str | None,
) -> None:
    def link() -> object:
        _insert_import(database)
        return _record_file(
            database,
            OwnerLinkAction.LINK,
            None,
            target,
            "file-request-00002",
            import_id=import_id,
            source=source,
        )

    with pytest.raises(OwnerLinkStoreError) as refused:
        _in_transaction(database, link)
    assert type(refused.value) is OwnerLinkStoreError
    assert _count(database) == 0
