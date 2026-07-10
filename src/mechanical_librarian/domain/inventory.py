"""Immutable library inventory entities and adapter capability declarations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from .identifiers import (
    AdapterId,
    AdapterVersion,
    ConnectionId,
    DomainValueError,
    Fingerprint,
    LibraryId,
    SnapshotId,
    TargetId,
)


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
        if not self.opaque_reference or len(self.opaque_reference) > 512:
            raise DomainValueError("cover reference must be a non-empty value up to 512 characters")

    def __repr__(self) -> str:
        return "CoverReference(opaque_reference=<redacted>, content_fingerprint=<redacted>)"


@dataclass(frozen=True)
class MetadataField:
    name: FieldName
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.values or any(not value or value.strip() != value for value in self.values):
            raise DomainValueError("metadata field values must be non-empty trimmed strings")


@dataclass(frozen=True)
class NormalizedMetadata:
    """Immutable normalized fields; raw adapter payloads stay separate."""

    fields: tuple[MetadataField, ...]

    def __post_init__(self) -> None:
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
        return "RawObservation(adapter=<redacted>, payload_fingerprint=<redacted>, artifact_reference=<redacted>)"


@dataclass(frozen=True)
class ObservationProvenance:
    adapter_version: AdapterVersion
    observed_at: datetime
    raw_observation: RawObservation

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise DomainValueError("observations must use timezone-aware timestamps")


@dataclass(frozen=True)
class LibraryItem:
    """Stable target identity and lifecycle, separate from changing snapshots."""

    target: TargetId
    media_type: MediaType
    lifecycle: ItemLifecycle
    snapshot_ids: tuple[SnapshotId, ...] = ()

    def __post_init__(self) -> None:
        if len(self.snapshot_ids) != len(set(self.snapshot_ids)):
            raise DomainValueError("library item cannot reference a snapshot more than once")


@dataclass(frozen=True)
class ItemSnapshot:
    """One immutable service observation for exactly one target."""

    id: SnapshotId
    target: TargetId
    fingerprint: Fingerprint
    metadata: NormalizedMetadata
    files: tuple[FileReference, ...]
    cover: CoverReference | None
    provenance: ObservationProvenance
    service_version: AdapterVersion

    def __post_init__(self) -> None:
        if len(self.files) != len(set(self.files)):
            raise DomainValueError("snapshot cannot contain duplicate file references")
