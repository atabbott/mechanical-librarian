"""The iTunes Search adapter, proved against a capture-shaped audiobook search."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Callable

import httpx
import pytest

from mechanical_librarian.adapters.providers.itunes_search_metadata import (
    ITUNES_SEARCH_ORIGIN,
    ITUNES_SEARCH_URL,
    ITunesSearchMetadataProvider,
    ITunesSearchPacer,
)
from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    audiobook_discovery_plan,
    observe_provider,
)
from mechanical_librarian.domain import metadata_report as d
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureStage,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FieldState,
    ProviderEvidenceKind,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    work_ladder_diagnostics,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from mechanical_librarian.domain.work_identity import (
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkTitleTransformationFamily,
    work_query_ladder,
)
from tests.support.audiobook_providers import (
    asin_plan,
    audio_target,
    payload,
    target_title_claim,
    work_plan,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    audio_local_claims,
    manual_audiobook_observation,
)
from tests.support.metadata_report_doubles import single_track_audio_facts


def _store(value: object, status_code: int = 200, **headers: str) -> httpx.Response:
    """Answer as the store does: `text/javascript`, never `application/json`."""
    return httpx.Response(
        status_code,
        headers={"content-type": "text/javascript; charset=utf-8", **headers},
        json=value,
    )


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    delays: list[float] | None = None,
    pacer: ITunesSearchPacer | None = None,
) -> ITunesSearchMetadataProvider:
    return ITunesSearchMetadataProvider(
        "itunes",
        httpx.Client(transport=httpx.MockTransport(handler)),
        262_144,
        5.0,
        pacer=pacer,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=(lambda value: None) if delays is None else delays.append,
        jitter=lambda: 0.0,
    )


def _recorder(
    search: httpx.Response | None = None,
) -> tuple[list[httpx.URL], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return search or _store(payload("itunes_search"))

    return seen, handle


def _claims(candidate: Any, kind: ClaimKind) -> tuple[Any, ...]:
    return tuple(claim for claim in candidate.claims if claim.kind is kind)


def _evidence(candidate: Any, kind: ProviderEvidenceKind) -> tuple[Any, ...]:
    return tuple(value for value in candidate.evidence if value.kind is kind)


def test_a_work_search_states_a_title_and_an_author() -> None:
    seen, handle = _recorder()
    observation = _provider(handle).observe(work_plan()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert ProviderQueryKind.WORK is observation.query_kind
    assert "itunes_search" == observation.authority
    assert 1 == len(seen)
    assert str(seen[0]).startswith(ITUNES_SEARCH_URL)
    assert "media=audiobook" in str(seen[0])
    assert "limit=6" in str(seen[0])
    candidate = observation.candidates[0]
    assert candidate.identity.startswith("itunes-collection-")
    assert ("Ember Tide Protocol",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.TITLE)
    )
    assert ("Corin Halloway",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.CREATOR)
    )


def test_latin_fold_reaches_itunes_only_on_the_final_bounded_search() -> None:
    seen: list[httpx.URL] = []
    page = copy.deepcopy(payload("itunes_search"))
    result = page["results"][1]
    result["collectionName"] = "Cafe: Notes"
    result["artistName"] = "Jose"
    page = {"resultCount": 1, "results": [result]}

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return _store({"resultCount": 0, "results": []} if len(seen) < 3 else page)

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
        "Caf\N{LATIN SMALL LETTER E WITH ACUTE}: Notes Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        "Caf\N{LATIN SMALL LETTER E WITH ACUTE} Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        "Cafe Jose",
    ] == [url.params["term"] for url in seen]
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


def test_the_collection_identifier_is_the_candidate_identity() -> None:
    """The store's own identifier is what the retained candidate is named by.

    The fixture page holds three collections: the exact work, the same work
    under an edition qualifier the store puts in the title, and a third-party
    summary by another artist.  Work qualification admits the exact one, and
    the identity it survives under is the store's own collection identifier.
    """
    _seen, handle = _recorder()
    observation = _provider(handle).observe(work_plan()).observation
    results = payload("itunes_search")["results"]
    exact = next(
        result
        for result in results
        if result["collectionName"] == "Ember Tide Protocol"
        and result["artistName"] == "Corin Halloway"
    )

    assert 3 == len(results)
    assert [f"itunes-collection-{exact['collectionId']}"] == [
        candidate.identity for candidate in observation.candidates
    ]


def test_an_itunes_candidate_can_never_carry_a_duration_or_a_narrator() -> None:
    """The structural blindness the corroboration gate depends on.

    An admission widened by the runtime band needs a `DURATION` claim to
    compare.  The store states none, so no candidate this adapter builds can
    ever reach that gate -- which is what keeps an iTunes-discovered identity
    from being promoted on its own.
    """
    _seen, handle = _recorder()
    observation = _provider(handle).observe(work_plan()).observation

    assert observation.candidates
    for candidate in observation.candidates:
        assert () == _claims(candidate, ClaimKind.DURATION)
        assert () == _claims(candidate, ClaimKind.CONTRIBUTOR)
        assert () == _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)
        assert () == _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)
        assert () == _claims(candidate, ClaimKind.ASIN)


def test_a_plain_description_is_read_with_its_character_references_decoded() -> None:
    """Both sides of a description comparison are decoded exactly once (#1424)."""
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        result["description"] = "Fish &amp; chips&nbsp;for &quot;tea&quot;."
    _seen, handle = _recorder(_store(document))
    candidate = _provider(handle).observe(work_plan()).observation.candidates[0]

    assert ['Fish & chips for "tea".'] == [
        claim.value for claim in _claims(candidate, ClaimKind.DESCRIPTION)
    ]
    assert () == _evidence(candidate, ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION)


@pytest.mark.parametrize(
    ("stated", "state", "value"),
    (
        ("&lt;img src=x onerror=alert(1)&gt;Hello there.", FieldState.MALFORMED, None),
        (
            "&lt;script&gt;alert(1)&lt;/script&gt;Hello there.",
            FieldState.MALFORMED,
            None,
        ),
        ("&lt;b&gt;Bold&lt;/b&gt; claim.", FieldState.VALID, "Bold claim."),
        (
            "&lt;a href=&quot;javascript:alert(1)&quot;&gt;Click&lt;/a&gt; here.",
            FieldState.VALID,
            "Click here.",
        ),
        # Text that would decode again is never read twice (#1424).
        ("Fish &amp;amp; chips for the qualification.", FieldState.MALFORMED, None),
    ),
    ids=("handler", "script", "bold", "link", "double"),
)
def test_plain_text_carrying_markup_once_decoded_is_read_as_markup(
    stated: str, state: FieldState, value: str | None
) -> None:
    # Decoding never hands a claim a tag the renderer would refuse (#1424).
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        result["description"] = stated
    _seen, handle = _recorder(_store(document))
    candidate = _provider(handle).observe(work_plan()).observation.candidates[0]

    assert [(state, value)] == [
        (claim.state, claim.value)
        for claim in _claims(candidate, ClaimKind.DESCRIPTION)
    ]
    assert (state is FieldState.VALID) == bool(
        _evidence(candidate, ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION)
    )


def test_the_stores_html_description_becomes_visible_plain_text() -> None:
    _seen, handle = _recorder()
    candidate = next(
        candidate
        for candidate in _provider(handle).observe(work_plan()).observation.candidates
        if any(
            claim.kind is ClaimKind.TITLE and claim.value == "Ember Tide Protocol"
            for claim in candidate.claims
        )
    )
    description = _claims(candidate, ClaimKind.DESCRIPTION)[0]
    stated = next(
        result["description"]
        for result in payload("itunes_search")["results"]
        if candidate.identity.endswith(str(result["collectionId"]))
    )

    assert "<" in stated
    assert description.value is not None
    assert "<" not in description.value and ">" not in description.value
    assert stated.count("<br />") and " • " in description.value
    assert ("html-visible-text-v1",) == tuple(
        value.value
        for value in _evidence(
            candidate, ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION
        )
    )
    local = audio_local_claims()
    report = admitted_audiobook_report(
        local,
        additional_providers=(
            manual_audiobook_observation("itunes", "itunes_search", candidate),
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


def test_a_plan_with_no_work_lane_is_insufficient_input() -> None:
    """The store searches and resolves nothing, exactly as Audnexus states."""
    seen, handle = _recorder()
    observation = _provider(handle).observe(asin_plan()).observation

    assert ProviderStatus.INSUFFICIENT_INPUT is observation.status
    assert [] == seen


def test_the_javascript_media_type_is_admitted_and_nothing_adjacent_is() -> None:
    _seen, handle = _recorder()
    assert (
        ProviderStatus.CANDIDATES
        is _provider(handle).observe(work_plan()).observation.status
    )

    _seen, html = _recorder(
        httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b'{"resultCount": 0, "results": []}',
        )
    )
    assert (
        ProviderStatus.MALFORMED
        is _provider(html).observe(work_plan()).observation.status
    )


@pytest.mark.parametrize(
    ("status_code", "expected"),
    (
        (404, ProviderStatus.NO_MATCH),
        (401, ProviderStatus.AUTHENTICATION),
        (403, ProviderStatus.AUTHORIZATION),
        (500, ProviderStatus.UNAVAILABLE),
    ),
    ids=("not_found", "unauthenticated", "forbidden", "unclassified"),
)
def test_the_stores_status_vocabulary_reaches_one_terminal_outcome(
    status_code: int, expected: ProviderStatus
) -> None:
    _seen, handle = _recorder(_store({"errorMessage": "no"}, status_code))
    assert expected is _provider(handle).observe(work_plan()).observation.status


def test_a_throttled_search_retries_on_the_stated_delay() -> None:
    delays: list[float] = []
    answers = [
        _store({"errorMessage": "slow down"}, 429, **{"retry-after": "4"}),
        _store(payload("itunes_search")),
    ]
    observation = (
        _provider(lambda _: answers.pop(0), delays=delays)
        .observe(work_plan())
        .observation
    )

    assert ProviderStatus.CANDIDATES is observation.status
    assert [4.0] == delays


def test_too_many_results_exceed_the_candidate_limit() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    one = document["results"][0]
    document["results"] = [
        {**one, "collectionId": one["collectionId"] + offset} for offset in range(6)
    ]
    _seen, handle = _recorder(_store(document))

    assert (
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
        is _provider(handle).observe(work_plan()).observation.status
    )


def test_an_overshot_store_page_exceeds_the_candidate_limit() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    one = document["results"][0]
    document["results"] = [
        {**one, "collectionId": one["collectionId"] + offset} for offset in range(8)
    ]
    _seen, handle = _recorder(_store(document))

    assert (
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
        is _provider(handle).observe(work_plan()).observation.status
    )


def test_a_result_without_a_collection_identifier_is_malformed() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    del document["results"][0]["collectionId"]
    _seen, handle = _recorder(_store(document))

    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(work_plan()).observation.status
    )


def test_an_empty_result_set_is_a_no_match() -> None:
    _seen, handle = _recorder(_store({"resultCount": 0, "results": []}))
    assert (
        ProviderStatus.NO_MATCH
        is _provider(handle).observe(work_plan()).observation.status
    )


def test_an_unstated_member_is_counted_as_a_schema_rejection() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    document["results"][0]["unexpectedMember"] = "value"
    _seen, handle = _recorder(_store(document))
    observation = _provider(handle).observe(work_plan()).observation

    assert 1 == sum(value.count for value in observation.diagnostics)


def test_one_adapter_observes_once() -> None:
    _seen, handle = _recorder()
    provider = _provider(handle)

    assert (
        ProviderStatus.CANDIDATES is (provider.observe(work_plan())).observation.status
    )
    assert (
        ProviderStatus.UNAVAILABLE is (provider.observe(work_plan())).observation.status
    )


def test_every_request_reaches_the_one_admitted_origin() -> None:
    seen, handle = _recorder()
    _provider(handle).observe(work_plan())

    assert seen
    for url in seen:
        assert str(url).startswith(ITUNES_SEARCH_ORIGIN + "/")


def test_a_search_states_one_related_edition_cover_for_each_retained_result() -> None:
    _seen, handle = _recorder()
    provider = _provider(handle)

    provider_result = provider.observe(work_plan())
    observation = (provider_result).observation

    identities = {candidate.identity for candidate in observation.candidates}
    assert identities
    assert identities == {
        descriptor.provenance.edition_identity
        for descriptor in provider_result.cover_descriptors
        if descriptor.provenance is not None
    }
    for descriptor in provider_result.cover_descriptors:
        assert CoverSourceKind.ITUNES_SEARCH is descriptor.kind
        assert descriptor.url is not None
        assert "is1-ssl.mzstatic.com" == httpx.URL(descriptor.url).host
        assert descriptor.terminal_state is None
        assert descriptor.allow_redirects is False
        assert descriptor.provenance is not None
        assert CoverDesignation.RELATED_EDITION is descriptor.provenance.designation


def _repeated_collection_page(*, drift: bool) -> dict[str, Any]:
    """Restate one fixture result, optionally with a claim that disagrees."""
    document = copy.deepcopy(payload("itunes_search"))
    repeated = copy.deepcopy(document["results"][1])
    if drift:
        repeated["description"] = "The store states another summary for this one."
    document["results"].append(repeated)
    document["resultCount"] = len(document["results"])
    return document


def test_a_repeated_collection_with_disagreeing_claims_states_an_identity_failure() -> (
    None
):
    _seen, handle = _recorder(_store(_repeated_collection_page(drift=True)))
    provider = _provider(handle)

    result = observe_provider("itunes", "itunes_search", provider, work_plan())

    observation = result.observation
    assert ProviderStatus.MALFORMED is observation.status
    assert () == observation.candidates
    assert () == result.cover_descriptors
    assert [(FailureCategory.IDENTITY, FailureStage.IDENTITY)] == [
        (value.category, value.stage) for value in observation.diagnostics
    ]
    assert [(FailureCategory.IDENTITY, FailureStage.IDENTITY)] == [
        (value.category, value.stage)
        for lane in observation.lanes
        for value in lane.diagnostics
    ]


def test_a_byte_identical_repeated_collection_stays_a_malformed_response() -> None:
    _seen, handle = _recorder(_store(_repeated_collection_page(drift=False)))
    provider = _provider(handle)

    observation = observe_provider(
        "itunes", "itunes_search", provider, work_plan()
    ).observation

    assert ProviderStatus.MALFORMED is observation.status
    assert () == observation.candidates
    assert [FailureCategory.MALFORMED_RESPONSE] == [
        value.category for value in observation.diagnostics
    ]
    assert [] == [value for lane in observation.lanes for value in lane.diagnostics]


@pytest.mark.parametrize(
    "url",
    (
        "https://is6-ssl.mzstatic.com/image/thumb/a/100x100bb.jpg",
        "https://is9-ssl.mzstatic.com/image/thumb/a/100x100bb.jpg",
        "https://is1.mzstatic.com/image/thumb/a/100x100bb.jpg",
        "https://mzstatic.com.example.invalid/image/thumb/a/100x100bb.jpg",
        "http://is1-ssl.mzstatic.com/image/thumb/a/100x100bb.jpg",
        "https://is1-ssl.mzstatic.com:8443/image/thumb/a/100x100bb.jpg",
    ),
)
def test_an_artwork_url_outside_the_closed_host_set_is_refused_with_a_typed_state(
    url: str,
) -> None:
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        result["artworkUrl100"] = url
    _seen, handle = _recorder(_store(document))
    provider = _provider(handle)

    provider_result = provider.observe(work_plan())
    observation = (provider_result).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert provider_result.cover_descriptors
    for descriptor in provider_result.cover_descriptors:
        assert descriptor.url is None
        assert CoverSourceState.UNSAFE_REMOTE is descriptor.terminal_state


def test_a_result_that_states_no_artwork_states_an_absent_cover_source() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        del result["artworkUrl100"]
    _seen, handle = _recorder(_store(document))
    provider = _provider(handle)

    provider_result = provider.observe(work_plan())

    assert provider_result.cover_descriptors
    for descriptor in provider_result.cover_descriptors:
        assert descriptor.url is None
        assert descriptor.terminal_state is None


def test_an_unreadable_artwork_declaration_is_typed_not_dropped() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        result["artworkUrl100"] = ""
    _seen, handle = _recorder(_store(document))
    provider = _provider(handle)

    provider_result = provider.observe(work_plan())

    assert provider_result.cover_descriptors
    for descriptor in provider_result.cover_descriptors:
        assert descriptor.url is None
        assert CoverSourceState.INVALID_DECLARATION is descriptor.terminal_state


def test_an_asin_only_plan_states_no_cover_at_all() -> None:
    _seen, handle = _recorder()
    provider = _provider(handle)

    provider_result = provider.observe(asin_plan())

    assert () == provider_result.cover_descriptors


def _descriptor_urls(document: object) -> tuple[str | None, ...]:
    _seen, handle = _recorder(_store(document))
    provider = _provider(handle)
    provider_result = provider.observe(work_plan())
    assert provider_result.cover_descriptors
    return tuple(value.url for value in provider_result.cover_descriptors)


def test_the_stated_thumbnail_segment_is_constructed_into_a_legible_rendition() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        result["artworkUrl100"] = (
            "https://is1-ssl.mzstatic.com/image/thumb/Music221/v4/a/rm.jpg/100x100bb.jpg"
        )

    assert (
        "https://is1-ssl.mzstatic.com/image/thumb/Music221/v4/a/rm.jpg/600x600bb.jpg",
    ) == tuple(set(_descriptor_urls(document)))


def test_an_artwork_url_that_is_not_the_stated_segment_is_left_unmodified() -> None:
    stated = (
        "https://is1-ssl.mzstatic.com/image/thumb/Music221/v4/a/rm.jpg/170x170bb.png"
    )
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        result["artworkUrl100"] = stated

    assert (stated,) == tuple(set(_descriptor_urls(document)))


@pytest.mark.parametrize("shard", (1, 2, 3, 4, 5))
def test_every_admitted_artwork_shard_is_acquired(shard: int) -> None:
    """The store balances one result set across its shards, not one host."""
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        result["artworkUrl100"] = (
            f"https://is{shard}-ssl.mzstatic.com/image/thumb/a/100x100bb.jpg"
        )

    assert (
        f"https://is{shard}-ssl.mzstatic.com/image/thumb/a/600x600bb.jpg",
    ) == tuple(set(_descriptor_urls(document)))


@pytest.mark.parametrize("key", ["collectionName"])
@pytest.mark.parametrize("value", ["Example\u200b Book", "Café — 日本語!"])
def test_http_title_isolation_preserves_candidate_evidence(
    key: str, value: str
) -> None:
    from mechanical_librarian.domain.metadata_report import FieldState

    document = payload("itunes_search")
    document["results"][0][key] = value
    _seen, handle = _recorder(search=_store(document))
    observation = _provider(handle).observe(work_plan()).observation
    assert observation.status is ProviderStatus.CANDIDATES
    kind = ClaimKind.SUBTITLE if key == "subtitle" else ClaimKind.TITLE
    claims = [
        claim for candidate in observation.candidates for claim in candidate.claims
    ]
    from mechanical_librarian.adapters.providers.itunes_search_metadata import (
        _candidate,
    )

    physical = _candidate(
        "itunes",
        document["results"][0],
        work_plan().local_claims,
        schema_rejections=lambda _: None,
    )
    assert all(
        candidate.identity != physical.identity for candidate in observation.candidates
    )
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
    "count,malformed", ((5, False), (6, False), (7, False), (7, True))
)
def test_complete_itunes_overflow_is_terminal_before_projection(
    count: int, malformed: bool
) -> None:
    matching = {
        "collectionId": 100,
        "collectionName": "Synthetic Work",
        "artistName": "Example Writer",
        "description": "An original synthetic account.",
    }
    rows = [matching]
    for index in range(1, count):
        row = copy.deepcopy(matching)
        row["collectionId"] = 100 + index
        row["collectionName"] = f"Unrelated Synthetic Work {index}"
        row["artistName"] = "Another Writer"
        rows.append(row)
    if malformed:
        # A JSON-compatible malformed member must still be rejected by the
        # complete raw-result bound before any member projection.
        rows[-1] = {"collectionId": None}
    seen, handle = _recorder(_store({"resultCount": count, "results": rows}))
    observation = (
        _provider(handle)
        .observe(work_plan(title="Synthetic Work", authors=("Example Writer",)))
        .observation
    )
    assert len(seen) == 1
    assert seen[0].params["limit"] == "6"
    assert observation.status is (
        ProviderStatus.CANDIDATES
        if count == 5
        else ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
    )
    assert len(observation.candidates) == (1 if count == 5 else 0)
    assert observation.request_accounting.retry_dispatches == 0


