from __future__ import annotations

import httpx
import pytest

from mechanical_librarian.adapters.providers.inventaire_metadata import (
    INVENTAIRE_ENTITIES_URL,
    INVENTAIRE_SEARCH_URL,
    InventaireMetadataProvider,
    InventairePacer,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceKind,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_SCALAR_CHARACTERS,
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    ProviderQueryKind,
    ProviderStatus,
)
from mechanical_librarian.infrastructure.security.credentials import inventaire_client
from tests.support.metadata_report_schemas import synthetic_report


def _isbn() -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.ISBN,
        observed="9780306406157",
        canonical="9780306406157",
        excerpt="ISBN 9780306406157",
        applicability="ebook",
        basis="opening_matter",
        checksum="valid_isbn_13",
        applicability_basis="ebook",
        scope=ClaimScope.EDITION,
    )


def _lookup() -> MetadataLookup:
    claim = _isbn()
    return MetadataLookup(
        ProviderQueryKind.ISBN,
        (claim,),
        matched_family="9780306406157",
    )


def _provider(
    handler: httpx.MockTransport, *, maximum_bytes: int = 65_536
) -> InventaireMetadataProvider:
    return InventaireMetadataProvider(
        "inventaire",
        inventaire_client(5, transport=handler),
        maximum_bytes,
        pacer=InventairePacer(0.001),
        clock=lambda: 10.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda _: None,
        jitter=lambda: 0.5,
    )


def test_isbn_hydrates_linked_work_labels_and_repeated_query_parameters() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "inventaire.io"
        assert request.headers["user-agent"] == (
            "MechanicalLibrarian/0.19.0 "
            "(+https://github.com/atabbott/mechanical-librarian)"
        )
        uris = request.url.params.get_list("uris")
        assert request.url.params.get_list("attributes") == (
            ["labels"]
            if set(uris) == {"wd:Q1860", "wd:Q200", "wd:Q300"}
            else ["claims", "labels", "image"]
        )
        if uris == ["isbn:9780306406157"]:
            entities = {
                "inv:edition-one": {
                    "uri": "inv:edition-one",
                    "labels": {"en": "Example Book"},
                    "image": {
                        "url": "/img/entities/0123456789abcdef0123456789abcdef01234567"
                    },
                    "claims": {
                        "wdt:P212": ["978-0-306-40615-7"],
                        "wdt:P629": ["wd:Q100"],
                        "wdt:P1476": ["Example Book"],
                        "wdt:P407": ["wd:Q1860"],
                        "wdt:P123": ["wd:Q200"],
                        "wdt:P577": ["2026"],
                    },
                }
            }
        elif uris == ["wd:Q100"]:
            entities = {
                "wd:Q100": {
                    "uri": "wd:Q100",
                    "labels": {"en": "Example Book"},
                    "claims": {
                        "wdt:P179": ["wd:Q300"],
                        "wdt:P1545": ["2"],
                    },
                }
            }
        else:
            assert set(uris) == {"wd:Q1860", "wd:Q200", "wd:Q300"}
            entities = {
                "wd:Q1860": {
                    "uri": "wd:Q1860",
                    "labels": {"en": "English"},
                    "claims": {},
                },
                "wd:Q200": {
                    "uri": "wd:Q200",
                    "labels": {"fromclaims": "Example Press"},
                    "claims": {},
                },
                "wd:Q300": {
                    "uri": "wd:Q300",
                    "labels": {"fr": "Fallback Series"},
                    "claims": {},
                },
            }
        redirects = (
            {"isbn:9780306406157": "inv:edition-one"}
            if uris == ["isbn:9780306406157"]
            else {}
        )
        return httpx.Response(200, json={"entities": entities, "redirects": redirects})

    provider = _provider(httpx.MockTransport(respond))
    observation = provider.observe(_lookup())

    assert observation.status is ProviderStatus.CANDIDATES
    candidate = observation.candidates[0]
    assert candidate.identity == "inventaire-edition-inv-edition-one"
    assert candidate.work_identity == "inventaire-work-wd-Q100"
    values = {(claim.kind, claim.value, claim.scope) for claim in candidate.claims}
    assert (ClaimKind.ISBN, "9780306406157", ClaimScope.EDITION) in values
    assert (ClaimKind.LANGUAGE, "English", ClaimScope.EDITION) in values
    assert (ClaimKind.PUBLISHER, "Example Press", ClaimScope.EDITION) in values
    assert (ClaimKind.SERIES, "Fallback Series", ClaimScope.WORK) in values
    assert (ClaimKind.SERIES_SEQUENCE, "2", ClaimScope.WORK) in values
    assert len(requests) == 3
    assert observation.request_accounting.logical_requests == 3
    assert observation.request_accounting.initial_dispatches == 3
    assert observation.request_accounting.retry_dispatches == 0
    descriptor = provider.cover_descriptors[0]
    assert descriptor.kind is CoverSourceKind.INVENTAIRE
    assert descriptor.provenance is not None
    assert descriptor.provenance.designation is CoverDesignation.EXACT_EDITION
    assert descriptor.url is not None
    assert descriptor.url.startswith("https://inventaire.io/img/entities/")
    seed = synthetic_report()
    report = MetadataReport.create(
        target=seed.target,
        file=seed.file,
        local_claims=_lookup().local_claims,
        local_reasons=("opening_claims",),
        providers=(observation,),
    )
    assert report.schema_version == "metadata-report-v33"
    assert report == MetadataReport.from_json(report.to_json())


