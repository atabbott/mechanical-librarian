"""Stable, secret-safe identifiers and reproducibility primitives.

These value objects deliberately accept only already-scoped, bounded values.
They do not generate identifiers, read clocks, or inspect the host environment.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Protocol, Self, TypeVar


_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EXTERNAL_VALUE_MAX_LENGTH = 512


class DomainValueError(ValueError):
    """Raised when untrusted input cannot become a domain value."""


@dataclass(frozen=True, repr=False)
class Identifier:
    """An opaque internal identifier with a stable, non-empty representation."""

    value: str
    kind: ClassVar[str] = "identifier"

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.value):
            raise DomainValueError(
                f"{self.kind} must be 1-64 lowercase ASCII letters, digits, '_' or '-'"
            )

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"


class ConnectionId(Identifier):
    kind = "connection ID"


class LibraryId(Identifier):
    kind = "library ID"


class SnapshotId(Identifier):
    kind = "snapshot ID"


class EvidenceId(Identifier):
    kind = "evidence ID"


class ProposalId(Identifier):
    kind = "proposal ID"


class ReviewCaseId(Identifier):
    kind = "review case ID"


class DecisionId(Identifier):
    kind = "decision ID"


class DecisionAuthorizationId(Identifier):
    kind = "decision authorization ID"


class ApplyPlanId(Identifier):
    kind = "apply plan ID"


class ApplyAttemptId(Identifier):
    kind = "apply attempt ID"


class JobId(Identifier):
    kind = "job ID"


class BackupId(Identifier):
    kind = "backup ID"


class BulkDecisionBatchId(Identifier):
    kind = "bulk decision batch ID"


class AuditEventId(Identifier):
    kind = "audit event ID"


class AdapterId(Identifier):
    kind = "adapter ID"


@dataclass(frozen=True, repr=False)
class ExternalItemId:
    """An adapter-owned identifier that is never globally unique."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > _EXTERNAL_VALUE_MAX_LENGTH:
            raise DomainValueError("external item ID must be 1-512 characters")
        if self.value.strip() != self.value or "\x00" in self.value:
            raise DomainValueError("external item ID must be trimmed and contain no NUL")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "ExternalItemId(<redacted>)"


@dataclass(frozen=True)
class TargetId:
    """The complete namespace for a library-service item."""

    adapter: AdapterId
    connection: ConnectionId
    library: LibraryId
    external_item: ExternalItemId

    def __repr__(self) -> str:
        return (
            "TargetId("
            f"adapter={self.adapter!r}, connection={self.connection!r}, "
            f"library={self.library!r}, external_item={self.external_item!r})"
        )


@dataclass(frozen=True, repr=False)
class Version:
    """A comparable, schema-independent compatibility token."""

    value: str
    kind: ClassVar[str] = "version"

    def __post_init__(self) -> None:
        if not _VERSION_PATTERN.fullmatch(self.value):
            raise DomainValueError(
                f"{self.kind} must be 1-64 lowercase ASCII letters, digits, '.', '_' or '-'"
            )

    def __str__(self) -> str:
        return self.value

    def is_compatible_with(self, other: object) -> bool:
        """Return true only for an exact version kind and value match.

        Version tokens are opaque compatibility declarations, not semantic
        versions; callers must not infer ordering from their spelling.
        """

        return type(self) is type(other) and self.value == other.value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.value!r})"


class SchemaVersion(Version):
    kind = "schema version"


class PolicyVersion(Version):
    kind = "policy version"


class ExtractorVersion(Version):
    kind = "extractor version"


class PromptVersion(Version):
    kind = "prompt version"


class AdapterVersion(Version):
    kind = "adapter version"


class CompatibilityVersion(Version):
    kind = "compatibility version"


@dataclass(frozen=True)
class Fingerprint:
    """A versioned SHA-256 digest of a canonical, public-safe observation."""

    algorithm: str
    schema: SchemaVersion
    digest: str

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise DomainValueError("fingerprint algorithm must be 'sha256'")
        if not _DIGEST_PATTERN.fullmatch(self.digest):
            raise DomainValueError("fingerprint digest must be a lowercase SHA-256 digest")

    @classmethod
    def from_canonical_value(cls, value: Any, *, schema: SchemaVersion) -> Self:
        serialized = _canonical_json(value)
        return cls(
            algorithm="sha256",
            schema=schema,
            digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    def __repr__(self) -> str:
        return f"Fingerprint(algorithm={self.algorithm!r}, schema={self.schema!r}, digest=<redacted>)"


@dataclass(frozen=True)
class IdempotencyKey:
    """A deterministic key for one scoped external side effect."""

    operation: str
    target: TargetId
    digest: str

    def __post_init__(self) -> None:
        if not _IDENTIFIER_PATTERN.fullmatch(self.operation):
            raise DomainValueError("idempotency operation must be a bounded identifier")
        if not _DIGEST_PATTERN.fullmatch(self.digest):
            raise DomainValueError("idempotency digest must be a lowercase SHA-256 digest")

    @classmethod
    def from_canonical_input(
        cls, *, operation: str, target: TargetId, value: Any
    ) -> Self:
        serialized = _canonical_json(
            {
                "operation": operation,
                "target": {
                    "adapter": target.adapter.value,
                    "connection": target.connection.value,
                    "library": target.library.value,
                    "external_item": target.external_item.value,
                },
                "value": value,
            }
        )
        return cls(
            operation=operation,
            target=target,
            digest=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    def __repr__(self) -> str:
        return f"IdempotencyKey(operation={self.operation!r}, target={self.target!r}, digest=<redacted>)"


TIdentifier = TypeVar("TIdentifier", bound=Identifier)


class IdentifierGenerator(Protocol[TIdentifier]):
    """An injected source of valid internal identifiers."""

    def new(self) -> TIdentifier: ...


class Clock(Protocol):
    """An injected source of timezone-aware instants."""

    def now(self) -> datetime: ...


def _canonical_json(value: Any) -> str:
    """Serialize JSON-compatible values deterministically without coercion."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise DomainValueError("canonical input must be finite JSON-compatible data") from error
