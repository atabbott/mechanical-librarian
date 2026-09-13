"""The Audnexus adapter, proved against captured book and chapter payloads."""

from __future__ import annotations

import copy
from typing import Any, Callable

import httpx
import pytest

from mechanical_librarian.adapters.providers.audnexus_metadata import (
    AUDNEXUS_BOOKS_URL,
    AUDNEXUS_ORIGIN,
    AudnexusMetadataProvider,
    AudnexusPacer,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_PROVIDER_CHAPTER_DURATIONS,
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
from tests.support.audiobook_providers import (
    asin_plan,
    local_claims,
    payload,
    work_plan,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    manual_audiobook_observation,
)

_ASIN = "B08G9PRS1K"
_BOOK = f"{AUDNEXUS_BOOKS_URL}/{_ASIN}"
_CHAPTERS = f"{_BOOK}/chapters"


def _json(value: object, status_code: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"content-type": "application/json; charset=utf-8", **headers},
        json=value,
    )


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    marketplaces: tuple[str, ...] = ("us",),
    delays: list[float] | None = None,
) -> AudnexusMetadataProvider:
    return AudnexusMetadataProvider(
        "audnexus",
        httpx.Client(transport=httpx.MockTransport(handler)),
        262_144,
        5.0,
        marketplaces=marketplaces,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=(lambda value: None) if delays is None else delays.append,
        jitter=lambda: 0.0,
    )


def _recorder(
    *,
    book: httpx.Response | None = None,
    chapters: httpx.Response | None = None,
    by_region: dict[str, httpx.Response] | None = None,
) -> tuple[list[httpx.URL], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if str(request.url).startswith(_CHAPTERS):
            return chapters or _json(payload("audnexus_chapters"))
        region = request.url.params.get("region", "us")
        if by_region is not None:
            return by_region.get(
                region, _json(payload("audnexus_region_unavailable"), 404)
            )
        return book or _json(payload("audnexus_book"))

    return seen, handle


def _claims(candidate: Any, kind: ClaimKind) -> tuple[Any, ...]:
    return tuple(claim for claim in candidate.claims if claim.kind is kind)


def _evidence(candidate: Any, kind: ProviderEvidenceKind) -> tuple[Any, ...]:
    return tuple(value for value in candidate.evidence if value.kind is kind)


def test_a_book_lookup_maps_the_captured_record_onto_report_claims() -> None:
    seen, handle = _recorder()
    observation = _provider(handle).observe(asin_plan()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert ProviderQueryKind.ASIN is observation.query_kind
    assert "audnexus" == observation.authority
    assert [_BOOK, _CHAPTERS] == [str(url) for url in seen]
    candidate = observation.candidates[0]
    assert "audnexus-book-B08G9PRS1K" == candidate.identity
    assert (_ASIN,) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )
    assert ("Project Hail Mary",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.TITLE)
    )
    assert ("Andy Weir",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.CREATOR)
    )
    narrators = _claims(candidate, ClaimKind.CONTRIBUTOR)
    assert ("Ray Porter",) == tuple(claim.value for claim in narrators)
    assert {"narrator"} == {claim.role for claim in narrators}
    assert ("english",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.LANGUAGE)
    )
    assert (
        payload("audnexus_book")["description"]
        == _claims(candidate, ClaimKind.DESCRIPTION)[0].value
    )
    assert ("us",) == tuple(
        value.value
        for value in _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)
    )


def test_typed_audio_fields_and_source_associations_survive_the_adapter() -> None:
    book = {
        "asin": _ASIN,
        "title": "Synthetic Journey",
        "authors": [{"name": "Example Author"}],
        "narrators": [{"name": "Example Reader"}],
        "publisherName": "Example Audio",
        "releaseDate": "2025-02-03T00:00:00.000Z",
        "isbn": "9780306406157",
        "language": "english",
        "seriesPrimary": {"name": "Example Cycle", "position": "2"},
        "seriesSecondary": {"name": "Example Collection", "position": "5"},
        "genres": [
            {"type": "genre", "name": "Fantasy"},
            {"type": "tag", "name": "Adventure"},
            {"type": "genre", "name": 42},
        ],
    }
    _seen, handle = _recorder(book=_json(book))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    assert _claims(candidate, ClaimKind.PUBLISHER)[0].value == "Example Audio"
    date = _claims(candidate, ClaimKind.PUBLICATION_DATE)[0]
    assert (date.value, date.date_event.value, date.date_precision.value) == (
        "2025-02-03",
        "manifestation_publication",
        "day",
    )
    assert _claims(candidate, ClaimKind.ISBN)[0].applicability == "unknown"
    assert [claim.value for claim in _claims(candidate, ClaimKind.SERIES)] == [
        "Example Cycle",
        "Example Collection",
    ]
    assert [claim.value for claim in _claims(candidate, ClaimKind.SERIES_SEQUENCE)] == [
        "2",
        "5",
    ]
    assert [
        (claim.value, claim.state) for claim in _claims(candidate, ClaimKind.GENRE)
    ] == [("Fantasy", FieldState.VALID), (None, FieldState.MALFORMED)]
    assert [claim.value for claim in _claims(candidate, ClaimKind.TAG)] == ["Adventure"]


