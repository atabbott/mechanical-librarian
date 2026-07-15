"""Immutable, bounded vocabulary for transient local-media evidence.

This module deliberately does not reuse the persistence-oriented
``EvidenceRecord``.  Local extractors must be able to retain repeated source
observations, declared order and path-free citations before any later policy or
persistence projection is considered.
"""

from __future__ import annotations

import json
import re
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Callable, ClassVar, Self

from .evidence_bounds import (
    EvidenceValueError,
    validate_code,
    validate_projection_text,
    validate_text,
)
from .identifiers import (
    CompatibilityVersion,
    ExtractorVersion,
    Fingerprint,
    SchemaVersion,
    TargetId,
)
from .inventory import FileReference


LOCAL_EVIDENCE_REPORT_SCHEMA = SchemaVersion("local-evidence-report-v1")
LOCAL_EVIDENCE_LIMIT_PROFILE = "epub-package-limits-v1"

MAX_FACT_FIELDS = 64
MAX_OBSERVATIONS = 256
MAX_VALUES_PER_FIELD = 32
MAX_WARNINGS = 32
MAX_DECLARATIONS = 8_192
MAX_RAW_VALUE_CHARACTERS = 16_384
MAX_NORMALIZED_VALUE_CHARACTERS = 16_384
MAX_PUBLIC_REPORT_BYTES = 524_288

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LANGUAGE = re.compile(
    r"^(?:[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,7}|"
    r"[xX](?:-[A-Za-z0-9]{1,8}){1,7})$"
)
_XML_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$")


class LocalEvidenceCapability(StrEnum):
    PACKAGE = "package"
    OPENING_MATTER = "opening_matter"
    COVER = "cover"
    AUDIO_TAGS = "audio_tags"
    CHAPTERS = "chapters"
    DURATION = "duration"
    HEALTH = "health"
    WRITE_BACK = "write_back"


class LocalEvidenceOutcome(StrEnum):
    INVALID_INVOCATION = "invalid_invocation"
    SUCCESS = "success"
    NO_APPLICABLE_EVIDENCE = "no_applicable_evidence"
    INVALID_CONFIGURATION = "invalid_configuration"
    INVALID_REFERENCE = "invalid_reference"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNSUPPORTED_COMPRESSION = "unsupported_compression"
    UNSUPPORTED_MULTIPLE_RENDITIONS = "unsupported_multiple_renditions"
    ENCRYPTED = "encrypted"
    MALFORMED = "malformed"
    INTEGRITY_FAILURE = "integrity_failure"
    RESOURCE_LIMIT = "resource_limit"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    UNSTABLE_SOURCE = "unstable_source"
    UNAVAILABLE_SOURCE = "unavailable_source"
    CHILD_FAILURE = "child_failure"
    INTERNAL_FAILURE = "internal_failure"

    @property
    def succeeded(self) -> bool:
        return self in {self.SUCCESS, self.NO_APPLICABLE_EVIDENCE}


class ObservationApplicability(StrEnum):
    WORK = "work"
    EDITION = "edition"
    FILE = "file"
    UNKNOWN = "unknown"
    INAPPLICABLE = "inapplicable"


class LocalEvidenceSourceError(EvidenceValueError):
    """The authorized byte source failed independently of parser limits."""


class CitationSection(StrEnum):
    PACKAGE = "package"
    METADATA = "metadata"
    MANIFEST = "manifest"
    SPINE = "spine"
    GUIDE = "guide"
    NAVIGATION = "navigation"
    COVER = "cover"
    ENCRYPTION = "encryption"


class LocalEvidenceField(StrEnum):
    PACKAGE_IDENTIFIER = "package_identifier"
    PACKAGE_VERSION = "package_version"
    TITLE = "title"
    SUBTITLE = "subtitle"
    CREATOR = "creator"
    CREATOR_ROLE = "creator_role"
    CONTRIBUTOR = "contributor"
    CONTRIBUTOR_ROLE = "contributor_role"
    SORT_KEY = "sort_key"
    LANGUAGE = "language"
    PUBLISHER = "publisher"
    PUBLICATION_DATE = "publication_date"
    MODIFICATION_DATE = "modification_date"
    COPYRIGHT_DATE = "copyright_date"
    CREATION_DATE = "creation_date"
    SUBJECT = "subject"
    DESCRIPTION = "description"
    RIGHTS = "rights"
    SOURCE = "source"
    RELATION = "relation"
    TYPE = "type"
    IDENTIFIER = "identifier"
    IDENTIFIER_SCHEME = "identifier_scheme"
    DATE_EVENT = "date_event"
    COLLECTION = "collection"
    COLLECTION_POSITION = "collection_position"
    FORMAT = "format"
    REGION = "region"
    EDITION = "edition"
    COVER = "cover"
    NAVIGATION = "navigation"
    NCX = "ncx"
    GUIDE = "guide"
    MANIFEST = "manifest"
    SPINE = "spine"


