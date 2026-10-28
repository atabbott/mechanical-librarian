"""Band-agreeing audiobook products admitted through corroboration (#1307).

Exact work-identity admission refuses an Audible product whose title differs
from the asserted one however slightly, so a tight recording with an agreeing
narrator stayed a stub. These cases prove the corroborated admission rule, the
band-first set rule, the leading-number rung, the minute-floor reading of the
tight band, the lone-apostrophe fold and the retained refused products, from
the domain rules through the catalogue's ladder walk to report replay. Every
title, name and identifier here is synthetic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest

from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
    AudibleCatalogMetadataProvider,
)
from mechanical_librarian.application import metadata_report as m
from mechanical_librarian.application.provider_retry_successor import _retry_plan
from mechanical_librarian.domain.audiobook_corroboration import (
    CorroborationOutcome,
    band_first_candidate,
    corroborated_recording_candidate,
    runtime_agreement,
    runtime_corroborated_candidate,
    tight_runtime_distance,
)
from mechanical_librarian.domain.metadata_report import (
    AudioDiscoveryTitle,
    ClaimKind,
    ClaimScope,
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
    RecordingCorroboration,
    RecordingLookupHint,
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
    WorkDispatchLineage,
    canonical_json,
    default_target_fields,
    finalize_candidate_comparisons,
    provider_candidate_set_work_identity_admission,
)
from mechanical_librarian.domain.work_identity import (
    CORROBORATED_TITLE_PRODUCTION,
    WorkDispatchRung,
    WorkTitleTransformationFamily,
    replay_provider_work_identity_admission,
    work_query_ladder,
    work_title_extends,
    work_title_related,
)
from mechanical_librarian.domain.work_identity import (
    _closed_equal as closed_equal,
)
from tests.support.audiobook_providers import (
    audio_claim,
    local_runtime_claim,
    target_title_claim,
)
from tests.support.audiobook_reports import recorded_dispatch
from tests.support.metadata_report_doubles import single_track_audio_facts

_AUTHOR = "Ada Example"
_NARRATOR = "Nell Reader"
_OTHER_NARRATOR = "Otto Voice"
# One thousand minutes and a few seconds: 60 000 s states it exactly.
_RUNTIME = "60030.000"
_LOCAL = 60_030


# -- the leading-number rung -------------------------------------------------


def test_only_an_asserted_audiobook_ladder_strips_a_bare_leading_number() -> None:
    authors = (_AUTHOR,)
    plain = work_query_ladder("12 Synthetic Journey", authors, audiobook=True)
    stripped = work_query_ladder(
        "12 Synthetic Journey", authors, audiobook=True, leading_number=True
    )

    assert ["12 Synthetic Journey"] == [query.title for query in plain]
    assert stripped[: len(plain)] == plain
    assert ("Synthetic Journey", WorkDispatchRung.LEADING_NUMBER_STRIPPED) == (
        stripped[-1].title,
        stripped[-1].rung,
    )
    assert (WorkTitleTransformationFamily.LEADING_NUMBER,) == stripped[
        -1
    ].transformations
    # The lineage the rung records is one the persisted grammar accepts.
    assert WorkDispatchLineage.from_query(stripped[-1]).rung is (
        WorkDispatchRung.LEADING_NUMBER_STRIPPED
    )
    with pytest.raises(ValueError):
        work_query_ladder("12 Synthetic Journey", authors, leading_number=True)
    with pytest.raises(ValueError):
        WorkDispatchLineage(
            WorkDispatchRung.LEADING_NUMBER_STRIPPED,
            (WorkTitleTransformationFamily.SUBTITLE_SEGMENT,),
        )


@pytest.mark.parametrize(
    "title",
    (
        "Synthetic Journey",
        # A four-digit or a bare number is a title, not a position.
        "1984 Synthetic Journey",
        "12 34",
        # A separated position is the leading-sequence family's, already
        # stripped on the normalized rung.
        "12 - Synthetic Journey",
        "12. Synthetic Journey",
    ),
)
def test_no_leading_number_rung_without_a_bare_number(title: str) -> None:
    ladder = work_query_ladder(title, (_AUTHOR,), audiobook=True, leading_number=True)

    assert all(
        query.rung is not WorkDispatchRung.LEADING_NUMBER_STRIPPED for query in ladder
    )


# -- the title relation -------------------------------------------------------


@pytest.mark.parametrize(
    ("local", "title", "subtitle", "related"),
    (
        ("Synthetic Journey", "Synthetic Journey", None, True),
        ("The Synthetic Journey", "Synthetic Journey", None, True),
        (
            "Synthetic Journey: Into the Fold",
            "Synthetic Journey",
            "Into the Fold",
            True,
        ),
        ("03 Synthetic Journey", "Synthetic Journey", None, True),
        ("Synthetic Journey", "Synthetic Journey: Example Saga, Book 2", None, True),
        ("Synthetic Journey (Example Saga 2)", "Synthetic Journey", None, True),
        ("Synthétic Journey’s End", "Synthetic Journey's End", None, True),
        ("Synthetic Journey Home", "Synthetic Road Home", None, True),
        ("Synthetic Journey", "Distant Harbour", None, False),
        ("Synthetic Journey", "Synthetic Harbour Lights", None, False),
        ("The", "The", None, False),
    ),
)
def test_a_title_relation_needs_containment_a_join_or_half_the_tokens(
    local: str, title: str, subtitle: str | None, related: bool
) -> None:
    assert related is work_title_related(local, title, subtitle)


# -- the lone apostrophe ------------------------------------------------------


def test_a_lone_apostrophe_between_letters_is_an_apostrophe() -> None:
    assert closed_equal("Synthetic Journey’s End", "Synthetic Journey's End")
    assert closed_equal("O’Example", "O'Example")
    # A mark that is not between two letters still pairs or disagrees.
    assert not closed_equal("Synthetic Journey’", "Synthetic Journey'")
    assert not closed_equal("’Synthetic Journey", "'Synthetic Journey")
    assert not closed_equal("The ’Road‘", "The 'Road'")


# -- the minute floor --------------------------------------------------------


@pytest.mark.parametrize(
    ("local", "stated", "minutes", "distance"),
    (
        (60_030, 60_000, True, 0),
        (60_059, 60_000, True, 0),
        (60_149, 60_000, True, 90),
        (60_150, 60_000, True, 91),
        (59_910, 60_000, True, 90),
        (60_030, 60_000, False, 30),
        (60_149, 60_000, False, 149),
        # A value that is not a whole minute is read exactly.
        (60_149, 60_001, True, 148),
    ),
)
def test_a_minute_is_read_as_its_floor_for_the_tight_band(
    local: int, stated: int, minutes: bool, distance: int
) -> None:
    assert distance == tight_runtime_distance(local, stated, provider_minutes=minutes)


def test_the_floor_widens_only_the_tight_band_and_only_for_minutes() -> None:
    assert (
        CorroborationOutcome.CORROBORATED
        is runtime_agreement(60_140, 60_000, provider_minutes=True).outcome
    )
    assert (
        CorroborationOutcome.WEAKLY_CORROBORATED
        is runtime_agreement(60_140, 60_000).outcome
    )
    assert (
        CorroborationOutcome.CONTRADICTED
        is runtime_agreement(60_000 + 601, 60_000, provider_minutes=True).outcome
    )


def test_a_band_read_exactly_keeps_precedence_over_one_the_floor_brings_in() -> None:
    claims = (local_runtime_claim("60140.000"),)
    floor_only = _candidate("audible-product-B000000001", minutes=1_000)
    exact = _candidate("audible-product-B000000002", minutes=1_001)

    chosen = runtime_corroborated_candidate((floor_only, exact), claims)
    assert chosen is not None
    assert exact == chosen[0]
    alone = runtime_corroborated_candidate((floor_only,), claims)
    assert alone is not None
    assert floor_only == alone[0]


# -- the corroborated rule ----------------------------------------------------


def _claim(
    identity: str,
    order: int,
    kind: ClaimKind,
    value: str | None,
    scope: ClaimScope,
    role: str | None = None,
) -> ProviderClaim:
    return ProviderClaim.create(
        provider="audible",
        candidate=identity,
        kind=kind,
        value=value,
        state=FieldState.VALID if value is not None else FieldState.ABSENT,
        scope=scope,
        applicability="not_applicable",
        role=role,
        raw_order=order,
    )


def _candidate(
    identity: str = "audible-product-B000000001",
    *,
    title: str = "Synthetic Journey",
    subtitle: str | None = None,
    authors: tuple[str, ...] = (_AUTHOR,),
    narrators: tuple[str, ...] = (_NARRATOR,),
    series: tuple[tuple[str, str], ...] = (),
    minutes: int | None = 1_000,
    language: str | None = None,
) -> ProviderCandidate:
    asin = identity.rsplit("-", 1)[1]
    specifications: list[tuple[ClaimKind, str | None, ClaimScope, str | None]] = [
        (ClaimKind.ASIN, asin, ClaimScope.EDITION, None),
        (ClaimKind.TITLE, title, ClaimScope.WORK, None),
    ]
    if subtitle is not None:
        specifications.append((ClaimKind.SUBTITLE, subtitle, ClaimScope.WORK, None))
    specifications += [
        (ClaimKind.CREATOR, value, ClaimScope.WORK, "author") for value in authors
    ]
    specifications += [
        (ClaimKind.CONTRIBUTOR, value, ClaimScope.WORK, "narrator")
        for value in narrators
    ]
    for name, sequence in series:
        specifications += [
            (ClaimKind.SERIES, name, ClaimScope.WORK, None),
            (ClaimKind.SERIES_SEQUENCE, sequence, ClaimScope.WORK, None),
        ]
    if language is not None:
        specifications.append((ClaimKind.LANGUAGE, language, ClaimScope.EDITION, None))
    if minutes is not None:
        specifications.append(
            (ClaimKind.DURATION, f"{minutes * 60}.000", ClaimScope.EDITION, None)
        )
    return ProviderCandidate(
        identity,
        "unresolved",
        tuple(
            _claim(identity, order, kind, value, scope, role)
            for order, (kind, value, scope, role) in enumerate(specifications)
        ),
        (
            ProviderEvidence(
                ProviderEvidenceKind.ASIN_MARKETPLACE, "us", ClaimScope.EDITION
            ),
            ProviderEvidence(
                ProviderEvidenceKind.EDITION_WORK_RELATION,
                "unresolved",
                ClaimScope.EDITION,
            ),
        ),
    )


def _local_claims(
    *,
    narrator: str | None = None,
    series: tuple[str, str] | None = None,
    language: str | None = None,
) -> tuple[LocalClaim, ...]:
    claims: list[LocalClaim] = [local_runtime_claim(_RUNTIME)]
    if narrator is not None:
        claims.append(audio_claim(ClaimKind.CONTRIBUTOR, narrator, 3, role="narrator"))
    if series is not None:
        claims += [
            audio_claim(ClaimKind.SERIES, series[0], 4),
            audio_claim(ClaimKind.SERIES_SEQUENCE, series[1], 5),
        ]
    if language is not None:
        claims.append(audio_claim(ClaimKind.LANGUAGE, language, 6))
    return tuple(claims)


_HEARD = RecordingCorroboration((_NARRATOR,))


def _ruled(
    *candidates: ProviderCandidate,
    title: str = "Synthetic Journey: Into the Fold",
    claims: tuple[LocalClaim, ...] | None = None,
    corroboration: RecordingCorroboration = _HEARD,
) -> ProviderCandidate | None:
    return corroborated_recording_candidate(
        candidates,
        title,
        (_AUTHOR,),
        _local_claims() if claims is None else claims,
        corroboration,
    )


def test_a_tight_related_product_with_an_agreeing_narrator_is_admitted() -> None:
    product = _candidate(subtitle="Into the Fold")

    assert product == _ruled(product)
    # The narrator may come from the recording's own tags instead.
    assert product == _ruled(
        product,
        claims=_local_claims(narrator=_NARRATOR),
        corroboration=RecordingCorroboration(),
    )


def test_a_series_number_corroborates_where_no_narrator_is_known() -> None:
    product = _candidate(narrators=(), series=(("Example Saga", "2"),))
    by_target = RecordingCorroboration(series=(TargetSeries("The Example Saga", "2"),))

    assert product == _ruled(product, corroboration=by_target)
    assert product == _ruled(
        product,
        claims=_local_claims(series=("Example Saga Series", "2.0")),
        corroboration=RecordingCorroboration(),
    )
    assert None is _ruled(
        product,
        corroboration=RecordingCorroboration(
            series=(TargetSeries("Example Saga", "3"),)
        ),
    )


@pytest.mark.parametrize(
    ("product", "corroboration"),
    (
        # The title guard: a series neighbour with the same narrator and band.
        (_candidate(title="Distant Harbour"), _HEARD),
        # Runtime alone, with no narrator or series to agree.
        (_candidate(), RecordingCorroboration()),
        # A different narrator contradicts.
        (_candidate(narrators=(_OTHER_NARRATOR,)), _HEARD),
        # So does a different author.
        (_candidate(authors=("Zed Other",)), _HEARD),
        # Outside the tight band, even read as a floor.
        (_candidate(minutes=1_003), _HEARD),
        # No runtime at all.
        (_candidate(minutes=None), _HEARD),
        # A variant marker vetoes the product.
        (_candidate(title="Synthetic Journey (Abridged Edition)"), _HEARD),
        # A different series number contradicts an agreeing narrator.
        (
            _candidate(series=(("Example Saga", "3"),)),
            RecordingCorroboration((_NARRATOR,), (TargetSeries("Example Saga", "2"),)),
        ),
    ),
)
def test_the_rule_refuses_without_corroboration_or_with_a_contradiction(
    product: ProviderCandidate, corroboration: RecordingCorroboration
) -> None:
    assert None is _ruled(product, corroboration=corroboration)


def test_a_language_conflict_refuses_the_rule() -> None:
    product = _candidate(subtitle="Into the Fold", language="de")

    assert None is _ruled(product, claims=_local_claims(language="en"))
    assert product == _ruled(product, claims=_local_claims(language="de"))


def test_qualifying_editions_must_share_a_narrator_and_the_closest_wins() -> None:
    near = _candidate("audible-product-B000000002", minutes=1_000)
    far = _candidate("audible-product-B000000001", minutes=999)
    other = _candidate(
        "audible-product-B000000003", narrators=(f"{_NARRATOR}, {_OTHER_NARRATOR}",)
    )
    disjoint = _candidate("audible-product-B000000004", narrators=(_OTHER_NARRATOR,))

    assert near == _ruled(far, near, title="Synthetic Journey")
    assert near == _ruled(far, near, other, title="Synthetic Journey")
    # A contradicting product never qualifies, so it never splits the set.
    assert near == _ruled(near, disjoint, title="Synthetic Journey")
    # Two qualifying editions with disjoint casts are two recordings.
    split = _candidate(
        "audible-product-B000000005", narrators=(_OTHER_NARRATOR,), minutes=999
    )
    assert None is _ruled(
        near,
        split,
        title="Synthetic Journey",
        corroboration=RecordingCorroboration((_NARRATOR, _OTHER_NARRATOR)),
    )


# -- the band-first set rule --------------------------------------------------


def test_band_first_keeps_the_one_tight_product_beside_other_cuts() -> None:
    tight = _candidate("audible-product-B000000001")
    outside = _candidate("audible-product-B000000002", minutes=1_200)
    loose = _candidate("audible-product-B000000003", minutes=1_005)
    claims = _local_claims()

    assert tight == band_first_candidate(
        (tight, outside), "Synthetic Journey", claims, _HEARD
    )
    # A product inside the loose band may be the same recording re-cut.
    assert None is band_first_candidate(
        (tight, outside, loose), "Synthetic Journey", claims, _HEARD
    )
    assert None is band_first_candidate(
        (tight, _candidate("audible-product-B000000004")),
        "Synthetic Journey",
        claims,
        _HEARD,
    )
    assert None is band_first_candidate(
        (
            _candidate("audible-product-B000000005", narrators=(_OTHER_NARRATOR,)),
            outside,
        ),
        "Synthetic Journey",
        claims,
        _HEARD,
    )


# -- set admission ------------------------------------------------------------

_DECORATED = "Synthetic Journey: Into the Fold"
_DECORATED_LADDER = tuple(
    WorkDispatchLineage.from_query(query)
    for query in work_query_ladder(
        _DECORATED, (_AUTHOR,), audiobook=True, leading_number=True
    )
)


def _set(
    *candidates: ProviderCandidate,
    title: str = _DECORATED,
    lineage: WorkDispatchLineage = _DECORATED_LADDER[0],
    corroboration: RecordingCorroboration | None = _HEARD,
) -> Any:
    return provider_candidate_set_work_identity_admission(
        candidates,
        title,
        (_AUTHOR,),
        lineage,
        audiobook=True,
        band_claims=_local_claims(),
        corroboration=corroboration,
    )


def test_the_rule_admits_only_a_set_the_exact_path_refused() -> None:
    joined = _candidate(subtitle="Into the Fold")
    admitted = _set(joined)

    assert admitted.admitted
    assert (joined,) == admitted.candidates
    assert CORROBORATED_TITLE_PRODUCTION == admitted.transformation.production
    assert not _set(joined, corroboration=None).admitted
    # An exact title keeps its own admission and lineage.
    exact = _candidate("audible-product-B000000002", title=_DECORATED)
    assert _set(exact).transformation is None
    # With an exact product beside it, the rule is never read.
    assert not _set(
        exact, _candidate("audible-product-B000000003", title=_DECORATED)
    ).admitted


def test_band_first_replays_as_the_one_product_it_keeps() -> None:
    tight = _candidate("audible-product-B000000001", title=_DECORATED)
    outside = _candidate("audible-product-B000000002", title=_DECORATED, minutes=1_200)
    admitted = _set(tight, outside)

    assert admitted.admitted
    assert (tight,) == admitted.candidates
    assert admitted.transformation is None
    replayed = _set(tight, corroboration=None)
    assert replayed.admitted and replayed.transformation is None


# -- the catalogue's ladder walk and the report ------------------------------


def _target_fields(title: str, narrators: tuple[str, ...]) -> tuple[TargetField, ...]:
    overrides = {
        TargetDestination.AUTHORS: TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, (_AUTHOR,)
        ),
    }
    if narrators:
        overrides[TargetDestination.NARRATORS] = TargetField(
            TargetDestination.NARRATORS, TargetFieldState.VALUE, narrators
        )
    return tuple(
        overrides.get(field.destination, field)
        for field in default_target_fields(title)
    )


def _plan(
    title: str, narrators: tuple[str, ...] = (_NARRATOR,)
) -> m.MetadataDiscoveryPlan:
    return m.audiobook_discovery_plan(
        m.TargetSnapshot(
            "audiobook", title, (), fields=_target_fields(title, narrators)
        ),
        (target_title_claim(title), local_runtime_claim(_RUNTIME)),
        audio=single_track_audio_facts(),
    )


def _product(
    index: int,
    title: str,
    minutes: int = 1_000,
    *,
    subtitle: str | None = None,
    narrator: str = _NARRATOR,
) -> dict[str, Any]:
    product: dict[str, Any] = {
        "asin": f"B0{index:08d}",
        "title": title,
        "authors": [{"name": _AUTHOR}],
        "narrators": [{"name": narrator}],
        "runtime_length_min": minutes,
    }
    if subtitle is not None:
        product["subtitle"] = subtitle
    return product


def _walk(
    plan: m.MetadataDiscoveryPlan, *pages: list[dict[str, Any]]
) -> tuple[ProviderObservation, list[httpx.URL]]:
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        products = pages[len(seen) - 1] if len(seen) <= len(pages) else []
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"total_results": len(products), "products": products},
        )

    provider = AudibleCatalogMetadataProvider(
        "audible",
        httpx.Client(transport=httpx.MockTransport(handle)),
        262_144,
        5.0,
        marketplaces=("us",),
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda value: None,
        jitter=lambda: 0.0,
    )
    try:
        result = m.observe_provider("audible", "audible_catalog", provider, plan)
    finally:
        provider._client.close()
    return result.observation, seen


def _report(
    plan: m.MetadataDiscoveryPlan,
    title: str,
    observation: ProviderObservation,
    narrators: tuple[str, ...] = (_NARRATOR,),
) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "audio", "item-1", _target_fields(title, narrators)
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=plan.local_claims,
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=single_track_audio_facts(),
    )


def _readdressed(data: dict[str, Any]) -> str:
    data.pop("report_id")
    data["report_id"] = (
        "report_" + hashlib.sha256(canonical_json(data).encode()).hexdigest()
    )
    return canonical_json(data)


def test_a_joined_title_admits_its_tight_narrated_recording_after_the_walk() -> None:
    plan = _plan(_DECORATED)
    observation, seen = _walk(
        plan,
        [_product(1, "Synthetic Journey", subtitle="Into the Fold")],
        [_product(1, "Synthetic Journey", subtitle="Into the Fold")],
    )
    (lane,) = observation.lanes

    # Both rungs were asked before the rule read the first one's page.
    assert ["Synthetic Journey: Into the Fold", "Synthetic Journey"] == [
        url.params["title"] for url in seen
    ]
    assert ProviderStatus.CANDIDATES is lane.status
    assert _DECORATED_LADDER[0] == lane.dispatch_lineage
    assert lane.transformation_lineage is not None
    assert CORROBORATED_TITLE_PRODUCTION == lane.transformation_lineage.production
    assert lane.dispatch is not None
    assert 2 == lane.dispatch.rungs
    report = _report(plan, _DECORATED, observation)
    assert report == MetadataReport.from_json(report.to_json())
    assert ProviderEvidenceKind.ASIN_MARKETPLACE in {
        value.kind for value in report.providers[0].candidates[0].evidence
    }

    # Replay reads the narrator off the target: without it, the lineage is
    # forged.
    data = json.loads(report.to_json())
    for field in data["target"]["fields"]:
        if field["destination"] == "narrators":
            field["value"] = [_OTHER_NARRATOR]
    with pytest.raises(ReportValueError, match="lineage is inconsistent"):
        MetadataReport.from_json(_readdressed(data))


def test_an_exact_admission_on_a_later_rung_comes_before_the_rule() -> None:
    plan = _plan(_DECORATED)
    observation, _ = _walk(
        plan,
        [_product(1, "Synthetic Journey", subtitle="Into the Fold")],
        # The second rung asks the plain title and hears the full one back.
        [_product(2, _DECORATED)],
    )
    (lane,) = observation.lanes

    assert _DECORATED_LADDER[1] == lane.dispatch_lineage
    assert lane.transformation_lineage is None
    assert {"audible-product-B000000002"} == {
        value.identity for value in observation.candidates
    }
    _report(plan, _DECORATED, observation)


def test_a_tight_related_product_without_corroboration_stays_refused() -> None:
    plan = _plan(_DECORATED, narrators=())
    observation, _ = _walk(
        plan, [_product(1, "Synthetic Journey", subtitle="Into the Fold")], []
    )
    (lane,) = observation.lanes

    assert ProviderStatus.NO_MATCH is lane.status
    assert lane.dispatch is not None
    assert 2 == lane.dispatch.rungs
    assert {"B000000001"} == {hint.asin for hint in lane.recording_lookup_hints}
    _report(plan, _DECORATED, observation, narrators=())


def test_a_bare_leading_number_is_asked_again_without_it() -> None:
    title = "12 Synthetic Journey"
    plan = _plan(title)
    observation, seen = _walk(plan, [], [_product(1, "Synthetic Journey")])
    (lane,) = observation.lanes

    assert ["12 Synthetic Journey", "Synthetic Journey"] == [
        url.params["title"] for url in seen
    ]
    assert ProviderStatus.CANDIDATES is lane.status
    assert lane.dispatch_lineage is not None
    assert WorkDispatchRung.LEADING_NUMBER_STRIPPED is lane.dispatch_lineage.rung
    assert lane.dispatch is not None
    assert 2 == lane.dispatch.rungs
    report = _report(plan, title, observation)
    assert report == MetadataReport.from_json(report.to_json())


def test_two_cuts_of_one_title_admit_the_tight_one_band_first() -> None:
    plan = _plan(_DECORATED)
    observation, _ = _walk(
        plan,
        [_product(1, _DECORATED), _product(2, _DECORATED, minutes=1_200)],
        [],
    )

    assert {"audible-product-B000000001"} == {
        value.identity for value in observation.candidates
    }
    assert observation.lanes[0].transformation_lineage is None
    _report(plan, _DECORATED, observation)


def test_the_leading_number_rung_stays_inside_the_dispatch_bound() -> None:
    plan = _plan("12 Synthetic Journey's End: Into the Fold")
    (lane,) = plan.lanes

    assert 4 == len(
        work_query_ladder(
            lane.title or "", lane.authors, audiobook=True, leading_number=True
        )
    )
    assert m.MAX_PROVIDER_DISCOVERY_QUERIES >= 4 + 3 + 1


# -- refused products ---------------------------------------------------------


def _fed_report(
    product: ProviderCandidate, *, title: str = "Synthetic Journey"
) -> MetadataReport:
    asin = product.identity.rsplit("-", 1)[1]
    asserted = LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed=asin,
        canonical=asin,
        excerpt=asin,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    fields = tuple(
        TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, asin)
        if field.destination is TargetDestination.ASIN
        else field
        for field in _target_fields(title, (_NARROWED_NARRATOR,))
    )
    snapshot = m.TargetSnapshot("audiobook", title, (), fields=fields)
    claims = (target_title_claim(title), asserted, local_runtime_claim(_RUNTIME))
    plan = m.audiobook_discovery_plan(
        snapshot, claims, audio=single_track_audio_facts()
    )
    feed = m.audiobook_asin_feed_plan(snapshot, plan, ())
    assert feed is not None
    (lane,) = feed.lanes
    qualified = m.qualify_provider_observation(
        ProviderObservation(
            "enrichment",
            ProviderStatus.CANDIDATES,
            (),
            (finalize_candidate_comparisons(product, claims, "enrichment"),),
            ProviderQueryKind.ASIN,
            "audnexus",
        ),
        m.MetadataLookup(
            ProviderQueryKind.ASIN,
            feed.local_claims,
            audio=feed.audio,
            work_title=feed.work_title,
            work_authors=feed.work_authors,
            requested_asin=lane.canonical_asin,
            recording_corroboration=feed.recording_corroboration,
        ),
    )
    observation = m.provider_observation(
        "enrichment",
        (
            m.ProviderLaneResult(
                lane,
                qualified.status,
                qualified.candidates,
                refused_products=qualified.refused_products,
            ),
        ),
        authority="audnexus",
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(recorded_dispatch(observation),),
        audio=single_track_audio_facts(),
    )


_NARROWED_NARRATOR = _NARRATOR


def _enrichment(identity: str, title: str) -> ProviderCandidate:
    product = _candidate(identity, title=title)
    return replace(
        product,
        claims=tuple(
            ProviderClaim.create(
                provider="enrichment",
                candidate=identity,
                kind=claim.kind,
                value=claim.value,
                state=claim.state,
                scope=claim.scope,
                applicability=claim.applicability,
                role=claim.role,
                raw_order=claim.raw_order,
            )
            for claim in product.claims
        ),
    )


def test_a_refused_fed_product_keeps_its_claims_beside_its_stub() -> None:
    report = _fed_report(_enrichment("enrichment-B000000007", "Distant Harbour"))
    (provider,) = report.providers
    (stub,) = provider.candidates
    (refused,) = provider.refused_products

    assert {ClaimKind.ASIN} == {claim.kind for claim in stub.claims}
    assert stub.identity == refused.identity
    assert {ClaimKind.ASIN, ClaimKind.TITLE, ClaimKind.DURATION} <= {
        claim.kind for claim in refused.claims
    }
    assert () == refused.evidence
    assert report == MetadataReport.from_json(report.to_json())

    # A refused product never names a product the report did not cut to a stub.
    data = json.loads(report.to_json())
    data["providers"][0]["refused_products"][0]["identity"] = "enrichment-B000000008"
    with pytest.raises(ReportValueError):
        MetadataReport.from_json(_readdressed(data))


def test_an_admitted_fed_product_retains_nothing_refused() -> None:
    report = _fed_report(_enrichment("enrichment-B000000007", "Synthetic Journey"))

    assert () == report.providers[0].refused_products
    assert {ClaimKind.TITLE} <= {
        claim.kind for claim in report.providers[0].candidates[0].claims
    }


def _carried_report(product: ProviderCandidate, narrators: tuple[str, ...]) -> Any:
    asin = product.identity.rsplit("-", 1)[1]
    prior = LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed=asin,
        canonical=asin,
        excerpt=asin,
        applicability="not_applicable",
        basis="prior_admission",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    claims = (target_title_claim(_DECORATED), prior, local_runtime_claim(_RUNTIME))
    fields = _target_fields(_DECORATED, narrators)
    plan = m.audiobook_carried_plan(
        m.audiobook_discovery_plan(
            m.TargetSnapshot("audiobook", _DECORATED, (), fields=fields),
            claims,
            audio=single_track_audio_facts(),
        )
    )
    results = []
    for lane in plan.lanes:
        if lane.kind is not ProviderQueryKind.ASIN:
            results.append(m.ProviderLaneResult(lane, ProviderStatus.NO_MATCH))
            continue
        qualified = m.qualify_provider_observation(
            ProviderObservation(
                "audible",
                ProviderStatus.CANDIDATES,
                (),
                (finalize_candidate_comparisons(product, claims, "audible"),),
                ProviderQueryKind.ASIN,
                "audible_catalog",
            ),
            m.MetadataLookup(
                ProviderQueryKind.ASIN,
                plan.local_claims,
                audio=plan.audio,
                work_title=plan.work_title,
                work_authors=plan.work_authors,
                requested_asin=lane.canonical_asin,
                recording_corroboration=plan.recording_corroboration,
            ),
        )
        results.append(
            m.ProviderLaneResult(
                lane,
                qualified.status,
                qualified.candidates,
                refused_products=qualified.refused_products,
            )
        )
    observation = m.provider_observation(
        "audible", tuple(results), authority="audible_catalog"
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(recorded_dispatch(observation),),
        audio=single_track_audio_facts(),
    )


def test_a_carried_edition_the_rule_admitted_is_verified_by_the_rule() -> None:
    from mechanical_librarian.domain.audiobook_corroboration import (
        admitted_recording_asin,
    )

    product = _candidate(subtitle="Into the Fold")
    verified = _carried_report(product, (_NARRATOR,))

    assert "B000000001" == admitted_recording_asin(verified)
    assert () == verified.providers[0].refused_products
    # Without the narrator to agree, the lookup is a stub and the edition is
    # refuted rather than carried again.
    refuted = _carried_report(product, ())
    assert admitted_recording_asin(refuted) is None
    assert ("audible-product-B000000001",) == tuple(
        value.identity for value in refuted.providers[0].refused_products
    )


# -- review regressions -------------------------------------------------------


def _initialled(identity: str, minutes: int) -> ProviderCandidate:
    # "J. R. Example" admits "J.R. Example" only through the band's initials
    # fold, so only a tight product admits at all.
    return _candidate(
        identity, title="Plain Work", authors=("J. R. Example",), minutes=minutes
    )


def test_the_minute_floor_never_undoes_an_exact_band_admission() -> None:
    lineage = WorkDispatchLineage.from_query(
        work_query_ladder("Plain Work", ("J.R. Example",), audiobook=True)[0]
    )
    claims = (local_runtime_claim("60140.000"),)
    exact = _initialled("audible-product-B000000001", 1_001)  # 80 s exactly
    floor = _initialled("audible-product-B000000002", 1_000)  # 140 s, 81 as floor

    def admit(*candidates: ProviderCandidate) -> Any:
        return provider_candidate_set_work_identity_admission(
            candidates,
            "Plain Work",
            ("J.R. Example",),
            lineage,
            audiobook=True,
            band_claims=claims,
        )

    assert (exact,) == admit(exact).candidates
    # Read as a floor both are tight, but the exact reading admitted first.
    assert (exact,) == admit(exact, floor).candidates
    # The floor alone still admits where the exact band admits nothing.
    assert (floor,) == admit(floor).candidates


def test_a_hint_the_exact_band_kept_is_never_displaced_by_the_floor() -> None:
    claims = (local_runtime_claim("60100.000"),)
    floors = [
        RecordingLookupHint(f"B00000000{index}", 60_000, _DECORATED_LADDER[0])
        for index in (1, 2)
    ]
    exact = RecordingLookupHint("B000000003", 60_060, _DECORATED_LADDER[1])
    final = ProviderObservation(
        "audible", ProviderStatus.NO_MATCH, authority="audible_catalog"
    )

    kept = m._bounded_lookup_hints([*floors, exact], final, claims)

    assert m.MAX_RECORDING_LOOKUP_HINTS == len(kept)
    assert exact in kept and floors[0] in kept


def _alternative_plan() -> m.MetadataDiscoveryPlan:
    return m.audiobook_discovery_plan(
        m.TargetSnapshot(
            "audiobook",
            _DECORATED,
            (),
            fields=_target_fields(_DECORATED, (_NARRATOR,)),
        ),
        (target_title_claim(_DECORATED), local_runtime_claim(_RUNTIME)),
        audio=_alternative_audio(),
    )


def _alternative_audio() -> Any:
    return replace(
        single_track_audio_facts(),
        discovery_titles=(AudioDiscoveryTitle("path_leaf", "Distant Harbour"),),
    )


def _walk_by_title(
    plan: m.MetadataDiscoveryPlan, pages: dict[str, list[dict[str, Any]]]
) -> tuple[ProviderObservation, list[str]]:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        title = request.url.params.get("title") or ""
        seen.append(title)
        products = pages.get(title, [])
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"total_results": len(products), "products": products},
        )

    provider = AudibleCatalogMetadataProvider(
        "audible",
        httpx.Client(transport=httpx.MockTransport(handle)),
        262_144,
        5.0,
        marketplaces=("us",),
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda value: None,
        jitter=lambda: 0.0,
    )
    try:
        result = m.observe_provider("audible", "audible_catalog", provider, plan)
    finally:
        provider._client.close()
    return result.observation, seen


def _alternative_report(observation: ProviderObservation) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts(
            "primary", "audio", "item-1", _target_fields(_DECORATED, (_NARRATOR,))
        ),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=(target_title_claim(_DECORATED), local_runtime_claim(_RUNTIME)),
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=_alternative_audio(),
    )


def test_an_exact_alternative_lane_admission_comes_before_the_rule() -> None:
    plan = _alternative_plan()
    joined = [_product(1, "Synthetic Journey", subtitle="Into the Fold")]
    observation, seen = _walk_by_title(
        plan,
        {
            _DECORATED: joined,
            "Synthetic Journey": joined,
            "Distant Harbour": [_product(2, "Distant Harbour")],
        },
    )

    # The rule waits, so the alternative lane is asked and its exact answer
    # stands; the asserted lane stays exhausted.
    assert [_DECORATED, "Synthetic Journey", "Distant Harbour"] == seen
    assert {"audible-product-B000000002"} == {
        value.identity for value in observation.candidates
    }
    assert [ProviderStatus.NO_MATCH, ProviderStatus.CANDIDATES] == [
        lane.status for lane in observation.lanes
    ]
    report = _alternative_report(observation)
    assert report == MetadataReport.from_json(report.to_json())


def test_the_rule_reads_the_asserted_lane_once_the_alternative_refused() -> None:
    plan = _alternative_plan()
    joined = [_product(1, "Synthetic Journey", subtitle="Into the Fold")]
    observation, seen = _walk_by_title(
        plan, {_DECORATED: joined, "Synthetic Journey": joined}
    )

    assert [_DECORATED, "Synthetic Journey", "Distant Harbour"] == seen
    asserted, alternative = observation.lanes
    assert ProviderStatus.CANDIDATES is asserted.status
    assert asserted.transformation_lineage is not None
    assert CORROBORATED_TITLE_PRODUCTION == asserted.transformation_lineage.production
    assert asserted.dispatch is not None
    assert 2 == asserted.dispatch.rungs
    assert ProviderStatus.NO_MATCH is alternative.status
    report = _alternative_report(observation)
    assert report == MetadataReport.from_json(report.to_json())

    # A rule admission records every rung the walk sent.
    data = json.loads(report.to_json())
    for lane in data["providers"][0]["lanes"]:
        if lane["status"] == "candidates":
            lane["dispatch"]["rungs"] = 1
    with pytest.raises(ReportValueError, match="rungs do not replay"):
        MetadataReport.from_json(_readdressed(data))


def test_a_series_matches_only_by_its_normalized_name() -> None:
    product = _candidate(narrators=(), series=(("Darkness Cycle", "2"),))

    assert None is _ruled(
        product,
        corroboration=RecordingCorroboration(series=(TargetSeries("Dark", "2"),)),
    )
    assert product == _ruled(
        product,
        corroboration=RecordingCorroboration(
            series=(TargetSeries("The Darkness Cycle", "2"),)
        ),
    )


def test_a_series_contradiction_on_any_pair_wins_over_an_agreement() -> None:
    product = _candidate(series=(("Example Saga", "2"),))

    assert None is _ruled(
        product,
        corroboration=RecordingCorroboration(
            (_NARRATOR,),
            (TargetSeries("Example Saga", "2"), TargetSeries("Example Saga", "3")),
        ),
    )


def test_an_unnarrated_qualifier_does_not_pass_beside_a_narrated_one() -> None:
    corroboration = RecordingCorroboration(
        (_NARRATOR,), (TargetSeries("Example Saga", "2"),)
    )
    narrated = _candidate("audible-product-B000000001", series=(("Example Saga", "2"),))
    unnarrated = _candidate(
        "audible-product-B000000002",
        narrators=(),
        series=(("Example Saga", "2"),),
        minutes=1_001,
    )

    assert narrated == _ruled(narrated, corroboration=corroboration)
    assert unnarrated == _ruled(unnarrated, corroboration=corroboration)
    assert None is _ruled(narrated, unnarrated, corroboration=corroboration)


def test_a_title_running_on_past_the_local_one_needs_its_series_to_agree() -> None:
    neighbour = _candidate(
        title="Synthetic Journey Home", series=(("Example Saga", "2"),)
    )

    assert work_title_extends("Synthetic Journey", "Synthetic Journey Home")
    assert not work_title_extends("Synthetic Journey", "Synthetic Journey: A Novel")
    # Same author, narrator and band, and no local series: refused.
    assert None is _ruled(neighbour, title="Synthetic Journey")
    # An agreeing series position is what separates the same book.
    assert neighbour == _ruled(
        neighbour,
        title="Synthetic Journey",
        corroboration=RecordingCorroboration(
            (_NARRATOR,), (TargetSeries("Example Saga", "2"),)
        ),
    )


def test_an_overlapping_series_neighbour_is_a_documented_residual() -> None:
    # Pinned residual risk (ADR-0067, #1307): two titles sharing over half
    # their words, with the same author, narrator and band and no local series
    # to contradict, are related. No guard refuses this without also refusing
    # a measured rescue of the same shape.
    neighbour = _candidate(
        title="Example and the Iron Gate", series=(("Example Saga", "2"),)
    )

    assert neighbour == _ruled(neighbour, title="Example and the Silver Gate")
    assert None is _ruled(
        neighbour,
        title="Example and the Silver Gate",
        corroboration=RecordingCorroboration(
            (_NARRATOR,), (TargetSeries("Example Saga", "1"),)
        ),
    )


def test_an_unknown_local_language_cannot_contradict() -> None:
    # Pinned (ADR-0067): only a language the file states contradicts. An
    # edition in another language still needs a narrator or series agreement.
    product = _candidate(narrators=(), series=(("Example Saga", "2"),), language="de")
    by_series = RecordingCorroboration(series=(TargetSeries("Example Saga", "2"),))

    assert product == _ruled(product, corroboration=by_series)
    assert None is _ruled(
        product, claims=_local_claims(language="en"), corroboration=by_series
    )
    assert None is _ruled(product, corroboration=RecordingCorroboration())


def test_only_the_asserted_audible_lane_replays_the_leading_number_rung() -> None:
    lineage = WorkDispatchLineage.from_query(
        work_query_ladder(
            "12 Synthetic Journey", (_AUTHOR,), audiobook=True, leading_number=True
        )[-1]
    )

    def replay(*, leading_number: bool) -> bool:
        return replay_provider_work_identity_admission(
            local_title="12 Synthetic Journey",
            local_authors=(_AUTHOR,),
            # Admission compares the asserted title, whatever the rung asked.
            provider_title="12 Synthetic Journey",
            provider_authors=(_AUTHOR,),
            provider_work_identity="audible-product-B000000001",
            dispatch_lineage=lineage,
            audiobook=True,
            leading_number=leading_number,
        ).admitted

    assert WorkDispatchRung.LEADING_NUMBER_STRIPPED is lineage.rung
    assert replay(leading_number=True)
    assert not replay(leading_number=False)


def test_refused_product_comparison_links_replay() -> None:
    report = _fed_report(_enrichment("enrichment-B000000007", "Distant Harbour"))
    data = json.loads(report.to_json())
    claims = data["providers"][0]["refused_products"][0]["claims"]
    assert any(claim["links"] for claim in claims)
    for claim in claims:
        claim["links"] = []

    with pytest.raises(ReportValueError, match="comparison links are inconsistent"):
        MetadataReport.from_json(_readdressed(data))


@pytest.mark.parametrize(
    ("local", "title", "subtitle"),
    (
        # A provider subtitle that names something new.
        ("Synthetic Journey", "Synthetic Journey: The Return", None),
        ("Synthetic Journey", "Synthetic Journey", "The Return"),
        # The local title is the longer one.
        ("Synthetic Journey Home", "Synthetic Journey", None),
        ("Synthetic Journey: Into the Fold", "Synthetic Journey", None),
        # A number or numeral after the shorter title marks a sequel.
        ("Synthetic Journey", "Synthetic Journey 2", None),
        ("Synthetic Journey", "Synthetic Journey II", None),
        ("Synthetic Journey", "Synthetic Journey: Volume 2", None),
        ("Synthetic Journey", "Synthetic Journey", "Example Saga, Book 2"),
        ("Synthetic Journey 2", "Synthetic Journey", None),
        # Words that only spell a numeral are words.
        ("Synthetic Journey", "Synthetic Journey Mix", None),
        ("Synthetic Journey", "Mi Synthetic Journey", None),
        ("Synthetic Journey", "Synthetic Journey I", None),
    ),
)
def test_a_title_extended_on_either_side_needs_its_series_to_agree(
    local: str, title: str, subtitle: str | None
) -> None:
    product = _candidate(
        title=title, subtitle=subtitle, series=(("Example Saga", "2"),)
    )

    assert work_title_extends(local, title, subtitle)
    assert None is _ruled(product, title=local)
    assert product == _ruled(
        product,
        title=local,
        corroboration=RecordingCorroboration(
            (_NARRATOR,), (TargetSeries("Example Saga", "2"),)
        ),
    )


@pytest.mark.parametrize(
    ("local", "title", "subtitle"),
    (
        ("Synthetic Journey", "Synthetic Journey: A Novel", None),
        ("Synthetic Journey", "Synthetic Journey", "Example Saga"),
        ("12 Synthetic Journey", "Synthetic Journey", None),
        ("Synthetic Journey", "12 Synthetic Journey", None),
        ("Synthetic Journey (Unabridged)", "Synthetic Journey", None),
    ),
)
def test_decoration_does_not_extend_a_title(
    local: str, title: str, subtitle: str | None
) -> None:
    assert not work_title_extends(local, title, subtitle, decorations=("Example Saga",))


def test_a_leading_number_that_is_the_series_position_extends_a_title() -> None:
    # "12 X" against "X" stays decoration, but not when 12 is where the
    # product sits in its own series: then it reads as "X, part 12".
    product = _candidate(title="12 Synthetic Journey", series=(("Example Saga", "12"),))

    assert not work_title_extends("Synthetic Journey", "12 Synthetic Journey")
    assert work_title_extends(
        "Synthetic Journey", "12 Synthetic Journey", sequences=("12",)
    )
    assert None is _ruled(product, title="Synthetic Journey")


def test_the_feed_takes_an_exactly_tight_hint_before_a_floor_only_one() -> None:
    title = "Synthetic Journey"
    asserted, carried = "B000000008", "B000000009"
    claims = (
        target_title_claim(title),
        *(
            LocalClaim.create(
                kind=ClaimKind.ASIN,
                observed=asin,
                canonical=asin,
                excerpt=asin,
                applicability="not_applicable",
                basis=basis,
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.EDITION,
            )
            for asin, basis in (
                (asserted, "target_metadata"),
                (carried, "prior_admission"),
            )
        ),
        local_runtime_claim("60100.000"),
    )
    fields = tuple(
        TargetField(TargetDestination.ASIN, TargetFieldState.VALUE, asserted)
        if field.destination is TargetDestination.ASIN
        else field
        for field in _target_fields(title, (_NARRATOR,))
    )
    snapshot = m.TargetSnapshot("audiobook", title, (), fields=fields)
    plan = m.audiobook_discovery_plan(
        snapshot, claims, audio=single_track_audio_facts()
    )
    # Unrelated titles, so admission and the rule refuse them all: 1,000
    # minutes is 100 s short (tight only as a floor), 1,001 is 40 s short.
    floor_only, _ = _walk(
        plan,
        [_product(1, "Other Book One"), _product(2, "Other Book Two")],
        [],
    )
    exact, _ = _walk(plan, [_product(3, "Other Book Three", minutes=1_001)], [])
    assert {"B000000001", "B000000002"} == {
        hint.asin for hint in floor_only.lanes[0].recording_lookup_hints
    }

    feed = m.audiobook_asin_feed_plan(snapshot, plan, (floor_only, exact))

    assert feed is not None
    fed = {lane.canonical_asin for lane in feed.lanes}
    assert {asserted, carried, "B000000003"} <= fed
    assert m.MAX_ASIN_DISCOVERY_LANES == len(fed)


def test_a_retry_of_the_asserted_lane_alone_cannot_admit_by_rule() -> None:
    plan = _alternative_plan()
    (asserted,) = (lane for lane in plan.lanes if lane.title_basis is None)
    joined = [_product(1, "Synthetic Journey", subtitle="Into the Fold")]
    pages = {_DECORATED: joined, "Synthetic Journey": joined}
    original = m.provider_observation(
        "audible",
        (m.ProviderLaneResult(asserted, ProviderStatus.UNAVAILABLE),),
        authority="audible_catalog",
    )

    # The successor narrows the plan to the failed lane and, since the
    # suppressed alternative lane was never asked, drops the rule with it.
    retry = _retry_plan(plan, (asserted,), original)
    assert retry.recording_corroboration is None
    observation, _ = _walk_by_title(retry, pages)
    assert [ProviderStatus.NO_MATCH] == [lane.status for lane in observation.lanes]

    # A report that admits by rule beside an alternative lane never asked is
    # refused, so no merge can carry one.
    unsafe = replace(plan, lanes=(asserted,))
    admitted, _ = _walk_by_title(unsafe, pages)
    assert ProviderStatus.CANDIDATES is admitted.lanes[0].status
    with pytest.raises(ReportValueError, match="precedes an exact path"):
        _alternative_report(admitted)


def test_a_retry_keeps_the_rule_once_the_alternative_lane_refused() -> None:
    plan = _alternative_plan()
    asserted, alternative = sorted(
        plan.lanes, key=lambda lane: lane.title_basis is not None
    )
    original = m.provider_observation(
        "audible",
        (
            m.ProviderLaneResult(asserted, ProviderStatus.UNAVAILABLE),
            m.ProviderLaneResult(alternative, ProviderStatus.NO_MATCH),
        ),
        authority="audible_catalog",
    )

    assert _retry_plan(plan, (asserted,), original).recording_corroboration == (
        plan.recording_corroboration
    )
    assert _retry_plan(plan, (asserted, alternative), original).recording_corroboration


def test_an_exact_carried_lane_admission_comes_before_the_rule() -> None:
    prior = LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed="B000000009",
        canonical="B000000009",
        excerpt="B000000009",
        applicability="not_applicable",
        basis="prior_admission",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    claims = (target_title_claim(_DECORATED), prior, local_runtime_claim(_RUNTIME))
    fields = _target_fields(_DECORATED, (_NARRATOR,))
    plan = m.audiobook_carried_plan(
        m.audiobook_discovery_plan(
            m.TargetSnapshot("audiobook", _DECORATED, (), fields=fields),
            claims,
            audio=single_track_audio_facts(),
        )
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if "title" in request.url.params:
            products = (
                [_product(1, "Synthetic Journey", subtitle="Into the Fold")]
                if request.url.params["title"] == _DECORATED
                else []
            )
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"total_results": len(products), "products": products},
            )
        product = _product(9, _DECORATED, minutes=999)
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json={"product": product}
        )

    provider = AudibleCatalogMetadataProvider(
        "audible",
        httpx.Client(transport=httpx.MockTransport(handle)),
        262_144,
        5.0,
        marketplaces=("us",),
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda value: None,
        jitter=lambda: 0.0,
    )
    try:
        observation = m.observe_provider(
            "audible", "audible_catalog", provider, plan
        ).observation
    finally:
        provider._client.close()

    assert {"audible-product-B000000009"} == {
        value.identity for value in observation.candidates
    }
    assert ProviderStatus.NO_MATCH in {lane.status for lane in observation.lanes}
    MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=plan.local_claims,
        local_reasons=("target_metadata_title",),
        providers=(recorded_dispatch(observation),),
        audio=single_track_audio_facts(),
    )


def test_a_narrator_refused_carried_stub_still_comes_before_the_rule() -> None:
    # The #1316 narrator gate keeps another reader's carried recording only as
    # its identifier. The carried lane still answered, so the rule waits even
    # though the asserted lane's page holds a product it would admit.
    prior = LocalClaim.create(
        kind=ClaimKind.ASIN,
        observed="B000000009",
        canonical="B000000009",
        excerpt="B000000009",
        applicability="not_applicable",
        basis="prior_admission",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
    )
    claims = (
        target_title_claim(_DECORATED),
        prior,
        local_runtime_claim(_RUNTIME),
        audio_claim(ClaimKind.CONTRIBUTOR, _NARRATOR, 3, role="narrator"),
    )
    fields = _target_fields(_DECORATED, (_NARRATOR,))
    plan = m.audiobook_carried_plan(
        m.audiobook_discovery_plan(
            m.TargetSnapshot("audiobook", _DECORATED, (), fields=fields),
            claims,
            audio=single_track_audio_facts(),
        )
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if "title" in request.url.params:
            products = (
                [_product(1, "Synthetic Journey", subtitle="Into the Fold")]
                if request.url.params["title"] == _DECORATED
                else []
            )
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"total_results": len(products), "products": products},
            )
        product = _product(9, _DECORATED, narrator="Other Voice")
        return httpx.Response(
            200, headers={"content-type": "application/json"}, json={"product": product}
        )

    provider = AudibleCatalogMetadataProvider(
        "audible",
        httpx.Client(transport=httpx.MockTransport(handle)),
        262_144,
        5.0,
        marketplaces=("us",),
        clock=lambda: 0.0,
        wall_clock=lambda: 1_700_000_000.0,
        sleep=lambda value: None,
        jitter=lambda: 0.0,
    )
    try:
        observation = m.observe_provider(
            "audible", "audible_catalog", provider, plan
        ).observation
    finally:
        provider._client.close()

    assert {"audible-product-B000000009"} == {
        value.identity for value in observation.candidates
    }
    (carried,) = observation.candidates
    assert all(claim.kind is ClaimKind.ASIN for claim in carried.claims)
    assert not carried.evidence
    assert {"audible-product-B000000009"} == {
        value.identity for value in observation.refused_products
    }
    assert ProviderStatus.NO_MATCH in {lane.status for lane in observation.lanes}
    MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=plan.local_claims,
        local_reasons=("target_metadata_title",),
        providers=(recorded_dispatch(observation),),
        audio=single_track_audio_facts(),
    )


def test_a_band_first_admission_is_recorded_as_its_exact_path_stops() -> None:
    plan = _plan(_DECORATED)
    observation, seen = _walk(
        plan,
        [_product(1, _DECORATED), _product(2, _DECORATED, minutes=1_200)],
        [],
    )
    (lane,) = observation.lanes

    assert 2 == len(seen)
    assert lane.dispatch is not None
    assert 1 == lane.dispatch.rungs
    report = _report(plan, _DECORATED, observation)
    # An exact admission may not claim the whole ladder was sent.
    data = json.loads(report.to_json())
    data["providers"][0]["lanes"][0]["dispatch"]["rungs"] = 2
    with pytest.raises(ReportValueError, match="rungs do not replay"):
        MetadataReport.from_json(_readdressed(data))
