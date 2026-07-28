import sqlite3
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import (
    POLICY_VERSION,
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)


def _report(title: str, digest: str) -> MetadataReport:
    return MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", default_target_fields(title)),
        file=FileFacts(0, "a" * 64, digest),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
    )


class ProposalPersistenceTests(unittest.TestCase):
    def test_placeholder_report_and_nonmutating_proposal_replay_exactly(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            fields = {
                field.destination: field
                for field in default_target_fields("Title")
            }
            fields[TargetDestination.AUTHORS] = TargetField(
                TargetDestination.AUTHORS,
                TargetFieldState.MISSING,
            )
            claim = LocalClaim.create(
                kind=ClaimKind.CREATOR,
                observed="UNKNOWN",
                canonical="UNKNOWN",
                excerpt="UNKNOWN",
                applicability="not_applicable",
                basis="package_metadata",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
                state=FieldState.VALID,
                role="author",
            )
            report = MetadataReport.create(
                target=TargetFacts(
                    "primary",
                    "ebooks",
                    "item-1",
                    tuple(
                        fields[destination]
                        for destination in TargetDestination
                    ),
                ),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(claim,),
                local_reasons=("package_metadata_creator",),
                providers=(),
            )
            repository = open_repository(root / "reports.sqlite")
            self.assertEqual(
                report.to_json(), repository.publish(report, lambda: True)
            )
            proposal = evaluate_report(report)
            self.assertEqual(POLICY_VERSION, proposal.policy_version)
            authors = next(
                result
                for result in proposal.results
                if result.destination is TargetDestination.AUTHORS
            )
            self.assertEqual(ProposalOutcome.INELIGIBLE, authors.outcome)
            self.assertEqual((), authors.alternatives)
            stored = repository.publish_proposal(proposal)
            self.assertEqual(proposal.to_json(), stored)
            self.assertEqual(
                proposal,
                ProposalSet.from_json(
                    repository.get_proposal(proposal.proposal_set_id)
                ),
            )
            self.assertEqual(
                report.to_json(), repository.get(report.report_id)
            )
            repository.close()

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
                [
                    "proposal_set_id",
                    "report_id",
                    "policy_version",
                    "document",
                ],
                [column[1] for column in columns],
            )
            policy = repository._connection.execute(
                "SELECT policy_version FROM proposal_sets"
            ).fetchone()
            self.assertEqual((POLICY_VERSION,), policy)
            repository.close()

    def test_prior_policy_store_schema_is_rejected_without_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reports.sqlite"
            repository = open_repository(path)
            repository.close()

            connection = sqlite3.connect(path)
            connection.execute(
                "ALTER TABLE proposal_sets DROP COLUMN policy_version"
            )
            connection.commit()
            connection.close()
            before = path.read_bytes()

            with self.assertRaises(MetadataReportError):
                open_repository(path)

            self.assertEqual(before, path.read_bytes())

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
