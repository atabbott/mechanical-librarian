"""Canonical, independently corroborated publication facts."""

from dataclasses import replace

import pytest

from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import ProposalOutcome, evaluate_report
from tests.support.proposal_evidence import (
    _claim,
    _contradictory_publication_provider,
    _fields,
    _provider,
    _publication_local_isbn,
    _publication_provider,
    _report,
    _result,
)


def _local_date(value: str, precision: DatePrecision) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.PUBLICATION_DATE,
        observed=value,
        canonical=value,
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        scope=ClaimScope.EDITION,
        date_event=DateEvent.MANIFESTATION_PUBLICATION,
        date_precision=precision,
    )


def _publication(values: tuple[str, str], *, current_year: str | None = None):
    return evaluate_report(
        _report(
            fields=_fields(
                published_year=(
                    TargetField(
                        TargetDestination.PUBLISHED_YEAR,
                        TargetFieldState.VALUE,
                        current_year,
                    )
                    if current_year is not None
                    else TargetField(
                        TargetDestination.PUBLISHED_YEAR, TargetFieldState.MISSING
                    )
                ),
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
                ),
            ),
            local_claims=(
                _publication_local_isbn(),
                _local_date(values[0], DatePrecision.DAY),
            ),
            providers=(
                _publication_provider(
                    "alpha", "alpha-edition", (values[0],), authority="hardcover"
                ),
                _publication_provider(
                    "beta", "beta-edition", (values[1],), authority="open_library"
                ),
            ),
        )
    )


def _wrong_work_publication_provider(
    name: str, authority: str, *, inverted: bool = False
):
    provider = _publication_provider(
        name, f"{name}-edition", ("2001-02-03",), authority=authority
    )
    candidate = provider.candidates[0]
    return replace(
        provider,
        candidates=(
            replace(
                candidate,
                claims=(
                    _claim(
                        name,
                        candidate.identity,
                        ClaimKind.TITLE,
                        "Example Book" if inverted else "Different Book",
                        scope=ClaimScope.WORK,
                    ),
                    _claim(
                        name,
                        candidate.identity,
                        ClaimKind.CREATOR,
                        "Example, Ada" if inverted else "Other Author",
                        scope=ClaimScope.WORK,
                        role="author",
                    ),
                    *candidate.claims,
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    "complete_identity", ("unavailable", "contradictory", "inverted")
)
def test_only_explicit_wrong_work_evidence_loses_publication_votes(
    complete_identity: str,
) -> None:
    providers = (
        (
            _wrong_work_publication_provider(
                "alpha", "hardcover", inverted=complete_identity == "inverted"
            ),
            _wrong_work_publication_provider(
                "beta", "open_library", inverted=complete_identity == "inverted"
            ),
        )
        if complete_identity != "unavailable"
        else (
            _publication_provider(
                "alpha", "alpha-edition", ("2001-02-03",), authority="hardcover"
            ),
            _publication_provider(
                "beta", "beta-edition", ("2001-02-03",), authority="open_library"
            ),
        )
    )
    report = _report(
        title="Example Book",
        fields=_fields(
            "Example Book",
            authors=TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.VALUE,
                ("Ada Example",),
            ),
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
            ),
        ),
        local_claims=(
            _publication_local_isbn(),
            _local_date("2001-02-03", DatePrecision.DAY),
        ),
        providers=providers,
    )

    result = _result(evaluate_report(report), TargetDestination.PUBLISHED_DATE)

    assert 2 == len(report.providers)
    assert (
        ProposalOutcome.INELIGIBLE
        if complete_identity == "contradictory"
        else ProposalOutcome.PROPOSE_CHANGE
    ) is result.outcome


def test_strongest_shared_year_month_and_day_are_one_canonical_date_fact() -> None:
    for values, expected, precision in (
        (("2001-02-03", "2001-02-03"), "2001-02-03", DatePrecision.DAY),
        (("2001-02-03", "2001-02-09"), "2001-02", DatePrecision.MONTH),
        (("2001-02-03", "2001-08-09"), "2001", DatePrecision.YEAR),
    ):
        proposal = _publication(values)
        date = _result(proposal, TargetDestination.PUBLISHED_DATE)
        year = _result(proposal, TargetDestination.PUBLISHED_YEAR)
        assert ProposalOutcome.PROPOSE_CHANGE is date.outcome
        assert expected == date.alternatives[0].value.value
        assert precision is date.alternatives[0].date_precision
        assert ProposalOutcome.INELIGIBLE is year.outcome
        assert () == year.alternatives
        assert ("redundant_publication_context",) == year.reasons


def test_local_and_fewer_than_two_distinct_provider_authorities_abstain() -> None:
    report = _report(
        fields=_fields(
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
            )
        ),
        local_claims=(
            _publication_local_isbn(),
            _local_date("2001-02", DatePrecision.MONTH),
        ),
        providers=(
            _publication_provider(
                "alpha", "alpha-edition", ("2001-02",), authority="hardcover"
            ),
            _publication_provider(
                "beta", "beta-edition", ("2001-02",), authority="hardcover"
            ),
        ),
    )
    assert (
        ProposalOutcome.INELIGIBLE
        is _result(evaluate_report(report), TargetDestination.PUBLISHED_DATE).outcome
    )


