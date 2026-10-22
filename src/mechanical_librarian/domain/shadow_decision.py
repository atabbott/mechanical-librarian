"""Deterministic, provider-independent decisions for one shadow-review packet."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, TypedDict, cast

from mechanical_librarian.domain.field_value_comparison import (
    PRAISE_OPENING,
    PRAISE_QUOTED_SHARE,
    contributor_credit_prefixed,
    promotional_announcement,
)
from mechanical_librarian.domain.proposal_set import (
    _TITLE_PART_BOUNDARY,
    DECORATION_TIDY_TITLE_REASONS,
    EVIDENCE_LED_TITLE_REASON,
    PROVIDER_CONSENSUS_TITLE_REASONS,
)
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.shadow_review_actions import (
    ACTION_POLICY_VERSION,
    PolicyAction,
    PolicyActionCatalogue,
    PolicyActionCompilationError,
    compile_shadow_review_actions,
)

DECISION_SET_SCHEMA = "shadow-decision-set-v1"
DECISION_POLICY_VERSION = "deterministic-shadow-decision-policy-v23"
MAX_DECISION_SET_BYTES = 1_048_576

_SUPPORTED_DESTINATIONS = frozenset(
    {"title", "authors", "narrators", "language", "publisher", "publishedDate"}
)
_REPORT_ONLY_SOURCE_DESTINATIONS = frozenset({"language", "publisher", "publishedDate"})
_WORK_DESTINATIONS = frozenset({"title", "authors"})
_PUBLICATION_DESTINATIONS = frozenset({"publishedDate"})
_COMBINATION_DESTINATIONS = ("title", "subtitle", "series")
_COMBINATION_SELECTED_DESTINATION = "title"
_COMBINATION_WITNESS_DESTINATIONS = ("subtitle", "series")
_REASON_CODES = frozenset(
    {
        "ambiguous_eligible_actions",
        "bibliographic_combination_unresolved",
        "evidence_sufficiency_manual",
        "insufficient_support",
        "isbn_title_material_conflict",
        "no_eligible_action",
        "target_only_preservation",
        "unique_supported_candidate",
        "unique_supported_preservation",
        "unsupported_destination",
    }
)
# Both media's synopsis rules refuse the same praise shape (#1283).
_PRAISE_RULE = {
    "mostly_quoted_praise_refused": True,
    "praise_quote_block": "balanced_double_quoted_span_and_dash_attribution",
    "praise_attribution_ends": ["line_end", "next_quote", "full_stop_after_word"],
    "praise_quoted_non_whitespace_share_minimum": PRAISE_QUOTED_SHARE,
    "praise_dash_attribution": ["em_dash", "en_dash", "spaced_hyphen"],
    "praise_opening": PRAISE_OPENING,
}
_RULE_TABLE = {
    "scalar_incumbent_title": {
        "sole_eligible_candidate_equals_current": True,
        "compiled_keep_has_identical_citations": True,
        "neither_action_has_combinations": True,
        "selection": "recommend_keep_current",
    },
    "title_material_conflict": {
        "reason": "isbn_title_material_conflict",
        "manual_before_candidate_or_combination_selection": True,
    },
    "ebook_synopsis": {
        "execution": "rich_metadata_preview",
        "comparison": "canonical_description_whole_value",
        "placeholders": [
            "(duplicate)",
            "ascii_integer_pages_semicolon_ascii_integer_cm",
        ],
        "empty_target_only": True,
        "minimum_length": None,
        "source_record_tiebreak": ["provider", "candidate_identity"],
        "safety_identity_and_source_conflict_precede_fitness": True,
        "explicit_owner_choice_unchanged": True,
        **_PRAISE_RULE,
    },
    "audiobook_synopsis": {
        "execution": "rich_metadata_preview",
        "comparison": "canonical_description_whole_value",
        "placeholders": [
            "chapter_ascii_digits",
            "track_ascii_digits",
            "no description",
            "no description available",
            "description unavailable",
            "no synopsis",
            "no synopsis available",
            "synopsis unavailable",
            "chapter_track_part_section_disc_cd_side_ascii_digits_optional_of_ascii_digits",
            "n/a",
            "none",
            "unknown",
            "tbd",
            "closed_format_word_optionally_parenthesized",
            "unabridged_prefix_of_at_least_four_letters",
            "markup_without_text",
            "closed_format_word_created_by_markable",
            "fre:ac - free audio converter",
            "credit_line_role_by_names_label_colon_names_or_format_word",
            "credit_line_bare_role_by_optional_colon",
            "credit_line_ripper_role_by_handles_with_digits_and_underscore",
            "credit_line_length_duration",
            "credit_line_publisher_to_segment_end",
            "credit_line_release_date_or_released_date",
        ],
        "credit_line_maximum_characters": 240,
        "credit_line_segments": [1, 6],
        "credit_line_names": [1, 6],
        "credit_line_name_tokens": [1, 5],
        "credit_line_prose_words_refuse": True,
        "optional_final_full_stop": True,
        "preserve_other_safe_plain_incumbents": True,
        "safety_and_identity_precede_comparison": True,
        **_PRAISE_RULE,
    },
    "audiobook_genres": {
        "execution": "rich_metadata_preview",
        "format_only_list_is_empty_equivalent": True,
        "format_words": [
            "unabridged",
            "abridged",
            "audiobook",
            "audiobooks",
            "audio book",
            "audio books",
            "unabridged audiobook",
            "abridged audiobook",
            "audible audiobook",
            "audio cd",
            "mp3 cd",
        ],
        "format_only_candidate_refusal": "no_candidate",
        "ebook_unchanged": True,
    },
    "audiobook_recording": {
        "report_derived_candidate_binding": True,
        "minimum_independent_authorities": 1,
        "local_field_agreement_required": False,
        "retained_recording_admission_required": True,
    },
    "audiobook_narrator_agreement": {
        "complete_plausible_source_bound_set": True,
        "minimum_distinct_products": 2,
        "equivalent_complete_casts": True,
        "recording_identity_granted": False,
        "selected_manifestation_binding": "associated_work_only",
    },
    "audiobook_title_series_strip": {
        "identity": "audiobook_title_replace",
        "label_carried_by": "elected_automatic_subtitle_split",
        "removed_segment": "either_side_of_one_contiguous_kept_run",
        "series_label": [
            "closed_series_grammar_in_any_part",
            "case_series_name_in_any_part",
            "case_series_name_word_run",
        ],
        "series_preserved_by": ["populated_target", "elected_automatic_fill"],
        "otherwise": "ask",
    },
    "audiobook_narrator_cast": {
        "identity": "audiobook_narrators_replace",
        "local_cast": "valid_file_narrator_credits",
        "comparison": "closed_creator_display_or_contained_credit",
        "disjoint_nonempty_casts": "ask",
    },
    "candidate": {
        "compiler_admission_required": True,
        "local_evidence_required_except": ["authors"],
        "proposal_evidence_sufficiency_required_for": ["authors", "title"],
        "same_candidate_provider_correlation_required_for": ["title"],
        "source_evidence_report_only_destinations": sorted(
            _REPORT_ONLY_SOURCE_DESTINATIONS
        ),
        "target_only_preservation": 0,
        "authors": "complete_set_proposal_sufficiency_and_recording_or_local_or_two_independent_providers",
        "unique_action_required": True,
        "credit_prefixed_contributor_refused_for": ["authors", "narrators"],
        "credit_prefixed_contributor": [
            "credit_line_role_by_names",
            "credit_line_label_colon_names",
            "credit_line_bare_role_by_optional_colon",
        ],
        "promotional_title_part_refused": True,
        "promotional_title_part": [
            "winner_of_award_or_prize_optional_year",
            "optionally_qualified_bestseller",
            "now_a_major_motion_picture_film_or_series",
        ],
        "title_parts_split_at": [
            "enclosure",
            "colon",
            "spaced_dash",
            "comma",
            "semicolon",
        ],
    },
    "combinations": {
        "exact_destination_order": list(_COMBINATION_DESTINATIONS),
        "fully_witnessed_combination_requirement": "at_least_one",
        "selected_supported_destination": _COMBINATION_SELECTED_DESTINATION,
        "unsupported_witness_destinations": list(_COMBINATION_WITNESS_DESTINATIONS),
        "unanimous_full_policy_action_required": True,
    },
    "edition_fields": {
        "destinations": ["language", "publisher", "publishedDate"],
        "evidence_scope": "edition",
        "selected_manifestation_binding": "established",
    },
    "keep_current": {
        "evidence_positive_preservation_required": True,
        "evidence_sufficiency": "keep_current",
        "unique_preservation_required": True,
    },
    "publication": {
        "destinations": ["publishedDate"],
        "event_by_media": {
            "ebook": "manifestation_publication",
            "audiobook": "work_first_publication",
        },
        "audiobook_evidence_scope": "work",
        "audiobook_source_bound_work_required": True,
        "published_date_precision": {
            "candidate": "year_month_or_day",
            "independent_correlation": "required",
        },
    },
    "decoration_tidy_title": {
        "candidate_local_evidence": "any",
        "minimum_independent_provider_correlations": 1,
        "proposal_target_only_preservation": 1,
        "reasons": sorted(DECORATION_TIDY_TITLE_REASONS),
    },
    "independent_title": {
        "reason": EVIDENCE_LED_TITLE_REASON,
        "elected_candidate_only": True,
        "minimum_independent_provider_correlations": 1,
        "companion_witness_required": False,
    },
    "provider_consensus_title": {
        "candidate_local_evidence": 0,
        "minimum_independent_provider_correlations": 2,
        "proposal_target_only_preservation": 1,
        "reasons": sorted(PROVIDER_CONSENSUS_TITLE_REASONS),
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


class _CandidateSummary(TypedDict):
    """A packet candidate's decision summary, as validated on packet entry."""

    bibliographic_compatibility_ids: list[str]
    creator_roles: dict[str, object]
    evidence_scope: str
    independent_title: bool
    recording_support: bool
    narrator_agreement: bool
    publication: dict[str, object] | None
    selected_manifestation_binding: str
    support: dict[str, int]


