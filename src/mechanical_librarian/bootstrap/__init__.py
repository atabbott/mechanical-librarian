"""Composition root for executable Mechanical Librarian processes."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TypeVar

from mechanical_librarian.adapters.persistence.audit import SQLiteAuditRepository
from mechanical_librarian.adapters.persistence.jobs import SQLiteJobHeartbeatRepository
from mechanical_librarian.adapters.persistence.inventory import SQLiteInventoryRepository
from mechanical_librarian.adapters.library_services.synthetic import (
    SyntheticLibraryReader,
    synthetic_capabilities,
)
from mechanical_librarian.application.configuration import ConfigurationError
from mechanical_librarian.application.job_heartbeat import (
    HeartbeatOutcome,
    HeartbeatResult,
    RunSyntheticJobHeartbeat,
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
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.application.synthetic_inventory import (
    InspectSyntheticInventory,
    SyntheticInventoryOutcome,
    SyntheticInventoryResult,
)
from mechanical_librarian.domain.identifiers import AdapterId, LibraryId
from mechanical_librarian.domain.inventory import LibraryConnection
from mechanical_librarian.infrastructure.configuration import load_configuration_file
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
        return RunSyntheticJobHeartbeat(
            SQLiteJobHeartbeatRepository(connection)
        ).run()

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
        return InspectSyntheticInventory(
            reader, SQLiteInventoryRepository(state)
        ).run(connection, library, context)

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


def main(argv: Sequence[str] | None = None) -> int:
    """Assemble the current process and delegate to its CLI interface."""

    return cli_main(
        argv,
        state_check_runner=_run_state_check,
        job_heartbeat_runner=_run_job_heartbeat,
        synthetic_inventory_runner=_run_synthetic_inventory,
    )


__all__ = ["main"]
