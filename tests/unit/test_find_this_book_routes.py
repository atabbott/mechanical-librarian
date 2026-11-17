"""Find this book over HTTP: the link view, search, preview, choose and withdraw (#1477).

Every route runs against a real store and the real owner search service,
with only the source, the clocks and the preparation supervisor faked. Each
refusal is proved at its place in the contract's order (S11 for a search,
S16 for a choice or withdrawal), and every write is proved by the rows the
store holds afterwards, never by the answer alone.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from mechanical_librarian.adapters.persistence import owner_links
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.adapters.persistence.owner_links import (
    OwnerLinkItem,
    current_owner_link_row,
    record_owner_link,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    preparation_work_id,
)
from mechanical_librarian.adapters.providers.retry_safety import (
    ProviderRequestPacer,
)
from mechanical_librarian.application.configuration import (
    ConfigurationError,
    LibraryConfiguration,
    ProviderConnectionConfiguration,
    ReviewWebConfiguration,
)
from mechanical_librarian.application.owner_search import OwnerSearchService
from mechanical_librarian.application.review_pipeline import PreparationTrigger
from mechanical_librarian.application.review_web import OWNER_LINK_VIEW_SCHEMA
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLinkAction,
    OwnerLinkScope,
    OwnerLinkTarget,
    OwnerSearchAnswer,
    OwnerSearchCover,
    OwnerSearchFailure,
    OwnerSearchQuery,
    OwnerSearchRecord,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from tests.support.review_web_doubles import FakePreparation
from tests.unit.review_app_fixture import (
    TEST_REACT_BUILD_IDENTITY,
    create_transport_test_app,
)

ORIGIN = "https://review.test"
NOW = 1_000
CONNECTION, LIBRARY, ITEM = "abs", "books", "item-1"
WALL = 1_800_000_000.0
KEY = "choose-request-0001"
SEARCH = "/api/v1/library/find/searches"
WITHDRAW = "/api/v1/library/link/withdraw"
VIEW = f"/api/v1/library/link?connection={CONNECTION}&library={LIBRARY}&item={ITEM}"


def _report() -> MetadataReport:
    fields = tuple(
        TargetField(TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Ada",))
        if value.destination is TargetDestination.AUTHORS
        else value
        for value in default_target_fields("Current title")
    )
    return MetadataReport.create(
        target=TargetFacts(CONNECTION, LIBRARY, ITEM, fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed="Current title",
                canonical="Current title",
                excerpt="Current title",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            ),
        ),
        local_reasons=("opening_matter_title",),
        providers=(),
    )


def _record(identity: str) -> OwnerSearchRecord:
    candidate = f"open-library-work-{identity.removeprefix('/works/')}"
    claims = (
        ProviderClaim.create(
            provider="open_library",
            candidate=candidate,
            kind=ClaimKind.TITLE,
            value="Current title",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            raw_order=0,
        ),
        ProviderClaim.create(
            provider="open_library",
            candidate=candidate,
            kind=ClaimKind.CREATOR,
            value="Ada",
            state=FieldState.VALID,
            scope=ClaimScope.WORK,
            applicability="not_applicable",
            role="author",
            raw_order=1,
        ),
    )
    return OwnerSearchRecord(
        ProviderCandidate(candidate, candidate, claims),
        identity,
        cover=OwnerSearchCover("https://covers.openlibrary.org/b/id/1-L.jpg"),
    )


_PROVIDERS = (
    ProviderConnectionConfiguration(
        "ol", True, True, None, adapter="open_library", contact_email="o@example.org"
    ),
)


@dataclass
class _Configuration:
    library_id: str = "library-1"
    limits: Any = field(default_factory=lambda: SimpleNamespace(timeout_seconds=30))
    providers: tuple[ProviderConnectionConfiguration, ...] = _PROVIDERS

    def select(self, connection: str, library: str) -> tuple[Any, ...]:
        if (connection, library) != (CONNECTION, LIBRARY):
            raise ConfigurationError("requested connection or library is unavailable")
        selected = LibraryConfiguration(
            LIBRARY, CONNECTION, self.library_id, "book", None, ("ol",)
        )
        return None, selected, None, self.providers


class _Preparation(FakePreparation):
    """The supervisor double, serving this test's library."""

    libraries = ((CONNECTION, LIBRARY, "library-1"),)

    def __init__(self) -> None:
        super().__init__()
        self.refuse = False

    def begin_drain(self) -> None:
        return None

    def stop(self, deadline: float | None = None) -> None:
        return None

    def progress(self, connection: str, library: str):
        progress = super().progress("primary", "fiction")
        return replace(progress, connection=connection, library=library)

    def request(self, connection, library, *, trigger=None):
        self.requests.append((connection, library, trigger))
        if self.refuse:
            raise RuntimeError("scan refused")
        return self.progress(connection, library)


