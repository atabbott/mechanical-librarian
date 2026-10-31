"""Closed domain contract for transient EPUB opening-matter evidence."""

from __future__ import annotations

import hashlib
import json
import platform
import re
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Mapping
from xml.parsers import expat

from .epub_inspection import (
    MAX_PUBLIC_REPORT_BYTES,
    DateEvent,
    DatePrecision,
    EpubInspectionOutcome,
    EpubObservationApplicability,
    EpubPackageLimits,
    epub_file_lineage,
)
from .evidence_bounds import (
    EvidenceValueError,
    validate_code,
    validate_projection_text,
    validate_role_projection,
)
from .identifiers import Fingerprint, SchemaVersion

EPUB_OPENING_REPORT_SCHEMA = SchemaVersion("epub-opening-report-v2")
EPUB_OPENING_CITATION_SCHEMA = SchemaVersion("epub-opening-citation-v1")
EPUB_OPENING_LIMIT_PROFILE = "epub-opening-limits-v1"
EPUB_OPENING_EXTRACTOR_VERSION = "epub-opening-v5"
EPUB_OPENING_NORMALIZER_VERSION = "epub-opening-normalizer-v1"
EPUB_OPENING_PARSER_VERSION = (
    f"cpython-{platform.python_version()}-expat-{expat.EXPAT_VERSION}-htmlparser-v1"
)
EPUB_OPENING_RECOVERY_VERSION = "epub-opening-recovery-v1"
EPUB_OPENING_PROJECTION_VERSION = "epub-opening-text-projection-v1"
EPUB_OPENING_SELECTION_VERSION = "epub-opening-selection-v1"
EPUB_OPENING_VOCABULARY_VERSION = "epub-ssv-1.1-2026-05-28"

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ELEMENT = re.compile(r"^element-[1-9][0-9]{0,8}$")
_EPUB3_LANDMARKS = frozenset(
    {
        "titlepage",
        "halftitlepage",
        "copyright-page",
        "dedication",
        "contributors",
        "imprint",
        "frontmatter",
        "seriespage",
        "other-credits",
    }
)
_EPUB2_GUIDE_TYPES = frozenset(
    {
        "title_page",
        "copyright_page",
        "dedication",
        "acknowledgements",
        "preface",
        "foreword",
        "other.ms_firstpage",
    }
)


