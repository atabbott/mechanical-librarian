from __future__ import annotations

import unittest
from dataclasses import replace

from mechanical_librarian.domain.evidence import Disposition
from mechanical_librarian.domain.identifiers import AdapterId, ConnectionId, DecisionAuthorizationId, DecisionId, ExternalItemId, Fingerprint, LibraryId, PolicyVersion, ProposalId, ReviewCaseId, SchemaVersion, SnapshotId, TargetId
from mechanical_librarian.domain.lifecycle import ReviewCaseState
from mechanical_librarian.domain.review import AutomaticPolicyQualification, DecisionAuthorization, HumanDecision, HumanDecisionAction, NoticeClass, ReviewCase, ReviewGroup, ReviewNotice, supersedes


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
        with self.assertRaises(ValueError):
            DecisionAuthorization(DecisionAuthorizationId("authorization-one"), case)
        qualification = AutomaticPolicyQualification(case, PolicyVersion("policy-v1"), ("safe-change",))
        authorization = DecisionAuthorization(
            DecisionAuthorizationId("authorization-one"), case,
            automatic_qualification=qualification,
        )
        self.assertIsNone(authorization.human_decision)

    def test_automatic_authorization_stales_when_retained_case_evidence_changes(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.AUTO_RESOLVED, Disposition.AUTO_APPLY,
        )
        qualification = AutomaticPolicyQualification(case, PolicyVersion("policy-v1"), ("safe-change",))
        authorization = DecisionAuthorization(
            DecisionAuthorizationId("authorization-one"), case,
            automatic_qualification=qualification,
        )
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

    def test_automatic_authorization_stales_on_notice_drift_and_rejects_blocking_notices(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.AUTO_RESOLVED, Disposition.AUTO_APPLY,
        )
        qualification = AutomaticPolicyQualification(case, PolicyVersion("policy-v1"), ("safe-change",))
        authorization = DecisionAuthorization(
            DecisionAuthorizationId("authorization-one"), case,
            automatic_qualification=qualification,
        )
        blocking_notice = ReviewNotice(
            NoticeClass.BLOCKING, ProposalId("proposal-one"), ("unsafe-change",), "require human review",
        )
        with self.assertRaises(ValueError):
            ReviewCase(
                ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
                "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.AUTO_RESOLVED, Disposition.AUTO_APPLY,
                (blocking_notice,),
            )

    def test_human_decision_binds_complete_case_and_notice_evidence(self) -> None:
        notice = ReviewNotice(
            NoticeClass.RESOLVED, ProposalId("proposal-one"),
            ("check-source",), "confirm source",
        )
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.HUMAN_RESOLVED,
            Disposition.NEEDS_REVIEW, (notice,),
        )
        decision = HumanDecision(
            DecisionId("decision-one"), case, HumanDecisionAction.ACCEPT_SELECTED,
            (ProposalId("proposal-one"),), "operator",
        )
        authorization = DecisionAuthorization(DecisionAuthorizationId("authorization-one"), case, decision)
        for classification in NoticeClass:
            changed_notice = ReviewNotice(
                classification, ProposalId("proposal-one"), ("check-source",), "confirm source",
            )
            if classification in {NoticeClass.BLOCKING, NoticeClass.NEEDS_ATTENTION}:
                with self.assertRaises(ValueError):
                    ReviewCase(
                        case.id, case.target, case.snapshot, case.fingerprint,
                        case.proposal_set_version, case.proposals, case.state,
                        case.disposition, (changed_notice,),
                    )
                continue
            changed = ReviewCase(
                case.id, case.target, case.snapshot, case.fingerprint, case.proposal_set_version,
                case.proposals, case.state, case.disposition, (changed_notice,),
            )
            expected = "authorized" if changed == case else "stale"
            self.assertEqual(authorization.applicability_for(changed).state.value, expected)
            self.assertEqual(
                decision.applicability_for(changed).value,
                "current" if changed == case else "stale",
            )

    def test_authority_constructors_reject_substitute_and_mutable_values(self) -> None:
        proposal = ProposalId("proposal-one")
        notice = ReviewNotice(NoticeClass.RESOLVED, proposal, ("resolved",), "no action")
        with self.assertRaises(ValueError):
            ReviewNotice("resolved", proposal, ("resolved",), "no action")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ReviewNotice(NoticeClass.RESOLVED, proposal, ["resolved"], "no action")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ReviewNotice(NoticeClass.RESOLVED, ProposalId("proposal-two"), ("resolved",), " no action")
        with self.assertRaises(ValueError):
            ReviewCase(
                ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
                "set-v1", [proposal], ReviewCaseState.OPEN, Disposition.NEEDS_REVIEW,  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            ReviewCase(
                ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
                "set-v1", (proposal,), ReviewCaseState.OPEN, Disposition.NEEDS_REVIEW,
                (ReviewNotice(NoticeClass.RESOLVED, ProposalId("proposal-two"), ("resolved",), "no action"),),
            )

    def test_automatic_qualification_requires_exact_sorted_policy_evidence(self) -> None:
        resolved_notice = ReviewNotice(
            NoticeClass.RESOLVED, ProposalId("proposal-one"), ("resolved",), "no action",
        )
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"), self.fingerprint,
            "set-v1", (ProposalId("proposal-one"),), ReviewCaseState.AUTO_RESOLVED,
            Disposition.AUTO_APPLY, (resolved_notice,),
        )
        qualification = AutomaticPolicyQualification(
            case, PolicyVersion("policy-v1"), ("bounded-risk", "safe-change"),
        )
        authorization = DecisionAuthorization(
            DecisionAuthorizationId("authorization-one"), case,
            automatic_qualification=qualification,
        )
        self.assertEqual(
            authorization.applicability_for(case, qualification).state.value,
            "authorized",
        )
        self.assertEqual(authorization.applicability_for(case).state.value, "stale")
        changed_version = AutomaticPolicyQualification(
            case, PolicyVersion("policy-v2"), ("bounded-risk", "safe-change"),
        )
        changed_rules = AutomaticPolicyQualification(
            case, PolicyVersion("policy-v1"), ("safe-change",),
        )
        self.assertEqual(
            authorization.applicability_for(case, changed_version).state.value,
            "stale",
        )
        self.assertEqual(
            authorization.applicability_for(case, changed_rules).state.value,
            "stale",
        )
        for invalid_rules in (["safe-change"], ("safe-change", "bounded-risk"), (), ("safe-change", "safe-change")):
            with self.subTest(invalid_rules=invalid_rules), self.assertRaises(ValueError):
                AutomaticPolicyQualification(case, PolicyVersion("policy-v1"), invalid_rules)  # type: ignore[arg-type]

    def test_review_case_rejects_untyped_target_components(self) -> None:
        unsafe_target = TargetId("abs", "primary", "books", "book-1")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ReviewCase(
                ReviewCaseId("case-one"), unsafe_target, SnapshotId("snapshot-one"),
                self.fingerprint, "set-v1", (ProposalId("proposal-one"),),
                ReviewCaseState.OPEN, Disposition.NEEDS_REVIEW,
            )

    def test_authority_ids_actions_and_selections_require_exact_types(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"),
            self.fingerprint, "set-v1", (ProposalId("proposal-one"),),
            ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        with self.assertRaises(ValueError):
            HumanDecision(
                "decision-one", case, HumanDecisionAction.ACCEPT_SELECTED,  # type: ignore[arg-type]
                (ProposalId("proposal-one"),), "operator",
            )
        with self.assertRaises(ValueError):
            HumanDecision(
                DecisionId("decision-one"), case, "accept_selected",  # type: ignore[arg-type]
                (ProposalId("proposal-one"),), "operator",
            )
        with self.assertRaises(ValueError):
            HumanDecision(
                DecisionId("decision-one"), case,
                HumanDecisionAction.ACCEPT_SELECTED,
                [ProposalId("proposal-one")], "operator",  # type: ignore[arg-type]
            )
        decision = HumanDecision(
            DecisionId("decision-one"), case, HumanDecisionAction.ACCEPT_SELECTED,
            (ProposalId("proposal-one"),), "operator",
        )
        with self.assertRaises(ValueError):
            DecisionAuthorization("authorization-one", case, decision)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            DecisionAuthorization(
                DecisionAuthorizationId("authorization-one"),
                case,
                DecisionId("decision-two"),  # type: ignore[arg-type]
            )

    def test_each_independent_case_guard_change_fails_closed(self) -> None:
        case = ReviewCase(
            ReviewCaseId("case-one"), self.target, SnapshotId("snapshot-one"),
            self.fingerprint, "set-v1", (ProposalId("proposal-one"),),
            ReviewCaseState.HUMAN_RESOLVED, Disposition.NEEDS_REVIEW,
        )
        decision = HumanDecision(
            DecisionId("decision-one"), case, HumanDecisionAction.ACCEPT_SELECTED,
            (ProposalId("proposal-one"),), "operator",
        )
        authorization = DecisionAuthorization(
            DecisionAuthorizationId("authorization-one"), case, decision,
        )
        other_target = TargetId(
            AdapterId("abs"), ConnectionId("secondary"), LibraryId("books"),
            ExternalItemId("book-1"),
        )
        other_fingerprint = Fingerprint.from_canonical_value(
            {"title": "Changed"}, schema=SchemaVersion("snapshot-v1")
        )
        changes = (
            {"target": other_target},
            {"snapshot": SnapshotId("snapshot-two")},
            {"fingerprint": other_fingerprint},
            {"proposal_set_version": "set-v2"},
            {"proposals": (ProposalId("proposal-two"),)},
            {
                "notices": (
                    ReviewNotice(
                        NoticeClass.RESOLVED, ProposalId("proposal-one"),
                        ("resolved",), "no action",
                    ),
                )
            },
            {"state": ReviewCaseState.NEEDS_REVIEW},
            {"disposition": Disposition.CLOSED_REJECTED},
        )
        for change in changes:
            with self.subTest(change=change):
                changed = replace(case, **change)
                self.assertEqual(
                    decision.applicability_for(changed).value,
                    "stale",
                )
                self.assertEqual(
                    authorization.applicability_for(changed).state.value,
                    "stale",
                )


if __name__ == "__main__":
    unittest.main()
