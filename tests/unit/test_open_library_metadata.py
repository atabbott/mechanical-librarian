from __future__ import annotations

import unittest

import httpx

from mechanical_librarian.adapters.providers.open_library_metadata import (
    OPEN_LIBRARY_COVERS_ORIGIN,
    OPEN_LIBRARY_SEARCH_URL,
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    LocalClaim,
    ProviderEvidenceKind,
    ProviderStatus,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverSourceKind,
    cover_source_id,
)
from mechanical_librarian.infrastructure.security.credentials import (
    open_library_client,
)


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


class OpenLibraryMetadataTests(unittest.TestCase):
    def test_exact_search_details_rich_evidence_and_covers_are_bounded(self) -> None:
        now = [0.0]
        request_starts: list[float] = []
        requests: list[httpx.Request] = []

        def sleep(seconds: float) -> None:
            now[0] += seconds

        responses = {
            "/search.json": _search(
                "/books/OL20M",
                "/books/OL10M",
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
        observation = provider.observe((_isbn(), _title()))

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(
            [
                "open-library-edition-OL10M",
                "open-library-edition-OL20M",
            ],
            [candidate.identity for candidate in observation.candidates],
        )
        self.assertEqual(
            [
                "/search.json",
                "/books/OL10M.json",
                "/works/OL5W.json",
                "/books/OL20M.json",
            ],
            [request.url.path for request in requests],
        )
        self.assertEqual([0.0, 1.0, 2.0, 3.0], request_starts)
        search = requests[0]
        self.assertEqual(OPEN_LIBRARY_SEARCH_URL, str(search.url.copy_with(query=None)))
        self.assertEqual("isbn:9780306406157", search.url.params["q"])
        self.assertEqual("3", search.url.params["limit"])
        self.assertEqual(
            "key,editions,editions.key,editions.isbn",
            search.url.params["fields"],
        )
        self.assertEqual(
            "Mechanical-Librarian (operator@example.invalid)",
            search.headers["user-agent"],
        )
        candidate = observation.candidates[0]
        self.assertEqual("open-library-work-OL5W", candidate.work_identity)
        kinds = {value.kind for value in candidate.evidence}
        self.assertTrue(
            {
                ProviderEvidenceKind.AUTHOR_IDENTITY,
                ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
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
        )
        work_title = next(
            claim
            for claim in candidate.claims
            if claim.kind is ClaimKind.TITLE and claim.scope is ClaimScope.WORK
        )
        self.assertEqual(Comparison.AGREEMENT, work_title.links[0].outcome)
        self.assertEqual(
            (
                (
                    cover_source_id(
                        CoverSourceKind.OPEN_LIBRARY,
                        "edition:/books/OL10M:100",
                    ),
                    f"{OPEN_LIBRARY_COVERS_ORIGIN}/b/id/100-L.jpg?default=false",
                ),
                (
                    cover_source_id(
                        CoverSourceKind.OPEN_LIBRARY,
                        "work:/works/OL5W:300",
                    ),
                    f"{OPEN_LIBRARY_COVERS_ORIGIN}/b/id/300-L.jpg?default=false",
                ),
            ),
            provider.cover_descriptors,
        )
        self.assertEqual(
            ProviderStatus.UNAVAILABLE,
            provider.observe((_isbn(),)).status,
        )
        self.assertEqual(4, len(requests))

    def test_zero_or_multiple_usable_families_make_no_request(self) -> None:
        for claims in (
            (),
            (_isbn(applicability="print"),),
            (_isbn(), _isbn("9780140328721")),
        ):
            requests: list[httpx.Request] = []
            provider = OpenLibraryMetadataProvider(
                "open_library",
                httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: requests.append(request)
                        or httpx.Response(500)
                    )
                ),
                65_536,
            )
            with self.subTest(count=len(claims)):
                self.assertEqual(
                    ProviderStatus.INSUFFICIENT_INPUT,
                    provider.observe(claims).status,
                )
                self.assertEqual([], requests)

    def test_isbn_10_and_13_of_one_family_share_one_exact_search(self) -> None:
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
        observation = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        ).observe((ten, thirteen))
        self.assertEqual(ProviderStatus.NO_MATCH, observation.status)
        self.assertEqual(
            tuple(sorted((ten.reference, thirteen.reference))),
            observation.input_references,
        )
        self.assertEqual("isbn:9780306406157", requests[0].url.params["q"])
        self.assertEqual(1, len(requests))

    def test_two_distinct_works_use_the_five_request_maximum_in_order(self) -> None:
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
            ),
            "/works/OL5W.json": _work("/works/OL5W"),
            "/books/OL2M.json": _edition(
                "/books/OL2M",
                work="/works/OL9W",
            ),
            "/works/OL9W.json": _work("/works/OL9W"),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=responses[request.url.path],
            )

        observation = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        ).observe((_isbn(),))
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(
            [
                "/search.json",
                "/books/OL1M.json",
                "/works/OL5W.json",
                "/books/OL2M.json",
                "/works/OL9W.json",
            ],
            [request.url.path for request in requests],
        )

    def test_third_exact_candidate_is_terminal_before_detail_requests(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            value = _search(
                "/books/OL4M",
                "/books/OL3M",
                "/books/OL1M",
                "/books/OL2M",
            )
            value["docs"][0]["editions"]["docs"][0]["isbn"] = [  # type: ignore[index]
                "9780140328721"
            ]
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=value,
            )

        observation = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        ).observe((_isbn(),))
        self.assertEqual(
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            observation.status,
        )
        self.assertEqual((), observation.candidates)
        self.assertEqual(["/search.json"], [request.url.path for request in requests])

    def test_nonmatching_search_rows_are_ignored_without_detail_requests(self) -> None:
        requests: list[httpx.Request] = []
        search = _search("/books/OL1M")
        search["docs"][0]["editions"]["docs"][0]["isbn"] = ["9780140328721"]  # type: ignore[index]

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=search,
            )

        observation = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        ).observe((_isbn(),))
        self.assertEqual(ProviderStatus.NO_MATCH, observation.status)
        self.assertEqual(["/search.json"], [request.url.path for request in requests])

    def test_required_request_failures_are_terminal_and_mapped(self) -> None:
        cases = (
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
        for label, maximum, failure, expected in cases:
            requests: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                if request.url.path == "/search.json":
                    return httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json=_search("/books/OL1M"),
                    )
                return failure()

            provider = OpenLibraryMetadataProvider(
                "open_library",
                httpx.Client(transport=httpx.MockTransport(handler)),
                maximum,
                pacer=OpenLibraryPacer(sleep=lambda _: None),
            )
            with self.subTest(label=label):
                observation = provider.observe((_isbn(),))
                self.assertEqual(expected, observation.status)
                self.assertEqual((), observation.candidates)
                self.assertEqual(
                    ["/search.json", "/books/OL1M.json"],
                    [request.url.path for request in requests],
                )

    def test_malformed_optional_cover_ids_do_not_erase_metadata(self) -> None:
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
        observation = provider.observe((_isbn(),))
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(1, len(observation.candidates))
        self.assertEqual((), provider.cover_descriptors)
        self.assertFalse(
            {
                ProviderEvidenceKind.EDITION_COVER_ID,
                ProviderEvidenceKind.WORK_COVER_ID,
            }
            & {value.kind for value in observation.candidates[0].evidence}
        )

    def test_invalid_required_detail_content_discards_all_candidates(self) -> None:
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

        observation = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        ).observe((_isbn(),))
        self.assertEqual(ProviderStatus.MALFORMED, observation.status)
        self.assertEqual((), observation.candidates)
        self.assertEqual(
            [
                "/search.json",
                "/books/OL1M.json",
                "/works/OL5W.json",
                "/books/OL2M.json",
            ],
            [request.url.path for request in requests],
        )


if __name__ == "__main__":
    unittest.main()
