"""Immutable, bounded evidence and reference-only review vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json

from .evidence_bounds import (
    MAX_CODE_LENGTH,
    MAX_EVIDENCE_REFERENCES,
    MAX_EXCERPT_LENGTH,
    MAX_FACTS,
    MAX_FACT_VALUE_LENGTH,
    MAX_FIELD_VALUE_LENGTH,
    MAX_FIELD_VALUES,
    MAX_NOTICES,
    MAX_PACKET_RECORDS,
    MAX_RISKS,
    MAX_SERIALIZED_BYTES,
    MAX_SHORT_TEXT_LENGTH,
    MAX_WARNINGS,
    EvidenceValueError,
    ensure_unique,
    validate_code,
    validate_excerpt,
    validate_projection_text,
    validate_string_tuple,
    validate_text,
    validate_tuple,
)
from .identifiers import (
    AdapterId,
    ConnectionId,
    EvidenceId,
    ExternalItemId,
    ExtractorVersion,
    Fingerprint,
    LibraryId,
    PolicyVersion,
    ProposalId,
    SchemaVersion,
    SnapshotId,
    TargetId,
)
from .inventory import FieldName


EVIDENCE_PERSISTENCE_SCHEMA = SchemaVersion("evidence-persistence-v1")
EVIDENCE_API_SCHEMA = SchemaVersion("evidence-api-v1")
REVIEWER_EVIDENCE_SCHEMA = SchemaVersion("reviewer-evidence-v1")
EVIDENCE_READINESS_SCHEMA = SchemaVersion("evidence-readiness-v1")

_PROJECTION_SAFE_FACT_CODES = frozenset(
    {
        "author",
        "chapter-count",
        "cover-dimensions",
        "duration",
        "identifier",
        "language",
        "narrator",
        "published-date",
        "publisher",
        "series",
        "series-sequence",
        "title",
    }
)


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


class EvidenceReadinessState(StrEnum):
    CURRENT = "current"
    MISSING = "missing"
    STALE = "stale"
    UNSUPPORTED = "unsupported"
    APPROVED_SKIP = "approved_skip"


@dataclass(frozen=True, repr=False)
class FieldValue:
    values: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_string_tuple(
            self.values,
            label="field values",
            maximum=MAX_FIELD_VALUES,
            item_maximum=MAX_FIELD_VALUE_LENGTH,
            required=True,
        )

    def __repr__(self) -> str:
        return f"FieldValue(values=<redacted>, count={len(self.values)})"


@dataclass(frozen=True, repr=False)
class ConfidenceFact:
    code: str
    value: str

    def __post_init__(self) -> None:
        validate_code(self.code, "confidence fact code")
        validate_text(self.value, "confidence fact value", MAX_FACT_VALUE_LENGTH)

    def __repr__(self) -> str:
        return f"ConfidenceFact(code={self.code!r}, value=<redacted>)"


@dataclass(frozen=True, repr=False)
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
        _require_exact(self.id, EvidenceId, "evidence record ID")
        _validate_target(self.target)
        _require_exact(self.snapshot, SnapshotId, "evidence snapshot ID")
        _require_exact(self.snapshot_fingerprint, Fingerprint, "evidence fingerprint")
        _require_exact(self.extractor_version, ExtractorVersion, "extractor version")
        _require_exact(self.schema_version, SchemaVersion, "evidence schema version")
        validate_code(self.source_type, "evidence source type")
        validate_tuple(
            self.facts,
            label="evidence facts",
            item_type=ConfidenceFact,
            maximum=MAX_FACTS,
        )
        ensure_unique((fact.code for fact in self.facts), "evidence fact codes")
        validate_string_tuple(
            self.warnings,
            label="evidence warnings",
            maximum=MAX_WARNINGS,
            item_maximum=MAX_CODE_LENGTH,
            codes=True,
        )
        if self.lineage is not None:
            validate_text(self.lineage, "evidence lineage", MAX_SHORT_TEXT_LENGTH)

    def to_persistence_json(self) -> str:
        return _bounded_json(
            {
                "extractor_version": self.extractor_version.value,
                "facts": [
                    {
                        "code": fact.code,
                        "value": fact.value,
                    }
                    for fact in self.facts
                ],
                "fingerprint": _fingerprint_data(self.snapshot_fingerprint),
                "id": self.id.value,
                "lineage": self.lineage,
                "schema": EVIDENCE_PERSISTENCE_SCHEMA.value,
                "schema_version": self.schema_version.value,
                "snapshot": self.snapshot.value,
                "source_type": self.source_type,
                "target": _target_data(self.target),
                "warnings": list(self.warnings),
            },
            "evidence persistence record",
        )

    @classmethod
    def from_persistence_json(cls, serialized: str) -> EvidenceRecord:
        value = _load_object(serialized, EVIDENCE_PERSISTENCE_SCHEMA)
        _require_keys(
            value,
            {
                "extractor_version",
                "facts",
                "fingerprint",
                "id",
                "lineage",
                "schema",
                "schema_version",
                "snapshot",
                "source_type",
                "target",
                "warnings",
            },
            "evidence persistence record",
        )
        facts_value = value["facts"]
        if type(facts_value) is not list:
            raise EvidenceValueError("evidence persistence facts must be a list")
        facts = []
        for fact_value in facts_value:
            fact = _require_object(fact_value, "evidence persistence fact")
            _require_keys(fact, {"code", "value"}, "evidence fact")
            facts.append(ConfidenceFact(fact["code"], fact["value"]))
        warnings = value["warnings"]
        if type(warnings) is not list:
            raise EvidenceValueError("evidence persistence warnings must be a list")
        try:
            return cls(
                id=EvidenceId(value["id"]),
                target=_target_from_data(value["target"]),
                snapshot=SnapshotId(value["snapshot"]),
                snapshot_fingerprint=_fingerprint_from_data(value["fingerprint"]),
                extractor_version=ExtractorVersion(value["extractor_version"]),
                schema_version=SchemaVersion(value["schema_version"]),
                source_type=value["source_type"],
                facts=tuple(facts),
                warnings=tuple(warnings),
                lineage=value["lineage"],
            )
        except EvidenceValueError:
            raise
        except (TypeError, ValueError) as error:
            raise EvidenceValueError("evidence persistence record is invalid") from error

    def __repr__(self) -> str:
        return (
            f"EvidenceRecord(id={self.id!r}, target={self.target!r}, snapshot={self.snapshot!r}, "
            "facts=<redacted>, warnings=<redacted>, lineage=<redacted>)"
        )


@dataclass(frozen=True)
class CrossTargetPolicyDecision:
    policy_version: PolicyVersion
    rule_code: str

    def __post_init__(self) -> None:
        _require_exact(self.policy_version, PolicyVersion, "cross-target policy version")
        validate_code(self.rule_code, "cross-target policy rule")


@dataclass(frozen=True)
class EvidenceReference:
    """A target-scoped attachment that never copies underlying evidence facts."""

    evidence: EvidenceId
    source_target: TargetId
    consuming_target: TargetId
    relationship: EvidenceRelationship
    policy_decision: CrossTargetPolicyDecision | None = None

    def __post_init__(self) -> None:
        _require_exact(self.evidence, EvidenceId, "evidence reference ID")
        _validate_target(self.source_target)
        _validate_target(self.consuming_target)
        _require_exact(self.relationship, EvidenceRelationship, "evidence relationship")
        same_target = self.source_target == self.consuming_target
        if same_target != (self.relationship is EvidenceRelationship.SAME_TARGET):
            raise EvidenceValueError("evidence relationship does not match target scope")
        if same_target and self.policy_decision is not None:
            raise EvidenceValueError("same-target evidence must not carry cross-target policy")
        if not same_target and type(self.policy_decision) is not CrossTargetPolicyDecision:
            raise EvidenceValueError("cross-target evidence requires a versioned policy decision")


@dataclass(frozen=True, repr=False)
class EvidenceSummary:
    evidence: EvidenceId
    source_type: str
    fact_count: int
    warning_count: int
    excerpt: str | None = None

    def __post_init__(self) -> None:
        _require_exact(self.evidence, EvidenceId, "evidence summary ID")
        validate_code(self.source_type, "evidence summary source type")
        _validate_count(self.fact_count, MAX_FACTS, "evidence fact count")
        _validate_count(self.warning_count, MAX_WARNINGS, "evidence warning count")
        if self.excerpt is not None:
            validate_excerpt(self.excerpt)

    def to_api_json(self) -> str:
        return _bounded_json(
            {
                "evidence": self.evidence.value,
                "excerpt": self.excerpt,
                "fact_count": self.fact_count,
                "schema": EVIDENCE_API_SCHEMA.value,
                "source_type": self.source_type,
                "warning_count": self.warning_count,
            },
            "evidence API summary",
        )

    def __repr__(self) -> str:
        return (
            f"EvidenceSummary(evidence={self.evidence!r}, source_type={self.source_type!r}, "
            f"fact_count={self.fact_count!r}, warning_count={self.warning_count!r}, "
            "excerpt=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class EvidenceDetail:
    reference: EvidenceReference
    source_type: str
    facts: tuple[ConfidenceFact, ...]
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_exact(self.reference, EvidenceReference, "evidence detail reference")
        validate_code(self.source_type, "evidence detail source type")
        validate_tuple(
            self.facts,
            label="evidence detail facts",
            item_type=ConfidenceFact,
            maximum=MAX_FACTS,
        )
        ensure_unique((fact.code for fact in self.facts), "evidence detail fact codes")
        if any(fact.code not in _PROJECTION_SAFE_FACT_CODES for fact in self.facts):
            raise EvidenceValueError("ordinary evidence detail contains a private fact class")
        for fact in self.facts:
            validate_projection_text(
                fact.value, "projection-safe evidence fact", MAX_FACT_VALUE_LENGTH
            )
        validate_string_tuple(
            self.warnings,
            label="evidence detail warnings",
            maximum=MAX_WARNINGS,
            item_maximum=MAX_CODE_LENGTH,
            codes=True,
        )

    @classmethod
    def from_record(
        cls,
        record: EvidenceRecord,
        *,
        consuming_target: TargetId | None = None,
        policy_decision: CrossTargetPolicyDecision | None = None,
    ) -> EvidenceDetail:
        _require_exact(record, EvidenceRecord, "evidence detail record")
        selected_target = record.target if consuming_target is None else consuming_target
        relationship = (
            EvidenceRelationship.SAME_TARGET
            if selected_target == record.target
            else EvidenceRelationship.EXPLICIT_CROSS_TARGET
        )
        return cls(
            EvidenceReference(
                record.id,
                record.target,
                selected_target,
                relationship,
                policy_decision,
            ),
            record.source_type,
            tuple(fact for fact in record.facts if fact.code in _PROJECTION_SAFE_FACT_CODES),
            record.warnings,
        )

    def to_api_json(self) -> str:
        return _bounded_json(
            {
                "reference": _reference_projection(self.reference),
                "facts": [{"code": fact.code, "value": fact.value} for fact in self.facts],
                "schema": EVIDENCE_API_SCHEMA.value,
                "source_type": self.source_type,
                "warnings": list(self.warnings),
            },
            "evidence API detail",
        )

    def __repr__(self) -> str:
        return "EvidenceDetail(reference=<redacted>, facts=<redacted>, warnings=<redacted>)"


@dataclass(frozen=True, repr=False)
class Candidate:
    target: TargetId
    source_id: str
    value: FieldValue
    applicability: SourceApplicability
    lineage: str
    correlation_id: str
    contradicts: tuple[EvidenceReference, ...] = ()

    def __post_init__(self) -> None:
        _validate_target(self.target)
        validate_text(self.source_id, "candidate source ID", MAX_SHORT_TEXT_LENGTH)
        _require_exact(self.value, FieldValue, "candidate field value")
        _require_exact(self.applicability, SourceApplicability, "source applicability")
        validate_text(self.lineage, "candidate lineage", MAX_SHORT_TEXT_LENGTH)
        validate_text(self.correlation_id, "candidate correlation ID", MAX_SHORT_TEXT_LENGTH)
        _validate_references(self.contradicts, self.target, "candidate contradictions")

    def __repr__(self) -> str:
        return (
            f"Candidate(target={self.target!r}, source_id=<redacted>, value={self.value!r}, "
            "lineage=<redacted>, correlation_id=<redacted>, contradicts=<redacted>)"
        )


@dataclass(frozen=True)
class Risk:
    code: str
    severity: RiskSeverity
    blocking: bool
    evidence: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        validate_code(self.code, "risk code")
        _require_exact(self.severity, RiskSeverity, "risk severity")
        if type(self.blocking) is not bool:
            raise EvidenceValueError("risk blocking flag must be a boolean")
        validate_tuple(
            self.evidence,
            label="risk evidence",
            item_type=EvidenceReference,
            maximum=MAX_EVIDENCE_REFERENCES,
            required=True,
        )
        _require_single_consuming_target(self.evidence, "risk evidence")
        ensure_unique(
            (reference.evidence for reference in self.evidence), "risk evidence IDs"
        )


@dataclass(frozen=True)
class FieldProposal:
    id: ProposalId
    target: TargetId
    snapshot: SnapshotId
    field: FieldName
    current_value: FieldValue | None
    proposed_value: FieldValue
    kind: ProposalKind
    supporting_evidence: tuple[EvidenceReference, ...]
    contradicting_evidence: tuple[EvidenceReference, ...] = ()
    risks: tuple[Risk, ...] = ()

    def __post_init__(self) -> None:
        _require_exact(self.id, ProposalId, "proposal ID")
        _validate_target(self.target)
        _require_exact(self.snapshot, SnapshotId, "proposal snapshot ID")
        _require_exact(self.field, FieldName, "proposal field")
        if self.current_value is not None:
            _require_exact(self.current_value, FieldValue, "proposal current value")
        _require_exact(self.proposed_value, FieldValue, "proposal value")
        _require_exact(self.kind, ProposalKind, "proposal kind")
        _validate_references(
            self.supporting_evidence,
            self.target,
            "proposal supporting evidence",
            required=True,
        )
        _validate_references(
            self.contradicting_evidence,
            self.target,
            "proposal contradicting evidence",
        )
        ensure_unique(
            [
                reference.evidence
                for reference in self.supporting_evidence + self.contradicting_evidence
            ],
            "proposal evidence",
        )
        validate_tuple(
            self.risks,
            label="proposal risks",
            item_type=Risk,
            maximum=MAX_RISKS,
        )
        ensure_unique((risk.code for risk in self.risks), "proposal risk codes")
        if any(
            reference.consuming_target != self.target
            for risk in self.risks
            for reference in risk.evidence
        ):
            raise EvidenceValueError("proposal risk evidence must consume the proposal target")
        if self.kind is ProposalKind.FILL_EMPTY and self.current_value is not None:
            raise EvidenceValueError("fill-empty proposals require no current value")
        if self.kind is ProposalKind.REPLACE_POPULATED and self.current_value is None:
            raise EvidenceValueError("replacement proposals require a current value")
        if self.kind is ProposalKind.NO_CHANGE and self.current_value != self.proposed_value:
            raise EvidenceValueError("no-change proposals must preserve the current value")


@dataclass(frozen=True, repr=False)
class ReviewNotice:
    code: str
    blocking: bool
    evidence: tuple[EvidenceReference, ...]
    required_action: str

    def __post_init__(self) -> None:
        validate_code(self.code, "evidence notice code")
        if type(self.blocking) is not bool:
            raise EvidenceValueError("evidence notice blocking flag must be a boolean")
        validate_tuple(
            self.evidence,
            label="evidence notice references",
            item_type=EvidenceReference,
            maximum=MAX_EVIDENCE_REFERENCES,
            required=True,
        )
        _require_single_consuming_target(
            self.evidence, "evidence notice references"
        )
        ensure_unique(
            (reference.evidence for reference in self.evidence),
            "evidence notice reference IDs",
        )
        validate_text(self.required_action, "evidence notice action", MAX_SHORT_TEXT_LENGTH)

    def __repr__(self) -> str:
        return (
            f"ReviewNotice(code={self.code!r}, blocking={self.blocking!r}, "
            "evidence=<redacted>, action=<redacted>)"
        )


@dataclass(frozen=True)
class EvidenceReadinessFact:
    target: TargetId
    state: EvidenceReadinessState
    evidence: tuple[EvidenceReference, ...] = ()
    skip_policy: CrossTargetPolicyDecision | None = None

    def __post_init__(self) -> None:
        _validate_target(self.target)
        _require_exact(self.state, EvidenceReadinessState, "evidence readiness state")
        _validate_references(self.evidence, self.target, "readiness evidence")
        if self.state in {EvidenceReadinessState.CURRENT, EvidenceReadinessState.STALE}:
            if not self.evidence:
                raise EvidenceValueError("current or stale readiness requires evidence")
        elif self.evidence:
            raise EvidenceValueError("terminal evidence readiness states must not invent evidence")
        if self.state is EvidenceReadinessState.APPROVED_SKIP:
            if type(self.skip_policy) is not CrossTargetPolicyDecision:
                raise EvidenceValueError(
                    "approved-skip readiness requires versioned policy evidence"
                )
        elif self.skip_policy is not None:
            raise EvidenceValueError("only approved-skip readiness may carry skip policy evidence")


@dataclass(frozen=True)
class EvidenceReadiness:
    facts: tuple[EvidenceReadinessFact, ...]

    def __post_init__(self) -> None:
        validate_tuple(
            self.facts,
            label="evidence readiness facts",
            item_type=EvidenceReadinessFact,
            maximum=MAX_PACKET_RECORDS,
            required=True,
            unique=False,
        )
        ensure_unique((fact.target for fact in self.facts), "evidence readiness targets")

    @property
    def evidence_count(self) -> int:
        return len({reference.evidence for fact in self.facts for reference in fact.evidence})

    @property
    def target_count(self) -> int:
        return len({fact.target for fact in self.facts})

    def to_api_json(self) -> str:
        return _bounded_json(
            {
                "evidence_count": self.evidence_count,
                "facts": [
                    {
                        "evidence": [
                            _reference_projection(reference)
                            for reference in sorted(
                                fact.evidence, key=lambda item: item.evidence.value
                            )
                        ],
                        "skip_policy": _policy_projection(fact.skip_policy),
                        "state": fact.state.value,
                        "target": _projection_target_data(fact.target),
                    }
                    for fact in sorted(self.facts, key=lambda item: _target_sort_key(item.target))
                ],
                "schema": EVIDENCE_READINESS_SCHEMA.value,
                "target_count": self.target_count,
            },
            "evidence readiness projection",
        )


@dataclass(frozen=True)
class ReviewerEvidencePacket:
    target: TargetId
    snapshot: SnapshotId
    details: tuple[EvidenceDetail, ...]

    def __post_init__(self) -> None:
        _validate_target(self.target)
        _require_exact(self.snapshot, SnapshotId, "reviewer packet snapshot")
        validate_tuple(
            self.details,
            label="reviewer evidence details",
            item_type=EvidenceDetail,
            maximum=MAX_PACKET_RECORDS,
            required=True,
        )
        ensure_unique(
            (detail.reference.evidence for detail in self.details),
            "reviewer evidence records",
        )
        if any(
            detail.reference.consuming_target != self.target for detail in self.details
        ):
            raise EvidenceValueError("reviewer evidence must consume the packet target")
        self.to_json()

    def to_json(self) -> str:
        return _bounded_json(
            {
                "details": [
                    {
                        "reference": _reference_projection(detail.reference),
                        "facts": [
                            {"code": fact.code, "value": fact.value} for fact in detail.facts
                        ],
                        "source_type": detail.source_type,
                        "warnings": list(detail.warnings),
                    }
                    for detail in sorted(
                        self.details, key=lambda item: item.reference.evidence.value
                    )
                ],
                "schema": REVIEWER_EVIDENCE_SCHEMA.value,
                "snapshot": self.snapshot.value,
                "target": _projection_target_data(self.target),
            },
            "reviewer evidence packet",
        )


def _validate_references(
    references: object,
    target: TargetId,
    label: str,
    *,
    required: bool = False,
) -> tuple[EvidenceReference, ...]:
    validated = validate_tuple(
        references,
        label=label,
        item_type=EvidenceReference,
        maximum=MAX_EVIDENCE_REFERENCES,
        required=required,
    )
    if any(reference.consuming_target != target for reference in validated):
        raise EvidenceValueError(f"{label} must consume the owning target")
    ensure_unique((reference.evidence for reference in validated), f"{label} IDs")
    return validated


def _require_single_consuming_target(
    references: tuple[EvidenceReference, ...], label: str
) -> None:
    if len({reference.consuming_target for reference in references}) != 1:
        raise EvidenceValueError(f"{label} must share one consuming target")


def _validate_target(target: object) -> TargetId:
    _require_exact(target, TargetId, "evidence target")
    _require_exact(target.adapter, AdapterId, "target adapter")
    _require_exact(target.connection, ConnectionId, "target connection")
    _require_exact(target.library, LibraryId, "target library")
    _require_exact(target.external_item, ExternalItemId, "target external item")
    return target


def _require_exact(value: object, expected: type[object], label: str) -> None:
    if type(value) is not expected:
        raise EvidenceValueError(f"{label} has an invalid type")


def _validate_count(value: object, maximum: int, label: str) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise EvidenceValueError(f"{label} is outside its bounded range")
    return value


def _bounded_json(value: object, label: str) -> str:
    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise EvidenceValueError(f"{label} is not serializable") from error
    if len(serialized.encode("utf-8")) > MAX_SERIALIZED_BYTES:
        raise EvidenceValueError(f"{label} exceeds its serialized size limit")
    return serialized


def _load_object(serialized: object, schema: SchemaVersion) -> dict[str, object]:
    if type(serialized) is not str:
        raise EvidenceValueError("serialized evidence must be text")
    try:
        size = len(serialized.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise EvidenceValueError("serialized evidence contains invalid Unicode") from error
    if size > MAX_SERIALIZED_BYTES:
        raise EvidenceValueError("serialized evidence exceeds its size limit")
    try:
        value = json.loads(serialized)
    except (RecursionError, TypeError, ValueError) as error:
        raise EvidenceValueError("serialized evidence must be valid JSON") from error
    mapping = _require_object(value, "serialized evidence")
    if mapping.get("schema") != schema.value:
        raise EvidenceValueError("serialized evidence schema is incompatible")
    if _bounded_json(mapping, "serialized evidence") != serialized:
        raise EvidenceValueError("serialized evidence must use canonical JSON")
    return mapping


def _require_object(value: object, label: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise EvidenceValueError(f"{label} must be an object")
    return value


def _require_keys(value: dict[str, object], keys: set[str], label: str) -> None:
    if set(value) != keys:
        raise EvidenceValueError(f"{label} fields are invalid")


def _target_data(target: TargetId) -> dict[str, str]:
    return {
        "adapter": target.adapter.value,
        "connection": target.connection.value,
        "external_item": target.external_item.value,
        "library": target.library.value,
    }


def _target_from_data(value: object) -> TargetId:
    mapping = _require_object(value, "evidence target")
    _require_keys(mapping, {"adapter", "connection", "external_item", "library"}, "target")
    try:
        return TargetId(
            AdapterId(mapping["adapter"]),
            ConnectionId(mapping["connection"]),
            LibraryId(mapping["library"]),
            ExternalItemId(mapping["external_item"]),
        )
    except (TypeError, ValueError) as error:
        raise EvidenceValueError("evidence target is invalid") from error


def _fingerprint_data(fingerprint: Fingerprint) -> dict[str, str]:
    return {
        "algorithm": fingerprint.algorithm,
        "digest": fingerprint.digest,
        "schema": fingerprint.schema.value,
    }


def _fingerprint_from_data(value: object) -> Fingerprint:
    mapping = _require_object(value, "evidence fingerprint")
    _require_keys(mapping, {"algorithm", "digest", "schema"}, "fingerprint")
    try:
        return Fingerprint(
            mapping["algorithm"], SchemaVersion(mapping["schema"]), mapping["digest"]
        )
    except (TypeError, ValueError) as error:
        raise EvidenceValueError("evidence fingerprint is invalid") from error


def _target_sort_key(target: TargetId) -> tuple[str, str, str, str]:
    return (
        target.adapter.value,
        target.connection.value,
        target.library.value,
        target.external_item.value,
    )


def _projection_target_data(target: TargetId) -> dict[str, str]:
    serialized = "\x00".join(_target_sort_key(target)).encode("utf-8")
    return {"key": hashlib.sha256(serialized).hexdigest()}


def _reference_projection(reference: EvidenceReference) -> dict[str, object]:
    return {
        "consuming_target": _projection_target_data(reference.consuming_target),
        "evidence": reference.evidence.value,
        "policy": _policy_projection(reference.policy_decision),
        "relationship": reference.relationship.value,
        "source_target": _projection_target_data(reference.source_target),
    }


def _policy_projection(
    policy: CrossTargetPolicyDecision | None,
) -> dict[str, str] | None:
    if policy is None:
        return None
    return {
        "policy_version": policy.policy_version.value,
        "rule_code": policy.rule_code,
    }


__all__ = [
    "Candidate",
    "ConfidenceFact",
    "CrossTargetPolicyDecision",
    "Disposition",
    "EvidenceDetail",
    "EvidenceReadiness",
    "EvidenceReadinessFact",
    "EvidenceReadinessState",
    "EvidenceRecord",
    "EvidenceReference",
    "EvidenceRelationship",
    "EvidenceSummary",
    "EvidenceValueError",
    "FieldProposal",
    "FieldValue",
    "ProposalKind",
    "ReviewerEvidencePacket",
    "ReviewNotice",
    "Risk",
    "RiskSeverity",
    "SourceApplicability",
]
