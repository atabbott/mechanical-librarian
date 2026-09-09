from __future__ import annotations

import gzip
import tempfile
import threading
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import httpx
import pytest

import mechanical_librarian.adapters.providers.open_library_metadata as open_library_metadata
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OPEN_LIBRARY_COVERS_ORIGIN,
    OPEN_LIBRARY_SEARCH_URL,
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderObservationFailure,
    TargetSnapshot,
    metadata_discovery_plan,
    qualify_provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverProvenance,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.epub_inspection import DatePrecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderEvidenceKind,
    ProviderQueryKind,
    ProviderStatus,
    PublicationDateRelation,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
    isbn_family,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchRung,
    WorkTitleTransformationFamily,
)
from mechanical_librarian.infrastructure.security.credentials import (
    open_library_client,
)


class _InterruptedStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b"x"
        raise httpx.ReadTimeout("synthetic interrupted body")


class _HostileEncodedStream(httpx.SyncByteStream):
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __iter__(self):
        yield self._payload


def _isbn(
    value: str = "9780306406157",
    *,
    applicability: str = "ebook",
) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed=value,
        canonical=value,
        excerpt=f"ISBN: {value}",
        applicability=applicability,
        checksum="valid_isbn_10" if len(value) == 10 else "valid_isbn_13",
        applicability_basis=applicability,
        scope=ClaimScope.EDITION,
    )


def _isbn_lookup(claims: tuple[LocalClaim, ...]) -> MetadataLookup:
    return MetadataLookup(ProviderQueryKind.ISBN, claims)


def _work_lookup() -> MetadataLookup:
    return MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Local work title",
        ("Ada Example",),
    )


def _identifier_first_plan() -> MetadataDiscoveryPlan:
    claim = _isbn()
    return MetadataDiscoveryPlan(
        (claim,),
        (
            MetadataDiscoveryLane(
                "lane_" + "1" * 64,
                ProviderQueryKind.WORK,
                (claim.reference,),
                title="Local work title",
                authors=("Ada Example",),
            ),
            MetadataDiscoveryLane(
                "lane_" + "2" * 64,
                ProviderQueryKind.ISBN,
                (claim.reference,),
                matched_family="9780306406157",
                canonical_isbns=("9780306406157",),
            ),
        ),
    )


def test_identifier_no_match_falls_back_to_open_library_discovery() -> None:
    queries: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params.get("q"))
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"numFound": 0, "docs": []},
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_identifier_first_plan())
        .observation
    )

    assert "isbn:9780306406157" == queries[0]
    assert 2 == len(queries)
    assert {ProviderQueryKind.ISBN, ProviderQueryKind.WORK} == {
        lane.kind for lane in observation.lanes
    }
    assert 2 == observation.request_accounting.logical_requests


def test_identifier_candidate_without_work_identity_preserves_open_library_discovery() -> (
    None
):
    queries: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search.json":
            queries.append(request.url.params.get("q"))
            value = _search("/books/OL1M")
        elif request.url.path == "/books/OL1M.json":
            value = _edition("/books/OL1M")
        else:
            assert "/works/OL5W.json" == request.url.path
            value = _work()
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=value,
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_identifier_first_plan())
        .observation
    )

    assert "isbn:9780306406157" == queries[0]
    assert 2 == len(queries)
    assert {ProviderQueryKind.ISBN, ProviderQueryKind.WORK} == {
        lane.kind for lane in observation.lanes
    }
    assert 6 == observation.request_accounting.logical_requests


def test_malformed_identifier_falls_back_to_open_library_discovery() -> None:
    queries: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params.get("q"))
        if len(queries) == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b"{",
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"numFound": 0, "docs": []},
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_identifier_first_plan())
        .observation
    )

    assert "isbn:9780306406157" == queries[0]
    assert 2 == len(queries)
    assert {ProviderQueryKind.ISBN, ProviderQueryKind.WORK} == {
        lane.kind for lane in observation.lanes
    }


def test_work_query_ladder_is_bounded_and_accounted_as_retries() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": []},
        )

    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Local work title: Subtitle",
        ("Ada & Bea",),
    )
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(lookup)
        .observation
    )

    assert ProviderStatus.NO_MATCH is observation.status
    assert 3 == len(requests)
    assert "Local work title: Subtitle" == requests[0].url.params["title"]
    assert "Local work title" == requests[1].url.params["title"]
    assert "Ada and Bea" == requests[2].url.params["author"]
    assert 1 == observation.request_accounting.initial_dispatches
    assert 2 == observation.request_accounting.retry_dispatches


def test_latin_fold_reaches_open_library_only_on_final_bounded_search() -> None:
    requests: list[httpx.Request] = []
    search_count = 0
    work = _work()
    work["title"] = "Cafe: Notes"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_count
        requests.append(request)
        if request.url.path == "/search.json":
            search_count += 1
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=(
                    {"docs": []}
                    if search_count < 3
                    else {
                        "docs": [
                            {
                                "key": "/works/OL5W",
                                "title": "Cafe: Notes",
                                "author_name": ["Jose"],
                                "author_key": ["OL7A"],
                                "edition_key": ["OL1M"],
                                "editions": {"docs": [{"key": "/books/OL1M"}]},
                            }
                        ]
                    }
                ),
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=(
                _edition("/books/OL1M")
                if request.url.path == "/books/OL1M.json"
                else work
            ),
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(
            MetadataLookup(
                ProviderQueryKind.WORK,
                (),
                "Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes",
                ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",),
            )
        )
        .observation
    )

    searches = [request for request in requests if request.url.path == "/search.json"]
    assert [
        (
            "Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes",
            "Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        ),
        (
            "Caf\N{LATIN SMALL LETTER E WITH ACUTE}",
            "Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        ),
        ("Cafe", "Jose"),
    ] == [
        (request.url.params["title"], request.url.params["author"])
        for request in searches
    ]
    assert observation.dispatch_lineage is not None
    assert observation.dispatch_lineage.rung is WorkDispatchRung.PUNCTUATION_FOLDED
    assert observation.dispatch_lineage.transformations[-1] is (
        WorkTitleTransformationFamily.LATIN_COMBINING_MARKS
    )
    assert 3 == observation.request_accounting.logical_requests
    assert (3, 2) == (
        observation.request_accounting.initial_dispatches,
        observation.request_accounting.retry_dispatches,
    )


@pytest.mark.parametrize(
    "title",
    ("[retail]", "(1)", "(2010)", "ISBN 9780306406157", "2nd Edition", "Book 2"),
)
def test_decoration_only_work_query_is_insufficient_without_request(title: str) -> None:
    requests: list[httpx.Request] = []
    lookup = MetadataLookup(ProviderQueryKind.WORK, (), title, ("Ada Example",))

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(lookup)
        .observation
    )

    assert ProviderStatus.INSUFFICIENT_INPUT is observation.status
    assert [] == requests
    assert (0, 0) == (
        observation.request_accounting.initial_dispatches,
        observation.request_accounting.retry_dispatches,
    )


def test_filename_author_prefix_shapes_open_library_query_but_never_admits() -> None:
    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (),
        "Ada_Example_-_Plain Work",
        ("Ada Example",),
    )
    work = _work()
    work["title"] = "Plain Work"
    values = {
        "/search.json": {
            "docs": [
                {
                    "key": "/works/OL5W",
                    "title": "Plain Work",
                    "author_name": ["Ada Example"],
                    "author_key": ["OL7A"],
                    "edition_key": ["OL1M"],
                    "editions": {"docs": [{"key": "/books/OL1M"}]},
                }
            ]
        },
        "/books/OL1M.json": _edition("/books/OL1M"),
        "/works/OL5W.json": work,
    }
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=values[request.url.path],
                    )
                )
            ),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(lookup)
        .observation
    )
    qualified = qualify_provider_observation(observation, lookup)

    assert ProviderStatus.CANDIDATES is observation.status
    assert observation.dispatch_lineage is not None
    assert WorkTitleTransformationFamily.FILENAME_AUTHOR_TITLE_PREFIX in (
        observation.dispatch_lineage.transformations
    )
    assert ProviderStatus.NO_MATCH is qualified.status
    assert qualified.transformation_lineage is None


def test_candidate_limit_gets_one_open_library_phrase_retry() -> None:
    requests: list[httpx.Request] = []
    docs = [
        {
            "key": f"/works/OL{index}W",
            "author_name": ["Ada Example"],
            "author_key": ["OL7A"],
            "edition_key": [f"OL{index}M"],
            "editions": {"docs": [{"key": f"/books/OL{index}M"}]},
        }
        for index in range(1, 4)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": docs if len(requests) == 1 else []},
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_work_lookup())
        .observation
    )

    assert ProviderStatus.NO_MATCH is observation.status
    assert 2 == len(requests)
    assert '"Local work title" author:"Ada Example"' == requests[1].url.params["q"]
    assert 1 == observation.request_accounting.retry_dispatches


def _title() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Local work title",
        canonical="Local work title",
        excerpt="Local work title",
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )


def _search(*editions: str, work: str = "/works/OL5W") -> dict[str, object]:
    return {
        "docs": [
            {
                "key": work,
                "editions": {
                    "docs": [
                        {
                            "key": edition,
                            "isbn": ["0306406152", "9780306406157"],
                        }
                        for edition in editions
                    ]
                },
            }
        ]
    }


def _edition(
    key: str,
    *,
    work: str = "/works/OL5W",
    cover: int | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "key": key,
        "works": [{"key": work}],
        "isbn_10": ["0306406152"],
        "isbn_13": ["9780306406157"],
        "title": "Edition title",
        "subtitle": "Edition subtitle",
        "publish_date": "2026-01-02",
        "publishers": ["Example Press"],
        "languages": [{"key": "/languages/eng"}],
        "authors": [{"key": "/authors/OL7A"}],
        "revision": 3,
        "last_modified": {
            "type": "/type/datetime",
            "value": "2026-01-02T03:04:05.000000",
        },
        "oclc_numbers": ["12345"],
        "lccn": ["2026123456"],
        "dewey_decimal_class": ["813.6"],
        "lc_classifications": ["PS3605.X36"],
        "publish_places": ["London"],
        "identifiers": {"wikidata": ["Q123"]},
        "physical_format": "eBook",
        "number_of_pages": 321,
        "pagination": "321 pages",
        "copyright_date": "2026",
    }
    if cover is not None:
        value["covers"] = [cover]
    return value


def _work(key: str = "/works/OL5W", *, cover: int | None = None) -> dict[str, object]:
    value: dict[str, object] = {
        "key": key,
        "title": "Local work title",
        "description": {"type": "/type/text", "value": "Description"},
        "authors": [
            {
                "author": {"key": "/authors/OL7A"},
                "type": {"key": "/type/author_role"},
            }
        ],
        "revision": 4,
        "last_modified": {
            "type": "/type/datetime",
            "value": "2026-01-03T03:04:05.000000",
        },
        "subjects": ["Speculative fiction"],
        "subject_people": ["Example Person"],
        "subject_places": ["Example Place"],
        "subject_times": ["Near future"],
        "identifiers": {"wikidata": ["Q456"]},
        "first_publish_date": "2025-12-31",
    }
    if cover is not None:
        value["covers"] = [cover]
    return value


def test_pacer_serializes_concurrent_callers() -> None:
    active = 0
    maximum = 0
    guard = threading.Lock()
    barrier = threading.Barrier(3)

    def sleep(_: float) -> None:
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with guard:
            active -= 1

    pacer = OpenLibraryPacer(clock=lambda: 0.0, sleep=sleep)
    assert pacer.wait()

    def wait() -> None:
        barrier.wait()
        assert pacer.wait()

    threads = [threading.Thread(target=wait) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert maximum == 1


def test_detail_schema_diagnostics_are_context_local_and_replay_exact() -> None:
    edition = _edition("/books/OL1M")
    edition["subjects"] = ["wrong edition subject"]
    work = _work()
    work["publishers"] = ["wrong work publisher"]
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": edition,
        "/works/OL5W.json": work,
    }
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=values[request.url.path],
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES == observation.status
    assert 2 == observation.diagnostics[0].count
    retained = {
        value
        for candidate in observation.candidates
        for claim in candidate.claims
        for value in (claim.value,)
        if value is not None
    } | {
        evidence.value
        for candidate in observation.candidates
        for evidence in candidate.evidence
    }
    assert "wrong edition subject" not in retained
    assert "wrong work publisher" not in retained
    assert all(
        context.value not in {"wrong edition subject", "wrong work publisher"}
        for descriptor in provider_result.cover_descriptors
        if descriptor.provenance is not None
        for context in descriptor.provenance.context
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Current title"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(_isbn(),),
        local_reasons=("not_applicable",),
        providers=(observation,),
    )
    replay = MetadataReport.from_json(report.to_json())
    assert observation.diagnostics == replay.providers[0].diagnostics
    assert 0 == open_library_metadata._unknown_projection_rejections(
        _edition("/books/OL1M"),
        schema="edition_detail",
    )
    assert 0 == open_library_metadata._unknown_projection_rejections(
        _work(),
        schema="work_detail",
    )


