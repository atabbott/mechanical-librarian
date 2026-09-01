"""Closed provider-consensus title election cases."""

from __future__ import annotations

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    evaluate_report,
)
from tests.support.proposal_evidence import _claim, _fields, _provider, _report


def _local_title(value: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=0,
    )


def _provider_title(name: str, title: str, *, authority: str | None = None):
    return _provider(
        name,
        f"{name}-edition",
        (
            _claim(
                name,
                f"{name}-edition",
                ClaimKind.TITLE,
                title,
                scope=ClaimScope.WORK,
            ),
        ),
        authority=name if authority is None else authority,
    )


def _title_result(*, local: str, providers: tuple, current: str | None = None):
    report = _report(
        title=local if current is None else current,
        fields=_fields(
            local if current is None else current,
            title=TargetField(
                TargetDestination.TITLE,
                TargetFieldState.VALUE,
                local if current is None else current,
            ),
        ),
        local_claims=(_local_title(local),),
        providers=providers,
    )
    return next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.TITLE
    )


def test_exact_provider_consensus_elects_a_single_bracket_source_title() -> None:
    result = _title_result(
        local="Plain Work [retail]",
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert ("provider_consensus_title_bracket_source_tag",) == result.reasons
    assert "Plain Work" == result.alternatives[0].value.value
    assert len(result.alternatives[0].correlations) == 2


def test_provider_consensus_requires_two_authority_correlations() -> None:
    result = _title_result(
        local="Plain Work [retail]",
        providers=(
            _provider_title("first", "Plain Work", authority="open_library"),
            _provider_title("second", "Plain Work", authority="open_library"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert result.reasons == (
        "competing_evidence",
        "provider_consensus_title_bracket_source_tag_unresolved",
    )


def test_provider_consensus_abstains_for_dissent_or_non_current_local_title() -> None:
    dissent = _title_result(
        local="Plain Work [retail]",
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Another Work"),
        ),
    )
    not_current = _title_result(
        local="Plain Work [retail]",
        current="Different current title",
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is dissent.outcome
    assert ProposalOutcome.UNRESOLVED_CHOICE is not_current.outcome
    assert "provider_consensus_title_bracket_source_tag_unresolved" in dissent.reasons
    assert not_current.reasons == ("competing_evidence",)


def test_provider_consensus_requires_byte_exact_provider_raw_titles() -> None:
    result = _title_result(
        local="Café [retail]",
        providers=(
            _provider_title("open_library", "Café"),
            _provider_title("hardcover", "Cafe\u0301"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert "provider_consensus_title_bracket_source_tag_unresolved" in result.reasons


def test_provider_decoration_never_inverts_the_local_consensus_direction() -> None:
    result = _title_result(
        local="Plain Work",
        providers=(
            _provider_title("open_library", "Plain Work [retail]"),
            _provider_title("hardcover", "Plain Work [retail]"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert result.reasons == ("competing_evidence",)


def test_colon_title_never_self_elects_and_keeps_a_closed_reason() -> None:
    result = _title_result(
        local="Plain Work: A subtitle",
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert result.reasons == (
        "competing_evidence",
        "provider_consensus_title_colon_unused",
    )


def test_series_name_removal_requires_the_structured_series_to_be_preserved() -> None:
    local = "Plain Work (The Sequence)"
    providers = (
        _provider_title("open_library", "Plain Work"),
        _provider_title("hardcover", "Plain Work"),
    )
    report = _report(
        fields=_fields(
            local,
            title=TargetField(TargetDestination.TITLE, TargetFieldState.VALUE, local),
            series=TargetField(
                TargetDestination.SERIES,
                TargetFieldState.VALUE,
                (TargetSeries("The Sequence", "1"),),
            ),
        ),
        local_claims=(_local_title(local),),
        providers=providers,
    )
    elected = next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.TITLE
    )
    unpreserved = _title_result(local=local, providers=providers)

    assert ProposalOutcome.PROPOSE_CHANGE is elected.outcome
    assert ProposalOutcome.UNRESOLVED_CHOICE is unpreserved.outcome
    assert "provider_consensus_title_series_unpreserved" in unpreserved.reasons
