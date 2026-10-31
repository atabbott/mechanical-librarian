"""Open Library values in another language or script stay out (#1497)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    LocalClaim,
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalResult,
    evaluate_report,
)
from mechanical_librarian.domain.provider_value_language import (
    ProviderValueLanguageExclusion,
    established_work_languages,
    open_library_value_exclusion,
)
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.proposal_evidence import (
    _claim,
    _provider,
    _report,
    consensus_local_title,
)

_CURRENT = "Example Series 03 - Example Title"


def _title(**values: object) -> ProposalResult:
    report = MetadataReport.from_json(
        isbn_title_report(current=_CURRENT, **values).to_json()  # type: ignore[arg-type]
    )
    return next(
        result
        for result in evaluate_report(report).results
        if result.destination is TargetDestination.TITLE
    )


def _offered(result: ProposalResult) -> set[str]:
    return {str(value.value.value) for value in result.alternatives}


@pytest.mark.parametrize(
    "other",
    ("Пример заглавия", "示例标题", "Пример заглавия Example"),
    ids=("cyrillic", "han", "mostly_cyrillic"),
)
def test_an_open_library_title_in_another_script_is_not_offered(other):
    # The disagreeing ISBN-bound source still vetoes the election; its title
    # is only no longer one of the choices.
    result = _title(titles=("Example Title", other), local_language="en")

    assert (ProposalOutcome.UNRESOLVED_CHOICE, ("isbn_title_material_conflict",)) == (
        result.outcome,
        result.reasons,
    )
    assert other not in _offered(result)
    assert "Example Title" in _offered(result)


def test_an_open_library_edition_in_another_language_is_not_offered():
    # A translation's edition is still a variant-identity veto on the ISBN
    # route; its title is just no longer one of the choices.
    result = _title(
        titles=("Example Title", "Titre Exemple"),
        provider_languages=(None, "fre"),
        local_language="en",
    )

    assert ProposalOutcome.UNRESOLVED_CHOICE is result.outcome
    assert "Titre Exemple" not in _offered(result)
    assert "Example Title" in _offered(result)


def test_providers_that_agree_establish_the_language():
    result = _title(
        titles=("Example Title", "Titre Exemple", "Example Title"),
        provider_languages=("en", "fre", "eng"),
    )

    assert "Titre Exemple" not in _offered(result)


@pytest.mark.parametrize(
    ("values", "reason"),
    (
        (
            {"titles": ("Пример заглавия", "Example Title"), "local_language": "en"},
            "hardcover_is_not_open_library",
        ),
        ({"titles": ("Example Title", "Пример заглавия")}, "no_established_language"),
        (
            {
                "titles": ("Example Title", "Example Book"),
                "local_language": "en",
            },
            "same_script_other_title",
        ),
        (
            {
                "titles": ("Example Title", "Titre Exemple"),
                "provider_languages": (None, "fre"),
            },
            "one_provider_states_a_language",
        ),
        (
            {
                "titles": ("Example Title", "Titre Exemple"),
                "provider_languages": (None, "fre"),
                "local_language": "fr",
            },
            "work_in_that_language",
        ),
        (
            {"titles": ("Example Title", "Пример заглавия"), "local_language": "ru"},
            "work_in_another_script",
        ),
        (
            {
                "titles": ("Example Title", "Пример Example Title"),
                "local_language": "en",
            },
            "mostly_latin",
        ),
    ),
    ids=(
        "hardcover",
        "no_language",
        "same_script",
        "one_provider",
        "work_in_french",
        "work_in_russian",
        "mostly_latin",
    ),
)
def test_near_misses_still_offer_the_other_title(values, reason):
    result = _title(**values)

    assert (ProposalOutcome.UNRESOLVED_CHOICE, ("isbn_title_material_conflict",)) == (
        result.outcome,
        result.reasons,
    ), reason
    assert {"Example Title", *values["titles"]} <= _offered(result), reason


def test_an_open_library_edition_naming_the_work_language_too_is_kept():
    report = isbn_title_report(
        current=_CURRENT,
        titles=("Example Title", "Titre Exemple"),
        provider_languages=(None, "fr"),
        local_language="en",
    )
    open_library = next(
        provider
        for provider in report.providers
        if provider.authority == "open_library"
    )
    candidate = open_library.candidates[0]
    bilingual = replace(
        candidate,
        claims=(
            *candidate.claims,
            _claim(
                "open_library",
                candidate.identity,
                ClaimKind.LANGUAGE,
                "eng",
                scope=ClaimScope.EDITION,
                order=9,
            ),
        ),
    )

    assert (
        open_library_value_exclusion(
            "open_library",
            bilingual,
            TargetDestination.TITLE,
            "Titre Exemple",
            established_work_languages(report),
        )
        is None
    )


def _language(value: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.LANGUAGE,
        observed=value,
        canonical=value,
        excerpt=value,
        scope=ClaimScope.EDITION,
        basis="package_metadata",
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
    )


def _synopsis(language: str | None, local: str | None) -> ProposalResult:
    english = _claim(
        "hardcover",
        "hardcover-1",
        ClaimKind.DESCRIPTION,
        "A voyage reveals a hidden city.",
        scope=ClaimScope.WORK,
    )
    french = [
        _claim(
            "open_library",
            "open_library-1",
            ClaimKind.DESCRIPTION,
            "Un voyage révèle une ville.",
            scope=ClaimScope.WORK,
        )
    ]
    if language is not None:
        french.append(
            _claim(
                "open_library",
                "open_library-1",
                ClaimKind.LANGUAGE,
                language,
                scope=ClaimScope.EDITION,
                order=1,
            )
        )
    report = _report(
        title="Synthetic Voyage",
        local_claims=(
            consensus_local_title("Synthetic Voyage"),
            *(() if local is None else (_language(local),)),
        ),
        providers=(
            _provider("hardcover", "hardcover-1", (english,), authority="hardcover"),
            _provider(
                "open_library",
                "open_library-1",
                tuple(french),
                authority="open_library",
            ),
        ),
    )
    return next(
        result
        for result in evaluate_report(
            MetadataReport.from_json(report.to_json())
        ).results
        if result.destination is TargetDestination.DESCRIPTION
    )


def test_an_open_library_description_in_another_language_is_not_offered():
    result = _synopsis("fre", "en")

    assert {"A voyage reveals a hidden city."} == _offered(result)


@pytest.mark.parametrize(
    ("language", "local"),
    ((None, "en"), ("fre", None), ("fre", "fr")),
    ids=("no_edition_language", "no_work_language", "work_in_french"),
)
def test_an_open_library_description_is_kept_without_a_contradiction(language, local):
    result = _synopsis(language, local)

    assert "Un voyage révèle une ville." in _offered(result)


@pytest.mark.parametrize(
    ("value", "stated", "established", "reason"),
    (
        ("Пример", (), {("en", None)}, "value_script_contradicted"),
        ("示例", (), {("en", "Latn")}, "value_script_contradicted"),
        ("Example", ("fre",), {("en", None)}, "edition_language_contradicted"),
        ("Example", ("fre",), {("fr", "Latn")}, None),
        ("示例", (), {("zh", "Hans")}, None),
        ("Example", (), set(), None),
        ("1984", (), {("en", None)}, None),
    ),
    ids=(
        "cyrillic",
        "han_latn",
        "french",
        "french_work",
        "han_work",
        "unknown",
        "no_letters",
    ),
)
def test_closed_reasons(value, stated, established, reason):
    report = isbn_title_report(titles=("Example Title", "Example Title"))
    candidate = next(
        provider
        for provider in report.providers
        if provider.authority == "open_library"
    ).candidates[0]
    candidate = replace(
        candidate,
        claims=(
            *candidate.claims,
            *(
                _claim(
                    "open_library",
                    candidate.identity,
                    ClaimKind.LANGUAGE,
                    language,
                    scope=ClaimScope.EDITION,
                    order=20 + index,
                )
                for index, language in enumerate(stated)
            ),
        ),
    )
    found = open_library_value_exclusion(
        "open_library",
        candidate,
        TargetDestination.DESCRIPTION,
        value,
        frozenset(established),
    )

    assert (None if found is None else found.value) == reason
    assert {member.value for member in ProviderValueLanguageExclusion} == {
        "edition_language_contradicted",
        "value_script_contradicted",
    }
    assert (
        open_library_value_exclusion(
            "hardcover",
            candidate,
            TargetDestination.DESCRIPTION,
            value,
            frozenset(established),
        )
        is None
    )


def test_fullwidth_latin_is_latin():
    assert (
        open_library_value_exclusion(
            "open_library",
            isbn_title_report(titles=("Example Title", "Example Title"))
            .providers[1]
            .candidates[0],
            TargetDestination.TITLE,
            "Ｅｘａｍｐｌｅ",
            frozenset({("en", None)}),
        )
        is None
    )
