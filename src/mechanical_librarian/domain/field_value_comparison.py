"""Canonical comparison of values already admitted for one target field."""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from typing import cast

from mechanical_librarian.domain.evidence_bounds import validate_projection_text
from mechanical_librarian.domain.field_outcomes import language_outcomes_compatible
from mechanical_librarian.domain.metadata_report import (
    MAX_DESCRIPTION_CHARACTERS,
    TargetDestination,
    TargetSeries,
    isbn_family,
    normalize_title,
)


def canonical_text(destination: TargetDestination, value: str) -> str:
    """Return the proposal evaluator's comparison form for one text field."""
    if destination is TargetDestination.DESCRIPTION:
        return " ".join(
            unicodedata.normalize(
                "NFC",
                validate_projection_text(
                    value,
                    "proposal value",
                    MAX_DESCRIPTION_CHARACTERS,
                ),
            ).split()
        ).casefold()
    return normalize_title(value).casefold()


def values_match(
    destination: TargetDestination,
    current: object,
    candidate: object,
) -> bool:
    """Compare admitted target values with the evaluator's field semantics."""
    if destination is TargetDestination.SERIES:
        current_series = _series_values(current)
        candidate_series = _series_values(candidate)
        return current_series is not None and current_series == candidate_series
    if destination is TargetDestination.ISBN:
        return _isbn_values_match(current, candidate)
    current_sequence = _text_sequence(current)
    candidate_sequence = _text_sequence(candidate)
    if current_sequence is not None and candidate_sequence is not None:
        return tuple(
            canonical_text(destination, value) for value in current_sequence
        ) == tuple(canonical_text(destination, value) for value in candidate_sequence)
    if type(current) is str and type(candidate) is str:
        return (
            language_outcomes_compatible(current, candidate)
            if destination is TargetDestination.LANGUAGE
            else canonical_text(destination, current)
            == canonical_text(destination, candidate)
        )
    return current == candidate


def _isbn_values_match(current: object, candidate: object) -> bool:
    if type(current) is not str or type(candidate) is not str:
        return False
    current_family = isbn_family(current)
    candidate_family = isbn_family(candidate)
    return (
        current_family is not None
        and candidate_family is not None
        and current_family == candidate_family
        and _isbn_representation(current) == _isbn_representation(candidate)
    )


def _isbn_representation(value: str) -> str:
    return "".join(
        character for character in value if character.isdigit() or character in "Xx"
    ).upper()


def _text_sequence(value: object) -> tuple[str, ...] | None:
    if type(value) not in {list, tuple} or any(
        type(item) is not str for item in cast(Sequence[object], value)
    ):
        return None
    return tuple(cast(Sequence[str], value))


def _series_values(value: object) -> tuple[tuple[str, str | None], ...] | None:
    if type(value) not in {list, tuple}:
        return None
    members: list[tuple[str, str | None]] = []
    for item in cast(Sequence[object], value):
        if type(item) is TargetSeries:
            members.append(
                (canonical_text(TargetDestination.SERIES, item.name), item.sequence)
            )
        elif (
            type(item) is dict
            and set(item) == {"name", "sequence"}
            and type(item["name"]) is str
            and (item["sequence"] is None or type(item["sequence"]) is str)
        ):
            members.append(
                (
                    canonical_text(TargetDestination.SERIES, item["name"]),
                    item["sequence"],
                )
            )
        else:
            return None
    return tuple(members)


__all__ = ["canonical_text", "values_match"]
