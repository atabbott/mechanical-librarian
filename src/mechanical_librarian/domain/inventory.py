"""Immutable library inventory entities and adapter capability declarations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from urllib.parse import urlsplit

from .identifiers import (
    AdapterId,
    AdapterVersion,
    ConnectionId,
    DomainValueError,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    SchemaVersion,
    ServiceVersion,
    SnapshotId,
    TargetId,
    canonical_json,
)


SNAPSHOT_SCHEMA_V1 = SchemaVersion("snapshot-v1")
SNAPSHOT_CANONICAL_MAX_BYTES = 1_048_576
SNAPSHOT_CANONICAL_SOURCE_MAX_BYTES = SNAPSHOT_CANONICAL_MAX_BYTES // 8
SNAPSHOT_CANONICAL_MAX_COLLECTION_ITEMS = 10_000


class MediaType(StrEnum):
    EBOOK = "ebook"
    AUDIOBOOK = "audiobook"


class ItemLifecycle(StrEnum):
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"
    REMOVED = "removed"


class FieldName(StrEnum):
    TITLE = "title"
    AUTHORS = "authors"
    NARRATORS = "narrators"
    SERIES = "series"
    SERIES_SEQUENCE = "series_sequence"
    PUBLISHER = "publisher"
    PUBLISHED_DATE = "published_date"
    IDENTIFIERS = "identifiers"
    DESCRIPTION = "description"
    LANGUAGE = "language"


@dataclass(frozen=True)
class AdapterCapabilities:
    """Target-specific behavior that narrows workflows but grants no authority."""

    inventory_read: bool
    metadata_read: bool
    media_file_read: bool
    notifications: bool
    pagination: bool
    concurrency_tokens: bool
    cover_read: bool
    writable_fields: frozenset[FieldName]
    cover_write: bool
    supported_media_types: frozenset[MediaType]

    def __post_init__(self) -> None:
        boolean_values = (
            self.inventory_read,
            self.metadata_read,
            self.media_file_read,
            self.notifications,
            self.pagination,
            self.concurrency_tokens,
            self.cover_read,
            self.cover_write,
        )
        if any(type(value) is not bool for value in boolean_values):
            raise DomainValueError("capability flags must be booleans")
        if type(self.writable_fields) is not frozenset or not all(
            type(field) is FieldName for field in self.writable_fields
        ):
            raise DomainValueError("writable fields must be an immutable field-name set")
        if type(self.supported_media_types) is not frozenset or not all(
            type(media_type) is MediaType for media_type in self.supported_media_types
        ):
            raise DomainValueError("supported media types must be an immutable media-type set")
        if not self.inventory_read and (self.metadata_read or self.media_file_read):
            raise DomainValueError("metadata or media reads require inventory reads")
        if self.writable_fields and not self.metadata_read:
            raise DomainValueError("writable fields require metadata reads")
        if self.cover_write and not self.cover_read:
            raise DomainValueError("cover writes require cover reads")
        if not self.supported_media_types:
            raise DomainValueError("at least one media type must be declared")

    @property
    def write_supported(self) -> bool:
        """Whether the target exposes any write capability, not permission to use it."""

        return bool(self.writable_fields) or self.cover_write


@dataclass(frozen=True, repr=False)
class SecretReference:
    """A non-secret locator resolved outside the domain model."""

    value: str

    def __post_init__(self) -> None:
        if not self.value or len(self.value) > 256 or self.value.strip() != self.value:
            raise DomainValueError("secret reference must be a trimmed value up to 256 characters")
        if "://" in self.value or "\x00" in self.value:
            raise DomainValueError("secret reference must not contain a URL or NUL")

    def __repr__(self) -> str:
        return "SecretReference(<redacted>)"


@dataclass(frozen=True, repr=False)
class ServiceEndpoint:
    """A validated service origin, intentionally redacted in diagnostics."""

    value: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise DomainValueError("service endpoint must use http or https")
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or self.value.rstrip("/") != self.value
        ):
            raise DomainValueError("service endpoint must not embed credentials or end with '/' ")

    def __repr__(self) -> str:
        return "ServiceEndpoint(<redacted>)"


@dataclass(frozen=True)
class LibraryConnection:
    id: ConnectionId
    adapter: AdapterId
    endpoint: ServiceEndpoint
    secret: SecretReference
    capabilities: AdapterCapabilities

    def __repr__(self) -> str:
        return (
            "LibraryConnection("
            f"id={self.id!r}, adapter={self.adapter!r}, endpoint={self.endpoint!r}, "
            f"secret={self.secret!r}, capabilities={self.capabilities!r})"
        )


@dataclass(frozen=True)
class AdapterRelease:
    """An adapter implementation version bound to its adapter family."""

    adapter: AdapterId
    version: AdapterVersion

    def __post_init__(self) -> None:
        if type(self.adapter) is not AdapterId or type(self.version) is not AdapterVersion:
            raise DomainValueError("adapter release requires an adapter ID and adapter version")


@dataclass(frozen=True)
class ServiceRelease:
    """An upstream service version observed through one adapter family."""

    adapter: AdapterId
    version: ServiceVersion

    def __post_init__(self) -> None:
        if type(self.adapter) is not AdapterId or type(self.version) is not ServiceVersion:
            raise DomainValueError("service release requires an adapter ID and service version")


@dataclass(frozen=True, repr=False)
class ConcurrencyToken:
    """Opaque upstream evidence used to reject stale observations."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str:
            raise DomainValueError("concurrency token must be text")
        if not self.value or len(self.value) > 512 or self.value.strip() != self.value:
            raise DomainValueError("concurrency token must be a trimmed value up to 512 characters")
        if any(ord(character) < 32 or ord(character) == 127 for character in self.value):
            raise DomainValueError("concurrency token must not contain control characters")

    def __repr__(self) -> str:
        return "ConcurrencyToken(<redacted>)"