def test_provider_only_and_local_only_date_evidence_abstain() -> None:
    providers = (
        _publication_provider(
            "alpha", "alpha-edition", ("2001",), authority="hardcover"
        ),
        _publication_provider(
            "beta", "beta-edition", ("2001",), authority="open_library"
        ),
    )
    for local_claims, selected in (
        ((_publication_local_isbn(),), providers),
        (
            (_publication_local_isbn(), _local_date("2001", DatePrecision.YEAR)),
            providers[:1],
        ),
    ):
        report = _report(
            fields=_fields(
                published_date=TargetField(
                    TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
                )
            ),
            local_claims=local_claims,
            providers=selected,
        )
        assert (
            ProposalOutcome.INELIGIBLE
            is _result(
                evaluate_report(report), TargetDestination.PUBLISHED_DATE
            ).outcome
        )


def test_current_year_is_canonical_published_date_context() -> None:
    proposal = _publication(("2001-02-03", "2001-08-09"), current_year="2001")
    date = _result(proposal, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.KEEP is date.outcome
    assert "2001" == date.alternatives[0].value.value


def test_work_and_incompatible_manifestation_dates_never_vote() -> None:
    work_only = _provider(
        "work",
        "work-edition",
        (),
        (
            ProviderEvidence(
                ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                "2001-02-03",
                ClaimScope.WORK,
            ),
        ),
        authority="hardcover",
    )
    for provider in (work_only, _contradictory_publication_provider()):
        proposal = evaluate_report(
            _report(
                fields=_fields(
                    published_date=TargetField(
                        TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
                    )
                ),
                local_claims=(
                    _publication_local_isbn(),
                    _local_date("2001-02-03", DatePrecision.DAY),
                ),
                providers=(provider,),
            )
        )
        assert (
            ProposalOutcome.INELIGIBLE
            is _result(proposal, TargetDestination.PUBLISHED_DATE).outcome
        )


@pytest.mark.parametrize(
    ("providers", "reason"),
    (
        pytest.param(
            (
                _publication_provider(
                    "alpha", "alpha-edition", ("2001-02-03",), authority="hardcover"
                ),
                _publication_provider(
                    "beta", "beta-edition", ("2001-02-03",), authority="hardcover"
                ),
            ),
            "duplicate_authority",
            id="duplicate_authority",
        ),
        pytest.param(
            (
                _publication_provider(
                    "alpha", "alpha-edition", ("2001-02-03",), authority="hardcover"
                ),
                replace(
                    _publication_provider(
                        "beta",
                        "beta-edition",
                        ("2001-02-03",),
                        authority="open_library",
                    ),
                    status=ProviderStatus.UNAVAILABLE,
                    candidates=(),
                ),
            ),
            "failed_provider",
            id="failed_provider",
        ),
        pytest.param(
            (
                _publication_provider(
                    "alpha", "alpha-edition", ("2001-02-03",), authority="hardcover"
                ),
                _publication_provider(
                    "beta", "beta-edition", ("2002-02-03",), authority="open_library"
                ),
            ),
            "conflict",
            id="conflict",
        ),
    ),
)
def test_only_two_distinct_matching_provider_authorities_qualify(
    providers, reason: str
) -> None:
    report = _report(
        fields=_fields(
            published_date=TargetField(
                TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
            )
        ),
        local_claims=(
            _publication_local_isbn(),
            _local_date("2001-02-03", DatePrecision.DAY),
        ),
        providers=providers,
    )
    assert reason
    assert (
        ProposalOutcome.INELIGIBLE
        is _result(evaluate_report(report), TargetDestination.PUBLISHED_DATE).outcome
    )


def test_offset_timestamp_normalizes_to_day_and_provider_order_replays() -> None:
    lexical = "2001-02-03T04:05:06-04:00"
    first = _publication_provider(
        "alpha", "alpha-edition", (lexical,), authority="hardcover"
    )
    second = _publication_provider(
        "beta", "beta-edition", (lexical,), authority="open_library"
    )
    fields = _fields(
        published_date=TargetField(
            TargetDestination.PUBLISHED_DATE, TargetFieldState.MISSING
        )
    )
    local_claims = (
        _publication_local_isbn(),
        _local_date("2001-02-03", DatePrecision.DAY),
    )
    forward = evaluate_report(
        _report(fields=fields, local_claims=local_claims, providers=(first, second))
    )
    reverse = evaluate_report(
        _report(fields=fields, local_claims=local_claims, providers=(second, first))
    )
    date = _result(forward, TargetDestination.PUBLISHED_DATE)
    assert ProposalOutcome.PROPOSE_CHANGE is date.outcome
    assert ("2001-02-03", DatePrecision.DAY) == (
        date.alternatives[0].value.value,
        date.alternatives[0].date_precision,
    )
    assert forward == reverse
