"""An identifiers file from its preview to the works prepared through it (#1478).

The issue's 20-row file is previewed and imported over HTTP against the real
store and catalogue, across both media: 15 rows become file links under one
import, one scan is asked for, and Activity has one row for the import. One
imported ebook is then prepared through the real producer, which reads its
record by the link's ISBN. A link changed by search since the import and one
withdrawn on its own are left alone by Withdraw this import, which withdraws
the other 13; the next preparation reads no link. Only the sources'
transport, the clocks and the preparation supervisor are fakes.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import (
    NonClosingRepository,
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkItem,
    current_owner_link_row,
    record_owner_link,
    withdraw_owner_link,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    ReviewWebConfiguration,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.metadata_report import ProviderBinding
from mechanical_librarian.application.owner_search import OwnerSearchService
from mechanical_librarian.application.review_pipeline import PreparationTrigger
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.owner_link import OwnerLinkAction
from tests.integration.test_owner_link_preparation import (
    CONNECTION,
    ISBN,
    ITEM,
    LINKED_AUTHOR,
    LINKED_TITLE,
    WORK_LINK,
    _google,
    _open_library,
    _prepare_ebook,
    _volume,
)
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
from tests.unit.test_find_this_book_routes import (
    _Configuration as _RouteConfiguration,
)
from tests.unit.test_find_this_book_routes import _Preparation as _RoutePreparation

ORIGIN = "https://review.test"
NOW = 1_000
LIBRARY = "ebooks"
FINGERPRINT = "f" * 64
PREVIEWS = f"/api/v1/libraries/{CONNECTION}/{LIBRARY}/identifier-previews"


def _isbn13(stem: str) -> str:
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(stem))
    return stem + str((10 - total % 10) % 10)


def _entry(index: int, path: str, *, audio: bool = False) -> CatalogueEntry:
    return CatalogueEntry(
        CONNECTION,
        LIBRARY,
        "library-1",
        f"item-{index}",
        1,
        f"Neutral Title {index}",
        (f"Author {index}",),
        not audio,
        audio,
        "singular-supported",
        f"{index + 1:064x}",
        "book.m4b" if audio else "book.epub",
        "m4b" if audio else "epub",
        "books",
        item_path=path,
    )


# Ten ebooks and five audiobooks are linked. Two works share a folder name,
# so the shorter path to them matches both; two more ebooks are named by rows
# with an identifier they cannot take.
EBOOKS = tuple(_entry(i, f"Shelf/Ebook {i}") for i in range(1, 11))
AUDIOBOOKS = tuple(_entry(i, f"Audio/Recording {i}", audio=True) for i in range(11, 16))
OTHERS = (
    _entry(16, "Twin"),
    _entry(17, "Shelf/Twin"),
    _entry(18, "Spare/Ebook 18"),
    _entry(19, "Spare/Ebook 19"),
)
ISBNS = {
    f"item-{i}": ISBN if i == 1 else _isbn13(f"978030640{i:03d}") for i in range(1, 11)
}
ASINS = {f"item-{i}": f"B00TEST{i:03d}" for i in range(11, 16)}
# Each refused row, by its line in the file, and the reason it is refused.
REFUSED = {
    6: ("Shelf/Nothing Here\t9780306406157", "no_item"),
    9: ("Shelf/Twin\t9780306406157", "ambiguous"),
    13: ("Spare/Ebook 18\t9780306406158", "invalid_isbn"),
    17: ("Spare/Ebook 19\tB00TEST999", "asin_for_ebook"),
    20: ("Unsorted/Nothing Here\tB00TEST998", "no_item"),
}


def _demo_file() -> bytes:
    """The issue's 20 rows: 15 link, two match no work, one matches two works,
    one has an ISBN with a bad check digit and one gives an ebook an ASIN."""
    linked = [f"{e.item_path}\t{ISBNS[e.item_id]}" for e in EBOOKS] + [
        f"{e.item_path}\t{ASINS[e.item_id].lower()}" for e in AUDIOBOOKS
    ]
    rows = ["path\tidentifier"]
    for line in range(2, 22):
        rows.append(REFUSED[line][0] if line in REFUSED else linked.pop(0))
    assert not linked
    return ("\r\n".join(rows) + "\r\n").encode()


class _Configuration(_RouteConfiguration):
    def select(self, connection: str, library: str) -> tuple[Any, ...]:
        selected = LibraryConfiguration(
            LIBRARY, CONNECTION, "library-1", "book", "books", ("ol",)
        )
        return None, selected, None, self.providers


class _Preparation(_RoutePreparation):
    libraries = ((CONNECTION, LIBRARY, "library-1"),)

    def progress(self, connection: str, library: str):
        return replace(super().progress(connection, library), library=library)


@dataclass
class _World:
    path: Path
    client: TestClient
    headers: dict[str, str]
    repository: SQLiteMetadataReportRepository
    preparation: _Preparation

    def post(self, path: str, key: str | None = None, **kwargs: Any) -> Any:
        headers = dict(self.headers)
        if key is not None:
            headers["Idempotency-Key"] = key
        headers.update(kwargs.pop("headers", {}))
        return self.client.post(path, headers=headers, **kwargs)

    def get(self, path: str) -> Any:
        answer = self.client.get(path)
        assert answer.status_code == 200, answer.text
        return answer.json()

    def links(self) -> dict[str, tuple[str, str, str | None]]:
        """Each item's current row: its action, source and import."""
        rows = self.repository._connection.execute(
            "SELECT item_id,action,source,import_id FROM owner_links ORDER BY rowid"
        ).fetchall()
        return {
            item: (action, source, imported) for item, action, source, imported in rows
        }

    def activity(self) -> list[dict[str, Any]]:
        events = self.get("/api/v1/activity?event_type=all&range_days=90")["events"]
        return [e for e in events if e["field_label"] in {"Links", "Book link"}]


