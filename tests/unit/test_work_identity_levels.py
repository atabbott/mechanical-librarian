"""ADR-0096's work-identity level predicates over synthetic controls (#1481)."""

from __future__ import annotations

import itertools
import time

import pytest

from mechanical_librarian.domain.work_identity_levels import (
    IdentityReference,
    LevelAdmission,
    WorkIdentityLevel,
    admitting_level,
    level_admission,
    omnibus_against_volume,
    title_and_creator,
    title_and_creator_exact,
)

EXACT = WorkIdentityLevel.TITLE_AND_CREATOR_EXACT
WIDE = WorkIdentityLevel.TITLE_AND_CREATOR
IDENTIFIER = WorkIdentityLevel.IDENTIFIER
STRICTNESS = (IDENTIFIER, EXACT, WIDE)

ALDER = "Ada Alder"
BIRCH = "Bo Birch"
CEDAR = "Cy Cedar"


def ref(title: str | None, *creators: str) -> IdentityReference:
    return IdentityReference(title, creators)


def test_levels_are_closed_with_their_configuration_values() -> None:
    assert [value.value for value in WorkIdentityLevel] == [
        "identifier",
        "title_and_creator_exact",
        "title_and_creator",
    ]


@pytest.mark.parametrize(
    ("target_title", "record_title"),
    (
        ("Lantern Harbour", "Lantern Harbour"),
        ("Lantern Harbour", "lantern  HARBOUR"),
        ("Lantern\u00a0Harbour", "Lantern Harbour"),
        ("The Keeper\u2019s Lantern", "The Keeper's Lantern"),
        ("Lantern \u2014 Harbour", "Lantern - Harbour"),
        ("Cafe\u0301 Lantern", "Caf\u00e9 Lantern"),
    ),
    ids=[
        "identical",
        "case_and_spacing",
        "no_break_space",
        "curly_apostrophe",
        "em_dash",
        "nfc",
    ],
)
def test_closed_title_comparison_meets_both_levels(
    target_title: str, record_title: str
) -> None:
    target = ref(target_title, ALDER, BIRCH)
    record = ref(record_title, BIRCH, ALDER)

    assert title_and_creator_exact(target, record)
    assert title_and_creator(target, record)


@pytest.mark.parametrize(
    "decorated",
    (
        "Lantern Harbour (Example Saga 3)",
        "Lantern Harbour (Example Saga, Book 3)",
        "Lantern Harbour (2001)",
        "Lantern Harbour (3)",
        "Lantern Harbour [retail]",
        "Lantern Harbour: A Tale of Tides",
        "Lantern Harbour [ISBN 9780306406157]",
    ),
    ids=[
        "series_name_position",
        "series_name_book_marker",
        "year",
        "bare_number",
        "retail_tag",
        "subtitle_segment",
        "bracketed_isbn",
    ],
)
def test_one_decoration_step_meets_only_the_wide_level(decorated: str) -> None:
    plain = ref("lantern harbour", ALDER)
    decorated_ref = ref(decorated, ALDER)

    for target, record in ((plain, decorated_ref), (decorated_ref, plain)):
        assert not title_and_creator_exact(target, record)
        assert title_and_creator(target, record)


@pytest.mark.parametrize(
    ("target_title", "record_title"),
    (
        # Stacked decorations take two steps, never one.
        ("Lantern Harbour", "Lantern Harbour (Example Saga 3) (2001)"),
        ("Lantern Harbour", "Lantern Harbour: A Tale (2001)"),
        # Each side decorated differently is two steps apart.
        ("Lantern Harbour (2001)", "Lantern Harbour [retail]"),
        # No fuzzy distance, no diacritic stripping, no partial titles.
        ("Lantern Harbour", "Lantern Harbor"),
        ("Caf\u00e9 Lantern", "Cafe Lantern"),
        ("Lantern Harbour", "Lantern Harbour Returns"),
        ("Lantern Harbour", "Harbour"),
        # An unbracketed series word is not a decoration in the grammar.
        ("Lantern Harbour", "Lantern Harbour Book 3"),
        # A variant marker gains no non-exact agreement (ADR-0018).
        ("Lantern Harbour", "Lantern Harbour (Abridged)"),
        ("Lantern Harbour: Abridged", "lantern harbour: abridged"),
    ),
    ids=[
        "stacked_series_year",
        "stacked_subtitle_year",
        "differently_decorated",
        "fuzzy_spelling",
        "diacritic_stripped",
        "longer_title",
        "partial_title",
        "unbracketed_series_word",
        "variant_parenthesis",
        "variant_case_folded",
    ],
)
def test_titles_outside_the_grammar_meet_neither_level(
    target_title: str, record_title: str
) -> None:
    target = ref(target_title, ALDER)
    record = ref(record_title, ALDER)

    for left, right in ((target, record), (record, target)):
        assert not title_and_creator_exact(left, right)
        assert not title_and_creator(left, right)


