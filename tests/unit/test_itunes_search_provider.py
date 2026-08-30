"""The iTunes Search adapter, proved against a captured audiobook search."""

from __future__ import annotations

import copy
from typing import Any, Callable

import httpx
import pytest

from mechanical_librarian.adapters.providers.itunes_search_metadata import (
    ITUNES_SEARCH_ORIGIN,
    ITUNES_SEARCH_URL,
    ITunesSearchMetadataProvider,
    ITunesSearchPacer,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ProviderEvidenceKind,
    ProviderQueryKind,
    ProviderStatus,
)
from tests.support.audiobook_providers import asin_plan, payload, work_plan


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
    observation = _provider(handle).observe(work_plan())

    assert ProviderStatus.CANDIDATES is observation.status
    assert ProviderQueryKind.WORK is observation.query_kind
    assert "itunes_search" == observation.authority
    assert 1 == len(seen)
    assert str(seen[0]).startswith(ITUNES_SEARCH_URL)
    assert "media=audiobook" in str(seen[0])
    assert "limit=6" in str(seen[0])
    candidate = observation.candidates[0]
    assert candidate.identity.startswith("itunes-collection-")
    assert ("Project Hail Mary",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.TITLE)
    )
    assert ("Andy Weir",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.CREATOR)
    )


def test_the_collection_identifier_is_the_candidate_identity() -> None:
    """The store's own identifier is what the retained candidate is named by.

    The captured page holds three collections: the exact work, the same work
    under an edition qualifier the store puts in the title, and a third-party
    summary by another artist.  Work qualification admits the exact one, and
    the identity it survives under is the store's own collection identifier.
    """
    _seen, handle = _recorder()
    observation = _provider(handle).observe(work_plan())
    results = payload("itunes_search")["results"]
    exact = next(
        result
        for result in results
        if result["collectionName"] == "Project Hail Mary"
        and result["artistName"] == "Andy Weir"
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
    observation = _provider(handle).observe(work_plan())

    assert observation.candidates
    for candidate in observation.candidates:
        assert () == _claims(candidate, ClaimKind.DURATION)
        assert () == _claims(candidate, ClaimKind.CONTRIBUTOR)
        assert () == _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)
        assert () == _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)
        assert () == _claims(candidate, ClaimKind.ASIN)


def test_the_stores_html_description_becomes_visible_plain_text() -> None:
    _seen, handle = _recorder()
    candidate = _provider(handle).observe(work_plan()).candidates[0]
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


def test_a_plan_with_no_work_lane_is_insufficient_input() -> None:
    """The store searches and resolves nothing, exactly as Audnexus states."""
    seen, handle = _recorder()
    observation = _provider(handle).observe(asin_plan())

    assert ProviderStatus.INSUFFICIENT_INPUT is observation.status
    assert [] == seen


def test_the_javascript_media_type_is_admitted_and_nothing_adjacent_is() -> None:
    _seen, handle = _recorder()
    assert ProviderStatus.CANDIDATES is _provider(handle).observe(work_plan()).status

    _seen, html = _recorder(
        httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b'{"resultCount": 0, "results": []}',
        )
    )
    assert ProviderStatus.MALFORMED is _provider(html).observe(work_plan()).status


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
    assert expected is _provider(handle).observe(work_plan()).status


def test_a_throttled_search_retries_on_the_stated_delay() -> None:
    delays: list[float] = []
    answers = [
        _store({"errorMessage": "slow down"}, 429, **{"retry-after": "4"}),
        _store(payload("itunes_search")),
    ]
    observation = _provider(lambda _: answers.pop(0), delays=delays).observe(
        work_plan()
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
        is _provider(handle).observe(work_plan()).status
    )


def test_a_result_without_a_collection_identifier_is_malformed() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    del document["results"][0]["collectionId"]
    _seen, handle = _recorder(_store(document))

    assert ProviderStatus.MALFORMED is _provider(handle).observe(work_plan()).status


def test_an_empty_result_set_is_a_no_match() -> None:
    _seen, handle = _recorder(_store({"resultCount": 0, "results": []}))
    assert ProviderStatus.NO_MATCH is _provider(handle).observe(work_plan()).status


def test_an_unstated_member_is_counted_as_a_schema_rejection() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    document["results"][0]["unexpectedMember"] = "value"
    _seen, handle = _recorder(_store(document))
    observation = _provider(handle).observe(work_plan())

    assert 1 == sum(value.count for value in observation.diagnostics)


def test_one_adapter_observes_once() -> None:
    _seen, handle = _recorder()
    provider = _provider(handle)

    assert ProviderStatus.CANDIDATES is provider.observe(work_plan()).status
    assert ProviderStatus.UNAVAILABLE is provider.observe(work_plan()).status


def test_every_request_reaches_the_one_admitted_origin() -> None:
    seen, handle = _recorder()
    _provider(handle).observe(work_plan())

    assert seen
    for url in seen:
        assert str(url).startswith(ITUNES_SEARCH_ORIGIN + "/")


def test_a_search_states_one_related_edition_cover_for_each_retained_result() -> None:
    _seen, handle = _recorder()
    provider = _provider(handle)

    observation = provider.observe(work_plan())

    identities = {candidate.identity for candidate in observation.candidates}
    assert identities
    assert identities == {
        descriptor.provenance.edition_identity
        for descriptor in provider.cover_descriptors
        if descriptor.provenance is not None
    }
    for descriptor in provider.cover_descriptors:
        assert CoverSourceKind.ITUNES_SEARCH is descriptor.kind
        assert descriptor.url is not None
        assert "is1-ssl.mzstatic.com" == httpx.URL(descriptor.url).host
        assert descriptor.terminal_state is None
        assert descriptor.allow_redirects is False
        assert descriptor.provenance is not None
        assert CoverDesignation.RELATED_EDITION is descriptor.provenance.designation


@pytest.mark.parametrize(
    "url",
    (
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

    observation = provider.observe(work_plan())

    assert ProviderStatus.CANDIDATES is observation.status
    assert provider.cover_descriptors
    for descriptor in provider.cover_descriptors:
        assert descriptor.url is None
        assert CoverSourceState.UNSAFE_REMOTE is descriptor.terminal_state


def test_a_result_that_states_no_artwork_states_an_absent_cover_source() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        del result["artworkUrl100"]
    _seen, handle = _recorder(_store(document))
    provider = _provider(handle)

    provider.observe(work_plan())

    assert provider.cover_descriptors
    for descriptor in provider.cover_descriptors:
        assert descriptor.url is None
        assert descriptor.terminal_state is None


def test_an_unreadable_artwork_declaration_is_typed_not_dropped() -> None:
    document = copy.deepcopy(payload("itunes_search"))
    for result in document["results"]:
        result["artworkUrl100"] = ""
    _seen, handle = _recorder(_store(document))
    provider = _provider(handle)

    provider.observe(work_plan())

    assert provider.cover_descriptors
    for descriptor in provider.cover_descriptors:
        assert descriptor.url is None
        assert CoverSourceState.INVALID_DECLARATION is descriptor.terminal_state


def test_an_asin_only_plan_states_no_cover_at_all() -> None:
    _seen, handle = _recorder()
    provider = _provider(handle)

    provider.observe(asin_plan())

    assert () == provider.cover_descriptors
