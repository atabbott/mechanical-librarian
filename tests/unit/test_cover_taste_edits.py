"""Owner edits of the taste profile, the leave-out key and the rebuild merge
(#1372 PR 2 S2-S4, S6, S7, S10)."""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.cover_taste import (
    MAX_TASTE_REMOVED,
    MAX_TASTE_REMOVED_CODE_POINTS,
    MAX_TASTE_RULE_NUMBER,
    MAX_TASTE_RULES,
    TASTE_SECTIONS,
    CoverTasteError,
    TasteEditError,
    TasteProfile,
    TasteRule,
    apply_taste_edit,
    merge_taste_rebuild,
    taste_removed_valid,
    taste_rule_key,
)


def _profile(*rules: tuple[int, str, str, str]) -> TasteProfile:
    return TasteProfile(
        tuple(
            TasteRule(number, section, text, provenance)  # type: ignore[arg-type]
            for number, section, text, provenance in rules
        )
    )


BASE = _profile(
    (1, "drawn_to", "Bold art.", "model"),
    (2, "drawn_to", "Calm type.", "owner"),
    (3, "avoid", "Busy type.", "model"),
)


def _rows(profile: TasteProfile) -> list[tuple[int, str, str, str]]:
    return [
        (rule.number, rule.section, rule.text, rule.provenance)
        for rule in profile.rules
    ]


def _edit(*args: object, removed: tuple[str, ...] = (), profile=BASE):
    result = apply_taste_edit(profile, removed, *args)  # type: ignore[arg-type]
    assert result is not None
    return result


def test_editing_a_model_rule_marks_it_edited_and_lists_the_old_text() -> None:
    profile, removed = _edit("edit", 1, None, "Bright art.")
    assert _rows(profile)[0] == (1, "drawn_to", "Bright art.", "edited")
    assert removed == ("Bold art.",)
    again, removed = _edit("edit", 1, None, "Brighter art.", profile=profile)
    assert _rows(again)[0] == (1, "drawn_to", "Brighter art.", "edited")
    assert removed == ("Bright art.",)


def test_editing_an_owner_rule_keeps_it_the_owners() -> None:
    profile, _removed = _edit("edit", 2, None, "Calm serif type.")
    assert _rows(profile)[1] == (2, "drawn_to", "Calm serif type.", "owner")


def test_an_edit_that_normalises_to_the_old_text_lists_nothing() -> None:
    profile, removed = _edit("edit", 1, None, "bold art")
    assert _rows(profile)[0][2:] == ("bold art", "edited")
    assert removed == ()


def test_unchanged_text_saves_nothing() -> None:
    assert apply_taste_edit(BASE, (), "edit", 1, None, "Bold art.") is None


def test_add_takes_max_plus_one_at_the_end_of_its_section() -> None:
    profile, removed = _edit("add", None, "drawn_to", "Muted colour.")
    assert _rows(profile) == [
        (1, "drawn_to", "Bold art.", "model"),
        (2, "drawn_to", "Calm type.", "owner"),
        (4, "drawn_to", "Muted colour.", "owner"),
        (3, "avoid", "Busy type.", "model"),
    ]
    assert removed == ()
    empty, _ = _edit(
        "add", None, "keep_or_switch", "Keep sharp covers.", profile=TasteProfile(())
    )
    assert _rows(empty) == [(1, "keep_or_switch", "Keep sharp covers.", "owner")]


def test_add_at_the_top_number_takes_the_lowest_free_one() -> None:
    profile = _profile(
        (2, "drawn_to", "Bold art.", "model"),
        (MAX_TASTE_RULE_NUMBER, "avoid", "Busy type.", "owner"),
    )
    added, _ = _edit("add", None, "avoid", "Thin type.", profile=profile)
    assert _rows(added)[-1] == (1, "avoid", "Thin type.", "owner")


