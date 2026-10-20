"""One admitted audiobook report, and the evidence it is built from.

The discovery cohort proved the runtime corroboration band over these exact
builders, and F45 forms proposals behind that same admission, so both cohorts
read one description of the recording rather than two (#966).
"""

from __future__ import annotations

from typing import Any

from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    qualify_provider_observation,
)
from mechanical_librarian.domain import human_decision
from mechanical_librarian.domain.audio_inspection import AudioChapter
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderChapterStructure,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
    WorkDispatchLineage,
    default_target_fields,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.work_identity import work_query_ladder
from tests.support.metadata_report_doubles import single_track_audio_facts

__all__ = [
    "admitted_audiobook_report",
    "corroborated_audiobook_report",
    "asin_lane_observation",
    "asserted_asin_report",
    "audio_local_claims",
    "audio_target_fields",
    "audiobook_candidate",
    "manual_audiobook_observation",
    "qualified_audiobook",
    "recording_lookup_hint_report",
    "retained_recorded_only_decision",
]


def _audio_work_dispatch(title: str, authors: tuple[str, ...]) -> WorkDispatchLineage:
    return WorkDispatchLineage.from_query(work_query_ladder(title, authors)[0])


def audio_target_fields() -> tuple[TargetField, ...]:
    """Target facts naming both the title and the author the replay reads."""
    populated = TargetField(
        TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Andy Weir",)
    )
    return tuple(
        populated if field.destination is TargetDestination.AUTHORS else field
        for field in default_target_fields("Project Hail Mary")
    )


def audio_local_claims(duration: str | None = "58244.201") -> tuple[LocalClaim, ...]:
    """One audiobook's local evidence, optionally without any running time."""
    values: list[LocalClaim] = [
        LocalClaim.create(
            kind=ClaimKind.TITLE,
            observed="Project Hail Mary",
            canonical="Project Hail Mary",
            excerpt="Project Hail Mary",
            applicability="not_applicable",
            basis="embedded_tag",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            occurrence=1,
        )
    ]
    if duration is not None:
        values.append(
            LocalClaim.create(
                kind=ClaimKind.DURATION,
                observed=duration,
                canonical=duration,
                excerpt=duration,
                applicability="not_applicable",
                basis="embedded_tag",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                occurrence=2,
            )
        )
    return tuple(values)


def audiobook_candidate(
    local_claims: tuple[LocalClaim, ...],
    *,
    identity: str = "audible-product-B08G9PRS1K",
    asin: str = "B08G9PRS1K",
    duration: str = "58200.000",
    provider: str = "catalogue",
    chapters: ProviderChapterStructure | None = None,
    series: tuple[str, str] | None = None,
    title: str = "Project Hail Mary",
) -> ProviderCandidate:
    """One candidate shaped exactly as the audiobook adapters build it."""
    specifications: tuple[tuple[ClaimKind, str, ClaimScope, str | None], ...] = (
        (ClaimKind.TITLE, title, ClaimScope.WORK, None),
        (ClaimKind.CREATOR, "Andy Weir", ClaimScope.WORK, "author"),
        (ClaimKind.ASIN, asin, ClaimScope.EDITION, None),
        (ClaimKind.DURATION, duration, ClaimScope.EDITION, None),
        (ClaimKind.CONTRIBUTOR, "Ray Porter", ClaimScope.WORK, "narrator"),
        *(
            ()
            if series is None
            else (
                (ClaimKind.SERIES, series[0], ClaimScope.WORK, None),
                (ClaimKind.SERIES_SEQUENCE, series[1], ClaimScope.WORK, None),
            )
        ),
    )
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
    chapters = chapters or ProviderChapterStructure(2, 58_200, (29_100, 29_100), False)
    evidence = tuple(
        sorted(
            (
                ProviderEvidence(
                    ProviderEvidenceKind.EDITION_WORK_RELATION,
                    "unresolved",
                    ClaimScope.EDITION,
                ),
                ProviderEvidence(
                    ProviderEvidenceKind.ASIN_MARKETPLACE, "us", ClaimScope.EDITION
                ),
                ProviderEvidence(
                    ProviderEvidenceKind.CHAPTER_STRUCTURE,
                    str(chapters.runtime_seconds),
                    ClaimScope.EDITION,
                    chapters=chapters,
                ),
            ),
            key=lambda value: (value.scope.value, value.kind.value, value.value),
        )
    )
    return finalize_candidate_comparisons(
        ProviderCandidate(identity, "unresolved", claims, evidence),
        local_claims,
        provider,
    )


