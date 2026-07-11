"""Snapshot-aware review surfaces with no cross-target decision authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .evidence import Disposition
from .identifiers import DecisionAuthorizationId, DecisionId, Fingerprint, ProposalId, ReviewCaseId, SnapshotId, TargetId
from .lifecycle import AuthorizationEvent, AuthorizationResult, AuthorizationState, HumanDecisionApplicability, ReviewCaseState


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
        if not self.diagnostic_codes or not self.required_action:
            raise ValueError("review notices require diagnostics and an action")


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

    def __post_init__(self) -> None:
        if not self.proposal_set_version or not self.proposals:
            raise ValueError("review cases require a proposal-set version and proposals")
        if len(self.proposals) != len(set(self.proposals)):
            raise ValueError("review cases cannot repeat proposals")
        if self.destructive:
            raise ValueError("destructive work belongs to a separate review flow")


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
        if self.case.disposition is not Disposition.NEEDS_REVIEW or self.case.state is not ReviewCaseState.HUMAN_RESOLVED:
            raise ValueError("human decisions require a resolved needs-review case")
        if not self.actor.strip():
            raise ValueError("human decisions require an actor")
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
        if self.case.id != case.id:
            return HumanDecisionApplicability.SUPERSEDED
        if self.case.target != case.target or self.case.fingerprint != case.fingerprint or self.case.proposal_set_version != case.proposal_set_version:
            return HumanDecisionApplicability.STALE
        if case.state is not ReviewCaseState.HUMAN_RESOLVED or case.disposition is not Disposition.NEEDS_REVIEW:
            return HumanDecisionApplicability.REJECTED
        return HumanDecisionApplicability.CURRENT

@dataclass(frozen=True)
class DecisionAuthorization:
    id: DecisionAuthorizationId
    case: ReviewCase
    human_decision: HumanDecision | None = None

    def __post_init__(self) -> None:
        result = self.applicability_for(self.case)
        if result.state is not AuthorizationState.AUTHORIZED:
            raise ValueError(result.reason)

    @property
    def selected_proposals(self) -> tuple[ProposalId, ...]:
        return () if self.human_decision is None else self.human_decision.selected_proposals

    def applicability_for(self, current_case: ReviewCase) -> AuthorizationResult:
        if current_case.id != self.case.id:
            return AuthorizationResult.stale("review case changed")
        if current_case.target != self.case.target:
            return AuthorizationResult.stale("review target changed")
        if current_case.fingerprint != self.case.fingerprint:
            return AuthorizationResult.stale("review fingerprint changed")
        if current_case.proposal_set_version != self.case.proposal_set_version:
            return AuthorizationResult.stale("review proposal set changed")
        if self.case.destructive:
            return AuthorizationResult.rejected("destructive work has no automatic authorization path")
        if self.case.disposition is Disposition.AUTO_APPLY:
            if self.human_decision is not None:
                return AuthorizationResult.rejected("automatic authorization cannot carry a human decision")
            if current_case.state is not ReviewCaseState.AUTO_RESOLVED:
                return AuthorizationResult.stale("automatic disposition is no longer resolved")
            return AuthorizationResult.authorized(AuthorizationEvent.AUTOMATIC_POLICY)
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
