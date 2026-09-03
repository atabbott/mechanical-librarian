"""Closed provider-consensus title election cases."""

from __future__ import annotations

from dataclasses import replace

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
from tests.support.proposal_evidence import (
    _fields,
    _report,
)
from tests.support.proposal_evidence import (
    consensus_local_title as _local_title,
)
from tests.support.proposal_evidence import (
    consensus_provider_title as _provider_title,
)


def _title_result(
    *,
    local: str,
    providers: tuple,
    current: str | None = None,
    current_subtitle: str | None = None,
    local_claims: tuple[LocalClaim, ...] | None = None,
):
    _, title, _ = _title_and_subtitle_results(
        local=local,
        providers=providers,
        current=current,
        current_subtitle=current_subtitle,
        local_claims=local_claims,
    )
    return title


def _title_and_subtitle_results(
    *,
    local: str,
    providers: tuple,
    current: str | None = None,
    current_subtitle: str | None = None,
    local_claims: tuple[LocalClaim, ...] | None = None,
):
    report = _report(
        title=local if current is None else current,
        fields=_fields(
            local if current is None else current,
            title=TargetField(
                TargetDestination.TITLE,
                TargetFieldState.VALUE,
                local if current is None else current,
            ),
            **(
                {
                    "subtitle": TargetField(
                        TargetDestination.SUBTITLE,
                        TargetFieldState.VALUE,
                        current_subtitle,
                    )
                }
                if current_subtitle is not None
                else {}
            ),
        ),
        local_claims=(_local_title(local),) if local_claims is None else local_claims,
        providers=providers,
    )
    results = {result.destination: result for result in evaluate_report(report).results}
    return report, results[TargetDestination.TITLE], results[TargetDestination.SUBTITLE]


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
    assert result.reasons == ("competing_evidence",)


def test_provider_consensus_excludes_null_authority_from_its_quorum() -> None:
    result = _title_result(
        local="Plain Work [retail]",
        providers=(
            _provider_title("open_library", "Plain Work"),
            replace(_provider_title("hardcover", "Plain Work"), authority=None),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert result.reasons == ("competing_evidence",)


def test_provider_consensus_keeps_null_authority_titles_as_dissent() -> None:
    result = _title_result(
        local="Plain Work [retail]",
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
            replace(_provider_title("google_books", "Different Work"), authority=None),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert result.reasons == ("competing_evidence",)


def test_provider_consensus_rejects_non_source_bracket_qualifiers() -> None:
    result = _title_result(
        local="Plain Work [Illustrated Edition]",
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert result.reasons == ("competing_evidence",)


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
    assert dissent.reasons == ("competing_evidence",)
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
    assert result.reasons == ("competing_evidence",)


def test_provider_consensus_requires_every_provider_title_observation_to_match() -> (
    None
):
    result = _title_result(
        local="Plain Work [retail]",
        providers=(
            _provider_title("open_library", "Plain Work", edition_title="plain work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert result.reasons == ("competing_evidence",)


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


def test_colon_title_elects_when_the_current_structured_subtitle_is_exact() -> None:
    result = _title_result(
        local="Plain Work: A subtitle",
        current_subtitle="A subtitle",
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert result.reasons == ("provider_consensus_title_subtitle_segment",)


def test_colon_title_elects_when_provider_subtitle_candidate_preserves_suffix() -> None:
    result = _title_result(
        local="Plain Work: A subtitle",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle="A subtitle"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome
    assert result.reasons == ("provider_consensus_title_subtitle_segment",)


def test_colon_subtitle_corroboration_withholds_against_provider_subtitle_dissent() -> (
    None
):
    _, _, subtitle = _title_and_subtitle_results(
        local="Plain Work: A subtitle",
        providers=(
            _provider_title("open_library", "Plain Work", subtitle="A subtitle"),
            _provider_title("hardcover", "Plain Work", subtitle="Other subtitle"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is subtitle.outcome
    assert subtitle.reasons == (
        "competing_evidence",
        "subtitle_segment_provider_dissent",
    )


def test_an_explicit_local_subtitle_claim_still_elects_against_dissent() -> None:
    report, _, subtitle = _title_and_subtitle_results(
        local="Plain Work",
        local_claims=(
            _local_title("Plain Work"),
            LocalClaim.create(
                kind=ClaimKind.SUBTITLE,
                observed="A subtitle",
                canonical="A subtitle",
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                basis="package_metadata",
                excerpt="A subtitle",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                occurrence=1,
            ),
        ),
        providers=(
            _provider_title("open_library", "Plain Work", subtitle="A subtitle"),
            _provider_title("hardcover", "Plain Work", subtitle="Other subtitle"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is subtitle.outcome
    assert subtitle.reasons == ("local_attested_subtitle",)
    assert "A subtitle" == subtitle.alternatives[0].value.value
    assert report.local_claims


def test_competing_provider_subtitles_keep_an_uncorroborated_segment_visible() -> None:
    _, _, subtitle = _title_and_subtitle_results(
        local="Plain Work: A subtitle",
        providers=(
            _provider_title(
                "open_library", "Plain Work", subtitle="Different subtitle"
            ),
            _provider_title("hardcover", "Plain Work", subtitle="Other subtitle"),
        ),
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is subtitle.outcome
    assert subtitle.reasons == (
        "competing_evidence",
        "subtitle_segment_uncorroborated",
    )


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


def test_colon_title_does_not_use_local_or_mismatched_subtitle_as_corroboration() -> (
    None
):
    local_only = _title_result(
        local="Plain Work: A subtitle",
        local_claims=(
            _local_title("Plain Work: A subtitle"),
            LocalClaim.create(
                kind=ClaimKind.SUBTITLE,
                observed="A subtitle",
                canonical="A subtitle",
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                basis="package_metadata",
                excerpt="A subtitle",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                occurrence=1,
            ),
        ),
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )
    mismatch = _title_result(
        local="Plain Work: A subtitle",
        providers=(
            _provider_title(
                "open_library", "Plain Work", subtitle="Different subtitle"
            ),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert local_only.reasons == (
        "competing_evidence",
        "provider_consensus_title_colon_unused",
    )
    assert mismatch.reasons == (
        "competing_evidence",
        "provider_consensus_title_colon_unused",
    )


def test_provider_consensus_deduplicates_identical_eligible_local_titles() -> None:
    result = _title_result(
        local="Plain Work [retail]",
        local_claims=(
            _local_title("Plain Work [retail]"),
            _local_title("Plain Work [retail]", occurrence=1),
        ),
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert ProposalOutcome.PROPOSE_CHANGE is result.outcome


def test_provider_consensus_abstains_for_distinct_eligible_local_titles() -> None:
    result = _title_result(
        local="Plain Work [retail]",
        local_claims=(
            _local_title("Plain Work [retail]"),
            _local_title("Other Work [retail]", occurrence=1),
        ),
        providers=(
            _provider_title("open_library", "Plain Work"),
            _provider_title("hardcover", "Plain Work"),
        ),
    )

    assert result.reasons == ("unique_typed_candidate",)
    assert not result.reasons[0].startswith("provider_consensus_title_")


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
