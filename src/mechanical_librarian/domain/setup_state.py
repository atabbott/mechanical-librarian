"""Closed durable ledger for the one supported first-run journey."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)

from mechanical_librarian.domain.owner_settings import (
    LibrarySettings,
    OwnerSettingsDocument,
    ProviderSettings,
)

SetupStep = Literal["claim", "connect", "providers", "libraries", "mounts", "kick_off"]
_RECORDED_STEPS: tuple[SetupStep, ...] = (
    "claim",
    "connect",
    "providers",
    "libraries",
    "mounts",
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class SetupStateError(ValueError):
    """Fixed, value-free refusal for the closed first-run state."""


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SetupConnection(_Closed):
    name: Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    endpoint: Annotated[StrictStr, Field(min_length=8, max_length=2048)]


class SetupMountResult(_Closed):
    library: Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    state: Literal["matched", "ambiguous", "missing", "unavailable"]


class SetupLibraryCandidate(_Closed):
    library_id: Annotated[StrictStr, Field(min_length=1, max_length=128)]
    name: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    media_kind: Literal["book", "unsupported"]
    service_paths: tuple[
        Annotated[StrictStr, Field(min_length=1, max_length=4096)], ...
    ] = Field(default=(), max_length=16, exclude=True)


class SetupStateDocument(_Closed):
    """The only incomplete authority admitted before the owner is established."""

    schema_version: Literal["setup-state-v1"]
    revision: Annotated[StrictInt, Field(ge=1)]
    bootstrap_verifier_digest: StrictStr
    completed_steps: list[SetupStep] = Field(default_factory=list, max_length=5)
    claimed: StrictBool = False
    connection: SetupConnection | None = None
    providers: list[ProviderSettings] = Field(default_factory=list, max_length=32)
    libraries: list[LibrarySettings] = Field(default_factory=list, max_length=128)
    mounts: list[SetupMountResult] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def _validate_ledger(self) -> "SetupStateDocument":
        if _DIGEST.fullmatch(self.bootstrap_verifier_digest) is None:
            raise SetupStateError("setup state is unavailable")
        expected = list(_RECORDED_STEPS[: len(self.completed_steps)])
        if self.completed_steps != expected:
            raise SetupStateError("setup state is unavailable")
        if self.claimed != ("claim" in self.completed_steps):
            raise SetupStateError("setup state is unavailable")
        if (self.connection is not None) != ("connect" in self.completed_steps):
            raise SetupStateError("setup state is unavailable")
        if (self.providers != []) != ("providers" in self.completed_steps):
            # A skipped provider step is represented by the empty list only
            # after it has completed; the preceding-state distinction is held
            # by the ledger rather than an extra flag.
            if not ("providers" in self.completed_steps and self.providers == []):
                raise SetupStateError("setup state is unavailable")
        if (self.libraries != []) != ("libraries" in self.completed_steps):
            raise SetupStateError("setup state is unavailable")
        if self.mounts and "libraries" not in self.completed_steps:
            raise SetupStateError("setup state is unavailable")
        if "mounts" in self.completed_steps and not self.mounts:
            raise SetupStateError("setup state is unavailable")
        return self

    def next_step(self) -> SetupStep | None:
        if len(self.completed_steps) < len(_RECORDED_STEPS):
            return _RECORDED_STEPS[len(self.completed_steps)]
        return "kick_off"

    def invalidate_after(self, step: SetupStep) -> "SetupStateDocument":
        """A changed answer removes dependent recorded answers deterministically."""
        if step == "kick_off":
            return self
        index = _RECORDED_STEPS.index(step)
        values = self.model_dump()
        values["completed_steps"] = list(_RECORDED_STEPS[:index])
        values["claimed"] = index > 0
        if index <= 1:
            values["connection"] = None
        if index <= 2:
            values["providers"] = []
        if index <= 3:
            values["libraries"] = []
        if index <= 4:
            values["mounts"] = []
        return SetupStateDocument.model_validate(values)

    def secret_fields(self) -> frozenset[str]:
        """Return every secret field this setup document permits."""
        fields: set[str] = set()
        if self.connection is not None:
            fields.add(f"connection.{self.connection.name}.key")
        fields.update(
            f"provider.{item.name}.key"
            for item in self.providers
            if item.enabled and item.adapter in {"hardcover", "google_books"}
        )
        return frozenset(fields)

    def required_secret_fields(self) -> frozenset[str]:
        """Return secrets required before this document can be resumed or promoted."""
        fields = (
            set()
            if self.connection is None
            else {f"connection.{self.connection.name}.key"}
        )
        fields.update(
            f"provider.{item.name}.key"
            for item in self.providers
            if item.enabled and item.adapter == "hardcover"
        )
        return frozenset(fields)

    def complete_owner_document(self) -> OwnerSettingsDocument:
        if self.completed_steps != list(_RECORDED_STEPS) or self.connection is None:
            raise SetupStateError("setup state is incomplete")
        return OwnerSettingsDocument.model_validate(
            {
                "schema_version": "owner-settings-v1",
                "revision": 1,
                "connections": [self.connection.model_dump()],
                "providers": [item.model_dump() for item in self.providers],
                "libraries": [item.model_dump() for item in self.libraries],
                # Kept in the strict v1 document for exact compatibility, but
                # live generations leave no restart debt behind.
                "pending_restart": [],
            }
        )


__all__ = [
    "SetupConnection",
    "SetupLibraryCandidate",
    "SetupMountResult",
    "SetupStateDocument",
    "SetupStateError",
    "SetupStep",
]
