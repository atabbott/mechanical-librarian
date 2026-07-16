"""Durable identities and path-free results for EPUB opening evidence."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from .epub_opening import (
    EPUB_OPENING_CITATION_SCHEMA,
    EPUB_OPENING_EXTRACTOR_VERSION,
    EPUB_OPENING_LIMIT_PROFILE,
    EPUB_OPENING_NORMALIZER_VERSION,
    EPUB_OPENING_PARSER_VERSION,
    EPUB_OPENING_PROJECTION_VERSION,
    EPUB_OPENING_RECOVERY_VERSION,
    EPUB_OPENING_REPORT_SCHEMA,
    EPUB_OPENING_SELECTION_VERSION,
    EPUB_OPENING_VOCABULARY_VERSION,
    EpubOpeningLimits,
    EpubOpeningReport,
    OpeningParseMode,
)
from .identifiers import DomainValueError, canonical_json
from .local_evidence import LocalEvidenceOutcome


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_TARGET = re.compile(r"^target_[0-9a-f]{32}$")
_SNAPSHOT = re.compile(r"^snapshot_[0-9a-f]{32}$")
_EVIDENCE = re.compile(r"^evidence_[0-9a-f]{32}$")

PUBLISHABLE_EPUB_OUTCOMES = frozenset(
    {
        LocalEvidenceOutcome.SUCCESS,
        LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE,
        LocalEvidenceOutcome.UNSUPPORTED_FORMAT,
        LocalEvidenceOutcome.UNSUPPORTED_VERSION,
        LocalEvidenceOutcome.UNSUPPORTED_COMPRESSION,
        LocalEvidenceOutcome.UNSUPPORTED_MULTIPLE_RENDITIONS,
        LocalEvidenceOutcome.ENCRYPTED,
        LocalEvidenceOutcome.MALFORMED,
        LocalEvidenceOutcome.INTEGRITY_FAILURE,
    }
)


class EpubEvidenceMode(StrEnum):
    EXECUTED = "executed"
    REUSED = "reused"
    FAILED = "failed"


class EpubEvidenceFailure(StrEnum):
    INVALID_INVOCATION = "invalid_invocation"
    INVALID_CONFIGURATION = "invalid_configuration"
    SOURCE_REFERENCE_UNAVAILABLE = "source_reference_unavailable"
    AMBIGUOUS_REFERENCE = "ambiguous_reference"
    WRONG_TARGET = "wrong_target"
    STALE_SNAPSHOT = "stale_snapshot"
    FILE_INDEX_OUT_OF_RANGE = "file_index_out_of_range"
    NON_EPUB = "non_epub"
    MOUNT_MISMATCH = "mount_mismatch"
    BINDING_INTEGRITY_FAILURE = "binding_integrity_failure"
    CORRUPT_STATE = "corrupt_state"
    SOURCE_CHANGED = "source_changed"
    BUSY = "busy"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    RESOURCE_LIMIT = "resource_limit"
    INTEGRITY_FAILURE = "integrity_failure"
    RETRY_EXHAUSTED = "retry_exhausted"
    PERSISTENCE_FAILURE = "persistence_failure"
    INTERNAL_FAILURE = "internal_failure"


@dataclass(frozen=True, repr=False)
class EpubEvidenceLocator:
    target_reference: str
    snapshot_reference: str
    file_index: int

    def __post_init__(self) -> None:
        if (
            _TARGET.fullmatch(self.target_reference) is None
            or _SNAPSHOT.fullmatch(self.snapshot_reference) is None
            or type(self.file_index) is not int
            or not 0 <= self.file_index <= 10_000
        ):
            raise DomainValueError("EPUB evidence locator is malformed")

    def __repr__(self) -> str:
        return "EpubEvidenceLocator(<opaque>)"


@dataclass(frozen=True, repr=False)
class ResolvedEpubSource:
    locator: EpubEvidenceLocator
    binding_digest: str
    mount_configuration_digest: str
    mount_name: str
    relative_path: str

    def __post_init__(self) -> None:
        if (
            type(self.locator) is not EpubEvidenceLocator
            or _DIGEST.fullmatch(self.binding_digest) is None
            or _DIGEST.fullmatch(self.mount_configuration_digest) is None
            or type(self.mount_name) is not str
            or not self.mount_name
            or "/" in self.mount_name
            or type(self.relative_path) is not str
            or not self.relative_path
            or self.relative_path.startswith("/")
        ):
            raise DomainValueError("resolved EPUB source is malformed")

    def __repr__(self) -> str:
        return "ResolvedEpubSource(<redacted>)"


@dataclass(frozen=True)
class EpubSourceResolution:
    source: ResolvedEpubSource | None = None
    failure: EpubEvidenceFailure | None = None

    def __post_init__(self) -> None:
        if (self.source is None) == (self.failure is None):
            raise DomainValueError("EPUB source resolution is malformed")


@dataclass(frozen=True)
class EpubRecoverableWork:
    work_id: str
    work_key: str
    locator: EpubEvidenceLocator
    mount_configuration_digest: str
    source_fingerprint: str

    def __post_init__(self) -> None:
        if (
            type(self.work_id) is not str
            or not self.work_id.startswith("epub_work_")
            or _DIGEST.fullmatch(self.work_key) is None
            or type(self.locator) is not EpubEvidenceLocator
            or _DIGEST.fullmatch(self.mount_configuration_digest) is None
            or _DIGEST.fullmatch(self.source_fingerprint) is None
        ):
            raise DomainValueError("recoverable EPUB work is malformed")


@dataclass(frozen=True)
class EpubEvidenceClaim:
    work_id: str
    work_key: str
    attempt: int
    fence: int
    worker_id: str

    def __post_init__(self) -> None:
        if (
            type(self.work_id) is not str
            or not self.work_id.startswith("epub_work_")
            or _DIGEST.fullmatch(self.work_key) is None
            or type(self.attempt) is not int
            or not 1 <= self.attempt <= 3
            or type(self.fence) is not int
            or self.fence < 1
            or type(self.worker_id) is not str
            or not self.worker_id
        ):
            raise DomainValueError("EPUB evidence claim is malformed")


@dataclass(frozen=True)
class EpubEvidenceResult:
    mode: EpubEvidenceMode
    outcome: LocalEvidenceOutcome | None = None
    failure: EpubEvidenceFailure | None = None
    evidence_id: str | None = None
    current_applicable: bool = False
    claim_count: int = 0
    citation_count: int = 0
    selected_count: int = 0
    rejected_count: int = 0
    warning_count: int = 0
    parser_mode: str = "none"

    def __post_init__(self) -> None:
        counts = (
            self.claim_count,
            self.citation_count,
            self.selected_count,
            self.rejected_count,
            self.warning_count,
        )
        if (
            type(self.mode) is not EpubEvidenceMode
            or (
                self.outcome is not None
                and type(self.outcome) is not LocalEvidenceOutcome
            )
            or (self.failure is not None and type(self.failure) is not EpubEvidenceFailure)
            or (self.evidence_id is not None and _EVIDENCE.fullmatch(self.evidence_id) is None)
            or type(self.current_applicable) is not bool
            or any(type(value) is not int or value < 0 for value in counts)
            or self.parser_mode not in {"none", "strict", "recovered", "mixed"}
            or (self.mode is EpubEvidenceMode.FAILED) != (self.failure is not None)
            or (self.mode is not EpubEvidenceMode.FAILED)
            != (self.outcome is not None and self.evidence_id is not None)
            or (
                self.mode is not EpubEvidenceMode.FAILED
                and self.outcome not in PUBLISHABLE_EPUB_OUTCOMES
            )
        ):
            raise DomainValueError("EPUB evidence result is malformed")

    @property
    def succeeded(self) -> bool:
        return self.mode is not EpubEvidenceMode.FAILED


def epub_governing_digest(limits: EpubOpeningLimits, material_config: object) -> str:
    if type(limits) is not EpubOpeningLimits:
        raise DomainValueError("EPUB evidence limits are malformed")
    value = {
        "citation_schema": EPUB_OPENING_CITATION_SCHEMA.value,
        "extractor": EPUB_OPENING_EXTRACTOR_VERSION,
        "limit_profile": EPUB_OPENING_LIMIT_PROFILE,
        "limits": limits.to_data(),
        "material_config": material_config,
        "normalizer": EPUB_OPENING_NORMALIZER_VERSION,
        "parser": EPUB_OPENING_PARSER_VERSION,
        "projection": EPUB_OPENING_PROJECTION_VERSION,
        "recovery": EPUB_OPENING_RECOVERY_VERSION,
        "report_schema": EPUB_OPENING_REPORT_SCHEMA.value,
        "schema": "durable-epub-opening-governing-v1",
        "selection": EPUB_OPENING_SELECTION_VERSION,
        "vocabulary": EPUB_OPENING_VOCABULARY_VERSION,
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def epub_work_key(
    source: ResolvedEpubSource,
    source_fingerprint: str,
    governing_digest: str,
) -> str:
    if (
        type(source) is not ResolvedEpubSource
        or _DIGEST.fullmatch(source_fingerprint) is None
        or _DIGEST.fullmatch(governing_digest) is None
    ):
        raise DomainValueError("EPUB work identity is malformed")
    value = {
        "binding": source.binding_digest,
        "file_index": source.locator.file_index,
        "governing": governing_digest,
        "schema": "durable-epub-opening-work-v1",
        "snapshot": source.locator.snapshot_reference,
        "source_generation": source_fingerprint,
        "target": source.locator.target_reference,
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def epub_evidence_identity(
    *,
    work_key: str,
    binding_digest: str,
    source_fingerprint: str,
    governing_digest: str,
    report: EpubOpeningReport,
) -> tuple[str, str, str, str]:
    if (
        _DIGEST.fullmatch(work_key) is None
        or _DIGEST.fullmatch(binding_digest) is None
        or _DIGEST.fullmatch(source_fingerprint) is None
        or _DIGEST.fullmatch(governing_digest) is None
        or type(report) is not EpubOpeningReport
        or report.outcome not in PUBLISHABLE_EPUB_OUTCOMES
    ):
        raise DomainValueError("EPUB evidence identity input is malformed")
    report_json = report.to_json()
    report_digest = hashlib.sha256(report_json.encode("utf-8")).hexdigest()
    modes = {value.parse_mode for value in report.resources if value.parse_mode is not None}
    parser_mode = (
        "mixed"
        if modes == {OpeningParseMode.STRICT, OpeningParseMode.RECOVERED}
        else next(iter(modes)).value
        if modes
        else "none"
    )
    value = {
        "binding": binding_digest,
        "governing": governing_digest,
        "outcome": report.outcome.value,
        "package_mode": "validated",
        "parser_mode": parser_mode,
        "report": report_digest,
        "schema": "durable-epub-opening-evidence-v1",
        "source": source_fingerprint,
        "work": work_key,
    }
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return "evidence_" + digest[:32], digest, report_digest, parser_mode


def result_from_report(
    mode: EpubEvidenceMode,
    evidence_id: str,
    report: EpubOpeningReport,
    *,
    current_applicable: bool = True,
) -> EpubEvidenceResult:
    _, _, _, parser_mode = epub_evidence_identity(
        work_key="0" * 64,
        binding_digest="0" * 64,
        source_fingerprint="0" * 64,
        governing_digest="0" * 64,
        report=report,
    )
    selected = sum(value.disposition.value == "selected" for value in report.resources)
    rejected = len(report.resources) - selected
    return EpubEvidenceResult(
        mode,
        report.outcome,
        evidence_id=evidence_id,
        current_applicable=current_applicable,
        claim_count=len(report.claims),
        citation_count=len(report.claims),
        selected_count=selected,
        rejected_count=rejected,
        warning_count=len(report.warnings),
        parser_mode=parser_mode,
    )


__all__ = [
    "EpubEvidenceClaim",
    "EpubEvidenceFailure",
    "EpubEvidenceLocator",
    "EpubEvidenceMode",
    "EpubEvidenceResult",
    "EpubRecoverableWork",
    "PUBLISHABLE_EPUB_OUTCOMES",
    "ResolvedEpubSource",
    "EpubSourceResolution",
    "epub_evidence_identity",
    "epub_governing_digest",
    "epub_work_key",
    "result_from_report",
]