def qualified_audiobook(
    local_claims: tuple[LocalClaim, ...],
    *,
    authority: str | None = "audible_catalog",
    duration: str = "58200.000",
    candidates: tuple[ProviderCandidate, ...] | None = None,
    chapters: ProviderChapterStructure | None = None,
    provider: str = "catalogue",
) -> ProviderObservation:
    admitted = (
        (
            audiobook_candidate(
                local_claims,
                duration=duration,
                chapters=chapters,
                provider=provider,
            ),
        )
        if candidates is None
        else candidates
    )
    return qualify_provider_observation(
        ProviderObservation(
            provider,
            ProviderStatus.CANDIDATES,
            (),
            admitted,
            ProviderQueryKind.WORK,
            authority,
            dispatch_lineage=_audio_work_dispatch("Project Hail Mary", ("Andy Weir",)),
        ),
        MetadataLookup(
            ProviderQueryKind.WORK,
            local_claims,
            "Project Hail Mary",
            ("Andy Weir",),
        ),
    )


def manual_audiobook_observation(
    provider: str,
    authority: str,
    candidate: ProviderCandidate,
) -> ProviderObservation:
    """Retain one provider candidate for review without granting audio authority."""
    return ProviderObservation(
        provider,
        ProviderStatus.CANDIDATES,
        (),
        (candidate,),
        ProviderQueryKind.WORK,
        authority,
        dispatch_lineage=_audio_work_dispatch("Project Hail Mary", ("Andy Weir",)),
    )


