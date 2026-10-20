"""The Audible catalog adapter, proved against captured catalog payloads."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    AUDIBLE_CATALOG_URLS,
    AUDIBLE_MARKETPLACE_HOSTS,
    AudibleCatalogMetadataProvider,
    AudibleCatalogPacer,
)
from mechanical_librarian.adapters.providers.retry_safety import ProviderRequestPacer
from mechanical_librarian.application import metadata_report as m
from mechanical_librarian.application.metadata_report import ProviderObservationFailure
from mechanical_librarian.domain import metadata_report as d
from mechanical_librarian.domain import proposal_set as p
from mechanical_librarian.domain.audiobook_corroboration import (
    recording_identity_basis,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.failure_diagnostics import FailureCategory
from mechanical_librarian.domain.metadata_report import (
    AUDIOBOOK_MARKETPLACES,
    ClaimKind,
    Comparison,
    ComparisonMethod,
    FieldState,
    MetadataReport,
    ProviderEvidenceKind,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkTitleTransformationFamily,
    work_query_ladder,
)
from tests.support.audiobook_providers import (
    asin_plan,
    audio_claim,
    audio_target,
    local_claims,
    local_runtime_claim,
    payload,
    target_title_claim,
    work_plan,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    asserted_asin_report,
    manual_audiobook_observation,
    recorded_dispatch,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

_ASIN = "B08G9PRS1K"


@pytest.mark.parametrize(
    "failure", (None, "series", "sequence", "author", "subtitle", "residual")
)
@pytest.mark.parametrize("side", ("provider", "local"))
def test_provider_series_suffix_admission_replays_the_actual_association(
    failure,
    side,
    tmp_path,
) -> None:
    title = "Synthetic Journey"
    decorated = (
        title + ": Example Cycle, Book 2" + (" extra" if failure == "residual" else "")
    )
    lookup_title = decorated if side == "local" else title
    product = {
        "asin": _ASIN,
        "title": decorated if side == "provider" else title,
        "authors": [
            {"name": "Other Author" if failure == "author" else "Example Author"}
        ],
        "series": [
            {
                "title": "Other Cycle" if failure == "series" else "Example Cycle",
                "sequence": "3" if failure == "sequence" else "2",
            }
        ],
        "runtime_length_min": 970,
        "publisher_name": "Example Audio",
        "release_date": "2025-02-03",
    }
    if failure == "subtitle":
        product["subtitle"] = "A genuine subtitle"
    plan = work_plan(
        local_runtime_claim(), title=lookup_title, authors=("Example Author",)
    )
    observation = (
        _provider(lambda request: _json({"products": [product]}))
        .observe(plan)
        .observation
    )
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from tests.support.audiobook_providers import audio_target

    base = admitted_audiobook_report()
    report = MetadataReport.create(
        target=replace(
            base.target, fields=audio_target(lookup_title, ("Example Author",)).fields
        ),
        file=base.file,
        local_claims=plan.local_claims,
        local_reasons=("embedded_tag_duration",),
        providers=(recorded_dispatch(observation),),
        audio=base.audio,
    )
    path = tmp_path / "application.sqlite3"
    repository = open_repository(path)
    try:
        repository.publish(report, lambda: True)
    finally:
        repository.close()
    repository = open_repository(path)
    try:
        stored = repository.current(
            report.target.connection,
            report.target.library,
            report.target.item,
            report.file.identity,
        )
        assert stored == report.to_json()
        assert stored is not None
        reopened = MetadataReport.from_json(stored)
        assert reopened == report
    finally:
        repository.close()
    if failure is not None:
        assert observation.status is ProviderStatus.NO_MATCH
        assert not observation.candidates
        return
    assert observation.status is ProviderStatus.CANDIDATES
    candidate = observation.candidates[0]
    assert (
        next(
            claim.value
            for claim in candidate.claims
            if claim.kind is ClaimKind.PUBLISHER
        )
        == "Example Audio"
    )
    assert (
        next(
            claim.value
            for claim in candidate.claims
            if claim.kind is ClaimKind.PUBLICATION_DATE
        )
        == "2025-02-03"
    )
    assert reopened.providers[0].lanes[0].transformation_lineage is not None
    assert (
        reopened.providers[0].lanes[0].transformation_lineage.production
        == "provider_associated_series_suffix"
    )
    assert (
        reopened.providers[0].lanes[0].transformation_lineage.applications[0].side.value
        == side
    )
    changed = json.loads(report.to_json())
    changed["providers"][0]["lanes"][0]["transformation_lineage"]["applications"][0][
        "side"
    ] = "local" if side == "provider" else "provider"
    with pytest.raises(
        ReportValueError, match="provider work identity lineage is inconsistent"
    ):
        MetadataReport.from_json(json.dumps(changed))


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
    assert _claims(candidate, ClaimKind.CONTRIBUTOR)
    assert all(
        c.scope.value == "work" and c.role == "narrator"
        for c in _claims(candidate, ClaimKind.CONTRIBUTOR)
    )
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


def test_all_series_pairs_survive_with_explicit_partial_member_failure() -> None:
    document = {
        "product": {
            "asin": _ASIN,
            "title": "Synthetic Journey",
            "authors": [{"name": "Example Author"}],
            "series": [
                {"title": "Example Cycle", "sequence": "2"},
                {"title": "Example Collection", "sequence": 5},
                {"title": 42, "sequence": "9"},
            ],
        }
    }
    _seen, handle = _recorder(default=_json(document))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    assert [
        (claim.value, claim.state) for claim in _claims(candidate, ClaimKind.SERIES)
    ] == [
        ("Example Cycle", FieldState.VALID),
        ("Example Collection", FieldState.VALID),
        (None, FieldState.MALFORMED),
    ]
    assert [claim.value for claim in _claims(candidate, ClaimKind.SERIES_SEQUENCE)] == [
        "2",
        "5",
        "9",
    ]


def _cast_report(
    third: list[dict[str, str]],
    reverse: bool = False,
    third_language: str | None = None,
    third_first: bool = False,
    local_language: str = "en",
) -> MetadataReport:
    base = asserted_asin_report(provider_duration="58200.000")
    target = audio_target("The Synthetic Journey", ("Example Author",))
    local: tuple[d.LocalClaim, ...] = (target_title_claim("The Synthetic Journey"),)
    if third_language is not None:
        local += (audio_claim(d.ClaimKind.LANGUAGE, local_language, 2),)
    plan = m.audiobook_discovery_plan(target, local, audio=base.audio)
    products: list[dict[str, Any]] = [
        {
            "asin": f"B00000000{i}",
            "title": "The Synthetic Journey",
            "authors": [{"name": "Example Author"}],
            "narrators": [{"name": "Example Narrator"}],
            "runtime_length_min": 1000,
        }
        for i in (1, 2, 3)
    ]
    products[2]["narrators"] = third
    if third_language is not None:
        products[2]["language"] = third_language
    if third_first:
        products[2]["asin"] = "A000000001"
    if reverse:
        products.reverse()
    provider = _provider(lambda req: _json({"products": products, "total_results": 3}))
    try:
        o = m.observe_provider("audible", "audible_catalog", provider, plan).observation
    finally:
        provider._client.close()
    r = d.MetadataReport.create(
        target=d.TargetFacts("primary", "audio", "casts", target.fields),
        file=base.file,
        local_claims=local,
        local_reasons=("target_metadata_title",),
        providers=(o,),
        audio=base.audio,
    )
    return d.MetadataReport.from_json(r.to_json())


def _cast_references(report):
    return tuple(
        sorted(
            {
                claim.reference
                for observation in report.providers
                for candidate in p._plausible_narrator_candidates(report, observation)
                for claim in candidate.claims
                if claim.kind is d.ClaimKind.CONTRIBUTOR and claim.role == "narrator"
            }
        )
    )


@pytest.mark.parametrize(
    "third",
    [
        [{"name": "Example Narrator"}],
        [],
        [{"name": "Different Reader"}],
        [{"name": "Example Narrator"}, {}],
        [{"name": "Full Cast"}],
        [{"name": f"Reader {i}"} for i in range(100)],
    ],
)
def test_all_plausible_products_must_supply_complete_equal_cast(
    third: list[dict[str, str]],
) -> None:
    r = _cast_report(third)
    proposal = p.evaluate_report(r)
    result = next(
        x for x in proposal.results if x.destination is d.TargetDestination.NARRATORS
    )
    refs = _cast_references(r)
    assert not result.alternatives
    assert not m.admitted_audiobook_identity(r)
    expected = third == [{"name": "Example Narrator"}]
    assert p.narrator_candidate_set_agrees(r, refs) is expected
    assert not any(
        p.provider_candidate_represents_target_recording(r, o, c)
        for o in r.providers
        for c in o.candidates
    )
    packet = ShadowReviewPacket.create(ReviewCase.create(proposal, r), proposal, r)
    packet = ShadowReviewPacket.from_json(packet.to_json(), r, proposal)
    decisions = decide_shadow_packet(packet).decisions
    assert not any(
        x.destination == "narrators" and x.recommendation == "recommend_candidate"
        for x in decisions
    )
    if expected:
        assert len(refs) == 3
        assert not p.narrator_candidate_set_agrees(r, refs[:-1])
    assert not any(
        x.destination in {"asin", "isbn"} and x.recommendation == "recommend_candidate"
        for x in decisions
    )


def test_canonical_input_permutation_retains_cast_members() -> None:
    a = _cast_report([{"name": "Example Narrator"}])
    b = _cast_report([{"name": "Example Narrator"}], True)
    assert a.to_json() == b.to_json()


def test_explicit_incompatible_language_keeps_review_candidate_but_not_cast_vote() -> (
    None
):
    r = _cast_report([{"name": "Different Reader"}], third_language="de")
    proposal = p.evaluate_report(r)
    result = next(
        x for x in proposal.results if x.destination is d.TargetDestination.NARRATORS
    )
    excluded = next(
        c
        for o in r.providers
        for c in o.recording_lookup_candidates
        if any(x.kind is d.ClaimKind.LANGUAGE and x.value == "de" for x in c.claims)
    )
    assert r.target.current_title is not None
    assert (
        d.recording_variant_conflict(excluded, r.local_claims, r.target.current_title)
        == "recording_language_conflict"
    )
    refs = _cast_references(r)
    assert len(refs) == 2
    assert p.narrator_candidate_set_agrees(r, refs)
    assert not any(x.reference in refs for x in excluded.claims)
    assert not result.alternatives
    assert not m.admitted_audiobook_identity(r)
    packet = ShadowReviewPacket.create(ReviewCase.create(proposal, r), proposal, r)
    decisions = decide_shadow_packet(
        ShadowReviewPacket.from_json(packet.to_json(), r, proposal)
    ).decisions
    assert not any(
        x.destination == "narrators" and x.recommendation == "recommend_candidate"
        for x in decisions
    )


def test_incompatible_product_canonical_id_cannot_block_plausible_cast() -> None:
    r = _cast_report(
        [{"name": "Different Reader"}], third_language="de", third_first=True
    )
    refs = _cast_references(r)
    assert len(refs) == 2 and p.narrator_candidate_set_agrees(r, refs)
    assert refs == _cast_references(
        _cast_report([{"name": "Different Reader"}], third_language="de")
    )
    assert not m.admitted_audiobook_identity(r)
    proposal = p.evaluate_report(r)
    assert not next(
        x for x in proposal.results if x.destination is d.TargetDestination.NARRATORS
    ).alternatives
    packet = ShadowReviewPacket.create(ReviewCase.create(proposal, r), proposal, r)
    decisions = decide_shadow_packet(
        ShadowReviewPacket.from_json(packet.to_json(), r, proposal)
    ).decisions
    assert not any(
        x.destination == "narrators" and x.recommendation == "recommend_candidate"
        for x in decisions
    )


@pytest.mark.parametrize("key", ["title", "subtitle"])
@pytest.mark.parametrize("value", ["Example\u200b Book", "Café — 日本語!"])
def test_http_title_isolation_preserves_candidate_evidence(
    key: str, value: str
) -> None:
    from mechanical_librarian.domain.metadata_report import FieldState

    document = payload("audible_lookup")
    document["product"][key] = value
    _seen, handle = _recorder(default=_json(document))
    observation = _provider(handle).observe(asin_plan()).observation
    assert observation.status is ProviderStatus.CANDIDATES
    kind = ClaimKind.SUBTITLE if key == "subtitle" else ClaimKind.TITLE
    claims = [
        claim for candidate in observation.candidates for claim in candidate.claims
    ]
    from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
        _candidate,
    )

    physical = _candidate(
        "audible_catalog",
        document["product"],
        asin_plan().local_claims,
        marketplace="us",
        schema_rejections=lambda _: None,
    )
    if key == "subtitle" and "\u200b" in value:
        assert not any(claim.kind is ClaimKind.SUBTITLE for claim in claims)
    selected = [claim for claim in physical.claims if claim.kind is kind]
    assert selected
    expected = FieldState.MALFORMED if "\u200b" in value else FieldState.VALID
    assert any(
        claim.state is expected
        and claim.value == (None if expected is FieldState.MALFORMED else value)
        for claim in selected
    )
    assert any(
        claim.kind is ClaimKind.CREATOR and claim.state is FieldState.VALID
        for claim in claims
    )
    assert all(claim.value is None or "\u200b" not in claim.value for claim in claims)


@pytest.mark.parametrize(
    "local_language, provider_language, conflict",
    (
        ("English", "fr", True),
        ("English", "en", False),
        ("UND", "fr", False),
        ("en", "und", False),
    ),
)
def test_actual_work_producer_language_controls_recording_cast(
    local_language, provider_language, conflict
):
    report = _cast_report(
        [{"name": "Different Reader"}],
        third_language=provider_language,
        local_language=local_language,
    )
    candidate = next(
        candidate
        for observation in report.providers
        for candidate in (
            *observation.candidates,
            *observation.recording_lookup_candidates,
        )
        if any(
            claim.kind is d.ClaimKind.LANGUAGE and claim.value == provider_language
            for claim in candidate.claims
        )
    )
    assert d.recording_variant_conflict(
        candidate, report.local_claims, report.target.current_title or ""
    ) == ("recording_language_conflict" if conflict else None)
    result = next(
        value
        for value in p.evaluate_report(report).results
        if value.destination is d.TargetDestination.NARRATORS
    )
    assert not result.alternatives
    assert not m.admitted_audiobook_identity(report)
    references = set(_cast_references(report))
    narrator_references = {
        claim.reference
        for claim in candidate.claims
        if claim.kind is d.ClaimKind.CONTRIBUTOR and claim.role == "narrator"
    }
    assert narrator_references.isdisjoint(references) is conflict


@pytest.mark.parametrize(
    "language,conflict", (("fr", True), ("en", False), ("und", False))
)
def test_actual_asin_producer_binds_supported_name_language_conflict(
    language, conflict
):
    local = local_claims() + (audio_claim(ClaimKind.LANGUAGE, "English", 9),)
    base = asserted_asin_report(provider_duration="58200.000")
    plan = m.audiobook_discovery_plan(audio_target(), local, audio=base.audio)
    document = copy.deepcopy(payload("audible_lookup"))
    document["product"]["language"] = language
    provider = _provider(lambda request: _json(document))
    try:
        observation = m.observe_provider(
            "audible", "audible_catalog", provider, plan
        ).observation
    finally:
        provider._client.close()
    candidates = (*observation.candidates, *observation.recording_lookup_candidates)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert d.recording_variant_conflict(candidate, local, "Project Hail Mary") == (
        "recording_language_conflict" if conflict else None
    )
    assert any(
        claim.kind is ClaimKind.LANGUAGE and claim.value == language
        for claim in candidate.claims
    )
    assert (
        any(
            value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
            for value in candidate.evidence
        )
        is not conflict
    )


# The #1245 ladder: on an audiobook plan a rung whose products work-identity
# admission refuses no longer ends the walk.
_SUBTITLED = "Project Hail Mary: A Novel"
_FOLDED = "Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes"
_FOLDED_AUTHORS = ("Jos\N{LATIN SMALL LETTER E WITH ACUTE}",)


def _audiobook_work_plan(
    title: str = _SUBTITLED, authors: tuple[str, ...] = ("Andy Weir",)
) -> m.MetadataDiscoveryPlan:
    return m.audiobook_discovery_plan(
        audio_target(title, authors),
        (target_title_claim(title),),
        audio=single_track_audio_facts(),
    )


def _search_page(*indices: int) -> dict[str, Any]:
    """A search page of captured products: 0 is the work, 1 another one."""
    products = payload("audible_search")["products"]
    return {
        "total_results": len(indices),
        "products": [copy.deepcopy(products[index]) for index in indices],
    }


def _work_page(title: str) -> dict[str, Any]:
    """One page whose only product is the work, under the stated title."""
    page = _search_page(0)
    page["products"][0]["title"] = title
    return page


def _ladder(title: str, authors: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(title, authors)
    )


def _pages(
    *pages: dict[str, Any],
) -> tuple[list[httpx.URL], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return _json(pages[len(seen) - 1])

    return seen, handle


def _observed(
    handle: Callable[[httpx.Request], httpx.Response],
    plan: m.MetadataDiscoveryPlan,
    **arguments: Any,
) -> m.ProviderResult:
    provider = _provider(handle)
    try:
        return m.observe_provider(
            "audible", "audible_catalog", provider, plan, **arguments
        )
    finally:
        provider._client.close()


def _diagnostics(observation: Any) -> tuple[tuple[Any, int], ...]:
    return tuple((value.category, value.count) for value in observation.diagnostics)


def _ladder_report(observation: Any, title: str, authors: tuple[str, ...]):
    target = audio_target(title, authors)
    return MetadataReport.create(
        target=d.TargetFacts("primary", "audio", "item-1", target.fields),
        file=d.FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(target_title_claim(title),),
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=single_track_audio_facts(),
    )


def test_a_refused_rung_continues_to_the_rung_that_admits() -> None:
    seen, handle = _pages(_search_page(1), _work_page(_SUBTITLED))
    result = _observed(handle, _audiobook_work_plan())
    observation = result.observation

    assert ["Project Hail Mary: A Novel", "Project Hail Mary"] == [
        url.params["title"] for url in seen
    ]
    assert ProviderStatus.CANDIDATES is observation.status
    lane = observation.lanes[0]
    assert lane.dispatch_lineage == _ladder(_SUBTITLED, ("Andy Weir",))[1]
    assert lane.dispatch_lineage is not None
    assert lane.dispatch_lineage.rung is WorkDispatchRung.SUBTITLE_STRIPPED
    assert () == lane.exhausted_dispatch
    assert () == lane.diagnostics
    # An admitted walk drops the earlier rung's refusal with the rest of it.
    assert FailureCategory.WORK_IDENTITY_REFUSED not in {
        category for category, _ in _diagnostics(observation)
    }
    retained = {candidate.identity for candidate in observation.candidates}
    assert (
        retained
        and {
            descriptor.provenance.edition_identity
            for descriptor in result.cover_descriptors
            if descriptor.provenance is not None
        }
        <= retained
    )
    assert all(descriptor.provenance for descriptor in result.cover_descriptors)


@pytest.mark.parametrize(
    ("title", "authors"),
    ((_SUBTITLED, ("Andy Weir",)), (_FOLDED, _FOLDED_AUTHORS)),
)
def test_a_ladder_refused_on_every_rung_records_each_dispatch(
    title: str, authors: tuple[str, ...]
) -> None:
    ladder = _ladder(title, authors)
    seen, handle = _pages(*(_search_page(1, 2) for _ in ladder))
    result = _observed(handle, _audiobook_work_plan(title, authors))
    observation = result.observation

    # Every rung is dispatched once, inside the reserved per-rung budget.
    assert len(ladder) == len(seen) <= 24
    assert ProviderStatus.NO_MATCH is observation.status
    lane = observation.lanes[0]
    assert ladder == lane.exhausted_dispatch
    assert lane.dispatch_lineage is None and lane.transformation_lineage is None
    assert d.work_ladder_diagnostics(0, 2 * len(ladder)) == lane.diagnostics
    assert ((FailureCategory.WORK_IDENTITY_REFUSED, 2 * len(ladder)),) == (
        _diagnostics(observation)
    )
    assert () == result.cover_descriptors
    report = _ladder_report(observation, title, authors)
    assert MetadataReport.from_json(report.to_json()) == report


def test_empty_and_refused_rungs_are_counted_apart() -> None:
    seen, handle = _pages(_search_page(), _search_page(1))
    observation = _observed(handle, _audiobook_work_plan()).observation

    assert 2 == len(seen)
    lane = observation.lanes[0]
    assert _ladder(_SUBTITLED, ("Andy Weir",)) == lane.exhausted_dispatch
    assert d.work_ladder_diagnostics(1, 1) == lane.diagnostics
    assert (
        (FailureCategory.EMPTY_NO_MATCH, 1),
        (FailureCategory.WORK_IDENTITY_REFUSED, 1),
    ) == _diagnostics(observation)


def test_an_empty_ladder_records_its_dispatches_and_stays_an_empty_no_match() -> None:
    ladder = _ladder(_FOLDED, _FOLDED_AUTHORS)
    seen, handle = _pages(*(_search_page() for _ in ladder))
    observation = _observed(
        handle, _audiobook_work_plan(_FOLDED, _FOLDED_AUTHORS)
    ).observation

    assert len(ladder) == len(seen)
    lane = observation.lanes[0]
    assert ladder == lane.exhausted_dispatch
    assert d.work_ladder_diagnostics(len(ladder), 0) == lane.diagnostics
    assert ((FailureCategory.EMPTY_NO_MATCH, 1),) == _diagnostics(observation)


def test_an_admitted_first_rung_sends_exactly_one_request() -> None:
    seen, handle = _pages(_work_page(_SUBTITLED))
    observation = _observed(handle, _audiobook_work_plan()).observation

    assert 1 == len(seen)
    assert ProviderStatus.CANDIDATES is observation.status
    dispatch = observation.lanes[0].dispatch_lineage
    assert dispatch is not None and dispatch.rung is WorkDispatchRung.NORMALIZED
    assert (1, 0) == (
        observation.request_accounting.initial_dispatches,
        observation.request_accounting.retry_dispatches,
    )


def test_a_deadline_after_a_refused_rung_ends_incomplete_without_its_refusal() -> None:
    seen, handle = _pages(_search_page(1), _work_page(_SUBTITLED))
    # The shared pacer cannot start the second rung before the deadline.
    pacer = ProviderRequestPacer(3.0, clock=lambda: 0.0, sleep=lambda value: None)
    provider = AudibleCatalogMetadataProvider(
        "audible",
        httpx.Client(transport=httpx.MockTransport(handle)),
        262_144,
        5.0,
        pacer=pacer,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda value: None,
        jitter=lambda: 0.0,
    )
    try:
        observation = m.observe_provider(
            "audible",
            "audible_catalog",
            provider,
            _audiobook_work_plan(),
            deadline=2.0,
        ).observation
    finally:
        provider._client.close()

    assert 1 == len(seen)
    assert ProviderStatus.INCOMPLETE is observation.status
    lane = observation.lanes[0]
    assert () == lane.exhausted_dispatch and () == lane.diagnostics
    categories = {category for category, _ in _diagnostics(observation)}
    assert FailureCategory.TIMEOUT in categories
    assert FailureCategory.WORK_IDENTITY_REFUSED not in categories


def test_a_terminal_rung_after_a_refusal_keeps_its_own_failure() -> None:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return (
            _json(_search_page(1))
            if len(seen) == 1
            else httpx.Response(200, text="<html></html>")
        )

    observation = _observed(handle, _audiobook_work_plan()).observation

    assert 2 == len(seen)
    assert ProviderStatus.MALFORMED is observation.status
    assert () == observation.lanes[0].exhausted_dispatch
    assert FailureCategory.WORK_IDENTITY_REFUSED not in {
        category for category, _ in _diagnostics(observation)
    }


def test_an_ebook_plan_still_stops_at_the_first_rung_that_returned_products() -> None:
    seen, handle = _pages(_search_page(1), _work_page(_SUBTITLED))
    observation = _observed(handle, work_plan(title=_SUBTITLED)).observation

    assert 1 == len(seen)
    assert ProviderStatus.NO_MATCH is observation.status
    assert () == observation.lanes[0].exhausted_dispatch
    assert ((FailureCategory.EMPTY_NO_MATCH, 1),) == _diagnostics(observation)


_ASSERTED = "Asserted Title"
_ALTERNATIVE = "The Real Title"
_AUTHORS = ("Andy Weir",)


def _alternative_audio(title: str = _ALTERNATIVE) -> d.AudioFacts:
    return replace(
        single_track_audio_facts(),
        discovery_titles=(d.AudioDiscoveryTitle("path_leaf", title),),
    )


def _series_collapsed_target() -> m.TargetSnapshot:
    recorded = d.TargetField(
        TargetDestination.SERIES,
        d.TargetFieldState.VALUE,
        (d.TargetSeries(_ASSERTED, "3"),),
    )
    target = audio_target(_ASSERTED, _AUTHORS)
    return replace(
        target,
        fields=tuple(
            recorded if field.destination is TargetDestination.SERIES else field
            for field in target.fields
        ),
    )


def _alternative_plan(
    runtime: str = "58244.201",
    *,
    title: str = _ASSERTED,
    target: m.TargetSnapshot | None = None,
) -> m.MetadataDiscoveryPlan:
    return m.audiobook_discovery_plan(
        audio_target(title, _AUTHORS) if target is None else target,
        (target_title_claim(title), local_runtime_claim(runtime)),
        audio=_alternative_audio(),
    )


def _alternative_report(
    observation: Any,
    *,
    audio: d.AudioFacts | None = None,
    target: m.TargetSnapshot | None = None,
) -> MetadataReport:
    target = audio_target(_ASSERTED, _AUTHORS) if target is None else target
    return MetadataReport.create(
        target=d.TargetFacts("primary", "audio", "item-1", target.fields),
        file=d.FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(target_title_claim(_ASSERTED), local_runtime_claim()),
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=_alternative_audio() if audio is None else audio,
    )


def _by_basis(observation: Any, plan: m.MetadataDiscoveryPlan) -> dict[Any, Any]:
    bases = {lane.identity: lane.title_basis for lane in plan.lanes}
    return {bases[lane.identity]: lane for lane in observation.lanes}


def test_an_asserted_no_match_dispatches_the_alternative_lane_after_it() -> None:
    plan = _alternative_plan()
    seen, handle = _pages(_search_page(), _work_page(_ALTERNATIVE))
    observation = _observed(handle, plan).observation
    lanes = _by_basis(observation, plan)

    assert [_ASSERTED, _ALTERNATIVE] == [url.params["title"] for url in seen]
    assert 2 == observation.request_accounting.logical_requests
    assert ProviderStatus.NO_MATCH is lanes[None].status
    assert ProviderStatus.CANDIDATES is lanes["path_leaf"].status
    (candidate,) = observation.candidates
    assert ("us",) == tuple(
        value.value
        for value in _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)
    )
    assert () == observation.recording_lookup_candidates
    report = _alternative_report(observation)
    assert report == MetadataReport.from_json(report.to_json())
    # The corroborated recording is the alternative lane's only authority: it
    # grants no chapter basis, which stays bound to the target's own title.
    (provider,) = report.providers
    assert p.provider_candidate_represents_target_recording(
        report, provider, provider.candidates[0]
    )
    assert recording_identity_basis(report) is None


def test_an_alternative_candidate_outside_the_band_is_a_refused_rung() -> None:
    plan = _alternative_plan("51000.000")
    seen, handle = _pages(_search_page(), _work_page(_ALTERNATIVE))
    observation = _observed(handle, plan).observation
    alternative = _by_basis(observation, plan)["path_leaf"]

    assert 2 == len(seen)
    assert ProviderStatus.NO_MATCH is alternative.status
    assert _ladder(_ALTERNATIVE, _AUTHORS) == alternative.exhausted_dispatch
    assert d.work_ladder_diagnostics(0, 1) == alternative.diagnostics
    assert () == observation.candidates
    assert () == observation.recording_lookup_candidates
    assert FailureCategory.WORK_IDENTITY_REFUSED in {
        category for category, _ in _diagnostics(observation)
    }
    _alternative_report(observation)


@pytest.mark.parametrize(
    "asserted",
    (
        _json(_work_page(_ASSERTED)),
        _json({"error": "unavailable"}, 503),
        _json(
            {
                "total_results": 6,
                "products": [
                    {**_search_page(0)["products"][0], "asin": f"B0{index:08d}"}
                    for index in range(6)
                ],
            }
        ),
    ),
    ids=("candidates", "unavailable", "candidate_limit"),
)
def test_an_asserted_lane_that_answers_suppresses_the_alternative(
    asserted: httpx.Response,
) -> None:
    plan = _alternative_plan()
    seen, handle = _recorder(default=asserted)
    observation = _observed(handle, plan).observation

    assert {_ASSERTED} == {url.params["title"] for url in seen}
    assert [None] == list(_by_basis(observation, plan))
    assert ProviderStatus.NO_MATCH is not observation.lanes[0].status


def test_a_series_collapsed_title_asks_only_the_alternative() -> None:
    target = _series_collapsed_target()
    plan = _alternative_plan(target=target)
    seen, handle = _pages(_work_page(_ALTERNATIVE))
    observation = _observed(handle, plan).observation

    assert [_ALTERNATIVE] == [url.params["title"] for url in seen]
    assert ProviderStatus.CANDIDATES is observation.status
    _alternative_report(observation, target=target)


def _lane_result(
    lane: m.MetadataDiscoveryLane,
    observed: Any,
    candidates: tuple[Any, ...] = (),
    accessed_at: str | None = None,
) -> m.ProviderLaneResult:
    return m.ProviderLaneResult(
        lane,
        observed.status,
        candidates,
        accessed_at,
        diagnostics=observed.diagnostics,
        dispatch_lineage=observed.dispatch_lineage,
        transformation_lineage=observed.transformation_lineage,
        exhausted_dispatch=observed.exhausted_dispatch,
    )


def _asserted_only(title: str, runtime: str) -> Any:
    """One asserted-lane observation whose single product is titled `title`."""
    seen, handle = _pages(_work_page(title))
    return _observed(
        handle,
        m.audiobook_discovery_plan(
            audio_target(title, _AUTHORS),
            (target_title_claim(title), local_runtime_claim(runtime)),
            audio=single_track_audio_facts(),
        ),
    ).observation


def test_a_report_replays_its_alternative_lane_in_precedence_order() -> None:
    plan = _alternative_plan()
    asserted, alternative = m.provider_discovery_lane_order(plan)
    _, handle = _pages(_search_page(), _work_page(_ALTERNATIVE))
    valid = _observed(handle, plan).observation
    lanes = _by_basis(valid, plan)
    at = valid.accessed_at

    def observation(*results: m.ProviderLaneResult) -> Any:
        return m.provider_observation("audible", results, authority="audible_catalog")

    uncorroborated = _asserted_only(_ALTERNATIVE, "51000.000")
    other = _asserted_only("Other Title", "58244.201")
    shapeless = observation(
        _lane_result(asserted, lanes[None], accessed_at=at),
        _lane_result(
            alternative,
            uncorroborated.lanes[0],
            uncorroborated.candidates,
            at,
        ),
    )
    cases = (
        # Unknown identity outranks the bad candidate it also carries.
        (shapeless, _alternative_audio("Other Real Title"), "work lane is unknown"),
        (
            observation(
                _lane_result(alternative, lanes["path_leaf"], valid.candidates, at)
            ),
            None,
            "work lanes do not match the plan",
        ),
        (shapeless, None, "alternative candidate is invalid"),
        (
            observation(
                _lane_result(asserted, lanes[None], accessed_at=at),
                _lane_result(alternative, other.lanes[0], other.candidates, at),
            ),
            None,
            "work identity lineage is inconsistent",
        ),
        (
            observation(
                _lane_result(asserted, lanes[None], accessed_at=at),
                # A ladder the alternative title does not produce.
                m.ProviderLaneResult(
                    alternative,
                    ProviderStatus.NO_MATCH,
                    accessed_at=at,
                    diagnostics=d.work_ladder_diagnostics(2, 0),
                    exhausted_dispatch=_ladder(_SUBTITLED, _AUTHORS),
                ),
            ),
            None,
            "exhausted dispatch is inconsistent",
        ),
    )
    for provider, audio, message in cases:
        with pytest.raises(ReportValueError, match=message):
            _alternative_report(provider, audio=audio)


# The #1256 narrowing: an audiobook search whose item states one composed
# runtime asks for a 25-product page and drops what the loose band contradicts
# before the five-candidate bound applies.
_NARROWED = "Synthetic Journey"
_NARROWED_AUTHORS = ("Example Author",)
# One thousand minutes, so a stated 990 sits on the loose band's edge.
_NARROWED_RUNTIME = "60000.000"


def _narrowing_plan(runtime: str | None = _NARROWED_RUNTIME) -> m.MetadataDiscoveryPlan:
    return m.audiobook_discovery_plan(
        audio_target(_NARROWED, _NARROWED_AUTHORS),
        (target_title_claim(_NARROWED),)
        + (() if runtime is None else (local_runtime_claim(runtime),)),
        audio=single_track_audio_facts(),
    )


def _product(index: int, minutes: int | None) -> dict[str, Any]:
    product: dict[str, Any] = {
        "asin": f"B0{index:08d}",
        "title": _NARROWED,
        "authors": [{"name": "Example Author"}],
    }
    if minutes is not None:
        product["runtime_length_min"] = minutes
    return product


def _narrowed_page(*minutes: int | None) -> dict[str, Any]:
    return {
        "total_results": len(minutes),
        "products": [_product(index, value) for index, value in enumerate(minutes)],
    }


def _identities(*indices: int) -> set[str]:
    return {f"audible-product-B0{index:08d}" for index in indices}


def _narrowed_report(observation: Any) -> MetadataReport:
    target = audio_target(_NARROWED, _NARROWED_AUTHORS)
    return MetadataReport.create(
        target=d.TargetFacts("primary", "audio", "item-1", target.fields),
        file=d.FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(
            target_title_claim(_NARROWED),
            local_runtime_claim(_NARROWED_RUNTIME),
        ),
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=single_track_audio_facts(),
    )


def test_an_overflowing_page_narrows_to_its_three_in_band_products(
    tmp_path: Path,
) -> None:
    seen, handle = _recorder(default=_json(_narrowed_page(*[1_000] * 3, *[500] * 16)))
    observation = _observed(handle, _narrowing_plan()).observation

    assert [("25", _NARROWED)] == [
        (url.params["num_results"], url.params["title"]) for url in seen
    ]
    # Three products of one work is a refused rung: its count and its hints are
    # the three survivors, never the sixteen the band dropped.
    assert ProviderStatus.NO_MATCH is observation.status
    assert d.work_ladder_diagnostics(0, 3) == observation.lanes[0].diagnostics
    assert _identities(0, 1, 2) == {
        candidate.identity for candidate in observation.recording_lookup_candidates
    }
    report = _narrowed_report(observation)
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )

    path = tmp_path / "application.sqlite3"
    repository = open_repository(path)
    try:
        repository.publish(report, lambda: True)
    finally:
        repository.close()
    repository = open_repository(path, create=False)
    try:
        stored = repository.get(report.report_id)
    finally:
        repository.close()
    assert MetadataReport.from_json(stored) == report
    # A v51 store's lanes meant the unnarrowed page, so v52 refuses them.
    previous = json.loads(report.to_json())
    previous["schema_version"] = "metadata-report-v51"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(json.dumps(previous))


def test_more_than_five_in_band_products_still_overflow() -> None:
    seen, handle = _recorder(default=_json(_narrowed_page(*[1_000] * 7, *[500] * 12)))
    observation = _observed(handle, _narrowing_plan()).observation

    assert 1 == len(seen)
    assert ProviderStatus.CANDIDATE_LIMIT_EXCEEDED is observation.status
    assert () == observation.candidates
    assert () == observation.recording_lookup_candidates
    report = _narrowed_report(observation)
    assert MetadataReport.from_json(report.to_json()) == report


def test_the_loose_band_edge_is_kept_and_one_minute_past_it_is_dropped() -> None:
    # 990 minutes is 600 s short of the item; 989 is 660 s short.
    _seen, handle = _recorder(default=_json(_narrowed_page(990, 989, *[500] * 4)))
    observation = _observed(handle, _narrowing_plan()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert _identities(0) == {value.identity for value in observation.candidates}
    report = _narrowed_report(observation)
    assert MetadataReport.from_json(report.to_json()) == report


def test_products_that_state_no_runtime_survive_and_count_toward_the_bound() -> None:
    _seen, overflow = _recorder(default=_json(_narrowed_page(*[None] * 6, *[500] * 10)))
    assert (
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
        is _observed(overflow, _narrowing_plan()).observation.status
    )

    _seen, single = _recorder(default=_json(_narrowed_page(None, *[500] * 5)))
    observation = _observed(single, _narrowing_plan()).observation
    assert ProviderStatus.CANDIDATES is observation.status
    assert _identities(0) == {value.identity for value in observation.candidates}


def test_a_page_of_five_or_fewer_is_not_narrowed() -> None:
    _seen, handle = _recorder(default=_json(_narrowed_page(*[500] * 5)))
    observation = _observed(handle, _narrowing_plan()).observation

    # Narrowing would have emptied the rung; unnarrowed, all five are refused.
    assert ProviderStatus.NO_MATCH is observation.status
    assert d.work_ladder_diagnostics(0, 5) == observation.lanes[0].diagnostics


def test_a_page_the_band_empties_is_an_empty_rung() -> None:
    _seen, handle = _recorder(default=_json(_narrowed_page(*[500] * 6)))
    observation = _observed(handle, _narrowing_plan()).observation

    assert ProviderStatus.NO_MATCH is observation.status
    assert d.work_ladder_diagnostics(1, 0) == observation.lanes[0].diagnostics
    assert ((FailureCategory.EMPTY_NO_MATCH, 1),) == _diagnostics(observation)


def _duplicated_out_of_band() -> dict[str, Any]:
    page = _narrowed_page(*[1_000] * 2, *[500] * 5)
    page["products"][-1]["asin"] = page["products"][-2]["asin"]
    return page


def _anonymous_out_of_band() -> dict[str, Any]:
    page = _narrowed_page(*[1_000] * 2, *[500] * 5)
    del page["products"][-1]["asin"]
    return page


@pytest.mark.parametrize(
    "page",
    (
        _narrowed_page(*[1_000] * 2, *[500] * 24),
        _duplicated_out_of_band(),
        _anonymous_out_of_band(),
    ),
    ids=("over_page_size", "duplicate_out_of_band", "anonymous_out_of_band"),
)
def test_a_page_the_parser_refuses_fails_whole_before_narrowing(
    page: dict[str, Any],
) -> None:
    _seen, handle = _recorder(default=_json(page))
    observation = _observed(handle, _narrowing_plan()).observation

    assert ProviderStatus.MALFORMED is observation.status
    assert () == observation.candidates


def _captured_products(count: int) -> bytes:
    product = payload("audible_search")["products"][0]
    return json.dumps(
        {
            "total_results": count,
            "products": [
                {**copy.deepcopy(product), "asin": f"B0{index:08d}"}
                for index in range(count)
            ],
        }
    ).encode()


def _body(
    content: bytes,
) -> tuple[list[httpx.URL], Callable[[httpx.Request], httpx.Response]]:
    return _recorder(
        default=httpx.Response(
            200, headers={"content-type": "application/json"}, content=content
        )
    )


def test_the_narrowed_page_reads_under_its_own_bound_whatever_is_configured() -> None:
    body = _captured_products(19)
    assert 65_536 < len(body) < 262_144

    _seen, handle = _body(body)
    narrowed = _provider(handle, maximum_bytes=65_536)
    observation = m.observe_provider(
        "audible", "audible_catalog", narrowed, _narrowing_plan()
    ).observation
    # Every captured product states 970 minutes, outside the band of 1,000.
    assert ProviderStatus.NO_MATCH is observation.status
    assert 1_048_576 - len(body) == narrowed._remaining_response_bytes

    seen, handle = _body(body)
    unnarrowed = _provider(handle, maximum_bytes=65_536)
    observation = m.observe_provider(
        "audible", "audible_catalog", unnarrowed, _narrowing_plan(runtime=None)
    ).observation
    assert ProviderStatus.OVERSIZED is observation.status
    assert ["6"] == [url.params["num_results"] for url in seen]
    assert 0 == unnarrowed._remaining_response_bytes


def test_a_narrowed_body_over_its_bound_fails_bounded_rather_than_truncated() -> None:
    body = _captured_products(41)
    assert len(body) > 262_144
    seen, handle = _body(body)
    provider = _provider(handle, maximum_bytes=262_144)
    observation = m.observe_provider(
        "audible", "audible_catalog", provider, _narrowing_plan()
    ).observation

    assert ProviderStatus.OVERSIZED is observation.status
    assert 1 == len(seen)
    assert 0 == provider._remaining_response_bytes
    assert () == observation.candidates


def test_the_aggregate_is_one_fixed_mebibyte_for_every_configured_bound() -> None:
    for maximum_bytes in (1_024, 65_536, 262_144):
        provider = _provider(_recorder()[1], maximum_bytes=maximum_bytes)
        assert 1_048_576 == provider._remaining_response_bytes
        provider._client.close()


def test_an_ebook_shaped_plan_with_a_runtime_still_asks_for_six() -> None:
    seen, handle = _recorder(default=_json(_narrowed_page(*[1_000] * 6)))
    observation = _observed(handle, work_plan(local_runtime_claim())).observation

    assert {"6"} == {url.params["num_results"] for url in seen}
    assert ProviderStatus.CANDIDATE_LIMIT_EXCEEDED is observation.status


def test_a_narrowed_empty_asserted_lane_permits_the_alternative() -> None:
    plan = _alternative_plan()
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if request.url.params["title"] == _ALTERNATIVE:
            return _json(_work_page(_ALTERNATIVE))
        # Six products of the asserted title, every one far outside the band.
        return _json(
            {
                "total_results": 6,
                "products": [
                    {
                        **_search_page(0)["products"][0],
                        "asin": f"B0{index:08d}",
                        "runtime_length_min": 500,
                    }
                    for index in range(6)
                ],
            }
        )

    observation = _observed(handle, plan).observation
    lanes = _by_basis(observation, plan)

    assert {"25"} == {url.params["num_results"] for url in seen}
    assert _ALTERNATIVE == seen[-1].params["title"]
    asserted = lanes[None]
    assert ProviderStatus.NO_MATCH is asserted.status
    assert asserted.exhausted_dispatch
    assert asserted.diagnostics == d.work_ladder_diagnostics(
        len(asserted.exhausted_dispatch), 0
    )
    assert ProviderStatus.CANDIDATES is lanes["path_leaf"].status
    (candidate,) = observation.candidates
    assert _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)
    report = _alternative_report(observation)
    assert report == MetadataReport.from_json(report.to_json())


# #1257: a refused product inside the tight band keeps its ASIN as a
# lookup-only hint on the asserted lane.
_HINTED = "Synthetic Journey: A Novel"
_HINT_LADDER = tuple(
    WorkDispatchLineage.from_query(query)
    for query in work_query_ladder(_HINTED, _NARROWED_AUTHORS, audiobook=True)
)


def _hint_plan() -> m.MetadataDiscoveryPlan:
    return m.audiobook_discovery_plan(
        audio_target(_HINTED, _NARROWED_AUTHORS),
        (target_title_claim(_HINTED), local_runtime_claim(_NARROWED_RUNTIME)),
        audio=single_track_audio_facts(),
    )


def _titled(index: int, minutes: int, title: str = "Other Work") -> dict[str, Any]:
    return {**_product(index, minutes), "title": title}


def _page(*products: dict[str, Any]) -> dict[str, Any]:
    return {"total_results": len(products), "products": list(products)}


def _hint_report(observation: Any) -> MetadataReport:
    target = audio_target(_HINTED, _NARROWED_AUTHORS)
    return MetadataReport.create(
        target=d.TargetFacts("primary", "audio", "item-1", target.fields),
        file=d.FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(
            target_title_claim(_HINTED),
            local_runtime_claim(_NARROWED_RUNTIME),
        ),
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=single_track_audio_facts(),
    )


def _hint(index: int, minutes: int, rung: int) -> d.RecordingLookupHint:
    return d.RecordingLookupHint(f"B0{index:08d}", minutes * 60, _HINT_LADDER[rung])


def test_a_refused_tight_product_on_an_exhausted_ladder_becomes_a_hint() -> None:
    seen, handle = _pages(
        # A tight product and one far outside the band, both refused on title.
        _page(_titled(1, 1_001), _titled(2, 900)),
        _page(),
    )
    observation = _observed(handle, _hint_plan()).observation
    (lane,) = observation.lanes

    assert 2 == len(seen) == len(_HINT_LADDER)
    assert ProviderStatus.NO_MATCH is lane.status
    assert _HINT_LADDER == lane.exhausted_dispatch
    assert (_hint(1, 1_001, 0),) == lane.recording_lookup_hints
    assert () == observation.recording_lookup_candidates
    report = _hint_report(observation)
    assert report == MetadataReport.from_json(report.to_json())
    assert [
        {
            "asin": "B000000001",
            "basis": "runtime_within_tight_band",
            "rung": _HINT_LADDER[0].to_data(),
            "runtime_seconds": 60_060,
        }
    ] == json.loads(report.to_json())["providers"][0]["lanes"][0][
        "recording_lookup_hints"
    ]


def test_a_refused_tight_product_beside_a_later_admission_is_kept() -> None:
    _, handle = _pages(
        _page(_titled(1, 999)),
        _page(_titled(2, 1_000, _HINTED)),
    )
    observation = _observed(handle, _hint_plan()).observation
    (lane,) = observation.lanes

    assert ProviderStatus.CANDIDATES is lane.status
    assert _HINT_LADDER[1] == lane.dispatch_lineage
    assert (_hint(1, 999, 0),) == lane.recording_lookup_hints
    assert {"audible-product-B000000002"} == {
        value.identity for value in observation.candidates
    }
    _hint_report(observation)


def test_a_refused_tight_product_beside_one_admitted_on_its_rung_is_kept() -> None:
    _, handle = _pages(_page(_titled(2, 1_000, _HINTED), _titled(1, 1_001)))
    observation = _observed(handle, _hint_plan()).observation
    (lane,) = observation.lanes

    assert ProviderStatus.CANDIDATES is lane.status
    assert _HINT_LADDER[0] == lane.dispatch_lineage
    assert (_hint(1, 1_001, 0),) == lane.recording_lookup_hints
    assert {"audible-product-B000000002"} == {
        value.identity for value in observation.candidates
    }
    _hint_report(observation)


def test_a_hint_from_a_rung_after_the_admitting_one_is_refused() -> None:
    import hashlib

    _, handle = _pages(_page(_titled(2, 1_000, _HINTED), _titled(1, 1_001)))
    report = _hint_report(_observed(handle, _hint_plan()).observation)
    data = json.loads(report.to_json())
    lane = data["providers"][0]["lanes"][0]
    lane["recording_lookup_hints"][0]["rung"] = _HINT_LADDER[1].to_data()
    data.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(d.canonical_json(data).encode()).hexdigest()
    )

    with pytest.raises(ReportValueError, match="hint rung is inconsistent"):
        MetadataReport.from_json(d.canonical_json(data))


def test_hints_are_bounded_earliest_rung_first_without_repeats() -> None:
    _, handle = _pages(
        _page(_titled(5, 1_000), _titled(3, 1_000)),
        # Rung two repeats a hint and refuses one more tight product, whose
        # lower ASIN does not displace the earlier rung's.
        _page(_titled(3, 1_000), _titled(1, 1_000)),
    )
    observation = _observed(handle, _hint_plan()).observation
    (lane,) = observation.lanes

    assert ProviderStatus.NO_MATCH is lane.status
    assert d.work_ladder_diagnostics(0, 4) == lane.diagnostics
    assert (_hint(3, 1_000, 0), _hint(5, 1_000, 0)) == lane.recording_lookup_hints
    _hint_report(observation)


def _series_decorated(index: int) -> dict[str, Any]:
    """A product only the series-suffix transformation admits, never exactly."""
    return {
        **_titled(index, 1_000, _HINTED + ": Example Cycle, Book 2"),
        "series": [{"title": "Example Cycle", "sequence": "2"}],
    }


def test_a_product_admitted_on_a_later_rung_is_not_also_a_hint() -> None:
    _, handle = _pages(
        # Two works admitted on rung one refuse its set, and neither is exact.
        _page(_series_decorated(4), _series_decorated(6)),
        _page(_series_decorated(4)),
    )
    observation = _observed(handle, _hint_plan()).observation
    (lane,) = observation.lanes

    assert ProviderStatus.CANDIDATES is lane.status
    assert {"audible-product-B000000004"} == {
        value.identity for value in observation.candidates
    }
    assert (_hint(6, 1_000, 0),) == lane.recording_lookup_hints
    _hint_report(observation)


def test_a_terminal_rung_after_a_tight_refusal_keeps_no_hint() -> None:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return (
            _json(_page(_titled(1, 1_000)))
            if len(seen) == 1
            else httpx.Response(200, text="<html></html>")
        )

    observation = _observed(handle, _hint_plan()).observation

    assert 2 == len(seen)
    assert ProviderStatus.MALFORMED is observation.status
    assert () == observation.lanes[0].recording_lookup_hints


def test_the_alternative_lane_keeps_no_hint() -> None:
    plan = _alternative_plan()
    _, handle = _pages(_search_page(), _page(_titled(1, 970)))
    observation = _observed(handle, plan).observation
    lanes = _by_basis(observation, plan)

    assert ProviderStatus.NO_MATCH is lanes["path_leaf"].status
    assert lanes["path_leaf"].exhausted_dispatch
    assert () == lanes["path_leaf"].recording_lookup_hints
    assert () == lanes[None].recording_lookup_hints
    _alternative_report(observation)


def test_an_alternative_admission_is_not_also_the_asserted_lanes_hint() -> None:
    """The asserted ladder refuses what the alternative title then admits."""
    plan = _alternative_plan()
    _, handle = _pages(_work_page(_ALTERNATIVE), _work_page(_ALTERNATIVE))
    observation = _observed(handle, plan).observation
    lanes = _by_basis(observation, plan)

    assert ProviderStatus.NO_MATCH is lanes[None].status
    assert ProviderStatus.CANDIDATES is lanes["path_leaf"].status
    assert () == lanes[None].recording_lookup_hints
    report = _alternative_report(observation)
    assert report == MetadataReport.from_json(report.to_json())


def test_a_lane_result_states_hints_only_where_the_lane_can() -> None:
    plan = _hint_plan()
    (lane,) = plan.lanes
    hint = _hint(1, 1_000, 0)
    valid = m.ProviderLaneResult(
        lane,
        ProviderStatus.NO_MATCH,
        diagnostics=d.work_ladder_diagnostics(1, 1),
        exhausted_dispatch=_HINT_LADDER,
        recording_lookup_hints=(hint,),
    )
    alternative = _alternative_plan().lanes
    for changes in (
        {"recording_lookup_hints": [hint]},
        {"recording_lookup_hints": (hint, hint)},
        {"recording_lookup_hints": (_hint(2, 1_000, 0), hint)},
        {
            "recording_lookup_hints": (
                _hint(1, 1_000, 0),
                _hint(2, 1_000, 0),
                _hint(3, 1_000, 0),
            )
        },
        {"recording_lookup_hints": ("B000000001",)},
        {"exhausted_dispatch": ()},
        {"status": ProviderStatus.UNAVAILABLE, "exhausted_dispatch": ()},
        {
            "lane": next(value for value in alternative if value.title_basis),
        },
    ):
        with pytest.raises(m.MetadataReportError):
            replace(valid, **changes)
    assert valid.recording_lookup_hints == (hint,)


# The #1258 near-miss: the closest title-matching product the loose band ruled
# out, read from the whole parsed page before narrowing and never sent for.
def _dispatch(observation: Any, plan: m.MetadataDiscoveryPlan, basis: Any = None):
    lane = _by_basis(observation, plan)[basis]
    assert lane.dispatch is not None
    return lane.dispatch


def _without_near_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    from mechanical_librarian.adapters.providers import audible_catalog_metadata

    monkeypatch.setattr(
        audible_catalog_metadata, "loose_band_near_miss", lambda *_: None
    )


def _observed_twice(
    monkeypatch: pytest.MonkeyPatch,
    plan: m.MetadataDiscoveryPlan,
    *pages: dict[str, Any],
) -> tuple[Any, Any, list[httpx.URL], list[httpx.URL]]:
    """The same walk with and without the near-miss, to show it is free."""
    seen, handle = _pages(*pages)
    observation = _observed(handle, plan).observation
    with monkeypatch.context() as patched:
        _without_near_miss(patched)
        unmeasured_seen, unmeasured = _pages(*pages)
        bare = _observed(unmeasured, plan).observation
    return observation, bare, seen, unmeasured_seen


def _stripped(observation: Any) -> dict[str, Any]:
    data = observation.to_data()
    for lane in data["lanes"]:
        lane["dispatch"]["near_miss_seconds"] = None
    return data


def test_a_narrowed_page_measures_its_near_miss_before_the_band_drops_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1000 minutes locally: 980 is 1200 s short, 1030 is 1800 s long, and the
    # three at 1000 are the survivors the five-candidate bound sees.
    page = _narrowed_page(*[1_000] * 3, 980, 1_030, *[500] * 14)
    plan = _narrowing_plan()
    observation, bare, seen, bare_seen = _observed_twice(monkeypatch, plan, page)

    assert [("25", _NARROWED)] == [
        (url.params["num_results"], url.params["title"]) for url in seen
    ]
    assert seen == bare_seen
    assert _identities(0, 1, 2) == {
        value.identity for value in observation.recording_lookup_candidates
    }
    assert _stripped(observation) == _stripped(bare)
    assert -1_200 == _dispatch(observation, plan).near_miss_seconds
    assert _dispatch(bare, plan).near_miss_seconds is None
    report = _narrowed_report(observation)
    assert MetadataReport.from_json(report.to_json()) == report


def test_an_unnarrowed_page_measures_its_near_miss_too() -> None:
    plan = _narrowing_plan()
    seen, handle = _pages(_narrowed_page(500, 1_030, 1_000))
    observation = _observed(handle, plan).observation

    assert [("25", _NARROWED)] == [
        (url.params["num_results"], url.params["title"]) for url in seen
    ]
    assert 1_800 == _dispatch(observation, plan).near_miss_seconds
    report = _narrowed_report(observation)
    assert MetadataReport.from_json(report.to_json()) == report


def _refused_page(title: str, minutes: int, local_minutes: int) -> dict[str, Any]:
    """A narrowed page whose only title match the band drops, beside five
    in-band products of other works that admission refuses."""
    products = [
        {
            "asin": f"B0{index:08d}",
            "title": f"Another Work {index}",
            "authors": [{"name": "Andy Weir"}],
            "runtime_length_min": local_minutes,
        }
        for index in range(1, 6)
    ]
    products.append(
        {
            "asin": "B000000099",
            "title": title,
            "authors": [{"name": "Andy Weir"}],
            "runtime_length_min": minutes,
        }
    )
    return {"total_results": len(products), "products": products}


def test_the_near_miss_is_the_minimum_over_every_rung_walked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 850 minutes locally: 875 is 1500 s long and 835 is 900 s short.
    plan = m.audiobook_discovery_plan(
        audio_target(_SUBTITLED, ("Andy Weir",)),
        (target_title_claim(_SUBTITLED), local_runtime_claim("51000.000")),
        audio=single_track_audio_facts(),
    )
    observation, bare, seen, bare_seen = _observed_twice(
        monkeypatch,
        plan,
        _refused_page(_SUBTITLED, 875, 850),
        _refused_page(_SUBTITLED, 835, 850),
    )

    ladder = _ladder(_SUBTITLED, ("Andy Weir",))
    assert 2 == len(seen) == len(ladder)
    assert seen == bare_seen
    assert _stripped(observation) == _stripped(bare)
    assert d.ProviderLaneDispatch(
        d.DispatchOutcome.REFUSED, len(ladder), -900
    ) == _dispatch(observation, plan)


def test_only_the_asserted_lane_records_a_near_miss() -> None:
    # 58244 s locally, and both lanes' title match states 950 minutes.
    plan = _alternative_plan()
    seen, handle = _pages(
        _refused_page(_ASSERTED, 950, 970), _refused_page(_ALTERNATIVE, 950, 970)
    )
    observation = _observed(handle, plan).observation

    assert [_ASSERTED, _ALTERNATIVE] == [url.params["title"] for url in seen]
    assert d.ProviderLaneDispatch(d.DispatchOutcome.REFUSED, 1, 57_000 - 58_244) == (
        _dispatch(observation, plan)
    )
    assert d.ProviderLaneDispatch(d.DispatchOutcome.REFUSED, 1, None) == (
        _dispatch(observation, plan, "path_leaf")
    )
    _alternative_report(observation)


def test_an_admitted_rung_counts_its_depth() -> None:
    seen, handle = _pages(_search_page(1), _work_page(_SUBTITLED))
    plan = _audiobook_work_plan()
    observation = _observed(handle, plan).observation

    assert 2 == len(seen)
    assert d.ProviderLaneDispatch(d.DispatchOutcome.ADMITTED, 2, None) == (
        _dispatch(observation, plan)
    )


# Band-corroborated work admission (#1260), over the real adapter. The item's
# own running time is 58,244 s; 970 minutes is inside the 90 s tight band and
# 900 minutes is outside the 600 s loose band. Every name here is synthetic.
_BAND_TITLE = "Plain Work (Example Saga)"
_BAND_AUTHORS = ("Ada Example",)


def _band_product(
    asin: str,
    title: str,
    *,
    authors: tuple[str, ...] = _BAND_AUTHORS,
    minutes: int | None = 970,
) -> dict[str, Any]:
    product: dict[str, Any] = {
        "asin": asin,
        "title": title,
        "authors": [{"name": value} for value in authors],
    }
    if minutes is not None:
        product["runtime_length_min"] = minutes
    return product


def _band_plan(
    *,
    title: str = _BAND_TITLE,
    authors: tuple[str, ...] = _BAND_AUTHORS,
    runtime: str | None = "58244.201",
    alternative: str | None = None,
) -> m.MetadataDiscoveryPlan:
    from mechanical_librarian.domain.metadata_report import AudioDiscoveryTitle

    return m.audiobook_discovery_plan(
        audio_target(title, authors),
        (
            target_title_claim(title),
            *(() if runtime is None else (local_runtime_claim(runtime),)),
        ),
        audio=replace(
            single_track_audio_facts(),
            discovery_titles=(
                ()
                if alternative is None
                else (AudioDiscoveryTitle("path_leaf", alternative),)
            ),
        ),
    )


def _band_observation(
    products: Callable[[str], list[dict[str, Any]]],
    plan: m.MetadataDiscoveryPlan,
) -> tuple[Any, list[str]]:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["title"])
        page = products(asked[-1])
        return _json({"total_results": len(page), "products": page})

    return _observed(handle, plan).observation, asked


def _band_report(
    observation: Any,
    plan: m.MetadataDiscoveryPlan,
    *,
    title: str = _BAND_TITLE,
    authors: tuple[str, ...] = _BAND_AUTHORS,
) -> MetadataReport:
    assert plan.audio is not None
    report = MetadataReport.create(
        target=d.TargetFacts(
            "primary", "audio", "item-1", audio_target(title, authors).fields
        ),
        file=d.FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=plan.local_claims,
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=plan.audio,
    )
    assert MetadataReport.from_json(report.to_json()) == report
    return report


@pytest.mark.parametrize(
    ("title", "production"),
    (
        (_BAND_TITLE, "band_parenthesized_series_name"),
        ("Plain Work, Book 2", "band_trailing_series_sequence"),
        ("Example Saga Book 2 - Plain Work", "band_order_inverted_series_prefix"),
    ),
)
def test_a_tight_product_equal_to_the_shaped_rung_admits_as_a_recording(
    title: str, production: str
) -> None:
    observation, asked = _band_observation(
        lambda _title: [_band_product("B000000001", "Plain Work")],
        _band_plan(title=title),
    )
    report = _band_report(observation, _band_plan(title=title), title=title)

    (lane,) = observation.lanes
    assert ["Plain Work"] == asked
    assert ProviderStatus.CANDIDATES is lane.status
    assert lane.transformation_lineage is not None
    assert production == lane.transformation_lineage.production
    (candidate,) = observation.candidates
    assert any(
        value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        for value in candidate.evidence
    )
    assert not lane.recording_lookup_hints
    assert p.AudiobookReviewScope.RECORDING is p.audiobook_review_scope(report)
    assert recording_identity_basis(report) is None
    # The proposal set replays the same band-aware admission before it lets
    # the candidate describe the target work.
    (provider,) = report.providers
    assert p.provider_candidate_represents_target_work(
        report, provider, provider.candidates[0]
    )


@pytest.mark.parametrize(
    ("products", "plan_arguments"),
    (
        ([_band_product("B000000001", "Plain Work", minutes=900)], {}),
        ([_band_product("B000000001", "Plain Work")], {"runtime": None}),
        (
            [_band_product("B000000001", "Plain Work")],
            {"title": "Plain Work (Dramatized)"},
        ),
        ([_band_product("B000000001", "Other Work")], {}),
    ),
    ids=("out_of_band", "no_local_runtime", "dramatized", "wrong_work_tight"),
)
def test_a_band_first_refusal_leaves_the_lane_refused(
    products: list[dict[str, Any]], plan_arguments: dict[str, Any]
) -> None:
    plan = _band_plan(**plan_arguments)
    observation, _asked = _band_observation(lambda _title: products, plan)
    report = _band_report(
        observation, plan, title=plan_arguments.get("title", _BAND_TITLE)
    )

    assert ProviderStatus.NO_MATCH is observation.status
    assert not observation.candidates
    assert p.AudiobookReviewScope.RECORDING is not p.audiobook_review_scope(report)


def test_a_wrong_work_tight_product_stays_a_hint() -> None:
    plan = _band_plan()
    observation, _asked = _band_observation(
        lambda _title: [_band_product("B000000001", "Other Work")], plan
    )

    (lane,) = observation.lanes
    assert ("B000000001",) == tuple(value.asin for value in lane.recording_lookup_hints)


def test_the_alternative_lane_never_admits_band_first() -> None:
    """#1255's rule is byte-identical: the widening is the asserted lane's."""
    plan = _band_plan(title="Asserted Title", alternative=_BAND_TITLE)
    observation, asked = _band_observation(
        lambda title: (
            []
            if title == "Asserted Title"
            else [_band_product("B000000001", "Plain Work")]
        ),
        plan,
    )
    _band_report(observation, plan, title="Asserted Title")

    assert ["Asserted Title", "Plain Work"] == asked
    assert ProviderStatus.NO_MATCH is observation.status
    assert all(lane.transformation_lineage is None for lane in observation.lanes)