class _ProposalSummary(TypedDict):
    """A packet proposal's decision summary, as validated on packet entry."""

    evidence_sufficiency: str
    target_only_preservation: int


class _DecisionFields(TypedDict):
    """The `ShadowDecision` fields other than its derived identity."""

    action_id: str
    proposal_id: str
    destination: str
    recommendation: str
    candidate_id: str | None
    citation_ids: tuple[str, ...]
    combination_ids: tuple[str, ...]
    reason_code: str


class _DecisionSetFields(TypedDict):
    """The `ShadowDecisionSet` core fields other than its decisions."""

    catalogue_sha256: str
    compiler_policy_version: str
    mode: str
    packet_sha256: str
    policy_sha256: str
    policy_version: str
    review_case_id: str
    schema_version: str


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
        data: dict[str, object] = {
            "action_id": action.action_id,
            "candidate_id": action.candidate_id,
            "citation_ids": list(action.citation_ids),
            "combination_ids": list(action.combination_ids),
            "destination": action.destination,
            "proposal_id": action.proposal_id,
            "reason_code": reason_code,
            "recommendation": action.recommendation,
        }
        # The mapping below restates the dataclass fields key by key, so it is
        # exactly a _DecisionFields; the cast lets mypy check the ** unpack.
        return cls(
            _decision_identity(data),
            **cast(
                "_DecisionFields",
                {
                    "action_id": action.action_id,
                    "proposal_id": action.proposal_id,
                    "destination": action.destination,
                    "recommendation": action.recommendation,
                    "candidate_id": action.candidate_id,
                    "citation_ids": action.citation_ids,
                    "combination_ids": action.combination_ids,
                    "reason_code": reason_code,
                },
            ),
        )

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
                    or re.fullmatch(r"alternative_[0-9a-f]{64}", self.candidate_id)
                    is None
                )
            )
            or (
                self.recommendation != "recommend_candidate"
                and self.candidate_id is not None
            )
            or self.citation_ids != tuple(sorted(set(self.citation_ids)))
            or self.combination_ids != tuple(sorted(set(self.combination_ids)))
            or any(type(value) is not str or not value for value in self.citation_ids)
            or any(
                type(value) is not str or not value for value in self.combination_ids
            )
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
        if (
            type(packet) is not ShadowReviewPacket
            or type(catalogue) is not PolicyActionCatalogue
        ):
            raise ShadowDecisionError("shadow decision source is invalid")
        core: dict[str, object] = {
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
        # Dropping "decisions" from "core" leaves exactly the str-valued
        # _DecisionSetFields; the cast lets mypy check the ** unpack.
        return cls(
            _set_identity(core),
            decisions=decisions,
            **cast(
                "_DecisionSetFields",
                {key: value for key, value in core.items() if key != "decisions"},
            ),
        )

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
            or any(type(value) is not ShadowDecision for value in self.decisions)
            or len({value.proposal_id for value in self.decisions})
            != len(self.decisions)
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
        if (
            type(serialized) is not str
            or len(serialized.encode("utf-8")) > MAX_DECISION_SET_BYTES
        ):
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
        # ShadowReviewPacket.__post_init__ has already proved every proposal's
        # "candidates" is a list of candidate dicts.
        for value in cast("list[dict[str, object]]", proposal["candidates"])
        if value.get("candidate_id") == action.candidate_id
    ]
    return matches[0] if len(matches) == 1 else None


