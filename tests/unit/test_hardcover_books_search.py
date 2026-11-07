"""Hardcover's books-first WORK search (#1380, #1777): the one constant
document, its spelling variables, the ten-plus-sentinel limit, the
`canonical_id` collapse, the request-body cap, the narrowing retry rule and
the typed failures.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import cast

import httpx
import pytest

import mechanical_librarian.adapters.providers.hardcover_metadata as hardcover_metadata
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HARDCOVER_URL,
    HardcoverMetadataProvider,
)
from mechanical_librarian.application.metadata_report import (
    qualify_provider_observation,
)
from mechanical_librarian.domain.epub_inspection import DateEvent
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    ProviderCandidate,
    ProviderStatus,
)
from mechanical_librarian.infrastructure.security.credentials import (
    hardcover_client_token,
)
from tests.support.audiobook_providers import local_runtime_claim, work_plan
from tests.support.hardcover_evidence import (
    books_envelope,
    hardcover_answer,
    work_lookup,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

_DOCUMENT = (
    "query BooksByTitleAuthor($titles:[String!]!,$authors:[String!]!){"
    "books(where:{title:{_in:$titles},contributions:{author:{name:{_in:$authors}}}},"
    "order_by:{id:asc},limit:11)"
    "{id title canonical_id compilation description release_date image{url} "
    "contributions(order_by:{id:asc},limit:17){id contribution author{id name}} "
    "book_series(order_by:{id:asc},limit:17){id position series{id name}} "
    "taggings(order_by:{id:asc},limit:33){id tag_id}} "
    "tags:tags(where:{taggings:{book:{title:{_in:$titles},"
    "contributions:{author:{name:{_in:$authors}}}}}},order_by:{id:asc},limit:364)"
    "{id tag tag_category{id category}}}"
)
_PATTERN_OPERATORS = ("_ilike", "_like", "_regex", "_iregex", "_similar")


def _book(
    book_id: int,
    *,
    title: str = "Current title",
    author: str = "Ada Example",
    canonical_id: int | None = None,
) -> dict[str, object]:
    return {
        "id": book_id,
        "title": title,
        "canonical_id": canonical_id,
        "release_date": "2001-02-03",
        "image": {"url": f"https://assets.hardcover.app/book/{book_id}.jpg"},
        "contributions": [
            {
                "id": book_id,
                "contribution": None,
                "author": {"id": 7, "name": author},
            }
        ],
    }


def _provider(
    handler: object, **arguments: object
) -> tuple[HardcoverMetadataProvider, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return cast("httpx.Response", handler(request))  # type: ignore[operator]

    return (
        HardcoverMetadataProvider(
            "catalogue",
            httpx.Client(transport=httpx.MockTransport(record)),
            262_144,
            **arguments,  # type: ignore[arg-type]
        ),
        sent,
    )


def _answer(books: list[dict[str, object]]) -> object:
    return lambda request: httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json=hardcover_answer(request, [], books),
    )


def test_the_books_document_is_pinned_and_every_document_is_closed() -> None:
    assert _DOCUMENT == hardcover_metadata._BOOKS_QUERY
    documents = {
        name: value
        for name, value in vars(hardcover_metadata).items()
        if name.endswith("_QUERY") and type(value) is str
    }
    assert {"_AUDIO_EDITIONS_QUERY", "_BOOKS_QUERY", "_ISBN_QUERY", "_QUERY"} == set(
        documents
    )
    assert not hasattr(hardcover_metadata, "_WORK_QUERY")
    assert not hasattr(hardcover_metadata, "_WORK_NARROW_QUERY")
    for document in documents.values():
        assert not any(operator in document for operator in _PATTERN_OPERATORS)


def test_each_rung_sends_its_spellings_with_the_one_document() -> None:
    provider, sent = _provider(_answer([]))
    observation = provider.observe(
        work_lookup("the name of the WIND: A Tale", ("Kuang, R. F.",))
    ).observation

    assert ProviderStatus.NO_MATCH is observation.status
    assert {_DOCUMENT} == {body["query"] for body in sent}
    assert {"BooksByTitleAuthor"} == {body["operationName"] for body in sent}
    assert [
        {
            "titles": [
                "the name of the WIND: A Tale",
                "The Name Of The WIND: A Tale",
                "The Name Of The Wind: A Tale",
                "the name of the wind: a tale",
            ],
            "authors": ["Kuang, R. F.", "R. F. Kuang", "R.F. Kuang"],
        },
        {
            "titles": [
                "the name of the WIND",
                "The Name Of The WIND",
                "The Name Of The Wind",
                "the name of the wind",
            ],
            "authors": ["Kuang, R. F.", "R. F. Kuang", "R.F. Kuang"],
        },
        {
            "titles": [
                "the name of the WIND",
                "The Name Of The WIND",
                "The Name Of The Wind",
                "the name of the wind",
            ],
            "authors": ["Kuang R F", "Kuang R. F.", "Kuang R.F."],
        },
    ] == [body["variables"] for body in sent]


def test_an_ebook_book_keeps_todays_work_claims_and_states_no_first_publication() -> (
    None
):
    provider, _ = _provider(_answer([_book(10)]))
    lookup = work_lookup()
    observation = provider.observe(lookup).observation
    qualified = qualify_provider_observation(observation, lookup)

    assert ProviderStatus.CANDIDATES is qualified.status
    (book,) = observation.candidates
    assert ("hardcover-work-0000000010",) * 2 == (book.identity, book.work_identity)
    assert {ClaimScope.WORK} == {claim.scope for claim in book.claims}
    assert {ClaimKind.TITLE, ClaimKind.CREATOR} <= {claim.kind for claim in book.claims}
    assert not any(
        claim.date_event is DateEvent.WORK_FIRST_PUBLICATION for claim in book.claims
    )
    assert ("hardcover-work-0000000010",) == tuple(
        candidate.identity for candidate in qualified.candidates
    )


def test_an_audiobook_book_still_states_its_first_publication() -> None:
    provider, _ = _provider(
        lambda request: httpx.Response(
            200,
            json=(
                {"data": {"editions": []}}
                if json.loads(request.content)["operationName"] == "AudioEditions"
                else books_envelope(
                    [_book(10, title="Ember Tide Protocol", author="Corin Halloway")]
                )
            ),
        )
    )
    plan = work_plan(local_runtime_claim())
    observation = provider.observe(
        replace(plan, audio=single_track_audio_facts())
    ).observation

    assert any(
        claim.date_event is DateEvent.WORK_FIRST_PUBLICATION
        for candidate in observation.candidates
        for claim in candidate.claims
    )


@pytest.mark.parametrize(
    "books",
    (
        [_book(index) for index in range(1, 12)],
        [_book(index) for index in range(1, 7)],
    ),
    ids=("sentinel", "six_books"),
)
def test_the_sentinel_or_six_books_is_over_the_limit_with_nothing_kept(
    books: list[dict[str, object]],
) -> None:
    provider, sent = _provider(_answer(books))
    result = provider.observe(work_lookup())

    assert ProviderStatus.CANDIDATE_LIMIT_EXCEEDED is result.observation.status
    assert () == result.observation.candidates
    assert result.observation.edition_enrichment_overflow is None
    assert () == result.cover_descriptors
    # One stored author: the retry would send the list just sent.
    assert 1 == len(sent)


def test_seven_books_collapsing_to_five_are_kept_with_only_their_covers() -> None:
    books = [_book(index) for index in range(1, 6)]
    books += [_book(6, canonical_id=1), _book(7, canonical_id=2)]
    provider, _ = _provider(_answer(books))
    result = provider.observe(work_lookup())

    assert ProviderStatus.CANDIDATES is result.observation.status
    assert [f"hardcover-work-{index:010d}" for index in range(1, 6)] == [
        candidate.identity for candidate in result.observation.candidates
    ]
    provenances = [descriptor.provenance for descriptor in result.cover_descriptors]
    assert {f"hardcover-work-{index:010d}" for index in range(1, 6)} == {
        provenance.work_identity for provenance in provenances if provenance
    }
    assert all(provenances)


@pytest.mark.parametrize(
    "pointers",
    (
        {6: 6},
        {6: 1, 1: 6},
        {6: 99},
        {6: 5},
    ),
    ids=("self", "cycle", "outside", "refused_target"),
)
def test_a_pointer_the_collapse_does_not_trust_leaves_the_book_standing(
    pointers: dict[int, int],
) -> None:
    books = [_book(index, canonical_id=pointers.get(index)) for index in range(1, 7)]
    # Book 5 names another work, so it fails admission on its own values.
    books[4] = _book(5, title="Another work", canonical_id=pointers.get(5))
    provider, _ = _provider(_answer(books))

    observation = provider.observe(work_lookup()).observation

    assert ProviderStatus.CANDIDATE_LIMIT_EXCEEDED is observation.status


def test_a_pointer_is_followed_one_hop_only() -> None:
    # 6 names 1, which names 2: 1 joins 2, and 6 stands because its target
    # carries a pointer of its own.
    books = [
        _book(1, canonical_id=2),
        *(_book(index) for index in range(2, 6)),
        _book(6, canonical_id=1),
    ]
    provider, _ = _provider(_answer(books))

    observation = provider.observe(work_lookup()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert [f"hardcover-work-{index:010d}" for index in range(2, 7)] == [
        candidate.identity for candidate in observation.candidates
    ]


def test_the_collapse_removes_only_and_never_lends_values() -> None:
    def candidate(identity: str) -> ProviderCandidate:
        return ProviderCandidate(identity, identity, ())

    books = {
        1: (None, candidate("hardcover-work-0000000001")),
        2: (1, candidate("hardcover-work-0000000002")),
        3: (2, candidate("hardcover-work-0000000003")),
        4: (4, candidate("hardcover-work-0000000004")),
        5: (40, candidate("hardcover-work-0000000005")),
    }
    asked: list[str] = []

    def admits(value: ProviderCandidate) -> bool:
        asked.append(value.identity)
        return True

    kept = hardcover_metadata._collapse_canonical_books(books, admits)

    assert [books[index][1] for index in (1, 3, 4, 5)] == kept
    assert ["hardcover-work-0000000001"] == asked
    assert [books[1][1], books[2][1]] == hardcover_metadata._collapse_canonical_books(
        {1: books[1], 2: books[2]}, lambda _value: False
    )


@pytest.mark.parametrize(
    ("value", "rejections"),
    ((None, 0), (True, 0), (False, 0), ("yes", 1), (1, 1), (0, 1)),
)
def test_compilation_is_a_strict_boolean_member(value: object, rejections: int) -> None:
    provider, _ = _provider(_answer([{**_book(10), "compilation": value}]))
    observation = provider.observe(work_lookup()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert rejections == sum(
        diagnostic.count or 0 for diagnostic in observation.diagnostics
    )


def test_a_malformed_canonical_id_points_nowhere() -> None:
    books = [_book(1), {**_book(2), "canonical_id": "1"}]
    provider, _ = _provider(_answer(books))
    observation = provider.observe(work_lookup()).observation

    assert 2 == len(observation.candidates)
    assert 1 == sum(diagnostic.count or 0 for diagnostic in observation.diagnostics)


def test_an_answer_with_edition_rows_or_twelve_books_is_malformed() -> None:
    provider, _ = _provider(
        lambda _request: httpx.Response(
            200,
            json={"data": {"editions": [], "books": [], "tags": []}},
        )
    )
    assert (
        ProviderStatus.MALFORMED is provider.observe(work_lookup()).observation.status
    )
    provider, _ = _provider(
        lambda _request: httpx.Response(
            200, json=books_envelope([_book(index) for index in range(1, 13)])
        )
    )
    assert (
        ProviderStatus.MALFORMED is provider.observe(work_lookup()).observation.status
    )


def test_a_body_over_sixteen_kib_is_never_sent() -> None:
    provider, sent = _provider(_answer([]))
    authors = tuple(f"Author {index:03d} " + "x" * 480 for index in range(40))
    observation = provider.observe(work_lookup(authors=authors)).observation

    assert [] == sent
    assert ProviderStatus.QUERY_LIMIT_EXCEEDED is observation.status
    assert 0 == observation.request_accounting.initial_dispatches


def test_a_four_title_sixteen_author_body_is_sent() -> None:
    provider, sent = _provider(_answer([]))
    authors = tuple(f"Author{index:02d} " + "x" * 500 for index in range(16))
    title = "the title " + "x" * 490
    observation = provider.observe(work_lookup(title, authors)).observation

    assert ProviderStatus.NO_MATCH is observation.status
    assert sent and all(
        len(hardcover_metadata.canonical_json(body).encode()) <= 16_384 for body in sent
    )


def test_a_two_author_overflow_retries_with_the_first_author_alone() -> None:
    answers = [[_book(index) for index in range(1, 7)], []]
    provider, sent = _provider(
        lambda request: httpx.Response(
            200, json=hardcover_answer(request, [], answers.pop(0))
        )
    )
    observation = provider.observe(
        work_lookup(authors=("Ada Example", "Bea Example"))
    ).observation

    assert ProviderStatus.NO_MATCH is observation.status
    assert [["Ada Example", "Bea Example"], ["Ada Example"]] == [
        cast("dict[str, object]", body["variables"])["authors"] for body in sent
    ]
    assert sent[0]["query"] == sent[1]["query"] == _DOCUMENT
    assert (1, 1) == (
        observation.request_accounting.initial_dispatches,
        observation.request_accounting.retry_dispatches,
    )


def test_an_inverted_single_author_overflow_retries_without_its_forms() -> None:
    answers = [[_book(index) for index in range(1, 7)], []]
    provider, sent = _provider(
        lambda request: httpx.Response(
            200, json=hardcover_answer(request, [], answers.pop(0))
        )
    )
    provider.observe(work_lookup(authors=("Example, Ada",)))

    assert [["Example, Ada", "Ada Example"], ["Example, Ada"]] == [
        cast("dict[str, object]", body["variables"])["authors"] for body in sent
    ]


@pytest.mark.parametrize(
    ("response", "status"),
    (
        (
            httpx.Response(429, headers={"retry-after": "3"}),
            ProviderStatus.RATE_LIMITED,
        ),
        (httpx.Response(503), ProviderStatus.UNAVAILABLE),
        (httpx.Response(403), ProviderStatus.AUTHORIZATION),
    ),
    ids=("rate_limited", "unavailable", "forbidden"),
)
def test_a_failed_search_is_typed_and_never_a_no_match(
    response: httpx.Response, status: ProviderStatus
) -> None:
    provider, sent = _provider(
        lambda _request: response, clock=lambda: 0.0, sleep=lambda _seconds: None
    )
    observation = provider.observe(work_lookup(), deadline=2.0).observation

    assert status is observation.status
    # A 503 keeps its one declared transient retry; nothing else is resent.
    assert (2 if response.status_code == 503 else 1) == len(sent)
    assert {_DOCUMENT} == {body["query"] for body in sent}


def test_an_expired_deadline_stops_the_search_as_incomplete() -> None:
    provider, sent = _provider(_answer([]), clock=lambda: 10.0)
    observation = provider.observe(work_lookup(), deadline=1.0).observation

    assert [] == sent
    assert observation.status not in {
        ProviderStatus.NO_MATCH,
        ProviderStatus.CANDIDATES,
    }


def test_the_token_goes_only_to_the_graphql_post() -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.method, str(request.url), request.headers.get("authorization"))
        )
        if request.method != "POST":
            return httpx.Response(200, content=b"")
        return httpx.Response(200, json=hardcover_answer(request, [], []))

    client = hardcover_client_token(
        "synthetic-token", 5, transport=httpx.MockTransport(handler)
    )
    HardcoverMetadataProvider("catalogue", client, 65_536).observe(work_lookup())
    image = hardcover_client_token(
        "synthetic-token", 5, transport=httpx.MockTransport(handler)
    )
    image.get("https://assets.hardcover.app/book/10.jpg")

    assert ("POST", HARDCOVER_URL, "Bearer synthetic-token") == seen[0]
    assert ("GET", "https://assets.hardcover.app/book/10.jpg", None) == seen[-1]
