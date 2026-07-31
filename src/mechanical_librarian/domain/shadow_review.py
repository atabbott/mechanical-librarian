"""Closed qualification-only shadow-review documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Self

from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
    ComparisonMethod,
    DateLinkConfidence,
    FieldState,
    MAX_SCALAR_CHARACTERS,
    MetadataReport,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    PublicationDateRelation,
    ReportValueError,
    TargetDestination,
    normalize_date_value,
)
from mechanical_librarian.domain.proposal_set import (
    BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA,
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.reviewer_decision_summary import (
    DecisionSummaryValueError,
    candidate_decision_summary,
    compatibility_by_candidate,
    proposal_decision_summary,
    validate_candidate_decision_summary,
    validate_proposal_decision_summary,
)

PACKET_SCHEMA = "shadow-review-packet-v5"
RESULT_SCHEMA = "shadow-review-result-v1"
REQUEST_SCHEMA = "shadow-review-request-v5"
PROFILE_IDENTITY_SCHEMA = "reviewer-profile-identity-v1"
QUALIFICATION_MODE = "qualification_only"
MAX_PACKET_BYTES = 1_048_576
MAX_RESULT_BYTES = 262_144
MAX_PROFILE_IDENTITY_BYTES = 16_384
MAX_OUTPUT_SCHEMA_BYTES = MAX_PACKET_BYTES
# The generated schema is ASCII-only. Embedding it as a JSON string can add at
# most one escape byte per schema byte; the remaining allowance covers the
# fixed request keys, identifiers, and JSON punctuation.
MAX_REQUEST_BYTES = (
    MAX_PACKET_BYTES
    + MAX_PROFILE_IDENTITY_BYTES
    + (2 * MAX_OUTPUT_SCHEMA_BYTES)
    + 65_536
)
MAX_REASON_CHARACTERS = 1_024
MAX_PROBLEMS = 32
MAX_CITATIONS = 256
MAX_LINKAGE_IDENTIFIER_FACTS = 16
MAX_LINKAGE_FORMAT_FACTS = 16
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^review_case_[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^review_request_[0-9a-f]{64}$")
_PROPOSAL_ID = re.compile(r"^review_proposal_[0-9a-f]{64}$")
_ALTERNATIVE_ID = re.compile(r"^alternative_[0-9a-f]{64}$")
_COMBINATION_ID = re.compile(r"^bibliographic_combination_[0-9a-f]{64}$")
_REPRESENTATION_ID = re.compile(r"^representation_[0-9a-f]{64}$")
_PROVIDER_REFERENCE = re.compile(r"^provider_[0-9a-f]{64}$")
_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ASIN = re.compile(r"^B[0-9A-Z]{9}$")
_PROVIDER_DATE_EVIDENCE = frozenset(
    {
        ProviderEvidenceKind.COPYRIGHT_DATE,
        ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
        ProviderEvidenceKind.LAST_MODIFIED,
        ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
    }
)
_PROVIDER_DATE_EVIDENCE_VALUES = frozenset(
    kind.value for kind in _PROVIDER_DATE_EVIDENCE
)
_RECOMMENDATIONS = frozenset(
    {"recommend_candidate", "recommend_keep_current", "manual"}
)


class ShadowReviewValueError(ValueError):
    """A reviewer document is outside the closed F11 contract."""


class ShadowReviewDocumentError(ShadowReviewValueError):
    """Reviewer bytes are not one canonical JSON document."""


class ShadowReviewSemanticError(ShadowReviewValueError):
    """A canonical reviewer document violates its bound request."""


class ShadowReviewIdentityError(ShadowReviewSemanticError):
    """A canonical reviewer result names a different bound request."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _candidate_date_matches_precision(candidate: dict[str, object]) -> bool:
    wrapped = candidate.get("value")
    if type(wrapped) is not dict or set(wrapped) != {"kind", "value"}:
        return False
    value = wrapped.get("value")
    if wrapped.get("kind") != "text" or type(value) is not str:
        return False
    try:
        normalized, precision = normalize_date_value(value)
    except ValueError:
        return False
    return (
        normalized == value
        and precision.value == candidate.get("date_precision")
    )


