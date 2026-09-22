"""Exact incumbent paragraph admission through the supported target reader."""

import pytest

from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import (
    TargetDestination,
    TargetFieldState,
)
from tests.support.audiobookshelf_targets import target_snapshot


@pytest.mark.parametrize(
    "value",
    (
        "First paragraph.\n\nSecond paragraph.",
        "First paragraph.\r\n\r\nSecond paragraph.",
        " \tCafé description.\t ",
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


def test_incumbent_description_comparison_is_not_source_identity() -> None:
    assert values_match(
        TargetDestination.DESCRIPTION,
        " Café\r\nSecond paragraph. ",
        "CAFE\u0301 second paragraph.",
    )
    assert not values_match(
        TargetDestination.DESCRIPTION, "First\nparagraph.", "Changed paragraph."
    )


@pytest.mark.parametrize(
    "value",
    (
        "First\rSecond",
        "First\tSecond",
        " \t\r\n ",
        "Text\x00",
        "Text\x85",
        "Text\u202e",
        "Text\u2028",
        "Text\ufdd0",
        "Text\uffff",
        "Text /home/private/book",
        "Text api_key=synthetic",
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


def test_domain_bounds_precede_safety_and_paths_precede_credentials() -> None:
    from mechanical_librarian.domain.evidence_bounds import (
        EvidenceValueError,
        validate_incumbent_description,
    )

    for suffix in ("\x00", " /home/private/book", " api_key=synthetic"):
        with pytest.raises(EvidenceValueError, match="size limit"):
            validate_incumbent_description("a" * 16385 + suffix, "description", 16384)
    with pytest.raises(EvidenceValueError, match="production path"):
        validate_incumbent_description(
            "/home/private/book api_key=synthetic", "description", 16384
        )
    with pytest.raises(EvidenceValueError, match="forbidden control"):
        validate_incumbent_description(
            "Text\x00 /home/private/book", "description", 16384
        )


def test_description_original_bounds_exact_type_and_scalar_contract() -> None:
    from mechanical_librarian.domain.evidence_bounds import (
        EvidenceValueError,
        validate_incumbent_description,
        validate_projection_text,
    )

    class Text(str):
        pass

    for value in (Text("Description"), None, 123, "Text\ud800"):
        with pytest.raises(EvidenceValueError):
            validate_incumbent_description(value, "description", 16384)
    text = " " + "a" * 16382 + "\n"
    assert validate_incumbent_description(text, "description", 16384) == text
    with pytest.raises(EvidenceValueError, match="size limit"):
        validate_incumbent_description(text + " ", "description", 16384)
    for value in ("First\nSecond", " padded ", "\tboundary"):
        with pytest.raises(EvidenceValueError):
            validate_projection_text(value, "scalar", 16384)
