"""The Audible catalog adapter, proved against captured catalog payloads."""

from __future__ import annotations

import copy
from typing import Any, Callable

import httpx
import pytest

from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    AUDIBLE_CATALOG_URLS,
    AUDIBLE_MARKETPLACE_HOSTS,
    AudibleCatalogMetadataProvider,
    AudibleCatalogPacer,
)
from mechanical_librarian.domain.metadata_report import (
    AUDIOBOOK_MARKETPLACES,
    ClaimKind,
    Comparison,
    ComparisonMethod,
    FieldState,
    ProviderEvidenceKind,
    ProviderQueryKind,
    ProviderStatus,
)
from tests.support.audiobook_providers import (
    asin_plan,
    local_claims,
    payload,
    work_plan,
)

_ASIN = "B08G9PRS1K"


def _json(value: object, status_code: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"content-type": "application/json", **headers},
        json=value,
    )


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    marketplaces: tuple[str, ...] = ("us",),
    maximum_bytes: int = 262_144,
    delays: list[float] | None = None,
) -> AudibleCatalogMetadataProvider:
    return AudibleCatalogMetadataProvider(
        "audible",
        httpx.Client(transport=httpx.MockTransport(handler)),
        maximum_bytes,
        5.0,
        marketplaces=marketplaces,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=(lambda value: None) if delays is None else delays.append,
        jitter=lambda: 0.0,
    )


def _recorder(
    responses: dict[str, httpx.Response] | None = None,
    *,
    default: httpx.Response | None = None,
) -> tuple[list[httpx.URL], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        for suffix, response in (responses or {}).items():
            if str(request.url).startswith(suffix):
                return response
        if default is None:
            raise AssertionError(f"unexpected request {request.url}")
        return default

    return seen, handle


def _claims(candidate: Any, kind: ClaimKind) -> tuple[Any, ...]:
    return tuple(claim for claim in candidate.claims if claim.kind is kind)


def _evidence(candidate: Any, kind: ProviderEvidenceKind) -> tuple[Any, ...]:
    return tuple(value for value in candidate.evidence if value.kind is kind)


def test_a_lookup_maps_the_captured_product_onto_report_claims() -> None:
    seen, handle = _recorder(default=_json(payload("audible_lookup")))
    observation = _provider(handle).observe(asin_plan())

    assert ProviderStatus.CANDIDATES is observation.status
    assert ProviderQueryKind.ASIN is observation.query_kind
    assert "audible_catalog" == observation.authority
    assert [
        httpx.URL(
            f"{AUDIBLE_CATALOG_URLS['us']}/{_ASIN}",
            params={
                "response_groups": (
                    "contributors,media,product_attrs,product_desc,"
                    "product_extended_attrs,series"
                )
            },
        )
    ] == seen
    candidate = observation.candidates[0]
    assert "audible-product-B08G9PRS1K" == candidate.identity
    assert (_ASIN,) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )
    assert ("Project Hail Mary",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.TITLE)
    )
    assert ("Andy Weir",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.CREATOR)
    )
    assert ("Ray Porter",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.CONTRIBUTOR)
    )
    assert all(
        claim.role == "narrator" for claim in _claims(candidate, ClaimKind.CONTRIBUTOR)
    )
    assert ("english",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.LANGUAGE)
    )
    assert (
        _claims(candidate, ClaimKind.DESCRIPTION)[0].value
        == payload("audible_lookup")["product"]["short_description"]
    )


def test_a_stated_runtime_agrees_with_the_container_inside_the_band() -> None:
    """970 stated minutes and 58,244.201 measured seconds are one recording."""
    _seen, handle = _recorder(default=_json(payload("audible_lookup")))
    observation = _provider(handle).observe(asin_plan())
    duration = _claims(observation.candidates[0], ClaimKind.DURATION)[0]

    assert "58200.000" == duration.value
    local = next(claim for claim in local_claims() if claim.kind is ClaimKind.DURATION)
    link = next(
        link for link in duration.links if link.local_reference == local.reference
    )
    assert Comparison.AGREEMENT is link.outcome
    assert ComparisonMethod.DURATION_BAND_AGREEMENT is link.method


