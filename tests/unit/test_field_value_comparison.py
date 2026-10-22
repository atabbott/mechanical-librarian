"""Canonical equality shared by proposal evaluation and no-change terminalization."""

import pytest

from mechanical_librarian.domain.field_value_comparison import (
    contributor_credit_prefixed,
    description_alternative_immaterial,
    genre_alternative_immaterial,
    mostly_quoted_praise,
    promotional_announcement,
    values_match,
)
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
        ("unsupported-language", "unsupported-language", False),
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


_SYNOPSIS = "A voyage reveals a hidden city. The crew must choose who returns home."


@pytest.mark.parametrize(
    ("current", "candidate", "immaterial"),
    (
        (_SYNOPSIS, "A voyage reveals a hidden city.", True),
        ("A voyage reveals a hidden city.", _SYNOPSIS, True),
        (_SYNOPSIS, "<p>A VOYAGE reveals a hidden   city!</p>", True),
        (_SYNOPSIS, "The crew must choose who returns home.", True),
        (_SYNOPSIS, _SYNOPSIS, True),
        (
            _SYNOPSIS,
            "A voyage reveals a hidden city. Nobody returns from the deep.",
            False,
        ),
        (_SYNOPSIS, "A voyage reveals a city.", False),
        (_SYNOPSIS, "", False),
        ("Narrated by Example Narrator", "A voyage reveals a hidden city.", False),
        ("Unabridged", "Unabridged audiobook", False),
        ("<p>A voyage reveals a hidden city.</p>", "A voyage reveals", False),
        ("A voyage reveals a hidden city...", "A voyage reveals", False),
        (None, "A voyage reveals a hidden city.", False),
        (("A voyage",), "A voyage", False),
        (_SYNOPSIS, None, False),
    ),
    ids=(
        "trims-trailing-sentence",
        "extends-incumbent",
        "markup-case-punctuation",
        "trims-leading-sentence",
        "equal",
        "new-sentence",
        "drops-inner-word",
        "no-words",
        "credit-line-incumbent",
        "format-word-incumbent",
        "tag-bearing-incumbent",
        "visibly-cut-incumbent",
        "missing-incumbent",
        "list-incumbent",
        "non-text-candidate",
    ),
)
def test_description_alternative_is_immaterial_only_by_contiguous_words(
    current: object, candidate: object, immaterial: bool
) -> None:
    assert description_alternative_immaterial(current, candidate) is immaterial


@pytest.mark.parametrize(
    ("current", "candidate", "immaterial"),
    (
        (("Fantasy", "Adventure"), ("Fantasy",), True),
        (("Fantasy",), ("fantasy", "Adventure"), True),
        (("Fantasy", "Audiobook"), ("Fantasy",), True),
        (("Fantasy",), ("Fantasy", "Unabridged"), True),
        (("Fantasy",), ("Audiobook",), True),
        (("Fantasy", "Adventure"), ("Fantasy", "Mystery"), False),
        (("Fantasy",), ("Mystery",), False),
        (("Audiobook",), ("Fantasy",), False),
        ((), ("Fantasy",), False),
        (None, ("Fantasy",), False),
        (("Fantasy",), "Fantasy", False),
    ),
    ids=(
        "subset",
        "superset",
        "format-word-incumbent-member",
        "format-word-candidate-member",
        "format-only-candidate",
        "new-genre",
        "disjoint",
        "format-only-incumbent",
        "empty-incumbent",
        "missing-incumbent",
        "non-list-candidate",
    ),
)
def test_genre_alternative_is_immaterial_only_by_set_containment(
    current: object, candidate: object, immaterial: bool
) -> None:
    assert genre_alternative_immaterial(current, candidate) is immaterial


@pytest.mark.parametrize(
    ("value", "prefixed"),
    (
        ("Narrated by Example Reader", True),
        ("Read by Example Reader", True),
        ("Written by Example Author", True),
        ("  READ BY  Example Reader ", True),
        ("Narrated and produced by Example Reader", True),
        ("Read by", True),
        ("Narrated by:", True),
        ("Narrator: Example Reader", True),
        ("Cast: Example Reader & Other Reader", True),
        ("Byron Read", False),
        ("Narrated Example", False),
        ("Reader Byers", False),
        ("Example Reader", False),
        ("Ann By", False),
        (None, False),
    ),
)
def test_contributor_credit_prefix_reads_the_credit_grammar(
    value: object, prefixed: bool
) -> None:
    assert contributor_credit_prefixed(value) is prefixed


@pytest.mark.parametrize(
    ("value", "announcement"),
    (
        ("Now a Major Motion Picture", True),
        (" now an  HBO series", False),
        ("Now a Netflix Series", True),
        ("The Number One Bestseller", True),
        ("A New York Times Bestseller", True),
        ("Winner of the Example Prize 2024", True),
        ("A Bestselling Life", False),
        ("A Tale of Courage", False),
        ("The Prize", False),
        (None, False),
    ),
)
def test_promotional_announcement_reads_whole_segments(
    value: object, announcement: bool
) -> None:
    assert promotional_announcement(value) is announcement


_PRAISE = (
    '"A triumph of quiet suspense." — Example Review '
    '"I could not put it down." — Sample Times'
)


@pytest.mark.parametrize(
    ("value", "praise"),
    (
        (_PRAISE, True),
        ("“Luminous.” – Example Review", True),
        ('"Gripping from start to finish." - Example Weekly', True),
        ("Praise for Synthetic Journey: a voyage reveals a hidden city.", True),
        ("  PRAISE   FOR the author", True),
        (
            '"Luminous." — Example Review\n\nAn explorer crosses the sea, '
            "maps a coastline nobody has charted and discovers an ancient city "
            "whose archive holds the last record of a vanished people.",
            False,
        ),
        (
            '"Where are we?" she asked. "Somewhere old," he said. "Keep walking."',
            False,
        ),
        ('"A triumph." "Gripping." "Luminous."', False),
        ('"Unforgettable." — J. R. Example, author of Another Journey', True),
        (
            '"Luminous." — Example Review. An explorer crosses the sea and '
            "discovers an ancient city whose archive holds a vanished record.",
            False,
        ),
        ("A voyage reveals a hidden city.", False),
        ("", False),
        (None, False),
    ),
    ids=(
        "attributed_quotes",
        "curly_en_dash",
        "spaced_hyphen",
        "praise_for",
        "praise_for_folded",
        "one_pull_quote_and_synopsis",
        "dialogue_without_attribution",
        "quotes_without_attribution",
        "initials_in_attribution",
        "pull_quote_then_synopsis_on_one_line",
        "synopsis",
        "empty",
        "not_text",
    ),
)
def test_mostly_quoted_praise_reads_attributed_quote_blocks(
    value: object, praise: bool
) -> None:
    assert mostly_quoted_praise(value) is praise
