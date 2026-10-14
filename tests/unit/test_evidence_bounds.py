"""The forbidden-codepoint set the evidence validators refuse and replace."""

import pytest

from mechanical_librarian.domain.evidence_bounds import (
    _FORBIDDEN_CODEPOINTS,
    EvidenceValueError,
    replace_forbidden_codepoints,
    validate_incumbent_description,
    validate_text,
)

# A literal transcription of the per-codepoint predicate the compiled class
# replaced, kept here so the class is checked against an independent statement
# of the set rather than against itself.
_BIDI_CONTROLS = frozenset(
    {
        0x061C,
        0x200E,
        0x200F,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    }
)


def _forbidden_codepoint(codepoint: int) -> bool:
    return (
        codepoint < 0x20
        or 0x7F <= codepoint <= 0x9F
        or 0xD800 <= codepoint <= 0xDFFF
        or codepoint in _BIDI_CONTROLS
        or codepoint in {0x2028, 0x2029}
        or 0xFDD0 <= codepoint <= 0xFDEF
        or codepoint & 0xFFFF in {0xFFFE, 0xFFFF}
    )


def test_pattern_matches_exactly_the_forbidden_codepoints() -> None:
    mismatches = [
        hex(codepoint)
        for codepoint in range(0x110000)
        if bool(_FORBIDDEN_CODEPOINTS.search(chr(codepoint)))
        != _forbidden_codepoint(codepoint)
    ]
    assert mismatches == []


_FORBIDDEN_SAMPLES = (
    "\x00",
    "\x7f",
    "\u202e",
    "\ufdd0",
    "\U0010ffff",
    chr(0xD800),
    chr(0xDFFF),
)


@pytest.mark.parametrize("character", _FORBIDDEN_SAMPLES)
@pytest.mark.parametrize("shape", ("{}Text body", "Text{}body", "Text body{}"))
@pytest.mark.parametrize("validator", (validate_text, validate_incumbent_description))
def test_forbidden_codepoint_is_refused_anywhere_in_text(
    validator, shape: str, character: str
) -> None:
    with pytest.raises(EvidenceValueError) as raised:
        validator(shape.format(character), "label", 256)
    assert type(raised.value) is EvidenceValueError
    assert str(raised.value) == "label contains forbidden control or Unicode text"


@pytest.mark.parametrize("validator", (validate_text, validate_incumbent_description))
def test_text_without_forbidden_codepoints_is_admitted(validator) -> None:
    value = "Café 語 \U0001f600 \ufffd \ufffd\U0001fffd"
    assert validator(value, "label", 256) == value


@pytest.mark.parametrize(
    "value,message",
    (
        ("a" * 257 + "\x00", "label exceeds its size limit"),
        (" \x00", "label must be non-empty trimmed text"),
        ("\x00 ", "label must be non-empty trimmed text"),
    ),
)
def test_text_bounds_precede_the_codepoint_check(value: str, message: str) -> None:
    with pytest.raises(EvidenceValueError) as raised:
        validate_text(value, "label", 256)
    assert str(raised.value) == message


@pytest.mark.parametrize(
    "value,message",
    (
        ("a" * 257 + "\x00", "label exceeds its size limit"),
        (" \t\n", "label must be non-empty text"),
    ),
)
def test_description_bounds_precede_the_codepoint_check(
    value: str, message: str
) -> None:
    with pytest.raises(EvidenceValueError) as raised:
        validate_incumbent_description(value, "label", 256)
    assert str(raised.value) == message


def test_replacement_substitutes_each_forbidden_codepoint() -> None:
    value = "\x00a\u202eb" + chr(0xD800) + chr(0xDFFF) + "c\U0010ffff\ufffe"
    assert (
        replace_forbidden_codepoints(value) == "\ufffda\ufffdb\ufffd\ufffdc\ufffd\ufffd"
    )
    clean = "Café\u00a0語 \U0001f600"
    assert replace_forbidden_codepoints(clean) == clean
    assert replace_forbidden_codepoints("") == ""
