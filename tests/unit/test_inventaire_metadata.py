from __future__ import annotations

from typing import Any, Callable

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
    qualify_provider_observation,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceKind,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_SCALAR_CHARACTERS,
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderQueryKind,
    ProviderStatus,
)
from mechanical_librarian.domain.work_identity import (
    WorkDispatchRung,
    WorkTitleTransformationFamily,
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
    handler: httpx.MockTransport,
    *,
    maximum_bytes: int = 65_536,
    clock: Callable[[], float] | None = None,
) -> InventaireMetadataProvider:
    active_clock = (lambda: 10.0) if clock is None else clock
    return InventaireMetadataProvider(
        "inventaire",
        inventaire_client(5, transport=handler),
        maximum_bytes,
        pacer=InventairePacer(0.001, clock=active_clock, sleep=lambda _: None),
        clock=active_clock,
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
    assert report.schema_version == "metadata-report-v38"
    assert report == MetadataReport.from_json(report.to_json())


@pytest.mark.parametrize("missing_isbn", (False, True))
def test_work_search_hydrates_discovery_identity_before_mapping(
    missing_isbn: bool,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.params.get_list("uris") == ["isbn:9780306406157"]:
            return httpx.Response(
                200,
                json={
                    "entities": {},
                    "redirects": {},
                    "notFound": ["isbn:9780306406157"],
                },
            )
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
    plan = MetadataDiscoveryPlan(
        lookup.local_claims,
        (
            MetadataDiscoveryLane(
                "lane_" + "1" * 64,
                ProviderQueryKind.WORK,
                (_isbn().reference,),
                title=lookup.title,
                authors=lookup.authors,
            ),
            MetadataDiscoveryLane(
                "lane_" + "2" * 64,
                ProviderQueryKind.ISBN,
                (_isbn().reference,),
                matched_family="9780306406157",
                canonical_isbns=("9780306406157",),
            ),
        ),
    )
    observation = provider.observe(plan if missing_isbn else lookup)
    if missing_isbn:
        assert {lane.kind: lane.status for lane in observation.lanes} == {
            ProviderQueryKind.ISBN: ProviderStatus.NO_MATCH,
            ProviderQueryKind.WORK: ProviderStatus.CANDIDATES,
        }

    assert observation.status is ProviderStatus.CANDIDATES
    if not missing_isbn:
        assert (
            qualify_provider_observation(observation, lookup).status
            is ProviderStatus.CANDIDATES
        )
    assert observation.candidates[0].identity == "inventaire-work-wd-Q100"
    assert observation.candidates[0].work_identity == "inventaire-work-wd-Q100"
    assert observation.candidates[0].claims
    assert observation.candidates[0].claims[0].kind is ClaimKind.TITLE
    assert len(requests) == 3 + int(missing_isbn)
    # A work entity image remains work-scoped; no edition identity is invented.
    descriptor = provider.cover_descriptors[0]
    assert descriptor.provenance is not None
    assert descriptor.provenance.designation is CoverDesignation.WORK
    assert descriptor.provenance.edition_identity is None
    assert descriptor.provenance.work_identity == "inventaire-work-wd-Q100"
    assert requests[int(missing_isbn)].url.path == "/api/search"
    assert requests[1 + int(missing_isbn)].url.path == "/api/entities/by-uris"


def test_latin_fold_reaches_inventaire_only_on_final_bounded_search() -> None:
    requests: list[httpx.Request] = []
    search_count = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal search_count
        requests.append(request)
        if request.url.path == "/api/search":
            search_count += 1
            return httpx.Response(
                200,
                json=(
                    {"results": []}
                    if search_count < 3
                    else {
                        "results": [{"id": "Q100", "type": "works", "uri": "wd:Q100"}]
                    }
                ),
            )
        if request.url.params.get_list("uris") == ["wd:Q100"]:
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "wd:Q100": {
                            "uri": "wd:Q100",
                            "labels": {"en": "Cafe: Notes"},
                            "claims": {
                                "wdt:P1476": ["Cafe: Notes"],
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
                        "labels": {"en": "Jose"},
                        "claims": {},
                    }
                },
                "redirects": {},
            },
        )

    observation = _provider(httpx.MockTransport(respond)).observe(
        MetadataLookup(
            ProviderQueryKind.WORK,
            (),
            title="Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes",
            authors=("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",),
        )
    )

    searches = [request for request in requests if request.url.path == "/api/search"]
    assert [
        "Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        "Caf\N{LATIN SMALL LETTER E WITH ACUTE} Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        "Cafe Jose",
    ] == [request.url.params["search"] for request in searches]
    assert observation.dispatch_lineage is not None
    assert observation.dispatch_lineage.rung is WorkDispatchRung.PUNCTUATION_FOLDED
    assert observation.dispatch_lineage.transformations[-1] is (
        WorkTitleTransformationFamily.LATIN_COMBINING_MARKS
    )
    assert (3, 2) == (
        observation.request_accounting.initial_dispatches,
        observation.request_accounting.retry_dispatches,
    )


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


