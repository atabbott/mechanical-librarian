"""Closed source-validated packet for deterministic shadow decisions."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Self, TypedDict, cast, overload

from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_SCALAR_CHARACTERS,
    ClaimKind,
    ClaimScope,
    Comparison,
    ComparisonLink,
    ComparisonMethod,
    DateLinkConfidence,
    FieldState,
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
    canonical_target_fields,
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

PACKET_SCHEMA = "shadow-review-packet-v8"
MAX_PACKET_BYTES = 1_048_576
# The generated schema is ASCII-only. Embedding it as a JSON string can add at
# most one escape byte per schema byte; the remaining allowance covers the
# fixed request keys, identifiers, and JSON punctuation.
MAX_CITATIONS = 256
MAX_LINKAGE_IDENTIFIER_FACTS = 16
MAX_LINKAGE_FORMAT_FACTS = 16
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CASE_ID = re.compile(r"^review_case_[0-9a-f]{64}$")
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


class ShadowReviewValueError(ValueError):
    """A deterministic packet is outside its closed domain contract."""


class _WrappedProposal(TypedDict):
    """One `ReviewCase.proposals` entry, exactly as that property builds it."""

    destination: str
    manual_required: bool
    proposal_id: str
    result: dict[str, object]


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
    return normalized == value and precision.value == candidate.get("date_precision")


def _validate_bibliographic_combinations(
    combinations: tuple[dict[str, object], ...],
    proposals: tuple[dict[str, object], ...],
) -> None:
    by_destination = {proposal["destination"]: proposal for proposal in proposals}
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
            or _COMBINATION_ID.fullmatch(combination["combination_id"]) is None
            or type(combination["selections"]) is not list
            or type(combination["source_representations"]) is not list
            or any(
                type(value) is not str or _REPRESENTATION_ID.fullmatch(value) is None
                for value in combination["source_representations"]
            )
            # The short-circuiting any(...) guard above proved the list holds
            # only str, so this comparison orders strings against strings.
            or combination["source_representations"]
            != sorted(set(combination["source_representations"]))
            or not combination["source_representations"]
        ):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        selections = combination["selections"]
        if [
            value.get("destination") if type(value) is dict else None
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
                # The caller (ShadowReviewPacket.__post_init__) already
                # rejected any proposal whose "candidates" is not a list of
                # dicts before reaching this validation.
                for value in cast("list[dict[str, object]]", proposal["candidates"])
            }
            if selection["kind"] == "candidate":
                if (
                    type(selection["alternative_id"]) is not str
                    or selection["alternative_id"] not in candidate_ids
                    or selection["target_field_fingerprint"] is not None
                ):
                    raise ShadowReviewValueError("shadow-review packet is invalid")
            elif selection["kind"] == "preserve_target":
                fingerprint = hashlib.sha256(
                    canonical_json(proposal["current"]).encode("utf-8")
                ).hexdigest()
                if (
                    selection["alternative_id"] is not None
                    or selection["target_field_fingerprint"] != fingerprint
                ):
                    raise ShadowReviewValueError("shadow-review packet is invalid")
            else:
                raise ShadowReviewValueError("shadow-review packet is invalid")
        identity_data = {
            "relationship_schema_version": (BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA),
            "selections": selections,
            "source_representations": combination["source_representations"],
        }
        expected_id = (
            "bibliographic_combination_"
            + hashlib.sha256(canonical_json(identity_data).encode("utf-8")).hexdigest()
        )
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
    bibliographic_relationship_schema_version: str = BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA
    bibliographic_combinations: tuple[dict[str, object], ...] = ()

    def __post_init__(self) -> None:
        if (
            self.schema_version != PACKET_SCHEMA
            or _CASE_ID.fullmatch(self.review_case_id) is None
            or type(self.proposals) is not tuple
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
                or destination not in {value.value for value in TargetDestination}
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
            try:
                validate_proposal_decision_summary(proposal["decision_summary"])
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
                {
                    ("manifestation_publication", "year"),
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
                if candidate["decision_summary"]["independent_title"] and (
                    proposal["reasons"] != ["isbn_creator_supported_title"]
                    or proposal["outcome"] != "propose_change"
                ):
                    raise ShadowReviewValueError("shadow-review packet is invalid")
                candidate_compatibility[candidate["candidate_id"]] = compatibility_ids
                proposal_evidence_references.update(candidate["evidence_references"])
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
                sum(c["decision_summary"]["independent_title"] for c in candidates)
                != int(proposal["reasons"] == ["isbn_creator_supported_title"])
                or any(
                    type(value) is not str or _ALTERNATIVE_ID.fullmatch(value) is None
                    for value in candidate_ids
                )
                # The short-circuiting any(...) above has already proved every
                # collected identity is a str by the time this runs.
                or candidate_ids != sorted(set(cast("list[str]", candidate_ids)))
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
            # _validate_bibliographic_combinations above has already proved
            # every combination carries a str "combination_id" and a list of
            # selection dicts whose "alternative_id" is a str whenever their
            # "kind" is "candidate".
            for selection in cast("list[dict[str, object]]", combination["selections"]):
                alternative_id = selection["alternative_id"]
                if selection["kind"] == "candidate":
                    expected_compatibility.setdefault(
                        cast("str", alternative_id), set()
                    ).add(cast("str", combination["combination_id"]))
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
            # The guard above already rejected any citation whose
            # "citation_id" is not a str or whose "fact" is not a dict.
            citation_id = cast("str", citation["citation_id"])
            fact = cast("dict[str, object]", citation["fact"])
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
                or type(correlation["correlation_id"]) is not str
                or type(correlation["candidate_ids"]) is not list
                or type(correlation["proposal_ids"]) is not list
                or any(type(value) is not str for value in correlation["candidate_ids"])
                or any(type(value) is not str for value in correlation["proposal_ids"])
                # The short-circuiting any(...) guards above proved both
                # identity lists hold only str, so these comparisons order
                # strings against strings.
                or correlation["candidate_ids"]
                != sorted(set(correlation["candidate_ids"]))
                or correlation["proposal_ids"]
                != sorted(set(correlation["proposal_ids"]))
            ):
                raise ShadowReviewValueError("shadow-review packet is invalid")
            correlation_ids.append(correlation["correlation_id"])
        if correlation_ids != sorted(set(correlation_ids)):
            raise ShadowReviewValueError("shadow-review packet is invalid")
        if len(self.to_json().encode("utf-8")) > MAX_PACKET_BYTES:
            raise ShadowReviewValueError("shadow-review packet exceeds its byte limit")

    @classmethod
    def create(
        cls,
        case: ReviewCase,
        proposal_set: ProposalSet,
        report: MetadataReport,
        *,
        include_policy_ignored: bool = False,
    ) -> Self:
        if (
            type(case) is not ReviewCase
            or type(proposal_set) is not ProposalSet
            or type(report) is not MetadataReport
            or type(include_policy_ignored) is not bool
            or (case.proposal_set_id, case.report_id)
            != (proposal_set.proposal_set_id, report.report_id)
            or case.proposal_document != proposal_set.to_json()
            or case.target_fingerprint != report.target.fingerprint
            or case.relationships != proposal_set.to_data()["relationships"]
        ):
            raise ShadowReviewValueError("shadow-review packet binding is invalid")
        current = {
            destination.value: value.to_data()
            for destination, value in canonical_target_fields(
                report.target.fields
            ).items()
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
            # ProposalResult.to_data always projects a str "destination".
            cast("str", wrapped["result"]["destination"]): wrapped["result"]
            for wrapped in cast("tuple[_WrappedProposal, ...]", case.proposals)
        }
        for wrapped in cast("tuple[_WrappedProposal, ...]", case.proposals):
            result = wrapped["result"]
            manual_required = wrapped["manual_required"]
            preview_ignored = (
                include_policy_ignored
                and result["outcome"] == ProposalOutcome.INELIGIBLE.value
                and result["reasons"] == ["policy_ignored"]
            )
            if not preview_ignored and (
                result["outcome"]
                not in {
                    ProposalOutcome.ACCEPTABLE_CHOICE.value,
                    ProposalOutcome.KEEP.value,
                    ProposalOutcome.PROPOSE_CHANGE.value,
                    ProposalOutcome.UNRESOLVED_CHOICE.value,
                }
                and result["destination"] not in constrained_destinations
                and not manual_required
            ):
                continue
            candidates: list[dict[str, object]] = []
            # ProposalAlternative.to_data always projects str identities and
            # lists of str references/correlations, and ProposalResult.to_data
            # always projects "alternatives" as a list of those dicts.
            for alternative in cast("list[dict[str, object]]", result["alternatives"]):
                references.update(cast("list[str]", alternative["evidence_references"]))
                for correlation_id in cast("list[str]", alternative["correlations"]):
                    grouped = correlations.setdefault(
                        correlation_id,
                        {"candidate_ids": set(), "proposal_ids": set()},
                    )
                    grouped["candidate_ids"].add(
                        cast("str", alternative["alternative_id"])
                    )
                    grouped["proposal_ids"].add(wrapped["proposal_id"])
                decision_summary, role_references = candidate_decision_summary(
                    report,
                    cast("str", result["destination"]),
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
                        candidates,
                        key=lambda value: cast("str", value["candidate_id"]),
                    ),
                    "current": current[cast("str", result["destination"])],
                    "decision_summary": proposal_decision_summary(
                        result,
                        manual_required=manual_required,
                        current=current[cast("str", result["destination"])],
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
        citations: tuple[dict[str, object], ...] = tuple(
            {"citation_id": reference, "fact": evidence[reference]}
            for reference in sorted(references)
        )
        correlation_facts: tuple[dict[str, object], ...] = tuple(
            {
                "candidate_ids": sorted(values["candidate_ids"]),
                "correlation_id": correlation_id,
                "proposal_ids": sorted(values["proposal_ids"]),
            }
            for correlation_id, values in sorted(correlations.items())
        )
        value = cls(
            case.review_case_id,
            tuple(
                sorted(
                    proposals,
                    key=lambda value: cast("str", value["proposal_id"]),
                )
            ),
            citations,
            correlation_facts,
            bibliographic_combinations=tuple(
                value.to_data() for value in proposal_set.bibliographic_combinations
            ),
        )
        return value

    def to_data(self) -> dict[str, object]:
        return {
            "bibliographic_combinations": [
                _copy_json(value) for value in self.bibliographic_combinations
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
        cls,
        serialized: str,
        source_report: MetadataReport | None = None,
        source_proposal: ProposalSet | None = None,
        policy_destinations: dict[str, str] | None = None,
    ) -> Self:
        return cls._from_json(
            serialized,
            source_report,
            source_proposal,
            policy_destinations,
            source_validated=False,
        )

    @classmethod
    def from_source_validated_json(cls, serialized: str) -> Self:
        """Decode same-process bytes already validated with their source report."""
        return cls._from_json(serialized, None, None, None, source_validated=True)

    @classmethod
    def _from_json(
        cls,
        serialized: str,
        source_report: MetadataReport | None,
        source_proposal: ProposalSet | None,
        policy_destinations: dict[str, str] | None,
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
            value.validate_source(source_report, source_proposal, policy_destinations)
        return value

    def validate_source(
        self,
        source_report: MetadataReport | None,
        source_proposal: ProposalSet | None = None,
        policy_destinations: dict[str, str] | None = None,
    ) -> None:
        """Require the exact packet projection of one authoritative report."""
        if type(source_report) is not MetadataReport:
            raise ShadowReviewValueError(
                "shadow-review authoritative source is unavailable"
            )
        proposal_set = (
            evaluate_report(source_report)
            if source_proposal is None
            else source_proposal
        )
        if source_proposal is not None and (
            source_proposal.report_id != source_report.report_id
            or source_proposal.to_json()
            != evaluate_report(
                source_report,
                source_proposal.resolved_policy_id,
                policy_destinations,
            ).to_json()
        ):
            raise ShadowReviewValueError(
                "shadow-review authoritative source is unavailable"
            )
        case = ReviewCase.create(proposal_set, source_report)
        expected = type(self).create(case, proposal_set, source_report)
        if self != expected:
            raise ShadowReviewValueError(
                "shadow-review source binding is invalid: authoritative projection"
            )


def _date_link_local_references(report: MetadataReport) -> set[str]:
    """Close reviewer citations over every retained local date-link operand."""
    references: set[str] = set()
    for provider in report.providers:
        for candidate in provider.candidates:
            for value in (*candidate.claims, *candidate.evidence):
                if (
                    type(value) is ProviderClaim
                    and value.kind is ClaimKind.PUBLICATION_DATE
                ) or (
                    type(value) is ProviderEvidence
                    and value.kind in _PROVIDER_DATE_EVIDENCE
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
                if (
                    type(provider) is not dict
                    or type(provider.get("candidates")) is not list
                ):
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
                        rich_id = (
                            "evidence_"
                            + hashlib.sha256(
                                canonical_json(rich_identity).encode("utf-8")
                            ).hexdigest()
                        )
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
    format_facts = (
        sorted(
            (
                _copy_json(fact)
                for fact in evidence
                if type(fact) is dict and fact.get("kind") == "physical_format"
            ),
            key=canonical_json,
        )
        if type(evidence) is list
        else []
    )
    if len(format_facts) > MAX_LINKAGE_FORMAT_FACTS:
        raise ShadowReviewValueError(
            "shadow-review provider format linkage exceeds its limit"
        )
    return {
        "format_facts": format_facts,
        "identifier_facts": identifier_facts,
        "identifier_references": [fact["reference"] for fact in identifier_facts],
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
            raise ShadowReviewValueError("shadow-review provider citation is invalid")
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
            raise ShadowReviewValueError("shadow-review provider citation is invalid")
        return
    evidence_data = value["evidence"]
    if (
        type(evidence_data) is not dict
        or set(evidence_data)
        != {
            "chapters",
            "date_confidence",
            "date_event",
            "date_normalized",
            "date_precision",
            "kind",
            "links",
            "role",
            "scope",
            "source_ordinal",
            "taxonomy_type",
            "value",
        }
        # The shadow reviewer reads ebook proposals. An audiobook's chapter
        # structure is not citable evidence for one, and a packet that carries
        # it is not a packet this reviewer produced.
        or evidence_data["chapters"] is not None
    ):
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
            evidence_data["taxonomy_type"],
        )
    except (ReportValueError, TypeError, ValueError) as error:
        raise ShadowReviewValueError(
            "shadow-review provider citation is invalid"
        ) from error
    expected_id = (
        "evidence_"
        + hashlib.sha256(
            canonical_json(
                {
                    "candidate": candidate,
                    "evidence": evidence.to_data(),
                    "provider": provider,
                }
            ).encode("utf-8")
        ).hexdigest()
    )
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
                        else PublicationDateRelation(item["publication_date_relation"])
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
            raise ShadowReviewValueError("shadow-review provider linkage is invalid")
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
            kind is ClaimKind.ASIN and _ASIN.fullmatch(fact["value"]) is None
        ) or reconstructed.reference != fact["reference"]:
            raise ShadowReviewValueError("shadow-review provider linkage is invalid")
        expected_references.append(fact["reference"])
    if (
        identifiers != sorted(identifiers, key=lambda fact: fact["reference"])
        or references != expected_references
        or references != sorted(set(references))
    ):
        raise ShadowReviewValueError("shadow-review provider linkage is invalid")
    for fact in formats:
        if (
            type(fact) is not dict
            or set(fact)
            != {
                "chapters",
                "date_confidence",
                "date_event",
                "date_normalized",
                "date_precision",
                "kind",
                "links",
                "role",
                "scope",
                "source_ordinal",
                "taxonomy_type",
                "value",
            }
            or fact["chapters"] is not None
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
                    "taxonomy_type",
                )
            )
            or type(fact["value"]) is not str
            or not _bounded_scalar(fact["value"])
        ):
            raise ShadowReviewValueError("shadow-review provider linkage is invalid")
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
            raise ShadowReviewValueError("shadow-review provider linkage is invalid")


def _bounded_scalar(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_SCALAR_CHARACTERS
        and value.strip() == value
        and not any(ord(character) < 0x20 for character in value)
    )


@overload
def _copy_json(value: dict[str, object]) -> dict[str, object]: ...


@overload
def _copy_json(value: object) -> object: ...


def _copy_json(value: object) -> object:
    if type(value) is dict:
        return {key: _copy_json(item) for key, item in value.items()}
    if type(value) is list:
        return [_copy_json(item) for item in value]
    return value


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
    "MAX_PACKET_BYTES",
    "PACKET_SCHEMA",
    "ShadowReviewPacket",
    "ShadowReviewValueError",
    "canonical_json",
]
