import json
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.metadata_report import FileFacts, MetadataReport, TargetFacts, default_target_fields
from mechanical_librarian.domain.proposal_set import evaluate_report


class ReviewCasePersistenceTests(unittest.TestCase):
    def test_nonactionable_set_retires_without_creating_case_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = MetadataReport.create(
                target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Title")),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(), local_reasons=("no_opening_claims",), providers=(),
            )
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            first = repository.admit_review_case(proposal)
            replay = repository.admit_review_case(proposal)
            self.assertEqual("retired", json.loads(first)["status"])
            self.assertEqual("replayed", json.loads(replay)["status"])
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone())
            self.assertEqual((1,), repository._connection.execute("SELECT COUNT(*) FROM review_case_transitions").fetchone())
            repository.close()


if __name__ == "__main__":
    unittest.main()