def _candidate_is_eligible(
    packet: ShadowReviewPacket,
    proposal: dict[str, object],
    candidate: dict[str, object],
    action: PolicyAction,
) -> bool:
    # validate_candidate_decision_summary and validate_proposal_decision_summary
    # ran over every packet summary in ShadowReviewPacket.__post_init__, so
    # these shapes (and the int-valued support counts) are already proved.
    summary = cast("_CandidateSummary", candidate["decision_summary"])
    support = summary["support"]
    destination = proposal["destination"]
    known_citations = {value["citation_id"] for value in packet.citations}
    known_combinations = {
        value["combination_id"] for value in packet.bibliographic_combinations
    }
    work_publication = (
        destination == "publishedDate"
        and candidate["date_event"] == "work_first_publication"
    )
    work_destination = destination in _WORK_DESTINATIONS or work_publication
    expected_scope = "work" if work_destination else "edition"
    expected_binding = "associated_work_only" if work_destination else "established"
    source_evidence_report_only = destination in _REPORT_ONLY_SOURCE_DESTINATIONS
    reasons = proposal.get("reasons")
    provider_consensus_title = (
        destination == "title"
        and type(reasons) is list
        and len(reasons) == 1
        and reasons[0] in PROVIDER_CONSENSUS_TITLE_REASONS
    )
    decoration_tidy_title = (
        destination == "title"
        and type(reasons) is list
        and len(reasons) == 1
        and reasons[0] in DECORATION_TIDY_TITLE_REASONS
    )
    wrapped = candidate["value"]
    value = wrapped.get("value") if type(wrapped) is dict else wrapped
    if (
        destination in {"authors", "narrators"}
        and type(value) is list
        and any(contributor_credit_prefixed(member) for member in value)
    ) or (
        destination == "title"
        and type(value) is str
        and any(
            promotional_announcement(part) for part in _TITLE_PART_BOUNDARY.split(value)
        )
    ):
        # A credit line in a contributor or an announcement in a title is
        # never automatic; the owner may still choose it (#1283).
        return False
    recording_support = summary["recording_support"]
    narrator_agreement = destination == "narrators" and summary["narrator_agreement"]
    field_support = recording_support or narrator_agreement or work_publication
    if narrator_agreement:
        expected_binding = "associated_work_only"
    if destination == "narrators" and not field_support:
        return False
    independent_title = summary["independent_title"]
    if reasons == [EVIDENCE_LED_TITLE_REASON] and not independent_title:
        return False
    title_election = provider_consensus_title or decoration_tidy_title
    proposal_target_only_preservation = cast(
        "_ProposalSummary", proposal["decision_summary"]
    )["target_only_preservation"]
    minimum_provider_correlations = (
        2 if provider_consensus_title and not field_support else 1
    )
    if (
        (
            not source_evidence_report_only
            and destination != "authors"
            and not field_support
            and not independent_title
            and proposal_target_only_preservation != (1 if title_election else 0)
        )
        or support["target_only_preservation"] != 0
        or (
            destination == "authors"
            and not recording_support
            and support["local_evidence"] == 0
            and support["independent_provider_correlations"] < 2
        )
        or (
            destination not in {"authors", "publishedDate"}
            and not field_support
            and not decoration_tidy_title
            and not independent_title
            and support["local_evidence"] != (0 if provider_consensus_title else 1)
        )
        or (
            not source_evidence_report_only
            and destination != "authors"
            and support["independent_provider_correlations"]
            < minimum_provider_correlations
        )
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
            or candidate["date_event"]
            != (
                "work_first_publication"
                if work_publication
                else "manifestation_publication"
            )
            or publication["event"] != candidate["date_event"]
            or publication["paired_destination_consistency"] != "consistent"
        ):
            return False
        if (
            candidate["date_precision"] not in {"year", "month", "day"}
            or not field_support
            and support["local_evidence"] != 1
            or support["independent_provider_correlations"]
            < (1 if field_support else 2)
        ):
            return False
    return True