def test_a_variant_marked_title_still_agrees_byte_for_byte() -> None:
    target = ref("Lantern Harbour (Abridged)", ALDER)

    assert title_and_creator_exact(target, ref("Lantern Harbour (Abridged)", ALDER))


@pytest.mark.parametrize(
    ("target", "record"),
    (
        (ref(None, ALDER), ref("Lantern Harbour", ALDER)),
        (ref("Lantern Harbour", ALDER), ref(None, ALDER)),
        (ref("  ", ALDER), ref("  ", ALDER)),
        (ref("", ALDER), ref("", ALDER)),
        (ref("Lantern Harbour"), ref("Lantern Harbour", ALDER)),
        (ref("Lantern Harbour", ALDER), ref("Lantern Harbour")),
        (ref("Lantern Harbour"), ref("Lantern Harbour")),
        (ref("Lantern Harbour", " "), ref("Lantern Harbour", " ")),
    ),
    ids=[
        "target_no_title",
        "record_no_title",
        "blank_titles",
        "empty_titles",
        "target_no_creator",
        "record_no_creator",
        "neither_creator",
        "blank_creators",
    ],
)
def test_no_title_or_no_creator_meets_neither_level(
    target: IdentityReference, record: IdentityReference
) -> None:
    assert not title_and_creator_exact(target, record)
    assert not title_and_creator(target, record)
    assert admitting_level(WIDE, target, record) is None


def test_one_shared_creator_meets_only_the_wide_level() -> None:
    target = ref("Lantern Harbour", ALDER, BIRCH)

    for record in (
        ref("Lantern Harbour", ALDER),
        ref("Lantern Harbour", ALDER, CEDAR),
        ref("Lantern Harbour", ALDER, BIRCH, CEDAR),
    ):
        assert not title_and_creator_exact(target, record)
        assert title_and_creator(target, record)


def test_the_full_creator_set_in_any_order_meets_the_exact_level() -> None:
    target = ref("Lantern Harbour", ALDER, BIRCH)

    assert title_and_creator_exact(target, ref("Lantern Harbour", BIRCH, ALDER))
    assert title_and_creator_exact(
        target, ref("Lantern Harbour", "ada  ALDER", "Bo\u00a0Birch")
    )


def test_adr_control_title_agreement_with_a_different_creator_is_refused() -> None:
    target = ref("Lantern Harbour", ALDER)
    record = ref("Lantern Harbour", CEDAR)

    assert not title_and_creator_exact(target, record)
    assert not title_and_creator(target, record)


def test_adr_control_same_author_same_title_different_book_is_the_callers() -> None:
    # Two different books by one author under one title agree on everything
    # the predicates read. Only the caller's contradiction refuses the pair.
    target = ref("Lantern Harbour", ALDER)
    record = ref("Lantern Harbour", ALDER)

    assert title_and_creator_exact(target, record)
    assert title_and_creator(target, record)
    for level in (EXACT, WIDE):
        arguments = (level, "Lantern Harbour", (ALDER,), "Lantern Harbour", (ALDER,))
        assert level_admission(
            *arguments, omnibus_against_volume=False, contradicted=False
        ) == LevelAdmission(EXACT, "work_identity:title_and_creator_exact")
        assert (
            level_admission(*arguments, omnibus_against_volume=False, contradicted=True)
            is None
        )