@pytest.mark.parametrize(
    ("failure", "label_attempts", "category", "outcome"),
    (
        ("404", 1, "http_404", "terminal"),
        ("503", 3, "http_503", "exhausted"),
        ("timeout", 3, "transport_read_timeout", "exhausted"),
    ),
)
def test_optional_label_transport_failures_preserve_edition_evidence(
    failure: str,
    label_attempts: int,
    category: str,
    outcome: str,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        uris = request.url.params.get_list("uris")
        if uris == ["isbn:9780306406157"]:
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "inv:edition-one": {
                            "uri": "inv:edition-one",
                            "labels": {"en": "Example Book"},
                            "image": {
                                "url": "/img/entities/0123456789abcdef0123456789abcdef01234567"
                            },
                            "claims": {
                                "wdt:P212": ["9780306406157"],
                                "wdt:P1476": ["Example Book"],
                                "wdt:P123": ["wd:Q200"],
                                "wdt:P407": ["wd:Q1860"],
                                "wdt:P629": ["wd:Q100"],
                            },
                        }
                    },
                    "redirects": {"isbn:9780306406157": "inv:edition-one"},
                },
            )
        if uris == ["wd:Q100"]:
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
        assert set(uris) == {"wd:Q1860", "wd:Q200"}
        assert request.url.params.get_list("attributes") == ["labels"]
        if failure == "404":
            return httpx.Response(404, json={"status": 404})
        if failure == "503":
            return httpx.Response(503, json={"status": 503})
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    provider = _provider(httpx.MockTransport(respond))
    observation = provider.observe(_lookup())

    assert observation.status is ProviderStatus.CANDIDATES
    claims = observation.candidates[0].claims
    assert {
        (claim.kind, claim.value)
        for claim in claims
        if claim.kind in {ClaimKind.ISBN, ClaimKind.TITLE}
    } == {
        (ClaimKind.ISBN, "9780306406157"),
        (ClaimKind.TITLE, "Example Book"),
    }
    assert not any(
        claim.kind in {ClaimKind.PUBLISHER, ClaimKind.LANGUAGE} for claim in claims
    )
    assert provider.cover_descriptors[0].provenance is not None
    assert (
        provider.cover_descriptors[0].provenance.designation
        is CoverDesignation.EXACT_EDITION
    )
    assert len(requests) == 2 + label_attempts
    assert observation.request_accounting.logical_requests == 3
    assert observation.request_accounting.initial_dispatches == 3
    assert observation.request_accounting.retry_dispatches == label_attempts - 1
    assert len(provider.attempt_records) == 2 + label_attempts
    assert provider.attempt_records[-1].category == category
    assert provider.attempt_records[-1].outcome == outcome
    assert provider.incomplete_causes == ()
    assert observation.diagnostics == ()


