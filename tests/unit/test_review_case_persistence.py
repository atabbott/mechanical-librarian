import json
import os
import sys
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
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionIncompatibleError,
    ReviewAdmissionResult,
    ReviewCase,
    ReviewCaseValueError,
)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.review_case import ReviewTransition


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


class ReviewCasePersistenceTests(unittest.TestCase):
    def test_partial_human_decision_remains_current_unapplied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            proposal = evaluate_report(report)
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
            case = ReviewCase.create_from_data(json.loads(repository.get_review_case(case_id))["case"])
            field = next(value for value in case.proposals if value["result"]["outcome"] == "unresolved_choice")
            repository.record_human_decision(HumanDecision.create(case, proposal_id=field["proposal_id"], target_fingerprint=case.target_fingerprint, destination=field["destination"], actor="owner", outcome="keep_current", selected_value=None, idempotency_key="partial-count-0001", occurred_at=1_000))
            self.assertEqual(1, repository.current_unapplied_count("ebooks"))
            repository.close()

    def test_nondefault_resolved_policy_replays_admits_reopens_and_browses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            resolved = resolve_policy(
                MetadataPolicyProfile.builtin(),
                report.target.library,
                {"options": {"date_precision": "full_date"}, "destinations": {}},
            )
            proposal = evaluate_report(report, resolved.resolved_id)
            original_report = report.to_json()
            repository.publish(report, lambda: True)
            repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal))
            self.assertEqual("admitted", admitted["status"])
            case = json.loads(repository.get_review_case(admitted["review_case_id"]))
            self.assertEqual(proposal.proposal_set_id, case["case"]["proposal_set_id"])
            self.assertEqual(original_report, repository.get(report.report_id))
            browsed = repository.list_current_review_case_documents(None, 50, 0, None)
            self.assertEqual(1, len(browsed))
            repository.close()

    def test_case_parser_rejects_duplicate_and_oversized_documents(self) -> None:
        with self.assertRaises(ReviewCaseValueError):
            ReviewCase.from_json('{"schema_version":"review-case-v1","schema_version":"review-case-v1"}')
        with self.assertRaises(ReviewCaseValueError):
            ReviewCase.from_json("x" * 2_097_153)

    def test_admission_result_requires_canonical_closed_shape(self) -> None:
        value = ReviewAdmissionResult("no_case", None)
        self.assertEqual(value, ReviewAdmissionResult.from_json(value.to_json()))
        with self.assertRaises(ReviewCaseValueError):
            ReviewAdmissionResult.from_json('{"status":"no_case"}')

    def test_transition_has_deterministic_identity(self) -> None:
        transition = ReviewTransition("primary", "ebooks", "item-1", 1, "proposal_set_" + "a" * 64, "report_" + "b" * 64, "no_case", None, None, "nonactionable")
        self.assertTrue(transition.transition_id.startswith("review_transition_"))
        with self.assertRaises(ReviewCaseValueError):
            ReviewTransition("primary", "ebooks", "item-1", 0, "x", "y", "admitted", None, None, "actionable")
        case_id = "review_case_" + "c" * 64
        invalid_predecessors = (
            ("admitted", case_id, case_id, "actionable"),
            ("no_case", case_id, None, "nonactionable"),
            ("superseded", None, case_id, "actionable"),
            ("retired", None, None, "nonactionable"),
        )
        for kind, predecessor, successor, reason in invalid_predecessors:
            with self.subTest(kind=kind), self.assertRaises(
                ReviewCaseValueError
            ):
                ReviewTransition(
                    "primary",
                    "ebooks",
                    "item-1",
                    1,
                    "proposal_set_" + "a" * 64,
                    "report_" + "b" * 64,
                    kind,
                    predecessor,
                    successor,
                    reason,
                )

    def test_case_projections_are_detached_from_content_identity(self) -> None:
        report = _report()
        case = ReviewCase.create(self._actionable(report), report)
        baseline = case.to_json()

        case.target["item"] = "mutated"
        case.results[0]["outcome"] = "mutated"
        case.target["fields"].append("mutated")  # type: ignore[union-attr]

        data = case.to_data()
        data["target"]["item"] = "mutated"  # type: ignore[index]
        data["results"][0]["outcome"] = "mutated"  # type: ignore[index]
        proposals = case.proposals
        proposals[0]["result"]["outcome"] = "mutated"  # type: ignore[index]
        actionable = case.actionable
        actionable[0]["outcome"] = "mutated"

        source = json.loads(baseline)
        restored = ReviewCase.create_from_data(source)
        source["target"]["item"] = "mutated"
        source["results"][0]["outcome"] = "mutated"

        self.assertEqual(baseline, case.to_json())
        self.assertEqual(baseline, restored.to_json())
        self.assertEqual(case.review_case_id, restored.review_case_id)
    def _actionable(self, report: MetadataReport) -> ProposalSet:
        proposal = evaluate_report(report)
        self.assertTrue(
            any(
                result.outcome
                in {
                    ProposalOutcome.PROPOSE_CHANGE,
                    ProposalOutcome.UNRESOLVED_CHOICE,
                }
                for result in proposal.results
            )
        )
        return proposal

    def test_nonactionable_set_retires_without_creating_case_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report(actionable=False)
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            first = repository.admit_review_case(proposal)
            replay = repository.admit_review_case(proposal)
            self.assertEqual("no_case", json.loads(first)["status"])
            self.assertEqual("no_case", json.loads(replay)["status"])
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone())
            self.assertEqual((1,), repository._connection.execute("SELECT COUNT(*) FROM review_case_transitions").fetchone())
            repository.close()

    def test_transition_kind_tracks_an_active_prior_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            outcomes: list[str] = []

            first = _report(actionable=False)
            repository.publish(first, lambda: True)
            first_set = evaluate_report(first)
            repository.publish_proposal(first_set)
            outcomes.append(
                json.loads(repository.admit_review_case(first_set))["status"]
            )

            second = _report("Second", "c", "d")
            repository.publish(second, lambda: True)
            second_set = self._actionable(second)
            repository.publish_proposal(second_set)
            outcomes.append(
                json.loads(repository.admit_review_case(second_set))["status"]
            )

            third = _report("Third", "e", "f", actionable=False)
            repository.publish(third, lambda: True)
            third_set = evaluate_report(third)
            repository.publish_proposal(third_set)
            outcomes.append(
                json.loads(repository.admit_review_case(third_set))["status"]
            )

            fourth = _report("Fourth", "0", "1", actionable=False)
            repository.publish(fourth, lambda: True)
            fourth_set = evaluate_report(fourth)
            repository.publish_proposal(fourth_set)
            outcomes.append(
                json.loads(repository.admit_review_case(fourth_set))["status"]
            )

            transitions = [
                row[0]
                for row in repository._connection.execute(
                    "SELECT kind FROM review_case_transitions ORDER BY checkpoint"
                ).fetchall()
            ]
            repository.close()

        self.assertEqual(
            ["no_case", "admitted", "retired", "no_case"],
            outcomes,
        )
        self.assertEqual(outcomes, transitions)

    def test_actionable_set_creates_complete_case_and_exact_get(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            repository.publish(report, lambda: True)
            proposal = self._actionable(report)
            repository.publish_proposal(proposal)
            admitted = json.loads(repository.admit_review_case(proposal))
            opened = json.loads(repository.get_review_case(admitted["review_case_id"]))
            self.assertEqual("admitted", admitted["status"])
            self.assertTrue(repository.review_case_exists(admitted["review_case_id"]))
            self.assertFalse(repository.review_case_exists("review_case_" + "f" * 64))
            self.assertTrue(opened["current"])
            self.assertEqual(16, len(opened["case"]["results"]))
            repository.close()

    def test_successor_makes_prior_case_historical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            first = _report()
            repository.publish(first, lambda: True)
            first_set = self._actionable(first); repository.publish_proposal(first_set)
            first_case = json.loads(repository.admit_review_case(first_set))["review_case_id"]
            successor = _report("Changed", "c", "d")
            repository.publish(successor, lambda: True)
            successor_set = self._actionable(successor); repository.publish_proposal(successor_set)
            successor_case = json.loads(repository.admit_review_case(successor_set))["review_case_id"]
            self.assertFalse(json.loads(repository.get_review_case(first_case))["current"])
            self.assertTrue(json.loads(repository.get_review_case(successor_case))["current"])
            repository.close()

    def test_newer_report_immediately_makes_case_historical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            first = _report()
            repository.publish(first, lambda: True); proposal = self._actionable(first); repository.publish_proposal(proposal)
            case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
            newer = _report("New", "c", "d")
            repository.publish(newer, lambda: True)
            self.assertFalse(json.loads(repository.get_review_case(case_id))["current"])
            repository.close()

    def test_nonactionable_successor_retires_current_case_without_empty_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            first = _report()
            repository.publish(first, lambda: True); first_set = self._actionable(first); repository.publish_proposal(first_set)
            case_id = json.loads(repository.admit_review_case(first_set))["review_case_id"]
            successor = _report("Changed", "c", "d", actionable=False)
            repository.publish(successor, lambda: True); successor_set = evaluate_report(successor); repository.publish_proposal(successor_set)
            self.assertEqual("retired", json.loads(repository.admit_review_case(successor_set))["status"])
            self.assertFalse(json.loads(repository.get_review_case(case_id))["current"])
            self.assertEqual((1,), repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone())
            repository.close()

    def test_stale_and_cross_target_proposals_mutate_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            first = _report()
            repository.publish(first, lambda: True); stale = self._actionable(first); repository.publish_proposal(stale)
            current = _report("Changed", "c", "d")
            repository.publish(current, lambda: True)
            with self.assertRaises(Exception): repository.admit_review_case(stale)
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM review_cases").fetchone())
            self.assertEqual((0,), repository._connection.execute("SELECT COUNT(*) FROM review_case_transitions").fetchone())
            repository.close()

    def test_synthetic_evaluator_output_is_incompatible_and_mutates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            repository.publish(report, lambda: True)
            base = evaluate_report(report)
            alternative = ProposalAlternative.create(
                ProposalValue(ProposalValueKind.TEXT, "Fabricated title"),
                ProposalAssessment.REPLACES_TARGET,
                ("local_" + "a" * 64,),
                ("correlation_" + "b" * 64,),
                ProposalScope.WORK,
            )
            result = ProposalResult(
                base.results[0].destination,
                ProposalOutcome.PROPOSE_CHANGE,
                (alternative,),
                alternative.alternative_id,
                ("unique_typed_candidate",),
                base.results[0].dependencies,
            )
            synthetic = ProposalSet.create(
                report_id=report.report_id,
                target_fingerprint=report.target.fingerprint,
                results=(result, *base.results[1:]),
                resolved_policy_id=base.resolved_policy_id,
            )
            repository._connection.execute(
                "INSERT INTO proposal_sets("
                "proposal_set_id,report_id,policy_version,document"
                ") VALUES(?,?,?,?)",
                (
                    synthetic.proposal_set_id,
                    report.report_id,
                    synthetic.policy_version,
                    synthetic.to_json(),
                ),
            )
            repository._connection.commit()
            with self.assertRaises(ReviewAdmissionIncompatibleError):
                repository.admit_review_case(synthetic)
            for table in (
                "review_cases",
                "review_case_transitions",
                "current_review_case_lineages",
            ):
                self.assertEqual(
                    (0,),
                    repository._connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone(),
                )
            repository.close()

    def test_target_incompatible_nonactionable_set_mutates_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report(actionable=False)
            repository.publish(report, lambda: True)
            exact = evaluate_report(report)
            incompatible = ProposalSet.create(
                report_id=report.report_id,
                target_fingerprint="f" * 64,
                results=exact.results,
                resolved_policy_id=exact.resolved_policy_id,
            )
            repository._connection.execute(
                "INSERT INTO proposal_sets("
                "proposal_set_id,report_id,policy_version,document"
                ") VALUES(?,?,?,?)",
                (
                    incompatible.proposal_set_id,
                    report.report_id,
                    incompatible.policy_version,
                    incompatible.to_json(),
                ),
            )
            repository._connection.commit()
            with self.assertRaises(ReviewAdmissionIncompatibleError):
                repository.admit_review_case(incompatible)
            for table in (
                "review_cases",
                "review_case_transitions",
                "current_review_case_lineages",
            ):
                self.assertEqual(
                    (0,),
                    repository._connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone(),
                )
            repository.close()

    def test_replay_uses_current_checkpoint_outcome_not_transition_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            first = _report(actionable=False)
            repository.publish(first, lambda: True)
            first_set = evaluate_report(first)
            repository.publish_proposal(first_set)
            self.assertEqual(
                "no_case",
                json.loads(repository.admit_review_case(first_set))["status"],
            )

            successor = _report("Changed", "c", "d")
            repository.publish(successor, lambda: True)
            successor_set = self._actionable(successor)
            repository.publish_proposal(successor_set)
            repository.admit_review_case(successor_set)

            repository.publish(
                first,
                lambda: True,
                expected_current=successor.report_id,
            )
            self.assertEqual(
                "retired",
                json.loads(repository.admit_review_case(first_set))["status"],
            )
            self.assertEqual(
                "retired",
                json.loads(repository.admit_review_case(first_set))["status"],
            )
            repository.close()

    def test_impossible_transition_pointer_combination_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report(actionable=False)
            repository.publish(report, lambda: True)
            proposal = evaluate_report(report)
            repository.publish_proposal(proposal)
            repository.admit_review_case(proposal)
            transition_id = repository._connection.execute(
                "SELECT transition_id FROM current_review_case_lineages"
            ).fetchone()[0]
            repository._connection.execute(
                "DROP TRIGGER review_case_transitions_no_update"
            )
            repository._connection.execute("PRAGMA ignore_check_constraints=ON")
            repository._connection.execute(
                "UPDATE review_case_transitions SET kind='admitted',"
                "reason='actionable' WHERE transition_id=?",
                (transition_id,),
            )
            repository._connection.execute(
                "UPDATE current_review_case_lineages SET outcome='admitted'"
            )
            repository._connection.execute("PRAGMA ignore_check_constraints=OFF")
            repository._connection.commit()
            with self.assertRaises(Exception):
                repository.admit_review_case(proposal)
            repository.close()

    def test_concurrent_identical_admission_converges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository = open_repository(database)
            report = _report()
            repository.publish(report, lambda: True); proposal = self._actionable(report); repository.publish_proposal(proposal); repository.close()
            barrier, results = threading.Barrier(2), []
            def admit() -> None:
                if sys.platform.startswith("linux"):
                    barrier.wait()
                    local = open_repository(database, create=False)
                else:
                    local = open_repository(database, create=False)
                    barrier.wait()
                try:
                    results.append(json.loads(local.admit_review_case(proposal)))
                finally: local.close()
            threads = [threading.Thread(target=admit) for _ in range(2)]
            [thread.start() for thread in threads]; [thread.join() for thread in threads]
            self.assertEqual(2, len(results))
            self.assertEqual(1, len({value["review_case_id"] for value in results}))

    def test_transition_failure_rolls_back_case_and_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
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

    def test_corrupt_case_is_redacted_and_unrelated_history_survives(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            repository.publish(report, lambda: True); proposal = self._actionable(report); repository.publish_proposal(proposal)
            case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
            self.assertEqual(proposal.to_json(), repository.get_proposal(proposal.proposal_set_id))
            repository._connection.execute("DROP TRIGGER review_cases_no_update")
            repository._connection.execute("UPDATE review_cases SET document='{}' WHERE review_case_id=?", (case_id,))
            with self.assertRaises(Exception): repository.get_review_case(case_id)
            self.assertEqual(proposal.to_json(), repository.get_proposal(proposal.proposal_set_id))
            repository.close()

    def test_exact_current_backup_restore_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository = open_repository(database); repository.close()
            backup = Path(directory) / "backup.sqlite"; backup.write_bytes(database.read_bytes())
            restored = open_repository(backup, create=False); restored.close()

    def test_malformed_current_pointer_is_not_projected_as_historical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            repository.publish(report, lambda: True); proposal = self._actionable(report); repository.publish_proposal(proposal)
            case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
            repository._connection.execute("PRAGMA foreign_keys=OFF")
            repository._connection.execute("UPDATE current_review_case_lineages SET review_case_id='bad' WHERE item_id='item-1'")
            repository._connection.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(Exception): repository.get_review_case(case_id)
            repository.close()

    def test_read_only_database_is_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository = open_repository(database); repository.close()
            os.chmod(database, 0o400)
            try:
                with self.assertRaises(Exception): open_repository(database, create=False)
            finally:
                os.chmod(database, 0o600)

    def test_held_immediate_lock_serializes_identical_admission(self) -> None:
        if sys.platform.startswith("linux"):
            self.skipTest("Linux serializes repository processes at the binding lock")
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository = open_repository(database)
            report = _report()
            repository.publish(report, lambda: True); proposal = self._actionable(report); repository.publish_proposal(proposal)
            repository._connection.execute("BEGIN IMMEDIATE")
            result = []
            def admit() -> None:
                local = open_repository(database, create=False)
                try: result.append(json.loads(local.admit_review_case(proposal))["status"])
                finally: local.close()
            thread = threading.Thread(target=admit); thread.start()
            import time; time.sleep(0.05)
            repository._connection.commit(); thread.join()
            self.assertEqual(["admitted"], result)
            repository.close()

    def test_disk_full_equivalent_rolls_back_every_review_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_repository(Path(directory) / "reports.sqlite")
            report = _report()
            repository.publish(report, lambda: True); proposal = self._actionable(report); repository.publish_proposal(proposal)
            repository._connection.execute("CREATE TRIGGER injected_full BEFORE INSERT ON review_case_transitions BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END")
            with self.assertRaises(Exception): repository.admit_review_case(proposal)
            for table in ("review_cases", "review_case_transitions", "current_review_case_lineages"):
                self.assertEqual((0,), repository._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone())
            repository.close()


if __name__ == "__main__":
    unittest.main()
