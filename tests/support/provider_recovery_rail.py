"""Synthetic provider evidence shared by rail and exact preview proofs."""

from dataclasses import replace

from mechanical_librarian.application.metadata_report import (
    ProviderLaneResult,
    TargetSnapshot,
    metadata_discovery_plan,
    provider_observation,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    MetadataReport,
    ProviderLaneObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _local_isbn,
    _provider,
    _report,
)


def _series_claims(provider, candidate, name="Reading Order", sequence="2"):
    return (
        _claim(
            provider,
            candidate,
            ClaimKind.SERIES,
            name,
            scope=ClaimScope.WORK,
        ),
        _claim(
            provider,
            candidate,
            ClaimKind.SERIES_SEQUENCE,
            sequence,
            scope=ClaimScope.WORK,
            order=1,
        ),
    )


def _automatic_series_report(*providers, current_title="Current title"):
    return _report(
        fields=_automatic_series_fields(current_title),
        local_claims=(_local_isbn("9780306406157", "valid_isbn_13"),),
        providers=providers,
    )


def _automatic_series_fields(current_title="Current title"):
    return _fields(
        current_title,
        authors=TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Ada Example",)
        ),
        series=TargetField(TargetDestination.SERIES, TargetFieldState.MISSING),
    )


def _series_provider(
    name, candidate, authority, claims=None, *, current_title="Current title"
):
    identity = (
        _claim(name, candidate, ClaimKind.TITLE, current_title, scope=ClaimScope.WORK),
        _claim(
            name,
            candidate,
            ClaimKind.CREATOR,
            "Ada Example",
            scope=ClaimScope.WORK,
            role="author",
        ),
        _claim(
            name,
            candidate,
            ClaimKind.ISBN,
            "9780306406157",
            scope=ClaimScope.EDITION,
            applicability="ebook",
        ),
    )
    source = _provider(
        name,
        candidate,
        identity + (_series_claims(name, candidate) if claims is None else claims),
        authority=authority,
    )
    lane = metadata_discovery_plan(
        TargetSnapshot(
            "ebook", current_title, (), fields=_automatic_series_fields(current_title)
        ),
        (_local_isbn("9780306406157", "valid_isbn_13"),),
    ).lanes[0]
    return provider_observation(
        name,
        (ProviderLaneResult(lane, ProviderStatus.CANDIDATES, source.candidates),),
        authority=authority,
    )


def provider_failure_report(*, contributing=True):
    base = _automatic_series_report(
        _series_provider("catalogue", "edition-1", "hardcover")
    )
    provider = base.providers[0]
    local = base.local_claims[0]
    failed = ProviderLaneObservation(
        "lane_" + "b" * 64,
        ProviderQueryKind.ISBN if contributing else ProviderQueryKind.WORK,
        ProviderStatus.UNAVAILABLE,
        (local.reference,),
        "9780306406157" if contributing else None,
    )
    return MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(
            replace(
                provider,
                query_kind=ProviderQueryKind.DISCOVERY,
                lanes=(*provider.lanes, failed),
            ),
        ),
    )


def publish_provider_failure_case(
    repository, *, contributing=True, automatic=True, project=True
):
    """Publish the exact real policy/report/proposal composition, without outcomes."""
    import json

    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report

    report = provider_failure_report(contributing=contributing)
    builtin = MetadataPolicyProfile.builtin()
    destinations = dict(builtin.destinations)
    destinations["series"] = "automatic" if automatic else "ask"
    profile = MetadataPolicyProfile.create(
        "Series review", dict(builtin.options), destinations
    )
    policy = resolve_policy(profile, report.target.library)
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        builtin.policy_id,
        ((report.target.library, policy.resolved_id, policy.canonical_json),),
        2,
    )
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    if project:
        repository.persist_shadow_outcome(case_id, 3)
    return case_id, report, policy, proposal
