"""Proposal publication dates: work-relation non-voting, precision
derivation, timestamp offset eligibility, and the shared-month and
shared-year projections.
"""

from dataclasses import replace

import pytest

from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderQueryKind,
    PublicationDateRelation,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from tests.support.proposal_evidence import (
    _contradictory_publication_provider,
    _fields,
    _provider,
    _publication_local_isbn,
    _publication_provider,
    _report,
    _result,
)


def test_work_publication_date_never_votes_for_manifestation_fields() -> None:
    evidence = (
        ProviderEvidence(
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            "2001-02-03",
            ClaimScope.WORK,
        ),
    )
    provider = _provider("catalogue", "edition-1", (), evidence)
    fields = _fields(
        published_year=TargetField(
            TargetDestination.PUBLISHED_YEAR,
            TargetFieldState.VALUE,
            "1999",
        ),
        published_date=TargetField(
            TargetDestination.PUBLISHED_DATE,
            TargetFieldState.MISSING,
        ),
    )
    proposal_set = evaluate_report(
        _report(fields=fields, providers=(provider,))
    )
    date_result = _result(
        proposal_set, TargetDestination.PUBLISHED_DATE
    )
    assert ProposalOutcome.INELIGIBLE == date_result.outcome


def test_contradictory_work_relation_date_remains_non_voting() -> None:
    proposal_set = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=(_contradictory_publication_provider(),),
        )
    )
    assert ProposalOutcome.INELIGIBLE == _result(
            proposal_set, TargetDestination.PUBLISHED_YEAR
        ).outcome
    assert ProposalOutcome.INELIGIBLE == _result(
            proposal_set, TargetDestination.PUBLISHED_DATE
        ).outcome


def test_month_precision_derives_only_the_stated_year() -> None:
    proposal = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=(
                _publication_provider(
                    "catalogue", "edition-1", ("2001-02",)
                ),
            ),
        )
    )
    year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
    published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.PROPOSE_CHANGE == year.outcome
    assert "2001" == year.alternatives[0].value.value
    assert DateEvent.MANIFESTATION_PUBLICATION == year.alternatives[0].date_event
    assert DatePrecision.YEAR == year.alternatives[0].date_precision
    assert ProposalOutcome.INELIGIBLE == published_date.outcome


def test_signed_offset_timestamp_reduces_to_day_without_losing_source_precision() -> None:
    lexical = "2001-02-03T04:05:06-04:00"
    provider = _publication_provider(
        "catalogue", "edition-1", (lexical,)
    )
    claim = provider.candidates[0].claims[1]
    proposal = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=(provider,),
        )
    )
    published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.UNRESOLVED_CHOICE == published_date.outcome
    assert "date_refinement_requires_corroboration" in published_date.reasons
    assert "2001-02-03" == published_date.alternatives[0].value.value
    assert DatePrecision.DAY == published_date.alternatives[0].date_precision
    assert lexical == claim.date_lexical
    assert DatePrecision.TIMESTAMP == claim.date_precision


@pytest.mark.parametrize("include_local_date", (False, True))
@pytest.mark.parametrize("provider_count", (1, 2))
def test_naive_timestamps_never_vote_with_any_provider_or_local_date_count(
    provider_count: int, include_local_date: bool
) -> None:
    naive = "2001-02-03T04:05:06"
    local_date = LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed="2001",
        canonical="2001",
        excerpt="2001",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=DatePrecision.YEAR,
    )
    providers = tuple(
        _publication_provider(
            f"catalogue_{index}",
            f"edition-{index}",
            (naive,),
        )
        for index in range(provider_count)
    )
    report = _report(
        fields=_fields(
            published_year=TargetField(
                TargetDestination.PUBLISHED_YEAR,
                TargetFieldState.MISSING,
            ),
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE,
                TargetFieldState.MISSING,
            ),
        ),
        local_claims=(
            _publication_local_isbn(),
            *((local_date,) if include_local_date else ()),
        ),
        providers=providers,
    )
    replayed = MetadataReport.from_json(report.to_json())
    proposal = evaluate_report(replayed)
    visible_naive = [
        evidence
        for provider in replayed.providers
        for candidate in provider.candidates
        for evidence in candidate.evidence
        if evidence.kind
        is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        and evidence.value == naive
        and evidence.date_precision is DatePrecision.TIMESTAMP
    ]
    assert proposal.to_json() == evaluate_report(
            MetadataReport.from_json(replayed.to_json())
        ).to_json()
    assert provider_count == len(visible_naive)
    date = _result(
        proposal, TargetDestination.PUBLISHED_DATE
    )
    assert all(
            set(alternative.evidence_references)
            <= ({local_date.reference} if include_local_date else set())
            for alternative in date.alternatives
        )
    if not include_local_date:
        assert ProposalOutcome.INELIGIBLE == date.outcome


