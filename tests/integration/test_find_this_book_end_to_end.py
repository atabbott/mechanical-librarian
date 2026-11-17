"""Find this book from the owner's search to the work prepared through its link (#1477).

One work that Hardcover matched on its local title is searched again by the
owner, over HTTP, against Hardcover's real adapter answering synthetic
GraphQL. The chosen card becomes the work's link; the store asks for the
scan, and the next preparation reads the linked record by its key. Library,
History and Activity each show the link at every step, and a withdrawal
ends it. Only the source's transport, the clocks and the preparation
supervisor are fakes.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    preparation_work_id,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderRequestPacer,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    LibraryConfiguration,
    MediaMountConfiguration,
    ProviderConnectionConfiguration,
    ReportLimits,
    ReviewWebConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.metadata_report import (
    GenerateMetadataReport,
    InspectionResult,
    ProviderBinding,
    TargetFile,
    TargetSnapshot,
)
from mechanical_librarian.application.owner_search import OwnerSearchService
from mechanical_librarian.application.review_pipeline import PreparationTrigger
from mechanical_librarian.application.review_web import (
    OWNER_LINK_HISTORY_EVENT_SCHEMA,
    BrowseReviewLibrary,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ProviderQueryKind,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.hardcover_evidence import books_envelope
from tests.support.metadata_report_doubles import FakeInspector, FakeTarget
from tests.support.metadata_report_schemas import default_target_fields
from tests.support.review_web_doubles import FakePreparation
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)

ORIGIN = "https://review.test"
NOW = 1_000
CONNECTION, LIBRARY, ITEM = "primary", "ebooks", "item-1"
LOCAL_TITLE, LOCAL_AUTHOR = "Local Title", "Local Author"
LINKED_TITLE, LINKED_AUTHOR = "Linked Title", "Linked Author"
ISBN = "9780306406157"
PROVIDER = ProviderConnectionConfiguration("hardcover", True, False, None)
SELECTED = LibraryConfiguration(
    LIBRARY, CONNECTION, "library-id", "ebook", "books", ("hardcover",)
)


def _book(book_id: int, title: str, author: str) -> dict[str, object]:
    return {
        "id": book_id,
        "title": title,
        "release_date": "2001-02-03",
        "description": f"A synthetic description of {title}.",
        "contributions": [
            {"id": book_id, "contribution": None, "author": {"id": 9, "name": author}}
        ],
    }


def _hardcover(requests: list[str]) -> HardcoverMetadataProvider:
    """Hardcover over synthetic GraphQL: the local book by title, the linked one
    by its title and by its key."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body["operationName"])
        wanted = (
            [_book(4321, LINKED_TITLE, LINKED_AUTHOR)]
            if body["operationName"] == "BookById"
            or LINKED_TITLE in request.content.decode()
            else [_book(1111, LOCAL_TITLE, LOCAL_AUTHOR)]
            if LOCAL_TITLE in request.content.decode()
            else []
        )
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=books_envelope(wanted),
        )

    return HardcoverMetadataProvider(
        "hardcover",
        httpx.Client(transport=httpx.MockTransport(handle)),
        262_144,
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )


class _Borrowed:
    """The served store lent to one preparation, which closes what it opens."""

    def __init__(self, repository: SQLiteMetadataReportRepository) -> None:
        self._repository = repository

    def __getattr__(self, name: str) -> Any:
        return getattr(self._repository, name)

    def close(self) -> None:
        return None


def _prepare(
    path: Path,
    requests: list[str],
    served: SQLiteMetadataReportRepository | None = None,
) -> MetadataReport:
    """One preparation of the work through the real producer, then its case.

    While the app serves the store, the scan prepares through that same store,
    as the installed supervisor does.
    """

    def store() -> Any:
        return open_repository(path) if served is None else _Borrowed(served)

    fields = tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, (LOCAL_AUTHOR,))
        if value.destination is TargetDestination.AUTHORS
        else value
        for value in default_target_fields(LOCAL_TITLE)
    )
    selected = TargetFile("a" * 64, "book.epub", "epub")
    snapshot = TargetSnapshot("ebook", LOCAL_TITLE, (selected,), fields=fields)
    report = MetadataReport.from_json(
        GenerateMetadataReport(
            target=FakeTarget([snapshot, snapshot]),
            inspector=FakeInspector(InspectionResult("c" * 64, ())),
            repository_factory=store,
            providers=(
                ProviderBinding(
                    "hardcover",
                    True,
                    lambda: _hardcover(requests),
                    authority="hardcover",
                ),
            ),
            connection=TargetConnectionConfiguration(
                CONNECTION, "https://example.invalid", "/private/target"
            ),
            library=SELECTED,
            mount=MediaMountConfiguration("books", "/media", "/library", True),
            limits=ReportLimits(),
        ).run(ITEM, 0)
    )
    repository = store()
    try:
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        repository.admit_review_case(proposal)
    finally:
        repository.close()
    return report


