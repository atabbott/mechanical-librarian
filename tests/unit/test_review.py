from __future__ import annotations

import unittest

from mechanical_librarian.domain.evidence import Disposition
from mechanical_librarian.domain.identifiers import AdapterId, ConnectionId, ExternalItemId, Fingerprint, LibraryId, ProposalId, ReviewCaseId, SchemaVersion, SnapshotId, TargetId
from mechanical_librarian.domain.lifecycle import ReviewCaseState
from mechanical_librarian.domain.review import ReviewCase, ReviewGroup, supersedes


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


if __name__ == "__main__":
    unittest.main()
