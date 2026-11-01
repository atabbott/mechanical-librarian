"""Immutable, bounded vocabulary for transient local-media evidence.

This module deliberately does not reuse the persistence-oriented
``EvidenceRecord``.  Local extractors must be able to retain repeated source
observations, declared order and path-free citations before any later policy or
persistence projection is considered.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable

from mechanical_librarian.domain.cover_evidence import EpubCoverFallback
from mechanical_librarian.domain.epub_inspection import (
    EpubInspectionOutcome,
    EpubObservationApplicability,
    EpubPackageLimits,
    epub_file_lineage,
)
from mechanical_librarian.domain.epub_source import FileReference
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_code,
    validate_projection_text,
    validate_text,
)
from mechanical_librarian.domain.identifiers import (
    CompatibilityVersion,
    ExtractorVersion,
    Fingerprint,
    SchemaVersion,
    TargetId,
)

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


class EpubPackageCapability(StrEnum):
    PACKAGE = "package"
    OPENING_MATTER = "opening_matter"


class EpubPackageSourceError(EvidenceValueError):
    """The authorized byte source failed independently of parser limits."""


class EpubPackageCitationSection(StrEnum):
    PACKAGE = "package"
    METADATA = "metadata"
    MANIFEST = "manifest"
    SPINE = "spine"
    GUIDE = "guide"
    NAVIGATION = "navigation"
    COVER = "cover"
    ENCRYPTION = "encryption"


class EpubPackageField(StrEnum):
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


class EpubPackageDeclarationKind(StrEnum):
    ROOTFILE = "rootfile"
    PACKAGE = "package"
    MANIFEST_ITEM = "manifest_item"
    SPINE_ITEM = "spine_item"
    SPINE_SLOT = "spine_slot"
    GUIDE_REFERENCE = "guide_reference"
    NAVIGATION = "navigation"
    NCX = "ncx"
    COVER = "cover"
    ENCRYPTION = "encryption"


_FIELD_ORDER = {field: index for index, field in enumerate(EpubPackageField)}


@dataclass(frozen=True, repr=False)
class EpubPackageCancellation:
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
        return "EpubPackageCancellation(<typed>)"


@dataclass(frozen=True, repr=False)
class EpubPackageSource:
    """Random-access read view bounded to one already-authorized source."""

    length: int
    _reader: Callable[[int, int], bytes]

    def __post_init__(self) -> None:
        if (
            type(self.length) is not int
            or self.length < 0
            or not callable(self._reader)
        ):
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
            raise EpubPackageSourceError("local evidence source read failed") from None
        if type(value) is not bytes or len(value) > maximum_bytes:
            raise EpubPackageSourceError(
                "local evidence source returned an invalid read"
            )
        return value

    def __repr__(self) -> str:
        return f"EpubPackageSource(length={self.length}, reader=<redacted>)"


@dataclass(frozen=True)
class EpubPackageRequest:
    target: TargetId
    file: FileReference
    source: EpubPackageSource
    expected_source: Fingerprint
    capability: EpubPackageCapability
    limits: EpubPackageLimits
    deadline: datetime
    monotonic_deadline: float
    cancellation: EpubPackageCancellation

    def __post_init__(self) -> None:
        if (
            type(self.target) is not TargetId
            or type(self.file) is not FileReference
            or type(self.source) is not EpubPackageSource
            or type(self.expected_source) is not Fingerprint
            or type(self.capability) is not EpubPackageCapability
            or type(self.limits) is not EpubPackageLimits
            or type(self.deadline) is not datetime
            or self.deadline.tzinfo is None
            or self.deadline.utcoffset() is None
            or type(self.monotonic_deadline) is not float
            or self.monotonic_deadline <= 0
            or type(self.cancellation) is not EpubPackageCancellation
        ):
            raise EvidenceValueError("local evidence request is malformed")
        if self.source.length > self.limits.source_archive_bytes:
            raise EvidenceValueError(
                "local evidence source exceeds the requested limit"
            )
        self.deadline.astimezone(timezone.utc)


@dataclass(frozen=True)
class EpubPackageCitation:
    source_fingerprint: Fingerprint
    resource_identity: str
    section: EpubPackageCitationSection
    expanded_name: str
    source_ordinal: int
    selector: str | None = None

    def __post_init__(self) -> None:
        if type(self.source_fingerprint) is not Fingerprint:
            raise EvidenceValueError("citation source fingerprint is malformed")
        _digest(self.resource_identity, "citation resource identity")
        if type(self.section) is not EpubPackageCitationSection:
            raise EvidenceValueError("citation section is malformed")
        _expanded_name(self.expanded_name)
        if (
            type(self.source_ordinal) is not int
            or not 1 <= self.source_ordinal <= 1_000_000
        ):
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
class EpubPackageObservation:
    field: EpubPackageField
    raw_value: str
    normalized_value: str | None
    applicability: EpubObservationApplicability
    citation: EpubPackageCitation
    schema_version: SchemaVersion
    extractor_version: ExtractorVersion
    compatibility_version: CompatibilityVersion
    declared_display_sequence: int | None = None
    language: str | None = None
    same_file_lineage: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.field) is not EpubPackageField
            or type(self.applicability) is not EpubObservationApplicability
            or type(self.citation) is not EpubPackageCitation
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
            "EpubPackageObservation(field="
            f"{self.field.value!r}, value=<redacted>, citation=<typed>)"
        )


@dataclass(frozen=True)
class EpubPackageWarning:
    code: str
    citation: EpubPackageCitation | None = None

    def __post_init__(self) -> None:
        validate_code(self.code, "local evidence warning code")
        if self.citation is not None and type(self.citation) is not EpubPackageCitation:
            raise EvidenceValueError("local evidence warning citation is malformed")

    @property
    def sort_key(self) -> tuple[object, ...]:
        return (self.code, () if self.citation is None else self.citation.identity)


@dataclass(frozen=True)
class EpubPackageDeclaration:
    kind: EpubPackageDeclarationKind
    source_ordinal: int
    citation: EpubPackageCitation
    resource_identity: str | None = None
    identifier: str | None = None
    media_type: str | None = None
    properties: tuple[str, ...] = ()
    linear: bool = True
    spine_position: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not EpubPackageDeclarationKind
            or type(self.citation) is not EpubPackageCitation
        ):
            raise EvidenceValueError("local evidence declaration is malformed")
        if type(self.linear) is not bool:
            raise EvidenceValueError("declaration linearity is malformed")
        if self.spine_position is not None and (
            type(self.spine_position) is not int or self.spine_position < 1
        ):
            raise EvidenceValueError("declaration spine position is malformed")
        if (
            type(self.source_ordinal) is not int
            or not 1 <= self.source_ordinal <= 1_000_000
        ):
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
        if len(set(self.properties)) != len(
            self.properties
        ) or self.properties != tuple(sorted(self.properties)):
            raise EvidenceValueError("declaration properties must be unique and sorted")


@dataclass(frozen=True)
class EpubPackageUsage:
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
        if any(
            type(value) is not int or value < 0 for value in self.to_data().values()
        ):
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
class EpubCoverFallbackTarget:
    """One cover fallback declaration, kept only inside the extraction child.

    ``resource_identity`` and ``media_type`` are the local manifest item the
    declaration names, or both ``None`` when it names none. The plan never
    leaves the child: only the closed fallback names reach a report.
    """

    fallback: EpubCoverFallback
    resource_identity: str | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        if type(self.fallback) is not EpubCoverFallback or (
            self.resource_identity is None
        ) != (self.media_type is None):
            raise EvidenceValueError("EPUB cover fallback is malformed")
        if self.resource_identity is not None:
            _digest(self.resource_identity, "EPUB cover fallback identity")
            validate_text(self.media_type, "EPUB cover fallback media type", 128)


@dataclass(frozen=True)
class EpubPackageReport:
    outcome: EpubInspectionOutcome
    capability: EpubPackageCapability
    limits: EpubPackageLimits
    source_fingerprint: Fingerprint | None = None
    observations: tuple[EpubPackageObservation, ...] = ()
    warnings: tuple[EpubPackageWarning, ...] = ()
    declarations: tuple[EpubPackageDeclaration, ...] = ()
    usage: EpubPackageUsage = EpubPackageUsage()
    # The cover fallback plan, present only when the package declares no valid
    # cover. It is internal to the child and never projected into a fact.
    cover_fallbacks: tuple[EpubCoverFallbackTarget, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not EpubInspectionOutcome
            or type(self.capability) is not EpubPackageCapability
            or type(self.limits) is not EpubPackageLimits
            or type(self.usage) is not EpubPackageUsage
        ):
            raise EvidenceValueError("local evidence report is malformed")
        if (
            self.source_fingerprint is not None
            and type(self.source_fingerprint) is not Fingerprint
        ):
            raise EvidenceValueError("local evidence source fingerprint is malformed")
        _typed_tuple(
            self.observations, EpubPackageObservation, MAX_OBSERVATIONS, "observations"
        )
        _typed_tuple(self.warnings, EpubPackageWarning, MAX_WARNINGS, "warnings")
        _typed_tuple(
            self.declarations, EpubPackageDeclaration, MAX_DECLARATIONS, "declarations"
        )
        _typed_tuple(
            self.cover_fallbacks,
            EpubCoverFallbackTarget,
            MAX_DECLARATIONS,
            "cover fallbacks",
        )
        if self.cover_fallbacks and (
            not self.outcome.succeeded
            or any(item.field is EpubPackageField.COVER for item in self.observations)
        ):
            raise EvidenceValueError(
                "a cover fallback plan requires a package without a selected cover"
            )
        if (
            self.outcome is EpubInspectionOutcome.SUCCESS
            and self.source_fingerprint is None
        ):
            raise EvidenceValueError(
                "successful local evidence requires a source fingerprint"
            )
        if not self.outcome.succeeded and (self.observations or self.declarations):
            raise EvidenceValueError(
                "failed local evidence cannot contain a partial report"
            )
        if self.outcome is EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE and (
            self.observations or self.declarations
        ):
            raise EvidenceValueError(
                "no-applicable-evidence cannot contain observations"
            )
        if self.observations != tuple(
            sorted(
                self.observations,
                key=lambda item: (item.citation.source_ordinal, item.citation.identity),
            )
        ):
            raise EvidenceValueError(
                "raw observations must retain source occurrence order"
            )
        if self.warnings != tuple(
            sorted(self.warnings, key=lambda item: item.sort_key)
        ):
            raise EvidenceValueError(
                "warnings must use canonical code and citation order"
            )
        if self.declarations != tuple(
            sorted(
                self.declarations,
                key=lambda item: (item.source_ordinal, item.citation.identity),
            )
        ):
            raise EvidenceValueError("declarations must retain canonical source order")
        citations = (
            tuple(item.citation for item in self.observations)
            + tuple(item.citation for item in self.declarations)
            + tuple(
                item.citation for item in self.warnings if item.citation is not None
            )
        )
        if self.source_fingerprint is not None and any(
            item.source_fingerprint != self.source_fingerprint for item in citations
        ):
            raise EvidenceValueError(
                "local evidence citations must match source identity"
            )
        if (
            self.capability is EpubPackageCapability.PACKAGE
            and self.source_fingerprint is not None
            and any(
                item.same_file_lineage != epub_file_lineage(self.source_fingerprint)
                for item in self.observations
            )
        ):
            raise EvidenceValueError("package evidence file lineage is not derived")
        if len(
            {item.identity for item in (entry.citation for entry in self.observations)}
        ) != len(self.observations):
            raise EvidenceValueError(
                "local evidence observation citations must be unique"
            )
        if any(
            item.source_ordinal != item.citation.source_ordinal
            for item in self.declarations
        ):
            raise EvidenceValueError("declaration and citation order must agree")
        counts: dict[EpubPackageField, int] = {}
        for observation in self.observations:
            counts[observation.field] = counts.get(observation.field, 0) + 1
        if len(counts) > MAX_FACT_FIELDS or any(
            count > MAX_VALUES_PER_FIELD for count in counts.values()
        ):
            raise EvidenceValueError("local evidence observation bounds are exceeded")

    @property
    def succeeded(self) -> bool:
        return self.outcome.succeeded

    @property
    def normalized_observations(self) -> tuple[EpubPackageObservation, ...]:
        return tuple(
            sorted(self.observations, key=lambda item: item.normalized_sort_key)
        )


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise EvidenceValueError(f"{label} must be a lowercase SHA-256 digest")
    return value


def epub_resource_identity(normalized_member_name: str) -> str:
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
        validate_projection_text(namespace, "citation expanded namespace", 512)
        validate_projection_text(local_name, "citation expanded local name", 512)
    elif _XML_NAME.fullmatch(text) is None:
        raise EvidenceValueError("citation expanded name is malformed")
    else:
        validate_projection_text(text, "citation expanded local name", 512)
    return text


def _typed_tuple(
    values: object, item_type: type[Any], maximum: int, label: str
) -> None:
    if (
        type(values) is not tuple
        or len(values) > maximum
        or any(type(item) is not item_type for item in values)
    ):
        raise EvidenceValueError(f"local evidence {label} are malformed")


__all__ = [
    "EpubCoverFallbackTarget",
    "EpubPackageCitationSection",
    "EpubPackageDeclarationKind",
    "EpubPackageLimits",
    "EpubPackageCancellation",
    "EpubPackageCapability",
    "EpubPackageCitation",
    "EpubPackageDeclaration",
    "EpubPackageField",
    "EpubPackageObservation",
    "EpubInspectionOutcome",
    "EpubPackageReport",
    "EpubPackageRequest",
    "EpubPackageSource",
    "EpubPackageSourceError",
    "EpubPackageUsage",
    "EpubPackageWarning",
    "EpubObservationApplicability",
    "epub_file_lineage",
    "epub_resource_identity",
]
