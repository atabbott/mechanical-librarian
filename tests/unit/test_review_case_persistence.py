import json
import tempfile
import threading
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.metadata_report import FileFacts, MetadataReport, TargetFacts, default_target_fields
from mechanical_librarian.domain.proposal_set import (
    ProposalAlternative,
    ProposalAssessment,
    ProposalOutcome,
    ProposalResult,
    ProposalScope,
    ProposalSet,
    ProposalValue,
    ProposalValueKind,
    evaluate_report,
)


class ReviewCasePersistenceTests(unittest.TestCase):
    def _actionable(self, report: MetadataReport) -> ProposalSet:
        base = evaluate_report(report)
        alternative = ProposalAlternative.create(
            ProposalValue(ProposalValueKind.TEXT, "Revised title"),
            ProposalAssessment.REPLACES_TARGET,
            ("local_" + "a" * 64,), ("correlation_" + "b" * 64,), ProposalScope.WORK,
        )
        changed = ProposalResult(base.results[0].destination, ProposalOutcome.PROPOSE_CHANGE, (alternative,), alternative.alternative_id, ("unique_typed_candidate",), base.results[0].dependencies)
        return ProposalSet.create(report_id=base.report_id, target_fingerprint=base.target_fingerprint, results=(changed, *base.results[1:]))

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

    def test_actionable_set_creates_complete_case_and_exact_get(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = MetadataReport.create(
                target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Title")),
                file=FileFacts(0, "a" * 64, "b" * 64),
                local_claims=(), local_reasons=("no_opening_claims",), providers=(),
            )
            repository.publish(report, lambda: True)
            proposal = self._actionable(report)
            repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal))
            opened = json.loads(repository.get_review_case(admitted["review_case_id"]))
            self.assertEqual("admitted", admitted["status"])
            self.assertTrue(opened["current"])
            self.assertEqual(16, len(opened["case"]["results"]))
            repository.close()

    def test_successor_makes_prior_case_historical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            first = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(first, lambda: True)
            first_set = self._actionable(first); repository.publish_proposal(first_set)
            first_case = json.loads(repository.admit_review_case(first_set))["review_case_id"]
            successor = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Changed")), file=FileFacts(0, "c" * 64, "d" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(successor, lambda: True)
            successor_set = self._actionable(successor); repository.publish_proposal(successor_set)
            successor_case = json.loads(repository.admit_review_case(successor_set))["review_case_id"]
            self.assertFalse(json.loads(repository.get_review_case(first_case))["current"])
            self.assertTrue(json.loads(repository.get_review_case(successor_case))["current"])
            repository.close()

    def test_nonactionable_successor_retires_current_case_without_empty_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            first = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(first, lambda: True); first_set = self._actionable(first); repository.publish_proposal(first_set)
            case_id = json.loads(repository.admit_review_case(first_set))["review_case_id"]
            successor = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Changed")), file=FileFacts(0, "c" * 64, "d" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(successor, lambda: True); successor_set = evaluate_report(successor); repository.publish_proposal(successor_set)
            self.assertEqual("retired", json.loads(repository.admit_review_case(successor_set))["status"])
            self.assertFalse(json.loads(repository.get_review_case(case_id))["current"])
            self.assertEqual((1,), repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone())
            repository.close()

    def test_stale_and_cross_target_proposals_mutate_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            first = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(first, lambda: True); stale = self._actionable(first); repository.publish_proposal(stale)
            current = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Changed")), file=FileFacts(0, "c" * 64, "d" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(current, lambda: True)
            with self.assertRaises(Exception): repository.admit_review_case(stale)
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone())
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM review_case_transitions").fetchone())
            repository.close()

    def test_concurrent_identical_admission_converges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository = open_repository(database)
            report = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(report, lambda: True); proposal = self._actionable(report); repository.publish_proposal(proposal); repository.close()
            barrier, results = threading.Barrier(2), []
            def admit() -> None:
                local = open_repository(database, create=False)
                try:
                    barrier.wait(); results.append(json.loads(local.admit_review_case(proposal)))
                finally: local.close()
            threads = [threading.Thread(target=admit) for _ in range(2)]
            [thread.start() for thread in threads]; [thread.join() for thread in threads]
            self.assertEqual(2, len(results))
            self.assertEqual(1, len({value["review_case_id"] for value in results}))

    def test_transition_failure_rolls_back_case_and_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = MetadataReport.create(target=TargetFacts("primary", "ebooks", "item-1", default_target_fields("Title")), file=FileFacts(0, "a" * 64, "b" * 64), local_claims=(), local_reasons=("no_opening_claims",), providers=())
            repository.publish(report, lambda: True); proposal = self._actionable(report); repository.publish_proposal(proposal)
            repository._connection.execute("CREATE TRIGGER injected_review_transition_failure BEFORE INSERT ON review_case_transitions BEGIN SELECT RAISE(ABORT, 'injected'); END")
            with self.assertRaises(Exception): repository.admit_review_case(proposal)
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone())
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM current_review_case_lineages").fetchone())
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM review_case_transitions").fetchone())
            repository._connection.execute("DROP TRIGGER injected_review_transition_failure")
            self.assertEqual("admitted", json.loads(repository.admit_review_case(proposal))["status"])
            repository.close()

    def test_exact_current_schema_restarts_and_prior_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository = open_repository(database); repository.close()
            reopened = open_repository(database, create=False); reopened.close()
            old = Path(directory) / "old.sqlite"
            old.write_bytes(database.read_bytes())
            import sqlite3
            connection = sqlite3.connect(old)
            connection.execute("DROP TABLE review_case_transitions"); connection.commit(); connection.close()
            before = old.read_bytes()
            with self.assertRaises(Exception): open_repository(old, create=False)
            self.assertEqual(before, old.read_bytes())


if __name__ == "__main__":
    unittest.main()