@dataclass(frozen=True, repr=False)
class FileReference:
    """A read-only, logical media location rather than a host path."""

    mount: str
    relative_path: str

    def __post_init__(self) -> None:
        if not self.mount or "/" in self.mount or self.mount.strip() != self.mount:
            raise DomainValueError("file mount must be a non-empty logical name")
        parts = self.relative_path.split("/")
        if (
            not self.relative_path
            or self.relative_path.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)
            or "\x00" in self.relative_path
        ):
            raise DomainValueError("file reference must be a relative, normalized logical path")

    def __repr__(self) -> str:
        return f"FileReference(mount={self.mount!r}, relative_path=<redacted>)"


@dataclass(frozen=True, repr=False)
class CoverReference:
    """An opaque cover locator and optional content identity."""

    opaque_reference: str
    content_fingerprint: Fingerprint | None = None

    def __post_init__(self) -> None:
        if type(self.opaque_reference) is not str or not self.opaque_reference or len(self.opaque_reference) > 512:
            raise DomainValueError("cover reference must be a non-empty value up to 512 characters")
        if self.content_fingerprint is not None and type(self.content_fingerprint) is not Fingerprint:
            raise DomainValueError("cover content identity must be an exact fingerprint")

    def __repr__(self) -> str:
        return "CoverReference(opaque_reference=<redacted>, content_fingerprint=<redacted>)"


@dataclass(frozen=True)
class MetadataField:
    name: FieldName
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.name) is not FieldName or type(self.values) is not tuple:
            raise DomainValueError("metadata field requires a field name and immutable values")
        if not self.values or any(not value or value.strip() != value for value in self.values):
            raise DomainValueError("metadata field values must be non-empty trimmed strings")


@dataclass(frozen=True)
class NormalizedMetadata:
    """Immutable normalized fields; raw adapter payloads stay separate."""

    fields: tuple[MetadataField, ...]

    def __post_init__(self) -> None:
        if type(self.fields) is not tuple or not all(
            type(field) is MetadataField for field in self.fields
        ):
            raise DomainValueError("normalized metadata fields must be an immutable tuple")
        names = [field.name for field in self.fields]
        if len(names) != len(set(names)):
            raise DomainValueError("normalized metadata cannot contain duplicate fields")

    def get(self, name: FieldName) -> MetadataField | None:
        return next((field for field in self.fields if field.name == name), None)