_SERIES_AGGREGATE_CASES: tuple[
    tuple[
        str,
        list[object],
        list[str],
        tuple[tuple[ClaimKind, FieldState, str | None], ...],
    ],
    ...,
] = (
    (
        "full",
        ["Cycle"],
        ["2"],
        (
            (ClaimKind.SERIES, FieldState.VALID, "Cycle"),
            (ClaimKind.SERIES_SEQUENCE, FieldState.VALID, "2"),
        ),
    ),
    (
        "multiple full",
        ["Cycle", "Publication order"],
        ["2", "5"],
        (
            (ClaimKind.SERIES, FieldState.VALID, "Cycle"),
            (ClaimKind.SERIES_SEQUENCE, FieldState.VALID, "2"),
            (
                ClaimKind.SERIES,
                FieldState.VALID,
                "Publication order",
            ),
            (ClaimKind.SERIES_SEQUENCE, FieldState.VALID, "5"),
        ),
    ),
    (
        "series only",
        ["Cycle"],
        [],
        ((ClaimKind.SERIES, FieldState.VALID, "Cycle"),),
    ),
    (
        "absent",
        [],
        [],
        (
            (ClaimKind.SERIES, FieldState.UNSUPPORTED, None),
            (
                ClaimKind.SERIES_SEQUENCE,
                FieldState.UNSUPPORTED,
                None,
            ),
        ),
    ),
    (
        "malformed",
        ["Cycle", 7],
        ["2"],
        (
            (ClaimKind.SERIES, FieldState.VALID, "Cycle"),
            (ClaimKind.SERIES_SEQUENCE, FieldState.VALID, "2"),
            (ClaimKind.SERIES, FieldState.MALFORMED, None),
            (
                ClaimKind.SERIES_SEQUENCE,
                FieldState.MALFORMED,
                None,
            ),
        ),
    ),
    (
        "oversized",
        [f"Cycle {index}" for index in range(9)],
        [],
        (
            (ClaimKind.SERIES, FieldState.OVERSIZED, None),
            (
                ClaimKind.SERIES_SEQUENCE,
                FieldState.OVERSIZED,
                None,
            ),
        ),
    ),
)


@pytest.mark.parametrize(
    ("label", "series", "sequences", "expected"),
    _SERIES_AGGREGATE_CASES,
)
def test_series_aggregate_states_do_not_mix_valid_and_unsupported(
    label, series, sequences, expected
) -> None:
    work = _work()
    work["series"] = series
    work["series_number"] = sequences
    candidate, _, _ = open_library_metadata._candidate(
        "open_library",
        "/books/OL1M",
        "/works/OL5W",
        _edition("/books/OL1M"),
        work,
        (_isbn(),),
        "9780306406157",
    )
    actual = tuple(
        (claim.kind, claim.state, claim.value)
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK
        and claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
    )
    assert expected == actual


_BY_STATEMENT_CASES = (
    ("scalar", "By Ada", ("By Ada",), 0),
    ("list", ["By Ada", "With Zed"], ("By Ada", "With Zed"), 0),
    (
        "mixed",
        ["By Ada", "bad\x00byline", 7],
        ("By Ada",),
        2,
    ),
    (
        "overcap",
        [f"By Author {index}" for index in range(9)],
        (),
        1,
    ),
    ("mapping", {"value": "By Ada"}, (), 1),
)


@pytest.mark.parametrize(
    ("label", "value", "expected", "diagnostics"),
    _BY_STATEMENT_CASES,
)
def test_by_statement_scalar_or_list_is_retained_and_diagnosed_once(
    label, value, expected, diagnostics
) -> None:
    edition = _edition("/books/OL1M")
    edition["by_statement"] = value
    candidate, _, _ = open_library_metadata._candidate(
        "open_library",
        "/books/OL1M",
        "/works/OL5W",
        edition,
        _work(),
        (_isbn(),),
        "9780306406157",
    )
    retained = tuple(
        evidence.value
        for evidence in candidate.evidence
        if evidence.kind is ProviderEvidenceKind.RESPONSIBILITY_STATEMENT
    )
    assert expected == retained
    assert diagnostics == open_library_metadata._optional_projection_rejections(
        edition,
        schema="edition_detail",
    )


def test_candidate_semantics_exclude_only_evidence_source_ordinals() -> None:
    candidate, _, _ = open_library_metadata._candidate(
        "open_library",
        "/books/OL1M",
        "/works/OL5W",
        _edition("/books/OL1M"),
        _work(),
        (_isbn(),),
        "9780306406157",
    )
    reordered = replace(
        candidate,
        evidence=tuple(
            replace(value, source_ordinal=index + 100)
            for index, value in enumerate(candidate.evidence)
        ),
    )
    assert candidate != reordered
    assert open_library_metadata._candidate_semantic_key(
        candidate
    ) == open_library_metadata._candidate_semantic_key(reordered)
    assert tuple(range(len(candidate.evidence))) == tuple(
        sorted(value.source_ordinal for value in candidate.evidence)
    )


def test_candidate_semantics_keep_required_work_join_strict() -> None:
    candidate, _, _ = open_library_metadata._candidate(
        "open_library",
        "/books/OL1M",
        "/works/OL5W",
        _edition("/books/OL1M"),
        _work(),
        (_isbn(),),
        "9780306406157",
    )
    conflicting = replace(
        candidate,
        work_identity="open-library-work-OL9W",
        evidence=tuple(
            replace(value, value="open-library-work-OL9W")
            if value.kind is ProviderEvidenceKind.EDITION_WORK_RELATION
            else value
            for value in candidate.evidence
        ),
    )
    assert open_library_metadata._candidate_semantic_key(
        candidate
    ) != open_library_metadata._candidate_semantic_key(conflicting)


def test_search_author_pairs_keep_safe_names_without_misbinding_keys() -> None:
    assert (
        ("Ada Example", "/authors/OL7A"),
        ("Zed Example", None),
    ) == open_library_metadata._search_authors(
        ["Ada Example", "Zed Example"],
        ["OL7A", "not-an-author-key"],
    )
    assert () == open_library_metadata._search_authors(
        [f"Author {index}" for index in range(9)],
        [f"OL{index + 1}A" for index in range(9)],
    )


@pytest.mark.parametrize(
    ("label", "present", "value", "expected_state", "expected_context"),
    (
        ("absent", False, None, FieldState.ABSENT, ()),
        ("null", True, None, FieldState.ABSENT, ()),
        ("valid", True, "2026-01-02", FieldState.VALID, ()),
        (
            "malformed",
            True,
            "not a date",
            FieldState.MALFORMED,
            ("not a date",),
        ),
        (
            "oversized",
            True,
            "x" * (open_library_metadata.MAX_SCALAR_CHARACTERS + 1),
            FieldState.OVERSIZED,
            (),
        ),
    ),
)
def test_publication_date_presence_emits_one_typed_claim_and_safe_context(
    label, present, value, expected_state, expected_context
) -> None:
    edition = _edition("/books/OL1M")
    if present:
        edition["publish_date"] = value
    else:
        edition.pop("publish_date")
    candidate, _, _ = open_library_metadata._candidate(
        "open_library",
        "/books/OL1M",
        "/works/OL5W",
        edition,
        _work(),
        (_isbn(),),
        "9780306406157",
    )
    claims = tuple(
        claim
        for claim in candidate.claims
        if claim.kind is ClaimKind.PUBLICATION_DATE
        and claim.scope is ClaimScope.EDITION
    )
    context = tuple(
        evidence.value
        for evidence in candidate.evidence
        if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert 1 == len(claims)
    assert (5, expected_state) == (claims[0].raw_order, claims[0].state)
    assert expected_context == context


def test_work_detail_author_identity_survives_unusable_search_and_edition_links() -> (
    None
):
    work = _work()
    work["authors"] = [
        {
            "author": {"key": "/authors/OL7A"},
            "type": {"key": "/type/author_role"},
        },
        {"author": {"key": "bad"}, "type": {"key": "/type/author_role"}},
    ]
    edition = _edition("/books/OL1M")
    edition["authors"] = [{"key": "bad"}]
    values = {
        "/search.json": {
            "docs": [
                {
                    "key": "/works/OL5W",
                    "author_name": ["Ada Example"],
                    "author_key": ["bad"],
                    "editions": {"docs": [{"key": "/books/OL1M"}]},
                }
            ]
        },
        "/books/OL1M.json": edition,
        "/works/OL5W.json": work,
    }
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=values[request.url.path],
                    )
                )
            ),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_work_lookup())
        .observation
    )

    assert ProviderStatus.CANDIDATES == observation.status
    assert 3 == observation.diagnostics[0].count
    identity = next(
        value
        for value in observation.candidates[0].evidence
        if value.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY
    )
    assert "open-library-author-OL7A" == identity.value
    assert ClaimScope.WORK == identity.scope
    assert "author" == identity.role


_OPTIONAL_DETAIL_CASES = (
    ("work description scalar", "work", "description", 7),
    (
        "work description typed object",
        "work",
        "description",
        {"type": "/type/object", "value": "Description"},
    ),
    ("edition by statement", "edition", "by_statement", "bad\x00byline"),
    (
        "unknown work author role",
        "work",
        "authors",
        [
            {
                "author": {"key": "/authors/OL7A"},
                "type": {"key": "/type/editor_role"},
            }
        ],
    ),
    ("edition cover range", "edition", "covers", [2_147_483_648]),
    ("work cover range", "work", "covers", [2_147_483_648]),
)


@pytest.mark.parametrize(
    ("name", "document", "field", "invalid"),
    _OPTIONAL_DETAIL_CASES,
)
def test_optional_detail_failures_are_local_and_diagnosed_once(
    name, document, field, invalid
) -> None:
    edition = _edition("/books/OL1M")
    work = _work()
    target = edition if document == "edition" else work
    target[field] = invalid
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": edition,
        "/works/OL5W.json": work,
    }
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=values[request.url.path],  # noqa: B023 - closure is created and consumed within this iteration
                    )
                )
            ),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_isbn_lookup((_isbn(),)))
        .observation
    )

    assert ProviderStatus.CANDIDATES == observation.status
    assert (1,) == tuple(value.count for value in observation.diagnostics)
    if field == "description":
        description = next(
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.DESCRIPTION
        )
        assert FieldState.MALFORMED is description.state
        assert description.value is None
    if field == "by_statement":
        assert ProviderEvidenceKind.RESPONSIBILITY_STATEMENT not in {
            value.kind for value in observation.candidates[0].evidence
        }
    if field == "authors":
        assert "open-library-author-OL7A" not in {
            value.value
            for value in observation.candidates[0].evidence
            if value.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY
        }


_OPTIONAL_PROVENANCE_CASES: tuple[
    tuple[
        str,
        str,
        str,
        object,
        ProviderEvidenceKind,
        ClaimScope,
        ProviderEvidenceKind,
    ],
    ...,
] = (
    (
        "edition revision object",
        "edition",
        "revision",
        {},
        ProviderEvidenceKind.REVISION,
        ClaimScope.EDITION,
        ProviderEvidenceKind.LAST_MODIFIED,
    ),
    (
        "work revision scalar",
        "work",
        "revision",
        "bad",
        ProviderEvidenceKind.REVISION,
        ClaimScope.WORK,
        ProviderEvidenceKind.LAST_MODIFIED,
    ),
    (
        "edition last-modified scalar",
        "edition",
        "last_modified",
        "bad",
        ProviderEvidenceKind.LAST_MODIFIED,
        ClaimScope.EDITION,
        ProviderEvidenceKind.REVISION,
    ),
    (
        "work last-modified typed object",
        "work",
        "last_modified",
        {
            "type": "/type/object",
            "value": "2026-01-03T03:04:05.000000",
        },
        ProviderEvidenceKind.LAST_MODIFIED,
        ClaimScope.WORK,
        ProviderEvidenceKind.REVISION,
    ),
)


