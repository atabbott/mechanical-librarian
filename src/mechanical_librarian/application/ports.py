"""Least-authority contracts implemented by adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from mechanical_librarian.domain.identifiers import (
    Clock,
    Fingerprint,
    IdempotencyKey,
    IdentifierGenerator,
    TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    ItemSnapshot,
    LibraryConnection,
    SecretReference,
)


class FailureKind(StrEnum):
    AUTHENTICATION = "authentication"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    INVALID_RESPONSE = "invalid_response"
    RESOURCE_EXHAUSTED = "resource_exhausted"


@dataclass(frozen=True)
class PortFailure:
    kind: FailureKind
    retryable: bool
    safe_summary: str


T = TypeVar("T")


@dataclass(frozen=True)
class PortResult(Generic[T]):
    value: T | None = None
    failure: PortFailure | None = None

    def __post_init__(self) -> None:
        if (self.value is None) == (self.failure is None):
            raise ValueError("port result must contain exactly one of value or failure")


@dataclass(frozen=True)
class PageRequest:
    cursor: str | None
    limit: int

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 500:
            raise ValueError("page limit must be between 1 and 500")


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class WorkRequest:
    kind: str
    idempotency_key: IdempotencyKey
    target: TargetId


class Transaction(Protocol):
    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class TransactionManager(Protocol):
    def begin(self) -> Transaction: ...


class WorkScheduler(Protocol):
    def schedule(self, request: WorkRequest) -> PortResult[None]: ...


class LibraryService(Protocol):
    def discover_capabilities(self, connection: LibraryConnection) -> PortResult[AdapterCapabilities]: ...
    def list_snapshots(self, connection: LibraryConnection, page: PageRequest) -> PortResult[Page[ItemSnapshot]]: ...
    def read_snapshot(self, target: TargetId) -> PortResult[ItemSnapshot]: ...


class EvidenceExtractor(Protocol):
    def extract(self, snapshot: ItemSnapshot) -> PortResult[Fingerprint]: ...


class MetadataProvider(Protocol):
    def lookup(self, snapshot: ItemSnapshot, page: PageRequest) -> PortResult[Page[Fingerprint]]: ...


class Reviewer(Protocol):
    """Receives only staged, opaque evidence; it has no secret or write methods."""

    def review(self, packet_fingerprint: Fingerprint) -> PortResult[Fingerprint]: ...


class SecretResolver(Protocol):
    def configured(self, reference: SecretReference) -> bool: ...


class BackupReadiness(Protocol):
    def ready(self, target: TargetId) -> PortResult[bool]: ...


class AuditRecorder(Protocol):
    def record(self, kind: str, target: TargetId, summary: str) -> PortResult[None]: ...


class Notifier(Protocol):
    def notify(self, kind: str, summary: str) -> PortResult[None]: ...


__all__ = [
    "AuditRecorder", "BackupReadiness", "Clock", "EvidenceExtractor", "FailureKind",
    "IdentifierGenerator", "LibraryService", "MetadataProvider", "Notifier", "Page",
    "PageRequest", "PortFailure", "PortResult", "Reviewer", "SecretResolver",
    "Transaction", "TransactionManager", "WorkRequest", "WorkScheduler",
]
