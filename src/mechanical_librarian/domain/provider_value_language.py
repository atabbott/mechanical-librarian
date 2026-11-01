"""Open Library values whose language contradicts the work's (#1497).

Open Library groups every translation of a work under one record, so an
English ebook's candidates can carry a title or description from a French or
Russian edition. Those values never name this item, so they are not offered
as title or description alternatives. The two closed cases below say why one
is left out; the proposal set still decides the outcome with it.

The work's established language comes from its package's language claims and
its target's current language value; with neither, from one language that
two or more provider authorities state. With no established language nothing is
excluded, and a value in the work's language is never affected.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Final

from mechanical_librarian.domain.field_outcomes import language_equivalence_class
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    FieldState,
    MetadataReport,
    ProviderCandidate,
    TargetDestination,
    TargetFieldState,
)

OPEN_LIBRARY_PROVIDER: Final = "open_library"

# Languages ordinarily written in Latin script. A work established in one of
# them (and only them) does not take a title or description written mostly in
# another script. Works in any other language get no script rule.
_LATIN_WRITTEN: Final = frozenset(
    {
        "af", "ca", "cs", "cy", "da", "de", "en", "eo", "es", "et", "eu", "fi",
        "fr", "ga", "gd", "gl", "hr", "hu", "id", "is", "it", "la", "lt", "lv",
        "ms", "mt", "nb", "nl", "nn", "no", "pl", "pt", "ro", "sk", "sl", "sq",
        "sv", "sw", "tl", "tr", "vi",
    }
)  # fmt: skip


# ISO 639 designations that name no one language: multiple, uncoded and no
# linguistic content. They neither establish a work's language nor contradict
# it (`und` already has no class).
_NON_SPECIFIC: Final = frozenset({"mul", "mis", "zxx"})


class ProviderValueLanguageExclusion(StrEnum):
    """Closed reasons an Open Library value leaves the alternatives."""

    EDITION_LANGUAGE_CONTRADICTED = "edition_language_contradicted"
    VALUE_SCRIPT_CONTRADICTED = "value_script_contradicted"


Language = tuple[str, str | None]


def _non_specific(value: str) -> bool:
    language = language_equivalence_class(value)
    return language is not None and language[0] in _NON_SPECIFIC


def _specific_language(value: str) -> Language | None:
    language = language_equivalence_class(value)
    return None if language is None or language[0] in _NON_SPECIFIC else language


def established_work_languages(report: MetadataReport) -> frozenset[Language]:
    """Return the work's language classes: package and target, else agreement."""
    own: set[Language] = {
        language
        for claim in report.local_claims
        if claim.kind is ClaimKind.LANGUAGE
        and claim.state is FieldState.VALID
        and claim.canonical is not None
        and (language := _specific_language(claim.canonical)) is not None
    }
    own.update(
        language
        for field in report.target.fields
        if field.destination is TargetDestination.LANGUAGE
        and field.state is TargetFieldState.VALUE
        and type(field.value) is str
        and (language := _specific_language(field.value)) is not None
    )
    if any(
        _non_specific(claim.canonical)
        for claim in report.local_claims
        if claim.kind is ClaimKind.LANGUAGE
        and claim.state is FieldState.VALID
        and claim.canonical is not None
    ) or any(
        _non_specific(field.value)
        for field in report.target.fields
        if field.destination is TargetDestination.LANGUAGE
        and field.state is TargetFieldState.VALUE
        and type(field.value) is str
    ):
        # A work said to be in several or uncoded languages has no one
        # language for a value to contradict.
        return frozenset()
    if own:
        return frozenset(own)
    stated: dict[Language, set[str]] = {}
    for provider in report.providers:
        for language in {
            language
            for candidate in provider.candidates
            for language in _stated_languages(candidate)
        }:
            stated.setdefault(language, set()).add(
                provider.authority or provider.provider
            )
    agreed = {language for language, sources in stated.items() if len(sources) >= 2}
    return frozenset(agreed) if len(agreed) == 1 else frozenset()


def open_library_value_exclusion(
    authority: str | None,
    candidate: ProviderCandidate,
    destination: TargetDestination,
    value: str,
    established: frozenset[Language],
) -> ProviderValueLanguageExclusion | None:
    """Name why one Open Library title or description is not offered, if it is not."""
    if (
        authority != OPEN_LIBRARY_PROVIDER
        or destination not in {TargetDestination.TITLE, TargetDestination.DESCRIPTION}
        or not established
    ):
        return None
    stated = _stated_languages(candidate)
    if stated and not {base for base, _ in stated} & {base for base, _ in established}:
        return ProviderValueLanguageExclusion.EDITION_LANGUAGE_CONTRADICTED
    if all(_latin_written(language) for language in established) and (
        _mostly_other_script(value)
    ):
        return ProviderValueLanguageExclusion.VALUE_SCRIPT_CONTRADICTED
    return None


def _stated_languages(candidate: ProviderCandidate) -> frozenset[Language]:
    return frozenset(
        language
        for claim in candidate.claims
        if claim.kind is ClaimKind.LANGUAGE
        and claim.state is FieldState.VALID
        and type(claim.value) is str
        and (language := _specific_language(claim.value)) is not None
    )


def _latin_written(language: Language) -> bool:
    base, script = language
    return script == "Latn" if script is not None else base in _LATIN_WRITTEN


def _mostly_other_script(value: str) -> bool:
    letters = [
        character
        for character in unicodedata.normalize("NFC", value)
        if unicodedata.category(character).startswith("L")
    ]
    other = sum(
        "LATIN " not in unicodedata.name(character, "") for character in letters
    )
    return bool(letters) and other * 2 > len(letters)