@pytest.mark.parametrize(
    ("guard", "cause"),
    (("aggregate", "aggregate_bytes"), ("deadline", "deadline")),
)
def test_optional_label_predispatch_refusal_preserves_edition_evidence(
    monkeypatch: pytest.MonkeyPatch,
    guard: str,
    cause: str,
) -> None:
    now = [10.0]
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        uris = request.url.params.get_list("uris")
        if uris == ["isbn:9780306406157"]:
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "inv:edition-one": {
                            "uri": "inv:edition-one",
                            "labels": {"en": "Example Book"},
                            "image": {
                                "url": "/img/entities/0123456789abcdef0123456789abcdef01234567"
                            },
                            "claims": {
                                "wdt:P212": ["9780306406157"],
                                "wdt:P1476": ["Example Book"],
                                "wdt:P123": ["wd:Q200"],
                                "wdt:P407": ["wd:Q1860"],
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

    provider = _provider(
        httpx.MockTransport(respond),
        clock=lambda: now[0],
    )
    optional_label_entities = provider._optional_label_entities  # noqa: SLF001

    def refuse_optional_labels(
        uris: tuple[str, ...],
        *,
        deadline: float | None,
        detail: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        if detail:
            if guard == "aggregate":
                provider._session.remaining_response_bytes = 0  # noqa: SLF001
            else:
                now[0] = 11.0
        return optional_label_entities(uris, deadline=deadline, detail=detail)

    monkeypatch.setattr(provider, "_optional_label_entities", refuse_optional_labels)
    observation = provider.observe(
        _lookup(),
        deadline=11.0 if guard == "deadline" else None,
    )

    assert observation.status is ProviderStatus.CANDIDATES
    claims = observation.candidates[0].claims
    assert {
        (claim.kind, claim.value)
        for claim in claims
        if claim.kind in {ClaimKind.ISBN, ClaimKind.TITLE}
    } == {
        (ClaimKind.ISBN, "9780306406157"),
        (ClaimKind.TITLE, "Example Book"),
    }
    assert not any(
        claim.kind in {ClaimKind.PUBLISHER, ClaimKind.LANGUAGE} for claim in claims
    )
    assert provider.cover_descriptors[0].provenance is not None
    assert (
        provider.cover_descriptors[0].provenance.designation
        is CoverDesignation.EXACT_EDITION
    )
    assert [record.cause.value for record in provider.incomplete_causes] == [cause]
    accounting = observation.request_accounting
    assert accounting.logical_requests == 3
    assert accounting.initial_dispatches == 2
    assert accounting.retry_dispatches == 0
    assert accounting.replays == 0
    assert accounting.undispatched_incomplete == 1
    assert len(provider.attempt_records) == 2
    assert len(requests) == 2
    assert observation.diagnostics == ()


def test_optional_label_absence_preserves_work_search_evidence() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/search":
            return httpx.Response(
                200,
                json={"results": [{"id": "Q100", "type": "works", "uri": "wd:Q100"}]},
            )
        uris = request.url.params.get_list("uris")
        if uris == ["wd:Q100"]:
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
        assert uris == ["wd:Q42"]
        return httpx.Response(404, json={"status": 404})

    lookup = MetadataLookup(
        ProviderQueryKind.WORK,
        (_isbn(),),
        title="Example Book",
        authors=("Ada Example",),
    )
    provider = _provider(httpx.MockTransport(respond))
    observation = provider.observe(lookup)

    assert observation.status is ProviderStatus.CANDIDATES
    candidate = observation.candidates[0]
    assert any(
        claim.kind is ClaimKind.TITLE and claim.value == "Example Book"
        for claim in candidate.claims
    )
    authors = tuple(
        claim for claim in candidate.claims if claim.kind is ClaimKind.CREATOR
    )
    assert len(authors) == 1
    assert authors[0].role == "author"
    assert authors[0].state is FieldState.ABSENT
    assert (
        qualify_provider_observation(observation, lookup).status
        is ProviderStatus.NO_MATCH
    )
    assert provider.cover_descriptors[0].provenance is not None
    assert provider.cover_descriptors[0].provenance.designation is CoverDesignation.WORK
    assert len(requests) == 3
    assert observation.request_accounting.logical_requests == 3
    assert observation.request_accounting.initial_dispatches == 3
    assert observation.request_accounting.retry_dispatches == 0
    assert len(provider.attempt_records) == 3
    assert provider.attempt_records[-1].category == "http_404"
    assert provider.attempt_records[-1].outcome == "terminal"
    assert observation.diagnostics == ()


def test_mixed_label_keys_omit_only_the_dependent_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(
        httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(AssertionError(request.url))
        )
    )

    def request(
        _url: str,
        params: dict[str, str] | httpx.QueryParams,
        *,
        deadline: float | None,
        query_retry: bool = False,
    ) -> dict[str, object]:
        del deadline, query_retry
        query = httpx.QueryParams(params)
        uris = query.get_list("uris")
        if uris == ["isbn:9780306406157"]:
            return {
                "entities": {
                    "inv:edition-one": {
                        "uri": "inv:edition-one",
                        "labels": {"en": "Example Book"},
                        "image": {
                            "url": "/img/entities/0123456789abcdef0123456789abcdef01234567"
                        },
                        "claims": {
                            "wdt:P212": ["9780306406157"],
                            "wdt:P1476": ["Example Book"],
                            "wdt:P123": ["wd:Q200"],
                            "wdt:P629": ["wd:Q100"],
                        },
                    }
                },
                "redirects": {"isbn:9780306406157": "inv:edition-one"},
            }
        if uris == ["wd:Q100"]:
            return {
                "entities": {
                    "wd:Q100": {
                        "uri": "wd:Q100",
                        "labels": {"en": "Example Book"},
                        "claims": {},
                    }
                },
                "redirects": {},
            }
        assert uris == ["wd:Q200"]
        return {
            "entities": {
                "wd:Q200": {
                    "uri": "wd:Q200",
                    "labels": {1: "numeric key", "en": 7},
                }
            },
            "redirects": {},
        }

    monkeypatch.setattr(provider, "_request", request)

    observation = provider.observe(_lookup())

    assert observation.status is ProviderStatus.CANDIDATES
    claims = observation.candidates[0].claims
    assert any(
        claim.kind is ClaimKind.ISBN and claim.value == "9780306406157"
        for claim in claims
    )
    assert any(
        claim.kind is ClaimKind.TITLE and claim.value == "Example Book"
        for claim in claims
    )
    assert not any(claim.kind is ClaimKind.PUBLISHER for claim in claims)
    assert provider.cover_descriptors
    assert observation.diagnostics[0].count == 2


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


def test_identifier_candidate_without_work_identity_preserves_work_search() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/search":
            return httpx.Response(200, json={"results": []})
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
    assert [request.url.path for request in requests] == [
        "/api/entities/by-uris",
        "/api/search",
    ]
    assert observation.request_accounting.logical_requests == 2
    assert observation.request_accounting.initial_dispatches == 2


def test_exact_missing_isbn_is_no_match() -> None:
    observation = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "entities": {},
                    "redirects": {},
                    "notFound": ["isbn:9780306406157"],
                },
            )
        )
    ).observe(_lookup())
    assert observation.status is ProviderStatus.NO_MATCH
    assert observation.candidates == ()
    assert observation.input_references == (_isbn().reference,)


