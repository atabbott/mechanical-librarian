"""SQLite persistence for complete-only versioned inventory reconciliation."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from mechanical_librarian.application.inventory_reconciliation import (
    InventoryScanRepository,
)
from mechanical_librarian.application.job_heartbeat import (
    RepositoryOutcome,
    RepositoryResult,
)
from mechanical_librarian.application.ports import OperationContext
from mechanical_librarian.domain.identifiers import (
    LibraryId,
    ScanId,
    SnapshotId,
    canonical_json,
)
from mechanical_librarian.domain.inventory import LibraryConnection
from mechanical_librarian.domain.inventory import FileReference
from mechanical_librarian.domain.inventory_polling import PollClaim
from mechanical_librarian.domain.inventory_v2 import (
    InventoryBindingSetMember,
    InventoryFileBinding,
    InventoryInspection,
    InventoryObservation,
    InventorySnapshotV2,
    InventorySelector,
    ScanBounds,
    ScanStart,
    ScanState,
    ScanUsage,
    ValidatedInventoryFileBinding,
    inventory_file_binding_digest,
    opaque_snapshot_reference,
    opaque_target_reference,
    validate_inventory_binding_set,
)


def validated_current_inventory_bindings(
    connection: sqlite3.Connection,
    snapshot: InventorySnapshotV2,
) -> tuple[ValidatedInventoryFileBinding, ...]:
    """Load one exact current binding set and reject semantic corruption."""

    if type(snapshot) is not InventorySnapshotV2:
        raise ValueError("inventory snapshot is invalid")
    target = snapshot.observation.target
    identity = (
        target.adapter.value,
        target.connection.value,
        target.library.value,
        target.external_item.value,
    )
    pointer = connection.execute(
        "SELECT snapshot_id,binding_set_digest FROM "
        "current_inventory_snapshot_binding_sets WHERE adapter_id=? "
        "AND connection_id=? AND library_id=? AND external_item=?",
        identity,
    ).fetchone()
    if pointer is None:
        locator = connection.execute(
            "SELECT 1 FROM inventory_snapshot_locator_index WHERE adapter_id=? "
            "AND connection_id=? AND library_id=? AND external_item=? "
            "AND snapshot_id=?",
            (*identity, snapshot.id.value),
        ).fetchone()
        if locator is not None:
            raise ValueError("current inventory binding set pointer is unavailable")
        return ()
    if str(pointer[0]) != snapshot.id.value:
        raise ValueError("current inventory binding set snapshot is inconsistent")
    set_digest = str(pointer[1])
    header = connection.execute(
        "SELECT adapter_id,connection_id,library_id,external_item,snapshot_id,"
        "binding_count FROM inventory_snapshot_binding_sets "
        "WHERE binding_set_digest=?",
        (set_digest,),
    ).fetchone()
    if header is None:
        raise ValueError("current inventory binding set identity is inconsistent")
    rows = connection.execute(
        "SELECT m.file_index,m.binding_digest,b.adapter_id,b.connection_id,"
        "b.library_id,b.external_item,b.snapshot_id,b.target_reference,"
        "b.snapshot_reference,b.file_index,b.file_identity,b.media_kind,"
        "b.mount_configuration_digest,b.mount_name,b.relative_path,b.binding_digest "
        "FROM inventory_snapshot_binding_set_members m LEFT JOIN "
        "inventory_snapshot_file_bindings b ON b.binding_digest=m.binding_digest "
        "WHERE m.binding_set_digest=? ORDER BY m.file_index",
        (set_digest,),
    ).fetchall()
    members: list[InventoryBindingSetMember] = []
    for row in rows:
        if any(value is None for value in row[2:]):
            raise ValueError("current inventory binding set member is unavailable")
        binding = InventoryFileBinding(
            int(row[9]),
            str(row[10]),
            str(row[11]),
            str(row[12]),
            FileReference(str(row[13]), str(row[14])),
        )
        members.append(
            InventoryBindingSetMember(
                int(row[0]),
                str(row[1]),
                tuple(str(value) for value in row[2:6]),
                str(row[6]),
                str(row[7]),
                str(row[8]),
                binding,
            )
        )
    return validate_inventory_binding_set(
        snapshot,
        set_digest,
        tuple(str(value) for value in header[:4]),
        str(header[4]),
        int(header[5]),
        tuple(members),
    )


class SQLiteInventoryV2Repository(InventoryScanRepository):
    """Keep immutable history and publish a complete scan in one transaction."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str | None = None,
        owner_alive: Callable[[str], bool] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        binding_configuration_digests: frozenset[str] = frozenset(),
    ) -> None:
        self._connection = connection
        self._owner_id = _default_owner_id() if owner_id is None else owner_id
        self._owner_alive = _owner_alive if owner_alive is None else owner_alive
        self._now = now
        if (
            type(binding_configuration_digests) is not frozenset
            or any(
                type(value) is not str
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in binding_configuration_digests
            )
        ):
            raise ValueError("inventory binding configuration digests are invalid")
        self._binding_configuration_digests = binding_configuration_digests
        if not self._owner_id or len(self._owner_id) > 64:
            raise ValueError("inventory scan owner is invalid")
        self._prepared: dict[
            str,
            tuple[
                ScanStart,
                tuple[InventorySnapshotV2, ...],
                ScanUsage,
                datetime,
            ],
        ] = {}

    def recover_interrupted(
        self, finished_at: datetime, context: OperationContext
    ) -> RepositoryResult[int]:
        if not _valid_time(finished_at) or type(context) is not OperationContext:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        if (
            context.preflight_result(item_count=1, serialized_bytes=0).failure
            is not None
        ):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        try:
            recovered = 0
            with _transaction(self._connection):
                running_count = int(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM inventory_scans WHERE state='running'"
                    ).fetchone()[0]
                )
                if (
                    context.preflight_result(
                        item_count=running_count, serialized_bytes=0
                    ).failure
                    is not None
                ):
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                rows = self._connection.execute(
                    "SELECT scan_id,owner_id FROM inventory_scans WHERE state='running'"
                ).fetchall()
                for scan_id, owner_id in rows:
                    if owner_id == self._owner_id or self._owner_alive(str(owner_id)):
                        continue
                    cursor = self._connection.execute(
                        "UPDATE inventory_scans SET state='incomplete', "
                        "failure_kind='interrupted', finished_at=? "
                        "WHERE scan_id=? AND state='running'",
                        (_format_timestamp(finished_at), scan_id),
                    )
                    recovered += cursor.rowcount
            return RepositoryResult(RepositoryOutcome.ACCEPTED, recovered)
        except sqlite3.Error:
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def begin(
        self,
        scan_id: ScanId,
        connection: LibraryConnection,
        library: LibraryId,
        bounds: ScanBounds,
        started_at: datetime,
        context: OperationContext,
        *,
        cohort_id: str | None = None,
        cohort_size: int = 1,
        claim: PollClaim | None = None,
    ) -> RepositoryResult[ScanStart]:
        if (
            type(scan_id) is not ScanId
            or type(connection) is not LibraryConnection
            or type(library) is not LibraryId
            or type(bounds) is not ScanBounds
            or not _valid_time(started_at)
            or type(context) is not OperationContext
            or (cohort_id is not None and (not cohort_id or len(cohort_id) > 64))
            or type(cohort_size) is not int
            or cohort_size < 1
            or (claim is not None and type(claim) is not PollClaim)
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        effective_cohort = scan_id.value if cohort_id is None else cohort_id
        if (
            context.preflight_result(item_count=1, serialized_bytes=0).failure
            is not None
        ):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        try:
            with _transaction(self._connection):
                if claim is not None and not self._claim_is_live(claim, started_at):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                item_count, serialized_bytes = self._current_stats(
                    connection.adapter.value, connection.id.value, library.value
                )
                if item_count > bounds.max_items or serialized_bytes > bounds.max_bytes:
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                if (
                    context.preflight_result(
                        item_count=item_count,
                        serialized_bytes=serialized_bytes,
                    ).failure
                    is not None
                ):
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                current = self._current(connection, library)
                current_digest = _current_digest(current)
                self._connection.execute(
                    "INSERT INTO inventory_scans(scan_id,owner_id,cohort_id,cohort_size,"
                    "adapter_id,connection_id,library_id,"
                    "state,failure_kind,base_digest,page_size,max_pages,max_items,max_bytes,"
                    "max_seconds,stability_delay_seconds,started_at) VALUES (?,?,?,?,?,?,?,"
                    "'running',NULL,?,?,?,?,?,?,?,?)",
                    (
                        scan_id.value,
                        self._owner_id,
                        effective_cohort,
                        cohort_size,
                        connection.adapter.value,
                        connection.id.value,
                        library.value,
                        current_digest,
                        bounds.page_size,
                        bounds.max_pages,
                        bounds.max_items,
                        bounds.max_bytes,
                        bounds.max_seconds,
                        bounds.stability_delay_seconds,
                        _format_timestamp(started_at),
                    ),
                )
                if claim is not None:
                    self._connection.execute(
                        "INSERT INTO inventory_scan_poll_claims(scan_id,poll_id,attempt,fence) "
                        "VALUES (?,?,?,?)",
                        (scan_id.value, claim.poll_id, claim.attempt, claim.fence),
                    )
            return RepositoryResult(
                RepositoryOutcome.ACCEPTED,
                ScanStart(scan_id, current, current_digest),
            )
        except sqlite3.IntegrityError:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def finish(
        self,
        scan_id: ScanId,
        state: ScanState,
        failure_kind: str,
        usage: ScanUsage,
        finished_at: datetime,
        context: OperationContext,
        *,
        claim: PollClaim | None = None,
    ) -> RepositoryResult[InventoryInspection]:
        if (
            type(scan_id) is not ScanId
            or state not in {ScanState.INCOMPLETE, ScanState.UNSTABLE}
            or type(failure_kind) is not str
            or type(usage) is not ScanUsage
            or not _valid_time(finished_at)
            or type(context) is not OperationContext
            or (claim is not None and type(claim) is not PollClaim)
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                if claim is not None and not self._scan_claim_is_live(
                    scan_id.value, claim, finished_at
                ):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                row = self._connection.execute(
                    "SELECT adapter_id,connection_id,library_id,state FROM inventory_scans "
                    "WHERE scan_id=?",
                    (scan_id.value,),
                ).fetchone()
                if row is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if row[3] != ScanState.RUNNING.value:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                self._prepared.pop(scan_id.value, None)
                cursor = self._connection.execute(
                    "UPDATE inventory_scans SET state=?,failure_kind=?,page_count=?,"
                    "item_count=?,response_bytes=?,finished_at=? WHERE scan_id=? AND state='running'",
                    (
                        state.value,
                        failure_kind,
                        usage.pages,
                        usage.items,
                        usage.response_bytes,
                        _format_timestamp(finished_at),
                        scan_id.value,
                    ),
                )
                if cursor.rowcount != 1:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                inspection = self._inspection_for(
                    scan_id,
                    row[0],
                    row[1],
                    row[2],
                    context,
                    terminalizing=True,
                )
            return RepositoryResult(RepositoryOutcome.ACCEPTED, inspection)
        except sqlite3.IntegrityError:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def checkpoint(
        self,
        scan_id: ScanId,
        usage: ScanUsage,
        observed_at: datetime,
        context: OperationContext,
        *,
        claim: PollClaim | None = None,
    ) -> RepositoryResult[InventoryInspection]:
        if (
            type(scan_id) is not ScanId
            or type(usage) is not ScanUsage
            or not _valid_time(observed_at)
            or type(context) is not OperationContext
            or (claim is not None and type(claim) is not PollClaim)
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                if claim is not None and not self._scan_claim_is_live(
                    scan_id.value, claim, observed_at
                ):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                row = self._connection.execute(
                    "SELECT adapter_id,connection_id,library_id,state,page_count,"
                    "item_count,response_bytes FROM inventory_scans WHERE scan_id=?",
                    (scan_id.value,),
                ).fetchone()
                if (
                    row is None
                    or row[3] != ScanState.RUNNING.value
                    or usage.pages < int(row[4])
                    or usage.items < int(row[5])
                    or usage.response_bytes < int(row[6])
                ):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                self._connection.execute(
                    "UPDATE inventory_scans SET page_count=?,item_count=?,response_bytes=? "
                    "WHERE scan_id=? AND state='running'",
                    (usage.pages, usage.items, usage.response_bytes, scan_id.value),
                )
                inspection = self._inspection_for(
                    scan_id, str(row[0]), str(row[1]), str(row[2]), context
                )
            return RepositoryResult(RepositoryOutcome.ACCEPTED, inspection)
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def publish_complete(
        self,
        start: ScanStart,
        snapshots: tuple[InventorySnapshotV2, ...],
        usage: ScanUsage,
        finished_at: datetime,
        context: OperationContext,
        *,
        claim: PollClaim | None = None,
    ) -> RepositoryResult[InventoryInspection]:
        if (
            type(start) is not ScanStart
            or type(snapshots) is not tuple
            or any(type(item) is not InventorySnapshotV2 for item in snapshots)
            or len({item.observation.target for item in snapshots}) != len(snapshots)
            or type(usage) is not ScanUsage
            or not _valid_time(finished_at)
            or type(context) is not OperationContext
            or (claim is not None and type(claim) is not PollClaim)
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            cohort = self._connection.execute(
                "SELECT cohort_size FROM inventory_scans WHERE scan_id=?",
                (start.scan_id.value,),
            ).fetchone()
            if cohort is not None and int(cohort[0]) > 1:
                return self._publish_cohort_part(
                    start, snapshots, usage, finished_at, context, claim
                )
            with _transaction(self._connection):
                if claim is not None and not self._scan_claim_is_live(
                    start.scan_id.value, claim, finished_at
                ):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                row = self._connection.execute(
                    "SELECT adapter_id,connection_id,library_id,state,base_digest,"
                    "max_items,max_bytes "
                    "FROM inventory_scans WHERE scan_id=?",
                    (start.scan_id.value,),
                ).fetchone()
                if row is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if row[3] != ScanState.RUNNING.value or row[4] != start.current_digest:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                checkpoint = context.preflight_result(item_count=1, serialized_bytes=0)
                if checkpoint.failure is not None:
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                current_count, current_bytes = self._current_stats(
                    str(row[0]), str(row[1]), str(row[2])
                )
                checkpoint = context.preflight_result(
                    item_count=current_count,
                    serialized_bytes=current_bytes,
                )
                if checkpoint.failure is not None:
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                current = self._current_values(row[0], row[1], row[2])
                if _current_digest(current) != start.current_digest:
                    return RepositoryResult(RepositoryOutcome.REJECTED)

                for snapshot in snapshots:
                    target = snapshot.observation.target
                    if (
                        target.adapter.value != row[0]
                        or target.connection.value != row[1]
                        or target.library.value != row[2]
                    ):
                        return RepositoryResult(RepositoryOutcome.REJECTED)
                serialized_bytes = sum(
                    len(snapshot.observation.canonical.encode("utf-8"))
                    for snapshot in snapshots
                )
                if len(snapshots) > int(row[5]) or serialized_bytes > int(row[6]):
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                checkpoint = context.preflight_result(
                    item_count=len(snapshots),
                    serialized_bytes=serialized_bytes,
                )
                if checkpoint.failure is not None:
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

                self._connection.execute(
                    "DELETE FROM current_inventory_snapshot_binding_sets WHERE adapter_id=? "
                    "AND connection_id=? AND library_id=?",
                    (row[0], row[1], row[2]),
                )
                self._connection.execute(
                    "DELETE FROM current_inventory_v2 WHERE adapter_id=? AND "
                    "connection_id=? AND library_id=?",
                    (row[0], row[1], row[2]),
                )
                for snapshot in snapshots:
                    target = snapshot.observation.target
                    persisted_id = self._persist_or_reuse(snapshot)
                    values = (
                        target.adapter.value,
                        target.connection.value,
                        target.library.value,
                        target.external_item.value,
                        persisted_id.value,
                    )
                    self._connection.execute(
                        "INSERT INTO current_inventory_v2(adapter_id,connection_id,library_id,"
                        "external_item,snapshot_id) VALUES (?,?,?,?,?) ON CONFLICT("
                        "adapter_id,connection_id,library_id,external_item) DO UPDATE SET "
                        "snapshot_id=excluded.snapshot_id",
                        values,
                    )
                    self._set_current_binding_set(snapshot, persisted_id)
                    self._connection.execute(
                        "INSERT INTO inventory_scan_items(scan_id,adapter_id,connection_id,"
                        "library_id,external_item,snapshot_id) VALUES (?,?,?,?,?,?)",
                        (start.scan_id.value, *values),
                    )

                current_count = int(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM current_inventory_v2 WHERE adapter_id=? "
                        "AND connection_id=? AND library_id=?",
                        (row[0], row[1], row[2]),
                    ).fetchone()[0]
                )
                membership_count = int(
                    self._connection.execute(
                        "SELECT COUNT(*) FROM inventory_scan_items WHERE scan_id=?",
                        (start.scan_id.value,),
                    ).fetchone()[0]
                )
                if current_count != len(snapshots) or membership_count != len(
                    snapshots
                ):
                    raise _PublicationAborted

                checkpoint = context.preflight_result(
                    item_count=len(snapshots),
                    serialized_bytes=serialized_bytes,
                )
                if checkpoint.failure is not None:
                    raise _PublicationAborted
                cursor = self._connection.execute(
                    "UPDATE inventory_scans SET state='complete',failure_kind=NULL,page_count=?,"
                    "item_count=?,response_bytes=?,finished_at=? WHERE scan_id=? AND state='running'",
                    (
                        usage.pages,
                        usage.items,
                        usage.response_bytes,
                        _format_timestamp(finished_at),
                        start.scan_id.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise _PublicationAborted
                checkpoint = context.preflight_result(
                    item_count=len(snapshots),
                    serialized_bytes=serialized_bytes,
                )
                if checkpoint.failure is not None:
                    raise _PublicationAborted
                inspection = self._inspection_for(
                    start.scan_id, row[0], row[1], row[2], context
                )
                checkpoint = context.preflight_result(
                    item_count=len(snapshots),
                    serialized_bytes=serialized_bytes,
                )
                if checkpoint.failure is not None:
                    raise _PublicationAborted
            return RepositoryResult(RepositoryOutcome.ACCEPTED, inspection)
        except _PublicationAborted:
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        except sqlite3.IntegrityError:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def _publish_cohort_part(
        self,
        start: ScanStart,
        snapshots: tuple[InventorySnapshotV2, ...],
        usage: ScanUsage,
        finished_at: datetime,
        context: OperationContext,
        claim: PollClaim | None,
    ) -> RepositoryResult[InventoryInspection]:
        """Hold complete libraries in memory and publish the whole cohort atomically."""

        try:
            with _transaction(self._connection):
                row = self._connection.execute(
                    "SELECT adapter_id,connection_id,library_id,state,base_digest,"
                    "max_items,max_bytes,cohort_id,cohort_size FROM inventory_scans "
                    "WHERE scan_id=?",
                    (start.scan_id.value,),
                ).fetchone()
                if row is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if (
                    row[3] != ScanState.RUNNING.value
                    or row[4] != start.current_digest
                    or (claim is not None and not self._scan_claim_is_live(
                        start.scan_id.value, claim, finished_at
                    ))
                    or _current_digest(
                        self._current_values(str(row[0]), str(row[1]), str(row[2]))
                    )
                    != start.current_digest
                ):
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                for snapshot in snapshots:
                    target = snapshot.observation.target
                    if (
                        target.adapter.value != row[0]
                        or target.connection.value != row[1]
                        or target.library.value != row[2]
                    ):
                        return RepositoryResult(RepositoryOutcome.REJECTED)
                serialized_bytes = sum(
                    len(snapshot.observation.canonical.encode("utf-8"))
                    for snapshot in snapshots
                )
                if len(snapshots) > int(row[5]) or serialized_bytes > int(row[6]):
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                if context.preflight_result(
                    item_count=len(snapshots), serialized_bytes=serialized_bytes
                ).failure is not None:
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
                self._connection.execute(
                    "UPDATE inventory_scans SET page_count=?,item_count=?,response_bytes=? "
                    "WHERE scan_id=? AND state='running'",
                    (
                        usage.pages,
                        usage.items,
                        usage.response_bytes,
                        start.scan_id.value,
                    ),
                )
                cohort_id = str(row[7])
                cohort_size = int(row[8])
                inspection = self._inspection_for(
                    start.scan_id, str(row[0]), str(row[1]), str(row[2]), context
                )
            self._prepared[start.scan_id.value] = (
                start,
                snapshots,
                usage,
                finished_at,
            )
            cohort_rows = self._connection.execute(
                "SELECT scan_id FROM inventory_scans WHERE cohort_id=? ORDER BY scan_id",
                (cohort_id,),
            ).fetchall()
            cohort_scans = tuple(str(value[0]) for value in cohort_rows)
            if (
                len(cohort_scans) != cohort_size
                or any(scan_id not in self._prepared for scan_id in cohort_scans)
            ):
                return RepositoryResult(RepositoryOutcome.ACCEPTED, inspection)

            with _transaction(self._connection):
                if context.preflight_result(
                    item_count=0, serialized_bytes=0
                ).failure is not None:
                    raise _PublicationAborted
                validated = []
                for scan_id in cohort_scans:
                    prepared_start, prepared_snapshots, prepared_usage, prepared_at = (
                        self._prepared[scan_id]
                    )
                    scan_row = self._connection.execute(
                        "SELECT adapter_id,connection_id,library_id,state,base_digest "
                        "FROM inventory_scans WHERE scan_id=? AND cohort_id=?",
                        (scan_id, cohort_id),
                    ).fetchone()
                    if (
                        scan_row is None
                        or scan_row[3] != ScanState.RUNNING.value
                        or scan_row[4] != prepared_start.current_digest
                        or _current_digest(
                            self._current_values(
                                str(scan_row[0]), str(scan_row[1]), str(scan_row[2])
                            )
                        )
                        != prepared_start.current_digest
                        or (claim is not None and not self._scan_claim_is_live(
                            scan_id, claim, finished_at
                        ))
                    ):
                        raise _PublicationRejected
                    validated.append(
                        (
                            scan_id,
                            scan_row,
                            prepared_snapshots,
                            prepared_usage,
                            prepared_at,
                        )
                    )
                for (
                    scan_id,
                    scan_row,
                    prepared_snapshots,
                    prepared_usage,
                    prepared_at,
                ) in validated:
                    self._connection.execute(
                        "DELETE FROM current_inventory_snapshot_binding_sets WHERE adapter_id=? "
                        "AND connection_id=? AND library_id=?",
                        (scan_row[0], scan_row[1], scan_row[2]),
                    )
                    self._connection.execute(
                        "DELETE FROM current_inventory_v2 WHERE adapter_id=? AND "
                        "connection_id=? AND library_id=?",
                        (scan_row[0], scan_row[1], scan_row[2]),
                    )
                    for snapshot in prepared_snapshots:
                        target = snapshot.observation.target
                        persisted_id = self._persist_or_reuse(snapshot)
                        values = (
                            target.adapter.value,
                            target.connection.value,
                            target.library.value,
                            target.external_item.value,
                            persisted_id.value,
                        )
                        self._connection.execute(
                            "INSERT INTO current_inventory_v2(adapter_id,connection_id,"
                            "library_id,external_item,snapshot_id) VALUES (?,?,?,?,?)",
                            values,
                        )
                        self._set_current_binding_set(snapshot, persisted_id)
                        self._connection.execute(
                            "INSERT INTO inventory_scan_items(scan_id,adapter_id,connection_id,"
                            "library_id,external_item,snapshot_id) VALUES (?,?,?,?,?,?)",
                            (scan_id, *values),
                        )
                    cursor = self._connection.execute(
                        "UPDATE inventory_scans SET state='complete',failure_kind=NULL,"
                        "page_count=?,item_count=?,response_bytes=?,finished_at=? "
                        "WHERE scan_id=? AND state='running'",
                        (
                            prepared_usage.pages,
                            prepared_usage.items,
                            prepared_usage.response_bytes,
                            _format_timestamp(prepared_at),
                            scan_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise _PublicationAborted
                final = self._connection.execute(
                    "SELECT adapter_id,connection_id,library_id FROM inventory_scans "
                    "WHERE scan_id=?",
                    (start.scan_id.value,),
                ).fetchone()
                inspection = self._inspection_for(
                    start.scan_id, str(final[0]), str(final[1]), str(final[2]), context
                )
            for scan_id in cohort_scans:
                self._prepared.pop(scan_id, None)
            return RepositoryResult(RepositoryOutcome.ACCEPTED, inspection)
        except _PublicationAborted:
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        except _PublicationRejected:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        except sqlite3.IntegrityError:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        except (sqlite3.Error, ValueError, KeyError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def inspect(
        self,
        connection: LibraryConnection,
        library: LibraryId,
        context: OperationContext,
    ) -> RepositoryResult[InventoryInspection]:
        if (
            type(connection) is not LibraryConnection
            or type(library) is not LibraryId
            or type(context) is not OperationContext
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        if (
            context.preflight_result(item_count=1, serialized_bytes=0).failure
            is not None
        ):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        try:
            row = self._connection.execute(
                "SELECT scan_id FROM inventory_scans WHERE adapter_id=? AND connection_id=? "
                "AND library_id=? ORDER BY started_at DESC,scan_id DESC LIMIT 1",
                (connection.adapter.value, connection.id.value, library.value),
            ).fetchone()
            if row is None:
                return RepositoryResult(RepositoryOutcome.NOT_FOUND)
            scan_id = ScanId(str(row[0]))
            return RepositoryResult(
                RepositoryOutcome.ACCEPTED,
                self._inspection_for(
                    scan_id,
                    connection.adapter.value,
                    connection.id.value,
                    library.value,
                    context,
                ),
            )
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def inspect_scan(
        self,
        scan_id: ScanId,
        context: OperationContext,
    ) -> RepositoryResult[InventoryInspection]:
        if type(scan_id) is not ScanId or type(context) is not OperationContext:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        if (
            context.preflight_result(item_count=1, serialized_bytes=0).failure
            is not None
        ):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        try:
            row = self._connection.execute(
                "SELECT adapter_id,connection_id,library_id FROM inventory_scans "
                "WHERE scan_id=?",
                (scan_id.value,),
            ).fetchone()
            if row is None:
                return RepositoryResult(RepositoryOutcome.NOT_FOUND)
            return RepositoryResult(
                RepositoryOutcome.ACCEPTED,
                self._inspection_for(
                    scan_id, str(row[0]), str(row[1]), str(row[2]), context
                ),
            )
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def _claim_is_live(self, claim: PollClaim, now: datetime) -> bool:
        now = max(now, self._now())
        row = self._connection.execute(
            "SELECT state,attempt,fence,lease_owner,lease_expires_at,"
            "cancellation_requested,updated_at FROM inventory_polls WHERE poll_id=?",
            (claim.poll_id,),
        ).fetchone()
        return bool(
            row is not None
            and row[0] == "running"
            and int(row[1]) == claim.attempt
            and int(row[2]) == claim.fence
            and row[3] == claim.worker_id
            and not bool(row[5])
            and _parse_timestamp(str(row[4])) > now
            and _parse_timestamp(str(row[6])) <= now
        )

    def _scan_claim_is_live(
        self, scan_id: str, claim: PollClaim, now: datetime
    ) -> bool:
        linked = self._connection.execute(
            "SELECT 1 FROM inventory_scan_poll_claims WHERE scan_id=? AND poll_id=? "
            "AND attempt=? AND fence=?",
            (scan_id, claim.poll_id, claim.attempt, claim.fence),
        ).fetchone()
        return linked is not None and self._claim_is_live(claim, now)

    def _current(
        self, connection: LibraryConnection, library: LibraryId
    ) -> tuple[InventorySnapshotV2, ...]:
        return self._current_values(
            connection.adapter.value, connection.id.value, library.value
        )

    def _current_values(
        self, adapter: str, connection: str, library: str
    ) -> tuple[InventorySnapshotV2, ...]:
        rows = self._connection.execute(
            "SELECT c.external_item,s.snapshot_id,s.fingerprint_digest,"
            "s.canonical_observation,s.observed_at "
            "FROM current_inventory_v2 c JOIN inventory_v2_snapshots s USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE c.adapter_id=? AND c.connection_id=? AND c.library_id=? "
            "ORDER BY c.external_item",
            (adapter, connection, library),
        ).fetchall()
        values: list[InventorySnapshotV2] = []
        for external_item, snapshot_id, digest, canonical, observed_at in rows:
            observation = InventoryObservation.from_json(
                str(canonical), _parse_timestamp(str(observed_at))
            )
            snapshot = InventorySnapshotV2.create(observation)
            if (
                snapshot.id != SnapshotId(str(snapshot_id))
                or snapshot.fingerprint.digest != digest
            ):
                raise ValueError("stored inventory snapshot identity is inconsistent")
            values.append(
                InventorySnapshotV2.create(
                    observation,
                    file_bindings=tuple(
                        value.binding
                        for value in validated_current_inventory_bindings(
                            self._connection, snapshot
                        )
                        if not self._binding_configuration_digests
                        or value.binding.mount_configuration_digest
                        in self._binding_configuration_digests
                    ),
                )
            )
        return tuple(values)

    def _current_stats(
        self, adapter: str, connection: str, library: str
    ) -> tuple[int, int]:
        row = self._connection.execute(
            "SELECT COUNT(*),COALESCE(SUM(length(CAST(s.canonical_observation AS BLOB))),0) "
            "FROM current_inventory_v2 c JOIN inventory_v2_snapshots s USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE c.adapter_id=? AND c.connection_id=? AND c.library_id=?",
            (adapter, connection, library),
        ).fetchone()
        return int(row[0]), int(row[1])

    def _persist_or_reuse(self, snapshot: InventorySnapshotV2) -> SnapshotId:
        target = snapshot.observation.target
        identity = (
            target.adapter.value,
            target.connection.value,
            target.library.value,
            target.external_item.value,
        )
        row = self._connection.execute(
            "SELECT snapshot_id,canonical_observation FROM inventory_v2_snapshots "
            "WHERE adapter_id=? AND connection_id=? AND library_id=? AND external_item=? "
            "AND fingerprint_schema=? AND fingerprint_digest=?",
            (*identity, snapshot.fingerprint.schema.value, snapshot.fingerprint.digest),
        ).fetchone()
        if row is not None:
            if row[1] != snapshot.observation.canonical:
                raise ValueError("inventory fingerprint collision")
            persisted_id = SnapshotId(str(row[0]))
        else:
            self._connection.execute(
                "INSERT INTO inventory_v2_snapshots(adapter_id,connection_id,library_id,"
                "external_item,snapshot_id,fingerprint_algorithm,fingerprint_schema,"
                "fingerprint_digest,canonical_observation,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    *identity,
                    snapshot.id.value,
                    snapshot.fingerprint.algorithm,
                    snapshot.fingerprint.schema.value,
                    snapshot.fingerprint.digest,
                    snapshot.observation.canonical,
                    _format_timestamp(snapshot.observation.observed_at),
                ),
            )
            persisted_id = snapshot.id
        locator_values = (
            *identity,
            persisted_id.value,
            opaque_target_reference(snapshot.observation.target),
            opaque_snapshot_reference(snapshot),
            snapshot.observation.media_type.value,
            snapshot.observation.lifecycle.value,
        )
        indexed = self._connection.execute(
            "SELECT target_reference,snapshot_reference,media_type,lifecycle FROM "
            "inventory_snapshot_locator_index WHERE adapter_id=? AND connection_id=? "
            "AND library_id=? AND external_item=? AND snapshot_id=?",
            (*identity, persisted_id.value),
        ).fetchone()
        if indexed is None:
            self._connection.execute(
                "INSERT INTO inventory_snapshot_locator_index(adapter_id,connection_id,"
                "library_id,external_item,snapshot_id,target_reference,snapshot_reference,"
                "media_type,lifecycle) VALUES (?,?,?,?,?,?,?,?,?)",
                locator_values,
            )
        elif tuple(map(str, indexed)) != locator_values[5:]:
            raise ValueError("inventory snapshot locator collision")
        binding_digests: list[tuple[int, str]] = []
        for binding in snapshot.file_bindings:
            binding_digest = self._binding_digest(snapshot, persisted_id, binding)
            binding_digests.append((binding.file_index, binding_digest))
            existing = self._connection.execute(
                "SELECT file_identity,media_kind,mount_name,relative_path,binding_digest,"
                "target_reference,snapshot_reference "
                "FROM inventory_snapshot_file_bindings WHERE adapter_id=? AND "
                "connection_id=? AND library_id=? AND external_item=? AND snapshot_id=? "
                "AND file_index=? AND mount_configuration_digest=?",
                (
                    *identity,
                    persisted_id.value,
                    binding.file_index,
                    binding.mount_configuration_digest,
                ),
            ).fetchone()
            expected = (
                binding.file_identity,
                binding.media_kind,
                binding.reference.mount,
                binding.reference.relative_path,
                binding_digest,
                opaque_target_reference(snapshot.observation.target),
                opaque_snapshot_reference(snapshot),
            )
            if existing is not None:
                if tuple(existing) != expected:
                    raise ValueError("inventory file binding identity collision")
                continue
            self._connection.execute(
                "INSERT INTO inventory_snapshot_file_bindings(adapter_id,connection_id,"
                "library_id,external_item,snapshot_id,target_reference,snapshot_reference,"
                "file_index,file_identity,media_kind,"
                "mount_configuration_digest,mount_name,relative_path,binding_digest) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    *identity,
                    persisted_id.value,
                    opaque_target_reference(snapshot.observation.target),
                    opaque_snapshot_reference(snapshot),
                    binding.file_index,
                    binding.file_identity,
                    binding.media_kind,
                    binding.mount_configuration_digest,
                    binding.reference.mount,
                    binding.reference.relative_path,
                    binding_digest,
                ),
            )
        self._persist_binding_set(snapshot, persisted_id, tuple(binding_digests))
        return persisted_id

    @staticmethod
    def _binding_digest(
        snapshot: InventorySnapshotV2,
        persisted_id: SnapshotId,
        binding: InventoryFileBinding,
    ) -> str:
        return inventory_file_binding_digest(snapshot, persisted_id, binding)

    def _persist_binding_set(
        self,
        snapshot: InventorySnapshotV2,
        persisted_id: SnapshotId,
        members: tuple[tuple[int, str], ...],
    ) -> str:
        target = snapshot.observation.target
        identity = (
            target.adapter.value,
            target.connection.value,
            target.library.value,
            target.external_item.value,
        )
        value = {
            "bindings": [digest for _, digest in members],
            "schema": "inventory-snapshot-binding-set-v1",
            "snapshot": persisted_id.value,
            "target": list(identity),
        }
        set_digest = hashlib.sha256(
            canonical_json(value).encode("utf-8")
        ).hexdigest()
        existing = self._connection.execute(
            "SELECT adapter_id,connection_id,library_id,external_item,snapshot_id,"
            "binding_count FROM inventory_snapshot_binding_sets WHERE binding_set_digest=?",
            (set_digest,),
        ).fetchone()
        expected = (*identity, persisted_id.value, len(members))
        if existing is None:
            self._connection.execute(
                "INSERT INTO inventory_snapshot_binding_sets(binding_set_digest,adapter_id,"
                "connection_id,library_id,external_item,snapshot_id,binding_count) "
                "VALUES (?,?,?,?,?,?,?)",
                (set_digest, *expected),
            )
            for file_index, binding_digest in members:
                self._connection.execute(
                    "INSERT INTO inventory_snapshot_binding_set_members(binding_set_digest,"
                    "binding_digest,file_index) VALUES (?,?,?)",
                    (set_digest, binding_digest, file_index),
                )
        elif tuple(existing) != expected:
            raise ValueError("inventory binding set identity collision")
        else:
            observed_members = self._connection.execute(
                "SELECT file_index,binding_digest FROM inventory_snapshot_binding_set_members "
                "WHERE binding_set_digest=? ORDER BY file_index",
                (set_digest,),
            ).fetchall()
            if tuple((int(row[0]), str(row[1])) for row in observed_members) != members:
                raise ValueError("inventory binding set membership is inconsistent")
        return set_digest

    def _set_current_binding_set(
        self, snapshot: InventorySnapshotV2, persisted_id: SnapshotId
    ) -> None:
        members = tuple(
            (
                binding.file_index,
                self._binding_digest(snapshot, persisted_id, binding),
            )
            for binding in snapshot.file_bindings
        )
        set_digest = self._persist_binding_set(snapshot, persisted_id, members)
        target = snapshot.observation.target
        self._connection.execute(
            "INSERT INTO current_inventory_snapshot_binding_sets(adapter_id,connection_id,"
            "library_id,external_item,snapshot_id,binding_set_digest) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(adapter_id,connection_id,library_id,external_item) DO UPDATE SET "
            "snapshot_id=excluded.snapshot_id,binding_set_digest=excluded.binding_set_digest",
            (
                target.adapter.value,
                target.connection.value,
                target.library.value,
                target.external_item.value,
                persisted_id.value,
                set_digest,
            ),
        )

    def _inspection_for(
        self,
        scan_id: ScanId,
        adapter: str,
        connection: str,
        library: str,
        context: OperationContext,
        *,
        terminalizing: bool = False,
    ) -> InventoryInspection:
        scan = self._connection.execute(
            "SELECT state,failure_kind,page_count,response_bytes,max_items,max_bytes "
            "FROM inventory_scans "
            "WHERE scan_id=?",
            (scan_id.value,),
        ).fetchone()
        if scan is None:
            raise ValueError("inventory scan disappeared")
        if str(scan[0]) == ScanState.COMPLETE.value:
            counts = self._connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(CAST(s.canonical_observation AS BLOB))),0),"
                "COALESCE(SUM(CASE WHEN instr(s.canonical_observation, "
                '\'"lifecycle":"removed"\')>0 THEN 1 ELSE 0 END),0) '
                "FROM inventory_scan_items i JOIN inventory_v2_snapshots s USING "
                "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
                "WHERE i.scan_id=?",
                (scan_id.value,),
            ).fetchone()
        else:
            counts = self._connection.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(CAST(s.canonical_observation AS BLOB))),0),"
                "COALESCE(SUM(CASE WHEN instr(s.canonical_observation, "
                '\'"lifecycle":"removed"\')>0 THEN 1 ELSE 0 END),0) '
                "FROM current_inventory_v2 c JOIN inventory_v2_snapshots s USING "
                "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
                "WHERE c.adapter_id=? AND c.connection_id=? AND c.library_id=?",
                (adapter, connection, library),
            ).fetchone()
        item_count, serialized_bytes, removed = map(int, counts)
        if item_count > int(scan[4]) or serialized_bytes > int(scan[5]):
            raise _InspectionAborted
        if terminalizing:
            bounded = (
                item_count <= context.resources.max_items
                and serialized_bytes <= context.resources.max_bytes
            )
        else:
            bounded = (
                context.preflight_result(
                    item_count=item_count, serialized_bytes=serialized_bytes
                ).failure
                is None
            )
        if not bounded:
            raise _InspectionAborted
        active = item_count - removed
        history = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM inventory_v2_snapshots WHERE adapter_id=? "
                "AND connection_id=? AND library_id=?",
                (adapter, connection, library),
            ).fetchone()[0]
        )
        moved = self._movement_count(scan_id, adapter, connection, library)
        return InventoryInspection(
            scan_id,
            ScanState(str(scan[0])),
            None if scan[1] is None else str(scan[1]),
            item_count,
            active,
            removed,
            history,
            int(scan[2]),
            int(scan[3]),
            moved,
            self._selectors(adapter, connection, library),
        )

    def _selectors(
        self, adapter: str, connection: str, library: str
    ) -> tuple[InventorySelector, ...]:
        if not self._binding_configuration_digests:
            return ()
        snapshots = self._current_values(adapter, connection, library)
        selectors = [
            InventorySelector(
                opaque_target_reference(snapshot.observation.target),
                opaque_snapshot_reference(snapshot),
                binding.file_index,
                binding.media_kind,
            )
            for snapshot in snapshots
            if snapshot.observation.lifecycle.value == "active"
            for binding in snapshot.file_bindings
        ]
        return tuple(
            sorted(
                selectors,
                key=lambda value: (
                    value.target_reference,
                    value.snapshot_reference,
                    value.file_index,
                ),
            )
        )

    def _movement_count(
        self, scan_id: ScanId, adapter: str, connection: str, library: str
    ) -> int:
        cohort = self._connection.execute(
            "SELECT cohort_id,cohort_size FROM inventory_scans WHERE scan_id=?",
            (scan_id.value,),
        ).fetchone()
        if cohort is None:
            return 0
        cohort_id, cohort_size = str(cohort[0]), int(cohort[1])
        scans = self._connection.execute(
            "SELECT COUNT(*),COUNT(DISTINCT library_id),"
            "COALESCE(SUM(CASE WHEN state='complete' THEN 1 ELSE 0 END),0) "
            "FROM inventory_scans WHERE adapter_id=? AND connection_id=? AND cohort_id=?",
            (adapter, connection, cohort_id),
        ).fetchone()
        if (
            int(scans[0]) != cohort_size
            or int(scans[1]) != cohort_size
            or int(scans[2]) != cohort_size
        ):
            return 0
        return int(
            self._connection.execute(
                "SELECT COUNT(DISTINCT source.external_item) FROM inventory_scan_items source "
                "JOIN inventory_scans source_scan ON source_scan.scan_id=source.scan_id "
                "JOIN inventory_v2_snapshots source_snapshot USING "
                "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
                "WHERE source_scan.adapter_id=? AND source_scan.connection_id=? "
                "AND source_scan.cohort_id=? AND source.library_id=? "
                "AND instr(source_snapshot.canonical_observation, "
                '\'"lifecycle":"removed"\')>0 AND EXISTS ('
                "SELECT 1 FROM inventory_scan_items destination "
                "JOIN inventory_scans destination_scan ON "
                "destination_scan.scan_id=destination.scan_id "
                "JOIN inventory_v2_snapshots destination_snapshot USING "
                "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
                "WHERE destination_scan.adapter_id=source_scan.adapter_id "
                "AND destination_scan.connection_id=source_scan.connection_id "
                "AND destination_scan.cohort_id=source_scan.cohort_id "
                "AND destination.library_id<>source.library_id "
                "AND destination.external_item=source.external_item "
                "AND instr(destination_snapshot.canonical_observation, "
                '\'"lifecycle":"removed"\')=0)',
                (adapter, connection, cohort_id, library),
            ).fetchone()[0]
        )