@pytest.mark.parametrize(
    ("name", "document", "field", "invalid", "omitted_kind", "scope", "sibling_kind"),
    _OPTIONAL_PROVENANCE_CASES,
)
def test_optional_provenance_failures_are_local_diagnosed_and_replayable(
    name, document, field, invalid, omitted_kind, scope, sibling_kind
) -> None:
    edition = _edition("/books/OL1M")
    work = _work()
    target = edition if document == "edition" else work
    target[field] = invalid
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": edition,
        "/works/OL5W.json": work,
    }
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=values[request.url.path],  # noqa: B023 - closure is created and consumed within this iteration
                    )
                )
            ),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_isbn_lookup((_isbn(),)))
        .observation
    )

    assert ProviderStatus.CANDIDATES == observation.status
    assert (1,) == tuple(value.count for value in observation.diagnostics)
    candidate = observation.candidates[0]
    assert any(
        claim.kind is ClaimKind.TITLE and claim.state is FieldState.VALID
        for claim in candidate.claims
    )
    assert (omitted_kind, scope) not in {
        (value.kind, value.scope) for value in candidate.evidence
    }
    assert (sibling_kind, scope) in {
        (value.kind, value.scope) for value in candidate.evidence
    }
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            f"item-{document}-{field}",
            default_target_fields("Current title"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(_isbn(),),
        local_reasons=("not_applicable",),
        providers=(observation,),
    )
    assert report == MetadataReport.from_json(report.to_json())

    if name == "work last-modified typed object":
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            try:
                repository.publish(report, lambda: True)
            finally:
                repository.close()
            reopened = open_repository(path, create=False)
            try:
                persisted = MetadataReport.from_json(reopened.get(report.report_id))
            finally:
                reopened.close()
        assert report == persisted


_REVISION_ABSENT = object()

_REVISION_ALIAS_CASES: tuple[
    tuple[str, str, object, object, str | None, int, bool], ...
] = (
    (
        "edition malformed revision with valid latest",
        "edition",
        {},
        31,
        "31",
        1,
        True,
    ),
    (
        "work malformed revision with valid latest",
        "work",
        "bad",
        41,
        "41",
        1,
        False,
    ),
    (
        "edition valid revision with malformed latest",
        "edition",
        3,
        {},
        "3",
        1,
        False,
    ),
    (
        "work latest only",
        "work",
        _REVISION_ABSENT,
        44,
        "44",
        0,
        False,
    ),
    (
        "edition revision only",
        "edition",
        3,
        _REVISION_ABSENT,
        "3",
        0,
        False,
    ),
    (
        "work both valid prefers revision",
        "work",
        4,
        45,
        "4",
        0,
        False,
    ),
    (
        "edition both malformed",
        "edition",
        {},
        "bad",
        None,
        2,
        False,
    ),
)


@pytest.mark.parametrize(
    (
        "name",
        "document",
        "revision",
        "latest_revision",
        "expected",
        "diagnostic_count",
        "persist",
    ),
    _REVISION_ALIAS_CASES,
)
def test_revision_aliases_are_independent_and_replay_one_preferred_value(
    name,
    document,
    revision,
    latest_revision,
    expected,
    diagnostic_count,
    persist,
) -> None:
    edition = _edition("/books/OL1M")
    work = _work()
    target = edition if document == "edition" else work
    if revision is _REVISION_ABSENT:
        target.pop("revision", None)
    else:
        target["revision"] = revision
    if latest_revision is _REVISION_ABSENT:
        target.pop("latest_revision", None)
    else:
        target["latest_revision"] = latest_revision
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": edition,
        "/works/OL5W.json": work,
    }
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=values[request.url.path],  # noqa: B023 - closure is created and consumed within this iteration
                    )
                )
            ),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_isbn_lookup((_isbn(),)))
        .observation
    )

    assert ProviderStatus.CANDIDATES == observation.status
    assert (() if diagnostic_count == 0 else (diagnostic_count,)) == tuple(
        value.count for value in observation.diagnostics
    )
    scope = ClaimScope.EDITION if document == "edition" else ClaimScope.WORK
    revisions = [
        value
        for value in observation.candidates[0].evidence
        if value.kind is ProviderEvidenceKind.REVISION and value.scope is scope
    ]
    assert ([] if expected is None else [expected]) == [
        value.value for value in revisions
    ]
    if revisions:
        assert (1 if scope is ClaimScope.EDITION else 3) == revisions[0].source_ordinal
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            f"item-revision-{document}",
            default_target_fields("Current title"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(_isbn(),),
        local_reasons=("not_applicable",),
        providers=(observation,),
    )
    replayed = MetadataReport.from_json(report.to_json())
    assert report == replayed

    if persist:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            try:
                repository.publish(report, lambda: True)
            finally:
                repository.close()
            reopened = open_repository(path, create=False)
            try:
                persisted = MetadataReport.from_json(reopened.get(report.report_id))
            finally:
                reopened.close()
        assert report == persisted


def test_nested_work_author_evidence_persists_with_exact_source_semantics() -> None:
    search = _search("/books/OL1M")
    search["docs"][0].update(
        {
            "publisher": ["Search Press"],
            "publish_date": ["2026"],
            "language": ["eng"],
            "edition_key": ["OL1M"],
        }
    )
    edition = _edition("/books/OL1M", cover=101)
    edition.update(
        {
            "alternate_titles": ["Alternate edition"],
            "alternative_titles": ["Other edition"],
            "imprints": ["Example Imprint"],
            "imprint": "Scalar Imprint",
            "distributors": ["Example Distributor"],
            "by_statement": "By Ada Author",
            "edition_name": "First edition",
            "edition_statement": "First edition statement",
            "translation_of": "Original title",
            "description": {
                "type": "/type/text",
                "value": "Edition description",
            },
        }
    )
    work = _work(cover=202)
    work.update(
        {
            "subtitle": "Work subtitle",
            "alternate_titles": ["Alternate work"],
            "series": ["Cycle"],
            "series_number": ["2"],
        }
    )
    values = {
        "/search.json": search,
        "/books/OL1M.json": edition,
        "/works/OL5W.json": work,
    }
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=values[request.url.path],
                    )
                )
            ),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_isbn_lookup((_isbn(),)))
        .observation
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields("Current title"),
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(_isbn(),),
        local_reasons=("not_applicable",),
        providers=(observation,),
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "reports.sqlite"
        repository = open_repository(path)
        try:
            repository.publish(report, lambda: True)
            persisted = repository.get(report.report_id)
        finally:
            repository.close()
        reopened_repository = open_repository(path, create=False)
        try:
            reopened = MetadataReport.from_json(
                reopened_repository.get(report.report_id)
            )
        finally:
            reopened_repository.close()

    assert report.to_json() == persisted
    assert ProviderStatus.CANDIDATES == reopened.providers[0].status
    assert () == reopened.providers[0].diagnostics
    candidate = reopened.providers[0].candidates[0]
    edition_subtitle = next(
        value
        for value in candidate.claims
        if value.kind is ClaimKind.SUBTITLE and value.scope is ClaimScope.EDITION
    )
    assert "ebook" == edition_subtitle.applicability
    expected_claims = (
        (0, ClaimKind.ISBN, ClaimScope.EDITION, None, FieldState.VALID, "0306406152"),
        (
            1,
            ClaimKind.ISBN,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "9780306406157",
        ),
        (2, ClaimKind.ASIN, ClaimScope.EDITION, None, FieldState.UNSUPPORTED, None),
        (
            3,
            ClaimKind.TITLE,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "Edition title",
        ),
        (
            4,
            ClaimKind.SUBTITLE,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "Edition subtitle",
        ),
        (
            5,
            ClaimKind.PUBLICATION_DATE,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "2026-01-02",
        ),
        (
            6,
            ClaimKind.TITLE,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "Alternate edition",
        ),
        (
            7,
            ClaimKind.TITLE,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "Other edition",
        ),
        (
            8,
            ClaimKind.PUBLISHER,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "Example Press",
        ),
        (
            9,
            ClaimKind.IMPRINT,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "Example Imprint",
        ),
        (
            10,
            ClaimKind.IMPRINT,
            ClaimScope.EDITION,
            None,
            FieldState.VALID,
            "Scalar Imprint",
        ),
        (11, ClaimKind.LANGUAGE, ClaimScope.EDITION, None, FieldState.VALID, "eng"),
        (12, ClaimKind.CREATOR, ClaimScope.EDITION, None, FieldState.UNSUPPORTED, None),
        (
            13,
            ClaimKind.CONTRIBUTOR,
            ClaimScope.EDITION,
            None,
            FieldState.UNSUPPORTED,
            None,
        ),
        (
            14,
            ClaimKind.TITLE,
            ClaimScope.WORK,
            None,
            FieldState.VALID,
            "Local work title",
        ),
        (
            15,
            ClaimKind.SUBTITLE,
            ClaimScope.WORK,
            None,
            FieldState.VALID,
            "Work subtitle",
        ),
        (
            16,
            ClaimKind.TITLE,
            ClaimScope.WORK,
            None,
            FieldState.VALID,
            "Alternate work",
        ),
        (
            17,
            ClaimKind.DESCRIPTION,
            ClaimScope.WORK,
            None,
            FieldState.VALID,
            "Description",
        ),
        (18, ClaimKind.CREATOR, ClaimScope.WORK, None, FieldState.UNSUPPORTED, None),
        (
            19,
            ClaimKind.CONTRIBUTOR,
            ClaimScope.WORK,
            None,
            FieldState.UNSUPPORTED,
            None,
        ),
        (20, ClaimKind.TAG, ClaimScope.WORK, None, FieldState.UNSUPPORTED, None),
        (21, ClaimKind.GENRE, ClaimScope.WORK, None, FieldState.UNSUPPORTED, None),
        (22, ClaimKind.SERIES, ClaimScope.WORK, None, FieldState.VALID, "Cycle"),
        (23, ClaimKind.SERIES_SEQUENCE, ClaimScope.WORK, None, FieldState.VALID, "2"),
    )
    assert expected_claims == tuple(
        (
            value.raw_order,
            value.kind,
            value.scope,
            value.role,
            value.state,
            value.value,
        )
        for value in candidate.claims
    )
    expected_matrix = (
        (30, ProviderEvidenceKind.COPYRIGHT_DATE, ClaimScope.EDITION, None, "2026"),
        (
            18,
            ProviderEvidenceKind.DEWEY_CLASSIFICATION,
            ClaimScope.EDITION,
            None,
            "813.6",
        ),
        (
            6,
            ProviderEvidenceKind.DISTRIBUTOR,
            ClaimScope.EDITION,
            None,
            "Example Distributor",
        ),
        (33, ProviderEvidenceKind.EDITION_COVER_ID, ClaimScope.EDITION, None, "101"),
        (
            27,
            ProviderEvidenceKind.EDITION_DESCRIPTION,
            ClaimScope.EDITION,
            None,
            "Edition description",
        ),
        (
            7,
            ProviderEvidenceKind.EDITION_STATEMENT,
            ClaimScope.EDITION,
            None,
            "First edition",
        ),
        (
            8,
            ProviderEvidenceKind.EDITION_STATEMENT,
            ClaimScope.EDITION,
            None,
            "First edition statement",
        ),
        (
            0,
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            ClaimScope.EDITION,
            None,
            "open-library-work-OL5W",
        ),
        (
            2,
            ProviderEvidenceKind.LAST_MODIFIED,
            ClaimScope.EDITION,
            None,
            "2026-01-02T03:04:05.000000",
        ),
        (
            19,
            ProviderEvidenceKind.LC_CLASSIFICATION,
            ClaimScope.EDITION,
            None,
            "PS3605.X36",
        ),
        (17, ProviderEvidenceKind.LCCN, ClaimScope.EDITION, None, "2026123456"),
        (16, ProviderEvidenceKind.OCLC, ClaimScope.EDITION, None, "12345"),
        (31, ProviderEvidenceKind.PAGE_COUNT, ClaimScope.EDITION, None, "321"),
        (29, ProviderEvidenceKind.PAGINATION, ClaimScope.EDITION, None, "321 pages"),
        (28, ProviderEvidenceKind.PHYSICAL_FORMAT, ClaimScope.EDITION, None, "eBook"),
        (
            20,
            ProviderEvidenceKind.PUBLICATION_PLACE,
            ClaimScope.EDITION,
            None,
            "London",
        ),
        (
            5,
            ProviderEvidenceKind.RESPONSIBILITY_STATEMENT,
            ClaimScope.EDITION,
            None,
            "By Ada Author",
        ),
        (1, ProviderEvidenceKind.REVISION, ClaimScope.EDITION, None, "3"),
        (
            9,
            ProviderEvidenceKind.TRANSLATION_STATEMENT,
            ClaimScope.EDITION,
            None,
            "Original title",
        ),
        (25, ProviderEvidenceKind.WIKIDATA, ClaimScope.EDITION, None, "Q123"),
        (
            14,
            ProviderEvidenceKind.AUTHOR_IDENTITY,
            ClaimScope.WORK,
            None,
            "open-library-author-OL7A",
        ),
        (
            15,
            ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
            ClaimScope.WORK,
            "author",
            "open-library-author-OL7A",
        ),
        (
            32,
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            ClaimScope.WORK,
            None,
            "2025-12-31",
        ),
        (
            4,
            ProviderEvidenceKind.LAST_MODIFIED,
            ClaimScope.WORK,
            None,
            "2026-01-03T03:04:05.000000",
        ),
        (22, ProviderEvidenceKind.PERSON, ClaimScope.WORK, None, "Example Person"),
        (23, ProviderEvidenceKind.PLACE, ClaimScope.WORK, None, "Example Place"),
        (3, ProviderEvidenceKind.REVISION, ClaimScope.WORK, None, "4"),
        (13, ProviderEvidenceKind.SEARCH_EDITION, ClaimScope.WORK, None, "OL1M"),
        (12, ProviderEvidenceKind.SEARCH_LANGUAGE, ClaimScope.WORK, None, "eng"),
        (
            11,
            ProviderEvidenceKind.SEARCH_PUBLICATION_DATE,
            ClaimScope.WORK,
            None,
            "2026",
        ),
        (
            10,
            ProviderEvidenceKind.SEARCH_PUBLISHER,
            ClaimScope.WORK,
            None,
            "Search Press",
        ),
        (
            21,
            ProviderEvidenceKind.SUBJECT,
            ClaimScope.WORK,
            None,
            "Speculative fiction",
        ),
        (24, ProviderEvidenceKind.TIME, ClaimScope.WORK, None, "Near future"),
        (26, ProviderEvidenceKind.WIKIDATA, ClaimScope.WORK, None, "Q456"),
        (34, ProviderEvidenceKind.WORK_COVER_ID, ClaimScope.WORK, None, "202"),
    )
    assert expected_matrix == tuple(
        (
            value.source_ordinal,
            value.kind,
            value.scope,
            value.role,
            value.value,
        )
        for value in candidate.evidence
    )
    identity = next(
        value
        for value in candidate.evidence
        if value.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY
        and value.value == "open-library-author-OL7A"
    )
    assert ClaimScope.WORK == identity.scope
    assert "author" == identity.role
    assert identity.source_ordinal >= 0
    assert [
        value.source_ordinal for value in report.providers[0].candidates[0].evidence
    ] == [value.source_ordinal for value in candidate.evidence]