def test_itunes_narrowing_flag_does_not_change_wire_term() -> None:
    from mechanical_librarian.adapters.providers.itunes_search_metadata import (
        _search_term,
    )
    from mechanical_librarian.domain.work_identity import (
        narrowing_work_query,
        work_query_ladder,
    )

    query = work_query_ladder("Synthetic Work: An Account", ("Example Writer",))[0]
    assert _search_term(narrowing_work_query(query)) == _search_term(query)


# The #1245 ladder: on an audiobook plan a rung whose results work-identity
# admission refuses no longer ends the walk.
_SUBTITLED = "Ember Tide Protocol: A Novel"


def _audiobook_work_plan(title: str = _SUBTITLED) -> MetadataDiscoveryPlan:
    return audiobook_discovery_plan(
        audio_target(title, ("Corin Halloway",)),
        (target_title_claim(title),),
        audio=single_track_audio_facts(),
    )


def _page(*results: dict[str, Any]) -> dict[str, Any]:
    return {"resultCount": len(results), "results": list(results)}


def _refused() -> dict[str, Any]:
    """The fixture's third-party summary, which is not the work."""
    return copy.deepcopy(payload("itunes_search")["results"][2])


def _work(title: str) -> dict[str, Any]:
    result = copy.deepcopy(payload("itunes_search")["results"][1])
    result["collectionName"] = title
    return result