@dataclass
class _Clocks:
    wall: float = WALL
    mono: float = 100.0

    def advance(self, seconds: float) -> None:
        self.wall += seconds
        self.mono += seconds


@dataclass
class _Live:
    client: TestClient
    headers: dict[str, str]
    repository: SQLiteMetadataReportRepository
    clocks: _Clocks
    configuration: _Configuration
    preparation: _Preparation
    answers: list[Callable[[OwnerSearchQuery], OwnerSearchAnswer]]
    queries: list[OwnerSearchQuery]
    published: list[tuple[str, str | None]]
    login: Callable[[], dict[str, str]]

    def post(
        self,
        path: str,
        body: object,
        headers: dict[str, str] | None = None,
        key: str | None = None,
    ):
        sent = dict(headers or self.headers)
        if key is not None:
            sent["Idempotency-Key"] = key
        return self.client.post(path, json=body, headers=sent)

    def search(self, changes: dict[str, object] | None = None):
        # Each search starts after the pacer's interval.
        self.clocks.advance(61)
        return self.post(SEARCH, _search_body(**(changes or {})))

    def choose(
        self,
        search_id: str,
        position: int = 0,
        scope: str = "work",
        key: str = KEY,
        headers: dict[str, str] | None = None,
    ):
        return self.post(
            f"{SEARCH}/{search_id}/choice",
            {"position": position, "scope": scope},
            headers,
            key,
        )

    def withdraw(self, link_row_id: str, key: str = "withdraw-request-01"):
        return self.post(
            WITHDRAW,
            {
                "connection": CONNECTION,
                "library": LIBRARY,
                "item": ITEM,
                "link_row_id": link_row_id,
            },
            key=key,
        )

    def rows(self) -> list[tuple[str, str]]:
        return self.repository._connection.execute(
            "SELECT action,request_id FROM owner_links ORDER BY rowid"
        ).fetchall()


def _search_body(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "connection": CONNECTION,
        "library": LIBRARY,
        "item": ITEM,
        "provider": "open_library",
        "lane": "title_authors",
        "title": "Current title",
        "authors": ["Ada"],
    }
    body.update(changes)
    return body


@pytest.fixture
def live(tmp_path: Path) -> Iterator[_Live]:
    repository = open_repository(tmp_path / "state.sqlite3", cross_thread=True)
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.admit_review_case(proposal)
    clocks = _Clocks()
    configuration = _Configuration()
    answers: list[Callable[[OwnerSearchQuery], OwnerSearchAnswer]] = [
        lambda _: OwnerSearchAnswer(
            (_record("/works/OL1W"), _record("/works/OL2W")), more=False
        )
    ]
    queries: list[OwnerSearchQuery] = []

    class _Source:
        def owner_search(self, query: OwnerSearchQuery, *, deadline: float):
            queries.append(query)
            return answers[-1](query)

    pacer = ProviderRequestPacer(
        60.0, clock=lambda: clocks.mono, sleep=lambda seconds: clocks.advance(seconds)
    )
    service = OwnerSearchService(
        configuration=cast(Any, configuration),
        works=repository,
        adapter=lambda provider, *, marketplaces: _Source(),
        pacers={"open_library": pacer},
        throttling=lambda: {"entries": []},
        fetch_preview=lambda cover, deadline: b"\x89PNG-preview",
        clock=lambda: clocks.wall,
        monotonic=lambda: clocks.mono,
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
        owner_search=service,
    )
    published: list[tuple[str, str | None]] = []
    channel = app.state.review_application.updates
    publish = channel.publish

    def recorded(topic: str, subject: str | None = None) -> int | None:
        published.append((topic, subject))
        return publish(topic, subject)

    channel.publish = recorded
    with TestClient(app, base_url=ORIGIN) as client:

        def login() -> dict[str, str]:
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

        yield _Live(
            client,
            login(),
            repository,
            clocks,
            configuration,
            preparation,
            answers,
            queries,
            published,
            login,
        )
    repository.close()