def test_detail_freeze_replay_preserves_empty_containers_and_decimal() -> None:
    value = {
        "empty_mapping": {},
        "empty_sequence": [],
        "nested": [{}, []],
        "decimal": Decimal("1.25"),
    }
    frozen = open_library_metadata._freeze_detail(value)
    assert value == open_library_metadata._thaw_detail(frozen)


def test_first_dispatch_captures_injected_access_time() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"numFound": 0, "docs": []},
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                headers={"User-Agent": "Mechanical-Librarian/test"},
                transport=httpx.MockTransport(handler),
            ),
            65_536,
            wall_clock=lambda: 1_700_000_000.125,
        )
        .observe(_isbn_lookup((_isbn(),)))
        .observation
    )
    assert ProviderStatus.NO_MATCH == observation.status
    assert "2023-11-14T22:13:20.125000Z" == observation.accessed_at
    assert 1 == len(requests)


@pytest.mark.parametrize(
    "lexical",
    (
        "2024",
        "2024-03",
        "2024-03-05",
        "2024-03-05T10:30:00-04:00",
    ),
)
def test_publication_field_accepts_year_month_day_and_signed_offset(lexical) -> None:
    assert (FieldState.VALID, lexical) == open_library_metadata._field(
        ClaimKind.PUBLICATION_DATE,
        lexical,
    )


def test_work_plan_persists_ordered_qualified_creators_through_replay() -> None:
    title = _title()
    fields = tuple(
        TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            ("Zed Author", "Ada Author"),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields("Local work title")
    )
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", "Local work title", (), fields=fields),
        (title,),
    )
    responses = {
        "/search.json": {
            "docs": [
                {
                    "key": "/works/OL5W",
                    "author_name": ["Zed Author", "Ada Author"],
                    "editions": {"docs": [{"key": "/books/OL1M"}]},
                }
            ]
        },
        "/books/OL1M.json": _edition("/books/OL1M", work="/works/OL5W"),
        "/works/OL5W.json": _work("/works/OL5W"),
    }

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=responses[request.url.path],
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    observation = (provider.observe(plan)).observation
    assert ProviderStatus.CANDIDATES == observation.status
    assert ProviderQueryKind.WORK == observation.query_kind
    assert (title.reference,) == observation.input_references
    assert ("Zed Author", "Ada Author") == tuple(
        claim.value
        for claim in observation.candidates[0].claims
        if claim.kind is ClaimKind.CREATOR
        and claim.state.value == "valid"
        and claim.role == "author"
    )

    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(title,),
        local_reasons=("target_metadata_title",),
        providers=(observation,),
    )
    replay = MetadataReport.from_json(report.to_json())
    assert ("Zed Author", "Ada Author") == tuple(
        claim.value
        for claim in replay.providers[0].candidates[0].claims
        if claim.kind is ClaimKind.CREATOR
        and claim.state.value == "valid"
        and claim.role == "author"
    )


@pytest.mark.parametrize(
    ("label", "authors", "expected"),
    (
        (
            "reordered",
            ["Ada Author", "Zed Author"],
            ProviderStatus.CANDIDATES,
        ),
        (
            "duplicate",
            ["Zed Author", "Ada Author", "Ada Author"],
            ProviderStatus.CANDIDATES,
        ),
        ("invalid", ["Zed Author", 7], ProviderStatus.NO_MATCH),
        (
            "overflow",
            [f"Author {index}" for index in range(9)],
            ProviderStatus.NO_MATCH,
        ),
    ),
)
def test_work_plan_accepts_reordered_and_preserves_duplicate_invalid_and_overflow_authors(
    label, authors, expected
) -> None:
    title = _title()
    fields = tuple(
        TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            ("Zed Author", "Ada Author"),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields("Local work title")
    )
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", "Local work title", (), fields=fields),
        (title,),
    )
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)  # noqa: B023 - closure is created and consumed within this iteration
        value: dict[str, object]
        if request.url.path == "/search.json":
            value = {
                "docs": [
                    {
                        "key": "/works/OL5W",
                        "author_name": authors,  # noqa: B023 - closure is created and consumed within this iteration
                        "editions": {"docs": [{"key": "/books/OL1M"}]},
                    }
                ]
            }
        elif request.url.path == "/books/OL1M.json":
            value = _edition("/books/OL1M", work="/works/OL5W")
        else:
            assert "/works/OL5W.json" == request.url.path
            value = _work("/works/OL5W")
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=value,
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(plan)
        .observation
    )
    assert expected == observation.status
    assert (1 if expected is ProviderStatus.CANDIDATES else 0) == len(
        observation.candidates
    )
    assert 3 == len(requests)


def test_work_lookup_uses_complete_title_and_authors_then_retains_only_neutral_work_evidence() -> (
    None
):
    requests: list[httpx.Request] = []
    values = {
        "/search.json": {
            "docs": [
                {
                    "key": "/works/OL5W",
                    "author_name": ["Ada Example"],
                    "author_key": ["OL7A"],
                    "publisher": ["Search Press"],
                    "publish_date": ["not a typed publication date"],
                    "language": ["eng"],
                    "edition_key": ["OL1M"],
                    "editions": {"docs": [{"key": "/books/OL1M"}]},
                }
            ]
        },
        "/books/OL1M.json": {
            **_edition("/books/OL1M", cover=100),
            "physical_format": "Hardcover",
        },
        "/works/OL5W.json": _work(cover=300),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=values[request.url.path],
        )

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    lookup = _work_lookup()
    observation = (provider.observe(lookup)).observation
    assert ProviderQueryKind.WORK == observation.query_kind
    assert "Local work title" == requests[0].url.params["title"]
    assert "Ada Example" == requests[0].url.params["author"]
    assert (
        "key,author_name,author_key,publisher,publish_date,language,edition_key,editions,editions.key"
        == requests[0].url.params["fields"]
    )
    qualified = qualify_provider_observation(observation, lookup)
    assert ProviderStatus.CANDIDATES == qualified.status
    candidate = qualified.candidates[0]
    assert 1 == len(candidate.identity_bindings)
    binding = candidate.identity_bindings[0]
    bound_claim = next(
        claim
        for claim in candidate.claims
        if claim.reference == binding.claim_reference
    )
    assert (
        binding.identity,
        binding.scope,
        binding.role,
        bound_claim.value,
    ) == (
        "open-library-author-OL7A",
        ClaimScope.WORK,
        "author",
        "Ada Example",
    )
    assert not (
        {ClaimKind.ISBN, ClaimKind.ASIN, ClaimKind.PUBLISHER}
        & {claim.kind for claim in candidate.claims}
    )
    assert not (
        {ClaimKind.GENRE, ClaimKind.TAG}
        & {claim.kind for claim in candidate.claims if claim.state.value == "valid"}
    )
    assert ProviderEvidenceKind.SUBJECT in {
        value.kind for value in observation.candidates[0].evidence
    }
    assert ProviderEvidenceKind.CONTRIBUTOR_IDENTITY in {
        value.kind for value in observation.candidates[0].evidence
    }
    assert ProviderEvidenceKind.SUBJECT not in {
        value.kind for value in candidate.evidence
    }
    assert ProviderEvidenceKind.FIRST_PUBLICATION_DATE in {
        value.kind for value in candidate.evidence
    }
    retained = next(
        value
        for value in candidate.evidence
        if value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert () == retained.links
    assert all(
        value.scope is ClaimScope.WORK or value is retained
        for value in candidate.evidence
    )
    search_evidence = {
        (value.kind, value.value, value.scope)
        for value in observation.candidates[0].evidence
        if value.kind
        in {
            ProviderEvidenceKind.SEARCH_PUBLISHER,
            ProviderEvidenceKind.SEARCH_PUBLICATION_DATE,
            ProviderEvidenceKind.SEARCH_LANGUAGE,
            ProviderEvidenceKind.SEARCH_EDITION,
        }
    }
    assert {
        (ProviderEvidenceKind.SEARCH_PUBLISHER, "Search Press", ClaimScope.WORK),
        (
            ProviderEvidenceKind.SEARCH_PUBLICATION_DATE,
            "not a typed publication date",
            ClaimScope.WORK,
        ),
        (ProviderEvidenceKind.SEARCH_LANGUAGE, "eng", ClaimScope.WORK),
        (ProviderEvidenceKind.SEARCH_EDITION, "OL1M", ClaimScope.WORK),
    } == search_evidence


def test_exact_search_details_rich_evidence_and_covers_are_bounded() -> None:
    now = [0.0]
    request_starts: list[float] = []
    requests: list[httpx.Request] = []

    def sleep(seconds: float) -> None:
        now[0] += seconds

    responses = {
        "/search.json": _search(
            "/books/OL20M",
            "/books/OL10M",
        ),
        "/books/OL10M.json": _edition("/books/OL10M", cover=100),
        "/books/OL20M.json": _edition("/books/OL20M", cover=200),
        "/works/OL5W.json": _work(cover=300),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        request_starts.append(now[0])
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=responses[request.url.path],
        )

    provider = OpenLibraryMetadataProvider(
        "open_library",
        open_library_client(
            "operator@example.invalid",
            5,
            transport=httpx.MockTransport(handler),
        ),
        65_536,
        pacer=OpenLibraryPacer(clock=lambda: now[0], sleep=sleep),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(), _title())))
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES == observation.status
    assert [
        "open-library-edition-OL10M",
        "open-library-edition-OL20M",
    ] == [candidate.identity for candidate in observation.candidates]
    assert [
        "/search.json",
        "/books/OL10M.json",
        "/works/OL5W.json",
        "/books/OL20M.json",
    ] == [request.url.path for request in requests]
    assert [0.0, 1.0, 2.0, 3.0] == request_starts
    search = requests[0]
    assert OPEN_LIBRARY_SEARCH_URL == str(search.url.copy_with(query=None))
    assert "isbn:9780306406157" == search.url.params["q"]
    assert "3" == search.url.params["limit"]
    assert "key,editions,editions.key,editions.isbn" == search.url.params["fields"]
    assert (
        "Mechanical-Librarian (operator@example.invalid)"
        == search.headers["user-agent"]
    )
    candidate = observation.candidates[0]
    assert "open-library-work-OL5W" == candidate.work_identity
    kinds = {value.kind for value in candidate.evidence}
    assert {
        ProviderEvidenceKind.REVISION,
        ProviderEvidenceKind.LAST_MODIFIED,
        ProviderEvidenceKind.OCLC,
        ProviderEvidenceKind.LCCN,
        ProviderEvidenceKind.WIKIDATA,
        ProviderEvidenceKind.SUBJECT,
        ProviderEvidenceKind.PERSON,
        ProviderEvidenceKind.PLACE,
        ProviderEvidenceKind.TIME,
        ProviderEvidenceKind.DEWEY_CLASSIFICATION,
        ProviderEvidenceKind.LC_CLASSIFICATION,
        ProviderEvidenceKind.PHYSICAL_FORMAT,
        ProviderEvidenceKind.PAGE_COUNT,
        ProviderEvidenceKind.PAGINATION,
        ProviderEvidenceKind.PUBLICATION_PLACE,
        ProviderEvidenceKind.COPYRIGHT_DATE,
        ProviderEvidenceKind.EDITION_WORK_RELATION,
        ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
        ProviderEvidenceKind.EDITION_COVER_ID,
        ProviderEvidenceKind.WORK_COVER_ID,
    }.issubset(kinds)
    work_title = next(
        claim
        for claim in candidate.claims
        if claim.kind is ClaimKind.TITLE and claim.scope is ClaimScope.WORK
    )
    assert Comparison.AGREEMENT == work_title.links[0].outcome
    assert 3 == len(provider_result.cover_descriptors)
    assert {
        cover_source_id(
            CoverSourceKind.OPEN_LIBRARY,
            "open_library:edition:/books/OL10M",
        ),
        cover_source_id(
            CoverSourceKind.OPEN_LIBRARY,
            "open_library:edition:/books/OL20M",
        ),
        cover_source_id(
            CoverSourceKind.OPEN_LIBRARY,
            "open_library:work:/works/OL5W",
        ),
    } == {value.source_id for value in provider_result.cover_descriptors}
    assert all(
        value.provenance is not None and value.provenance.provider == "open_library"
        for value in provider_result.cover_descriptors
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    assert ProviderStatus.UNAVAILABLE == (provider_result).observation.status
    assert 4 == len(requests)


@pytest.mark.parametrize(
    "claims",
    (
        (),
        (_isbn(applicability="print"),),
        (_isbn(), _isbn("9780140328721")),
    ),
)
def test_zero_or_multiple_usable_families_make_no_request(claims) -> None:
    requests: list[httpx.Request] = []
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (
                    requests.append(request)  # type: ignore[func-returns-value]  # reason: `append(...) or x` deliberately records the request and still yields the response.  # noqa: B023 - closure is created and consumed within this iteration
                    or httpx.Response(500)
                )
            )
        ),
        65_536,
    )
    assert (
        ProviderStatus.INSUFFICIENT_INPUT
        == (provider.observe(_isbn_lookup(claims))).observation.status
    )
    assert [] == requests


