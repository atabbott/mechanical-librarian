"""Deterministic, provider-independent decisions for one shadow-review packet."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.shadow_review_actions import (
    ACTION_POLICY_VERSION,
    PolicyAction,
    PolicyActionCatalogue,
    PolicyActionCompilationError,
    compile_shadow_review_actions,
)


DECISION_SET_SCHEMA = "shadow-decision-set-v1"
DECISION_POLICY_VERSION = "deterministic-shadow-decision-policy-v4"
MAX_DECISION_SET_BYTES = 1_048_576

_SUPPORTED_DESTINATIONS = frozenset(
    {"title", "authors", "language", "publisher", "publishedDate", "publishedYear"}
)
_WORK_DESTINATIONS = frozenset({"title", "authors"})
_PUBLICATION_DESTINATIONS = frozenset({"publishedDate", "publishedYear"})
_COMBINATION_DESTINATIONS = ("title", "subtitle", "series")
_COMBINATION_SELECTED_DESTINATION = "title"
_COMBINATION_WITNESS_DESTINATIONS = ("subtitle", "series")
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
_RULE_TABLE = {
    "candidate": {
        "compiler_admission_required": True,
        "evidence_sufficiency": "candidate",
        "independent_provider_correlations_minimum": 1,
        "local_evidence": 1,
        "target_only_preservation": 0,
        "unique_action_required": True,
    },
    "combinations": {
        "exact_destination_order": list(_COMBINATION_DESTINATIONS),
        "fully_witnessed_combination_requirement": "at_least_one",
        "selected_supported_destination": _COMBINATION_SELECTED_DESTINATION,
        "unsupported_witness_destinations": list(
            _COMBINATION_WITNESS_DESTINATIONS
        ),
        "unanimous_full_policy_action_required": True,
    },
    "edition_fields": {
        "destinations": ["language", "publisher", "publishedDate", "publishedYear"],
        "evidence_scope": "edition",
        "selected_manifestation_binding": "established",
    },
    "keep_current": {
        "evidence_positive_preservation_required": True,
        "evidence_sufficiency": "keep_current",
        "unique_preservation_required": True,
    },
    "publication": {
        "destinations": ["publishedDate", "publishedYear"],
        "event": "manifestation_publication",
        "paired_destination_consistency": "consistent",
        "published_date_precision": {
            "candidate": "day",
            "local_day": "one_or_more_provider_correlations",
            "local_timestamp_normalized_to_day": "two_or_more_provider_correlations",
        },
    },
    "supported_destinations": sorted(_SUPPORTED_DESTINATIONS),
    "unsupported_disposition": "manual",
    "work_fields": {
        "destinations": ["authors", "title"],
        "evidence_scope": "work",
        "selected_manifestation_binding": "associated_work_only",
    },
}


class ShadowDecisionError(ValueError):
    """The packet, catalogue, or decision document violates the closed contract."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


DECISION_POLICY_SHA256 = _sha256(_canonical_json(_RULE_TABLE))


def _decision_identity(data: dict[str, object]) -> str:
    return "shadow_decision_" + _sha256(_canonical_json(data))