def admitted_audiobook_report(
    local_claims: tuple[LocalClaim, ...] | None = None,
    *,
    connection: str = "primary",
    library: str = "audio",
    item: str = "item-1",
    identity: str = "a" * 64,
    content_digest: str = "b" * 64,
    additional_providers: tuple[ProviderObservation, ...] = (),
    chapters: tuple[AudioChapter, ...] = (),
) -> MetadataReport:
    """One published recording whose work identity the runtime band admits.

    This is the report F45 forms proposals behind, so the cases that ask what
    an admitted audiobook produces all start from the same one. The target and
    file identity are open because a browser cohort seeds its own library and
    a report has to name the item its catalogue entry names (#966).
    """
    claims = audio_local_claims() if local_claims is None else local_claims
    return MetadataReport.create(
        target=TargetFacts(connection, library, item, audio_target_fields()),
        file=FileFacts(0, identity, content_digest, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=tuple(
            sorted(
                (qualified_audiobook(claims), *additional_providers),
                key=lambda value: value.provider,
            )
        ),
        audio=single_track_audio_facts(content_digest, chapters=chapters),
    )


def asin_lane_observation(
    local_claims: tuple[LocalClaim, ...],
    *,
    duration: str = "58200.000",
    authority: str | None = "audible_catalog",
) -> ProviderObservation:
    """One per-ASIN answer, unqualified, as an adapter hands it over.

    The lane is fed by something other than the item's own surfaces -- a target
    assertion or a gate promotion -- so ADR-0061 leaves its identity to the
    runtime corroboration band rather than to the lookup that built it.
    """
    return ProviderObservation(
        "catalogue",
        ProviderStatus.CANDIDATES,
        (),
        (audiobook_candidate(local_claims, duration=duration),),
        ProviderQueryKind.ASIN,
        authority,
    )


def asserted_asin_report(
    *,
    provider_duration: str,
    local_asin: str | None = None,
    connection: str = "primary",
    library: str = "audio",
    item: str = "item-1",
) -> MetadataReport:
    """A recording whose only provider answer came from an ASIN lane."""
    claims = audio_local_claims()
    if local_asin is not None:
        claims = (
            *claims,
            LocalClaim.create(
                kind=ClaimKind.ASIN,
                observed=local_asin,
                canonical=local_asin,
                excerpt=local_asin,
                applicability="not_applicable",
                basis="embedded_tag",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                occurrence=3,
            ),
        )
    observation = asin_lane_observation(claims, duration=provider_duration)
    return MetadataReport.create(
        target=TargetFacts(connection, library, item, audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=(
            qualify_provider_observation(
                observation,
                MetadataLookup(
                    ProviderQueryKind.ASIN,
                    claims,
                    work_title="Project Hail Mary",
                    work_authors=("Andy Weir",),
                    audio=single_track_audio_facts("b" * 64),
                ),
            ),
        ),
        audio=single_track_audio_facts("b" * 64),
    )


def retained_recorded_only_decision(
    case: ReviewCase, destination: TargetDestination
) -> HumanDecision:
    """Rebuild one F45 decision exactly as it was retained before F47.

    `HumanDecision.create` has said `apply` on both media since F47, so the
    only way to hold the history the SQL seam, the scheduler and the planner
    still have to recognise is to re-seal the document the way F45 sealed it
    (#968).
    """
    proposal = next(
        value for value in case.proposals if value["destination"] == destination.value
    )
    result = proposal["result"]
    assert type(result) is dict
    alternatives = result["alternatives"]
    assert type(alternatives) is list
    document = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination=destination.value,
        actor="owner",
        outcome="accept",
        selected_value=alternatives[0]["value"],
        idempotency_key="retained-recorded-only-0001",
        occurred_at=1_000,
    ).to_data()
    document["application"] = "recorded_only"
    document["request_digest"] = human_decision._sha256(
        human_decision._canonical(
            {
                key: document[key]
                for key in (
                    "actor",
                    "application",
                    "destination",
                    "idempotency_digest",
                    "outcome",
                    "proposal_id",
                    "review_case_id",
                    "selected_value",
                    "target_fingerprint",
                )
            }
        )
    )
    document["decision_id"] = "human_decision_" + human_decision._sha256(
        human_decision._canonical(
            {
                "request_digest": document["request_digest"],
                "occurred_at": document["occurred_at"],
            }
        )
    )
    return HumanDecision.from_json(human_decision._canonical(document))


def corroborated_audiobook_report(
    *,
    connection: str = "primary",
    library: str = "audio",
    item: str = "item-1",
    identity: str = "a" * 64,
    content_digest: str = "b" * 64,
    series: tuple[str, str] | None = ("Reading Order", "2"),
) -> MetadataReport:
    """One recording two independent audiobook authorities agree about.

    F46's tranches are measured on recordings whose narrator, series and ASIN
    claims two authorities state alike, so the case that asks what a qualified
    tranche does starts from one of those rather than from the single-authority
    report F45's sentinels use (#967).
    """
    claims = audio_local_claims()
    providers = tuple(
        sorted(
            (
                qualified_audiobook(
                    claims,
                    authority=authority,
                    provider=provider,
                    candidates=(
                        audiobook_candidate(claims, provider=provider, series=series),
                    ),
                )
                for provider, authority in (
                    ("catalogue", "audible_catalog"),
                    ("registry", "audnexus"),
                )
            ),
            key=lambda value: value.provider,
        )
    )
    return MetadataReport.create(
        target=TargetFacts(connection, library, item, audio_target_fields()),
        file=FileFacts(0, identity, content_digest, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=providers,
        audio=single_track_audio_facts(content_digest),
    )


def chapter_runtime_report(
    *,
    provider_duration: str | None = None,
    duration_state: FieldState = FieldState.ABSENT,
    local_duration: str | None = "1748.000",
    local_asin: str | None = "B08G9PRS1K",
    asin_basis: str = "embedded_tag",
    provider: str = "catalogue",
    authority: str = "audnexus",
    chapters: tuple[AudioChapter, ...] | None = None,
    structure: ProviderChapterStructure | None = None,
    chapter_rows_omitted: bool = False,
) -> MetadataReport:
    """Synthetic retained local-ASIN lane with explicit absent-runtime evidence."""
    from dataclasses import replace

    from mechanical_librarian.application.metadata_report import (
        ProviderLaneResult,
        audiobook_asin_feed_plan,
        audiobook_discovery_plan,
        provider_observation,
    )

    claims = audio_local_claims(local_duration)
    if local_asin is not None:
        claims += (
            LocalClaim.create(
                kind=ClaimKind.ASIN,
                observed=local_asin,
                canonical=local_asin,
                excerpt=local_asin,
                applicability="not_applicable",
                basis=asin_basis,
                checksum="not_applicable",
                applicability_basis="not_applicable",
                occurrence=3,
            ),
        )
    durations = (113, 171, 243, 317, 401, 503)
    if chapters is None:
        end = 0
        values = []
        for i, duration in enumerate(durations, 1):
            values.append(AudioChapter(i, end, end + duration * 1000, None))
            end += duration * 1000
        chapters = tuple(values)
    structure = structure or ProviderChapterStructure(6, 1748, durations)
    candidate = audiobook_candidate(claims, provider=provider, chapters=structure)
    candidate = replace(
        candidate,
        claims=tuple(
            ProviderClaim.create(
                provider=provider,
                candidate=candidate.identity,
                kind=c.kind,
                value=provider_duration,
                state=duration_state,
                scope=c.scope,
                applicability=c.applicability,
                raw_order=c.raw_order,
            )
            if c.kind is ClaimKind.DURATION
            else c
            for c in candidate.claims
        ),
    )
    from tests.support.audiobook_providers import asserted_target, audio_target

    target = (
        asserted_target(local_asin)
        if asin_basis == "target_metadata" and local_asin is not None
        else audio_target()
    )
    plan = audiobook_discovery_plan(target, claims)
    if asin_basis == "target_metadata":
        feed = audiobook_asin_feed_plan(target, plan, ())
        assert feed is not None
        plan = feed
    lane = next(value for value in plan.lanes if value.kind is ProviderQueryKind.ASIN)
    candidate = finalize_candidate_comparisons(candidate, claims, provider)
    observation = provider_observation(
        provider,
        (ProviderLaneResult(lane, ProviderStatus.CANDIDATES, (candidate,)),),
        authority=authority,
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("embedded_tag_duration",),
        providers=(observation,),
        audio=single_track_audio_facts(
            chapters=chapters, chapter_rows_omitted=chapter_rows_omitted
        ),
    )


def recording_lookup_report(
    *,
    provider: str = "catalogue",
    asins: tuple[str, ...] = ("B000000001", "B000000002"),
    duration: str = "58200.000",
) -> MetadataReport:
    from mechanical_librarian.application.metadata_report import (
        ProviderLaneResult,
        provider_observation,
    )
    from tests.support.audiobook_providers import work_plan
    from tests.support.metadata_report_doubles import single_track_audio_facts

    plan = work_plan(*audio_local_claims(None))
    local = plan.local_claims
    candidates = tuple(
        audiobook_candidate(
            local,
            provider=provider,
            identity=f"recording-{i}",
            asin=asin,
            duration=duration,
        )
        for i, asin in enumerate(asins, 1)
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=local,
        local_reasons=("embedded_tag_title",),
        providers=(
            provider_observation(
                provider,
                (
                    ProviderLaneResult(
                        plan.lanes[0],
                        ProviderStatus.NO_MATCH,
                        recording_lookup_candidates=candidates,
                    ),
                ),
                authority="audnexus",
            ),
        ),
        audio=single_track_audio_facts(),
    )


def recording_lookup_hint_report(
    *,
    hint_asins: tuple[str, ...] = ("B000000011",),
    lookup_asins: tuple[str, ...] = (),
    provider: str = "audible",
    authority: str = "audible_catalog",
) -> MetadataReport:
    """An asserted Audible lane that walked its ladder and kept hints (#1257).

    Every rung refused its products. `lookup_asins` are #1004's exact lookup
    candidates, and `hint_asins` the band-first hints the first rung kept from
    products inside the tight band of the item's runtime.
    """
    from mechanical_librarian.application.metadata_report import (
        ProviderLaneResult,
        provider_observation,
    )
    from mechanical_librarian.domain.metadata_report import (
        RecordingLookupHint,
        work_ladder_diagnostics,
    )
    from tests.support.audiobook_providers import work_plan

    plan = work_plan(*audio_local_claims())
    local = plan.local_claims
    ladder = tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(
            "Project Hail Mary", ("Andy Weir",), audiobook=True
        )
    )
    diagnostics = work_ladder_diagnostics(
        0, max(len(ladder), len(hint_asins) + len(lookup_asins))
    )
    return MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", audio_target_fields()),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=local,
        local_reasons=("embedded_tag_duration",),
        providers=(
            provider_observation(
                provider,
                (
                    ProviderLaneResult(
                        plan.lanes[0],
                        ProviderStatus.NO_MATCH,
                        diagnostics=diagnostics,
                        recording_lookup_candidates=tuple(
                            audiobook_candidate(
                                local,
                                provider=provider,
                                identity=f"recording-{asin}",
                                asin=asin,
                            )
                            for asin in lookup_asins
                        ),
                        exhausted_dispatch=ladder,
                        recording_lookup_hints=tuple(
                            RecordingLookupHint(asin, 58_200, ladder[0])
                            for asin in hint_asins
                        ),
                    ),
                ),
                authority=authority,
                diagnostics=diagnostics,
            ),
        ),
        audio=single_track_audio_facts(),
    )


