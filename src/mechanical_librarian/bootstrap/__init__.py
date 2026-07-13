"""Composition root for executable Mechanical Librarian processes."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from mechanical_librarian.adapters.persistence.audit import SQLiteAuditRepository
from mechanical_librarian.adapters.persistence.jobs import SQLiteJobHeartbeatRepository
from mechanical_librarian.adapters.persistence.inventory import (
    SQLiteInventoryRepository,
)
from mechanical_librarian.adapters.persistence.inventory_v2 import (
    SQLiteInventoryV2Repository,
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
from mechanical_librarian.application.connection_probe import (
    ConnectionProbeOutcome,
    ConnectionProbeResult,
    InspectLibraryConnection,
)
from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.application.job_heartbeat import (
    HeartbeatOutcome,
    HeartbeatResult,
    RunSyntheticJobHeartbeat,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryConnectionVerification,
    InventoryLibraryResult,
    InventoryOutcome,
    InventoryRunResult,
    ReconcileInventory,
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
from mechanical_librarian.domain.identifiers import AdapterId, LibraryId
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    LibraryConnection,
    MediaType,
)
from mechanical_librarian.domain.inventory_v2 import ScanBounds, ScanUsage
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.security.target_secrets import (
    PrivateFileTargetCredentials,
    SecretFileUnavailable,
    target_endpoint_allowed,
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
                    credentials.target_client(handle, configured.endpoint)
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


def _inventory_context(bounds: ScanBounds, cancellation: str) -> OperationContext:
    now = datetime.now(timezone.utc)
    return OperationContext(
        Deadline(now + timedelta(seconds=bounds.max_seconds)),
        CancellationProbe(CancellationId(cancellation), lambda: False),
        ResourceBounds(bounds.max_items, min(bounds.max_bytes, 67_108_864)),
        TimeProbe(lambda: datetime.now(timezone.utc)),
    )


def _inventory_probe_failure(outcome: ConnectionProbeOutcome) -> FailureKind | None:
    if outcome is ConnectionProbeOutcome.READY:
        return None
    return {
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
    }[outcome]


def _run_audiobookshelf_inventory(
    configuration_path: Path, database: Path
) -> InventoryRunResult:
    """Execute one durable, compatibility-gated GET-only inventory scan."""

    try:
        _, configured, connection, libraries, bounds = _inventory_connection(
            configuration_path
        )
        credentials = PrivateFileTargetCredentials()
        handle = SecretHandle(configured.secret, SecretScope.TARGET_SERVICE)

        def client_factory():
            try:
                return PortResult.success(
                    credentials.inventory_client(handle, configured.endpoint)
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
            )
            return InventoryConnectionVerification(
                ScanUsage(response_bytes=response_bytes),
                _inventory_probe_failure(probe.outcome),
            )

        source = AudiobookshelfInventoryReader(
            client_factory=client_factory,
            connection_verifier=verify_connection,
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


def _inspect_audiobookshelf_inventory(
    configuration_path: Path, database: Path
) -> InventoryRunResult:
    """Inspect durable scan state without resolving a credential or using the network."""

    try:
        _, _, connection, libraries, bounds = _inventory_connection(configuration_path)

        def operation(state: sqlite3.Connection) -> InventoryRunResult:
            repository = SQLiteInventoryV2Repository(state)
            results = []
            context = _inventory_context(bounds, "inventory-inspect")
            for library in libraries:
                inspected = repository.inspect(connection, library, context)
                if not inspected.accepted or inspected.value is None:
                    return InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())
                results.append(
                    InventoryLibraryResult(
                        library, InventoryOutcome.READY, inspected.value
                    )
                )
            return InventoryRunResult(InventoryOutcome.READY, tuple(results))

        preflight(database)
        return _run_existing_operation(database, operation)
    except (ConfigurationError, SchemaError, ValueError):
        return InventoryRunResult(InventoryOutcome.INVALID_REQUEST, ())
    except Exception:
        return InventoryRunResult(InventoryOutcome.PERSISTENCE_FAILURE, ())


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
    )


__all__ = ["main"]
