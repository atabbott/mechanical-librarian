"""Private, closed values returned by the isolated EPUB inspector.

This vocabulary intentionally belongs to the single EPUB operation.  It is not
a media-evidence protocol or a future format extension point.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Callable, ClassVar, Mapping

from mechanical_librarian.domain.evidence_bounds import EvidenceValueError, validate_role_projection
from mechanical_librarian.domain.identifiers import Fingerprint, SchemaVersion

if TYPE_CHECKING:
    from mechanical_librarian.domain.epub_opening import EpubOpeningReport


_CHECKSUMS = frozenset({"valid_isbn_10", "valid_isbn_13", "invalid", "not_applicable"})
_MEDIA = frozenset({"ebook", "print", "unknown", "ambiguous", "not_applicable"})
_CLASSIFICATION_BASES = frozenset({"ebook", "paperback", "hardcover", "print", "unlabelled", "ambiguous", "not_applicable"})
_BASES = frozenset({"package_metadata", "opening_matter"})
_FIELD_STATES = frozenset({"valid", "malformed", "oversized", "unsupported", "untyped", "inapplicable"})
_DIGEST = re.compile(r"^[0-9a-f]{64}$")

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


class DateEvent(StrEnum):
    """Closed local date-event vocabulary retained for later policy."""

    MANIFESTATION_PUBLICATION = "manifestation_publication"
    WORK_FIRST_PUBLICATION = "work_first_publication"
    COPYRIGHT = "copyright"
    CREATION = "creation"
    MODIFICATION = "modification"
    CONVERSION = "conversion"
    UNKNOWN = "unknown"


class DatePrecision(StrEnum):
    """Maximum precision stated by one source date."""

    YEAR = "year"
    MONTH = "month"
    DAY = "day"
    TIMESTAMP = "timestamp"


def epub_file_lineage(source: Fingerprint) -> str:
    if type(source) is not Fingerprint:
        raise EvidenceValueError("EPUB lineage source is malformed")
    return hashlib.sha256(
        (source.schema.value + "\x00" + source.digest + "\x00local-file-v1").encode("ascii")
    ).hexdigest()


class EpubInspectionField(StrEnum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    CREATOR = "creator"
    CONTRIBUTOR = "contributor"
    SERIES = "series"
    SERIES_SEQUENCE = "series_sequence"
    ISBN = "isbn"
    PUBLICATION_DATE = "publication_date"
    DATE_EVENT = "date_event"
    CONTEXTUAL_CREDIT = "contextual_credit"
    PUBLISHER = "publisher"
    IMPRINT = "imprint"
    DESCRIPTION = "description"
    LANGUAGE = "language"
    SUBJECT = "subject"


@dataclass(frozen=True, repr=False)
class EpubRearProvenance:
    """Bounded, opaque provenance for a rear-spine observation."""

    resource_identity: str
    spine_ordinal: int
    tail_slot: int
    marker: str
    citation: str

    def __post_init__(self) -> None:
        if (
            _DIGEST.fullmatch(self.resource_identity) is None
            or type(self.spine_ordinal) is not int or self.spine_ordinal < 1
            or type(self.tail_slot) is not int or not 1 <= self.tail_slot <= 8
            or type(self.marker) is not str or not self.marker or len(self.marker) > 64
            or type(self.citation) is not str or not self.citation or len(self.citation) > 280
        ):
            raise ValueError("EPUB rear provenance is malformed")

    def to_data(self) -> dict[str, object]:
        return {"citation": self.citation, "marker": self.marker, "resource_identity": self.resource_identity, "spine_ordinal": self.spine_ordinal, "tail_slot": self.tail_slot}


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
    classification_basis: str
    basis: str
    outcome: EpubInspectionOutcome = EpubInspectionOutcome.SUCCESS
    state: str = "valid"
    role: str | None = None
    rear_provenance: EpubRearProvenance | None = None
    date_event: DateEvent | None = None
    date_precision: DatePrecision | None = None

    def __post_init__(self) -> None:
        if (
            type(self.field) is not EpubInspectionField
            or type(self.source) is not Fingerprint
            or type(self.observed_value) is not str
            or not self.observed_value
            or len(self.observed_value) > 280
            or (self.canonical_value is not None and (type(self.canonical_value) is not str or not self.canonical_value or len(self.canonical_value) > 512))
            or type(self.supporting_context) is not str
            or not self.supporting_context
            or len(self.supporting_context) > 280
            or self.checksum not in _CHECKSUMS
            or self.media not in _MEDIA
            or self.classification_basis not in _CLASSIFICATION_BASES
            or self.basis not in _BASES
            or type(self.outcome) is not EpubInspectionOutcome
            or self.state not in _FIELD_STATES
            or (self.role is not None and not _valid_role(self.role))
            or (self.rear_provenance is not None and type(self.rear_provenance) is not EpubRearProvenance)
            or (self.date_event is not None and type(self.date_event) is not DateEvent)
            or (
                self.date_precision is not None
                and type(self.date_precision) is not DatePrecision
            )
        ):
            raise ValueError("EPUB inspection fact is malformed")
        is_date = self.field in {
            EpubInspectionField.PUBLICATION_DATE,
            EpubInspectionField.DATE_EVENT,
        }
        if is_date != (self.date_event is not None):
            raise ValueError("EPUB inspection date typing is inconsistent")
        if (
            is_date
            and self.state == "valid"
            and self.date_precision is None
        ) or (not is_date and self.date_precision is not None):
            raise ValueError("EPUB inspection date precision is inconsistent")
        if (
            self.field is EpubInspectionField.PUBLICATION_DATE
            and self.date_event is not DateEvent.MANIFESTATION_PUBLICATION
        ):
            raise ValueError("EPUB publication date event is inconsistent")
        if self.field is not EpubInspectionField.ISBN and (
            (self.state == "valid" and self.canonical_value is None)
            or self.checksum != "not_applicable"
            or self.media != "not_applicable"
            or self.classification_basis != "not_applicable"
        ):
            raise ValueError("EPUB non-identifier fact has identifier fields")
        if self.field is EpubInspectionField.ISBN and self.checksum.startswith("valid_") != (self.canonical_value is not None):
            raise ValueError("EPUB ISBN checksum does not match canonical value")

    def __repr__(self) -> str:
        return "EpubInspectionFact(<redacted>)"


def _valid_role(value: object) -> bool:
    try:
        validate_role_projection(value)
    except EvidenceValueError:
        return False
    return True


@dataclass(frozen=True, repr=False)
class EpubInspection:
    """The one private projection consumed by the retained title report."""

    outcome: EpubInspectionOutcome
    source: Fingerprint | None
    facts: tuple[EpubInspectionFact, ...] = ()
    rear_resource_limit: bool = False

    def __post_init__(self) -> None:
        if type(self.outcome) is not EpubInspectionOutcome or (
            self.source is not None and type(self.source) is not Fingerprint
        ) or type(self.facts) is not tuple or any(type(value) is not EpubInspectionFact for value in self.facts) or type(self.rear_resource_limit) is not bool:
            raise ValueError("EPUB inspection is malformed")
        if self.outcome is EpubInspectionOutcome.SUCCESS and self.source is None:
            raise ValueError("successful EPUB inspection needs a source")
        if self.source is not None and any(value.source != self.source for value in self.facts):
            raise ValueError("EPUB inspection fact source is inconsistent")
        if not self.outcome.succeeded and self.rear_resource_limit:
            raise ValueError("failed EPUB inspection cannot retain a rear limit")

    @classmethod
    def from_opening_report(cls, report: EpubOpeningReport) -> "EpubInspection":
        from mechanical_librarian.domain.epub_opening import EpubOpeningReport, OpeningField

        if type(report) is not EpubOpeningReport:
            raise ValueError("EPUB inspection requires a typed opening report")
        outcome = report.outcome
        source = report.source_fingerprint
        if source is None:
            return cls(outcome, None)
        facts: list[EpubInspectionFact] = []
        fields = {
            OpeningField.TITLE: EpubInspectionField.TITLE,
            OpeningField.SUBTITLE: EpubInspectionField.SUBTITLE,
            OpeningField.CREATOR: EpubInspectionField.CREATOR,
            OpeningField.CONTRIBUTOR: EpubInspectionField.CONTRIBUTOR,
            OpeningField.SERIES: EpubInspectionField.SERIES,
            OpeningField.SERIES_ORDER: EpubInspectionField.SERIES_SEQUENCE,
            OpeningField.PUBLICATION_DATE: EpubInspectionField.PUBLICATION_DATE,
            OpeningField.DATE_EVENT: EpubInspectionField.DATE_EVENT,
            OpeningField.CONTEXTUAL_CREDIT: EpubInspectionField.CONTEXTUAL_CREDIT,
            OpeningField.PUBLISHER: EpubInspectionField.PUBLISHER,
            OpeningField.IMPRINT: EpubInspectionField.IMPRINT,
        }
        contributor_index: int | None = None
        creator_index: int | None = None
        for claim in report.claims:
            if claim.field is OpeningField.TITLE and (claim.normalized_value is not None or claim.state != "valid") and claim.applicability not in {EpubObservationApplicability.INAPPLICABLE, EpubObservationApplicability.UNKNOWN}:
                facts.append(EpubInspectionFact(EpubInspectionField.TITLE, source, claim.raw_value, claim.normalized_value, claim.citation.exact_excerpt, "not_applicable", "not_applicable", "not_applicable", "opening_matter", state=claim.state))
            elif claim.field in fields and (claim.normalized_value is not None or claim.state != "valid"):
                facts.append(EpubInspectionFact(fields[claim.field], source, claim.raw_value, claim.normalized_value, claim.citation.exact_excerpt, "not_applicable", "not_applicable", "not_applicable", "opening_matter", state=claim.state, role=claim.role, date_event=claim.date_event, date_precision=claim.date_precision))
                contributor_index = len(facts) - 1 if claim.field is OpeningField.CONTRIBUTOR else contributor_index
                creator_index = len(facts) - 1 if claim.field is OpeningField.CREATOR else creator_index
            elif claim.field is OpeningField.CONTRIBUTOR_ROLE and contributor_index is not None:
                facts[contributor_index] = replace(facts[contributor_index], role=claim.normalized_value or claim.raw_value)
            elif claim.field is OpeningField.CREATOR_ROLE and creator_index is not None:
                facts[creator_index] = replace(facts[creator_index], role=claim.normalized_value or claim.raw_value)
            elif claim.field is OpeningField.IDENTIFIER and claim.identifier_type in {"isbn_10", "isbn_13"}:
                checksum = claim.identifier_type if claim.identifier_type in {"isbn_10", "isbn_13"} and claim.normalized_value is not None else "invalid"
                classification_basis = claim.applicability_label or "unlabelled"
                media = (
                    "ebook" if classification_basis == "ebook"
                    else "print" if classification_basis in {"paperback", "hardcover", "print"}
                    else "ambiguous" if classification_basis == "ambiguous"
                    else "unknown"
                )
                facts.append(EpubInspectionFact(EpubInspectionField.ISBN, source, claim.raw_value, claim.normalized_value, claim.citation.exact_excerpt, f"valid_{checksum}" if checksum != "invalid" else checksum, media, classification_basis, "opening_matter", state="valid" if claim.normalized_value is not None else "malformed"))
        return cls(
            outcome,
            source,
            tuple(facts),
            "rear_resource_limit" in report.warnings,
        )

    def to_json(self) -> str:
        """Canonical closed protocol payload for the isolated child boundary."""
        return json.dumps(
            {
                "facts": [
                    {
                        "applicability_basis": fact.classification_basis,
                        "basis": fact.basis,
                        "canonical": fact.canonical_value,
                        "checksum": fact.checksum,
                        "field": fact.field.value,
                        "media": fact.media,
                        "observed": fact.observed_value,
                        "outcome": fact.outcome.value,
                        "rear_provenance": None if fact.rear_provenance is None else fact.rear_provenance.to_data(),
                        "role": fact.role,
                        "date_event": (
                            None if fact.date_event is None else fact.date_event.value
                        ),
                        "date_precision": (
                            None
                            if fact.date_precision is None
                            else fact.date_precision.value
                        ),
                        "support": fact.supporting_context,
                        "state": fact.state,
                    }
                    for fact in self.facts
                ],
                "outcome": self.outcome.value,
                "rear_resource_limit": self.rear_resource_limit,
                "schema": "epub-inspection-v3",
                "source": None if self.source is None else {
                    "algorithm": self.source.algorithm,
                    "digest": self.source.digest,
                    "schema": self.source.schema.value,
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, serialized: str) -> "EpubInspection":
        if type(serialized) is not str or len(serialized.encode("utf-8")) > MAX_PUBLIC_REPORT_BYTES:
            raise ValueError("EPUB inspection protocol is invalid")
        try:
            value = json.loads(serialized)
            if type(value) is not dict or set(value) != {"facts", "outcome", "rear_resource_limit", "schema", "source"} or value["schema"] != "epub-inspection-v3":
                raise ValueError
            source_data = value["source"]
            source = None if source_data is None else Fingerprint(source_data["algorithm"], SchemaVersion(source_data["schema"]), source_data["digest"])
            facts = tuple(
                EpubInspectionFact(
                    EpubInspectionField(item["field"]), source, item["observed"], item["canonical"], item["support"], item["checksum"], item["media"], item["applicability_basis"], item["basis"], EpubInspectionOutcome(item["outcome"]), item["state"], item["role"], None if item["rear_provenance"] is None else EpubRearProvenance(**item["rear_provenance"]), None if item["date_event"] is None else DateEvent(item["date_event"]), None if item["date_precision"] is None else DatePrecision(item["date_precision"])
                )
                for item in value["facts"]
            )
            result = cls(EpubInspectionOutcome(value["outcome"]), source, facts, value["rear_resource_limit"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("EPUB inspection protocol is invalid") from None
        if result.to_json() != serialized:
            raise ValueError("EPUB inspection protocol is not canonical")
        return result

    def __repr__(self) -> str:
        return "EpubInspection(<redacted>)"


__all__ = ["DateEvent", "DatePrecision", "EpubInspection", "EpubInspectionFact", "EpubInspectionField", "EpubInspectionOutcome", "EpubPackageLimits", "MAX_PUBLIC_REPORT_BYTES", "epub_file_lineage"]