@pytest.mark.parametrize(
    ("product_author", "admitted"),
    (("J. R. R. Example", True), ("JRR Example", False)),
)
def test_initials_fold_admits_inside_the_band_only(
    product_author: str, admitted: bool
) -> None:
    plan = _band_plan(title="Plain Work", authors=("J.R.R. Example",))
    observation, _asked = _band_observation(
        lambda _title: [
            _band_product("B000000001", "Plain Work", authors=(product_author,))
        ],
        plan,
    )
    _band_report(observation, plan, title="Plain Work", authors=("J.R.R. Example",))

    assert admitted is (observation.status is ProviderStatus.CANDIDATES)
    if admitted:
        (lane,) = observation.lanes
        assert lane.transformation_lineage is not None
        assert [
            (value.side.value, value.ordinal)
            for value in lane.transformation_lineage.applications
        ] == [("local", 0), ("provider", 0)]
        assert "band_initials_fold" == lane.transformation_lineage.production


def _forced_band_claims(
    monkeypatch: pytest.MonkeyPatch, claims: tuple[Any, ...]
) -> None:
    """Hand every set admission the band claims a forger would assert."""
    original = m.provider_candidate_set_work_identity_admission

    def forged(*arguments: Any, **keywords: Any) -> Any:
        return original(*arguments, **{**keywords, "band_claims": claims})

    monkeypatch.setattr(m, "provider_candidate_set_work_identity_admission", forged)