def test_a_lookup_walks_the_marketplace_order_and_names_the_one_that_resolved() -> None:
    seen, handle = _recorder(
        {AUDIBLE_CATALOG_URLS["de"]: _json(payload("audible_lookup"))},
        default=_json({"message": "not found"}, 404),
    )
    observation = _provider(handle, marketplaces=("us", "uk", "de")).observe(
        asin_plan()
    )

    assert ProviderStatus.CANDIDATES is observation.status
    assert [
        AUDIBLE_MARKETPLACE_HOSTS["us"],
        AUDIBLE_MARKETPLACE_HOSTS["uk"],
        AUDIBLE_MARKETPLACE_HOSTS["de"],
    ] == [url.host for url in seen]
    marketplace = _evidence(
        observation.candidates[0], ProviderEvidenceKind.ASIN_MARKETPLACE
    )
    assert ("de",) == tuple(value.value for value in marketplace)
    assert marketplace[0].value in AUDIOBOOK_MARKETPLACES


def test_an_asin_absent_from_every_marketplace_is_a_no_match() -> None:
    seen, handle = _recorder(default=_json({"message": "not found"}, 404))
    observation = _provider(handle, marketplaces=("us", "uk")).observe(asin_plan())

    assert ProviderStatus.NO_MATCH is observation.status
    assert 2 == len(seen)
    assert () == observation.candidates


@pytest.mark.parametrize(
    ("status_code", "expected"),
    (
        (401, ProviderStatus.AUTHENTICATION),
        (403, ProviderStatus.AUTHORIZATION),
        (418, ProviderStatus.UNAVAILABLE),
        (500, ProviderStatus.UNAVAILABLE),
    ),
    ids=("unauthenticated", "forbidden", "unrecognised", "server_failure"),
)
def test_a_service_failure_ends_the_lane_rather_than_reading_as_absence(
    status_code: int, expected: ProviderStatus
) -> None:
    seen, handle = _recorder(default=_json({"message": "no"}, status_code))
    observation = _provider(handle, marketplaces=("us", "uk")).observe(asin_plan())

    assert expected is observation.status
    assert [AUDIBLE_MARKETPLACE_HOSTS["us"]] == [url.host for url in seen]


def test_a_throttled_request_honours_retry_after_and_retries() -> None:
    delays: list[float] = []
    attempts: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url)
        if len(attempts) == 1:
            return _json({"message": "slow down"}, 429, **{"retry-after": "7"})
        return _json(payload("audible_lookup"))

    observation = _provider(handle, delays=delays).observe(asin_plan())

    assert ProviderStatus.CANDIDATES is observation.status
    assert [7.0] == delays
    assert 2 == len(attempts)


def test_a_search_lane_contacts_only_the_first_configured_marketplace() -> None:
    seen, handle = _recorder(default=_json(payload("audible_search")))
    observation = _provider(handle, marketplaces=("uk", "us", "de")).observe(
        work_plan()
    )

    assert ProviderStatus.CANDIDATES is observation.status
    assert ProviderQueryKind.WORK is observation.query_kind
    assert {AUDIBLE_MARKETPLACE_HOSTS["uk"]} == {url.host for url in seen}
    assert {"6"} == {url.params["num_results"] for url in seen}
    assert {"Project Hail Mary"} == {url.params["title"] for url in seen}
    # Three products come back and work-identity qualification keeps one, at
    # work scope only: a title-and-author search establishes no edition, so the
    # ASIN, runtime and narrator this adapter parsed are all withheld from it.
    candidate = observation.candidates[0]
    assert 1 == len(observation.candidates)
    assert ("Project Hail Mary",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.TITLE)
    )
    assert () == _claims(candidate, ClaimKind.ASIN)
    assert () == _claims(candidate, ClaimKind.DURATION)
    assert () == _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)


def test_a_search_page_above_the_candidate_bound_is_refused_whole() -> None:
    page = copy.deepcopy(payload("audible_search"))
    product = page["products"][0]
    page["products"] = [
        {**copy.deepcopy(product), "asin": f"B0{index:08d}"} for index in range(6)
    ]
    _seen, handle = _recorder(default=_json(page))
    observation = _provider(handle).observe(work_plan())

    assert ProviderStatus.CANDIDATE_LIMIT_EXCEEDED is observation.status
    assert () == observation.candidates


