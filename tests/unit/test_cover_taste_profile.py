"""The cover taste profile's bounds, refusal and stored form (#1372 A6)."""

from __future__ import annotations

import pytest

from mechanical_librarian.domain.cover_taste import (
    MAX_TASTE_PROFILE_CODE_POINTS,
    MAX_TASTE_PROFILE_WORDS,
    MAX_TASTE_RULE_CODE_POINTS,
    MAX_TASTE_RULES,
    TASTE_SECTIONS,
    CoverTasteError,
    TasteProfile,
)


def _output(**sections: list[str]) -> dict[str, object]:
    return {
        "sections": {section: sections.get(section, []) for section in TASTE_SECTIONS}
    }


def test_output_is_numbered_in_section_order() -> None:
    profile = TasteProfile.from_output(
        _output(
            keep_or_switch=["Keeps sharp covers."], drawn_to=["Bold art.", "Calm type."]
        )
    )
    assert [(rule.number, rule.section, rule.text) for rule in profile.rules] == [
        (1, "drawn_to", "Bold art."),
        (2, "drawn_to", "Calm type."),
        (3, "keep_or_switch", "Keeps sharp covers."),
    ]
    assert all(rule.provenance == "model" for rule in profile.rules)


def test_empty_sections_are_allowed() -> None:
    assert TasteProfile.from_output(_output()).rules == ()
    assert len(TasteProfile.from_output(_output(avoid=["Busy type."])).rules) == 1


@pytest.mark.parametrize(
    ("count", "admitted"),
    [(MAX_TASTE_RULES, True), (MAX_TASTE_RULES + 1, False)],
    ids=["40-rules", "41-rules"],
)
def test_rule_count_bound(count: int, admitted: bool) -> None:
    value = _output(drawn_to=[f"Rule {index}." for index in range(count)])
    if admitted:
        assert len(TasteProfile.from_output(value).rules) == count
    else:
        with pytest.raises(CoverTasteError):
            TasteProfile.from_output(value)


@pytest.mark.parametrize(
    ("length", "admitted"),
    [(MAX_TASTE_RULE_CODE_POINTS, True), (MAX_TASTE_RULE_CODE_POINTS + 1, False)],
    ids=["400-code-points", "401-code-points"],
)
def test_rule_length_bound(length: int, admitted: bool) -> None:
    # A multi-byte letter proves the bound counts code points, not bytes.
    value = _output(avoid=["é" * length])
    if admitted:
        assert TasteProfile.from_output(value).rules[0].text == "é" * length
    else:
        with pytest.raises(CoverTasteError):
            TasteProfile.from_output(value)


@pytest.mark.parametrize(
    ("words", "admitted"),
    [(MAX_TASTE_PROFILE_WORDS, True), (MAX_TASTE_PROFILE_WORDS + 1, False)],
    ids=["1200-words", "1201-words"],
)
def test_profile_word_bound(words: int, admitted: bool) -> None:
    # 30 words a rule across 40 rules is 1,200 words; one more word refuses.
    rules = ["a " * 29 + "a" for _ in range(40)]
    if words > MAX_TASTE_PROFILE_WORDS:
        rules[-1] += " a"
    value = _output(trade_offs=rules)
    if admitted:
        assert len(TasteProfile.from_output(value).rules) == 40
    else:
        with pytest.raises(CoverTasteError):
            TasteProfile.from_output(value)


@pytest.mark.parametrize(
    ("total", "admitted"),
    [(MAX_TASTE_PROFILE_CODE_POINTS, True), (MAX_TASTE_PROFILE_CODE_POINTS + 1, False)],
    ids=["8192-code-points", "8193-code-points"],
)
def test_profile_code_point_bound(total: int, admitted: bool) -> None:
    # Twenty rules of 400 one-word code points and one remainder.
    rules = ["b" * 400 for _ in range(20)] + ["b" * (total - 8000)]
    value = _output(drawn_to=rules)
    if admitted:
        assert (
            sum(len(rule.text) for rule in TasteProfile.from_output(value).rules)
            == total
        )
    else:
        with pytest.raises(CoverTasteError):
            TasteProfile.from_output(value)


@pytest.mark.parametrize(
    "text",
    [
        "See https://example.invalid for more.",
        "Bold\u0007art.",
        "Two\nlines.",
        "Covers like /library/books/cover.jpg.",
        "",
        "   ",
    ],
    ids=["uri", "control", "newline", "path", "empty", "blank"],
)
def test_rule_text_refusals(text: str) -> None:
    with pytest.raises(CoverTasteError):
        TasteProfile.from_output(_output(drawn_to=[text]))


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"sections": {}},
        {"sections": {**_output()["sections"], "extra": []}},  # type: ignore[dict-item]
        {**_output(), "notes": "x"},
        {"sections": {**_output()["sections"], "avoid": "Busy type."}},  # type: ignore[dict-item]
        {"sections": {**_output()["sections"], "avoid": [1]}},  # type: ignore[dict-item]
    ],
    ids=[
        "none",
        "list",
        "no-sections",
        "extra-section",
        "extra-member",
        "string",
        "number",
    ],
)
def test_output_outside_the_closed_schema_is_refused(value: object) -> None:
    with pytest.raises(CoverTasteError):
        TasteProfile.from_output(value)


def test_digest_is_deterministic_and_stored_rules_round_trip() -> None:
    profile = TasteProfile.from_output(
        _output(drawn_to=["Bold art."], avoid=["Busy type."])
    )
    again = TasteProfile.from_output(
        _output(drawn_to=["Bold art."], avoid=["Busy type."])
    )
    assert profile.digest == again.digest
    assert len(profile.digest) == 64
    assert TasteProfile.from_rules(profile.rules_data()) == profile
    changed = TasteProfile.from_output(
        _output(drawn_to=["Bold art!"], avoid=["Busy type."])
    )
    assert changed.digest != profile.digest


@pytest.mark.parametrize(
    "rules",
    [
        [
            {
                "number": 2,
                "provenance": "model",
                "section": "drawn_to",
                "text": "Bold art.",
            }
        ],
        [
            {
                "number": 1,
                "provenance": "owner",
                "section": "drawn_to",
                "text": "Bold art.",
            }
        ],
        [{"number": 1, "provenance": "model", "section": "drawn_to"}],
        [{"number": 1, "provenance": "model", "section": "style", "text": "Bold art."}],
        [
            {"number": 1, "provenance": "model", "section": "avoid", "text": "Busy."},
            {
                "number": 2,
                "provenance": "model",
                "section": "drawn_to",
                "text": "Bold.",
            },
        ],
    ],
    ids=["gap", "provenance", "missing-text", "section", "section-order"],
)
def test_stored_rules_a_build_could_not_write_are_refused(rules: object) -> None:
    with pytest.raises(CoverTasteError):
        TasteProfile.from_rules(rules)