@pytest.mark.parametrize(
    "missing",
    (
        None,
        "isbn:9780306406157",
        {},
        [],
        ["isbn:9780000000002"],
        ["isbn:9780306406157", "isbn:9780306406157"],
        [1],
    ),
)
def test_missing_isbn_envelope_requires_exact_negative_membership(
    missing: object,
) -> None:
    observation = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"entities": {}, "redirects": {}, "notFound": missing},
            )
        )
    ).observe(_lookup())
    assert observation.status is ProviderStatus.MALFORMED
    assert observation.candidates == ()


@pytest.mark.parametrize(
    "entities,redirects",
    (
        ({}, {"isbn:9780306406157": "inv:edition-one"}),
        ({"inv:edition-one": {"uri": "inv:edition-one", "claims": {}}}, {}),
    ),
)
def test_negative_isbn_contradictions_fail_before_absence(
    entities: dict[str, object], redirects: dict[str, str]
) -> None:
    observation = _provider(
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "entities": entities,
                    "redirects": redirects,
                    "notFound": ["isbn:9780306406157"],
                },
            )
        )
    ).observe(_lookup())
    assert observation.status is ProviderStatus.MALFORMED


@pytest.mark.parametrize(
    "matching,unresolved,expected,count",
    (
        (1, False, ProviderStatus.CANDIDATES, 1),
        (0, False, ProviderStatus.NO_MATCH, 0),
        (2, False, ProviderStatus.CANDIDATES, 2),
        (3, False, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, 0),
        (1, True, ProviderStatus.CANDIDATE_LIMIT_EXCEEDED, 0),
    ),
)
def test_three_search_rows_prune_only_explicit_wrong_work(
    matching: int, unresolved: bool, expected: ProviderStatus, count: int
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/search":
            assert request.url.params["limit"] == "3"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": f"Q{index}", "type": "works", "uri": f"wd:Q{index}"}
                        for index in range(3)
                    ]
                },
            )
        uris = request.url.params.get_list("uris")
        if uris == ["wd:Q42"]:
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
        assert uris == ["wd:Q0", "wd:Q1", "wd:Q2"]
        return httpx.Response(
            200,
            json={
                "entities": {
                    f"wd:Q{index}": {
                        "uri": f"wd:Q{index}",
                        "claims": {
                            "wdt:P1476": [
                                "Example Book" if index < matching else "Other Book"
                            ],
                            "wdt:P50": []
                            if unresolved and index >= matching
                            else ["wd:Q42"],
                        },
                        "image": {"url": "/img/entities/" + str(index) * 40},
                    }
                    for index in range(3)
                },
                "redirects": {},
            },
        )

    lookup = MetadataLookup(
        ProviderQueryKind.WORK, (), title="Example Book", authors=("Ada Example",)
    )
    provider = _provider(httpx.MockTransport(respond))
    observation = provider.observe(lookup)
    assert observation.status is expected
    assert len(observation.candidates) == count
    searches = [request for request in requests if request.url.path == "/api/search"]
    assert len(searches) == (
        2 if expected is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED else 1
    )
    qualified = qualify_provider_observation(observation, lookup)
    assert qualified.status is (ProviderStatus.NO_MATCH if matching == 2 else expected)
    if matching == 1 and not unresolved:
        assert {
            descriptor.provenance.work_identity
            for descriptor in provider.cover_descriptors
            if descriptor.provenance is not None
        } == {"inventaire-work-wd-Q0"}
        assert qualified.dispatch_lineage == observation.dispatch_lineage
    assert observation.request_accounting.initial_dispatches == 3
    assert observation.request_accounting.retry_dispatches == (
        1 if len(searches) == 2 else 0
    )


