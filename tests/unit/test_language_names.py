"""The fixed language display-name table (#1314)."""

import pytest

from mechanical_librarian.domain.field_outcomes import (
    _ISO_639_ALIASES,
    _SCRIPTS,
    language_canonical_name,
    language_equivalence_class,
    language_identity,
)
from mechanical_librarian.domain.language_names import (
    LANGUAGE_BASE_NAMES,
    LANGUAGE_SCRIPT_NAMES,
    language_display_name,
    language_name_class,
)


def test_every_supported_base_has_one_distinct_name() -> None:
    bases = set(_ISO_639_ALIASES.values()) - {"und"}
    assert set(LANGUAGE_BASE_NAMES) == bases
    folded = [name.casefold() for name in LANGUAGE_BASE_NAMES.values()]
    assert len(set(folded)) == len(folded)
    assert all(name == name.strip() and name for name in LANGUAGE_BASE_NAMES.values())


def test_script_names_are_distinct_and_only_name_supported_scripts() -> None:
    assert set(LANGUAGE_SCRIPT_NAMES) <= _SCRIPTS
    folded = [name.casefold() for name in LANGUAGE_SCRIPT_NAMES.values()]
    assert len(set(folded)) == len(folded)
    assert not any(
        "(" in name or ")" in name for name in LANGUAGE_SCRIPT_NAMES.values()
    )
    assert not any("(" in name or ")" in name for name in LANGUAGE_BASE_NAMES.values())


def test_no_name_reads_as_another_languages_code() -> None:
    # "Lao" is also the `lao` code of Lao; "Ga" would be Irish, so `gaa` is
    # named "Ga language".
    assert not any(
        _ISO_639_ALIASES.get(name.casefold(), base) != base
        for base, name in LANGUAGE_BASE_NAMES.items()
    )


def test_every_name_reads_back_as_its_own_class() -> None:
    for base, name in LANGUAGE_BASE_NAMES.items():
        for spelling in (name, name.casefold(), name.upper()):
            assert language_name_class(spelling) == (base, None)
            assert language_equivalence_class(spelling) == (base, None)
        for script in LANGUAGE_SCRIPT_NAMES:
            named = language_display_name(base, script)
            assert named is not None
            assert language_identity(named) is not None
            assert language_equivalence_class(named) == (base, script)


@pytest.mark.parametrize(
    ("value", "name"),
    (
        ("en", "English"),
        ("eng", "English"),
        ("en-US", "English"),
        ("english", "English"),
        ("ENGLISH", "English"),
        ("spa", "Spanish"),
        ("ger", "German"),
        ("fre", "French"),
        ("gre", "Greek"),
        ("nob", "Norwegian Bokmål"),
        ("pan", "Punjabi"),
        ("bh", "Bihari languages"),
        ("gaa", "Ga language"),
        ("zh-Hans", "Chinese (Simplified)"),
        ("zh-Hant-TW", "Chinese (Traditional)"),
        ("sr-Cyrl", "Serbian (Cyrillic)"),
        ("mul", "Multiple languages"),
    ),
)
def test_canonical_name_of_a_supported_representation(value, name) -> None:
    assert language_canonical_name(value) == name


@pytest.mark.parametrize("value", ("und", "UND", "qqq", "en-Shui", "Klingonese", ""))
def test_unknown_unsupported_and_unnamed_classes_have_no_name(value) -> None:
    assert language_canonical_name(value) is None


def test_a_tag_is_read_before_a_name() -> None:
    assert language_identity("ga") is not None
    assert language_equivalence_class("ga") == ("ga", None)
    assert language_equivalence_class("Ga language") == ("gaa", None)
