"""Exact incumbent paragraph admission through the supported target reader."""

import pytest

from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_report import (
    ReportValueError,
    TargetDestination,
    TargetFacts,
    TargetField,
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
        ("Fish &amp; chips", "Fish & chips"),
        # A decoded no-break space is read as the space it shows.
        ("Text&nbsp;more", "Text more"),
        ("&nbsp;Text", " Text"),
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


@pytest.mark.parametrize(
    ("value", "restorable"),
    (
        ("Plain text.", True),
        ("Fish &amp; chips &lt;3", True),
        ("Fish & chips", True),
        ("He said &quot;yes&quot;", False),
        ("Text&nbsp;more", False),
        ("<p>Fish &amp; chips</p>", False),
    ),
)
def test_a_description_is_restorable_only_when_writing_its_text_keeps_it(
    value: str, restorable: bool
) -> None:
    # The library escapes only a bare ampersand and angle bracket, so only a
    # value stored as its text, or with just those escaped, survives a write
    # of that text unchanged (#1424).
    snapshot = target_snapshot({"title": "Title", "description": value})
    field = next(
        field
        for field in snapshot.fields
        if field.destination is TargetDestination.DESCRIPTION
    )
    assert (field.state, field.restorable) == (TargetFieldState.VALUE, restorable)
    facts = TargetFacts("primary", "ebooks", "item-1", snapshot.fields)
    assert TargetFacts.from_data(facts.to_data()) == facts
    title = next(
        field.to_data()
        for field in snapshot.fields
        if field.destination is TargetDestination.TITLE
    )
    assert "restorable" not in title


@pytest.mark.parametrize(
    "field",
    (
        lambda: TargetField(
            TargetDestination.TITLE, TargetFieldState.VALUE, "x", restorable=True
        ),
        lambda: TargetField(
            TargetDestination.DESCRIPTION, TargetFieldState.EMPTY, restorable=True
        ),
        lambda: TargetField(
            TargetDestination.DESCRIPTION,
            TargetFieldState.VALUE,
            "x",
            restorable=1,  # type: ignore[arg-type]
        ),
    ),
    ids=("title", "empty", "integer"),
)
def test_only_a_description_value_can_be_restorable(field) -> None:
    with pytest.raises(ReportValueError, match="target field is invalid"):
        field()


@pytest.mark.parametrize(
    ("destination", "data"),
    (
        ("description", {"state": "value", "value": "x"}),
        ("description", {"state": "value", "value": "x", "restorable": 1}),
        ("title", {"state": "value", "value": "x", "restorable": False}),
    ),
    ids=("absent", "integer", "title"),
)
def test_a_persisted_restorable_flag_is_exactly_where_it_belongs(
    destination: str, data: dict[str, object]
) -> None:
    facts = TargetFacts(
        "primary",
        "ebooks",
        "item-1",
        target_snapshot({"title": "Title", "description": "x"}).fields,
    ).to_data()
    fields = facts["fields"]
    assert type(fields) is list
    index = next(
        position
        for position, value in enumerate(fields)
        if value["destination"] == destination
    )
    fields[index] = {"destination": destination, **data}
    with pytest.raises(ReportValueError):
        TargetFacts.from_data(facts)


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


@pytest.mark.parametrize(
    ("stored", "stated"),
    (
        (
            "Fish &amp; chips for the qualification.",
            "Fish &amp; chips for the qualification.",
        ),
        (
            "He said &quot;yes&quot; to the qualification.",
            "He said &quot;yes&quot; to the qualification.",
        ),
        (
            "Fish &amp; chips for the qualification.",
            "Fish & chips for the qualification.",
        ),
        # The qualification's pre-escaped landing, read back as admitted.
        ("Already &amp; escaped.", "Already &amp; escaped."),
    ),
    ids=("entity", "quotes", "decoded", "pre_escaped"),
)
def test_a_stated_description_equal_to_the_incumbent_text_keeps_it(
    stored: str, stated: str
) -> None:
    # Both sides are decoded exactly once, so a provider that states the
    # same text with its character references is not a proposal (#1424).
    from mechanical_librarian.adapters.providers.visible_text import (
        projected_description,
    )
    from mechanical_librarian.domain.proposal_set import (
        ProposalAssessment,
        ProposalOutcome,
        evaluate_report,
    )
    from tests.support.proposal_evidence import (
        _fields,
        _local_description,
        _report,
        _result,
    )

    field = next(
        field
        for field in target_snapshot({"title": "Title", "description": stored}).fields
        if field.destination is TargetDestination.DESCRIPTION
    )
    candidate, transformed = projected_description(stated)
    assert type(candidate) is str and transformed is False
    result = _result(
        evaluate_report(
            _report(
                fields=_fields(description=field),
                local_claims=(_local_description(candidate),),
            )
        ),
        TargetDestination.DESCRIPTION,
    )
    assert result.outcome is ProposalOutcome.KEEP
    assert [value.assessment for value in result.alternatives] == [
        ProposalAssessment.PRESERVES_TARGET
    ]
