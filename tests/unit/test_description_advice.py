"""Closed task projection, bounds and output refusal without model transport."""

import json

import pytest
from pydantic import ValidationError

from mechanical_librarian.application.contained_reviewer_contract import (
    ContainedReviewerError,
    ContainedReviewRequest,
)
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    DescriptionAdviceInput,
    DescriptionAdviceOutput,
    DescriptionAdviceSettings,
    DescriptionPreferences,
    description_document,
)
from tests.support.automatic_no_change import _automatic_no_change_inputs


def test_populated_target_projects_only_provider_descriptions() -> None:
    report, *_ = _automatic_no_change_inputs("description_preserved")
    value = DescriptionAdviceInput.from_report(report, DescriptionPreferences())
    document = description_document(value.model_dump())
    assert set(json.loads(document)) == {"descriptions", "preferences"}
    assert value.descriptions
    assert report.target.item not in document
    assert report.report_id not in document
    assert report.file.identity not in document
    assert report.file.content_digest not in document
    assert "sources" not in json.loads(document)
    request = ContainedReviewRequest(
        DESCRIPTION_INSTRUCTION, document, DESCRIPTION_OUTPUT_SCHEMA
    )
    changed = value.model_copy(
        update={"descriptions": ("A changed provider description.",)}
    )
    assert (
        request.digest
        != ContainedReviewRequest(
            DESCRIPTION_INSTRUCTION,
            description_document(changed.model_dump()),
            DESCRIPTION_OUTPUT_SCHEMA,
        ).digest
    )


def test_cleanup_defaults_do_not_enable_automatic_application() -> None:
    settings = DescriptionAdviceSettings()
    assert settings.cleanup().model_dump() == {
        "remove_marketing": True,
        "remove_sales_boasts": True,
        "remove_praise": True,
    }
    assert settings.automatic_application is False


@pytest.mark.parametrize(
    "descriptions",
    [
        ("same", "same"),
        ("z", "a"),
        (),
        ("x" * 16_385,),
        tuple(str(n) for n in range(257)),
    ],
)
def test_noncanonical_or_oversized_alternatives_refused(
    descriptions: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        DescriptionAdviceInput(
            descriptions=descriptions, preferences=DescriptionPreferences()
        )


def test_composition_overflow_is_refused_without_truncation() -> None:
    value = DescriptionAdviceInput(
        descriptions=tuple(sorted(f"{n:03d}" + "x" * 16_380 for n in range(256))),
        preferences=DescriptionPreferences(),
    )
    with pytest.raises(ContainedReviewerError):
        ContainedReviewRequest(
            DESCRIPTION_INSTRUCTION,
            description_document(value.model_dump()),
            DESCRIPTION_OUTPUT_SCHEMA,
        )


@pytest.mark.parametrize(
    "output",
    [
        {"description": "valid", "disposition": "ready", "confidence": 1},
        {"description": "valid", "disposition": "approved"},
        {"description": " ", "disposition": "ready"},
        {"description": "x" * 16_385, "disposition": "ready"},
    ],
)
def test_output_is_exactly_bounded_description_and_disposition(
    output: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DescriptionAdviceOutput.model_validate(output)
