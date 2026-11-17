"""The identifiers file routes over the real store and catalogue (#1478).

A preview decides a `text/plain` file and records nothing; an import links
every will-link row at once and asks for the library's scan; Withdraw this
import ends the import's links that are still current. The session, CSRF
token, Origin and browser build are checked before the body is read, and no
row text, path or item ID of the file ever leaves in an answer, an event or
the store.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.owner_search import OwnerSearchService
from mechanical_librarian.application.review_pipeline import PreparationTrigger
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)
from tests.unit.test_find_this_book_routes import (
    CONNECTION,
    LIBRARY,
    _Configuration,
    _Preparation,
)

ORIGIN = "https://review.test"
NOW = 1_000
FINGERPRINT = "f" * 64
PREVIEWS = f"/api/v1/libraries/{CONNECTION}/{LIBRARY}/identifier-previews"
ISBN13 = "9780306406157"
SENTINELS = ("sentinel-missing-item", "Sentinel Shelf/Sentinel Missing", "B0SENTINEL")


def _entry(index: int, *, audio: bool = False) -> CatalogueEntry:
    return CatalogueEntry(
        CONNECTION,
        LIBRARY,
        "library-1",
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
        item_path=f"Shelf/Book {index}",
    )


ENTRIES = (_entry(1), _entry(2), _entry(3, audio=True))
FILE = (
    "path\tidentifier\n"
    f"Shelf/Book 1\t{ISBN13}\n"
    "/library/Shelf/Book 2\t978-0-8044-2957-3\n"
    "Shelf/Book 3\tb00test001\n"
    f"{SENTINELS[1]}\t{ISBN13}\n"
    f"Shelf/Book 1\t{SENTINELS[2]}\n"
).encode()


@dataclass
class _Live:
    client: TestClient
    headers: dict[str, str]
    repository: SQLiteMetadataReportRepository
    preparation: _Preparation
    published: list[tuple[str, str | None]]
    root: Path

    def preview(
        self,
        data: bytes = FILE,
        *,
        path: str = PREVIEWS,
        content_type: str = "text/plain; charset=utf-8",
        headers: dict[str, str] | None = None,
    ):
        return self.client.post(
            path,
            content=data,
            headers={**(headers or self.headers), "Content-Type": content_type},
        )

    def confirm(self, preview_id: str, key: str = "import-request-0001"):
        return self.client.post(
            f"/api/v1/identifier-previews/{preview_id}/import",
            headers={**self.headers, "Idempotency-Key": key},
        )

    def withdraw(self, import_id: str, key: str = "withdraw-request-0001"):
        return self.client.post(
            f"/api/v1/identifier-imports/{import_id}/withdraw",
            headers={**self.headers, "Idempotency-Key": key},
        )

    def links(self) -> list[tuple[str, str, str | None]]:
        return self.repository._connection.execute(
            "SELECT item_id,action,import_id FROM owner_links ORDER BY rowid"
        ).fetchall()

    def republish(self, entries: tuple[CatalogueEntry, ...]) -> None:
        catalogue = open_catalogue(self.root / "catalogue.sqlite")
        catalogue.acquire_refresh()
        catalogue.publish(FINGERPRINT, entries)
        catalogue.close()


def _login(client: TestClient) -> dict[str, str]:
    answer = client.post(
        "/api/v1/session/login",
        json={"password": "correct horse"},
        headers={"Origin": ORIGIN},
    )
    assert answer.status_code == 200, answer.text
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": answer.json()["csrf_token"],
        "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
    }


@contextmanager
def identifier_web(tmp_path: Path) -> Iterator[_Live]:
    """A signed-in client over the real store with a three-work catalogue."""
    catalogue = open_catalogue(tmp_path / "catalogue.sqlite", create=True)
    catalogue.acquire_refresh()
    catalogue.publish(FINGERPRINT, ENTRIES)
    catalogue.close()
    repository = open_repository(tmp_path / "state.sqlite3", cross_thread=True)
    repository.bind_library_catalogue(tmp_path / "catalogue.sqlite", FINGERPRINT)
    configuration = _Configuration()
    search = OwnerSearchService(
        configuration=cast(Any, configuration),
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
    )
    published: list[tuple[str, str | None]] = []
    channel = app.state.review_application.updates
    publish = channel.publish

    def recorded(topic: str, subject: str | None = None) -> int | None:
        published.append((topic, subject))
        return publish(topic, subject)

    channel.publish = recorded
    with TestClient(app, base_url=ORIGIN) as client:
        yield _Live(
            client, _login(client), repository, preparation, published, tmp_path
        )
    repository.close()


@pytest.fixture
def live(tmp_path: Path) -> Iterator[_Live]:
    with identifier_web(tmp_path) as web:
        yield web


def _code(response) -> str:
    return response.json()["error"]["code"]


def _previewed(live: _Live, data: bytes = FILE) -> dict[str, Any]:
    answer = live.preview(data)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _imported(live: _Live) -> dict[str, Any]:
    answer = live.confirm(_previewed(live)["preview_id"])
    assert answer.status_code == 200, answer.text
    return answer.json()


# -- Preview -------------------------------------------------------------------


def test_a_preview_decides_every_row_and_records_nothing(live: _Live) -> None:
    answer = live.preview()
    assert answer.status_code == 200, answer.text
    assert "no-store" in answer.headers["cache-control"]
    body = answer.json()
    assert body["schema_version"] == "identifier-preview-v1"
    assert body["expires_at"] == NOW + 900
    assert body["rows"] == 5
    assert body["counts"] == {
        "will_link": 3,
        "already_linked": 0,
        "no_item": 1,
        "ambiguous": 0,
        "invalid_identifier": 0,
        "wrong_medium": 1,
        "repeated": 0,
        "conflicting": 0,
        "unreadable": 0,
    }
    # Each will-link row names its work as Library does, and its record.
    assert body["will_link"] == [
        {
            "row": row,
            "title": f"Title {index}",
            "authors": [f"Author {index}"],
            "identifier_kind": kind,
            "identifier": identifier,
        }
        for row, index, kind, identifier in (
            (2, 1, "isbn", ISBN13),
            (3, 2, "isbn", "9780804429573"),
            (4, 3, "asin", "B00TEST001"),
        )
    ]
    assert body["refused"] == [
        {"row": 5, "reason": "no_item"},
        {"row": 6, "reason": "asin_for_ebook"},
    ]
    assert live.links() == []
    assert live.preparation.requests == []
    assert live.published == []


@pytest.mark.parametrize(
    "content_type", ["application/json", "text/csv", "text/plain; charset=latin-1"]
)
def test_a_body_that_is_not_utf8_plain_text_is_refused(
    live: _Live, content_type: str
) -> None:
    answer = live.preview(content_type=content_type)
    assert (answer.status_code, _code(answer)) == (415, "unsupported_media_type")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"X-CSRF-Token": ""}, 403),
        ({"Origin": "https://elsewhere.test"}, 403),
        ({"x-ml-build-identity": "another-build"}, 409),
    ],
    ids=["no-csrf-token", "foreign-origin", "another-build"],
)
def test_the_pre_call_checks_answer_before_the_body_is_read(
    live: _Live, headers: dict[str, str], status: int
) -> None:
    answer = live.preview(
        b"\xff", content_type="application/json", headers={**live.headers, **headers}
    )
    assert answer.status_code == status, answer.text


def test_every_route_needs_a_session(live: _Live) -> None:
    preview_id = _previewed(live)["preview_id"]
    live.client.cookies.clear()
    import_id = "identifier_import_" + "0" * 64
    assert live.preview().status_code == 401
    assert live.confirm(preview_id).status_code == 401
    assert live.withdraw(import_id).status_code == 401
    assert live.client.get(f"/api/v1/identifier-previews/{preview_id}").status_code == (
        401
    )
    assert live.client.get(f"/api/v1/identifier-imports/{import_id}").status_code == (
        401
    )


def test_a_library_not_in_the_settings_is_not_found(live: _Live) -> None:
    answer = live.preview(
        path=f"/api/v1/libraries/{CONNECTION}/other/identifier-previews"
    )
    assert (answer.status_code, _code(answer)) == (404, "library_not_found")


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"", "file_header"),
        (b"path\tidentifier\n", "file_no_rows"),
        (b"path\tidentifier\n\xff\n", "file_encoding"),
        (b"item\tisbn\nitem-1\t" + ISBN13.encode() + b"\n", "file_header"),
    ],
)
def test_a_file_refusal_is_one_closed_code(live: _Live, data: bytes, code: str) -> None:
    answer = live.preview(data)
    assert (answer.status_code, answer.json()) == (
        422,
        {"error": {"code": code}, "schema_version": "review-error-v1"},
    )


def test_a_file_of_exactly_one_mebibyte_is_read_and_one_byte_more_is_not(
    live: _Live,
) -> None:
    header = b"path\tidentifier\n"
    rows = b"x" * 255 + b"\n"
    filler = 1_048_576 - len(header) - 4_080 * len(rows)
    data = header + rows * 4_080 + b"x" * (filler - 1) + b"\n"
    assert len(data) == 1_048_576
    answer = live.preview(data)
    assert answer.status_code == 200, answer.text
    assert answer.json()["counts"]["unreadable"] == 4_081
    over = live.preview(data + b"x")
    assert (over.status_code, _code(over)) == (413, "body_too_large")


def test_an_unreadable_catalogue_is_unavailable(live: _Live) -> None:
    (live.root / "catalogue.sqlite").unlink()
    answer = live.preview()
    assert (answer.status_code, _code(answer)) == (503, "library_unreadable")


# -- Import --------------------------------------------------------------------


def test_an_import_links_every_will_link_row_and_asks_for_the_scan(
    live: _Live,
) -> None:
    preview = _previewed(live)
    answer = live.confirm(preview["preview_id"])
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body == {
        "import_id": body["import_id"],
        "linked": preview["counts"]["will_link"],
        "scan_requested": True,
        "schema_version": "identifier-import-v1",
    }
    assert body["import_id"].startswith("identifier_import_")
    links = live.links()
    assert len(links) == preview["counts"]["will_link"]
    assert {action for _, action, _ in links} == {"link"}
    assert {import_id for _, _, import_id in links} == {body["import_id"]}
    assert live.preparation.requests == [
        (CONNECTION, LIBRARY, PreparationTrigger.BOOK_LINKED)
    ]
    assert ("library", None) in live.published
    assert ("activity", None) in live.published
    status = live.client.get(f"/api/v1/identifier-previews/{preview['preview_id']}")
    assert status.json() == {
        "state": "imported",
        "import_id": body["import_id"],
        "linked": body["linked"],
        "schema_version": "identifier-preview-status-v1",
    }
    # The Library row's file variant is proved in test_find_this_book_routes.


def test_the_same_key_replays_and_another_key_is_refused(live: _Live) -> None:
    preview_id = _previewed(live)["preview_id"]
    first = live.confirm(preview_id)
    again = live.confirm(preview_id)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()
    assert len(live.preparation.requests) == 2
    other = live.confirm(preview_id, key="import-request-0002")
    assert (other.status_code, _code(other)) == (409, "preview_imported")
    assert len(live.links()) == first.json()["linked"]


def test_another_request_under_the_same_key_is_a_conflict(live: _Live) -> None:
    _imported(live)
    second = _previewed(live)
    answer = live.confirm(second["preview_id"])
    assert (answer.status_code, _code(answer)) == (409, "idempotency_conflict")


def test_a_preview_of_another_session_or_gone_is_not_found(live: _Live) -> None:
    preview_id = _previewed(live)["preview_id"]
    live.client.cookies.clear()
    other = _login(live.client)
    answer = live.client.post(
        f"/api/v1/identifier-previews/{preview_id}/import",
        headers={**other, "Idempotency-Key": "import-request-0001"},
    )
    assert (answer.status_code, _code(answer)) == (404, "preview_not_found")
    status = live.client.get(f"/api/v1/identifier-previews/{preview_id}")
    assert status.json()["state"] == "closed"
    assert live.links() == []


def test_a_changed_library_makes_the_preview_stale(live: _Live) -> None:
    preview_id = _previewed(live)["preview_id"]
    live.republish((*ENTRIES, _entry(4)))
    answer = live.confirm(preview_id)
    assert (answer.status_code, _code(answer)) == (409, "preview_stale")
    assert live.links() == []
    status = live.client.get(f"/api/v1/identifier-previews/{preview_id}")
    assert status.json()["state"] == "open"


def test_a_preview_with_nothing_to_link_is_refused(live: _Live) -> None:
    preview = _previewed(live, b"path\tidentifier\nNowhere\t" + ISBN13.encode() + b"\n")
    assert preview["counts"]["will_link"] == 0
    answer = live.confirm(preview["preview_id"])
    assert (answer.status_code, _code(answer)) == (409, "nothing_to_link")


def test_an_idempotency_key_outside_its_grammar_is_refused(live: _Live) -> None:
    preview_id = _previewed(live)["preview_id"]
    answer = live.confirm(preview_id, key="short")
    assert answer.status_code == 422, answer.text
    assert live.links() == []


def test_a_refused_scan_still_records_the_import(live: _Live) -> None:
    live.preparation.refuse = True
    body = _imported(live)
    assert body["scan_requested"] is False
    assert len(live.links()) == body["linked"]


# -- Withdraw this import ------------------------------------------------------


def test_withdraw_ends_the_imports_current_links_and_replays(live: _Live) -> None:
    imported = _imported(live)
    import_id = imported["import_id"]
    status = live.client.get(f"/api/v1/identifier-imports/{import_id}")
    assert status.json() == {
        "import_id": import_id,
        "linked": imported["linked"],
        "still_linked": imported["linked"],
        "withdrawn": False,
        "schema_version": "identifier-import-v1",
    }
    assert "no-store" in status.headers["cache-control"]
    answer = live.withdraw(import_id)
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "import_id": import_id,
        "withdrawn": imported["linked"],
        "scan_requested": True,
        "schema_version": "identifier-import-withdrawal-v1",
    }
    assert live.withdraw(import_id).json() == answer.json()
    again = live.withdraw(import_id, key="withdraw-request-0002")
    assert (again.status_code, _code(again)) == (409, "import_withdrawn")
    assert (
        live.client.get(f"/api/v1/identifier-imports/{import_id}").json()[
            "still_linked"
        ]
        == 0
    )
    actions = [action for _, action, _ in live.links()]
    assert actions.count("withdraw") == imported["linked"]


def test_an_unknown_or_malformed_import_is_not_found(live: _Live) -> None:
    for import_id in ("identifier_import_" + "0" * 64, "identifier_import_x"):
        status = live.client.get(f"/api/v1/identifier-imports/{import_id}")
        assert (status.status_code, _code(status)) == (404, "import_not_found")
        answer = live.withdraw(import_id)
        assert (answer.status_code, _code(answer)) == (404, "import_not_found")


# -- Privacy -------------------------------------------------------------------


def test_no_row_text_path_or_item_reaches_an_answer_an_event_or_the_store(
    live: _Live, capsys: pytest.CaptureFixture[str]
) -> None:
    preview = live.preview()
    imported = live.confirm(preview.json()["preview_id"])
    status = live.client.get(
        f"/api/v1/identifier-imports/{imported.json()['import_id']}"
    )
    refused = live.preview(b"path\tidentifier\n" + SENTINELS[0].encode() + b"\x07\n")
    text = json.dumps([preview.json(), imported.json(), status.json(), refused.json()])
    stderr = capsys.readouterr().err
    connection = sqlite3.connect(live.root / "state.sqlite3")
    try:
        stored = "\n".join(
            line for line in connection.iterdump() if line.startswith("INSERT")
        )
    finally:
        connection.close()
    for sentinel in SENTINELS:
        for surface in (text, stderr, stored):
            assert sentinel.lower() not in surface.lower()
    events = [json.loads(line) for line in stderr.splitlines() if "identifier" in line]
    assert {event["outcome"] for event in events} >= {"previewed", "recorded"}
