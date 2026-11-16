"""Each source's owner search request, exactly as S8 shapes it (#1477).

One request per search (three for an Inventaire title search), one attempt,
the limit of eleven (Hardcover's ISBN document keeps its own six), the key in
its header and never the URL, no pattern operator, and the eleventh row a
sentinel that only says there are more.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from mechanical_librarian.adapters.providers import hardcover_metadata
from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    AudibleCatalogMetadataProvider,
)
from mechanical_librarian.adapters.providers.google_books_metadata import (
    GOOGLE_BOOKS_URL,
    GoogleBooksMetadataProvider,
)
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HARDCOVER_URL,
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.inventaire_metadata import (
    INVENTAIRE_ENTITIES_URL,
    INVENTAIRE_SEARCH_URL,
    InventaireMetadataProvider,
)
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OPEN_LIBRARY_SEARCH_URL,
    OWNER_CARD_FIELDS,
    OpenLibraryMetadataProvider,
)
from mechanical_librarian.domain.owner_link import (
    OwnerLinkScope,
    OwnerSearchAnswer,
    OwnerSearchFailure,
    OwnerSearchQuery,
    OwnerSearchWork,
    owner_search_cards,
)
from tests.support.hardcover_evidence import books_envelope, hardcover_envelope

ISBN = "9780306406157"
ASIN = "B000TEST01"
TITLE = OwnerSearchQuery("title_authors", title="Current title", authors=("Ada",))
BY_ISBN = OwnerSearchQuery("isbn", isbn=ISBN)
_PATTERN_OPERATORS = ("_ilike", "_like", "_regex", "_iregex", "_similar", "*")

_Handler = Callable[[httpx.Request], httpx.Response]


def _json(payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, headers={"content-type": "application/json"}, json=payload
    )


def _client(handler: _Handler) -> tuple[httpx.Client, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(record)), sent


def _search(provider: Any, query: OwnerSearchQuery) -> OwnerSearchAnswer:
    answer = provider.owner_search(query, deadline=time.monotonic() + 20)
    assert type(answer) is OwnerSearchAnswer
    return answer


# -- Open Library -----------------------------------------------------------


def _ol_doc(index: int) -> dict[str, object]:
    return {
        "key": f"/works/OL{index}W",
        "title": f"Current title {index}",
        "author_name": ["Ada Example"],
        "first_publish_year": 1999,
        "isbn": [ISBN],
        "cover_i": index,
    }


def _open_library(handler: _Handler) -> tuple[Any, list[httpx.Request]]:
    client, sent = _client(handler)
    return OpenLibraryMetadataProvider("catalogue", client, 262_144), sent


def test_open_library_title_search_is_one_get_with_limit_eleven() -> None:
    provider, sent = _open_library(
        lambda _: _json({"docs": [_ol_doc(index) for index in range(1, 12)]})
    )
    answer = _search(provider, TITLE)
    (request,) = sent
    assert request.method == "GET"
    assert str(request.url.copy_with(query=None)) == OPEN_LIBRARY_SEARCH_URL
    assert dict(request.url.params) == {
        "title": "Current title",
        "author": "Ada",
        "fields": OWNER_CARD_FIELDS,
        "limit": "11",
    }
    assert (len(answer.records), answer.more, answer.dropped) == (10, True, 0)
    assert answer.records[0].identity == "/works/OL1W"
    # A title search's document lists every edition's ISBN and names none.
    assert answer.records[0].isbn is None
    cover = answer.records[0].cover
    assert cover is not None and cover.archive_redirects
    assert cover.url.startswith("https://covers.openlibrary.org/b/id/1-L.jpg")


def test_open_library_isbn_search_names_the_queried_edition() -> None:
    provider, sent = _open_library(lambda _: _json({"docs": [_ol_doc(1)]}))
    answer = _search(provider, BY_ISBN)
    (request,) = sent
    assert dict(request.url.params) == {
        "q": f"isbn:{ISBN}",
        "fields": OWNER_CARD_FIELDS,
        "limit": "11",
    }
    assert (answer.records[0].isbn, answer.more) == (ISBN, False)


def test_open_library_drops_and_counts_a_document_outside_the_grammar() -> None:
    provider, _ = _open_library(
        lambda _: _json(
            {"docs": [_ol_doc(1), {"key": "/books/OL1M", "title": "x"}, "row"]}
        )
    )
    answer = _search(provider, TITLE)
    assert (len(answer.records), answer.dropped) == (1, 2)


# -- Google Books -----------------------------------------------------------


def _volume(index: int) -> dict[str, object]:
    return {
        "id": f"volume{index:04d}",
        "volumeInfo": {
            "title": f"Current title {index}",
            "authors": ["Ada Example"],
            "industryIdentifiers": [{"type": "ISBN_13", "identifier": ISBN}],
        },
    }


def _google(handler: _Handler) -> tuple[Any, list[httpx.Request]]:
    client, sent = _client(handler)
    provider = GoogleBooksMetadataProvider(
        "catalogue", client, 262_144, api_key="sentinel-google-key"
    )
    return provider, sent


@pytest.mark.parametrize(
    ("query", "q"),
    [(TITLE, None), (BY_ISBN, f"isbn:{ISBN}")],
    ids=["title", "isbn"],
)
def test_google_books_sends_max_results_eleven_and_the_key_in_its_header(
    query: OwnerSearchQuery, q: str | None
) -> None:
    provider, sent = _google(
        lambda _: _json({"items": [_volume(index) for index in range(1, 12)]})
    )
    answer = _search(provider, query)
    (request,) = sent
    assert request.method == "GET"
    assert str(request.url.copy_with(query=None)) == GOOGLE_BOOKS_URL
    params = dict(request.url.params)
    assert params["maxResults"] == "11"
    assert q is None or params["q"] == q
    assert "Current title" in params["q"] or q is not None
    assert request.headers["X-Goog-Api-Key"] == "sentinel-google-key"
    assert "sentinel-google-key" not in str(request.url)
    assert answer.more is True
    assert len(answer.records) == 10


# -- Hardcover --------------------------------------------------------------


def _book(book_id: int, canonical_id: int | None = None) -> dict[str, object]:
    return {
        "id": book_id,
        "title": "Current title",
        "canonical_id": canonical_id,
        "release_date": "2001-02-03",
        "image": {"url": f"https://assets.hardcover.app/book/{book_id}.jpg"},
        "contributions": [
            {"id": book_id, "contribution": None, "author": {"id": 7, "name": "Ada"}}
        ],
    }


def _hardcover(handler: _Handler) -> tuple[Any, list[httpx.Request]]:
    client, sent = _client(handler)
    return HardcoverMetadataProvider("catalogue", client, 262_144), sent


def test_hardcover_title_search_sends_the_books_document_unchanged() -> None:
    books = [_book(index) for index in range(1, 12)]
    provider, sent = _hardcover(lambda _: _json(books_envelope(books)))
    answer = _search(provider, TITLE)
    (request,) = sent
    assert request.method == "POST"
    assert str(request.url) == HARDCOVER_URL
    body = json.loads(request.content)
    assert body["operationName"] == "BooksByTitleAuthor"
    assert body["query"] == hardcover_metadata._BOOKS_QUERY
    assert "limit:11)" in body["query"]
    assert body["variables"]["authors"] == ["Ada"]
    assert not any(operator in body["query"] for operator in _PATTERN_OPERATORS)
    assert "authorization" not in {key.lower() for key in body}
    assert (len(answer.records), answer.more) == (10, True)
    assert answer.records[0].identity == "1"


def test_hardcover_isbn_search_keeps_its_own_limit_of_six() -> None:
    provider, sent = _hardcover(lambda _: _json(hardcover_envelope([], [_book(1)])))
    answer = _search(provider, BY_ISBN)
    (request,) = sent
    body = json.loads(request.content)
    assert body["operationName"] == "MetadataEvidenceByIsbn"
    assert body["query"] == hardcover_metadata._ISBN_QUERY
    assert body["variables"] == {"isbns": [ISBN]}
    assert "limit:6" in body["query"]
    assert not any(operator in body["query"] for operator in _PATTERN_OPERATORS)
    assert answer.records[0].isbn == ISBN


def test_hardcover_drops_a_book_merged_into_another() -> None:
    books = [_book(1), _book(2, canonical_id=1)]
    provider, _ = _hardcover(lambda _: _json(books_envelope(books)))
    answer = _search(provider, TITLE)
    assert [record.identity for record in answer.records] == ["1"]
    assert answer.dropped == 1


# -- Inventaire -------------------------------------------------------------


def _work(uri: str) -> dict[str, object]:
    return {
        "uri": uri,
        "claims": {"wdt:P1476": ["Current title"], "wdt:P50": ["wd:Q900"]},
        "labels": {"en": "Current title"},
    }


def _inventaire(handler: _Handler) -> tuple[Any, list[httpx.Request]]:
    client, sent = _client(handler)
    return InventaireMetadataProvider("catalogue", client, 262_144), sent


def _inventaire_answer(rows: int) -> _Handler:
    def answer(request: httpx.Request) -> httpx.Response:
        url = str(request.url.copy_with(query=None))
        if url == INVENTAIRE_SEARCH_URL:
            return _json(
                {
                    "results": [
                        {"type": "works", "uri": f"wd:Q{index}"}
                        for index in range(1, rows + 1)
                    ]
                }
            )
        uris = request.url.params.get_list("uris")
        if uris == ["wd:Q900"]:
            return _json(
                {"entities": {"wd:Q900": {"uri": "wd:Q900", "labels": {"en": "Ada"}}}}
            )
        return _json({"entities": {uri: _work(uri) for uri in uris}})

    return answer


def test_inventaire_title_search_is_one_search_one_read_and_one_labels_read() -> None:
    provider, sent = _inventaire(_inventaire_answer(11))
    answer = _search(provider, TITLE)
    search, works, labels = sent
    assert [request.method for request in sent] == ["GET"] * 3
    assert str(search.url.copy_with(query=None)) == INVENTAIRE_SEARCH_URL
    assert dict(search.url.params) == {
        "search": "Current title Ada",
        "types": "works",
        "limit": "11",
        "lang": "en",
    }
    assert str(works.url.copy_with(query=None)) == INVENTAIRE_ENTITIES_URL
    assert works.url.params.get_list("uris") == [f"wd:Q{i}" for i in range(1, 11)]
    assert labels.url.params.get_list("attributes") == ["labels"]
    assert labels.url.params.get_list("uris") == ["wd:Q900"]
    assert (len(answer.records), answer.more) == (10, True)
    assert answer.records[0].identity == "wd:Q1"


def test_inventaire_isbn_search_is_one_read_of_the_isbn() -> None:
    edition = {
        "uri": "inv:0123456789abcdef0123456789abcdef",
        "claims": {
            "wdt:P212": ["978-0-306-40615-7"],
            "wdt:P629": ["wd:Q1"],
            "wdt:P1476": ["Current title"],
        },
        "labels": {"en": "Current title"},
    }
    provider, sent = _inventaire(
        lambda _: _json(
            {
                "entities": {edition["uri"]: edition},
                "redirects": {f"isbn:{ISBN}": edition["uri"]},
            }
        )
    )
    answer = _search(provider, BY_ISBN)
    (request,) = sent
    assert request.url.params.get_list("uris") == [f"isbn:{ISBN}"]
    assert (answer.records[0].identity, answer.records[0].isbn) == ("wd:Q1", ISBN)


# -- Audible ----------------------------------------------------------------


def _product(index: int) -> dict[str, object]:
    return {
        "asin": f"B000TEST{index:02d}",
        "title": "Current title",
        "authors": [{"name": "Ada"}],
    }


def _audible(handler: _Handler) -> tuple[Any, list[httpx.Request]]:
    client, sent = _client(handler)
    provider = AudibleCatalogMetadataProvider(
        "catalogue", client, 262_144, marketplaces=("us", "uk")
    )
    return provider, sent


def test_audible_title_search_asks_eleven_products_at_the_chosen_marketplace() -> None:
    provider, sent = _audible(
        lambda _: _json({"products": [_product(index) for index in range(1, 12)]})
    )
    query = OwnerSearchQuery(
        "title_authors", title="Current title", authors=("Ada",), marketplace="uk"
    )
    answer = _search(provider, query)
    (request,) = sent
    assert request.method == "GET"
    assert str(request.url.copy_with(query=None)) == (
        "https://api.audible.co.uk/1.0/catalog/products"
    )
    params = dict(request.url.params)
    assert (params["num_results"], params["title"], params["author"]) == (
        "11",
        "Current title",
        "Ada",
    )
    assert (len(answer.records), answer.more) == (10, True)
    assert answer.records[0].identity == "B000TEST01"


def test_audible_asin_search_is_one_lookup_and_absence_is_no_cards() -> None:
    provider, sent = _audible(lambda _: _json({}, status=404))
    query = OwnerSearchQuery("asin", asin=ASIN, marketplace="us")
    answer = _search(provider, query)
    (request,) = sent
    assert str(request.url.copy_with(query=None)) == (
        f"https://api.audible.com/1.0/catalog/products/{ASIN}"
    )
    assert (answer.records, answer.failure, answer.requested) == ((), None, True)


# -- Every source -----------------------------------------------------------


_SOURCES: dict[str, Callable[[_Handler], tuple[Any, list[httpx.Request]]]] = {
    "open_library": _open_library,
    "google_books": _google,
    "hardcover": _hardcover,
    "inventaire": _inventaire,
    "audible_catalog": _audible,
}


@pytest.mark.parametrize("source", sorted(_SOURCES), ids=sorted(_SOURCES))
def test_one_attempt_and_no_retry_on_a_server_failure(source: str) -> None:
    provider, sent = _SOURCES[source](lambda _: _json({}, status=503))
    query = (
        OwnerSearchQuery(
            "title_authors", title="Current title", authors=("Ada",), marketplace="us"
        )
        if source == "audible_catalog"
        else TITLE
    )
    answer = _search(provider, query)
    assert len(sent) == 1
    assert answer.failure is OwnerSearchFailure.UNAVAILABLE
    assert answer.requested is True


@pytest.mark.parametrize("source", sorted(_SOURCES), ids=sorted(_SOURCES))
def test_a_rate_limit_answer_is_named_and_never_retried(source: str) -> None:
    provider, sent = _SOURCES[source](lambda _: _json({}, status=429))
    query = (
        OwnerSearchQuery(
            "title_authors", title="Current title", authors=("Ada",), marketplace="us"
        )
        if source == "audible_catalog"
        else TITLE
    )
    answer = _search(provider, query)
    assert len(sent) == 1
    assert answer.failure is OwnerSearchFailure.RATE_LIMITED


@pytest.mark.parametrize("source", sorted(_SOURCES), ids=sorted(_SOURCES))
def test_an_oversized_page_is_unreadable(source: str) -> None:
    rows: dict[str, object] = {
        "open_library": {"docs": [_ol_doc(1)] * 12},
        "google_books": {"items": [_volume(1)] * 12},
        "hardcover": books_envelope([_book(index) for index in range(1, 13)]),
        "inventaire": {"results": [{"type": "works", "uri": "wd:Q1"}] * 12},
        "audible_catalog": {"products": [_product(1)] * 12},
    }
    provider, _ = _SOURCES[source](lambda _: _json(rows[source]))
    query = (
        OwnerSearchQuery(
            "title_authors", title="Current title", authors=("Ada",), marketplace="us"
        )
        if source == "audible_catalog"
        else TITLE
    )
    answer = _search(provider, query)
    assert answer.failure is OwnerSearchFailure.UNREADABLE


_FIRST_PAGES: dict[str, object] = {
    "open_library": {"docs": [_ol_doc(1)]},
    "google_books": {"items": [_volume(1)]},
    "hardcover": books_envelope([_book(1)]),
    "inventaire": None,
    "audible_catalog": {"products": [_product(1)]},
}


@pytest.mark.parametrize("source", sorted(_SOURCES), ids=sorted(_SOURCES))
def test_every_source_projects_a_choosable_this_book_card(source: str) -> None:
    handler: _Handler = (
        _inventaire_answer(1)
        if source == "inventaire"
        else (lambda _: _json(_FIRST_PAGES[source]))
    )
    provider, _ = _SOURCES[source](handler)
    query = (
        OwnerSearchQuery(
            "title_authors", title="Current title", authors=("Ada",), marketplace="us"
        )
        if source == "audible_catalog"
        else TITLE
    )
    answer = _search(provider, query)
    work = OwnerSearchWork(
        "audiobook" if source == "audible_catalog" else "ebook",
        "Current title",
        ("Ada Example",),
        None,
        None,
        None,
        (),
        (),
    )
    cards, dropped = owner_search_cards(source, answer.records, work)
    assert dropped == 0
    (card,) = cards
    assert card.choosable
    assert OwnerLinkScope.WORK in card.scopes
    assert card.title.startswith("Current title")


@pytest.mark.parametrize(
    ("url", "admitted"),
    [
        ("https://m.media-amazon.com/images/I/cover.jpg", True),
        ("https://images.example.org/cover.jpg", False),
        ("http://m.media-amazon.com/images/I/cover.jpg", False),
    ],
    ids=["audible-cover-host", "other-host", "plain-http"],
)
def test_a_preview_url_is_only_one_the_source_admits(url: str, admitted: bool) -> None:
    product = {**_product(1), "product_images": {"500": url}}
    provider, _ = _audible(lambda _: _json({"products": [product]}))
    query = OwnerSearchQuery(
        "title_authors", title="Current title", authors=("Ada",), marketplace="us"
    )
    (record,) = _search(provider, query).records
    assert (record.cover is not None) is admitted