@pytest.mark.parametrize(
    ("plan_arguments", "title"),
    (
        ({"runtime": "50000.000"}, _BAND_TITLE),
        ({"runtime": None}, _BAND_TITLE),
        ({"title": "Asserted Title", "alternative": _BAND_TITLE}, "Asserted Title"),
    ),
    ids=("out_of_band_local_runtime", "no_local_runtime", "alternative_lane"),
)
def test_a_report_refuses_a_band_lineage_its_own_claims_do_not_replay(
    monkeypatch: pytest.MonkeyPatch, plan_arguments: dict[str, Any], title: str
) -> None:
    plan = _band_plan(**plan_arguments)
    with monkeypatch.context() as patched:
        _forced_band_claims(
            patched,
            (
                *(
                    value
                    for value in plan.local_claims
                    if value.kind is not ClaimKind.DURATION
                ),
                local_runtime_claim("58244.201"),
            ),
        )
        observation, _asked = _band_observation(
            lambda value: (
                []
                if value == "Asserted Title"
                else [_band_product("B000000001", "Plain Work")]
            ),
            plan,
        )
    assert ProviderStatus.CANDIDATES is observation.status

    with pytest.raises(
        ReportValueError, match="provider work identity lineage is inconsistent"
    ):
        _band_report(observation, plan, title=title)