@dataclass(frozen=True, repr=False)
class RawObservation:
    """An opaque, adapter-owned observation retained outside normalized fields."""

    adapter: AdapterId
    payload_fingerprint: Fingerprint
    artifact_reference: str | None = None

    def __post_init__(self) -> None:
        if self.artifact_reference is not None and not self.artifact_reference:
            raise DomainValueError("raw observation artifact reference cannot be blank")

    def __repr__(self) -> str:
        return (
            "RawObservation(adapter=<redacted>, payload_fingerprint=<redacted>, "
            "artifact_reference=<redacted>)"
        )


@dataclass(frozen=True)
class ObservationProvenance:
    adapter_release: AdapterRelease
    observed_at: datetime
    raw_observation: RawObservation

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise DomainValueError("observations must use timezone-aware timestamps")
        if self.adapter_release.adapter != self.raw_observation.adapter:
            raise DomainValueError("observation adapter scope is inconsistent")


@dataclass(frozen=True)
class LibraryItem:
    """Stable target identity and lifecycle, separate from changing snapshots."""

    target: TargetId
    media_type: MediaType
    lifecycle: ItemLifecycle
    snapshot_ids: tuple[SnapshotId, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.target) is not TargetId
            or type(self.media_type) is not MediaType
            or type(self.lifecycle) is not ItemLifecycle
        ):
            raise DomainValueError(
                "library item requires a target, media type, and lifecycle"
            )
        if type(self.snapshot_ids) is not tuple or not all(
            type(snapshot_id) is SnapshotId for snapshot_id in self.snapshot_ids
        ):
            raise DomainValueError("library item snapshot IDs must be an immutable tuple")
        if len(self.snapshot_ids) != len(set(self.snapshot_ids)):
            raise DomainValueError("library item cannot reference a snapshot more than once")


@dataclass(frozen=True, repr=False)
class CanonicalSnapshotInput:
    """Frozen, versioned bytes defining snapshot state equality and fingerprints."""

    schema: SchemaVersion
    serialized: str

    def __post_init__(self) -> None:
        if type(self.schema) is not SchemaVersion or self.schema != SNAPSHOT_SCHEMA_V1:
            raise DomainValueError("unsupported canonical snapshot schema")
        if type(self.serialized) is not str:
            raise DomainValueError("canonical snapshot input must be text")
        try:
            serialized_bytes = self.serialized.encode("utf-8")
        except UnicodeEncodeError as error:
            raise DomainValueError("canonical snapshot input contains invalid Unicode") from error
        if len(self.serialized) > SNAPSHOT_CANONICAL_MAX_BYTES or (
            len(serialized_bytes) > SNAPSHOT_CANONICAL_MAX_BYTES
        ):
            raise DomainValueError("canonical snapshot input exceeds the size limit")
        try:
            value = json.loads(self.serialized)
        except (RecursionError, TypeError, ValueError) as error:
            raise DomainValueError("canonical snapshot input must be valid JSON") from error
        if not isinstance(value, dict) or value.get("schema") != self.schema.value:
            raise DomainValueError("canonical snapshot input schema marker is inconsistent")
        _validate_snapshot_v1(value)
        if canonical_json(value) != self.serialized:
            raise DomainValueError("canonical snapshot input must use canonical JSON")

    @classmethod
    def from_state(
        cls,
        *,
        target: TargetId,
        media_type: MediaType,
        lifecycle: ItemLifecycle,
        metadata: NormalizedMetadata,
        files: tuple[FileReference, ...],
        cover: CoverReference | None,
        provenance: ObservationProvenance,
        service_release: ServiceRelease,
        concurrency_token: ConcurrencyToken | None,
        capabilities: AdapterCapabilities,
    ) -> CanonicalSnapshotInput:
        _validate_snapshot_source_size(
            target=target,
            metadata=metadata,
            files=files,
            cover=cover,
            provenance=provenance,
            concurrency_token=concurrency_token,
        )
        fields = sorted(metadata.fields, key=lambda field: field.name.value)
        file_identities = sorted(
            _opaque_identity(file.mount, file.relative_path) for file in files
        )
        cover_value = None
        if cover is not None:
            cover_value = {
                "content_fingerprint": _fingerprint_value(cover.content_fingerprint),
                "reference_identity": _opaque_identity(cover.opaque_reference),
            }
        value = {
            "adapter_release": {
                "adapter": provenance.adapter_release.adapter.value,
                "version": provenance.adapter_release.version.value,
            },
            "capabilities": {
                "concurrency_tokens": capabilities.concurrency_tokens,
                "cover_read": capabilities.cover_read,
                "cover_write": capabilities.cover_write,
                "inventory_read": capabilities.inventory_read,
                "media_file_read": capabilities.media_file_read,
                "metadata_read": capabilities.metadata_read,
                "notifications": capabilities.notifications,
                "pagination": capabilities.pagination,
                "supported_media_types": sorted(
                    value.value for value in capabilities.supported_media_types
                ),
                "writable_fields": sorted(value.value for value in capabilities.writable_fields),
            },
            "concurrency_identity": (
                _opaque_identity(concurrency_token.value) if concurrency_token is not None else None
            ),
            "cover": cover_value,
            "files": file_identities,
            "lifecycle": lifecycle.value,
            "media_type": media_type.value,
            "metadata": [
                {"name": field.name.value, "values": list(field.values)} for field in fields
            ],
            "raw_observation_fingerprint": _fingerprint_value(
                provenance.raw_observation.payload_fingerprint
            ),
            "schema": SNAPSHOT_SCHEMA_V1.value,
            "service_release": {
                "adapter": service_release.adapter.value,
                "version": service_release.version.value,
            },
            "target": {
                "adapter": target.adapter.value,
                "connection": target.connection.value,
                "external_item": target.external_item.value,
                "library": target.library.value,
            },
        }
        return cls(schema=SNAPSHOT_SCHEMA_V1, serialized=canonical_json(value))

    @classmethod
    def from_json(cls, serialized: str) -> CanonicalSnapshotInput:
        return cls(schema=SNAPSHOT_SCHEMA_V1, serialized=serialized)

    def fingerprint(self) -> Fingerprint:
        return Fingerprint.from_canonical_value(json.loads(self.serialized), schema=self.schema)

    def __repr__(self) -> str:
        return f"CanonicalSnapshotInput(schema={self.schema!r}, serialized=<redacted>)"