def _set_identity(data: dict[str, object]) -> str:
    return "shadow_decision_set_" + _sha256(_canonical_json(data))


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    decision_id: str
    action_id: str
    proposal_id: str
    destination: str
    recommendation: str
    candidate_id: str | None
    citation_ids: tuple[str, ...]
    combination_ids: tuple[str, ...]
    reason_code: str

    @classmethod
    def create(cls, action: PolicyAction, reason_code: str) -> ShadowDecision:
        if type(action) is not PolicyAction or reason_code not in _REASON_CODES:
            raise ShadowDecisionError("shadow decision action is invalid")
        data = {
            "action_id": action.action_id,
            "candidate_id": action.candidate_id,
            "citation_ids": list(action.citation_ids),
            "combination_ids": list(action.combination_ids),
            "destination": action.destination,
            "proposal_id": action.proposal_id,
            "reason_code": reason_code,
            "recommendation": action.recommendation,
        }
        return cls(_decision_identity(data), **{
            "action_id": action.action_id,
            "proposal_id": action.proposal_id,
            "destination": action.destination,
            "recommendation": action.recommendation,
            "candidate_id": action.candidate_id,
            "citation_ids": action.citation_ids,
            "combination_ids": action.combination_ids,
            "reason_code": reason_code,
        })

    def __post_init__(self) -> None:
        data = self._identity_data()
        if (
            re.fullmatch(r"action_[0-9a-f]{64}", self.action_id) is None
            or re.fullmatch(r"review_proposal_[0-9a-f]{64}", self.proposal_id) is None
            or type(self.destination) is not str
            or not self.destination
            or self.recommendation
            not in {"manual", "recommend_candidate", "recommend_keep_current"}
            or (
                self.recommendation == "recommend_candidate"
                and (
                    type(self.candidate_id) is not str
                    or re.fullmatch(r"alternative_[0-9a-f]{64}", self.candidate_id) is None
                )
            )
            or (self.recommendation != "recommend_candidate" and self.candidate_id is not None)
            or self.citation_ids != tuple(sorted(set(self.citation_ids)))
            or self.combination_ids != tuple(sorted(set(self.combination_ids)))
            or any(type(value) is not str or not value for value in self.citation_ids)
            or any(type(value) is not str or not value for value in self.combination_ids)
            or self.reason_code not in _REASON_CODES
            or self.decision_id != _decision_identity(data)
        ):
            raise ShadowDecisionError("shadow decision is invalid")

    def _identity_data(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "candidate_id": self.candidate_id,
            "citation_ids": list(self.citation_ids),
            "combination_ids": list(self.combination_ids),
            "destination": self.destination,
            "proposal_id": self.proposal_id,
            "reason_code": self.reason_code,
            "recommendation": self.recommendation,
        }

    def to_data(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, **self._identity_data()}


