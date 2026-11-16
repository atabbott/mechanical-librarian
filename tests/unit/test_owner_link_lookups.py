"""Reading an owner link by its key, and asking with its identifier (#1477).

Every answer is synthetic. A key lookup reads the one record the link names:
present, missing, or merged into another record whose pointer is never
followed. An identifier link is asked on its own lane with its own claim.
"""

from __future__ import annotations

import json

import httpx
import pytest

from mechanical_librarian.adapters.providers.hardcover_metadata import (
    HardcoverMetadataProvider,
)
from mechanical_librarian.adapters.providers.inventaire_metadata import (
    InventaireMetadataProvider,
    InventairePacer,
)
from mechanical_librarian.adapters.providers.open_library_metadata import (
    OpenLibraryMetadataProvider,
    OpenLibraryPacer,
)
from mechanical_librarian.application.metadata_report import (
    OWNER_LINK_MERGED_DIAGNOSTIC,
    MetadataDiscoveryPlan,
    MetadataLookup,
    owner_link_discovery_lane,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
)
from mechanical_librarian.domain.owner_link import OwnerLinkLookup, OwnerLinkScope
from mechanical_librarian.infrastructure.security.credentials import inventaire_client
from tests.support.hardcover_evidence import books_envelope


def _title() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed="Local Title",
        canonical="Local Title",
        excerpt="Local Title",
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )


def _plan(link: OwnerLinkLookup) -> MetadataDiscoveryPlan:
    return MetadataDiscoveryPlan(
        (_title(),), (owner_link_discovery_lane(link),), owner_link=link
    )


def _lane_facts(observation: ProviderObservation) -> tuple[ProviderStatus, bool]:
    (lane,) = observation.lanes
    assert lane.kind is ProviderQueryKind.LINK
    return lane.status, OWNER_LINK_MERGED_DIAGNOSTIC in lane.diagnostics


def _json(value: object) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "application/json"}, json=value)


HARDCOVER = OwnerLinkLookup("ebook", OwnerLinkScope.WORK, "hardcover", "4321")


def _hardcover(books: list[dict[str, object]]) -> ProviderObservation:
    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        assert body["operationName"] == "BookById"
        assert body["variables"] == {"id": 4321}
        return _json(books_envelope(books))

    observation = (
        HardcoverMetadataProvider(
            "hardcover", httpx.Client(transport=httpx.MockTransport(handler)), 65_536
        )
        .observe(_plan(HARDCOVER))
        .observation
    )
    assert len(sent) == 1
    return observation


def test_hardcover_reads_the_linked_book_by_id() -> None:
    observation = _hardcover(
        [
            {
                "id": 4321,
                "title": "Linked Title",
                "contributions": [
                    {"id": 1, "contribution": None, "author": {"id": 9, "name": "Ann"}}
                ],
            }
        ]
    )
    assert _lane_facts(observation) == (ProviderStatus.CANDIDATES, False)
    assert [value.work_identity for value in observation.candidates] == [
        "hardcover-work-0000004321"
    ]


@pytest.mark.parametrize(
    ("books", "merged"),
    [
        pytest.param([], False, id="missing"),
        pytest.param(
            [{"id": 4321, "title": "Old", "canonical_id": 99}], True, id="merged"
        ),
    ],
)
def test_hardcover_missing_and_merged_books_find_nothing(
    books: list[dict[str, object]], merged: bool
) -> None:
    observation = _hardcover(books)
    assert _lane_facts(observation) == (ProviderStatus.NO_MATCH, merged)
    assert observation.candidates == ()


OPEN_LIBRARY = OwnerLinkLookup(
    "ebook", OwnerLinkScope.WORK, "open_library", "/works/OL5W"
)


def _ol_work() -> dict[str, object]:
    return {
        "key": "/works/OL5W",
        "title": "Linked Title",
        "authors": [
            {"author": {"key": "/authors/OL7A"}, "type": {"key": "/type/author_role"}}
        ],
    }


def _ol_edition() -> dict[str, object]:
    return {
        "key": "/books/OL9M",
        "works": [{"key": "/works/OL5W"}],
        "title": "Linked Title",
    }


def _open_library(answers: dict[str, httpx.Response]) -> ProviderObservation:
    def handler(request: httpx.Request) -> httpx.Response:
        return answers[request.url.path]

    return (
        OpenLibraryMetadataProvider(
            "open_library",
            httpx.Client(transport=httpx.MockTransport(handler)),
            65_536,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
        )
        .observe(_plan(OPEN_LIBRARY))
        .observation
    )


