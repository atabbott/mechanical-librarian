"""A matched audiobook edition carried into the next preparation (#1316).

Audible search stopped listing editions it had admitted while the products
themselves stayed live, so an item could lose its match to a sibling edition
on a later preparation. These cases prove the carried admission end to end at
the domain and plan seams: what a report admits and carries, how the carried
lane is planned, ordered and verified, and how a verified carried edition and
one recording's sibling ASINs reach the ASIN destination.
"""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    AUDIBLE_CATALOG_URLS,
    AudibleCatalogMetadataProvider,
)
from mechanical_librarian.adapters.providers.retry_safety import ProviderRequestPacer
from mechanical_librarian.application.metadata_report import (
    MAX_ASIN_DISCOVERY_LANES,
    MAX_PROVIDER_DISCOVERY_QUERIES,
    MetadataDiscoveryLane,
    MetadataDiscoveryPlan,
    MetadataLookup,
    ProviderLaneResult,
    TargetFile,
    TargetSnapshot,
    audiobook_asin_feed_plan,
    audiobook_carried_plan,
    audiobook_discovery_plan,
    observe_provider,
    provider_discovery_lane_order,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
    admitted_recording_asin,
    candidate_edition_asin,
    carried_asin_precedence,
)
from mechanical_librarian.domain.metadata_report import (
    AudioDiscoveryTitle,
    AudioFacts,
    ClaimKind,
    ClaimScope,
    FieldSource,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    WorkDispatchLineage,
    default_target_fields,
    derive_field_results,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from mechanical_librarian.domain.work_identity import work_query_ladder
from tests.support.audiobook_providers import audio_target, payload
from tests.support.audiobook_reports import (
    recorded_dispatch,
    recording_lookup_hint_report,
)
from tests.support.metadata_report_doubles import single_track_audio_facts

_TITLE = "Ember Tide Protocol"
_AUTHOR = "Corin Halloway"
_NARRATOR = "Ilsa Brandt"
_CARRIED = "B0SYNE7P1K"
_SIBLING = "B0SIBLING1"
_RUNTIME = "58244.201"
_TIGHT = "58200.000"
_DIGEST = "b" * 64


def _local(
    kind: ClaimKind,
    value: str,
    occurrence: int,
    *,
    basis: str = "embedded_tag",
    role: str | None = None,
    scope: ClaimScope | None = None,
) -> LocalClaim:
    return LocalClaim.create(
        kind=kind,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        basis=basis,
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role=role,
        occurrence=occurrence,
        scope=scope,
    )


def _prior_claim(asin: str = _CARRIED) -> LocalClaim:
    return _local(
        ClaimKind.ASIN, asin, 0, basis="prior_admission", scope=ClaimScope.EDITION
    )


def _claims(
    *, carried: str | None = _CARRIED, narrator: str | None = _NARRATOR
) -> tuple[LocalClaim, ...]:
    return (
        _local(
            ClaimKind.TITLE, _TITLE, 0, basis="target_metadata", scope=ClaimScope.WORK
        ),
        *(() if carried is None else (_prior_claim(carried),)),
        _local(ClaimKind.DURATION, _RUNTIME, 1),
        *(
            ()
            if narrator is None
            else (_local(ClaimKind.CONTRIBUTOR, narrator, 2, role="narrator"),)
        ),
    )


def _fields(asin: str | None = None) -> tuple[TargetField, ...]:
    overrides = {
        TargetDestination.AUTHORS: TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, (_AUTHOR,)
        )
    }
    if asin is not None:
        overrides[TargetDestination.ASIN] = TargetField(
            TargetDestination.ASIN, TargetFieldState.VALUE, asin
        )
    return tuple(
        overrides.get(field.destination, field)
        for field in default_target_fields(_TITLE)
    )


def _snapshot(asin: str | None = None) -> TargetSnapshot:
    return TargetSnapshot(
        "audiobook",
        _TITLE,
        (TargetFile("a" * 64, "book.m4b", "m4b"),),
        fields=_fields(asin),
    )


def _plan(
    claims: tuple[LocalClaim, ...],
    asin: str | None = None,
    audio: AudioFacts | None = None,
) -> MetadataDiscoveryPlan:
    return audiobook_discovery_plan(
        _snapshot(asin), claims, audio=audio or single_track_audio_facts(_DIGEST)
    )


