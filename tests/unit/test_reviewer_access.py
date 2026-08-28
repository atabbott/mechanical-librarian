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


def test_openrouter_selection_rejects_surrounding_model_whitespace() -> None:
    with pytest.raises(ValidationError, match="reviewer access is invalid"):
        ReviewerAccessDocument(
            schema_version="reviewer-access-v1",
            revision=1,
            cover_observation=OpenRouterTaskSelection(
                engine="openrouter", model=" example/vision"
            ),
        )
