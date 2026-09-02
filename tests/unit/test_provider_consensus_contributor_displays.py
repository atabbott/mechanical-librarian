"""Closed provider-consensus contributor display election cases."""

from __future__ import annotations

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    ProviderCandidate,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    finalize_candidate_comparisons,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from tests.support.proposal_evidence import _claim, _fields, _local_creator, _report


def _local_narrator(value: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.CONTRIBUTOR,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        role="narrator",
    )


def _provider_contributors(
    name: str,
    value: str,
    *,
    local_claims: tuple[LocalClaim, ...],
    destination: TargetDestination,
) -> ProviderObservation:
    kind, scope, role = (
        (ClaimKind.CREATOR, ClaimScope.WORK, "author")
        if destination is TargetDestination.AUTHORS
        else (ClaimKind.CONTRIBUTOR, ClaimScope.EDITION, "narrator")
    )
    claims = (_claim(name, f"{name}-edition", kind, value, scope=scope, role=role),)
    return ProviderObservation(
        name,
        ProviderStatus.CANDIDATES,
        candidates=(
            finalize_candidate_comparisons(
                ProviderCandidate(f"{name}-edition", f"{name}-work", claims),
                local_claims,
            ),
        ),
        authority=name,
    )


def _result(
    *,
    destination: TargetDestination,
    local: str,
    provider_values: tuple[str, ...],
):
    local_claims = (
        _local_creator(local)
        if destination is TargetDestination.AUTHORS
        else _local_narrator(local),
    )
    fields = _fields(
        authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING),
        narrators=TargetField(TargetDestination.NARRATORS, TargetFieldState.MISSING),
    )
    values = {field.destination: field for field in fields}
    values[destination] = TargetField(destination, TargetFieldState.VALUE, (local,))
    proposal = evaluate_report(
        _report(
            fields=tuple(values[value] for value in TargetDestination),
            local_claims=local_claims,
            providers=tuple(
                _provider_contributors(
                    ("open_library", "hardcover")[index],
                    value,
                    local_claims=local_claims,
                    destination=destination,
                )
                for index, value in enumerate(provider_values)
            ),
        )
    )
    return next(value for value in proposal.results if value.destination is destination)


def test_provider_consensus_elects_exact_author_display_with_local_provenance() -> None:
    result = _result(
        destination=TargetDestination.AUTHORS,
        local="Example, Ada",
        provider_values=("Ada Example", "Ada Example"),
    )

    assert result.outcome is ProposalOutcome.PROPOSE_CHANGE
    assert result.reasons == ("provider_consensus_contributor_display",)
    assert result.alternatives[0].value.value == ("Ada Example",)
    assert any(
        reference.startswith("local_")
        for reference in result.alternatives[0].evidence_references
    )
    assert len(result.alternatives[0].correlations) == 3


def test_provider_consensus_does_not_replace_an_exact_current_author_display() -> None:
    result = _result(
        destination=TargetDestination.AUTHORS,
        local="Ada Example",
        provider_values=("Ada Example", "Ada Example"),
    )

    assert result.outcome is ProposalOutcome.KEEP
    assert result.reasons == ("positive_preservation_evidence",)


def test_provider_display_disagreement_keeps_author_current() -> None:
    result = _result(
        destination=TargetDestination.AUTHORS,
        local="Example, Ada",
        provider_values=("Ada Example", "A. Example"),
    )

    assert result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
    assert result.reasons == ("competing_evidence",)


def test_one_provider_author_display_does_not_form_a_consensus() -> None:
    result = _result(
        destination=TargetDestination.AUTHORS,
        local="Example, Ada",
        provider_values=("Ada Example",),
    )

    assert result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
    assert result.reasons == ("competing_evidence",)


def test_provider_consensus_elects_a_narrator_display_only_from_existing_agreement() -> (
    None
):
    result = _result(
        destination=TargetDestination.NARRATORS,
        local="Ada\u00a0Example",
        provider_values=("Ada Example", "Ada Example"),
    )

    assert result.outcome is ProposalOutcome.PROPOSE_CHANGE
    assert result.reasons == ("provider_consensus_contributor_display",)