def _candidate(
    provider: str,
    asins: tuple[str, ...],
    *,
    narrator: str | None = _NARRATOR,
    duration: str | None = _TIGHT,
    title: str | None = _TITLE,
    identity: str | None = None,
) -> ProviderCandidate:
    """One product as the audiobook adapters state it; a stub states only ASINs."""
    identity = identity or f"{provider}-{asins[0]}"
    specifications: list[tuple[ClaimKind, str, ClaimScope, str | None]] = [
        (ClaimKind.ASIN, asin, ClaimScope.EDITION, None) for asin in asins
    ]
    if title is not None:
        specifications += [
            (ClaimKind.TITLE, title, ClaimScope.WORK, None),
            (ClaimKind.CREATOR, _AUTHOR, ClaimScope.WORK, "author"),
        ]
    if narrator is not None:
        specifications.append(
            (ClaimKind.CONTRIBUTOR, narrator, ClaimScope.WORK, "narrator")
        )
    if duration is not None:
        specifications.append((ClaimKind.DURATION, duration, ClaimScope.EDITION, None))
    claims = tuple(
        ProviderClaim.create(
            provider=provider,
            candidate=identity,
            kind=kind,
            value=value,
            state=FieldState.VALID,
            scope=scope,
            applicability="not_applicable",
            role=role,
            raw_order=order,
        )
        for order, (kind, value, scope, role) in enumerate(specifications)
    )
    evidence = (
        ProviderEvidence(
            ProviderEvidenceKind.ASIN_MARKETPLACE, "us", ClaimScope.EDITION
        ),
        ProviderEvidence(
            ProviderEvidenceKind.EDITION_WORK_RELATION, "unresolved", ClaimScope.EDITION
        ),
    )
    return ProviderCandidate(
        identity,
        "unresolved",
        claims,
        tuple(sorted(evidence, key=lambda e: (e.scope.value, e.kind.value, e.value))),
    )


def _lane_result(
    plan: MetadataDiscoveryPlan,
    lane: MetadataDiscoveryLane,
    provider: str,
    authority: str,
    candidates: tuple[ProviderCandidate, ...] = (),
    status: ProviderStatus = ProviderStatus.CANDIDATES,
) -> ProviderLaneResult:
    """Answer one lane and qualify it exactly as the adapters do."""
    if status is not ProviderStatus.CANDIDATES:
        return ProviderLaneResult(lane, status)
    work = lane.kind is ProviderQueryKind.WORK
    qualified = qualify_provider_observation(
        ProviderObservation(
            provider,
            ProviderStatus.CANDIDATES,
            (),
            tuple(
                finalize_candidate_comparisons(value, plan.local_claims, provider)
                for value in candidates
            ),
            lane.kind,
            authority,
            dispatch_lineage=(
                WorkDispatchLineage.from_query(
                    work_query_ladder(lane.title or "", lane.authors)[0]
                )
                if work
                else None
            ),
        ),
        MetadataLookup(
            lane.kind,
            plan.local_claims,
            lane.title if work else None,
            lane.authors if work else (),
            audio=plan.audio,
            work_title=plan.work_title,
            work_authors=plan.work_authors,
            requested_asin=lane.canonical_asin,
            title_basis=lane.title_basis,
        ),
    )
    return ProviderLaneResult(
        lane,
        qualified.status,
        qualified.candidates,
        dispatch_lineage=qualified.dispatch_lineage,
        transformation_lineage=qualified.transformation_lineage,
        recording_lookup_candidates=qualified.recording_lookup_candidates,
    )