def opening_selection_digest(
    same_file_lineage: str, resources: tuple[OpeningResourceResult, ...]
) -> str:
    payload = json.dumps(
        [
            {
                "resource_identity": resource.resource_identity,
                "resource_digest": resource.resource_digest,
                "selection_path": list(resource.selection_path),
            }
            for resource in resources
        ],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(
        (
            EPUB_OPENING_SELECTION_VERSION + "\0" + same_file_lineage + "\0" + payload
        ).encode("utf-8")
    ).hexdigest()


class OpeningField(StrEnum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    CREATOR = "creator"
    CONTRIBUTOR = "contributor"
    CONTRIBUTOR_ROLE = "contributor_role"
    CREATOR_ROLE = "creator_role"
    PUBLISHER = "publisher"
    IMPRINT = "imprint"
    PUBLICATION_DATE = "publication_date"
    DATE_EVENT = "date_event"
    CONTEXTUAL_CREDIT = "contextual_credit"
    COPYRIGHT_DATE = "copyright_date"
    EDITION = "edition"
    PRINTING = "printing"
    REVISION = "revision"
    TRANSLATION = "translation"
    ILLUSTRATION = "illustration"
    SERIES = "series"
    SERIES_ORDER = "series_order"
    OMNIBUS_MEMBERSHIP = "omnibus_membership"
    ANTHOLOGY_MEMBERSHIP = "anthology_membership"
    IDENTIFIER = "identifier"


class OpeningParseMode(StrEnum):
    STRICT = "strict"
    RECOVERED = "recovered"


class OpeningResourceDisposition(StrEnum):
    SELECTED = "selected"
    NO_CLAIMS = "no_claims"
    MISSING = "missing"
    IMAGE_ONLY = "image_only"
    MALFORMED = "malformed"
    UNSUPPORTED_MEDIA = "unsupported_media"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    ENCRYPTED = "encrypted"
    RESOURCE_LIMIT = "resource_limit"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class EpubOpeningLimits:
    """Capability limits bounded by the immutable F07a profile."""

    package: EpubPackageLimits = field(default_factory=EpubPackageLimits)
    selected_documents: int = 5
    expanded_bytes_per_resource: int = 1_048_576
    expanded_bytes_per_selection: int = 5_242_880
    text_characters_per_selection: int = 524_288
    nodes_per_resource: int = 25_000
    nodes_per_selection: int = 100_000
    document_depth: int = 64
    navigation_links: int = 2_048
    wall_seconds: int = 60
    child_cpu_seconds: int = 30
    termination_grace_seconds: int = 2
    facts: int = 64
    warnings: int = 32
    excerpt_characters: int = 280
    report_bytes: int = MAX_PUBLIC_REPORT_BYTES

    HARD_CAPS: ClassVar[Mapping[str, int]] = MappingProxyType(
        {
            "selected_documents": 8,
            "expanded_bytes_per_resource": 4_194_304,
            "expanded_bytes_per_selection": 33_554_432,
            "text_characters_per_selection": 2_097_152,
            "nodes_per_resource": 100_000,
            "nodes_per_selection": 400_000,
            "document_depth": 128,
            "navigation_links": 8_192,
            "wall_seconds": 3_600,
            "child_cpu_seconds": 30,
            "termination_grace_seconds": 2,
            "facts": 64,
            "warnings": 32,
            "excerpt_characters": 280,
            "report_bytes": MAX_PUBLIC_REPORT_BYTES,
        }
    )

    def __post_init__(self) -> None:
        if type(self.package) is not EpubPackageLimits:
            raise EvidenceValueError("EPUB opening package limits are malformed")
        for name, hard_cap in self.HARD_CAPS.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= hard_cap:
                raise EvidenceValueError(
                    "EPUB opening limit is outside its immutable cap"
                )
        if self.child_cpu_seconds != 30 or self.termination_grace_seconds != 2:
            raise EvidenceValueError("EPUB opening process limits are immutable")

    @property
    def profile(self) -> str:
        return EPUB_OPENING_LIMIT_PROFILE

    def to_data(self) -> dict[str, object]:
        return {
            "package": self.package.to_data(),
            "profile": self.profile,
            **{name: getattr(self, name) for name in self.HARD_CAPS},
        }


@dataclass(frozen=True)
class OpeningCitation:
    source_fingerprint: Fingerprint
    resource_identity: str
    resource_digest: str
    semantic_path: str
    selection_path: tuple[str, ...]
    same_file_lineage: str
    exact_excerpt: str
    excerpt_digest: str
    projection_start: int
    projection_end: int
    element_identity: str
    parse_mode: OpeningParseMode
    recovery_warnings: tuple[str, ...] = ()
    schema_version: SchemaVersion = EPUB_OPENING_CITATION_SCHEMA
    extractor_version: str = EPUB_OPENING_EXTRACTOR_VERSION
    normalizer_version: str = EPUB_OPENING_NORMALIZER_VERSION
    parser_version: str = EPUB_OPENING_PARSER_VERSION
    projection_version: str = EPUB_OPENING_PROJECTION_VERSION
    selection_version: str = EPUB_OPENING_SELECTION_VERSION
    vocabulary_version: str = EPUB_OPENING_VOCABULARY_VERSION

    def __post_init__(self) -> None:
        if type(self.source_fingerprint) is not Fingerprint:
            raise EvidenceValueError("opening citation source is malformed")
        for value in (
            self.resource_identity,
            self.resource_digest,
            self.same_file_lineage,
        ):
            if type(value) is not str or _DIGEST.fullmatch(value) is None:
                raise EvidenceValueError("opening citation digest is malformed")
        validate_projection_text(self.semantic_path, "opening semantic path", 512)
        if (
            type(self.selection_path) is not tuple
            or not 1 <= len(self.selection_path) <= 16
            or any(type(value) is not str for value in self.selection_path)
        ):
            raise EvidenceValueError("opening selection path is malformed")
        for value in self.selection_path:
            validate_code(value, "opening selection path")
        validate_projection_text(self.exact_excerpt, "opening exact excerpt", 280)
        if (
            hashlib.sha256(self.exact_excerpt.encode("utf-8")).hexdigest()
            != self.excerpt_digest
        ):
            raise EvidenceValueError("opening excerpt digest does not match")
        if (
            type(self.projection_start) is not int
            or type(self.projection_end) is not int
            or not 0 <= self.projection_start < self.projection_end
            or self.projection_end - self.projection_start != len(self.exact_excerpt)
            or _ELEMENT.fullmatch(self.element_identity) is None
            or type(self.parse_mode) is not OpeningParseMode
            or type(self.recovery_warnings) is not tuple
            or any(type(value) is not str for value in self.recovery_warnings)
            or self.schema_version != EPUB_OPENING_CITATION_SCHEMA
        ):
            raise EvidenceValueError("opening projection coordinates are malformed")
        expected_versions = (
            (self.extractor_version, EPUB_OPENING_EXTRACTOR_VERSION),
            (self.normalizer_version, EPUB_OPENING_NORMALIZER_VERSION),
            (self.parser_version, EPUB_OPENING_PARSER_VERSION),
            (self.projection_version, EPUB_OPENING_PROJECTION_VERSION),
            (self.selection_version, EPUB_OPENING_SELECTION_VERSION),
            (self.vocabulary_version, EPUB_OPENING_VOCABULARY_VERSION),
        )
        if any(actual != expected for actual, expected in expected_versions):
            raise EvidenceValueError("opening compatibility version is unsupported")
        for value in self.recovery_warnings:
            validate_code(value, "opening recovery warning")
        if (
            self.parse_mode is OpeningParseMode.RECOVERED
            and self.recovery_warnings != ("recovered_markup",)
        ) or (self.parse_mode is OpeningParseMode.STRICT and self.recovery_warnings):
            raise EvidenceValueError("opening recovery provenance is inconsistent")


@dataclass(frozen=True)
class OpeningClaim:
    field: OpeningField
    raw_value: str
    normalized_value: str | None
    applicability: EpubObservationApplicability
    citation: OpeningCitation
    identifier_type: str | None = None
    applicability_label: str | None = None
    state: str = "valid"
    date_event: DateEvent | None = None
    date_precision: DatePrecision | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.field) is not OpeningField
            or type(self.applicability) is not EpubObservationApplicability
            or type(self.citation) is not OpeningCitation
        ):
            raise EvidenceValueError("opening claim is malformed")
        if self.state not in {"valid", "oversized", "unsupported"}:
            raise EvidenceValueError("opening claim state is malformed")
        validate_projection_text(self.raw_value, "opening raw value", 280)
        if self.state == "valid" and self.raw_value not in self.citation.exact_excerpt:
            raise EvidenceValueError("opening claim is outside its cited excerpt")
        if self.state != "valid" and self.raw_value != self.citation.exact_excerpt:
            raise EvidenceValueError("redacted opening claim citation disagrees")
        if self.normalized_value is not None:
            validate_projection_text(
                self.normalized_value, "opening normalized value", 280
            )
        if self.field is OpeningField.IDENTIFIER:
            if self.identifier_type not in {"isbn_10", "isbn_13", "doi", "asin"}:
                raise EvidenceValueError("opening identifier type is malformed")
            if self.applicability_label is not None:
                validate_code(self.applicability_label, "opening applicability label")
        elif self.identifier_type is not None or self.applicability_label is not None:
            raise EvidenceValueError("non-identifier claim has identifier typing")
        is_date = self.field in {OpeningField.PUBLICATION_DATE, OpeningField.DATE_EVENT}
        if is_date != (
            type(self.date_event) is DateEvent
            and type(self.date_precision) is DatePrecision
        ):
            raise EvidenceValueError("opening date claim typing is inconsistent")
        if (
            self.field is OpeningField.PUBLICATION_DATE
            and self.date_event is not DateEvent.MANIFESTATION_PUBLICATION
        ):
            raise EvidenceValueError("opening publication date event is inconsistent")
        if self.role is not None:
            if self.field not in {
                OpeningField.CREATOR,
                OpeningField.CONTRIBUTOR,
                OpeningField.CONTEXTUAL_CREDIT,
            }:
                raise EvidenceValueError("opening claim role is inapplicable")
            validate_role_projection(self.role)


