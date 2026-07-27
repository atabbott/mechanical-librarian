"""Closed qualification-only shadow-review documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Self

from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import ProposalOutcome, ProposalSet
from mechanical_librarian.domain.review_case import ReviewCase

PACKET_SCHEMA = "shadow-review-packet-v1"
RESULT_SCHEMA = "shadow-review-result-v1"
REQUEST_SCHEMA = "shadow-review-request-v1"
PROFILE_IDENTITY_SCHEMA = "reviewer-profile-identity-v1"
QUALIFICATION_MODE = "qualification_only"
MAX_PACKET_BYTES = 1_048_576
MAX_RESULT_BYTES = 262_144
MAX_PROFILE_IDENTITY_BYTES = 16_384
MAX_REASON_CHARACTERS = 1_024
MAX_PROBLEMS = 32
MAX_CITATIONS = 256
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^review_case_[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^review_request_[0-9a-f]{64}$")
_PROPOSAL_ID = re.compile(r"^review_proposal_[0-9a-f]{64}$")
_ALTERNATIVE_ID = re.compile(r"^alternative_[0-9a-f]{64}$")
_RECOMMENDATIONS = frozenset(
    {"recommend_candidate", "recommend_keep_current", "manual"}
)


class ShadowReviewValueError(ValueError):
    """A reviewer document is outside the closed F11 contract."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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