def _report(
    claims: tuple[LocalClaim, ...],
    observations: tuple[ProviderObservation, ...],
    asin: str | None = None,
    audio: AudioFacts | None = None,
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", _fields(asin)),
        file=FileFacts(0, "a" * 64, _DIGEST, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=tuple(
            sorted(
                (recorded_dispatch(value) for value in observations),
                key=lambda value: value.provider,
            )
        ),
        audio=audio or single_track_audio_facts(_DIGEST),
    )


def _catalogue(
    claims: tuple[LocalClaim, ...],
    *,
    carried: tuple[ProviderCandidate, ...] | ProviderStatus = (),
    search: tuple[ProviderCandidate, ...] = (),
    asin: str | None = None,
    audio: AudioFacts | None = None,
) -> ProviderObservation:
    """The catalogue's phase-one answer: its carried lookup and its search."""
    plan = audiobook_carried_plan(_plan(claims, asin, audio))
    results = []
    for lane in plan.lanes:
        if lane.kind is ProviderQueryKind.ASIN:
            if isinstance(carried, ProviderStatus):
                results.append(
                    _lane_result(
                        plan, lane, "audible", "audible_catalog", status=carried
                    )
                )
            else:
                results.append(
                    _lane_result(plan, lane, "audible", "audible_catalog", carried)
                )
        elif search:
            results.append(
                _lane_result(plan, lane, "audible", "audible_catalog", search)
            )
        else:
            results.append(ProviderLaneResult(lane, ProviderStatus.NO_MATCH))
    return provider_observation("audible", tuple(results), authority="audible_catalog")


def _asserted_claims(asin: str) -> tuple[LocalClaim, ...]:
    return (
        *_claims(carried=None),
        _local(
            ClaimKind.ASIN, asin, 0, basis="target_metadata", scope=ClaimScope.EDITION
        ),
    )


def _fed(
    claims: tuple[LocalClaim, ...], asin: str, candidate: ProviderCandidate
) -> ProviderObservation:
    """Audnexus answering the asserted ASIN it was fed in phase two."""
    feed = audiobook_asin_feed_plan(_snapshot(asin), _plan(claims, asin=asin), ())
    assert feed is not None
    lane = next(value for value in feed.lanes if value.canonical_asin == asin)
    return provider_observation(
        "enrichment",
        (_lane_result(feed, lane, "enrichment", "audnexus", (candidate,)),),
        authority="audnexus",
    )


def _asin_values(report: MetadataReport) -> set[str]:
    result = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.ASIN
    )
    return {str(value.value.value) for value in result.alternatives}


# -- the claim and its owner-facing source ------------------------------------


def test_a_carried_admission_is_one_edition_asin_under_its_own_source() -> None:
    claim = _prior_claim()
    with pytest.raises(ReportValueError):
        _local(
            ClaimKind.TITLE, _TITLE, 0, basis="prior_admission", scope=ClaimScope.WORK
        )
    with pytest.raises(ReportValueError):
        _local(
            ClaimKind.ASIN, _CARRIED, 0, basis="prior_admission", scope=ClaimScope.WORK
        )
    results = {value.kind: value for value in derive_field_results((claim,))}
    asin_sources = {
        value.source: value.state for value in results[ClaimKind.ASIN].sources
    }
    title_sources = {
        value.source: value.state for value in results[ClaimKind.TITLE].sources
    }
    assert FieldState.VALID is asin_sources[FieldSource.PRIOR_ADMISSION]
    assert FieldState.UNSUPPORTED is title_sources[FieldSource.PRIOR_ADMISSION]


# -- what a report admits, and so carries -----------------------------------


def test_a_tight_search_admission_is_carried() -> None:
    claims = _claims(carried=None)
    report = _report(
        claims, (_catalogue(claims, search=(_candidate("audible", (_CARRIED,)),)),)
    )

    assert _CARRIED == admitted_recording_asin(report)


def test_an_item_with_its_own_asin_carries_nothing() -> None:
    claims = (*_claims(carried=None), _local(ClaimKind.ASIN, _CARRIED, 3))
    plan = _plan(claims)
    (lane,) = plan.lanes
    observation = provider_observation(
        "audible",
        (
            _lane_result(
                plan,
                lane,
                "audible",
                "audible_catalog",
                (_candidate("audible", (_CARRIED,)),),
            ),
        ),
        authority="audible_catalog",
    )

    assert admitted_recording_asin(_report(claims, (observation,))) is None


@pytest.mark.parametrize(
    ("narrator", "duration"),
    (("Someone Else", _TIGHT), (_NARRATOR, "58800.000")),
    ids=("narrator_disjoint", "loose_band_only"),
)
def test_a_disjoint_or_loose_admission_is_not_carried(
    narrator: str, duration: str
) -> None:
    claims = _claims(carried=None)
    candidate = _candidate("audible", (_CARRIED,), narrator=narrator, duration=duration)
    report = _report(claims, (_catalogue(claims, search=(candidate,)),))

    assert admitted_recording_asin(report) is None


def test_two_admitted_editions_carry_nothing() -> None:
    claims = _asserted_claims(_CARRIED)
    report = _report(
        claims,
        (
            _catalogue(
                claims, search=(_candidate("audible", (_SIBLING,)),), asin=_CARRIED
            ),
            _fed(claims, _CARRIED, _candidate("enrichment", (_CARRIED,))),
        ),
        asin=_CARRIED,
    )

    assert {_CARRIED, _SIBLING} == _asin_values(report)
    assert admitted_recording_asin(report) is None


# -- the carried lane: planned, bounded, ordered ------------------------------


