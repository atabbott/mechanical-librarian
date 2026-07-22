"""Private, closed values returned by the isolated EPUB inspector.

This vocabulary intentionally belongs to the single EPUB operation.  It is not
a media-evidence protocol or a future format extension point.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, ClassVar, Mapping

from mechanical_librarian.domain.evidence_bounds import EvidenceValueError
from mechanical_librarian.domain.identifiers import Fingerprint

if TYPE_CHECKING:
    from mechanical_librarian.domain.epub_opening import EpubOpeningReport


_CHECKSUMS = frozenset({"valid_isbn_10", "valid_isbn_13", "invalid", "not_applicable"})
_MEDIA = frozenset({"ebook", "print", "unknown", "not_applicable"})
_BASES = frozenset({"package_metadata", "opening_matter"})

EPUB_PACKAGE_LIMIT_PROFILE = "epub-package-limits-v1"
MAX_PUBLIC_REPORT_BYTES = 524_288
MAX_OBSERVATIONS = 256
MAX_VALUES_PER_FIELD = 32
MAX_WARNINGS = 32
MAX_RAW_VALUE_CHARACTERS = 16_384
MAX_NORMALIZED_VALUE_CHARACTERS = 16_384


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
        return EPUB_PACKAGE_LIMIT_PROFILE

    def to_data(self) -> dict[str, int | str]:
        return {
            "profile": self.profile,
            **{name: getattr(self, name) for name in self.HARD_CAPS},
        }

class EpubInspectionOutcome(StrEnum):
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


class EpubObservationApplicability(StrEnum):
    WORK = "work"
    EDITION = "edition"
    FILE = "file"
    UNKNOWN = "unknown"
    INAPPLICABLE = "inapplicable"


def epub_file_lineage(source: Fingerprint) -> str:
    if type(source) is not Fingerprint:
        raise EvidenceValueError("EPUB lineage source is malformed")
    return hashlib.sha256(
        (source.schema.value + "\x00" + source.digest + "\x00local-file-v1").encode("ascii")
    ).hexdigest()


class EpubInspectionField(StrEnum):
    TITLE = "title"
    ISBN = "isbn"


@dataclass(frozen=True, repr=False)
class EpubInspectionFact:
    """One bounded fact with its source and non-authoritative supporting span."""

    field: EpubInspectionField
    source: Fingerprint
    observed_value: str
    canonical_value: str | None
    supporting_context: str
    checksum: str
    media: str
    basis: str
    outcome: EpubInspectionOutcome = EpubInspectionOutcome.SUCCESS

    def __post_init__(self) -> None:
        if (
            type(self.field) is not EpubInspectionField
            or type(self.source) is not Fingerprint
            or type(self.observed_value) is not str
            or not self.observed_value
            or len(self.observed_value) > 280
            or (self.canonical_value is not None and (type(self.canonical_value) is not str or not self.canonical_value or len(self.canonical_value) > 280))
            or type(self.supporting_context) is not str
            or not self.supporting_context
            or len(self.supporting_context) > 280
            or self.checksum not in _CHECKSUMS
            or self.media not in _MEDIA
            or self.basis not in _BASES
            or type(self.outcome) is not EpubInspectionOutcome
        ):
            raise ValueError("EPUB inspection fact is malformed")
        if self.field is EpubInspectionField.TITLE and (
            self.canonical_value is None
            or self.checksum != "not_applicable"
            or self.media != "not_applicable"
        ):
            raise ValueError("EPUB title fact has identifier fields")
        if self.field is EpubInspectionField.ISBN and self.checksum.startswith("valid_") != (self.canonical_value is not None):
            raise ValueError("EPUB ISBN checksum does not match canonical value")

    def __repr__(self) -> str:
        return "EpubInspectionFact(<redacted>)"


@dataclass(frozen=True, repr=False)
class EpubInspection:
    """The one private projection consumed by the retained title report."""

    outcome: EpubInspectionOutcome
    source: Fingerprint | None
    facts: tuple[EpubInspectionFact, ...] = ()

    def __post_init__(self) -> None:
        if type(self.outcome) is not EpubInspectionOutcome or (
            self.source is not None and type(self.source) is not Fingerprint
        ) or type(self.facts) is not tuple or any(type(value) is not EpubInspectionFact for value in self.facts):
            raise ValueError("EPUB inspection is malformed")
        if self.outcome is EpubInspectionOutcome.SUCCESS and self.source is None:
            raise ValueError("successful EPUB inspection needs a source")
        if self.source is not None and any(value.source != self.source for value in self.facts):
            raise ValueError("EPUB inspection fact source is inconsistent")

    @classmethod
    def from_opening_report(cls, report: EpubOpeningReport) -> "EpubInspection":
        from mechanical_librarian.domain.epub_opening import EpubOpeningReport, OpeningField

        if type(report) is not EpubOpeningReport:
            raise ValueError("EPUB inspection requires a typed opening report")
        outcome = (
            EpubInspectionOutcome.SUCCESS
            if report.outcome is EpubInspectionOutcome.SUCCESS
            else EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
            if report.outcome is EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE
            else EpubInspectionOutcome.RESOURCE_LIMIT
            if report.outcome is EpubInspectionOutcome.RESOURCE_LIMIT
            else EpubInspectionOutcome.TIMEOUT
            if report.outcome is EpubInspectionOutcome.TIMEOUT
            else EpubInspectionOutcome.UNSTABLE_SOURCE
            if report.outcome is EpubInspectionOutcome.UNSTABLE_SOURCE
            else EpubInspectionOutcome.CHILD_FAILURE
        )
        source = report.source_fingerprint
        if source is None:
            return cls(outcome, None)
        facts: list[EpubInspectionFact] = []
        for claim in report.claims:
            if claim.applicability in {EpubObservationApplicability.INAPPLICABLE, EpubObservationApplicability.UNKNOWN}:
                continue
            if claim.field is OpeningField.TITLE and claim.normalized_value is not None:
                facts.append(EpubInspectionFact(EpubInspectionField.TITLE, source, claim.raw_value, claim.normalized_value, claim.citation.exact_excerpt, "not_applicable", "not_applicable", "opening_matter"))
            elif claim.field is OpeningField.IDENTIFIER:
                checksum = claim.identifier_type if claim.identifier_type in {"isbn_10", "isbn_13"} and claim.normalized_value is not None else "invalid"
                facts.append(EpubInspectionFact(EpubInspectionField.ISBN, source, claim.raw_value, claim.normalized_value, claim.citation.exact_excerpt, f"valid_{checksum}" if checksum != "invalid" else checksum, claim.applicability_label if claim.applicability_label in {"ebook", "print"} else "unknown", "opening_matter"))
        return cls(outcome, source, tuple(facts))

    def __repr__(self) -> str:
        return "EpubInspection(<redacted>)"


__all__ = ["EpubInspection", "EpubInspectionFact", "EpubInspectionField", "EpubInspectionOutcome", "EpubPackageLimits", "MAX_PUBLIC_REPORT_BYTES", "epub_file_lineage"]
