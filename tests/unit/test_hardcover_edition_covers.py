"""A found Hardcover book's edition covers reach cover review (#1380, #1778).

An ebook ISBN or WORK lane that accepts a Hardcover book reads up to two pages
of that book's edition list with one constant document, when the library lists
languages. Edition rows feed cover descriptors only: each passes a stated
size, shape, language and host pre-filter before any descriptor is built, and
every Hardcover cover is fetched from its one asset host with no redirect.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest

import mechanical_librarian.adapters.providers.hardcover_metadata as hardcover_metadata
from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HARDCOVER_URL,
    HardcoverMetadataProvider,
    HardcoverPacer,
)
from mechanical_librarian.application.metadata_report import (
    CoverRedirects,
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    ProviderBinding,
    ProviderCoverDescriptor,
    ProviderResult,
    binding_dispatch_plan,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceKind,
    CoverSourceState,
    CoverTruncationReason,
    cover_source_id,
)
from mechanical_librarian.domain.failure_diagnostics import FailureCategory
from mechanical_librarian.domain.metadata_report import (
    ProviderQueryKind,
    ProviderStatus,
)
from tests.support.audiobook_providers import local_runtime_claim, work_plan
from tests.support.hardcover_evidence import (
    books_envelope,
    hardcover_envelope,
    isbn_claim,
    title_claim,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

_DOCUMENT = (
    "query EditionCovers($book:Int!,$offset:Int!){editions(where:{book_id:{_eq:$book}},"
    "order_by:{id:asc},limit:100,offset:$offset){id book_id image{url width height} "
    "language{code2 code3}}}"
)
_ASSET = "https://assets.hardcover.app"


def _work_lane() -> MetadataDiscoveryLane:
    return MetadataDiscoveryLane(
        "lane_" + "1" * 64,
        ProviderQueryKind.WORK,
        (title_claim().reference,),
        title="Current title",
        authors=("Ada Example",),
    )


def _isbn_lane() -> MetadataDiscoveryLane:
    return MetadataDiscoveryLane(
        "lane_" + "2" * 64,
        ProviderQueryKind.ISBN,
        (isbn_claim().reference,),
        matched_family="9780306406157",
        canonical_isbns=("9780306406157",),
    )


def _plan(
    *lanes: MetadataDiscoveryLane, languages: tuple[str, ...] = ("en",)
) -> MetadataDiscoveryPlan:
    return MetadataDiscoveryPlan(
        (title_claim(), isbn_claim()),
        lanes or (_work_lane(),),
        library_languages=languages,
    )


def _book(
    book_id: int, *, author: bool = True, compilation: bool = False
) -> dict[str, object]:
    return {
        "id": book_id,
        "title": "Current title",
        "compilation": compilation,
        "image": {"url": f"{_ASSET}/books/{book_id}.jpg"},
        "contributions": (
            [
                {
                    "id": book_id,
                    "contribution": None,
                    "author": {"id": 7, "name": "Ada Example"},
                }
            ]
            if author
            else []
        ),
    }


def _row(
    edition_id: int,
    *,
    book: int = 10,
    width: object = 400,
    height: object = 600,
    code2: object = "en",
    code3: object = "eng",
    url: str | None = None,
) -> dict[str, object]:
    return {
        "id": edition_id,
        "book_id": book,
        "image": {
            "url": url or f"{_ASSET}/editions/{edition_id}.jpg",
            "width": width,
            "height": height,
        },
        "language": {"code2": code2, "code3": code3},
    }


def _page(*rows: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"data": {"editions": list(rows)}})


def _short(book: int, offset: int) -> httpx.Response:
    return _page(_row(book * 100 + 1 + offset, book=book))


def _observe(
    pages: Callable[[int, int], httpx.Response] = _short,
    plan: MetadataDiscoveryPlan | None = None,
    *,
    books: tuple[dict[str, object], ...] = (_book(10),),
    isbn_rows: list[dict[str, object]] | None = None,
    isbn_books: list[dict[str, object]] | None = None,
    pacer: HardcoverPacer | None = None,
    clock: Callable[[], float] = lambda: 0.0,
    deadline: float | None = None,
) -> tuple[ProviderResult, list[dict[str, object]]]:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body["operationName"] == "EditionCovers":
            return pages(body["variables"]["book"], body["variables"]["offset"])
        if body["operationName"] == "MetadataEvidenceByIsbn":
            return httpx.Response(
                200,
                json=hardcover_envelope(
                    isbn_rows or [], isbn_books or [dict(book) for book in books]
                ),
            )
        return httpx.Response(200, json=books_envelope([dict(book) for book in books]))

    result = HardcoverMetadataProvider(
        "hardcover",
        httpx.Client(transport=httpx.MockTransport(handler)),
        262_144,
        pacer=pacer,
        clock=clock,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    ).observe(plan or _plan(), deadline=deadline)
    return result, sent


def _isbn_books(
    pages: Callable[[int, int], httpx.Response] = _short,
    book_ids: tuple[int, ...] = (10, 11),
    **arguments: object,
) -> tuple[ProviderResult, list[dict[str, object]]]:
    """One ebook ISBN lane whose ISBN names editions of several books."""
    return _observe(
        pages,
        _plan(_isbn_lane()),
        isbn_rows=[
            {"id": index + 1, "book_id": book_id, "isbn_13": "9780306406157"}
            for index, book_id in enumerate(book_ids)
        ],
        isbn_books=[_book(book_id) for book_id in book_ids],
        **arguments,  # type: ignore[arg-type]
    )


def _edition_requests(sent: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        body["variables"]  # type: ignore[misc]
        for body in sent
        if body["operationName"] == "EditionCovers"
    ]


def _listed(result: ProviderResult) -> list[ProviderCoverDescriptor]:
    return [
        value
        for value in result.cover_descriptors
        if value.provenance is not None
        and value.provenance.designation is CoverDesignation.LISTED_EDITION
    ]


def _truncations(result: ProviderResult) -> set[CoverTruncationReason]:
    return {
        value.provenance.truncation_reason
        for value in result.cover_descriptors
        if value.provenance is not None
        and value.provenance.truncation_reason is not None
    }


def test_the_edition_document_is_pinned_with_the_book_and_offset() -> None:
    assert _DOCUMENT == hardcover_metadata._EDITION_COVERS_QUERY
    result, sent = _observe(lambda book, _offset: _page(_row(1001, book=book)))
    (body,) = [value for value in sent if value["operationName"] == "EditionCovers"]
    assert body == {
        "operationName": "EditionCovers",
        "query": _DOCUMENT,
        "variables": {"book": 10, "offset": 0},
    }
    assert [value.source_id for value in _listed(result)] == [
        cover_source_id(CoverSourceKind.HARDCOVER, "hardcover:listed:1001")
    ]


def test_a_full_page_reads_the_next_and_a_short_one_ends_the_book() -> None:
    def pages(book: int, offset: int) -> httpx.Response:
        if offset == 0:
            return _page(*(_row(index + 1, book=book) for index in range(100)))
        return _page(_row(101, book=book))

    result, sent = _observe(pages)

    assert [{"book": 10, "offset": 0}, {"book": 10, "offset": 100}] == (
        _edition_requests(sent)
    )
    assert 101 == len(_listed(result))
    assert set() == _truncations(result)


def test_two_full_pages_record_an_edition_limit_and_keep_no_cover() -> None:
    def pages(book: int, offset: int) -> httpx.Response:
        if book == 10:
            return _page(*(_row(offset + index + 1, book=book) for index in range(100)))
        return _page(_row(2001, book=book))

    result, sent = _isbn_books(pages)

    assert [
        {"book": 10, "offset": 0},
        {"book": 10, "offset": 100},
        {"book": 11, "offset": 0},
    ] == _edition_requests(sent)
    assert [value.provenance.work_identity for value in _listed(result)] == [  # type: ignore[union-attr]
        "hardcover-work-0000000011"
    ]
    assert {CoverTruncationReason.EDITION_LIMIT} == _truncations(result)


def test_a_third_book_is_an_edition_limit_and_sends_nothing() -> None:
    result, sent = _isbn_books(book_ids=(10, 11, 12))

    assert [10, 11] == [value["book"] for value in _edition_requests(sent)]
    assert {CoverTruncationReason.EDITION_LIMIT} == _truncations(result)
    assert 2 == len(_listed(result))


def test_a_work_lane_compilation_book_is_not_fanned_out() -> None:
    result, sent = _observe(books=(_book(10, compilation=True),))

    assert ProviderStatus.CANDIDATES is result.observation.status
    assert [] == _edition_requests(sent)
    assert [] == _listed(result)


@pytest.mark.parametrize(
    ("work_book", "requested"),
    ((10, [10]), (11, [10, 11])),
    ids=("same_book", "another_book"),
)
def test_books_are_fanned_out_once_across_lanes_isbn_lane_first(
    work_book: int, requested: list[int]
) -> None:
    result, sent = _observe(
        plan=_plan(_work_lane(), _isbn_lane()),
        books=(_book(work_book),),
        isbn_rows=[{"id": 1, "book_id": 10, "isbn_13": "9780306406157"}],
        isbn_books=[_book(10, author=False)],
    )

    assert ["MetadataEvidenceByIsbn", "EditionCovers", "BooksByTitleAuthor"] == [
        body["operationName"] for body in sent[:3]
    ]
    assert requested == [value["book"] for value in _edition_requests(sent)]
    assert {f"hardcover-work-{book:010d}" for book in requested} == {
        value.provenance.work_identity  # type: ignore[union-attr]
        for value in _listed(result)
    }


def _other_book_row(_book_id: int, _offset: int) -> httpx.Response:
    return _page(_row(2001, book=99))


def _duplicate_across_pages(book: int, offset: int) -> httpx.Response:
    if offset == 0:
        return _page(*(_row(index + 1, book=book) for index in range(100)))
    return _page(_row(1, book=book))


def _extra_member(book: int, _offset: int) -> httpx.Response:
    return _page({**_row(2001, book=book), "isbn_13": "9780306406157"})


def _graphql_errors(_book: int, _offset: int) -> httpx.Response:
    return httpx.Response(200, json={"errors": [{"message": "synthetic failure"}]})


def _rate_limited(_book: int, _offset: int) -> httpx.Response:
    return httpx.Response(429, headers={"retry-after": "1"}, json={})


def _unavailable(_book: int, _offset: int) -> httpx.Response:
    return httpx.Response(503, json={})


@pytest.mark.parametrize(
    "failure",
    (
        _other_book_row,
        _duplicate_across_pages,
        _extra_member,
        _graphql_errors,
        _rate_limited,
        _unavailable,
    ),
    ids=(
        "row_naming_another_book",
        "duplicate_edition_id",
        "extra_member",
        "graphql_errors",
        "rate_limited",
        "unavailable",
    ),
)
def test_a_failed_or_malformed_page_ends_the_fan_out_as_incomplete(
    failure: Callable[[int, int], httpx.Response],
) -> None:
    def pages(book: int, offset: int) -> httpx.Response:
        return _short(book, offset) if book == 10 else failure(book, offset)

    result, sent = _isbn_books(pages, (10, 11, 12))

    assert ProviderStatus.CANDIDATES is result.observation.status
    assert {
        "hardcover-edition-0000000001",
        "hardcover-edition-0000000002",
        "hardcover-edition-0000000003",
    } == {candidate.identity for candidate in result.observation.candidates}
    assert ["hardcover-work-0000000010"] == [
        value.provenance.work_identity  # type: ignore[union-attr]
        for value in _listed(result)
    ]
    assert {CoverTruncationReason.EDITION_FETCH_INCOMPLETE} == _truncations(result)
    assert 12 not in [value["book"] for value in _edition_requests(sent)]


def test_a_deadline_stop_ends_the_fan_out_as_incomplete() -> None:
    now = [0.0]

    def pages(book: int, offset: int) -> httpx.Response:
        if book == 11:
            now[0] = 50.0
        return _short(book, offset)

    result, sent = _isbn_books(pages, clock=lambda: now[0], deadline=40.0)

    assert ProviderStatus.CANDIDATES is result.observation.status
    assert [10, 11] == [value["book"] for value in _edition_requests(sent)]
    assert ["hardcover-work-0000000010"] == [
        value.provenance.work_identity  # type: ignore[union-attr]
        for value in _listed(result)
    ]
    assert {CoverTruncationReason.EDITION_FETCH_INCOMPLETE} == _truncations(result)


def test_a_library_with_no_languages_sends_no_edition_request() -> None:
    result, sent = _observe(plan=_plan(languages=()))

    assert [] == _edition_requests(sent)
    assert [] == _listed(result)
    assert set() == _truncations(result)


def test_an_audiobook_plan_never_asks_for_edition_covers() -> None:
    plan = replace(
        work_plan(
            local_runtime_claim(), title="Current title", authors=("Ada Example",)
        ),
        audio=single_track_audio_facts(),
        library_languages=("en",),
    )
    _result, sent = _observe(plan=plan)

    assert "EditionCovers" not in {body["operationName"] for body in sent}


def _covered(row: dict[str, object], *, audio: bool = False) -> bool:
    return (
        hardcover_metadata._listed_edition_cover(
            "hardcover-work-0000000010",
            row,
            audio=audio,
            library_languages=("en",),
            rejected=hardcover_metadata._OptionalRejections(),
        )
        is not None
    )


@pytest.mark.parametrize(
    ("changes", "kept"),
    (
        ({}, True),
        ({"width": 0}, False),
        ({"width": 8_193, "height": 12_000}, False),
        ({"width": 3_000, "height": 5_600}, False),
        ({"width": True}, False),
        ({"width": "400"}, False),
        ({"width": 299, "height": 450}, False),
        ({"width": 300, "height": 450}, True),
        ({"width": 600, "height": 600}, False),
        ({"width": 1_000, "height": 1_190}, False),
        ({"width": 1_000, "height": 1_200}, True),
        ({"width": 1_000, "height": 2_000}, True),
        ({"width": 1_000, "height": 2_010}, False),
        ({"code2": None, "code3": None}, False),
        ({"code2": "de", "code3": "deu"}, False),
        ({"code2": None, "code3": "eng"}, True),
    ),
    ids=(
        "portrait_in_library_language",
        "width_zero",
        "width_over_8192",
        "product_over_16777216",
        "boolean_width",
        "text_width",
        "short_edge_299",
        "short_edge_300",
        "ebook_square",
        "ratio_1_19",
        "ratio_1_2",
        "ratio_2_0",
        "ratio_2_01",
        "no_language",
        "other_language",
        "library_language_by_code3",
    ),
)
def test_the_ebook_pre_filter(changes: dict[str, object], kept: bool) -> None:
    assert _covered(_row(5, **changes)) is kept  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("width", "height", "kept"),
    ((600, 600, True), (600, 630, True), (600, 631, False), (400, 600, False)),
    ids=("square", "ratio_1_05", "ratio_over_1_05", "portrait"),
)
def test_the_audiobook_pre_filter_keeps_square_covers(
    width: int, height: int, kept: bool
) -> None:
    assert _covered(_row(5, width=width, height=height), audio=True) is kept


def test_an_off_host_listed_row_is_dropped_and_counted() -> None:
    result, _sent = _observe(
        lambda book, _offset: _page(
            _row(1001, book=book, url="https://images.example/1001.jpg"),
            _row(1002, book=book),
        )
    )

    assert [cover_source_id(CoverSourceKind.HARDCOVER, "hardcover:listed:1002")] == [
        value.source_id for value in _listed(result)
    ]
    assert [
        (diagnostic.category, diagnostic.count)
        for diagnostic in result.observation.diagnostics
    ] == [(FailureCategory.SCHEMA_CONSTRUCTION, 1)]


def test_a_listed_url_another_hardcover_cover_holds_is_skipped() -> None:
    result, _sent = _observe(
        lambda book, _offset: _page(
            _row(1001, book=book, url=f"{_ASSET}/books/{book}.jpg"),
            _row(1003, book=book, url=f"{_ASSET}/shared.jpg"),
            _row(1002, book=book, url=f"{_ASSET}/shared.jpg"),
        )
    )

    assert [cover_source_id(CoverSourceKind.HARDCOVER, "hardcover:listed:1002")] == [
        value.source_id for value in _listed(result)
    ]


def test_listed_covers_are_ordered_by_short_edge_area_and_edition() -> None:
    result, _sent = _observe(
        lambda book, _offset: _page(
            _row(1001, book=book, width=400, height=600),
            _row(1002, book=book, width=500, height=750),
            _row(1003, book=book, width=500, height=800),
            _row(1004, book=book, width=400, height=600),
        )
    )

    ordered = sorted(_listed(result), key=lambda value: value.order)
    assert [1003, 1002, 1001, 1004] == [
        int(value.provenance.edition_identity.removeprefix("hardcover-edition-"))  # type: ignore[union-attr]
        for value in ordered
    ]
    assert [0, 1, 2, 3] == [value.order for value in ordered]


def test_a_listed_provenance_carries_the_language_and_no_size() -> None:
    result, _sent = _observe(
        lambda book, _offset: _page(_row(1001, book=book, code2=None, code3="eng"))
    )

    (descriptor,) = _listed(result)
    assert descriptor.provenance is not None
    assert descriptor.provenance.to_data() == {
        "context": [{"field": "language", "value": "eng"}],
        "designation": "listed_edition",
        "edition_identity": "hardcover-edition-0000001001",
        "provider": "hardcover",
        "truncation_reason": None,
        "work_identity": "hardcover-work-0000000010",
    }
    assert descriptor.url == f"{_ASSET}/editions/1001.jpg"


def test_every_hardcover_cover_refuses_redirects_and_paces_on_the_asset_host() -> None:
    pacer = HardcoverPacer(clock=lambda: 0.0, sleep=lambda _seconds: None)
    result, _sent = _observe(
        plan=_plan(_isbn_lane()),
        isbn_rows=[
            {
                "id": 1,
                "book_id": 10,
                "isbn_13": "9780306406157",
                "image": {"url": f"{_ASSET}/editions/1.jpg"},
            }
        ],
        pacer=pacer,
    )

    designations = {
        value.provenance.designation  # type: ignore[union-attr]
        for value in result.cover_descriptors
    }
    assert {
        CoverDesignation.EXACT_EDITION,
        CoverDesignation.DEFAULT,
        CoverDesignation.LISTED_EDITION,
    } == designations
    assert {CoverRedirects.NONE} == {
        value.redirects for value in result.cover_descriptors
    }
    assert all(value.wait == pacer.asset_wait for value in result.cover_descriptors)


def test_without_a_pacer_a_hardcover_cover_waits_on_nothing() -> None:
    result, _sent = _observe()

    assert {None} == {value.wait for value in result.cover_descriptors}


def test_the_asset_pacer_keeps_its_own_one_second_clock() -> None:
    now = [0.0]
    delays: list[float] = []

    def sleep(delay: float) -> None:
        delays.append(delay)
        now[0] += delay

    pacer = HardcoverPacer(clock=lambda: now[0], sleep=sleep)
    assert pacer.wait(None)
    assert pacer.asset_wait(None)
    assert [] == delays
    assert pacer.asset_wait(None)
    assert [1.0] == delays
    assert not pacer.asset_wait(now[0] + 0.5)


@pytest.mark.parametrize(
    ("url", "allowed"),
    (
        (f"{_ASSET}/editions/1.jpg", True),
        ("https://assets.hardcover.app:443/editions/1.jpg", True),
        ("https://ASSETS.hardcover.app/editions/1.jpg", True),
        ("https://images.example/editions/1.jpg", False),
        ("https://assets.hardcover.app.example/1.jpg", False),
        ("https://assets.hardcover.app:8443/editions/1.jpg", False),
        ("https://user@assets.hardcover.app/editions/1.jpg", False),
        ("http://assets.hardcover.app/editions/1.jpg", False),
    ),
    ids=(
        "asset_host",
        "explicit_443",
        "upper_case_host",
        "other_host",
        "suffix_host",
        "other_port",
        "userinfo",
        "plain_http",
    ),
)
def test_a_hardcover_cover_is_admitted_only_from_the_asset_host(
    url: str, allowed: bool
) -> None:
    result, _sent = _observe(
        plan=_plan(_isbn_lane(), languages=()),
        isbn_rows=[
            {"id": 1, "book_id": 10, "isbn_13": "9780306406157", "image": {"url": url}}
        ],
    )

    (exact,) = [
        value
        for value in result.cover_descriptors
        if value.provenance is not None
        and value.provenance.designation is CoverDesignation.EXACT_EDITION
    ]
    if allowed:
        assert (exact.url, exact.terminal_state) == (url, None)
    else:
        assert (exact.url, exact.terminal_state) == (
            None,
            CoverSourceState.UNSAFE_REMOTE,
        )


def test_an_edition_request_carries_the_token_only_to_the_api() -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.method, str(request.url), request.headers.get("authorization"))
        )
        body = json.loads(request.content)
        if body["operationName"] == "EditionCovers":
            return _short(body["variables"]["book"], body["variables"]["offset"])
        return httpx.Response(200, json=books_envelope([_book(10)]))

    HardcoverMetadataProvider(
        "hardcover",
        httpx.Client(
            transport=httpx.MockTransport(handler),
            headers={"authorization": "Bearer synthetic-token"},
        ),
        262_144,
        clock=lambda: 0.0,
        sleep=lambda _seconds: None,
        jitter=lambda: 0.0,
    ).observe(_plan())

    assert {("POST", HARDCOVER_URL, "Bearer synthetic-token")} == set(seen)
    assert 2 == len(seen)


@pytest.mark.parametrize(
    "authority,cover_enabled,languages",
    (
        ("hardcover", True, ("en",)),
        ("hardcover", False, ()),
        ("google_books", False, ("en",)),
    ),
)
def test_hardcover_with_its_covers_off_is_handed_no_library_languages(
    authority: str, cover_enabled: bool, languages: tuple[str, ...]
) -> None:
    # Library languages only filter Hardcover's edition covers, so with its
    # covers off it is handed none and asks for no edition cover (#1778).
    binding = ProviderBinding(
        authority,
        True,
        lambda: pytest.fail("built"),
        authority=authority,
        cover_enabled=cover_enabled,
    )
    handed = binding_dispatch_plan(binding, _plan(), None)
    assert languages == handed.library_languages
    assert _plan().lanes == handed.lanes


def test_hardcover_with_its_covers_off_sends_no_edition_request() -> None:
    plan = binding_dispatch_plan(
        ProviderBinding(
            "hardcover",
            True,
            lambda: pytest.fail("built"),
            authority="hardcover",
            cover_enabled=False,
        ),
        _plan(),
        None,
    )
    _result, sent = _observe(plan=plan)
    assert sent
    # The same plan with its languages does ask, so this cannot pass vacuously.
    assert any(body["operationName"] == "EditionCovers" for body in _observe()[1])
    assert all(body["operationName"] != "EditionCovers" for body in sent)
