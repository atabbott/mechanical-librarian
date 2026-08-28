from __future__ import annotations

import pytest
from pydantic import ValidationError

from mechanical_librarian.domain.reviewer_access import (
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
)


def test_document_admits_only_the_one_task_and_two_fixed_engines() -> None:
    assert ReviewerAccessDocument.model_validate(
        {
            "schema_version": "reviewer-access-v1",
            "revision": 1,
            "cover_observation": {"engine": "openrouter", "model": "example/vision"},
        }
    ).cover_observation == OpenRouterTaskSelection(
        engine="openrouter", model="example/vision"
    )
    with pytest.raises(ValidationError):
        ReviewerAccessDocument.model_validate(
            {
                "schema_version": "reviewer-access-v1",
                "revision": 1,
                "description_advice": {
                    "engine": "openrouter",
                    "model": "example/vision",
                },
            }
        )
    with pytest.raises(ValidationError):
        ReviewerAccessDocument.model_validate(
            {
                "schema_version": "reviewer-access-v1",
                "revision": 1,
                "cover_observation": {"engine": "other"},
            }
        )


@pytest.mark.parametrize(
    "model",
    (
        " example/vision",
        "example/vision ",
        "example /vision",
        "example/vision model",
        "example/vision\nmodel",
        "example",
        "/vision",
        "example/",
        "example//vision",
        "example/vision:",
        "example/vision:free:extra",
    ),
    ids=lambda value: value.encode("utf-8").hex(),
)
def test_openrouter_selection_rejects_hostile_or_ambiguous_model_ids(
    model: str,
) -> None:
    with pytest.raises(ValidationError):
        OpenRouterTaskSelection(engine="openrouter", model=model)


@pytest.mark.parametrize(
    "model",
    (
        "dots-studio/dots-3-note-preview:free",
        "mistralai/ministral-3b-2512",
        "google/gemma-3-12b-it",
        "mistralai/mistral-small-3.2-24b-instruct",
    ),
)
def test_openrouter_selection_admits_provider_local_model_ids(model: str) -> None:
    assert OpenRouterTaskSelection(engine="openrouter", model=model).model == model
