"""Immutable, least-authority contracts implemented by service adapters.

The contracts in this module are deliberately vendor and transport neutral.  They
describe what application orchestration may ask an adapter to do, not how an
adapter performs it.  Capability declarations are observations only: a write
request still needs an independently issued authorization and stale-state guard.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, StrEnum
from collections.abc import Callable
from typing import Generic, Protocol, TypeVar

from mechanical_librarian.application.audit import AuditRepository
from mechanical_librarian.application.job_heartbeat import (
    JobHeartbeatRepository,
    RepositoryOutcome,
    RepositoryResult,
)
from mechanical_librarian.domain.evidence import (
    ConfidenceFact,
    EvidenceRecord,
    FieldProposal,
    FieldValue,
    ReviewerEvidencePacket,
)
from mechanical_librarian.domain.evidence_bounds import (
    validate_projection_text,
    validate_safe_diagnostic_text,
)
from mechanical_librarian.domain.identifiers import (
    AdapterVersion,
    ApplyPlanId,
    Clock,
    CompatibilityVersion,
    DecisionAuthorizationId,
    EvidenceId,
    Fingerprint,
    IdempotencyKey,
    Identifier,
    IdentifierGenerator,
    ProposalId,
    SchemaVersion,
    ServiceVersion,
    SnapshotId,
    TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    ConcurrencyToken,
    CoverReference,
    FieldName,
    FileReference,
    ItemSnapshot,
    LibraryConnection,
    MediaType,
    MetadataField,
    SecretReference,
)


MAX_PAGE_SIZE = 500
MAX_SUMMARY_LENGTH = 256
MAX_PROVIDER_FACTS = 16
MAX_PROVIDER_VALUES = 32
MAX_REVIEW_DECISIONS = 128
MAX_MUTATIONS = 32
_SAFE_TEXT = re.compile(r"^[\x20-\x7e]+$")
_CODE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def _bounded_text(value: object, label: str, maximum: int = MAX_SUMMARY_LENGTH) -> str:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value) > maximum
        or not _SAFE_TEXT.fullmatch(value)
    ):
        raise ValueError(f"{label} must be trimmed printable ASCII up to {maximum} characters")
    return value


def _bounded_code(value: object, label: str) -> str:
    if type(value) is not str or not _CODE.fullmatch(value):
        raise ValueError(f"{label} must be a bounded lowercase identifier")
    return value


def _aware_datetime(value: object, label: str) -> datetime:
    if type(value) is not datetime:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    try:
        offset = value.utcoffset()
        value.astimezone(timezone.utc)
    except Exception:
        raise ValueError(f"{label} must be representable in UTC") from None
    if value.tzinfo is None or offset is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value


class Unit(Enum):
    """A real value for successful operations that have no response payload."""

    VALUE = ()


class FailureKind(StrEnum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"
    INCOMPATIBLE = "incompatible"
    INVALID_REQUEST = "invalid_request"
    INVALID_RESPONSE = "invalid_response"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    INTEGRITY_FAILURE = "integrity_failure"


@dataclass(frozen=True, repr=False)
class SafeSummary:
    value: str

    def __post_init__(self) -> None:
        try:
            validate_safe_diagnostic_text(self.value, "safe summary", MAX_SUMMARY_LENGTH)
        except ValueError:
            raise ValueError("safe summary must be bounded public-safe diagnostic text") from None

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return "SafeSummary(<redacted>)"


@dataclass(frozen=True)
class RetryAdvice:
    retryable: bool
    retry_after: timedelta | None = None

    def __post_init__(self) -> None:
        if type(self.retryable) is not bool:
            raise ValueError("retryable must be a boolean")
        if self.retry_after is not None and (
            type(self.retry_after) is not timedelta
            or self.retry_after <= timedelta(0)
            or self.retry_after > timedelta(days=1)
        ):
            raise ValueError("retry delay must be greater than zero and no more than one day")
        if not self.retryable and self.retry_after is not None:
            raise ValueError("non-retryable failures cannot specify a retry delay")


@dataclass(frozen=True)
class PortFailure:
    kind: FailureKind
    retry: RetryAdvice
    safe_summary: SafeSummary

    def __post_init__(self) -> None:
        if type(self.kind) is not FailureKind or type(self.retry) is not RetryAdvice or type(self.safe_summary) is not SafeSummary:
            raise ValueError("port failures require typed kind, retry advice, and safe summary")
        if self.kind in {FailureKind.INVALID_REQUEST, FailureKind.AUTHORIZATION, FailureKind.UNSUPPORTED, FailureKind.INCOMPATIBLE, FailureKind.CONFLICT} and self.retry.retryable:
            raise ValueError("deterministic failures cannot be marked retryable")


T = TypeVar("T")


@dataclass(frozen=True)
class PortResult(Generic[T]):
    value: T | None = None
    failure: PortFailure | None = None

    def __post_init__(self) -> None:
        if self.failure is not None and type(self.failure) is not PortFailure:
            raise ValueError("port result failures must be exact PortFailure values")
        if (self.value is None) == (self.failure is None):
            raise ValueError("port result must contain exactly one of value or failure")

    @classmethod
    def success(cls, value: T) -> PortResult[T]:
        if value is None:
            raise ValueError("use Unit.VALUE for a success without a payload")
        return cls(value=value)

    @classmethod
    def failed(cls, failure: PortFailure) -> PortResult[T]:
        if type(failure) is not PortFailure:
            raise ValueError("failed results require an exact PortFailure")
        return cls(failure=failure)


@dataclass(frozen=True)
class Deadline:
    at: datetime

    def __post_init__(self) -> None:
        if type(self.at) is not datetime or self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("deadline must be a timezone-aware datetime")
        self.at.astimezone(timezone.utc)

    def expired_at(self, observed_at: datetime) -> bool:
        _aware_datetime(observed_at, "deadline observation")
        return observed_at >= self.at


class CancellationId(Identifier):
    kind = "cancellation ID"


@dataclass(frozen=True, repr=False)
class CancellationProbe:
    """Observation-only cancellation surface; adapters cannot request cancellation."""

    id: CancellationId
    _reader: Callable[[], bool]

    def __post_init__(self) -> None:
        if type(self.id) is not CancellationId or not callable(self._reader) or type(self._reader()) is not bool:
            raise ValueError("cancellation probes require a typed ID and boolean observation")

    def is_cancelled(self) -> bool:
        state = self._reader()
        if type(state) is not bool:
            raise ValueError("cancellation observation must remain boolean")
        return state

    def __repr__(self) -> str:
        return "CancellationProbe(id=<redacted>, state=<observed>)"


@dataclass(frozen=True, repr=False)
class TimeProbe:
    """Injected current-time observation used for deadline and grant checks."""

    _reader: Callable[[], datetime]

    def __post_init__(self) -> None:
        if not callable(self._reader):
            raise ValueError("time probes require a callable observation")
        _aware_datetime(self._reader(), "time observation")

    def now(self) -> datetime:
        return _aware_datetime(self._reader(), "time observation")

    def __repr__(self) -> str:
        return "TimeProbe(<observed>)"


@dataclass(frozen=True)
class ResourceBounds:
    max_items: int
    max_bytes: int

    def __post_init__(self) -> None:
        if type(self.max_items) is not int or not 1 <= self.max_items <= 10_000:
            raise ValueError("resource item bound must be between 1 and 10000")
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 64 * 1024 * 1024:
            raise ValueError("resource byte bound must be between 1 and 67108864")


@dataclass(frozen=True)
class OperationContext:
    deadline: Deadline
    cancellation: CancellationProbe
    resources: ResourceBounds
    time: TimeProbe

    def __post_init__(self) -> None:
        if type(self.deadline) is not Deadline or type(self.cancellation) is not CancellationProbe or type(self.resources) is not ResourceBounds or type(self.time) is not TimeProbe:
            raise ValueError("operation context requires typed deadline, cancellation, and bounds")
        if type(self.cancellation.id) is not CancellationId or type(self.cancellation.is_cancelled()) is not bool:
            raise ValueError("cancellation probes must expose only typed observation state")

    @property
    def observed_at(self) -> datetime:
        return self.time.now()

    @property
    def expired(self) -> bool:
        return self.deadline.expired_at(self.observed_at)

    @property
    def cancelled(self) -> bool:
        return self.cancellation.is_cancelled()

    def preflight(self, *, item_count: int, serialized_bytes: int) -> None:
        if type(item_count) is not int or type(serialized_bytes) is not int or item_count < 0 or serialized_bytes < 0:
            raise ValueError("operation preflight requires non-negative exact resource counts")
        if self.cancelled:
            raise ValueError("operation was cancelled before external I/O")
        if self.expired:
            raise ValueError("operation deadline expired before external I/O")
        if item_count > self.resources.max_items or serialized_bytes > self.resources.max_bytes:
            raise ValueError("operation exceeds declared resource bounds")

    def preflight_result(self, *, item_count: int, serialized_bytes: int) -> PortResult[Unit]:
        if self.cancelled:
            return PortResult.failed(PortFailure(FailureKind.CANCELLED, RetryAdvice(False), SafeSummary("operation cancelled")))
        if self.expired:
            return PortResult.failed(PortFailure(FailureKind.TIMEOUT, RetryAdvice(True), SafeSummary("operation deadline expired")))
        if type(item_count) is not int or type(serialized_bytes) is not int or item_count < 0 or serialized_bytes < 0:
            return PortResult.failed(PortFailure(FailureKind.INVALID_REQUEST, RetryAdvice(False), SafeSummary("operation resource accounting invalid")))
        if item_count > self.resources.max_items or serialized_bytes > self.resources.max_bytes:
            return PortResult.failed(PortFailure(FailureKind.RESOURCE_EXHAUSTED, RetryAdvice(False), SafeSummary("operation resource bounds exceeded")))
        return PortResult.success(Unit.VALUE)


@dataclass(frozen=True)
class PageRequest:
    cursor: str | None
    limit: int

    def __post_init__(self) -> None:
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"page limit must be between 1 and {MAX_PAGE_SIZE}")
        if self.cursor is not None:
            _bounded_text(self.cursor, "page cursor", 512)


@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or len(self.items) > MAX_PAGE_SIZE:
            raise ValueError("page items must be an immutable bounded tuple")
        if any(type(item) in {str, bytes, int, float, bool, dict, list, set, tuple} for item in self.items):
            raise ValueError("page items must be typed application/domain values, not raw payloads")
        if self.next_cursor is not None:
            _bounded_text(self.next_cursor, "next page cursor", 512)


class Capability(StrEnum):
    INVENTORY_READ = "inventory_read"
    METADATA_READ = "metadata_read"
    COVER_READ = "cover_read"
    METADATA_WRITE = "metadata_write"
    COVER_WRITE = "cover_write"
    NOTIFICATIONS = "notifications"
    PAGINATION = "pagination"
    CONCURRENCY_TOKENS = "concurrency_tokens"


@dataclass(frozen=True)
class CapabilitySet:
    supported: frozenset[Capability]

    def __post_init__(self) -> None:
        if type(self.supported) is not frozenset or not all(type(item) is Capability for item in self.supported):
            raise ValueError("capabilities must be an immutable typed set")
        if Capability.METADATA_WRITE in self.supported and Capability.METADATA_READ not in self.supported:
            raise ValueError("metadata write capability requires metadata read capability")
        if Capability.COVER_WRITE in self.supported and Capability.COVER_READ not in self.supported:
            raise ValueError("cover write capability requires cover read capability")


class CompatibilityStatus(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CompatibilityReport:
    contract: CompatibilityVersion
    adapter: AdapterVersion
    service: ServiceVersion | None
    status: CompatibilityStatus
    summary: SafeSummary

    def __post_init__(self) -> None:
        if type(self.contract) is not CompatibilityVersion or type(self.adapter) is not AdapterVersion or (self.service is not None and type(self.service) is not ServiceVersion):
            raise ValueError("compatibility reports require role-specific typed version values")
        if type(self.status) is not CompatibilityStatus or type(self.summary) is not SafeSummary:
            raise ValueError("compatibility reports require typed status and summary")


class HealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class HealthReport:
    status: HealthStatus
    checked_at: datetime
    summary: SafeSummary

    def __post_init__(self) -> None:
        if type(self.status) is not HealthStatus or type(self.summary) is not SafeSummary:
            raise ValueError("health reports require typed status and summary")
        _aware_datetime(self.checked_at, "health check time")


class AdapterDiagnostics(Protocol):
    def capabilities(self) -> CapabilitySet: ...
    def compatibility(self, context: OperationContext) -> PortResult[CompatibilityReport]: ...
    def health(self, context: OperationContext) -> PortResult[HealthReport]: ...


class Transaction(Protocol):
    def commit(self, context: OperationContext) -> PortResult[Unit]: ...
    def rollback(self, context: OperationContext) -> PortResult[Unit]: ...


class TransactionManager(Protocol):
    def begin(self, context: OperationContext) -> PortResult[Transaction]: ...


@dataclass(frozen=True)
class SnapshotStoreRequest:
    snapshot: ItemSnapshot
    idempotency_key: IdempotencyKey
    expected_previous: Fingerprint | None
    context: OperationContext

    def __post_init__(self) -> None:
        if type(self.snapshot) is not ItemSnapshot or type(self.idempotency_key) is not IdempotencyKey:
            raise ValueError("snapshot storage requires typed snapshot and idempotency key")
        if self.idempotency_key.target != self.snapshot.target:
            raise ValueError("snapshot storage idempotency must match the snapshot target")
        if self.idempotency_key.operation != "snapshot-store":
            raise ValueError("snapshot storage requires the snapshot-store operation")
        if self.expected_previous is not None and type(self.expected_previous) is not Fingerprint:
            raise ValueError("expected previous snapshot state must be a fingerprint")
        if type(self.context) is not OperationContext:
            raise ValueError("snapshot storage requires an operation context")


class SnapshotRepository(Protocol):
    """Canonical snapshot persistence with no database or query-language types."""

    def store(self, request: SnapshotStoreRequest) -> RepositoryResult[ItemSnapshot]: ...
    def read(self, request: LibraryReadRequest) -> RepositoryResult[ItemSnapshot]: ...
    def list(self, request: LibraryListRequest) -> RepositoryResult[Page[ItemSnapshot]]: ...


@dataclass(frozen=True)
class WorkRequest:
    kind: str
    idempotency_key: IdempotencyKey
    target: TargetId
    context: OperationContext

    def __post_init__(self) -> None:
        _bounded_code(self.kind, "work kind")
        if type(self.idempotency_key) is not IdempotencyKey or type(self.target) is not TargetId or type(self.context) is not OperationContext:
            raise ValueError("work requests require typed idempotency, target, and context")
        if self.idempotency_key.target != self.target:
            raise ValueError("work request idempotency must match the request target")


class WorkScheduler(Protocol):
    def schedule(self, request: WorkRequest) -> PortResult[Unit]: ...


@dataclass(frozen=True)
class LibraryListRequest:
    connection: LibraryConnection
    page: PageRequest
    context: OperationContext

    def __post_init__(self) -> None:
        if type(self.connection) is not LibraryConnection or type(self.page) is not PageRequest or type(self.context) is not OperationContext:
            raise ValueError("library list requests require exact connection, page, and context values")


@dataclass(frozen=True)
class LibraryReadRequest:
    target: TargetId
    context: OperationContext

    def __post_init__(self) -> None:
        if type(self.target) is not TargetId or type(self.context) is not OperationContext:
            raise ValueError("library read requests require exact target and context values")


class LibraryReader(Protocol):
    def discover_capabilities(self, connection: LibraryConnection, context: OperationContext) -> PortResult[AdapterCapabilities]: ...
    def list_snapshots(self, request: LibraryListRequest) -> PortResult[Page[ItemSnapshot]]: ...
    def read_snapshot(self, request: LibraryReadRequest) -> PortResult[ItemSnapshot]: ...


# Backwards-compatible role name for the former read-only scaffold.
LibraryService = LibraryReader


@dataclass(frozen=True)
class ExpectedState:
    fingerprint: Fingerprint
    concurrency_token: ConcurrencyToken | None

    def __post_init__(self) -> None:
        if type(self.fingerprint) is not Fingerprint or (self.concurrency_token is not None and type(self.concurrency_token) is not ConcurrencyToken):
            raise ValueError("expected state requires a fingerprint and optional concurrency token")


class WriteGrantId(Identifier):
    kind = "write grant ID"


def _mutation_payload(mutations: tuple[WriteMutation, ...]) -> list[dict[str, object]]:
    if type(mutations) is not tuple or not mutations or not all(type(item) in {MetadataMutation, CoverMutation} for item in mutations):
        raise ValueError("mutation payload requires exact immutable mutation values")
    values: list[dict[str, object]] = []
    metadata_destinations: set[FieldName] = set()
    cover_seen = False
    for mutation in mutations:
        if type(mutation) is MetadataMutation:
            if mutation.field.name in metadata_destinations:
                raise ValueError("mutation payload cannot repeat a metadata field destination")
            metadata_destinations.add(mutation.field.name)
            values.append({"kind": "metadata", "field": mutation.field.name.value, "values": mutation.field.values})
        else:
            if cover_seen:
                raise ValueError("mutation payload cannot contain more than one cover destination")
            cover_seen = True
            fingerprint = mutation.cover.content_fingerprint
            values.append({
                "kind": "cover",
                "reference": mutation.cover.opaque_reference,
                "fingerprint": None if fingerprint is None else {
                    "algorithm": fingerprint.algorithm,
                    "schema": fingerprint.schema.value,
                    "digest": fingerprint.digest,
                },
            })
    return values


def mutation_digest(mutations: tuple[WriteMutation, ...]) -> str:
    values = _mutation_payload(mutations)
    serialized = json.dumps(values, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def mutation_size(mutations: tuple[WriteMutation, ...]) -> int:
    serialized = json.dumps(_mutation_payload(mutations), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return len(serialized.encode("utf-8"))


@dataclass(frozen=True, repr=False)
class WriteGrant:
    id: WriteGrantId
    target: TargetId
    mutation_digest: str
    expected: ExpectedState
    idempotency_key: IdempotencyKey
    decision_authorization: DecisionAuthorizationId
    apply_plan: ApplyPlanId
    backup: BackupReadinessReport
    expires_at: datetime

    def __post_init__(self) -> None:
        if type(self.id) is not WriteGrantId or type(self.target) is not TargetId:
            raise ValueError("write grants require typed identity and target scope")
        if type(self.mutation_digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", self.mutation_digest):
            raise ValueError("write grants require a mutation-set digest")
        if type(self.expected) is not ExpectedState or type(self.idempotency_key) is not IdempotencyKey or self.idempotency_key.target != self.target:
            raise ValueError("write grants require exact target-scoped expected state and idempotency")
        if type(self.decision_authorization) is not DecisionAuthorizationId or type(self.apply_plan) is not ApplyPlanId:
            raise ValueError("write grants require decision authorization and apply-plan provenance")
        if type(self.backup) is not BackupReadinessReport or self.backup.state is not BackupState.READY or self.backup.target != self.target or self.backup.expected != self.expected:
            raise ValueError("write grants require matching verified backup readiness")
        _aware_datetime(self.expires_at, "write grant expiry")

    def __repr__(self) -> str:
        return "WriteGrant(id=<redacted>, target=<scoped>, operation=<redacted>, expires_at=<redacted>)"


@dataclass(frozen=True)
class MetadataMutation:
    field: MetadataField

    def __post_init__(self) -> None:
        if type(self.field) is not MetadataField:
            raise ValueError("metadata mutation requires one normalized metadata field")


@dataclass(frozen=True)
class CoverMutation:
    cover: CoverReference

    def __post_init__(self) -> None:
        if type(self.cover) is not CoverReference:
            raise ValueError("cover mutation requires one opaque cover reference")


WriteMutation = MetadataMutation | CoverMutation


@dataclass(frozen=True)
class WriteRequest:
    target: TargetId
    mutations: tuple[WriteMutation, ...]
    grant: WriteGrant
    idempotency_key: IdempotencyKey
    expected: ExpectedState
    context: OperationContext

    def __post_init__(self) -> None:
        if type(self.target) is not TargetId or type(self.grant) is not WriteGrant or self.grant.target != self.target:
            raise ValueError("write grant must be separately issued for the exact target")
        if type(self.mutations) is not tuple or not 1 <= len(self.mutations) <= MAX_MUTATIONS or not all(type(item) in {MetadataMutation, CoverMutation} for item in self.mutations):
            raise ValueError("write mutations must be a non-empty bounded immutable typed tuple")
        if type(self.idempotency_key) is not IdempotencyKey or self.idempotency_key.target != self.target:
            raise ValueError("write idempotency must be scoped to the exact target")
        if type(self.expected) is not ExpectedState or type(self.context) is not OperationContext:
            raise ValueError("writes require typed expected state and operation context")
        if self.grant.expected != self.expected or self.grant.idempotency_key != self.idempotency_key or self.grant.mutation_digest != mutation_digest(self.mutations):
            raise ValueError("write request does not match its immutable grant")
        self.preflight()

    def preflight(self) -> None:
        """Revalidate immediately before a supported external mutation."""

        if type(self.grant) is not WriteGrant or self.grant.target != self.target:
            raise ValueError("write grant no longer matches target")
        if self.grant.expected != self.expected or self.grant.idempotency_key != self.idempotency_key or self.grant.mutation_digest != mutation_digest(self.mutations):
            raise ValueError("write bindings changed before external mutation")
        now = self.context.observed_at
        if now >= self.grant.expires_at:
            raise ValueError("write grant expired before external mutation")
        if self.grant.backup.state is not BackupState.READY or self.grant.backup.target != self.target or self.grant.backup.expected != self.expected or self.grant.backup.checked_at > now:
            raise ValueError("write backup readiness is not current and matching")
        self.context.preflight(item_count=len(self.mutations), serialized_bytes=mutation_size(self.mutations))

    def preflight_result(self) -> PortResult[Unit]:
        context_result = self.context.preflight_result(item_count=len(self.mutations), serialized_bytes=mutation_size(self.mutations))
        if context_result.failure is not None:
            return context_result
        now = self.context.observed_at
        if now >= self.grant.expires_at:
            return PortResult.failed(PortFailure(FailureKind.AUTHORIZATION, RetryAdvice(False), SafeSummary("write grant expired")))
        if self.grant.backup.state is not BackupState.READY or self.grant.backup.target != self.target or self.grant.backup.expected != self.expected or self.grant.backup.checked_at > now:
            return PortResult.failed(PortFailure(FailureKind.AUTHORIZATION, RetryAdvice(False), SafeSummary("write backup readiness invalid")))
        if self.grant.target != self.target or self.grant.expected != self.expected or self.grant.idempotency_key != self.idempotency_key or self.grant.mutation_digest != mutation_digest(self.mutations):
            return PortResult.failed(PortFailure(FailureKind.CONFLICT, RetryAdvice(False), SafeSummary("write bindings changed")))
        return PortResult.success(Unit.VALUE)


@dataclass(frozen=True)
class WriteReceipt:
    idempotency_key: IdempotencyKey
    resulting_fingerprint: Fingerprint
    replayed: bool

    def __post_init__(self) -> None:
        if type(self.idempotency_key) is not IdempotencyKey or type(self.resulting_fingerprint) is not Fingerprint or type(self.replayed) is not bool:
            raise ValueError("write receipts require typed idempotency, fingerprint, and replay state")


class SupportedWriter(Protocol):
    def apply(self, request: WriteRequest) -> PortResult[WriteReceipt]:
        """Call ``preflight_result()`` at mutation time and return any typed failure."""
        ...


@dataclass(frozen=True)
class ExtractorRequest:
    target: TargetId
    snapshot: SnapshotId
    fingerprint: Fingerprint
    media_type: MediaType
    files: tuple[FileReference, ...]
    context: OperationContext

    def __post_init__(self) -> None:
        if type(self.target) is not TargetId or type(self.snapshot) is not SnapshotId or type(self.fingerprint) is not Fingerprint or type(self.media_type) is not MediaType or type(self.context) is not OperationContext:
            raise ValueError("extractor requests require typed bounded snapshot context")
        if type(self.files) is not tuple or not 1 <= len(self.files) <= 32 or not all(type(item) is FileReference for item in self.files):
            raise ValueError("extractor files must be a non-empty bounded immutable tuple")
        self.preflight()

    def preflight(self) -> None:
        size = sum(len(item.mount.encode("utf-8")) + len(item.relative_path.encode("utf-8")) for item in self.files)
        self.context.preflight(item_count=len(self.files), serialized_bytes=size)

    def preflight_result(self) -> PortResult[Unit]:
        size = sum(len(item.mount.encode("utf-8")) + len(item.relative_path.encode("utf-8")) for item in self.files)
        return self.context.preflight_result(item_count=len(self.files), serialized_bytes=size)


class EvidenceExtractor(Protocol):
    def extract(self, request: ExtractorRequest) -> PortResult[EvidenceRecord]: ...


class ProviderFactCode(StrEnum):
    TITLE = "title"
    AUTHOR = "author"
    NARRATOR = "narrator"
    ISBN = "isbn"
    ASIN = "asin"
    PUBLISHED_DATE = "published_date"
    LANGUAGE = "language"


@dataclass(frozen=True)
class ProviderFact:
    code: ProviderFactCode
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.code) is not ProviderFactCode or type(self.values) is not tuple or not 1 <= len(self.values) <= MAX_PROVIDER_VALUES:
            raise ValueError("provider facts require a typed code and bounded immutable values")
        for value in self.values:
            validate_projection_text(value, "provider fact", 512)
        if len(set(self.values)) != len(self.values):
            raise ValueError("provider fact values must be unique")


@dataclass(frozen=True, repr=False)
class ProviderQuery:
    media_type: MediaType
    field: FieldName | None
    facts: tuple[ProviderFact, ...]
    schema: SchemaVersion

    def __post_init__(self) -> None:
        if type(self.media_type) is not MediaType or (self.field is not None and type(self.field) is not FieldName) or type(self.schema) is not SchemaVersion:
            raise ValueError("provider queries require typed canonical scope")
        if type(self.facts) is not tuple or not 1 <= len(self.facts) <= MAX_PROVIDER_FACTS or not all(type(item) is ProviderFact for item in self.facts):
            raise ValueError("provider query facts must be a non-empty bounded immutable tuple")
        if len({fact.code for fact in self.facts}) != len(self.facts):
            raise ValueError("provider queries cannot repeat fact kinds")

    def preflight(self, page: PageRequest, context: OperationContext) -> None:
        if type(page) is not PageRequest or type(context) is not OperationContext:
            raise ValueError("provider preflight requires exact page and context")
        context.preflight(
            item_count=max(page.limit, sum(len(fact.values) for fact in self.facts)),
            serialized_bytes=self.canonical_request_size(page),
        )

    def preflight_result(self, page: PageRequest, context: OperationContext) -> PortResult[Unit]:
        if type(page) is not PageRequest or type(context) is not OperationContext:
            return PortResult.failed(PortFailure(FailureKind.INVALID_REQUEST, RetryAdvice(False), SafeSummary("provider request invalid")))
        return context.preflight_result(
            item_count=max(page.limit, sum(len(fact.values) for fact in self.facts)),
            serialized_bytes=self.canonical_request_size(page),
        )

    def canonical_request_size(self, page: PageRequest) -> int:
        if type(page) is not PageRequest:
            raise ValueError("provider request sizing requires an exact page request")
        projection = {
            "facts": [
                {"code": fact.code.value, "values": list(fact.values)}
                for fact in self.facts
            ],
            "field": None if self.field is None else self.field.value,
            "media_type": self.media_type.value,
            "page": {"cursor": page.cursor, "limit": page.limit},
            "schema": self.schema.value,
        }
        return len(json.dumps(projection, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    def __repr__(self) -> str:
        return "ProviderQuery(media_type=<typed>, field=<typed>, facts=<redacted>, schema=<typed>)"


@dataclass(frozen=True, repr=False)
class ProviderProvenance:
    source_id: str
    correlation_id: str
    fingerprint: Fingerprint

    def __post_init__(self) -> None:
        if type(self.fingerprint) is not Fingerprint:
            raise ValueError("provider provenance requires a typed fingerprint")
        _bounded_text(self.source_id, "provider source ID", 128)
        _bounded_text(self.correlation_id, "provider correlation ID", 128)

    def __repr__(self) -> str:
        return "ProviderProvenance(source=<redacted>, correlation=<redacted>, fingerprint=<redacted>)"


@dataclass(frozen=True)
class MetadataCandidate:
    field: FieldName
    value: FieldValue
    provenance: ProviderProvenance

    def __post_init__(self) -> None:
        if type(self.field) is not FieldName or type(self.value) is not FieldValue or type(self.provenance) is not ProviderProvenance:
            raise ValueError("metadata candidates require exact canonical value and provenance types")


@dataclass(frozen=True)
class CoverCandidate:
    cover: CoverReference
    provenance: ProviderProvenance

    def __post_init__(self) -> None:
        if type(self.cover) is not CoverReference or type(self.provenance) is not ProviderProvenance:
            raise ValueError("cover candidates require exact cover and provenance types")


def provider_response_preflight(
    page: Page[MetadataCandidate] | Page[CoverCandidate],
    request: PageRequest,
    context: OperationContext,
    item_type: type[MetadataCandidate] | type[CoverCandidate],
) -> PortResult[Unit]:
    """Validate canonical provider output before returning adapter success."""

    if type(page) is not Page or type(request) is not PageRequest or type(context) is not OperationContext or item_type not in {MetadataCandidate, CoverCandidate}:
        return PortResult.failed(PortFailure(FailureKind.INVALID_RESPONSE, RetryAdvice(False), SafeSummary("provider response shape invalid")))
    if not all(type(item) is item_type for item in page.items):
        return PortResult.failed(PortFailure(FailureKind.INVALID_RESPONSE, RetryAdvice(False), SafeSummary("provider response item invalid")))
    if len(page.items) > request.limit:
        return PortResult.failed(PortFailure(FailureKind.INVALID_RESPONSE, RetryAdvice(False), SafeSummary("provider response exceeds page limit")))
    size = provider_response_size(page, item_type)
    return context.preflight_result(item_count=len(page.items), serialized_bytes=size)


def provider_response_size(
    page: Page[MetadataCandidate] | Page[CoverCandidate],
    item_type: type[MetadataCandidate] | type[CoverCandidate],
) -> int:
    """Return the deterministic complete canonical provider-page envelope size."""

    if type(page) is not Page or item_type not in {MetadataCandidate, CoverCandidate} or not all(type(item) is item_type for item in page.items):
        raise ValueError("provider response sizing requires one exact canonical page family")
    serialized: list[dict[str, object]] = []
    for item in page.items:
        provenance = item.provenance
        common = {
            "correlation": provenance.correlation_id,
            "provenance_fingerprint": _provider_fingerprint_projection(provenance.fingerprint),
            "source": provenance.source_id,
        }
        if type(item) is MetadataCandidate:
            serialized.append({**common, "kind": "metadata", "field": item.field.value, "values": list(item.value.values)})
        else:
            serialized.append({
                **common,
                "kind": "cover",
                "cover": {
                    "content_fingerprint": None if item.cover.content_fingerprint is None else _provider_fingerprint_projection(item.cover.content_fingerprint),
                    "opaque_reference": item.cover.opaque_reference,
                },
            })
    envelope = {"items": serialized, "next_cursor": page.next_cursor}
    return len(json.dumps(envelope, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _provider_fingerprint_projection(fingerprint: Fingerprint) -> dict[str, str]:
    return {
        "algorithm": fingerprint.algorithm,
        "digest": fingerprint.digest,
        "schema": fingerprint.schema.value,
    }


class MetadataProvider(Protocol):
    def lookup(self, query: ProviderQuery, page: PageRequest, context: OperationContext) -> PortResult[Page[MetadataCandidate]]: ...


class CoverProvider(Protocol):
    def lookup_covers(self, query: ProviderQuery, page: PageRequest, context: OperationContext) -> PortResult[Page[CoverCandidate]]: ...


class ReviewerAction(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    MANUAL = "manual"


class CurrentFieldStateKind(StrEnum):
    LOADED = "loaded"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CurrentFieldState:
    field: FieldName
    state: CurrentFieldStateKind
    value: FieldValue | None = None

    def __post_init__(self) -> None:
        if type(self.field) is not FieldName or type(self.state) is not CurrentFieldStateKind or (self.value is not None and type(self.value) is not FieldValue):
            raise ValueError("current field state requires exact typed field, state, and value")
        if (self.state is CurrentFieldStateKind.LOADED) != (self.value is not None):
            raise ValueError("loaded state requires a value; missing/unavailable states forbid one")


@dataclass(frozen=True)
class ReviewerRequest:
    packet: ReviewerEvidencePacket
    proposals: tuple[FieldProposal, ...]
    current_fields: tuple[CurrentFieldState, ...]
    context: OperationContext

    def __post_init__(self) -> None:
        if type(self.packet) is not ReviewerEvidencePacket or type(self.context) is not OperationContext:
            raise ValueError("reviewer requests require a canonical packet and operation context")
        if type(self.proposals) is not tuple or not 1 <= len(self.proposals) <= MAX_REVIEW_DECISIONS or not all(type(item) is FieldProposal for item in self.proposals):
            raise ValueError("reviewer proposals must be a unique bounded immutable typed tuple")
        if len({item.id for item in self.proposals}) != len(self.proposals):
            raise ValueError("reviewer proposals must be unique")
        if any(item.target != self.packet.target or item.snapshot != self.packet.snapshot for item in self.proposals):
            raise ValueError("reviewer proposal context must exactly match the staged packet")
        if type(self.current_fields) is not tuple or not self.current_fields or not all(type(item) is CurrentFieldState for item in self.current_fields):
            raise ValueError("reviewer requests require exact current field states")
        if len({item.field for item in self.current_fields}) != len(self.current_fields) or {item.field for item in self.current_fields} != {item.field for item in self.proposals}:
            raise ValueError("reviewer current field states must cover every and only proposed fields")
        states = {item.field: item for item in self.current_fields}
        for proposal in self.proposals:
            current = states[proposal.field]
            if current.state is CurrentFieldStateKind.LOADED and proposal.current_value != current.value:
                raise ValueError("reviewer loaded current state must match proposal context")
            if current.state in {CurrentFieldStateKind.MISSING, CurrentFieldStateKind.UNAVAILABLE} and proposal.current_value is not None:
                raise ValueError("reviewer missing or unavailable current state must not carry a proposal current value")
            references = proposal.supporting_evidence + proposal.contradicting_evidence + tuple(reference for risk in proposal.risks for reference in risk.evidence)
            packet_references = {detail.reference for detail in self.packet.details}
            if not set(references).issubset(packet_references):
                raise ValueError("reviewer packet omits required proposal or risk evidence")
        self.preflight()

    def preflight(self) -> None:
        item_count, size = self._resource_usage()
        self.context.preflight(item_count=item_count, serialized_bytes=size)

    def preflight_result(self) -> PortResult[Unit]:
        item_count, size = self._resource_usage()
        return self.context.preflight_result(item_count=item_count, serialized_bytes=size)

    def _resource_usage(self) -> tuple[int, int]:
        proposal_bytes = sum(
            len(value.encode("utf-8"))
            for proposal in self.proposals
            for field_value in (proposal.current_value, proposal.proposed_value)
            if field_value is not None
            for value in field_value.values
        )
        loaded_current_bytes = sum(
            len(value.encode("utf-8"))
            for current in self.current_fields
            if current.state is CurrentFieldStateKind.LOADED and current.value is not None
            for value in current.value.values
        )
        return (
            len(self.packet.details) + len(self.proposals) + len(self.current_fields),
            len(self.packet.to_json().encode("utf-8")) + proposal_bytes + loaded_current_bytes,
        )


@dataclass(frozen=True, repr=False)
class ReviewerDecision:
    proposal: ProposalId
    action: ReviewerAction
    reason: SafeSummary
    cited_evidence: tuple[EvidenceId, ...]
    problems: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.proposal) is not ProposalId or type(self.action) is not ReviewerAction or type(self.reason) is not SafeSummary:
            raise ValueError("reviewer decisions require typed proposal, action, and reason")
        if type(self.cited_evidence) is not tuple or not self.cited_evidence or len(self.cited_evidence) > 64 or not all(type(item) is EvidenceId for item in self.cited_evidence) or len(set(self.cited_evidence)) != len(self.cited_evidence):
            raise ValueError("reviewer decisions require unique bounded cited evidence IDs")
        if type(self.problems) is not tuple or len(self.problems) > 16:
            raise ValueError("reviewer problems must be a bounded immutable tuple")
        for problem in self.problems:
            _bounded_code(problem, "reviewer problem")
        if len(set(self.problems)) != len(self.problems):
            raise ValueError("reviewer problems must be unique")

    def __repr__(self) -> str:
        return "ReviewerDecision(proposal=<typed>, action=<typed>, reason=<redacted>, problems=<typed>)"


@dataclass(frozen=True)
class ReviewerResponse:
    request: ReviewerRequest
    decisions: tuple[ReviewerDecision, ...]

    def __post_init__(self) -> None:
        if type(self.request) is not ReviewerRequest:
            raise ValueError("review responses require the exact reviewed request context")
        if type(self.decisions) is not tuple or not 1 <= len(self.decisions) <= MAX_REVIEW_DECISIONS or not all(type(item) is ReviewerDecision for item in self.decisions):
            raise ValueError("review response decisions must be a non-empty bounded immutable tuple")
        if len({item.proposal for item in self.decisions}) != len(self.decisions):
            raise ValueError("review response cannot repeat proposals")
        expected = {item.id for item in self.request.proposals}
        actual = {item.proposal for item in self.decisions}
        if actual != expected:
            raise ValueError("review response must cover every and only supplied proposal")
        evidence = {detail.reference.evidence for detail in self.request.packet.details}
        if any(not set(item.cited_evidence).issubset(evidence) for item in self.decisions):
            raise ValueError("review response cites unknown evidence")


class Reviewer(Protocol):
    """Receives only a staged packet; it has no secret, discovery, or write methods."""

    def review(self, request: ReviewerRequest) -> PortResult[ReviewerResponse]: ...


class SecretScope(StrEnum):
    TARGET_SERVICE = "target_service"
    METADATA_PROVIDER = "metadata_provider"
    COVER_PROVIDER = "cover_provider"
    REVIEWER = "reviewer"
    NOTIFICATION = "notification"


@dataclass(frozen=True, repr=False)
class SecretHandle:
    """Opaque role-scoped handle; secret material never becomes an application value."""

    reference: SecretReference
    scope: SecretScope

    def __post_init__(self) -> None:
        if type(self.reference) is not SecretReference or type(self.scope) is not SecretScope:
            raise ValueError("secret handles require typed reference and role scope")

    def __repr__(self) -> str:
        return "SecretHandle(reference=<redacted>, scope=<typed>)"


class SecretResolver(Protocol):
    def configured(self, reference: SecretReference, scope: SecretScope) -> bool: ...
    def resolve(self, reference: SecretReference, scope: SecretScope) -> PortResult[SecretHandle]: ...


@dataclass(frozen=True)
class BackupReadinessRequest:
    target: TargetId
    expected: ExpectedState
    context: OperationContext

    def __post_init__(self) -> None:
        if type(self.target) is not TargetId or type(self.expected) is not ExpectedState or type(self.context) is not OperationContext:
            raise ValueError("backup readiness requests require exact target, state, and context values")


class BackupState(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class BackupReadinessReport:
    state: BackupState
    target: TargetId
    expected: ExpectedState
    checked_at: datetime
    summary: SafeSummary

    def __post_init__(self) -> None:
        if type(self.state) is not BackupState or type(self.target) is not TargetId or type(self.expected) is not ExpectedState or type(self.summary) is not SafeSummary:
            raise ValueError("backup reports require exact typed state, target, expectation, and summary")
        _aware_datetime(self.checked_at, "backup readiness time")


class BackupReadiness(Protocol):
    def check(self, request: BackupReadinessRequest) -> PortResult[BackupReadinessReport]: ...


@dataclass(frozen=True)
class Notification:
    kind: str
    target: TargetId
    summary: SafeSummary
    idempotency_key: IdempotencyKey
    context: OperationContext

    def __post_init__(self) -> None:
        _bounded_code(self.kind, "notification kind")
        if type(self.target) is not TargetId or type(self.summary) is not SafeSummary or type(self.idempotency_key) is not IdempotencyKey or type(self.context) is not OperationContext:
            raise ValueError("notifications require typed safe values")
        if self.idempotency_key.target != self.target:
            raise ValueError("targeted notification idempotency must match its target")


class Notifier(Protocol):
    def notify(self, notification: Notification) -> PortResult[Unit]: ...


__all__ = [
    "AdapterDiagnostics", "AuditRepository", "BackupReadiness",
    "BackupReadinessReport", "BackupReadinessRequest", "BackupState", "CancellationId",
    "CancellationProbe", "Capability", "CapabilitySet", "Clock", "CompatibilityReport",
    "CompatibilityStatus", "CoverCandidate", "CoverMutation", "CoverProvider", "CurrentFieldState", "CurrentFieldStateKind", "Deadline", "EvidenceExtractor",
    "ExpectedState", "ExtractorRequest", "FailureKind", "HealthReport", "HealthStatus",
    "IdentifierGenerator", "JobHeartbeatRepository", "LibraryListRequest", "LibraryReadRequest",
    "LibraryReader", "LibraryService", "MetadataCandidate", "MetadataMutation", "MetadataProvider", "Notification",
    "Notifier", "OperationContext", "Page", "PageRequest", "PortFailure", "PortResult",
    "ProviderFact", "ProviderFactCode", "ProviderProvenance", "ProviderQuery", "RepositoryOutcome",
    "RepositoryResult", "ResourceBounds", "RetryAdvice", "Reviewer", "ReviewerAction",
    "ReviewerDecision", "ReviewerRequest", "ReviewerResponse", "SafeSummary", "SecretHandle",
    "SecretResolver", "SecretScope", "SnapshotRepository", "SnapshotStoreRequest",
    "SupportedWriter", "TimeProbe", "Transaction", "TransactionManager", "Unit", "WorkRequest",
    "WorkScheduler", "WriteGrant", "WriteGrantId", "WriteMutation",
    "WriteReceipt", "WriteRequest", "mutation_digest", "mutation_size", "provider_response_preflight", "provider_response_size",
]