@pytest.mark.parametrize("include_local_date", (False, True))
@pytest.mark.parametrize("provider_count", (1, 2))
@pytest.mark.parametrize(
    ("lexical", "expected_voting"),
    (
        ("2001-02-03T04:05:06Z", True),
        ("2001-02-03T04:05:06+01:00", True),
        ("2001-02-03T04:05:06+00:00:30", False),
        ("2001-02-03T04:05:06+01:02:03.500000", False),
    ),
)
def test_timestamp_offset_eligibility_is_identical_in_report_and_proposals(
    lexical: str, expected_voting: bool, provider_count: int, include_local_date: bool
) -> None:
    local_date = LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed="2001-02-03",
        canonical="2001-02-03",
        excerpt="2001-02-03",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=DatePrecision.DAY,
    )
    providers = tuple(
        _publication_provider(
            f"catalogue_{index}",
            f"edition-{index}",
            (lexical,),
        )
        for index in range(provider_count)
    )
    provider_references = {
        claim.reference
        for provider in providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.PUBLICATION_DATE
    }
    report = MetadataReport.from_json(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(
                _publication_local_isbn(),
                *((local_date,) if include_local_date else ()),
            ),
            providers=providers,
        ).to_json()
    )
    retained_claims = [
        claim
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.PUBLICATION_DATE
    ]
    retained_text = [
        evidence
        for provider in report.providers
        for candidate in provider.candidates
        for evidence in candidate.evidence
        if evidence.kind
        is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        and evidence.value == lexical
    ]
    proposal = evaluate_report(report)
    proposal_references = {
        reference
        for result in proposal.results
        for alternative in result.alternatives
        for reference in alternative.evidence_references
    }
    assert (provider_count if expected_voting else 0) == len(retained_claims)
    assert (0 if expected_voting else provider_count) == len(retained_text)
    if expected_voting:
        assert provider_references.issubset(
                proposal_references
            )
    else:
        assert provider_references.isdisjoint(
                proposal_references
            )


def test_unresolved_provider_publication_text_is_visible_but_nonselectable() -> None:
    evidence = (
        ProviderEvidence(
            ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
            "2001-02-03",
            ClaimScope.EDITION,
        ),
    )
    report = _report(
        title="Target title",
        fields=_fields(
            "Target title",
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Author",),
            ),
            published_year=TargetField(
                TargetDestination.PUBLISHED_YEAR,
                TargetFieldState.MISSING,
            ),
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE,
                TargetFieldState.MISSING,
            ),
        ),
        local_claims=(),
        providers=(
            replace(
                _provider("catalogue", "edition-1", (), evidence),
                query_kind=ProviderQueryKind.WORK,
            ),
        ),
    )
    proposal = evaluate_report(report)
    assert ProposalOutcome.INELIGIBLE == _result(proposal, TargetDestination.PUBLISHED_YEAR).outcome
    retained = report.providers[0].candidates[0].evidence[0]
    assert PublicationDateRelation.MANIFESTATION_INCOMPATIBLE == retained.links[0].publication_date_relation
    assert retained.links[0].local_reference is None
    assert DateEvent.UNKNOWN == evidence[0].date_event


def test_different_years_remain_conflicting_at_the_year_destination() -> None:
    providers = tuple(
        _publication_provider(
            provider,
            f"{provider}-edition",
            (value,),
            authority=provider,
        )
        for provider, value in (
            ("hardcover", "2001-02-03"),
            ("open_library", "2002-03-04"),
        )
    )
    proposal = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=providers,
        )
    )
    year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
    published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.UNRESOLVED_CHOICE == year.outcome
    assert ["2001", "2002"] == sorted(str(value.value.value) for value in year.alternatives)
    assert ProposalOutcome.INELIGIBLE == published_date.outcome
    assert () == published_date.alternatives