def _code(response) -> str:
    return response.json()["error"]["code"]


def _searched(live: _Live) -> str:
    answer = live.search()
    assert answer.status_code == 200, answer.text
    return answer.json()["search_id"]


def _link_directly(live: _Live, expected: str | None = None) -> str:
    """Another tab's link, landing between a search and its choice."""
    connection = live.repository._connection
    connection.execute("BEGIN IMMEDIATE")
    row = record_owner_link(
        connection,
        item=OwnerLinkItem(CONNECTION, LIBRARY, ITEM),
        action=OwnerLinkAction.LINK if expected is None else OwnerLinkAction.REPLACE,
        expected_current=expected,
        target=OwnerLinkTarget(
            OwnerLinkScope.WORK, "open_library", "/works/OL9W", "Another"
        ),
        source="search",
        medium="ebook",
        actor="owner",
        occurred_at=NOW,
        request_id="request-elsewhere-01",
        request_sha256="e" * 64,
    )
    connection.execute("COMMIT")
    return row.link_row_id


# -- The link view (S29) -------------------------------------------------------


def test_the_view_offers_the_work_and_names_no_link_until_one_is_chosen(
    live: _Live,
) -> None:
    answer = live.client.get(VIEW)
    assert answer.status_code == 200, answer.text
    view = answer.json()
    assert view == {
        "offered": True,
        "sources": [
            {"provider": "open_library", "lanes": view["sources"][0]["lanes"]}
            | (
                {"marketplaces": view["sources"][0]["marketplaces"]}
                if "marketplaces" in view["sources"][0]
                else {}
            )
        ],
        "prefill": {
            "title": "Current title",
            "authors": ["Ada"],
            "isbn": None,
            "asin": None,
        },
        "link": None,
        "schema_version": OWNER_LINK_VIEW_SCHEMA,
    }
    assert "title_authors" in view["sources"][0]["lanes"]
    choice = live.choose(_searched(live)).json()
    linked = live.client.get(VIEW).json()["link"]
    assert linked == {
        "creators": ["Ada"],
        "identifier": None,
        "link_row_id": choice["link_row_id"],
        "paused": False,
        "provider": "open_library",
        "reason": None,
        "retry_at": None,
        "scope": "work",
        "source": "search",
        "state": "preparing",
        "title": "Current title",
    }


@pytest.mark.parametrize("sources", [True, False], ids=["offered", "no-source"])
def test_the_library_row_says_what_the_view_says_before_and_after_a_link(
    live: _Live, sources: bool
) -> None:
    """The header shows its entry from the row, so row and view agree (#1477)."""
    if not sources:
        live.configuration.providers = ()

    def row() -> dict[str, object]:
        answer = live.client.get("/api/v1/library?lens=all")
        assert answer.status_code == 200, answer.text
        (item,) = answer.json()["items"]
        return item

    view = live.client.get(VIEW).json()
    assert row()["find_offered"] is view["offered"] is sources
    assert row()["link"] is None
    if not sources:
        return
    live.choose(_searched(live))
    view = live.client.get(VIEW).json()
    assert row()["find_offered"] is view["offered"] is True
    assert row()["link"] == {
        "state": view["link"]["state"],
        "source": view["link"]["source"],
        "provider": view["link"]["provider"],
    }


