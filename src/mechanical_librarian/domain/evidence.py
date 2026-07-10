"""Immutable evidence, proposal, risk, and disposition vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .identifiers import EvidenceId, ExtractorVersion, Fingerprint, ProposalId, SchemaVersion, SnapshotId, TargetId
from .inventory import FieldName


class SourceApplicability(StrEnum):
    EDITION = "edition"
    WORK = "work"
    UNKNOWN = "unknown"
    INAPPLICABLE = "inapplicable"


class ProposalKind(StrEnum):
    FILL_EMPTY = "fill_empty"
    REPLACE_POPULATED = "replace_populated"
    NO_CHANGE = "no_change"


class Disposition(StrEnum):
    AUTO_APPLY = "auto_apply"
    NEEDS_REVIEW = "needs_review"
    CLOSED_REJECTED = "closed_rejected"
    UNSUPPORTED = "unsupported"


class RiskSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceRelationship(StrEnum):
    SAME_TARGET = "same_target"
    EXPLICIT_CROSS_TARGET = "explicit_cross_target"


@dataclass(frozen=True)
class FieldValue:
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.values or any(not value or value.strip() != value for value in self.values):
            raise ValueError("field values must be non-empty trimmed strings")


@dataclass(frozen=True)
class ConfidenceFact:
    code: str
    value: str

    def __post_init__(self) -> None:
        if not self.code or not self.value:
            raise ValueError("confidence facts require a code and value")


@dataclass(frozen=True)
class EvidenceRecord:
    id: EvidenceId
    target: TargetId
    snapshot: SnapshotId
    snapshot_fingerprint: Fingerprint
    extractor_version: ExtractorVersion
    schema_version: SchemaVersion
    source_type: str
    facts: tuple[ConfidenceFact, ...]
    warnings: tuple[str, ...] = ()
    lineage: str | None = None

    def __post_init__(self) -> None:
        if not self.source_type or any(not warning for warning in self.warnings):
            raise ValueError("evidence requires a source type and non-empty warnings")


@dataclass(frozen=True)
class EvidenceReference:
    """A target-scoped attachment that never copies the underlying evidence."""

    evidence: EvidenceId
    source_target: TargetId
    consuming_target: TargetId
    relationship: EvidenceRelationship
    policy_approved: bool = False

    def __post_init__(self) -> None:
        same_target = self.source_target == self.consuming_target
        if same_target != (self.relationship is EvidenceRelationship.SAME_TARGET):
            raise ValueError("evidence relationship does not match target scope")
        if not same_target and not self.policy_approved:
            raise ValueError("cross-target evidence requires explicit policy approval")


@dataclass(frozen=True)
class EvidenceSummary:
    """A compact, bounded review projection; details load separately by ID."""

    evidence: EvidenceId
    source_type: str
    fact_count: int
    warning_count: int
    excerpt: str | None = None

    def __post_init__(self) -> None:
        if not self.source_type or self.fact_count < 0 or self.warning_count < 0:
            raise ValueError("evidence summary fields are invalid")
        if self.excerpt is not None and (len(self.excerpt) > 280 or "\n" in self.excerpt):
            raise ValueError("evidence excerpts must be single-line and at most 280 characters")


@dataclass(frozen=True)
class Candidate:
    target: TargetId
    source_id: str
    value: FieldValue
    applicability: SourceApplicability
    lineage: str
    correlation_id: str
    contradicts: tuple[EvidenceId, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id or not self.lineage or not self.correlation_id:
            raise ValueError("candidate source, lineage, and correlation identifiers are required")


@dataclass(frozen=True)
class Risk:
    code: str
    severity: RiskSeverity
    blocking: bool
    evidence: tuple[EvidenceId, ...]

    def __post_init__(self) -> None:
        if not self.code or not self.evidence:
            raise ValueError("risks require a code and evidence references")


@dataclass(frozen=True)
class FieldProposal:
    id: ProposalId
    target: TargetId
    snapshot: SnapshotId
    field: FieldName
    current_value: FieldValue | None
    proposed_value: FieldValue
    kind: ProposalKind
    supporting_evidence: tuple[EvidenceId, ...]
    contradicting_evidence: tuple[EvidenceId, ...] = ()
    risks: tuple[Risk, ...] = ()

    def __post_init__(self) -> None:
        if not self.supporting_evidence:
            raise ValueError("field proposals must cite supporting evidence")
        if self.kind is ProposalKind.FILL_EMPTY and self.current_value is not None:
            raise ValueError("fill-empty proposals require no current value")
        if self.kind is ProposalKind.REPLACE_POPULATED and self.current_value is None:
            raise ValueError("replacement proposals require a current value")
        if self.kind is ProposalKind.NO_CHANGE and self.current_value != self.proposed_value:
            raise ValueError("no-change proposals must preserve the current value")


@dataclass(frozen=True)
class ReviewNotice:
    code: str
    blocking: bool
    evidence: tuple[EvidenceId, ...]
    required_action: str

    def __post_init__(self) -> None:
        if not self.code or not self.evidence or not self.required_action:
            raise ValueError("review notices require a code, evidence, and action")