@dataclass(frozen=True, repr=False)
class ItemSnapshot:
    """One immutable service observation for exactly one target."""

    id: SnapshotId
    target: TargetId
    media_type: MediaType
    lifecycle: ItemLifecycle
    fingerprint: Fingerprint
    metadata: NormalizedMetadata
    files: tuple[FileReference, ...]
    cover: CoverReference | None
    provenance: ObservationProvenance
    service_release: ServiceRelease
    concurrency_token: ConcurrencyToken | None
    capabilities: AdapterCapabilities
    canonical_input: CanonicalSnapshotInput

    def __post_init__(self) -> None:
        if type(self.files) is not tuple or not all(
            type(file) is FileReference for file in self.files
        ):
            raise DomainValueError("snapshot files must be an immutable tuple")
        if len(self.files) != len(set(self.files)):
            raise DomainValueError("snapshot cannot contain duplicate file references")
        adapters = {
            self.target.adapter,
            self.provenance.adapter_release.adapter,
            self.provenance.raw_observation.adapter,
            self.service_release.adapter,
        }
        if len(adapters) != 1:
            raise DomainValueError("snapshot adapter scope is inconsistent")
        if self.media_type not in self.capabilities.supported_media_types:
            raise DomainValueError("snapshot media type is unsupported by declared capabilities")
        if self.capabilities.concurrency_tokens != (self.concurrency_token is not None):
            raise DomainValueError(
                "snapshot concurrency evidence does not match declared capabilities"
            )
        expected_input = CanonicalSnapshotInput.from_state(
            target=self.target,
            media_type=self.media_type,
            lifecycle=self.lifecycle,
            metadata=self.metadata,
            files=self.files,
            cover=self.cover,
            provenance=self.provenance,
            service_release=self.service_release,
            concurrency_token=self.concurrency_token,
            capabilities=self.capabilities,
        )
        if self.canonical_input != expected_input:
            raise DomainValueError("snapshot canonical input does not match observed state")
        if self.fingerprint != self.canonical_input.fingerprint():
            raise DomainValueError("snapshot fingerprint does not match canonical input")

    @classmethod
    def create(
        cls,
        *,
        id: SnapshotId,
        target: TargetId,
        media_type: MediaType,
        lifecycle: ItemLifecycle,
        metadata: NormalizedMetadata,
        files: tuple[FileReference, ...],
        cover: CoverReference | None,
        provenance: ObservationProvenance,
        service_release: ServiceRelease,
        concurrency_token: ConcurrencyToken | None,
        capabilities: AdapterCapabilities,
    ) -> ItemSnapshot:
        canonical_input = CanonicalSnapshotInput.from_state(
            target=target,
            media_type=media_type,
            lifecycle=lifecycle,
            metadata=metadata,
            files=files,
            cover=cover,
            provenance=provenance,
            service_release=service_release,
            concurrency_token=concurrency_token,
            capabilities=capabilities,
        )
        return cls(
            id=id,
            target=target,
            media_type=media_type,
            lifecycle=lifecycle,
            fingerprint=canonical_input.fingerprint(),
            metadata=metadata,
            files=files,
            cover=cover,
            provenance=provenance,
            service_release=service_release,
            concurrency_token=concurrency_token,
            capabilities=capabilities,
            canonical_input=canonical_input,
        )

    def same_canonical_state(self, other: ItemSnapshot) -> bool:
        return self.canonical_input == other.canonical_input

    def __repr__(self) -> str:
        return (
            "ItemSnapshot("
            f"id={self.id!r}, target={self.target!r}, media_type={self.media_type!r}, "
            f"lifecycle={self.lifecycle!r}, fingerprint={self.fingerprint!r}, "
            "metadata=<redacted>, files=<redacted>, cover=<redacted>, "
            f"provenance={self.provenance!r}, service_release={self.service_release!r}, "
            "concurrency_token=<redacted>, capabilities=<redacted>, canonical_input=<redacted>)"
        )