def _eligible_actions(
    packet: ShadowReviewPacket,
    catalogue: PolicyActionCatalogue,
    proposal: dict[str, object],
) -> tuple[PolicyAction, ...]:
    # Packet entry validated the proposal summary and the candidate list.
    if proposal["destination"] == "language" and (
        type(proposal.get("reasons")) is list
        and "field_policy_conflict" in cast("list[object]", proposal["reasons"])
    ):
        return ()
    sufficiency = cast("_ProposalSummary", proposal["decision_summary"])[
        "evidence_sufficiency"
    ]
    actions: list[PolicyAction] = []
    # Declared once because the two mutually exclusive branches below bind
    # "candidate" from a packet candidate list and from a lookup that may miss.
    candidate: dict[str, object] | None
    if sufficiency == "keep_current":
        keep = catalogue.action_for(
            proposal["proposal_id"], "recommend_keep_current", None
        )
        preservation = []
        if keep is not None:
            for candidate in cast("list[dict[str, object]]", proposal["candidates"]):
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
    elif (
        sufficiency == "candidate"
        or proposal["destination"] in _REPORT_ONLY_SOURCE_DESTINATIONS
        or any(
            cast("_CandidateSummary", candidate["decision_summary"])[
                "recording_support"
            ]
            for candidate in cast("list[dict[str, object]]", proposal["candidates"])
        )
    ):
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
    if proposal["destination"] == "title" and len(actions) == 1:
        selected = actions[0]
        candidate = _candidate_for_action(proposal, selected)
        keep = catalogue.action_for(
            proposal["proposal_id"], "recommend_keep_current", None
        )
        if (
            selected.recommendation == "recommend_candidate"
            and candidate is not None
            and candidate["value"]
            == {
                "kind": "text",
                "value": cast("dict[str, object]", proposal["current"])["value"],
            }
            and keep is not None
            and set(keep.citation_ids) == set(selected.citation_ids)
            and not keep.combination_ids
            and not selected.combination_ids
        ):
            actions = [keep]
    return tuple(actions)