@dataclass(frozen=True)
class OpeningResourceResult:
    resource_identity: str
    disposition: OpeningResourceDisposition
    selection_path: tuple[str, ...]
    parse_mode: OpeningParseMode | None = None
    warnings: tuple[str, ...] = ()
    opened: bool = False
    resource_digest: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.resource_identity) is not str
            or _DIGEST.fullmatch(self.resource_identity) is None
            or type(self.disposition) is not OpeningResourceDisposition
            or type(self.selection_path) is not tuple
            or any(type(value) is not str for value in self.selection_path)
            or (
                self.parse_mode is not None
                and type(self.parse_mode) is not OpeningParseMode
            )
            or type(self.warnings) is not tuple
            or type(self.opened) is not bool
            or (
                self.resource_digest is not None
                and (
                    type(self.resource_digest) is not str
                    or _DIGEST.fullmatch(self.resource_digest) is None
                )
            )
        ):
            raise EvidenceValueError("opening resource result is malformed")
        for value in (*self.selection_path, *self.warnings):
            validate_code(value, "opening resource code")
        if not (
            self.selection_path == ("epub3_navigation",)
            or self.selection_path == ("linear_spine_fallback",)
            or (
                len(self.selection_path) == 2
                and (
                    (
                        self.selection_path[0] == "epub3_landmark"
                        and self.selection_path[1] in _EPUB3_LANDMARKS
                    )
                    or (
                        self.selection_path[0] == "epub2_guide"
                        and self.selection_path[1] in _EPUB2_GUIDE_TYPES
                    )
                )
            )
        ):
            raise EvidenceValueError("opening resource selection path is malformed")
        parsed_dispositions = {
            OpeningResourceDisposition.SELECTED,
            OpeningResourceDisposition.NO_CLAIMS,
            OpeningResourceDisposition.EXCLUDED,
        }
        if (
            (
                self.parse_mode is not None
                and self.disposition not in parsed_dispositions
            )
            or (self.parse_mode is not None and not self.opened)
            or (
                self.disposition
                in {
                    OpeningResourceDisposition.SELECTED,
                    OpeningResourceDisposition.NO_CLAIMS,
                }
                and (
                    self.parse_mode is None
                    or not self.opened
                    or self.resource_digest is None
                )
            )
            or (
                self.disposition
                in {
                    OpeningResourceDisposition.MISSING,
                    OpeningResourceDisposition.IMAGE_ONLY,
                    OpeningResourceDisposition.UNSUPPORTED_MEDIA,
                    OpeningResourceDisposition.ENCRYPTED,
                }
                and (self.parse_mode is not None or self.opened)
            )
            or (
                self.disposition is OpeningResourceDisposition.UNSUPPORTED_ENCODING
                and (self.parse_mode is not None or not self.opened)
            )
            or (
                self.disposition is OpeningResourceDisposition.EXCLUDED
                and self.opened != (self.parse_mode is not None)
            )
        ):
            raise EvidenceValueError("opening resource state is inconsistent")
        if self.selection_path == ("epub3_navigation",) and self.disposition not in {
            OpeningResourceDisposition.NO_CLAIMS,
            OpeningResourceDisposition.MALFORMED,
            OpeningResourceDisposition.RESOURCE_LIMIT,
            OpeningResourceDisposition.ENCRYPTED,
        }:
            raise EvidenceValueError(
                "opening navigation resource state is inconsistent"
            )


