"""Synthetic installed-surface lifecycle proof for review admission storage."""
import json
import io
import tempfile
import threading
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.interfaces.cli import main as cli_main


def _actionable(report):
    return evaluate_report(report)


def _report(
    title: str = "Title",
    content_digest: str = "a",
    file_identity: str = "b",
    *,
    actionable: bool = True,
) -> MetadataReport:
    claims = ()
    reasons = ("no_opening_claims",)
    if actionable:
        claims = (
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed="Exact evaluator candidate",
                canonical="Exact evaluator candidate",
                excerpt="Exact evaluator candidate",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            ),
        )
        reasons = ("opening_matter_title",)
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(title),
        ),
        file=FileFacts(0, content_digest * 64, file_identity * 64),
        local_claims=claims,
        local_reasons=reasons,
        providers=(),
    )


class ReviewCaseLifecycleTests(unittest.TestCase):
    def test_exact_cli_admit_and_get_serialization(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reviews.sqlite"; repository = open_repository(database)
            report = _report()
            repository.publish(report, lambda: True); proposal = _actionable(report); repository.publish_proposal(proposal)
            out, err = io.StringIO(), io.StringIO()
            code = cli_main(["review-case-admit", "--config", "/synthetic-config", "--database", str(database), "--artifact-store", "/synthetic-artifacts", "--proposal-set-id", proposal.proposal_set_id], review_admit_runner=lambda *_: repository.admit_review_case(proposal), stdout=out, stderr=err)
            self.assertEqual(0, code); self.assertEqual("", err.getvalue())
            admitted = json.loads(out.getvalue()); case_id = admitted["review_case_id"]
            out = io.StringIO()
            self.assertEqual(0, cli_main(["review-case-get", "--database", str(database), "--review-case-id", case_id], review_get_runner=lambda *_: repository.get_review_case(case_id), stdout=out, stderr=err))
            self.assertTrue(json.loads(out.getvalue())["current"])
            self.assertEqual(2, cli_main(["review-case-get", "--database", str(database), "--review-case-id", "bad"], review_get_runner=lambda *_: self.fail("called"), stdout=io.StringIO(), stderr=io.StringIO()))
            repository.close()
    def test_absent_restart_replay_successor_and_retirement(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reviews.sqlite"
            repository = open_repository(database)
            first = _report()
            repository.publish(first, lambda: True); proposal = _actionable(first); repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal)); case_id = admitted["review_case_id"]
            self.assertEqual("admitted", admitted["status"]); self.assertEqual("replayed", json.loads(repository.admit_review_case(proposal))["status"])
            repository.close(); repository = open_repository(database, create=False)
            self.assertTrue(json.loads(repository.get_review_case(case_id))["current"])
            successor = _report("Changed", "c", "d")
            repository.publish(successor, lambda: True); later = _actionable(successor); repository.publish_proposal(later)
            later_case = json.loads(repository.admit_review_case(later))["review_case_id"]
            self.assertFalse(json.loads(repository.get_review_case(case_id))["current"]); self.assertTrue(json.loads(repository.get_review_case(later_case))["current"])
            retired_report = _report("Retired", "e", "f", actionable=False)
            repository.publish(retired_report, lambda: True)
            retired = evaluate_report(retired_report); repository.publish_proposal(retired)
            self.assertEqual("retired", json.loads(repository.admit_review_case(retired))["status"])
            self.assertFalse(json.loads(repository.get_review_case(later_case))["current"])
            self.assertEqual((2,), repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone())
            repository.close()

    def test_concurrent_identical_admission_converges(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reviews.sqlite"; repository = open_repository(database)
            report = _report()
            repository.publish(report, lambda: True); proposal = _actionable(report); repository.publish_proposal(proposal); repository.close()
            barrier, outcomes = threading.Barrier(2), []
            def admit():
                local = open_repository(database, create=False)
                try: barrier.wait(); outcomes.append(json.loads(local.admit_review_case(proposal)))
                finally: local.close()
            workers = [threading.Thread(target=admit) for _ in range(2)]
            [worker.start() for worker in workers]; [worker.join() for worker in workers]
            self.assertEqual(1, len({value["review_case_id"] for value in outcomes}))


if __name__ == "__main__": unittest.main()
