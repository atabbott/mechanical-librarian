"""The Looks wrong rules over a report's current values (#1334).

Precision comes first: every near miss below is a value a real library holds
correctly, so a flag on it is a defect.
"""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.metadata_report import (
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.value_flags import (
    VALUE_FLAG_RULES,
    VALUE_FLAG_RULES_VERSION,
    report_source_titles,
    value_flags,
)

_AUTHOR = "Example Author"


def _fields(
    title: str | None = None,
    authors: tuple[str, ...] | None = None,
    narrators: tuple[str, ...] | None = None,
    *,
    state: TargetFieldState = TargetFieldState.MISSING,
) -> tuple[TargetField, ...]:
    values: dict[TargetDestination, object] = {
        TargetDestination.TITLE: title,
        TargetDestination.AUTHORS: authors,
        TargetDestination.NARRATORS: narrators,
    }
    return tuple(
        TargetField(destination, TargetFieldState.VALUE, values[destination])  # type: ignore[arg-type]
        if values.get(destination) is not None
        else TargetField(destination, state)
        for destination in TargetDestination
    )


def _title_rules(
    title: str, authors: tuple[str, ...] = (), sources: tuple[str, ...] = ()
) -> tuple[str, ...]:
    flags = value_flags(_fields(title, authors or None), sources)
    assert all(destination == "title" for destination, _rule in flags)
    return tuple(rule for _destination, rule in flags)


def test_the_rule_list_is_closed_ordered_and_versioned() -> None:
    assert VALUE_FLAG_RULES == (
        "title_part_number",
        "title_disc_number",
        "title_is_author",
        "title_has_author",
        "title_all_capitals",
        "title_all_lower_case",
        "title_announcement",
        "credit_label",
    )
    assert VALUE_FLAG_RULES_VERSION == "value-flag-rules-v1"


@pytest.mark.parametrize(
    "title", ("Disc 01", "cd2", "Part 3 of 5", "Pt. 2", "[Disc 2]", "Part IV", "Part 2")
)
def test_a_title_that_is_only_a_disc_or_part_number_flags(title: str) -> None:
    assert _title_rules(title) == ("title_part_number",)


@pytest.mark.parametrize(
    "title",
    ("Part One", "Disco 1", "Part 2: The Return", "Disk Drive Basics", "(Disc 2]"),
)
def test_a_title_that_only_looks_like_a_part_number_does_not_flag(title: str) -> None:
    assert _title_rules(title) == ()


@pytest.mark.parametrize("title", ("Example Story - Disc 1", "Example Story (CD 2)"))
def test_a_title_that_ends_with_a_disc_number_flags(title: str) -> None:
    assert _title_rules(title) == ("title_disc_number",)


@pytest.mark.parametrize(
    "title",
    (
        # Real titles end with a part, so a trailing Part or Pt never flags.
        "Example Saga, Part 2",
        "Example Saga Pt. 3",
        "Example Story Discs 1-3",
        "The CD 2 Mystery",
        "Example Story (CD 2",
        "Example) Story (CD 2)",
    ),
)
def test_a_title_that_only_resembles_a_disc_number_does_not_flag(title: str) -> None:
    assert _title_rules(title) == ()


def test_a_title_that_is_the_author_flags() -> None:
    assert _title_rules(_AUTHOR, (_AUTHOR,)) == ("title_is_author",)
    assert _title_rules("example  author", ("Other Author", _AUTHOR)) == (
        "title_is_author",
        "title_all_lower_case",
    )


@pytest.mark.parametrize(
    "title", ("Example Author's Notebook", "Example Author: A Life")
)
def test_a_title_that_names_more_than_the_author_is_not_the_author(title: str) -> None:
    assert _title_rules(title, (_AUTHOR,)) == ()


def test_a_title_that_includes_the_author_flags() -> None:
    assert _title_rules("Example Author - Example Story", (_AUTHOR,)) == (
        "title_has_author",
    )
    assert _title_rules("Example Story — Example Author", (_AUTHOR,)) == (
        "title_has_author",
    )


@pytest.mark.parametrize(
    ("title", "authors"),
    (
        ("Example Story - A Novel", (_AUTHOR,)),
        ("Sky-Walker - Homecoming", (_AUTHOR,)),
        ("Example Author - Example Author", (_AUTHOR,)),
        ("Example Story: Example Author", (_AUTHOR,)),
        # Only the current authors count, so a dash title by nobody stands.
        ("Example Author - Example Story", ()),
    ),
)
def test_a_dash_title_without_exactly_one_author_side_does_not_flag(
    title: str, authors: tuple[str, ...]
) -> None:
    assert _title_rules(title, authors) == ()


@pytest.mark.parametrize(
    "title",
    (
        "THE EXAMPLE STORY",
        "EXAMPLE STORY: A NOVEL",
        "ПРИМЕРНАЯ ИСТОРИЯ",
        "ΤΟ ΠΑΡΑΔΕΙΓΜΑ ΒΙΒΛΙΟ",
    ),
)
def test_a_title_in_capitals_flags(title: str) -> None:
    assert _title_rules(title) == ("title_all_capitals",)


@pytest.mark.parametrize(
    "title", ("the example story", "примерная история", "the  example story")
)
def test_a_title_in_lower_case_flags(title: str) -> None:
    assert _title_rules(title) == ("title_all_lower_case",)


@pytest.mark.parametrize(
    "title",
    (
        "QRX",
        "ZEPHYRA",
        "NX4B2",
        # One qualifying word: the numeral does not count.
        "QB VII",
        "AI 2041",
        "東京物語",
        "東京物語 TOKYO",
        "iExample Story",
        "The Example Story",
        "XIV XX",
    ),
)
def test_capitals_need_two_qualifying_words(title: str) -> None:
    assert _title_rules(title) == ()


@pytest.mark.parametrize(
    "title",
    (
        "Example Story (Now a Major Motion Picture)",
        "Example Story: A New York Times Bestseller",
        "Example Story [Winner of the Example Prize]",
    ),
)
def test_a_title_with_an_announcement_after_its_name_flags(title: str) -> None:
    assert _title_rules(title) == ("title_announcement",)


@pytest.mark.parametrize(
    "title",
    (
        "The Bestseller",
        "Bestseller: A Novel",
        # Marketing words in a real subtitle are not an announcement.
        "Example Story: Netflix and Culture",
        "Now a Major Motion Picture",
    ),
)
def test_an_announcement_must_follow_the_title(title: str) -> None:
    assert _title_rules(title) == ()


@pytest.mark.parametrize(
    "member",
    (
        "Narrated by Example Reader",
        "Read by:",
        "Narrator: Example Reader",
        "Written by Example Author",
    ),
)
def test_a_contributor_with_a_credit_label_flags(member: str) -> None:
    assert value_flags(_fields("Example Story", (member,)), ()) == (
        ("authors", "credit_label"),
    )
    assert value_flags(
        _fields("Example Story", None, ("Example Reader", member)), ()
    ) == (("narrators", "credit_label"),)


@pytest.mark.parametrize("member", ("Byron Read", "Example Narrator", "Reader Example"))
def test_a_name_that_holds_a_role_word_does_not_flag(member: str) -> None:
    assert value_flags(_fields("Example Story", (member,), (member,)), ()) == ()


def test_a_credit_label_is_what_the_shared_credit_grammar_reads() -> None:
    # The rule is contributor_credit_prefixed, unchanged. A role verb, "by", a
    # colon and then a name is outside that grammar, so it is missed, never
    # falsely flagged; widening the grammar is separate work.
    member = "Read by: Example Reader"
    assert value_flags(_fields("Example Story", (member,), (member,)), ()) == ()


def test_a_title_a_source_spells_the_same_way_is_not_the_author_or_lower_case() -> None:
    # A self-titled work and a title published in lower case are real when a
    # source in the same report states exactly the same spelling (D8).
    assert _title_rules(_AUTHOR, (_AUTHOR,), (_AUTHOR,)) == ()
    assert _title_rules("the example story", (), ("the  example story",)) == ()
    # The match is case-sensitive.
    assert _title_rules(_AUTHOR, (_AUTHOR,), ("EXAMPLE AUTHOR",)) == (
        "title_is_author",
    )
    assert _title_rules("the example story", (), ("The Example Story",)) == (
        "title_all_lower_case",
    )


def test_a_source_spelling_excuses_neither_capitals_nor_a_part_number() -> None:
    assert _title_rules("THE EXAMPLE STORY", (), ("THE EXAMPLE STORY",)) == (
        "title_all_capitals",
    )
    assert _title_rules("Part 2", (), ("Part 2",)) == ("title_part_number",)
    assert _title_rules(
        "Example Author - Example Story",
        (_AUTHOR,),
        ("Example Author - Example Story",),
    ) == ("title_has_author",)


def test_findings_come_in_rule_order_at_most_once_each() -> None:
    flags = value_flags(
        _fields(
            "EXAMPLE AUTHOR - EXAMPLE STORY (NOW A MAJOR MOTION PICTURE) - DISC 1",
            ("Narrated by Example Reader", "Example Author", "Read by Other Reader"),
            ("Narrator: Example Reader",),
        ),
        (),
    )
    assert flags == (
        ("title", "title_disc_number"),
        ("title", "title_all_capitals"),
        ("title", "title_announcement"),
        ("authors", "credit_label"),
        ("narrators", "credit_label"),
    )
    assert len(flags) <= 9


@pytest.mark.parametrize(
    "state",
    (
        TargetFieldState.MISSING,
        TargetFieldState.NULL,
        TargetFieldState.EMPTY,
        TargetFieldState.UNLOADED,
        TargetFieldState.MALFORMED,
        TargetFieldState.OVERSIZED,
    ),
)
def test_a_field_without_a_value_gives_no_flag(state: TargetFieldState) -> None:
    assert value_flags(_fields(state=state), ("Disc 1",)) == ()


def test_only_title_authors_and_narrators_are_read() -> None:
    fields = tuple(
        TargetField(destination, TargetFieldState.VALUE, "Disc 1")
        if destination in {TargetDestination.SUBTITLE, TargetDestination.PUBLISHER}
        else TargetField(
            destination, TargetFieldState.VALUE, ("Narrated by Example Reader",)
        )
        if destination in {TargetDestination.GENRES, TargetDestination.TAGS}
        else TargetField(
            destination, TargetFieldState.VALUE, (TargetSeries("Disc 1", None),)
        )
        if destination is TargetDestination.SERIES
        else TargetField(destination, TargetFieldState.MISSING)
        for destination in TargetDestination
    )
    assert value_flags(fields, ()) == ()


def test_value_flags_refuses_anything_but_target_fields_and_text() -> None:
    with pytest.raises(ValueError):
        value_flags([], ())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        value_flags(("title",), ())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        value_flags(_fields("Example Story"), ["Example Story"])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        value_flags(_fields("Example Story"), (1,))  # type: ignore[arg-type]


def test_source_titles_are_every_candidate_title_claim_normalized() -> None:
    providers = [
        {
            "candidates": [
                {
                    "claims": [
                        {"kind": "title", "value": "The  Examplé Story"},
                        {"kind": "title", "value": None, "state": "missing"},
                        {"kind": "creator", "value": "Example Author"},
                    ]
                },
                {"claims": [{"kind": "title", "value": "Second Title"}]},
            ]
        },
        {"candidates": []},
        {"status": "no_match"},
    ]
    assert report_source_titles(providers) == ("Second Title", "The Exampl\u00e9 Story")
    assert report_source_titles([]) == ()
    assert report_source_titles(None) == ()