class DeclarationKind(StrEnum):
    ROOTFILE = "rootfile"
    PACKAGE = "package"
    MANIFEST_ITEM = "manifest_item"
    SPINE_ITEM = "spine_item"
    GUIDE_REFERENCE = "guide_reference"
    NAVIGATION = "navigation"
    NCX = "ncx"
    COVER = "cover"
    ENCRYPTION = "encryption"


_FIELD_ORDER = {field: index for index, field in enumerate(LocalEvidenceField)}


@dataclass(frozen=True)
class EpubPackageLimits:
    """Configured values bounded by immutable ``epub-package-limits-v1`` caps."""

    source_archive_bytes: int = 268_435_456
    archive_entries: int = 2_048
    compressed_bytes_per_member: int = 268_435_456
    expanded_bytes_per_member: int = 268_435_456
    expanded_bytes_per_archive: int = 1_073_741_824
    compression_ratio_per_member: int = 200
    container_xml_bytes: int = 65_536
    opf_package_bytes: int = 4_194_304
    xml_nodes_per_document: int = 50_000
    xml_attributes_per_document: int = 100_000
    attributes_per_element: int = 64
    xml_depth: int = 64
    decoded_xml_text_characters: int = 262_144
    metadata_nodes: int = 512
    manifest_items: int = 4_096
    spine_items: int = 4_096
    guide_references: int = 256
    prefix_mappings: int = 64

    HARD_CAPS: ClassVar[Mapping[str, int]] = MappingProxyType({
        "source_archive_bytes": 536_870_912,
        "archive_entries": 8_192,
        "compressed_bytes_per_member": 536_870_912,
        "expanded_bytes_per_member": 536_870_912,
        "expanded_bytes_per_archive": 2_147_483_648,
        "compression_ratio_per_member": 1_000,
        "container_xml_bytes": 1_048_576,
        "opf_package_bytes": 16_777_216,
        "xml_nodes_per_document": 200_000,
        "xml_attributes_per_document": 400_000,
        "attributes_per_element": 256,
        "xml_depth": 128,
        "decoded_xml_text_characters": 4_194_304,
        "metadata_nodes": 2_048,
        "manifest_items": 8_192,
        "spine_items": 8_192,
        "guide_references": 512,
        "prefix_mappings": 256,
    })
    MEMBER_NAME_UTF8_BYTES_HARD_CAP = 1_024
    COMPONENT_NAME_UTF8_BYTES_HARD_CAP = 255
    ARCHIVE_NESTING_DEPTH = 1
    MAX_REPORT_BYTES = MAX_PUBLIC_REPORT_BYTES
    MAX_OBSERVATIONS = MAX_OBSERVATIONS
    MAX_VALUES_PER_FIELD = MAX_VALUES_PER_FIELD
    MAX_WARNINGS = MAX_WARNINGS
    MAX_RAW_VALUE_CHARACTERS = MAX_RAW_VALUE_CHARACTERS
    MAX_NORMALIZED_VALUE_CHARACTERS = MAX_NORMALIZED_VALUE_CHARACTERS

    def __post_init__(self) -> None:
        for name, hard_cap in self.HARD_CAPS.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= hard_cap:
                raise EvidenceValueError("EPUB package limit is outside its immutable cap")

    @property
    def profile(self) -> str:
        return LOCAL_EVIDENCE_LIMIT_PROFILE

    def to_data(self) -> dict[str, int | str]:
        return {
            "profile": self.profile,
            **{name: getattr(self, name) for name in self.HARD_CAPS},
        }