@dataclass(frozen=True)
class InventoryRecord:
    """A connection, item, and current snapshot validated as one scoped graph."""

    connection: LibraryConnection
    item: LibraryItem
    snapshot: ItemSnapshot

    def __post_init__(self) -> None:
        if self.connection.adapter != self.item.target.adapter:
            raise DomainValueError("inventory adapter scope is inconsistent")
        if self.connection.id != self.item.target.connection:
            raise DomainValueError("inventory connection scope is inconsistent")
        if self.item.target != self.snapshot.target:
            raise DomainValueError("inventory target scope is inconsistent")
        if self.item.media_type != self.snapshot.media_type:
            raise DomainValueError("inventory media type is inconsistent")
        if self.item.lifecycle != self.snapshot.lifecycle:
            raise DomainValueError("inventory lifecycle is inconsistent")
        if self.snapshot.id not in self.item.snapshot_ids:
            raise DomainValueError("inventory item does not reference the snapshot")
        if self.connection.capabilities != self.snapshot.capabilities:
            raise DomainValueError("inventory capability state is inconsistent")
        if self.item.media_type not in self.connection.capabilities.supported_media_types:
            raise DomainValueError("inventory media type is unsupported by the connection")


def _opaque_identity(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _fingerprint_value(fingerprint: Fingerprint | None) -> dict[str, str] | None:
    if fingerprint is None:
        return None
    return {
        "algorithm": fingerprint.algorithm,
        "digest": fingerprint.digest,
        "schema": fingerprint.schema.value,
    }


def _validate_snapshot_v1(value: dict[object, object]) -> None:
    _require_keys(
        value,
        {
            "adapter_release",
            "capabilities",
            "concurrency_identity",
            "cover",
            "files",
            "lifecycle",
            "media_type",
            "metadata",
            "raw_observation_fingerprint",
            "schema",
            "service_release",
            "target",
        },
        "snapshot",
    )
    _validate_release(value["adapter_release"], AdapterVersion, "adapter release")
    _validate_release(value["service_release"], ServiceVersion, "service release")
    _validate_capabilities(value["capabilities"])
    _validate_digest_or_none(value["concurrency_identity"], "concurrency identity")
    _validate_cover(value["cover"])
    file_count = _validate_digest_list(value["files"], "files")
    try:
        ItemLifecycle(value["lifecycle"])
        MediaType(value["media_type"])
    except (TypeError, ValueError) as error:
        raise DomainValueError("canonical snapshot lifecycle or media type is invalid") from error
    metadata_value_count = _validate_metadata(value["metadata"])
    _validate_fingerprint(value["raw_observation_fingerprint"], "raw observation fingerprint")
    if value["schema"] != SNAPSHOT_SCHEMA_V1.value:
        raise DomainValueError("canonical snapshot schema marker is inconsistent")
    _validate_target(value["target"])
    if file_count + metadata_value_count > SNAPSHOT_CANONICAL_MAX_COLLECTION_ITEMS:
        raise DomainValueError("canonical snapshot has too many collection items")
    adapter_release = _require_mapping(value["adapter_release"], "adapter release")
    service_release = _require_mapping(value["service_release"], "service release")
    target = _require_mapping(value["target"], "target")
    adapter_scopes = {
        adapter_release["adapter"],
        service_release["adapter"],
        target["adapter"],
    }
    if len(adapter_scopes) != 1:
        raise DomainValueError("canonical snapshot adapter scope is inconsistent")
    capabilities = _require_mapping(value["capabilities"], "capabilities")
    if capabilities["concurrency_tokens"] != (value["concurrency_identity"] is not None):
        raise DomainValueError("canonical snapshot concurrency evidence is inconsistent")
    if value["media_type"] not in capabilities["supported_media_types"]:
        raise DomainValueError("canonical snapshot media type is unsupported")


def _validate_release(
    value: object,
    version_type: type[AdapterVersion] | type[ServiceVersion],
    label: str,
) -> None:
    mapping = _require_mapping(value, label)
    _require_keys(mapping, {"adapter", "version"}, label)
    try:
        AdapterId(mapping["adapter"])
        version_type(mapping["version"])
    except (TypeError, ValueError) as error:
        raise DomainValueError(f"canonical {label} is invalid") from error


def _validate_capabilities(value: object) -> None:
    mapping = _require_mapping(value, "capabilities")
    boolean_names = {
        "concurrency_tokens",
        "cover_read",
        "cover_write",
        "inventory_read",
        "media_file_read",
        "metadata_read",
        "notifications",
        "pagination",
    }
    _require_keys(
        mapping,
        boolean_names | {"supported_media_types", "writable_fields"},
        "capabilities",
    )
    if any(type(mapping[name]) is not bool for name in boolean_names):
        raise DomainValueError("canonical capability flags must be booleans")
    media_types = _require_sorted_unique_strings(
        mapping["supported_media_types"], "supported media types"
    )
    writable_fields = _require_sorted_unique_strings(
        mapping["writable_fields"], "writable fields"
    )
    try:
        AdapterCapabilities(
            inventory_read=mapping["inventory_read"],
            metadata_read=mapping["metadata_read"],
            media_file_read=mapping["media_file_read"],
            notifications=mapping["notifications"],
            pagination=mapping["pagination"],
            concurrency_tokens=mapping["concurrency_tokens"],
            cover_read=mapping["cover_read"],
            writable_fields=frozenset(FieldName(field) for field in writable_fields),
            cover_write=mapping["cover_write"],
            supported_media_types=frozenset(MediaType(media) for media in media_types),
        )
    except (TypeError, ValueError) as error:
        raise DomainValueError("canonical capabilities are invalid") from error


def _validate_cover(value: object) -> None:
    if value is None:
        return
    mapping = _require_mapping(value, "cover")
    _require_keys(mapping, {"content_fingerprint", "reference_identity"}, "cover")
    _validate_fingerprint_or_none(mapping["content_fingerprint"], "cover fingerprint")
    _validate_digest_or_none(mapping["reference_identity"], "cover identity", allow_none=False)


def _validate_metadata(value: object) -> int:
    if type(value) is not list:
        raise DomainValueError("canonical metadata must be a list")
    names: list[str] = []
    for entry in value:
        mapping = _require_mapping(entry, "metadata field")
        _require_keys(mapping, {"name", "values"}, "metadata field")
        try:
            name = FieldName(mapping["name"]).value
        except (TypeError, ValueError) as error:
            raise DomainValueError("canonical metadata field name is invalid") from error
        field_values = mapping["values"]
        if type(field_values) is not list or not field_values or not all(
            type(field_value) is str
            and field_value
            and field_value.strip() == field_value
            and not _contains_surrogate(field_value)
            for field_value in field_values
        ):
            raise DomainValueError("canonical metadata field values are invalid")
        names.append(name)
    if names != sorted(set(names)):
        raise DomainValueError("canonical metadata fields must be sorted and unique")
    return sum(len(entry["values"]) for entry in value)


def _validate_target(value: object) -> None:
    mapping = _require_mapping(value, "target")
    _require_keys(mapping, {"adapter", "connection", "external_item", "library"}, "target")
    if type(mapping["external_item"]) is str and _contains_surrogate(
        mapping["external_item"]
    ):
        raise DomainValueError("canonical target contains invalid Unicode")
    try:
        AdapterId(mapping["adapter"])
        ConnectionId(mapping["connection"])
        ExternalItemId(mapping["external_item"])
        LibraryId(mapping["library"])
    except (TypeError, ValueError) as error:
        raise DomainValueError("canonical target is invalid") from error


def _validate_fingerprint_or_none(value: object, label: str) -> None:
    if value is None:
        return
    _validate_fingerprint(value, label)


def _validate_fingerprint(value: object, label: str) -> None:
    mapping = _require_mapping(value, label)
    _require_keys(mapping, {"algorithm", "digest", "schema"}, label)
    try:
        Fingerprint(mapping["algorithm"], SchemaVersion(mapping["schema"]), mapping["digest"])
    except (TypeError, ValueError) as error:
        raise DomainValueError(f"canonical {label} is invalid") from error


def _validate_digest_list(value: object, label: str) -> int:
    strings = _require_sorted_unique_strings(value, label)
    for digest in strings:
        _validate_digest_or_none(digest, label, allow_none=False)
    return len(strings)


def _validate_digest_or_none(value: object, label: str, *, allow_none: bool = True) -> None:
    if value is None and allow_none:
        return
    if type(value) is not str or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise DomainValueError(f"canonical {label} is invalid")


def _require_sorted_unique_strings(value: object, label: str) -> list[str]:
    if type(value) is not list or not all(type(item) is str for item in value):
        raise DomainValueError(f"canonical {label} must be a string list")
    if value != sorted(set(value)):
        raise DomainValueError(f"canonical {label} must be sorted and unique")
    return value


def _require_mapping(value: object, label: str) -> dict[object, object]:
    if type(value) is not dict:
        raise DomainValueError(f"canonical {label} must be an object")
    return value


def _require_keys(mapping: dict[object, object], keys: set[str], label: str) -> None:
    if set(mapping) != keys:
        raise DomainValueError(f"canonical {label} fields are invalid")


def _validate_snapshot_source_size(
    *,
    target: TargetId,
    metadata: NormalizedMetadata,
    files: tuple[FileReference, ...],
    cover: CoverReference | None,
    provenance: ObservationProvenance,
    concurrency_token: ConcurrencyToken | None,
) -> None:
    collection_items = len(files) + sum(len(field.values) for field in metadata.fields)
    if collection_items > SNAPSHOT_CANONICAL_MAX_COLLECTION_ITEMS:
        raise DomainValueError("canonical snapshot source has too many collection items")
    values = [
        target.adapter.value,
        target.connection.value,
        target.library.value,
        target.external_item.value,
        provenance.adapter_release.adapter.value,
        provenance.adapter_release.version.value,
        provenance.raw_observation.payload_fingerprint.digest,
    ]
    values.extend(value for field in metadata.fields for value in field.values)
    values.extend(part for file in files for part in (file.mount, file.relative_path))
    if cover is not None:
        values.append(cover.opaque_reference)
        if cover.content_fingerprint is not None:
            values.append(cover.content_fingerprint.digest)
    if concurrency_token is not None:
        values.append(concurrency_token.value)
    byte_count = 0
    for value in values:
        if _contains_surrogate(value):
            raise DomainValueError("canonical snapshot source contains invalid Unicode")
        if len(value) > SNAPSHOT_CANONICAL_SOURCE_MAX_BYTES:
            raise DomainValueError("canonical snapshot source exceeds the size limit")
        byte_count += len(value.encode("utf-8"))
        if byte_count > SNAPSHOT_CANONICAL_SOURCE_MAX_BYTES:
            raise DomainValueError("canonical snapshot source exceeds the size limit")


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)
