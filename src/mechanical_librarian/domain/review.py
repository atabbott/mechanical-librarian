"""Snapshot-aware review surfaces with no cross-target decision authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .evidence import Disposition
from .identifiers import DecisionAuthorizationId, DecisionId, Fingerprint, ProposalId, ReviewCaseId, SnapshotId, TargetId
from .lifecycle import ReviewCaseState


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
    case: ReviewCaseId
    target: TargetId
    fingerprint: Fingerprint
    proposal_set_version: str
    action: HumanDecisionAction
    selected_proposals: tuple[ProposalId, ...]
    actor: str

    def __post_init__(self) -> None:
        if not self.actor or (self.action is HumanDecisionAction.ACCEPT_SELECTED and not self.selected_proposals):
            raise ValueError("human decisions require an actor and selected proposals when accepting")

    def is_current_for(self, case: ReviewCase) -> bool:
        return self.case == case.id and self.target == case.target and self.fingerprint == case.fingerprint and self.proposal_set_version == case.proposal_set_version


@dataclass(frozen=True)
class DecisionAuthorization:
    id: DecisionAuthorizationId
    case: ReviewCaseId
    fingerprint: Fingerprint
    proposal_set_version: str
    disposition: Disposition
    human_decision: DecisionId | None = None

    def __post_init__(self) -> None:
        if self.disposition is Disposition.NEEDS_REVIEW and self.human_decision is None:
            raise ValueError("human review authorization requires a current human decision")
        if self.disposition is not Disposition.NEEDS_REVIEW and self.human_decision is not None:
            raise ValueError("automatic authorization cannot carry a human decision")
        if self.disposition not in {Disposition.AUTO_APPLY, Disposition.NEEDS_REVIEW}:
            raise ValueError("only applicable dispositions can authorize apply")


def supersedes(previous: ReviewCase, replacement: ReviewCase) -> bool:
    """Validate immutable supersession of unresolved work for the same target."""

    if previous.target != replacement.target or previous.id == replacement.id:
        return False
    if previous.state not in {ReviewCaseState.OPEN, ReviewCaseState.NEEDS_REVIEW}:
        return False
    return previous.fingerprint != replacement.fingerprint or previous.proposal_set_version != replacement.proposal_set_version