@dataclass(frozen=True)
class EpubOpeningUsage:
    selected_documents: int = 0
    opened_resources: int = 0
    expanded_bytes: int = 0
    text_characters: int = 0
    nodes: int = 0
    navigation_links: int = 0
    strict_documents: int = 0
    recovered_documents: int = 0

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0 for value in self.to_data().values()
        ):
            raise EvidenceValueError("opening usage is malformed")

    def to_data(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class EpubOpeningReport:
    outcome: EpubInspectionOutcome
    limits: EpubOpeningLimits
    source_fingerprint: Fingerprint | None = None
    claims: tuple[OpeningClaim, ...] = ()
    resources: tuple[OpeningResourceResult, ...] = ()
    warnings: tuple[str, ...] = ()
    usage: EpubOpeningUsage = EpubOpeningUsage()
    schema_version: SchemaVersion = EPUB_OPENING_REPORT_SCHEMA
    same_file_lineage: str | None = None
    selection_digest: str | None = ""

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not EpubInspectionOutcome
            or type(self.limits) is not EpubOpeningLimits
            or type(self.usage) is not EpubOpeningUsage
            or self.schema_version != EPUB_OPENING_REPORT_SCHEMA
            or (
                self.source_fingerprint is not None
                and type(self.source_fingerprint) is not Fingerprint
            )
            or type(self.claims) is not tuple
            or type(self.resources) is not tuple
            or type(self.warnings) is not tuple
            or (
                self.same_file_lineage is not None
                and (
                    type(self.same_file_lineage) is not str
                    or _DIGEST.fullmatch(self.same_file_lineage) is None
                )
            )
            or (
                self.selection_digest not in {None, ""}
                and (
                    type(self.selection_digest) is not str
                    or _DIGEST.fullmatch(self.selection_digest) is None
                )
            )
            or any(type(value) is not OpeningClaim for value in self.claims)
            or any(type(value) is not OpeningResourceResult for value in self.resources)
            or any(type(value) is not str for value in self.warnings)
        ):
            raise EvidenceValueError("EPUB opening report is malformed")
        expected_selection_digest = (
            None
            if self.same_file_lineage is None
            else opening_selection_digest(self.same_file_lineage, self.resources)
        )
        if self.selection_digest == "":
            object.__setattr__(self, "selection_digest", expected_selection_digest)
        elif self.selection_digest != expected_selection_digest:
            raise EvidenceValueError("opening selection digest is inconsistent")
        post_open = self.outcome.succeeded and (
            bool(self.resources) or self.usage != EpubOpeningUsage()
        )
        if self.same_file_lineage is not None and self.source_fingerprint is None:
            raise EvidenceValueError("opening lineage requires a source fingerprint")
        if post_open and (
            self.source_fingerprint is None
            or self.same_file_lineage is None
            or self.selection_digest is None
        ):
            raise EvidenceValueError(
                "post-open report requires bound source provenance"
            )
        if (
            len(self.claims) > self.limits.facts
            or len(self.warnings) > self.limits.warnings
        ):
            raise EvidenceValueError("EPUB opening report bounds are exceeded")
        if any(
            len(claim.raw_value) > self.limits.excerpt_characters
            or len(claim.citation.exact_excerpt) > self.limits.excerpt_characters
            or (
                claim.normalized_value is not None
                and len(claim.normalized_value) > self.limits.excerpt_characters
            )
            or claim.raw_value not in claim.citation.exact_excerpt
            for claim in self.claims
        ):
            raise EvidenceValueError(
                "opening claim exceeds its configured excerpt profile"
            )
        usage = self.usage
        if (
            usage.selected_documents > self.limits.selected_documents
            or usage.opened_resources > self.limits.selected_documents + 1
            or usage.expanded_bytes > self.limits.expanded_bytes_per_selection
            or usage.text_characters > self.limits.text_characters_per_selection
            or usage.nodes > self.limits.nodes_per_selection
            or usage.navigation_links > self.limits.navigation_links
            or usage.strict_documents + usage.recovered_documents
            > usage.opened_resources
            or len(self.resources) > self.limits.selected_documents + 1
        ):
            raise EvidenceValueError("EPUB opening usage exceeds its closed envelope")
        for value in self.warnings:
            validate_code(value, "opening report warning")
        if (
            self.outcome is EpubInspectionOutcome.SUCCESS
            and self.source_fingerprint is None
        ):
            raise EvidenceValueError(
                "successful opening report requires a source fingerprint"
            )
        if not self.outcome.succeeded and (
            self.source_fingerprint is not None
            or self.claims
            or self.resources
            or self.warnings
            or self.usage != EpubOpeningUsage()
            or self.same_file_lineage is not None
        ):
            raise EvidenceValueError(
                "failed opening report cannot contain partial results"
            )
        if self.outcome is EpubInspectionOutcome.NO_APPLICABLE_EVIDENCE and self.claims:
            raise EvidenceValueError(
                "no-applicable opening report cannot contain claims"
            )
        if self.outcome is EpubInspectionOutcome.SUCCESS and not self.claims:
            raise EvidenceValueError("successful opening report requires claims")
        if self.source_fingerprint is not None and any(
            claim.citation.source_fingerprint != self.source_fingerprint
            for claim in self.claims
        ):
            raise EvidenceValueError("opening claims do not match source")
        if self.claims and self.same_file_lineage is None:
            raise EvidenceValueError("opening claims require a same-file lineage")
        if any(
            claim.citation.same_file_lineage != self.same_file_lineage
            for claim in self.claims
        ):
            raise EvidenceValueError("opening claims do not match file lineage")
        if (
            self.source_fingerprint is not None
            and self.same_file_lineage is not None
            and self.same_file_lineage != epub_file_lineage(self.source_fingerprint)
        ):
            raise EvidenceValueError("opening report file lineage is not derived")
        content_resources = tuple(
            resource
            for resource in self.resources
            if resource.selection_path != ("epub3_navigation",)
        )
        if self.usage.selected_documents != len(content_resources):
            raise EvidenceValueError("opening selected-resource usage is inconsistent")
        navigation_resources = tuple(
            resource
            for resource in self.resources
            if resource.selection_path == ("epub3_navigation",)
        )
        if len(navigation_resources) > 1 or (
            navigation_resources and self.resources[0] is not navigation_resources[0]
        ):
            raise EvidenceValueError(
                "opening navigation resource cardinality is inconsistent"
            )
        if len(
            {
                (resource.resource_identity, resource.selection_path)
                for resource in self.resources
            }
        ) != len(self.resources) or len(
            {resource.resource_identity for resource in content_resources}
        ) != len(content_resources):
            raise EvidenceValueError("opening resource identities are not unique")
        selected_by_identity = {
            resource.resource_identity: resource
            for resource in content_resources
            if resource.disposition is OpeningResourceDisposition.SELECTED
        }
        if any(
            claim.citation.resource_identity not in selected_by_identity
            or selected_by_identity[claim.citation.resource_identity].parse_mode
            is not claim.citation.parse_mode
            or selected_by_identity[claim.citation.resource_identity].selection_path
            != claim.citation.selection_path
            or selected_by_identity[claim.citation.resource_identity].resource_digest
            != claim.citation.resource_digest
            for claim in self.claims
        ):
            raise EvidenceValueError(
                "opening claim resource provenance is inconsistent"
            )
        strict_resources = sum(
            resource.parse_mode is OpeningParseMode.STRICT
            for resource in self.resources
        )
        recovered_resources = sum(
            resource.parse_mode is OpeningParseMode.RECOVERED
            for resource in self.resources
        )
        if self.usage.opened_resources != sum(
            resource.opened for resource in self.resources
        ) or (
            self.claims
            and (
                self.usage.opened_resources == 0
                or self.usage.expanded_bytes == 0
                or self.usage.text_characters == 0
                or self.usage.nodes == 0
            )
        ):
            raise EvidenceValueError("opening resource usage is inconsistent")
        if (
            self.usage.strict_documents != strict_resources
            or self.usage.recovered_documents != recovered_resources
        ):
            raise EvidenceValueError("opening parser usage is inconsistent")
        if len(self.to_json().encode("utf-8")) > self.limits.report_bytes:
            raise EvidenceValueError("EPUB opening report exceeds its byte limit")

    @property
    def succeeded(self) -> bool:
        return self.outcome.succeeded

    def to_data(self) -> dict[str, object]:
        return {
            "claims": [_claim_data(value) for value in self.claims],
            "limits": self.limits.to_data(),
            "outcome": self.outcome.value,
            "resources": [_resource_data(value) for value in self.resources],
            "schema": self.schema_version.value,
            "same_file_lineage": self.same_file_lineage,
            "selection_digest": self.selection_digest,
            "source_fingerprint": None
            if self.source_fingerprint is None
            else _fingerprint_data(self.source_fingerprint),
            "usage": self.usage.to_data(),
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_data(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )

    @classmethod
    def from_json(cls, serialized: str) -> EpubOpeningReport:
        if (
            type(serialized) is not str
            or len(serialized.encode("utf-8")) > MAX_PUBLIC_REPORT_BYTES
        ):
            raise EvidenceValueError("EPUB opening report JSON is invalid")
        try:
            value = json.loads(serialized, object_pairs_hook=_unique_object)
            report = _report_from_data(value)
        except EvidenceValueError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RecursionError):
            raise EvidenceValueError("EPUB opening report JSON is invalid") from None
        if report.to_json() != serialized:
            raise EvidenceValueError("EPUB opening report JSON is not canonical")
        return report


