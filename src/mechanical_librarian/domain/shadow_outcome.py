"""Immutable, non-authoritative outcomes produced while policy is in Preview."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from mechanical_librarian.domain.metadata_policy import (
    OPTION_CATALOGUE,
    ResolvedMetadataPolicy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport, TargetDestination
from mechanical_librarian.domain.proposal_set import (
    MAX_BIBLIOGRAPHIC_COMBINATIONS,
    ProposalSet,
)
from mechanical_librarian.domain.rich_metadata_preview import (
    RichFieldPreview,
    compile_rich_metadata_preview,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_QUALIFICATION_CANDIDATE_SHA256,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_decision import (
    DECISION_POLICY_SHA256,
    DECISION_POLICY_VERSION,
    ShadowDecisionSet,
)
from mechanical_librarian.domain.shadow_review import MAX_CITATIONS


SHADOW_OUTCOME_SCHEMA = "shadow-outcome-set-v1"
MAX_SHADOW_OUTCOME_BYTES = 1_048_576

_POLICY_CATEGORY_BY_DESTINATION = {
    "authors": "contributor_names",
    "narrators": "contributor_names",
    "language": "language_rendering",
    "publishedDate": "date_precision",
    "publishedYear": "date_precision",
    "publisher": "publisher_preference",
    "title": "title_layout",
    "subtitle": "title_layout",
    "series": "series",
    "genres": "genres",
    "tags": "tags",
    "description": "description_fill",
    "isbn": "isbn_form",
    "cover": "cover_order",
    "asin": "asin",
    "explicit": "explicit",
}
_DISPOSITIONS = frozenset(
    {"abstention", "candidate", "conflict", "ignore", "keep_current", "manual", "unsupported"}
)
_CONFLICT_REASONS = frozenset(
    {"ambiguous_eligible_actions", "bibliographic_combination_unresolved"}
)
_REASON_CODES = frozenset(
    {
        "ambiguous_eligible_actions",
        "bibliographic_combination_unresolved",
        "evidence_sufficiency_manual",
        "insufficient_support",
        "no_eligible_action",
        "target_only_preservation",
        "unique_supported_candidate",
        "unique_supported_preservation",
        "unsupported_destination",
    }
)


class ShadowOutcomeError(ValueError):
    """A persisted Preview outcome is outside the closed decision-only contract."""


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ShadowFieldOutcome:
    outcome_id: str
    proposal_id: str
    destination: str
    policy_category: str | None
    policy_option: str | None
    policy_destination: str
    disposition: str
    action_id: str
    candidate_id: str | None
    citation_ids: tuple[str, ...]
    combination_ids: tuple[str, ...]
    reason_code: str
    rule_id: str
    qualification_candidate_id: str

    def _identity_data(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "candidate_id": self.candidate_id,
            "citation_ids": list(self.citation_ids),
            "combination_ids": list(self.combination_ids),
            "destination": self.destination,
            "disposition": self.disposition,
            "policy_category": self.policy_category,
            "policy_destination": self.policy_destination,
            "policy_option": self.policy_option,
            "proposal_id": self.proposal_id,
            "qualification_candidate_id": self.qualification_candidate_id,
            "reason_code": self.reason_code,
            "rule_id": self.rule_id,
        }

    def __post_init__(self) -> None:
        expected_category = _POLICY_CATEGORY_BY_DESTINATION.get(self.destination)
        expected_disposition = (
            "ignore"
            if self.policy_destination == "ignore"
            else "manual"
            if self.policy_destination == "ask"
            else "unsupported"
            if self.policy_destination == "unsupported"
            else "candidate"
            if self.candidate_id is not None
            else "keep_current"
            if self.reason_code == "unique_supported_preservation"
            else "conflict"
            if self.reason_code in _CONFLICT_REASONS
            else "unsupported"
            if self.reason_code == "unsupported_destination"
            else "abstention"
        )
        if (
            re.fullmatch(r"shadow_outcome_[0-9a-f]{64}", self.outcome_id) is None
            or re.fullmatch(r"review_proposal_[0-9a-f]{64}", self.proposal_id) is None
            or self.destination not in {value.value for value in TargetDestination}
            or self.policy_category != expected_category
            or (
                expected_category is None
                and (
                    self.destination != "series"
                    or self.policy_option is not None
                    or self.policy_destination != "unsupported"
                )
            )
            or (
                expected_category is not None
                and self.policy_option not in OPTION_CATALOGUE[expected_category]
            )
            or self.policy_destination not in {"ask", "automatic", "ignore", "unsupported"}
            or self.disposition not in _DISPOSITIONS
            or self.disposition != expected_disposition
            or re.fullmatch(r"action_[0-9a-f]{64}", self.action_id) is None
            or (
                self.candidate_id is not None
                and (
                    type(self.candidate_id) is not str
                    or re.fullmatch(r"alternative_[0-9a-f]{64}", self.candidate_id) is None
                )
            )
            or re.fullmatch(r"shadow_rule_[0-9a-f]{64}", self.rule_id) is None
            or re.fullmatch(
                r"qualification_candidate_[0-9a-f]{64}",
                self.qualification_candidate_id,
            )
            is None
            or self.citation_ids != tuple(sorted(set(self.citation_ids)))
            or self.combination_ids != tuple(sorted(set(self.combination_ids)))
            or len(self.citation_ids) > MAX_CITATIONS
            or len(self.combination_ids) > MAX_BIBLIOGRAPHIC_COMBINATIONS
            or any(
                type(value) is not str
                or re.fullmatch(
                    r"[a-z][a-z0-9_-]{0,63}_[0-9a-f]{64}", value
                ) is None
                for value in self.citation_ids
            )
            or any(
                type(value) is not str
                or re.fullmatch(r"bibliographic_combination_[0-9a-f]{64}", value)
                is None
                for value in self.combination_ids
            )
            or self.reason_code not in _REASON_CODES
            or self.outcome_id != "shadow_outcome_" + _digest(self._identity_data())
        ):
            raise ShadowOutcomeError("shadow field outcome is invalid")

    def to_data(self) -> dict[str, object]:
        return {"outcome_id": self.outcome_id, **self._identity_data()}


def _rich_field_outcome(
    preview: RichFieldPreview,
    *,
    category: str,
    destination: str,
    policy_destination: str,
    proposal_id: str,
    catalogue_sha256: str,
) -> ShadowFieldOutcome:
    candidate_id = (
        preview.source_candidate_id if preview.outcome == "projected" else None
    )
    disposition = (
        "ignore" if policy_destination == "ignore"
        else "manual" if policy_destination == "ask"
        else "unsupported" if policy_destination == "unsupported"
        else "candidate" if candidate_id is not None
        else "keep_current" if preview.outcome == "no_op"
        else "abstention"
    )
    reason_code = (
        "unique_supported_candidate" if disposition == "candidate"
        else "unique_supported_preservation" if disposition == "keep_current"
        else "unsupported_destination" if disposition == "unsupported"
        else "no_eligible_action"
    )
    action_id = "action_" + _digest({
        "candidate_id": candidate_id,
        "destination": destination,
        "manifest_sha256": F20_QUALIFICATION_CANDIDATE_SHA256,
        "option": preview.option,
        "outcome": preview.outcome,
        "proposal_id": proposal_id,
    })
    rule_id = "shadow_rule_" + _digest({
        "category": category,
        "decision_policy_sha256": DECISION_POLICY_SHA256,
        "decision_policy_version": DECISION_POLICY_VERSION,
        "destination": policy_destination,
        "option": preview.option,
    })
    citation_ids = tuple(sorted(preview.source_evidence_references))
    combination_ids = tuple(sorted(preview.source_combination_ids))
    qualification_candidate_id = "qualification_candidate_" + _digest({
        "action_id": action_id,
        "candidate_id": candidate_id,
        "catalogue_sha256": catalogue_sha256,
        "citation_ids": list(citation_ids),
        "combination_ids": list(combination_ids),
        "rule_id": rule_id,
    })
    field_data = {
        "action_id": action_id,
        "candidate_id": candidate_id,
        "citation_ids": list(citation_ids),
        "combination_ids": list(combination_ids),
        "destination": destination,
        "disposition": disposition,
        "policy_category": category,
        "policy_destination": policy_destination,
        "policy_option": preview.option,
        "proposal_id": proposal_id,
        "qualification_candidate_id": qualification_candidate_id,
        "reason_code": reason_code,
        "rule_id": rule_id,
    }
    return ShadowFieldOutcome(
        "shadow_outcome_" + _digest(field_data), proposal_id, destination,
        category, preview.option, policy_destination, disposition, action_id,
        candidate_id, citation_ids, combination_ids, reason_code, rule_id,
        qualification_candidate_id,
    )


@dataclass(frozen=True, slots=True)
class ShadowOutcomeSet:
    outcome_set_id: str
    review_case_id: str
    report_id: str
    proposal_set_id: str
    target_fingerprint: str
    resolved_policy_id: str
    policy_profile_id: str
    policy_generation: int
    source_decision_set_id: str
    decision_policy_version: str
    decision_policy_sha256: str
    catalogue_sha256: str
    outcomes: tuple[ShadowFieldOutcome, ...]
    schema_version: str = SHADOW_OUTCOME_SCHEMA
    posture: str = "preview"
    authority: str = "none"

    @classmethod
    def create(
        cls,
        case: ReviewCase,
        decisions: ShadowDecisionSet,
        policy: ResolvedMetadataPolicy,
        policy_generation: int,
        report: MetadataReport,
        proposal: ProposalSet,
    ) -> "ShadowOutcomeSet":
        if (
            type(case) is not ReviewCase
            or type(decisions) is not ShadowDecisionSet
            or type(policy) is not ResolvedMetadataPolicy
            or type(report) is not MetadataReport
            or type(proposal) is not ProposalSet
            or type(policy_generation) is not int
            or policy_generation < 1
            or decisions.review_case_id != case.review_case_id
            or report.report_id != case.report_id
            or proposal.proposal_set_id != case.proposal_set_id
            or proposal.to_json() != case.proposal_document
            or policy.resolved_id
            != json.loads(case.proposal_document)["resolved_policy_id"]
        ):
            raise ShadowOutcomeError("shadow outcome source is invalid")
        destinations = dict(policy.destinations)
        options = dict(policy.options)
        fields: list[ShadowFieldOutcome] = []
        for decision in decisions.decisions:
            if decision.destination in {"series", "isbn"}:
                continue
            category = _POLICY_CATEGORY_BY_DESTINATION.get(decision.destination)
            policy_destination = (
                "unsupported" if category is None else destinations[category]
            )
            policy_option = None if category is None else options[category]
            if policy_destination == "ignore":
                disposition = "ignore"
            elif policy_destination == "ask":
                disposition = "manual"
            elif policy_destination == "unsupported" or category is None:
                disposition = "unsupported"
            elif decision.recommendation == "recommend_candidate":
                disposition = "candidate"
            elif decision.recommendation == "recommend_keep_current":
                disposition = "keep_current"
            elif decision.reason_code in _CONFLICT_REASONS:
                disposition = "conflict"
            elif decision.reason_code == "unsupported_destination":
                disposition = "unsupported"
            else:
                disposition = "abstention"
            rule_data = {
                "category": category,
                "decision_policy_sha256": DECISION_POLICY_SHA256,
                "decision_policy_version": DECISION_POLICY_VERSION,
                "destination": policy_destination,
                "option": policy_option,
            }
            rule_id = "shadow_rule_" + _digest(rule_data)
            qualification_candidate_id = "qualification_candidate_" + _digest(
                {
                    "action_id": decision.action_id,
                    "candidate_id": decision.candidate_id,
                    "catalogue_sha256": decisions.catalogue_sha256,
                    "citation_ids": list(decision.citation_ids),
                    "combination_ids": list(decision.combination_ids),
                    "rule_id": rule_id,
                }
            )
            field_data = {
                "action_id": decision.action_id,
                "candidate_id": decision.candidate_id,
                "citation_ids": list(decision.citation_ids),
                "combination_ids": list(decision.combination_ids),
                "destination": decision.destination,
                "disposition": disposition,
                "policy_category": category,
                "policy_destination": policy_destination,
                "policy_option": policy_option,
                "proposal_id": decision.proposal_id,
                "qualification_candidate_id": qualification_candidate_id,
                "reason_code": decision.reason_code,
                "rule_id": rule_id,
            }
            fields.append(
                ShadowFieldOutcome(
                    "shadow_outcome_" + _digest(field_data),
                    decision.proposal_id,
                    decision.destination,
                    category,
                    policy_option,
                    policy_destination,
                    disposition,
                    decision.action_id,
                    decision.candidate_id,
                    decision.citation_ids,
                    decision.combination_ids,
                    decision.reason_code,
                    rule_id,
                    qualification_candidate_id,
                )
            )
        rich = {
            value.field: value
            for value in compile_rich_metadata_preview(policy, report, proposal).fields
        }
        proposals = {
            value["destination"]: value["proposal_id"] for value in case.proposals
        }
        for category, destination in (("series", "series"), ("isbn_form", "isbn")):
            fields.append(
                _rich_field_outcome(
                    rich[category],
                    category=category,
                    destination=destination,
                    policy_destination=destinations[category],
                    proposal_id=proposals[destination],
                    catalogue_sha256=decisions.catalogue_sha256,
                )
            )
        core = {
            "authority": "none",
            "catalogue_sha256": decisions.catalogue_sha256,
            "decision_policy_sha256": DECISION_POLICY_SHA256,
            "decision_policy_version": DECISION_POLICY_VERSION,
            "outcomes": [value.to_data() for value in fields],
            "policy_generation": policy_generation,
            "policy_profile_id": policy.profile_id,
            "posture": "preview",
            "proposal_set_id": case.proposal_set_id,
            "report_id": case.report_id,
            "resolved_policy_id": policy.resolved_id,
            "review_case_id": case.review_case_id,
            "schema_version": SHADOW_OUTCOME_SCHEMA,
            "source_decision_set_id": decisions.decision_set_id,
            "target_fingerprint": case.target_fingerprint,
        }
        return cls(
            "shadow_outcome_set_" + _digest(core),
            case.review_case_id,
            case.report_id,
            case.proposal_set_id,
            case.target_fingerprint,
            policy.resolved_id,
            policy.profile_id,
            policy_generation,
            decisions.decision_set_id,
            DECISION_POLICY_VERSION,
            DECISION_POLICY_SHA256,
            decisions.catalogue_sha256,
            tuple(fields),
        )

    def _identity_data(self) -> dict[str, object]:
        return {
            "authority": self.authority,
            "catalogue_sha256": self.catalogue_sha256,
            "decision_policy_sha256": self.decision_policy_sha256,
            "decision_policy_version": self.decision_policy_version,
            "outcomes": [value.to_data() for value in self.outcomes],
            "policy_generation": self.policy_generation,
            "policy_profile_id": self.policy_profile_id,
            "posture": self.posture,
            "proposal_set_id": self.proposal_set_id,
            "report_id": self.report_id,
            "resolved_policy_id": self.resolved_policy_id,
            "review_case_id": self.review_case_id,
            "schema_version": self.schema_version,
            "source_decision_set_id": self.source_decision_set_id,
            "target_fingerprint": self.target_fingerprint,
        }

    def __post_init__(self) -> None:
        if (
            self.schema_version != SHADOW_OUTCOME_SCHEMA
            or self.posture != "preview"
            or self.authority != "none"
            or re.fullmatch(r"shadow_outcome_set_[0-9a-f]{64}", self.outcome_set_id) is None
            or re.fullmatch(r"review_case_[0-9a-f]{64}", self.review_case_id) is None
            or re.fullmatch(r"report_[0-9a-f]{64}", self.report_id) is None
            or re.fullmatch(r"proposal_set_[0-9a-f]{64}", self.proposal_set_id) is None
            or re.fullmatch(r"[0-9a-f]{64}", self.target_fingerprint) is None
            or re.fullmatch(r"resolved_policy_[0-9a-f]{64}", self.resolved_policy_id) is None
            or re.fullmatch(r"policy_[0-9a-f]{64}", self.policy_profile_id) is None
            or type(self.policy_generation) is not int
            or self.policy_generation < 1
            or re.fullmatch(
                r"shadow_decision_set_[0-9a-f]{64}", self.source_decision_set_id
            )
            is None
            or self.decision_policy_version != DECISION_POLICY_VERSION
            or self.decision_policy_sha256 != DECISION_POLICY_SHA256
            or re.fullmatch(r"[0-9a-f]{64}", self.catalogue_sha256) is None
            or type(self.outcomes) is not tuple
            or not self.outcomes
            or any(type(value) is not ShadowFieldOutcome for value in self.outcomes)
            or len({value.destination for value in self.outcomes})
            != len(self.outcomes)
            or len({value.proposal_id for value in self.outcomes}) != len(self.outcomes)
            or any(
                value.rule_id
                != "shadow_rule_"
                + _digest(
                    {
                        "category": value.policy_category,
                        "decision_policy_sha256": DECISION_POLICY_SHA256,
                        "decision_policy_version": DECISION_POLICY_VERSION,
                        "destination": value.policy_destination,
                        "option": value.policy_option,
                    }
                )
                or value.qualification_candidate_id
                != "qualification_candidate_"
                + _digest(
                    {
                        "action_id": value.action_id,
                        "candidate_id": value.candidate_id,
                        "catalogue_sha256": self.catalogue_sha256,
                        "citation_ids": list(value.citation_ids),
                        "combination_ids": list(value.combination_ids),
                        "rule_id": value.rule_id,
                    }
                )
                for value in self.outcomes
            )
            or self.outcome_set_id != "shadow_outcome_set_" + _digest(self._identity_data())
        ):
            raise ShadowOutcomeError("shadow outcome set is invalid")

    def to_data(self) -> dict[str, object]:
        return {"outcome_set_id": self.outcome_set_id, **self._identity_data()}

    def to_json(self) -> str:
        return _canonical(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> "ShadowOutcomeSet":
        if type(serialized) is not str or len(serialized.encode("utf-8")) > MAX_SHADOW_OUTCOME_BYTES:
            raise ShadowOutcomeError("shadow outcome document is invalid")
        try:
            raw = json.loads(serialized)
            if type(raw) is not dict or serialized != _canonical(raw):
                raise ValueError
            fields = tuple(
                ShadowFieldOutcome(
                    value["outcome_id"],
                    value["proposal_id"],
                    value["destination"],
                    value["policy_category"],
                    value["policy_option"],
                    value["policy_destination"],
                    value["disposition"],
                    value["action_id"],
                    value["candidate_id"],
                    tuple(value["citation_ids"]),
                    tuple(value["combination_ids"]),
                    value["reason_code"],
                    value["rule_id"],
                    value["qualification_candidate_id"],
                )
                for value in raw["outcomes"]
            )
            result = cls(
                raw["outcome_set_id"], raw["review_case_id"], raw["report_id"],
                raw["proposal_set_id"], raw["target_fingerprint"],
                raw["resolved_policy_id"], raw["policy_profile_id"],
                raw["policy_generation"], raw["source_decision_set_id"],
                raw["decision_policy_version"], raw["decision_policy_sha256"],
                raw["catalogue_sha256"], fields, raw["schema_version"],
                raw["posture"], raw["authority"],
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            RecursionError,
            json.JSONDecodeError,
            ShadowOutcomeError,
        ) as error:
            raise ShadowOutcomeError("shadow outcome document is invalid") from error
        if set(raw) != set(result.to_data()) or result.to_json() != serialized:
            raise ShadowOutcomeError("shadow outcome document is invalid")
        return result


__all__ = [
    "MAX_SHADOW_OUTCOME_BYTES",
    "SHADOW_OUTCOME_SCHEMA",
    "ShadowFieldOutcome",
    "ShadowOutcomeError",
    "ShadowOutcomeSet",
]