@pytest.fixture
def world(tmp_path: Path) -> Iterator[_World]:
    catalogue = open_catalogue(tmp_path / "catalogue.sqlite", create=True)
    catalogue.acquire_refresh()
    catalogue.publish(FINGERPRINT, EBOOKS + AUDIOBOOKS + OTHERS)
    catalogue.close()
    path = tmp_path / "state.sqlite3"
    repository = open_repository(path, cross_thread=True)
    repository.bind_library_catalogue(tmp_path / "catalogue.sqlite", FINGERPRINT)
    search = OwnerSearchService(
        configuration=cast(Any, _Configuration()),
        works=repository,
        adapter=lambda provider, *, marketplaces: None,
        pacers={},
        throttling=lambda: {"entries": []},
        fetch_preview=lambda cover, deadline: b"",
        clock=lambda: 1_800_000_000.0,
        monotonic=lambda: 100.0,
    )
    preparation = _Preparation()
    password = tmp_path / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin=ORIGIN,
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=lambda: NOW,
        review_preparation=cast(Any, preparation),
        owner_search=search,
        activity_projection=ContinuousGuardedApplication(
            repository,
            {},
            {},
            PersistedFieldEffectAuthority(repository),
            ((CONNECTION, LIBRARY),),
            lambda: NOW,
            lambda *_: None,
            activity_only=True,
        ),
    )
    with TestClient(app, base_url=ORIGIN) as client:
        login = client.post(
            "/api/v1/session/login",
            json={"password": "correct horse"},
            headers={"Origin": ORIGIN},
        )
        assert login.status_code == 200, login.text
        yield _World(
            path,
            client,
            {
                "Origin": ORIGIN,
                "X-CSRF-Token": login.json()["csrf_token"],
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
            },
            repository,
            preparation,
        )
    repository.close()


def _prepare_item_1(world: _World) -> MetadataReport:
    """Prepare item-1 through the real producer; both sources answer its ISBN
    with the linked book."""
    return _prepare_ebook(
        world.path,
        (
            ProviderBinding(
                "open_library",
                True,
                lambda: _open_library([], "eBook"),
                authority="open_library",
            ),
            ProviderBinding(
                "google_books",
                True,
                lambda: _google(
                    [],
                    lambda query: (
                        _volume(LINKED_TITLE, LINKED_AUTHOR)
                        if query == f"isbn:{ISBN}"
                        else {"totalItems": 0}
                    ),
                ),
                authority="google_books",
            ),
        ),
        # The running app holds the store; the producer shares its connection.
        repository_factory=lambda: cast(Any, NonClosingRepository(world.repository)),
    )


def _replace_by_search(path: Path, item: str) -> None:
    """The owner changes one imported link with Find this book."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = current_owner_link_row(
            connection, OwnerLinkItem(CONNECTION, LIBRARY, item)
        )
        assert current is not None
        record_owner_link(
            connection,
            item=OwnerLinkItem(CONNECTION, LIBRARY, item),
            action=OwnerLinkAction.REPLACE,
            expected_current=current.link_row_id,
            target=WORK_LINK,
            source="search",
            medium="ebook",
            actor="owner",
            occurred_at=NOW,
            request_id="replace-request-000001",
            request_sha256="c" * 64,
        )
        connection.execute("COMMIT")
    finally:
        connection.close()


def _withdraw_alone(path: Path, item: str) -> None:
    """The owner withdraws one imported link on its own."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        current = current_owner_link_row(
            connection, OwnerLinkItem(CONNECTION, LIBRARY, item)
        )
        assert current is not None
        withdraw_owner_link(
            connection,
            item=OwnerLinkItem(CONNECTION, LIBRARY, item),
            expected_current=current.link_row_id,
            medium="ebook",
            actor="owner",
            occurred_at=NOW,
            request_id="withdraw-request-00001",
            request_sha256="d" * 64,
        )
    finally:
        connection.close()