def _agree(target_creators: tuple[str, ...], record_creators: tuple[str, ...]):
    """Both levels' results for one title, folded and unfolded."""
    target = IdentityReference("Lantern Harbour", target_creators)
    record = IdentityReference("Lantern Harbour", record_creators)
    return tuple(
        predicate(target, record, fold_initials=fold)
        for fold in (True, False)
        for predicate in (title_and_creator_exact, title_and_creator)
    )


FOLDED_ONLY = (True, True, False, False)
NEITHER = (False, False, False, False)


@pytest.mark.parametrize(
    ("target_creator", "record_creator"),
    (
        ("J. R. Example", "J R Example"),
        ("J.R. Example", "J. R. Example"),
        ("J R Example", "J.R. Example"),
        ("R.\u00a0F. Example", "R.F. Example"),
        ("E\u0301. R. Example", "\u00c9.R. Example"),
    ),
    ids=[
        "dotted_vs_spaced_initials",
        "joined_vs_spaced_initials",
        "undotted_vs_joined_dotted",
        "no_break_space_inside_initials",
        "precomposable_decomposed_mark",
    ],
)
def test_dotted_initials_fold_at_both_levels(
    target_creator: str, record_creator: str
) -> None:
    """#1780: the three spellings are one creator; unfolded they stay two."""
    assert _agree((target_creator,), (record_creator,)) == FOLDED_ONLY
    # Beside a shared creator, the wide level already agreed unfolded.
    assert _agree((target_creator, ALDER), (ALDER, record_creator)) == (
        True,
        True,
        False,
        True,
    )


@pytest.mark.parametrize(
    ("target_creator", "record_creator"),
    (
        ("J. R. Example", "Jane Ruth Example"),
        ("Example, Ada", "Ada Example"),
        ("Example, R. F.", "R.F. Example"),
        ("R. F. Example", "R. Example"),
        ("J.R.R. Example", "J.R. Example"),
        ("R. F. Example", "F.R. Example"),
        ("Jane\u2009Example", "Jane Example"),
        ("Jane\u202fExample", "Jane Example"),
        ("R. F.", "R.F."),
        ("R. F.", "R F"),
    ),
    ids=[
        "initials_vs_full_names",
        "comma_inverted",
        "comma_inverted_initials",
        "dropped_initial",
        "added_initial",
        "reversed_initials",
        "thin_space_without_initials",
        "narrow_no_break_space_without_initials",
        "initials_only",
        "initials_only_undotted",
    ],
)
def test_fold_never_widens_beyond_respelled_initials(
    target_creator: str, record_creator: str
) -> None:
    """Guards: count, order, inversion, names without initials, initials only."""
    assert _agree((target_creator,), (record_creator,)) == NEITHER


@pytest.mark.parametrize(
    ("target_creator", "record_creator"),
    (
        ("\uff32. F. Example", "R.F. Example"),
        ("\u0410. F. Example", "A.F. Example"),
        ("R\u2024F\u2024 Example", "R. F. Example"),
        ("R\uff0eF\uff0e Example", "R. F. Example"),
        ("\u00df. R. Example", "\u00df.R. Example"),
        ("\u0130. R. Example", "\u0130.R. Example"),
        ("Q\u0301. R. Example", "Q\u0301.R. Example"),
        ("R.F.Example", "R. F. Example"),
        ("J.-P. Example", "J. P. Example"),
    ),
    ids=[
        "fullwidth_letter",
        "cyrillic_vs_latin",
        "one_dot_leader",
        "fullwidth_full_stop",
        "casefold_expanding_sharp_s",
        "casefold_expanding_dotted_capital_i",
        "mark_with_no_precomposed_form",
        "no_space_before_surname",
        "hyphenated_initials",
    ],
)
def test_unicode_initials_fail_closed(target_creator: str, record_creator: str) -> None:
    """Guards: NFC only, no NFKC or confusable mapping, ASCII full stops only."""
    assert _agree((target_creator,), (record_creator,)) == NEITHER


def test_fold_collapse_keeps_creators_unfolded() -> None:
    """Guard: a fold merging two listed creators leaves that side unfolded."""
    assert _agree(("R. F. Example", "R.F. Example"), ("R F Example",)) == NEITHER