@dataclass(frozen=True, repr=False)
class LocalEvidenceCancellation:
    id: str
    _reader: Callable[[], bool]

    def __post_init__(self) -> None:
        validate_code(self.id, "local evidence cancellation ID")
        if not callable(self._reader) or type(self._reader()) is not bool:
            raise EvidenceValueError("local evidence cancellation context is malformed")

    def is_cancelled(self) -> bool:
        value = self._reader()
        if type(value) is not bool:
            raise EvidenceValueError("local evidence cancellation context is malformed")
        return value

    def __repr__(self) -> str:
        return "LocalEvidenceCancellation(<typed>)"


@dataclass(frozen=True, repr=False)
class LocalEvidenceSource:
    """Random-access read view bounded to one already-authorized source."""

    length: int
    _reader: Callable[[int, int], bytes]

    def __post_init__(self) -> None:
        if type(self.length) is not int or self.length < 0 or not callable(self._reader):
            raise EvidenceValueError("local evidence source is malformed")

    def read_at(self, offset: int, maximum_bytes: int) -> bytes:
        if (
            type(offset) is not int
            or type(maximum_bytes) is not int
            or offset < 0
            or maximum_bytes < 0
            or offset > self.length
            or maximum_bytes > self.length - offset
        ):
            raise EvidenceValueError("local evidence read exceeds its source bounds")
        try:
            value = self._reader(offset, maximum_bytes)
        except Exception:
            raise LocalEvidenceSourceError("local evidence source read failed") from None
        if type(value) is not bytes or len(value) > maximum_bytes:
            raise LocalEvidenceSourceError("local evidence source returned an invalid read")
        return value

    def __repr__(self) -> str:
        return f"LocalEvidenceSource(length={self.length}, reader=<redacted>)"


@dataclass(frozen=True)
class LocalEvidenceRequest:
    target: TargetId
    file: FileReference
    source: LocalEvidenceSource
    expected_source: Fingerprint
    capability: LocalEvidenceCapability
    limits: EpubPackageLimits
    deadline: datetime
    monotonic_deadline: float
    cancellation: LocalEvidenceCancellation

    def __post_init__(self) -> None:
        if (
            type(self.target) is not TargetId
            or type(self.file) is not FileReference
            or type(self.source) is not LocalEvidenceSource
            or type(self.expected_source) is not Fingerprint
            or type(self.capability) is not LocalEvidenceCapability
            or type(self.limits) is not EpubPackageLimits
            or type(self.deadline) is not datetime
            or self.deadline.tzinfo is None
            or self.deadline.utcoffset() is None
            or type(self.monotonic_deadline) is not float
            or self.monotonic_deadline <= 0
            or type(self.cancellation) is not LocalEvidenceCancellation
        ):
            raise EvidenceValueError("local evidence request is malformed")
        if self.source.length > self.limits.source_archive_bytes:
            raise EvidenceValueError("local evidence source exceeds the requested limit")
        self.deadline.astimezone(timezone.utc)


@dataclass(frozen=True)
class LocalEvidenceCitation:
    source_fingerprint: Fingerprint
    resource_identity: str
    section: CitationSection
    expanded_name: str
    source_ordinal: int
    selector: str | None = None

    def __post_init__(self) -> None:
        if type(self.source_fingerprint) is not Fingerprint:
            raise EvidenceValueError("citation source fingerprint is malformed")
        _digest(self.resource_identity, "citation resource identity")
        if type(self.section) is not CitationSection:
            raise EvidenceValueError("citation section is malformed")
        _expanded_name(self.expanded_name)
        if type(self.source_ordinal) is not int or not 1 <= self.source_ordinal <= 1_000_000:
            raise EvidenceValueError("citation source ordinal is invalid")
        if self.selector is not None:
            validate_projection_text(self.selector, "citation selector", 256)

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.source_fingerprint.schema.value,
            self.source_fingerprint.digest,
            self.resource_identity,
            self.section.value,
            self.expanded_name,
            self.source_ordinal,
            self.selector or "",
        )


