"""Closed deterministic decision summaries for shadow-review candidates."""

from __future__ import annotations

import hashlib
import re

from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.metadata_report import (
    MAX_CONTRIBUTIONS,
    ClaimKind,
    ClaimScope,
    FieldState,
    IdentifierRelation,
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
)

MAX_DECISION_SUPPORT = 256
# A candidate can aggregate the closed maximum of 256 independent provider
# correlations plus the selected-EPUB/local correlation.  Each represented
# source contributes at most the metadata contract's creator cardinality.
MAX_DECISION_CORRELATIONS = MAX_DECISION_SUPPORT + 1
MAX_CREATOR_ROLE_FACTS = MAX_DECISION_CORRELATIONS * MAX_CONTRIBUTIONS
_COMBINATION_ID = re.compile(r"^bibliographic_combination_[0-9a-f]{64}$")


class DecisionSummaryValueError(ValueError):
    """A decision summary is outside the closed product contract."""


def validate_support_counts(value: object) -> None:
    if (
        type(value) is not dict
        or set(value)
        != {
            "independent_provider_correlations",
            "local_evidence",
            "target_only_preservation",
        }
        or any(
            type(item) is not int
            or not 0 <= item <= MAX_DECISION_SUPPORT
            for item in value.values()
        )
    ):
        raise DecisionSummaryValueError("decision-summary support is invalid")


def _validate_creator_roles(
    value: object,
    *,
    destination: object,
) -> set[str]:
    if (
        type(value) is not dict
        or set(value) != {"excluded", "included"}
        or type(value["included"]) is not list
        or type(value["excluded"]) is not list
        or len(value["included"]) > MAX_CREATOR_ROLE_FACTS
        or len(value["excluded"]) > MAX_CREATOR_ROLE_FACTS
    ):
        raise DecisionSummaryValueError("decision-summary roles are invalid")
    references: set[str] = set()
    for key in ("included", "excluded"):
        facts = value[key]
        if (
            facts
            != sorted(
                facts,
                key=lambda fact: (
                    fact.get("reference", "")
                    if type(fact) is dict
                    else "",
                    fact.get("role", "") if type(fact) is dict else "",
                    fact.get("scope", "") if type(fact) is dict else "",
                ),
            )
            or any(
                facts[index - 1] == facts[index]
                for index in range(1, len(facts))
            )
        ):
            raise DecisionSummaryValueError(
                "decision-summary roles are invalid"
            )
        for fact in facts:
            if (
                type(fact) is not dict
                or set(fact) != {"reference", "role", "scope"}
                or type(fact["reference"]) is not str
                or type(fact["role"]) is not str
                or not 1 <= len(fact["role"]) <= 128
                or any(ord(character) < 0x20 for character in fact["role"])
                or fact["scope"] not in {"edition", "work"}
            ):
                raise DecisionSummaryValueError(
                    "decision-summary roles are invalid"
                )
            references.add(fact["reference"])
    if destination != TargetDestination.AUTHORS.value and (
        value["included"] or value["excluded"]
    ):
        raise DecisionSummaryValueError("decision-summary roles are invalid")
    return references


