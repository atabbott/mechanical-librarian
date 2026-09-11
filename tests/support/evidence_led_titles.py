"""Synthetic title evidence through real retained discovery lanes."""

import json
from typing import cast

from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    ProviderLaneResult,
    TargetSnapshot,
    metadata_discovery_plan,
    provider_observation,
    qualify_provider_observation,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    RearClaimProvenance,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _provider,
    _report,
    consensus_local_title,
)


def publish_title_case(
    repository, report: MetadataReport, mode: str = "automatic", safety=None
):
    """Publish through real policy, report, proposal, case and decision consumers."""
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        resolve_policy,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report

    base = MetadataPolicyProfile.builtin()
    destinations = {
        k: "unsupported" if k == "explicit" else "ask" for k, _ in base.destinations
    }
    destinations["title_layout"] = mode
    destinations["publisher_preference"] = "automatic"
    profile = MetadataPolicyProfile.create(
        "Title qualification", dict(base.options), destinations
    )
    policy = resolve_policy(profile, report.target.library)
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        base.policy_id,
        ((report.target.library, policy.resolved_id, policy.canonical_json),),
        2,
    )
    repository.activate_qualified_automatic(3)
    proposal = evaluate_report(report, policy.resolved_id, dict(policy.destinations))
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    if safety is not None:
        repository.set_automatic_safety_state(safety, 3)
    if case_id is not None:
        repository.persist_shadow_outcome(case_id, 4)
    return case_id, proposal


def isbn_title_report(
    *,
    current: str = "Example Series 03 - Example Title",
    titles: tuple[str, ...] = ("Example Title",),
    author: str = "Ada Author",
    isbns: tuple[tuple[str, str], ...] = (("9780306406157", "ebook"),),
    role: str | None = None,
    second_work: bool = False,
    failed: bool = False,
    reverse: bool = False,
    content_title: str | None = None,
    rear_isbn: bool = False,
    variant_conflict: bool = False,
    subtitle: str | None = None,
    local_variant: str | None = None,
    additional_variant: str | None = None,
    local_authors: tuple[str, ...] = ("Ada Author",),
    opening_authors: tuple[str, ...] = (),
    provider_authors: tuple[str, ...] | None = None,
    target_authors: tuple[str, ...] = ("Ada Author",),
    empty_target_title: bool = False,
) -> MetadataReport:
    local = [
        consensus_local_title(current),
        *(
            LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed=name,
                canonical=name,
                excerpt=name,
                scope=ClaimScope.WORK,
                role="author",
                basis=basis,
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                occurrence=index,
            )
            for basis, names in (
                ("package_metadata", local_authors),
                ("opening_matter", opening_authors),
            )
            for index, name in enumerate(names)
        ),
    ]
    if local_variant is not None:
        assert role in {"translator", "adapter", "abridger"}
        local.append(
            LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed=local_variant,
                canonical=local_variant,
                excerpt=f"{role}: {local_variant}",
                scope=ClaimScope.WORK,
                role=role,
                basis="opening_matter",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
            )
        )
    local.extend(
        LocalClaim.create(
            kind=ClaimKind.ISBN,
            observed=value,
            canonical=value,
            excerpt=value,
            scope=ClaimScope.EDITION,
            applicability=app,
            applicability_basis=app,
            checksum="valid_isbn_13",
            occurrence=i,
            rear_provenance=(
                RearClaimProvenance("a" * 64, 1, 1, "isbn", "ISBN")
                if rear_isbn
                else None
            ),
        )
        for i, (value, app) in enumerate(isbns)
    )
    if content_title is not None:
        local.append(
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed=content_title,
                canonical=content_title,
                excerpt=content_title,
                scope=ClaimScope.WORK,
                basis="opening_matter",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                occurrence=10,
            )
        )
    fields = _fields(
        current,
        authors=TargetField(
            TargetDestination.AUTHORS,
            TargetFieldState.VALUE if target_authors else TargetFieldState.EMPTY,
            target_authors if target_authors else None,
        ),
    )
    if empty_target_title:
        fields = tuple(
            TargetField(f.destination, TargetFieldState.EMPTY, None)
            if f.destination is TargetDestination.TITLE
            else f
            for f in fields
        )
    plan = metadata_discovery_plan(
        TargetSnapshot("ebook", current, (), fields=fields), tuple(local)
    )
    providers = []
    for i, title in enumerate(titles):
        name = ("hardcover", "open_library", "google_books")[i]
        lanes = []
        for n, lane in enumerate(plan.lanes):
            if lane.kind is not ProviderQueryKind.ISBN:
                continue
            identity = f"{name}-edition-{n}"
            claims = [
                _claim(name, identity, ClaimKind.TITLE, title, scope=ClaimScope.WORK),
                *(
                    _claim(
                        name,
                        identity,
                        ClaimKind.CREATOR,
                        author_name,
                        scope=ClaimScope.WORK,
                        role="author",
                        order=1 + author_index,
                    )
                    for author_index, author_name in enumerate(
                        provider_authors if provider_authors is not None else (author,)
                    )
                ),
                _claim(
                    name,
                    identity,
                    ClaimKind.ISBN,
                    lane.canonical_isbns[0],
                    scope=ClaimScope.EDITION,
                    applicability="ebook",
                    order=2,
                ),
            ]
            if role:
                claims.append(
                    _claim(
                        name,
                        identity,
                        ClaimKind.CONTRIBUTOR,
                        "Other Contributor"
                        if variant_conflict and i
                        else "Bea Contributor",
                        scope=ClaimScope.WORK,
                        role=role,
                        order=3,
                    )
                )
            if subtitle is not None:
                claims.append(
                    _claim(
                        name,
                        identity,
                        ClaimKind.SUBTITLE,
                        subtitle,
                        scope=ClaimScope.EDITION,
                        applicability="ebook",
                        order=4,
                    )
                )
            if additional_variant is not None:
                assert role is not None
                claims.append(
                    _claim(
                        name,
                        identity,
                        ClaimKind.CONTRIBUTOR,
                        additional_variant,
                        scope=ClaimScope.WORK,
                        role=role,
                        order=5,
                    )
                )
            source = _provider(name, identity, tuple(claims), authority=name)
            if second_work:
                other = identity + "-other"
                source = ProviderObservation(
                    name,
                    ProviderStatus.CANDIDATES,
                    (),
                    (
                        *source.candidates,
                        ProviderCandidate(
                            other,
                            "other-work",
                            tuple(
                                _claim(
                                    name,
                                    other,
                                    c.kind,
                                    cast(
                                        str, c.value
                                    ),  # All claims above carry literal text.
                                    scope=c.scope,
                                    role=c.role,
                                    order=c.raw_order,
                                    applicability=c.applicability,
                                )
                                for c in claims
                            ),
                            (),
                        ),
                    ),
                    authority=name,
                )
            source = qualify_provider_observation(
                source,
                MetadataLookup(
                    ProviderQueryKind.ISBN,
                    tuple(local),
                    matched_family=lane.matched_family,
                ),
            )
            lanes.append(ProviderLaneResult(lane, source.status, source.candidates))
        providers.append(provider_observation(name, tuple(lanes), authority=name))
    if failed:
        providers.append(
            ProviderObservation(
                "unavailable_source",
                ProviderStatus.UNAVAILABLE,
                (),
                (),
                authority="google_books",
            )
        )
    return _report(
        fields=fields,
        local_claims=tuple(local),
        providers=tuple(reversed(providers) if reverse else providers),
    )
