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
from mechanical_librarian.application.metadata_report import ProviderObservationFailure
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceKind,
    CoverSourceState,
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
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from mechanical_librarian.domain.work_identity import (
    WorkDispatchRung,
    WorkTitleTransformationFamily,
)
from tests.support.audiobook_providers import (
    asin_plan,
    local_claims,
    local_runtime_claim,
    payload,
    work_plan,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    manual_audiobook_observation,
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
    observation = _provider(handle).observe(asin_plan()).observation

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
    observation = _provider(handle).observe(asin_plan()).observation
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
    observation = (
        _provider(handle, marketplaces=("us", "uk", "de"))
        .observe(asin_plan())
        .observation
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
    observation = (
        _provider(handle, marketplaces=("us", "uk")).observe(asin_plan()).observation
    )

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
    observation = (
        _provider(handle, marketplaces=("us", "uk")).observe(asin_plan()).observation
    )

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

    observation = _provider(handle, delays=delays).observe(asin_plan()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert [7.0] == delays
    assert 2 == len(attempts)


def test_a_search_lane_contacts_only_the_first_configured_marketplace() -> None:
    seen, handle = _recorder(default=_json(payload("audible_search")))
    observation = (
        _provider(handle, marketplaces=("uk", "us", "de"))
        .observe(work_plan())
        .observation
    )

    assert ProviderStatus.CANDIDATES is observation.status
    assert ProviderQueryKind.WORK is observation.query_kind
    assert {AUDIBLE_MARKETPLACE_HOSTS["uk"]} == {url.host for url in seen}
    assert {"6"} == {url.params["num_results"] for url in seen}
    assert {"Project Hail Mary"} == {url.params["title"] for url in seen}
    # The admitted work retains informational ASIN/runtime hints without
    # marketplace authority or narrator applicability.
    candidate = observation.candidates[0]
    assert 1 == len(observation.candidates)
    assert ("Project Hail Mary",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.TITLE)
    )
    assert _claims(candidate, ClaimKind.ASIN)
    assert _claims(candidate, ClaimKind.DURATION)
    assert () == _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)


def test_latin_fold_reaches_audible_only_on_the_final_bounded_search() -> None:
    seen: list[httpx.URL] = []
    page = copy.deepcopy(payload("audible_search"))
    product = page["products"][0]
    product["title"] = "Cafe: Notes"
    product["authors"] = [{"name": "Jose"}]
    page["products"] = [product]
    page["total_results"] = 1

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return _json({"total_results": 0, "products": []} if len(seen) < 3 else page)

    observation = (
        _provider(handle)
        .observe(
            work_plan(
                title="Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes",
                authors=("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",),
            )
        )
        .observation
    )

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
    ] == [(url.params["title"], url.params["author"]) for url in seen]
    lineage = observation.lanes[0].dispatch_lineage
    assert lineage is not None
    assert lineage.rung is WorkDispatchRung.PUNCTUATION_FOLDED
    assert lineage.transformations[-1] is (
        WorkTitleTransformationFamily.LATIN_COMBINING_MARKS
    )
    assert (1, 2) == (
        observation.request_accounting.initial_dispatches,
        observation.request_accounting.retry_dispatches,
    )


def test_a_search_page_above_the_candidate_bound_is_refused_whole() -> None:
    page = copy.deepcopy(payload("audible_search"))
    product = page["products"][0]
    page["products"] = [
        {**copy.deepcopy(product), "asin": f"B0{index:08d}"} for index in range(6)
    ]
    _seen, handle = _recorder(default=_json(page))
    observation = _provider(handle).observe(work_plan()).observation

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

    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(asin_plan()).observation.status
    )


def test_a_lower_case_identifier_is_normalised_rather_than_refused() -> None:
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["asin"] = _ASIN.lower()
    _seen, handle = _recorder(default=_json(document))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]

    assert (_ASIN,) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )


def test_an_isbn_ten_shaped_asin_is_a_genuine_identifier() -> None:
    """ADR-0061 clause 4: no prefix filter anywhere on the report path."""
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["asin"] = "0743563123"
    _seen, handle = _recorder(default=_json(document))
    observation = _provider(handle).observe(asin_plan("0743563123")).observation

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
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]

    assert ("The Hail Mary Cycle",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.SERIES)
    )
    assert ("1",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.SERIES_SEQUENCE)
    )