def test_a_report_refuses_forged_initials_fold_sides() -> None:
    from mechanical_librarian.domain.work_identity import (
        WorkTitleTransformationApplication,
        WorkTitleTransformationField,
        WorkTitleTransformationLineage,
        WorkTitleTransformationSide,
    )

    plan = _band_plan(title="Plain Work", authors=("J.R.R. Example",))
    observation, _asked = _band_observation(
        lambda _title: [
            _band_product("B000000001", "Plain Work", authors=("J. R. R. Example",))
        ],
        plan,
    )
    forged = WorkTitleTransformationLineage(
        (
            WorkTitleTransformationApplication(
                WorkTitleTransformationFamily.INITIALS_FOLD,
                WorkTitleTransformationSide.PROVIDER,
                WorkTitleTransformationField.AUTHORS,
                0,
            ),
        )
    )
    (lane,) = observation.lanes
    observation = replace(
        observation,
        lanes=(replace(lane, transformation_lineage=forged),),
    )

    with pytest.raises(
        ReportValueError, match="provider work identity lineage is inconsistent"
    ):
        _band_report(observation, plan, title="Plain Work", authors=("J.R.R. Example",))


def test_a_band_admitted_title_is_offered_for_the_owner_to_choose() -> None:
    plan = _band_plan()
    observation, _asked = _band_observation(
        lambda _title: [_band_product("B000000001", "Plain Work")], plan
    )
    title = next(
        value
        for value in evaluate_report(_band_report(observation, plan)).results
        if value.destination is TargetDestination.TITLE
    )

    # The series the decoration named is not preserved, so #1252's guard
    # leaves the plain title an owner choice rather than an automatic one.
    assert ProposalOutcome.UNRESOLVED_CHOICE is title.outcome
    assert ["Plain Work"] == [value.value.value for value in title.alternatives]