def test_full_date_refining_a_populated_year_needs_independent_sources() -> None:
    year = TargetField(
        TargetDestination.PUBLISHED_YEAR,
        TargetFieldState.VALUE,
        "2001",
    )
    providers = tuple(
        _publication_provider(
            provider,
            f"{provider}-edition",
            ("2001-02-03",),
            authority=authority,
        )
        for provider, authority in (
            ("alpha", "hardcover"),
            ("beta", "open_library"),
        )
    )
    one_source = _result(
        evaluate_report(
            _report(
                fields=_fields(published_year=year),
                local_claims=(_publication_local_isbn(),),
                providers=(providers[0],),
            )
        ),
        TargetDestination.PUBLISHED_DATE,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE == one_source.outcome
    assert "date_refinement_requires_corroboration" in one_source.reasons
    duplicate_authority = _result(
        evaluate_report(
            _report(
                fields=_fields(published_year=year),
                local_claims=(_publication_local_isbn(),),
                providers=(
                    providers[0],
                    replace(providers[1], authority="hardcover"),
                ),
            )
        ),
        TargetDestination.PUBLISHED_DATE,
    )
    assert ProposalOutcome.UNRESOLVED_CHOICE == duplicate_authority.outcome
    assert "date_refinement_requires_corroboration" in duplicate_authority.reasons
    two_sources = _result(
        evaluate_report(
            _report(
                fields=_fields(published_year=year),
                local_claims=(_publication_local_isbn(),),
                providers=providers,
            )
        ),
        TargetDestination.PUBLISHED_DATE,
    )
    assert ProposalOutcome.PROPOSE_CHANGE == two_sources.outcome


def test_year_only_edition_evidence_proposes_only_published_year() -> None:
    proposal_set = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=(
                _publication_provider(
                    "catalogue", "edition-1", ("2001",)
                ),
            ),
        )
    )
    year_result = _result(
        proposal_set, TargetDestination.PUBLISHED_YEAR
    )
    assert ProposalOutcome.PROPOSE_CHANGE == year_result.outcome
    assert "2001" == year_result.alternatives[0].value.value
    assert ProposalOutcome.INELIGIBLE == _result(
            proposal_set, TargetDestination.PUBLISHED_DATE
        ).outcome


def test_day_and_year_support_year_twice_but_day_once() -> None:
    proposal = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=(
                _publication_provider(
                    "alpha",
                    "alpha-edition",
                    ("2001-02-03",),
                    authority="hardcover",
                ),
                _publication_provider(
                    "beta",
                    "beta-edition",
                    ("2001",),
                    authority="open_library",
                ),
            ),
        )
    )
    year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
    published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.PROPOSE_CHANGE == year.outcome
    assert "2001" == year.alternatives[0].value.value
    assert 2 == len(year.alternatives[0].correlations)
    assert ProposalOutcome.UNRESOLVED_CHOICE == published_date.outcome
    assert 1 == len(published_date.alternatives[0].correlations)


def test_day_and_month_project_shared_month_with_union_support() -> None:
    proposal = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=(
                _publication_provider(
                    "alpha",
                    "alpha-edition",
                    ("2001-02-03",),
                    authority="hardcover",
                ),
                _publication_provider(
                    "beta",
                    "beta-edition",
                    ("2001-02",),
                    authority="open_library",
                ),
            ),
        )
    )
    year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
    published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.PROPOSE_CHANGE == year.outcome
    assert 2 == len(year.alternatives[0].correlations)
    assert ProposalOutcome.PROPOSE_CHANGE == published_date.outcome
    assert ("2001-02", DatePrecision.MONTH) == (
            published_date.alternatives[0].value.value,
            published_date.alternatives[0].date_precision,
        )
    assert 2 == len(published_date.alternatives[0].correlations)
    assert 2 == len(published_date.alternatives[0].evidence_references)


def test_same_month_day_conflicts_project_only_shared_month() -> None:
    proposal = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=(
                _publication_provider(
                    "alpha",
                    "alpha-edition",
                    ("2001-02-03",),
                    authority="hardcover",
                ),
                _publication_provider(
                    "beta",
                    "beta-edition",
                    ("2001-02-09",),
                    authority="open_library",
                ),
            ),
        )
    )
    published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.PROPOSE_CHANGE == published_date.outcome
    assert 1 == len(published_date.alternatives)
    alternative = published_date.alternatives[0]
    assert "2001-02" == alternative.value.value
    assert DateEvent.MANIFESTATION_PUBLICATION == alternative.date_event
    assert DatePrecision.MONTH == alternative.date_precision
    assert 2 == len(alternative.correlations)
    assert 2 == len(alternative.evidence_references)
    assert proposal == ProposalSet.from_json(proposal.to_json())


def test_different_months_preserve_only_shared_year() -> None:
    proposal = evaluate_report(
        _report(
            fields=_fields(
                published_year=TargetField(
                    TargetDestination.PUBLISHED_YEAR,
                    TargetFieldState.MISSING,
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE,
                    TargetFieldState.MISSING,
                ),
            ),
            local_claims=(_publication_local_isbn(),),
            providers=(
                _publication_provider(
                    "alpha",
                    "alpha-edition",
                    ("2001-02-03",),
                    authority="hardcover",
                ),
                _publication_provider(
                    "beta",
                    "beta-edition",
                    ("2001-08-09",),
                    authority="open_library",
                ),
            ),
        )
    )
    year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
    published_date = _result(proposal, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.PROPOSE_CHANGE == year.outcome
    assert "2001" == year.alternatives[0].value.value
    assert 2 == len(year.alternatives[0].correlations)
    assert ProposalOutcome.INELIGIBLE == published_date.outcome
    assert () == published_date.alternatives
