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
from mechanical_librarian.domain.inventory import ItemLifecycle, LibraryConnection
from mechanical_librarian.domain.inventory_v2 import (
    InventoryInspection,
    InventoryObservation,
    InventorySnapshotV2,
    ScanBounds,
    ScanStart,
    ScanState,
    ScanUsage,
)


class SQLiteInventoryV2Repository(InventoryScanRepository):
    """Keep immutable history and publish a complete scan in one transaction."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        owner_id: str | None = None,
        owner_alive: Callable[[str], bool] | None = None,
    ) -> None:
        self._connection = connection
        self._owner_id = _default_owner_id() if owner_id is None else owner_id
        self._owner_alive = _owner_alive if owner_alive is None else owner_alive
        if not self._owner_id or len(self._owner_id) > 64:
            raise ValueError("inventory scan owner is invalid")

    def recover_interrupted(
        self, finished_at: datetime, context: OperationContext
    ) -> RepositoryResult[int]:
        if not _valid_time(finished_at) or type(context) is not OperationContext:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            recovered = 0
            with _transaction(self._connection):
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
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        effective_cohort = scan_id.value if cohort_id is None else cohort_id
        try:
            with _transaction(self._connection):
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
    ) -> RepositoryResult[InventoryInspection]:
        if (
            type(scan_id) is not ScanId
            or state not in {ScanState.INCOMPLETE, ScanState.UNSTABLE}
            or type(failure_kind) is not str
            or type(usage) is not ScanUsage
            or not _valid_time(finished_at)
            or type(context) is not OperationContext
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                row = self._connection.execute(
                    "SELECT adapter_id,connection_id,library_id,state FROM inventory_scans "
                    "WHERE scan_id=?",
                    (scan_id.value,),
                ).fetchone()
                if row is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if row[3] != ScanState.RUNNING.value:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
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
                inspection = self._inspection_for(scan_id, row[0], row[1], row[2])
            return RepositoryResult(RepositoryOutcome.ACCEPTED, inspection)
        except sqlite3.IntegrityError:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

    def publish_complete(
        self,
        start: ScanStart,
        snapshots: tuple[InventorySnapshotV2, ...],
        usage: ScanUsage,
        finished_at: datetime,
        context: OperationContext,
    ) -> RepositoryResult[InventoryInspection]:
        if (
            type(start) is not ScanStart
            or type(snapshots) is not tuple
            or any(type(item) is not InventorySnapshotV2 for item in snapshots)
            or len({item.observation.target for item in snapshots}) != len(snapshots)
            or type(usage) is not ScanUsage
            or not _valid_time(finished_at)
            or type(context) is not OperationContext
        ):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        try:
            with _transaction(self._connection):
                row = self._connection.execute(
                    "SELECT adapter_id,connection_id,library_id,state,base_digest "
                    "FROM inventory_scans WHERE scan_id=?",
                    (start.scan_id.value,),
                ).fetchone()
                if row is None:
                    return RepositoryResult(RepositoryOutcome.NOT_FOUND)
                if row[3] != ScanState.RUNNING.value or row[4] != start.current_digest:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
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
                checkpoint = context.preflight_result(
                    item_count=max(1, len(snapshots)),
                    serialized_bytes=serialized_bytes,
                )
                if checkpoint.failure is not None:
                    return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

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
                    self._connection.execute(
                        "INSERT INTO inventory_scan_items(scan_id,adapter_id,connection_id,"
                        "library_id,external_item,snapshot_id) VALUES (?,?,?,?,?,?)",
                        (start.scan_id.value, *values),
                    )

                checkpoint = context.preflight_result(
                    item_count=max(1, len(snapshots)),
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
                    item_count=max(1, len(snapshots)),
                    serialized_bytes=serialized_bytes,
                )
                if checkpoint.failure is not None:
                    raise _PublicationAborted
                inspection = self._inspection_for(start.scan_id, row[0], row[1], row[2])
                checkpoint = context.preflight_result(
                    item_count=max(1, len(snapshots)),
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
                self._inspection_for(scan_id, str(row[0]), str(row[1]), str(row[2])),
            )
        except (sqlite3.Error, ValueError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)

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
            "SELECT s.snapshot_id,s.fingerprint_digest,s.canonical_observation,s.observed_at "
            "FROM current_inventory_v2 c JOIN inventory_v2_snapshots s USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE c.adapter_id=? AND c.connection_id=? AND c.library_id=? "
            "ORDER BY c.external_item",
            (adapter, connection, library),
        ).fetchall()
        values: list[InventorySnapshotV2] = []
        for snapshot_id, digest, canonical, observed_at in rows:
            observation = InventoryObservation.from_json(
                str(canonical), _parse_timestamp(str(observed_at))
            )
            snapshot = InventorySnapshotV2.create(observation)
            if (
                snapshot.id != SnapshotId(str(snapshot_id))
                or snapshot.fingerprint.digest != digest
            ):
                raise ValueError("stored inventory snapshot identity is inconsistent")
            values.append(snapshot)
        return tuple(values)

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
            return SnapshotId(str(row[0]))
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
        return snapshot.id

    def _inspection_for(
        self, scan_id: ScanId, adapter: str, connection: str, library: str
    ) -> InventoryInspection:
        scan = self._connection.execute(
            "SELECT state,failure_kind,page_count,response_bytes FROM inventory_scans "
            "WHERE scan_id=?",
            (scan_id.value,),
        ).fetchone()
        if scan is None:
            raise ValueError("inventory scan disappeared")
        if str(scan[0]) == ScanState.COMPLETE.value:
            counts = dict(
                self._connection.execute(
                    "SELECT s.canonical_observation,COUNT(*) FROM inventory_scan_items i "
                    "JOIN inventory_v2_snapshots s USING "
                    "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
                    "WHERE i.scan_id=? GROUP BY s.canonical_observation",
                    (scan_id.value,),
                ).fetchall()
            )
        else:
            counts = dict(
                self._connection.execute(
                    "SELECT s.canonical_observation,COUNT(*) FROM current_inventory_v2 c "
                    "JOIN inventory_v2_snapshots s USING "
                    "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
                    "WHERE c.adapter_id=? AND c.connection_id=? AND c.library_id=? "
                    "GROUP BY s.canonical_observation",
                    (adapter, connection, library),
                ).fetchall()
            )
        active = 0
        removed = 0
        for canonical, count in counts.items():
            if f'"lifecycle":"{ItemLifecycle.REMOVED.value}"' in str(canonical):
                removed += int(count)
            else:
                active += int(count)
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
            active + removed,
            active,
            removed,
            history,
            int(scan[2]),
            int(scan[3]),
            moved,
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
            "SELECT scan_id,library_id,state FROM inventory_scans WHERE adapter_id=? "
            "AND connection_id=? AND cohort_id=?",
            (adapter, connection, cohort_id),
        ).fetchall()
        if (
            len(scans) != cohort_size
            or len({str(row[1]) for row in scans}) != cohort_size
            or any(str(row[2]) != ScanState.COMPLETE.value for row in scans)
        ):
            return 0
        rows = self._connection.execute(
            "SELECT i.library_id,i.external_item,s.canonical_observation FROM "
            "inventory_scan_items i JOIN inventory_scans r USING (scan_id) "
            "JOIN inventory_v2_snapshots s USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE r.adapter_id=? AND r.connection_id=? AND r.cohort_id=?",
            (adapter, connection, cohort_id),
        ).fetchall()
        removed = {
            str(external)
            for row_library, external, canonical in rows
            if row_library == library
            and f'"lifecycle":"{ItemLifecycle.REMOVED.value}"' in str(canonical)
        }
        active_elsewhere = {
            str(external)
            for row_library, external, canonical in rows
            if row_library != library
            and f'"lifecycle":"{ItemLifecycle.REMOVED.value}"' not in str(canonical)
        }
        return len(removed & active_elsewhere)


class _PublicationAborted(Exception):
    """Force transaction rollback when publication becomes unsafe."""


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