def test_work_search_hydrates_discovery_identity_before_mapping() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url).startswith(INVENTAIRE_SEARCH_URL):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "Q100",
                            "type": "works",
                            "uri": "wd:Q100",
                            "label": "Example Book",
                            "claims": {},
                        }
                    ]
                },
            )
        if request.url.params.get_list("uris") == ["wd:Q100"]:
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "wd:Q100": {
                            "uri": "wd:Q100",
                            "labels": {"en": "Example Book"},
                            "image": {
                                "url": "/img/entities/abcdef0123456789abcdef0123456789abcdef01"
                            },
                            "claims": {
                                "wdt:P1476": ["Example Book"],
                                "wdt:P50": ["wd:Q42"],
                            },
                        }
                    },
                    "redirects": {},
                },
            )
        assert request.url.params.get_list("uris") == ["wd:Q42"]
        return httpx.Response(
            200,
            json={
                "entities": {
                    "wd:Q42": {
                        "uri": "wd:Q42",
                        "labels": {"en": "Ada Example"},
                        "claims": {},
                    }
                },
                "redirects": {},
            },
        )

    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (_isbn(),),
        title="Example Book",
        authors=("Ada Example",),
    )
    provider = _provider(httpx.MockTransport(respond))
    observation = provider.observe(lookup)

    assert observation.status is ProviderStatus.CANDIDATES
    assert observation.candidates[0].identity == "inventaire-work-wd-Q100"
    assert observation.candidates[0].work_identity == "inventaire-work-wd-Q100"
    assert observation.candidates[0].claims
    assert observation.candidates[0].claims[0].kind is ClaimKind.TITLE
    assert len(requests) == 3
    # A work entity image remains work-scoped; no edition identity is invented.
    descriptor = provider.cover_descriptors[0]
    assert descriptor.provenance is not None
    assert descriptor.provenance.designation is CoverDesignation.WORK
    assert descriptor.provenance.edition_identity is None
    assert descriptor.provenance.work_identity == "inventaire-work-wd-Q100"
    assert requests[0].url.path == "/api/search"
    assert requests[1].url.path == "/api/entities/by-uris"


