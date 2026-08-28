"""Closed owner-managed access selection for the installed cover reviewer."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

ReviewerTask = Literal["cover_observation"]
ReviewerEngine = Literal["codex", "openrouter"]


class ReviewerAccessError(ValueError):
    """Fixed refusal for an invalid reviewer-access document."""


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class OpenRouterTaskSelection(_Closed):
    engine: Literal["openrouter"]
    model: Annotated[StrictStr, Field(min_length=1, max_length=256)]


class CodexTaskSelection(_Closed):
    engine: Literal["codex"]


ReviewerTaskSelection = Annotated[
    CodexTaskSelection | OpenRouterTaskSelection,
    Field(discriminator="engine"),
]


class ReviewerAccessDocument(_Closed):
    """The one-task exact-current application authority for reviewer access."""

    schema_version: Literal["reviewer-access-v1"]
    revision: Annotated[StrictInt, Field(ge=1)]
    cover_observation: ReviewerTaskSelection | None = None

    @model_validator(mode="after")
    def _validate_document(self) -> "ReviewerAccessDocument":
        if self.cover_observation is not None and (
            isinstance(self.cover_observation, OpenRouterTaskSelection)
            and self.cover_observation.model.strip() != self.cover_observation.model
        ):
            raise ReviewerAccessError("reviewer access is invalid")
        return self


__all__ = [
    "CodexTaskSelection",
    "OpenRouterTaskSelection",
    "ReviewerAccessDocument",
    "ReviewerAccessError",
    "ReviewerEngine",
    "ReviewerTask",
    "ReviewerTaskSelection",
]