def test_a_markup_summary_projects_as_visible_plain_text() -> None:
    """The publisher summary is HTML, and the shared renderer reads it.

    Before the renderer was shared this claim was `MALFORMED`: the adapter
    could see that the provider had said something and could not say what.
    """
    document = copy.deepcopy(payload("audible_lookup"))
    del document["product"]["short_description"]
    stated = document["product"]["publisher_summary"]
    _seen, handle = _recorder(default=_json(document))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    description = _claims(candidate, ClaimKind.DESCRIPTION)[0]

    assert "<" in stated
    assert FieldState.VALID is description.state
    assert description.value is not None
    assert "<" not in description.value and ">" not in description.value
    assert ("html-visible-text-v1",) == tuple(
        value.value
        for value in _evidence(
            candidate, ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION
        )
    )
    report = admitted_audiobook_report(
        local_claims(),
        additional_providers=(
            manual_audiobook_observation("audible", "audible_catalog", candidate),
        ),
    )
    result = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.DESCRIPTION
    )
    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert [description.value] == [value.value.value for value in result.alternatives]
    assert [(description.reference,)] == [
        value.evidence_references for value in result.alternatives
    ]


def test_markup_the_renderer_refuses_stays_unprojectable_rather_than_absent() -> None:
    """Hostile markup is still a value the provider stated and we will not read."""
    document = copy.deepcopy(payload("audible_lookup"))
    del document["product"]["short_description"]
    document["product"]["publisher_summary"] = "<script>alert(1)</script>"
    _seen, handle = _recorder(default=_json(document))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
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

    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(asin_plan()).observation.status
    )


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

    assert (
        ProviderStatus.CANDIDATES is (provider.observe(asin_plan())).observation.status
    )
    assert (
        ProviderStatus.UNAVAILABLE is (provider.observe(asin_plan())).observation.status
    )
    with pytest.raises(ProviderObservationFailure) as invalid:
        _provider(handle).observe(object())  # type: ignore[arg-type]
    assert isinstance(invalid.value.__cause__, ValueError)
    assert invalid.value.__cause__.args == ("provider lookup is invalid",)


def test_the_pacer_is_the_shared_request_start_clock() -> None:
    assert AudibleCatalogPacer is not None
    pacer = AudibleCatalogPacer(1.0, clock=lambda: 0.0, sleep=lambda value: None)
    assert pacer.wait(None)


def test_a_corroborated_search_lane_keeps_the_edition_it_established() -> None:
    """A running time the container agrees with turns a work into an edition.

    The same search as above, with one difference: the item states its own
    runtime, and the admitted product's runtime is inside the agreement band.
    That is what widens the retention to the ASIN, the marketplace, the
    narrator and the running time itself.
    """
    seen, handle = _recorder(default=_json(payload("audible_search")))
    observation = (
        _provider(handle).observe(work_plan(local_runtime_claim())).observation
    )
    candidate = observation.candidates[0]

    assert ProviderStatus.CANDIDATES is observation.status
    assert ProviderQueryKind.WORK is observation.query_kind
    assert 1 == len(seen)
    assert ("B08G9PRS1K",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )
    assert ("58200.000",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.DURATION)
    )
    assert ("Ray Porter",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.CONTRIBUTOR)
    )
    assert ("us",) == tuple(
        value.value
        for value in _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)
    )


def test_a_search_lane_outside_the_band_keeps_only_its_work_shape() -> None:
    """A runtime two hours away is a different recording, not this one."""
    seen, handle = _recorder(default=_json(payload("audible_search")))
    observation = (
        _provider(handle)
        .observe(work_plan(local_runtime_claim("51000.000")))
        .observation
    )
    candidate = observation.candidates[0]

    assert 1 == len(seen)
    assert _claims(candidate, ClaimKind.ASIN)
    assert _claims(candidate, ClaimKind.DURATION)
    assert () == _claims(candidate, ClaimKind.CONTRIBUTOR)
    assert () == _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)


def test_an_asin_lookup_states_one_exact_edition_cover_source() -> None:
    _seen, handle = _recorder(default=_json(payload("audible_lookup")))
    provider = _provider(handle)

    provider_result = provider.observe(asin_plan())

    (descriptor,) = provider_result.cover_descriptors
    assert "audible" == descriptor.provider
    assert CoverSourceKind.AUDIBLE_CATALOG is descriptor.kind
    assert descriptor.url is not None
    assert "m.media-amazon.com" == httpx.URL(descriptor.url).host
    assert descriptor.terminal_state is None
    assert descriptor.allow_redirects is False
    assert descriptor.provenance is not None
    assert CoverDesignation.EXACT_EDITION is descriptor.provenance.designation
    assert "audible-product-" + _ASIN == descriptor.provenance.edition_identity


def test_a_work_search_states_related_edition_covers_for_retained_candidates() -> None:
    _seen, handle = _recorder(default=_json(payload("audible_search")))
    provider = _provider(handle)

    provider_result = provider.observe(work_plan(local_runtime_claim()))
    observation = (provider_result).observation

    identities = {candidate.identity for candidate in observation.candidates}
    assert identities
    assert identities == {
        descriptor.provenance.edition_identity
        for descriptor in provider_result.cover_descriptors
        if descriptor.provenance is not None
    }
    assert all(
        descriptor.provenance is not None
        and descriptor.provenance.designation is CoverDesignation.RELATED_EDITION
        for descriptor in provider_result.cover_descriptors
    )