class _Configuration:
    limits = SimpleNamespace(timeout_seconds=30)
    providers = (PROVIDER,)

    def select(self, connection: str, library: str) -> tuple[Any, ...]:
        return None, SELECTED, None, self.providers


class _Preparation(FakePreparation):
    libraries = ((CONNECTION, LIBRARY, "library-id"),)

    def begin_drain(self) -> None:
        return None

    def stop(self, deadline: float | None = None) -> None:
        return None

    def progress(self, connection: str, library: str):
        progress = super().progress("primary", "fiction")
        return replace(progress, connection=connection, library=library)


@dataclass
class _World:
    path: Path
    client: TestClient
    headers: dict[str, str]
    repository: SQLiteMetadataReportRepository
    preparation: _Preparation
    requests: list[str] = field(default_factory=list)
    clock: list[float] = field(default_factory=lambda: [1_800_000_000.0])
    app_clock: list[int] = field(default_factory=lambda: [NOW])

    def get(self, path: str) -> Any:
        answer = self.client.get(path)
        assert answer.status_code == 200, answer.text
        return answer.json()

    def post(self, path: str, body: object, key: str | None = None) -> Any:
        headers = dict(self.headers)
        if key is not None:
            headers["Idempotency-Key"] = key
        answer = self.client.post(path, json=body, headers=headers)
        assert answer.status_code == 200, answer.text
        return answer.json()

    def row(self) -> dict[str, Any]:
        page = self.get("/api/v1/library?lens=all&limit=50")
        (row,) = [item for item in page["items"] if item["item"] == ITEM]
        return cast("dict[str, Any]", row)

    def case_id(self) -> str:
        return cast(
            str,
            self.repository._connection.execute(
                "SELECT review_case_id FROM current_review_packages WHERE item_id=?",
                (ITEM,),
            ).fetchone()[0],
        )

    def activity(self) -> list[dict[str, Any]]:
        return [
            event
            for event in self.get("/api/v1/activity?event_type=all&range_days=90")[
                "events"
            ]
            if event.get("field_label") == "Book link"
        ]


@pytest.fixture
def world(tmp_path: Path) -> Iterator[_World]:
    path = tmp_path / "state.sqlite3"
    open_repository(path).close()
    requests: list[str] = []
    _prepare(path, requests)
    repository = open_repository(path, cross_thread=True)
    clock = [1_800_000_000.0]
    pacer = ProviderRequestPacer(
        1.0, clock=lambda: clock[0], sleep=lambda seconds: None
    )
    service = OwnerSearchService(
        configuration=cast(Any, _Configuration()),
        works=repository,
        adapter=lambda provider, *, marketplaces: _hardcover(requests),
        pacers={"hardcover": pacer},
        throttling=lambda: {"entries": []},
        fetch_preview=lambda cover, deadline: b"",
        clock=lambda: clock[0],
        monotonic=lambda: clock[0],
    )
    preparation = _Preparation()
    # The app's clock; a step moves it so each recorded event has its own time.
    app_clock = [NOW]
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
        clock=lambda: app_clock[0],
        review_preparation=cast(Any, preparation),
        owner_search=service,
        activity_projection=ContinuousGuardedApplication(
            repository,
            {},
            {},
            PersistedFieldEffectAuthority(repository),
            ((CONNECTION, LIBRARY),),
            lambda: app_clock[0],
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
            requests,
            clock,
            app_clock,
        )
    repository.close()