@dataclass(frozen=True, slots=True)
class ShadowDecisionSet:
    decision_set_id: str
    review_case_id: str
    packet_sha256: str
    policy_version: str
    policy_sha256: str
    compiler_policy_version: str
    catalogue_sha256: str
    decisions: tuple[ShadowDecision, ...]
    schema_version: str = DECISION_SET_SCHEMA
    mode: str = "qualification_only"

    @classmethod
    def create(
        cls,
        packet: ShadowReviewPacket,
        catalogue: PolicyActionCatalogue,
        decisions: tuple[ShadowDecision, ...],
    ) -> ShadowDecisionSet:
        if type(packet) is not ShadowReviewPacket or type(catalogue) is not PolicyActionCatalogue:
            raise ShadowDecisionError("shadow decision source is invalid")
        core = {
            "catalogue_sha256": _sha256(catalogue.to_json()),
            "compiler_policy_version": catalogue.policy_version,
            "decisions": [value.to_data() for value in decisions],
            "mode": "qualification_only",
            "packet_sha256": _sha256(packet.to_json()),
            "policy_sha256": DECISION_POLICY_SHA256,
            "policy_version": DECISION_POLICY_VERSION,
            "review_case_id": packet.review_case_id,
            "schema_version": DECISION_SET_SCHEMA,
        }
        return cls(_set_identity(core), decisions=decisions, **{
            key: value for key, value in core.items() if key != "decisions"
        })

    def __post_init__(self) -> None:
        core = self._identity_data()
        if (
            self.schema_version != DECISION_SET_SCHEMA
            or self.mode != "qualification_only"
            or re.fullmatch(r"review_case_[0-9a-f]{64}", self.review_case_id) is None
            or re.fullmatch(r"[0-9a-f]{64}", self.packet_sha256) is None
            or self.policy_version != DECISION_POLICY_VERSION
            or self.policy_sha256 != DECISION_POLICY_SHA256
            or self.compiler_policy_version != ACTION_POLICY_VERSION
            or re.fullmatch(r"[0-9a-f]{64}", self.catalogue_sha256) is None
            or type(self.decisions) is not tuple
            or not self.decisions
            or any(type(value) is not ShadowDecision for value in self.decisions)
            or len({value.proposal_id for value in self.decisions}) != len(self.decisions)
            or self.decision_set_id != _set_identity(core)
        ):
            raise ShadowDecisionError("shadow decision set is invalid")

    def _identity_data(self) -> dict[str, object]:
        return {
            "catalogue_sha256": self.catalogue_sha256,
            "compiler_policy_version": self.compiler_policy_version,
            "decisions": [value.to_data() for value in self.decisions],
            "mode": self.mode,
            "packet_sha256": self.packet_sha256,
            "policy_sha256": self.policy_sha256,
            "policy_version": self.policy_version,
            "review_case_id": self.review_case_id,
            "schema_version": self.schema_version,
        }

    def to_data(self) -> dict[str, object]:
        return {"decision_set_id": self.decision_set_id, **self._identity_data()}

    def to_json(self) -> str:
        return _canonical_json(self.to_data())

    @classmethod
    def from_json(
        cls,
        serialized: str,
        packet: ShadowReviewPacket | None = None,
    ) -> ShadowDecisionSet:
        if type(serialized) is not str or len(serialized.encode("utf-8")) > MAX_DECISION_SET_BYTES:
            raise ShadowDecisionError("shadow decision document is invalid")
        try:
            data = json.loads(serialized)
        except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
            raise ShadowDecisionError("shadow decision document is invalid") from error
        if type(data) is not dict or serialized != _canonical_json(data):
            raise ShadowDecisionError("shadow decision document is invalid")
        required = {
            "catalogue_sha256",
            "compiler_policy_version",
            "decision_set_id",
            "decisions",
            "mode",
            "packet_sha256",
            "policy_sha256",
            "policy_version",
            "review_case_id",
            "schema_version",
        }
        if set(data) != required or type(data["decisions"]) is not list:
            raise ShadowDecisionError("shadow decision document is invalid")
        decisions: list[ShadowDecision] = []
        decision_fields = {
            "action_id",
            "candidate_id",
            "citation_ids",
            "combination_ids",
            "decision_id",
            "destination",
            "proposal_id",
            "reason_code",
            "recommendation",
        }
        try:
            for value in data["decisions"]:
                if (
                    type(value) is not dict
                    or set(value) != decision_fields
                    or type(value["citation_ids"]) is not list
                    or type(value["combination_ids"]) is not list
                ):
                    raise ShadowDecisionError("shadow decision document is invalid")
                decisions.append(
                    ShadowDecision(
                        value["decision_id"],
                        value["action_id"],
                        value["proposal_id"],
                        value["destination"],
                        value["recommendation"],
                        value["candidate_id"],
                        tuple(value["citation_ids"]),
                        tuple(value["combination_ids"]),
                        value["reason_code"],
                    )
                )
            result = cls(
                data["decision_set_id"],
                data["review_case_id"],
                data["packet_sha256"],
                data["policy_version"],
                data["policy_sha256"],
                data["compiler_policy_version"],
                data["catalogue_sha256"],
                tuple(decisions),
                data["schema_version"],
                data["mode"],
            )
        except (KeyError, TypeError, ShadowDecisionError) as error:
            raise ShadowDecisionError("shadow decision document is invalid") from error
        if packet is not None and result != decide_shadow_packet(packet):
            raise ShadowDecisionError("shadow decision document disagrees with packet")
        return result


def _manual_action(catalogue: PolicyActionCatalogue, proposal_id: str) -> PolicyAction:
    action = catalogue.action_for(proposal_id, "manual", None)
    if action is None:
        raise ShadowDecisionError("manual action is unavailable")
    return action


def _candidate_for_action(
    proposal: dict[str, object], action: PolicyAction
) -> dict[str, object] | None:
    matches = [
        value
        for value in proposal["candidates"]
        if value.get("candidate_id") == action.candidate_id
    ]
    return matches[0] if len(matches) == 1 else None


