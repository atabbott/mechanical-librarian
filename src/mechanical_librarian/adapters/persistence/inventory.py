"""SQLite persistence for canonical F03 inventory snapshots."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from mechanical_librarian.application.job_heartbeat import RepositoryOutcome, RepositoryResult
from mechanical_librarian.application.ports import (
    LibraryListRequest,
    LibraryReadRequest,
    Page,
    SnapshotStoreRequest,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    AdapterVersion,
    ConnectionId,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    ServiceVersion,
    SnapshotId,
    TargetId,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    AdapterRelease,
    FieldName,
    ItemLifecycle,
    ItemSnapshot,
    MediaType,
    MetadataField,
    NormalizedMetadata,
    ObservationProvenance,
    RawObservation,
    ServiceRelease,
)
class SQLiteInventoryRepository:
    """Persist immutable history and publish its current pointer atomically."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        after_history_insert: Callable[[], None] | None = None,
    ) -> None:
        self._connection = connection
        self._after_history_insert = after_history_insert

    def store(self, request: SnapshotStoreRequest) -> RepositoryResult[ItemSnapshot]:
        if type(request) is not SnapshotStoreRequest:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        snapshot = request.snapshot
        size = len(snapshot.canonical_input.serialized.encode("utf-8"))
        if request.context.preflight_result(item_count=1, serialized_bytes=size).failure:
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        if not _supported_snapshot(snapshot):
            return RepositoryResult(RepositoryOutcome.REJECTED)
        target_values = _target_values(snapshot.target)
        try:
            with _transaction(self._connection):
                history_exists = self._history_exists(snapshot.target)
                pointer_exists = self._current_pointer_exists(snapshot.target)
                current = self._read_current_row(snapshot.target)
                if current is None and (history_exists or pointer_exists):
                    return RepositoryResult(RepositoryOutcome.CORRUPT)

                bound_row = self._row_for_idempotency(
                    snapshot.target,
                    request.idempotency_key.operation,
                    request.idempotency_key.digest,
                )
                if bound_row is not None:
                    bound = _snapshot_from_row(target_values, bound_row)
                    if bound != snapshot:
                        return RepositoryResult(RepositoryOutcome.CORRUPT)

                existing_row = self._row_for_snapshot(
                    snapshot.target, snapshot.id
                )
                if existing_row is not None:
                    existing = _snapshot_from_row(target_values, existing_row)
                    if (
                        existing != snapshot
                        or existing_row[1] != request.idempotency_key.operation
                        or existing_row[2] != request.idempotency_key.digest
                    ):
                        return RepositoryResult(RepositoryOutcome.CORRUPT)
                    if current != snapshot:
                        return RepositoryResult(RepositoryOutcome.REJECTED)
                    return RepositoryResult(RepositoryOutcome.REUSED, existing)

                if current is None and request.expected_previous is not None:
                    return RepositoryResult(RepositoryOutcome.REJECTED)
                if current is not None:
                    if request.expected_previous is None or (
                        current.fingerprint != request.expected_previous
                    ):
                        return RepositoryResult(RepositoryOutcome.REJECTED)
                    if current.fingerprint == snapshot.fingerprint:
                        return RepositoryResult(RepositoryOutcome.REUSED, current)
                    if self._fingerprint_exists_except(
                        snapshot.target, snapshot.fingerprint, current.id
                    ):
                        return RepositoryResult(RepositoryOutcome.REJECTED)

                self._connection.execute(
                    "INSERT INTO inventory_snapshots("
                    "adapter_id, connection_id, library_id, external_item, snapshot_id, "
                    "idempotency_operation, idempotency_digest, fingerprint_algorithm, "
                    "fingerprint_schema, fingerprint_digest, canonical_input, observed_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        *target_values,
                        snapshot.id.value,
                        request.idempotency_key.operation,
                        request.idempotency_key.digest,
                        snapshot.fingerprint.algorithm,
                        snapshot.fingerprint.schema.value,
                        snapshot.fingerprint.digest,
                        snapshot.canonical_input.serialized,
                        _format_timestamp(snapshot.provenance.observed_at),
                    ),
                )
                if self._after_history_insert is not None:
                    self._after_history_insert()
                if current is None:
                    self._connection.execute(
                        "INSERT INTO current_inventory_observations("
                        "adapter_id, connection_id, library_id, external_item, snapshot_id"
                        ") VALUES (?, ?, ?, ?, ?)",
                        (*target_values, snapshot.id.value),
                    )
                else:
                    self._connection.execute(
                        "UPDATE current_inventory_observations SET snapshot_id = ? "
                        "WHERE adapter_id = ? AND connection_id = ? AND library_id = ? "
                        "AND external_item = ?",
                        (snapshot.id.value, *target_values),
                    )
                readback = self._read_current_row(snapshot.target)
                if readback != snapshot:
                    raise ValueError("inventory readback mismatch")
            return RepositoryResult(RepositoryOutcome.ACCEPTED, snapshot)
        except (sqlite3.Error, OSError):
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        except (KeyError, RecursionError, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.CORRUPT)

    def read(self, request: LibraryReadRequest) -> RepositoryResult[ItemSnapshot]:
        if type(request) is not LibraryReadRequest:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        if request.context.preflight_result(item_count=1, serialized_bytes=0).failure:
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        try:
            snapshot = self._read_current_row(request.target)
            if snapshot is None:
                if self._history_exists(request.target) or self._current_pointer_exists(
                    request.target
                ):
                    return RepositoryResult(RepositoryOutcome.CORRUPT)
                return RepositoryResult(RepositoryOutcome.NOT_FOUND)
            size = len(snapshot.canonical_input.serialized.encode("utf-8"))
            if request.context.preflight_result(item_count=1, serialized_bytes=size).failure:
                return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
            return RepositoryResult(RepositoryOutcome.ACCEPTED, snapshot)
        except sqlite3.Error:
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        except (KeyError, RecursionError, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.CORRUPT)

    def list(self, request: LibraryListRequest) -> RepositoryResult[Page[ItemSnapshot]]:
        if type(request) is not LibraryListRequest or request.page.cursor is not None:
            return RepositoryResult(RepositoryOutcome.REJECTED)
        if request.context.preflight_result(item_count=0, serialized_bytes=0).failure:
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        try:
            orphan = self._connection.execute(
                "SELECT 1 FROM inventory_snapshots s LEFT JOIN "
                "current_inventory_observations c ON "
                "s.adapter_id = c.adapter_id AND s.connection_id = c.connection_id AND "
                "s.library_id = c.library_id AND s.external_item = c.external_item LEFT JOIN "
                "inventory_snapshots current_s ON current_s.adapter_id = c.adapter_id AND "
                "current_s.connection_id = c.connection_id AND current_s.library_id = c.library_id "
                "AND current_s.external_item = c.external_item AND "
                "current_s.snapshot_id = c.snapshot_id WHERE s.adapter_id = ? AND "
                "s.connection_id = ? AND (c.snapshot_id IS NULL OR current_s.snapshot_id IS NULL) "
                "LIMIT 1",
                (request.connection.adapter.value, request.connection.id.value),
            ).fetchone()
            if orphan is not None:
                return RepositoryResult(RepositoryOutcome.CORRUPT)
            dangling_pointer = self._connection.execute(
                "SELECT 1 FROM current_inventory_observations c LEFT JOIN "
                "inventory_snapshots s ON s.adapter_id = c.adapter_id AND "
                "s.connection_id = c.connection_id AND s.library_id = c.library_id AND "
                "s.external_item = c.external_item AND s.snapshot_id = c.snapshot_id "
                "WHERE c.adapter_id = ? AND c.connection_id = ? AND "
                "s.snapshot_id IS NULL LIMIT 1",
                (request.connection.adapter.value, request.connection.id.value),
            ).fetchone()
            if dangling_pointer is not None:
                return RepositoryResult(RepositoryOutcome.CORRUPT)
            rows = self._connection.execute(
                "SELECT s.adapter_id, s.connection_id, s.library_id, s.external_item, "
                "s.snapshot_id, s.idempotency_operation, s.idempotency_digest, "
                "s.fingerprint_algorithm, s.fingerprint_schema, s.fingerprint_digest, "
                "s.canonical_input, s.observed_at "
                "FROM current_inventory_observations c JOIN inventory_snapshots s ON "
                "s.adapter_id = c.adapter_id AND s.connection_id = c.connection_id AND "
                "s.library_id = c.library_id AND s.external_item = c.external_item AND "
                "s.snapshot_id = c.snapshot_id WHERE s.adapter_id = ? AND s.connection_id = ? "
                "ORDER BY s.library_id, s.external_item LIMIT ?",
                (
                    request.connection.adapter.value,
                    request.connection.id.value,
                    request.page.limit + 1,
                ),
            ).fetchall()
            if len(rows) > request.page.limit:
                return RepositoryResult(RepositoryOutcome.REJECTED)
            snapshots = tuple(_snapshot_from_selected_row(row) for row in rows)
            size = sum(len(item.canonical_input.serialized.encode("utf-8")) for item in snapshots)
            if request.context.preflight_result(
                item_count=len(snapshots), serialized_bytes=size
            ).failure:
                return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
            return RepositoryResult(RepositoryOutcome.ACCEPTED, Page(snapshots, None))
        except sqlite3.Error:
            return RepositoryResult(RepositoryOutcome.UNAVAILABLE)
        except (KeyError, RecursionError, TypeError, ValueError):
            return RepositoryResult(RepositoryOutcome.CORRUPT)

    def _read_current_row(self, target: TargetId) -> ItemSnapshot | None:
        target_values = _target_values(target)
        row = self._connection.execute(
            "SELECT s.snapshot_id, s.idempotency_operation, s.idempotency_digest, "
            "s.fingerprint_algorithm, s.fingerprint_schema, s.fingerprint_digest, "
            "s.canonical_input, s.observed_at "
            "FROM current_inventory_observations c JOIN inventory_snapshots s ON "
            "s.adapter_id = c.adapter_id AND s.connection_id = c.connection_id AND "
            "s.library_id = c.library_id AND s.external_item = c.external_item AND "
            "s.snapshot_id = c.snapshot_id WHERE c.adapter_id = ? AND c.connection_id = ? "
            "AND c.library_id = ? AND c.external_item = ?",
            target_values,
        ).fetchone()
        return None if row is None else _snapshot_from_row(target_values, row)

    def _history_exists(self, target: TargetId) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM inventory_snapshots WHERE adapter_id = ? AND "
            "connection_id = ? AND library_id = ? AND external_item = ? LIMIT 1",
            _target_values(target),
        ).fetchone() is not None

    def _current_pointer_exists(self, target: TargetId) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM current_inventory_observations WHERE adapter_id = ? AND "
            "connection_id = ? AND library_id = ? AND external_item = ? LIMIT 1",
            _target_values(target),
        ).fetchone() is not None

    def _row_for_snapshot(
        self, target: TargetId, snapshot: SnapshotId
    ) -> tuple[object, ...] | None:
        return self._connection.execute(
            "SELECT snapshot_id, idempotency_operation, idempotency_digest, "
            "fingerprint_algorithm, fingerprint_schema, fingerprint_digest, "
            "canonical_input, observed_at FROM inventory_snapshots "
            "WHERE adapter_id = ? AND connection_id = ? AND library_id = ? "
            "AND external_item = ? AND snapshot_id = ?",
            (*_target_values(target), snapshot.value),
        ).fetchone()

    def _row_for_idempotency(
        self, target: TargetId, operation: str, digest: str
    ) -> tuple[object, ...] | None:
        return self._connection.execute(
            "SELECT snapshot_id, idempotency_operation, idempotency_digest, "
            "fingerprint_algorithm, fingerprint_schema, fingerprint_digest, "
            "canonical_input, observed_at FROM inventory_snapshots "
            "WHERE adapter_id = ? AND connection_id = ? AND library_id = ? "
            "AND external_item = ? AND idempotency_operation = ? "
            "AND idempotency_digest = ?",
            (*_target_values(target), operation, digest),
        ).fetchone()

    def _fingerprint_exists_except(
        self, target: TargetId, fingerprint: Fingerprint, snapshot: SnapshotId
    ) -> bool:
        return self._connection.execute(
            "SELECT 1 FROM inventory_snapshots WHERE adapter_id = ? AND "
            "connection_id = ? AND library_id = ? AND external_item = ? AND "
            "fingerprint_algorithm = ? AND fingerprint_schema = ? AND "
            "fingerprint_digest = ? AND snapshot_id != ? LIMIT 1",
            (
                *_target_values(target),
                fingerprint.algorithm,
                fingerprint.schema.value,
                fingerprint.digest,
                snapshot.value,
            ),
        ).fetchone() is not None