def test_the_asserted_title_explicitly_contradicting_a_rung_match_refuses_it() -> None:
    """The rung title matches, but the asserted title contradicts the product.

    "Plain Work [Tag]" is the shaped rung; the product's own ladder strips the
    tag, so no rung of the asserted title meets one of the product's (#1260).
    """
    title = "Plain Work [Tag] (Example Saga)"
    plan = _band_plan(title=title)
    product = _band_product("B000000001", "Plain Work [Tag]")
    observation, asked = _band_observation(lambda _title: [product], plan)

    assert ["Plain Work [Tag]", "Plain Work Tag"] == asked
    assert ProviderStatus.NO_MATCH is observation.status


def test_an_alternative_trailing_sequence_rung_contradicting_the_asserted_title_is_refused() -> (
    None
):
    """#1259's control: the one W1 mention that explicitly contradicts the
    asserted title sits on an alternative rung, which never widens."""
    plan = _band_plan(title="Asserted Title", alternative="Plain Work, Book 2")
    product = _band_product("B000000001", "Plain Work")
    observation, asked = _band_observation(
        lambda value: [] if value == "Asserted Title" else [product], plan
    )
    _band_report(observation, plan, title="Asserted Title")
    (alternative,) = (lane for lane in plan.lanes if lane.title_basis is not None)
    probe = m.qualify_provider_observation(
        d.ProviderObservation(
            "audible",
            ProviderStatus.CANDIDATES,
            (),
            (_band_candidate(plan, product),),
            ProviderQueryKind.WORK,
            "audible_catalog",
            dispatch_lineage=WorkDispatchLineage(
                WorkDispatchRung.NORMALIZED,
                (WorkTitleTransformationFamily.TRAILING_SERIES_SEQUENCE,),
            ),
        ),
        _band_lookup(plan, alternative),
    )

    assert ["Asserted Title", "Plain Work"] == asked
    assert ProviderStatus.NO_MATCH is observation.status
    assert ProviderStatus.NO_MATCH is probe.status
    assert d.WorkIdentityContradiction.EXPLICIT is (
        d.provider_candidate_work_identity_contradiction(
            _band_candidate(plan, product), "Asserted Title", _BAND_AUTHORS
        )
    )