def test_remove_leaves_a_gap_and_lists_the_text_first() -> None:
    profile, removed = _edit("remove", 2, None, None, removed=("Old rule.",))
    assert [rule.number for rule in profile.rules] == [1, 3]
    assert removed == ("Calm type.", "Old rule.")


def test_removing_a_listed_text_moves_it_to_the_front() -> None:
    _profile_, removed = _edit(
        "remove", 3, None, None, removed=("Old rule.", "busy type")
    )
    assert removed == ("Busy type.", "Old rule.")


def test_saving_a_removed_text_unlists_it() -> None:
    """The builder is never told to keep and to drop the same rule (S3)."""
    _added, removed = _edit(
        "add", None, "avoid", "Glossy finish.", removed=("glossy finish", "Old.")
    )
    assert removed == ("Old.",)
    _edited, removed = _edit("edit", 1, None, "OLD", removed=("Old.", "Kept."))
    assert removed == ("Bold art.", "Kept.")


def test_the_removed_list_drops_its_oldest_past_100_texts() -> None:
    removed = tuple(f"Rule {index}." for index in range(MAX_TASTE_REMOVED))
    _profile_, after = _edit("remove", 3, None, None, removed=removed)
    assert len(after) == MAX_TASTE_REMOVED
    assert after[0] == "Busy type."
    assert after[-1] == f"Rule {MAX_TASTE_REMOVED - 2}."
    assert taste_removed_valid(after)


def test_the_removed_list_drops_its_oldest_past_16384_code_points() -> None:
    # 40 texts of 400 code points and one of 383 total 16,383; adding the
    # ten-code-point text crosses 16,384 and drops the oldest.
    removed = tuple(f"{chr(0x4E00 + index)}" * 400 for index in range(40)) + (
        "z" * 383,
    )
    assert sum(len(text) for text in removed) == MAX_TASTE_REMOVED_CODE_POINTS - 1
    _profile_, after = _edit("remove", 3, None, None, removed=removed)
    assert after[0] == "Busy type."
    assert after[1:] == removed[:-1]
    assert sum(len(text) for text in after) <= MAX_TASTE_REMOVED_CODE_POINTS


@pytest.mark.parametrize(
    ("args", "profile", "code"),
    [
        (("edit", 1, None, ""), BASE, "rule_empty"),
        (("add", None, "avoid", "   "), BASE, "rule_empty"),
        (("edit", 1, None, "é" * 401), BASE, "rule_too_long"),
        (("edit", 1, None, "See https://example.invalid"), BASE, "rule_text"),
        (("edit", 1, None, "Two  spaces."), BASE, "rule_text"),
        (("edit", 9, None, "Bright art."), BASE, "stale"),
        (("remove", 9, None, None), BASE, "stale"),
        (("edit", 1, None, "Bright art."), None, "stale"),
        (("remove", 1, None, None), None, "stale"),
        # Precedence: the text before stale, stale before the limit.
        (("add", None, "avoid", ""), None, "rule_empty"),
        (
            ("add", None, "avoid", "Thin type."),
            _profile(*((n, "drawn_to", f"Rule {n}.", "model") for n in range(1, 41))),
            "rule_limit",
        ),
        (
            ("add", None, "avoid", ""),
            _profile(*((n, "drawn_to", f"Rule {n}.", "model") for n in range(1, 41))),
            "rule_empty",
        ),
        (
            ("add", None, "avoid", "Thin type."),
            None,
            "stale",
        ),
        (
            ("add", None, "avoid", "word " * 9 + "word"),
            _profile(
                *((n, "drawn_to", "a " * 39 + "a", "model") for n in range(1, 31))
            ),
            "profile_full",
        ),
    ],
    ids=[
        "empty",
        "blank",
        "too-long",
        "address",
        "not-canonical",
        "missing-number",
        "remove-missing-number",
        "no-profile",
        "remove-no-profile",
        "empty-before-stale",
        "limit",
        "empty-before-limit",
        "stale-before-limit",
        "words",
    ],
)
def test_refusals_follow_their_precedence(args, profile, code) -> None:
    with pytest.raises(TasteEditError) as raised:
        apply_taste_edit(profile, (), *args)
    assert raised.value.code == code
    assert str(raised.value) == code