def test_a_row_that_could_not_finish_is_not_offered_and_keeps_its_link(
    live: _Live, tmp_path: Path
) -> None:
    """Could not finish (L4): the row offers no search but still shows the link."""
    live.choose(_searched(live))
    # The failed work row stands alone, with no scan row behind it, as the
    # owner-link store's own tests seed it.
    connection = sqlite3.connect(tmp_path / "state.sqlite3", isolation_level=None)
    connection.execute(
        "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,connection_name,"
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
    connection.close()
    view = live.client.get(VIEW).json()
    (row,) = live.client.get("/api/v1/library?lens=all").json()["items"]
    assert row["find_offered"] is view["offered"] is False
    assert row["link"] is not None and view["link"] is not None
    assert row["link"]["state"] == view["link"]["state"]


def test_the_view_needs_a_session_and_refuses_an_unknown_work(live: _Live) -> None:
    assert live.client.get(VIEW.replace(ITEM, "missing")).status_code == 404
    live.client.cookies.clear()
    assert live.client.get(VIEW).status_code == 401


# -- Search (S11) --------------------------------------------------------------


def test_a_search_answers_its_cards_and_records_nothing(live: _Live) -> None:
    answer = live.search()
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body["schema_version"] == "owner-search-v1"
    assert body["search_id"].startswith("search_")
    assert [card["position"] for card in body["cards"]] == [0, 1]
    assert (live.rows(), live.preparation.requests) == ([], [])
    # A search is not a library change: nothing is announced.
    assert live.published == []


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"X-CSRF-Token": ""}, 403),
        ({"Origin": "https://elsewhere.test"}, 403),
    ],
    ids=["no-csrf-token", "foreign-origin"],
)
def test_the_pre_call_checks_answer_before_the_body_is_read(
    live: _Live, headers: dict[str, str], status: int
) -> None:
    # The body is malformed too; the pre-call refusal still answers first.
    live.clocks.advance(61)
    answer = live.post(SEARCH, {"unexpected": True}, {**live.headers, **headers})
    assert answer.status_code == status, answer.text
    assert live.queries == []


def test_a_search_without_a_session_is_refused(live: _Live) -> None:
    live.client.cookies.clear()
    assert live.search().status_code == 401


@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({"item": "missing", "unexpected": 1}, 422, None),
        ({"item": "missing", "title": "  "}, 404, "work_not_found"),
        ({"provider": "itunes_search", "title": "  "}, 422, "provider_not_offered"),
        ({"title": "  ", "authors": []}, 422, "title_invalid"),
        ({"authors": []}, 422, "author_invalid"),
        ({"lane": "isbn", "isbn": "9780306406158"}, 422, "isbn_invalid"),
        ({"lane": "asin", "asin": "B000TEST01"}, 422, "provider_not_offered"),
        ({"marketplace": "uk"}, 422, "provider_not_offered"),
    ],
    ids=[
        "shape-before-work",
        "work-before-text",
        "source-before-text",
        "title-before-authors",
        "authors",
        "bad-isbn-checksum",
        "lane-not-offered",
        "marketplace-not-offered",
    ],
)
def test_search_refusals_answer_in_contract_order(
    live: _Live, changes: dict[str, object], status: int, code: str | None
) -> None:
    answer = live.search(changes)
    assert answer.status_code == status, answer.text
    if code is not None:
        assert answer.json() == {
            "error": {"code": code},
            "schema_version": "review-error-v1",
        }
    assert live.queries == []


def test_a_search_inside_the_hour_limit_answers_its_retry_time(live: _Live) -> None:
    for _ in range(30):
        assert live.search().status_code == 200
    refused = live.search()
    assert (refused.status_code, _code(refused)) == (429, "search_limit")
    assert type(refused.json()["error"]["retry_at"]) is int


@pytest.mark.parametrize(
    ("failure", "status", "error"),
    [
        (
            OwnerSearchFailure.RATE_LIMITED,
            429,
            {"code": "provider_deferred", "reason": "rate_limited"},
        ),
        (
            OwnerSearchFailure.UNAVAILABLE,
            502,
            {"code": "search_failed", "kind": "unavailable"},
        ),
    ],
    ids=["deferred", "failed"],
)
def test_a_source_refusal_is_one_closed_code(
    live: _Live,
    failure: OwnerSearchFailure,
    status: int,
    error: dict[str, object],
) -> None:
    live.answers.append(lambda _: OwnerSearchAnswer((), failure=failure))
    answer = live.search()
    assert answer.status_code == status, answer.text
    body = answer.json()["error"]
    body.pop("retry_at", None)
    assert body == error
    assert live.rows() == []


