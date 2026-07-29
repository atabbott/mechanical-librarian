from __future__ import annotations

import gzip
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import httpx

import mechanical_librarian.adapters.providers.open_library_metadata as open_library_metadata
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OPEN_LIBRARY_COVERS_ORIGIN,
    OPEN_LIBRARY_SEARCH_URL,
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    TargetSnapshot,
    metadata_discovery_plan,
    qualify_provider_observation,
)
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
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetFacts,
    default_target_fields,
    isbn_family,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceState,
    CoverSourceKind,
    cover_source_id,
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
    def test_search_author_pairs_keep_safe_names_without_misbinding_keys(self) -> None:
        self.assertEqual(
            (
                ("Ada Example", "/authors/OL7A"),
                ("Zed Example", None),
            ),
            open_library_metadata._search_authors(
                ["Ada Example", "Zed Example"],
                ["/authors/OL7A", "not-an-author-key"],
            ),
        )
        self.assertEqual(
            (),
            open_library_metadata._search_authors(
                [f"Author {index}" for index in range(9)],
                [f"/authors/OL{index + 1}A" for index in range(9)],
            ),
        )

    def test_work_detail_author_identity_survives_unusable_search_and_edition_links(self) -> None:
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
        observation = OpenLibraryMetadataProvider(
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
        ).observe(_work_lookup())

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(3, observation.diagnostics[0].count)
        identity = next(
            value
            for value in observation.candidates[0].evidence
            if value.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY
        )
        self.assertEqual("open-library-author-OL7A", identity.value)
        self.assertEqual(ClaimScope.WORK, identity.scope)
        self.assertEqual("author", identity.role)

    def test_optional_detail_failures_are_local_and_diagnosed_once(self) -> None:
        cases = (
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
        for name, document, field, invalid in cases:
            with self.subTest(name=name):
                edition = _edition("/books/OL1M")
                work = _work()
                target = edition if document == "edition" else work
                target[field] = invalid
                values = {
                    "/search.json": _search("/books/OL1M"),
                    "/books/OL1M.json": edition,
                    "/works/OL5W.json": work,
                }
                observation = OpenLibraryMetadataProvider(
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
                ).observe(_isbn_lookup((_isbn(),)))

                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                self.assertEqual(
                    (1,),
                    tuple(value.count for value in observation.diagnostics),
                )
                if field == "description":
                    description = next(
                        claim
                        for claim in observation.candidates[0].claims
                        if claim.kind is ClaimKind.DESCRIPTION
                    )
                    self.assertIs(FieldState.MALFORMED, description.state)
                    self.assertIsNone(description.value)
                if field == "by_statement":
                    self.assertNotIn(
                        ProviderEvidenceKind.RESPONSIBILITY_STATEMENT,
                        {
                            value.kind
                            for value in observation.candidates[0].evidence
                        },
                    )
                if field == "authors":
                    self.assertNotIn(
                        "open-library-author-OL7A",
                        {
                            value.value
                            for value in observation.candidates[0].evidence
                            if value.kind
                            is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY
                        },
                    )

    def test_nested_work_author_evidence_persists_with_exact_source_semantics(self) -> None:
        values = {
            "/search.json": _search("/books/OL1M"),
            "/books/OL1M.json": _edition("/books/OL1M"),
            "/works/OL5W.json": _work(),
        }
        observation = OpenLibraryMetadataProvider(
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
        ).observe(_isbn_lookup((_isbn(),)))
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

        self.assertEqual(report.to_json(), persisted)
        expected_matrix = (
            (ProviderEvidenceKind.COPYRIGHT_DATE, ClaimScope.EDITION, None, 20),
            (
                ProviderEvidenceKind.DEWEY_CLASSIFICATION,
                ClaimScope.EDITION,
                None,
                9,
            ),
            (
                ProviderEvidenceKind.EDITION_WORK_RELATION,
                ClaimScope.EDITION,
                None,
                0,
            ),
            (ProviderEvidenceKind.LAST_MODIFIED, ClaimScope.EDITION, None, 2),
            (
                ProviderEvidenceKind.LC_CLASSIFICATION,
                ClaimScope.EDITION,
                None,
                10,
            ),
            (ProviderEvidenceKind.LCCN, ClaimScope.EDITION, None, 8),
            (ProviderEvidenceKind.OCLC, ClaimScope.EDITION, None, 7),
            (ProviderEvidenceKind.PAGE_COUNT, ClaimScope.EDITION, None, 21),
            (ProviderEvidenceKind.PAGINATION, ClaimScope.EDITION, None, 19),
            (
                ProviderEvidenceKind.PHYSICAL_FORMAT,
                ClaimScope.EDITION,
                None,
                18,
            ),
            (
                ProviderEvidenceKind.PUBLICATION_PLACE,
                ClaimScope.EDITION,
                None,
                11,
            ),
            (ProviderEvidenceKind.REVISION, ClaimScope.EDITION, None, 1),
            (ProviderEvidenceKind.WIKIDATA, ClaimScope.EDITION, None, 16),
            (ProviderEvidenceKind.AUTHOR_IDENTITY, ClaimScope.WORK, None, 5),
            (
                ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
                ClaimScope.WORK,
                "author",
                6,
            ),
            (
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                ClaimScope.WORK,
                None,
                22,
            ),
            (ProviderEvidenceKind.LAST_MODIFIED, ClaimScope.WORK, None, 4),
            (ProviderEvidenceKind.PERSON, ClaimScope.WORK, None, 13),
            (ProviderEvidenceKind.PLACE, ClaimScope.WORK, None, 14),
            (ProviderEvidenceKind.REVISION, ClaimScope.WORK, None, 3),
            (ProviderEvidenceKind.SUBJECT, ClaimScope.WORK, None, 12),
            (ProviderEvidenceKind.TIME, ClaimScope.WORK, None, 15),
            (ProviderEvidenceKind.WIKIDATA, ClaimScope.WORK, None, 17),
        )
        self.assertEqual(
            expected_matrix,
            tuple(
                (value.kind, value.scope, value.role, value.source_ordinal)
                for value in reopened.providers[0].candidates[0].evidence
            ),
        )
        identity = next(
            value
            for value in reopened.providers[0].candidates[0].evidence
            if value.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY
            and value.value == "open-library-author-OL7A"
        )
        self.assertEqual(ClaimScope.WORK, identity.scope)
        self.assertEqual("author", identity.role)
        self.assertGreaterEqual(identity.source_ordinal, 0)
        self.assertEqual(
            [
                value.source_ordinal
                for value in report.providers[0].candidates[0].evidence
            ],
            [
                value.source_ordinal
                for value in reopened.providers[0].candidates[0].evidence
            ],
        )

    def test_detail_freeze_replay_preserves_empty_containers_and_decimal(self) -> None:
        value = {"empty_mapping": {}, "empty_sequence": [], "nested": [{}, []], "decimal": Decimal("1.25")}
        frozen = open_library_metadata._freeze_detail(value)
        self.assertEqual(value, open_library_metadata._thaw_detail(frozen))
    def test_first_dispatch_captures_injected_access_time(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"numFound": 0, "docs": []},
            )

        observation = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                headers={"User-Agent": "Mechanical-Librarian/test"},
                transport=httpx.MockTransport(handler),
            ),
            65_536,
            wall_clock=lambda: 1_700_000_000.125,
        ).observe(_isbn_lookup((_isbn(),)))
        self.assertEqual(ProviderStatus.NO_MATCH, observation.status)
        self.assertEqual(
            "2023-11-14T22:13:20.125000Z", observation.accessed_at
        )
        self.assertEqual(1, len(requests))

    def test_publication_field_accepts_year_month_day_and_signed_offset(self) -> None:
        for lexical in (
            "2024",
            "2024-03",
            "2024-03-05",
            "2024-03-05T10:30:00-04:00",
        ):
            with self.subTest(lexical=lexical):
                self.assertEqual(
                    (FieldState.VALID, lexical),
                    open_library_metadata._field(
                        ClaimKind.PUBLICATION_DATE,
                        lexical,
                    ),
                )

    def test_work_plan_persists_ordered_qualified_creators_through_replay(
        self,
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
            "/books/OL1M.json": _edition(
                "/books/OL1M", work="/works/OL5W"
            ),
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
        observation = provider.observe(plan)
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(ProviderQueryKind.WORK, observation.query_kind)
        self.assertEqual((title.reference,), observation.input_references)
        self.assertEqual(
            ("Zed Author", "Ada Author"),
            tuple(
                claim.value
                for claim in observation.candidates[0].claims
                if claim.kind is ClaimKind.CREATOR
                and claim.state.value == "valid"
                and claim.role == "author"
            ),
        )

        report = MetadataReport.create(
            target=TargetFacts("primary", "ebooks", "item-1", fields),
            file=FileFacts(0, "a" * 64, "b" * 64),
            local_claims=(title,),
            local_reasons=("target_metadata_title",),
            providers=(observation,),
        )
        replay = MetadataReport.from_json(report.to_json())
        self.assertEqual(
            ("Zed Author", "Ada Author"),
            tuple(
                claim.value
                for claim in replay.providers[0].candidates[0].claims
                if claim.kind is ClaimKind.CREATOR
                and claim.state.value == "valid"
                and claim.role == "author"
            ),
        )

    def test_work_plan_rejects_reordered_duplicate_invalid_and_overflow_authors(
        self,
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
        for label, authors, expected in (
            (
                "reordered",
                ["Ada Author", "Zed Author"],
                ProviderStatus.NO_MATCH,
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
        ):
            requests: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request.url.path)
                if request.url.path == "/search.json":
                    value = {
                        "docs": [
                            {
                                "key": "/works/OL5W",
                                "author_name": authors,
                                "editions": {
                                    "docs": [{"key": "/books/OL1M"}]
                                },
                            }
                        ]
                    }
                elif request.url.path == "/books/OL1M.json":
                    value = _edition(
                        "/books/OL1M", work="/works/OL5W"
                    )
                else:
                    self.assertEqual("/works/OL5W.json", request.url.path)
                    value = _work("/works/OL5W")
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=value,
                )

            observation = OpenLibraryMetadataProvider(
                "open_library",
                httpx.Client(transport=httpx.MockTransport(handler)),
                65_536,
                pacer=OpenLibraryPacer(sleep=lambda _: None),
            ).observe(plan)
            with self.subTest(label=label):
                self.assertEqual(expected, observation.status)
                self.assertEqual(
                    1 if expected is ProviderStatus.CANDIDATES else 0,
                    len(observation.candidates),
                )
                self.assertEqual(3, len(requests))

    def test_work_lookup_uses_complete_title_and_authors_then_retains_only_neutral_work_evidence(self) -> None:
        requests: list[httpx.Request] = []
        values = {
            "/search.json": {
                "docs": [
                    {
                        "key": "/works/OL5W",
                        "author_name": ["Ada Example"],
                        "author_key": ["/authors/OL7A"],
                        "publisher": ["Search Press"],
                        "publish_date": ["not a typed publication date"],
                        "language": ["eng"],
                        "edition_key": ["OL1M"],
                        "editions": {"docs": [{"key": "/books/OL1M"}]},
                    }
                ]
            },
            "/books/OL1M.json": _edition("/books/OL1M", cover=100),
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
        observation = provider.observe(lookup)
        self.assertEqual(ProviderQueryKind.WORK, observation.query_kind)
        self.assertEqual("Local work title", requests[0].url.params["title"])
        self.assertEqual("Ada Example", requests[0].url.params["author"])
        self.assertEqual(
            "key,author_name,author_key,publisher,publish_date,language,edition_key,editions,editions.key",
            requests[0].url.params["fields"],
        )
        qualified = qualify_provider_observation(observation, lookup)
        self.assertEqual(ProviderStatus.CANDIDATES, qualified.status)
        candidate = qualified.candidates[0]
        self.assertFalse(
            {ClaimKind.ISBN, ClaimKind.ASIN, ClaimKind.PUBLISHER}
            & {claim.kind for claim in candidate.claims}
        )
        self.assertFalse(
            {ClaimKind.GENRE, ClaimKind.TAG}
            & {
                claim.kind
                for claim in candidate.claims
                if claim.state.value == "valid"
            }
        )
        self.assertIn(
            ProviderEvidenceKind.SUBJECT,
            {value.kind for value in observation.candidates[0].evidence},
        )
        self.assertIn(
            ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
            {value.kind for value in observation.candidates[0].evidence},
        )
        self.assertNotIn(
            ProviderEvidenceKind.SUBJECT,
            {value.kind for value in candidate.evidence},
        )
        self.assertIn(
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            {value.kind for value in candidate.evidence},
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
        self.assertEqual(
            {
                (ProviderEvidenceKind.SEARCH_PUBLISHER, "Search Press", ClaimScope.WORK),
                (ProviderEvidenceKind.SEARCH_PUBLICATION_DATE, "not a typed publication date", ClaimScope.WORK),
                (ProviderEvidenceKind.SEARCH_LANGUAGE, "eng", ClaimScope.WORK),
                (ProviderEvidenceKind.SEARCH_EDITION, "OL1M", ClaimScope.WORK),
            },
            search_evidence,
        )

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
        observation = provider.observe(_isbn_lookup((_isbn(), _title())))

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
        self.assertEqual(3, len(provider.cover_descriptors))
        self.assertEqual(
            {
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
            },
            {value.source_id for value in provider.cover_descriptors},
        )
        self.assertTrue(
            all(
                value.provenance is not None
                and value.provenance.provider == "open_library"
                for value in provider.cover_descriptors
            )
        )
        self.assertEqual(
            ProviderStatus.UNAVAILABLE,
            provider.observe(_isbn_lookup((_isbn(),))).status,
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
                    provider.observe(_isbn_lookup(claims)).status,
                )
                self.assertEqual([], requests)

    def test_discovery_plan_searches_each_valid_family(self) -> None:
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
        observation = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                headers={"User-Agent": "Mechanical-Librarian/test"},
                transport=httpx.MockTransport(handler),
            ),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        ).observe(plan)
        self.assertEqual(2, len(requests))
        self.assertEqual(2, len(observation.lanes))
        self.assertEqual(
            {"isbn:9780306406157", "isbn:9780140328721"},
            {request.url.params["q"] for request in requests},
        )

    def test_terminal_detail_failure_finalizes_reserved_details(self) -> None:
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path == "/search.json":
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=_search("/books/OL1M", "/books/OL2M"),
                )
            self.assertEqual("/books/OL1M.json", request.url.path)
            return httpx.Response(404)

        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()), (_isbn(),)
        )
        observation = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        ).observe(plan)

        self.assertEqual(["/search.json", "/books/OL1M.json"], requests)
        self.assertEqual(ProviderStatus.UNAVAILABLE, observation.status)
        self.assertEqual(4, observation.request_accounting.logical_requests)
        self.assertEqual(2, observation.request_accounting.initial_dispatches)
        self.assertEqual(2, observation.request_accounting.undispatched_incomplete)

    def test_discovery_oversized_lane_stops_remaining_requests_at_exact_bound(
        self,
    ) -> None:
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()),
            (_isbn(), _isbn("9780140328721", applicability="print")),
        )
        payload = b'{"docs":[]}'

        for oversized in (False, True):
            requests: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=payload + (b" " if oversized else b""),
                )

            observation = OpenLibraryMetadataProvider(
                "open_library",
                httpx.Client(
                    headers={"User-Agent": "Mechanical-Librarian/test"},
                    transport=httpx.MockTransport(handler),
                ),
                len(payload),
                pacer=OpenLibraryPacer(sleep=lambda _: None),
            ).observe(plan)
            with self.subTest(oversized=oversized):
                self.assertEqual(1 if oversized else 2, len(requests))
                self.assertEqual(
                    (
                        {ProviderStatus.OVERSIZED, ProviderStatus.INCOMPLETE}
                        if oversized
                        else {ProviderStatus.NO_MATCH}
                    ),
                    {lane.status for lane in observation.lanes},
                )

    def test_conflicting_lane_contributes_no_cover_descriptors(self) -> None:
        plan = metadata_discovery_plan(
            TargetSnapshot("ebook", None, ()),
            (_isbn(), _isbn("9780140328721", applicability="print")),
        )
        search_count = 0
        shared_work_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal search_count, shared_work_count
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
                value = _edition(
                    "/books/OL1M", work="/works/OL6W", cover=200
                )
                value["isbn_13"] = ["9780306406157", "9780140328721"]
            elif request.url.path == "/books/OL9M.json":
                value = _edition("/books/OL9M", cover=100)
                value["isbn_13"] = ["9780306406157", "9780140328721"]
            elif request.url.path == "/works/OL6W.json":
                value = _work("/works/OL6W", cover=400)
            else:
                self.assertEqual("/works/OL5W.json", request.url.path)
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

        observation = provider.observe(plan)

        self.assertEqual(
            {ProviderStatus.CANDIDATES},
            {lane.status for lane in observation.lanes},
        )
        self.assertEqual(1, shared_work_count)
        self.assertEqual(4, len(provider.cover_descriptors))

    def test_discovery_converges_differing_isbn_applicability_and_shared_cover(
        self,
    ) -> None:
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
        work = _work("/works/OL5W", cover=200)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search.json":
                family_query = request.url.params.get("q")
                if family_query is not None:
                    search = _search("/books/OL1M")
                    search["docs"][0]["editions"]["docs"][0]["isbn"] = [  # type: ignore[index]
                        family_query.removeprefix("isbn:")
                    ]
                    value = search
                else:
                    value = {
                        "docs": [
                            {
                                "key": "/works/OL5W",
                                "author_name": ["Zed Author", "Ada Author"],
                                "editions": {
                                    "docs": [{"key": "/books/OL1M"}]
                                },
                            }
                        ]
                    }
            elif request.url.path == "/books/OL1M.json":
                value = edition
            else:
                self.assertEqual("/works/OL5W.json", request.url.path)
                value = work
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json=value,
            )

        provider = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        observation = provider.observe(plan)

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(1, len(observation.candidates))
        self.assertEqual(
            {ProviderStatus.CANDIDATES},
            {lane.status for lane in observation.lanes},
        )
        self.assertEqual(
            tuple(sorted(lane.identity for lane in plan.lanes)),
            observation.convergence[0].lane_identities,
        )
        lanes = {lane.matched_family: lane for lane in observation.lanes}
        self.assertEqual((ebook.reference,), lanes["9780306406157"].input_references)
        self.assertEqual(
            (print_only.reference,),
            lanes["9780140328721"].input_references,
        )
        self.assertEqual((title.reference,), lanes[None].input_references)
        self.assertEqual(
            3,
            len(
                {
                    candidate.reference
                    for lane in observation.lanes
                    for candidate in lane.candidates
                }
            ),
        )

        isbn_claims = tuple(
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.ISBN and claim.value is not None
        )
        self.assertEqual(
            ("Zed Author", "Ada Author"),
            tuple(
                claim.value
                for claim in observation.candidates[0].claims
                if claim.kind is ClaimKind.CREATOR
                and claim.state.value == "valid"
                and claim.role == "author"
            ),
        )
        self.assertEqual(
            {
                "9780306406157": {"ebook"},
                "9780140328721": {"print"},
            },
            {
                family: {
                    claim.applicability
                    for claim in isbn_claims
                    if isbn_family(claim.value) == family
                }
                for family in ("9780306406157", "9780140328721")
            },
        )
        self.assertEqual(
            {
                "9780306406157": {ebook.reference},
                "9780140328721": {print_only.reference},
            },
            {
                family: {
                    link.local_reference
                    for claim in isbn_claims
                    if isbn_family(claim.value) == family
                    for link in claim.links
                    if link.outcome is Comparison.AGREEMENT
                }
                for family in ("9780306406157", "9780140328721")
            },
        )
        self.assertTrue(
            all(
                {ebook.reference, print_only.reference}
                == {link.local_reference for link in claim.links}
                for claim in isbn_claims
            )
        )
        self.assertEqual(2, len(provider.cover_descriptors))
        self.assertEqual(
            2, len({value.source_id for value in provider.cover_descriptors})
        )
        edition_cover = next(
            value
            for value in provider.cover_descriptors
            if value.provenance is not None
            and value.provenance.edition_identity is not None
        )
        self.assertEqual(
            CoverDesignation.EXACT_EDITION,
            edition_cover.provenance.designation,
        )

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
        ).observe(_isbn_lookup((ten, thirteen)))
        self.assertEqual(ProviderStatus.NO_MATCH, observation.status)
        self.assertEqual(
            tuple(sorted((ten.reference, thirteen.reference))),
            observation.input_references,
        )
        self.assertEqual("isbn:9780306406157", requests[0].url.params["q"])
        self.assertEqual(1, len(requests))

        print_candidate, _, _ = open_library_metadata._candidate(
            "open_library",
            "/books/OL1M",
            "/works/OL5W",
            _edition("/books/OL1M", work="/works/OL5W"),
            _work("/works/OL5W"),
            (_isbn(applicability="print"),),
            "9780306406157",
        )
        self.assertEqual(
            {"print"},
            {
                claim.applicability
                for claim in print_candidate.claims
                if claim.kind is ClaimKind.ISBN
            },
        )

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
        observation = provider.observe(_isbn_lookup((_isbn(),)))
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
        self.assertEqual(4, len(provider.cover_descriptors))
        self.assertEqual(
            {
                "https://covers.openlibrary.org/b/id/100-L.jpg?default=false",
                "https://covers.openlibrary.org/b/id/200-L.jpg?default=false",
                "https://covers.openlibrary.org/b/id/300-L.jpg?default=false",
                "https://covers.openlibrary.org/b/id/400-L.jpg?default=false",
            },
            {value.url for value in provider.cover_descriptors},
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

        provider = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
        )
        observation = provider.observe(_isbn_lookup((_isbn(),)))
        self.assertEqual(
            ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            observation.status,
        )
        self.assertEqual((), observation.candidates)
        self.assertEqual((), provider.cover_descriptors)
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
        ).observe(_isbn_lookup((_isbn(),)))
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
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            )
            with self.subTest(label=label):
                observation = provider.observe(_isbn_lookup((_isbn(),)))
                self.assertEqual(expected, observation.status)
                self.assertEqual((), observation.candidates)
                expected_paths = ["/search.json", "/books/OL1M.json"]
                if label == "server":
                    expected_paths.append("/books/OL1M.json")
                self.assertEqual(
                    expected_paths,
                    [request.url.path for request in requests],
                )

    def test_transient_retries_use_exact_status_retry_after_and_pacing(
        self,
    ) -> None:
        for status_code in (500, 502, 503, 504):
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
            with self.subTest(status_code=status_code):
                self.assertEqual(
                    ProviderStatus.NO_MATCH,
                    provider.observe(_isbn_lookup((_isbn(),))).status,
                )
                expected_delay = 60.0 if status_code == 503 else 1.0
                self.assertEqual([0.0, expected_delay], starts)
                self.assertEqual([expected_delay], sleeps)

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
            sleep=lambda _: self.fail("429 must never retry"),
        )
        self.assertEqual(
            ProviderStatus.RATE_LIMITED,
            terminal.observe(_isbn_lookup((_isbn(),))).status,
        )
        self.assertEqual(1, calls)

        deadline_calls = 0

        def deadline_failure(_: httpx.Request) -> httpx.Response:
            nonlocal deadline_calls
            deadline_calls += 1
            return httpx.Response(503, headers={"retry-after": "2"})

        deadline_provider = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                transport=httpx.MockTransport(deadline_failure)
            ),
            65_536,
            pacer=OpenLibraryPacer(
                clock=lambda: 0.0,
                sleep=lambda _: None,
            ),
            clock=lambda: 0.0,
            sleep=lambda _: self.fail("deadline must refuse delay"),
        )
        self.assertEqual(
            ProviderStatus.UNAVAILABLE,
            deadline_provider.observe(
                _isbn_lookup((_isbn(),)), deadline=2.0
            ).status,
        )
        self.assertEqual(1, deadline_calls)

    def test_recovers_at_every_permitted_status_retry_ordinal(self) -> None:
        for prior_failures in range(2):
            responses = [
                httpx.Response(503) for _ in range(prior_failures)
            ]
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
                    transport=httpx.MockTransport(
                        lambda _, values=responses: values.pop(0)
                    )
                ),
                65_536,
                pacer=OpenLibraryPacer(sleep=lambda _: None),
                sleep=lambda _: None,
                jitter=lambda: 0.0,
            )
            with self.subTest(prior_failures=prior_failures):
                self.assertEqual(
                    ProviderStatus.NO_MATCH,
                    provider.observe(_isbn_lookup((_isbn(),))).status,
                )
                self.assertEqual(
                    prior_failures + 1, len(provider.attempt_records)
                )
                self.assertEqual(
                    ["retry"] * prior_failures + ["response_received"],
                    [record.outcome for record in provider.attempt_records],
                )

    def test_error_response_bodies_are_charged_before_status_retry(
        self,
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
            sleep=lambda _: self.fail("oversized body must not retry"),
        )
        self.assertEqual(
            ProviderStatus.OVERSIZED,
            oversized_provider.observe(_isbn_lookup((_isbn(),))).status,
        )
        self.assertEqual(1, oversized_calls)
        self.assertEqual(0, oversized_provider._remaining_response_bytes)

        success = httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"docs": []},
        )
        control = OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(
                transport=httpx.MockTransport(lambda _: success)
            ),
            1_024,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            control.observe(_isbn_lookup((_isbn(),))).status,
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
            httpx.Client(
                transport=httpx.MockTransport(lambda _: responses.pop(0))
            ),
            1_024,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
            sleep=lambda _: None,
            jitter=lambda: 0.0,
        )
        self.assertEqual(
            ProviderStatus.NO_MATCH,
            interrupted.observe(_isbn_lookup((_isbn(),))).status,
        )
        self.assertEqual(
            control._remaining_response_bytes - 1,
            interrupted._remaining_response_bytes,
        )
        for raw_body in (gzip.compress(b"error"), b"not-gzip"):
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
                httpx.Client(
                    transport=httpx.MockTransport(encoded_error)
                ),
                1_024,
                pacer=OpenLibraryPacer(sleep=lambda _: None),
                sleep=lambda _: self.fail(
                    "non-identity encoding must not retry"
                ),
            )
            with self.subTest(streamed_encoding=raw_body):
                self.assertEqual(
                    ProviderStatus.MALFORMED,
                    encoded.observe(_isbn_lookup((_isbn(),))).status,
                )
                self.assertEqual(1, calls)
                self.assertEqual(
                    1_024 * 49 - len(raw_body),
                    encoded._remaining_response_bytes,
                )

        for decoded_body in (b"error", b"not-gzip"):
            response = httpx.Response(503, content=decoded_body)
            response.headers["content-encoding"] = "gzip"
            encoded = OpenLibraryMetadataProvider(
                "open_library",
                httpx.Client(
                    transport=httpx.MockTransport(lambda _: response)
                ),
                1_024,
                pacer=OpenLibraryPacer(sleep=lambda _: None),
                sleep=lambda _: self.fail(
                    "preloaded non-identity response must not retry"
                ),
            )
            with self.subTest(preloaded_encoding=decoded_body):
                self.assertEqual(
                    ProviderStatus.MALFORMED,
                    encoded.observe(_isbn_lookup((_isbn(),))).status,
                )
                self.assertEqual(
                    1_024 * 49, encoded._remaining_response_bytes
                )

    def test_retry_budget_is_four_across_required_request_chain(
        self,
    ) -> None:
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
            "/books/OL1M.json": _edition(
                "/books/OL1M", work="/works/OL5W"
            ),
            "/works/OL5W.json": _work("/works/OL5W"),
            "/books/OL2M.json": _edition(
                "/books/OL2M", work="/works/OL9W"
            ),
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
        self.assertEqual(
            ProviderStatus.UNAVAILABLE,
            provider.observe(_isbn_lookup((_isbn(),))).status,
        )
        self.assertEqual(9, sum(path_calls.values()))
        self.assertEqual(9, len(provider.attempt_records))
        self.assertEqual(
            4,
            sum(
                record.outcome == "retry"
                for record in provider.attempt_records
            ),
        )

    def test_nine_lane_observation_is_capped_at_49_dispatches(
        self,
    ) -> None:
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
                family = request.url.params.get("q", "").removeprefix(
                    "isbn:"
                )
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
        observation = provider.observe(plan)
        self.assertEqual(49, request_count)
        self.assertEqual(49, len(provider.attempt_records))
        self.assertEqual(
            4,
            sum(
                record.outcome == "retry"
                for record in provider.attempt_records
            ),
        )
        self.assertEqual(9, len(observation.lanes))

    def test_invalid_503_retry_after_falls_back_to_fresh_jitter(
        self,
    ) -> None:
        for retry_after in ("61", "2, 3"):
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
                        lambda _: responses.pop(0)
                    )
                ),
                65_536,
                pacer=OpenLibraryPacer(sleep=lambda _: None),
                sleep=delays.append,
                jitter=lambda: 0.25,
            )
            with self.subTest(retry_after=retry_after):
                self.assertEqual(
                    ProviderStatus.NO_MATCH,
                    provider.observe(_isbn_lookup((_isbn(),))).status,
                )
                self.assertEqual([1.25], delays)

    def test_interrupted_body_bytes_are_charged_before_retry(self) -> None:
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
        self.assertEqual(
            ProviderStatus.RATE_LIMITED,
            provider.observe(_isbn_lookup((_isbn(),))).status,
        )
        self.assertEqual(2, calls)
        self.assertEqual(48, provider._remaining_response_bytes)

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
        observation = provider.observe(_isbn_lookup((_isbn(),)))
        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(1, len(observation.candidates))
        self.assertEqual(2, len(provider.cover_descriptors))
        self.assertTrue(
            all(value.url is None for value in provider.cover_descriptors)
        )
        self.assertEqual(
            (CoverSourceState.REJECTED, CoverSourceState.REJECTED),
            tuple(value.terminal_state for value in provider.cover_descriptors),
        )
        self.assertFalse(
            {
                ProviderEvidenceKind.EDITION_COVER_ID,
                ProviderEvidenceKind.WORK_COVER_ID,
            }
            & {value.kind for value in observation.candidates[0].evidence}
        )

    def test_missing_and_empty_cover_ids_remain_absent_while_valid_ids_request(self) -> None:
        for cover_fields, expected_url, expected_state in (
            ({}, None, None),
            ({"covers": None}, None, None),
            ({"covers": []}, None, None),
            (
                {"covers": [1]},
                f"{OPEN_LIBRARY_COVERS_ORIGIN}/b/id/1-L.jpg?default=false",
                None,
            ),
        ):
            with self.subTest(cover_fields=cover_fields):
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
                                json=values[request.url.path],
                            )
                        )
                    ),
                    65_536,
                    pacer=OpenLibraryPacer(sleep=lambda _: None),
                )
                provider.observe(_isbn_lookup((_isbn(),)))
                descriptor = provider.cover_descriptors[0]
                self.assertEqual(expected_url, descriptor.url)
                self.assertEqual(expected_state, descriptor.terminal_state)

    def test_invalid_present_cover_ids_are_rejected(self) -> None:
        for covers in (
            ["not-an-id"],
            [True],
            [0],
            [-1],
            [2_147_483_648],
            [1, "not-an-id"],
            {"not": "a list"},
        ):
            with self.subTest(covers=covers):
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
                                json=values[request.url.path],
                            )
                        )
                    ),
                    65_536,
                    pacer=OpenLibraryPacer(sleep=lambda _: None),
                )
                observation = provider.observe(_isbn_lookup((_isbn(),)))
                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                self.assertEqual(
                    CoverSourceState.REJECTED,
                    provider.cover_descriptors[0].terminal_state,
                )
                self.assertIsNone(provider.cover_descriptors[0].url)
                self.assertNotIn(
                    ProviderEvidenceKind.EDITION_COVER_ID,
                    {value.kind for value in observation.candidates[0].evidence},
                )

    def test_cover_context_at_its_limit_is_retained(self) -> None:
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
        observation = provider.observe(_isbn_lookup((_isbn(),)))

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        edition_descriptor = provider.cover_descriptors[0]
        self.assertIsNone(edition_descriptor.terminal_state)
        self.assertIsNotNone(edition_descriptor.provenance)
        self.assertEqual(16, len(edition_descriptor.provenance.context))

    def test_cover_context_overflow_retains_metadata_with_oversized_cover(self) -> None:
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
        observation = provider.observe(_isbn_lookup((_isbn(),)))

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(1, len(observation.candidates))
        edition_descriptor = provider.cover_descriptors[0]
        self.assertEqual(CoverSourceState.OVERSIZED, edition_descriptor.terminal_state)
        self.assertIsNotNone(edition_descriptor.provenance)
        self.assertEqual((), edition_descriptor.provenance.context)

    def test_cover_context_scalar_bound_preserves_metadata(self) -> None:
        for length, terminal_state in (
            (280, None),
            (281, CoverSourceState.OVERSIZED),
            (512, CoverSourceState.OVERSIZED),
            (513, CoverSourceState.OVERSIZED),
        ):
            with self.subTest(length=length):
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
                                json=values[request.url.path],
                            )
                        )
                    ),
                    65_536,
                    pacer=OpenLibraryPacer(sleep=lambda _: None),
                )
                observation = provider.observe(_isbn_lookup((_isbn(),)))

                self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
                self.assertEqual(1, len(observation.candidates))
                edition_descriptor = provider.cover_descriptors[0]
                self.assertEqual(terminal_state, edition_descriptor.terminal_state)
                self.assertIsNotNone(edition_descriptor.provenance)
                if terminal_state is None:
                    self.assertIn(
                        "C" * length,
                        {
                            value.value
                            for value in edition_descriptor.provenance.context
                        },
                    )
                else:
                    self.assertEqual((), edition_descriptor.provenance.context)

    def test_malformed_cover_ids_precede_oversized_context(self) -> None:
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
        observation = provider.observe(_isbn_lookup((_isbn(),)))

        self.assertEqual(ProviderStatus.CANDIDATES, observation.status)
        self.assertEqual(
            (CoverSourceState.REJECTED, CoverSourceState.REJECTED),
            tuple(value.terminal_state for value in provider.cover_descriptors),
        )
        self.assertTrue(all(value.url is None for value in provider.cover_descriptors))
        self.assertFalse(
            {
                ProviderEvidenceKind.EDITION_COVER_ID,
                ProviderEvidenceKind.WORK_COVER_ID,
            }
            & {value.kind for value in observation.candidates[0].evidence}
        )

    def test_unsafe_cover_context_remains_provider_malformed(self) -> None:
        for value in (
            "/Users/private",
            "/Users/private/" + "x" * 500,
            "sk-abcdefgh" + "x" * 502,
        ):
            with self.subTest(value=value[:20]):
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
                                json=values[request.url.path],
                            )
                        )
                    ),
                    65_536,
                    pacer=OpenLibraryPacer(sleep=lambda _: None),
                )

                observation = provider.observe(_isbn_lookup((_isbn(),)))

                self.assertEqual(ProviderStatus.MALFORMED, observation.status)
                self.assertEqual((), observation.candidates)
                self.assertEqual((), provider.cover_descriptors)

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
        ).observe(_isbn_lookup((_isbn(),)))
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

    def test_edition_with_multiple_work_relations_is_malformed(self) -> None:
        values = {
            "/search.json": _search("/books/OL1M"),
            "/books/OL1M.json": {
                **_edition("/books/OL1M"),
                "works": [
                    {"key": "/works/OL5W"},
                    {"key": "/works/OL9W"},
                ],
            },
            "/works/OL5W.json": _work(),
        }

        def handler(request: httpx.Request) -> httpx.Response:
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
        ).observe(_isbn_lookup((_isbn(),)))
        self.assertEqual(ProviderStatus.MALFORMED, observation.status)
        self.assertEqual((), observation.candidates)