def validate_candidate_decision_summary(
    summary: object,
    *,
    candidate: dict[str, object],
    destination: object,
) -> tuple[set[str], list[str]]:
    if (
        type(summary) is not dict
        or set(summary)
        != {
            "bibliographic_compatibility_ids",
            "creator_roles",
            "evidence_scope",
            "publication",
            "selected_manifestation_binding",
            "support",
        }
        or summary["evidence_scope"] != candidate.get("scope")
        or summary["evidence_scope"] not in {"cover", "edition", "work"}
        or summary["selected_manifestation_binding"]
        not in {
            "associated_work_only",
            "established",
            "not_applicable",
            "unresolved",
        }
        or type(summary["bibliographic_compatibility_ids"]) is not list
        or summary["bibliographic_compatibility_ids"]
        != sorted(set(summary["bibliographic_compatibility_ids"]))
        or any(
            type(value) is not str or _COMBINATION_ID.fullmatch(value) is None
            for value in summary["bibliographic_compatibility_ids"]
        )
    ):
        raise DecisionSummaryValueError("candidate decision summary is invalid")
    validate_support_counts(summary["support"])
    support = summary["support"]
    if support["target_only_preservation"] != 0:
        raise DecisionSummaryValueError("candidate decision summary is invalid")
    role_references = _validate_creator_roles(
        summary["creator_roles"],
        destination=destination,
    )
    publication = summary["publication"]
    publication_destinations = {
        TargetDestination.PUBLISHED_DATE.value,
        TargetDestination.PUBLISHED_YEAR.value,
    }
    if destination not in publication_destinations:
        if publication is not None:
            raise DecisionSummaryValueError(
                "candidate decision summary is invalid"
            )
    elif (
        type(publication) is not dict
        or set(publication)
        != {
            "destination",
            "event",
            "logical_field",
            "paired_destination_consistency",
            "precision",
            "report_only",
            "write_eligibility",
        }
        or publication["logical_field"] != "publication"
        or publication["destination"] != destination
        or publication["event"] != candidate.get("date_event")
        or publication["precision"] != candidate.get("date_precision")
        or publication["paired_destination_consistency"]
        not in {"conflicting", "consistent", "unresolved"}
        or publication["report_only"] is not True
        or publication["write_eligibility"] != "unsupported"
    ):
        raise DecisionSummaryValueError("candidate decision summary is invalid")
    return role_references, summary["bibliographic_compatibility_ids"]


def validate_proposal_decision_summary(value: object) -> None:
    if (
        type(value) is not dict
        or set(value)
        != {"evidence_sufficiency", "target_only_preservation"}
        or value["evidence_sufficiency"]
        not in {"candidate", "keep_current", "manual"}
        or type(value["target_only_preservation"]) is not int
        or value["target_only_preservation"] not in {0, 1}
    ):
        raise DecisionSummaryValueError("proposal decision summary is invalid")


def correlation_identity(value: str) -> str:
    return "correlation_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _known_correlations(report: MetadataReport) -> tuple[str, set[str]]:
    provider_correlations = {
        correlation_identity(
            "provider:" + (provider.authority or "unknown")
        )
        for provider in report.providers
    }
    provider_correlations.update(
        correlation_identity("cover:" + source.provenance.provider)
        for source in report.cover_sources
        if source.provenance is not None
    )
    return correlation_identity("local:selected_epub"), provider_correlations


def compatibility_by_candidate(
    proposal_set: ProposalSet,
) -> dict[str, list[str]]:
    result: dict[str, set[str]] = {}
    for combination in proposal_set.bibliographic_combinations:
        data = combination.to_data()
        for selection in data["selections"]:
            if selection["kind"] == "candidate":
                result.setdefault(selection["alternative_id"], set()).add(
                    data["combination_id"]
                )
    return {
        candidate_id: sorted(combination_ids)
        for candidate_id, combination_ids in result.items()
    }


def _provider_candidate_references(candidate: object) -> set[str]:
    return {
        reference
        for value in (
            *getattr(candidate, "claims", ()),
            *getattr(candidate, "evidence", ()),
        )
        if type(reference := getattr(value, "reference", None)) is str
    }


def _has_manifestation_anchor(
    report: MetadataReport,
    provider_name: str,
    candidate: object,
) -> bool:
    accepted_references = {
        evidence.reference
        for evidence in report.identifier_evidence
        if evidence.relation is IdentifierRelation.ACCEPTED
        and evidence.provider == provider_name
        and evidence.candidate_identity == getattr(candidate, "identity", None)
        and evidence.work_identity == getattr(candidate, "work_identity", None)
    }
    return any(
        claim.kind is ClaimKind.ISBN
        and claim.scope is ClaimScope.EDITION
        and claim.state is FieldState.VALID
        and claim.value is not None
        and claim.applicability == "ebook"
        and claim.reference in accepted_references
        for claim in getattr(candidate, "claims", ())
    )


