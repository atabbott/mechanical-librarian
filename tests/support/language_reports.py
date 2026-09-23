"""Synthetic language evidence shared by producer, persistence and owner journeys."""

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    audio_local_claims,
)
from tests.support.proposal_evidence import _report


def language_report(
    values: tuple[str, ...], current: str | None = None, *, audio: bool = False
) -> MetadataReport:
    claims = tuple(
        LocalClaim.create(
            kind=ClaimKind.LANGUAGE,
            observed=value,
            canonical=value,
            excerpt=value,
            applicability="not_applicable",
            checksum="not_applicable",
            applicability_basis="not_applicable",
            scope=ClaimScope.EDITION,
            basis="embedded_tag" if audio else "opening_matter",
            occurrence=index + 3,
        )
        for index, value in enumerate(values)
    )
    # A separate manual field keeps preservation-only cases reachable in detail.
    manual = LocalClaim.create(
        kind=ClaimKind.DESCRIPTION,
        observed="Synthetic description",
        canonical="Synthetic description",
        excerpt="Synthetic description",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.WORK,
    )
    if audio:
        base = admitted_audiobook_report(
            tuple(
                claim
                for claim in audio_local_claims()
                if claim.kind is not ClaimKind.LANGUAGE
            )
            + claims
            + (manual,)
        )
    else:
        base = _report(local_claims=claims + (manual,))
    field = TargetField(
        TargetDestination.LANGUAGE,
        TargetFieldState.MISSING if current is None else TargetFieldState.VALUE,
        current,
    )
    fields = tuple(
        field if value.destination is TargetDestination.LANGUAGE else value
        for value in base.target.fields
    )
    return MetadataReport.create(
        target=TargetFacts(
            base.target.connection, base.target.library, base.target.item, fields
        ),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        audio=base.audio,
    )


def publish_language_case(
    repository, report, destination="automatic", option="display_name"
):
    """Publish the actual owner policy and language outcome for browser fixtures."""
    import json

    from mechanical_librarian.domain.metadata_policy import (
        DEFAULT_OPTIONS,
        OPTION_CATALOGUE,
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report

    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    destinations["language_rendering"] = destination
    profile = MetadataPolicyProfile.create(
        "Language evidence",
        dict(DEFAULT_OPTIONS) | {"language_rendering": option},
        destinations,
    )
    resolved = resolve_policy(profile, report.target.library)
    repository.enter_shadow_preview(1)
    repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        MetadataPolicyProfile.builtin().policy_id,
        ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
        2,
    )
    if destination == "automatic":
        repository.activate_qualified_automatic(3)
    proposal = evaluate_report(
        report, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    repository.persist_shadow_outcome(case_id, 4)
    return case_id, proposal