def test_the_widest_stated_product_image_is_the_one_asked_for() -> None:
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["product_images"] = {
        "500": "https://m.media-amazon.com/images/I/small._SL500_.jpg",
        "1024": "https://m.media-amazon.com/images/I/large._SL1024_.jpg",
    }
    _seen, handle = _recorder(default=_json(document))
    provider = _provider(handle)

    provider_result = provider.observe(asin_plan())

    (descriptor,) = provider_result.cover_descriptors
    assert descriptor.url is not None
    assert descriptor.url.endswith("large._SL1024_.jpg")


@pytest.mark.parametrize(
    "url",
    (
        "https://images-na.ssl-images-amazon.com/images/I/51POf8gOyLL.jpg",
        "https://m.media-amazon.com.example.invalid/images/I/51POf8gOyLL.jpg",
        "http://m.media-amazon.com/images/I/51POf8gOyLL.jpg",
        "https://m.media-amazon.com:8443/images/I/51POf8gOyLL.jpg",
        "https://owner:secret@m.media-amazon.com/images/I/51POf8gOyLL.jpg",
    ),
)
def test_a_cover_url_outside_the_closed_host_set_is_refused_with_a_typed_state(
    url: str,
) -> None:
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["product_images"] = {"500": url}
    _seen, handle = _recorder(default=_json(document))
    provider = _provider(handle)

    provider_result = provider.observe(asin_plan())
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES is observation.status
    (descriptor,) = provider_result.cover_descriptors
    assert descriptor.url is None
    assert CoverSourceState.UNSAFE_REMOTE is descriptor.terminal_state


def test_a_product_that_states_no_art_states_an_absent_cover_source() -> None:
    document = copy.deepcopy(payload("audible_lookup"))
    del document["product"]["product_images"]
    _seen, handle = _recorder(default=_json(document))
    provider = _provider(handle)

    provider_result = provider.observe(asin_plan())

    (descriptor,) = provider_result.cover_descriptors
    assert descriptor.url is None
    assert descriptor.terminal_state is None


def test_an_unreadable_product_image_declaration_is_typed_not_dropped() -> None:
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["product_images"] = {"500": 12}
    _seen, handle = _recorder(default=_json(document))
    provider = _provider(handle)

    provider_result = provider.observe(asin_plan())

    (descriptor,) = provider_result.cover_descriptors
    assert descriptor.url is None
    assert CoverSourceState.INVALID_DECLARATION is descriptor.terminal_state


@pytest.mark.parametrize("after_descriptor", (False, True))
def test_later_lane_failure_retains_only_the_old_boundary_descriptors(
    monkeypatch, after_descriptor
):
    from mechanical_librarian.adapters.providers import audible_catalog_metadata
    from mechanical_librarian.application.metadata_report import MetadataDiscoveryPlan

    first, second = asin_plan(), asin_plan("B000000002")
    plan = MetadataDiscoveryPlan(
        tuple(
            {
                claim.reference: claim
                for claim in first.local_claims + second.local_claims
            }.values()
        ),
        tuple(sorted(first.lanes + second.lanes, key=lambda lane: lane.identity)),
    )
    cause = RuntimeError("second lane")
    seen = []
    calls = 0

    def dispatch(request):
        nonlocal calls
        calls += 1
        if calls == 2 and not after_descriptor:
            raise cause
        value = copy.deepcopy(payload("audible_lookup"))
        value["product"]["asin"] = plan.lanes[calls - 1].canonical_asin
        return _json(value)

    provider = _provider(dispatch)
    qualify = audible_catalog_metadata.qualify_provider_observation

    def qualification(observation, lookup):
        # This adapter-local field is the pre-1005 dispatch-boundary projection.
        seen.append(provider._cover_descriptors)
        if len(seen) == 2:
            raise cause
        return qualify(observation, lookup)

    monkeypatch.setattr(
        audible_catalog_metadata, "qualify_provider_observation", qualification
    )
    with pytest.raises(ProviderObservationFailure) as caught:
        provider.observe(plan)
    assert calls == 2
    assert seen[0]
    expected = seen[1] if after_descriptor else ()
    assert caught.value.snapshot.cover_descriptors == expected
    if after_descriptor:
        assert expected and expected != seen[0]
    assert caught.value.__cause__ is cause
    assert caught.value.snapshot.evidence.accessed_at == "2023-11-14T22:13:20.000000Z"