def _snapshot_from_selected_row(row: tuple[object, ...]) -> ItemSnapshot:
    return _snapshot_from_row(tuple(str(value) for value in row[:4]), row[4:])


def _snapshot_from_row(
    target_values: tuple[str, str, str, str], row: tuple[object, ...]
) -> ItemSnapshot:
    snapshot_id, _operation, _key_digest, algorithm, schema, digest, serialized, observed = row
    if not all(type(value) is str for value in row):
        raise ValueError("inventory row is malformed")
    value = json.loads(str(serialized))
    target_value = value["target"]
    if (
        not isinstance(value, dict)
        or not isinstance(target_value, dict)
        or tuple(target_value[name] for name in ("adapter", "connection", "library", "external_item"))
        != target_values
    ):
        raise ValueError("inventory target is inconsistent")
    target = TargetId(
        AdapterId(target_values[0]),
        ConnectionId(target_values[1]),
        LibraryId(target_values[2]),
        ExternalItemId(target_values[3]),
    )
    capabilities_value = value["capabilities"]
    metadata_value = value["metadata"]
    raw_fingerprint = value["raw_observation_fingerprint"]
    adapter_release = value["adapter_release"]
    service_release = value["service_release"]
    if (
        not isinstance(capabilities_value, dict)
        or not isinstance(metadata_value, list)
        or not isinstance(raw_fingerprint, dict)
        or not isinstance(adapter_release, dict)
        or not isinstance(service_release, dict)
        or value["files"] != []
        or value["cover"] is not None
        or value["concurrency_identity"] is not None
    ):
        raise ValueError("inventory snapshot shape is unsupported")
    capabilities = AdapterCapabilities(
        inventory_read=capabilities_value["inventory_read"],
        metadata_read=capabilities_value["metadata_read"],
        media_file_read=capabilities_value["media_file_read"],
        notifications=capabilities_value["notifications"],
        pagination=capabilities_value["pagination"],
        concurrency_tokens=capabilities_value["concurrency_tokens"],
        cover_read=capabilities_value["cover_read"],
        writable_fields=frozenset(FieldName(item) for item in capabilities_value["writable_fields"]),
        cover_write=capabilities_value["cover_write"],
        supported_media_types=frozenset(
            MediaType(item) for item in capabilities_value["supported_media_types"]
        ),
    )
    metadata = NormalizedMetadata(
        tuple(
            MetadataField(FieldName(item["name"]), tuple(item["values"]))
            for item in metadata_value
        )
    )
    observed_at = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
    snapshot = ItemSnapshot.create(
        id=SnapshotId(str(snapshot_id)),
        target=target,
        media_type=MediaType(value["media_type"]),
        lifecycle=ItemLifecycle(value["lifecycle"]),
        metadata=metadata,
        files=(),
        cover=None,
        provenance=ObservationProvenance(
            AdapterRelease(
                AdapterId(adapter_release["adapter"]),
                AdapterVersion(adapter_release["version"]),
            ),
            observed_at,
            RawObservation(
                AdapterId(adapter_release["adapter"]),
                Fingerprint(
                    raw_fingerprint["algorithm"],
                    SchemaVersion(raw_fingerprint["schema"]),
                    raw_fingerprint["digest"],
                ),
            ),
        ),
        service_release=ServiceRelease(
            AdapterId(service_release["adapter"]),
            ServiceVersion(service_release["version"]),
        ),
        concurrency_token=None,
        capabilities=capabilities,
    )
    if (
        snapshot.fingerprint
        != Fingerprint(str(algorithm), SchemaVersion(str(schema)), str(digest))
        or snapshot.canonical_input.serialized != serialized
    ):
        raise ValueError("inventory fingerprint is inconsistent")
    return snapshot


def _supported_snapshot(snapshot: ItemSnapshot) -> bool:
    return (
        not snapshot.files
        and snapshot.cover is None
        and snapshot.concurrency_token is None
        and snapshot.provenance.raw_observation.artifact_reference is None
        and not snapshot.capabilities.write_supported
    )


def _target_values(target: TargetId) -> tuple[str, str, str, str]:
    return (
        target.adapter.value,
        target.connection.value,
        target.library.value,
        target.external_item.value,
    )


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise sqlite3.OperationalError("nested inventory transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise


__all__ = ["SQLiteInventoryRepository"]