def test_malformed_optional_search_rows_are_diagnosed_and_ignored() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/search"
        return httpx.Response(
            200,
            json={"results": [{"type": "editions", "uri": "wd:Q1"}]},
        )

    observation = _provider(httpx.MockTransport(respond)).observe(
        MetadataLookup(
            ProviderQueryKind.WORK,
            (_isbn(),),
            title="Example Book",
            authors=("Ada Example",),
        )
    )

    assert observation.status is ProviderStatus.NO_MATCH
    assert observation.diagnostics[0].count >= 1


@pytest.mark.parametrize(
    "redirects",
    (
        {},
        {"isbn:9780306406157": "inv:different-edition"},
        {
            "isbn:9780306406157": "inv:unrequested-edition",
            "isbn:9780000000002": "inv:unrequested-edition",
        },
    ),
    ids=("absent", "mismatched_target", "ambiguous"),
)
def test_malformed_isbn_redirects_are_rejected(redirects: dict[str, str]) -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "entities": {
                    "inv:unrequested-edition": {
                        "uri": "inv:unrequested-edition",
                        "labels": {"en": "Unrequested"},
                        "claims": {"wdt:P212": ["9780306406157"]},
                    }
                },
                "redirects": redirects,
            },
        )

    observation = _provider(httpx.MockTransport(respond)).observe(_lookup())

    assert observation.status is ProviderStatus.MALFORMED


def test_hostile_cover_path_rejects_the_candidate_projection() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        uris = request.url.params.get_list("uris")
        if uris == ["isbn:9780306406157"]:
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "isbn:9780306406157": {
                            "uri": "inv:edition-one",
                            "image": "https://attacker.invalid/cover.jpg",
                            "labels": {"en": "Example Book"},
                            "claims": {
                                "wdt:P212": ["9780306406157"],
                                "wdt:P629": ["wd:Q100"],
                            },
                        }
                    },
                    "redirects": {"isbn:9780306406157": "inv:edition-one"},
                },
            )
        assert uris == ["wd:Q100"]
        return httpx.Response(
            200,
            json={
                "entities": {
                    "wd:Q100": {
                        "uri": "wd:Q100",
                        "labels": {"en": "Example Book"},
                        "claims": {},
                    }
                },
                "redirects": {},
            },
        )

    observation = _provider(httpx.MockTransport(respond)).observe(_lookup())

    assert observation.status is ProviderStatus.MALFORMED


@pytest.mark.parametrize(
    ("label_entities", "expected_publisher"),
    (
        ({}, None),
        ({"wd:Q200": {"uri": "wd:Q200"}}, None),
        (
            {
                "wd:Q200": {
                    "uri": "wd:Q200",
                    "labels": {"en": 7, "fromclaims": "Fallback Press"},
                }
            },
            "Fallback Press",
        ),
        (
            {
                "wd:Q200": {
                    "uri": "wd:Q200",
                    "labels": {
                        "en": "x" * (MAX_SCALAR_CHARACTERS + 1),
                        "fromclaims": "Bounded Press",
                    },
                }
            },
            "Bounded Press",
        ),
        (
            {
                "wd:Q200": {
                    "uri": "wd:Q200",
                    "labels": {
                        "en": 7,
                        "fromclaims": "x" * (MAX_SCALAR_CHARACTERS + 1),
                    },
                }
            },
            None,
        ),
    ),
    ids=(
        "missing_entity",
        "absent_labels",
        "malformed_preferred_fallback",
        "oversized_preferred_fallback",
        "all_unusable",
    ),
)
def test_optional_label_failures_preserve_other_candidate_evidence(
    label_entities: dict[str, object], expected_publisher: str | None
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        uris = request.url.params.get_list("uris")
        if uris == ["isbn:9780306406157"]:
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "inv:edition-one": {
                            "uri": "inv:edition-one",
                            "labels": {"en": "Example Book"},
                            "claims": {
                                "wdt:P212": ["9780306406157"],
                                "wdt:P1476": ["Example Book"],
                                "wdt:P123": ["wd:Q200"],
                            },
                        }
                    },
                    "redirects": {"isbn:9780306406157": "inv:edition-one"},
                },
            )
        assert uris == ["wd:Q200"]
        assert request.url.params.get_list("attributes") == ["labels"]
        return httpx.Response(200, json={"entities": label_entities, "redirects": {}})

    observation = _provider(httpx.MockTransport(respond)).observe(_lookup())

    assert observation.status is ProviderStatus.CANDIDATES
    claims = observation.candidates[0].claims
    assert any(
        claim.kind is ClaimKind.TITLE and claim.value == "Example Book"
        for claim in claims
    )
    publishers = [claim.value for claim in claims if claim.kind is ClaimKind.PUBLISHER]
    assert publishers == ([] if expected_publisher is None else [expected_publisher])
    assert observation.diagnostics
    assert observation.diagnostics[0].count >= 1