def _pages(
    *pages: dict[str, Any],
) -> tuple[list[httpx.URL], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return _store(pages[len(seen) - 1])

    return seen, handle


def _categories(observation: Any) -> tuple[tuple[FailureCategory, int], ...]:
    return tuple((value.category, value.count) for value in observation.diagnostics)


def test_a_refused_store_rung_continues_to_the_rung_that_admits() -> None:
    seen, handle = _pages(_page(_refused()), _page(_work(_SUBTITLED)))
    result = observe_provider(
        "itunes", "itunes_search", _provider(handle), _audiobook_work_plan()
    )
    observation = result.observation

    assert 2 == len(seen)
    assert ProviderStatus.CANDIDATES is observation.status
    lane = observation.lanes[0]
    assert lane.dispatch_lineage is not None
    assert lane.dispatch_lineage.rung is WorkDispatchRung.SUBTITLE_STRIPPED
    assert () == lane.exhausted_dispatch and () == lane.diagnostics
    assert FailureCategory.WORK_IDENTITY_REFUSED not in {
        category for category, _ in _categories(observation)
    }


def test_a_store_ladder_refused_on_every_rung_records_each_dispatch() -> None:
    ladder = tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(_SUBTITLED, ("Corin Halloway",))
    )
    seen, handle = _pages(*(_page(_refused()) for _ in ladder))
    result = observe_provider(
        "itunes", "itunes_search", _provider(handle), _audiobook_work_plan()
    )
    observation = result.observation

    assert len(ladder) == len(seen) <= 12
    assert ProviderStatus.NO_MATCH is observation.status
    lane = observation.lanes[0]
    assert ladder == lane.exhausted_dispatch
    assert work_ladder_diagnostics(0, len(ladder)) == lane.diagnostics
    assert ((FailureCategory.WORK_IDENTITY_REFUSED, len(ladder)),) == _categories(
        observation
    )
    assert () == result.cover_descriptors


