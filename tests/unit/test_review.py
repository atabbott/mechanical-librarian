from __future__ import annotations

import unittest

from mechanical_librarian.domain.evidence import Disposition
from mechanical_librarian.domain.identifiers import AdapterId, ConnectionId, DecisionAuthorizationId, DecisionId, ExternalItemId, Fingerprint, LibraryId, ProposalId, ReviewCaseId, SchemaVersion, SnapshotId, TargetId
from mechanical_librarian.domain.lifecycle import ReviewCaseState
from mechanical_librarian.domain.review import DecisionAuthorization, HumanDecision, HumanDecisionAction, ReviewCase, ReviewGroup, supersedes


class ReviewCaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = TargetId(AdapterId("abs"), ConnectionId("primary"), LibraryId("books"), ExternalItemId("book-1"))
        self.fingerprint = Fingerprint.from_canonical_value({"title": "Old"}, schema=SchemaVersion("snapshot-v1"))

    def test_new_fingerprint_supersedes_unresolved_case(self) -> None:
        old = ReviewCase(ReviewCaseId("case-old"), self.target, SnapshotId("snapshot-one"), self.fingerprint, "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.NEEDS_REVIEW, Disposition.NEEDS_REVIEW)
        new = ReviewCase(ReviewCaseId("case-new"), self.target, SnapshotId("snapshot-two"), Fingerprint.from_canonical_value({"title": "New"}, schema=SchemaVersion("snapshot-v1")), "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.OPEN, Disposition.NEEDS_REVIEW)
        self.assertTrue(supersedes(old, new))

    def test_group_only_holds_sibling_case_ids(self) -> None:
        group = ReviewGroup("Example", "identifier", "high", (ReviewCaseId("case-one"), ReviewCaseId("case-two")))
        self.assertEqual(group.sibling_case_ids, (ReviewCaseId("case-one"), ReviewCaseId("case-two")))

    def test_ordinary_case_rejects_destructive_work(self) -> None:
        with self.assertRaises(ValueError):
            ReviewCase(ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint, "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.OPEN, Disposition.NEEDS_REVIEW, destructive=True)

    def test_authorization_requires_actual_current_decision_and_case(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        decision = HumanDecision(
            DecisionId("decision-one"), case, HumanDecisionAction.ACCEPT_SELECTED,
            (ProposalId("proposal-one"),), "operator",
        )
        authorization = DecisionAuthorization(DecisionAuthorizationId("authorization-one"), case, decision)
        self.assertEqual(authorization.selected_proposals, (ProposalId("proposal-one"),))

    def test_authorization_rejects_wrong_case_target_and_selection(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        with self.assertRaises(ValueError):
            HumanDecision(DecisionId("decision-one"), case, HumanDecisionAction.ACCEPT_SELECTED, (ProposalId("proposal-two"),), "operator")
        other_case = ReviewCase(
            ReviewCaseId("case-two"), self.target, SnapshotId("snapshot-two"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        decision = HumanDecision(DecisionId("decision-one"), case, HumanDecisionAction.ACCEPT_SELECTED, (ProposalId("proposal-one"),), "operator")
        with self.assertRaises(ValueError):
            DecisionAuthorization(DecisionAuthorizationId("authorization-one"), other_case, decision)

    def test_authorization_stales_on_fingerprint_or_proposal_change(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        decision = HumanDecision(DecisionId("decision-one"), case, HumanDecisionAction.ACCEPT_SELECTED, (ProposalId("proposal-one"),), "operator")
        authorization = DecisionAuthorization(DecisionAuthorizationId("authorization-one"), case, decision)
        changed = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-two"),
            Fingerprint.from_canonical_value({"title": "Changed"}, schema=SchemaVersion("snapshot-v1")),
            "set-v2", (ProposalId("proposal-one"),), ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        self.assertEqual(authorization.applicability_for(changed).state.value, "stale")
        self.assertEqual(decision.applicability_for(changed).value, "stale")
        changed_proposals = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-two"), self.fingerprint,
            "set-v1", (ProposalId("proposal-two"),), ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        self.assertEqual(authorization.applicability_for(changed_proposals).state.value, "stale")
        replacement = ReviewCase(
            ReviewCaseId("case-two"), self.target, SnapshotId("snapshot-two"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        self.assertEqual(authorization.applicability_for(replacement).state.value, "superseded")

    def test_automatic_authorization_requires_auto_resolved_case(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.AUTO_RESOLVED, Disposition.AUTO_APPLY,
        )
        self.assertIsNone(DecisionAuthorization(DecisionAuthorizationId("authorization-one"), case).human_decision)

    def test_automatic_authorization_stales_when_retained_case_evidence_changes(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.AUTO_RESOLVED, Disposition.AUTO_APPLY,
        )
        authorization = DecisionAuthorization(DecisionAuthorizationId("authorization-one"), case)
        changed_proposals = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-two"),), ReviewCaseState.AUTO_RESOLVED, Disposition.AUTO_APPLY,
        )
        downgraded_disposition = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.AUTO_RESOLVED, Disposition.NEEDS_REVIEW,
        )

        self.assertEqual(authorization.applicability_for(changed_proposals).state.value, "stale")
        self.assertEqual(authorization.applicability_for(downgraded_disposition).state.value, "stale")


if __name__ == "__main__":
    unittest.main()
