"""Immutable decision-only facts admitted by an exact qualification envelope."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mechanical_librarian.domain.shadow_outcome import ShadowFieldOutcome

from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.contributor_display_qualification import (
    F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY,
    F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION,
    F38_QUALIFICATION_MANIFEST_SHA256,
)
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_PASSING_AUTOMATIC_OPTIONS,
    F20_QUALIFICATION_CANDIDATE_SHA256,
)


def title_preservation_bundle(source: ShadowFieldOutcome) -> str | None:
    """Bind citation-backed unchanged titles; retain audiobook witness identities."""
    if (
        source.destination != "title"
        or source.policy_category != "title_layout"
        or source.policy_option not in {
            "preserve", "title_and_subtitle",
            "preserve:audiobook_title_preserve",
            "title_and_subtitle:audiobook_title_preserve",
        }
        or source.disposition != "keep_current"
        or source.candidate_id is not None
        or source.combination_ids
        or not source.citation_ids
        or source.reason_code != "unique_supported_preservation"
    ):
        return None
    return "automatic_bundle_" + _digest({
        "kind": ("ordinary_title_preservation_v1"
                 if source.policy_option in {"preserve", "title_and_subtitle"}
                 else "audiobook_title_preservation_v1"),
        "source_outcome_id": source.outcome_id,
        "citation_ids": list(source.citation_ids),
    })


def audiobook_title_candidate_bundle(source: ShadowFieldOutcome) -> str | None:
    """Bind one qualified scalar audiobook title to its exact elected evidence."""
    if (
        source.destination != "title"
        or source.policy_category != "title_layout"
        or source.policy_option not in {
            "preserve:audiobook_title_fill",
            "preserve:audiobook_title_replace",
            "title_and_subtitle:audiobook_title_fill",
            "title_and_subtitle:audiobook_title_replace",
        }
        or source.policy_destination != "automatic"
        or source.disposition != "candidate"
        or source.candidate_id is None
        or source.combination_ids
        or not source.citation_ids
        or source.reason_code != "unique_supported_candidate"
    ):
        return None
    return "automatic_bundle_" + _digest({
        "kind": "audiobook_title_candidate_v1",
        "source_outcome_id": source.outcome_id,
        "candidate_id": source.candidate_id,
        "citation_ids": list(source.citation_ids),
    })


def audiobook_title_subtitle_bundle(members: tuple[ShadowFieldOutcome, ...]) -> str | None:
    """Bind exactly two independently owned split outcomes to one joint envelope."""
    ordered = tuple(sorted(members, key=lambda member: member.destination))
    if len(ordered) != 2 or tuple(member.destination for member in ordered) != ("subtitle", "title"):
        return None
    subtitle, title = ordered
    if (
        audiobook_decision_qualification.F46_TITLE_SUBTITLE_PAIR_IDENTITY
        not in audiobook_decision_qualification.F46_PASSING_PAIRED_OPTIONS
        or subtitle.policy_category != "subtitle"
        or subtitle.policy_option != "corroborated_split:audiobook_subtitle_fill"
        or title.policy_category != "title_layout"
        or title.policy_option != "title_and_subtitle:audiobook_title_replace"
        or any(member.policy_destination != "automatic" or member.disposition != "candidate"
               or member.candidate_id is None or not member.citation_ids for member in ordered)
        or len(title.combination_ids) != 1
        or title.combination_ids != subtitle.combination_ids
    ):
        return None
    return "automatic_bundle_" + _digest({
        "kind": "audiobook_title_subtitle_pair_v1",
        "qualification": audiobook_decision_qualification.F46_TITLE_SUBTITLE_PAIR_IDENTITY,
        "members": [{"outcome_id": member.outcome_id, "candidate_id": member.candidate_id,
                     "citations": list(member.citation_ids), "combinations": list(member.combination_ids)}
                    for member in ordered],
    })


class AutomaticDecisionError(ValueError):
    """Automatic authority is incomplete, stale, or outside its envelope."""


# Retained wire name: SHA-256 of the canonical current authority object only.
F19A_QUALIFICATION_MANIFEST_SHA256 = "d10cd9c66a1f7cf617b6a6a63b7df71c32455f5e172bb88a22895bd69143743e"
F19A_QUALIFIED_POLICY_OPTIONS = frozenset({
    "title_layout:preserve", "title_layout:title_and_subtitle",
    "date_precision:year", "date_precision:full_date",
    "language_rendering:display_name",
    "contributor_names:preserve", "contributor_names:canonical",
    "publisher_preference:publisher",
    "title:replace_target:provider_consensus_noise",
    "title:replace_target:decoration_tidy",
    "title:replace_target:isbn_creator_supported",
})
F38_QUALIFIED_POLICY_OPTIONS = frozenset({
    f"{F38_CONTRIBUTOR_DISPLAY_POLICY_CATEGORY}:"
    f"{F38_CONTRIBUTOR_DISPLAY_POLICY_OPTION}",
})
F46_QUALIFIED_POLICY_OPTIONS = (
    audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
)
QUALIFIED_POLICY_OPTIONS = (
    F19A_QUALIFIED_POLICY_OPTIONS
    | F20_PASSING_AUTOMATIC_OPTIONS
    | F38_QUALIFIED_POLICY_OPTIONS
    | F46_QUALIFIED_POLICY_OPTIONS
)


def automatic_safety_category(policy_category: str, policy_option: str) -> str:
    """Return the suspension lane for one immutable policy identity."""
    if policy_category == "title":
        # All title elections write the same target through the same lane.
        return "title_layout"
    return policy_category


def _current_qualification_manifest_sha256(
    policy_category: str, policy_option: str
) -> str:
    identity = f"{policy_category}:{policy_option}"
    if identity in F19A_QUALIFIED_POLICY_OPTIONS:
        return F19A_QUALIFICATION_MANIFEST_SHA256
    if identity in F38_QUALIFIED_POLICY_OPTIONS:
        return F38_QUALIFICATION_MANIFEST_SHA256
    if identity in F20_PASSING_AUTOMATIC_OPTIONS:
        return F20_QUALIFICATION_CANDIDATE_SHA256
    # An audiobook identity carries a third segment, so it can never collide
    # with an ebook option above. The envelope is read live, because a tranche is activated by editing that
    # module alone.
    if audiobook_decision_qualification.audiobook_identity(
        policy_category, policy_option
    ) is not None:
        try:
            return audiobook_decision_qualification.qualification_manifest_sha256(
                identity
            )
        except audiobook_decision_qualification.AudiobookQualificationError as error:
            raise AutomaticDecisionError("invalid") from error
    raise AutomaticDecisionError("invalid")


def qualification_manifest_sha256(policy_category: str, policy_option: str) -> str:
    """Return the current manifest used to create a new decision."""
    return _current_qualification_manifest_sha256(policy_category, policy_option)


def qualification_manifest_is_current(
    policy_category: str,
    policy_option: str,
    manifest_sha256: object,
) -> bool:
    """Return whether one persisted decision still carries effect authority."""
    try:
        return type(manifest_sha256) is str and manifest_sha256 == (
            _current_qualification_manifest_sha256(policy_category, policy_option)
        )
    except AutomaticDecisionError:
        return False


@dataclass(frozen=True, slots=True)
class AutomaticSafetyState:
    """Closed persisted safety posture; suspension never clears by itself."""

    paused: bool
    global_suspension: str | None
    category_suspensions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if type(self.paused) is not bool or (
            self.global_suspension is not None
            and (type(self.global_suspension) is not str or not self.global_suspension)
        ) or self.category_suspensions != tuple(sorted(set(self.category_suspensions))) or any(
            type(category) is not str or not category or type(reason) is not str or not reason
            for category, reason in self.category_suspensions
        ):
            raise AutomaticDecisionError("invalid")

    def permits(self, category: str) -> bool:
        return not self.paused and self.global_suspension is None and category not in dict(self.category_suspensions)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AutomaticDecision:
    """A server-authoritative candidate/keep-current or Ignore disposition.

    This is deliberately not a HumanDecision: it has no actor and every input
    that supplied authority is retained in its immutable identity.
    """

    decision_id: str
    review_case_id: str
    report_id: str
    proposal_set_id: str
    proposal_id: str
    target_fingerprint: str
    destination: str
    outcome: str
    candidate_id: str | None
    resolved_policy_id: str
    policy_generation: int
    rule_id: str
    policy_category: str
    policy_option: str
    qualification_manifest_sha256: str
    source_outcome_id: str
    bundle_id: str | None
    idempotency_digest: str
    occurred_at: int

    @classmethod
    def create(
        cls, *, review_case_id: str, report_id: str, proposal_set_id: str,
        proposal_id: str, target_fingerprint: str, destination: str,
        outcome: str, candidate_id: str | None, resolved_policy_id: str,
        policy_generation: int, rule_id: str, policy_category: str,
        policy_option: str, qualification_manifest_sha256: str,
        source_outcome_id: str, bundle_id: str | None, occurred_at: int,
    ) -> "AutomaticDecision":
        if qualification_manifest_sha256 != _current_qualification_manifest_sha256(
            policy_category, policy_option
        ):
            raise AutomaticDecisionError("invalid")
        core = {"candidate_id": candidate_id, "destination": destination,
            "outcome": outcome, "policy_category": policy_category,
            "policy_generation": policy_generation, "policy_option": policy_option,
            "proposal_id": proposal_id, "proposal_set_id": proposal_set_id,
            "qualification_manifest_sha256": qualification_manifest_sha256,
            "report_id": report_id, "resolved_policy_id": resolved_policy_id,
            "review_case_id": review_case_id, "rule_id": rule_id,
            "source_outcome_id": source_outcome_id, "bundle_id": bundle_id,
            "target_fingerprint": target_fingerprint}
        return cls("automatic_decision_" + _digest({**core, "occurred_at": occurred_at}),
            **core, idempotency_digest=_digest(core), occurred_at=occurred_at)

    @classmethod
    def from_json(cls, document: str) -> "AutomaticDecision":
        """Rehydrate one exact canonical persisted automatic decision."""
        try:
            if type(document) is not str:
                raise AutomaticDecisionError("invalid")
            data = json.loads(document)
            expected = {
                "bundle_id", "candidate_id", "decision_id", "destination",
                "idempotency_digest", "occurred_at", "origin",
                "outcome", "policy_category", "policy_generation", "policy_option",
                "proposal_id", "proposal_set_id",
                "qualification_manifest_sha256", "report_id",
                "resolved_policy_id", "review_case_id", "rule_id",
                "schema_version", "source_outcome_id", "target_fingerprint",
            }
            if (
                type(data) is not dict
                or set(data) != expected
                or data.get("origin") != "automatic_policy"
                or data.get("schema_version") != "automatic-decision-v1"
                or _canonical(data) != document
            ):
                raise AutomaticDecisionError("invalid")
            values = dict(data)
            values.pop("origin")
            values.pop("schema_version")
            return cls(**values)
        except AutomaticDecisionError:
            raise
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            raise AutomaticDecisionError("invalid") from None

    def __post_init__(self) -> None:
        valid_id = lambda prefix, value: type(value) is str and re.fullmatch(prefix + r"_[0-9a-f]{64}", value) is not None
        if (
            not valid_id("automatic_decision", self.decision_id)
            or not valid_id("review_case", self.review_case_id)
            or not valid_id("report", self.report_id)
            or not valid_id("proposal_set", self.proposal_set_id)
            or not valid_id("review_proposal", self.proposal_id)
            or not valid_id("shadow_outcome", self.source_outcome_id)
            or not valid_id("shadow_rule", self.rule_id)
            or self.outcome not in {"candidate", "keep_current", "ignore"}
            or (self.outcome == "candidate") != (type(self.candidate_id) is str and re.fullmatch(r"alternative_[0-9a-f]{64}", self.candidate_id) is not None)
            or (self.outcome != "candidate" and self.candidate_id is not None)
            or type(self.policy_generation) is not int or self.policy_generation < 1
            or type(self.occurred_at) is not int or self.occurred_at < 0
            or any(type(v) is not str or not v for v in (self.destination, self.policy_category, self.policy_option))
            or any(re.fullmatch(r"[0-9a-f]{64}", v) is None for v in (self.target_fingerprint, self.qualification_manifest_sha256, self.idempotency_digest))
            or re.fullmatch(r"resolved_policy_[0-9a-f]{64}", self.resolved_policy_id) is None
            or not qualification_manifest_is_current(
                self.policy_category,
                self.policy_option,
                self.qualification_manifest_sha256,
            )
            or (self.policy_category in {"title_layout", "date_precision"}
                and (type(self.bundle_id) is not str or re.fullmatch(r"automatic_bundle_[0-9a-f]{64}", self.bundle_id) is None))
            or (self.policy_category not in {"title_layout", "date_precision"} and self.bundle_id is not None
                and not (self.policy_category == "subtitle"
                         and self.policy_option == "corroborated_split:audiobook_subtitle_fill"
                         and type(self.bundle_id) is str
                         and re.fullmatch(r"automatic_bundle_[0-9a-f]{64}", self.bundle_id) is not None))
        ):
            raise AutomaticDecisionError("invalid")
        if self.decision_id != "automatic_decision_" + _digest({**self._core(), "occurred_at": self.occurred_at}) or self.idempotency_digest != _digest(self._core()):
            raise AutomaticDecisionError("invalid")

    def _core(self) -> dict[str, object]:
        return {"bundle_id": self.bundle_id, "candidate_id": self.candidate_id, "destination": self.destination, "outcome": self.outcome, "policy_category": self.policy_category, "policy_generation": self.policy_generation, "policy_option": self.policy_option, "proposal_id": self.proposal_id, "proposal_set_id": self.proposal_set_id, "qualification_manifest_sha256": self.qualification_manifest_sha256, "report_id": self.report_id, "resolved_policy_id": self.resolved_policy_id, "review_case_id": self.review_case_id, "rule_id": self.rule_id, "source_outcome_id": self.source_outcome_id, "target_fingerprint": self.target_fingerprint}

    def to_data(self) -> dict[str, object]:
        return {"decision_id": self.decision_id, **self._core(), "idempotency_digest": self.idempotency_digest, "occurred_at": self.occurred_at, "origin": "automatic_policy", "schema_version": "automatic-decision-v1"}

    def to_json(self) -> str:
        return _canonical(self.to_data())