def _fingerprint_data(value: Fingerprint) -> dict[str, str]:
    return {
        "algorithm": value.algorithm,
        "digest": value.digest,
        "schema": value.schema.value,
    }


def _fingerprint_from_data(value: object) -> Fingerprint:
    item = _object(value, {"algorithm", "digest", "schema"})
    return Fingerprint(item["algorithm"], SchemaVersion(item["schema"]), item["digest"])


def _citation_data(value: OpeningCitation) -> dict[str, object]:
    return {
        "element_identity": value.element_identity,
        "exact_excerpt": value.exact_excerpt,
        "excerpt_digest": value.excerpt_digest,
        "extractor_version": value.extractor_version,
        "normalizer_version": value.normalizer_version,
        "parse_mode": value.parse_mode.value,
        "recovery_warnings": list(value.recovery_warnings),
        "schema": value.schema_version.value,
        "parser_version": value.parser_version,
        "projection_end": value.projection_end,
        "projection_start": value.projection_start,
        "projection_version": value.projection_version,
        "resource_digest": value.resource_digest,
        "resource_identity": value.resource_identity,
        "same_file_lineage": value.same_file_lineage,
        "selection_path": list(value.selection_path),
        "selection_version": value.selection_version,
        "semantic_path": value.semantic_path,
        "source_fingerprint": _fingerprint_data(value.source_fingerprint),
        "vocabulary_version": value.vocabulary_version,
    }


