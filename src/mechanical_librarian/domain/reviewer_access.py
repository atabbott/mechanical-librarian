"""Closed owner-managed access selection for the installed cover reviewer."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

ReviewerTask = Literal["cover_observation", "description_rewrite"]
ReviewerEngine = Literal["codex", "openrouter"]
_OPENROUTER_MODEL_ID = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,190}"
    r"(?::[A-Za-z0-9][A-Za-z0-9._-]{0,31})?$"
)
OpenRouterModelId = Annotated[
    StrictStr,
    Field(min_length=3, max_length=256, pattern=_OPENROUTER_MODEL_ID.pattern),
]


class ReviewerAccessError(ValueError):
    """Fixed refusal for an invalid reviewer-access document."""


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OpenRouterTaskSelection(_Closed):
    engine: Literal["openrouter"]
    model: OpenRouterModelId


class CodexTaskSelection(_Closed):
    engine: Literal["codex"]


ReviewerTaskSelection = Annotated[
    CodexTaskSelection | OpenRouterTaskSelection,
    Field(discriminator="engine"),
]


class DescriptionModelMetadata(_Closed):
    model: OpenRouterModelId
    observed_at: Annotated[StrictInt, Field(ge=0)]
    prompt_price: Annotated[StrictStr, Field(min_length=1, max_length=40)]
    completion_price: Annotated[StrictStr, Field(min_length=1, max_length=40)]
    request_price: Annotated[StrictStr, Field(min_length=1, max_length=40)]
    text_input: Literal[True] = True
    text_output: Literal[True] = True
    structured_outputs: Literal[True] = True

    @field_validator("prompt_price", "completion_price", "request_price")
    @classmethod
    def bounded_price(cls, value: str) -> str:
        try:
            price = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("invalid model price") from error
        if not price.is_finite() or not 0 <= price <= 1_000_000:
            raise ValueError("invalid model price")
        return value


class CoverModelMetadata(_Closed):
    """Observed catalogue prices for exactly the saved cover model."""

    model: OpenRouterModelId
    observed_at: Annotated[StrictInt, Field(ge=0)]
    prompt_price: Annotated[StrictStr, Field(min_length=1, max_length=40)] | None
    completion_price: Annotated[StrictStr, Field(min_length=1, max_length=40)] | None
    image_price: Annotated[StrictStr, Field(min_length=1, max_length=40)] | None
    request_price: Annotated[StrictStr, Field(min_length=1, max_length=40)] | None

    @field_validator("prompt_price", "completion_price", "image_price", "request_price")
    @classmethod
    def bounded_price(cls, value: str | None) -> str | None:
        return None if value is None else DescriptionModelMetadata.bounded_price(value)


class ReviewerAccessDocument(_Closed):
    """The task-scoped exact-current application authority for reviewer access."""

    schema_version: Literal["reviewer-access-v3"]
    revision: Annotated[StrictInt, Field(ge=1)]
    cover_observation: ReviewerTaskSelection | None = None
    description_rewrite: ReviewerTaskSelection | None = None
    description_metadata: DescriptionModelMetadata | None = None
    cover_metadata: CoverModelMetadata | None = None

    @model_validator(mode="after")
    def bound_cover_prices(self) -> ReviewerAccessDocument:
        if self.cover_metadata is not None and (
            not isinstance(self.cover_observation, OpenRouterTaskSelection)
            or self.cover_metadata.model != self.cover_observation.model
        ):
            raise ValueError("cover prices do not match selection")
        return self


__all__ = [
    "CodexTaskSelection",
    "OpenRouterTaskSelection",
    "OpenRouterModelId",
    "ReviewerAccessDocument",
    "ReviewerAccessError",
    "ReviewerEngine",
    "ReviewerTask",
    "ReviewerTaskSelection",
]
