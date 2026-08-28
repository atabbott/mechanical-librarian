"""Closed owner-managed access selection for the installed cover reviewer."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

ReviewerTask = Literal["cover_observation"]
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


class ReviewerAccessDocument(_Closed):
    """The one-task exact-current application authority for reviewer access."""

    schema_version: Literal["reviewer-access-v1"]
    revision: Annotated[StrictInt, Field(ge=1)]
    cover_observation: ReviewerTaskSelection | None = None


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