def test_discovery_plan_searches_each_valid_family() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"numFound": 0, "docs": []},
        )

    claims = (_isbn(), _isbn("9780140328721", applicability="print"))
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", None, ()),
        claims,
    )
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                headers={"User-Agent": "Mechanical-Librarian/test"},
                transport=httpx.MockTransport(handler),
            ),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(plan)
        .observation
    )
    assert 2 == len(requests)
    assert 2 == len(observation.lanes)
    assert {"isbn:9780306406157", "isbn:9780140328721"} == {
        request.url.params["q"] for request in requests
    }


def test_terminal_detail_failure_finalizes_reserved_details() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/search.json":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=_search("/books/OL1M", "/books/OL2M"),
            )
        assert "/books/OL1M.json" == request.url.path
        return httpx.Response(404)

    plan = metadata_discovery_plan(TargetSnapshot("ebook", None, ()), (_isbn(),))
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(plan)
        .observation
    )

    assert ["/search.json", "/books/OL1M.json"] == requests
    assert ProviderStatus.UNAVAILABLE == observation.status
    assert 4 == observation.request_accounting.logical_requests
    assert 2 == observation.request_accounting.initial_dispatches
    assert 2 == observation.request_accounting.undispatched_incomplete


@pytest.mark.parametrize(
    "oversized",
    (False, True),
)
def test_discovery_oversized_lane_stops_remaining_requests_at_exact_bound(
    oversized,
) -> None:
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", None, ()),
        (_isbn(), _isbn("9780140328721", applicability="print")),
    )
    payload = b'{"docs":[]}'

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)  # noqa: B023 - closure is created and consumed within this iteration
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=payload + (b" " if oversized else b""),  # noqa: B023 - closure is created and consumed within this iteration
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                headers={"User-Agent": "Mechanical-Librarian/test"},
                transport=httpx.MockTransport(handler),
            ),
            len(payload),
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(plan)
        .observation
    )
    assert (1 if oversized else 2) == len(requests)
    assert (
        {ProviderStatus.OVERSIZED, ProviderStatus.INCOMPLETE}
        if oversized
        else {ProviderStatus.NO_MATCH}
    ) == {lane.status for lane in observation.lanes}


def test_conflicting_lane_contributes_no_cover_descriptors() -> None:
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", None, ()),
        (_isbn(), _isbn("9780140328721", applicability="print")),
    )
    search_count = 0
    shared_work_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal search_count, shared_work_count
        value: dict[str, object]
        if request.url.path == "/search.json":
            search_count += 1
            family = request.url.params["q"].removeprefix("isbn:")
            if search_count == 1:
                value = {
                    "docs": [
                        {
                            "key": "/works/OL5W",
                            "editions": {
                                "docs": [
                                    {
                                        "key": "/books/OL9M",
                                        "isbn": [family],
                                    }
                                ]
                            },
                        }
                    ]
                }
            else:
                value = {
                    "docs": [
                        {
                            "key": "/works/OL6W",
                            "editions": {
                                "docs": [
                                    {
                                        "key": "/books/OL1M",
                                        "isbn": [family],
                                    }
                                ]
                            },
                        },
                        {
                            "key": "/works/OL5W",
                            "editions": {
                                "docs": [
                                    {
                                        "key": "/books/OL9M",
                                        "isbn": [family],
                                    }
                                ]
                            },
                        },
                    ]
                }
        elif request.url.path == "/books/OL1M.json":
            value = _edition("/books/OL1M", work="/works/OL6W", cover=200)
            value["isbn_13"] = ["9780306406157", "9780140328721"]
        elif request.url.path == "/books/OL9M.json":
            value = _edition("/books/OL9M", cover=100)
            value["isbn_13"] = ["9780306406157", "9780140328721"]
        elif request.url.path == "/works/OL6W.json":
            value = _work("/works/OL6W", cover=400)
        else:
            assert "/works/OL5W.json" == request.url.path
            shared_work_count += 1
            value = _work(
                "/works/OL5W",
                cover=300 if shared_work_count == 1 else 301,
            )
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=value,
        )

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            headers={"User-Agent": "Mechanical-Librarian/test"},
            transport=httpx.MockTransport(handler),
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )

    provider_result = provider.observe(plan)
    observation = (provider_result).observation

    assert {ProviderStatus.CANDIDATES} == {lane.status for lane in observation.lanes}
    assert 1 == shared_work_count
    assert 4 == len(provider_result.cover_descriptors)


def test_discovery_converges_repeated_lane_evidence_authors_and_isbn_applicability() -> (
    None
):
    ebook = _isbn()
    print_only = _isbn("9780140328721", applicability="print")
    title = _title()
    fields = tuple(
        TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            ("Zed Author", "Ada Author"),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields("Local work title")
    )
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", "Local work title", (), fields=fields),
        (title, ebook, print_only),
    )
    edition = _edition("/books/OL1M", work="/works/OL5W", cover=100)
    edition["isbn_13"] = ["9780306406157", "9780140328721"]
    edition["edition_name"] = "bad\x00edition"
    edition["by_statement"] = ["By Ada", "bad\x00byline", 7]
    work = _work("/works/OL5W", cover=200)
    work["subjects"] = ["Speculative fiction", "bad\x00subject"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search.json":
            family_query = request.url.params.get("q")
            if family_query is not None:
                search = _search("/books/OL1M")
                search["docs"][0]["editions"]["docs"][0]["isbn"] = [
                    family_query.removeprefix("isbn:")
                ]
                publishers = [
                    "First Press",
                    "Second Press",
                    "Dup Press",
                    "Dup Press",
                    "bad\x00publisher",
                ]
                search["docs"][0]["publisher"] = publishers
                value = search
            else:
                value = {
                    "docs": [
                        {
                            "key": "/works/OL5W",
                            "author_name": ["Zed Author", "Ada Author"],
                            "author_key": ["OL9A", "OL7A"],
                            "publisher": [
                                "First Press",
                                "Second Press",
                                "Dup Press",
                                "Dup Press",
                                "bad\x00publisher",
                            ],
                            "editions": {"docs": [{"key": "/books/OL1M"}]},
                        }
                    ]
                }
        elif request.url.path == "/books/OL1M.json":
            value = edition
        else:
            assert "/works/OL5W.json" == request.url.path
            value = work
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=value,
        )

    isbn_only = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_isbn_lookup((ebook,)))
        .observation
    )
    isbn_duplicate_publishers = tuple(
        evidence
        for evidence in isbn_only.candidates[0].evidence
        if evidence.kind is ProviderEvidenceKind.SEARCH_PUBLISHER
        and evidence.value == "Dup Press"
    )
    assert 2 == len(isbn_duplicate_publishers)

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(plan)
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES == observation.status
    assert 7 == observation.diagnostics[0].count
    assert 1 == len(observation.candidates)
    assert 3 == len(plan.lanes)
    assert 3 == len(observation.lanes)
    assert 9 == observation.request_accounting.logical_requests
    assert {ProviderStatus.CANDIDATES} == {lane.status for lane in observation.lanes}
    assert (
        tuple(sorted(lane.identity for lane in plan.lanes))
        == observation.convergence[0].lane_identities
    )
    lanes = {lane.matched_family: lane for lane in observation.lanes}
    assert (ebook.reference,) == lanes["9780306406157"].input_references
    assert (print_only.reference,) == lanes["9780140328721"].input_references
    assert 3 == len(
        {
            candidate.reference
            for lane in observation.lanes
            for candidate in lane.candidates
        }
    )

    isbn_claims = tuple(
        claim
        for claim in observation.candidates[0].claims
        if claim.kind is ClaimKind.ISBN and claim.value is not None
    )
    assert ("Zed Author", "Ada Author") == tuple(
        claim.value
        for claim in observation.candidates[0].claims
        if claim.kind is ClaimKind.CREATOR
        and claim.state.value == "valid"
        and claim.role == "author"
    )
    identities = tuple(
        evidence
        for evidence in observation.candidates[0].evidence
        if evidence.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY
    )
    assert {"open-library-author-OL7A", "open-library-author-OL9A"} == {
        evidence.value for evidence in identities
    }
    publishers = tuple(
        evidence
        for evidence in observation.candidates[0].evidence
        if evidence.kind is ProviderEvidenceKind.SEARCH_PUBLISHER
    )
    assert {"First Press", "Second Press", "Dup Press"} == {
        evidence.value for evidence in publishers
    }
    assert 4 == len(publishers)
    converged_duplicates = tuple(
        evidence for evidence in publishers if evidence.value == "Dup Press"
    )
    assert tuple(
        evidence.source_ordinal for evidence in isbn_duplicate_publishers
    ) == tuple(evidence.source_ordinal for evidence in converged_duplicates)
    identical = open_library_metadata._canonical_evidence_union(
        isbn_duplicate_publishers,
        tuple(
            replace(value, source_ordinal=index)
            for index, value in enumerate(isbn_duplicate_publishers)
        ),
    )
    assert isbn_duplicate_publishers == identical
    excess = open_library_metadata._canonical_evidence_union(
        isbn_duplicate_publishers,
        tuple(
            replace(
                isbn_duplicate_publishers[0],
                source_ordinal=index,
            )
            for index in range(3)
        ),
    )
    assert excess is not None
    assert 3 == len(excess or ())
    assert (
        max(evidence.source_ordinal for evidence in isbn_duplicate_publishers) + 1
    ) == (excess or ())[-1].source_ordinal
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(title, ebook, print_only),
        local_reasons=("target_metadata_title",),
        providers=(observation,),
    )
    replay = MetadataReport.from_json(report.to_json())
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        try:
            repository.publish(report, lambda: True)
            sqlite_replay = MetadataReport.from_json(repository.get(report.report_id))
        finally:
            repository.close()
    assert observation.candidates[0] == replay.providers[0].candidates[0]
    assert report.to_json() == sqlite_replay.to_json()
    assert tuple(
        evidence.source_ordinal for evidence in observation.candidates[0].evidence
    ) == tuple(
        evidence.source_ordinal
        for evidence in replay.providers[0].candidates[0].evidence
    )
    assert {
        "9780306406157": {"ebook"},
        "9780140328721": {"print"},
    } == {
        family: {
            claim.applicability
            for claim in isbn_claims
            if isbn_family(claim.value) == family
        }
        for family in ("9780306406157", "9780140328721")
    }
    assert {
        "9780306406157": {ebook.reference},
        "9780140328721": {print_only.reference},
    } == {
        family: {
            link.local_reference
            for claim in isbn_claims
            if isbn_family(claim.value) == family
            for link in claim.links
            if link.outcome is Comparison.AGREEMENT
        }
        for family in ("9780306406157", "9780140328721")
    }
    assert all(
        {ebook.reference, print_only.reference}
        == {link.local_reference for link in claim.links}
        for claim in isbn_claims
    )
    assert 2 == len(provider_result.cover_descriptors)
    assert 2 == len({value.source_id for value in provider_result.cover_descriptors})
    edition_cover = next(
        value
        for value in provider_result.cover_descriptors
        if value.provenance is not None
        and value.provenance.edition_identity is not None
    )
    assert (
        CoverDesignation.EXACT_EDITION
        == cast(CoverProvenance, edition_cover.provenance).designation
    )