@pytest.mark.parametrize(
    "asin",
    (None, "", "B08G9PRS1", "B08G9PRS1KK", "B08G9PRS1K!", 8),
    ids=("absent", "empty", "short", "long", "punctuated", "numeric"),
)
def test_a_product_without_a_canonical_identity_fails_closed(asin: object) -> None:
    document = copy.deepcopy(payload("audible_lookup"))
    if asin is None:
        del document["product"]["asin"]
    else:
        document["product"]["asin"] = asin
    _seen, handle = _recorder(default=_json(document))

    assert ProviderStatus.MALFORMED is _provider(handle).observe(asin_plan()).status


def test_a_lower_case_identifier_is_normalised_rather_than_refused() -> None:
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["asin"] = _ASIN.lower()
    _seen, handle = _recorder(default=_json(document))
    candidate = _provider(handle).observe(asin_plan()).candidates[0]

    assert (_ASIN,) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )


def test_an_isbn_ten_shaped_asin_is_a_genuine_identifier() -> None:
    """ADR-0061 clause 4: no prefix filter anywhere on the report path."""
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["asin"] = "0743563123"
    _seen, handle = _recorder(default=_json(document))
    observation = _provider(handle).observe(asin_plan("0743563123"))

    assert ProviderStatus.CANDIDATES is observation.status
    assert ("0743563123",) == tuple(
        claim.value for claim in _claims(observation.candidates[0], ClaimKind.ASIN)
    )


def test_a_series_statement_becomes_a_name_and_a_sequence() -> None:
    """The captured product names no series, so this one is injected.

    Every other field in the document stays exactly as Audible sent it; only
    the `series` response group's own shape is added, because the reference
    title is a standalone.
    """
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["series"] = [
        {"asin": "B0000000AA", "title": "The Hail Mary Cycle", "sequence": "1"}
    ]
    _seen, handle = _recorder(default=_json(document))
    candidate = _provider(handle).observe(asin_plan()).candidates[0]

    assert ("The Hail Mary Cycle",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.SERIES)
    )
    assert ("1",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.SERIES_SEQUENCE)
    )


def test_a_markup_summary_is_recorded_as_unprojectable_rather_than_absent() -> None:
    document = copy.deepcopy(payload("audible_lookup"))
    del document["product"]["short_description"]
    _seen, handle = _recorder(default=_json(document))
    candidate = _provider(handle).observe(asin_plan()).candidates[0]
    description = _claims(candidate, ClaimKind.DESCRIPTION)[0]

    assert FieldState.MALFORMED is description.state
    assert description.value is None


def test_an_html_two_hundred_is_malformed_rather_than_parsed() -> None:
    _seen, handle = _recorder(
        default=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html></html>",
        )
    )

    assert ProviderStatus.MALFORMED is _provider(handle).observe(asin_plan()).status


def test_every_url_the_adapter_can_build_comes_from_the_closed_host_set() -> None:
    assert set(AUDIOBOOK_MARKETPLACES) == set(AUDIBLE_MARKETPLACE_HOSTS)
    assert set(AUDIBLE_MARKETPLACE_HOSTS) == set(AUDIBLE_CATALOG_URLS)
    for code, url in AUDIBLE_CATALOG_URLS.items():
        assert url == f"https://{AUDIBLE_MARKETPLACE_HOSTS[code]}/1.0/catalog/products"
    with pytest.raises(ValueError, match="marketplaces"):
        _provider(lambda request: _json({}), marketplaces=("us", "xx"))
    with pytest.raises(ValueError, match="marketplaces"):
        _provider(lambda request: _json({}), marketplaces=())
    with pytest.raises(ValueError, match="marketplaces"):
        _provider(lambda request: _json({}), marketplaces=("us", "us"))


def test_one_adapter_serves_one_observation_and_then_closes() -> None:
    _seen, handle = _recorder(default=_json(payload("audible_lookup")))
    provider = _provider(handle)

    assert ProviderStatus.CANDIDATES is provider.observe(asin_plan()).status
    assert ProviderStatus.UNAVAILABLE is provider.observe(asin_plan()).status
    with pytest.raises(ValueError, match="lookup"):
        _provider(handle).observe(object())  # type: ignore[arg-type]


def test_the_pacer_is_the_shared_request_start_clock() -> None:
    assert AudibleCatalogPacer is not None
    pacer = AudibleCatalogPacer(1.0, clock=lambda: 0.0, sleep=lambda value: None)
    assert pacer.wait(None)
