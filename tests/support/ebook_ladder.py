"""A series-titled ebook plan and its persisted report, for ladder tests (#1499)."""

from __future__ import annotations

from mechanical_librarian.application.metadata_report import (
    MetadataDiscoveryPlan,
    TargetSnapshot,
    metadata_discovery_plan,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    ProviderObservation,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from tests.support.metadata_report_schemas import synthetic_report

# Its subtitle gives the ladder two searches: with the subtitle, then without.
SERIES_TITLED_EBOOK = "Example Book: A Test (Example Saga)"
SERIES_TITLED_AUTHOR = "Ada Example"


def _fields(title: str) -> tuple[TargetField, ...]:
    return tuple(
        TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE,
            (SERIES_TITLED_AUTHOR,),
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in default_target_fields(title)
    )


def series_titled_ebook_plan(
    title: str = SERIES_TITLED_EBOOK,
) -> MetadataDiscoveryPlan:
    fields = _fields(title)
    claim = LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=title,
        canonical=title,
        excerpt=title,
        applicability="not_applicable",
        basis="target_metadata",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    return metadata_discovery_plan(
        TargetSnapshot("ebook", title, (), fields=fields), (claim,)
    )


def reloaded(
    plan: MetadataDiscoveryPlan, observation: ProviderObservation
) -> ProviderObservation:
    """The observation as a persisted report reads it back."""
    seed = synthetic_report(SERIES_TITLED_EBOOK)
    report = MetadataReport.create(
        target=TargetFacts(
            seed.target.connection,
            seed.target.library,
            seed.target.item,
            _fields(SERIES_TITLED_EBOOK),
        ),
        file=seed.file,
        local_claims=plan.local_claims,
        local_reasons=("no_opening_claims",),
        providers=(observation,),
    )
    return MetadataReport.from_json(report.to_json()).providers[0]
