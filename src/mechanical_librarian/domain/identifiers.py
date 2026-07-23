"""Small identifier vocabulary retained by the isolated EPUB boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, ClassVar, Self


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_LIBRARY = re.compile(r"^(?:[a-z][a-z0-9_-]{0,63}|[0-9][0-9a-f]{7}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$")
_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class DomainValueError(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class Identifier:
    value: str
    kind: ClassVar[str] = "identifier"

    def __post_init__(self) -> None:
        if type(self.value) is not str or _IDENTIFIER.fullmatch(self.value) is None:
            raise DomainValueError(f"{self.kind} is invalid")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"


class AdapterId(Identifier):
    kind = "adapter ID"


class ConnectionId(Identifier):
    kind = "connection ID"


class LibraryId(Identifier):
    kind = "library ID"

    def __post_init__(self) -> None:
        if type(self.value) is not str or _LIBRARY.fullmatch(self.value) is None:
            raise DomainValueError("library ID is invalid")


@dataclass(frozen=True, repr=False)
class ExternalItemId:
    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or not 1 <= len(self.value) <= 512 or self.value.strip() != self.value or "\x00" in self.value:
            raise DomainValueError("external item ID is invalid")


@dataclass(frozen=True)
class TargetId:
    adapter: AdapterId
    connection: ConnectionId
    library: LibraryId
    external_item: ExternalItemId


@dataclass(frozen=True, repr=False)
class Version:
    value: str
    kind: ClassVar[str] = "version"

    def __post_init__(self) -> None:
        if type(self.value) is not str or _VERSION.fullmatch(self.value) is None:
            raise DomainValueError(f"{self.kind} is invalid")

    def is_compatible_with(self, other: object) -> bool:
        return type(self) is type(other) and self.value == other.value


class SchemaVersion(Version):
    kind = "schema version"


class ExtractorVersion(Version):
    kind = "extractor version"


class CompatibilityVersion(Version):
    kind = "compatibility version"


@dataclass(frozen=True)
class Fingerprint:
    algorithm: str
    schema: SchemaVersion
    digest: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256" or type(self.schema) is not SchemaVersion or _DIGEST.fullmatch(self.digest) is None:
            raise DomainValueError("fingerprint is invalid")

    @classmethod
    def from_canonical_value(cls, value: Any, *, schema: SchemaVersion) -> Self:
        return cls("sha256", schema, hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


__all__ = [
    "AdapterId", "CompatibilityVersion", "ConnectionId", "DomainValueError",
    "ExternalItemId", "ExtractorVersion", "Fingerprint", "LibraryId", "SchemaVersion",
    "TargetId", "canonical_json",
]
