from __future__ import annotations

import unittest

from mechanical_librarian.domain.evidence import EvidenceReference, EvidenceRelationship, EvidenceSummary, FieldProposal, FieldValue, ProposalKind
from mechanical_librarian.domain.identifiers import AdapterId, ConnectionId, EvidenceId, ExternalItemId, LibraryId, ProposalId, SnapshotId, TargetId
from mechanical_librarian.domain.inventory import FieldName


class EvidenceTypeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target = TargetId(AdapterId("abs"), ConnectionId("primary"), LibraryId("books"), ExternalItemId("book-1"))

    def test_proposals_are_field_level_and_require_evidence(self) -> None:
        proposal = FieldProposal(ProposalId("proposal-one"), self.target, SnapshotId("snapshot-one"), FieldName.TITLE, None, FieldValue(("Example",)), ProposalKind.FILL_EMPTY, (EvidenceId("evidence-one"),))
        self.assertEqual(proposal.field, FieldName.TITLE)
        with self.assertRaises(ValueError):
            FieldProposal(ProposalId("proposal-two"), self.target, SnapshotId("snapshot-one"), FieldName.TITLE, FieldValue(("Existing",)), FieldValue(("Example",)), ProposalKind.FILL_EMPTY, (EvidenceId("evidence-one"),))

    def test_no_change_cannot_smuggle_a_replacement(self) -> None:
        with self.assertRaises(ValueError):
            FieldProposal(ProposalId("proposal-three"), self.target, SnapshotId("snapshot-one"), FieldName.TITLE, FieldValue(("Old",)), FieldValue(("New",)), ProposalKind.NO_CHANGE, (EvidenceId("evidence-one"),))

    def test_cross_target_evidence_requires_explicit_policy_approval(self) -> None:
        other = TargetId(AdapterId("abs"), ConnectionId("secondary"), LibraryId("books"), ExternalItemId("book-1"))
        with self.assertRaises(ValueError):
            EvidenceReference(EvidenceId("evidence-one"), self.target, other, EvidenceRelationship.EXPLICIT_CROSS_TARGET)
        reference = EvidenceReference(EvidenceId("evidence-one"), self.target, other, EvidenceRelationship.EXPLICIT_CROSS_TARGET, policy_approved=True)
        self.assertTrue(reference.policy_approved)

    def test_summary_is_bounded_and_does_not_contain_evidence_payload(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceSummary(EvidenceId("evidence-one"), "epub", 1, 0, "x" * 281)


if __name__ == "__main__":
    unittest.main()