def _manifestation_binding(
    report: MetadataReport,
    alternative: dict[str, object],
) -> str:
    scope = alternative["scope"]
    if scope == "work":
        return "associated_work_only"
    if scope == "cover":
        return "not_applicable"
    references = set(alternative["evidence_references"])
    correlations = set(alternative["correlations"])
    local_correlation, _ = _known_correlations(report)
    if local_correlation in correlations and any(
        claim.reference in references
        and claim.basis != "target_metadata"
        and claim.scope is ClaimScope.EDITION
        for claim in report.local_claims
    ):
        return "established"
    for provider in report.providers:
        provider_correlation = correlation_identity(
            "provider:" + (provider.authority or "unknown")
        )
        if provider_correlation not in correlations:
            continue
        for candidate in provider.candidates:
            if (
                references.intersection(
                    _provider_candidate_references(candidate)
                )
                and _has_manifestation_anchor(
                    report,
                    provider.provider,
                    candidate,
                )
            ):
                return "established"
    return "unresolved"


def _creator_role_fact(claim: object) -> dict[str, object] | None:
    reference = getattr(claim, "reference", None)
    role = getattr(claim, "role", None)
    scope = getattr(claim, "scope", None)
    if (
        type(reference) is not str
        or type(role) is not str
        or type(scope) is not ClaimScope
    ):
        return None
    return {
        "reference": reference,
        "role": role,
        "scope": scope.value,
    }


def _creator_roles(
    report: MetadataReport,
    destination: str,
    alternative: dict[str, object],
) -> tuple[dict[str, object], set[str]]:
    empty = {"excluded": [], "included": []}
    if destination != TargetDestination.AUTHORS.value:
        return empty, set()
    references = set(alternative["evidence_references"])
    correlations = set(alternative["correlations"])
    included: dict[str, dict[str, object]] = {}
    excluded: dict[str, dict[str, object]] = {}
    local_correlation, _ = _known_correlations(report)
    if local_correlation in correlations:
        represented_bases = {
            claim.basis
            for claim in report.local_claims
            if claim.reference in references
            and claim.kind is ClaimKind.CREATOR
            and claim.scope is ClaimScope.WORK
            and claim.role == "author"
        }
        for claim in report.local_claims:
            if claim.basis not in represented_bases:
                continue
            fact = _creator_role_fact(claim)
            if fact is None:
                continue
            if claim.reference in references:
                included[claim.reference] = fact
            elif (
                claim.state is FieldState.VALID
                and claim.kind in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}
                and claim.role != "author"
            ):
                excluded[claim.reference] = fact
    for provider in report.providers:
        provider_correlation = correlation_identity(
            "provider:" + (provider.authority or "unknown")
        )
        if provider_correlation not in correlations:
            continue
        for candidate in provider.candidates:
            represented = {
                claim.reference for claim in candidate.claims
            }.intersection(references)
            if not represented:
                continue
            for claim in candidate.claims:
                fact = _creator_role_fact(claim)
                if fact is None:
                    continue
                if claim.reference in represented:
                    included[claim.reference] = fact
                elif (
                    claim.state is FieldState.VALID
                    and claim.kind
                    in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}
                    and claim.role != "author"
                ):
                    excluded[claim.reference] = fact
    included_facts = sorted(
        included.values(),
        key=lambda fact: (
            fact["reference"],
            fact["role"],
            fact["scope"],
        ),
    )
    excluded_facts = sorted(
        excluded.values(),
        key=lambda fact: (
            fact["reference"],
            fact["role"],
            fact["scope"],
        ),
    )
    if (
        len(included_facts) > MAX_CREATOR_ROLE_FACTS
        or len(excluded_facts) > MAX_CREATOR_ROLE_FACTS
    ):
        raise DecisionSummaryValueError(
            "decision-summary creator roles exceed their limit"
        )
    role_references = set(included).union(excluded)
    return {
        "excluded": excluded_facts,
        "included": included_facts,
    }, role_references


