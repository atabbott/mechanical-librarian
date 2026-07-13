"""Generic bounded connection-probe orchestration and public-safe results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.application.ports import (
    FailureKind,
    HealthReport,
    LibraryConnectionDiagnostics,
    LibraryConnectionDiagnosticsRequest,
    LibraryConnectionFacts,
    OperationContext,
    PortResult,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    AdapterVersion,
    CompatibilityVersion,
    ConnectionId,
    LibraryId,
    ServiceVersion,
)


class CapabilityState(StrEnum):
    TESTED = "tested"
    SUPPORTED = "supported"
    EXPERIMENTAL = "experimental"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"


class ConnectionCapability(StrEnum):
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    VERSION_DISCOVERY = "version_discovery"
    INVENTORY_READ = "inventory_read"
    METADATA_READ = "metadata_read"
    COVER_READ = "cover_read"
    MEDIA_FILE_READ = "media_file_read"
    PAGINATION = "pagination"
    INCREMENTAL_SCAN = "incremental_scan"
    METADATA_FIELDS = "metadata_fields"
    COVER_BEHAVIOR = "cover_behavior"
    ROLLBACK_READ = "rollback_read"
    RATE_LIMITS = "rate_limits"
    NOTIFICATIONS = "notifications"
    CONCURRENCY_TOKENS = "concurrency_tokens"
    MUTATION = "mutation"


class CompatibilityState(StrEnum):
    SUPPORTED = "supported"
    OLDER = "older"
    NEWER = "newer"
    UNKNOWN = "unknown"


class ConnectionProbeOutcome(StrEnum):
    READY = "ready"
    CANCELLED = "cancelled"
    DEADLINE_EXPIRED = "deadline_expired"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    AUTHENTICATION_FAILED = "authentication_failed"
    AUTHORIZATION_FAILED = "authorization_failed"
    SECRET_UNAVAILABLE = "secret_unavailable"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"
    INVALID_RESPONSE = "invalid_response"
    CONFIGURATION_FAILURE = "configuration_failure"
    INTERNAL_FAILURE = "internal_failure"


TargetProbeFacts = LibraryConnectionFacts


@dataclass(frozen=True)
class CapabilityObservation:
    capability: ConnectionCapability
    state: CapabilityState

    def __post_init__(self) -> None:
        if (
            type(self.capability) is not ConnectionCapability
            or type(self.state) is not CapabilityState
        ):
            raise ValueError("capability observation is malformed")


@dataclass(frozen=True, repr=False)
class ConnectionInspection:
    adapter: AdapterId
    connection: ConnectionId
    compatibility: CompatibilityState
    service_version: ServiceVersion | None
    capabilities: tuple[CapabilityObservation, ...]
    adapter_contract: AdapterVersion
    fixture_revision: CompatibilityVersion
    health: HealthReport

    def __post_init__(self) -> None:
        if (
            type(self.adapter) is not AdapterId
            or type(self.connection) is not ConnectionId
            or type(self.compatibility) is not CompatibilityState
            or (
                self.service_version is not None
                and type(self.service_version) is not ServiceVersion
            )
            or type(self.capabilities) is not tuple
            or any(type(item) is not CapabilityObservation for item in self.capabilities)
            or tuple(item.capability for item in self.capabilities)
            != tuple(ConnectionCapability)
            or type(self.adapter_contract) is not AdapterVersion
            or type(self.fixture_revision) is not CompatibilityVersion
            or type(self.health) is not HealthReport
        ):
            raise ValueError("connection inspection is malformed")

    def __repr__(self) -> str:
        return "ConnectionInspection(<redacted>)"


@dataclass(frozen=True)
class ConnectionProbeResult:
    outcome: ConnectionProbeOutcome
    inspection: ConnectionInspection | None = None

    def __post_init__(self) -> None:
        if type(self.outcome) is not ConnectionProbeOutcome:
            raise ValueError("connection probe outcome is malformed")
        if self.outcome is ConnectionProbeOutcome.READY:
            if type(self.inspection) is not ConnectionInspection:
                raise ValueError("ready connection probes require an inspection")
        elif self.outcome is ConnectionProbeOutcome.INCOMPATIBLE:
            if (
                self.inspection is not None
                and type(self.inspection) is not ConnectionInspection
            ):
                raise ValueError("incompatible connection probe inspection is malformed")
        elif self.inspection is not None:
            raise ValueError("failed connection probes cannot expose an inspection")

    @property
    def succeeded(self) -> bool:
        return self.outcome is ConnectionProbeOutcome.READY


class InspectLibraryConnection:
    """Probe one configured target without inventory, persistence, or mutation ports."""

    def __init__(
        self,
        adapter: AdapterId,
        connection: ConnectionId,
        expected_libraries: tuple[LibraryId, ...],
        probe: LibraryConnectionDiagnostics,
    ) -> None:
        self._adapter = adapter
        self._connection = connection
        self._expected_libraries = expected_libraries
        self._probe = probe

    def run(self, context: OperationContext) -> ConnectionProbeResult:
        if type(context) is not OperationContext:
            return ConnectionProbeResult(ConnectionProbeOutcome.CONFIGURATION_FAILURE)
        bounded = context.preflight_result(item_count=1, serialized_bytes=0)
        if bounded.failure is not None:
            return ConnectionProbeResult(_failure_outcome(bounded.failure.kind))
        try:
            request = LibraryConnectionDiagnosticsRequest(
                self._expected_libraries, context
            )
        except ValueError:
            return ConnectionProbeResult(ConnectionProbeOutcome.CONFIGURATION_FAILURE)
        observed = self._probe.probe(request)
        boundary = context.preflight_result(item_count=0, serialized_bytes=0)
        if boundary.failure is not None:
            return ConnectionProbeResult(_failure_outcome(boundary.failure.kind))
        if type(observed) is not PortResult:
            return ConnectionProbeResult(ConnectionProbeOutcome.INVALID_RESPONSE)
        if observed.failure is not None:
            return ConnectionProbeResult(_failure_outcome(observed.failure.kind))
        if type(observed.value) is not LibraryConnectionFacts:
            return ConnectionProbeResult(ConnectionProbeOutcome.INVALID_RESPONSE)

        compatibility = _compatibility(observed.value.service_version)
        inspection = ConnectionInspection(
            self._adapter,
            self._connection,
            compatibility,
            observed.value.service_version,
            _capabilities(observed.value, compatibility),
            observed.value.adapter_contract,
            observed.value.fixture_revision,
            observed.value.health,
        )
        if compatibility is not CompatibilityState.SUPPORTED:
            return ConnectionProbeResult(
                ConnectionProbeOutcome.INCOMPATIBLE, inspection
            )
        if not observed.value.profile_complete:
            return ConnectionProbeResult(ConnectionProbeOutcome.AUTHORIZATION_FAILED)
        return ConnectionProbeResult(ConnectionProbeOutcome.READY, inspection)


def _version_tuple(version: ServiceVersion | None) -> tuple[int, int, int] | None:
    if version is None:
        return None
    value = version.value
    parts = value.split(".")
    if (
        len(value) > 32
        or len(parts) != 3
        or any(
            not part.isdigit()
            or (len(part) > 1 and part.startswith("0"))
            or len(part) > 6
            for part in parts
        )
    ):
        return None
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def _compatibility(version: ServiceVersion | None) -> CompatibilityState:
    parsed = _version_tuple(version)
    if parsed is None:
        return CompatibilityState.UNKNOWN
    if parsed == (2, 35, 1):
        return CompatibilityState.SUPPORTED
    if parsed < (2, 35, 1):
        return CompatibilityState.OLDER
    return CompatibilityState.NEWER


def _capabilities(
    facts: TargetProbeFacts, compatibility: CompatibilityState
) -> tuple[CapabilityObservation, ...]:
    if compatibility is not CompatibilityState.SUPPORTED:
        declared = CapabilityState.INCOMPATIBLE
    elif facts.profile_complete:
        declared = CapabilityState.SUPPORTED
    else:
        declared = CapabilityState.UNAVAILABLE
    states = {
        ConnectionCapability.AUTHENTICATION: (
            CapabilityState.TESTED
            if compatibility is CompatibilityState.SUPPORTED
            else CapabilityState.INCOMPATIBLE
        ),
        ConnectionCapability.AUTHORIZATION: (
            CapabilityState.TESTED
            if compatibility is CompatibilityState.SUPPORTED
            else CapabilityState.INCOMPATIBLE
        ),
        ConnectionCapability.VERSION_DISCOVERY: (
            CapabilityState.TESTED
            if facts.service_version is not None
            else CapabilityState.UNAVAILABLE
        ),
        ConnectionCapability.INVENTORY_READ: declared,
        ConnectionCapability.METADATA_READ: declared,
        ConnectionCapability.COVER_READ: declared,
        ConnectionCapability.MEDIA_FILE_READ: (
            declared if facts.media_download_allowed else CapabilityState.UNAVAILABLE
        ),
        ConnectionCapability.PAGINATION: declared,
        ConnectionCapability.INCREMENTAL_SCAN: CapabilityState.UNAVAILABLE,
        ConnectionCapability.METADATA_FIELDS: declared,
        ConnectionCapability.COVER_BEHAVIOR: declared,
        ConnectionCapability.ROLLBACK_READ: CapabilityState.UNAVAILABLE,
        ConnectionCapability.RATE_LIMITS: CapabilityState.UNAVAILABLE,
        ConnectionCapability.NOTIFICATIONS: CapabilityState.UNAVAILABLE,
        ConnectionCapability.CONCURRENCY_TOKENS: CapabilityState.UNAVAILABLE,
        ConnectionCapability.MUTATION: CapabilityState.UNAVAILABLE,
    }
    return tuple(CapabilityObservation(capability, states[capability]) for capability in ConnectionCapability)


def _failure_outcome(kind: FailureKind) -> ConnectionProbeOutcome:
    return {
        FailureKind.CANCELLED: ConnectionProbeOutcome.CANCELLED,
        FailureKind.TIMEOUT: ConnectionProbeOutcome.DEADLINE_EXPIRED,
        FailureKind.RESOURCE_EXHAUSTED: ConnectionProbeOutcome.RESOURCE_EXHAUSTED,
        FailureKind.AUTHENTICATION: ConnectionProbeOutcome.AUTHENTICATION_FAILED,
        FailureKind.AUTHORIZATION: ConnectionProbeOutcome.AUTHORIZATION_FAILED,
        FailureKind.NOT_FOUND: ConnectionProbeOutcome.SECRET_UNAVAILABLE,
        FailureKind.RATE_LIMITED: ConnectionProbeOutcome.RATE_LIMITED,
        FailureKind.INCOMPATIBLE: ConnectionProbeOutcome.INCOMPATIBLE,
        FailureKind.INVALID_RESPONSE: ConnectionProbeOutcome.INVALID_RESPONSE,
    }.get(kind, ConnectionProbeOutcome.UNAVAILABLE)


__all__ = [
    "CapabilityObservation",
    "CapabilityState",
    "CompatibilityState",
    "ConnectionCapability",
    "ConnectionInspection",
    "ConnectionProbeOutcome",
    "ConnectionProbeResult",
    "InspectLibraryConnection",
    "TargetProbeFacts",
]