def _candidate_is_eligible(
    packet: ShadowReviewPacket,
    proposal: dict[str, object],
    candidate: dict[str, object],
    action: PolicyAction,
) -> bool:
    summary = candidate["decision_summary"]
    support = summary["support"]
    destination = proposal["destination"]
    known_citations = {value["citation_id"] for value in packet.citations}
    known_combinations = {
        value["combination_id"] for value in packet.bibliographic_combinations
    }
    expected_scope = "work" if destination in _WORK_DESTINATIONS else "edition"
    expected_binding = (
        "associated_work_only" if destination in _WORK_DESTINATIONS else "established"
    )
    if (
        proposal["decision_summary"]["target_only_preservation"] != 0
        or support["target_only_preservation"] != 0
        or support["local_evidence"] != 1
        or support["independent_provider_correlations"] < 1
        or candidate["scope"] != expected_scope
        or summary["evidence_scope"] != expected_scope
        or summary["selected_manifestation_binding"] != expected_binding
        or not action.citation_ids
        or not set(action.citation_ids).issubset(known_citations)
        or not set(action.combination_ids).issubset(known_combinations)
    ):
        return False
    if destination in _PUBLICATION_DESTINATIONS:
        publication = summary["publication"]
        if (
            type(publication) is not dict
            or candidate["date_event"] != "manifestation_publication"
            or publication["event"] != "manifestation_publication"
            or publication["paired_destination_consistency"] != "consistent"
        ):
            return False
        if destination == "publishedDate":
            local_precisions = [
                citation["fact"].get("date_precision")
                for citation in packet.citations
                if citation["citation_id"] in action.citation_ids
                and type(citation["fact"]) is dict
                and citation["fact"].get("date_event")
                == "manifestation_publication"
                and "provider" not in citation["fact"]
            ]
            if (
                candidate["date_precision"] != "day"
                or len(local_precisions) != 1
                or local_precisions[0] not in {"day", "timestamp"}
                or (
                    local_precisions[0] == "timestamp"
                    and support["independent_provider_correlations"] < 2
                )
            ):
                return False
    return True


def _eligible_actions(
    packet: ShadowReviewPacket,
    catalogue: PolicyActionCatalogue,
    proposal: dict[str, object],
) -> tuple[PolicyAction, ...]:
    sufficiency = proposal["decision_summary"]["evidence_sufficiency"]
    actions: list[PolicyAction] = []
    if sufficiency == "candidate":
        for action in catalogue.actions:
            if (
                action.proposal_id != proposal["proposal_id"]
                or action.recommendation != "recommend_candidate"
            ):
                continue
            candidate = _candidate_for_action(proposal, action)
            if candidate is not None and _candidate_is_eligible(
                packet, proposal, candidate, action
            ):
                actions.append(action)
    elif sufficiency == "keep_current":
        keep = catalogue.action_for(
            proposal["proposal_id"], "recommend_keep_current", None
        )
        preservation = []
        if keep is not None:
            for candidate in proposal["candidates"]:
                candidate_action = catalogue.action_for(
                    proposal["proposal_id"],
                    "recommend_candidate",
                    candidate["candidate_id"],
                )
                if (
                    candidate["assessment"] == "preserves_target"
                    and candidate_action is not None
                    and _candidate_is_eligible(
                        packet, proposal, candidate, candidate_action
                    )
                ):
                    preservation.append(candidate)
        if keep is not None and len(preservation) == 1:
            actions.append(keep)
    return tuple(actions)


def _base_reason(proposal: dict[str, object], eligible: tuple[PolicyAction, ...]) -> str:
    summary = proposal["decision_summary"]
    if summary["evidence_sufficiency"] == "manual":
        return "evidence_sufficiency_manual"
    if summary["target_only_preservation"] != 0:
        return "target_only_preservation"
    if not eligible:
        candidates = proposal["candidates"]
        if candidates and any(
            value["decision_summary"]["support"]["local_evidence"] != 1
            or value["decision_summary"]["support"][
                "independent_provider_correlations"
            ]
            < 1
            for value in candidates
        ):
            return "insufficient_support"
        return "no_eligible_action"
    if len(eligible) > 1:
        return "ambiguous_eligible_actions"
    return (
        "unique_supported_preservation"
        if eligible[0].recommendation == "recommend_keep_current"
        else "unique_supported_candidate"
    )