def test_an_ebook_plan_still_stops_at_the_first_store_rung_with_results() -> None:
    seen, handle = _pages(_page(_refused()), _page(_work(_SUBTITLED)))
    observation = observe_provider(
        "itunes", "itunes_search", _provider(handle), work_plan(title=_SUBTITLED)
    ).observation

    assert 1 == len(seen)
    assert ProviderStatus.NO_MATCH is observation.status
    # The one rung it sent is recorded, so the miss shows what was searched
    # (#1499).
    assert (
        WorkDispatchLineage.from_query(
            work_query_ladder(_SUBTITLED, ("Corin Halloway",))[0]
        ),
    ) == observation.lanes[0].exhausted_dispatch
    # Its products were refused, not missing, and the lane now says so.
    assert ((FailureCategory.WORK_IDENTITY_REFUSED, 1),) == _categories(observation)


def _alternative_plan(*, collapsed: bool = False) -> MetadataDiscoveryPlan:
    target = audio_target("Asserted Title", ("Corin Halloway",))
    if collapsed:
        series = d.TargetField(
            TargetDestination.SERIES,
            d.TargetFieldState.VALUE,
            (d.TargetSeries("Asserted Title", "3"),),
        )
        target = replace(
            target,
            fields=tuple(
                series if field.destination is TargetDestination.SERIES else field
                for field in target.fields
            ),
        )
    return audiobook_discovery_plan(
        target,
        (target_title_claim("Asserted Title"),),
        audio=replace(
            single_track_audio_facts(),
            discovery_titles=(d.AudioDiscoveryTitle("path_leaf", "The Real Title"),),
        ),
    )