def _paired_publication_consistency(
    destination: str,
    alternative: dict[str, object],
    results: dict[str, dict[str, object]],
) -> str:
    paired_destination = (
        TargetDestination.PUBLISHED_YEAR.value
        if destination == TargetDestination.PUBLISHED_DATE.value
        else TargetDestination.PUBLISHED_DATE.value
    )
    paired = results.get(paired_destination)
    if paired is None:
        return "unresolved"
    candidate_value = alternative["value"]
    if (
        type(candidate_value) is not dict
        or type(candidate_value.get("value")) is not str
    ):
        return "unresolved"
    candidate_year = candidate_value["value"][:4]
    paired_years: set[str] = set()
    for paired_alternative in paired["alternatives"]:
        if (
            paired_alternative["date_event"]
            != DateEvent.MANIFESTATION_PUBLICATION.value
            or paired_alternative["date_precision"]
            not in {
                DatePrecision.DAY.value,
                DatePrecision.MONTH.value,
                DatePrecision.YEAR.value,
            }
            or type(paired_alternative["value"]) is not dict
            or type(paired_alternative["value"].get("value")) is not str
        ):
            continue
        paired_years.add(paired_alternative["value"]["value"][:4])
    if not paired_years:
        return "unresolved"
    return "consistent" if paired_years == {candidate_year} else "conflicting"


def candidate_decision_summary(
    report: MetadataReport,
    destination: str,
    alternative: dict[str, object],
    results: dict[str, dict[str, object]],
    compatibility_ids: dict[str, list[str]],
) -> tuple[dict[str, object], set[str]]:
    """Project one candidate's bounded deterministic decision facts."""
    local_correlation, provider_correlations = _known_correlations(report)
    correlations = set(alternative["correlations"])
    creator_roles, role_references = _creator_roles(
        report,
        destination,
        alternative,
    )
    publication = None
    if destination in {
        TargetDestination.PUBLISHED_DATE.value,
        TargetDestination.PUBLISHED_YEAR.value,
    }:
        publication = {
            "destination": destination,
            "event": alternative["date_event"],
            "logical_field": "publication",
            "paired_destination_consistency": (
                _paired_publication_consistency(
                    destination,
                    alternative,
                    results,
                )
            ),
            "precision": alternative["date_precision"],
            "report_only": True,
            "write_eligibility": "unsupported",
        }
    return {
        "bibliographic_compatibility_ids": compatibility_ids.get(
            alternative["alternative_id"], []
        ),
        "creator_roles": creator_roles,
        "evidence_scope": alternative["scope"],
        "publication": publication,
        "selected_manifestation_binding": (
            _manifestation_binding(report, alternative)
        ),
        "support": {
            "independent_provider_correlations": len(
                correlations.intersection(provider_correlations)
            ),
            "local_evidence": int(local_correlation in correlations),
            "target_only_preservation": 0,
        },
    }, role_references


def proposal_decision_summary(
    result: dict[str, object],
    *,
    manual_required: bool,
    current: dict[str, object],
) -> dict[str, object]:
    """Project closed proposal-level sufficiency without making a decision."""
    outcome = result["outcome"]
    has_evidence_backed_preservation = any(
        alternative["assessment"] == "preserves_target"
        for alternative in result["alternatives"]
    )
    if manual_required:
        sufficiency = "manual"
    elif outcome == ProposalOutcome.KEEP.value:
        sufficiency = "keep_current"
    elif outcome in {
        ProposalOutcome.ACCEPTABLE_CHOICE.value,
        ProposalOutcome.PROPOSE_CHANGE.value,
    }:
        sufficiency = "candidate"
    elif (
        outcome == ProposalOutcome.UNRESOLVED_CHOICE.value
        and result["destination"] == TargetDestination.TITLE.value
        and has_evidence_backed_preservation
    ):
        # The closed shadow rules may resolve a title conflict only when the
        # current value itself has evidence.  The target-only gate remains in
        # force, and zero or multiple fully supported bundle actions abstain.
        sufficiency = "candidate"
    else:
        sufficiency = "manual"
    target_only = int(
        current.get("state") == "value"
        and not has_evidence_backed_preservation
    )
    return {
        "evidence_sufficiency": sufficiency,
        "target_only_preservation": target_only,
    }


__all__ = [
    "DecisionSummaryValueError",
    "MAX_CREATOR_ROLE_FACTS",
    "MAX_DECISION_CORRELATIONS",
    "candidate_decision_summary",
    "compatibility_by_candidate",
    "proposal_decision_summary",
    "validate_candidate_decision_summary",
    "validate_proposal_decision_summary",
]