def _base_reason(
    proposal: dict[str, object], eligible: tuple[PolicyAction, ...]
) -> str:
    # Packet entry validated both summary shapes and the candidate list.
    summary = cast("_ProposalSummary", proposal["decision_summary"])
    source_evidence_report_only = proposal[
        "destination"
    ] in _REPORT_ONLY_SOURCE_DESTINATIONS or any(
        cast("_CandidateSummary", candidate["decision_summary"])["recording_support"]
        for candidate in cast("list[dict[str, object]]", proposal["candidates"])
    )
    if not source_evidence_report_only and summary["evidence_sufficiency"] == "manual":
        return "evidence_sufficiency_manual"
    if (
        not source_evidence_report_only
        and proposal["destination"] != "authors"
        and summary["target_only_preservation"] != 0
    ):
        return "target_only_preservation"
    if not eligible:
        candidates = cast("list[dict[str, object]]", proposal["candidates"])
        if proposal["destination"] == "authors":
            return (
                "insufficient_support"
                if candidates
                and any(
                    not cast("_CandidateSummary", value["decision_summary"])[
                        "recording_support"
                    ]
                    and cast("_CandidateSummary", value["decision_summary"])["support"][
                        "local_evidence"
                    ]
                    == 0
                    and cast("_CandidateSummary", value["decision_summary"])["support"][
                        "independent_provider_correlations"
                    ]
                    < 2
                    for value in candidates
                )
                else "no_eligible_action"
            )
        if candidates and any(
            cast("_CandidateSummary", value["decision_summary"])["support"][
                "local_evidence"
            ]
            != 1
            or (
                not source_evidence_report_only
                and cast("_CandidateSummary", value["decision_summary"])["support"][
                    "independent_provider_correlations"
                ]
                < 1
            )
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
    # Packet entry proved every proposal carries a str "proposal_id" and a
    # known str "destination", and every combination a list of selection dicts
    # whose "destination" is one of the bibliographic destinations.
    for proposal in packet.proposals:
        proposals_by_destination.setdefault(
            cast("str", proposal["destination"]), []
        ).append(proposal)
    qualifying: list[dict[str, PolicyAction]] = []
    for combination in packet.bibliographic_combinations:
        combination_id = combination["combination_id"]
        selections = cast("list[dict[str, object]]", combination["selections"])
        if (
            tuple(value["destination"] for value in selections)
            != _COMBINATION_DESTINATIONS
        ):
            continue
        all_preserve = all(value["kind"] == "preserve_target" for value in selections)
        selected_title: PolicyAction | None = None
        for selection in cast("list[dict[str, object]]", combination["selections"]):
            destination = cast("str", selection["destination"])
            # A retained title is terminal evidence, not authority to alter its
            # subtitle or series witnesses.  Once the selected title's
            # preservation action binds this source combination, those
            # witnesses need no independently executable action: their source
            # representation has already proved the title remains current.
            if (
                destination != _COMBINATION_SELECTED_DESTINATION
                and selected_title is not None
                and selected_title.recommendation == "recommend_keep_current"
            ):
                continue
            proposals = proposals_by_destination.get(destination, [])
            if len(proposals) != 1:
                break
            proposal = proposals[0]
            available = (
                tuple(
                    action
                    for action in catalogue.actions
                    if action.proposal_id == proposal["proposal_id"]
                    and action.destination == destination
                    and action.recommendation != "manual"
                )
                if destination == _COMBINATION_SELECTED_DESTINATION and all_preserve
                else tuple(
                    action
                    for action in catalogue.actions
                    if action.proposal_id == proposal["proposal_id"]
                    and action.destination == destination
                    and action.recommendation != "manual"
                )
                if destination != _COMBINATION_SELECTED_DESTINATION
                else eligible_by_proposal[cast("str", proposal["proposal_id"])]
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
        next(iter(selection.values())) != selected_title for selection in qualifying[1:]
    ):
        return None
    return qualifying[0], set(_COMBINATION_DESTINATIONS)


def _compatible_audiobook_publisher(
    packet: ShadowReviewPacket,
    proposal: dict[str, object],
    eligible: tuple[PolicyAction, ...],
) -> PolicyAction | None:
    """Resolve only a terminal Plc spelling variation of a recording publisher."""
    if proposal["destination"] != "publisher" or len(eligible) < 2:
        return None
    selected = [_candidate_for_action(proposal, action) for action in eligible]
    if any(candidate is None for candidate in selected):
        return None
    # Packet construction validates the complete nested candidate and citation shapes.
    candidates = cast(list[dict[str, Any]], selected)
    if any(
        not candidate["decision_summary"]["recording_support"]
        or candidate["value"]["kind"] != "text"
        for candidate in candidates
    ):
        return None
    facts = {
        citation["citation_id"]: cast(dict[str, Any], citation["fact"])
        for citation in packet.citations
    }
    if any(
        facts[reference].get("kind") != "publisher"
        for candidate in candidates
        for reference in candidate["evidence_references"]
    ):
        return None
    asin_candidates = [
        candidate
        for item in packet.proposals
        if item["destination"] == "asin"
        for candidate in cast(list[dict[str, Any]], item["candidates"])
    ]
    recordings = [
        {
            asin["value"]["value"]
            for asin in asin_candidates
            if set(asin["correlations"]) & set(candidate["correlations"])
        }
        for candidate in candidates
    ]
    if any(not values for values in recordings) or len(set().union(*recordings)) != 1:
        return None
    names = [candidate["value"]["value"] for candidate in candidates]
    if len({re.sub(r"\s+plc$", "", name.casefold()).strip() for name in names}) != 1:
        return None
    current = cast(dict[str, object], proposal["current"])["value"]
    preferred = [
        action
        for action, candidate in zip(eligible, candidates, strict=True)
        if candidate["value"]["value"] == current
    ]
    return min(
        preferred or list(eligible), key=lambda action: action.candidate_id or ""
    )


def decide_shadow_packet(packet: ShadowReviewPacket) -> ShadowDecisionSet:
    """Select one closed action per proposal without provider execution or writes."""
    if type(packet) is not ShadowReviewPacket:
        raise ShadowDecisionError("shadow decision packet is invalid")
    try:
        catalogue = compile_shadow_review_actions(packet)
    except PolicyActionCompilationError as error:
        raise ShadowDecisionError("shadow action catalogue is invalid") from error
    eligible_by_proposal: dict[str, tuple[PolicyAction, ...]] = {}
    # Packet entry proved every proposal carries a str "proposal_id" and a
    # known str "destination", and every combination a list of selection dicts.
    for proposal in packet.proposals:
        if proposal["destination"] in _SUPPORTED_DESTINATIONS:
            eligible_by_proposal[cast("str", proposal["proposal_id"])] = (
                _eligible_actions(packet, catalogue, proposal)
            )
        else:
            eligible_by_proposal[cast("str", proposal["proposal_id"])] = ()
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
            cast("str", selection["destination"])
            for combination in packet.bibliographic_combinations
            for selection in cast("list[dict[str, object]]", combination["selections"])
        }
    decisions: list[ShadowDecision] = []
    for proposal in packet.proposals:
        proposal_id = cast("str", proposal["proposal_id"])
        destination = proposal["destination"]
        eligible = eligible_by_proposal[proposal_id]
        publisher = _compatible_audiobook_publisher(packet, proposal, eligible)
        if publisher is not None:
            eligible = (publisher,)
        if destination not in _SUPPORTED_DESTINATIONS or (
            destination == "narrators"
            and not any(
                (
                    cast("_CandidateSummary", candidate["decision_summary"])[
                        "recording_support"
                    ]
                    or cast("_CandidateSummary", candidate["decision_summary"])[
                        "narrator_agreement"
                    ]
                )
                for candidate in cast("list[dict[str, object]]", proposal["candidates"])
            )
        ):
            action = _manual_action(catalogue, proposal_id)
            reason = "unsupported_destination"
        elif destination == "title" and "isbn_title_material_conflict" in cast(
            "list[str]", proposal["reasons"]
        ):
            action = _manual_action(catalogue, proposal_id)
            reason = "isbn_title_material_conflict"
        elif (
            destination == "title"
            and proposal["reasons"] == [EVIDENCE_LED_TITLE_REASON]
            and len(eligible) == 1
        ):
            action = eligible[0]
            reason = "unique_supported_candidate"
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