def _band_candidate(plan: m.MetadataDiscoveryPlan, product: dict[str, Any]) -> Any:
    """The one candidate the adapter builds from a product, unqualified."""
    from mechanical_librarian.adapters.providers import audible_catalog_metadata

    return audible_catalog_metadata._candidate(
        "audible",
        product,
        plan.local_claims,
        marketplace="us",
        schema_rejections=lambda _count: None,
    )


def _band_lookup(plan: m.MetadataDiscoveryPlan, lane: Any) -> m.MetadataLookup:
    return m.MetadataLookup(
        ProviderQueryKind.WORK,
        plan.local_claims,
        lane.title,
        lane.authors,
        audio=plan.audio,
        work_title=plan.work_title,
        work_authors=plan.work_authors,
        title_basis=lane.title_basis,
    )


@pytest.mark.parametrize(
    ("authority", "status"),
    (
        ("audible_catalog", ProviderStatus.CANDIDATES),
        ("hardcover", ProviderStatus.NO_MATCH),
        ("open_library", ProviderStatus.NO_MATCH),
    ),
)
def test_only_an_audiobook_authority_admits_band_first(
    authority: str, status: ProviderStatus
) -> None:
    plan = _band_plan()
    (lane,) = plan.lanes
    qualified = m.qualify_provider_observation(
        d.ProviderObservation(
            "audible",
            ProviderStatus.CANDIDATES,
            (),
            (_band_candidate(plan, _band_product("B000000001", "Plain Work")),),
            ProviderQueryKind.WORK,
            authority,
            dispatch_lineage=WorkDispatchLineage.from_query(
                work_query_ladder(_BAND_TITLE, _BAND_AUTHORS, audiobook=True)[0]
            ),
        ),
        _band_lookup(plan, lane),
    )

    assert status is qualified.status


