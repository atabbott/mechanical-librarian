#!/usr/bin/env python3
"""Prepare disposable F07b qualification state using the installed package."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mechanical_librarian.adapters.persistence.inventory_v2 import (
    SQLiteInventoryV2Repository,
)
from mechanical_librarian.application.ports import (
    CancellationId,
    CancellationProbe,
    Deadline,
    OperationContext,
    PortResult,
    ResourceBounds,
    TimeProbe,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryConnectionVerification,
    ReconcileInventory,
)
from mechanical_librarian.domain.identifiers import (
    AdapterId,
    AdapterVersion,
    ExternalItemId,
    LibraryId,
    SchemaVersion,
    ServiceVersion,
    TargetId,
    canonical_json,
)
from mechanical_librarian.domain.inventory import (
    AdapterCapabilities,
    FileReference,
    ItemLifecycle,
    LibraryConnection,
    MediaType,
)
from mechanical_librarian.domain.inventory_v2 import (
    FieldObservation,
    InventoryFileBinding,
    InventoryObservation,
    InventoryPass,
    InventorySnapshotV2,
    ObservationField,
    ObservationState,
    ScanBounds,
    ScanUsage,
    opaque_snapshot_reference,
    opaque_target_reference,
)
from mechanical_librarian.infrastructure.configuration import load_configuration_file
from mechanical_librarian.infrastructure.persistence.sqlite import connect, migrate


def _mount_digest(mount: object) -> str:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--selector-map", required=True, type=Path)
    arguments = parser.parse_args()
    configuration = load_configuration_file(arguments.config)
    if len(configuration.connections) != 1 or len(configuration.media_mounts) != 1:
        return 2
    configured = configuration.connections[0]
    mount = configuration.media_mounts[0]
    if configured.connection_id is None or mount.library_type != "ebook":
        return 2
    libraries = tuple(LibraryId(value) for value in configured.enabled_libraries)
    if len(libraries) != 1:
        return 2
    cohort = Path(mount.container_path)
    files = tuple(
        path
        for path in sorted(cohort.rglob("*"))
        if path.is_file() and not path.is_symlink() and path.suffix.casefold() == ".epub"
    )
    if not files:
        return 2
    now = datetime.now(timezone.utc)
    digest = _mount_digest(mount)
    snapshots = []
    selector_map = []
    for path in files:
        relative = path.relative_to(cohort).as_posix()
        source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        target = TargetId(
            AdapterId(configured.adapter),
            configured.connection_id,
            libraries[0],
            ExternalItemId("qualification-" + uuid.uuid4().hex),
        )
        observation = InventoryObservation.create(
            target=target,
            media_type=MediaType.EBOOK,
            lifecycle=ItemLifecycle.ACTIVE,
            fields=(
                FieldObservation(
                    ObservationField.TITLE,
                    ObservationState.PRESENT,
                    ("Synthetic qualification item",),
                ),
            ),
            file_identities=(source_digest,),
            cover_identity=None,
            adapter_version=AdapterVersion("abs-inventory-v1"),
            normalizer_version=SchemaVersion("abs-book-normalizer-v1"),
            service_version=ServiceVersion("2.35.1"),
            warnings=(),
            observed_at=now,
        )
        snapshot = InventorySnapshotV2.create(
            observation,
            file_bindings=(
                InventoryFileBinding(
                    0,
                    source_digest,
                    "epub",
                    digest,
                    FileReference(mount.name, relative),
                ),
            ),
        )
        snapshots.append(snapshot)
        selector_map.append(
            {
                "file_index": 0,
                "relative_path": relative,
                "snapshot": opaque_snapshot_reference(snapshot),
                "target": opaque_target_reference(target),
            }
        )
    connection = connect(arguments.database)
    try:
        migrate(connection)
        library_connection = LibraryConnection(
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
                supported_media_types=frozenset({MediaType.EBOOK}),
            ),
        )
        bounds = ScanBounds(500, 10, 100_000, 67_108_864, 60, 0)
        context = OperationContext(
            Deadline(now + timedelta(seconds=60)),
            CancellationProbe(CancellationId("qualification"), lambda: False),
            ResourceBounds(10_000, 67_108_864),
            TimeProbe(lambda: now),
        )
        repository = SQLiteInventoryV2Repository(connection)
        inventory_pass = InventoryPass(
            tuple(snapshots), ScanUsage(1, len(snapshots), 1)
        )

        class QualificationInventorySource:
            def verify_connection(self, *_args):
                return InventoryConnectionVerification(
                    ScanUsage(), compatibility="supported"
                )

            def verify_library(self, *_args):
                return PortResult.success(ScanUsage())

            def read_pass(self, *_args):
                return PortResult.success(inventory_pass)

        reconciled = ReconcileInventory(
            QualificationInventorySource(), repository
        ).run(library_connection, libraries, bounds, context)
        if not reconciled.succeeded:
            return 3
        arguments.selector_map.write_text(
            json.dumps(selector_map, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
