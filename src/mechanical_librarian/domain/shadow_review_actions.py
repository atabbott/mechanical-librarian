"""Deterministic policy-admissible actions for qualification shadow review."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from mechanical_librarian.domain.field_outcomes import (
    language_outcomes_compatible,
)
from mechanical_librarian.domain.metadata_report import TargetDestination

if TYPE_CHECKING:
    from mechanical_librarian.domain.shadow_review import ShadowReviewPacket


ACTION_CATALOGUE_SCHEMA = "shadow-review-action-catalogue-v1"
ACTION_POLICY_VERSION = "shadow-review-action-policy-v3"
_ACTION_KINDS = frozenset({"manual", "recommend_candidate", "recommend_keep_current"})
_ASSESSMENTS = frozenset({"fills_empty", "preserves_target", "replaces_target"})
_BIBLIOGRAPHIC_DESTINATIONS = frozenset({"title", "subtitle", "series"})
_DESTINATION_POLICIES = {
    TargetDestination.TITLE.value: "bibliographic",
    TargetDestination.SUBTITLE.value: "bibliographic",
    TargetDestination.AUTHORS.value: "scoped",
    TargetDestination.NARRATORS.value: "scoped",
    TargetDestination.SERIES.value: "bibliographic",
    TargetDestination.GENRES.value: "scoped",
    TargetDestination.PUBLISHED_DATE.value: "publication",
    TargetDestination.PUBLISHER.value: "scoped",
    TargetDestination.DESCRIPTION.value: "scoped",
    TargetDestination.ISBN.value: "scoped",
    TargetDestination.ASIN.value: "scoped",
    TargetDestination.LANGUAGE.value: "language",
    TargetDestination.EXPLICIT.value: "scoped",
    TargetDestination.TAGS.value: "scoped",
    TargetDestination.COVER.value: "cover",
}


class PolicyActionCompilationError(ValueError):
    """The action compiler input is outside its closed packet contract."""


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _identity(prefix: str, value: object) -> str:
    return prefix + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PolicyAction:
    action_id: str
    proposal_id: str
    destination: str
    recommendation: str
    candidate_id: str | None
    citation_ids: tuple[str, ...]
    combination_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            type(self.proposal_id) is not str
            or not self.proposal_id
            or type(self.destination) is not str
            or not self.destination
            or self.recommendation not in _ACTION_KINDS
            or (
                self.recommendation == "recommend_candidate"
                and (type(self.candidate_id) is not str or not self.candidate_id)
            )
            or (
                self.recommendation != "recommend_candidate"
                and self.candidate_id is not None
            )
            or type(self.citation_ids) is not tuple
            or self.citation_ids != tuple(sorted(set(self.citation_ids)))
            or any(type(value) is not str or not value for value in self.citation_ids)
            or type(self.combination_ids) is not tuple
            or self.combination_ids != tuple(sorted(set(self.combination_ids)))
            or any(
                type(value) is not str or not value for value in self.combination_ids
            )
            or self.action_id
            != _action_id(
                self.proposal_id,
                self.recommendation,
                self.candidate_id,
            )
        ):
            raise PolicyActionCompilationError("policy action is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "candidate_id": self.candidate_id,
            "citation_ids": list(self.citation_ids),
            "combination_ids": list(self.combination_ids),
            "destination": self.destination,
            "proposal_id": self.proposal_id,
            "recommendation": self.recommendation,
        }


@dataclass(frozen=True, slots=True)
class PolicyActionExclusion:
    exclusion_id: str
    action_id: str
    proposal_id: str
    recommendation: str
    candidate_id: str | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            self.action_id
            != _action_id(
                self.proposal_id,
                self.recommendation,
                self.candidate_id,
            )
            or type(self.reasons) is not tuple
            or not self.reasons
            or self.reasons != tuple(sorted(set(self.reasons)))
            or any(type(value) is not str or not value for value in self.reasons)
            or self.exclusion_id
            != _identity(
                "action_exclusion_",
                {"action_id": self.action_id, "reasons": list(self.reasons)},
            )
        ):
            raise PolicyActionCompilationError("policy action exclusion is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "candidate_id": self.candidate_id,
            "exclusion_id": self.exclusion_id,
            "proposal_id": self.proposal_id,
            "reasons": list(self.reasons),
            "recommendation": self.recommendation,
        }


@dataclass(frozen=True, slots=True)
class PolicyActionCatalogue:
    actions: tuple[PolicyAction, ...]
    exclusions: tuple[PolicyActionExclusion, ...]
    policy_version: str = ACTION_POLICY_VERSION
    schema_version: str = ACTION_CATALOGUE_SCHEMA

    def __post_init__(self) -> None:
        if (
            type(self.actions) is not tuple
            or any(type(value) is not PolicyAction for value in self.actions)
            or type(self.exclusions) is not tuple
            or any(
                type(value) is not PolicyActionExclusion for value in self.exclusions
            )
        ):
            raise PolicyActionCompilationError("policy action catalogue is invalid")
        action_ids = tuple(value.action_id for value in self.actions)
        exclusion_ids = tuple(value.exclusion_id for value in self.exclusions)
        excluded_action_ids = tuple(value.action_id for value in self.exclusions)
        action_keys = tuple(
            (value.proposal_id, value.recommendation, value.candidate_id)
            for value in self.actions
        )
        manual_proposals = tuple(
            value.proposal_id
            for value in self.actions
            if value.recommendation == "manual"
        )
        if (
            self.policy_version != ACTION_POLICY_VERSION
            or self.schema_version != ACTION_CATALOGUE_SCHEMA
            or action_ids != tuple(sorted(set(action_ids)))
            or len(action_keys) != len(set(action_keys))
            or exclusion_ids != tuple(sorted(set(exclusion_ids)))
            or len(excluded_action_ids) != len(set(excluded_action_ids))
            or set(action_ids) & set(excluded_action_ids)
            or set(manual_proposals) != {value.proposal_id for value in self.actions}
            or len(manual_proposals) != len(set(manual_proposals))
            or not {value.proposal_id for value in self.exclusions}.issubset(
                set(manual_proposals)
            )
        ):
            raise PolicyActionCompilationError("policy action catalogue is invalid")

    def action_for(
        self,
        proposal_id: object,
        recommendation: object,
        candidate_id: object,
    ) -> PolicyAction | None:
        matches = tuple(
            value
            for value in self.actions
            if (
                value.proposal_id == proposal_id
                and value.recommendation == recommendation
                and value.candidate_id == candidate_id
            )
        )
        return matches[0] if len(matches) == 1 else None

    def to_data(self) -> dict[str, object]:
        return {
            "actions": [value.to_data() for value in self.actions],
            "exclusions": [value.to_data() for value in self.exclusions],
            "policy_version": self.policy_version,
            "schema_version": self.schema_version,
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_data())


def _action_id(
    proposal_id: str,
    recommendation: str,
    candidate_id: str | None,
) -> str:
    if recommendation not in _ACTION_KINDS:
        raise PolicyActionCompilationError("policy action kind is invalid")
    return _identity(
        "action_",
        {
            "candidate_id": candidate_id,
            "proposal_id": proposal_id,
            "recommendation": recommendation,
        },
    )


def _action(
    *,
    proposal_id: str,
    destination: str,
    recommendation: str,
    candidate_id: str | None,
    citation_ids: set[str],
    combination_ids: set[str],
) -> PolicyAction:
    return PolicyAction(
        _action_id(proposal_id, recommendation, candidate_id),
        proposal_id,
        destination,
        recommendation,
        candidate_id,
        tuple(sorted(citation_ids)),
        tuple(sorted(combination_ids)),
    )


def _exclude(action: PolicyAction, reasons: set[str]) -> PolicyActionExclusion:
    ordered = tuple(sorted(reasons))
    return PolicyActionExclusion(
        _identity(
            "action_exclusion_",
            {"action_id": action.action_id, "reasons": list(ordered)},
        ),
        action.action_id,
        action.proposal_id,
        action.recommendation,
        action.candidate_id,
        ordered,
    )


def _role_references(summary: dict[str, object]) -> set[str]:
    references: set[str] = set()
    roles = summary.get("creator_roles")
    if type(roles) is not dict:
        return references
    for key in ("included", "excluded"):
        facts = roles.get(key)
        if type(facts) is not list:
            continue
        for fact in facts:
            if type(fact) is dict and type(fact.get("reference")) is str:
                references.add(fact["reference"])
    return references


def _combination_citations(
    packet: ShadowReviewPacket,
    combination_ids: set[str],
    known_citations: set[str],
) -> set[str]:
    # ShadowReviewPacket.__post_init__ has already proved every proposal's
    # "candidates" and every combination's "selections" are lists of dicts.
    candidates = {
        candidate["candidate_id"]: candidate
        for proposal in packet.proposals
        for candidate in cast("list[dict[str, object]]", proposal["candidates"])
    }
    references: set[str] = set()
    for combination in packet.bibliographic_combinations:
        if combination["combination_id"] not in combination_ids:
            continue
        for selection in cast("list[dict[str, object]]", combination["selections"]):
            candidate = candidates.get(selection.get("alternative_id"))
            if candidate is None:
                continue
            raw_references = candidate.get("evidence_references")
            if type(raw_references) is list:
                references.update(
                    value for value in raw_references if type(value) is str
                )
            summary = candidate.get("decision_summary")
            if type(summary) is dict:
                references.update(_role_references(summary))
    return references & known_citations


def _candidate_combination_ids(
    packet: ShadowReviewPacket,
    proposal: dict[str, object],
    candidate: dict[str, object],
    summary: dict[str, object],
    reasons: set[str],
) -> set[str]:
    # ShadowReviewPacket.__post_init__ has already proved every combination
    # carries a str "combination_id" and a list of selection dicts.
    known = {
        cast("str", value["combination_id"]): value
        for value in packet.bibliographic_combinations
    }
    raw = summary.get("bibliographic_compatibility_ids")
    if type(raw) is not list or any(type(value) is not str for value in raw):
        reasons.add("bibliographic_combination_binding_unknown")
        return set()
    if summary.get("independent_title") is True:
        return set()
    combination_ids = set(raw)
    if not combination_ids.issubset(known):
        reasons.add("bibliographic_combination_unknown")
    destination = proposal["destination"]
    if (
        known
        and destination in _BIBLIOGRAPHIC_DESTINATIONS
        and not combination_ids
        and candidate.get("assessment") == "preserves_target"
    ):
        combination_ids = {
            combination_id
            for combination_id, combination in known.items()
            if any(
                selection.get("destination") == destination
                and selection.get("kind") == "preserve_target"
                for selection in cast(
                    "list[dict[str, object]]", combination["selections"]
                )
            )
        }
    if known and destination in _BIBLIOGRAPHIC_DESTINATIONS and not combination_ids:
        reasons.add("bibliographic_combination_binding_missing")
    return combination_ids


def _publication_reasons(
    proposal: dict[str, object],
    candidate: dict[str, object],
    summary: dict[str, object],
) -> tuple[set[str], bool]:
    destination = proposal["destination"]
    if destination != "publishedDate":
        return set(), False
    reasons: set[str] = set()
    publication = summary.get("publication")
    if type(publication) is not dict:
        return {"publication_obligation_missing"}, False
    expected_precision = candidate.get("date_precision")
    if publication.get("destination") != destination:
        reasons.add("publication_destination_incompatible")
    if (
        publication.get("event") != "manifestation_publication"
        or candidate.get("date_event") != "manifestation_publication"
    ):
        reasons.add("publication_event_incompatible")
    if (
        type(expected_precision) is not str
        or publication.get("precision") != expected_precision
    ):
        reasons.add("publication_precision_incompatible")
    if publication.get("paired_destination_consistency") != "consistent":
        reasons.add("publication_pairing_unresolved")
    if (
        publication.get("report_only") is not True
        or publication.get("write_eligibility") != "unsupported"
    ):
        reasons.add("publication_authority_invalid")
    return reasons, not reasons


def _candidate_action(
    packet: ShadowReviewPacket,
    proposal: dict[str, object],
    candidate: dict[str, object],
    known_citations: set[str],
    correlation_bindings: set[tuple[str, str, str]],
) -> tuple[PolicyAction, set[str]]:
    # ShadowReviewPacket.__post_init__ has already proved every proposal
    # carries a str "proposal_id" and a known str "destination".
    proposal_id = cast("str", proposal["proposal_id"])
    destination = cast("str", proposal["destination"])
    candidate_id = candidate.get("candidate_id")
    if type(candidate_id) is not str:
        raise PolicyActionCompilationError("packet candidate identity is invalid")
    reasons: set[str] = set()
    raw_references = candidate.get("evidence_references")
    references = (
        {value for value in raw_references if type(value) is str}
        if type(raw_references) is list
        else set()
    )
    summary = candidate.get("decision_summary")
    if type(summary) is not dict:
        summary = {}
        reasons.add("candidate_decision_summary_missing")
    if destination == "narrators" and summary.get("recording_support") is not True:
        reasons.add("recording_support_absent")
    references.update(_role_references(summary))
    if not references or not references.issubset(known_citations):
        reasons.add("citation_closure_failed")
    assessment = candidate.get("assessment")
    if type(assessment) is not str or assessment not in _ASSESSMENTS:
        reasons.add("assessment_unknown")
    scope = candidate.get("scope")
    summary_scope = summary.get("evidence_scope")
    destination_policy = _DESTINATION_POLICIES.get(destination)
    if scope not in {"cover", "edition", "work"}:
        reasons.add("evidence_scope_unknown")
    elif summary_scope != scope:
        reasons.add("evidence_scope_mismatch")
    elif (destination_policy == "cover") != (scope == "cover"):
        reasons.add("destination_scope_incompatible")
    support = summary.get("support")
    provider_support = (
        support.get("independent_provider_correlations")
        if type(support) is dict
        else None
    )
    local_support = support.get("local_evidence") if type(support) is dict else None
    if (
        type(provider_support) is not int
        or type(local_support) is not int
        or isinstance(provider_support, bool)
        or isinstance(local_support, bool)
        or provider_support + local_support < 1
    ):
        reasons.add("evidence_support_absent")
    raw_correlations = candidate.get("correlations")
    correlation_ids = (
        {value for value in raw_correlations if type(value) is str}
        if type(raw_correlations) is list
        else set()
    )
    if not correlation_ids or any(
        (value, proposal_id, candidate_id) not in correlation_bindings
        for value in correlation_ids
    ):
        reasons.add("correlation_binding_missing")
    publication_reasons, publication_safe = _publication_reasons(
        proposal, candidate, summary
    )
    reasons.update(publication_reasons)
    combination_ids = _candidate_combination_ids(
        packet, proposal, candidate, summary, reasons
    )
    binding = summary.get("selected_manifestation_binding")
    if scope == "work" and binding != "associated_work_only":
        reasons.add("selected_manifestation_binding_missing")
    elif scope == "cover" and binding != "not_applicable":
        reasons.add("selected_manifestation_binding_missing")
    elif (
        scope == "edition"
        and binding != "established"
        and not publication_safe
        and not combination_ids
    ):
        reasons.add("selected_manifestation_binding_missing")
    return (
        _action(
            proposal_id=proposal_id,
            destination=destination,
            recommendation="recommend_candidate",
            candidate_id=candidate_id,
            citation_ids=references,
            combination_ids=combination_ids,
        ),
        reasons,
    )


def _text_value(value: object) -> str | None:
    if type(value) is str:
        return value
    if (
        type(value) is dict
        and value.get("kind") == "text"
        and type(value.get("value")) is str
    ):
        # The guard above proved the "value" member is a str.
        return cast("str", value["value"])
    return None


def _language_compatible(left: str, right: str) -> bool:
    return left.casefold() == right.casefold() or language_outcomes_compatible(
        left, right
    )


def _keep_action(
    packet: ShadowReviewPacket,
    proposal: dict[str, object],
    known_citations: set[str],
    correlation_bindings: set[tuple[str, str, str]],
) -> tuple[PolicyAction, set[str]]:
    # ShadowReviewPacket.__post_init__ has already proved every proposal
    # carries a str "proposal_id", a known str "destination", and a list of
    # candidate dicts.
    proposal_id = cast("str", proposal["proposal_id"])
    destination = cast("str", proposal["destination"])
    current = proposal.get("current")
    reasons: set[str] = set()
    references: set[str] = set()
    current_text = _text_value(current.get("value")) if type(current) is dict else None
    rejected_preservation_reasons: set[str] = set()
    for candidate in cast("list[dict[str, object]]", proposal["candidates"]):
        candidate_text = _text_value(candidate.get("value"))
        preserves = candidate.get("assessment") == "preserves_target"
        compatible_language = (
            destination == "language"
            and current_text is not None
            and candidate_text is not None
            and _language_compatible(current_text, candidate_text)
        )
        if preserves or compatible_language:
            evidence_action, evidence_reasons = _candidate_action(
                packet,
                proposal,
                candidate,
                known_citations,
                correlation_bindings,
            )
            if evidence_reasons:
                rejected_preservation_reasons.update(evidence_reasons)
            else:
                references.update(evidence_action.citation_ids)
    if not references and rejected_preservation_reasons:
        reasons.update(rejected_preservation_reasons)
    if type(current) is not dict or current.get("state") != "value":
        reasons.add("current_value_absent")
    if not references or not references.issubset(known_citations):
        reasons.add("positive_preservation_citation_missing")
    combination_ids: set[str] = set()
    if packet.bibliographic_combinations and destination in _BIBLIOGRAPHIC_DESTINATIONS:
        combination_ids = {
            cast("str", value["combination_id"])
            for value in packet.bibliographic_combinations
            if any(
                selection.get("destination") == destination
                and selection.get("kind") == "preserve_target"
                for selection in cast("list[dict[str, object]]", value["selections"])
            )
        }
        if not combination_ids:
            reasons.add("bibliographic_combination_binding_missing")
        else:
            reasons.discard("current_value_absent")
            reasons.discard("positive_preservation_citation_missing")
            references.update(
                _combination_citations(packet, combination_ids, known_citations)
            )
    return (
        _action(
            proposal_id=proposal_id,
            destination=destination,
            recommendation="recommend_keep_current",
            candidate_id=None,
            citation_ids=references,
            combination_ids=combination_ids,
        ),
        reasons,
    )


def compile_shadow_review_actions(
    packet: ShadowReviewPacket,
) -> PolicyActionCatalogue:
    """Compile a closed action catalogue from packet and policy facts only."""
    from mechanical_librarian.domain.shadow_review import ShadowReviewPacket

    if type(packet) is not ShadowReviewPacket:
        raise PolicyActionCompilationError("action compiler packet is invalid")
    # ShadowReviewPacket.__post_init__ has already proved every citation
    # carries a str "citation_id" and every correlation a str
    # "correlation_id" over lists of proposal and candidate identities.
    known_citations = {cast("str", value["citation_id"]) for value in packet.citations}
    correlation_bindings = {
        (cast("str", correlation["correlation_id"]), proposal_id, candidate_id)
        for correlation in packet.correlations
        for proposal_id in cast("list[str]", correlation["proposal_ids"])
        for candidate_id in cast("list[str]", correlation["candidate_ids"])
    }
    actions: list[PolicyAction] = []
    exclusions: list[PolicyActionExclusion] = []
    for proposal in packet.proposals:
        proposal_id = cast("str", proposal["proposal_id"])
        destination = cast("str", proposal["destination"])
        if destination not in _DESTINATION_POLICIES:
            raise PolicyActionCompilationError("packet destination policy is unknown")
        proposal_reasons = proposal.get("reasons")
        forced_manual_reasons = (
            {"no_safe_bibliographic_combination"}
            if type(proposal_reasons) is list
            and "no_safe_bibliographic_combination" in proposal_reasons
            else set()
        )
        proposal_citations: set[str] = set()
        for candidate in cast("list[dict[str, object]]", proposal["candidates"]):
            action, reasons = _candidate_action(
                packet,
                proposal,
                candidate,
                known_citations,
                correlation_bindings,
            )
            reasons.update(forced_manual_reasons)
            proposal_citations.update(action.citation_ids)
            if reasons:
                exclusions.append(_exclude(action, reasons))
            else:
                actions.append(action)
        keep, keep_reasons = _keep_action(
            packet,
            proposal,
            known_citations,
            correlation_bindings,
        )
        keep_reasons.update(forced_manual_reasons)
        proposal_citations.update(keep.citation_ids)
        if keep_reasons:
            exclusions.append(_exclude(keep, keep_reasons))
        else:
            actions.append(keep)
        actions.append(
            _action(
                proposal_id=proposal_id,
                destination=destination,
                recommendation="manual",
                candidate_id=None,
                citation_ids=proposal_citations,
                combination_ids=set(),
            )
        )
    return PolicyActionCatalogue(
        tuple(sorted(actions, key=lambda value: value.action_id)),
        tuple(sorted(exclusions, key=lambda value: value.exclusion_id)),
    )