def test_rate_limit_without_recovery_window_is_terminal() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url == httpx.URL(INVENTAIRE_ENTITIES_URL).copy_add_param(
            "uris", "isbn:9780306406157"
        ).copy_add_param("attributes", "claims").copy_add_param(
            "attributes", "labels"
        ).copy_add_param("attributes", "image").copy_add_param("lang", "en")
        return httpx.Response(429, json={"status": 429})

    observation = _provider(httpx.MockTransport(respond)).observe(_lookup())

    assert observation.status is ProviderStatus.RATE_LIMITED
    assert calls == 1


@pytest.mark.parametrize(
    ("status_code", "expected"),
    ((302, ProviderStatus.UNAVAILABLE), (404, ProviderStatus.NO_MATCH)),
    ids=("redirect", "ordinary_absence"),
)
def test_redirect_and_absence_are_terminal_typed_states(
    status_code: int, expected: ProviderStatus
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code,
            headers={"location": "https://attacker.invalid/redirect"},
            json={"status": status_code},
        )

    observation = _provider(httpx.MockTransport(respond)).observe(_lookup())

    assert observation.status is expected
    assert len(requests) == 1


def test_oversized_response_is_rejected_before_mapping() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"padding": "x" * 512})

    observation = _provider(httpx.MockTransport(respond), maximum_bytes=128).observe(
        _lookup()
    )

    assert observation.status is ProviderStatus.OVERSIZED


def test_pacer_enforces_one_shared_request_start_second() -> None:
    now = [10.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    pacer = InventairePacer(clock=lambda: now[0], sleep=sleep)

    assert pacer.wait(None) is True
    assert pacer.wait(None) is True
    assert sleeps == [1.0]


def test_transient_request_stops_after_three_attempts() -> None:
    calls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"status": 503})
        return httpx.Response(404, json={"status": 404})

    observation = _provider(httpx.MockTransport(respond)).observe(_lookup())

    assert observation.status is ProviderStatus.NO_MATCH
    assert calls == 3
    assert observation.request_accounting.logical_requests == 1
    assert observation.request_accounting.initial_dispatches == 1
    assert observation.request_accounting.retry_dispatches == 2


def test_identifier_candidate_suppresses_work_search() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "entities": {
                    "inv:edition-one": {
                        "uri": "inv:edition-one",
                        "labels": {"en": "Example Book"},
                        "claims": {
                            "wdt:P212": ["9780306406157"],
                            "wdt:P1476": ["Example Book"],
                        },
                    }
                },
                "redirects": {"isbn:9780306406157": "inv:edition-one"},
            },
        )

    claim = _isbn()
    plan = MetadataDiscoveryPlan(
        (claim,),
        (
            MetadataDiscoveryLane(
                "lane_" + "1" * 64,
                ProviderQueryKind.WORK,
                (claim.reference,),
                title="Example Book",
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
    observation = _provider(httpx.MockTransport(respond)).observe(plan)

    assert observation.status is ProviderStatus.CANDIDATES
    assert [request.url.path for request in requests] == ["/api/entities/by-uris"]
    assert observation.request_accounting.logical_requests == 1
    assert observation.request_accounting.initial_dispatches == 1
