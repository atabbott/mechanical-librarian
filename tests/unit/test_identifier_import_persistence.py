"""Identifiers file imports in the store: one transaction, replays, withdrawal (#1478)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.persistence import identifier_imports
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkItem,
    OwnerLinkRow,
    current_owner_link_row,
    owner_link_rows,
    record_owner_link,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.identifier_import import (
    IdentifierImportRefused,
    preview_digest,
)
from mechanical_librarian.domain.identifier_file import (
    classify_identifier_file,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLinkAction,
    OwnerLinkScope,
    OwnerLinkTarget,
)

CONNECTION, LIBRARY = "primary", "fiction"
FINGERPRINT = "a" * 64
ISBN13 = "9780306406157"


def _entry(
    index: int, *, audio: bool = False, path: str | None = None
) -> CatalogueEntry:
    return CatalogueEntry(
        CONNECTION,
        LIBRARY,
        "lib-1",
        f"item-{index}",
        1,
        f"Title {index}",
        (f"Author {index}",),
        not audio,
        audio,
        "singular-supported",
        f"{index + 1:064x}",
        "book.m4b" if audio else "book.epub",
        "m4b" if audio else "epub",
        "media",
        item_path=path or f"Shelf/Book {index}",
    )


ENTRIES = (_entry(1), _entry(2), _entry(3), _entry(4, audio=True))
FILE = (
    "item\tidentifier\n"
    f"item-1\t{ISBN13}\n"
    "item-2\t9780804429573\n"
    "item-3\t0306406152\n"
    "item-4\tB00TEST001\n"
    "item-9\tB00TEST001\n"
).encode()


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[SQLiteMetadataReportRepository]:
    catalogue = open_catalogue(tmp_path / "catalogue.sqlite", create=True)
    catalogue.acquire_refresh()
    catalogue.publish(FINGERPRINT, ENTRIES)
    catalogue.close()
    store = open_repository(tmp_path / "review.sqlite")
    store.bind_library_catalogue(tmp_path / "catalogue.sqlite", FINGERPRINT)
    try:
        yield store
    finally:
        store.close()


def _republish(tmp_path: Path, entries: tuple[CatalogueEntry, ...]) -> None:
    catalogue = open_catalogue(tmp_path / "catalogue.sqlite")
    catalogue.acquire_refresh()
    catalogue.publish(FINGERPRINT, entries)
    catalogue.close()


def _import(
    repository: SQLiteMetadataReportRepository,
    request_id: str = "import-request-0001",
    *,
    preview: str = "preview-one",
    sha: str = "c" * 64,
):
    read = repository.identifier_candidates(CONNECTION, LIBRARY)
    rows = classify_identifier_file(FILE, read.candidates, read.linked)
    return repository.import_identifiers(
        CONNECTION,
        LIBRARY,
        preview_digest=preview_digest(preview),
        catalogue_digest=read.digest,
        rows=rows.rows,
        counts=rows.counts,
        links=rows.will_link,
        actor="owner",
        occurred_at=1_700_000_000,
        request_id=request_id,
        request_sha256=sha,
    )


def _current(connection: sqlite3.Connection, item: OwnerLinkItem) -> OwnerLinkRow:
    row = current_owner_link_row(connection, item)
    assert row is not None
    return row


def _count(repository: SQLiteMetadataReportRepository, table: str) -> int:
    return repository._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608


def _refused(call) -> str:
    with pytest.raises(IdentifierImportRefused) as refused:
        call()
    return refused.value.code


def test_candidates_are_the_librarys_eligible_entries_with_their_digest(
    repository: SQLiteMetadataReportRepository, tmp_path: Path
) -> None:
    read = repository.identifier_candidates(CONNECTION, LIBRARY)
    assert [
        (entry.item_id, entry.item_path, entry.medium) for entry in read.candidates
    ] == [
        ("item-1", "Shelf/Book 1", "ebook"),
        ("item-2", "Shelf/Book 2", "ebook"),
        ("item-3", "Shelf/Book 3", "ebook"),
        ("item-4", "Shelf/Book 4", "audiobook"),
    ]
    assert read.linked == frozenset()
    assert repository.identifier_candidates(CONNECTION, "other").candidates == ()
    _republish(tmp_path, (*ENTRIES[:3], _entry(4, audio=True, path="Moved")))
    assert repository.identifier_candidates(CONNECTION, LIBRARY).digest != read.digest


def test_an_absent_catalogue_is_unreadable(tmp_path: Path) -> None:
    store = open_repository(tmp_path / "review.sqlite")
    try:
        assert (
            _refused(lambda: store.identifier_candidates(CONNECTION, LIBRARY))
            == "library_unreadable"
        )
        store.bind_library_catalogue(tmp_path / "missing.sqlite", FINGERPRINT)
        assert (
            _refused(lambda: store.identifier_candidates(CONNECTION, LIBRARY))
            == "library_unreadable"
        )
    finally:
        store.close()


def test_an_import_records_its_links_the_import_and_one_event(
    repository: SQLiteMetadataReportRepository,
) -> None:
    record, replayed = _import(repository)
    assert replayed is False
    assert record.linked == 4
    assert (
        _count(repository, "identifier_imports"),
        _count(repository, "identifier_import_events"),
    ) == (1, 1)
    event = repository._connection.execute(
        "SELECT kind,affected,request_id FROM identifier_import_events"
    ).fetchone()
    assert event == ("imported", 4, "import-request-0001")
    counts = repository._connection.execute(
        "SELECT row_count,will_link,no_item FROM identifier_imports"
    ).fetchone()
    assert counts == (5, 4, 1)
    row = current_owner_link_row(
        repository._connection, OwnerLinkItem(CONNECTION, LIBRARY, "item-3")
    )
    assert row is not None
    assert (row.source, row.import_id, row.target) == (
        "file",
        record.import_id,
        OwnerLinkTarget(OwnerLinkScope.EDITION, None, ISBN13, None),
    )
    audio = current_owner_link_row(
        repository._connection, OwnerLinkItem(CONNECTION, LIBRARY, "item-4")
    )
    assert audio is not None and audio.target == OwnerLinkTarget(
        OwnerLinkScope.RECORDING, None, "B00TEST001", None
    )
    status = repository.identifier_import_status(record.import_id)
    assert status == {
        "import_id": record.import_id,
        "linked": 4,
        "still_linked": 4,
        "withdrawn": False,
    }
    assert (
        repository.identifier_import_for_preview(preview_digest("preview-one"))
        == record
    )
    assert repository.identifier_candidates(CONNECTION, LIBRARY).linked == {
        "item-1",
        "item-2",
        "item-3",
        "item-4",
    }


def test_the_same_request_replays_and_another_key_finds_it_imported(
    repository: SQLiteMetadataReportRepository,
) -> None:
    record, _ = _import(repository)
    assert _import(repository) == (record, True)
    assert _refused(lambda: _import(repository, sha="d" * 64)) == "idempotency_conflict"
    assert (
        _refused(lambda: _import(repository, "import-request-0002"))
        == "preview_imported"
    )
    assert _count(repository, "identifier_imports") == 1
    assert _count(repository, "owner_links") == 4


def test_a_fault_in_any_link_leaves_nothing_behind(
    repository: SQLiteMetadataReportRepository,
) -> None:
    calls = []

    def failing(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("injected")
        return record_owner_link(*args, **kwargs)

    with patch.object(identifier_imports, "record_owner_link", failing):
        with pytest.raises(RuntimeError):
            _import(repository)
    for table in ("identifier_imports", "identifier_import_events", "owner_links"):
        assert _count(repository, table) == 0
    assert repository.identifier_import_replay("import-request-0001") is None


def test_a_changed_catalogue_or_a_new_link_makes_the_preview_stale(
    repository: SQLiteMetadataReportRepository, tmp_path: Path
) -> None:
    read = repository.identifier_candidates(CONNECTION, LIBRARY)
    rows = classify_identifier_file(FILE, read.candidates, read.linked)

    def confirm(request_id: str):
        return repository.import_identifiers(
            CONNECTION,
            LIBRARY,
            preview_digest=preview_digest("preview-" + request_id),
            catalogue_digest=read.digest,
            rows=rows.rows,
            counts=rows.counts,
            links=rows.will_link,
            actor="owner",
            occurred_at=1,
            request_id=request_id,
            request_sha256="c" * 64,
        )

    connection = repository._connection
    connection.execute("BEGIN IMMEDIATE")
    record_owner_link(
        connection,
        item=OwnerLinkItem(CONNECTION, LIBRARY, "item-2"),
        action=OwnerLinkAction.LINK,
        expected_current=None,
        target=OwnerLinkTarget(
            OwnerLinkScope.EDITION, "open_library", "9780804429573", "Title"
        ),
        source="search",
        medium="ebook",
        actor="owner",
        occurred_at=1,
        request_id="search-request-0001",
        request_sha256="a" * 64,
    )
    connection.execute("COMMIT")
    assert _refused(lambda: confirm("import-request-0003")) == "preview_stale"
    _republish(tmp_path, ENTRIES[:2])
    assert _refused(lambda: confirm("import-request-0004")) == "preview_stale"
    assert _count(repository, "identifier_imports") == 0


def test_an_import_with_no_will_link_rows_is_refused(
    repository: SQLiteMetadataReportRepository,
) -> None:
    read = repository.identifier_candidates(CONNECTION, LIBRARY)
    assert (
        _refused(
            lambda: repository.import_identifiers(
                CONNECTION,
                LIBRARY,
                preview_digest=preview_digest("empty"),
                catalogue_digest=read.digest,
                rows=1,
                counts={"no_item": 1},
                links=(),
                actor="owner",
                occurred_at=1,
                request_id="import-request-0005",
                request_sha256="c" * 64,
            )
        )
        == "nothing_to_link"
    )


def test_withdraw_touches_only_links_that_are_still_current(
    repository: SQLiteMetadataReportRepository,
) -> None:
    record, _ = _import(repository)
    connection = repository._connection
    replaced = OwnerLinkItem(CONNECTION, LIBRARY, "item-1")
    withdrawn = OwnerLinkItem(CONNECTION, LIBRARY, "item-2")
    connection.execute("BEGIN IMMEDIATE")
    record_owner_link(
        connection,
        item=replaced,
        action=OwnerLinkAction.REPLACE,
        expected_current=_current(connection, replaced).link_row_id,
        target=OwnerLinkTarget(OwnerLinkScope.EDITION, "open_library", ISBN13, "T"),
        source="search",
        medium="ebook",
        actor="owner",
        occurred_at=2,
        request_id="search-request-0002",
        request_sha256="a" * 64,
    )
    record_owner_link(
        connection,
        item=withdrawn,
        action=OwnerLinkAction.WITHDRAW,
        expected_current=_current(connection, withdrawn).link_row_id,
        target=None,
        source="search",
        medium="ebook",
        actor="owner",
        occurred_at=2,
        request_id="search-request-0003",
        request_sha256="a" * 64,
    )
    connection.execute("COMMIT")

    def withdraw(request_id: str = "withdraw-request-01", sha: str = "e" * 64):
        return repository.withdraw_identifier_import(
            record.import_id,
            actor="owner",
            occurred_at=3,
            request_id=request_id,
            request_sha256=sha,
        )

    assert repository.identifier_import_status(record.import_id)["still_linked"] == 2
    answer, affected, replayed = withdraw()
    assert (answer.import_id, affected, replayed) == (record.import_id, 2, False)
    assert withdraw() == (record, 2, True)
    assert _refused(lambda: withdraw(sha="f" * 64)) == "idempotency_conflict"
    assert _refused(lambda: withdraw("withdraw-request-02")) == "import_withdrawn"
    assert _refused(lambda: withdraw("import-request-0001")) == "idempotency_conflict"
    assert _current(connection, replaced).source == "search"
    assert current_owner_link_row(connection, withdrawn) is None
    assert (
        current_owner_link_row(connection, OwnerLinkItem(CONNECTION, LIBRARY, "item-3"))
        is None
    )
    last = owner_link_rows(connection, OwnerLinkItem(CONNECTION, LIBRARY, "item-4"))[-1]
    assert (last.action, last.source, last.import_id) == (
        OwnerLinkAction.WITHDRAW,
        "file",
        record.import_id,
    )
    assert repository.identifier_import_status(record.import_id) == {
        "import_id": record.import_id,
        "linked": 4,
        "still_linked": 0,
        "withdrawn": True,
    }
    assert connection.execute(
        "SELECT kind,affected FROM identifier_import_events ORDER BY kind"
    ).fetchall() == [("imported", 4), ("withdrawn", 2)]


def test_nothing_current_to_withdraw_writes_no_event(
    repository: SQLiteMetadataReportRepository,
) -> None:
    record, _ = _import(repository)
    connection = repository._connection
    connection.execute("BEGIN IMMEDIATE")
    for index in (1, 2, 3, 4):
        item = OwnerLinkItem(CONNECTION, LIBRARY, f"item-{index}")
        current = _current(connection, item)
        record_owner_link(
            connection,
            item=item,
            action=OwnerLinkAction.WITHDRAW,
            expected_current=current.link_row_id,
            target=None,
            source="search",
            medium=current.medium,
            actor="owner",
            occurred_at=2,
            request_id=f"search-withdraw-{index:04d}",
            request_sha256="a" * 64,
        )
    connection.execute("COMMIT")
    assert (
        _refused(
            lambda: repository.withdraw_identifier_import(
                record.import_id,
                actor="owner",
                occurred_at=3,
                request_id="withdraw-request-03",
                request_sha256="e" * 64,
            )
        )
        == "nothing_to_withdraw"
    )
    assert (
        _refused(
            lambda: repository.withdraw_identifier_import(
                "identifier_import_" + "0" * 64,
                actor="owner",
                occurred_at=3,
                request_id="withdraw-request-04",
                request_sha256="e" * 64,
            )
        )
        == "import_not_found"
    )
    assert _count(repository, "identifier_import_events") == 1
    assert repository.identifier_import_status("identifier_import_" + "0" * 64) is None


def test_import_tables_are_append_only_and_their_counts_add_up(
    repository: SQLiteMetadataReportRepository,
) -> None:
    record, _ = _import(repository)
    connection = repository._connection
    for statement in (
        "UPDATE identifier_imports SET actor='other'",
        "DELETE FROM identifier_imports",
        "UPDATE identifier_import_events SET affected=1",
        "DELETE FROM identifier_import_events",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(statement)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO identifier_imports(import_id,connection_name,library_name,"
            "row_count,will_link,already_linked,no_item,ambiguous,invalid_identifier,"
            "wrong_medium,repeated,conflicting,unreadable,actor,created_at,request_id,"
            "request_sha256,preview_digest) VALUES(?,'c','l',3,1,0,0,0,0,0,0,0,0,"
            "'owner',1,'import-request-0009',?,?)",
            ("identifier_import_" + "9" * 64, "a" * 64, "b" * 64),
        )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO identifier_import_events(event_id,import_id,kind,occurred_at,"
            "actor,affected,request_id,request_sha256) VALUES(?,?,'imported',1,"
            "'owner',1,'event-request-0009',?)",
            ("identifier_import_event_" + "9" * 64, record.import_id, "a" * 64),
        )