@dataclass(frozen=True, slots=True)
class ReviewerProfileIdentity:
    """Path-free identity of the exact qualification tuple."""

    executable_sha256: str
    executable_version: str
    sandbox_sha256: str
    sandbox_version: str
    model: str
    reasoning: str
    prompt_version: str
    profile_version: str
    packet_version: str
    output_version: str
    policy_version: str
    timeout_seconds: int
    output_bytes: int
    memory_bytes: int
    process_limit: int
    file_bytes: int
    mode: str = QUALIFICATION_MODE
    schema_version: str = PROFILE_IDENTITY_SCHEMA

    def __post_init__(self) -> None:
        if (
            self.schema_version != PROFILE_IDENTITY_SCHEMA
            or self.mode != QUALIFICATION_MODE
            or _DIGEST.fullmatch(self.executable_sha256) is None
            or _DIGEST.fullmatch(self.sandbox_sha256) is None
            or self.model != "gpt-5.6-sol"
            or self.reasoning != "medium"
            or self.prompt_version != "shadow-review-prompt-v2"
            or self.profile_version != "codex-shadow-profile-v1"
            or self.packet_version != PACKET_SCHEMA
            or self.output_version != RESULT_SCHEMA
            or self.policy_version != "qualification-only-policy-v1"
            or self.sandbox_version != "bubblewrap-v1"
            or any(
                type(value) is not str
                or not value
                or len(value) > 128
                or any(ord(character) < 0x20 for character in value)
                for value in (self.executable_version,)
            )
            or type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 3_600
            or type(self.output_bytes) is not int
            or not 1_024 <= self.output_bytes <= MAX_RESULT_BYTES
            or type(self.memory_bytes) is not int
            or not 134_217_728 <= self.memory_bytes <= 8_589_934_592
            or type(self.process_limit) is not int
            or not 1 <= self.process_limit <= 64
            or type(self.file_bytes) is not int
            or not self.output_bytes <= self.file_bytes <= 16_777_216
        ):
            raise ShadowReviewValueError("reviewer profile identity is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "executable_sha256": self.executable_sha256,
            "executable_version": self.executable_version,
            "file_bytes": self.file_bytes,
            "memory_bytes": self.memory_bytes,
            "mode": self.mode,
            "model": self.model,
            "output_bytes": self.output_bytes,
            "output_version": self.output_version,
            "packet_version": self.packet_version,
            "policy_version": self.policy_version,
            "process_limit": self.process_limit,
            "profile_version": self.profile_version,
            "prompt_version": self.prompt_version,
            "reasoning": self.reasoning,
            "sandbox_sha256": self.sandbox_sha256,
            "sandbox_version": self.sandbox_version,
            "schema_version": self.schema_version,
            "timeout_seconds": self.timeout_seconds,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        raw = _canonical_object(serialized, MAX_PROFILE_IDENTITY_BYTES)
        if set(raw) != {
            "executable_sha256",
            "executable_version",
            "file_bytes",
            "memory_bytes",
            "mode",
            "model",
            "output_bytes",
            "output_version",
            "packet_version",
            "policy_version",
            "process_limit",
            "profile_version",
            "prompt_version",
            "reasoning",
            "sandbox_sha256",
            "sandbox_version",
            "schema_version",
            "timeout_seconds",
        }:
            raise ShadowReviewValueError("reviewer profile identity is invalid")
        try:
            value = cls(**raw)  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise ShadowReviewValueError(
                "reviewer profile identity is invalid"
            ) from error
        if value.to_json() != serialized:
            raise ShadowReviewValueError("reviewer profile identity is not canonical")
        return value


def _validate_bibliographic_combinations(
    combinations: tuple[dict[str, object], ...],
    proposals: tuple[dict[str, object], ...],
) -> None:
    by_destination = {
        proposal["destination"]: proposal
        for proposal in proposals
    }
    expected_destinations = ["title", "subtitle", "series"]
    combination_ids: list[str] = []
    member_keys: set[str] = set()
    for combination in combinations:
        if (
            type(combination) is not dict
            or set(combination)
            != {
                "combination_id",
                "selections",
                "source_representations",
            }
            or type(combination["combination_id"]) is not str
            or _COMBINATION_ID.fullmatch(
                combination["combination_id"]
            )
            is None
            or type(combination["selections"]) is not list
            or type(combination["source_representations"]) is not list
            or combination["source_representations"]
            != sorted(set(combination["source_representations"]))
            or not combination["source_representations"]
            or any(
                type(value) is not str
                or _REPRESENTATION_ID.fullmatch(value) is None
                for value in combination["source_representations"]
            )
        ):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        selections = combination["selections"]
        if [
            value.get("destination")
            if type(value) is dict
            else None
            for value in selections
        ] != expected_destinations:
            raise ShadowReviewValueError("shadow-review packet is invalid")
        for selection in selections:
            if (
                type(selection) is not dict
                or set(selection)
                != {
                    "alternative_id",
                    "destination",
                    "kind",
                    "target_field_fingerprint",
                }
                or selection["destination"] not in by_destination
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
            proposal = by_destination[selection["destination"]]
            candidate_ids = {
                value["candidate_id"]
                for value in proposal["candidates"]
            }
            if selection["kind"] == "candidate":
                if (
                    type(selection["alternative_id"]) is not str
                    or selection["alternative_id"] not in candidate_ids
                    or selection["target_field_fingerprint"] is not None
                ):
                    raise ShadowReviewValueError(
                        "shadow-review packet is invalid"
                    )
            elif selection["kind"] == "preserve_target":
                fingerprint = hashlib.sha256(
                    canonical_json(proposal["current"]).encode("utf-8")
                ).hexdigest()
                if (
                    selection["alternative_id"] is not None
                    or selection["target_field_fingerprint"] != fingerprint
                ):
                    raise ShadowReviewValueError(
                        "shadow-review packet is invalid"
                    )
            else:
                raise ShadowReviewValueError(
                    "shadow-review packet is invalid"
                )
        identity_data = {
            "relationship_schema_version": (
                BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA
            ),
            "selections": selections,
            "source_representations": combination[
                "source_representations"
            ],
        }
        expected_id = "bibliographic_combination_" + hashlib.sha256(
            canonical_json(identity_data).encode("utf-8")
        ).hexdigest()
        if combination["combination_id"] != expected_id:
            raise ShadowReviewValueError("shadow-review packet is invalid")
        member_key = canonical_json(selections)
        if member_key in member_keys:
            raise ShadowReviewValueError("shadow-review packet is invalid")
        member_keys.add(member_key)
        combination_ids.append(combination["combination_id"])
    if combination_ids != sorted(set(combination_ids)):
        raise ShadowReviewValueError("shadow-review packet is invalid")


@dataclass(frozen=True, slots=True)
class ShadowReviewPacket:
    review_case_id: str
    proposals: tuple[dict[str, object], ...]
    citations: tuple[dict[str, object], ...]
    correlations: tuple[dict[str, object], ...]
    schema_version: str = PACKET_SCHEMA
    bibliographic_relationship_schema_version: str = (
        BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA
    )
    bibliographic_combinations: tuple[dict[str, object], ...] = ()

    def __post_init__(self) -> None:
        if (
            self.schema_version != PACKET_SCHEMA
            or _CASE_ID.fullmatch(self.review_case_id) is None
            or type(self.proposals) is not tuple
            or not self.proposals
            or type(self.citations) is not tuple
            or type(self.correlations) is not tuple
            or self.bibliographic_relationship_schema_version
            != BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA
            or type(self.bibliographic_combinations) is not tuple
        ):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        proposal_ids: list[str] = []
        citation_ids: list[str] = []
        proposal_evidence_references: set[str] = set()
        decision_role_references: set[str] = set()
        provider_date_references: set[str] = set()
        candidate_compatibility: dict[str, list[str]] = {}
        for proposal in self.proposals:
            if type(proposal) is not dict or set(proposal) != {
                "candidates",
                "current",
                "decision_summary",
                "destination",
                "outcome",
                "proposal_id",
                "reasons",
            }:
                raise ShadowReviewValueError("shadow-review packet is invalid")
            proposal_id = proposal.get("proposal_id")
            destination = proposal.get("destination")
            if (
                type(proposal_id) is not str
                or _PROPOSAL_ID.fullmatch(proposal_id) is None
                or destination
                not in {value.value for value in TargetDestination}
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
            try:
                validate_proposal_decision_summary(
                    proposal["decision_summary"]
                )
            except DecisionSummaryValueError as error:
                raise ShadowReviewValueError(
                    "shadow-review packet is invalid"
                ) from error
            proposal_ids.append(proposal_id)
            candidates = proposal.get("candidates")
            if type(candidates) is not list:
                raise ShadowReviewValueError("shadow-review packet is invalid")
            candidate_ids: list[object] = []
            expected_dates = (
                {("manifestation_publication", "year")}
                if destination == TargetDestination.PUBLISHED_YEAR.value
                else {
                    ("manifestation_publication", "month"),
                    ("manifestation_publication", "day"),
                }
                if destination == TargetDestination.PUBLISHED_DATE.value
                else {(None, None)}
            )
            for candidate in candidates:
                if (
                    type(candidate) is not dict
                    or set(candidate)
                    != {
                        "assessment",
                        "candidate_id",
                        "correlations",
                        "date_event",
                        "date_precision",
                        "decision_summary",
                        "evidence_references",
                        "scope",
                        "value",
                    }
                    or (
                        candidate["date_event"],
                        candidate["date_precision"],
                    )
                    not in expected_dates
                    or (
                        destination
                        in {
                            TargetDestination.PUBLISHED_YEAR.value,
                            TargetDestination.PUBLISHED_DATE.value,
                        }
                        and not _candidate_date_matches_precision(candidate)
                    )
                    or type(candidate["evidence_references"]) is not list
                    or any(
                        type(reference) is not str
                        for reference in candidate["evidence_references"]
                    )
                ):
                    raise ShadowReviewValueError("shadow-review packet is invalid")
                candidate_ids.append(candidate.get("candidate_id"))
                try:
                    role_references, compatibility_ids = (
                        validate_candidate_decision_summary(
                            candidate["decision_summary"],
                            candidate=candidate,
                            destination=destination,
                        )
                    )
                except DecisionSummaryValueError as error:
                    raise ShadowReviewValueError(
                        "shadow-review packet is invalid"
                    ) from error
                decision_role_references.update(role_references)
                candidate_compatibility[candidate["candidate_id"]] = (
                    compatibility_ids
                )
                proposal_evidence_references.update(
                    candidate["evidence_references"]
                )
                if destination in {
                    TargetDestination.PUBLISHED_YEAR.value,
                    TargetDestination.PUBLISHED_DATE.value,
                }:
                    provider_date_references.update(
                        reference
                        for reference in candidate["evidence_references"]
                        if reference.startswith(("provider_", "evidence_"))
                    )
            if (
                any(type(value) is not str or _ALTERNATIVE_ID.fullmatch(value) is None for value in candidate_ids)
                or candidate_ids != sorted(set(candidate_ids))
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
        if proposal_ids != sorted(set(proposal_ids)):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        _validate_bibliographic_combinations(
            self.bibliographic_combinations,
            self.proposals,
        )
        expected_compatibility: dict[str, set[str]] = {}
        for combination in self.bibliographic_combinations:
            for selection in combination["selections"]:
                alternative_id = selection["alternative_id"]
                if selection["kind"] == "candidate":
                    expected_compatibility.setdefault(
                        alternative_id, set()
                    ).add(combination["combination_id"])
        for candidate_id, compatibility_ids in candidate_compatibility.items():
            if compatibility_ids != sorted(
                expected_compatibility.get(candidate_id, [])
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
        for citation in self.citations:
            if (
                type(citation) is not dict
                or set(citation) != {"citation_id", "fact"}
                or type(citation.get("citation_id")) is not str
                or type(citation.get("fact")) is not dict
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
            citation_id = citation["citation_id"]
            fact = citation["fact"]
            if (
                citation_id in provider_date_references
                or citation_id.startswith("evidence_")
                or "provider" in fact
                or "linkage" in fact
                or _contains_provider_date(fact)
            ):
                _validate_provider_citation(
                    citation_id,
                    fact,
                    require_date=citation_id in provider_date_references,
                )
            citation_ids.append(citation_id)
        if citation_ids != sorted(set(citation_ids)):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        if not proposal_evidence_references.issubset(citation_ids):
            raise ShadowReviewValueError(
                "shadow-review proposal evidence binding is invalid"
            )
        if not decision_role_references.issubset(citation_ids):
            raise ShadowReviewValueError(
                "shadow-review decision-summary evidence binding is invalid"
            )
        correlation_ids: list[str] = []
        for correlation in self.correlations:
            if (
                type(correlation) is not dict
                or set(correlation)
                != {"candidate_ids", "correlation_id", "proposal_ids"}
                or type(correlation.get("correlation_id")) is not str
                or type(correlation.get("candidate_ids")) is not list
                or type(correlation.get("proposal_ids")) is not list
                or correlation["candidate_ids"]
                != sorted(set(correlation["candidate_ids"]))
                or correlation["proposal_ids"]
                != sorted(set(correlation["proposal_ids"]))
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
            correlation_ids.append(correlation["correlation_id"])  # type: ignore[arg-type]
        if correlation_ids != sorted(set(correlation_ids)):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        if len(self.to_json().encode("utf-8")) > MAX_PACKET_BYTES:
            raise ShadowReviewValueError("shadow-review packet exceeds its byte limit")

    @classmethod
    def create(
        cls, case: ReviewCase, proposal_set: ProposalSet, report: MetadataReport
    ) -> Self:
        if (
            type(case) is not ReviewCase
            or type(proposal_set) is not ProposalSet
            or type(report) is not MetadataReport
            or (case.proposal_set_id, case.report_id)
            != (proposal_set.proposal_set_id, report.report_id)
            or case.proposal_document != proposal_set.to_json()
            or case.target_fingerprint != report.target.fingerprint
            or case.relationships
            != proposal_set.to_data()["relationships"]
        ):
            raise ShadowReviewValueError("shadow-review packet binding is invalid")
        current = {
            value.destination.value: value.to_data() for value in report.target.fields
        }
        proposals: list[dict[str, object]] = []
        references: set[str] = set()
        correlations: dict[str, dict[str, set[str]]] = {}
        constrained_destinations = {
            selection.destination.value
            for combination in proposal_set.bibliographic_combinations
            for selection in combination.selections
        }
        compatibility_ids = compatibility_by_candidate(proposal_set)
        raw_results = {
            wrapped["result"]["destination"]: wrapped["result"]
            for wrapped in case.proposals
        }
        for wrapped in case.proposals:
            result = wrapped["result"]
            manual_required = wrapped["manual_required"]
            if (
                result["outcome"] not in {
                    ProposalOutcome.ACCEPTABLE_CHOICE.value,
                    ProposalOutcome.PROPOSE_CHANGE.value,
                    ProposalOutcome.UNRESOLVED_CHOICE.value,
                }
                and result["destination"] not in constrained_destinations
                and not manual_required
            ):
                continue
            candidates: list[dict[str, object]] = []
            for alternative in result["alternatives"]:
                references.update(alternative["evidence_references"])
                for correlation_id in alternative["correlations"]:
                    grouped = correlations.setdefault(
                        correlation_id,
                        {"candidate_ids": set(), "proposal_ids": set()},
                    )
                    grouped["candidate_ids"].add(alternative["alternative_id"])
                    grouped["proposal_ids"].add(wrapped["proposal_id"])
                decision_summary, role_references = candidate_decision_summary(
                    report,
                    result["destination"],
                    alternative,
                    raw_results,
                    compatibility_ids,
                )
                references.update(role_references)
                candidates.append(
                    {
                        "assessment": alternative["assessment"],
                        "candidate_id": alternative["alternative_id"],
                        "correlations": alternative["correlations"],
                        "date_event": alternative["date_event"],
                        "date_precision": alternative["date_precision"],
                        "decision_summary": decision_summary,
                        "evidence_references": alternative["evidence_references"],
                        "scope": alternative["scope"],
                        "value": alternative["value"],
                    }
                )
            proposals.append(
                {
                    "candidates": sorted(
                        candidates, key=lambda value: value["candidate_id"]
                    ),
                    "current": current[result["destination"]],
                    "decision_summary": proposal_decision_summary(
                        result,
                        manual_required=manual_required,
                        current=current[result["destination"]],
                    ),
                    "destination": result["destination"],
                    "outcome": (
                        ProposalOutcome.UNRESOLVED_CHOICE.value
                        if manual_required
                        else result["outcome"]
                    ),
                    "proposal_id": wrapped["proposal_id"],
                    "reasons": (
                        ["no_safe_bibliographic_combination"]
                        if manual_required
                        else result["reasons"]
                    ),
                }
            )
        evidence = _evidence_index(report.to_data())
        references.update(
            reference
            for reference, fact in evidence.items()
            if _contains_provider_date(fact)
        )
        references.update(_date_link_local_references(report))
        if not references.issubset(evidence):
            raise ShadowReviewValueError("shadow-review evidence binding is invalid")
        citations = tuple(
            {"citation_id": reference, "fact": evidence[reference]}
            for reference in sorted(references)
        )
        correlation_facts = tuple(
            {
                "candidate_ids": sorted(values["candidate_ids"]),
                "correlation_id": correlation_id,
                "proposal_ids": sorted(values["proposal_ids"]),
            }
            for correlation_id, values in sorted(correlations.items())
        )
        value = cls(
            case.review_case_id,
            tuple(sorted(proposals, key=lambda value: value["proposal_id"])),
            citations,
            correlation_facts,
            bibliographic_combinations=tuple(
                value.to_data()
                for value in proposal_set.bibliographic_combinations
            ),
        )
        return value
    def to_data(self) -> dict[str, object]:
        return {
            "bibliographic_combinations": [
                _copy_json(value)
                for value in self.bibliographic_combinations
            ],
            "bibliographic_relationship_schema_version": (
                self.bibliographic_relationship_schema_version
            ),
            "citations": [_copy_json(value) for value in self.citations],
            "correlations": [_copy_json(value) for value in self.correlations],
            "proposals": [_copy_json(value) for value in self.proposals],
            "review_case_id": self.review_case_id,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(
        cls, serialized: str, source_report: MetadataReport | None = None
    ) -> Self:
        return cls._from_json(serialized, source_report, source_validated=False)

    @classmethod
    def from_source_validated_json(cls, serialized: str) -> Self:
        """Decode same-process bytes already validated with their source report."""
        return cls._from_json(serialized, None, source_validated=True)

    @classmethod
    def _from_json(
        cls,
        serialized: str,
        source_report: MetadataReport | None,
        *,
        source_validated: bool,
    ) -> Self:
        raw = _canonical_object(serialized, MAX_PACKET_BYTES)
        if set(raw) != {
            "bibliographic_combinations",
            "bibliographic_relationship_schema_version",
            "citations",
            "correlations",
            "proposals",
            "review_case_id",
            "schema_version",
        }:
            raise ShadowReviewValueError("shadow-review packet is invalid")
        if (
            type(raw["proposals"]) is not list
            or type(raw["citations"]) is not list
            or type(raw["correlations"]) is not list
            or type(raw["bibliographic_combinations"]) is not list
        ):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        try:
            value = cls(
                raw["review_case_id"],  # type: ignore[arg-type]
                tuple(raw["proposals"]),
                tuple(raw["citations"]),
                tuple(raw["correlations"]),
                raw["schema_version"],  # type: ignore[arg-type]
                raw["bibliographic_relationship_schema_version"],  # type: ignore[arg-type]
                tuple(raw["bibliographic_combinations"]),
            )
        except (TypeError, ValueError) as error:
            raise ShadowReviewValueError("shadow-review packet is invalid") from error
        if value.to_json() != serialized:
            raise ShadowReviewValueError("shadow-review packet is not canonical")
        if not source_validated:
            value.validate_source(source_report)
        return value

    def validate_source(self, source_report: MetadataReport | None) -> None:
        """Require the exact packet projection of one authoritative report."""
        if type(source_report) is not MetadataReport:
            raise ShadowReviewValueError(
                "shadow-review authoritative source is unavailable"
            )
        proposal_set = evaluate_report(source_report)
        case = ReviewCase.create(proposal_set, source_report)
        expected = type(self).create(case, proposal_set, source_report)
        if self != expected:
            raise ShadowReviewValueError(
                "shadow-review source binding is invalid: authoritative projection"
            )


@dataclass(frozen=True, slots=True)
class ShadowReviewRequest:
    review_request_id: str
    review_case_id: str
    profile: ReviewerProfileIdentity
    packet: ShadowReviewPacket
    output_schema: str
    output_schema_digest: str
    mode: str = QUALIFICATION_MODE
    schema_version: str = REQUEST_SCHEMA

    def __post_init__(self) -> None:
        try:
            schema_bytes = self.output_schema.encode("utf-8")
            schema = json.loads(
                self.output_schema,
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(value)
                ),
            )
        except (
            AttributeError,
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
            json.JSONDecodeError,
        ) as error:
            raise ShadowReviewValueError(
                "shadow-review request is invalid"
            ) from error
        if (
            self.schema_version != REQUEST_SCHEMA
            or self.mode != QUALIFICATION_MODE
            or _REQUEST_ID.fullmatch(self.review_request_id) is None
            or type(self.profile) is not ReviewerProfileIdentity
            or type(self.packet) is not ShadowReviewPacket
            or type(self.output_schema) is not str
            or not self.output_schema.isascii()
            or not 1 <= len(schema_bytes) <= MAX_OUTPUT_SCHEMA_BYTES
            or type(self.output_schema_digest) is not str
            or hashlib.sha256(schema_bytes).hexdigest()
            != self.output_schema_digest
            or canonical_json(schema) != self.output_schema
            or self.output_schema
            != canonical_json(
                _reviewer_output_schema(self.packet, self.review_request_id)
            )
            or self.packet.review_case_id != self.review_case_id
            or self.review_request_id != self.expected_id()
            or len(self.to_json().encode("utf-8")) > MAX_REQUEST_BYTES
        ):
            raise ShadowReviewValueError("shadow-review request is invalid")

    @classmethod
    def create(
        cls, case: ReviewCase, profile: ReviewerProfileIdentity, packet: ShadowReviewPacket
    ) -> Self:
        if (
            type(case) is not ReviewCase
            or type(profile) is not ReviewerProfileIdentity
            or type(packet) is not ShadowReviewPacket
            or case.review_case_id != packet.review_case_id
        ):
            raise ShadowReviewValueError("shadow-review request binding is invalid")
        return cls.create_from_packet(profile, packet)

    @classmethod
    def create_from_packet(
        cls, profile: ReviewerProfileIdentity, packet: ShadowReviewPacket
    ) -> Self:
        if (
            type(profile) is not ReviewerProfileIdentity
            or type(packet) is not ShadowReviewPacket
        ):
            raise ShadowReviewValueError("shadow-review request binding is invalid")
        data = {
            "mode": QUALIFICATION_MODE,
            "packet": packet.to_data(),
            "profile": profile.to_data(),
            "review_case_id": packet.review_case_id,
            "schema_version": REQUEST_SCHEMA,
        }
        identity = "review_request_" + hashlib.sha256(
            canonical_json(data).encode("utf-8")
        ).hexdigest()
        schema = canonical_json(_reviewer_output_schema(packet, identity))
        return cls(
            identity, packet.review_case_id, profile, packet, schema,
            hashlib.sha256(schema.encode("utf-8")).hexdigest(),
        )

    @classmethod
    def synthetic_canary(cls, profile: ReviewerProfileIdentity) -> Self:
        """Build one deterministic, synthetic request through the production contract."""
        case_id = "review_case_" + ("0" * 64)
        proposal_id = "review_proposal_" + ("1" * 64)
        candidate_id = "alternative_" + ("2" * 64)
        citation_id = "synthetic_citation_" + ("3" * 64)
        packet = ShadowReviewPacket(
            case_id,
            (
                {
                    "candidates": [
                        {
                            "assessment": {
                                "confidence": "synthetic",
                                "problems": [],
                            },
                            "candidate_id": candidate_id,
                            "correlations": [],
                            "date_event": None,
                            "date_precision": None,
                            "decision_summary": {
                                "bibliographic_compatibility_ids": [],
                                "creator_roles": {
                                    "excluded": [],
                                    "included": [],
                                },
                                "evidence_scope": "work",
                                "publication": None,
                                "selected_manifestation_binding": (
                                    "associated_work_only"
                                ),
                                "support": {
                                    "independent_provider_correlations": 0,
                                    "local_evidence": 0,
                                    "target_only_preservation": 0,
                                },
                            },
                            "evidence_references": [citation_id],
                            "scope": "work",
                            "value": "Synthetic title",
                        }
                    ],
                    "current": {
                        "confidence": "unknown",
                        "source": "synthetic",
                        "value": "Current synthetic title",
                    },
                    "decision_summary": {
                        "evidence_sufficiency": "manual",
                        "target_only_preservation": 0,
                    },
                    "destination": "title",
                    "outcome": "unresolved_choice",
                    "proposal_id": proposal_id,
                    "reasons": ["Synthetic compatibility canary."],
                },
            ),
            (
                {
                    "citation_id": citation_id,
                    "fact": {
                        "reference": citation_id,
                        "state": "synthetic",
                        "value": "Synthetic title",
                    },
                },
            ),
            (),
        )
        return cls.create_from_packet(profile, packet)

    def expected_id(self) -> str:
        data = {"mode": self.mode, "packet": self.packet.to_data(), "profile": self.profile.to_data(), "review_case_id": self.review_case_id, "schema_version": self.schema_version}
        return "review_request_" + hashlib.sha256(
            canonical_json(data).encode("utf-8")
        ).hexdigest()

    def to_data(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "packet": self.packet.to_data(),
            "output_schema": self.output_schema,
            "output_schema_digest": self.output_schema_digest,
            "profile": self.profile.to_data(),
            "review_case_id": self.review_case_id,
            "review_request_id": self.review_request_id,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(
        cls, serialized: str, source_report: MetadataReport | None = None
    ) -> Self:
        return cls._from_json(serialized, source_report, source_validated=False)

    @classmethod
    def from_source_validated_json(cls, serialized: str) -> Self:
        """Decode same-process bytes already validated with their source report."""
        return cls._from_json(serialized, None, source_validated=True)

    @classmethod
    def _from_json(
        cls,
        serialized: str,
        source_report: MetadataReport | None,
        *,
        source_validated: bool,
    ) -> Self:
        raw = _canonical_object(serialized, MAX_REQUEST_BYTES)
        if set(raw) != {
            "mode",
            "packet",
            "output_schema",
            "output_schema_digest",
            "profile",
            "review_case_id",
            "review_request_id",
            "schema_version",
        }:
            raise ShadowReviewValueError("shadow-review request is invalid")
        try:
            profile_json = canonical_json(raw["profile"])
            packet_json = canonical_json(raw["packet"])
            value = cls(
                raw["review_request_id"],  # type: ignore[arg-type]
                raw["review_case_id"],  # type: ignore[arg-type]
                ReviewerProfileIdentity.from_json(profile_json),
                (
                    ShadowReviewPacket.from_source_validated_json(packet_json)
                    if source_validated
                    else ShadowReviewPacket.from_json(packet_json, source_report)
                ),
                raw["output_schema"],  # type: ignore[arg-type]
                raw["output_schema_digest"],  # type: ignore[arg-type]
                raw["mode"],  # type: ignore[arg-type]
                raw["schema_version"],  # type: ignore[arg-type]
            )
        except (TypeError, ValueError) as error:
            raise ShadowReviewValueError("shadow-review request is invalid") from error
        if value.to_json() != serialized:
            raise ShadowReviewValueError("shadow-review request is not canonical")
        return value


@dataclass(frozen=True, slots=True)
class ShadowReviewResult:
    review_request_id: str
    recommendations: tuple[dict[str, object], ...]
    schema_version: str = RESULT_SCHEMA

    @staticmethod
    def validate_identity_for(
        source: dict[str, object],
        request: ShadowReviewRequest,
    ) -> None:
        """Validate the canonical outer result shape and its bound identity."""
        if type(source) is not dict or set(source) != {
            "recommendations",
            "review_request_id",
            "schema_version",
        }:
            raise ShadowReviewSemanticError(
                "shadow-review result is invalid"
            )
        review_request_id = source["review_request_id"]
        if (
            type(review_request_id) is not str
            or _REQUEST_ID.fullmatch(review_request_id) is None
        ):
            raise ShadowReviewSemanticError(
                "shadow-review result is invalid"
            )
        if review_request_id != request.review_request_id:
            raise ShadowReviewIdentityError(
                "shadow-review result identity is invalid"
            )

    def validate_for(self, request: ShadowReviewRequest) -> None:
        if (
            type(self.review_request_id) is not str
            or _REQUEST_ID.fullmatch(self.review_request_id) is None
        ):
            raise ShadowReviewSemanticError(
                "shadow-review result is invalid"
            )
        if self.review_request_id != request.review_request_id:
            raise ShadowReviewIdentityError(
                "shadow-review result identity is invalid"
            )
        if (
            self.schema_version != RESULT_SCHEMA
            or type(self.recommendations) is not tuple
        ):
            raise ShadowReviewSemanticError("shadow-review result is invalid")
        expected = {
            value["proposal_id"]: {
                item["candidate_id"] for item in value["candidates"]
            }
            for value in request.packet.proposals
        }
        manual_bibliographic_proposals = {
            value["proposal_id"]
            for value in request.packet.proposals
            if "no_safe_bibliographic_combination" in value["reasons"]
        }
        citations = {value["citation_id"] for value in request.packet.citations}
        seen: set[str] = set()
        for recommendation in self.recommendations:
            if type(recommendation) is not dict or set(recommendation) != {
                "candidate_id",
                "citations",
                "problems",
                "proposal_id",
                "reasons",
                "recommendation",
            }:
                raise ShadowReviewSemanticError("shadow-review result is invalid")
            proposal_id = recommendation["proposal_id"]
            kind = recommendation["recommendation"]
            candidate_id = recommendation["candidate_id"]
            reasons = recommendation["reasons"]
            problems = recommendation["problems"]
            used_citations = recommendation["citations"]
            if (
                type(proposal_id) is not str
                or proposal_id not in expected
                or proposal_id in seen
                or kind not in _RECOMMENDATIONS
                or type(reasons) is not list
                or not reasons
                or len(reasons) > 16
                or type(problems) is not list
                or len(problems) > MAX_PROBLEMS
                or type(used_citations) is not list
                or not used_citations
                or len(used_citations) > MAX_CITATIONS
                or used_citations != sorted(set(used_citations))
                or not set(used_citations).issubset(citations)
                or any(not _bounded_text(value) for value in (*reasons, *problems))
            ):
                raise ShadowReviewValueError("shadow-review result is invalid")
            if kind == "recommend_candidate":
                if type(candidate_id) is not str or candidate_id not in expected[proposal_id]:
                    raise ShadowReviewSemanticError(
                        "shadow-review result is invalid"
                    )
            elif candidate_id is not None:
                raise ShadowReviewSemanticError("shadow-review result is invalid")
            if kind == "manual" and not problems:
                raise ShadowReviewSemanticError("shadow-review result is invalid")
            if (
                proposal_id in manual_bibliographic_proposals
                and kind != "manual"
            ):
                raise ShadowReviewSemanticError(
                    "shadow-review bibliographic result requires manual review"
                )
            seen.add(proposal_id)
        if seen != set(expected):
            raise ShadowReviewSemanticError(
                "shadow-review result coverage is invalid"
            )
        self.selected_bibliographic_combination_id(request)

    def selected_bibliographic_combination_id(
        self,
        request: ShadowReviewRequest,
    ) -> str | None:
        """Derive the one allowed combination selected by existing advice."""
        if not request.packet.bibliographic_combinations:
            return None
        proposal_by_id = {
            value["proposal_id"]: value
            for value in request.packet.proposals
        }
        recommendation_by_destination = {
            proposal_by_id[value["proposal_id"]]["destination"]: value
            for value in self.recommendations
            if value.get("proposal_id") in proposal_by_id
        }
        matches: list[str] = []
        for combination in request.packet.bibliographic_combinations:
            if all(
                _recommendation_matches_bibliographic_selection(
                    recommendation_by_destination.get(
                        selection["destination"]
                    ),
                    selection,
                )
                for selection in combination["selections"]
            ):
                matches.append(combination["combination_id"])  # type: ignore[arg-type]
        if len(matches) != 1:
            raise ShadowReviewSemanticError(
                "shadow-review bibliographic combination is invalid"
            )
        return matches[0]

    def to_data(self) -> dict[str, object]:
        return {
            "recommendations": [_copy_json(value) for value in self.recommendations],
            "review_request_id": self.review_request_id,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str, request: ShadowReviewRequest) -> Self:
        try:
            raw = _canonical_object(serialized, MAX_RESULT_BYTES)
        except ShadowReviewValueError as error:
            raise ShadowReviewDocumentError(
                "shadow-review result document is malformed"
            ) from error
        cls.validate_identity_for(raw, request)
        if type(raw["recommendations"]) is not list:
            raise ShadowReviewSemanticError("shadow-review result is invalid")
        try:
            value = cls(
                raw["review_request_id"],  # type: ignore[arg-type]
                tuple(raw["recommendations"]),
                raw["schema_version"],  # type: ignore[arg-type]
            )
            value.validate_for(request)
        except ShadowReviewSemanticError:
            raise
        except (TypeError, ValueError) as error:
            raise ShadowReviewSemanticError(
                "shadow-review result is invalid"
            ) from error
        if value.to_json() != serialized:
            raise ShadowReviewDocumentError(
                "shadow-review result document is not canonical"
            )
        return value


def _recommendation_matches_bibliographic_selection(
    recommendation: object,
    selection: dict[str, object],
) -> bool:
    if type(recommendation) is not dict:
        return False
    if selection["kind"] == "candidate":
        return (
            recommendation.get("recommendation")
            == "recommend_candidate"
            and recommendation.get("candidate_id")
            == selection["alternative_id"]
        )
    return (
        selection["kind"] == "preserve_target"
        and recommendation.get("recommendation")
        == "recommend_keep_current"
        and recommendation.get("candidate_id") is None
    )


def _date_link_local_references(report: MetadataReport) -> set[str]:
    """Close reviewer citations over every retained local date-link operand."""
    references: set[str] = set()
    for provider in report.providers:
        for candidate in provider.candidates:
            for value in (*candidate.claims, *candidate.evidence):
                if (
                    (
                        type(value) is ProviderClaim
                        and value.kind is ClaimKind.PUBLICATION_DATE
                    )
                    or (
                        type(value) is ProviderEvidence
                        and value.kind in _PROVIDER_DATE_EVIDENCE
                    )
                ):
                    references.update(
                        link.local_reference
                        for link in value.links
                        if link.local_reference is not None
                    )
    return references


def _evidence_index(value: object) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    if type(value) is dict:
        reference = value.get("reference")
        if type(reference) is str:
            result[reference] = _copy_json(value)
        source_id = value.get("source_id")
        if type(source_id) is str:
            result[source_id] = _copy_json(value)
        providers = value.get("providers")
        if type(providers) is list:
            for provider in providers:
                if type(provider) is not dict or type(provider.get("candidates")) is not list:
                    continue
                for candidate in provider["candidates"]:
                    if (
                        type(candidate) is not dict
                        or type(candidate.get("identity")) is not str
                        or type(candidate.get("evidence")) is not list
                    ):
                        continue
                    for evidence in candidate["evidence"]:
                        if type(evidence) is not dict:
                            continue
                        rich = {
                            "access": {
                                "accessed_at": provider.get("accessed_at"),
                                "authority": provider.get("authority"),
                                "query_kind": provider.get("query_kind"),
                            },
                            "candidate": candidate["identity"],
                            "evidence": evidence,
                            "linkage": _provider_linkage(candidate),
                            "provider": provider.get("provider"),
                            "work_identity": candidate.get("work_identity"),
                        }
                        rich_identity = {
                            "candidate": candidate["identity"],
                            "evidence": evidence,
                            "provider": provider.get("provider"),
                        }
                        rich_id = "evidence_" + hashlib.sha256(
                            canonical_json(rich_identity).encode("utf-8")
                        ).hexdigest()
                        result[rich_id] = rich
        for item in value.values():
            result.update(_evidence_index(item))
        if type(providers) is list:
            for provider in providers:
                if (
                    type(provider) is not dict
                    or type(provider.get("candidates")) is not list
                ):
                    continue
                for candidate in provider["candidates"]:
                    if (
                        type(candidate) is not dict
                        or type(candidate.get("claims")) is not list
                        or type(candidate.get("identity")) is not str
                        or type(candidate.get("work_identity")) is not str
                    ):
                        continue
                    for claim in candidate["claims"]:
                        if (
                            type(claim) is not dict
                            or type(claim.get("reference")) is not str
                            or claim.get("date_event") is None
                        ):
                            continue
                        result[claim["reference"]] = {
                            "access": {
                                "accessed_at": provider.get("accessed_at"),
                                "authority": provider.get("authority"),
                                "query_kind": provider.get("query_kind"),
                            },
                            "candidate": candidate["identity"],
                            "fact": _copy_json(claim),
                            "linkage": _provider_linkage(candidate),
                            "provider": provider.get("provider"),
                            "work_identity": candidate["work_identity"],
                        }
    elif type(value) is list:
        for item in value:
            result.update(_evidence_index(item))
    return result


def _provider_linkage(candidate: dict[str, object]) -> dict[str, object]:
    claims = candidate.get("claims")
    evidence = candidate.get("evidence")
    identifier_facts = (
        sorted(
            (
                {
                    "applicability": claim["applicability"],
                    "kind": claim["kind"],
                    "raw_order": claim["raw_order"],
                    "reference": claim["reference"],
                    "scope": claim["scope"],
                    "value": claim["value"],
                }
                for claim in claims
                if (
                    type(claim) is dict
                    and claim.get("kind") in {"asin", "isbn"}
                    and claim.get("state") == "valid"
                    and type(claim.get("reference")) is str
                    and type(claim.get("value")) is str
                )
            ),
            key=lambda fact: fact["reference"],
        )
        if type(claims) is list
        else []
    )
    if len(identifier_facts) > MAX_LINKAGE_IDENTIFIER_FACTS:
        raise ShadowReviewValueError(
            "shadow-review provider identifier linkage exceeds its limit"
        )
    format_facts = sorted(
        (
            _copy_json(fact)
            for fact in evidence
            if type(fact) is dict and fact.get("kind") == "physical_format"
        ),
        key=canonical_json,
    ) if type(evidence) is list else []
    if len(format_facts) > MAX_LINKAGE_FORMAT_FACTS:
        raise ShadowReviewValueError(
            "shadow-review provider format linkage exceeds its limit"
        )
    return {
        "format_facts": format_facts,
        "identifier_facts": identifier_facts,
        "identifier_references": [
            fact["reference"] for fact in identifier_facts
        ],
    }


def _validate_provider_citation(
    citation_id: str,
    value: dict[str, object],
    *,
    require_date: bool,
) -> None:
    claim_keys = {
        "access",
        "candidate",
        "fact",
        "linkage",
        "provider",
        "work_identity",
    }
    evidence_keys = {
        "access",
        "candidate",
        "evidence",
        "linkage",
        "provider",
        "work_identity",
    }
    value_keys = set(value)
    if value_keys != claim_keys and value_keys != evidence_keys:
        raise ShadowReviewValueError("shadow-review provider citation is invalid")
    provider = value["provider"]
    candidate = value["candidate"]
    work_identity = value["work_identity"]
    if (
        type(provider) is not str
        or _PROVIDER_NAME.fullmatch(provider) is None
        or type(candidate) is not str
        or type(work_identity) is not str
    ):
        raise ShadowReviewValueError("shadow-review provider citation is invalid")
    try:
        validate_projection_text(candidate, "provider candidate identity", 128)
        validate_projection_text(work_identity, "provider work identity", 128)
    except EvidenceValueError as error:
        raise ShadowReviewValueError(
            "shadow-review provider citation is invalid"
        ) from error
    _validate_provider_linkage(value["linkage"], provider, candidate)
    if "fact" in value:
        fact = value["fact"]
        if type(fact) is not dict or set(fact) != {
            "applicability",
            "date_confidence",
            "date_event",
            "date_lexical",
            "date_precision",
            "kind",
            "links",
            "raw_order",
            "reference",
            "role",
            "scope",
            "state",
            "value",
        }:
            raise ShadowReviewValueError(
                "shadow-review provider citation is invalid"
            )
        try:
            reconstructed = ProviderClaim.create(
                provider=provider,
                candidate=candidate,
                kind=ClaimKind(fact["kind"]),
                value=fact["value"],
                state=FieldState(fact["state"]),
                scope=ClaimScope(fact["scope"]),
                applicability=fact["applicability"],
                role=fact["role"],
                raw_order=fact["raw_order"],
                date_event=(
                    None
                    if fact["date_event"] is None
                    else DateEvent(fact["date_event"])
                ),
                date_precision=(
                    None
                    if fact["date_precision"] is None
                    else DatePrecision(fact["date_precision"])
                ),
                date_lexical=fact["date_lexical"],
                date_confidence=(
                    None
                    if fact["date_confidence"] is None
                    else DateLinkConfidence(fact["date_confidence"])
                ),
                links=_comparison_links(fact["links"]),
            )
        except (ReportValueError, TypeError, ValueError) as error:
            raise ShadowReviewValueError(
                "shadow-review provider citation is invalid"
            ) from error
        if (
            reconstructed.reference != citation_id
            or fact["reference"] != citation_id
            or reconstructed.kind is not ClaimKind.PUBLICATION_DATE
            or reconstructed.date_event is None
        ):
            raise ShadowReviewValueError(
                "shadow-review provider citation is invalid"
            )
        return
    evidence_data = value["evidence"]
    if type(evidence_data) is not dict or set(evidence_data) != {
        "date_confidence",
        "date_event",
        "date_normalized",
        "date_precision",
        "kind",
        "links",
        "role",
        "scope",
        "source_ordinal",
        "value",
    }:
        raise ShadowReviewValueError("shadow-review provider citation is invalid")
    try:
        evidence = ProviderEvidence(
            ProviderEvidenceKind(evidence_data["kind"]),
            evidence_data["value"],
            ClaimScope(evidence_data["scope"]),
            evidence_data["role"],
            (
                None
                if evidence_data["date_event"] is None
                else DateEvent(evidence_data["date_event"])
            ),
            (
                None
                if evidence_data["date_precision"] is None
                else DatePrecision(evidence_data["date_precision"])
            ),
            evidence_data["date_normalized"],
            (
                None
                if evidence_data["date_confidence"] is None
                else DateLinkConfidence(evidence_data["date_confidence"])
            ),
            evidence_data["source_ordinal"],
            _comparison_links(evidence_data["links"]),
        )
    except (ReportValueError, TypeError, ValueError) as error:
        raise ShadowReviewValueError(
            "shadow-review provider citation is invalid"
        ) from error
    expected_id = "evidence_" + hashlib.sha256(
        canonical_json(
            {
                "candidate": candidate,
                "evidence": evidence.to_data(),
                "provider": provider,
            }
        ).encode("utf-8")
    ).hexdigest()
    if (
        evidence.to_data() != evidence_data
        or citation_id != expected_id
        or (
            require_date
            and (
                evidence.kind not in _PROVIDER_DATE_EVIDENCE
                or evidence.date_event is None
            )
        )
    ):
        raise ShadowReviewValueError("shadow-review provider citation is invalid")


def _comparison_links(value: object) -> tuple[ComparisonLink, ...]:
    if type(value) is not list:
        raise ShadowReviewValueError(
            "shadow-review provider date comparison is invalid"
        )
    links: list[ComparisonLink] = []
    for item in value:
        if type(item) is not dict or set(item) != {
            "local_reference",
            "method",
            "outcome",
            "publication_date_relation",
            "shared_date_precision",
        }:
            raise ShadowReviewValueError(
                "shadow-review provider date comparison is invalid"
            )
        try:
            links.append(
                ComparisonLink(
                    item["local_reference"],
                    Comparison(item["outcome"]),
                    ComparisonMethod(item["method"]),
                    (
                        None
                        if item["publication_date_relation"] is None
                        else PublicationDateRelation(
                            item["publication_date_relation"]
                        )
                    ),
                    (
                        None
                        if item["shared_date_precision"] is None
                        else DatePrecision(item["shared_date_precision"])
                    ),
                )
            )
        except (ReportValueError, TypeError, ValueError) as error:
            raise ShadowReviewValueError(
                "shadow-review provider date comparison is invalid"
            ) from error
    return tuple(links)


def _contains_provider_date(value: dict[str, object]) -> bool:
    claim = value.get("fact")
    evidence = value.get("evidence")
    return (
        type(claim) is dict
        and (
            claim.get("kind") == ClaimKind.PUBLICATION_DATE.value
            or claim.get("date_event") is not None
        )
    ) or (
        type(evidence) is dict
        and (
            evidence.get("kind") in _PROVIDER_DATE_EVIDENCE_VALUES
            or evidence.get("date_event") is not None
        )
    )


def _is_provider_citation(citation_id: str, fact: dict[str, object]) -> bool:
    return (
        citation_id.startswith(("provider_", "evidence_"))
        or "provider" in fact
        or "linkage" in fact
        or _contains_provider_date(fact)
    )


def _validate_provider_linkage(
    value: object,
    provider: str,
    candidate: str,
) -> None:
    if type(value) is not dict or set(value) != {
        "format_facts",
        "identifier_facts",
        "identifier_references",
    }:
        raise ShadowReviewValueError("shadow-review provider linkage is invalid")
    identifiers = value["identifier_facts"]
    references = value["identifier_references"]
    formats = value["format_facts"]
    if (
        type(identifiers) is not list
        or len(identifiers) > MAX_LINKAGE_IDENTIFIER_FACTS
        or type(references) is not list
        or any(
            type(reference) is not str
            or _PROVIDER_REFERENCE.fullmatch(reference) is None
            for reference in references
        )
        or type(formats) is not list
        or len(formats) > MAX_LINKAGE_FORMAT_FACTS
        or formats != sorted(formats, key=canonical_json)
    ):
        raise ShadowReviewValueError("shadow-review provider linkage is invalid")
    expected_references: list[str] = []
    for fact in identifiers:
        if (
            type(fact) is not dict
            or set(fact)
            != {
                "applicability",
                "kind",
                "raw_order",
                "reference",
                "scope",
                "value",
            }
            or type(fact["kind"]) is not str
            or fact["kind"] not in {"asin", "isbn"}
            or type(fact["applicability"]) is not str
            or fact["applicability"]
            not in {"ebook", "print", "unknown", "ambiguous", "not_applicable"}
            or type(fact["scope"]) is not str
            or fact["scope"] != "edition"
            or type(fact["raw_order"]) is not int
            or fact["raw_order"] < 0
            or type(fact["reference"]) is not str
            or _PROVIDER_REFERENCE.fullmatch(fact["reference"]) is None
            or type(fact["value"]) is not str
            or not _bounded_scalar(fact["value"])
        ):
            raise ShadowReviewValueError(
                "shadow-review provider linkage is invalid"
            )
        try:
            kind = ClaimKind(fact["kind"])
            reconstructed = ProviderClaim.create(
                provider=provider,
                candidate=candidate,
                kind=kind,
                value=fact["value"],
                state=FieldState.VALID,
                scope=ClaimScope.EDITION,
                applicability=fact["applicability"],
                raw_order=fact["raw_order"],
            )
        except (ReportValueError, TypeError, ValueError) as error:
            raise ShadowReviewValueError(
                "shadow-review provider linkage is invalid"
            ) from error
        if (
            (kind is ClaimKind.ASIN and _ASIN.fullmatch(fact["value"]) is None)
            or reconstructed.reference != fact["reference"]
        ):
            raise ShadowReviewValueError(
                "shadow-review provider linkage is invalid"
            )
        expected_references.append(fact["reference"])
    if (
        identifiers
        != sorted(identifiers, key=lambda fact: fact["reference"])
        or references != expected_references
        or references != sorted(set(references))
    ):
        raise ShadowReviewValueError("shadow-review provider linkage is invalid")
    for fact in formats:
        if (
            type(fact) is not dict
            or set(fact)
            != {
                "date_confidence",
                "date_event",
                "date_normalized",
                "date_precision",
                "kind",
                "links",
                "role",
                "scope",
                "source_ordinal",
                "value",
            }
            or type(fact["kind"]) is not str
            or fact["kind"] != "physical_format"
            or fact["role"] is not None
            or type(fact["scope"]) is not str
            or fact["scope"] not in {"work", "edition"}
            or type(fact["source_ordinal"]) is not int
            or fact["source_ordinal"] < 0
            or fact["links"] != []
            or any(
                fact[key] is not None
                for key in (
                    "date_confidence",
                    "date_event",
                    "date_normalized",
                    "date_precision",
                )
            )
            or type(fact["value"]) is not str
            or not _bounded_scalar(fact["value"])
        ):
            raise ShadowReviewValueError(
                "shadow-review provider linkage is invalid"
            )
        try:
            projected = ProviderEvidence(
                ProviderEvidenceKind.PHYSICAL_FORMAT,
                fact["value"],
                ClaimScope(fact["scope"]),
                source_ordinal=fact["source_ordinal"],
            )
        except (ReportValueError, TypeError, ValueError) as error:
            raise ShadowReviewValueError(
                "shadow-review provider linkage is invalid"
            ) from error
        if projected.to_data() != fact:
            raise ShadowReviewValueError(
                "shadow-review provider linkage is invalid"
            )


def _bounded_scalar(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_SCALAR_CHARACTERS
        and value.strip() == value
        and not any(ord(character) < 0x20 for character in value)
    )


def _copy_json(value: object) -> object:
    if type(value) is dict:
        return {key: _copy_json(item) for key, item in value.items()}
    if type(value) is list:
        return [_copy_json(item) for item in value]
    return value


def _bounded_text(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_REASON_CHARACTERS
        and value.strip() == value
        and not any(ord(character) < 0x20 for character in value)
    )


def _reviewer_output_schema(packet: ShadowReviewPacket, review_request_id: str) -> dict[str, object]:
    proposal_ids = [value["proposal_id"] for value in packet.proposals]
    citation_ids = [value["citation_id"] for value in packet.citations]
    candidate_ids = sorted(
        {
            candidate["candidate_id"]
            for proposal in packet.proposals
            for candidate in proposal["candidates"]  # type: ignore[union-attr]
        }
    )
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "additionalProperties": False, "properties": {"recommendations": {"items": {"additionalProperties": False, "properties": {"candidate_id": {"anyOf": [{"enum": candidate_ids, "type": "string"}, {"type": "null"}]}, "citations": {"items": {"enum": citation_ids, "type": "string"}, "maxItems": MAX_CITATIONS, "minItems": 1, "type": "array"}, "problems": {"items": {"maxLength": MAX_REASON_CHARACTERS, "minLength": 1, "type": "string"}, "maxItems": MAX_PROBLEMS, "type": "array"}, "proposal_id": {"enum": proposal_ids, "type": "string"}, "reasons": {"items": {"maxLength": MAX_REASON_CHARACTERS, "minLength": 1, "type": "string"}, "maxItems": 16, "minItems": 1, "type": "array"}, "recommendation": {"enum": ["manual", "recommend_candidate", "recommend_keep_current"], "type": "string"}}, "required": ["candidate_id", "citations", "problems", "proposal_id", "reasons", "recommendation"], "type": "object"}, "maxItems": len(proposal_ids), "minItems": len(proposal_ids), "type": "array"}, "review_request_id": {"const": review_request_id, "type": "string"}, "schema_version": {"const": RESULT_SCHEMA, "type": "string"}}, "required": ["recommendations", "review_request_id", "schema_version"], "type": "object"}


def _canonical_object(serialized: str, maximum: int) -> dict[str, object]:
    try:
        if (
            type(serialized) is not str
            or not serialized
            or len(serialized.encode("utf-8")) > maximum
        ):
            raise ValueError
        raw = json.loads(
            serialized,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        json.JSONDecodeError,
    ) as error:
        raise ShadowReviewValueError("reviewer document is invalid") from error
    if type(raw) is not dict or canonical_json(raw) != serialized:
        raise ShadowReviewValueError("reviewer document is not canonical")
    return raw


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ShadowReviewValueError("reviewer document has duplicate keys")
        result[key] = value
    return result


__all__ = [
    "MAX_CITATIONS",
    "MAX_LINKAGE_FORMAT_FACTS",
    "MAX_LINKAGE_IDENTIFIER_FACTS",
    "MAX_OUTPUT_SCHEMA_BYTES",
    "MAX_PACKET_BYTES",
    "MAX_PROFILE_IDENTITY_BYTES",
    "MAX_REQUEST_BYTES",
    "MAX_RESULT_BYTES",
    "PACKET_SCHEMA",
    "PROFILE_IDENTITY_SCHEMA",
    "QUALIFICATION_MODE",
    "REQUEST_SCHEMA",
    "RESULT_SCHEMA",
    "ReviewerProfileIdentity",
    "ShadowReviewPacket",
    "ShadowReviewRequest",
    "ShadowReviewResult",
    "ShadowReviewDocumentError",
    "ShadowReviewIdentityError",
    "ShadowReviewSemanticError",
    "ShadowReviewValueError",
    "canonical_json",
]
