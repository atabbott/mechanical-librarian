"""The AudioSilo adapter, proved against captured lookup, work and chapters."""

from __future__ import annotations

import copy
from typing import Any, Callable

import httpx
import pytest

from mechanical_librarian.adapters.providers.audiosilo_metadata import (
    AUDIOSILO_BASE_URL,
    AUDIOSILO_ORIGIN,
    AudioSiloMetadataProvider,
    AudioSiloPacer,
)
from mechanical_librarian.application.failure_diagnostics import (
    provider_attempt_diagnostics,
)
from mechanical_librarian.application.metadata_report import (
    audiobook_discovery_plan,
)
from mechanical_librarian.domain.failure_diagnostics import FailureCategory
from mechanical_librarian.domain.metadata_report import (
    AUDIOBOOK_MARKETPLACES,
    ClaimKind,
    Comparison,
    ComparisonMethod,
    ProviderEvidenceKind,
    ProviderQueryKind,
    ProviderStatus,
)
from tests.support.audiobook_providers import (
    asin_plan,
    audio_claim,
    audio_target,
    local_claims,
    payload,
    unknown_path_html,
    work_plan,
)

_ASIN = "B08G9PRS1K"
_LOOKUP = f"{AUDIOSILO_BASE_URL}/lookup"
_WORK = f"{AUDIOSILO_BASE_URL}/works/project-hail-mary"
_CHAPTERS = f"{_WORK}/recordings/ray-porter-2021/chapters"


def _json(value: object, status_code: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"content-type": "application/json; charset=utf-8", **headers},
        json=value,
    )


def _html(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code,
        headers={"content-type": "text/html; charset=utf-8"},
        content=unknown_path_html(),
    )


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    delays: list[float] | None = None,
) -> AudioSiloMetadataProvider:
    return AudioSiloMetadataProvider(
        "audiosilo",
        httpx.Client(transport=httpx.MockTransport(handler)),
        262_144,
        5.0,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=(lambda value: None) if delays is None else delays.append,
        jitter=lambda: 0.0,
    )


def _recorder(
    *,
    lookup: httpx.Response | None = None,
    work: httpx.Response | None = None,
    chapters: httpx.Response | None = None,
) -> tuple[list[httpx.URL], Callable[[httpx.Request], httpx.Response]]:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if str(request.url).endswith("/chapters"):
            return chapters or _json(payload("audiosilo_chapters"))
        if str(request.url).startswith(_LOOKUP):
            return lookup or _json(payload("audiosilo_lookup"))
        return work or _json(payload("audiosilo_work"))

    return seen, handle


def _claims(candidate: Any, kind: ClaimKind) -> tuple[Any, ...]:
    return tuple(claim for claim in candidate.claims if claim.kind is kind)


def _evidence(candidate: Any, kind: ProviderEvidenceKind) -> tuple[Any, ...]:
    return tuple(value for value in candidate.evidence if value.kind is kind)


def test_a_lookup_joins_a_work_to_the_recording_it_named() -> None:
    seen, handle = _recorder()
    observation = _provider(handle).observe(asin_plan()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert ProviderQueryKind.ASIN is observation.query_kind
    assert "audiosilo" == observation.authority
    assert [f"{_LOOKUP}?asin={_ASIN}", _WORK, _CHAPTERS] == [str(url) for url in seen]
    candidate = observation.candidates[0]
    assert "audiosilo-recording-project-hail-mary-ray-porter-2021" == candidate.identity
    assert ("Project Hail Mary",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.TITLE)
    )
    assert ("Andy Weir",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.CREATOR)
    )
    narrators = _claims(candidate, ClaimKind.CONTRIBUTOR)
    assert ("Ray Porter",) == tuple(claim.value for claim in narrators)
    assert {"narrator"} == {claim.role for claim in narrators}
    assert ("en",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.LANGUAGE)
    )


def test_the_regional_identifier_map_becomes_claims_and_one_marketplace() -> None:
    _seen, handle = _recorder()
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    recording = payload("audiosilo_work")["recordings"][0]

    assert sorted(value["asin"] for value in recording["asin"]) == sorted(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )
    # The queried identifier is the US one, so US is the marketplace that
    # resolved; the other five are stated identifiers, not provenance.
    assert ("us",) == tuple(
        value.value
        for value in _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)
    )