def test_open_library_reads_the_work_one_edition_and_its_authors() -> None:
    observation = _open_library(
        {
            "/works/OL5W.json": _json(_ol_work()),
            "/works/OL5W/editions.json": _json({"entries": [{"key": "/books/OL9M"}]}),
            "/books/OL9M.json": _json(_ol_edition()),
            "/authors/OL7A.json": _json({"key": "/authors/OL7A", "name": "Ann"}),
        }
    )
    assert _lane_facts(observation) == (ProviderStatus.CANDIDATES, False)
    (candidate,) = observation.candidates
    assert candidate.work_identity == "open-library-work-OL5W"
    assert any(
        claim.kind is ClaimKind.CREATOR and claim.value == "Ann"
        for claim in candidate.claims
    )


@pytest.mark.parametrize(
    ("answer", "merged"),
    [
        pytest.param(httpx.Response(404), False, id="missing"),
        pytest.param(
            _json({"key": "/works/OL5W", "type": {"key": "/type/delete"}}),
            False,
            id="deleted",
        ),
        pytest.param(
            _json(
                {
                    "key": "/works/OL5W",
                    "type": {"key": "/type/redirect"},
                    "location": "/works/OL6W",
                }
            ),
            True,
            id="merged",
        ),
    ],
)
def test_open_library_missing_and_merged_records_find_nothing(
    answer: httpx.Response, merged: bool
) -> None:
    observation = _open_library({"/works/OL5W.json": answer})
    assert _lane_facts(observation) == (ProviderStatus.NO_MATCH, merged)


def test_open_library_outage_is_a_failed_lane_not_a_miss() -> None:
    observation = _open_library({"/works/OL5W.json": httpx.Response(502)})
    status, _ = _lane_facts(observation)
    assert status is ProviderStatus.UNAVAILABLE


INVENTAIRE = OwnerLinkLookup("ebook", OwnerLinkScope.WORK, "inventaire", "wd:Q100")


def _inventaire(answer: dict[str, object]) -> ProviderObservation:
    def handler(request: httpx.Request) -> httpx.Response:
        uris = request.url.params.get_list("uris")
        if uris == ["wd:Q100"]:
            return _json(answer)
        assert uris == ["wd:Q42"]
        return _json(
            {
                "entities": {
                    "wd:Q42": {"uri": "wd:Q42", "labels": {"en": "Ann"}, "claims": {}}
                },
                "redirects": {},
            }
        )

    return (
        InventaireMetadataProvider(
            "inventaire",
            inventaire_client(5, transport=httpx.MockTransport(handler)),
            65_536,
            pacer=InventairePacer(0.001, clock=lambda: 10.0, sleep=lambda _: None),
            clock=lambda: 10.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda _: None,
            jitter=lambda: 0.5,
        )
        .observe(_plan(INVENTAIRE))
        .observation
    )


def test_inventaire_reads_the_linked_entity() -> None:
    observation = _inventaire(
        {
            "entities": {
                "wd:Q100": {
                    "uri": "wd:Q100",
                    "labels": {"en": "Linked Title"},
                    "claims": {"wdt:P1476": ["Linked Title"], "wdt:P50": ["wd:Q42"]},
                }
            },
            "redirects": {},
        }
    )
    assert _lane_facts(observation) == (ProviderStatus.CANDIDATES, False)
    assert [value.work_identity for value in observation.candidates] == [
        "inventaire-work-wd-Q100"
    ]


@pytest.mark.parametrize(
    ("answer", "merged"),
    [
        pytest.param(
            {"entities": {}, "redirects": {}, "notFound": ["wd:Q100"]},
            False,
            id="missing",
        ),
        pytest.param(
            {
                "entities": {"wd:Q7": {"uri": "wd:Q7", "labels": {}, "claims": {}}},
                "redirects": {"wd:Q100": "wd:Q7"},
            },
            True,
            id="merged",
        ),
    ],
)
def test_inventaire_missing_and_merged_entities_find_nothing(
    answer: dict[str, object], merged: bool
) -> None:
    observation = _inventaire(answer)
    assert _lane_facts(observation) == (ProviderStatus.NO_MATCH, merged)


def test_an_identifier_link_is_a_lane_input_only_on_its_own_lane() -> None:
    link = OwnerLinkLookup("ebook", OwnerLinkScope.EDITION, None, "9780306406157")
    claim = link.claim
    assert claim is not None
    lane = owner_link_discovery_lane(link)
    assert (lane.kind, lane.input_references, lane.matched_family) == (
        ProviderQueryKind.ISBN,
        (claim.reference,),
        "9780306406157",
    )
    own = MetadataLookup(
        ProviderQueryKind.ISBN,
        (_title(),),
        matched_family="9780306406157",
        owner_link=link,
    )
    other = MetadataLookup(
        ProviderQueryKind.ISBN,
        (_title(),),
        matched_family="9780141439518",
        owner_link=link,
    )
    assert claim in own.query_claims and claim not in own.local_claims
    assert claim not in other.query_claims


def test_a_key_lookup_needs_a_key_link() -> None:
    with pytest.raises(Exception, match="lookup is invalid"):
        MetadataLookup(ProviderQueryKind.LINK, (_title(),))