def test_the_carried_lane_goes_to_the_catalogue_first_and_stays_inside_the_bound() -> (
    None
):
    claims = _claims()
    plan = _plan(claims)
    carried = audiobook_carried_plan(plan)
    (lane,) = (value for value in carried.lanes if value.kind is ProviderQueryKind.ASIN)

    assert not any(value.kind is ProviderQueryKind.ASIN for value in plan.lanes)
    assert (_prior_claim().reference,) == lane.input_references
    assert _CARRIED == lane.canonical_asin
    assert lane is provider_discovery_lane_order(carried)[0]
    # Nothing carried, an own ASIN, or an overflow adds no lane.
    assert _plan(_claims(carried=None)) == audiobook_carried_plan(
        _plan(_claims(carried=None))
    )
    overflow = replace(plan, lanes=(), overflow=True)
    assert overflow is audiobook_carried_plan(overflow)


def test_two_three_rung_ladders_and_the_carried_lookup_fit_the_query_bound() -> None:
    """The worst audiobook plan: an asserted and an alternative ladder of three."""
    title, authors = "Example: A Subtitle", ("Ada & Bea",)
    assert 3 == len(work_query_ladder(title, authors, audiobook=True))
    plan = _plan(_claims())
    work = next(value for value in plan.lanes if value.kind is ProviderQueryKind.WORK)
    alternative = replace(
        work,
        identity="lane_" + "e" * 64,
        title=title,
        authors=authors,
        title_basis="path_leaf",
    )
    asserted = replace(work, title=title, authors=authors)
    worst = replace(
        plan,
        lanes=tuple(sorted((asserted, alternative), key=lambda value: value.identity)),
    )
    carried = audiobook_carried_plan(worst)

    rungs = sum(
        len(work_query_ladder(value.title or "", value.authors, audiobook=True))
        for value in carried.lanes
        if value.kind is ProviderQueryKind.WORK
    )
    assert 7 == rungs + 1 <= MAX_PROVIDER_DISCOVERY_QUERIES
    assert 3 == len(carried.lanes)


def test_the_feed_asks_the_carried_asin_before_every_other_origin() -> None:
    """Local, carried, promoted, asserted, hints: the carried one is never cut."""
    exact = ("B000000001", "B000000002", "B000000003")
    report = recording_lookup_hint_report(
        lookup_asins=exact, hint_asins=("B000000011", "B000000012")
    )
    (observation,) = report.providers
    plain = audiobook_discovery_plan(
        audio_target(), report.local_claims, audio=report.audio
    )
    carried = audiobook_discovery_plan(
        audio_target(), (*report.local_claims, _prior_claim()), audio=report.audio
    )

    before = audiobook_asin_feed_plan(audio_target(), plain, (observation,))
    after = audiobook_asin_feed_plan(audio_target(), carried, (observation,))

    assert before is not None and after is not None
    assert {*exact, "B000000011"} == {value.canonical_asin for value in before.lanes}
    assert MAX_ASIN_DISCOVERY_LANES == len(after.lanes)
    assert {*exact, _CARRIED} == {value.canonical_asin for value in after.lanes}
    lane = next(value for value in after.lanes if value.canonical_asin == _CARRIED)
    assert (_prior_claim().reference,) == lane.input_references


def test_the_catalogue_looks_the_carried_asin_up_before_a_starved_search() -> None:
    """The search is the only lane a deadline can starve."""
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json=payload("audible_lookup"),
        )

    pacer = ProviderRequestPacer(3.0, clock=lambda: 0.0, sleep=lambda value: None)
    provider = AudibleCatalogMetadataProvider(
        "audible",
        httpx.Client(transport=httpx.MockTransport(handle)),
        262_144,
        5.0,
        marketplaces=("us",),
        pacer=pacer,
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda value: None,
        jitter=lambda: 0.0,
    )
    plan = audiobook_carried_plan(_plan(_claims()))
    try:
        observation = observe_provider(
            "audible", "audible_catalog", provider, plan, deadline=2.0
        ).observation
    finally:
        provider._client.close()

    assert 1 == len(seen)
    assert str(seen[0]).startswith(f"{AUDIBLE_CATALOG_URLS['us']}/{_CARRIED}")
    statuses = {value.kind: value.status for value in observation.lanes}
    assert ProviderStatus.CANDIDATES is statuses[ProviderQueryKind.ASIN]
    assert ProviderStatus.INCOMPLETE is statuses[ProviderQueryKind.WORK]
    report = _report(plan.local_claims, (observation,))
    assert _CARRIED == admitted_recording_asin(report)


# -- verification of the carried lane -----------------------------------------