@dataclass(frozen=True, slots=True)
class ShadowReviewPacket:
    review_case_id: str
    proposals: tuple[dict[str, object], ...]
    citations: tuple[dict[str, object], ...]
    correlations: tuple[dict[str, object], ...]
    schema_version: str = PACKET_SCHEMA

    def __post_init__(self) -> None:
        if (
            self.schema_version != PACKET_SCHEMA
            or _CASE_ID.fullmatch(self.review_case_id) is None
            or type(self.proposals) is not tuple
            or not self.proposals
            or type(self.citations) is not tuple
            or type(self.correlations) is not tuple
        ):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        proposal_ids: list[str] = []
        citation_ids: list[str] = []
        for proposal in self.proposals:
            if type(proposal) is not dict or set(proposal) != {
                "candidates",
                "current",
                "destination",
                "outcome",
                "proposal_id",
                "reasons",
            }:
                raise ShadowReviewValueError("shadow-review packet is invalid")
            proposal_id = proposal.get("proposal_id")
            if type(proposal_id) is not str or _PROPOSAL_ID.fullmatch(proposal_id) is None:
                raise ShadowReviewValueError("shadow-review packet is invalid")
            proposal_ids.append(proposal_id)
            candidates = proposal.get("candidates")
            if type(candidates) is not list or not candidates:
                raise ShadowReviewValueError("shadow-review packet is invalid")
            candidate_ids = [value.get("candidate_id") for value in candidates if type(value) is dict]
            if (
                len(candidate_ids) != len(candidates)
                or any(type(value) is not str or _ALTERNATIVE_ID.fullmatch(value) is None for value in candidate_ids)
                or candidate_ids != sorted(set(candidate_ids))
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
        if proposal_ids != sorted(set(proposal_ids)):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        for citation in self.citations:
            if (
                type(citation) is not dict
                or set(citation) != {"citation_id", "fact"}
                or type(citation.get("citation_id")) is not str
                or type(citation.get("fact")) is not dict
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
            citation_ids.append(citation["citation_id"])  # type: ignore[arg-type]
        if citation_ids != sorted(set(citation_ids)):
            raise ShadowReviewValueError("shadow-review packet is invalid")
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
        ):
            raise ShadowReviewValueError("shadow-review packet binding is invalid")
        current = {
            value.destination.value: value.to_data() for value in report.target.fields
        }
        proposals: list[dict[str, object]] = []
        references: set[str] = set()
        correlations: dict[str, dict[str, set[str]]] = {}
        for wrapped in case.proposals:
            result = wrapped["result"]
            if result["outcome"] not in {
                ProposalOutcome.PROPOSE_CHANGE.value,
                ProposalOutcome.UNRESOLVED_CHOICE.value,
            }:
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
                candidates.append(
                    {
                        "assessment": alternative["assessment"],
                        "candidate_id": alternative["alternative_id"],
                        "correlations": alternative["correlations"],
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
                    "destination": result["destination"],
                    "outcome": result["outcome"],
                    "proposal_id": wrapped["proposal_id"],
                    "reasons": result["reasons"],
                }
            )
        evidence = _evidence_index(report.to_data())
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
        return cls(
            case.review_case_id,
            tuple(sorted(proposals, key=lambda value: value["proposal_id"])),
            citations,
            correlation_facts,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "citations": [_copy_json(value) for value in self.citations],
            "correlations": [_copy_json(value) for value in self.correlations],
            "proposals": [_copy_json(value) for value in self.proposals],
            "review_case_id": self.review_case_id,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        raw = _canonical_object(serialized, MAX_PACKET_BYTES)
        if set(raw) != {
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
        ):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        try:
            value = cls(
                raw["review_case_id"],  # type: ignore[arg-type]
                tuple(raw["proposals"]),
                tuple(raw["citations"]),
                tuple(raw["correlations"]),
                raw["schema_version"],  # type: ignore[arg-type]
            )
        except (TypeError, ValueError) as error:
            raise ShadowReviewValueError("shadow-review packet is invalid") from error
        if value.to_json() != serialized:
            raise ShadowReviewValueError("shadow-review packet is not canonical")
        return value


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
        if (
            self.schema_version != REQUEST_SCHEMA
            or self.mode != QUALIFICATION_MODE
            or _REQUEST_ID.fullmatch(self.review_request_id) is None
            or type(self.profile) is not ReviewerProfileIdentity
            or type(self.packet) is not ShadowReviewPacket
            or type(self.output_schema) is not str
            or type(self.output_schema_digest) is not str
            or hashlib.sha256(self.output_schema.encode("utf-8")).hexdigest()
            != self.output_schema_digest
            or canonical_json(json.loads(self.output_schema)) != self.output_schema
            or self.output_schema != canonical_json(_reviewer_output_schema(self.packet, self.review_request_id))
            or self.packet.review_case_id != self.review_case_id
            or self.review_request_id != self.expected_id()
        ):
            raise ShadowReviewValueError("shadow-review request is invalid")

    @classmethod
    def create(
        cls, case: ReviewCase, profile: ReviewerProfileIdentity, packet: ShadowReviewPacket
    ) -> Self:
        data = {
            "mode": QUALIFICATION_MODE,
            "packet": packet.to_data(),
            "profile": profile.to_data(),
            "review_case_id": case.review_case_id,
            "schema_version": REQUEST_SCHEMA,
        }
        identity = "review_request_" + hashlib.sha256(
            canonical_json(data).encode("utf-8")
        ).hexdigest()
        schema = canonical_json(_reviewer_output_schema(packet, identity))
        return cls(
            identity, case.review_case_id, profile, packet, schema,
            hashlib.sha256(schema.encode("utf-8")).hexdigest(),
        )

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
    def from_json(cls, serialized: str) -> Self:
        raw = _canonical_object(serialized, MAX_PACKET_BYTES + MAX_PROFILE_IDENTITY_BYTES)
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
                ShadowReviewPacket.from_json(packet_json),
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

    def validate_for(self, request: ShadowReviewRequest) -> None:
        if (
            self.schema_version != RESULT_SCHEMA
            or self.review_request_id != request.review_request_id
            or type(self.recommendations) is not tuple
        ):
            raise ShadowReviewValueError("shadow-review result is invalid")
        expected = {
            value["proposal_id"]: {
                item["candidate_id"] for item in value["candidates"]
            }
            for value in request.packet.proposals
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
                raise ShadowReviewValueError("shadow-review result is invalid")
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
                    raise ShadowReviewValueError("shadow-review result is invalid")
            elif candidate_id is not None:
                raise ShadowReviewValueError("shadow-review result is invalid")
            if kind == "manual" and not problems:
                raise ShadowReviewValueError("shadow-review result is invalid")
            seen.add(proposal_id)
        if seen != set(expected):
            raise ShadowReviewValueError("shadow-review result coverage is invalid")

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
        raw = _canonical_object(serialized, MAX_RESULT_BYTES)
        if set(raw) != {"recommendations", "review_request_id", "schema_version"}:
            raise ShadowReviewValueError("shadow-review result is invalid")
        if type(raw["recommendations"]) is not list:
            raise ShadowReviewValueError("shadow-review result is invalid")
        try:
            value = cls(
                raw["review_request_id"],  # type: ignore[arg-type]
                tuple(raw["recommendations"]),
                raw["schema_version"],  # type: ignore[arg-type]
            )
            value.validate_for(request)
        except (TypeError, ValueError) as error:
            raise ShadowReviewValueError("shadow-review result is invalid") from error
        if value.to_json() != serialized:
            raise ShadowReviewValueError("shadow-review result is not canonical")
        return value


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
                            "candidate": candidate["identity"],
                            "evidence": evidence,
                            "provider": provider.get("provider"),
                        }
                        rich_id = "evidence_" + hashlib.sha256(
                            canonical_json(rich).encode("utf-8")
                        ).hexdigest()
                        result[rich_id] = rich
        for item in value.values():
            result.update(_evidence_index(item))
    elif type(value) is list:
        for item in value:
            result.update(_evidence_index(item))
    return result


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
    candidate_ids = [
        candidate["candidate_id"]
        for proposal in packet.proposals
        for candidate in proposal["candidates"]  # type: ignore[union-attr]
    ]
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
    "MAX_PACKET_BYTES",
    "MAX_PROFILE_IDENTITY_BYTES",
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
    "ShadowReviewValueError",
    "canonical_json",
]