def test_a_set_admitted_without_the_band_keeps_its_candidates_and_lineage() -> None:
    """Monotone: an exact decorated product beside a tight plain product."""
    plan = _band_plan()
    (lane,) = plan.lanes
    candidates = (
        _band_candidate(plan, _band_product("B000000001", _BAND_TITLE)),
        _band_candidate(plan, _band_product("B000000002", "Plain Work")),
    )
    dispatch = WorkDispatchLineage.from_query(
        work_query_ladder(_BAND_TITLE, _BAND_AUTHORS, audiobook=True)[0]
    )
    banded, plain = (
        d.provider_candidate_set_work_identity_admission(
            candidates,
            _BAND_TITLE,
            _BAND_AUTHORS,
            dispatch,
            audiobook=True,
            band_claims=claims,
        )
        for claims in (plan.local_claims, None)
    )

    assert banded == plain
    assert banded.admitted and (candidates[0],) == banded.candidates
    assert banded.transformation is None


def test_an_ebook_lookup_never_admits_band_first() -> None:
    plan = _band_plan()
    (lane,) = plan.lanes
    candidate = _band_candidate(plan, _band_product("B000000001", "Plain Work"))
    qualified = m.qualify_provider_observation(
        d.ProviderObservation(
            "audible",
            ProviderStatus.CANDIDATES,
            (),
            (candidate,),
            ProviderQueryKind.WORK,
            "audible_catalog",
            dispatch_lineage=WorkDispatchLineage.from_query(
                work_query_ladder(_BAND_TITLE, _BAND_AUTHORS)[0]
            ),
        ),
        m.MetadataLookup(
            ProviderQueryKind.WORK, plan.local_claims, lane.title, lane.authors
        ),
    )

    assert ProviderStatus.NO_MATCH is qualified.status


def test_a_band_admitted_report_is_v57_and_a_v56_relabel_is_refused() -> None:
    plan = _band_plan()
    observation, _asked = _band_observation(
        lambda _title: [_band_product("B000000001", "Plain Work")], plan
    )
    report = _band_report(observation, plan)
    previous = report.to_data()
    previous["schema_version"] = "metadata-report-v56"
    previous.pop("report_id")
    previous["report_id"] = d.report_content_address(previous)

    assert "metadata-report-v57" == report.schema_version
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(d.canonical_json(previous))