def test_an_asin_only_stub_is_refused_and_drops_the_carried_admission() -> None:
    claims = _claims()
    stub = _candidate("audible", (_CARRIED,), narrator=None, duration=None, title=None)
    observation = _catalogue(claims, carried=(stub,))
    (candidate,) = observation.candidates

    assert {ClaimKind.ASIN} == {value.kind for value in candidate.claims}
    assert () == candidate.evidence
    assert admitted_recording_asin(_report(claims, (observation,))) is None


def test_a_narrator_disjoint_product_is_refused_only_on_the_carried_lane() -> None:
    claims = _claims()
    other = _candidate("audible", (_CARRIED,), narrator="Someone Else")
    refused = _catalogue(claims, carried=(other,))
    (candidate,) = refused.candidates
    alone = _report(claims, (refused,))

    # Another recording: only its identifier survives, so its tight runtime
    # cannot admit this one and its cast is never offered.
    assert {ClaimKind.ASIN} == {value.kind for value in candidate.claims}
    assert () == candidate.evidence
    assert not admitted_audiobook_identity(alone)
    assert all(
        not result.alternatives
        for result in evaluate_report(alone).results
        if result.destination is TargetDestination.NARRATORS
    )
    assert admitted_recording_asin(alone) is None
    # The same product agreeing on its narrator is admitted and carried again.
    admitted = _catalogue(claims, carried=(_candidate("audible", (_CARRIED,)),))
    assert _CARRIED == admitted_recording_asin(_report(claims, (admitted,)))
    # An asserted ASIN carries no narrator gate: only the carried origin does.
    asserted_claims = (
        *_claims(carried=None),
        _local(
            ClaimKind.ASIN,
            _CARRIED,
            0,
            basis="target_metadata",
            scope=ClaimScope.EDITION,
        ),
    )
    plan = _plan(asserted_claims, asin=_CARRIED)
    feed = audiobook_asin_feed_plan(_snapshot(_CARRIED), plan, ())
    assert feed is not None
    (lane,) = feed.lanes
    result = _lane_result(
        feed,
        lane,
        "silo",
        "audiosilo",
        (_candidate("silo", (_CARRIED,), narrator="Someone Else"),),
    )
    assert any(
        value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        for value in result.candidates[0].evidence
    )


@pytest.mark.parametrize(
    "status",
    (
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.INCOMPLETE,
        ProviderStatus.AUTHENTICATION,
        ProviderStatus.DISABLED,
    ),
    ids=lambda value: value.value,
)
def test_a_recoverable_carried_lane_carries_the_admission_again(
    status: ProviderStatus,
) -> None:
    claims = _claims()
    report = _report(claims, (_catalogue(claims, carried=status),))

    assert _CARRIED == admitted_recording_asin(report)


@pytest.mark.parametrize(
    "status",
    (
        ProviderStatus.MALFORMED,
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
        ProviderStatus.OVERSIZED,
    ),
    ids=lambda value: value.value,
)
def test_a_failed_carried_lane_carries_the_admission_again(
    status: ProviderStatus,
) -> None:
    """A failure is not an answer, recoverable or not, so it refutes nothing."""
    claims = _claims()
    report = _report(claims, (_catalogue(claims, carried=status),))

    assert _CARRIED == admitted_recording_asin(report)


def test_a_refuted_carried_admission_yields_to_a_fresh_one() -> None:
    claims = _claims()
    absent = _report(claims, (_catalogue(claims, carried=ProviderStatus.NO_MATCH),))
    replaced = _report(
        claims,
        (
            _catalogue(
                claims,
                carried=ProviderStatus.NO_MATCH,
                search=(_candidate("audible", (_SIBLING,)),),
            ),
        ),
    )

    assert admitted_recording_asin(absent) is None
    assert _SIBLING == admitted_recording_asin(replaced)


_LEAF = "The Leaf Title"


def _leaf_audio() -> AudioFacts:
    return replace(
        single_track_audio_facts(_DIGEST),
        discovery_titles=(AudioDiscoveryTitle("path_leaf", _LEAF),),
    )


def test_a_carried_edition_first_matched_on_the_path_leaf_re_verifies() -> None:
    """A carried lookup admits against the alternative discovery title (#1316).

    The edition was first matched on the path-leaf lane (#1255), so its product
    states that title rather than the target's own. Its direct lookup admits
    against the same alternative title, under the tight band, and verifies.
    """
    audio = _leaf_audio()
    leaf = _candidate("audible", (_CARRIED,), title=_LEAF)
    first_claims = _claims(carried=None)
    first = _report(
        first_claims,
        (_catalogue(first_claims, search=(leaf,), audio=audio),),
        audio=audio,
    )
    assert _CARRIED == admitted_recording_asin(first)

    claims = _claims()
    second = _report(
        claims, (_catalogue(claims, carried=(leaf,), audio=audio),), audio=audio
    )

    (carried,) = second.providers[0].candidates
    assert any(
        value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        for value in carried.evidence
    )
    assert _CARRIED == admitted_recording_asin(second)
    # Without that discovery title the same product is an unadmitted stub.
    plain = _report(claims, (_catalogue(claims, carried=(leaf,)),))
    (stub,) = plain.providers[0].candidates
    assert {ClaimKind.ASIN} == {value.kind for value in stub.claims}
    assert admitted_recording_asin(plain) is None