@pytest.mark.parametrize("second_label", (None, {"en": 123}, {"en": "Other Author"}))
def test_incomplete_or_different_p50_authors_cannot_become_single_author(
    second_label: object,
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/search":
            return httpx.Response(
                200, json={"results": [{"id": "Q1", "uri": "wd:Q1", "type": "works"}]}
            )
        if request.url.params.get_list("uris") == ["wd:Q1"]:
            return httpx.Response(
                200,
                json={
                    "entities": {
                        "wd:Q1": {
                            "uri": "wd:Q1",
                            "claims": {
                                "wdt:P1476": ["Example Book"],
                                "wdt:P50": ["wd:Q42", "wd:Q43"],
                            },
                        }
                    },
                    "redirects": {},
                },
            )
        return httpx.Response(
            200,
            json={
                "entities": {
                    "wd:Q42": {"uri": "wd:Q42", "labels": {"en": "Ada Example"}},
                    "wd:Q43": {"uri": "wd:Q43", "labels": second_label},
                },
                "redirects": {},
            },
        )

    lookup = MetadataLookup(
        ProviderQueryKind.WORK, (), title="Example Book", authors=("Ada Example",)
    )
    observation = _provider(httpx.MockTransport(respond)).observe(lookup)
    assert (
        qualify_provider_observation(observation, lookup).status
        is ProviderStatus.NO_MATCH
    )
    if second_label != {"en": "Other Author"}:
        authors = [
            claim
            for claim in observation.candidates[0].claims
            if claim.kind is ClaimKind.CREATOR
        ]
        assert len(authors) == 2
        assert all(claim.role == "author" for claim in authors)
        assert sum(claim.state is FieldState.ABSENT for claim in authors) == 1
