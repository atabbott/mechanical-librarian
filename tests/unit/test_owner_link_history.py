"""History shows the work's book link events (#1477, S31).

Each link row of the case's work is one event: linked, link changed or link
withdrawn, with the record's title, source and scope. A withdrawal names the
link it ended. A link row outside its grammar fails the read closed rather
than showing a value the store never recorded.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkItem,
    record_owner_link,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.owner_link import (
    OwnerLinkAction,
    OwnerLinkScope,
    OwnerLinkTarget,
)
from mechanical_librarian.interfaces.web.app import _HistoryOwnerLinkItem
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.owner_values import OwnerStore

WORK = OwnerLinkTarget(OwnerLinkScope.WORK, "open_library", "/works/OL1W", "A Title")
EDITION = OwnerLinkTarget(
    OwnerLinkScope.EDITION, "google_books", "9780306406157", "An Edition", ("Ann",)
)


def _item(store: OwnerStore) -> OwnerLinkItem:
    target = store.case.target
    return OwnerLinkItem(
        str(target["connection"]), str(target["library"]), str(target["item"])
    )


def _write(
    connection: sqlite3.Connection,
    item: OwnerLinkItem,
    action: OwnerLinkAction,
    expected: str | None,
    target: OwnerLinkTarget | None,
    at: int,
) -> str:
    connection.execute("BEGIN IMMEDIATE")
    row = record_owner_link(
        connection,
        item=item,
        action=action,
        expected_current=expected,
        target=target,
        source="search",
        medium="ebook",
        actor="owner",
        occurred_at=at,
        request_id=f"request-history-{at:010d}",
        request_sha256="a" * 64,
    )
    connection.execute("COMMIT")
    return row.link_row_id


def _link_events(store: OwnerStore, limit: int = 50) -> list[dict[str, object]]:
    return [
        event
        for event in store.repository.get_review_case_history(
            store.case.review_case_id, limit
        )
        if event.get("schema_version") == "owner-link-history-event-v1"
    ]


def test_link_change_and_withdrawal_are_history_events_newest_first(
    tmp_path: Path,
) -> None:
    store = OwnerStore(tmp_path, isbn_title_report())
    connection = store.repository._connection
    item = _item(store)
    first = _write(connection, item, OwnerLinkAction.LINK, None, WORK, 2_000)
    second = _write(connection, item, OwnerLinkAction.REPLACE, first, EDITION, 3_000)
    third = _write(connection, item, OwnerLinkAction.WITHDRAW, second, None, 4_000)
    events = _link_events(store)
    case_id = store.case.review_case_id
    assert events == [
        {
            "kind": "link_withdrawn",
            "link_row_id": third,
            "occurred_at": 4_000,
            "provider": "google_books",
            "review_case_id": case_id,
            "schema_version": "owner-link-history-event-v1",
            "scope": "edition",
            "title": "An Edition",
        },
        {
            "kind": "link_changed",
            "link_row_id": second,
            "occurred_at": 3_000,
            "provider": "google_books",
            "review_case_id": case_id,
            "schema_version": "owner-link-history-event-v1",
            "scope": "edition",
            "title": "An Edition",
        },
        {
            "kind": "linked",
            "link_row_id": first,
            "occurred_at": 2_000,
            "provider": "open_library",
            "review_case_id": case_id,
            "schema_version": "owner-link-history-event-v1",
            "scope": "work",
            "title": "A Title",
        },
    ]
    # Each event is exactly the closed wire item.
    for event in events:
        assert _HistoryOwnerLinkItem.model_validate(event).model_dump() == event
    # The history's own limit cuts the oldest.
    assert [event["kind"] for event in _link_events(store, 2)] == [
        "link_withdrawn",
        "link_changed",
    ]


def test_another_works_link_is_not_this_cases_history(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, isbn_title_report())
    item = _item(store)
    other = OwnerLinkItem(item.connection_name, item.library_name, "another-item")
    _write(store.repository._connection, other, OwnerLinkAction.LINK, None, WORK, 2_000)
    assert _link_events(store) == []


def test_a_malformed_link_row_fails_the_history_closed(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, isbn_title_report())
    item = _item(store)
    connection = store.repository._connection
    # The table admits this row, but its digest is not the one its values
    # make: it was not written by the store.
    connection.execute(
        "INSERT INTO owner_links(link_row_id,connection_name,library_name,item_id,"
        "sequence,action,source,medium,scope,provider,record_identity,display_title,"
        "display_creators,actor,occurred_at,request_id,request_sha256,link_digest) "
        "VALUES(?,?,?,?,1,'link','search','ebook','work','open_library','/works/OL1W',"
        "'A Title','[]','owner',1,'request-raw-0001',?,?)",
        (
            "owner_link_" + "1" * 64,
            item.connection_name,
            item.library_name,
            item.item_id,
            "a" * 64,
            "b" * 64,
        ),
    )
    with pytest.raises(MetadataReportError):
        store.repository.get_review_case_history(store.case.review_case_id, 50)