def test_the_carried_lookup_admits_against_the_title_the_alternative_lane_asks() -> (
    None
):
    """The path leaf repeats the target title, so the lane asks the tag title.

    The carried lookup re-verifies against that same surviving title, never
    against the path leaf the lane dropped (#1255, #1316).
    """
    tag = "The Tag Title"
    audio = replace(
        single_track_audio_facts(_DIGEST),
        discovery_titles=(
            AudioDiscoveryTitle("path_leaf", _TITLE),
            AudioDiscoveryTitle("title_tag", tag),
        ),
    )
    (alternative,) = (
        lane
        for lane in _plan(_claims(carried=None), audio=audio).lanes
        if lane.title_basis is not None
    )
    assert ("title_tag", tag) == (alternative.title_basis, alternative.title)
    product = _candidate("audible", (_CARRIED,), title=tag)
    first_claims = _claims(carried=None)
    first = _report(
        first_claims,
        (_catalogue(first_claims, search=(product,), audio=audio),),
        audio=audio,
    )
    assert _CARRIED == admitted_recording_asin(first)

    claims = _claims()
    second = _report(
        claims, (_catalogue(claims, carried=(product,), audio=audio),), audio=audio
    )

    (carried,) = second.providers[0].candidates
    assert ClaimKind.TITLE in {value.kind for value in carried.claims}
    assert _CARRIED == admitted_recording_asin(second)


def test_a_loose_alternative_title_product_stays_a_stub() -> None:
    audio = _leaf_audio()
    claims = _claims()
    loose = _candidate("audible", (_CARRIED,), title=_LEAF, duration="57000.000")
    report = _report(
        claims, (_catalogue(claims, carried=(loose,), audio=audio),), audio=audio
    )

    (stub,) = report.providers[0].candidates
    assert {ClaimKind.ASIN} == {value.kind for value in stub.claims}


def _silo_carried(
    claims: tuple[LocalClaim, ...],
    candidates: tuple[ProviderCandidate, ...] = (),
    status: ProviderStatus = ProviderStatus.CANDIDATES,
    *,
    provider: str = "silo",
    authority: str = "audiosilo",
) -> ProviderObservation:
    """A per-ASIN provider answering the carried ASIN it was fed in phase two."""
    feed = audiobook_asin_feed_plan(_snapshot(), _plan(claims), ())
    assert feed is not None
    lane = next(value for value in feed.lanes if value.canonical_asin == _CARRIED)
    return provider_observation(
        provider,
        (_lane_result(feed, lane, provider, authority, candidates, status),),
        authority=authority,
    )


def test_a_carried_edition_refuted_at_audible_is_kept_while_another_lookup_waits() -> (
    None
):
    """Keep-or-refute reads every audiobook authority's carried lane (#1316)."""
    claims = _claims()
    loose = (_candidate("audible", (_CARRIED,), duration="57000.000"),)
    waiting = _report(
        claims,
        (
            _catalogue(claims, carried=loose),
            _silo_carried(claims, status=ProviderStatus.RATE_LIMITED),
        ),
    )
    verified = _report(
        claims,
        (
            _catalogue(claims, carried=loose),
            _silo_carried(claims, (_candidate("silo", (_CARRIED,)),)),
        ),
    )
    refuted = _report(
        claims,
        (
            _catalogue(claims, carried=loose),
            _silo_carried(claims, status=ProviderStatus.NO_MATCH),
        ),
    )

    assert _CARRIED == admitted_recording_asin(waiting)
    assert _CARRIED == admitted_recording_asin(verified)
    assert admitted_recording_asin(refuted) is None


def test_a_carried_edition_every_answering_authority_refuses_is_refuted() -> None:
    claims = _claims()
    report = _report(
        claims,
        (
            _catalogue(
                claims,
                carried=(_candidate("audible", (_CARRIED,), duration="57000.000"),),
            ),
            _silo_carried(claims, status=ProviderStatus.NO_MATCH),
            _silo_carried(
                claims,
                status=ProviderStatus.NO_MATCH,
                provider="enrichment",
                authority="audnexus",
            ),
        ),
    )

    assert admitted_recording_asin(report) is None


