"""Versioned vendor-neutral inventory observations and reconciliation state."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from .identifiers import (
    AdapterVersion,
    DomainValueError,
    Fingerprint,
    ScanId,
    SchemaVersion,
    ServiceVersion,
    SnapshotId,
    TargetId,
    canonical_json,
)
from .inventory import ItemLifecycle, MediaType


OBSERVATION_SCHEMA = SchemaVersion("inventory-observation-v1")
SNAPSHOT_SCHEMA = SchemaVersion("inventory-snapshot-v2")
MAX_OBSERVATION_BYTES = 1_048_576
MAX_FIELD_VALUES = 10_000
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_WARNING = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ObservationState(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    NULL = "null"
    EMPTY = "empty"
    MALFORMED = "malformed"
    UNSUPPORTED = "unsupported"


class ObservationField(StrEnum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    AUTHORS = "authors"
    NARRATORS = "narrators"
    SERIES = "series"
    IDENTIFIERS = "identifiers"
    PUBLISHED_DATE = "published_date"
    PUBLISHED_YEAR = "published_year"
    PUBLISHER = "publisher"
    DESCRIPTION = "description"
    GENRES = "genres"
    LANGUAGE = "language"
    FILES = "files"
    COVER = "cover"
    DURATION = "duration"
    CHAPTERS = "chapters"
    UPDATED_AT = "updated_at"


class ScanState(StrEnum):
    RUNNING = "running"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNSTABLE = "unstable"


@dataclass(frozen=True)
class FieldObservation:
    field: ObservationField
    state: ObservationState
    values: tuple[str, ...] = ()
    raw_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.field) is not ObservationField
            or type(self.state) is not ObservationState
            or type(self.values) is not tuple
            or type(self.raw_values) is not tuple
            or len(self.values) > MAX_FIELD_VALUES
            or len(self.raw_values) > MAX_FIELD_VALUES
            or any(
                type(value) is not str
                or not value
                or value.strip() != value
                or "\x00" in value
                for value in (*self.values, *self.raw_values)
            )
        ):
            raise DomainValueError("inventory field observation is malformed")
        if self.state is ObservationState.PRESENT:
            if not self.values:
                raise DomainValueError("present inventory observations require values")
            if not self.raw_values:
                object.__setattr__(self, "raw_values", self.values)
        elif self.values or self.raw_values:
            raise DomainValueError(
                "non-present inventory observations cannot carry values"
            )


@dataclass(frozen=True, repr=False)
class InventoryObservation:
    """One bounded canonical target observation with no vendor payload keys or paths."""

    target: TargetId
    media_type: MediaType
    lifecycle: ItemLifecycle
    fields: tuple[FieldObservation, ...]
    file_identities: tuple[str, ...]
    cover_identity: str | None
    adapter_version: AdapterVersion
    normalizer_version: SchemaVersion
    service_version: ServiceVersion
    warnings: tuple[str, ...]
    observed_at: datetime
    canonical: str
    fingerprint: Fingerprint

    def __post_init__(self) -> None:
        if (
            type(self.target) is not TargetId
            or type(self.media_type) is not MediaType
            or type(self.lifecycle) is not ItemLifecycle
            or type(self.fields) is not tuple
            or any(type(value) is not FieldObservation for value in self.fields)
            or len({value.field for value in self.fields}) != len(self.fields)
            or tuple(sorted(self.fields, key=lambda value: value.field.value))
            != self.fields
            or type(self.file_identities) is not tuple
            or self.file_identities != tuple(sorted(set(self.file_identities)))
            or any(_DIGEST.fullmatch(value) is None for value in self.file_identities)
            or (
                self.cover_identity is not None
                and _DIGEST.fullmatch(self.cover_identity) is None
            )
            or type(self.adapter_version) is not AdapterVersion
            or type(self.normalizer_version) is not SchemaVersion
            or type(self.service_version) is not ServiceVersion
            or type(self.warnings) is not tuple
            or self.warnings != tuple(sorted(set(self.warnings)))
            or any(_WARNING.fullmatch(value) is None for value in self.warnings)
            or self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
            or type(self.canonical) is not str
            or len(self.canonical.encode("utf-8")) > MAX_OBSERVATION_BYTES
            or type(self.fingerprint) is not Fingerprint
            or self.fingerprint.schema != OBSERVATION_SCHEMA
        ):
            raise DomainValueError("inventory observation is malformed")
        expected = _observation_value(self)
        if canonical_json(expected) != self.canonical:
            raise DomainValueError(
                "inventory observation canonical input is inconsistent"
            )
        if (
            Fingerprint.from_canonical_value(expected, schema=OBSERVATION_SCHEMA)
            != self.fingerprint
        ):
            raise DomainValueError("inventory observation fingerprint is inconsistent")

    @classmethod
    def create(
        cls,
        *,
        target: TargetId,
        media_type: MediaType,
        lifecycle: ItemLifecycle,
        fields: tuple[FieldObservation, ...],
        file_identities: tuple[str, ...],
        cover_identity: str | None,
        adapter_version: AdapterVersion,
        normalizer_version: SchemaVersion,
        service_version: ServiceVersion,
        warnings: tuple[str, ...],
        observed_at: datetime,
    ) -> "InventoryObservation":
        ordered_fields = tuple(sorted(fields, key=lambda value: value.field.value))
        ordered_files = tuple(sorted(set(file_identities)))
        ordered_warnings = tuple(sorted(set(warnings)))
        provisional = cls.__new__(cls)
        object.__setattr__(provisional, "target", target)
        object.__setattr__(provisional, "media_type", media_type)
        object.__setattr__(provisional, "lifecycle", lifecycle)
        object.__setattr__(provisional, "fields", ordered_fields)
        object.__setattr__(provisional, "file_identities", ordered_files)
        object.__setattr__(provisional, "cover_identity", cover_identity)
        object.__setattr__(provisional, "adapter_version", adapter_version)
        object.__setattr__(provisional, "normalizer_version", normalizer_version)
        object.__setattr__(provisional, "service_version", service_version)
        object.__setattr__(provisional, "warnings", ordered_warnings)
        object.__setattr__(provisional, "observed_at", observed_at)
        value = _observation_value(provisional)
        return cls(
            target,
            media_type,
            lifecycle,
            ordered_fields,
            ordered_files,
            cover_identity,
            adapter_version,
            normalizer_version,
            service_version,
            ordered_warnings,
            observed_at,
            canonical_json(value),
            Fingerprint.from_canonical_value(value, schema=OBSERVATION_SCHEMA),
        )

    @classmethod
    def from_json(cls, canonical: str, observed_at: datetime) -> "InventoryObservation":
        try:
            value = json.loads(canonical)
            target_value = value["target"]
            from .identifiers import AdapterId, ConnectionId, ExternalItemId, LibraryId

            observation = cls.create(
                target=TargetId(
                    AdapterId(target_value["adapter"]),
                    ConnectionId(target_value["connection"]),
                    LibraryId(target_value["library"]),
                    ExternalItemId(target_value["external_item"]),
                ),
                media_type=MediaType(value["media_type"]),
                lifecycle=ItemLifecycle(value["lifecycle"]),
                fields=tuple(
                    FieldObservation(
                        ObservationField(item["field"]),
                        ObservationState(item["state"]),
                        tuple(item["values"]),
                        tuple(item["raw_values"]),
                    )
                    for item in value["fields"]
                ),
                file_identities=tuple(value["file_identities"]),
                cover_identity=value["cover_identity"],
                adapter_version=AdapterVersion(value["adapter_version"]),
                normalizer_version=SchemaVersion(value["normalizer_version"]),
                service_version=ServiceVersion(value["service_version"]),
                warnings=tuple(value["warnings"]),
                observed_at=observed_at,
            )
            if observation.canonical != canonical:
                raise DomainValueError("stored inventory observation is noncanonical")
            return observation
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise DomainValueError(
                "stored inventory observation is malformed"
            ) from error

    def __repr__(self) -> str:
        return (
            "InventoryObservation("
            f"target={self.target!r}, media_type={self.media_type!r}, "
            f"lifecycle={self.lifecycle!r}, fingerprint={self.fingerprint!r}, "
            "fields=<redacted>, file_identities=<redacted>, cover_identity=<redacted>)"
        )


def _observation_value(observation: InventoryObservation) -> dict[str, object]:
    return {
        "adapter_version": observation.adapter_version.value,
        "cover_identity": observation.cover_identity,
        "fields": [
            {
                "field": item.field.value,
                "state": item.state.value,
                "values": list(item.values),
                "raw_values": list(item.raw_values),
            }
            for item in observation.fields
        ],
        "file_identities": list(observation.file_identities),
        "lifecycle": observation.lifecycle.value,
        "media_type": observation.media_type.value,
        "normalizer_version": observation.normalizer_version.value,
        "schema": OBSERVATION_SCHEMA.value,
        "service_version": observation.service_version.value,
        "target": {
            "adapter": observation.target.adapter.value,
            "connection": observation.target.connection.value,
            "external_item": observation.target.external_item.value,
            "library": observation.target.library.value,
        },
        "warnings": list(observation.warnings),
    }


@dataclass(frozen=True, repr=False)
class InventorySnapshotV2:
    id: SnapshotId
    observation: InventoryObservation
    fingerprint: Fingerprint

    def __post_init__(self) -> None:
        if (
            type(self.id) is not SnapshotId
            or type(self.observation) is not InventoryObservation
            or type(self.fingerprint) is not Fingerprint
            or self.fingerprint.schema != SNAPSHOT_SCHEMA
        ):
            raise DomainValueError("inventory snapshot v2 is malformed")
        if self.fingerprint != _snapshot_fingerprint(self.observation):
            raise DomainValueError("inventory snapshot v2 fingerprint is inconsistent")

    @classmethod
    def create(cls, observation: InventoryObservation) -> "InventorySnapshotV2":
        fingerprint = _snapshot_fingerprint(observation)
        return cls(
            SnapshotId("snapshot-" + fingerprint.digest[:32]),
            observation,
            fingerprint,
        )

    def removed(self, *, observed_at: datetime) -> "InventorySnapshotV2":
        fields = tuple(
            item
            for item in self.observation.fields
            if item.field is not ObservationField.UPDATED_AT
        ) + (
            FieldObservation(
                ObservationField.UPDATED_AT,
                ObservationState.UNSUPPORTED,
            ),
        )
        return InventorySnapshotV2.create(
            InventoryObservation.create(
                target=self.observation.target,
                media_type=self.observation.media_type,
                lifecycle=ItemLifecycle.REMOVED,
                fields=fields,
                file_identities=self.observation.file_identities,
                cover_identity=self.observation.cover_identity,
                adapter_version=self.observation.adapter_version,
                normalizer_version=self.observation.normalizer_version,
                service_version=self.observation.service_version,
                warnings=(*self.observation.warnings, "disappeared"),
                observed_at=observed_at,
            )
        )

    def __repr__(self) -> str:
        return (
            "InventorySnapshotV2("
            f"id={self.id!r}, target={self.observation.target!r}, "
            f"fingerprint={self.fingerprint!r}, observation=<redacted>)"
        )


def _snapshot_fingerprint(observation: InventoryObservation) -> Fingerprint:
    return Fingerprint.from_canonical_value(
        {
            "observation_fingerprint": observation.fingerprint.digest,
            "observation_schema": observation.fingerprint.schema.value,
            "schema": SNAPSHOT_SCHEMA.value,
            "target": {
                "adapter": observation.target.adapter.value,
                "connection": observation.target.connection.value,
                "external_item": observation.target.external_item.value,
                "library": observation.target.library.value,
            },
        },
        schema=SNAPSHOT_SCHEMA,
    )


@dataclass(frozen=True)
class ScanBounds:
    page_size: int
    max_pages: int
    max_items: int
    max_bytes: int
    max_seconds: int
    stability_delay_seconds: int

    def __post_init__(self) -> None:
        if (
            type(self.page_size) is not int
            or not 1 <= self.page_size <= 1_000
            or type(self.max_pages) is not int
            or not 1 <= self.max_pages <= 10_000
            or type(self.max_items) is not int
            or not 1 <= self.max_items <= 1_000_000
            or type(self.max_bytes) is not int
            or not 1_024 <= self.max_bytes <= 1_073_741_824
            or type(self.max_seconds) is not int
            or not 1 <= self.max_seconds <= 3_600
            or type(self.stability_delay_seconds) is not int
            or not 0 <= self.stability_delay_seconds <= 86_400
        ):
            raise DomainValueError("inventory scan bounds are invalid")

    def remaining(self, *, pages: int, items: int, response_bytes: int) -> "ScanBounds":
        return replace(
            self,
            max_pages=self.max_pages - pages,
            max_items=self.max_items - items,
            max_bytes=self.max_bytes - response_bytes,
        )


@dataclass(frozen=True)
class ScanUsage:
    pages: int = 0
    items: int = 0
    response_bytes: int = 0

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (self.pages, self.items, self.response_bytes)
        ):
            raise DomainValueError("inventory scan usage is invalid")

    def __add__(self, other: object) -> "ScanUsage":
        if type(other) is not ScanUsage:
            return NotImplemented
        return ScanUsage(
            self.pages + other.pages,
            self.items + other.items,
            self.response_bytes + other.response_bytes,
        )


@dataclass(frozen=True)
class InventoryPass:
    snapshots: tuple[InventorySnapshotV2, ...]
    usage: ScanUsage
    recent_item: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.snapshots) is not tuple
            or any(type(value) is not InventorySnapshotV2 for value in self.snapshots)
            or len({value.observation.target for value in self.snapshots})
            != len(self.snapshots)
            or type(self.usage) is not ScanUsage
            or type(self.recent_item) is not bool
        ):
            raise DomainValueError("inventory pass is invalid")

    @property
    def digest(self) -> str:
        value = [
            {
                "fingerprint": snapshot.fingerprint.digest,
                "target": {
                    "adapter": snapshot.observation.target.adapter.value,
                    "connection": snapshot.observation.target.connection.value,
                    "external_item": snapshot.observation.target.external_item.value,
                    "library": snapshot.observation.target.library.value,
                },
            }
            for snapshot in sorted(
                self.snapshots,
                key=lambda value: value.observation.target.external_item.value,
            )
        ]
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScanStart:
    scan_id: ScanId
    current: tuple[InventorySnapshotV2, ...]
    current_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.scan_id) is not ScanId
            or type(self.current) is not tuple
            or any(type(value) is not InventorySnapshotV2 for value in self.current)
            or _DIGEST.fullmatch(self.current_digest) is None
        ):
            raise DomainValueError("inventory scan start is invalid")


@dataclass(frozen=True)
class InventoryInspection:
    scan_id: ScanId
    state: ScanState
    failure_kind: str | None
    item_count: int
    active_count: int
    removed_count: int
    history_count: int
    page_count: int
    response_bytes: int
    moved_count: int

    def __post_init__(self) -> None:
        if (
            type(self.scan_id) is not ScanId
            or type(self.state) is not ScanState
            or (
                self.failure_kind is not None
                and _WARNING.fullmatch(self.failure_kind) is None
            )
            or any(
                type(value) is not int or value < 0
                for value in (
                    self.item_count,
                    self.active_count,
                    self.removed_count,
                    self.history_count,
                    self.page_count,
                    self.response_bytes,
                    self.moved_count,
                )
            )
            or self.item_count != self.active_count + self.removed_count
        ):
            raise DomainValueError("inventory inspection is invalid")


__all__ = [
    "FieldObservation",
    "InventoryInspection",
    "InventoryObservation",
    "InventoryPass",
    "InventorySnapshotV2",
    "ObservationField",
    "ObservationState",
    "OBSERVATION_SCHEMA",
    "ScanBounds",
    "ScanStart",
    "ScanState",
    "ScanUsage",
    "SNAPSHOT_SCHEMA",
]