def test_isbn_10_and_13_of_one_family_share_one_exact_search() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": []},
        )

    ten = _isbn("0306406152")
    thirteen = _isbn()
    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        )
        .observe(_isbn_lookup((ten, thirteen)))
        .observation
    )
    assert ProviderStatus.NO_MATCH == observation.status
    assert (
        tuple(sorted((ten.reference, thirteen.reference)))
        == observation.input_references
    )
    assert "isbn:9780306406157" == requests[0].url.params["q"]
    assert 1 == len(requests)

    print_candidate, _, _ = open_library_metadata._candidate(
        "open_library",
        "/books/OL1M",
        "/works/OL5W",
        _edition("/books/OL1M", work="/works/OL5W"),
        _work("/works/OL5W"),
        (_isbn(applicability="print"),),
        "9780306406157",
    )
    assert {"print"} == {
        claim.applicability
        for claim in print_candidate.claims
        if claim.kind is ClaimKind.ISBN
    }


def test_two_distinct_works_use_the_five_request_maximum_in_order() -> None:
    requests: list[httpx.Request] = []
    search = {
        "docs": [
            {
                "key": "/works/OL9W",
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL2M",
                            "isbn": ["9780306406157"],
                        }
                    ]
                },
            },
            {
                "key": "/works/OL5W",
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL1M",
                            "isbn": ["9780306406157"],
                        }
                    ]
                },
            },
        ]
    }
    responses = {
        "/search.json": search,
        "/books/OL1M.json": _edition(
            "/books/OL1M",
            work="/works/OL5W",
            cover=100,
        ),
        "/works/OL5W.json": _work("/works/OL5W", cover=200),
        "/books/OL2M.json": _edition(
            "/books/OL2M",
            work="/works/OL9W",
            cover=300,
        ),
        "/works/OL9W.json": _work("/works/OL9W", cover=400),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=responses[request.url.path],
        )

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation
    assert ProviderStatus.CANDIDATES == observation.status
    assert [
        "/search.json",
        "/books/OL1M.json",
        "/works/OL5W.json",
        "/books/OL2M.json",
        "/works/OL9W.json",
    ] == [request.url.path for request in requests]
    assert 4 == len(provider_result.cover_descriptors)
    assert {
        "https://covers.openlibrary.org/b/id/100-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/200-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/300-L.jpg?default=false",
        "https://covers.openlibrary.org/b/id/400-L.jpg?default=false",
    } == {value.url for value in provider_result.cover_descriptors}


def test_third_exact_candidate_is_terminal_before_detail_requests() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        value = _search(
            "/books/OL4M",
            "/books/OL3M",
            "/books/OL1M",
            "/books/OL2M",
        )
        value["docs"][0]["editions"]["docs"][0]["isbn"] = ["9780140328721"]
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=value,
        )

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation
    assert ProviderStatus.CANDIDATE_LIMIT_EXCEEDED == observation.status
    assert () == observation.candidates
    assert () == provider_result.cover_descriptors
    assert ["/search.json"] == [request.url.path for request in requests]


def test_nonmatching_search_rows_are_ignored_without_detail_requests() -> None:
    requests: list[httpx.Request] = []
    search = _search("/books/OL1M")
    search["docs"][0]["editions"]["docs"][0]["isbn"] = ["9780140328721"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=search,
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        )
        .observe(_isbn_lookup((_isbn(),)))
        .observation
    )
    assert ProviderStatus.NO_MATCH == observation.status
    assert ["/search.json"] == [request.url.path for request in requests]


_REQUIRED_FAILURE_CASES = (
    (
        "rate",
        65_536,
        lambda: httpx.Response(429),
        ProviderStatus.RATE_LIMITED,
    ),
    (
        "overflow",
        512,
        lambda: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"x" * 513,
        ),
        ProviderStatus.OVERSIZED,
    ),
    (
        "malformed",
        65_536,
        lambda: httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=b"{",
        ),
        ProviderStatus.MALFORMED,
    ),
    (
        "missing",
        65_536,
        lambda: httpx.Response(404),
        ProviderStatus.UNAVAILABLE,
    ),
    (
        "server",
        65_536,
        lambda: httpx.Response(503),
        ProviderStatus.UNAVAILABLE,
    ),
)


@pytest.mark.parametrize(
    ("label", "maximum", "failure", "expected"),
    _REQUIRED_FAILURE_CASES,
)
def test_required_request_failures_are_terminal_and_mapped(
    label, maximum, failure, expected
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)  # noqa: B023 - closure is created and consumed within this iteration
        if request.url.path == "/search.json":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=_search("/books/OL1M"),
            )
        return failure()  # noqa: B023 - closure is created and consumed within this iteration

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        maximum,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    observation = (provider.observe(_isbn_lookup((_isbn(),)))).observation
    assert expected == observation.status
    assert () == observation.candidates
    expected_paths = ["/search.json", "/books/OL1M.json"]
    if label == "server":
        expected_paths.append("/books/OL1M.json")
    assert expected_paths == [request.url.path for request in requests]


@pytest.mark.parametrize("status_code", (500, 502, 503, 504))
def test_transient_retries_use_exact_status_retry_after_and_pacing(
    status_code,
) -> None:
    responses = [
        httpx.Response(
            status_code,
            headers={"retry-after": "60"},
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": []},
        ),
    ]
    now = [0.0]
    sleeps: list[float] = []
    starts: list[float] = []

    def sleep(delay: float) -> None:
        sleeps.append(delay)
        now[0] += delay

    def handler(_: httpx.Request) -> httpx.Response:
        starts.append(now[0])
        return responses.pop(0)

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        pacer=OpenLibraryPacer(
            clock=lambda: now[0],
            sleep=sleep,
        ),
        clock=lambda: now[0],
        sleep=sleep,
        jitter=lambda: 0.0,
    )
    assert (
        ProviderStatus.NO_MATCH
        == (provider.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    expected_delay = 60.0 if status_code == 503 else 1.0
    assert [0.0, expected_delay] == starts
    assert [expected_delay] == sleeps

    calls = 0

    def rate_limited(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "1"})

    terminal = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(rate_limited)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: pytest.fail("429 must never retry"),
    )
    assert (
        ProviderStatus.RATE_LIMITED
        == (terminal.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    assert 1 == calls

    deadline_calls = 0

    def deadline_failure(_: httpx.Request) -> httpx.Response:
        nonlocal deadline_calls
        deadline_calls += 1
        return httpx.Response(503, headers={"retry-after": "2"})

    deadline_provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(deadline_failure)),
        65_536,
        pacer=OpenLibraryPacer(
            clock=lambda: 0.0,
            sleep=lambda _: None,
        ),
        clock=lambda: 0.0,
        sleep=lambda _: pytest.fail("deadline must refuse delay"),
    )
    assert (
        ProviderStatus.UNAVAILABLE
        == (
            deadline_provider.observe(_isbn_lookup((_isbn(),)), deadline=2.0)
        ).observation.status
    )
    assert 1 == deadline_calls


@pytest.mark.parametrize("prior_failures", (0, 1))
def test_recovers_at_every_permitted_status_retry_ordinal(
    prior_failures,
) -> None:
    responses = [httpx.Response(503) for _ in range(prior_failures)]
    responses.append(
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": []},
        )
    )
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(lambda _, values=responses: values.pop(0))
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    assert ProviderStatus.NO_MATCH == (provider_result).observation.status
    assert provider_result.evidence.attempt_records is not None
    assert prior_failures + 1 == len(provider_result.evidence.attempt_records)
    assert provider_result.evidence.attempt_records is not None
    assert ["retry"] * prior_failures + ["response_received"] == [
        record.outcome for record in provider_result.evidence.attempt_records
    ]


def test_final_retry_after_is_retained_when_local_attempts_are_exhausted() -> None:
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(503, headers={"retry-after": "7"})
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    assert ProviderStatus.UNAVAILABLE == (provider_result).observation.status
    assert provider_result.evidence.attempt_records is not None
    assert "exhausted" == provider_result.evidence.attempt_records[-1].outcome
    assert provider_result.evidence.attempt_records is not None
    assert 7.0 == provider_result.evidence.attempt_records[-1].delay_seconds