class OpenLibraryDiagnosticBoundaryTests(unittest.TestCase):
    def test_edition_scalar_and_search_list_publish_dates_are_contextual(self) -> None:
        edition = _edition("/books/OL1M")
        self.assertEqual(
            0, open_library_metadata._optional_projection_rejections(edition)
        )
        self.assertEqual(
            0,
            open_library_metadata._optional_projection_rejections(
                {"key": "/works/OL1W", "publish_date": ["2026"]},
                search_projection=True,
            ),
        )

    def test_optional_rejections_and_duplicate_search_identities_fail_closed(self) -> None:
        for invalid in (7, {"value": "2026"}):
            with self.subTest(invalid=invalid):
                self.assertEqual(
                    1,
                    open_library_metadata._optional_projection_rejections(
                        {"key": "/works/OL1W", "publish_date": [invalid]},
                        search_projection=True,
                    ),
                )
        source = _search("/books/OL1M", "/books/OL1M")
        with self.assertRaises(ValueError):
            open_library_metadata._admitted_candidates(source, "9780306406157")
        with self.assertRaises(ValueError):
            open_library_metadata._admitted_work_candidates(source)

    def test_optional_projection_diagnostics_are_field_bounded(self) -> None:
        self.assertEqual(
            0,
            open_library_metadata._optional_projection_rejections(
                {"key": "/works/OL1W", "contributions": ["Ada"]}
            ),
        )

    def test_optional_projection_shapes_are_exact_and_local(self) -> None:
        diagnostic = open_library_metadata._optional_projection_rejections
        self.assertEqual(0, diagnostic({"key": "/works/OL1W", "covers": [1, 2]}))
        self.assertEqual(1, diagnostic({"key": "/works/OL1W", "covers": ["1"]}))
        scalars = ("edition_name", "edition_statement", "translation_of", "description", "copyright_date", "number_of_pages")
        malformed = {key: 1 for key in scalars if key != "number_of_pages"}
        malformed["number_of_pages"] = "1"
        self.assertEqual(len(scalars), diagnostic({"key": "/works/OL1W", **malformed}))
        self.assertEqual(2, diagnostic({"key": "/works/OL1W", "authors": [{"key": "/authors/bad"}], "languages": [{"key": "/languages/toolong"}]}))
        self.assertEqual(1, diagnostic({"key": "/works/OL1W", "author_key": ["/authors/bad"]}))
        self.assertEqual(0, diagnostic(_work()))
        self.assertEqual(1, diagnostic({"key": "/works/OL1W", "authors": [{"key": 7}]}))
        self.assertEqual(1, diagnostic({"key": "/works/OL1W", "languages": [{"key": 7}]}))
        self.assertEqual(1, diagnostic({"key": "/works/OL1W", "covers": [0]}))
        self.assertEqual(1, diagnostic({"key": "/works/OL1W", "number_of_pages": 0}))
        self.assertEqual(1, diagnostic({"key": "/works/OL1W", "edition_name": "bad\x00name"}))
        scalar_fields = (
            "publish_date", "physical_format", "pagination", "by_statement", "imprint"
        )
        document = {"key": "/works/OL1W", **{field: 7 for field in scalar_fields}}
        self.assertEqual(5, open_library_metadata._optional_projection_rejections(document))
        hostile = [object() for _ in range(open_library_metadata._MAX_VALUES + 1)]
        self.assertEqual(
            1,
            open_library_metadata._optional_projection_rejections(
                {"key": "/works/OL1W", "subjects": hostile}
            ),
        )

    def test_duplicate_work_rows_with_different_editions_fail_closed(self) -> None:
        source = {
            "docs": [
                _search("/books/OL1M")["docs"][0],
                _search("/books/OL2M")["docs"][0],
            ]
        }
        with self.assertRaises(ValueError):
            open_library_metadata._admitted_candidates(source, "9780306406157")
        with self.assertRaises(ValueError):
            open_library_metadata._admitted_work_candidates(source)


if __name__ == "__main__":
    unittest.main()