# -- Previews (S14) ------------------------------------------------------------


def test_a_preview_is_private_and_same_origin_only(live: _Live) -> None:
    search_id = _searched(live)
    path = f"{SEARCH}/{search_id}/previews/0"
    # The preview waits its turn on the source's shared pacer.
    live.clocks.advance(61)
    answer = live.client.get(path, headers={"Sec-Fetch-Site": "same-origin"})
    assert answer.status_code == 200, answer.text
    assert answer.content == b"\x89PNG-preview"
    assert answer.headers["content-type"] == "image/png"
    directives = {value.strip() for value in answer.headers["cache-control"].split(",")}
    assert directives == {"private", "no-store"}
    assert answer.headers["content-security-policy"].startswith("default-src 'none'")
    assert answer.headers["x-content-type-options"] == "nosniff"
    cross = live.client.get(path, headers={"Sec-Fetch-Site": "cross-site"})
    assert (cross.status_code, _code(cross)) == (404, "preview_unavailable")
    missing = live.client.get(f"{SEARCH}/{search_id}/previews/7")
    assert (missing.status_code, _code(missing)) == (404, "preview_unavailable")
    foreign = live.client.get(f"{SEARCH}/search_{'x' * 43}/previews/0")
    assert (foreign.status_code, _code(foreign)) == (404, "search_not_found")


# -- Choose (S16) --------------------------------------------------------------


def test_a_choice_records_the_retained_card_and_asks_for_the_scan(
    live: _Live,
) -> None:
    answer = live.choose(_searched(live), position=1)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert body == {
        "action": "link",
        "link_row_id": body["link_row_id"],
        "scan_requested": True,
        "schema_version": "owner-link-change-v1",
    }
    row = current_owner_link_row(
        live.repository._connection, OwnerLinkItem(CONNECTION, LIBRARY, ITEM)
    )
    assert row is not None and row.link_row_id == body["link_row_id"]
    assert row.target is not None and row.target.identity == "/works/OL2W"
    assert (row.actor, row.request_id) == ("owner", KEY)
    assert live.preparation.requests == [
        (CONNECTION, LIBRARY, PreparationTrigger.BOOK_LINKED)
    ]
    # Only the existing topics are announced; this work has no open case, so
    # there is no case topic (the end-to-end proof covers one that does).
    case_id = live.repository._connection.execute(
        "SELECT review_case_id FROM current_review_packages"
    ).fetchone()[0]
    assert live.published == [("library", case_id), ("activity", None)] + (
        [] if case_id is None else [("case", case_id)]
    )


def test_browser_sent_identity_is_refused_as_shape(live: _Live) -> None:
    search_id = _searched(live)
    answer = live.post(
        f"{SEARCH}/{search_id}/choice",
        {
            "position": 0,
            "scope": "work",
            "provider": "open_library",
            "identity": "/works/OL666W",
        },
        key=KEY,
    )
    assert answer.status_code == 422, answer.text
    assert live.rows() == []


@pytest.mark.parametrize(
    "key",
    ["short-key", "x" * 129, "has a space in it!"],
    ids=["too-short", "too-long", "not-printable-token"],
)
def test_an_idempotency_key_outside_its_grammar_is_refused(
    live: _Live, key: str
) -> None:
    answer = live.choose(_searched(live), key=key)
    assert answer.status_code == 422, answer.text
    assert live.rows() == []


def test_the_same_choice_replays_and_asks_for_the_scan_again(live: _Live) -> None:
    search_id = _searched(live)
    first = live.choose(search_id).json()
    # The search expires; the replay is read from the store first.
    live.clocks.advance(16 * 60)
    again = live.choose(search_id)
    assert again.status_code == 200, again.text
    assert again.json() == first
    assert live.rows() == [("link", KEY)]
    assert [trigger for *_, trigger in live.preparation.requests] == [
        PreparationTrigger.BOOK_LINKED,
        PreparationTrigger.BOOK_LINKED,
    ]