def test_a_disabled_carried_lane_does_not_keep_a_refused_edition() -> None:
    """A disabled provider answers nothing, so it cannot keep a carry alive."""
    claims = _claims()
    report = _report(
        claims,
        (
            _catalogue(claims, carried=ProviderStatus.DISABLED),
            _silo_carried(claims, status=ProviderStatus.NO_MATCH),
        ),
    )

    assert admitted_recording_asin(report) is None


# -- the ASIN destination ------------------------------------------------------


def test_a_verified_carried_edition_outranks_a_tight_search_sibling() -> None:
    claims = _claims()
    report = _report(
        claims,
        (
            _catalogue(
                claims,
                carried=(_candidate("audible", (_CARRIED,)),),
                search=(_candidate("audible", (_SIBLING,)),),
            ),
        ),
    )

    assert {("audible", f"audible-{_SIBLING}")} == carried_asin_precedence(report)
    assert {_CARRIED} == _asin_values(report)
    assert _CARRIED == admitted_recording_asin(report)
    # The carried claim itself is never offered as a local value.
    result = next(
        value
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.ASIN
    )
    assert all(
        _prior_claim().reference not in value.evidence_references
        for value in result.alternatives
    )


def _owner_claims(narrator: str | None = _NARRATOR) -> tuple[LocalClaim, ...]:
    """A carried admission on an item whose owner records a sibling edition."""
    return (
        *_claims(narrator=narrator),
        _local(
            ClaimKind.ASIN,
            _SIBLING,
            0,
            basis="target_metadata",
            scope=ClaimScope.EDITION,
        ),
    )


def _asin_outcome(report: MetadataReport) -> ProposalOutcome:
    return next(
        value.outcome
        for value in evaluate_report(report).results
        if value.destination is TargetDestination.ASIN
    )


def test_the_owners_admitted_edition_outranks_the_carried_one() -> None:
    """A verified carried edition never contests the owner's own value (#1316).

    One preparation admitted C while the owner records S; the next verifies C
    and search returns S as the same recording. S is kept, C is not offered,
    and C is not carried again, so the field settles.
    """
    claims = _owner_claims()
    report = _report(
        claims,
        (
            _catalogue(
                claims,
                carried=(_candidate("audible", (_CARRIED,)),),
                search=(_candidate("audible", (_SIBLING,)),),
                asin=_SIBLING,
            ),
            _fed(claims, _SIBLING, _candidate("enrichment", (_SIBLING,))),
        ),
        asin=_SIBLING,
    )

    withheld = carried_asin_precedence(report)
    assert ("audible", f"audible-{_SIBLING}") not in withheld
    assert ("enrichment", f"enrichment-{_SIBLING}") not in withheld
    assert {("audible", f"audible-{_CARRIED}")} == withheld
    assert {_SIBLING} == _asin_values(report)
    assert ProposalOutcome.KEEP is _asin_outcome(report)
    assert admitted_recording_asin(report) is None


def test_an_admitted_owner_edition_stops_an_unverified_carry() -> None:
    claims = _owner_claims()
    report = _report(
        claims,
        (
            _catalogue(
                claims,
                carried=ProviderStatus.UNAVAILABLE,
                search=(_candidate("audible", (_SIBLING,)),),
                asin=_SIBLING,
            ),
        ),
        asin=_SIBLING,
    )

    assert admitted_recording_asin(report) is None
    assert ProposalOutcome.KEEP is _asin_outcome(report)


def test_an_owner_edition_nothing_admitted_does_not_stop_the_carry() -> None:
    claims = _owner_claims()
    report = _report(
        claims,
        (
            _catalogue(
                claims, carried=(_candidate("audible", (_CARRIED,)),), asin=_SIBLING
            ),
        ),
        asin=_SIBLING,
    )

    assert _CARRIED == admitted_recording_asin(report)


def test_a_differently_narrated_owner_edition_is_offered_beside_the_carried_one() -> (
    None
):
    claims = _owner_claims(narrator=None)
    report = _report(
        claims,
        (
            _catalogue(
                claims,
                carried=(_candidate("audible", (_CARRIED,)),),
                search=(_candidate("audible", (_SIBLING,), narrator="Someone Else"),),
                asin=_SIBLING,
            ),
        ),
        asin=_SIBLING,
    )

    assert frozenset() == carried_asin_precedence(report)
    assert {_CARRIED, _SIBLING} == _asin_values(report)
    assert admitted_recording_asin(report) is None