def _citation_from_data(value: object) -> OpeningCitation:
    item = _object(
        value,
        {
            "element_identity",
            "exact_excerpt",
            "excerpt_digest",
            "extractor_version",
            "normalizer_version",
            "parse_mode",
            "parser_version",
            "projection_end",
            "projection_start",
            "projection_version",
            "resource_digest",
            "resource_identity",
            "recovery_warnings",
            "same_file_lineage",
            "schema",
            "selection_path",
            "selection_version",
            "semantic_path",
            "source_fingerprint",
            "vocabulary_version",
        },
    )
    return OpeningCitation(
        source_fingerprint=_fingerprint_from_data(item["source_fingerprint"]),
        resource_identity=item["resource_identity"],
        resource_digest=item["resource_digest"],
        semantic_path=item["semantic_path"],
        selection_path=tuple(_list(item["selection_path"])),
        same_file_lineage=item["same_file_lineage"],
        exact_excerpt=item["exact_excerpt"],
        excerpt_digest=item["excerpt_digest"],
        projection_start=item["projection_start"],
        projection_end=item["projection_end"],
        element_identity=item["element_identity"],
        parse_mode=OpeningParseMode(item["parse_mode"]),
        extractor_version=item["extractor_version"],
        normalizer_version=item["normalizer_version"],
        parser_version=item["parser_version"],
        projection_version=item["projection_version"],
        selection_version=item["selection_version"],
        vocabulary_version=item["vocabulary_version"],
        recovery_warnings=tuple(_list(item["recovery_warnings"])),
        schema_version=SchemaVersion(item["schema"]),
    )