class _PublicationAborted(Exception):
    """Force transaction rollback when publication becomes unsafe."""


class _PublicationRejected(Exception):
    """Force rollback when an optimistic cohort precondition changed."""


class _InspectionAborted(ValueError):
    """Stop before a bounded inspection materializes an unsafe result."""


_OWNER = re.compile(r"^pid-(0|[1-9][0-9]*)-start-([0-9a-f]{16})$")


def _default_owner_id() -> str:
    pid = os.getpid()
    token = _process_start_token(pid)
    if token is None:
        raise ValueError("inventory process identity unavailable")
    return f"pid-{pid}-start-{hashlib.sha256(token.encode()).hexdigest()[:16]}"


def _process_start_token(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        after_name = stat.rsplit(")", 1)[1].split()
        return "proc-" + after_name[19]
    except (OSError, IndexError):
        try:
            completed = subprocess.run(
                ["ps", "-o", "lstart=", "-p", str(pid)],
                check=False,
                capture_output=True,
                text=True,
                timeout=1,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        value = completed.stdout.strip()
        return "ps-" + value if completed.returncode == 0 and value else None


def _current_digest(snapshots: tuple[InventorySnapshotV2, ...]) -> str:
    value = [
        {
            "external_item": item.observation.target.external_item.value,
            "fingerprint": item.fingerprint.digest,
            "file_bindings": [
                {
                    "file_identity": binding.file_identity,
                    "file_index": binding.file_index,
                    "media_kind": binding.media_kind,
                    "mount": binding.reference.mount,
                    "mount_configuration_digest": binding.mount_configuration_digest,
                    "relative_path": binding.reference.relative_path,
                }
                for binding in item.file_bindings
            ],
        }
        for item in sorted(
            snapshots, key=lambda item: item.observation.target.external_item.value
        )
    ]
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _valid_time(value: object) -> bool:
    return (
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


def _owner_alive(owner_id: str) -> bool:
    match = _OWNER.fullmatch(owner_id)
    if match is None:
        return False
    pid = int(match.group(1))
    token = _process_start_token(pid)
    return token is not None and hashlib.sha256(token.encode()).hexdigest()[
        :16
    ] == match.group(2)


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise sqlite3.OperationalError("nested inventory v2 transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise


__all__ = ["SQLiteInventoryV2Repository"]