def test_the_owner_links_the_work_and_the_next_preparation_reads_the_link(
    world: _World,
) -> None:
    view = f"/api/v1/library/link?connection={CONNECTION}&library={LIBRARY}&item={ITEM}"
    first_case = world.case_id()
    assert world.row()["link"] is None
    offer = world.get(view)
    assert (offer["offered"], offer["link"]) == (True, None)
    assert offer["prefill"]["title"] == LOCAL_TITLE

    # The owner searches Hardcover for the book this really is.
    world.clock[0] += 5
    search = world.post(
        "/api/v1/library/find/searches",
        {
            "connection": CONNECTION,
            "library": LIBRARY,
            "item": ITEM,
            "provider": "hardcover",
            "lane": "title_authors",
            "title": LINKED_TITLE,
            "authors": [LINKED_AUTHOR],
        },
    )
    (card,) = search["cards"]
    assert (card["title"], card["choosable"]) == (LINKED_TITLE, True)
    assert "work" in card["scopes"]

    chosen = world.post(
        f"/api/v1/library/find/searches/{search['search_id']}/choice",
        {"position": 0, "scope": "work"},
        key="end-to-end-choice-0001",
    )
    assert (chosen["action"], chosen["scan_requested"]) == ("link", True)
    assert world.preparation.requests == [
        (CONNECTION, LIBRARY, PreparationTrigger.BOOK_LINKED)
    ]
    # Until the scan prepares it, the link is waiting on its preparation.
    assert world.row()["link"] == {
        "state": "preparing",
        "source": "search",
        "provider": "hardcover",
    }
    link = world.get(view)["link"]
    assert (link["title"], link["provider"], link["scope"], link["state"]) == (
        LINKED_TITLE,
        "hardcover",
        "work",
        "preparing",
    )
    history = world.get(f"/api/v1/review-cases/{first_case}/history")["items"]
    assert [
        (item["kind"], item["title"], item["provider"])
        for item in history
        if item.get("schema_version") == OWNER_LINK_HISTORY_EVENT_SCHEMA
    ] == [("linked", LINKED_TITLE, "hardcover")]
    (linked,) = world.activity()
    assert (linked["change"], linked["work_label"]) == (
        f"Linked to {LINKED_TITLE} on Hardcover",
        LOCAL_TITLE,
    )

    # The scan prepares the work through its link: Hardcover is read by key.
    del world.requests[:]
    report = _prepare(world.path, world.requests, world.repository)
    assert world.requests == ["BookById"]
    assert report.owner_link is not None and report.owner_link.outcome == "admitted"
    (lane,) = next(
        value for value in report.providers if value.provider == "hardcover"
    ).lanes
    assert lane.kind is ProviderQueryKind.LINK
    # The served Library row of a linked report also carries its `link` lane
    # outcome, whose wire vocabulary is the D-wire change; the row's link is
    # read here through the same read model the route serves.
    page = BrowseReviewLibrary(world.repository).library(
        lens="all", search=None, limit=50, cursor=None
    )
    (row,) = [item for item in page["items"] if item["item"] == ITEM]
    assert row["link"] == {
        "state": "linked",
        "source": "search",
        "provider": "hardcover",
    }
    assert world.get(view)["link"]["state"] == "linked"

    # A later preparation that could not finish leaves the link read: the row
    # says Could not finish, and the link keeps the report's outcome (#1941).
    failed = sqlite3.connect(world.path, isolation_level=None)
    failed.execute(
        "INSERT OR REPLACE INTO review_preparation_work(work_id,scan_id,ordinal,"
        "connection_name,"
        "library_name,library_id,item_id,file_identity,source_revision,"
        "source_fingerprint,producer_fingerprint,catalogue_generation,"
        "catalogue_fingerprint,stage,state,failure_code,updated_at) "
        "VALUES(?,'scan',0,?,?,'library-1',?,?,0,?,?,1,?,'complete',"
        "'failed','provider',?)",
        (
            preparation_work_id(CONNECTION, LIBRARY, ITEM),
            CONNECTION,
            LIBRARY,
            ITEM,
            "b" * 64,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            NOW + 1,
        ),
    )
    failed.close()
    assert world.row()["preparation"] is not None
    assert world.row()["link"] == {"state": "linked", "provider": "hardcover"}
    assert world.get(view)["link"]["state"] == "linked"
    failed = sqlite3.connect(world.path, isolation_level=None)
    failed.execute(
        "DELETE FROM review_preparation_work WHERE work_id=?",
        (preparation_work_id(CONNECTION, LIBRARY, ITEM),),
    )
    failed.close()

    # The owner withdraws it later; the work is prepared again without it.
    world.app_clock[0] += 10
    withdrawn = world.post(
        "/api/v1/library/link/withdraw",
        {
            "connection": CONNECTION,
            "library": LIBRARY,
            "item": ITEM,
            "link_row_id": chosen["link_row_id"],
        },
        key="end-to-end-withdraw-01",
    )
    assert withdrawn["action"] == "withdraw"
    assert world.get(view)["link"] is None
    assert [event["change"] for event in world.activity()] == [
        f"Withdrew the link to {LINKED_TITLE}",
        f"Linked to {LINKED_TITLE} on Hardcover",
    ]
    assert [trigger for *_, trigger in world.preparation.requests] == [
        PreparationTrigger.BOOK_LINKED
    ] * 2