def _claim_data(value: OpeningClaim) -> dict[str, object]:
    return {
        "applicability": value.applicability.value,
        "applicability_label": value.applicability_label,
        "citation": _citation_data(value.citation),
        "date_event": None if value.date_event is None else value.date_event.value,
        "date_precision": None
        if value.date_precision is None
        else value.date_precision.value,
        "field": value.field.value,
        "identifier_type": value.identifier_type,
        "normalized_value": value.normalized_value,
        "raw_value": value.raw_value,
        "role": value.role,
        "state": value.state,
    }


def _claim_from_data(value: object) -> OpeningClaim:
    item = _object(
        value,
        {
            "applicability",
            "applicability_label",
            "citation",
            "date_event",
            "date_precision",
            "field",
            "identifier_type",
            "normalized_value",
            "raw_value",
            "role",
            "state",
        },
    )
    return OpeningClaim(
        OpeningField(item["field"]),
        item["raw_value"],
        item["normalized_value"],
        EpubObservationApplicability(item["applicability"]),
        _citation_from_data(item["citation"]),
        item["identifier_type"],
        item["applicability_label"],
        item["state"],
        None if item["date_event"] is None else DateEvent(item["date_event"]),
        None
        if item["date_precision"] is None
        else DatePrecision(item["date_precision"]),
        item["role"],
    )


