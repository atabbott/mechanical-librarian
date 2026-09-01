"""Canonical equality shared by proposal evaluation and no-change terminalization."""

import pytest

from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import TargetDestination


@pytest.mark.parametrize(
    ("destination", "current", "candidate"),
    (
        (TargetDestination.TITLE, "A  Title", "a title"),
        (TargetDestination.ISBN, "0-306-40615-2", "0306406152"),
        (TargetDestination.AUTHORS, ("Mick Herron",), ("mick herron",)),
        (TargetDestination.SUBTITLE, None, None),
    ),
    ids=("scalar", "identifier", "creator", "nullable"),
)
def test_values_match_uses_the_field_canonical_comparison(
    destination: TargetDestination,
    current: object,
    candidate: object,
) -> None:
    assert values_match(destination, current, candidate)


def test_values_match_keeps_a_near_match_actionable() -> None:
    assert not values_match(
        TargetDestination.TITLE,
        "A title",
        "A different title",
    )


@pytest.mark.parametrize(
    ("current", "candidate", "matches"),
    (
        ("en-US", "eng-GB", True),
        ("en", "eng", True),
        ("zh-Hans-CN", "zho-Hans-TW", True),
        ("zh-Hans", "zh-Hant", False),
        ("zh", "zh-Hans", False),
        ("en", "fr", False),
        ("english", "english", False),
    ),
    ids=(
        "region-stripped",
        "iso-alias",
        "same-script",
        "different-script",
        "unspecified-script",
        "different-base",
        "malformed",
    ),
)
def test_language_values_match_only_within_one_base_and_script_class(
    current: str, candidate: str, matches: bool
) -> None:
    assert matches is values_match(TargetDestination.LANGUAGE, current, candidate)


@pytest.mark.parametrize(
    ("current", "candidate"),
    (
        ("0-306-40615-2", "0306406152"),
        ("978-0-306-40615-7", "9780306406157"),
    ),
    ids=("formatted-isbn10", "formatted-isbn13"),
)
def test_isbn_comparison_preserves_the_selected_representation(
    current: str, candidate: str
) -> None:
    assert values_match(TargetDestination.ISBN, current, candidate)


@pytest.mark.parametrize(
    ("current", "candidate"),
    (
        ("0306406152", "9780306406157"),
        ("9780306406157", "0306406152"),
        ("0306406152", "9780131103627"),
        ("not-an-isbn", "not-an-isbn"),
    ),
    ids=(
        "preserve-exact-does-not-collapse-into-prefer-isbn13",
        "prefer-isbn13-does-not-collapse-into-preserve-exact",
        "distinct-family",
        "invalid-identifier",
    ),
)
def test_isbn_comparison_keeps_nonmatching_or_invalid_values_actionable(
    current: str, candidate: str
) -> None:
    assert not values_match(TargetDestination.ISBN, current, candidate)
