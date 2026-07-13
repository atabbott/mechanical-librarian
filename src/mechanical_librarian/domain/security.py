"""Opaque role-scoped secret handles; material never enters domain values."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.domain.inventory import SecretReference


class SecretScope(StrEnum):
    TARGET_SERVICE = "target_service"
    METADATA_PROVIDER = "metadata_provider"
    COVER_PROVIDER = "cover_provider"
    REVIEWER = "reviewer"
    NOTIFICATION = "notification"


@dataclass(frozen=True, repr=False)
class SecretHandle:
    """Opaque role-scoped handle without credential material."""

    reference: SecretReference
    scope: SecretScope

    def __post_init__(self) -> None:
        if type(self.reference) is not SecretReference or type(self.scope) is not SecretScope:
            raise ValueError("secret handles require typed reference and role scope")

    def __repr__(self) -> str:
        return "SecretHandle(reference=<redacted>, scope=<typed>)"


__all__ = ["SecretHandle", "SecretScope"]