def test_the_chapter_document_becomes_one_bounded_whole_second_vector() -> None:
    _seen, handle = _recorder()
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    structure = _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)[0]
    document = payload("audiosilo_chapters")

    assert str(structure.chapters.runtime_seconds) == structure.value
    assert len(document["chapters"]) == structure.chapters.chapter_count
    assert not structure.chapters.durations_truncated
    assert (
        tuple(chapter["length_ms"] // 1000 for chapter in document["chapters"])
        == structure.chapters.durations_seconds
    )
    assert (
        sum(chapter["length_ms"] for chapter in document["chapters"]) // 1000
        == structure.chapters.runtime_seconds
    )


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


def test_a_recording_without_chapters_is_never_asked_about_them() -> None:
    resolved = copy.deepcopy(payload("audiosilo_lookup"))
    resolved["recording_id"] = "william-angiuli-2024"
    seen, handle = _recorder(lookup=_json(resolved))
    candidate = (
        _provider(handle).observe(asin_plan("B0CTZYCZW7")).observation.candidates[0]
    )

    assert 2 == len(seen)
    assert () == _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)
    assert ("William Angiuli",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.CONTRIBUTOR)
    )


def test_an_unknown_identifier_is_a_no_match() -> None:
    seen, handle = _recorder(lookup=_json({"message": "not found"}, 404))
    observation = _provider(handle).observe(asin_plan()).observation

    assert ProviderStatus.NO_MATCH is observation.status
    assert 1 == len(seen)


def test_an_html_answer_is_never_read_as_evidence() -> None:
    """The service answers an unknown API path with its single-page document."""
    _seen, handle = _recorder(work=_html(404))
    assert (
        ProviderStatus.NO_MATCH
        is _provider(handle).observe(asin_plan()).observation.status
    )

    _seen, handle = _recorder(work=_html(200))
    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(asin_plan()).observation.status
    )


def test_a_work_that_does_not_contain_the_named_recording_fails_closed() -> None:
    work = copy.deepcopy(payload("audiosilo_work"))
    work["recordings"] = work["recordings"][1:]
    _seen, handle = _recorder(work=_json(work))

    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(asin_plan()).observation.status
    )


@pytest.mark.parametrize(
    "resolved",
    (
        {"work": {"id": "project-hail-mary"}},
        {"recording_id": "ray-porter-2021"},
        {"work": {"id": "../../etc"}, "recording_id": "ray-porter-2021"},
        {"work": {"id": "project-hail-mary"}, "recording_id": "a/b"},
        {"work": "project-hail-mary", "recording_id": "ray-porter-2021"},
    ),
    ids=(
        "no_recording",
        "no_work",
        "traversal_work_id",
        "path_recording_id",
        "textual_work",
    ),
)
def test_a_lookup_without_a_usable_join_builds_no_url_at_all(
    resolved: dict[str, object],
) -> None:
    seen, handle = _recorder(lookup=_json(resolved))

    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(asin_plan()).observation.status
    )
    assert 1 == len(seen)


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
    _seen, handle = _recorder(lookup=_json({"message": "no"}, status_code))

    assert expected is _provider(handle).observe(asin_plan()).observation.status


def test_a_throttled_request_honours_retry_after_and_retries() -> None:
    delays: list[float] = []
    attempts: list[httpx.URL] = []
    _seen, inner = _recorder()

    def handle(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url)
        if len(attempts) == 1:
            return _json({"message": "slow down"}, 429, **{"retry-after": "5"})
        return inner(request)

    observation = _provider(handle, delays=delays).observe(asin_plan()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    assert [5.0] == delays


def test_a_missing_chapter_document_removes_evidence_rather_than_the_candidate() -> (
    None
):
    _seen, handle = _recorder(chapters=_json({"message": "no"}, 404))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]

    assert () == _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)
    assert ("Project Hail Mary",) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.TITLE)
    )


def _regional_work(entries: list[dict[str, object]]) -> httpx.Response:
    work = copy.deepcopy(payload("audiosilo_work"))
    work["recordings"][0]["asin"] = entries
    return _json(work)


def _every_marketplace(queried_region: str = "us") -> list[dict[str, object]]:
    """One entry per admitted marketplace plus one outside the closed set."""
    entries: list[dict[str, object]] = [
        {"region": region, "asin": f"B0{index:08d}"}
        for index, region in enumerate(AUDIOBOOK_MARKETPLACES)
        if region != queried_region
    ]
    entries.insert(3, {"region": queried_region, "asin": _ASIN})
    return [*entries, {"region": "br", "asin": "B0BRAZIL00"}]