@pytest.mark.parametrize(
    "decoded_body",
    (b"error", b"not-gzip"),
)
@pytest.mark.parametrize(
    "raw_body",
    (gzip.compress(b"error"), b"not-gzip"),
    ids=("gzip", "not_gzip"),
)
def test_error_response_bodies_are_charged_before_status_retry(
    decoded_body, raw_body
) -> None:
    oversized_calls = 0

    def oversized(_: httpx.Request) -> httpx.Response:
        nonlocal oversized_calls
        oversized_calls += 1
        return httpx.Response(503, content=b"x" * 2_000)

    oversized_provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(oversized)),
        1_024,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: pytest.fail("oversized body must not retry"),
    )
    assert (
        ProviderStatus.OVERSIZED
        == (oversized_provider.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    assert 1 == oversized_calls
    assert 0 == oversized_provider._remaining_response_bytes

    success = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={"docs": []},
    )
    control = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(lambda _: success)),
        1_024,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    assert (
        ProviderStatus.NO_MATCH
        == (control.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    responses = [
        httpx.Response(503, stream=_InterruptedStream()),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": []},
        ),
    ]
    interrupted = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(lambda _: responses.pop(0))),
        1_024,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    assert (
        ProviderStatus.NO_MATCH
        == (interrupted.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    assert (
        control._remaining_response_bytes - 1 == interrupted._remaining_response_bytes
    )
    calls = 0

    def encoded_error(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503,
            headers={"content-encoding": "gzip"},
            stream=_HostileEncodedStream(raw_body),
        )

    encoded = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(encoded_error)),
        1_024,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: pytest.fail("non-identity encoding must not retry"),
    )
    assert (
        ProviderStatus.MALFORMED
        == (encoded.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    assert 1 == calls
    assert 1_024 * 49 - len(raw_body) == encoded._remaining_response_bytes

    response = httpx.Response(503, content=decoded_body)
    response.headers["content-encoding"] = "gzip"
    encoded = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(lambda _: response)),
        1_024,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: pytest.fail("preloaded non-identity response must not retry"),
    )
    assert (
        ProviderStatus.MALFORMED
        == (encoded.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    assert 1_024 * 49 == encoded._remaining_response_bytes


def test_retry_budget_is_four_across_required_request_chain() -> None:
    search = {
        "docs": [
            {
                "key": "/works/OL5W",
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL1M",
                            "isbn": ["9780306406157"],
                        }
                    ]
                },
            },
            {
                "key": "/works/OL9W",
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL2M",
                            "isbn": ["9780306406157"],
                        }
                    ]
                },
            },
        ]
    }
    success = {
        "/search.json": search,
        "/books/OL1M.json": _edition("/books/OL1M", work="/works/OL5W"),
        "/works/OL5W.json": _work("/works/OL5W"),
        "/books/OL2M.json": _edition("/books/OL2M", work="/works/OL9W"),
        "/works/OL9W.json": _work("/works/OL9W"),
    }
    first_failures = {
        "/search.json",
        "/books/OL1M.json",
        "/works/OL5W.json",
        "/books/OL2M.json",
    }
    path_calls: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        path_calls[path] = path_calls.get(path, 0) + 1
        if path in first_failures and path_calls[path] == 1:
            return httpx.Response(503)
        if path == "/works/OL9W.json":
            return httpx.Response(503)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=success[path],
        )

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    assert ProviderStatus.UNAVAILABLE == (provider_result).observation.status
    assert 9 == sum(path_calls.values())
    assert provider_result.evidence.attempt_records is not None
    assert 9 == len(provider_result.evidence.attempt_records)
    assert provider_result.evidence.attempt_records is not None
    assert 4 == sum(
        record.outcome == "retry" for record in provider_result.evidence.attempt_records
    )


def test_nine_lane_observation_is_capped_at_49_dispatches() -> None:
    values = (
        "9780306406157",
        "9780140328721",
        "9780439023528",
        "9780061120084",
        "9780743273565",
        "9780316769488",
        "9780451524935",
        "9780547928227",
    )
    claims = tuple(_isbn(value) for value in values)
    fields = tuple(
        TargetField(
            field.destination,
            TargetFieldState.VALUE,
            ("Ada Example",),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields("Local work title")
    )
    plan = metadata_discovery_plan(
        TargetSnapshot(
            "ebook",
            "Local work title",
            (),
            fields=fields,
        ),
        (_title(), *claims),
    )
    search_index = 0
    request_count = 0
    failed_paths: set[str] = set()
    detail_context: dict[str, tuple[str, str | None]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count, search_index
        request_count += 1
        path = request.url.path
        if len(failed_paths) < 4 and path not in failed_paths:
            failed_paths.add(path)
            return httpx.Response(503)
        if path == "/search.json":
            search_index += 1
            ordinal = search_index * 10
            family = request.url.params.get("q", "").removeprefix("isbn:")
            rows = []
            for offset in (1, 2):
                edition_key = f"/books/OL{ordinal + offset}M"
                work_key = f"/works/OL{ordinal + offset}W"
                detail_context[edition_key + ".json"] = (
                    work_key,
                    family or None,
                )
                detail_context[work_key + ".json"] = (
                    work_key,
                    None,
                )
                edition_row: dict[str, object] = {
                    "key": edition_key,
                }
                if family:
                    edition_row["isbn"] = [family]
                rows.append(
                    {
                        "key": work_key,
                        "author_name": ["Ada Example"],
                        "editions": {"docs": [edition_row]},
                    }
                )
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"docs": rows},
            )
        key = path.removesuffix(".json")
        work_key, family = detail_context[path]
        if key.startswith("/books/"):
            payload = _edition(key, work=work_key)
            if family is not None:
                payload["isbn_10"] = []
                payload["isbn_13"] = [family]
        else:
            payload = _work(key)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=payload,
        )

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    provider_result = provider.observe(plan)
    observation = (provider_result).observation
    assert 49 == request_count
    assert provider_result.evidence.attempt_records is not None
    assert 49 == len(provider_result.evidence.attempt_records)
    assert provider_result.evidence.attempt_records is not None
    assert 4 == sum(
        record.outcome == "retry" for record in provider_result.evidence.attempt_records
    )
    assert 9 == len(observation.lanes)


@pytest.mark.parametrize(
    "retry_after",
    ("61", "2, 3"),
)
def test_invalid_503_retry_after_falls_back_to_fresh_jitter(retry_after) -> None:
    responses = [
        httpx.Response(
            503,
            headers={"retry-after": retry_after},
        ),
        httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": []},
        ),
    ]
    delays: list[float] = []
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: responses.pop(0)  # noqa: B023 - closure is created and consumed within this iteration
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=delays.append,
        jitter=lambda: 0.25,
    )
    assert (
        ProviderStatus.NO_MATCH
        == (provider.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    assert [1.25] == delays


def test_interrupted_body_bytes_are_charged_before_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                stream=_InterruptedStream(),
            )
        return httpx.Response(429)

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        1,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
        sleep=lambda _: None,
        jitter=lambda: 0.0,
    )
    assert (
        ProviderStatus.RATE_LIMITED
        == (provider.observe(_isbn_lookup((_isbn(),)))).observation.status
    )
    assert 2 == calls
    assert 48 == provider._remaining_response_bytes


def test_malformed_optional_cover_ids_do_not_erase_metadata() -> None:
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": {
            **_edition("/books/OL1M"),
            "covers": ["not-an-id"],
        },
        "/works/OL5W.json": {
            **_work(),
            "covers": [-1],
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=values[request.url.path],
        )

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(transport=httpx.MockTransport(handler)),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation
    assert ProviderStatus.CANDIDATES == observation.status
    assert 1 == len(observation.candidates)
    assert 2 == len(provider_result.cover_descriptors)
    assert all(value.url is None for value in provider_result.cover_descriptors)
    assert (CoverSourceState.REJECTED, CoverSourceState.REJECTED) == tuple(
        value.terminal_state for value in provider_result.cover_descriptors
    )
    assert not (
        {
            ProviderEvidenceKind.EDITION_COVER_ID,
            ProviderEvidenceKind.WORK_COVER_ID,
        }
        & {value.kind for value in observation.candidates[0].evidence}
    )


@pytest.mark.parametrize(
    ("cover_fields", "expected_url", "expected_state"),
    (
        ({}, None, None),
        ({"covers": None}, None, None),
        ({"covers": []}, None, None),
        (
            {"covers": [1]},
            f"{OPEN_LIBRARY_COVERS_ORIGIN}/b/id/1-L.jpg?default=false",
            None,
        ),
    ),
)
def test_missing_and_empty_cover_ids_remain_absent_while_valid_ids_request(
    cover_fields, expected_url, expected_state
) -> None:
    edition = {**_edition("/books/OL1M"), **cover_fields}
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": edition,
        "/works/OL5W.json": _work(),
    }
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=values[request.url.path],  # noqa: B023 - closure is created and consumed within this iteration
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    descriptor = provider_result.cover_descriptors[0]
    assert expected_url == descriptor.url
    assert expected_state == descriptor.terminal_state


@pytest.mark.parametrize(
    "covers",
    (
        ["not-an-id"],
        [True],
        [0],
        [-1],
        [2_147_483_648],
        [1, "not-an-id"],
        {"not": "a list"},
    ),
)
def test_invalid_present_cover_ids_are_rejected(covers) -> None:
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": {
            **_edition("/books/OL1M"),
            "covers": covers,
        },
        "/works/OL5W.json": _work(),
    }
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=values[request.url.path],  # noqa: B023 - closure is created and consumed within this iteration
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation
    assert ProviderStatus.CANDIDATES == observation.status
    assert (
        CoverSourceState.REJECTED == provider_result.cover_descriptors[0].terminal_state
    )
    assert provider_result.cover_descriptors[0].url is None
    assert ProviderEvidenceKind.EDITION_COVER_ID not in {
        value.kind for value in observation.candidates[0].evidence
    }


def test_cover_context_at_its_limit_is_retained() -> None:
    context_isbns = [
        "9780306406157",
        "9780306406003",
        "9780306406010",
        "9780306406027",
        "9780306406034",
        "9780306406041",
        "9780306406058",
        "9780306406065",
    ]
    edition = {
        **_edition("/books/OL1M", cover=1),
        "isbn_10": [],
        "isbn_13": context_isbns,
        "publish_date": None,
        "publishers": [],
        "publish_places": [],
        "physical_format": None,
        "languages": [
            {"key": f"/languages/{value}"}
            for value in ("aa", "ab", "ac", "ad", "ae", "af", "ag", "ah")
        ],
    }
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": edition,
        "/works/OL5W.json": _work(),
    }

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=values[request.url.path],
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES == observation.status
    edition_descriptor = provider_result.cover_descriptors[0]
    assert edition_descriptor.terminal_state is None
    assert edition_descriptor.provenance is not None
    assert 16 == len(edition_descriptor.provenance.context)


def test_cover_context_overflow_retains_metadata_with_oversized_cover() -> None:
    context_isbns = [
        "9780306406157",
        "9780306406003",
        "9780306406010",
        "9780306406027",
        "9780306406034",
        "9780306406041",
        "9780306406058",
        "9780306406065",
    ]
    edition = {
        **_edition("/books/OL1M", cover=1),
        "isbn_10": [],
        "isbn_13": context_isbns,
        "publish_date": None,
        "languages": [
            {"key": f"/languages/{value}"}
            for value in ("aa", "ab", "ac", "ad", "ae", "af", "ag", "ah")
        ],
        "publishers": [f"Publisher {value}" for value in range(8)],
        "publish_places": [],
        "physical_format": None,
    }
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": edition,
        "/works/OL5W.json": _work(),
    }

    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=values[request.url.path],
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES == observation.status
    assert 1 == len(observation.candidates)
    edition_descriptor = provider_result.cover_descriptors[0]
    assert CoverSourceState.OVERSIZED == edition_descriptor.terminal_state
    assert edition_descriptor.provenance is not None
    assert () == edition_descriptor.provenance.context


@pytest.mark.parametrize(
    ("length", "terminal_state"),
    (
        (280, None),
        (281, CoverSourceState.OVERSIZED),
        (512, CoverSourceState.OVERSIZED),
        (513, CoverSourceState.OVERSIZED),
    ),
)
def test_cover_context_scalar_bound_preserves_metadata(length, terminal_state) -> None:
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": {
            **_edition("/books/OL1M", cover=1),
            "publish_country": "C" * length,
        },
        "/works/OL5W.json": _work(),
    }
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=values[request.url.path],  # noqa: B023 - closure is created and consumed within this iteration
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES == observation.status
    assert 1 == len(observation.candidates)
    edition_descriptor = provider_result.cover_descriptors[0]
    assert terminal_state == edition_descriptor.terminal_state
    assert edition_descriptor.provenance is not None
    if terminal_state is None:
        assert "C" * length in {
            value.value for value in edition_descriptor.provenance.context
        }
    else:
        assert () == edition_descriptor.provenance.context


def test_malformed_cover_ids_precede_oversized_context() -> None:
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": {
            **_edition("/books/OL1M"),
            "covers": ["not-an-id"],
            "publish_country": "C" * 281,
        },
        "/works/OL5W.json": {
            **_work(),
            "covers": [True],
            "publish_country": "C" * 281,
        },
    }
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=values[request.url.path],
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )
    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES == observation.status
    assert (CoverSourceState.REJECTED, CoverSourceState.REJECTED) == tuple(
        value.terminal_state for value in provider_result.cover_descriptors
    )
    assert all(value.url is None for value in provider_result.cover_descriptors)
    assert not (
        {
            ProviderEvidenceKind.EDITION_COVER_ID,
            ProviderEvidenceKind.WORK_COVER_ID,
        }
        & {value.kind for value in observation.candidates[0].evidence}
    )