def test_code_points_over_the_profile_bound_are_profile_full() -> None:
    # 20 rules of 400 code points is 8,000; 193 more crosses 8,192.
    profile = _profile(*((n, "avoid", "b" * 400, "model") for n in range(1, 21)))
    with pytest.raises(TasteEditError) as raised:
        apply_taste_edit(profile, (), "add", None, "avoid", "x" * 96 + " " + "y" * 96)
    assert raised.value.code == "profile_full"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Bold art.", "bold art"),
        ("Bold art.", "BOLD ART."),
        ("Bold art", "Bold art. ."),
        ("Ｂｏｌｄ art.", "bold art"),
        ("Straße art.", "STRASSE ART"),
    ],
    ids=["case-and-stop", "upper", "trailing-stops", "nfkc-width", "casefold"],
)
def test_the_leave_out_key_ignores_case_width_and_a_trailing_stop(
    left: str, right: str
) -> None:
    assert taste_rule_key(left) == taste_rule_key(right)


def test_the_leave_out_key_keeps_paraphrases_apart() -> None:
    assert taste_rule_key("Bold art.") != taste_rule_key("Strong art.")


def _built(**sections: list[str]) -> TasteProfile:
    return TasteProfile.from_output(
        {"sections": {name: sections.get(name, []) for name in TASTE_SECTIONS}}
    )


def test_a_rebuild_keeps_owner_and_edited_rules_ahead_as_written() -> None:
    base = _profile(
        (4, "drawn_to", "Calm type.", "owner"),
        (2, "avoid", "Busy type!", "edited"),
        (7, "avoid", "Model rule.", "model"),
    )
    built = _built(
        drawn_to=["Bold art.", "calm type"],
        avoid=["BUSY TYPE!", "Glossy finish.", "Thin type."],
        keep_or_switch=["Keep sharp covers."],
    )
    merged, left_out = merge_taste_rebuild(base, ("thin type",), built)
    assert _rows(merged) == [
        (1, "drawn_to", "Calm type.", "owner"),
        (2, "drawn_to", "Bold art.", "model"),
        (3, "avoid", "Busy type!", "edited"),
        (4, "avoid", "Glossy finish.", "model"),
        (5, "keep_or_switch", "Keep sharp covers.", "model"),
    ]
    # "calm type", "BUSY TYPE!" and "Thin type." repeat a kept or removed text.
    assert left_out == 3


def test_a_first_build_merges_nothing() -> None:
    built = _built(drawn_to=["Bold art."])
    assert merge_taste_rebuild(None, (), built) == (built, 0)


@pytest.mark.parametrize(
    ("kept", "built"),
    [
        (
            [(n, "drawn_to", f"Rule {n}.", "owner") for n in range(1, 21)],
            [f"Model {n}." for n in range(21)],
        ),
        (
            [(n, "drawn_to", "a " * 29 + "a", "owner") for n in range(1, 21)],
            ["b " * 29 + "b"] * 19 + ["b " * 29 + "b c"],
        ),
    ],
    ids=["41-rules", "1201-words"],
)
def test_a_merge_over_a_bound_is_refused(kept, built) -> None:
    with pytest.raises(CoverTasteError):
        merge_taste_rebuild(_profile(*kept), (), _built(avoid=built))


def test_the_removed_list_bounds_are_checked() -> None:
    assert taste_removed_valid(())
    assert taste_removed_valid(["Bold art."])
    assert not taste_removed_valid(("Bold art.", "bold art"))
    assert not taste_removed_valid(("See https://example.invalid",))
    assert not taste_removed_valid(tuple(f"R {n}." for n in range(101)))
    assert not taste_removed_valid(tuple(chr(0x4E00 + n) * 400 for n in range(41)))
    assert not taste_removed_valid("Bold art.")
    assert MAX_TASTE_RULES == 40