def test_same_initials_different_book_is_refused_by_title() -> None:
    """Guard: agreeing creators never stand in for the title."""
    target = ref("Lantern Harbour", "R. F. Example")
    record = ref("Tidewater Lantern", "R.F. Example")

    assert admitting_level(WIDE, target, record) is None


def test_fold_on_bounded_long_name_is_linear() -> None:
    """Guard: a long near-miss token for the dotted-initials pattern stays cheap."""
    near_miss = "R." * 2000 + "! Example"
    started = time.monotonic()
    assert _agree((near_miss,), ("R. F. Example",)) == NEITHER
    assert time.monotonic() - started < 0.5


@pytest.mark.parametrize("level", (EXACT, WIDE))
def test_level_admission_folds_unless_asked_not_to(level: WorkIdentityLevel) -> None:
    """#1780: the keyword passes through `admitting_level` to both predicates."""
    target = ref("Lantern Harbour", "R. F. Example")
    record = ref("Lantern Harbour", "R.F. Example")

    assert admitting_level(level, target, record) is EXACT
    assert admitting_level(level, target, record, fold_initials=False) is None
    for fold, expected in (
        (True, LevelAdmission(EXACT, "work_identity:title_and_creator_exact")),
        (False, None),
    ):
        assert (
            level_admission(
                level,
                target.title,
                target.creators,
                record.title,
                record.creators,
                omnibus_against_volume=False,
                contradicted=False,
                fold_initials=fold,
            )
            == expected
        )


def test_identifier_never_admits() -> None:
    target = ref("Lantern Harbour", ALDER)

    assert admitting_level(IDENTIFIER, target, target) is None
    assert (
        level_admission(
            IDENTIFIER,
            "Lantern Harbour",
            (ALDER,),
            "Lantern Harbour",
            (ALDER,),
            omnibus_against_volume=False,
            contradicted=False,
        )
        is None
    )


def test_admitting_level_returns_the_strictest_level_met() -> None:
    target = ref("Lantern Harbour", ALDER, BIRCH)
    exact = ref("lantern harbour", BIRCH, ALDER)
    wide = ref("Lantern Harbour (2001)", ALDER)

    assert admitting_level(EXACT, target, exact) is EXACT
    assert admitting_level(WIDE, target, exact) is EXACT
    assert admitting_level(EXACT, target, wide) is None
    assert admitting_level(WIDE, target, wide) is WIDE


_TITLES = (
    None,
    "",
    "Lantern Harbour",
    "lantern  harbour",
    "Lantern Harbour (2001)",
    "Lantern Harbour: A Tale of Tides",
    "Lantern Harbour: A Tale (2001)",
    "Lantern Harbour (Abridged)",
    "Lantern Harbor",
)
_CREATORS: tuple[tuple[str, ...], ...] = (
    (),
    (ALDER,),
    ("ada alder",),
    (ALDER, BIRCH),
    (BIRCH, ALDER),
    (CEDAR,),
    ("A. Alder",),
)
_REFERENCES = tuple(
    IdentityReference(title, creators)
    for title, creators in itertools.product(_TITLES, _CREATORS)
)


def test_levels_are_monotone_over_every_synthetic_pair() -> None:
    admitted = {level: 0 for level in STRICTNESS}
    for target, record in itertools.product(_REFERENCES, repeat=2):
        exact = title_and_creator_exact(target, record)
        assert not exact or title_and_creator(target, record)
        assert exact == title_and_creator_exact(record, target)
        assert title_and_creator(target, record) == title_and_creator(record, target)
        for level in STRICTNESS:
            result = admitting_level(level, target, record)
            if result is not None:
                admitted[level] += 1
                assert STRICTNESS.index(result) <= STRICTNESS.index(level)
                assert result is not IDENTIFIER
        # A wider level never removes an admission a stricter one makes.
        if admitting_level(EXACT, target, record) is not None:
            assert admitting_level(WIDE, target, record) is not None
    assert admitted[IDENTIFIER] == 0
    assert 0 < admitted[EXACT] < admitted[WIDE]