@dataclass(frozen=True, repr=False)
class LocalEvidenceObservation:
    field: LocalEvidenceField
    raw_value: str
    normalized_value: str | None
    applicability: ObservationApplicability
    citation: LocalEvidenceCitation
    schema_version: SchemaVersion
    extractor_version: ExtractorVersion
    compatibility_version: CompatibilityVersion
    declared_display_sequence: int | None = None
    language: str | None = None
    same_file_lineage: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.field) is not LocalEvidenceField
            or type(self.applicability) is not ObservationApplicability
            or type(self.citation) is not LocalEvidenceCitation
            or type(self.schema_version) is not SchemaVersion
            or type(self.extractor_version) is not ExtractorVersion
            or type(self.compatibility_version) is not CompatibilityVersion
        ):
            raise EvidenceValueError("local evidence observation is malformed")
        validate_text(self.raw_value, "raw observation value", MAX_RAW_VALUE_CHARACTERS)
        validate_projection_text(
            self.raw_value,
            "public raw observation value",
            MAX_RAW_VALUE_CHARACTERS,
        )
        if self.normalized_value is not None:
            validate_text(
                self.normalized_value,
                "normalized observation value",
                MAX_NORMALIZED_VALUE_CHARACTERS,
            )
            validate_projection_text(
                self.normalized_value,
                "public normalized observation value",
                MAX_NORMALIZED_VALUE_CHARACTERS,
            )
        if self.declared_display_sequence is not None and (
            type(self.declared_display_sequence) is not int
            or not 0 <= self.declared_display_sequence <= 1_000_000
        ):
            raise EvidenceValueError("declared display sequence is invalid")
        if self.language is not None and (
            type(self.language) is not str or _LANGUAGE.fullmatch(self.language) is None
        ):
            raise EvidenceValueError("observation language is malformed")
        if self.same_file_lineage is not None:
            _digest(self.same_file_lineage, "same-file lineage")

    @property
    def normalized_sort_key(self) -> tuple[object, ...]:
        return (
            _FIELD_ORDER[self.field],
            self.declared_display_sequence is None,
            self.declared_display_sequence or 0,
            self.citation.source_ordinal,
            self.citation.identity,
        )

    def __repr__(self) -> str:
        return (
            "LocalEvidenceObservation(field="
            f"{self.field.value!r}, value=<redacted>, citation=<typed>)"
        )


@dataclass(frozen=True)
class LocalEvidenceWarning:
    code: str
    citation: LocalEvidenceCitation | None = None

    def __post_init__(self) -> None:
        validate_code(self.code, "local evidence warning code")
        if self.citation is not None and type(self.citation) is not LocalEvidenceCitation:
            raise EvidenceValueError("local evidence warning citation is malformed")

    @property
    def sort_key(self) -> tuple[object, ...]:
        return (self.code, () if self.citation is None else self.citation.identity)


@dataclass(frozen=True)
class LocalEvidenceDeclaration:
    kind: DeclarationKind
    source_ordinal: int
    citation: LocalEvidenceCitation
    resource_identity: str | None = None
    identifier: str | None = None
    media_type: str | None = None
    properties: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.kind) is not DeclarationKind or type(self.citation) is not LocalEvidenceCitation:
            raise EvidenceValueError("local evidence declaration is malformed")
        if type(self.source_ordinal) is not int or not 1 <= self.source_ordinal <= 1_000_000:
            raise EvidenceValueError("declaration source ordinal is invalid")
        if self.resource_identity is not None:
            _digest(self.resource_identity, "declaration resource identity")
        if self.identifier is not None:
            validate_text(self.identifier, "declaration identifier", 256)
            validate_projection_text(
                self.identifier, "public declaration identifier", 256
            )
        if self.media_type is not None:
            validate_text(self.media_type, "declaration media type", 128)
            validate_projection_text(
                self.media_type, "public declaration media type", 128
            )
        if type(self.properties) is not tuple or len(self.properties) > 64:
            raise EvidenceValueError("declaration properties are malformed")
        for value in self.properties:
            validate_code(value, "declaration property")
            validate_projection_text(value, "public declaration property", 64)
        if len(set(self.properties)) != len(self.properties) or self.properties != tuple(
            sorted(self.properties)
        ):
            raise EvidenceValueError("declaration properties must be unique and sorted")


@dataclass(frozen=True)
class LocalEvidenceUsage:
    """Deterministic logical counters; runtime peaks and timings stay out of reports."""

    archive_entries: int = 0
    opened_members: int = 0
    compressed_bytes: int = 0
    expanded_bytes: int = 0
    xml_nodes: int = 0
    xml_attributes: int = 0
    xml_text_characters: int = 0
    metadata_nodes: int = 0
    manifest_items: int = 0
    spine_items: int = 0
    guide_references: int = 0
    prefix_mappings: int = 0

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 0 for value in self.to_data().values()):
            raise EvidenceValueError("local evidence usage is malformed")

    def to_data(self) -> dict[str, int]:
        return {
            "archive_entries": self.archive_entries,
            "opened_members": self.opened_members,
            "compressed_bytes": self.compressed_bytes,
            "expanded_bytes": self.expanded_bytes,
            "xml_nodes": self.xml_nodes,
            "xml_attributes": self.xml_attributes,
            "xml_text_characters": self.xml_text_characters,
            "metadata_nodes": self.metadata_nodes,
            "manifest_items": self.manifest_items,
            "spine_items": self.spine_items,
            "guide_references": self.guide_references,
            "prefix_mappings": self.prefix_mappings,
        }