@pytest.mark.parametrize("taxonomy_type", [[], {}, "unknown"])
def test_invalid_taxonomy_type_keeps_the_book_and_valid_siblings(
    taxonomy_type: object,
) -> None:
    book = {
        "asin": _ASIN,
        "title": "Synthetic Journey",
        "authors": [{"name": "Example Author"}],
        "genres": [
            {"type": "genre", "name": "Fantasy"},
            {"type": taxonomy_type, "name": "Rejected member"},
            {"type": "tag", "name": "Adventure"},
        ],
    }
    seen, handle = _recorder(book=_json(book))
    observation = _provider(handle).observe(asin_plan()).observation
    assert observation.status is ProviderStatus.CANDIDATES
    assert len(observation.candidates) == 1
    assert [str(url) for url in seen] == [_BOOK, _CHAPTERS]
    candidate = observation.candidates[0]
    assert [claim.value for claim in _claims(candidate, ClaimKind.TITLE)] == [
        "Synthetic Journey"
    ]
    assert [claim.value for claim in _claims(candidate, ClaimKind.CREATOR)] == [
        "Example Author"
    ]
    assert [claim.value for claim in _claims(candidate, ClaimKind.GENRE)] == ["Fantasy"]
    assert [claim.value for claim in _claims(candidate, ClaimKind.TAG)] == ["Adventure"]
    assert [
        (item.value, item.taxonomy_type)
        for item in _evidence(candidate, ProviderEvidenceKind.TAXONOMY)
    ] == [("Adventure", "tag"), ("Fantasy", "genre")]
    assert len(observation.diagnostics) == 1
    assert observation.diagnostics[0].count == 1


def test_partial_cast_and_malformed_release_remain_explicit_invalid_claims() -> None:
    book = {
        "asin": _ASIN,
        "narrators": [{"name": "Example Reader"}, {"name": 42}],
        "releaseDate": "2025-99-99",
    }
    _seen, handle = _recorder(book=_json(book))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    assert [claim.state for claim in _claims(candidate, ClaimKind.CONTRIBUTOR)] == [
        FieldState.VALID,
        FieldState.MALFORMED,
    ]
    assert (
        _claims(candidate, ClaimKind.PUBLICATION_DATE)[0].state is FieldState.MALFORMED
    )


def test_the_chapter_document_becomes_one_bounded_whole_second_vector() -> None:
    _seen, handle = _recorder()
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    structure = _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)[0]
    document = payload("audnexus_chapters")

    assert str(structure.chapters.runtime_seconds) == structure.value
    assert document["runtimeLengthSec"] == structure.chapters.runtime_seconds
    assert len(document["chapters"]) == structure.chapters.chapter_count
    assert not structure.chapters.durations_truncated
    assert (
        tuple(chapter["lengthMs"] // 1000 for chapter in document["chapters"])
        == structure.chapters.durations_seconds
    )
    assert (11, 5, 2203) == structure.chapters.durations_seconds[:3]


def test_a_stated_runtime_agrees_with_the_container_inside_the_band() -> None:
    _seen, handle = _recorder()
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    duration = _claims(candidate, ClaimKind.DURATION)[0]
    local = next(claim for claim in local_claims() if claim.kind is ClaimKind.DURATION)
    link = next(
        link for link in duration.links if link.local_reference == local.reference
    )

    assert "58200.000" == duration.value
    assert Comparison.AGREEMENT is link.outcome
    assert ComparisonMethod.DURATION_BAND_AGREEMENT is link.method


def test_a_region_unavailable_error_advances_the_walk_rather_than_ending_it() -> None:
    seen, handle = _recorder(by_region={"de": _json(payload("audnexus_book"))})
    observation = (
        _provider(handle, marketplaces=("us", "uk", "de"))
        .observe(asin_plan())
        .observation
    )

    assert ProviderStatus.CANDIDATES is observation.status
    assert ["us", "uk", "de", "de"] == [url.params.get("region", "us") for url in seen]
    # The provider's own default region is asked about without a parameter.
    assert "region" not in seen[0].params
    assert ("de",) == tuple(
        value.value
        for value in _evidence(
            observation.candidates[0], ProviderEvidenceKind.ASIN_MARKETPLACE
        )
    )


def test_an_asin_unavailable_in_every_region_is_a_no_match() -> None:
    seen, handle = _recorder(by_region={})
    observation = (
        _provider(handle, marketplaces=("us", "uk")).observe(asin_plan()).observation
    )

    assert ProviderStatus.NO_MATCH is observation.status
    assert 2 == len(seen)


def test_a_missing_chapter_document_removes_evidence_rather_than_the_candidate() -> (
    None
):
    _seen, handle = _recorder(chapters=_json({"message": "no"}, 404))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]

    assert () == _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)
    assert (_ASIN,) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )


