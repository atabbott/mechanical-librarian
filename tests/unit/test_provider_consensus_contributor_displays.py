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
    values: str | tuple[str, ...],
    *,
    local_claims: tuple[LocalClaim, ...],
    destination: TargetDestination,
    role: str | None = None,
) -> ProviderObservation:
    kind, scope, default_role = (
        (ClaimKind.CREATOR, ClaimScope.WORK, "author")
        if destination is TargetDestination.AUTHORS
        else (ClaimKind.CONTRIBUTOR, ClaimScope.EDITION, "narrator")
    )
    claims = tuple(
        _claim(
            name,
            f"{name}-edition",
            kind,
            value,
            scope=scope,
            order=index,
            role=default_role if role is None else role,
        )
        for index, value in enumerate((values,) if type(values) is str else values)
    )
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
    local: str | tuple[str, ...],
    provider_values: tuple[str | tuple[str, ...], ...],
    provider_roles: tuple[str | None, ...] | None = None,
):
    local_values = (local,) if type(local) is str else local
    local_claims = tuple(
        (
            _local_creator(value, occurrence=index)
            if destination is TargetDestination.AUTHORS
            else _local_narrator(value)
        )
        for index, value in enumerate(local_values)
    )
    fields = _fields(
        authors=TargetField(TargetDestination.AUTHORS, TargetFieldState.MISSING),
        narrators=TargetField(TargetDestination.NARRATORS, TargetFieldState.MISSING),
    )
    values = {field.destination: field for field in fields}
    values[destination] = TargetField(destination, TargetFieldState.VALUE, local_values)
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
                    role=(provider_roles or (None,) * len(provider_values))[index],
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


def test_provider_consensus_does_not_replace_a_non_equivalent_author() -> None:
    result = _result(
        destination=TargetDestination.AUTHORS,
        local="Ada Example",
        provider_values=("Bea Example", "Bea Example"),
    )

    assert result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
    assert result.reasons == ("competing_evidence",)


def test_provider_consensus_does_not_replace_an_author_sequence_mismatch() -> None:
    result = _result(
        destination=TargetDestination.AUTHORS,
        local=("Example, Ada", "Bert Example"),
        provider_values=(
            ("Bert Example", "Ada Example"),
            ("Bert Example", "Ada Example"),
        ),
    )

    assert result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
    assert result.reasons == ("competing_evidence",)


def test_provider_consensus_does_not_replace_an_author_from_narrator_claims() -> None:
    result = _result(
        destination=TargetDestination.AUTHORS,
        local="Example, Ada",
        provider_values=("Ada Example", "Ada Example"),
        provider_roles=("narrator", "narrator"),
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
