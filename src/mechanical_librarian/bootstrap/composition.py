"""Composition root for executable Mechanical Librarian processes."""

from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
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
    run_epub_opening_child,
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
from mechanical_librarian.adapters.providers.hardcover_title import (
    AddressResolver,
    HardcoverTitleProvider,
)
from mechanical_librarian.application.connection_probe import (
    ConnectionProbeOutcome,
    ConnectionProbeResult,
    InspectLibraryConnection,
)
from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.application.hardcover_identity import (
    HardcoverIdentityReport,
    IdentityDecisionPreviewReport,
    InertPreviewReport,
    IsbnIdentityQuery,
    RunHardcoverIdentityReport,
    inert_preview,
    preactivation_report,
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
from mechanical_librarian.application.title_review_cases import (
    InspectSyntheticTitleReviewCase,
    SyntheticTitleReviewCaseReport,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    canonical_json,
)
from mechanical_librarian.domain.epub_opening import EpubOpeningLimits, EpubOpeningReport
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceFailure,
    EpubEvidenceLocator,
    EpubEvidenceMode,
    EpubEvidenceResult,
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
    ProviderBookObservation,
    ProviderIdentityObservation,
    isbn_identity,
    validate_private_query,
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
from mechanical_librarian.infrastructure.persistence.sqlite import (
    SchemaError,
    connect,
    connect_existing,
    finalize_staged_state,
    migrate,
    path_entry_exists,
    preflight,
    publish_staged_state,
    staged_state,
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
        report = run_epub_opening_child(
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


def _run_hardcover_identity_report(configuration_path: Path) -> HardcoverIdentityReport:
    configuration = load_configuration_file(configuration_path)
    return preactivation_report(configuration.hardcover_provider)


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
        synthetic_title_proposal_runner=_run_synthetic_title_proposal_report,
        hardcover_title_proposal_runner=_run_hardcover_title_proposal_report,
        synthetic_title_review_case_runner=_run_synthetic_title_review_case_report,
        hardcover_identity_runner=_run_hardcover_identity_report,
        hardcover_inert_preview_runner=_run_hardcover_inert_preview,
        hardcover_identity_decision_preview_runner=(
            _run_hardcover_identity_decision_preview
        ),
    )


__all__ = ["main"]