def test_a_narrator_disjoint_search_product_is_still_offered() -> None:
    claims = _claims(narrator=None)
    report = _report(
        claims,
        (
            _catalogue(
                claims,
                carried=(_candidate("audible", (_CARRIED,)),),
                search=(_candidate("audible", (_SIBLING,), narrator="Someone Else"),),
            ),
        ),
    )

    assert frozenset() == carried_asin_precedence(report)
    assert {_CARRIED, _SIBLING} == _asin_values(report)


def test_without_a_carried_lookup_both_editions_are_offered() -> None:
    claims = _asserted_claims(_CARRIED)
    report = _report(
        claims,
        (
            _catalogue(
                claims, search=(_candidate("audible", (_SIBLING,)),), asin=_CARRIED
            ),
            _fed(claims, _CARRIED, _candidate("enrichment", (_CARRIED,))),
        ),
        asin=_CARRIED,
    )

    assert frozenset() == carried_asin_precedence(report)
    assert {_CARRIED, _SIBLING} == _asin_values(report)


def test_a_recording_reached_through_the_carry_and_a_sibling_offers_the_carry() -> None:
    """The carried ASIN wins between sibling lookups of one recording (#1316).

    The catalogue's carried lookup is unavailable and its search finds the
    sibling, so the feed asks AudioSilo for both. AudioSilo resolves both to the
    one recording that states them, which verifies the carry; the recording
    offers the carried ASIN, and the withheld search sibling is not the only
    value lost.
    """
    claims = _claims()
    catalogue = _catalogue(
        claims,
        carried=ProviderStatus.UNAVAILABLE,
        search=(_candidate("audible", (_SIBLING,)),),
    )
    feed = audiobook_asin_feed_plan(_snapshot(), _plan(claims), (catalogue,))
    assert feed is not None
    assert {_CARRIED, _SIBLING} <= {lane.canonical_asin for lane in feed.lanes}
    recording = _candidate("silo", (_SIBLING, _CARRIED), identity="silo-recording")
    silo = provider_observation(
        "silo",
        tuple(
            _lane_result(feed, lane, "silo", "audiosilo", (recording,))
            for lane in feed.lanes
            if lane.canonical_asin in {_CARRIED, _SIBLING}
        ),
        authority="audiosilo",
    )
    report = _report(claims, (catalogue, silo))

    assert _CARRIED == admitted_recording_asin(report)
    assert ("audible", f"audible-{_SIBLING}") in carried_asin_precedence(report)
    assert {_CARRIED} == _asin_values(report)
    # Without a verified carry, two queried siblings still name no one edition.
    (observed,) = (value for value in report.providers if value.provider == "silo")
    (stated,) = observed.candidates
    assert candidate_edition_asin(observed, stated) is None
    assert _CARRIED == candidate_edition_asin(observed, stated, carried=_CARRIED)


def _silo_report(current: str | None) -> MetadataReport:
    """AudioSilo answers the carried lookup with both of one recording's ASINs."""
    claims = _claims()
    if current is not None:
        claims = (
            *claims,
            _local(
                ClaimKind.ASIN,
                current,
                0,
                basis="target_metadata",
                scope=ClaimScope.EDITION,
            ),
        )
    plan = _plan(claims, asin=current)
    feed = audiobook_asin_feed_plan(_snapshot(current), plan, ())
    assert feed is not None
    lane = next(value for value in feed.lanes if value.canonical_asin == _CARRIED)
    silo = provider_observation(
        "silo",
        (
            _lane_result(
                feed,
                lane,
                "silo",
                "audiosilo",
                (_candidate("silo", (_SIBLING, _CARRIED), identity="silo-recording"),),
            ),
        ),
        authority="audiosilo",
    )
    return _report(claims, (silo,), asin=current)


@pytest.mark.parametrize(
    ("current", "offered"),
    ((None, _CARRIED), (_SIBLING, _SIBLING), ("B0ELSEWHER", _CARRIED)),
    ids=(
        "no_current_asin_queried_one",
        "current_sibling_kept",
        "other_current_queried_one",
    ),
)
def test_sibling_asins_of_one_recording_offer_one_value(
    current: str | None, offered: str
) -> None:
    report = _silo_report(current)
    (candidate,) = report.providers[0].candidates
    assert 2 == sum(value.kind is ClaimKind.ASIN for value in candidate.claims)

    assert {offered} == _asin_values(report)
    assert _CARRIED == admitted_recording_asin(report)