@pytest.mark.parametrize(
    "value",
    (
        "/Users/private",
        "/Users/private/" + "x" * 500,
        "sk-abcdefgh" + "x" * 502,
    ),
)
def test_unsafe_cover_context_remains_provider_malformed(value) -> None:
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": {
            **_edition("/books/OL1M", cover=1),
            "publish_country": value,
        },
        "/works/OL5W.json": _work(),
    }
    provider = OpenLibraryMetadataProvider(
        "open_library",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=values[request.url.path],  # noqa: B023 - closure is created and consumed within this iteration
                )
            )
        ),
        65_536,
        pacer=OpenLibraryPacer(sleep=lambda _: None),
    )

    provider_result = provider.observe(_isbn_lookup((_isbn(),)))
    observation = (provider_result).observation

    assert ProviderStatus.MALFORMED == observation.status
    assert () == observation.candidates
    assert () == provider_result.cover_descriptors


def test_conflicting_detail_relation_preserves_bounded_candidates() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        values = {
            "/search.json": _search("/books/OL1M", "/books/OL2M"),
            "/books/OL1M.json": _edition("/books/OL1M"),
            "/works/OL5W.json": _work(),
            "/books/OL2M.json": {
                **_edition("/books/OL2M"),
                "works": [{"key": "/works/OL9W"}],
            },
        }
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=values[request.url.path],
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_isbn_lookup((_isbn(),)))
        .observation
    )
    assert ProviderStatus.CANDIDATES == observation.status
    assert 2 == len(observation.candidates)
    assert 1 == sum(
        evidence.kind is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
        for candidate in observation.candidates
        for evidence in candidate.evidence
    )
    assert [
        "/search.json",
        "/books/OL1M.json",
        "/works/OL5W.json",
        "/books/OL2M.json",
    ] == [request.url.path for request in requests]


@pytest.mark.parametrize(
    "relations",
    (
        [{"key": "/works/OL9W"}],
        [
            {"key": "/works/OL5W"},
            {"key": "/works/OL5W"},
        ],
        [
            {"key": "/works/OL5W"},
            {"key": "/works/OL9W"},
        ],
    ),
)
def test_conflicting_or_multiple_work_relations_keep_only_edition_authority(
    relations,
) -> None:
    values = {
        "/search.json": _search("/books/OL1M"),
        "/books/OL1M.json": {
            **_edition("/books/OL1M"),
            "works": relations,
        },
        "/works/OL5W.json": _work(),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=values[request.url.path],  # noqa: B023 - closure is created and consumed within this iteration
        )

    observation = (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_isbn_lookup((_isbn(),)))
        .observation
    )
    assert ProviderStatus.CANDIDATES == observation.status
    candidate = observation.candidates[0]
    assert () == candidate.identity_bindings
    assert all(claim.scope is ClaimScope.EDITION for claim in candidate.claims)
    assert all(evidence.scope is ClaimScope.EDITION for evidence in candidate.evidence)
    assert [
        "edition_work_relation",
        "edition_work_relation_contradiction",
    ] == sorted(
        evidence.kind.value
        for evidence in candidate.evidence
        if evidence.kind
        in {
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
        }
    )
    publication = next(
        evidence
        for evidence in candidate.evidence
        if evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
    )
    assert "2026-01-02" == publication.value
    assert DatePrecision.DAY == publication.date_precision
    assert (
        PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
        == publication.links[0].publication_date_relation
    )
    assert not any(
        claim.kind is ClaimKind.PUBLICATION_DATE for claim in candidate.claims
    )


def test_edition_scalar_and_search_list_publish_dates_are_contextual() -> None:
    edition = _edition("/books/OL1M")
    assert 0 == open_library_metadata._optional_projection_rejections(
        edition,
        schema="edition_detail",
    )
    assert 0 == open_library_metadata._optional_projection_rejections(
        {"key": "/works/OL1W", "publish_date": ["2026"]},
        search_projection=True,
    )


@pytest.mark.parametrize("invalid", (7, {"value": "2026"}))
def test_optional_rejections_and_duplicate_search_identities_fail_closed(
    invalid,
) -> None:
    assert 1 == open_library_metadata._optional_projection_rejections(
        {"key": "/works/OL1W", "publish_date": [invalid]},
        search_projection=True,
    )
    source = _search("/books/OL1M", "/books/OL1M")
    with pytest.raises(ValueError):
        open_library_metadata._admitted_candidates(source, "9780306406157")
    with pytest.raises(ValueError):
        open_library_metadata._admitted_work_candidates(source)


def test_optional_projection_diagnostics_are_field_bounded() -> None:
    assert 1 == open_library_metadata._optional_projection_rejections(
        {"key": "/works/OL1W", "contributions": ["Ada"]}
    )


def test_optional_projection_shapes_are_exact_and_local() -> None:
    diagnostic = open_library_metadata._optional_projection_rejections
    assert 0 == diagnostic({"key": "/works/OL1W", "covers": [1, 2]})
    assert 1 == diagnostic({"key": "/works/OL1W", "covers": ["1"]})
    scalars = (
        "edition_name",
        "edition_statement",
        "translation_of",
        "description",
        "copyright_date",
        "number_of_pages",
    )
    malformed: dict[str, object] = {
        key: 1 for key in scalars if key != "number_of_pages"
    }
    malformed["number_of_pages"] = "1"
    assert len(scalars) == diagnostic(
        {"key": "/books/OL1M", **malformed},
        schema="edition_detail",
    )
    assert 2 == diagnostic(
        {
            "key": "/books/OL1M",
            "authors": [{"key": "/authors/bad"}],
            "languages": [{"key": "/languages/toolong"}],
        },
        schema="edition_detail",
    )
    assert 1 == diagnostic(
        {"key": "/works/OL1W", "author_key": ["bad"]},
        search_projection=True,
    )
    assert 0 == diagnostic(_work())
    assert 1 == diagnostic({"key": "/works/OL1W", "authors": [{"key": 7}]})
    assert 1 == diagnostic(
        {"key": "/books/OL1M", "languages": [{"key": 7}]},
        schema="edition_detail",
    )
    assert 1 == diagnostic({"key": "/works/OL1W", "covers": [0]})
    assert 1 == diagnostic(
        {"key": "/books/OL1M", "number_of_pages": 0},
        schema="edition_detail",
    )
    assert 1 == diagnostic(
        {"key": "/books/OL1M", "edition_name": "bad\x00name"},
        schema="edition_detail",
    )
    scalar_fields = (
        "publish_date",
        "physical_format",
        "pagination",
        "by_statement",
        "imprint",
    )
    document = {"key": "/books/OL1M", **{field: 7 for field in scalar_fields}}
    assert 5 == open_library_metadata._optional_projection_rejections(
        document,
        schema="edition_detail",
    )
    hostile = [object() for _ in range(open_library_metadata._MAX_VALUES + 1)]
    assert 1 == open_library_metadata._optional_projection_rejections(
        {"key": "/works/OL1W", "subjects": hostile}
    )


def test_optional_string_members_use_retention_bounds_without_double_counts() -> None:
    diagnostic = open_library_metadata._optional_projection_rejections
    unsafe = "bad\x00value"
    oversized = "x" * (open_library_metadata.MAX_SCALAR_CHARACTERS + 1)
    document = {
        "key": "/works/OL1W",
        "subjects": ["valid", unsafe, oversized, 7],
        "author_name": ["Ada", unsafe],
        "author_key": ["OL7A", "bad"],
        "languages": [
            {"key": "/languages/eng"},
            {"key": "/languages/toolong"},
        ],
        "authors": [
            {"key": "/authors/OL7A"},
            {"key": "/authors/bad"},
        ],
    }
    assert 10 == diagnostic(document, search_projection=True)
    assert ("valid",) == open_library_metadata._optional_string_list(
        document["subjects"], open_library_metadata._MAX_VALUES
    )


_EDITION_OPTIONAL_FIELDS = (
    "publish_date",
    "physical_format",
    "pagination",
    "by_statement",
    "imprint",
    "edition_name",
    "edition_statement",
    "translation_of",
    "description",
    "copyright_date",
    "number_of_pages",
)


@pytest.mark.parametrize(
    "field",
    (*_EDITION_OPTIONAL_FIELDS, "first_publish_date"),
)
def test_explicit_null_optional_scalars_are_absent_not_rejected(field) -> None:
    edition_fields = _EDITION_OPTIONAL_FIELDS
    assert 0 == open_library_metadata._optional_projection_rejections(
        {"key": "/books/OL1M", **dict.fromkeys(edition_fields)},
        schema="edition_detail",
    )
    assert 0 == open_library_metadata._optional_projection_rejections(
        {"key": "/works/OL1W", "first_publish_date": None}
    )
    value: object = "bad\x00value"
    if field == "number_of_pages":
        value = "one"
    assert 1 == open_library_metadata._optional_projection_rejections(
        {
            "key": ("/works/OL1W" if field == "first_publish_date" else "/books/OL1M"),
            field: value,
        },
        schema=("work_detail" if field == "first_publish_date" else "edition_detail"),
    )


def test_search_author_keys_are_bare_canonical_and_index_local() -> None:
    assert (
        ("Ada", "/authors/OL7A"),
        ("Broken", None),
        ("Zed", "/authors/OL9A"),
        ("Unsafe", None),
    ) == open_library_metadata._search_authors(
        ["Ada", "Broken", "Zed", "Unsafe"],
        ["OL7A", "/authors/OL8A", "OL9A", "bad\x00key"],
    )
    assert 1 == open_library_metadata._optional_projection_rejections(
        {
            "key": "/works/OL1W",
            "author_key": ["OL7A", "/authors/OL8A"],
        },
        search_projection=True,
    )
    assert (("Ada", "/authors/OL7A"),) == open_library_metadata._search_authors(
        ["Ada", "Ada"], ["OL7A", "OL9A"]
    )


def test_duplicate_work_rows_with_different_editions_fail_closed() -> None:
    source = {
        "docs": [
            _search("/books/OL1M")["docs"][0],
            _search("/books/OL2M")["docs"][0],
        ]
    }
    with pytest.raises(ValueError):
        open_library_metadata._admitted_candidates(source, "9780306406157")
    with pytest.raises(ValueError):
        open_library_metadata._admitted_work_candidates(source)


@pytest.mark.parametrize("after_descriptor", (False, True))
def test_later_lane_failure_retains_only_the_old_boundary_descriptors(
    monkeypatch, after_descriptor
):
    second = _isbn("9780140328721")
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", "Current title", ()), (_isbn(), second)
    )
    cause = RuntimeError("second lane")
    seen = []
    searches = 0
    families = tuple(lane.matched_family for lane in plan.lanes)

    def dispatch(request):
        nonlocal searches
        if request.url.path == "/search.json":
            searches += 1
            if searches == 2 and not after_descriptor:
                raise cause
            value = _search(f"/books/OL{searches}M", work=f"/works/OL{searches}W")
            value["docs"][0]["editions"]["docs"][0]["isbn"] = [families[searches - 1]]
        elif request.url.path.startswith("/books/"):
            value = _edition(
                request.url.path.removesuffix(".json"),
                work=f"/works/OL{searches}W",
                cover=searches,
            )
            value["isbn_10"] = []
            value["isbn_13"] = [families[searches - 1]]
        else:
            value = _work(f"/works/OL{searches}W")
        return httpx.Response(200, json=value)

    provider = OpenLibraryMetadataProvider(
        "catalogue",
        httpx.Client(
            transport=httpx.MockTransport(dispatch), headers={"User-Agent": "synthetic"}
        ),
        65536,
        pacer=OpenLibraryPacer(clock=lambda: 0.0, sleep=lambda _: None),
        clock=lambda: 0.0,
        wall_clock=lambda: 1700000000.0,
    )
    qualify = open_library_metadata.qualify_provider_observation

    def qualification(observation, lookup):
        # This adapter-local field is the pre-1005 dispatch-boundary projection.
        seen.append(provider._cover_descriptors)
        if len(seen) == 2:
            raise cause
        return qualify(observation, lookup)

    monkeypatch.setattr(
        open_library_metadata, "qualify_provider_observation", qualification
    )
    with pytest.raises(ProviderObservationFailure) as caught:
        provider.observe(plan)
    assert searches == 2
    assert seen[0]
    expected = seen[1] if after_descriptor else ()
    assert caught.value.snapshot.cover_descriptors == expected
    if after_descriptor:
        assert expected and expected != seen[0]
    assert caught.value.__cause__ is cause
    assert caught.value.snapshot.evidence.accessed_at == "2023-11-14T22:13:20.000000Z"