@pytest.mark.parametrize("level", STRICTNESS)
def test_level_admission_equals_the_predicates_plus_refusals(
    level: WorkIdentityLevel,
) -> None:
    for target, record in itertools.product(_REFERENCES, repeat=2):
        expected = admitting_level(level, target, record)
        for omnibus, contradicted in itertools.product((False, True), repeat=2):
            result = level_admission(
                level,
                target.title,
                target.creators,
                record.title,
                record.creators,
                omnibus_against_volume=omnibus,
                contradicted=contradicted,
            )
            if omnibus or contradicted or expected is None:
                assert result is None
            else:
                assert result == LevelAdmission(
                    expected, f"work_identity:{expected.value}"
                )


def test_level_admission_names_its_rule() -> None:
    admission = level_admission(
        WIDE,
        "Lantern Harbour",
        (ALDER, BIRCH),
        "Lantern Harbour [retail]",
        (ALDER,),
        omnibus_against_volume=False,
        contradicted=False,
    )

    assert admission == LevelAdmission(WIDE, "work_identity:title_and_creator")


@pytest.mark.parametrize(
    ("level", "rule"),
    (
        (IDENTIFIER, "work_identity:identifier"),
        (EXACT, "work_identity:title_and_creator"),
        ("title_and_creator", "work_identity:title_and_creator"),
    ),
    ids=["identifier_level", "rule_for_other_level", "level_as_plain_string"],
)
def test_level_admission_value_rejects_an_inconsistent_rule(
    level: object, rule: str
) -> None:
    with pytest.raises(ValueError, match="level admission is invalid"):
        LevelAdmission(level, rule)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("title", "creators"),
    ((1, ()), ("Lantern Harbour", [ALDER]), ("Lantern Harbour", (ALDER, None))),
    ids=["title_not_text", "creators_not_tuple", "creator_not_text"],
)
def test_identity_reference_rejects_malformed_input(
    title: object, creators: object
) -> None:
    with pytest.raises(ValueError, match="identity reference is invalid"):
        IdentityReference(title, creators)  # type: ignore[arg-type]


def test_entry_points_reject_a_level_or_flag_outside_the_closed_set() -> None:
    target = ref("Lantern Harbour", ALDER)
    with pytest.raises(ValueError, match="level is invalid"):
        admitting_level("title_and_creator", target, target)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="input is invalid"):
        level_admission(
            WIDE,
            "Lantern Harbour",
            (ALDER,),
            "Lantern Harbour",
            (ALDER,),
            omnibus_against_volume=0,  # type: ignore[arg-type]
            contradicted=False,
        )
    with pytest.raises(ValueError, match="input is invalid"):
        level_admission(
            WIDE,
            "Lantern Harbour",
            (ALDER,),
            "Lantern Harbour",
            (ALDER,),
            omnibus_against_volume=False,
            contradicted=False,
            fold_initials=1,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="input is invalid"):
        title_and_creator(target, target, fold_initials=None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("collection", "volume", "expected"),
    (
        ("The Synthetic Saga: Books 1-3", "The Synthetic Saga", True),
        ("The Synthetic Saga Omnibus", "The Synthetic Saga", True),
        ("The Complete Synthetic Trilogy", "The First Voyage", True),
        ("Synthetic Tales Box Set", "Synthetic Tales", True),
        ("The Synthetic Saga 1\u20133", "The Synthetic Saga", True),
        ("The Synthetic Saga", "The Synthetic Saga: Books 1-3", False),
        ("The Synthetic Saga", "The Synthetic Saga", False),
        ("The Synthetic Saga: Books 1-3", "The Synthetic Saga: Books 4-6", False),
        ("The Synthetic Saga, Book 1", "The Synthetic Saga", False),
    ),
    ids=(
        "books_range",
        "omnibus",
        "complete_trilogy",
        "box_set",
        "en_dash_range",
        "reverse",
        "neither",
        "both",
        "one_book",
    ),
)
def test_omnibus_against_volume_is_closed_and_directional(
    collection: str, volume: str, expected: bool
) -> None:
    """#1481: a collection beside one of its volumes, never the reverse."""
    assert omnibus_against_volume(collection, volume) is expected


def test_omnibus_against_volume_refuses_a_non_text_input() -> None:
    with pytest.raises(ValueError):
        omnibus_against_volume(None, "The Synthetic Saga")  # type: ignore[arg-type]
