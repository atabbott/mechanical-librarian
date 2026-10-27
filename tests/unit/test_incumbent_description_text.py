"""Exact incumbent paragraph admission through the supported target reader."""

import pytest

from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import (
    TargetDestination,
    TargetFacts,
    TargetFieldState,
)
from tests.support.audiobookshelf_targets import target_snapshot


@pytest.mark.parametrize(
    "value",
    (
        "First paragraph.\n\nSecond paragraph.",
        "First paragraph.\r\n\r\nSecond paragraph.",
        " \tCafé description.\t ",
        "\r\n\tText\t\r\n",
        "\n \tText\t \n",
        "Text\u2028",
        "First paragraph.\u2028Second paragraph.",
    ),
)
def test_incumbent_description_retains_exact_text(value: str) -> None:
    snapshot = target_snapshot({"title": "Title", "description": value})
    field = next(
        field
        for field in snapshot.fields
        if field.destination is TargetDestination.DESCRIPTION
    )
    assert field.state is TargetFieldState.VALUE
    assert field.value == value
    facts = TargetFacts("primary", "ebooks", "item-1", snapshot.fields)
    assert TargetFacts.from_data(facts.to_data()) == facts


@pytest.mark.parametrize(
    ("value", "visible"),
    (
        (
            "<p>First paragraph.</p>\n<p>Second<br />paragraph.</p>",
            "First paragraph. • Second • paragraph.",
        ),
        ('See <a href="https://example.com/page">x</a>', "See x"),
        ("&lt;/p&gt;", "</p>"),
    ),
)
def test_incumbent_description_markup_is_admitted_as_its_visible_text(
    value: str, visible: str
) -> None:
    # Audiobookshelf stores HTML; the incumbent is the text it shows (#1424).
    snapshot = target_snapshot({"title": "Title", "description": value})
    field = next(
        field
        for field in snapshot.fields
        if field.destination is TargetDestination.DESCRIPTION
    )
    assert (field.state, field.value) == (TargetFieldState.VALUE, visible)
    facts = TargetFacts("primary", "ebooks", "item-1", snapshot.fields)
    assert TargetFacts.from_data(facts.to_data()) == facts


def test_incumbent_description_comparison_is_not_source_identity() -> None:
    assert values_match(
        TargetDestination.DESCRIPTION,
        " Café\r\nSecond paragraph. ",
        "CAFE\u0301 second paragraph.",
    )
    assert not values_match(
        TargetDestination.DESCRIPTION, "First\nparagraph.", "Changed paragraph."
    )


def test_incumbent_line_separator_and_markup_compare_as_text() -> None:
    # U+2028 is whitespace to the comparison; markup is never rendered (#1251).
    assert values_match(
        TargetDestination.DESCRIPTION, "First\u2028paragraph.", "First paragraph."
    )
    assert not values_match(TargetDestination.DESCRIPTION, "<p>X</p>", "X")


@pytest.mark.parametrize(
    "value",
    (
        "First\rSecond",
        "First\tSecond",
        " \t\r\n ",
        "Text\x00",
        "Text\x85",
        "Text\u202e",
        "Text\u2029",
        "It\x92s",
        "Text\ufdd0",
        "Text\uffff",
        "Text /home/private/book",
        "Text api_key=synthetic",
        "<p>Read /srv/media/b.m4b</p>",
        "</p>/srv/media",
        "&lt;/p&gt;/srv/media",
        " \t\\private\t ",
        " \tsecret: synthetic\t ",
        "\u00a0",
        "\u00a0Text",
        "Text\u00a0",
        " \n\u2003Text\t\r\n",
        "\rText",
        "Text\r",
    ),
)
def test_incumbent_description_rejects_unsafe_or_blank_text(value: str) -> None:
    snapshot = target_snapshot({"title": "Title", "description": value})
    field = next(
        field
        for field in snapshot.fields
        if field.destination is TargetDestination.DESCRIPTION
    )
    assert (field.state, field.value) == (TargetFieldState.MALFORMED, None)


@pytest.mark.parametrize(
    "suffix", ("\x00", " /home/private/book", " api_key=synthetic")
)
def test_intake_safety_refusal_precedes_oversized(suffix: str) -> None:
    snapshot = target_snapshot({"title": "Title", "description": "a" * 16385 + suffix})
    field = next(
        field
        for field in snapshot.fields
        if field.destination is TargetDestination.DESCRIPTION
    )
    assert field.state is TargetFieldState.MALFORMED


@pytest.mark.parametrize(
    "suffix", ("\x00", " /home/private/book", " api_key=synthetic")
)
def test_domain_bounds_precede_safety(suffix: str) -> None:
    from mechanical_librarian.domain.evidence_bounds import (
        EvidenceValueError,
        validate_incumbent_description,
    )

    with pytest.raises(EvidenceValueError, match="size limit"):
        validate_incumbent_description("a" * 16385 + suffix, "description", 16384)


@pytest.mark.parametrize(
    "value,message",
    (
        ("/home/private/book api_key=synthetic", "production path"),
        ("Text\x00 /home/private/book", "forbidden control"),
    ),
)
def test_domain_safety_precedence(value: str, message: str) -> None:
    from mechanical_librarian.domain.evidence_bounds import (
        EvidenceValueError,
        validate_incumbent_description,
    )

    with pytest.raises(EvidenceValueError, match=message):
        validate_incumbent_description(value, "description", 16384)


class Text(str):
    pass


@pytest.mark.parametrize("value", (Text("Description"), None, 123, "Text\ud800"))
def test_description_exact_type_and_unicode(value: object) -> None:
    from mechanical_librarian.domain.evidence_bounds import (
        EvidenceValueError,
        validate_incumbent_description,
    )

    with pytest.raises(EvidenceValueError):
        validate_incumbent_description(value, "description", 16384)


def test_description_original_bounds() -> None:
    from mechanical_librarian.domain.evidence_bounds import (
        EvidenceValueError,
        validate_incumbent_description,
    )

    text = " " + "a" * 16382 + "\n"
    assert validate_incumbent_description(text, "description", 16384) == text
    with pytest.raises(EvidenceValueError, match="size limit"):
        validate_incumbent_description(text + " ", "description", 16384)


@pytest.mark.parametrize("value", ("First\nSecond", " padded ", "\tboundary"))
def test_scalar_contract_unchanged(value: str) -> None:
    from mechanical_librarian.domain.evidence_bounds import (
        EvidenceValueError,
        validate_projection_text,
    )

    with pytest.raises(EvidenceValueError):
        validate_projection_text(value, "scalar", 16384)


def test_report_valid_multibyte_description_cannot_exceed_effect_byte_bound() -> None:
    from mechanical_librarian.domain.evidence_bounds import (
        validate_incumbent_description,
    )
    from mechanical_librarian.domain.field_effect import (
        FieldEffectError,
        validate_effect_value,
    )

    value = "語" * 12000
    assert validate_incumbent_description(value, "description", 16384) == value
    with pytest.raises(FieldEffectError, match="invalid_value"):
        validate_effect_value("description", value)