def test_an_identifiers_file_links_its_works_and_its_import_is_withdrawn(
    world: _World,
) -> None:
    # Preview: the 20 rows are decided and nothing is recorded.
    answer = world.post(
        PREVIEWS,
        content=_demo_file(),
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    assert answer.status_code == 200, answer.text
    preview = answer.json()
    assert preview["rows"] == 20
    assert preview["counts"] == {
        "will_link": 15,
        "already_linked": 0,
        "no_item": 2,
        "ambiguous": 1,
        "invalid_identifier": 1,
        "wrong_medium": 1,
        "repeated": 0,
        "conflicting": 0,
        "unreadable": 0,
    }
    assert [(row["row"], row["reason"]) for row in preview["refused"]] == [
        (line, reason) for line, (_, reason) in REFUSED.items()
    ]
    assert len(preview["will_link"]) == 15
    assert world.links() == {}
    assert world.preparation.requests == []

    # Link: 15 file links under one import, one scan, one Activity row.
    imported = world.post(
        f"/api/v1/identifier-previews/{preview['preview_id']}/import",
        key="lifecycle-import-0001",
    )
    assert imported.status_code == 200, imported.text
    import_id = imported.json()["import_id"]
    assert imported.json()["linked"] == 15
    links = world.links()
    assert set(links) == {e.item_id for e in EBOOKS + AUDIOBOOKS}
    assert set(links.values()) == {("link", "file", import_id)}
    assert world.preparation.requests == [
        (CONNECTION, LIBRARY, PreparationTrigger.BOOK_LINKED)
    ]
    (row,) = world.activity()
    assert row["identifier_import_id"] == import_id
    assert row["state"] == "imported"
    status = world.get(f"/api/v1/identifier-imports/{import_id}")
    assert (status["linked"], status["still_linked"], status["withdrawn"]) == (
        15,
        15,
        False,
    )
    # The next preparation of an imported ebook reads its record by the ISBN.
    report = _prepare_item_1(world)
    assert report.owner_link is not None
    assert report.owner_link.outcome == "admitted"

    # Find this book shows the prepared work's link as from the file.
    view = world.get(
        f"/api/v1/library/link?connection={CONNECTION}&library={LIBRARY}&item={ITEM}"
    )["link"]
    assert (view["source"], view["provider"], view["title"], view["identifier"]) == (
        "file",
        None,
        None,
        ISBN,
    )

    # One link is changed by search and one withdrawn on its own since.
    _replace_by_search(world.path, "item-2")
    _withdraw_alone(world.path, "item-3")
    assert world.get(f"/api/v1/identifier-imports/{import_id}")["still_linked"] == 13

    # Withdraw this import ends the 13 still current and leaves the other two.
    answer = world.post(
        f"/api/v1/identifier-imports/{import_id}/withdraw", key="lifecycle-withdraw-01"
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["withdrawn"] == 13
    links = world.links()
    assert links["item-2"] == ("replace", "search", None)
    assert links["item-3"][0] == "withdraw"
    rest = {item: links[item] for item in links if item not in {"item-2", "item-3"}}
    assert len(rest) == 13
    assert {action for action, _, _ in rest.values()} == {"withdraw"}
    assert (
        world.preparation.requests
        == [(CONNECTION, LIBRARY, PreparationTrigger.BOOK_LINKED)] * 2
    )
    # Activity: the import's row, its withdrawal's row and each owner change.
    # Every write here shares one clock time, so their order is not asserted.
    assert sorted((e["state"], e["change"]) for e in world.activity()) == [
        ("imported", "Linked 15 works"),
        ("link_changed", f"Linked to {LINKED_TITLE} on Hardcover"),
        ("link_withdrawn", f"Withdrew the link to ISBN {ISBNS['item-3']}"),
        ("withdraw", "Withdrew 13 links"),
    ]
    status = world.get(f"/api/v1/identifier-imports/{import_id}")
    assert (status["still_linked"], status["withdrawn"]) == (0, True)
    again = world.post(
        f"/api/v1/identifier-imports/{import_id}/withdraw", key="lifecycle-withdraw-02"
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "import_withdrawn"

    # The next preparation reads no link for the work the import linked.
    assert _prepare_item_1(world).owner_link is None