def _import(world: _World, key: str) -> dict[str, Any]:
    """Preview and import a one-row identifiers file naming the work."""
    preview = world.client.post(
        f"/api/v1/libraries/{CONNECTION}/{LIBRARY}/identifier-previews",
        content=f"item\tidentifier\n{ITEM}\t{ISBN}\n".encode(),
        headers={**world.headers, "Content-Type": "text/plain; charset=utf-8"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"]["will_link"] == 1
    return cast(
        "dict[str, Any]",
        world.post(
            f"/api/v1/identifier-previews/{preview.json()['preview_id']}/import",
            None,
            key=key,
        ),
    )


def test_a_link_from_an_identifiers_file_is_withdrawn_and_changed_by_search(
    world: _World, tmp_path: Path
) -> None:
    """A file link shows as from the file everywhere it shows (#1478); the
    owner withdraws it on its own, and after a second import changes it with
    Find this book."""
    catalogue = open_catalogue(tmp_path / "catalogue.sqlite", create=True)
    catalogue.acquire_refresh()
    catalogue.publish(
        "f" * 64,
        (
            CatalogueEntry(
                CONNECTION,
                LIBRARY,
                "library-id",
                ITEM,
                1,
                LOCAL_TITLE,
                (LOCAL_AUTHOR,),
                True,
                False,
                "singular-supported",
                "a" * 64,
                "book.epub",
                "epub",
                "books",
                item_path="Shelf/Local Title",
            ),
        ),
    )
    catalogue.close()
    world.repository.bind_library_catalogue(tmp_path / "catalogue.sqlite", "f" * 64)
    view = f"/api/v1/library/link?connection={CONNECTION}&library={LIBRARY}&item={ITEM}"
    case = world.case_id()

    imported = _import(world, "file-import-request-01")
    assert imported["linked"] == 1
    assert world.row()["link"] == {
        "state": "preparing",
        "source": "file",
        "provider": None,
    }
    link = world.get(view)["link"]
    assert (link["source"], link["provider"], link["title"]) == ("file", None, None)
    assert (link["scope"], link["identifier"]) == ("edition", ISBN)
    history = world.get(f"/api/v1/review-cases/{case}/history")["items"]
    assert [
        (item["kind"], item["source"], item["provider"], item["identifier"])
        for item in history
        if item.get("schema_version") == OWNER_LINK_HISTORY_EVENT_SCHEMA
    ] == [("linked", "file", None, ISBN)]

    # Withdrawn on its own, from Find this book, as any link is.
    world.app_clock[0] += 10
    withdrawn = world.post(
        "/api/v1/library/link/withdraw",
        {
            "connection": CONNECTION,
            "library": LIBRARY,
            "item": ITEM,
            "link_row_id": link["link_row_id"],
        },
        key="file-withdraw-request-1",
    )
    assert withdrawn["action"] == "withdraw"
    assert world.get(view)["link"] is None
    assert (
        world.get(f"/api/v1/identifier-imports/{imported['import_id']}")["still_linked"]
        == 0
    )
    assert [event["change"] for event in world.activity()] == [
        f"Withdrew the link to ISBN {ISBN}"
    ]

    # Linked again by a second import, then changed with Find this book.
    world.app_clock[0] += 10
    again = _import(world, "file-import-request-02")
    world.clock[0] += 5
    search = world.post(
        "/api/v1/library/find/searches",
        {
            "connection": CONNECTION,
            "library": LIBRARY,
            "item": ITEM,
            "provider": "hardcover",
            "lane": "title_authors",
            "title": LINKED_TITLE,
            "authors": [LINKED_AUTHOR],
        },
    )
    world.app_clock[0] += 10
    changed = world.post(
        f"/api/v1/library/find/searches/{search['search_id']}/choice",
        {"position": 0, "scope": "work"},
        key="file-change-request-01",
    )
    assert changed["action"] == "replace"
    assert world.row()["link"] == {
        "state": "preparing",
        "source": "search",
        "provider": "hardcover",
    }
    assert (
        world.get(f"/api/v1/identifier-imports/{again['import_id']}")["still_linked"]
        == 0
    )
    assert world.activity()[0]["change"] == f"Linked to {LINKED_TITLE} on Hardcover"
    assert [trigger for *_, trigger in world.preparation.requests] == [
        PreparationTrigger.BOOK_LINKED
    ] * 4
