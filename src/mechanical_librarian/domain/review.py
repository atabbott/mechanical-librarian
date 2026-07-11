"""Snapshot-aware review surfaces with no cross-target decision authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .evidence import Disposition, EvidenceReference
from .evidence_bounds import (
    MAX_EVIDENCE_REFERENCES,
    MAX_NOTICES,
    MAX_SHORT_TEXT_LENGTH,
    ensure_unique,
    validate_text as validate_bounded_text,
    validate_tuple,
)
from .identifiers import (
    AdapterId,
    ConnectionId,
    DecisionAuthorizationId,
    DecisionId,
    ExternalItemId,
    Fingerprint,
    LibraryId,
    PolicyVersion,
    ProposalId,
    ReviewCaseId,
    SchemaVersion,
    SnapshotId,
    TargetId,
)
from .lifecycle import (
    AuthorizationEvent,
    AuthorizationResult,
    AuthorizationState,
    HumanDecisionApplicability,
    ReviewCaseState,
)


class NoticeClass(StrEnum):
    BLOCKING = "blocking"
    NEEDS_ATTENTION = "needs_attention"
    RESOLVED = "resolved"
    REJECTED_ALTERNATIVE = "rejected_alternative"


class HumanDecisionAction(StrEnum):
    ACCEPT_SELECTED = "accept_selected"
    KEEP_CURRENT = "keep_current"
    REJECT_PROPOSALS = "reject_proposals"


@dataclass(frozen=True)
class ReviewNotice:
    classification: NoticeClass
    affected_proposal: ProposalId
    diagnostic_codes: tuple[str, ...]
    required_action: str

    def __post_init__(self) -> None:
        if (
            type(self.classification) is not NoticeClass
            or type(self.affected_proposal) is not ProposalId
        ):
            raise ValueError("review notices require exact classification and proposal types")
        _validate_code_tuple(self.diagnostic_codes, "review notice diagnostics")
        validate_bounded_text(
            self.required_action, "review notice action", MAX_SHORT_TEXT_LENGTH
        )


@dataclass(frozen=True)
class ReviewCase:
    id: ReviewCaseId
    target: TargetId
    snapshot: SnapshotId
    fingerprint: Fingerprint
    proposal_set_version: str
    proposals: tuple[ProposalId, ...]
    state: ReviewCaseState
    disposition: Disposition
    notices: tuple[ReviewNotice, ...] = ()
    destructive: bool = False
    evidence: tuple[EvidenceReference, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.id) is not ReviewCaseId
            or type(self.target) is not TargetId
            or type(self.snapshot) is not SnapshotId
            or type(self.fingerprint) is not Fingerprint
            or type(self.state) is not ReviewCaseState
            or type(self.disposition) is not Disposition
            or type(self.destructive) is not bool
        ):
            raise ValueError("review cases require exact authority-bearing domain types")
        if (
            type(self.target.adapter) is not AdapterId
            or type(self.target.connection) is not ConnectionId
            or type(self.target.library) is not LibraryId
            or type(self.target.external_item) is not ExternalItemId
            or type(self.fingerprint.schema) is not SchemaVersion
        ):
            raise ValueError("review cases require fully typed target and fingerprint components")
        _validate_text(self.proposal_set_version, "proposal-set version", 64)
        if (
            type(self.proposals) is not tuple
            or not self.proposals
            or len(self.proposals) > MAX_EVIDENCE_REFERENCES
            or any(type(item) is not ProposalId for item in self.proposals)
        ):
            raise ValueError("review case proposals must be a non-empty immutable typed tuple")
        if type(self.notices) is not tuple or any(
            type(item) is not ReviewNotice for item in self.notices
        ):
            raise ValueError("review case notices must be an immutable typed tuple")
        if len(self.notices) > MAX_NOTICES or len(self.notices) != len(set(self.notices)):
            raise ValueError("review case notices must be bounded and unique")
        validate_tuple(
            self.evidence,
            label="review case evidence",
            item_type=EvidenceReference,
            maximum=MAX_EVIDENCE_REFERENCES,
        )
        if any(reference.consuming_target != self.target for reference in self.evidence):
            raise ValueError("review case evidence must consume the case target")
        ensure_unique(
            (reference.evidence for reference in self.evidence),
            "review case evidence IDs",
        )
        if len(self.proposals) != len(set(self.proposals)):
            raise ValueError("review cases cannot repeat proposals")
        if any(notice.affected_proposal not in self.proposals for notice in self.notices):
            raise ValueError("review notices must affect a proposal in their review case")
        if self.destructive:
            raise ValueError("destructive work belongs to a separate review flow")
        if self.disposition is Disposition.AUTO_APPLY and any(
            notice.classification in {NoticeClass.BLOCKING, NoticeClass.NEEDS_ATTENTION}
            for notice in self.notices
        ):
            raise ValueError("automatic cases cannot retain unresolved review notices")
        if self.state is ReviewCaseState.HUMAN_RESOLVED and any(
            notice.classification in {NoticeClass.BLOCKING, NoticeClass.NEEDS_ATTENTION}
            for notice in self.notices
        ):
            raise ValueError("human-resolved cases cannot retain unresolved review notices")


@dataclass(frozen=True)
class ReviewGroup:
    """A display projection; it intentionally carries no decision state."""

    label: str
    link_method: str
    confidence: str
    sibling_case_ids: tuple[ReviewCaseId, ...]

    def __post_init__(self) -> None:
        if not self.label or not self.link_method or not self.sibling_case_ids:
            raise ValueError("review groups require identity labels and siblings")
        if len(self.sibling_case_ids) != len(set(self.sibling_case_ids)):
            raise ValueError("review groups cannot repeat siblings")


@dataclass(frozen=True)
class HumanDecision:
    id: DecisionId
    case: ReviewCase
    action: HumanDecisionAction
    selected_proposals: tuple[ProposalId, ...]
    actor: str

    def __post_init__(self) -> None:
        if (
            type(self.id) is not DecisionId
            or type(self.case) is not ReviewCase
            or type(self.action) is not HumanDecisionAction
        ):
            raise ValueError("human decisions require exact authority-bearing domain types")
        if type(self.selected_proposals) is not tuple or any(
            type(item) is not ProposalId for item in self.selected_proposals
        ):
            raise ValueError("selected proposals must be an immutable typed tuple")
        if (
            self.case.disposition is not Disposition.NEEDS_REVIEW
            or self.case.state is not ReviewCaseState.HUMAN_RESOLVED
        ):
            raise ValueError("human decisions require a resolved needs-review case")
        _validate_text(self.actor, "human decision actor", 128)
        if self.action is HumanDecisionAction.ACCEPT_SELECTED:
            if not self.selected_proposals:
                raise ValueError("accepting a decision requires selected proposals")
            if not set(self.selected_proposals).issubset(self.case.proposals):
                raise ValueError("human decisions may select only proposals from their review case")
        elif self.selected_proposals:
            raise ValueError("only accepting a decision may select proposals")
        if len(self.selected_proposals) != len(set(self.selected_proposals)):
            raise ValueError("human decisions cannot repeat selected proposals")

    def is_current_for(self, case: ReviewCase) -> bool:
        return self.applicability_for(case) is HumanDecisionApplicability.CURRENT

    def applicability_for(self, case: ReviewCase) -> HumanDecisionApplicability:
        if type(case) is not ReviewCase:
            raise ValueError("decision applicability requires an exact review case")
        if self.case.id != case.id:
            return HumanDecisionApplicability.SUPERSEDED
        if self.case != case:
            return HumanDecisionApplicability.STALE
        if (
            case.state is not ReviewCaseState.HUMAN_RESOLVED
            or case.disposition is not Disposition.NEEDS_REVIEW
        ):
            return HumanDecisionApplicability.REJECTED
        if self.action is HumanDecisionAction.ACCEPT_SELECTED and not set(
            self.selected_proposals
        ).issubset(case.proposals):
            return HumanDecisionApplicability.STALE
        return HumanDecisionApplicability.CURRENT


@dataclass(frozen=True)
class AutomaticPolicyQualification:
    case: ReviewCase
    policy_version: PolicyVersion
    rule_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.case) is not ReviewCase or type(self.policy_version) is not PolicyVersion:
            raise ValueError("automatic qualification requires exact case and policy types")
        _validate_code_tuple(self.rule_codes, "automatic policy rule codes")
        if tuple(sorted(self.rule_codes)) != self.rule_codes:
            raise ValueError("automatic policy rule codes must be deterministically sorted")
        if (
            self.case.disposition is not Disposition.AUTO_APPLY
            or self.case.state is not ReviewCaseState.AUTO_RESOLVED
        ):
            raise ValueError("automatic qualification requires an auto-resolved case")
        if self.case.destructive:
            raise ValueError("destructive work cannot be automatically qualified")


@dataclass(frozen=True)
class DecisionAuthorization:
    id: DecisionAuthorizationId
    case: ReviewCase
    human_decision: HumanDecision | None = None
    automatic_qualification: AutomaticPolicyQualification | None = None

    def __post_init__(self) -> None:
        if type(self.id) is not DecisionAuthorizationId or type(self.case) is not ReviewCase:
            raise ValueError("decision authorizations require exact authority-bearing types")
        if self.human_decision is not None and type(self.human_decision) is not HumanDecision:
            raise ValueError("decision authorization human evidence has an invalid type")
        if (
            self.automatic_qualification is not None
            and type(self.automatic_qualification) is not AutomaticPolicyQualification
        ):
            raise ValueError("decision authorization policy evidence has an invalid type")
        result = self.applicability_for(self.case, self.automatic_qualification)
        if result.state is not AuthorizationState.AUTHORIZED:
            raise ValueError(result.reason)

    @property
    def selected_proposals(self) -> tuple[ProposalId, ...]:
        return () if self.human_decision is None else self.human_decision.selected_proposals

    def applicability_for(
        self,
        current_case: ReviewCase,
        current_automatic_qualification: AutomaticPolicyQualification | None = None,
    ) -> AuthorizationResult:
        if type(current_case) is not ReviewCase:
            raise ValueError("authorization applicability requires an exact review case")
        if current_case.id != self.case.id:
            return AuthorizationResult.superseded("review case was replaced")
        if current_case != self.case:
            return AuthorizationResult.stale("review case evidence changed")
        if self.case.destructive:
            return AuthorizationResult.rejected("destructive work has no automatic authorization path")
        if self.case.disposition is Disposition.AUTO_APPLY:
            if self.human_decision is not None:
                return AuthorizationResult.rejected("automatic authorization cannot carry a human decision")
            if self.automatic_qualification is None:
                return AuthorizationResult.rejected("automatic authorization requires policy qualification")
            if (
                type(current_automatic_qualification) is not AutomaticPolicyQualification
                or current_automatic_qualification != self.automatic_qualification
                or current_automatic_qualification.case != current_case
            ):
                return AuthorizationResult.stale("automatic policy qualification is no longer current")
            if current_case.state is not ReviewCaseState.AUTO_RESOLVED:
                return AuthorizationResult.stale("automatic disposition is no longer resolved")
            return AuthorizationResult.authorized(AuthorizationEvent.AUTOMATIC_POLICY)
        if self.automatic_qualification is not None:
            return AuthorizationResult.rejected("human authorization cannot carry automatic policy evidence")
        if self.case.disposition is not Disposition.NEEDS_REVIEW or self.human_decision is None:
            return AuthorizationResult.rejected("authorization requires an applicable disposition")
        if not self.human_decision.is_current_for(current_case):
            return AuthorizationResult.stale("human decision is no longer current")
        return AuthorizationResult.authorized(AuthorizationEvent.HUMAN_DECISION)


def supersedes(previous: ReviewCase, replacement: ReviewCase) -> bool:
    """Validate immutable supersession of unresolved work for the same target."""

    if previous.target != replacement.target or previous.id == replacement.id:
        return False
    if previous.state not in {ReviewCaseState.OPEN, ReviewCaseState.NEEDS_REVIEW}:
        return False
    return previous.fingerprint != replacement.fingerprint or previous.proposal_set_version != replacement.proposal_set_version


def _validate_text(value: object, label: str, maximum: int) -> None:
    if (
        type(value) is not str
        or not value
        or value.strip() != value
        or len(value) > maximum
        or not value.isprintable()
    ):
        raise ValueError(f"{label} must be bounded trimmed printable text")


def _validate_code_tuple(value: object, label: str) -> None:
    if type(value) is not tuple or not value or len(value) > 16:
        raise ValueError(f"{label} must be a non-empty immutable bounded tuple")
    for code in value:
        _validate_text(code, label, 64)
    if len(value) != len(set(value)):
        raise ValueError(f"{label} cannot contain duplicates")