def test_the_same_key_with_another_choice_is_a_conflict(live: _Live) -> None:
    search_id = _searched(live)
    live.choose(search_id)
    answer = live.choose(search_id, position=1)
    assert (answer.status_code, _code(answer)) == (409, "idempotency_conflict")
    assert live.rows() == [("link", KEY)]


def test_an_expired_or_foreign_search_is_not_found(live: _Live) -> None:
    expired_id = _searched(live)
    live.clocks.advance(16 * 60)
    expired = live.choose(expired_id)
    assert (expired.status_code, _code(expired)) == (404, "search_not_found")
    search_id = _searched(live)
    # Another session (the client now holds its cookie) cannot choose it.
    other = live.login()
    foreign = live.choose(search_id, key="choose-request-0002", headers=other)
    assert (foreign.status_code, _code(foreign)) == (404, "search_not_found")
    assert live.rows() == []


@pytest.mark.parametrize(
    ("position", "scope", "code"),
    [(5, "work", "card_not_choosable"), (0, "edition", "scope_not_offered")],
    ids=["no-such-card", "scope-not-offered"],
)
def test_a_card_or_scope_not_offered_is_refused(
    live: _Live, position: int, scope: str, code: str
) -> None:
    answer = live.choose(_searched(live), position=position, scope=scope)
    assert (answer.status_code, _code(answer)) == (422, code)
    assert live.rows() == []


def test_a_changed_work_is_refused_before_a_changed_link(live: _Live) -> None:
    search_id = _searched(live)
    _link_directly(live)
    live.configuration.library_id = "library-2"
    answer = live.choose(search_id)
    assert (answer.status_code, _code(answer)) == (409, "work_changed")
    live.configuration.library_id = "library-1"
    answer = live.choose(search_id)
    assert (answer.status_code, _code(answer)) == (409, "link_changed")
    assert [action for action, _ in live.rows()] == ["link"]


def test_a_choice_on_a_linked_work_replaces_its_link(live: _Live) -> None:
    first = live.choose(_searched(live)).json()
    second = live.choose(_searched(live), position=1, key="choose-request-0002")
    assert second.status_code == 200, second.text
    assert second.json()["action"] == "replace"
    assert second.json()["link_row_id"] != first["link_row_id"]
    assert [action for action, _ in live.rows()] == ["link", "replace"]


def test_a_failed_write_rolls_back_and_answers_unavailable(
    live: _Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_: object, **__: object) -> None:
        raise sqlite3.OperationalError("disk I/O error")

    search_id = _searched(live)
    monkeypatch.setattr(owner_links, "mark_preparation_work_fresh", fail)
    answer = live.choose(search_id)
    assert (answer.status_code, _code(answer)) == (503, "unavailable")
    assert live.rows() == []
    assert live.repository._connection.in_transaction is False
    assert live.preparation.requests == []


def test_a_refused_scan_still_records_the_link(live: _Live) -> None:
    live.preparation.refuse = True
    answer = live.choose(_searched(live))
    assert answer.status_code == 200, answer.text
    assert answer.json()["scan_requested"] is False
    assert live.rows() == [("link", KEY)]


# -- Withdraw (S16) ------------------------------------------------------------


def test_withdraw_ends_the_named_link_and_replays(live: _Live) -> None:
    link_row_id = live.choose(_searched(live)).json()["link_row_id"]
    answer = live.withdraw(link_row_id)
    assert answer.status_code == 200, answer.text
    assert answer.json()["action"] == "withdraw"
    assert live.client.get(VIEW).json()["link"] is None
    assert live.withdraw(link_row_id).json() == answer.json()
    assert [action for action, _ in live.rows()] == ["link", "withdraw"]
    assert [trigger for *_, trigger in live.preparation.requests] == [
        PreparationTrigger.BOOK_LINKED
    ] * 3