def test_an_over_bound_regional_map_keeps_the_queried_asins_own_entry() -> None:
    """#1294 lost 24 AudioSilo answers to an 11-region map dropped whole (#1308)."""
    entries = _every_marketplace()
    assert len(AUDIOBOOK_MARKETPLACES) + 1 == len(entries)
    _seen, handle = _recorder(work=_regional_work(entries))
    observation = _provider(handle).observe(asin_plan()).observation

    assert ProviderStatus.CANDIDATES is observation.status
    candidate = observation.candidates[0]
    assert (_ASIN,) == tuple(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )
    assert ("us",) == tuple(
        value.value
        for value in _evidence(candidate, ProviderEvidenceKind.ASIN_MARKETPLACE)
    )
    # The over-bound map is still one stated schema rejection.
    assert [(FailureCategory.SCHEMA_CONSTRUCTION, 1)] == [
        (value.category, value.count) for value in observation.diagnostics
    ]


def test_a_regional_map_at_the_bound_keeps_every_admitted_entry() -> None:
    entries = _every_marketplace()[:-1]
    _seen, handle = _recorder(work=_regional_work(entries))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]

    assert sorted(str(entry["asin"]) for entry in entries) == sorted(
        claim.value for claim in _claims(candidate, ClaimKind.ASIN)
    )


@pytest.mark.parametrize(
    "entries",
    (
        # The queried product only beyond the bounded scan.
        [{"region": "br", "asin": f"B1{index:08d}"} for index in range(64)]
        + [{"region": "us", "asin": _ASIN}],
        # The queried product held only by a marketplace outside the closed set:
        # the scan never widens the marketplaces.
        [{"region": "br", "asin": _ASIN}]
        + [{"region": "us", "asin": f"B1{index:08d}"} for index in range(11)],
        # The queried product absent from an over-bound map.
        [{"region": "us", "asin": f"B1{index:08d}"} for index in range(11)],
    ),
    ids=("beyond_the_scan", "outside_the_marketplaces", "absent"),
)
def test_an_over_bound_regional_map_without_its_own_entry_still_fails_closed(
    entries: list[dict[str, object]],
) -> None:
    _seen, handle = _recorder(work=_regional_work(entries))

    assert (
        ProviderStatus.MALFORMED
        is _provider(handle).observe(asin_plan()).observation.status
    )


def test_an_unknown_identifier_is_named_a_miss_rather_than_an_http_failure() -> None:
    _seen, handle = _recorder(lookup=_json({"message": "not found"}, 404))
    result = _provider(handle).observe(asin_plan())

    assert result.evidence.attempt_records is not None
    assert ("not_found",) == tuple(
        record.category for record in result.evidence.attempt_records
    )
    assert (FailureCategory.EMPTY_NO_MATCH,) == tuple(
        value.category
        for value in provider_attempt_diagnostics(
            result.evidence.attempt_records, status=result.observation.status.value
        )
    )


def test_an_over_bound_chapter_document_is_stated_and_spares_the_next_lane() -> None:
    """A dropped chapter body no longer zeroes the observation's byte budget."""
    second = "B08G9RZBTT"
    plan = audiobook_discovery_plan(
        audio_target(), (*local_claims(), audio_claim(ClaimKind.ASIN, second, 5))
    )
    assert 2 == len(plan.lanes)
    oversized = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        content=b'{"chapters": []}' + b" " * 262_144,
    )
    # The second ASIN names another work, so the two lanes are two candidates.
    other_lookup = copy.deepcopy(payload("audiosilo_lookup"))
    other_lookup["work"]["id"] = "other-work"
    other_work = copy.deepcopy(payload("audiosilo_work"))
    other_work["id"] = "other-work"
    other_work["recordings"][0]["asin"] = [{"region": "de", "asin": second}]
    served: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        served.append(url)
        if url.startswith(_LOOKUP):
            return _json(
                payload("audiosilo_lookup") if url.endswith(_ASIN) else other_lookup
            )
        if url == _CHAPTERS:
            return _json(payload("audiosilo_chapters"))
        if url.endswith("/chapters"):
            return oversized
        return _json(payload("audiosilo_work") if url == _WORK else other_work)

    provider = _provider(handle)
    result = provider.observe(plan)

    # The over-bound body comes first, and the other lane is still answered in
    # full, chapters included.
    assert served[2].endswith("/other-work/recordings/ray-porter-2021/chapters")
    assert 6 == len(served)
    assert {ProviderStatus.CANDIDATES} == {
        lane.status for lane in result.observation.lanes
    }
    chapters = {
        candidate.identity: _evidence(candidate, ProviderEvidenceKind.CHAPTER_STRUCTURE)
        for candidate in result.observation.candidates
    }
    assert 1 == sum(bool(value) for value in chapters.values())
    assert result.evidence.incomplete_causes == ()
    assert result.evidence.attempt_records is not None
    assert ["response_oversized"] == [
        record.category
        for record in result.evidence.attempt_records
        if record.category != "none"
    ]
    assert (FailureCategory.OVERSIZED_RESPONSE,) == tuple(
        value.category
        for value in provider_attempt_diagnostics(
            result.evidence.attempt_records, status=result.observation.status.value
        )
    )
    assert 0 < provider._remaining_response_bytes


