"""Series and title choices from witnessed book evidence (#1253).

A recording without admitted identity proposes a series, a position and a
cleaned-up title only from a provider candidate that is itself a witnessed
work source. Lookup-only candidates, local tags and every other candidate stay
out, the subtitle stays out, and a choice whose alternatives only trim or
extend a usable description or genre list stays choosable without asking.
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    LocalClaim,
    MetadataReport,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.proposal_set import (
    WORK_ALTERNATIVE_IMMATERIAL_REASON,
    ProposalOutcome,
    ProposalResult,
    ProposalSet,
    ProposalValue,
    ProposalValueKind,
    _observations,
    _work_title_cleanup,
    audiobook_review_scope,
    evaluate_report,
)
from tests.support.audiobook_providers import audio_claim
from tests.support.audiobook_reports import (
    recording_lookup_report,
    synthetic_audiobook_product_lookup_report,
)
from tests.support.audiobook_synopsis import recording_synopsis_report, synopsis_report

_SAGA = {"name": "Example Saga", "position": "2"}


def _product(asin: str = "B000000001", **fields: object) -> dict[str, object]:
    return {
        "asin": asin,
        "title": "Synthetic Journey",
        "authors": [{"name": "Example Author"}],
        **fields,
    }


def _report(
    *products: dict[str, object],
    target_title: str = "Synthetic Journey",
    current: dict[TargetDestination, tuple[str, ...]] | None = None,
    local: tuple[LocalClaim, ...] = (),
) -> MetadataReport:
    report = synthetic_audiobook_product_lookup_report(
        products, target_title=target_title
    )
    values = current or {}
    return MetadataReport.create(
        target=replace(
            report.target,
            fields=tuple(
                TargetField(
                    field.destination, TargetFieldState.VALUE, values[field.destination]
                )
                if field.destination in values
                else field
                for field in report.target.fields
            ),
        ),
        file=report.file,
        local_claims=(*report.local_claims, *local),
        local_reasons=report.local_reasons,
        providers=report.providers,
        audio=report.audio,
    )


def _results(proposal: ProposalSet) -> dict[str, ProposalResult]:
    return {result.destination.value: result for result in proposal.results}


def _values(result: ProposalResult) -> set[object]:
    return {alternative.value.value for alternative in result.alternatives}


def test_witnessed_series_is_proposed_with_its_position():
    report = _report(_product(seriesPrimary=_SAGA))
    assert not admitted_audiobook_identity(report)
    series = _results(evaluate_report(report))["series"]
    assert series.outcome is ProposalOutcome.PROPOSE_CHANGE
    assert _values(series) == {(TargetSeries("Example Saga", "2"),)}
    assert series.alternatives[0].assessment.value == "fills_empty"
    assert series.dependencies == (TargetDestination.TITLE,)


@pytest.mark.parametrize(
    "identity",
    ("exact", "wrong_title", "wrong_author", "incomplete_authors", "variant"),
)
def test_work_series_cannot_borrow_another_candidates_identity(identity):
    report = _report(
        _product(seriesPrimary=_SAGA),
        _product(
            "B000000002",
            title={
                "wrong_title": "Different Story",
                "variant": "Synthetic Journey (Abridged)",
            }.get(identity, "Synthetic Journey"),
            authors=[]
            if identity == "incomplete_authors"
            else [
                {
                    "name": "Other Author"
                    if identity == "wrong_author"
                    else "Example Author"
                }
            ],
            seriesPrimary={"name": "Borrowed Saga", "position": "7"},
        ),
    )
    names = {
        member.name
        for value in _values(_results(evaluate_report(report))["series"])
        for member in cast("tuple[TargetSeries, ...]", value)
    }
    assert "Example Saga" in names
    assert ("Borrowed Saga" in names) is (identity == "exact")


@pytest.mark.parametrize(
    "source", ("wrong_title", "wrong_author", "incomplete_authors", "variant")
)
def test_series_on_an_unwitnessed_candidate_has_no_supported_evidence(source):
    report = _report(
        _product(
            title={
                "wrong_title": "Different Story",
                "variant": "Synthetic Journey (Dramatized)",
            }.get(source, "Synthetic Journey"),
            authors=[]
            if source == "incomplete_authors"
            else [
                {
                    "name": "Other Author"
                    if source == "wrong_author"
                    else "Example Author"
                }
            ],
            summary="A voyage reveals a hidden city.",
            seriesPrimary=_SAGA,
        )
    )
    series = _results(evaluate_report(report))["series"]
    assert (series.outcome, series.reasons, series.alternatives) == (
        ProposalOutcome.INELIGIBLE,
        ("no_supported_evidence",),
        (),
    )


def test_a_local_series_tag_is_not_work_evidence():
    report = _report(
        _product(summary="A voyage reveals a hidden city."),
        local=(
            audio_claim(ClaimKind.SERIES, "Local Saga", 3),
            audio_claim(ClaimKind.SERIES_SEQUENCE, "4", 4),
        ),
    )
    assert any(claim.kind is ClaimKind.SERIES for claim in report.local_claims)
    series = _results(evaluate_report(report))["series"]
    assert (series.outcome, series.reasons, series.alternatives) == (
        ProposalOutcome.INELIGIBLE,
        ("no_supported_evidence",),
        (),
    )


def test_lookup_only_products_are_never_read_for_series_or_title():
    # Each lookup product states the target's own title and a series, so
    # reading either would leave an alternative behind.
    report = recording_lookup_report(series=("Lookup Saga", "3"))
    assert not admitted_audiobook_identity(report)
    assert any(
        claim.kind is ClaimKind.SERIES
        for provider in report.providers
        for candidate in provider.recording_lookup_candidates
        for claim in candidate.claims
    )
    assert audiobook_review_scope(report) is None
    results = _results(evaluate_report(report))
    for destination in ("title", "subtitle", "series"):
        assert not results[destination].alternatives


def test_a_witnessed_title_cleanup_is_proposed_without_a_subtitle():
    report = _report(
        _product(seriesPrimary=_SAGA),
        target_title="Synthetic Journey (Unabridged)",
    )
    proposal = evaluate_report(report)
    results = _results(proposal)
    title = results["title"]
    assert title.outcome is ProposalOutcome.PROPOSE_CHANGE
    assert _values(title) == {"Synthetic Journey"}
    assert title.dependencies == (
        TargetDestination.SUBTITLE,
        TargetDestination.SERIES,
    )
    assert results["subtitle"].reasons == ("no_supported_evidence",)
    assert not results["subtitle"].alternatives
    # The duplicate-component and combination checks still run over the pair.
    assert len(proposal.bibliographic_combinations) == 1


def test_a_witnessed_title_restores_missing_diacritics():
    report = _report(
        _product(title="Synthetic Journéy"),
        target_title="Synthetic Journey",
    )
    title = _results(evaluate_report(report))["title"]
    assert "Synthetic Journéy" in _values(title)
    assert audiobook_review_scope(report) == "work"


_MASKED_TITLE = ProposalResult(
    TargetDestination.TITLE,
    ProposalOutcome.INELIGIBLE,
    (),
    None,
    ("no_supported_evidence",),
    (TargetDestination.SUBTITLE, TargetDestination.SERIES),
)


@pytest.mark.parametrize(
    ("current", "candidate", "cleanup"),
    (
        ("Synthetic Journey (Unabridged)", "Synthetic Journey", True),
        ("Synthetic_Journey", "Synthetic Journey", True),
        ("Synthetic Journey", "Synthétic Journey", True),
        ("Synthétic Journey", "Synthetic Journey", False),
        ("Synthétic Journey (Unabridged)", "Synthetic Journey", False),
        ("Synthétic Journey (Unabridged)", "Synthétic Journey", True),
        ("Synthetic \u201cJourney\u201d", 'Synthetic "Journey"', False),
        ("Synthetic Journey\u2019s", "Synthetic Journey's", False),
        ("Synthetic Journey's", "Synthetic Journey\u2019s", False),
        ("synthetic journey", "Synthetic Journey", False),
    ),
)
def test_a_title_cleanup_keeps_every_mark_and_more_than_quote_form(
    current, candidate, cleanup
):
    target = TargetField(TargetDestination.TITLE, TargetFieldState.VALUE, current)
    value = ProposalValue(ProposalValueKind.TEXT, candidate)
    assert _work_title_cleanup(target, value) is cleanup


@pytest.mark.parametrize(
    ("current", "provider_title"),
    (
        ("synthetic journey", "Synthetic Journey"),
        ("Synthétic Journey", "Synthetic Journey"),
        ("Synthetic \u201cJourney\u201d", 'Synthetic "Journey"'),
        ('Synthetic "Journey"', "Synthetic \u201cJourney\u201d"),
    ),
    ids=("case", "accent-strip", "straight-quotes", "typographic-quotes"),
)
def test_a_witnessed_title_that_is_no_cleanup_stays_masked(current, provider_title):
    report = synthetic_audiobook_product_lookup_report(
        (_product(title=provider_title),), target_title=current, catalog_title=current
    )
    assert provider_title in {
        value.value.value for value in _observations(report, TargetDestination.TITLE)
    }
    assert audiobook_review_scope(report) is None
    assert _results(evaluate_report(report))["title"] == _MASKED_TITLE


def test_a_description_only_work_case_leaves_title_as_base():
    report = _report(_product(summary="A voyage reveals a hidden city."))
    assert audiobook_review_scope(report) == "work"
    results = _results(evaluate_report(report))
    assert results["description"].alternatives
    assert results["title"] == _MASKED_TITLE
    assert not results["series"].alternatives


def test_a_series_witness_keeps_a_preserving_title_masked():
    proposal = evaluate_report(_report(_product(seriesPrimary=_SAGA)))
    results = _results(proposal)
    assert results["title"] == _MASKED_TITLE
    assert results["series"].outcome is ProposalOutcome.PROPOSE_CHANGE
    assert len(proposal.bibliographic_combinations) == 1


@pytest.mark.parametrize(
    "provider_title",
    ("Synthetic Journey: A Novel", "Synthetic Journey (Dramatized)"),
)
def test_a_decorated_or_variant_title_is_never_offered(provider_title):
    report = _report(
        _product(),
        _product("B000000002", title=provider_title, seriesPrimary=_SAGA),
        target_title="Synthetic Journey (Unabridged)",
    )
    results = _results(evaluate_report(report))
    assert _values(results["title"]) == {"Synthetic Journey"}
    assert not results["subtitle"].alternatives
    assert not results["series"].alternatives


def test_a_series_witness_alone_opens_work_review():
    report = _report(_product(seriesPrimary=_SAGA))
    assert audiobook_review_scope(report) == "work"


def test_a_title_cleanup_alone_opens_work_review():
    report = _report(_product(), target_title="Synthetic Journey (Unabridged)")
    assert audiobook_review_scope(report) == "work"


_INCUMBENT = "A voyage reveals a hidden city. The crew must choose who returns home."


@pytest.mark.parametrize(
    ("summaries", "marked"),
    (
        (("A voyage reveals a hidden city.",), True),
        (("A voyage reveals a hidden city.", _INCUMBENT), True),
        (("An explorer crosses the sea and discovers an ancient city.",), False),
        (
            (
                "A voyage reveals a hidden city.",
                "An explorer crosses the sea and discovers an ancient city.",
            ),
            False,
        ),
    ),
)
def test_an_immaterial_work_description_stays_a_choice(summaries, marked):
    result = _results(evaluate_report(synopsis_report(_INCUMBENT, summaries)))[
        "description"
    ]
    assert result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
    assert result.alternatives
    assert (WORK_ALTERNATIVE_IMMATERIAL_REASON in result.reasons) is marked


def test_the_immaterial_bound_belongs_to_the_work_route_only():
    report = recording_synopsis_report(_INCUMBENT, ("A voyage reveals a hidden city.",))
    assert admitted_audiobook_identity(report)
    result = _results(evaluate_report(report))["description"]
    assert result.reasons == ("populated_subjective_field",)


def test_an_incumbent_below_the_floor_makes_the_alternative_material():
    result = _results(
        evaluate_report(
            synopsis_report(
                "Narrated by Example Narrator", ("A voyage reveals a hidden city.",)
            )
        )
    )["description"]
    assert WORK_ALTERNATIVE_IMMATERIAL_REASON not in result.reasons


@pytest.mark.parametrize(
    ("current", "offered", "marked"),
    (
        (("Adventure", "Fantasy"), ("Fantasy",), True),
        (("Fantasy", "Audiobook"), ("Fantasy", "Adventure"), True),
        (("Mystery",), ("Fantasy",), False),
        (("Audiobook",), ("Fantasy",), False),
    ),
)
def test_an_immaterial_work_genre_list_stays_a_choice(current, offered, marked):
    report = _report(
        _product(genres=[{"type": "genre", "name": name} for name in offered]),
        current={TargetDestination.GENRES: current},
    )
    result = _results(evaluate_report(report))["genres"]
    assert result.alternatives
    assert (WORK_ALTERNATIVE_IMMATERIAL_REASON in result.reasons) is marked
