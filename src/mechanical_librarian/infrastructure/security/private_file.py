"""Strict no-follow reader shared by role-scoped private-file credentials."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from mechanical_librarian.domain.inventory import SecretReference


@dataclass(frozen=True, repr=False)
class PrivateSecretMaterial:
    value: str

    def __repr__(self) -> str:
        return "PrivateSecretMaterial(<redacted>)"


def read_private_secret(reference: SecretReference) -> PrivateSecretMaterial | None:
    descriptor: int | None = None
    try:
        if type(reference) is not SecretReference:
            return None
        path = Path(reference.value)
        if not path.is_absolute():
            return None
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or not 1 <= information.st_size <= 2_048
            or information.st_mode & 0o077
        ):
            return None
        payload = os.read(descriptor, information.st_size + 1)
        if len(payload) != information.st_size:
            return None
        value = payload.decode("utf-8").strip()
        if (
            not value
            or len(value.encode("utf-8")) > 2_048
            or any(
                character.isspace() or not 0x21 <= ord(character) <= 0x7E
                for character in value
            )
        ):
            return None
        return PrivateSecretMaterial(value)
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


__all__ = ["PrivateSecretMaterial", "read_private_secret"]