def test_every_request_stays_under_the_one_fixed_base_path() -> None:
    seen, handle = _recorder()
    _provider(handle).observe(asin_plan())

    assert "https://meta.audiosilo.app" == AUDIOSILO_ORIGIN
    assert AUDIOSILO_ORIGIN + "/api/v1" == AUDIOSILO_BASE_URL
    assert all(str(url).startswith(AUDIOSILO_BASE_URL + "/") for url in seen)
    pacer = AudioSiloPacer(1.0, clock=lambda: 0.0, sleep=lambda value: None)
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


@pytest.mark.parametrize("member", ({}, {"name": []}, "not a credit"))
@pytest.mark.parametrize("field", ("authors", "narrators"))
def test_malformed_credit_members_remain_closed_set_evidence(field, member):
    work = copy.deepcopy(payload("audiosilo_work"))
    owner = work if field == "authors" else work["recordings"][0]
    owner[field] = [{"name": "Synthetic Credit"}, member]
    _seen, handle = _recorder(work=_json(work))
    observation = _provider(handle).observe(asin_plan()).observation
    kind = ClaimKind.CREATOR if field == "authors" else ClaimKind.CONTRIBUTOR
    claims = _claims(observation.candidates[0], kind)
    assert [(claim.value, claim.state.value) for claim in claims] == [
        ("Synthetic Credit", "valid"),
        (None, "malformed"),
    ]
    assert observation.diagnostics[0].count == 1


@pytest.mark.parametrize(
    "companion",
    (
        {"name": "Second Cycle", "sequence": 7},
        {},
        {"name": "Second Cycle", "sequence": []},
    ),
)
def test_series_keeps_ordered_pairs_and_malformed_companions(companion):
    work = copy.deepcopy(payload("audiosilo_work"))
    work["series"] = [{"name": "First Cycle", "sequence": "2"}, companion]
    _seen, handle = _recorder(work=_json(work))
    candidate = _provider(handle).observe(asin_plan()).observation.candidates[0]
    claims = tuple(
        claim
        for claim in candidate.claims
        if claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
    )
    assert len(claims) == 4
    assert [claim.kind for claim in claims] == [
        ClaimKind.SERIES,
        ClaimKind.SERIES_SEQUENCE,
    ] * 2
    assert [claim.raw_order for claim in claims] == list(
        range(claims[0].raw_order, claims[0].raw_order + 4)
    )
    assert [claim.value for claim in claims[:2]] == ["First Cycle", "2"]
    assert claims[2].value == companion.get("name")
    assert claims[3].value == ("7" if companion.get("sequence") == 7 else None)
    if not companion or type(companion.get("sequence")) is list:
        assert any(claim.state.value == "malformed" for claim in claims[2:])


@pytest.mark.parametrize("key", ["title"])
@pytest.mark.parametrize("value", ["Example\u200b Book", "Café — 日本語!"])
def test_http_title_isolation_preserves_candidate_evidence(
    key: str, value: str
) -> None:
    from mechanical_librarian.domain.metadata_report import FieldState

    document = payload("audiosilo_work")
    document[key] = value
    _seen, handle = _recorder(work=_json(document))
    observation = _provider(handle).observe(asin_plan()).observation
    assert observation.status is ProviderStatus.CANDIDATES
    kind = ClaimKind.SUBTITLE if key == "subtitle" else ClaimKind.TITLE
    claims = [
        claim for candidate in observation.candidates for claim in candidate.claims
    ]
    selected = [claim for claim in claims if claim.kind is kind]
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