def test_the_store_never_sends_an_audiobook_alternative_title() -> None:
    """It states no running time, so an alternative lane could retain nothing."""
    plan = _alternative_plan()
    seen, handle = _recorder(_store({"resultCount": 0, "results": []}))
    observation = _provider(handle).observe(plan).observation

    assert ["Asserted Title Corin Halloway"] == [url.params["term"] for url in seen]
    assert [None] == [
        next(lane for lane in plan.lanes if lane.identity == value.identity).title_basis
        for value in observation.lanes
    ]
    assert ProviderStatus.NO_MATCH is observation.status


def test_a_series_collapsed_plan_sends_the_store_no_work_lane() -> None:
    seen, handle = _recorder()
    observation = _provider(handle).observe(_alternative_plan(collapsed=True))

    assert ProviderStatus.INSUFFICIENT_INPUT is observation.observation.status
    assert [] == seen


def test_an_ebook_titled_with_its_series_matches_the_store_work_without_it() -> None:
    """The bare-tier EPUB of #1499: the series-stripped rung's answer is admitted."""
    title = "Ember Tide Protocol (The Tide Cycle)"
    ladder = work_query_ladder(title, ("Corin Halloway",))
    pages = [_page() for _ in ladder]
    stripped = next(
        index
        for index, query in enumerate(ladder)
        if query.transformations
        == (WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,)
    )
    pages[stripped] = _page(_work("Ember Tide Protocol"))
    seen, handle = _pages(*pages)
    observation = observe_provider(
        "itunes", "itunes_search", _provider(handle), work_plan(title=title)
    ).observation

    (lane,) = observation.lanes
    assert stripped + 1 == len(seen)
    assert ProviderStatus.CANDIDATES is lane.status
    assert WorkDispatchLineage.from_query(ladder[stripped]) == lane.dispatch_lineage
    assert lane.transformation_lineage is not None
    assert "band_parenthesized_series_name" == lane.transformation_lineage.production
