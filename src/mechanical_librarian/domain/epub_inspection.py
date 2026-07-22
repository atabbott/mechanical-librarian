"""Private, closed values returned by the isolated EPUB inspector.

This vocabulary intentionally belongs to the single EPUB operation.  It is not
a media-evidence protocol or a future format extension point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.domain.identifiers import Fingerprint
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mechanical_librarian.domain.epub_opening import EpubOpeningReport


_CHECKSUMS = frozenset({"valid_isbn_10", "valid_isbn_13", "invalid", "not_applicable"})
_MEDIA = frozenset({"ebook", "print", "unknown", "not_applicable"})
_BASES = frozenset({"package_metadata", "opening_matter"})


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
            self.canonical_value is not None
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
                facts.append(EpubInspectionFact(EpubInspectionField.TITLE, source, claim.raw_value, None, claim.citation.exact_excerpt, "not_applicable", "not_applicable", "opening_matter"))
            elif claim.field is OpeningField.IDENTIFIER:
                checksum = claim.identifier_type if claim.identifier_type in {"isbn_10", "isbn_13"} and claim.normalized_value is not None else "invalid"
                facts.append(EpubInspectionFact(EpubInspectionField.ISBN, source, claim.raw_value, claim.normalized_value, claim.citation.exact_excerpt, f"valid_{checksum}" if checksum != "invalid" else checksum, claim.applicability_label if claim.applicability_label in {"ebook", "print"} else "unknown", "opening_matter"))
        return cls(outcome, source, tuple(facts))

    def __repr__(self) -> str:
        return "EpubInspection(<redacted>)"


__all__ = ["EpubInspection", "EpubInspectionFact", "EpubInspectionField", "EpubInspectionOutcome"]
