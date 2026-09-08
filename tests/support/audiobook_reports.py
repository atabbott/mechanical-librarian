"""One admitted audiobook report, and the evidence it is built from.

The discovery cohort proved the runtime corroboration band over these exact
builders, and F45 forms proposals behind that same admission, so both cohorts
read one description of the recording rather than two (#966).
"""

from __future__ import annotations

from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    qualify_provider_observation,
)
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
    WorkDispatchLineage,
    default_target_fields,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.work_identity import work_query_ladder
from tests.support.metadata_report_doubles import single_track_audio_facts

__all__ = [
    "admitted_audiobook_report",
    "asin_lane_observation",
    "asserted_asin_report",
    "audio_local_claims",
    "audio_target_fields",
    "audiobook_candidate",
    "manual_audiobook_observation",
    "qualified_audiobook",
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
) -> ProviderCandidate:
    """One candidate shaped exactly as the audiobook adapters build it."""
    specifications: tuple[tuple[ClaimKind, str, ClaimScope, str | None], ...] = (
        (ClaimKind.TITLE, "Project Hail Mary", ClaimScope.WORK, None),
        (ClaimKind.CREATOR, "Andy Weir", ClaimScope.WORK, "author"),
        (ClaimKind.ASIN, asin, ClaimScope.EDITION, None),
        (ClaimKind.DURATION, duration, ClaimScope.EDITION, None),
        (ClaimKind.CONTRIBUTOR, "Ray Porter", ClaimScope.WORK, "narrator"),
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
    chapters = ProviderChapterStructure(2, 58_200, (29_100, 29_100), False)
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
) -> ProviderObservation:
    admitted = (
        (audiobook_candidate(local_claims, duration=duration),)
        if candidates is None
        else candidates
    )
    return qualify_provider_observation(
        ProviderObservation(
            "catalogue",
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
        audio=single_track_audio_facts(content_digest),
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
                MetadataLookup(ProviderQueryKind.ASIN, claims),
            ),
        ),
        audio=single_track_audio_facts("b" * 64),
    )