def synthetic_audiobook_bibliographic_report(
    *,
    adult_product: object = None,
    source_authors: tuple[str, ...] = ("Example Author",),
    second_source_authors: tuple[str, ...] | None = None,
    target_authors: tuple[str, ...] = ("Example Author",),
    title: str = "Synthetic Journey: A Tale of Courage",
    source_title: str = "Synthetic Journey",
    source_subtitle: str | None = "A Tale of Courage",
    source_series: tuple[str, str] | None = None,
    target_series: tuple[str, str | None] | None = None,
    source_narrators: tuple[str, ...] = (),
    target_narrators: tuple[str, ...] = (),
    local_narrators: tuple[str, ...] = (),
) -> MetadataReport:
    """A real adapter produces a recording-admitted synthetic bibliographic pair.

    The keywords vary only the target title, series and narrators, the
    product's title, subtitle, series and narrators, the second product's
    authors, and the file's own narrator tags (#1252).
    """
    from dataclasses import replace

    import httpx

    from mechanical_librarian.adapters.providers.audnexus_metadata import (
        AudnexusMetadataProvider,
    )
    from mechanical_librarian.application.metadata_report import (
        audiobook_discovery_plan,
    )
    from tests.support.audiobook_providers import (
        audio_claim,
        audio_target,
        target_title_claim,
    )

    target = audio_target(title, target_authors)
    populated = {
        TargetDestination.SERIES: None
        if target_series is None
        else (TargetSeries(*target_series),),
        TargetDestination.NARRATORS: target_narrators or None,
    }
    target = replace(
        target,
        fields=tuple(
            TargetField(
                field.destination,
                TargetFieldState.VALUE,
                populated[field.destination],
            )
            if populated.get(field.destination) is not None
            else field
            for field in target.fields
        ),
    )
    claims = (
        target_title_claim(title),
        audio_claim(ClaimKind.ASIN, "B000000001", 1),
        audio_claim(ClaimKind.ASIN, "B000000002", 2),
        audio_claim(ClaimKind.DURATION, "58200.000", 3),
        *(
            audio_claim(ClaimKind.CONTRIBUTOR, name, 4 + index, role="narrator")
            for index, name in enumerate(local_narrators)
        ),
    )
    audio = single_track_audio_facts("b" * 64)
    plan = audiobook_discovery_plan(target, claims, audio=audio)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/chapters"):
            return httpx.Response(404, json={})
        return httpx.Response(
            200,
            json={
                "asin": request.url.path.split("/")[2],
                "title": source_title,
                "subtitle": source_subtitle,
                "runtimeLengthMin": 970,
                "isAdult": adult_product,
                "authors": [
                    {"name": name}
                    for name in (
                        second_source_authors
                        if second_source_authors is not None
                        and request.url.path.split("/")[2] == "B000000002"
                        else source_authors
                    )
                ],
                **(
                    {"narrators": [{"name": name} for name in source_narrators]}
                    if source_narrators
                    else {}
                ),
                **(
                    {
                        "seriesPrimary": {
                            "name": source_series[0],
                            "position": source_series[1],
                        }
                    }
                    if source_series is not None
                    else {}
                ),
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = AudnexusMetadataProvider(
            "audnexus",
            client,
            262_144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda value: None,
            jitter=lambda: 0.0,
        )
        observation = provider.observe(plan).observation
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", target.fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(observation,),
        audio=audio,
    )
    return MetadataReport.from_json(report.to_json())


def synthetic_audiobook_product_lookup_report(
    products: tuple[dict[str, object], ...],
    *,
    unavailable: bool = False,
    recording_runtime: str | None = None,
) -> MetadataReport:
    """Real discovery and per-product adapters, without independent local ASINs."""
    import httpx

    from mechanical_librarian.adapters.providers.audible_catalog_metadata import (
        AudibleCatalogMetadataProvider,
    )
    from mechanical_librarian.adapters.providers.audnexus_metadata import (
        AudnexusMetadataProvider,
    )
    from mechanical_librarian.application.metadata_report import (
        audiobook_asin_feed_plan,
        audiobook_discovery_plan,
        observe_provider,
    )
    from tests.support.audiobook_providers import (
        audio_target,
        local_runtime_claim,
        target_title_claim,
    )

    target = audio_target("Synthetic Journey", ("Example Author",))
    claims: tuple[LocalClaim, ...] = (target_title_claim("Synthetic Journey"),)
    if recording_runtime is not None:
        claims = (*claims, local_runtime_claim(recording_runtime))
    audio = single_track_audio_facts("b" * 64)
    plan = audiobook_discovery_plan(target, claims, audio=audio)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.audible.com":
            return httpx.Response(
                200,
                json={
                    "products": [
                        {
                            "asin": product["asin"],
                            "title": "Synthetic Journey",
                            "authors": [{"name": "Example Author"}],
                            "narrators": product.get("narrators"),
                            **(
                                {"runtime_length_min": product["runtimeLengthMin"]}
                                if recording_runtime is not None
                                and "runtimeLengthMin" in product
                                else {}
                            ),
                        }
                        for product in products
                    ],
                    "total_results": len(products),
                },
            )
        if unavailable:
            raise httpx.ReadTimeout("synthetic unavailable", request=request)
        if request.url.path.endswith("/chapters"):
            return httpx.Response(404, json={})
        asin = request.url.path.split("/")[2]
        return httpx.Response(
            200, json=next(product for product in products if product["asin"] == asin)
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        discovery_provider = AudibleCatalogMetadataProvider(
            "audible",
            client,
            262_144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda value: None,
            jitter=lambda: 0.0,
        )
        discovery = observe_provider(
            "audible", "audible_catalog", discovery_provider, plan
        ).observation
    feed = audiobook_asin_feed_plan(target, plan, (discovery,))
    assert feed is not None
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        lookup_provider = AudnexusMetadataProvider(
            "audnexus",
            client,
            262_144,
            5.0,
            marketplaces=("us",),
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
            sleep=lambda value: None,
            jitter=lambda: 0.0,
        )
        lookup = observe_provider(
            "audnexus", "audnexus", lookup_provider, feed
        ).observation
    report = MetadataReport.create(
        target=TargetFacts("primary", "audio", "item-1", target.fields),
        file=FileFacts(0, "a" * 64, "b" * 64, "m4b"),
        local_claims=claims,
        local_reasons=("target_metadata_title",),
        providers=(discovery, lookup),
        audio=audio,
    )
    return MetadataReport.from_json(report.to_json())


def publish_audiobook_preview(
    repository: Any,
    report: MetadataReport,
    automatic: tuple[str, ...],
    *,
    selected_options: dict[str, str] | None = None,
    selected_destinations: dict[str, str] | None = None,
) -> tuple[str, ResolvedMetadataPolicy]:
    """Publish one recording under a policy routing `automatic` to Automatic."""
    import json
    from typing import cast

    from mechanical_librarian.domain.metadata_policy import (
        DEFAULT_OPTIONS,
        OPTION_CATALOGUE,
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report

    options = dict(DEFAULT_OPTIONS)
    options.update(selected_options or {})
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    for category in automatic:
        destinations[category] = "automatic"
    destinations.update(selected_destinations or {})
    profile = MetadataPolicyProfile.create("F46 tranche", options, destinations)
    resolved = resolve_policy(profile, report.target.library)
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        2,
        expected_active_generation=1,
    )
    repository.activate_qualified_automatic(3)
    proposal = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    return cast(str, admitted["review_case_id"]), resolved


def synthetic_audiobook_isbn_reason_report(shape: str) -> MetadataReport:
    values: tuple[object, object] = {
        "absent": (None, None),
        "unassociated": ("9781250077004", None),
        "conflict": ("9781250077004", "9780593135204"),
        "malformed": ("9781250077004", {}),
        "unavailable": (None, None),
    }[shape]
    return synthetic_audiobook_product_lookup_report(
        tuple(
            {
                "runtimeLengthMin": 970,
                "asin": f"B00000000{index + 1}",
                "title": "Synthetic Journey",
                "authors": [{"name": "Example Author"}],
                "narrators": [{"name": f"Narrator {index}"}],
                "isbn": value,
            }
            for index, value in enumerate(values)
        ),
        unavailable=shape == "unavailable",
        recording_runtime="58200.000",
    )


def synthetic_audiobook_original_date_report(
    current: str | None = None, source: str = "2025-02-03"
) -> MetadataReport:
    """Replay the complete synthetic linked-work source, retaining date precision."""
    import json
    from dataclasses import replace
    from pathlib import Path

    import httpx

    from mechanical_librarian.adapters.providers.open_library_metadata import (
        OpenLibraryMetadataProvider,
        OpenLibraryPacer,
    )
    from mechanical_librarian.application.metadata_report import (
        audiobook_discovery_plan,
        observe_provider,
    )
    from tests.support.audiobook_providers import audio_target

    base = synthetic_audiobook_product_lookup_report(
        (
            {
                "runtimeLengthMin": 970,
                "asin": "B000000001",
                "title": "Synthetic Journey",
                "authors": [{"name": "Example Author"}],
            },
        ),
        recording_runtime="58200.000",
    )
    fields = tuple(
        TargetField(
            field.destination,
            TargetFieldState.MISSING if current is None else TargetFieldState.VALUE,
            current,
        )
        if field.destination is TargetDestination.PUBLISHED_DATE
        else field
        for field in base.target.fields
    )
    plan = audiobook_discovery_plan(
        audio_target("Synthetic Journey", ("Example Author",)),
        base.local_claims,
        audio=base.audio,
    )
    path = (
        Path(__file__).with_name("audiobook_provider_payloads")
        / "open_library_original_date.json"
    )
    responses = json.loads(path.read_text())
    responses["/works/OL5W.json"]["first_publish_date"] = source

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path in responses
        return httpx.Response(200, json=responses[request.url.path])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = OpenLibraryMetadataProvider(
            "open_library",
            client,
            262_144,
            pacer=OpenLibraryPacer(sleep=lambda _: None),
            clock=lambda: 0.0,
            wall_clock=lambda: 1_700_000_000.0,
        )
        observation = observe_provider(
            "open_library", "open_library", provider, plan
        ).observation
    report = MetadataReport.create(
        target=replace(base.target, fields=fields),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(*base.providers, observation),
        audio=base.audio,
    )
    return MetadataReport.from_json(report.to_json())