def test_withdraw_of_a_link_that_is_not_current_is_refused(live: _Live) -> None:
    link_row_id = live.choose(_searched(live)).json()["link_row_id"]
    replaced = _link_directly(live, link_row_id)
    answer = live.withdraw(link_row_id)
    assert (answer.status_code, _code(answer)) == (409, "link_changed")
    assert live.withdraw(replaced, key="withdraw-request-02").status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {
            "connection": CONNECTION,
            "library": LIBRARY,
            "item": ITEM,
            "link_row_id": "x",
        },
        {
            "connection": CONNECTION,
            "library": LIBRARY,
            "item": ITEM,
            "link_row_id": "owner_link_" + "0" * 64,
            "scope": "work",
        },
    ],
    ids=["bad-row-id", "extra-field"],
)
def test_a_withdraw_body_outside_its_shape_is_refused(
    live: _Live, body: dict[str, object]
) -> None:
    answer = live.post(WITHDRAW, body, key="withdraw-request-01")
    assert answer.status_code == 422, answer.text


# -- A link named by an identifiers file (#1478) ---------------------------------

FILE_IMPORT = "identifier_import_" + "d" * 64


def _link_from_a_file(live: _Live) -> str:
    """A file link: its import recorded it with no source and no title."""
    connection = live.repository._connection
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO identifier_imports(import_id,connection_name,library_name,"
        "row_count,will_link,already_linked,no_item,ambiguous,invalid_identifier,"
        "wrong_medium,repeated,conflicting,unreadable,actor,created_at,request_id,"
        "request_sha256,preview_digest) VALUES(?,?,?,1,1,0,0,0,0,0,0,0,0,'owner',?,"
        "'import-request-0001',?,?)",
        (FILE_IMPORT, CONNECTION, LIBRARY, NOW, "a" * 64, "b" * 64),
    )
    row = record_owner_link(
        connection,
        item=OwnerLinkItem(CONNECTION, LIBRARY, ITEM),
        action=OwnerLinkAction.LINK,
        expected_current=None,
        target=OwnerLinkTarget(OwnerLinkScope.EDITION, None, "9780306406157", None),
        source="file",
        medium="ebook",
        actor="owner",
        occurred_at=NOW,
        request_id="identifier_import_link_" + "e" * 64,
        request_sha256="a" * 64,
        import_id=FILE_IMPORT,
    )
    connection.execute("COMMIT")
    return row.link_row_id


def test_a_file_link_reads_on_every_link_surface(live: _Live) -> None:
    linked = _link_from_a_file(live)
    view = live.client.get(VIEW)
    assert view.status_code == 200, view.text
    assert view.json()["link"] == {
        "creators": [],
        "identifier": "9780306406157",
        "link_row_id": linked,
        "paused": False,
        "provider": None,
        "reason": None,
        "retry_at": None,
        "scope": "edition",
        "source": "file",
        "state": "preparing",
        "title": None,
    }
    (row,) = live.client.get("/api/v1/library?lens=all").json()["items"]
    assert row["link"] == {"state": "preparing", "source": "file", "provider": None}
    # History's file variant is read through the store in test_owner_link_history.


def test_find_this_book_changes_and_withdraws_a_file_link(live: _Live) -> None:
    linked = _link_from_a_file(live)
    changed = live.choose(_searched(live))
    assert changed.status_code == 200, changed.text
    assert changed.json()["action"] == "replace"
    assert live.client.get(VIEW).json()["link"]["source"] == "search"
    withdrawn = live.withdraw(changed.json()["link_row_id"])
    assert withdrawn.status_code == 200, withdrawn.text
    assert live.client.get(VIEW).json()["link"] is None
    assert [action for action, _ in live.rows()] == ["link", "replace", "withdraw"]
    assert live.rows()[0][1].startswith("identifier_import_link_")
    assert linked != changed.json()["link_row_id"]


def test_find_this_book_withdraws_a_file_link_directly(live: _Live) -> None:
    linked = _link_from_a_file(live)
    withdrawn = live.withdraw(linked)
    assert withdrawn.status_code == 200, withdrawn.text
    assert live.client.get(VIEW).json()["link"] is None
