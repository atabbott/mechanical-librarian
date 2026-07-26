import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import (
    FileFacts,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report


def _report(title: str, digest: str) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
        file=FileFacts(0, "a" * 64, digest),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )


class ProposalPersistenceTests(unittest.TestCase):
    def test_publish_is_idempotent_and_reopens_exact_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            repository = open_repository(root / "reports.sqlite")
            report = _report("Title", "b" * 64)
            repository.publish(report, lambda: True)
            proposal_set = evaluate_report(report)
            expected = proposal_set.to_json()
            self.assertEqual(
                expected, repository.publish_proposal(proposal_set)
            )
            self.assertEqual(
                expected, repository.publish_proposal(proposal_set)
            )
            self.assertEqual(
                expected, repository.get_proposal(proposal_set.proposal_set_id)
            )
            columns = repository._connection.execute(
                "PRAGMA table_info(proposal_sets)"
            ).fetchall()
            self.assertEqual(
                ["proposal_set_id", "report_id", "document"],
                [column[1] for column in columns],
            )
            repository.close()

    def test_transaction_rechecks_current_report_before_insert(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            repository = open_repository(root / "reports.sqlite")
            original = _report("Original", "b" * 64)
            repository.publish(original, lambda: True)
            proposal_set = evaluate_report(original)
            successor = _report("Successor", "c" * 64)
            repository.publish(
                successor,
                lambda: True,
                expected_current=original.report_id,
            )
            with self.assertRaises(MetadataReportError):
                repository.publish_proposal(proposal_set)
            count = repository._connection.execute(
                "SELECT count(*) FROM proposal_sets"
            ).fetchone()
            self.assertEqual((0,), count)
            repository.close()

    def test_report_and_pointer_recheck_are_the_final_queries_before_insert(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            repository = open_repository(root / "reports.sqlite")
            report = _report("Title", "b" * 64)
            repository.publish(report, lambda: True)
            statements: list[str] = []
            repository._connection.set_trace_callback(statements.append)
            repository.publish_proposal(evaluate_report(report))
            relevant = tuple(
                statement
                for statement in statements
                if statement.startswith(
                    "SELECT report_id,connection_name,library_name,item_id,"
                )
                or statement.startswith(
                    "SELECT report_id FROM current_metadata_reports"
                )
                or statement.startswith("INSERT INTO proposal_sets")
            )
            self.assertEqual(3, len(relevant))
            self.assertTrue(relevant[0].startswith("SELECT report_id,"))
            self.assertTrue(
                relevant[1].startswith(
                    "SELECT report_id FROM current_metadata_reports"
                )
            )
            self.assertTrue(relevant[2].startswith("INSERT INTO proposal_sets"))
            repository.close()


if __name__ == "__main__":
    unittest.main()