def _combination_selections(
    packet: ShadowReviewPacket,
    catalogue: PolicyActionCatalogue,
    eligible_by_proposal: dict[str, tuple[PolicyAction, ...]],
) -> tuple[dict[str, PolicyAction], set[str]] | None:
    if not packet.bibliographic_combinations:
        return {}, set()
    proposals_by_destination: dict[str, list[dict[str, object]]] = {}
    for proposal in packet.proposals:
        proposals_by_destination.setdefault(proposal["destination"], []).append(
            proposal
        )
    qualifying: list[dict[str, PolicyAction]] = []
    for combination in packet.bibliographic_combinations:
        combination_id = combination["combination_id"]
        selections = combination["selections"]
        if (
            tuple(value["destination"] for value in selections)
            != _COMBINATION_DESTINATIONS
        ):
            continue
        selected_title: PolicyAction | None = None
        for selection in combination["selections"]:
            destination = selection["destination"]
            proposals = proposals_by_destination.get(destination, [])
            if len(proposals) != 1:
                break
            proposal = proposals[0]
            available = (
                eligible_by_proposal[proposal["proposal_id"]]
                if destination == _COMBINATION_SELECTED_DESTINATION
                else tuple(
                    action
                    for action in catalogue.actions
                    if action.proposal_id == proposal["proposal_id"]
                    and action.destination == destination
                    and action.recommendation != "manual"
                )
            )
            matches = [
                action
                for action in available
                if combination_id in action.combination_ids
                and (
                    (
                        selection["kind"] == "candidate"
                        and action.recommendation == "recommend_candidate"
                        and action.candidate_id == selection["alternative_id"]
                    )
                    or (
                        selection["kind"] == "preserve_target"
                        and action.recommendation == "recommend_keep_current"
                    )
                )
            ]
            if len(matches) != 1:
                break
            if destination == _COMBINATION_SELECTED_DESTINATION:
                selected_title = matches[0]
        else:
            if selected_title is not None:
                qualifying.append({selected_title.proposal_id: selected_title})
    if not qualifying:
        return None
    selected_title = next(iter(qualifying[0].values()))
    if any(
        next(iter(selection.values())) != selected_title
        for selection in qualifying[1:]
    ):
        return None
    return qualifying[0], set(_COMBINATION_DESTINATIONS)


def decide_shadow_packet(packet: ShadowReviewPacket) -> ShadowDecisionSet:
    """Select one closed action per proposal without provider execution or writes."""
    if type(packet) is not ShadowReviewPacket:
        raise ShadowDecisionError("shadow decision packet is invalid")
    try:
        catalogue = compile_shadow_review_actions(packet)
    except PolicyActionCompilationError as error:
        raise ShadowDecisionError("shadow action catalogue is invalid") from error
    eligible_by_proposal: dict[str, tuple[PolicyAction, ...]] = {}
    for proposal in packet.proposals:
        if proposal["destination"] in _SUPPORTED_DESTINATIONS:
            eligible_by_proposal[proposal["proposal_id"]] = _eligible_actions(
                packet, catalogue, proposal
            )
        else:
            eligible_by_proposal[proposal["proposal_id"]] = ()
    combination_result = _combination_selections(
        packet, catalogue, eligible_by_proposal
    )
    combination_actions: dict[str, PolicyAction] = {}
    constrained_destinations: set[str] = set()
    combination_failed = combination_result is None
    if combination_result is not None:
        combination_actions, constrained_destinations = combination_result
    elif packet.bibliographic_combinations:
        constrained_destinations = {
            selection["destination"]
            for combination in packet.bibliographic_combinations
            for selection in combination["selections"]
        }
    decisions: list[ShadowDecision] = []
    for proposal in packet.proposals:
        proposal_id = proposal["proposal_id"]
        destination = proposal["destination"]
        eligible = eligible_by_proposal[proposal_id]
        if destination not in _SUPPORTED_DESTINATIONS:
            action = _manual_action(catalogue, proposal_id)
            reason = "unsupported_destination"
        elif destination in constrained_destinations:
            action = combination_actions.get(proposal_id) or _manual_action(
                catalogue, proposal_id
            )
            reason = (
                "bibliographic_combination_unresolved"
                if combination_failed or proposal_id not in combination_actions
                else _base_reason(proposal, (action,))
            )
        elif len(eligible) == 1:
            action = eligible[0]
            reason = _base_reason(proposal, eligible)
        else:
            action = _manual_action(catalogue, proposal_id)
            reason = _base_reason(proposal, eligible)
        decisions.append(ShadowDecision.create(action, reason))
    if tuple(value.proposal_id for value in decisions) != tuple(
        value["proposal_id"] for value in packet.proposals
    ):
        raise ShadowDecisionError("shadow decision coverage is invalid")
    return ShadowDecisionSet.create(packet, catalogue, tuple(decisions))


__all__ = [
    "DECISION_POLICY_SHA256",
    "DECISION_POLICY_VERSION",
    "DECISION_SET_SCHEMA",
    "MAX_DECISION_SET_BYTES",
    "ShadowDecision",
    "ShadowDecisionError",
    "ShadowDecisionSet",
    "decide_shadow_packet",
]