@dataclass(frozen=True)
class LocalEvidenceArtifactReference:
    kind: str
    identity: str

    def __post_init__(self) -> None:
        validate_code(self.kind, "artifact kind")
        _digest(self.identity, "artifact identity")


@dataclass(frozen=True)
class LocalEvidenceReport:
    outcome: LocalEvidenceOutcome
    capability: LocalEvidenceCapability
    limits: EpubPackageLimits
    source_fingerprint: Fingerprint | None = None
    observations: tuple[LocalEvidenceObservation, ...] = ()
    warnings: tuple[LocalEvidenceWarning, ...] = ()
    declarations: tuple[LocalEvidenceDeclaration, ...] = ()
    usage: LocalEvidenceUsage = LocalEvidenceUsage()
    artifact: LocalEvidenceArtifactReference | None = None
    schema_version: SchemaVersion = LOCAL_EVIDENCE_REPORT_SCHEMA

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not LocalEvidenceOutcome
            or type(self.capability) is not LocalEvidenceCapability
            or type(self.limits) is not EpubPackageLimits
            or type(self.usage) is not LocalEvidenceUsage
            or type(self.schema_version) is not SchemaVersion
            or self.schema_version != LOCAL_EVIDENCE_REPORT_SCHEMA
        ):
            raise EvidenceValueError("local evidence report is malformed")
        if self.source_fingerprint is not None and type(self.source_fingerprint) is not Fingerprint:
            raise EvidenceValueError("local evidence source fingerprint is malformed")
        _typed_tuple(self.observations, LocalEvidenceObservation, MAX_OBSERVATIONS, "observations")
        _typed_tuple(self.warnings, LocalEvidenceWarning, MAX_WARNINGS, "warnings")
        _typed_tuple(self.declarations, LocalEvidenceDeclaration, MAX_DECLARATIONS, "declarations")
        if self.artifact is not None and type(self.artifact) is not LocalEvidenceArtifactReference:
            raise EvidenceValueError("local evidence artifact reference is malformed")
        if self.capability is LocalEvidenceCapability.PACKAGE and self.artifact is not None:
            raise EvidenceValueError("package evidence must not retain an artifact")
        if self.outcome is LocalEvidenceOutcome.SUCCESS and self.source_fingerprint is None:
            raise EvidenceValueError("successful local evidence requires a source fingerprint")
        if not self.outcome.succeeded and (self.observations or self.declarations):
            raise EvidenceValueError("failed local evidence cannot contain a partial report")
        if self.outcome is LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE and (
            self.observations or self.declarations
        ):
            raise EvidenceValueError("no-applicable-evidence cannot contain observations")
        if self.observations != tuple(
            sorted(
                self.observations,
                key=lambda item: (item.citation.source_ordinal, item.citation.identity),
            )
        ):
            raise EvidenceValueError("raw observations must retain source occurrence order")
        if self.warnings != tuple(sorted(self.warnings, key=lambda item: item.sort_key)):
            raise EvidenceValueError("warnings must use canonical code and citation order")
        if self.declarations != tuple(
            sorted(self.declarations, key=lambda item: (item.source_ordinal, item.citation.identity))
        ):
            raise EvidenceValueError("declarations must retain canonical source order")
        citations = (
            tuple(item.citation for item in self.observations)
            + tuple(item.citation for item in self.declarations)
            + tuple(item.citation for item in self.warnings if item.citation is not None)
        )
        if self.source_fingerprint is not None and any(
            item.source_fingerprint != self.source_fingerprint for item in citations
        ):
            raise EvidenceValueError("local evidence citations must match source identity")
        if (
            self.capability is LocalEvidenceCapability.PACKAGE
            and self.source_fingerprint is not None
            and any(
                item.same_file_lineage
                != local_file_lineage(self.source_fingerprint)
                for item in self.observations
            )
        ):
            raise EvidenceValueError("package evidence file lineage is not derived")
        if len({item.identity for item in (entry.citation for entry in self.observations)}) != len(
            self.observations
        ):
            raise EvidenceValueError("local evidence observation citations must be unique")
        if any(item.source_ordinal != item.citation.source_ordinal for item in self.declarations):
            raise EvidenceValueError("declaration and citation order must agree")
        counts: dict[LocalEvidenceField, int] = {}
        for observation in self.observations:
            counts[observation.field] = counts.get(observation.field, 0) + 1
        if len(counts) > MAX_FACT_FIELDS or any(
            count > MAX_VALUES_PER_FIELD for count in counts.values()
        ):
            raise EvidenceValueError("local evidence observation bounds are exceeded")
        serialized = self._canonical_json(check_size=False)
        if len(serialized.encode("utf-8")) > MAX_PUBLIC_REPORT_BYTES:
            raise EvidenceValueError("local evidence report exceeds its byte limit")

    @property
    def succeeded(self) -> bool:
        return self.outcome.succeeded

    @property
    def normalized_observations(self) -> tuple[LocalEvidenceObservation, ...]:
        return tuple(sorted(self.observations, key=lambda item: item.normalized_sort_key))

    def to_data(self) -> dict[str, object]:
        return {
            "artifact": None if self.artifact is None else _artifact_data(self.artifact),
            "capability": self.capability.value,
            "declarations": [_declaration_data(item) for item in self.declarations],
            "limits": self.limits.to_data(),
            "normalized_observations": [
                _observation_data(item) for item in self.normalized_observations
            ],
            "observations": [_observation_data(item) for item in self.observations],
            "outcome": self.outcome.value,
            "schema": self.schema_version.value,
            "source_fingerprint": (
                None if self.source_fingerprint is None else _fingerprint_data(self.source_fingerprint)
            ),
            "usage": self.usage.to_data(),
            "warnings": [_warning_data(item) for item in self.warnings],
        }

    def _canonical_json(self, *, check_size: bool = True) -> str:
        serialized = json.dumps(
            self.to_data(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        if check_size and len(serialized.encode("utf-8")) > MAX_PUBLIC_REPORT_BYTES:
            raise EvidenceValueError("local evidence report exceeds its byte limit")
        return serialized

    def to_json(self) -> str:
        return self._canonical_json()

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        if type(serialized) is not str or len(serialized.encode("utf-8")) > MAX_PUBLIC_REPORT_BYTES:
            raise EvidenceValueError("local evidence report JSON is invalid")
        try:
            value = json.loads(serialized, object_pairs_hook=_unique_object)
            report = _report_from_data(value)
        except EvidenceValueError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
            raise EvidenceValueError("local evidence report JSON is invalid") from None
        if report.to_json() != serialized:
            raise EvidenceValueError("local evidence report JSON is not canonical")
        return report


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise EvidenceValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def local_resource_identity(normalized_member_name: str) -> str:
    """Hash a validated normalized internal member name for a path-free citation."""

    validate_text(normalized_member_name, "normalized member name", 1_024)
    encoded = normalized_member_name.encode("utf-8")
    parts = normalized_member_name.split("/")
    if (
        len(encoded) > EpubPackageLimits.MEMBER_NAME_UTF8_BYTES_HARD_CAP
        or normalized_member_name.startswith("/")
        or "\\" in normalized_member_name
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise EvidenceValueError("normalized member name is malformed")
    return hashlib.sha256(encoded).hexdigest()


def local_file_lineage(source: Fingerprint) -> str:
    """Return one capability-independent lineage for an immutable local file."""

    if type(source) is not Fingerprint:
        raise EvidenceValueError("local file lineage source is malformed")
    return hashlib.sha256(
        (source.schema.value + "\x00" + source.digest + "\x00local-file-v1").encode(
            "ascii"
        )
    ).hexdigest()


def _expanded_name(value: object) -> str:
    text = validate_text(value, "citation expanded name", 512)
    if text.startswith("{"):
        namespace, separator, local_name = text[1:].partition("}")
        lowered = namespace.casefold()
        if (
            not separator
            or not namespace
            or _XML_NAME.fullmatch(local_name) is None
            or lowered.startswith(("file:", "smb:", "nfs:", "afp:"))
            or "/users/" in lowered
            or "/home/" in lowered
            or "../" in namespace
            or "\\" in namespace
        ):
            raise EvidenceValueError("citation expanded name is malformed")
        validate_projection_text(
            namespace, "citation expanded namespace", 512
        )
        validate_projection_text(
            local_name, "citation expanded local name", 512
        )
    elif _XML_NAME.fullmatch(text) is None:
        raise EvidenceValueError("citation expanded name is malformed")
    else:
        validate_projection_text(text, "citation expanded local name", 512)
    return text


def _typed_tuple(values: object, item_type: type[Any], maximum: int, label: str) -> None:
    if type(values) is not tuple or len(values) > maximum or any(
        type(item) is not item_type for item in values
    ):
        raise EvidenceValueError(f"local evidence {label} are malformed")


def _require_object(value: object, keys: set[str], label: str) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise EvidenceValueError(f"{label} has unknown or missing fields")
    return value


def _require_list(value: object, label: str) -> list[object]:
    if type(value) is not list:
        raise EvidenceValueError(f"{label} must be a list")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceValueError("local evidence report contains a duplicate field")
        result[key] = value
    return result


def _fingerprint_data(value: Fingerprint) -> dict[str, str]:
    return {"algorithm": value.algorithm, "digest": value.digest, "schema": value.schema.value}


def _fingerprint_from_data(value: object) -> Fingerprint:
    item = _require_object(value, {"algorithm", "digest", "schema"}, "fingerprint")
    return Fingerprint(item["algorithm"], SchemaVersion(item["schema"]), item["digest"])


def _citation_data(value: LocalEvidenceCitation) -> dict[str, object]:
    return {
        "expanded_name": value.expanded_name,
        "resource_identity": value.resource_identity,
        "section": value.section.value,
        "selector": value.selector,
        "source_fingerprint": _fingerprint_data(value.source_fingerprint),
        "source_ordinal": value.source_ordinal,
    }


def _citation_from_data(value: object) -> LocalEvidenceCitation:
    item = _require_object(
        value,
        {
            "expanded_name",
            "resource_identity",
            "section",
            "selector",
            "source_fingerprint",
            "source_ordinal",
        },
        "citation",
    )
    return LocalEvidenceCitation(
        source_fingerprint=_fingerprint_from_data(item["source_fingerprint"]),
        resource_identity=item["resource_identity"],
        section=CitationSection(item["section"]),
        expanded_name=item["expanded_name"],
        source_ordinal=item["source_ordinal"],
        selector=item["selector"],
    )


def _observation_data(value: LocalEvidenceObservation) -> dict[str, object]:
    return {
        "applicability": value.applicability.value,
        "citation": _citation_data(value.citation),
        "compatibility_version": value.compatibility_version.value,
        "declared_display_sequence": value.declared_display_sequence,
        "extractor_version": value.extractor_version.value,
        "field": value.field.value,
        "language": value.language,
        "normalized_value": value.normalized_value,
        "raw_value": value.raw_value,
        "same_file_lineage": value.same_file_lineage,
        "schema_version": value.schema_version.value,
    }


def _observation_from_data(value: object) -> LocalEvidenceObservation:
    item = _require_object(
        value,
        {
            "applicability",
            "citation",
            "compatibility_version",
            "declared_display_sequence",
            "extractor_version",
            "field",
            "language",
            "normalized_value",
            "raw_value",
            "same_file_lineage",
            "schema_version",
        },
        "observation",
    )
    return LocalEvidenceObservation(
        field=LocalEvidenceField(item["field"]),
        raw_value=item["raw_value"],
        normalized_value=item["normalized_value"],
        applicability=ObservationApplicability(item["applicability"]),
        citation=_citation_from_data(item["citation"]),
        schema_version=SchemaVersion(item["schema_version"]),
        extractor_version=ExtractorVersion(item["extractor_version"]),
        compatibility_version=CompatibilityVersion(item["compatibility_version"]),
        declared_display_sequence=item["declared_display_sequence"],
        language=item["language"],
        same_file_lineage=item["same_file_lineage"],
    )


def _warning_data(value: LocalEvidenceWarning) -> dict[str, object]:
    return {
        "citation": None if value.citation is None else _citation_data(value.citation),
        "code": value.code,
    }


def _warning_from_data(value: object) -> LocalEvidenceWarning:
    item = _require_object(value, {"citation", "code"}, "warning")
    return LocalEvidenceWarning(
        item["code"],
        None if item["citation"] is None else _citation_from_data(item["citation"]),
    )


def _declaration_data(value: LocalEvidenceDeclaration) -> dict[str, object]:
    return {
        "citation": _citation_data(value.citation),
        "identifier": value.identifier,
        "kind": value.kind.value,
        "media_type": value.media_type,
        "properties": list(value.properties),
        "resource_identity": value.resource_identity,
        "source_ordinal": value.source_ordinal,
    }


def _declaration_from_data(value: object) -> LocalEvidenceDeclaration:
    item = _require_object(
        value,
        {
            "citation",
            "identifier",
            "kind",
            "media_type",
            "properties",
            "resource_identity",
            "source_ordinal",
        },
        "declaration",
    )
    return LocalEvidenceDeclaration(
        kind=DeclarationKind(item["kind"]),
        source_ordinal=item["source_ordinal"],
        citation=_citation_from_data(item["citation"]),
        resource_identity=item["resource_identity"],
        identifier=item["identifier"],
        media_type=item["media_type"],
        properties=tuple(_require_list(item["properties"], "declaration properties")),
    )


def _artifact_data(value: LocalEvidenceArtifactReference) -> dict[str, str]:
    return {"identity": value.identity, "kind": value.kind}


def _limits_from_data(value: object) -> EpubPackageLimits:
    keys = {"profile", *EpubPackageLimits.HARD_CAPS}
    item = _require_object(value, keys, "limits")
    if item["profile"] != LOCAL_EVIDENCE_LIMIT_PROFILE:
        raise EvidenceValueError("local evidence limit profile is unsupported")
    return EpubPackageLimits(**{name: item[name] for name in EpubPackageLimits.HARD_CAPS})


def _usage_from_data(value: object) -> LocalEvidenceUsage:
    keys = set(LocalEvidenceUsage().to_data())
    item = _require_object(value, keys, "usage")
    return LocalEvidenceUsage(**item)


def _report_from_data(value: object) -> LocalEvidenceReport:
    item = _require_object(
        value,
        {
            "artifact",
            "capability",
            "declarations",
            "limits",
            "normalized_observations",
            "observations",
            "outcome",
            "schema",
            "source_fingerprint",
            "usage",
            "warnings",
        },
        "local evidence report",
    )
    observations = tuple(
        _observation_from_data(entry)
        for entry in _require_list(item["observations"], "observations")
    )
    normalized = tuple(
        _observation_from_data(entry)
        for entry in _require_list(item["normalized_observations"], "normalized observations")
    )
    report = LocalEvidenceReport(
        outcome=LocalEvidenceOutcome(item["outcome"]),
        capability=LocalEvidenceCapability(item["capability"]),
        limits=_limits_from_data(item["limits"]),
        source_fingerprint=(
            None
            if item["source_fingerprint"] is None
            else _fingerprint_from_data(item["source_fingerprint"])
        ),
        observations=observations,
        warnings=tuple(
            _warning_from_data(entry)
            for entry in _require_list(item["warnings"], "warnings")
        ),
        declarations=tuple(
            _declaration_from_data(entry)
            for entry in _require_list(item["declarations"], "declarations")
        ),
        usage=_usage_from_data(item["usage"]),
        artifact=(
            None
            if item["artifact"] is None
            else LocalEvidenceArtifactReference(
                **_require_object(item["artifact"], {"identity", "kind"}, "artifact")
            )
        ),
        schema_version=SchemaVersion(item["schema"]),
    )
    if normalized != report.normalized_observations:
        raise EvidenceValueError("normalized observations are not canonically ordered")
    return report


__all__ = [
    "CitationSection",
    "DeclarationKind",
    "EpubPackageLimits",
    "LOCAL_EVIDENCE_LIMIT_PROFILE",
    "LOCAL_EVIDENCE_REPORT_SCHEMA",
    "LocalEvidenceArtifactReference",
    "LocalEvidenceCancellation",
    "LocalEvidenceCapability",
    "LocalEvidenceCitation",
    "LocalEvidenceDeclaration",
    "LocalEvidenceField",
    "LocalEvidenceObservation",
    "LocalEvidenceOutcome",
    "LocalEvidenceReport",
    "LocalEvidenceRequest",
    "LocalEvidenceSource",
    "LocalEvidenceSourceError",
    "LocalEvidenceUsage",
    "LocalEvidenceWarning",
    "ObservationApplicability",
    "local_file_lineage",
    "local_resource_identity",
]
