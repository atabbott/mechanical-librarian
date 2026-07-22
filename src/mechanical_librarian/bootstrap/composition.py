"""Composition root for executable Mechanical Librarian processes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import signal
import sqlite3
import stat
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

import httpx

from mechanical_librarian.adapters.persistence.audit import SQLiteAuditRepository
from mechanical_librarian.adapters.persistence.jobs import SQLiteJobHeartbeatRepository
from mechanical_librarian.adapters.persistence.inventory import (
    SQLiteInventoryRepository,
)
from mechanical_librarian.adapters.persistence.inventory_v2 import (
    SQLiteInventoryV2Repository,
)
from mechanical_librarian.adapters.persistence.inventory_polling import (
    SQLiteInventoryPollingRepository,
)
from mechanical_librarian.adapters.persistence.epub_opening_evidence import (
    SQLiteEpubOpeningEvidenceRepository,
)
from mechanical_librarian.adapters.persistence.hardcover_identity_work import (
    SQLiteHardcoverIdentityWorkRepository,
)
from mechanical_librarian.adapters.library_services.synthetic import (
    SyntheticLibraryReader,
    synthetic_capabilities,
)
from mechanical_librarian.adapters.library_services.audiobookshelf import (
    AudiobookshelfConnectionProbe,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_inventory import (
    AudiobookshelfInventoryReader,
)
from mechanical_librarian.adapters.evidence_extractors.process import (
    run_epub_inspector_child,
    run_epub_package_child,
)
from mechanical_librarian.adapters.evidence_extractors.synthetic_epub import (
    generated_synthetic_epub,
)
from mechanical_librarian.adapters.persistence.title_proposals import (
    SQLiteTitleProposalRepository,
)
from mechanical_librarian.adapters.persistence.title_review_cases import (
    SQLiteTitleReviewCaseRepository,
)
from mechanical_librarian.adapters.providers.synthetic_title import (
    SyntheticTitleProvider,
)
from mechanical_librarian.adapters.providers.synthetic_hardcover_identity import (
    SyntheticBookIdentityProvider,
)
from mechanical_librarian.adapters.providers.hardcover_identity import (
    HttpxIdentityByteChannel,
    LiveHardcoverIdentityProvider,
    StructuralHardcoverIdentityPreview,
)
from mechanical_librarian.adapters.providers.hardcover_title import (
    AddressResolver,
    HardcoverTitleProvider,
)
from mechanical_librarian.application.connection_probe import (
    ConnectionProbeOutcome,
    ConnectionProbeResult,
    InspectLibraryConnection,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    ConfigurationError,
    HardcoverProviderConfiguration,
)
from mechanical_librarian.application.hardcover_identity import (
    DurableWorkPreviewReport,
    FixedAdapterPreviewReport,
    HardcoverExecutionPolicy,
    HardcoverIdentityOutcome,
    HardcoverIdentityReport,
    IdentityDecisionPreviewReport,
    IdentityLifecyclePreviewReport,
    IdentityAttemptFailureKind,
    IdentityAttemptResult,
    InertPreviewReport,
    IsbnIdentityQuery,
    ManageHardcoverIdentityWork,
    RunHardcoverFixedAdapterPreview,
    RunHardcoverIdentityReport,
    SafeExecutorPreviewReport,
    inert_preview,
)
from mechanical_librarian.application.hardcover_identity_orchestration import (
    RunHardcoverIdentityLifecycle,
)
from mechanical_librarian.application.hardcover_safe_executor import (
    HardcoverIdentityExecutionRequest,
    RunSafeHardcoverIdentity,
    hardcover_identity_work_key,
)
from mechanical_librarian.application.job_heartbeat import (
    HeartbeatOutcome,
    HeartbeatResult,
    RunSyntheticJobHeartbeat,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryConnectionVerification,
    InventoryLibraryResult,
    InventoryMountMapping,
    InventoryOutcome,
    InventoryRunResult,
    ReconcileInventory,
)
from mechanical_librarian.application.epub_opening_evidence import (
    EpubOpeningExecution,
    EpubSourceFingerprintError,
    RunEpubOpeningEvidence,
)
from mechanical_librarian.application.inventory_polling import (
    InventoryPollOutcome,
    InventoryPollResult,
    RunInventoryPoll,
)
from mechanical_librarian.application.state_check import (
    CheckDurableState,
    StateCheckOutcome,
    StateCheckResult,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    OperationContext,
    FailureKind,
    MetadataProvider as _MetadataProvider,
    PortFailure,
    PortResult,
    ResourceBounds,
    RetryAdvice,
    SafeSummary,
    SecretHandle,
    SecretScope,
    TimeProbe,
)
from mechanical_librarian.application.synthetic_inventory import (
    InspectSyntheticInventory,
    SyntheticInventoryOutcome,
    SyntheticInventoryResult,
)
from mechanical_librarian.application.title_proposals import (
    ProviderPreviewProfile,
    ProposeSyntheticTitle,
    SyntheticScenario,
    SyntheticTitleProposalReport,
    scenario_lineage,
    scenario_named,
)
from mechanical_librarian.application.live_title_proposals import (
    LiveTitleProposalReport,
    ProposeLiveTitle,
)
from mechanical_librarian.application.title_review_cases import (
    InspectSyntheticTitleReviewCase,
    SyntheticTitleReviewCaseReport,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    TargetId,
    canonical_json,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceFailure,
    EpubEvidenceLocator,
    EpubEvidenceMode,
    EpubEvidenceResult,
    ResolvedEpubSource,
    epub_governing_digest,
)
from mechanical_librarian.domain.local_evidence import (
    EpubPackageLimits,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    LibraryConnection,
    MediaType,
    SecretReference,
)
from mechanical_librarian.domain.inventory_v2 import ScanBounds, ScanState, ScanUsage
from mechanical_librarian.domain.inventory_polling import PollPolicy, PollRequest
from mechanical_librarian.domain.hardcover_identity import (
    PrivateQueryError,
    ProviderBookObservation,
    ProviderIdentityObservation,
    isbn_identity,
    validate_private_query,
)
from mechanical_librarian.domain.hardcover_identity_work import (
    IdentityMatchOutcome,
    IdentityWorkClaim,
    IdentityWorkObservation,
)
from mechanical_librarian.infrastructure.configuration import (
    load_configuration_file,
    validate_hardcover_provider,
)
from mechanical_librarian.infrastructure.observability.inventory_polling import (
    LoggingInventoryPollTelemetry,
)
from mechanical_librarian.infrastructure.local_media import (
    LocalMediaError,
    descriptor_identity,
    open_local_media,
    sha256_descriptor,
    validate_relative_reference,
)
from mechanical_librarian.infrastructure.security.target_secrets import (
    PrivateFileTargetCredentials,
    SecretFileUnavailable,
    target_endpoint_allowed,
)
from mechanical_librarian.infrastructure.security.provider_secrets import (
    PrivateFileProviderCredentials,
    ProviderSecretUnavailable,
)
from mechanical_librarian.infrastructure.security.hardcover_transport import (
    ContextTransportLogIsolation,
    IsolatedAddressResolverFactory,
)
from mechanical_librarian.infrastructure.security.private_query import (
    read_private_query,
)
from mechanical_librarian.infrastructure.persistence.sqlite import (
    MIGRATIONS,
    SchemaError,
    bound_disposable_state,
    bound_existing_state,
    connect,
    connect_existing,
    finalize_staged_state,
    migrate,
    migrate_to_version,
    path_entry_exists,
    preflight,
    publish_staged_state,
    staged_state,
    validate_current_schema,
)
from mechanical_librarian.adapters.persistence.live_title_evidence import (
    SQLiteLiveTitleEvidenceReader,
)
from mechanical_librarian.infrastructure.hardcover_identity_gate import (
    DescriptorHardcoverIdentityGate,
)
from mechanical_librarian.interfaces.cli import main as cli_main
from urllib.parse import urlsplit


T = TypeVar("T")


def _run_existing_state_check(database: Path) -> StateCheckResult:
    connection = None
    try:
        connection = connect_existing(database)
        migrate(connection)
        return CheckDurableState(SQLiteAuditRepository(connection)).run()
    finally:
        if connection is not None:
            connection.close()


def _prepare_staged_state(database: Path) -> StateCheckResult:
    connection = None
    try:
        connection = connect(database)
        migrate(connection)
        result = CheckDurableState(SQLiteAuditRepository(connection)).run()
        if result.succeeded:
            finalize_staged_state(connection)
        return result
    finally:
        if connection is not None:
            connection.close()


def _run_state_check(database: Path) -> StateCheckResult:
    try:
        preflight(database)
        if path_entry_exists(database):
            # Recheck after the existence observation so a concurrent creator
            # cannot bypass the existing-state validation boundary.
            preflight(database)
            return _run_existing_state_check(database)

        with staged_state(database) as staged:
            result = _prepare_staged_state(staged)
            if not result.succeeded:
                return result
            published = publish_staged_state(staged, database)
        if published:
            return result

        # A concurrent initializer won the no-clobber publication race. Treat
        # its target as ordinary existing state and verify it independently.
        preflight(database)
        return _run_existing_state_check(database)
    except SchemaError:
        return StateCheckResult(StateCheckOutcome.SETUP_FAILURE)


def _run_new_qualification_state_check(
    database: Path,
) -> tuple[StateCheckResult, tuple[int, int] | None]:
    """Publish one new disposable state and retain its exact inode identity."""

    failed = StateCheckResult(StateCheckOutcome.SETUP_FAILURE)
    try:
        with bound_disposable_state(database) as bound, bound.staged_state() as staged:
            connection = staged.connect()
            try:
                migrate(connection)
                result = CheckDurableState(
                    SQLiteAuditRepository(connection)
                ).run()
                if result.succeeded:
                    staged.persist(connection)
            finally:
                connection.close()
            if not result.succeeded:
                return result, None
            identity = staged.identity
            if not bound.publish(staged):
                return failed, None
        return result, identity
    except (OSError, SchemaError):
        return failed, None


def _run_bound_existing_state_check(
    database: Path,
) -> tuple[StateCheckResult, tuple[int, int] | None]:
    """Validate the exact existing inode that the final gate must accept."""

    failed = StateCheckResult(StateCheckOutcome.SETUP_FAILURE)
    connection = None
    try:
        with bound_existing_state(database) as bound:
            connection = bound.connect_existing()
            validate_current_schema(connection)
            result = CheckDurableState(SQLiteAuditRepository(connection)).run()
            connection.close()
            connection = None
            return result, bound.identity if result.succeeded else None
    except (OSError, SchemaError):
        return failed, None
    finally:
        if connection is not None:
            connection.close()


def _run_existing_operation(
    database: Path, operation: Callable[[sqlite3.Connection], T]
) -> T:
    connection = None
    try:
        connection = connect_existing(database)
        migrate(connection)
        return operation(connection)
    finally:
        if connection is not None:
            connection.close()


def _prepare_staged_operation(
    database: Path,
    operation: Callable[[sqlite3.Connection], T],
    succeeded: Callable[[T], bool],
) -> T:
    connection = None
    try:
        connection = connect(database)
        migrate(connection)
        result = operation(connection)
        if succeeded(result):
            finalize_staged_state(connection)
        return result
    finally:
        if connection is not None:
            connection.close()


def _run_job_heartbeat(database: Path) -> HeartbeatResult:
    def operation(connection: sqlite3.Connection) -> HeartbeatResult:
        return RunSyntheticJobHeartbeat(SQLiteJobHeartbeatRepository(connection)).run()

    try:
        preflight(database)
        if path_entry_exists(database):
            preflight(database)
            return _run_existing_operation(database, operation)
        with staged_state(database) as staged:
            result = _prepare_staged_operation(
                staged,
                operation,
                lambda value: value.succeeded,
            )
            if not result.succeeded:
                return result
            published = publish_staged_state(staged, database)
        if published:
            return result
        preflight(database)
        return _run_existing_operation(database, operation)
    except SchemaError:
        return HeartbeatResult(HeartbeatOutcome.FAILED)


def _run_synthetic_inventory(
    configuration_path: Path, database: Path
) -> SyntheticInventoryResult:
    """Validate configuration before constructing any target or durable authority."""

    try:
        configuration = load_configuration_file(configuration_path)
    except ConfigurationError:
        return SyntheticInventoryResult(SyntheticInventoryOutcome.CONFIGURATION_FAILURE)

    try:
        if (
            len(configuration.connections) != 1
            or configuration.connections[0].adapter != "synthetic"
            or configuration.connections[0].connection_id is None
            or len(configuration.connections[0].enabled_libraries) != 1
            or configuration.connections[0].capability_overrides
            not in {(), ("inventory-read",)}
            or configuration.providers
            or configuration.reviewers
            or configuration.notifications
        ):
            return SyntheticInventoryResult(
                SyntheticInventoryOutcome.CONFIGURATION_FAILURE
            )
        configured = configuration.connections[0]
        capabilities = synthetic_capabilities()
        connection = LibraryConnection(
            configured.connection_id,
            AdapterId(configured.adapter),
            configured.endpoint,
            configured.secret,
            capabilities,
        )
        library = LibraryId(configured.enabled_libraries[0])
        reader = SyntheticLibraryReader(connection, library)
    except (TypeError, ValueError):
        return SyntheticInventoryResult(SyntheticInventoryOutcome.CONFIGURATION_FAILURE)

    def operation(state: sqlite3.Connection) -> SyntheticInventoryResult:
        now = datetime.now(timezone.utc)
        context = OperationContext(
            Deadline(now + timedelta(seconds=30)),
            CancellationProbe(CancellationId("synthetic-inventory"), lambda: False),
            ResourceBounds(1, 524_288),
            TimeProbe(lambda: datetime.now(timezone.utc)),
        )
        return InspectSyntheticInventory(reader, SQLiteInventoryRepository(state)).run(
            connection, library, context
        )

    try:
        preflight(database)
        if path_entry_exists(database):
            preflight(database)
            return _run_existing_operation(database, operation)
        with staged_state(database) as staged:
            result = _prepare_staged_operation(
                staged,
                operation,
                lambda value: value.succeeded,
            )
            if not result.succeeded:
                return result
            published = publish_staged_state(staged, database)
        if published:
            return result
        preflight(database)
        return _run_existing_operation(database, operation)
    except SchemaError:
        return SyntheticInventoryResult(SyntheticInventoryOutcome.SETUP_FAILURE)
    except Exception:
        return SyntheticInventoryResult(SyntheticInventoryOutcome.INTERNAL_FAILURE)


def _run_connection_probe(configuration_path: Path) -> ConnectionProbeResult:
    """Compose one isolated target-only probe without durable or mutation authority."""

    try:
        configuration = load_configuration_file(configuration_path)
        if (
            len(configuration.connections) != 1
            or configuration.connections[0].adapter != "audiobookshelf"
            or configuration.connections[0].connection_id is None
        ):
            return ConnectionProbeResult(ConnectionProbeOutcome.CONFIGURATION_FAILURE)
        return _probe_configured_connection(configuration.connections[0])
    except ConfigurationError:
        return ConnectionProbeResult(ConnectionProbeOutcome.CONFIGURATION_FAILURE)
    except Exception:
        return ConnectionProbeResult(ConnectionProbeOutcome.INTERNAL_FAILURE)


def _probe_configured_connection(
    configured,
    *,
    context: OperationContext | None = None,
    response_meter: Callable[[int], bool] | None = None,
    connect_timeout_seconds: int = 3,
    read_timeout_seconds: int = 5,
    max_in_flight_requests: int = 2,
) -> ConnectionProbeResult:
    try:
        if not target_endpoint_allowed(configured.endpoint):
            return ConnectionProbeResult(ConnectionProbeOutcome.CONFIGURATION_FAILURE)
        credentials = PrivateFileTargetCredentials()
        if not credentials.configured(configured.secret):
            return ConnectionProbeResult(ConnectionProbeOutcome.SECRET_UNAVAILABLE)
        handle = SecretHandle(configured.secret, SecretScope.TARGET_SERVICE)

        def client_factory():
            try:
                return PortResult.success(
                    credentials.target_client(
                        handle,
                        configured.endpoint,
                        connect_timeout_seconds=connect_timeout_seconds,
                        read_timeout_seconds=read_timeout_seconds,
                        max_in_flight_requests=max_in_flight_requests,
                    )
                )
            except SecretFileUnavailable:
                return PortResult.failed(
                    PortFailure(
                        FailureKind.NOT_FOUND,
                        RetryAdvice(False),
                        SafeSummary("connection credential unavailable"),
                    )
                )

        probe = AudiobookshelfConnectionProbe(
            configured.endpoint,
            client_factory=client_factory,
            response_meter=response_meter,
        )
        if context is None:
            now = datetime.now(timezone.utc)
            context = OperationContext(
                Deadline(now + timedelta(seconds=30)),
                CancellationProbe(CancellationId("connection-check"), lambda: False),
                ResourceBounds(1, 65_536),
                TimeProbe(lambda: datetime.now(timezone.utc)),
            )
        return InspectLibraryConnection(
            AdapterId(configured.adapter),
            configured.connection_id,
            tuple(LibraryId(value) for value in configured.enabled_libraries),
            probe,
        ).run(context)
    except ConfigurationError:
        return ConnectionProbeResult(ConnectionProbeOutcome.CONFIGURATION_FAILURE)
    except Exception:
        return ConnectionProbeResult(ConnectionProbeOutcome.INTERNAL_FAILURE)


def _inventory_connection(configuration_path: Path):
    configuration = load_configuration_file(configuration_path)
    if (
        len(configuration.connections) != 1
        or configuration.connections[0].adapter != "audiobookshelf"
        or configuration.connections[0].connection_id is None
        or configuration.connections[0].capability_overrides
        or urlsplit(configuration.connections[0].endpoint.value).path not in {"", "/"}
        or not target_endpoint_allowed(configuration.connections[0].endpoint)
    ):
        raise ConfigurationError("inventory configuration is invalid")
    configured = configuration.connections[0]
    connection = LibraryConnection(
        configured.connection_id,
        AdapterId(configured.adapter),
        configured.endpoint,
        configured.secret,
        AdapterCapabilities(
            inventory_read=True,
            metadata_read=True,
            media_file_read=False,
            notifications=False,
            pagination=True,
            concurrency_tokens=False,
            cover_read=True,
            writable_fields=frozenset(),
            cover_write=False,
            supported_media_types=frozenset({MediaType.AUDIOBOOK, MediaType.EBOOK}),
        ),
    )
    libraries = tuple(LibraryId(value) for value in configured.enabled_libraries)
    bounds = ScanBounds(
        configuration.inventory.page_size,
        configuration.inventory.max_pages,
        configuration.inventory.max_items,
        configuration.inventory.max_response_bytes,
        configuration.inventory.timeout_seconds,
        configuration.inventory.stability_delay_seconds,
    )
    return configuration, configured, connection, libraries, bounds


def _mount_configuration_digest(mount) -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "container_path": mount.container_path,
                "library_type": mount.library_type,
                "name": mount.name,
                "read_only": mount.read_only,
                "schema": "media-mount-configuration-v1",
                "service_path": mount.service_path,
            }
        ).encode("utf-8")
    ).hexdigest()


def _inventory_mount_mappings(configuration) -> tuple[InventoryMountMapping, ...]:
    return tuple(
        InventoryMountMapping(
            mount.name,
            mount.library_type,
            mount.service_path.rstrip("/") or "/",
            _mount_configuration_digest(mount),
        )
        for mount in getattr(configuration, "media_mounts", ())
        if mount.library_type == "ebook" and mount.read_only
    )


def _inventory_context(bounds: ScanBounds, cancellation: str) -> OperationContext:
    now = datetime.now(timezone.utc)
    return OperationContext(
        Deadline(now + timedelta(seconds=bounds.max_seconds)),
        CancellationProbe(CancellationId(cancellation), lambda: False),
        ResourceBounds(bounds.max_items, min(bounds.max_bytes, 67_108_864)),
        TimeProbe(lambda: datetime.now(timezone.utc)),
    )


def _inventory_poll_context(
    bounds: ScanBounds,
    polling: SQLiteInventoryPollingRepository,
    claim,
) -> OperationContext:
    now = datetime.now(timezone.utc)
    return OperationContext(
        Deadline(min(now + timedelta(seconds=bounds.max_seconds), claim.lease_expires_at)),
        CancellationProbe(
            CancellationId("inventory-poll"),
            lambda: not polling.claim_is_live(claim, datetime.now(timezone.utc)),
        ),
        ResourceBounds(bounds.max_items, min(bounds.max_bytes, 67_108_864)),
        TimeProbe(lambda: datetime.now(timezone.utc)),
    )


def _inventory_probe_failure(probe: ConnectionProbeResult) -> PortFailure | None:
    if probe.outcome is ConnectionProbeOutcome.READY:
        return None
    if probe.failure is not None:
        return probe.failure
    kind = {
        ConnectionProbeOutcome.CANCELLED: FailureKind.CANCELLED,
        ConnectionProbeOutcome.DEADLINE_EXPIRED: FailureKind.TIMEOUT,
        ConnectionProbeOutcome.RESOURCE_EXHAUSTED: FailureKind.RESOURCE_EXHAUSTED,
        ConnectionProbeOutcome.AUTHENTICATION_FAILED: FailureKind.AUTHENTICATION,
        ConnectionProbeOutcome.AUTHORIZATION_FAILED: FailureKind.AUTHORIZATION,
        ConnectionProbeOutcome.SECRET_UNAVAILABLE: FailureKind.NOT_FOUND,
        ConnectionProbeOutcome.RATE_LIMITED: FailureKind.RATE_LIMITED,
        ConnectionProbeOutcome.UNAVAILABLE: FailureKind.UNAVAILABLE,
        ConnectionProbeOutcome.INCOMPATIBLE: FailureKind.INCOMPATIBLE,
        ConnectionProbeOutcome.INVALID_RESPONSE: FailureKind.INVALID_RESPONSE,
        ConnectionProbeOutcome.CONFIGURATION_FAILURE: FailureKind.INVALID_REQUEST,
        ConnectionProbeOutcome.INTERNAL_FAILURE: FailureKind.UNAVAILABLE,
    }[probe.outcome]
    return PortFailure(
        kind,
        RetryAdvice(
            kind
            in {
                FailureKind.UNAVAILABLE,
                FailureKind.RATE_LIMITED,
                FailureKind.TIMEOUT,
            }
        ),
        SafeSummary("inventory connection verification failed safely"),
    )


def _inventory_probe_compatibility(outcome: ConnectionProbeOutcome) -> str:
    if outcome is ConnectionProbeOutcome.READY:
        return "supported"
    if outcome is ConnectionProbeOutcome.INCOMPATIBLE:
        return "incompatible"
    if outcome is ConnectionProbeOutcome.INVALID_RESPONSE:
        return "contract_drift"
    return "unknown"


def _run_audiobookshelf_inventory(
    configuration_path: Path, database: Path
) -> InventoryRunResult:
    """Execute one durable, compatibility-gated GET-only inventory scan."""

    try:
        configuration, configured, connection, libraries, bounds = _inventory_connection(
            configuration_path
        )
        credentials = PrivateFileTargetCredentials()
        handle = SecretHandle(configured.secret, SecretScope.TARGET_SERVICE)

        def client_factory():
            try:
                return PortResult.success(
                    credentials.inventory_client(
                        handle,
                        configured.endpoint,
                        connect_timeout_seconds=configuration.inventory.connect_timeout_seconds,
                        read_timeout_seconds=configuration.inventory.read_timeout_seconds,
                        max_in_flight_requests=configuration.inventory.max_in_flight_requests,
                    )
                )
            except SecretFileUnavailable:
                return PortResult.failed(
                    PortFailure(
                        FailureKind.NOT_FOUND,
                        RetryAdvice(False),
                        SafeSummary("inventory credential unavailable"),
                    )
                )

        def verify_connection(
            _connection: LibraryConnection,
            _libraries: tuple[LibraryId, ...],
            _bounds: ScanBounds,
            context: OperationContext,
        ) -> InventoryConnectionVerification:
            response_bytes = 0

            def meter(amount: int) -> bool:
                nonlocal response_bytes
                response_bytes += amount
                return response_bytes <= bounds.max_bytes

            probe = _probe_configured_connection(
                configured,
                context=context,
                response_meter=meter,
                connect_timeout_seconds=configuration.inventory.connect_timeout_seconds,
                read_timeout_seconds=configuration.inventory.read_timeout_seconds,
                max_in_flight_requests=configuration.inventory.max_in_flight_requests,
            )
            return InventoryConnectionVerification(
                ScanUsage(response_bytes=response_bytes),
                _inventory_probe_failure(probe),
                _inventory_probe_compatibility(probe.outcome),
            )

        source = AudiobookshelfInventoryReader(
            client_factory=client_factory,
            connection_verifier=verify_connection,
            mount_mappings=_inventory_mount_mappings(configuration),
        )

        def operation(state: sqlite3.Connection) -> InventoryRunResult:
            return ReconcileInventory(source, SQLiteInventoryV2Repository(state)).run(
                connection,
                libraries,
                bounds,
                _inventory_context(bounds, "inventory-scan"),
            )

        preflight(database)
        if not path_entry_exists(database):
            with staged_state(database) as staged:
                _prepare_staged_operation(
                    staged,
                    lambda _state: True,
                    lambda value: value,
                )
                publish_staged_state(staged, database)
            preflight(database)
        return _run_existing_operation(database, operation)
    except (ConfigurationError, SchemaError, ValueError):
        return InventoryRunResult(InventoryOutcome.INVALID_REQUEST, ())
    except Exception:
        return InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())


def _run_audiobookshelf_poll(
    configuration_path: Path, database: Path, trigger_id: str
) -> InventoryPollResult:
    """Persist one poll trigger, then run at most one eligible GET-only attempt."""

    try:
        configuration, configured, connection, libraries, bounds = (
            _inventory_connection(configuration_path)
        )
        scope_digest = hashlib.sha256(
            canonical_json(
                {
                    "adapter": connection.adapter.value,
                    "connection": connection.id.value,
                    "libraries": sorted(value.value for value in libraries),
                }
            ).encode("utf-8")
        ).hexdigest()
        governing_digest = hashlib.sha256(
            canonical_json(
                {
                    "contract": "abs-inventory-v1",
                    "generation": configuration.inventory.poll_generation,
                    "page_size": bounds.page_size,
                    "max_pages": bounds.max_pages,
                    "max_items": bounds.max_items,
                    "max_bytes": bounds.max_bytes,
                    "timeout_seconds": bounds.max_seconds,
                    "stability_delay_seconds": bounds.stability_delay_seconds,
                    "max_attempts": configuration.inventory.max_attempts,
                    "max_elapsed_seconds": configuration.inventory.max_elapsed_seconds,
                    "initial_backoff_seconds": configuration.inventory.initial_backoff_seconds,
                    "max_backoff_seconds": configuration.inventory.max_backoff_seconds,
                    "connect_timeout_seconds": configuration.inventory.connect_timeout_seconds,
                    "read_timeout_seconds": configuration.inventory.read_timeout_seconds,
                    "max_in_flight_requests": configuration.inventory.max_in_flight_requests,
                }
            ).encode("utf-8")
        ).hexdigest()
        poll_digest = hashlib.sha256(
            f"{trigger_id}:{governing_digest}".encode("utf-8")
        ).hexdigest()
        multiplier = configuration.inventory.max_attempts * len(libraries)
        request = PollRequest(
            "poll_" + poll_digest[:32],
            trigger_id,
            scope_digest,
            governing_digest,
            PollPolicy(
                configuration.inventory.max_attempts,
                configuration.inventory.max_elapsed_seconds,
                min(bounds.max_seconds, 3_600),
                configuration.inventory.initial_backoff_seconds,
                configuration.inventory.max_backoff_seconds,
                bounds.max_pages * multiplier,
                bounds.max_items * multiplier,
                bounds.max_bytes * multiplier,
            ),
        )
        credentials = PrivateFileTargetCredentials()
        handle = SecretHandle(configured.secret, SecretScope.TARGET_SERVICE)

        def operation(state: sqlite3.Connection) -> InventoryPollResult:
            def client_factory():
                try:
                    return PortResult.success(
                        credentials.inventory_client(
                            handle,
                            configured.endpoint,
                            connect_timeout_seconds=configuration.inventory.connect_timeout_seconds,
                            read_timeout_seconds=configuration.inventory.read_timeout_seconds,
                            max_in_flight_requests=configuration.inventory.max_in_flight_requests,
                        )
                    )
                except SecretFileUnavailable:
                    return PortResult.failed(
                        PortFailure(
                            FailureKind.NOT_FOUND,
                            RetryAdvice(False),
                            SafeSummary("inventory credential unavailable"),
                        )
                    )

            def verify_connection(
                _connection: LibraryConnection,
                _libraries: tuple[LibraryId, ...],
                _bounds: ScanBounds,
                context: OperationContext,
            ) -> InventoryConnectionVerification:
                response_bytes = 0

                def meter(amount: int) -> bool:
                    nonlocal response_bytes
                    response_bytes += amount
                    return response_bytes <= bounds.max_bytes

                probe = _probe_configured_connection(
                    configured,
                    context=context,
                    response_meter=meter,
                    connect_timeout_seconds=configuration.inventory.connect_timeout_seconds,
                    read_timeout_seconds=configuration.inventory.read_timeout_seconds,
                    max_in_flight_requests=configuration.inventory.max_in_flight_requests,
                )
                return InventoryConnectionVerification(
                    ScanUsage(response_bytes=response_bytes),
                    _inventory_probe_failure(probe),
                    _inventory_probe_compatibility(probe.outcome),
                )

            source = AudiobookshelfInventoryReader(
                client_factory=client_factory,
                connection_verifier=verify_connection,
                mount_mappings=_inventory_mount_mappings(configuration),
            )
            polling = SQLiteInventoryPollingRepository(state)
            return RunInventoryPoll(
                polling,
                telemetry=LoggingInventoryPollTelemetry(),
                adapter=connection.adapter,
            ).run(
                request,
                "inventory_worker",
                datetime.now(timezone.utc),
                lambda claim: ReconcileInventory(
                    source, SQLiteInventoryV2Repository(state)
                ).run(
                    connection,
                    libraries,
                    bounds,
                    _inventory_poll_context(bounds, polling, claim),
                    claim=claim,
                ),
                lambda: datetime.now(timezone.utc),
            )

        preflight(database)
        if not path_entry_exists(database):
            with staged_state(database) as staged:
                _prepare_staged_operation(staged, lambda _state: True, lambda value: value)
                publish_staged_state(staged, database)
            preflight(database)
        return _run_existing_operation(database, operation)
    except (ConfigurationError, SchemaError, ValueError):
        return InventoryPollResult(InventoryPollOutcome.INVALID, None)
    except Exception:
        return InventoryPollResult(InventoryPollOutcome.FAILED, None)


def _inspect_audiobookshelf_poll(database: Path, poll_id: str) -> InventoryPollResult:
    """Inspect poll evidence without configuration, credential, or network access."""

    try:
        def operation(state: sqlite3.Connection) -> InventoryPollResult:
            inspected = SQLiteInventoryPollingRepository(state).inspect(poll_id)
            if not inspected.accepted or inspected.value is None:
                return InventoryPollResult(InventoryPollOutcome.FAILED, None)
            poll_state = inspected.value.state
            outcome = (
                InventoryPollOutcome.SUCCEEDED
                if poll_state.value == "succeeded"
                else InventoryPollOutcome.BLOCKED
                if poll_state.value == "blocked"
                else InventoryPollOutcome.DEFERRED
                if poll_state.value in {"pending", "running", "retry_wait"}
                else InventoryPollOutcome.FAILED
            )
            return InventoryPollResult(outcome, inspected.value)

        preflight(database)
        return _run_existing_operation(database, operation)
    except Exception:
        return InventoryPollResult(InventoryPollOutcome.FAILED, None)


def _inspect_audiobookshelf_inventory(
    configuration_path: Path, database: Path
) -> InventoryRunResult:
    """Inspect durable scan state without resolving a credential or using the network."""

    try:
        configuration, _, connection, libraries, bounds = _inventory_connection(
            configuration_path
        )

        def operation(state: sqlite3.Connection) -> InventoryRunResult:
            repository = SQLiteInventoryV2Repository(
                state,
                binding_configuration_digests=frozenset(
                    mapping.configuration_digest
                    for mapping in _inventory_mount_mappings(configuration)
                ),
            )
            results = []
            context = _inventory_context(bounds, "inventory-inspect")
            for library in libraries:
                inspected = repository.inspect(connection, library, context)
                if not inspected.accepted or inspected.value is None:
                    return InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())
                results.append(
                    InventoryLibraryResult(
                        library,
                        _inventory_inspection_outcome(inspected.value.state),
                        inspected.value,
                    )
                )
            outcomes = tuple(result.outcome for result in results)
            outcome = (
                InventoryOutcome.READY
                if all(value is InventoryOutcome.READY for value in outcomes)
                else InventoryOutcome.UNSTABLE
                if any(value is InventoryOutcome.UNSTABLE for value in outcomes)
                else InventoryOutcome.INCOMPLETE
            )
            return InventoryRunResult(outcome, tuple(results))

        preflight(database)
        return _run_existing_operation(database, operation)
    except (ConfigurationError, SchemaError, ValueError):
        return InventoryRunResult(InventoryOutcome.INVALID_REQUEST, ())
    except Exception:
        return InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())


def _inventory_inspection_outcome(state: ScanState) -> InventoryOutcome:
    return {
        ScanState.RUNNING: InventoryOutcome.INCOMPLETE,
        ScanState.COMPLETE: InventoryOutcome.READY,
        ScanState.INCOMPLETE: InventoryOutcome.INCOMPLETE,
        ScanState.UNSTABLE: InventoryOutcome.UNSTABLE,
    }[state]


def _local_evidence_failure(
    outcome: LocalEvidenceOutcome,
    limits: EpubPackageLimits | None = None,
) -> LocalEvidenceReport:
    return LocalEvidenceReport(
        outcome,
        LocalEvidenceCapability.PACKAGE,
        EpubPackageLimits() if limits is None else limits,
    )


def _run_epub_package_report(
    configuration_path: Path, mount_name: str, relative_reference: str
) -> LocalEvidenceReport:
    """Validate one logical reference and extract it through a transient child."""

    try:
        configuration = load_configuration_file(configuration_path)
    except ConfigurationError:
        return _local_evidence_failure(LocalEvidenceOutcome.INVALID_CONFIGURATION)
    limits = replace(
        configuration.evidence.epub_package,
        source_archive_bytes=min(
            configuration.evidence.epub_package.source_archive_bytes,
            configuration.evidence.maximum_document_bytes,
        ),
    )
    if "epub" not in configuration.evidence.enabled_formats:
        return _local_evidence_failure(LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE, limits)
    try:
        validate_relative_reference(relative_reference)
    except LocalMediaError:
        return _local_evidence_failure(LocalEvidenceOutcome.INVALID_REFERENCE, limits)
    mounts = tuple(mount for mount in configuration.media_mounts if mount.name == mount_name)
    if len(mounts) != 1 or not mounts[0].read_only:
        return _local_evidence_failure(LocalEvidenceOutcome.INVALID_REFERENCE, limits)
    opened = None
    replacement = None
    baseline_established = False
    try:
        opened = open_local_media(mounts[0].container_path, relative_reference)
        if opened.length > limits.source_archive_bytes:
            return _local_evidence_failure(LocalEvidenceOutcome.RESOURCE_LIMIT, limits)
        before_identity = opened.identity
        before_digest = sha256_descriptor(
            opened.descriptor, opened.length, maximum=limits.source_archive_bytes
        )
        baseline_established = True
        expected = Fingerprint("sha256", SchemaVersion("source-v1"), before_digest)
        logical_identity = hashlib.sha256(
            f"{mount_name}\x00{relative_reference}".encode("utf-8")
        ).hexdigest()
        report = run_epub_package_child(
            opened.descriptor,
            expected,
            limits,
            logical_identity=logical_identity,
            wall_seconds=min(configuration.limits.parser_timeout_seconds, 60),
        )
        after_digest = sha256_descriptor(
            opened.descriptor, opened.length, maximum=limits.source_archive_bytes
        )
        after_identity = descriptor_identity(opened.descriptor)
        replacement = open_local_media(mounts[0].container_path, relative_reference)
        replacement_digest = sha256_descriptor(
            replacement.descriptor,
            replacement.length,
            maximum=limits.source_archive_bytes,
        )
        if (
            before_identity != after_identity
            or replacement.identity != before_identity
            or before_digest != after_digest
            or replacement_digest != before_digest
        ):
            return _local_evidence_failure(LocalEvidenceOutcome.UNSTABLE_SOURCE, limits)
        return report
    except LocalMediaError:
        return _local_evidence_failure(
            LocalEvidenceOutcome.UNSTABLE_SOURCE
            if baseline_established
            else LocalEvidenceOutcome.UNAVAILABLE_SOURCE,
            limits,
        )
    except Exception:
        return _local_evidence_failure(LocalEvidenceOutcome.INTERNAL_FAILURE, limits)
    finally:
        for value in (opened, replacement):
            if value is not None:
                try:
                    os.close(value.descriptor)
                except OSError:
                    pass


def _opening_failure(
    outcome: LocalEvidenceOutcome,
    limits: EpubOpeningLimits | None = None,
) -> EpubOpeningReport:
    return EpubOpeningReport(
        outcome,
        EpubOpeningLimits() if limits is None else limits,
    )


def _run_epub_opening_report(
    configuration_path: Path,
    mount_name: str,
    relative_reference: str,
    *,
    frozen_configuration=None,
) -> EpubOpeningReport:
    """Extract one source-stable opening report without persistence or paths."""

    execution = _run_epub_opening_execution(
        configuration_path,
        mount_name,
        relative_reference,
        frozen_configuration=frozen_configuration,
    )
    return execution.report if type(execution) is EpubOpeningExecution else execution


def _run_epub_opening_execution(
    configuration_path: Path,
    mount_name: str,
    relative_reference: str,
    *,
    frozen_configuration=None,
) -> EpubOpeningExecution | EpubOpeningReport:
    """Pair a stable internal report with independently captured provenance."""

    if frozen_configuration is None:
        try:
            configuration = load_configuration_file(configuration_path)
        except ConfigurationError:
            return _opening_failure(LocalEvidenceOutcome.INVALID_CONFIGURATION)
    else:
        configuration = frozen_configuration
    package_limits = replace(
        configuration.evidence.epub_package,
        source_archive_bytes=min(
            configuration.evidence.epub_package.source_archive_bytes,
            configuration.evidence.maximum_document_bytes,
        ),
    )
    limits = replace(
        configuration.evidence.epub_opening,
        package=package_limits,
        text_characters_per_selection=min(
            configuration.evidence.epub_opening.text_characters_per_selection,
            configuration.evidence.maximum_text_characters,
        ),
    )
    if "epub" not in configuration.evidence.enabled_formats:
        return _opening_failure(LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE, limits)
    try:
        validate_relative_reference(relative_reference)
    except LocalMediaError:
        return _opening_failure(LocalEvidenceOutcome.INVALID_REFERENCE, limits)
    mounts = tuple(
        mount for mount in configuration.media_mounts if mount.name == mount_name
    )
    if len(mounts) != 1 or not mounts[0].read_only:
        return _opening_failure(LocalEvidenceOutcome.INVALID_REFERENCE, limits)
    opened = replacement = None
    baseline_established = False
    try:
        opened = open_local_media(mounts[0].container_path, relative_reference)
        if opened.length > package_limits.source_archive_bytes:
            return _opening_failure(LocalEvidenceOutcome.RESOURCE_LIMIT, limits)
        before_identity = opened.identity
        before_digest = sha256_descriptor(
            opened.descriptor,
            opened.length,
            maximum=package_limits.source_archive_bytes,
        )
        baseline_established = True
        expected = Fingerprint("sha256", SchemaVersion("source-v1"), before_digest)
        logical_identity = hashlib.sha256(
            f"{mount_name}\x00{relative_reference}".encode("utf-8")
        ).hexdigest()
        report = run_epub_inspector_child(
            opened.descriptor,
            expected,
            limits,
            logical_identity=logical_identity,
        )
        after_digest = sha256_descriptor(
            opened.descriptor,
            opened.length,
            maximum=package_limits.source_archive_bytes,
        )
        after_identity = descriptor_identity(opened.descriptor)
        replacement = open_local_media(mounts[0].container_path, relative_reference)
        replacement_digest = sha256_descriptor(
            replacement.descriptor,
            replacement.length,
            maximum=package_limits.source_archive_bytes,
        )
        if (
            before_identity != after_identity
            or replacement.identity != before_identity
            or before_digest != after_digest
            or replacement_digest != before_digest
        ):
            return _opening_failure(LocalEvidenceOutcome.UNSTABLE_SOURCE, limits)
        return EpubOpeningExecution(report, before_digest)
    except LocalMediaError:
        return _opening_failure(
            LocalEvidenceOutcome.UNSTABLE_SOURCE
            if baseline_established
            else LocalEvidenceOutcome.UNAVAILABLE_SOURCE,
            limits,
        )
    except Exception:
        return _opening_failure(LocalEvidenceOutcome.INTERNAL_FAILURE, limits)
    finally:
        for value in (opened, replacement):
            if value is not None:
                try:
                    os.close(value.descriptor)
                except OSError:
                    pass


def _run_epub_opening_evidence(
    configuration_path: Path,
    database: Path,
    target_reference: str,
    snapshot_reference: str,
    file_index: int,
) -> EpubEvidenceResult:
    """Resolve one opaque selector and execute or reuse its durable evidence."""

    failed = lambda value: EpubEvidenceResult(EpubEvidenceMode.FAILED, failure=value)
    try:
        configuration = load_configuration_file(configuration_path)
        locator = (
            None
            if file_index == -1 and not target_reference and not snapshot_reference
            else EpubEvidenceLocator(target_reference, snapshot_reference, file_index)
        )
        mappings = _inventory_mount_mappings(configuration)
        if not mappings or "epub" not in configuration.evidence.enabled_formats:
            return failed(EpubEvidenceFailure.INVALID_CONFIGURATION)
        package_limits = replace(
            configuration.evidence.epub_package,
            source_archive_bytes=min(
                configuration.evidence.epub_package.source_archive_bytes,
                configuration.evidence.maximum_document_bytes,
            ),
        )
        limits = replace(
            configuration.evidence.epub_opening,
            package=package_limits,
            text_characters_per_selection=min(
                configuration.evidence.epub_opening.text_characters_per_selection,
                configuration.evidence.maximum_text_characters,
            ),
        )
        governing = epub_governing_digest(
            limits, _epub_material_configuration(configuration, mappings)
        )

        def operation(state: sqlite3.Connection) -> EpubEvidenceResult:
            repository = SQLiteEpubOpeningEvidenceRepository(state)
            selected_mapping = None
            if locator is not None:
                resolutions = tuple(
                    (mapping, repository.resolve(locator, mapping.configuration_digest))
                    for mapping in mappings
                )
                resolved = tuple(
                    (mapping, value)
                    for mapping, resolution in resolutions
                    if (value := resolution.source) is not None
                )
                if len(resolved) > 1:
                    return failed(EpubEvidenceFailure.AMBIGUOUS_REFERENCE)
                if not resolved:
                    failures = tuple(resolution.failure for _, resolution in resolutions)
                    return failed(next((value for value in failures if value is not None), EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE))
                selected_mapping = resolved[0][0]

            def mount_for(value):
                candidates = tuple(
                    mount
                    for mount in configuration.media_mounts
                    if mount.name == value.mount_name
                    and _mount_configuration_digest(mount)
                    == value.mount_configuration_digest
                )
                if len(candidates) != 1:
                    raise ValueError("resolved EPUB mount changed")
                return candidates[0]

            def fingerprint(value) -> str:
                mount = mount_for(value)
                opened = open_local_media(mount.container_path, value.relative_path)
                try:
                    if opened.length > package_limits.source_archive_bytes:
                        raise EpubSourceFingerprintError(
                            EpubEvidenceFailure.RESOURCE_LIMIT
                        )
                    return sha256_descriptor(
                        opened.descriptor,
                        opened.length,
                        maximum=package_limits.source_archive_bytes,
                    )
                finally:
                    os.close(opened.descriptor)

            runner = RunEpubOpeningEvidence(repository)
            common = (
                governing,
                "epub_evidence_worker",
                lambda: datetime.now(timezone.utc),
                fingerprint,
                lambda value: _run_epub_opening_execution(
                    configuration_path,
                    value.mount_name,
                    value.relative_path,
                    frozen_configuration=configuration,
                ),
                lambda value, expected: fingerprint(value) == expected,
                timedelta(
                    seconds=min(
                        3_700,
                        limits.wall_seconds + limits.termination_grace_seconds + 10,
                    )
                ),
                configuration.limits.parser_concurrency,
            )
            if locator is None:
                return runner.resume(
                    frozenset(mapping.configuration_digest for mapping in mappings),
                    *common,
                ) or failed(EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE)
            return runner.run(
                locator,
                selected_mapping.configuration_digest,
                *common,
            )

        preflight(database)
        if not path_entry_exists(database):
            return failed(EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE)
        return _run_existing_operation(database, operation)
    except (ConfigurationError, LocalMediaError, SchemaError, ValueError):
        return failed(EpubEvidenceFailure.INVALID_CONFIGURATION)
    except Exception:
        return failed(EpubEvidenceFailure.INTERNAL_FAILURE)


def _epub_material_configuration(configuration, mappings) -> dict[str, object]:
    """Return extraction-semantic configuration, excluding worker capacity."""

    return {
        "mounts": sorted(mapping.configuration_digest for mapping in mappings),
        "parser_timeout_seconds": configuration.limits.parser_timeout_seconds,
        "temporary_bytes": configuration.limits.temporary_bytes,
    }


def _run_live_title_proposal_report(
    configuration_path: Path,
    database: Path,
    target_reference: str,
    snapshot_reference: str,
    file_index: int,
) -> LiveTitleProposalReport:
    """Publish one current live proposal without provider or target egress."""

    configuration = load_configuration_file(configuration_path)
    mappings = _inventory_mount_mappings(configuration)
    mount_digests = tuple(
        sorted(mapping.configuration_digest for mapping in mappings)
    )
    if not mount_digests:
        raise ConfigurationError("live title proposal requires a read-only ebook mount")
    locator = EpubEvidenceLocator(target_reference, snapshot_reference, file_index)
    preflight(database)
    if not path_entry_exists(database):
        raise SchemaError("live title proposal state is unavailable")
    package_limits = replace(
        configuration.evidence.epub_package,
        source_archive_bytes=min(
            configuration.evidence.epub_package.source_archive_bytes,
            configuration.evidence.maximum_document_bytes,
        ),
    )
    limits = replace(
        configuration.evidence.epub_opening,
        package=package_limits,
        text_characters_per_selection=min(
            configuration.evidence.epub_opening.text_characters_per_selection,
            configuration.evidence.maximum_text_characters,
        ),
    )
    governing = epub_governing_digest(
        limits, _epub_material_configuration(configuration, mappings)
    )
    provider = configuration.hardcover_provider
    connection = None
    try:
        connection = connect_existing(database)
        migrate(connection)
        epub_repository = SQLiteEpubOpeningEvidenceRepository(connection)
        evidence_reader = SQLiteLiveTitleEvidenceReader(
            connection,
            epub_repository,
            epub_repository,
            SQLiteHardcoverIdentityWorkRepository(connection),
            lambda source: _fingerprint_live_epub_source(configuration, source),
            governing,
            lambda target: _live_title_source_is_configured(
                configuration_path,
                configuration,
                target,
                mount_digests,
                governing,
            ),
        )
        repository = SQLiteTitleProposalRepository(
            connection,
            source_reader=evidence_reader,
            provider_reader=evidence_reader,
            publication_clock=lambda: datetime.now(timezone.utc),
            provider_configuration_is_current=lambda: (
                _live_title_provider_is_configured(
                    configuration_path, provider
                )
            ),
        )
        return ProposeLiveTitle(repository).run(
            locator,
            mount_digests,
            provider_enabled=provider.enabled,
            authority_revision=provider.authority_revision,
            observed_at=datetime.now(timezone.utc),
        )
    finally:
        if connection is not None:
            connection.close()


def _fingerprint_live_epub_source(
    configuration: ApplicationConfiguration, source: ResolvedEpubSource
) -> str:
    mounts = tuple(
        mount
        for mount in configuration.media_mounts
        if mount.name == source.mount_name
        and _mount_configuration_digest(mount) == source.mount_configuration_digest
    )
    if len(mounts) != 1:
        raise ValueError("resolved EPUB mount changed")
    maximum = min(
        configuration.evidence.epub_package.source_archive_bytes,
        configuration.evidence.maximum_document_bytes,
    )
    opened = open_local_media(mounts[0].container_path, source.relative_path)
    replacement = None
    try:
        before_identity = opened.identity
        digest = sha256_descriptor(
            opened.descriptor, opened.length, maximum=maximum
        )
        after_identity = descriptor_identity(opened.descriptor)
        replacement = open_local_media(
            mounts[0].container_path, source.relative_path
        )
        if before_identity != after_identity or replacement.identity != before_identity:
            raise LocalMediaError("local media source changed while reading")
        return digest
    finally:
        if replacement is not None:
            os.close(replacement.descriptor)
        os.close(opened.descriptor)


def _live_title_source_is_configured(
    configuration_path: Path,
    expected_configuration: ApplicationConfiguration,
    target: TargetId,
    expected_mount_digests: tuple[str, ...],
    expected_governing_digest: str,
) -> bool:
    configuration = load_configuration_file(configuration_path)
    mappings = _inventory_mount_mappings(configuration)
    current_mount_digests = tuple(
        sorted(mapping.configuration_digest for mapping in mappings)
    )
    package_limits = replace(
        configuration.evidence.epub_package,
        source_archive_bytes=min(
            configuration.evidence.epub_package.source_archive_bytes,
            configuration.evidence.maximum_document_bytes,
        ),
    )
    limits = replace(
        configuration.evidence.epub_opening,
        package=package_limits,
        text_characters_per_selection=min(
            configuration.evidence.epub_opening.text_characters_per_selection,
            configuration.evidence.maximum_text_characters,
        ),
    )
    expected_selected = tuple(
        configured
        for configured in expected_configuration.connections
        if configured.adapter == "audiobookshelf"
        and configured.connection_id == target.connection
        and target.library.value in configured.enabled_libraries
    )
    selected = tuple(
        configured
        for configured in configuration.connections
        if configured.adapter == "audiobookshelf"
        and configured.connection_id == target.connection
        and target.library.value in configured.enabled_libraries
    )
    return (
        "epub" in configuration.evidence.enabled_formats
        and len(expected_selected) == 1
        and len(selected) == 1
        and selected[0] == expected_selected[0]
        and current_mount_digests == expected_mount_digests
        and epub_governing_digest(
            limits, _epub_material_configuration(configuration, mappings)
        )
        == expected_governing_digest
    )


def _live_title_provider_is_configured(
    configuration_path: Path,
    expected_provider: HardcoverProviderConfiguration,
) -> bool:
    provider = load_configuration_file(configuration_path).hardcover_provider
    return provider == expected_provider


def _run_synthetic_title_proposal_report(
    database: Path, scenario_name: str
) -> SyntheticTitleProposalReport:
    """Run the closed generated-EPUB preview before opening durable state."""

    scenario = scenario_named(scenario_name)
    limits = EpubPackageLimits(source_archive_bytes=4 * 1024 * 1024)
    with generated_synthetic_epub(scenario) as generated:
        expected = Fingerprint("sha256", SchemaVersion("source-v1"), generated.digest)
        local_report = run_epub_package_child(
            generated.descriptor,
            expected,
            limits,
            wall_seconds=30,
            logical_identity=generated.opaque_identity,
        )
        # The child has consumed only the descriptor.  The generated resource is
        # closed by the context manager before this function returns on any path.
        epub_digest = generated.digest
        opaque_identity = generated.opaque_identity
    if local_report.outcome is not LocalEvidenceOutcome.SUCCESS:
        raise SchemaError("synthetic title evidence extraction failed")
    return _persist_synthetic_title_proposal(
        database, scenario, local_report, epub_digest, opaque_identity
    )


def _run_hardcover_title_proposal_report(
    database: Path,
    secret_reference: Path | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: AddressResolver | None = None,
) -> SyntheticTitleProposalReport:
    """Run one fixed generated-title proof of concept without live target authority."""

    scenario = scenario_named("agree-empty")
    limits = EpubPackageLimits(source_archive_bytes=4 * 1024 * 1024)
    with generated_synthetic_epub(scenario) as generated:
        expected = Fingerprint("sha256", SchemaVersion("source-v1"), generated.digest)
        local_report = run_epub_package_child(
            generated.descriptor,
            expected,
            limits,
            wall_seconds=30,
            logical_identity=generated.opaque_identity,
        )
        epub_digest = generated.digest
        opaque_identity = generated.opaque_identity
    if local_report.outcome is not LocalEvidenceOutcome.SUCCESS:
        raise SchemaError("Hardcover title evidence extraction failed")

    provider = None
    unavailable_kind = None
    client: httpx.AsyncClient | None = None
    if secret_reference is not None:
        try:
            handle = SecretHandle(
                SecretReference(str(secret_reference)),
                SecretScope.METADATA_PROVIDER,
            )
            client = PrivateFileProviderCredentials().hardcover_client(
                handle, transport=transport
            )
            provider = HardcoverTitleProvider(client, resolver=resolver)
        except (ProviderSecretUnavailable, TypeError, ValueError):
            unavailable_kind = FailureKind.UNAVAILABLE
    try:
        return _persist_synthetic_title_proposal(
            database,
            scenario,
            local_report,
            epub_digest,
            opaque_identity,
            profile=ProviderPreviewProfile.HARDCOVER_POC,
            provider=provider,
            unavailable_kind=unavailable_kind,
        )
    finally:
        if client is not None:
            asyncio.run(client.aclose())


def _persist_synthetic_title_proposal(
    database: Path,
    scenario: SyntheticScenario,
    local_report: LocalEvidenceReport,
    epub_digest: str,
    opaque_identity: str,
    *,
    profile: ProviderPreviewProfile = ProviderPreviewProfile.SYNTHETIC,
    provider: _MetadataProvider | None = None,
    unavailable_kind: FailureKind | None = None,
) -> SyntheticTitleProposalReport:
    def service(repository: SQLiteTitleProposalRepository) -> ProposeSyntheticTitle:
        selected_provider = (
            SyntheticTitleProvider(scenario)
            if profile is ProviderPreviewProfile.SYNTHETIC
            else provider
        )
        return ProposeSyntheticTitle(
            selected_provider,
            repository,
            profile=profile,
            unavailable_kind=unavailable_kind,
        )

    preflight(database)
    if path_entry_exists(database):
        preflight(database)
        connection = None
        try:
            connection = connect_existing(database)
            migrate(connection)
            return service(SQLiteTitleProposalRepository(connection)).run(
                scenario,
                local_report,
                epub_digest=epub_digest,
                opaque_file_identity=opaque_identity,
            )
        finally:
            if connection is not None:
                connection.close()

    with staged_state(database) as staged:
        connection = None
        try:
            connection = connect(staged)
            migrate(connection)
            report = service(SQLiteTitleProposalRepository(connection)).run(
                scenario,
                local_report,
                epub_digest=epub_digest,
                opaque_file_identity=opaque_identity,
            )
            finalize_staged_state(connection)
        finally:
            if connection is not None:
                connection.close()
        published = publish_staged_state(staged, database)
    if published:
        return report
    if profile is ProviderPreviewProfile.HARDCOVER_POC:
        return _store_precomputed_title_proposal(database, report)
    # A concurrent complete initializer won. Reuse the already-extracted,
    # deterministic input against that winner without regenerating resources.
    return _persist_synthetic_title_proposal(
        database,
        scenario,
        local_report,
        epub_digest,
        opaque_identity,
        profile=profile,
        provider=provider,
        unavailable_kind=unavailable_kind,
    )


def _store_precomputed_title_proposal(
    database: Path, report: SyntheticTitleProposalReport
) -> SyntheticTitleProposalReport:
    preflight(database)
    connection = None
    try:
        connection = connect_existing(database)
        migrate(connection)
        persistence = SQLiteTitleProposalRepository(connection).store(
            report.proposal_set
        )
        restored = SyntheticTitleProposalReport(persistence)
        restored.to_json()
        return restored
    finally:
        if connection is not None:
            connection.close()


def _run_synthetic_title_review_case_report(
    database: Path, scenario_name: str
) -> SyntheticTitleReviewCaseReport:
    """Admit only the already-persisted current F09a lineage selected by scenario."""

    lineage = scenario_lineage(scenario_name)
    preflight(database)
    if not path_entry_exists(database):
        raise SchemaError("synthetic title review state is unavailable")
    preflight(database)
    connection = None
    try:
        connection = connect_existing(database)
        migrate(connection)
        return InspectSyntheticTitleReviewCase(
            SQLiteTitleReviewCaseRepository(connection)
        ).run(lineage)
    finally:
        if connection is not None:
            connection.close()


def _run_hardcover_identity_report(
    configuration_path: Path,
    database: Path,
    query_path: Path,
    execution_policy: HardcoverExecutionPolicy,
) -> HardcoverIdentityReport:
    """Run the installed F09f boundary with authority acquired in safe order."""

    if type(execution_policy) is not HardcoverExecutionPolicy:
        return HardcoverIdentityReport(HardcoverIdentityOutcome.INVALID_INPUT)
    configuration = load_configuration_file(configuration_path)
    provider_configuration = configuration.hardcover_provider
    if not provider_configuration.enabled:
        return HardcoverIdentityReport(HardcoverIdentityOutcome.DISABLED)
    try:
        query = read_private_query(query_path)
    except PrivateQueryError:
        return HardcoverIdentityReport(HardcoverIdentityOutcome.INVALID_INPUT)
    if (
        execution_policy.requires_disposable_state
        and not _qualification_state_is_disposable(database)
    ):
        return HardcoverIdentityReport(HardcoverIdentityOutcome.INVALID_INPUT)
    try:
        DescriptorHardcoverIdentityGate(database)
    except Exception:
        return HardcoverIdentityReport(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
    expected_database_identity = None
    if execution_policy.requires_disposable_state:
        state, expected_database_identity = _run_new_qualification_state_check(
            database
        )
    else:
        state = _run_state_check(database)
        if state.succeeded:
            # Ordinary migration/check can begin from existing state, publish
            # this process's staged state, or converge on a concurrent winner.
            # Revalidate the result through a descriptor-backed snapshot and
            # carry that exact inode into the final deployment gate.
            state, expected_database_identity = _run_bound_existing_state_check(
                database
            )
    if not state.succeeded:
        return HardcoverIdentityReport(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
    try:
        gate = DescriptorHardcoverIdentityGate(
            database,
            expected_database_identity=expected_database_identity,
        )
    except Exception:
        return HardcoverIdentityReport(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)

    reference = provider_configuration.secret_reference
    if reference is None:
        return HardcoverIdentityReport(HardcoverIdentityOutcome.CREDENTIAL_UNAVAILABLE)
    handle = SecretHandle(
        SecretReference(reference),
        SecretScope.METADATA_PROVIDER,
    )
    log_isolation = ContextTransportLogIsolation()
    provider = LiveHardcoverIdentityProvider(
        lambda: PrivateFileProviderCredentials().hardcover_identity_token(handle),
        resolver_factory=IsolatedAddressResolverFactory(),
        channel_factory=lambda: HttpxIdentityByteChannel(
            log_isolation_factory=log_isolation
        ),
    )
    connection = None
    cancellation = threading.Event()
    try:
        connection = connect_existing(database)
        migrate(connection)
        gate.bind_connection(connection)
        work = ManageHardcoverIdentityWork(
            SQLiteHardcoverIdentityWorkRepository(connection)
        )
        lifecycle = RunHardcoverIdentityLifecycle(
            work,
            RunSafeHardcoverIdentity(work, provider, gate),
            gate,
            cancellation,
        )
        now = lambda: datetime.now(timezone.utc)
        context = OperationContext(
            Deadline(now() + timedelta(seconds=30)),
            CancellationProbe(
                CancellationId("hardcover-identity-report"),
                cancellation.is_set,
            ),
            ResourceBounds(4, 65_536),
            TimeProbe(now),
        )
        request = HardcoverIdentityExecutionRequest(
            hardcover_identity_work_key(
                query, provider_configuration.authority_revision
            ),
            _C5_INTERNAL_OWNER,
            query,
            execution_policy.max_starts,
        )
        with _temporary_identity_cancellation_signals(cancellation):
            report = RunHardcoverIdentityReport(lifecycle).run(request, context)
        if type(report) is not HardcoverIdentityReport:
            raise TypeError("Hardcover identity report is malformed")
        return report
    except Exception:
        return HardcoverIdentityReport(HardcoverIdentityOutcome.PERSISTENCE_FAILURE)
    finally:
        if connection is not None:
            connection.close()


def _qualification_state_is_disposable(database: Path) -> bool:
    """Require one absent target in an empty owner-only state directory."""

    descriptor: int | None = None
    try:
        if (
            not database.is_absolute()
            or database.name in {"", ".", ".."}
            or any(part in {".", ".."} for part in database.parts)
        ):
            return False
        supplied_parent = database.parent
        parent = database.parent.resolve(strict=True)
        status = parent.stat()
        supplied = supplied_parent.lstat()
        nofollow = getattr(os, "O_NOFOLLOW", None)
        cloexec = getattr(os, "O_CLOEXEC", None)
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if (
            nofollow is None
            or cloexec is None
            or directory_flag is None
            or not stat.S_ISDIR(status.st_mode)
            or not stat.S_ISDIR(supplied.st_mode)
            or (status.st_dev, status.st_ino) != (supplied.st_dev, supplied.st_ino)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) != 0o700
        ):
            return False
        descriptor = os.open(
            parent,
            os.O_RDONLY | nofollow | cloexec | directory_flag,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (status.st_dev, status.st_ino)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o700
        ):
            return False
        try:
            os.stat(
                database.name,
                dir_fd=descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            return False
        empty = _directory_descriptor_is_empty(descriptor)
        try:
            os.close(descriptor)
        except OSError:
            descriptor = None
            return False
        descriptor = None
        return empty
    except (OSError, RuntimeError, SchemaError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _directory_descriptor_is_empty(descriptor: int) -> bool:
    entries = os.scandir(descriptor)
    try:
        try:
            next(entries)
        except StopIteration:
            return True
        return False
    finally:
        entries.close()


def _run_hardcover_inert_preview(
    source: Mapping[str, object],
    *,
    privacy_observer: Callable[[str, tuple[object, ...]], None] | None = None,
) -> InertPreviewReport:
    if (
        set(source) != {"schema", "scenario", "config", "private_query"}
        or source.get("schema") != "hardcover-synthetic-input-v3"
        or source.get("scenario") != "inert-report-v3"
        or not isinstance(source.get("config"), Mapping)
        or not isinstance(source.get("private_query"), Mapping)
    ):
        raise ValueError("synthetic input is invalid")
    configuration = validate_hardcover_provider(source["config"])
    query = validate_private_query(source["private_query"])
    return inert_preview(configuration, query, privacy_observer=privacy_observer)


def _run_hardcover_identity_decision_preview(
    source: Mapping[str, object],
    *,
    privacy_observer: Callable[[str, tuple[object, ...]], None] | None = None,
) -> IdentityDecisionPreviewReport:
    if set(source) != {
        "schema",
        "scenario",
        "canonical_isbn_13",
        "derived_isbn_10",
        "local_title",
        "provider_rows",
    } or source.get("schema") != "hardcover-synthetic-input-v3" or source.get(
        "scenario"
    ) != "identity-decision-v3":
        raise ValueError("synthetic input is invalid")
    canonical_isbn = source.get("canonical_isbn_13")
    derived_isbn_10 = source.get("derived_isbn_10")
    local_title = source.get("local_title")
    provider_rows = source.get("provider_rows")
    if (
        type(canonical_isbn) is not str
        or len(canonical_isbn) != 13
        or not canonical_isbn.isascii()
        or not canonical_isbn.isdigit()
        or (derived_isbn_10 is not None and type(derived_isbn_10) is not str)
        or type(local_title) is not str
        or not 1 <= len(local_title) <= 512
        or type(provider_rows) is not list
        or len(provider_rows) > 4
    ):
        raise ValueError("synthetic input is invalid")
    query = validate_private_query({"isbn": canonical_isbn, "title": local_title})
    identity = isbn_identity(canonical_isbn)
    if derived_isbn_10 != identity.equivalent_isbn_10:
        raise ValueError("synthetic input is invalid")
    observations = tuple(_synthetic_identity_row(row) for row in provider_rows)
    typed_query = IsbnIdentityQuery(
        identity.canonical_isbn_13, identity.equivalent_isbn_10
    )
    provider = SyntheticBookIdentityProvider(typed_query, observations)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = OperationContext(
        Deadline(now + timedelta(seconds=5)),
        CancellationProbe(CancellationId("hardcover-identity-preview"), lambda: False),
        ResourceBounds(4, 65_536),
        TimeProbe(lambda: now),
    )
    return RunHardcoverIdentityReport().run(
        provider, query, context, privacy_observer=privacy_observer
    )


def _run_hardcover_fixed_adapter_preview(
    source: Mapping[str, object],
) -> FixedAdapterPreviewReport:
    return RunHardcoverFixedAdapterPreview().run(
        StructuralHardcoverIdentityPreview(), source
    )


_DURABLE_WORK_OPERATIONS = (
    ("regenerate_schema8", "preview", "exact-prefix-1-through-8", 0, None, None),
    ("verify_snapshot", "preview", "dump-ledger-integrity-fk-match", 100_000, None, None),
    ("apply_migration9", "preview", "schema9", 200_000, None, None),
    ("verify_migration9_idempotence", "preview", "schema9-unchanged", 300_000, None, None),
    ("accept", "api", None, 500_000, None, None),
    ("gate_acquire", "worker-a", None, 1_500_000, None, None),
    ("claim", "worker-a", None, 1_600_000, 1, 1),
    ("renew_start", "worker-a", None, 1_700_000, 1, 1),
    ("socket_close", "worker-a", "matched", 2_700_000, None, None),
    ("publish", "worker-a", "failure-before-commit", 2_800_000, None, 1),
    ("gate_release", "worker-a", None, 2_900_000, None, None),
    ("restart", "worker-b", None, 16_700_000, None, None),
    ("gate_acquire", "worker-b", None, 16_800_000, None, None),
    ("recover", "worker-b", "interrupted", 16_900_000, None, 2),
    ("claim", "worker-b", None, 17_000_000, 2, 2),
    ("renew_start", "worker-b", None, 17_100_000, 2, 2),
    ("socket_close", "worker-b", "matched", 18_100_000, None, None),
    ("publish", "worker-b", "matched", 18_200_000, None, 2),
    ("gate_release", "worker-b", None, 18_300_000, None, None),
    ("read_current", "api", "reuse", 86_418_200_000, None, None),
    ("expire_current", "api", "stale", 86_418_200_001, None, None),
)

_SCHEMA8_SNAPSHOT_SHA256 = "1040b8eefc8703503a9f515543c3618908bd7af0d08fae7700806b37a48811ed"
_SCHEMA8_SENTINEL = ("synthetic_schema8_sentinel", "fixture-v1")


def _validate_durable_work_preview_input(source: Mapping[str, object]) -> None:
    if set(source) != {"schema", "scenario", "failure_points", "script"}:
        raise ValueError("durable work preview input is malformed")
    if source.get("schema") != "hardcover-synthetic-input-v3" or source.get("scenario") != "durable-work-v3":
        raise ValueError("durable work preview input is malformed")
    if source.get("failure_points") != [
        "migration_statement",
        "publication_before_commit",
        "restart_after_start",
        "stale_fence",
    ]:
        raise ValueError("durable work preview input is malformed")
    script = source.get("script")
    if type(script) is not list or len(script) != len(_DURABLE_WORK_OPERATIONS):
        raise ValueError("durable work preview input is malformed")
    for row, (op, process, result, wall_us, candidate, fence) in zip(
        script, _DURABLE_WORK_OPERATIONS, strict=True
    ):
        expected: dict[str, object] = {"op": op, "process": process, "wall_us": wall_us}
        if result is not None:
            expected["result"] = result
        if candidate is not None:
            expected["candidate"] = candidate
        if fence is not None:
            expected["fence"] = fence
        if type(row) is not dict or row != expected:
            raise ValueError("durable work preview input is malformed")


def _run_hardcover_durable_work_preview(
    source: Mapping[str, object], database: Path
) -> DurableWorkPreviewReport:
    """Exercise migration and repository recovery without provider authority."""

    _validate_durable_work_preview_input(source)
    work_key = hashlib.sha256(b"public-synthetic-hardcover-work-v3").hexdigest()
    origin = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with bound_disposable_state(database) as bound, bound.staged_state() as staged:
        connection = staged.connect()
        try:
            if migrate_to_version(connection, 8) != 8:
                raise SchemaError("schema-eight preview could not be regenerated")
            connection.execute(
                "INSERT INTO instance_metadata(key,value) VALUES(?,?)",
                _SCHEMA8_SENTINEL,
            )
            staged.persist(connection)
            snapshot = "\n".join(connection.iterdump()) + "\n"
            if hashlib.sha256(snapshot.encode("utf-8")).hexdigest() != _SCHEMA8_SNAPSHOT_SHA256:
                raise SchemaError("schema-eight logical snapshot is inconsistent")
            ledger = connection.execute(
                "SELECT version,checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            if ledger != [(item.version, item.checksum) for item in MIGRATIONS[:8]]:
                raise SchemaError("schema-eight ledger is inconsistent")
            if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or connection.execute("PRAGMA foreign_key_check").fetchall():
                raise SchemaError("schema-eight preview is inconsistent")
            try:
                connection.execute("BEGIN IMMEDIATE")
                removed = connection.execute(
                    "DELETE FROM instance_metadata WHERE key=? AND value=?",
                    _SCHEMA8_SENTINEL,
                ).rowcount
                if removed != 1:
                    raise SchemaError("schema-eight sentinel cleanup failed")
                connection.commit()
            except (sqlite3.Error, SchemaError):
                connection.rollback()
                raise

            migration_rollback_verified = False
            try:
                connection.execute("BEGIN EXCLUSIVE")
                connection.execute(MIGRATIONS[8].statements[0])
                raise sqlite3.OperationalError("injected migration interruption")
            except sqlite3.OperationalError:
                connection.rollback()
                migration_rollback_verified = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='hardcover_identity_generations'"
                ).fetchone() is None
            if (
                not migration_rollback_verified
                or migrate_to_version(connection, 11) != 11
                or migrate_to_version(connection, 11) != 11
            ):
                raise SchemaError("current schema migration proof failed")
            migration_idempotence_verified = connection.execute(
                "SELECT version FROM schema_migrations WHERE version IN (9,10,11) "
                "ORDER BY version"
            ).fetchall() == [(9,), (10,), (11,)]

            service = ManageHardcoverIdentityWork(
                SQLiteHardcoverIdentityWorkRepository(connection)
            )
            checkpoint = service.begin_preview(work_key, origin)
            publication_rollback_verified = (
                connection.execute(
                    "SELECT COUNT(*) FROM hardcover_identity_results WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                == (0,)
                and connection.execute(
                    "SELECT COUNT(*) FROM current_hardcover_identity WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                == (0,)
            )
            if not publication_rollback_verified:
                raise SchemaError("identity publication rollback proof failed")
            staged.persist(connection)
        finally:
            connection.close()

        connection = staged.connect_existing()
        try:
            service = ManageHardcoverIdentityWork(
                SQLiteHardcoverIdentityWorkRepository(connection)
            )
            exact, stale = service.resume_preview(checkpoint, origin)
            if exact.outcome.value != "matched" or stale.outcome.value != "stale" or stale.reference.generation != 2:
                raise SchemaError("identity currentness boundary proof failed")
            staged.persist(connection)
        finally:
            connection.close()
        if not bound.publish(staged):
            raise SchemaError("durable work preview database publication raced")

    return DurableWorkPreviewReport(
        outcome="stale",
        schema_from=8,
        schema_to=11,
        migration_rollback_verified=migration_rollback_verified,
        migration_idempotence_verified=migration_idempotence_verified,
        publication_rollback_verified=publication_rollback_verified,
        interrupted_recovery_gate_owned=True,
    )


class _SyntheticExecutorClock:
    def __init__(self, now: datetime) -> None:
        self.now = now


class _ObservedGateLease:
    def __init__(self, inner: object, gate: _ObservedGate) -> None:
        self._inner = inner
        self._gate = gate

    def validate_for_send(self) -> None:
        if not self._gate.held:
            raise SchemaError("safe executor gate order is invalid")
        self._gate.send_validations += 1
        self._inner.validate_for_send()

    def release(self) -> None:
        self._inner.release()
        self._gate.held = False
        self._gate.releases += 1


class _ObservedGate:
    def __init__(self, inner: DescriptorHardcoverIdentityGate) -> None:
        self._inner = inner
        self.held = False
        self.acquisitions = 0
        self.releases = 0
        self.send_validations = 0

    def try_acquire(self) -> _ObservedGateLease | None:
        if self.held:
            raise SchemaError("safe executor gate was acquired recursively")
        lease = self._inner.try_acquire()
        if lease is None:
            return None
        self.held = True
        self.acquisitions += 1
        return _ObservedGateLease(lease, self)


class _SyntheticExecutorProvider:
    def __init__(
        self,
        result: IdentityAttemptResult,
        clock: _SyntheticExecutorClock,
        duration_ms: int,
    ) -> None:
        self._result = result
        self._clock = clock
        self._duration = timedelta(milliseconds=duration_ms)
        self.starts = 0
        self.deadlines: list[datetime] = []

    def prepare(
        self,
        query: IsbnIdentityQuery,
        context: OperationContext,
    ) -> _SyntheticExecutorProvider:
        return self

    def exchange(
        self,
        context: OperationContext,
        *,
        begin_transport,
    ) -> IdentityAttemptResult:
        context = begin_transport()
        self.starts += 1
        self.deadlines.append(context.deadline.at)
        self._clock.now += self._duration
        return self._result


class _SyntheticFailingGate:
    def try_acquire(self) -> object:
        raise SchemaError("safe executor synthetic gate failure")


def _safe_executor_context(
    clock: _SyntheticExecutorClock, caller_deadline_ms: int
) -> OperationContext:
    return OperationContext(
        Deadline(clock.now + timedelta(milliseconds=caller_deadline_ms)),
        CancellationProbe(
            CancellationId("hardcover-safe-executor-preview"), lambda: False
        ),
        ResourceBounds(4, 65_536),
        TimeProbe(lambda: clock.now),
    )


def _safe_executor_request(
    query: object,
) -> HardcoverIdentityExecutionRequest:
    return HardcoverIdentityExecutionRequest(
        secrets.token_hex(32),
        _C5_INTERNAL_OWNER,
        query,
    )


_C5_INTERNAL_OWNER = "internal-safe-executor-owner-v1-noncontributor-boundary"


def _safe_executor_match() -> IdentityAttemptResult:
    row = ProviderIdentityObservation(
        id=1001,
        book_id=2001,
        canonical_id=None,
        isbn_10="0000000019",
        isbn_10_valid=True,
        isbn_13="9780000000019",
        isbn_13_valid=True,
        isbns_match=True,
        reading_format_id=4,
        state="normalized",
        compilation=False,
        book=ProviderBookObservation(
            id=2001,
            canonical_id=None,
            book_status_id=1,
            state="processed",
            title="Remote Volume Zeta",
            parent_book_id=None,
            compilation=False,
            is_partial_book=False,
        ),
    )
    return IdentityAttemptResult.success((row,))


def _validate_safe_executor_preview_input(source: Mapping[str, object]) -> None:
    if (
        set(source) != {"schema", "scenario", "cases"}
        or source.get("schema") != "hardcover-synthetic-input-v3"
        or source.get("scenario") != "safe-executor-v3"
    ):
        raise ValueError("safe executor preview input is malformed")
    cases = source.get("cases")
    if type(cases) is not list or not 1 <= len(cases) <= 16:
        raise ValueError("safe executor preview input is malformed")
    operations = {
        "matched",
        "unavailable",
        "busy_gate",
        "aggregate_expired",
        "caller_expired",
        "caller_deadline",
        "late_match",
        "late_exhausted",
        "gate_failure",
        "stale_refresh",
    }
    for case in cases:
        if not isinstance(case, Mapping) or set(case) != {
            "label",
            "process",
            "operation",
            "caller_deadline_ms",
            "provider_duration_ms",
        }:
            raise ValueError("safe executor preview input is malformed")
        label = case.get("label")
        process = case.get("process")
        operation = case.get("operation")
        deadline = case.get("caller_deadline_ms")
        duration = case.get("provider_duration_ms")
        if (
            type(label) is not str
            or not 1 <= len(label) <= 48
            or not label.isascii()
            or not label.isprintable()
            or label.strip() != label
            or type(process) is not str
            or not 1 <= len(process) <= 32
            or not process.isascii()
            or not process.isprintable()
            or process.strip() != process
            or operation not in operations
            or type(deadline) is not int
            or not 0 <= deadline <= 30_000
            or type(duration) is not int
            or not 0 <= duration <= 30_000
        ):
            raise ValueError("safe executor preview input is malformed")
        if operation == "caller_expired" and deadline != 0:
            raise ValueError("safe executor preview input is malformed")
        if operation == "caller_deadline" and not 1 <= deadline < 5_000:
            raise ValueError("safe executor preview input is malformed")
        if operation == "late_match" and duration < min(deadline, 5_000):
            raise ValueError("safe executor preview input is malformed")
        if operation == "late_exhausted" and (
            deadline != 30_000 or duration != 4_001
        ):
            raise ValueError("safe executor preview input is malformed")
        if operation not in {"caller_expired", "caller_deadline"} and deadline < 5_000:
            raise ValueError("safe executor preview input is malformed")


def _run_hardcover_safe_executor_preview(
    source: Mapping[str, object],
    database: Path,
    privacy_observer: Callable[[str, tuple[object, ...]], None] | None = None,
) -> SafeExecutorPreviewReport:
    """Exercise the real lifecycle, repository, and descriptor gate without egress."""

    _validate_safe_executor_preview_input(source)
    if privacy_observer is not None and not callable(privacy_observer):
        raise TypeError("safe executor privacy observer must be callable")
    query = validate_private_query(
        {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
    )
    cases = source["cases"]

    with bound_disposable_state(database) as bound, bound.staged_state() as staged:
        connection = staged.connect()
        try:
            migrate(connection)
            staged.persist(connection)
        finally:
            connection.close()
        if not bound.publish(staged):
            raise SchemaError("safe executor preview database publication raced")

        connection = connect_existing(database)
        try:
            service = ManageHardcoverIdentityWork(
                SQLiteHardcoverIdentityWorkRepository(connection)
            )
            raw_gate = DescriptorHardcoverIdentityGate(database)
            gate = _ObservedGate(raw_gate)
            clock = _SyntheticExecutorClock(datetime.now(timezone.utc))
            providers: list[_SyntheticExecutorProvider] = []
            owners: list[str] = []
            # Each operation-specific proof is universal over the selected
            # applicable cases and therefore true for an empty applicable set.
            verified = {
                "deadline": True,
                "caller": True,
                "late": True,
                "closed": True,
            }

            for case in cases:
                operation = case["operation"]
                deadline_ms = case["caller_deadline_ms"]
                duration_ms = case["provider_duration_ms"]
                request = _safe_executor_request(query)
                owners.append(request.owner)
                provider_result = (
                    IdentityAttemptResult.failed(
                        IdentityAttemptFailureKind.UNAVAILABLE
                    )
                    if operation in {"unavailable", "late_exhausted"}
                    else _safe_executor_match()
                )
                provider = _SyntheticExecutorProvider(
                    provider_result, clock, duration_ms
                )
                providers.append(provider)
                selected_gate: object = gate
                held = None
                if operation == "busy_gate":
                    held = raw_gate.try_acquire()
                    if held is None:
                        raise SchemaError("safe executor busy proof could not acquire")
                elif operation == "gate_failure":
                    selected_gate = _SyntheticFailingGate()
                elif operation == "stale_refresh":
                    seed = service.accept(request.work_key, clock.now)
                    seed_lease = gate.try_acquire()
                    if seed_lease is None:
                        raise SchemaError("safe executor stale seed gate failed")
                    try:
                        claim = service.claim(
                            seed.reference,
                            request.owner,
                            clock.now,
                            gate_owned=True,
                        )
                        started = service.start(
                            claim,
                            request.owner,
                            clock.now,
                            gate_owned=True,
                        )
                        if type(started) is not IdentityWorkClaim:
                            raise SchemaError("safe executor stale seed start failed")
                        service.publish_matched(
                            started,
                            request.owner,
                            clock.now,
                            IdentityWorkObservation(
                                IdentityMatchOutcome.MATCHED,
                                1,
                                1001,
                                2001,
                                "Remote Volume Zeta",
                            ),
                            gate_owned=True,
                        )
                    finally:
                        seed_lease.release()
                    clock.now += timedelta(hours=24, microseconds=1)
                elif operation == "aggregate_expired":
                    service.accept(request.work_key, clock.now)
                    clock.now += timedelta(seconds=30)
                elif operation == "late_exhausted":
                    service.accept(request.work_key, clock.now)
                    clock.now += timedelta(seconds=20)

                before_acquire = gate.acquisitions
                context = _safe_executor_context(clock, deadline_ms)
                runner = RunHardcoverIdentityReport(
                    RunSafeHardcoverIdentity(service, provider, selected_gate)
                )
                try:
                    report = runner.run(request, context)
                    if operation == "stale_refresh":
                        if (
                            report.outcome is not HardcoverIdentityOutcome.STALE
                            or provider.starts != 0
                        ):
                            raise SchemaError(
                                "safe executor stale report case failed"
                            )
                        report = runner.run(request, context)
                finally:
                    if held is not None:
                        held.release()

                expected = {
                    "matched": "matched",
                    "unavailable": "in_progress",
                    "busy_gate": "in_progress",
                    "aggregate_expired": "retry_exhausted",
                    "caller_expired": "retry_exhausted",
                    "caller_deadline": "matched",
                    "late_match": "in_progress",
                    "late_exhausted": "retry_exhausted",
                    "gate_failure": "persistence_failure",
                    "stale_refresh": "matched",
                }[operation]
                if report.outcome.value != expected:
                    raise SchemaError("safe executor semantic case failed")
                durable = connection.execute(
                    "SELECT generation,state,attempt_count "
                    "FROM hardcover_identity_generations WHERE work_key=? "
                    "ORDER BY generation DESC LIMIT 1",
                    (request.work_key,),
                ).fetchone()
                expected_durable = {
                    "matched": (1, "succeeded", 1),
                    "unavailable": (1, "retry_wait", 1),
                    "busy_gate": (1, "pending", 0),
                    "aggregate_expired": (1, "pending", 0),
                    "caller_expired": (1, "pending", 0),
                    "caller_deadline": (1, "succeeded", 1),
                    "late_match": (1, "retry_wait", 1),
                    "late_exhausted": (1, "failed", 1),
                    "gate_failure": (1, "pending", 0),
                    "stale_refresh": (2, "succeeded", 1),
                }[operation]
                expected_starts = int(
                    operation
                    in {
                        "matched",
                        "unavailable",
                        "caller_deadline",
                        "late_match",
                        "late_exhausted",
                        "stale_refresh",
                    }
                )
                if durable != expected_durable or provider.starts != expected_starts:
                    raise SchemaError("safe executor durable semantic case failed")
                if operation == "aggregate_expired":
                    verified["deadline"] = (
                        verified["deadline"]
                        and gate.acquisitions == before_acquire
                    )
                elif operation == "caller_deadline":
                    verified["caller"] = (
                        verified["caller"]
                        and provider.deadlines == [context.deadline.at]
                    )
                elif operation in {"late_match", "late_exhausted"}:
                    matched_publications = connection.execute(
                        "SELECT COUNT(*) FROM hardcover_identity_results "
                        "WHERE work_key=? AND outcome='matched'",
                        (request.work_key,),
                    ).fetchone()
                    verified["late"] = (
                        verified["late"] and matched_publications == (0,)
                    )
                elif operation == "gate_failure":
                    verified["closed"] = (
                        verified["closed"] and provider.starts == 0
                    )

            if privacy_observer is not None:
                try:
                    privacy_observer(
                        "persistence_checkpoint", ("sqlite-main-wal-shm",)
                    )
                except Exception as exc:
                    raise SchemaError(
                        "safe executor privacy persistence observation failed"
                    ) from exc

            labels_private = (
                48 < len(_C5_INTERNAL_OWNER) <= 64
                and all(owner == _C5_INTERNAL_OWNER for owner in owners)
            )
        finally:
            connection.close()

    starts = sum(provider.starts for provider in providers)
    report = SafeExecutorPreviewReport(
        outcome="all_cases_passed",
        cases_executed=len(cases),
        attempts_started=starts,
        gate_order_verified=(
            gate.send_validations == starts * 3
            and gate.acquisitions == gate.releases
            and not gate.held
        ),
        deadline_before_gate_verified=verified["deadline"],
        caller_deadline_verified=verified["caller"],
        late_result_rejected=verified["late"],
        closed_mapping_verified=verified["closed"],
        contributor_labels_private=labels_private,
    )
    if privacy_observer is not None:
        observations = (
            (
                "traces",
                ("hardcover.synthetic.safe_executor", "safe-executor-v3"),
            ),
            ("spans", ("one-attempt-case-set", report.outcome)),
            (
                "metrics",
                (
                    "hardcover_safe_executor_preview_total",
                    (("outcome", report.outcome),),
                    1,
                    None,
                ),
            ),
        )
        try:
            for observer_channel, observer_values in observations:
                privacy_observer(observer_channel, observer_values)
        except Exception as exc:
            raise SchemaError("safe executor privacy observation failed") from exc
    return report


class _SyntheticLifecycleProvider:
    def __init__(
        self,
        results: list[IdentityAttemptResult],
        *,
        raised_signal: signal.Signals | None = None,
    ) -> None:
        self._results = results
        self._raised_signal = raised_signal
        self.starts = 0

    def prepare(
        self,
        query: object,
        context: OperationContext,
    ) -> _SyntheticLifecycleProvider:
        return self

    def exchange(
        self,
        context: OperationContext,
        *,
        begin_transport,
    ) -> IdentityAttemptResult:
        begin_transport()
        self.starts += 1
        if self._raised_signal is not None:
            signal.raise_signal(self._raised_signal)
        return self._results.pop(0)


class _SyntheticLifecycleWait:
    def __init__(
        self,
        clock: _SyntheticExecutorClock,
        gate: _ObservedGate,
        cancellation: threading.Event,
    ) -> None:
        self._clock = clock
        self._gate = gate
        self._cancellation = cancellation
        self.timeouts: list[float] = []
        self.released = True

    def wait(self, timeout: float) -> bool:
        self.released = self.released and not self._gate.held
        self.timeouts.append(timeout)
        self._clock.now += timedelta(seconds=timeout)
        return self._cancellation.is_set()


@contextmanager
def _temporary_identity_cancellation_signals(
    cancellation: threading.Event,
):
    """Make foreground SIGINT/SIGTERM observable without owning shutdown."""

    if (
        type(cancellation) is not threading.Event
        or threading.current_thread() is not threading.main_thread()
    ):
        raise SchemaError("identity lifecycle signal handling is unavailable")

    installed: list[tuple[signal.Signals, object]] = []

    def cancel(signum: int, frame: object) -> None:
        cancellation.set()

    try:
        for selected in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(selected)
            signal.signal(selected, cancel)
            installed.append((selected, previous))
        yield
    finally:
        for selected, previous in reversed(installed):
            signal.signal(selected, previous)


def _lifecycle_result(source: Mapping[str, object]) -> IdentityAttemptResult:
    outcome = source["outcome"]
    retry_after = source["retry_after_seconds"]
    if outcome == "matched":
        return _safe_executor_match()
    kind = {
        "unavailable": IdentityAttemptFailureKind.UNAVAILABLE,
        "timeout": IdentityAttemptFailureKind.TIMEOUT,
        "rate_limited": IdentityAttemptFailureKind.RATE_LIMITED,
    }[outcome]
    return IdentityAttemptResult.failed(kind, retry_after_seconds=retry_after)


def _validate_lifecycle_preview_input(source: Mapping[str, object]) -> None:
    if (
        set(source) != {"schema", "scenario", "cases"}
        or source.get("schema") != "hardcover-synthetic-input-v3"
        or source.get("scenario") != "lifecycle-orchestration-v3"
    ):
        raise ValueError("identity lifecycle preview input is malformed")
    cases = source.get("cases")
    if type(cases) is not list or len(cases) != 6:
        raise ValueError("identity lifecycle preview input is malformed")
    operations: dict[str, list[Mapping[str, object]]] = {}
    for case in cases:
        if not isinstance(case, Mapping) or set(case) != {
            "label",
            "process",
            "operation",
            "results",
        }:
            raise ValueError("identity lifecycle preview input is malformed")
        label = case.get("label")
        process = case.get("process")
        operation = case.get("operation")
        results = case.get("results")
        if (
            type(label) is not str
            or not 1 <= len(label) <= 48
            or not label.isascii()
            or not label.isprintable()
            or label.strip() != label
            or type(process) is not str
            or not 1 <= len(process) <= 32
            or not process.isascii()
            or not process.isprintable()
            or process.strip() != process
            or type(operation) is not str
            or type(results) is not list
        ):
            raise ValueError("identity lifecycle preview input is malformed")
        typed_results: list[Mapping[str, object]] = []
        for result in results:
            if not isinstance(result, Mapping) or set(result) != {
                "outcome",
                "retry_after_seconds",
            }:
                raise ValueError("identity lifecycle preview input is malformed")
            outcome = result.get("outcome")
            retry_after = result.get("retry_after_seconds")
            if (
                outcome not in {"matched", "unavailable", "timeout", "rate_limited"}
                or (
                    retry_after is not None
                    and (
                        outcome != "rate_limited"
                        or type(retry_after) is not int
                        or not 0 <= retry_after <= 3
                    )
                )
            ):
                raise ValueError("identity lifecycle preview input is malformed")
            typed_results.append(result)
        operations[operation] = typed_results
    if set(operations) != {
        "retry_match",
        "retry_exhausted",
        "restart",
        "death_recovery",
        "one_shot",
        "signals",
    }:
        raise ValueError("identity lifecycle preview input is malformed")
    retryable = {"unavailable", "timeout", "rate_limited"}
    if not (
        2 <= len(operations["retry_match"]) <= 3
        and operations["retry_match"][-1]["outcome"] == "matched"
        and all(row["outcome"] in retryable for row in operations["retry_match"][:-1])
        and len(operations["retry_exhausted"]) == 3
        and all(row["outcome"] in retryable for row in operations["retry_exhausted"])
        and [row["outcome"] for row in operations["restart"]]
        == ["unavailable", "matched"]
        and [row["outcome"] for row in operations["death_recovery"]] == ["matched"]
        and len(operations["one_shot"]) == 1
        and operations["one_shot"][0]["outcome"] in retryable
        and [row["outcome"] for row in operations["signals"]] == ["matched"]
    ):
        raise ValueError("identity lifecycle preview input is malformed")


def _run_hardcover_identity_lifecycle_preview(
    source: Mapping[str, object],
    database: Path,
    privacy_observer: Callable[[str, tuple[object, ...]], None] | None = None,
) -> IdentityLifecyclePreviewReport:
    """Exercise restart-safe foreground orchestration without egress."""

    _validate_lifecycle_preview_input(source)
    if privacy_observer is not None and not callable(privacy_observer):
        raise TypeError("identity lifecycle privacy observer must be callable")
    query = validate_private_query(
        {"isbn": "9780000000019", "title": "Synthetic Ebook Alpha"}
    )
    by_operation = {case["operation"]: case for case in source["cases"]}
    providers: list[_SyntheticLifecycleProvider] = []
    waits: list[_SyntheticLifecycleWait] = []
    retry_schedule_verified = True
    restart_verified = False
    death_recovery_gate_owned = False
    cancellation_verified = True
    signal_handlers_restored = True
    one_shot_egress_count = 0
    fourth_start_forbidden = False

    with bound_disposable_state(database) as bound, bound.staged_state() as staged:
        connection = staged.connect()
        try:
            migrate(connection)
            staged.persist(connection)
        finally:
            connection.close()
        if not bound.publish(staged):
            raise SchemaError("identity lifecycle preview database publication raced")

        connection = connect_existing(database)
        try:
            service = ManageHardcoverIdentityWork(
                SQLiteHardcoverIdentityWorkRepository(connection)
            )
            gate = _ObservedGate(DescriptorHardcoverIdentityGate(database))
            clock = _SyntheticExecutorClock(datetime.now(timezone.utc))

            def request(operation: str, *, egress_limit: int = 3):
                return HardcoverIdentityExecutionRequest(
                    secrets.token_hex(32),
                    _C5_INTERNAL_OWNER,
                    query,
                    egress_limit,
                )

            def execute(
                operation: str,
                cancellation: threading.Event,
                *,
                egress_limit: int = 3,
                signal_before: signal.Signals | None = None,
                raised_signal: signal.Signals | None = None,
            ):
                case = by_operation[operation]
                provider = _SyntheticLifecycleProvider(
                    [_lifecycle_result(row) for row in case["results"]],
                    raised_signal=raised_signal,
                )
                retry_wait = _SyntheticLifecycleWait(clock, gate, cancellation)
                providers.append(provider)
                waits.append(retry_wait)
                context = OperationContext(
                    Deadline(clock.now + timedelta(seconds=29)),
                    CancellationProbe(
                        CancellationId("hardcover-lifecycle-preview"),
                        cancellation.is_set,
                    ),
                    ResourceBounds(4, 65_536),
                    TimeProbe(lambda: clock.now),
                )
                selected = request(operation, egress_limit=egress_limit)
                lifecycle = RunHardcoverIdentityLifecycle(
                    service,
                    RunSafeHardcoverIdentity(service, provider, gate),
                    gate,
                    retry_wait,
                )
                runner = RunHardcoverIdentityReport(lifecycle)
                restored = True
                if signal_before is None and raised_signal is None:
                    report = runner.run(selected, context)
                else:
                    previous = {
                        sig: signal.getsignal(sig)
                        for sig in (signal.SIGINT, signal.SIGTERM)
                    }
                    with _temporary_identity_cancellation_signals(cancellation):
                        if signal_before is not None:
                            signal.raise_signal(signal_before)
                        report = runner.run(selected, context)
                    restored = all(
                        signal.getsignal(sig) is previous[sig]
                        for sig in (signal.SIGINT, signal.SIGTERM)
                    )
                return report, selected, provider, retry_wait, context, restored

            report, _, provider, retry_wait, _, _ = execute(
                "retry_match", threading.Event()
            )
            expected_waits = []
            for candidate, result in enumerate(
                by_operation["retry_match"]["results"][:-1], start=1
            ):
                expected_waits.append(
                    float(max(candidate, result["retry_after_seconds"] or 0))
                )
            retry_schedule_verified = (
                report.outcome.value == "matched"
                and retry_wait.timeouts == expected_waits
            )

            exhausted, exhausted_request, provider, _, _, _ = execute(
                "retry_exhausted", threading.Event()
            )
            fourth_start_forbidden = (
                exhausted.outcome.value == "retry_exhausted"
                and provider.starts == 3
                and connection.execute(
                    "SELECT attempt_count FROM hardcover_identity_generations "
                    "WHERE work_key=?",
                    (exhausted_request.work_key,),
                ).fetchone()
                == (3,)
            )

            restart_case = by_operation["restart"]
            restart_provider = _SyntheticLifecycleProvider(
                [_lifecycle_result(row) for row in restart_case["results"]]
            )
            providers.append(restart_provider)
            restart_request = request("restart")
            restart_context = _safe_executor_context(clock, 29_000)
            first = RunSafeHardcoverIdentity(
                service, restart_provider, gate
            ).run(restart_request, restart_context)
            restart_wait = _SyntheticLifecycleWait(clock, gate, threading.Event())
            waits.append(restart_wait)
            restarted = RunHardcoverIdentityLifecycle(
                service,
                RunSafeHardcoverIdentity(service, restart_provider, gate),
                gate,
                restart_wait,
            )
            before_due = restarted.run(restart_request, restart_context)
            clock.now += timedelta(seconds=1)
            after_due = restarted.run(restart_request, restart_context)
            restart_verified = (
                first.outcome.value == "in_progress"
                and before_due.outcome.value == "in_progress"
                and after_due.outcome.value == "matched"
                and restart_wait.timeouts == []
                and restart_provider.starts == 2
            )

            death_case = by_operation["death_recovery"]
            death_provider = _SyntheticLifecycleProvider(
                [_lifecycle_result(row) for row in death_case["results"]]
            )
            providers.append(death_provider)
            death_request = request("death_recovery")
            seed_lease = gate.try_acquire()
            if seed_lease is None:
                raise SchemaError("identity lifecycle death seed gate failed")
            try:
                accepted = service.accept(death_request.work_key, clock.now)
                claim = service.claim(
                    accepted.reference,
                    death_request.owner,
                    clock.now,
                    gate_owned=True,
                )
                started = service.start(
                    claim, death_request.owner, clock.now, gate_owned=True
                )
            finally:
                seed_lease.release()
            if type(started) is not IdentityWorkClaim:
                raise SchemaError("identity lifecycle death seed failed")
            clock.now = started.lease_expires_at + timedelta(microseconds=1)
            death_wait = _SyntheticLifecycleWait(clock, gate, threading.Event())
            waits.append(death_wait)
            recovered = RunHardcoverIdentityLifecycle(
                service,
                RunSafeHardcoverIdentity(service, death_provider, gate),
                gate,
                death_wait,
            ).run(death_request, _safe_executor_context(clock, 14_000))
            death_recovery_gate_owned = (
                recovered.outcome.value == "matched"
                and connection.execute(
                    "SELECT outcome FROM hardcover_identity_attempts "
                    "WHERE work_key=? ORDER BY attempt",
                    (death_request.work_key,),
                ).fetchall()
                == [("interrupted",), ("matched",)]
            )

            one, one_request, one_provider, _, one_context, _ = execute(
                "one_shot", threading.Event(), egress_limit=1
            )
            repeated = RunSafeHardcoverIdentity(
                service, one_provider, gate
            ).run(one_request, one_context)
            one_shot_egress_count = one_provider.starts
            cancellation_verified = cancellation_verified and (
                one.outcome.value == "retry_exhausted"
                and repeated.outcome.value == "retry_exhausted"
            )

            for selected_signal in (signal.SIGINT, signal.SIGTERM):
                before_event = threading.Event()
                before_report, _, before_provider, _, _, before_restored = execute(
                    "signals", before_event, signal_before=selected_signal
                )
                during_event = threading.Event()
                cancellation_verified = cancellation_verified and (
                    before_report.outcome.value == "cancelled"
                    and before_provider.starts == 0
                )
                during_report, _, during_provider, _, _, during_restored = execute(
                    "signals", during_event, raised_signal=selected_signal
                )
                cancellation_verified = cancellation_verified and (
                    during_report.outcome.value == "cancelled"
                    and during_provider.starts == 1
                )
                signal_handlers_restored = (
                    signal_handlers_restored
                    and before_restored
                    and during_restored
                )
            if privacy_observer is not None:
                try:
                    privacy_observer(
                        "persistence_checkpoint", ("sqlite-main-wal-shm",)
                    )
                except Exception as exc:
                    raise SchemaError(
                        "identity lifecycle privacy persistence observation failed"
                    ) from exc
        finally:
            connection.close()

    report = IdentityLifecyclePreviewReport(
        outcome="all_cases_passed",
        cases_executed=6,
        attempts_started=sum(provider.starts for provider in providers),
        retry_schedule_verified=retry_schedule_verified,
        release_before_wait_verified=all(wait.released for wait in waits),
        restart_verified=restart_verified,
        death_recovery_gate_owned=death_recovery_gate_owned,
        cancellation_verified=cancellation_verified,
        signal_handlers_restored=signal_handlers_restored,
        one_shot_egress_count=one_shot_egress_count,
        fourth_start_forbidden=fourth_start_forbidden,
    )
    report.validate()
    if privacy_observer is not None:
        for channel, values in (
            (
                "traces",
                ("hardcover.synthetic.identity_lifecycle", "lifecycle-orchestration-v3"),
            ),
            ("spans", ("foreground-lifecycle-case-set", report.outcome)),
            (
                "metrics",
                (
                    "hardcover_identity_lifecycle_preview_total",
                    (("outcome", report.outcome),),
                    1,
                    None,
                ),
            ),
        ):
            privacy_observer(channel, values)
    return report


def _synthetic_identity_row(source: object) -> ProviderIdentityObservation:
    if not isinstance(source, Mapping) or set(source) != {
        "id",
        "book_id",
        "canonical_id",
        "isbn_10",
        "isbn_10_valid",
        "isbn_13",
        "isbn_13_valid",
        "isbns_match",
        "reading_format_id",
        "state",
        "compilation",
        "book",
    }:
        raise ValueError("synthetic provider row is invalid")
    if not isinstance(source.get("book"), Mapping):
        raise ValueError("synthetic provider row is invalid")
    book = _synthetic_book(source["book"])
    if not (
        _json_positive_integer(source.get("id"))
        and _json_positive_integer(source.get("book_id"))
        and _json_optional_integer(source.get("canonical_id"))
        and _json_optional_text(source.get("isbn_10"))
        and _json_optional_boolean(source.get("isbn_10_valid"))
        and _json_optional_text(source.get("isbn_13"))
        and _json_optional_boolean(source.get("isbn_13_valid"))
        and _json_optional_boolean(source.get("isbns_match"))
        and type(source.get("reading_format_id")) is int
        and type(source.get("state")) is str
        and type(source.get("compilation")) is bool
    ):
        raise ValueError("synthetic provider row is invalid")
    return ProviderIdentityObservation(
        id=source["id"],
        book_id=source["book_id"],
        canonical_id=source["canonical_id"],
        isbn_10=source["isbn_10"],
        isbn_10_valid=source["isbn_10_valid"],
        isbn_13=source["isbn_13"],
        isbn_13_valid=source["isbn_13_valid"],
        isbns_match=source["isbns_match"],
        reading_format_id=source["reading_format_id"],
        state=source["state"],
        compilation=source["compilation"],
        book=book,
    )


def _synthetic_book(source: Mapping[str, object]) -> ProviderBookObservation:
    if set(source) != {
        "id",
        "canonical_id",
        "book_status_id",
        "state",
        "title",
        "parent_book_id",
        "compilation",
        "is_partial_book",
    } or not (
        _json_positive_integer(source.get("id"))
        and _json_optional_integer(source.get("canonical_id"))
        and type(source.get("book_status_id")) is int
        and type(source.get("state")) is str
        and type(source.get("title")) is str
        and 1 <= len(source["title"]) <= 512
        and _json_optional_integer(source.get("parent_book_id"))
        and type(source.get("compilation")) is bool
        and type(source.get("is_partial_book")) is bool
    ):
        raise ValueError("synthetic provider book is invalid")
    return ProviderBookObservation(
        id=source["id"],
        canonical_id=source["canonical_id"],
        book_status_id=source["book_status_id"],
        state=source["state"],
        title=source["title"],
        parent_book_id=source["parent_book_id"],
        compilation=source["compilation"],
        is_partial_book=source["is_partial_book"],
    )


def _json_positive_integer(value: object) -> bool:
    return type(value) is int and value >= 1


def _json_optional_integer(value: object) -> bool:
    return value is None or type(value) is int


def _json_optional_boolean(value: object) -> bool:
    return value is None or type(value) is bool


def _json_optional_text(value: object) -> bool:
    return value is None or type(value) is str


def main(argv: Sequence[str] | None = None) -> int:
    """Assemble the current process and delegate to its CLI interface."""

    return cli_main(
        argv,
        state_check_runner=_run_state_check,
        job_heartbeat_runner=_run_job_heartbeat,
        synthetic_inventory_runner=_run_synthetic_inventory,
        connection_probe_runner=_run_connection_probe,
        audiobookshelf_inventory_runner=_run_audiobookshelf_inventory,
        inventory_inspection_runner=_inspect_audiobookshelf_inventory,
        audiobookshelf_poll_runner=_run_audiobookshelf_poll,
        inventory_poll_inspection_runner=_inspect_audiobookshelf_poll,
        epub_package_report_runner=_run_epub_package_report,
        epub_opening_report_runner=_run_epub_opening_report,
        epub_opening_evidence_runner=_run_epub_opening_evidence,
        live_title_proposal_runner=_run_live_title_proposal_report,
        synthetic_title_proposal_runner=_run_synthetic_title_proposal_report,
        hardcover_title_proposal_runner=_run_hardcover_title_proposal_report,
        synthetic_title_review_case_runner=_run_synthetic_title_review_case_report,
        hardcover_identity_runner=_run_hardcover_identity_report,
        hardcover_inert_preview_runner=_run_hardcover_inert_preview,
        hardcover_identity_decision_preview_runner=(
            _run_hardcover_identity_decision_preview
        ),
        hardcover_fixed_adapter_preview_runner=_run_hardcover_fixed_adapter_preview,
        hardcover_durable_work_preview_runner=_run_hardcover_durable_work_preview,
        hardcover_safe_executor_preview_runner=_run_hardcover_safe_executor_preview,
        hardcover_identity_lifecycle_preview_runner=(
            _run_hardcover_identity_lifecycle_preview
        ),
    )


__all__ = ["main"]
