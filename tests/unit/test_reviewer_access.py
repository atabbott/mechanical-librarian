from __future__ import annotations

import pytest
from pydantic import ValidationError

from mechanical_librarian.domain.reviewer_access import (
    REVIEWER_ACCESS_SCHEMA,
    DescriptionModelMetadata,
    OpenRouterTaskSelection,
    ReviewerAccessDocument,
)


def test_document_admits_only_two_tasks_and_two_fixed_engines() -> None:
    assert ReviewerAccessDocument.model_validate(
        {
            "schema_version": REVIEWER_ACCESS_SCHEMA,
            "revision": 1,
            "cover_observation": {"engine": "openrouter", "model": "example/vision"},
        }
    ).cover_observation == OpenRouterTaskSelection(
        engine="openrouter", model="example/vision"
    )
    with pytest.raises(ValidationError):
        ReviewerAccessDocument.model_validate(
            {
                "schema_version": REVIEWER_ACCESS_SCHEMA,
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
                "schema_version": REVIEWER_ACCESS_SCHEMA,
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


def test_predecessor_reviewer_document_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReviewerAccessDocument.model_validate_json(
            '{"cover_observation":null,"revision":1,"schema_version":"reviewer-access-v1"}'
        )


def test_frozen_v2_producer_document_is_rejected():
    with pytest.raises(ValidationError):
        ReviewerAccessDocument.model_validate_json(
            '{"cover_observation":null,"description_metadata":null,"description_rewrite":null,"revision":1,"schema_version":"reviewer-access-v2"}'
        )


@pytest.mark.parametrize("model", [None, "example/other"])
def test_cover_prices_reject_absent_or_different_model(model):
    with pytest.raises(ValidationError):
        ReviewerAccessDocument.model_validate(
            {
                "schema_version": REVIEWER_ACCESS_SCHEMA,
                "revision": 1,
                "cover_observation": None
                if model is None
                else {"engine": "openrouter", "model": model},
                "cover_metadata": {
                    "model": "example/vision",
                    "observed_at": 1,
                    "prompt_price": "0",
                    "completion_price": None,
                    "image_price": None,
                    "request_price": None,
                },
            }
        )


_DESCRIPTION_METADATA = {
    "model": "example/text",
    "observed_at": 1,
    "prompt_price": "0.000001",
    "completion_price": "0.000002",
    "request_price": "0",
}
_ABSENT = object()


@pytest.mark.parametrize(
    ("changes", "admitted"),
    [
        ({"request_price": None}, True),
        ({"request_price": "0"}, True),
        ({"request_price": _ABSENT}, False),
        ({"request_price": "-1"}, False),
        ({"request_price": "NaN"}, False),
        ({"request_price": "abc"}, False),
        ({"request_price": "1000001"}, False),
        ({"request_price": ""}, False),
        ({"request_price": 0}, False),
        ({"prompt_price": None}, False),
        ({"completion_price": None}, False),
    ],
    ids=[
        "null_fee",
        "predecessor_fee",
        "absent_key",
        "negative",
        "not_a_number",
        "malformed",
        "over_bound",
        "empty",
        "non_string",
        "missing_prompt",
        "missing_completion",
    ],
)
def test_description_request_price_is_optional_but_bounded(changes, admitted) -> None:
    document = {
        key: value
        for key, value in {**_DESCRIPTION_METADATA, **changes}.items()
        if value is not _ABSENT
    }

    if admitted:
        metadata = DescriptionModelMetadata.model_validate(document)
        assert metadata.request_price == changes["request_price"]
        return
    with pytest.raises(ValidationError):
        DescriptionModelMetadata.model_validate(document)


_TASTE_PRICES = {
    "model": "example/vision",
    "observed_at": 1,
    "prompt_price": "0.000003",
    "completion_price": None,
    "image_price": "0.0048",
    "request_price": None,
}


def test_taste_build_is_openrouter_only_with_prices_for_its_model() -> None:
    """The taste profile builder is its own OpenRouter task (#1372 R3)."""
    document = ReviewerAccessDocument.model_validate(
        {
            "schema_version": REVIEWER_ACCESS_SCHEMA,
            "revision": 1,
            "cover_taste_build": {"engine": "openrouter", "model": "example/vision"},
            "taste_metadata": _TASTE_PRICES,
        }
    )
    assert document.cover_taste_build == OpenRouterTaskSelection(
        engine="openrouter", model="example/vision"
    )
    for refused in (
        {"cover_taste_build": {"engine": "codex"}},
        {
            "cover_taste_build": {
                "engine": "openrouter",
                "model": "example/vision",
                "x": 1,
            }
        },
        {"taste_metadata": _TASTE_PRICES},
        {
            "cover_taste_build": {"engine": "openrouter", "model": "example/other"},
            "taste_metadata": _TASTE_PRICES,
        },
        {"cover_taste_judge": {"engine": "openrouter", "model": "example/vision"}},
    ):
        with pytest.raises(ValidationError):
            ReviewerAccessDocument.model_validate(
                {"schema_version": REVIEWER_ACCESS_SCHEMA, "revision": 1, **refused}
            )


def test_frozen_v3_document_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReviewerAccessDocument.model_validate_json(
            '{"cover_metadata":null,"cover_observation":null,"description_metadata":null,'
            '"description_rewrite":null,"revision":1,"schema_version":"reviewer-access-v3"}'
        )