def _resource_data(value: OpeningResourceResult) -> dict[str, object]:
    return {
        "disposition": value.disposition.value,
        "opened": value.opened,
        "parse_mode": None if value.parse_mode is None else value.parse_mode.value,
        "resource_digest": value.resource_digest,
        "resource_identity": value.resource_identity,
        "selection_path": list(value.selection_path),
        "warnings": list(value.warnings),
    }


def _resource_from_data(value: object) -> OpeningResourceResult:
    item = _object(
        value,
        {
            "disposition",
            "opened",
            "parse_mode",
            "resource_digest",
            "resource_identity",
            "selection_path",
            "warnings",
        },
    )
    return OpeningResourceResult(
        item["resource_identity"],
        OpeningResourceDisposition(item["disposition"]),
        tuple(_list(item["selection_path"])),
        None if item["parse_mode"] is None else OpeningParseMode(item["parse_mode"]),
        tuple(_list(item["warnings"])),
        item["opened"],
        item["resource_digest"],
    )


def _limits_from_data(value: object) -> EpubOpeningLimits:
    item = _object(value, {"package", "profile", *EpubOpeningLimits.HARD_CAPS})
    if item["profile"] != EPUB_OPENING_LIMIT_PROFILE:
        raise EvidenceValueError("EPUB opening limit profile is unsupported")
    package_data = _object(item["package"], {"profile", *EpubPackageLimits.HARD_CAPS})
    if package_data["profile"] != "epub-package-limits-v1":
        raise EvidenceValueError("EPUB package limit profile is unsupported")
    package = EpubPackageLimits(
        **{name: package_data[name] for name in EpubPackageLimits.HARD_CAPS}
    )
    return EpubOpeningLimits(
        package=package, **{name: item[name] for name in EpubOpeningLimits.HARD_CAPS}
    )


def _report_from_data(value: object) -> EpubOpeningReport:
    item = _object(
        value,
        {
            "claims",
            "limits",
            "outcome",
            "resources",
            "same_file_lineage",
            "schema",
            "selection_digest",
            "source_fingerprint",
            "usage",
            "warnings",
        },
    )
    if item["schema"] != EPUB_OPENING_REPORT_SCHEMA.value:
        raise EvidenceValueError("EPUB opening report schema is unsupported")
    usage = _object(item["usage"], set(EpubOpeningUsage().to_data()))
    return EpubOpeningReport(
        EpubInspectionOutcome(item["outcome"]),
        _limits_from_data(item["limits"]),
        None
        if item["source_fingerprint"] is None
        else _fingerprint_from_data(item["source_fingerprint"]),
        tuple(_claim_from_data(value) for value in _list(item["claims"])),
        tuple(_resource_from_data(value) for value in _list(item["resources"])),
        tuple(_list(item["warnings"])),
        EpubOpeningUsage(**usage),
        same_file_lineage=item["same_file_lineage"],
        selection_digest=item["selection_digest"],
    )


def _object(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise EvidenceValueError("EPUB opening report has unknown or missing fields")
    return value


def _list(value: object) -> list[Any]:
    if type(value) is not list:
        raise EvidenceValueError("EPUB opening report field must be a list")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceValueError("EPUB opening report contains a duplicate field")
        result[key] = value
    return result


__all__ = [
    "EPUB_OPENING_CITATION_SCHEMA",
    "EPUB_OPENING_EXTRACTOR_VERSION",
    "EPUB_OPENING_LIMIT_PROFILE",
    "EPUB_OPENING_NORMALIZER_VERSION",
    "EPUB_OPENING_PARSER_VERSION",
    "EPUB_OPENING_RECOVERY_VERSION",
    "EPUB_OPENING_PROJECTION_VERSION",
    "EPUB_OPENING_REPORT_SCHEMA",
    "EPUB_OPENING_SELECTION_VERSION",
    "EPUB_OPENING_VOCABULARY_VERSION",
    "EpubOpeningLimits",
    "EpubOpeningReport",
    "EpubOpeningUsage",
    "OpeningCitation",
    "OpeningClaim",
    "OpeningField",
    "OpeningParseMode",
    "OpeningResourceDisposition",
    "OpeningResourceResult",
    "opening_selection_digest",
]
