"""The Audnexus adapter, proved against captured book and chapter payloads."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any, Callable

import httpx
import pytest

from mechanical_librarian.adapters.providers.audnexus_metadata import (
    AUDNEXUS_BOOKS_URL,
    AUDNEXUS_ORIGIN,
    AudnexusMetadataProvider,
    AudnexusPacer,
    _candidate,
)
from mechanical_librarian.adapters.providers.visible_text import visible_description
from mechanical_librarian.application import metadata_report as m
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain import metadata_report as d
from mechanical_librarian.domain import proposal_set as p
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
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
from mechanical_librarian.domain.rich_metadata_preview import (
    audiobook_tag_union_candidate,
    compile_rich_metadata_preview,
)
from tests.support.audiobook_providers import (
    asin_plan,
    asserted_target,
    audio_claim,
    audio_target,
    local_claims,
    payload,
    target_asin_claim,
    target_title_claim,
    work_plan,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    asserted_asin_report,
    manual_audiobook_observation,
)
from tests.support.audiobook_reports import (
    synthetic_audiobook_isbn_reason_report as _isbn_source_report,
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
        visible_description(payload("audnexus_book")["summary"])
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
    assert [
        (claim.value, claim.state) for claim in _claims(candidate, ClaimKind.TAG)
    ] == [(None, FieldState.MALFORMED), ("Adventure", FieldState.VALID)]
    assert [
        (item.value, item.taxonomy_type)
        for item in _evidence(candidate, ProviderEvidenceKind.TAXONOMY)
    ] == [("Adventure", "tag"), ("Fantasy", "genre")]
    assert len(observation.diagnostics) == 1
    assert observation.diagnostics[0].count == 1


def test_partial_cast_and_malformed_release_remain_explicit_invalid_claims() -> None:
    book = {
        "asin": _ASIN,
        "title": "Project Hail Mary",
        "authors": [{"name": "Andy Weir"}],
        "narrators": [{"name": "Example Reader"}, {"name": 42}],
        "releaseDate": "2025-99-99",
    }
    _seen, handle = _recorder(book=_json(book))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    assert [claim.state for claim in _claims(candidate, ClaimKind.CONTRIBUTOR)] == [
        FieldState.VALID,
        FieldState.MALFORMED,
    ]
    # The raw parser records invalid recording data; WORK shaping grants no date vote.
    raw_candidate = _candidate(
        "audnexus",
        book,
        local_claims(),
        marketplace="us",
        chapters=None,
        schema_rejections=lambda _: None,
    )
    assert (
        _claims(raw_candidate, ClaimKind.PUBLICATION_DATE)[0].state
        is FieldState.MALFORMED
    )
    assert not _claims(candidate, ClaimKind.PUBLICATION_DATE)


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


@pytest.mark.parametrize(
    ("summary", "description", "expected"),
    [
        (
            "A complete short synopsis.",
            "A different complete shorter description.",
            "A complete short synopsis.",
        ),
        ("<p>A complete safe synopsis.</p>", "Teaser...", "A complete safe synopsis."),
        ("", "Fallback text.", "Fallback text."),
        ("   ", "Fallback text.", "Fallback text."),
        (None, "Fallback text.", "Fallback text."),
    ],
)
def test_actual_adapter_summary_priority(
    summary: str | None, description: str, expected: str
) -> None:
    raw = {
        "asin": _ASIN,
        "title": "Synthetic Voyage",
        "authors": [{"name": "Example Writer"}],
        "summary": summary,
        "description": description,
    }
    _, handler = _recorder(book=_json(raw))
    provider = _provider(handler)
    try:
        c = provider.observe(asin_plan()).observation.candidates[0]
    finally:
        provider._client.close()
    claim = _claims(c, ClaimKind.DESCRIPTION)[0]
    assert claim.state is FieldState.VALID and claim.value == expected


@pytest.mark.parametrize(
    "summary", [{}, ["bad"], "<script>bad()</script>", "x" * 30000]
)
def test_invalid_primary_summary_does_not_fallback(summary: object) -> None:
    raw = {
        "asin": _ASIN,
        "title": "Synthetic Voyage",
        "authors": [{"name": "Example Writer"}],
        "summary": summary,
        "description": "Safe fallback must not hide failure.",
    }
    _, handler = _recorder(book=_json(raw))
    provider = _provider(handler)
    try:
        o = provider.observe(asin_plan()).observation
    finally:
        provider._client.close()
    assert not o.candidates or all(
        x.state is not FieldState.VALID
        for c in o.candidates
        for x in _claims(c, ClaimKind.DESCRIPTION)
    )


def _tag_source_report(shape: str = "complete") -> d.MetadataReport:
    base = asserted_asin_report(provider_duration="58200.000")
    target = audio_target("Synthetic Journey", ("Example Author",))
    claims = (
        target_title_claim("Synthetic Journey"),
        audio_claim(d.ClaimKind.ASIN, "B000000001", 1),
        audio_claim(d.ClaimKind.ASIN, "B000000002", 2),
    )
    plan = m.audiobook_discovery_plan(target, claims, audio=base.audio)
    raw: dict[str, dict[str, Any]] = {
        asin: {
            "asin": asin,
            "title": "Synthetic Journey",
            "authors": [{"name": "Example Author"}],
            "genres": [{"type": "tag", "name": tag} for tag in tags],
        }
        for asin, tags in (
            ("B000000001", ("First", "Shared")),
            ("B000000002", ("Shared", "Second")),
        )
    }
    if shape == "malformed_sibling":
        raw["B000000002"]["genres"].append({"type": "tag", "name": None})
    elif shape == "genre_only":
        for product in raw.values():
            for genre in product["genres"]:
                genre["type"] = "genre"
    elif shape == "oversized_union":
        for i, product in enumerate(raw.values()):
            product["genres"] = [
                {"type": "tag", "name": f"Category {i}-{j}"}
                for j in range(d.MAX_TAGS // 2 + 1)
            ]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chapters"):
            return _json({}, 404)
        return _json(raw[request.url.path.split("/")[2]])

    provider = _provider(handle)
    try:
        observation = provider.observe(plan).observation
    finally:
        provider._client.close()
    report = d.MetadataReport.create(
        target=d.TargetFacts("primary", "audio", "tags", target.fields),
        file=base.file,
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=base.audio,
    )
    return d.MetadataReport.from_json(report.to_json())


def test_non_nested_tag_union_retains_every_source_member_and_reference() -> None:
    report = _tag_source_report()
    result = next(
        x
        for x in p.evaluate_report(report).results
        if x.destination is d.TargetDestination.TAGS
    )
    union = audiobook_tag_union_candidate(report, result, "fill_empty")
    assert union is not None
    assert union.value.value == ("First", "Shared", "Second")
    candidates = tuple(c for o in report.providers for c in o.candidates)
    assert len(candidates) == 2
    source_references = {
        claim.reference
        for candidate in candidates
        for claim in candidate.claims
        if claim.kind is d.ClaimKind.TAG and claim.state is d.FieldState.VALID
    }
    assert set(union.evidence_references) == source_references
    assert audiobook_tag_union_candidate(report, result, "replace") is None
    reopened = d.MetadataReport.from_json(report.to_json())
    assert audiobook_tag_union_candidate(reopened, result, "fill_empty") == union


@pytest.mark.parametrize(
    "shape", ("malformed_sibling", "genre_only", "oversized_union")
)
def test_tag_union_refuses_incomplete_or_non_tag_source_lists(shape: str) -> None:
    report = _tag_source_report(shape)
    result = next(
        x
        for x in p.evaluate_report(report).results
        if x.destination is d.TargetDestination.TAGS
    )
    assert audiobook_tag_union_candidate(report, result, "fill_empty") is None


def _series_work_lookup_report(
    *,
    current_sequence: str | None = None,
    source_title: str = "Synthetic Journey",
    source_author: str = "Example Author",
) -> d.MetadataReport:
    """A target hint locates one product; its complete work facts earn admission."""
    target = asserted_target("B000000001", "Synthetic Journey", ("Example Author",))
    if current_sequence is not None:
        target = replace(
            target,
            fields=tuple(
                d.TargetField(
                    field.destination,
                    d.TargetFieldState.VALUE,
                    (d.TargetSeries("Synthetic Cycle", current_sequence),),
                )
                if field.destination is d.TargetDestination.SERIES
                else field
                for field in target.fields
            ),
        )
    claims = (target_title_claim("Synthetic Journey"), target_asin_claim("B000000001"))
    base = asserted_asin_report(provider_duration="58200.000")
    discovery = m.audiobook_discovery_plan(target, claims, audio=base.audio)
    feed = m.audiobook_asin_feed_plan(target, discovery, ())
    assert feed is not None

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chapters"):
            return _json({}, 404)
        return _json(
            {
                "asin": "B000000001",
                "title": source_title,
                "authors": [{"name": source_author}],
                "seriesPrimary": {"name": "Synthetic Cycle", "position": "2"},
            }
        )

    provider = _provider(handle)
    try:
        observation = provider.observe(feed).observation
    finally:
        provider._client.close()
    report = d.MetadataReport.create(
        target=d.TargetFacts("primary", "audio", "series", target.fields),
        file=base.file,
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=base.audio,
    )
    return d.MetadataReport.from_json(report.to_json())


@pytest.mark.parametrize(
    ("sequence", "title", "author", "reason"),
    [
        (None, "Synthetic Journey", "Example Author", None),
        ("1", "Synthetic Journey", "Example Author", "series_conflicting_membership"),
        (
            None,
            "Different Journey",
            "Example Author",
            "target_work_applicability_unproved",
        ),
        (None, "Synthetic Journey", "Example", "target_work_applicability_unproved"),
    ],
)
def test_one_complete_asin_work_lookup_supports_series_without_recording_authority(
    sequence: str | None,
    title: str,
    author: str,
    reason: str | None,
) -> None:
    report = _series_work_lookup_report(
        current_sequence=sequence,
        source_title=title,
        source_author=author,
    )
    observation = report.providers[0]
    assert len(observation.candidates) == 1
    candidate = observation.candidates[0]
    assert all(claim.basis == "target_metadata" for claim in report.local_claims)
    assert not p.provider_candidate_represents_target_recording(
        report, observation, candidate
    )
    assert p.provider_candidate_represents_target_work(
        report, observation, candidate
    ) == (title == "Synthetic Journey" and author == "Example Author")
    options = dict(MetadataPolicyProfile.builtin().options)
    options["series"] = "replace_qualified"
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["series"] = "automatic"
    policy = resolve_policy(
        MetadataPolicyProfile.create("Automatic series", options, destinations),
        report.target.library,
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    field = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "series"
    )
    assert field.reason == reason
    if reason is None:
        assert field.outcome == "projected"
        assert field.desired_value == ({"name": "Synthetic Cycle", "sequence": "2"},)
        result = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.SERIES
        )
        references = {
            claim.reference
            for claim in candidate.claims
            if claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
        }
        assert set(result.alternatives[0].evidence_references) == references
    else:
        assert field.outcome == "abstain" and field.desired_value is None
        assert next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.SERIES
        ).alternatives


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"identity": "lane_" + "0" * 64},
            "provider lane candidate reference is inconsistent",
        ),
        ({"input_references": ()}, "provider lane inputs are invalid"),
        ({"candidates": ()}, "provider lane candidates are invalid"),
    ],
)
def test_actual_asin_work_lane_refuses_corrupt_identity_inputs_and_membership(
    changes: dict[str, Any],
    message: str,
) -> None:
    report = _series_work_lookup_report()
    observation = report.providers[0]
    assert p.provider_candidate_represents_target_work(
        report, observation, observation.candidates[0]
    )
    with pytest.raises(d.ReportValueError, match=message):
        replace(observation, lanes=(replace(observation.lanes[0], **changes),))


@pytest.mark.parametrize("mode", ("automatic", "ignore"))
@pytest.mark.parametrize(
    ("shape", "reason"),
    [
        ("absent", "source_value_absent"),
        ("unassociated", "source_value_insufficient_association"),
        ("conflict", "source_values_conflict"),
        ("malformed", "source_value_malformed_or_incomplete"),
        ("unavailable", "source_unavailable"),
    ],
)
def test_actual_isbn_source_reasons_preserve_earlier_policy_precedence(
    monkeypatch,
    shape,
    reason,
    mode,
):
    report = _isbn_source_report(shape)
    monkeypatch.setattr(
        audiobook_decision_qualification,
        "F46_PASSING_AUTOMATIC_OPTIONS",
        audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
        | {"isbn_form:preserve_exact:audiobook_isbn_fill"},
    )
    options = dict(MetadataPolicyProfile.builtin().options)
    options["isbn_form"] = "preserve_exact"
    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations["isbn_form"] = mode
    policy = resolve_policy(
        MetadataPolicyProfile.create("ISBN source reasons", options, destinations),
        report.target.library,
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    field = next(
        value
        for value in compile_rich_metadata_preview(policy, report, proposal).fields
        if value.field == "isbn_form"
    )
    assert (field.outcome, field.reason, field.desired_value) == (
        "abstain",
        "policy_ignored" if mode == "ignore" else reason,
        None,
    )
    assert not any(
        p.provider_candidate_represents_target_recording(report, provider, candidate)
        for provider in report.providers
        for candidate in provider.candidates
    )
    assert d.MetadataReport.from_json(report.to_json()).to_json() == report.to_json()


@pytest.mark.parametrize("key", ["title", "subtitle"])
@pytest.mark.parametrize("value", ["Example\u200b Book", "Café — 日本語!"])
def test_http_title_isolation_preserves_candidate_evidence(
    key: str, value: str
) -> None:
    from mechanical_librarian.domain.metadata_report import FieldState

    document = payload("audnexus_book")
    document[key] = value
    _seen, handle = _recorder(book=_json(document))
    observation = _provider(handle).observe(asin_plan()).observation
    assert observation.status is ProviderStatus.CANDIDATES
    kind = ClaimKind.SUBTITLE if key == "subtitle" else ClaimKind.TITLE
    claims = [
        claim for candidate in observation.candidates for claim in candidate.claims
    ]
    from mechanical_librarian.adapters.providers.audnexus_metadata import _candidate

    physical = _candidate(
        "audnexus",
        document,
        asin_plan().local_claims,
        marketplace="us",
        chapters=None,
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