def test_a_chapter_vector_above_its_cap_is_truncated_and_says_so() -> None:
    """Synthesised from the captured document: no real title has 1,025 chapters."""
    document = copy.deepcopy(payload("audnexus_chapters"))
    chapter = document["chapters"][0]
    document["chapters"] = [
        {**copy.deepcopy(chapter), "startOffsetMs": index * 1000}
        for index in range(MAX_PROVIDER_CHAPTER_DURATIONS + 1)
    ]
    _seen, handle = _recorder(chapters=_json(document))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    structure = _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)[0].chapters

    assert structure.durations_truncated
    assert MAX_PROVIDER_CHAPTER_DURATIONS == len(structure.durations_seconds)
    assert MAX_PROVIDER_CHAPTER_DURATIONS + 1 == structure.chapter_count


@pytest.mark.parametrize(
    "chapters",
    ({"chapters": "many"}, {"chapters": [{"lengthMs": "11264"}]}, {}),
    ids=("not_a_list", "textual_length", "absent"),
)
def test_a_malformed_chapter_document_is_no_structure_at_all(
    chapters: dict[str, object],
) -> None:
    _seen, handle = _recorder(chapters=_json(chapters))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]

    assert () == _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)


def test_a_work_lane_states_insufficient_input_and_sends_nothing() -> None:
    seen, handle = _recorder()
    observation = _provider(handle).observe(work_plan()).observation

    assert ProviderStatus.INSUFFICIENT_INPUT is observation.status
    assert [] == seen


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
    seen, handle = _recorder(book=_json({"message": "no"}, status_code))
    observation = (
        _provider(handle, marketplaces=("us", "uk")).observe(asin_plan()).observation
    )

    assert expected is observation.status
    assert 1 == len(seen)


def test_a_throttled_request_honours_retry_after_and_retries() -> None:
    delays: list[float] = []
    attempts: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url)
        if len(attempts) == 1:
            return _json({"message": "slow down"}, 429, **{"retry-after": "9"})
        if str(request.url).startswith(_CHAPTERS):
            return _json(payload("audnexus_chapters"))
        return _json(payload("audnexus_book"))

    observation = _provider(handle, delays=delays).observe(asin_plan()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert [9.0] == delays


@pytest.mark.parametrize(
    "asin",
    (None, "", "B08G9PRS1", "B08G9PRS1K!", 8),
    ids=("absent", "empty", "short", "punctuated", "numeric"),
)
def test_a_book_without_a_canonical_identity_fails_closed(asin: object) -> None:
    document = copy.deepcopy(payload("audnexus_book"))
    if asin is None:
        del document["asin"]
    else:
        document["asin"] = asin
    _seen, handle = _recorder(book=_json(document))

    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(asin_plan()).observation.status
    )


def test_an_html_two_hundred_is_malformed_rather_than_parsed() -> None:
    _seen, handle = _recorder(
        book=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html></html>",
        )
    )

    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(asin_plan()).observation.status
    )


def test_the_origin_and_pacer_are_fixed() -> None:
    assert "https://api.audnex.us" == AUDNEXUS_ORIGIN
    assert AUDNEXUS_ORIGIN + "/books" == AUDNEXUS_BOOKS_URL
    with pytest.raises(ValueError, match="marketplaces"):
        _provider(lambda request: _json({}), marketplaces=("us", "xx"))
    pacer = AudnexusPacer(1.0, clock=lambda: 0.0, sleep=lambda value: None)
    assert pacer.wait(None)


def test_one_adapter_serves_one_observation() -> None:
    _seen, handle = _recorder()
    provider = _provider(handle)

    assert (
        ProviderStatus.CANDIDATES is (provider.observe(asin_plan())).observation.status
    )
    assert (
        ProviderStatus.UNAVAILABLE is (provider.observe(asin_plan())).observation.status
    )


def test_an_html_summary_is_read_only_when_no_plain_description_is_stated() -> None:
    """The plain field is preferred; the markup one is a fallback, not a rewrite."""
    document = copy.deepcopy(payload("audnexus_book"))
    del document["description"]
    stated = document["summary"]
    _seen, handle = _recorder(book=_json(document))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    description = _claims(candidate, ClaimKind.DESCRIPTION)[0]

    assert "<" in stated
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
            manual_audiobook_observation("audnexus", "audnexus", candidate),
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
