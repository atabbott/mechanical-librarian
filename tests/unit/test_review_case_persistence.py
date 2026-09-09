import json
import os
import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.domain.cover_evidence import (CoverArtifact, CoverDesignation, CoverProvenance, CoverSource, CoverSourceKind, CoverSourceState, cover_source_id)
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
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
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionIncompatibleError,
    ReviewAdmissionResult,
    ReviewAdmissionStaleError,
    ReviewCase,
    ReviewCaseValueError,
    ReviewTransition,
)
from tests.support.proposal_evidence import _report as _cover_report
from tests.support.review_cases import _report

_CASE_ID = "review_case_" + "c" * 64


def _actionable(report: MetadataReport) -> ProposalSet:
    proposal = evaluate_report(report)
    assert any(
        result.outcome
        in {
            ProposalOutcome.PROPOSE_CHANGE,
            ProposalOutcome.UNRESOLVED_CHOICE,
        }
        for result in proposal.results
    )
    return proposal


def test_partial_human_decision_remains_current_unapplied() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        proposal = evaluate_report(report)
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        field = next(
            value
            for value in case.proposals
            if value["result"]["outcome"] == "unresolved_choice"
        )
        repository.record_human_decision(
            HumanDecision.create(
                case,
                proposal_id=field["proposal_id"],
                target_fingerprint=case.target_fingerprint,
                destination=field["destination"],
                actor="owner",
                outcome="keep_current",
                selected_value=None,
                idempotency_key="partial-count-0001",
                occurred_at=1_000,
            )
        )
        assert 1 == repository.current_unapplied_count("ebooks")
        repository.close()


def test_nondefault_resolved_policy_replays_admits_reopens_and_browses() -> None:
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
        assert "admitted" == admitted["status"]
        case = json.loads(repository.get_review_case(admitted["review_case_id"]))
        assert proposal.proposal_set_id == case["case"]["proposal_set_id"]
        assert original_report == repository.get(report.report_id)
        browsed = repository.list_current_review_case_documents(None, 50, 0, None)
        assert 1 == len(browsed)
        repository.close()


def test_case_parser_rejects_duplicate_and_oversized_documents() -> None:
    with pytest.raises(ReviewCaseValueError):
        ReviewCase.from_json(
            '{"schema_version":"review-case-v1","schema_version":"review-case-v1"}'
        )
    with pytest.raises(ReviewCaseValueError):
        ReviewCase.from_json("x" * 2_097_153)


def test_admission_result_requires_canonical_closed_shape() -> None:
    value = ReviewAdmissionResult("no_case", None)
    assert value == ReviewAdmissionResult.from_json(value.to_json())
    with pytest.raises(ReviewCaseValueError):
        ReviewAdmissionResult.from_json('{"status":"no_case"}')


def test_transition_has_deterministic_identity() -> None:
    transition = ReviewTransition(
        "primary",
        "ebooks",
        "item-1",
        1,
        "proposal_set_" + "a" * 64,
        "report_" + "b" * 64,
        "no_case",
        None,
        None,
        "nonactionable",
    )
    assert transition.transition_id.startswith("review_transition_")
    with pytest.raises(ReviewCaseValueError):
        ReviewTransition(
            "primary",
            "ebooks",
            "item-1",
            0,
            "x",
            "y",
            "admitted",
            None,
            None,
            "actionable",
        )


@pytest.mark.parametrize(
    ("kind", "predecessor", "successor", "reason"),
    (
        ("admitted", _CASE_ID, _CASE_ID, "actionable"),
        ("no_case", _CASE_ID, None, "nonactionable"),
        ("superseded", None, _CASE_ID, "actionable"),
        ("retired", None, None, "nonactionable"),
    ),
    ids=("admitted", "no_case", "superseded", "retired"),
)
def test_transition_rejects_impossible_predecessor_pointers(
    kind, predecessor, successor, reason
) -> None:
    with pytest.raises(ReviewCaseValueError):
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


def test_case_projections_are_detached_from_content_identity() -> None:
    report = _report()
    case = ReviewCase.create(_actionable(report), report)
    baseline = case.to_json()

    case.target["item"] = "mutated"
    case.results[0]["outcome"] = "mutated"
    case.target["fields"].append("mutated")

    data = case.to_data()
    data["target"]["item"] = "mutated"
    data["results"][0]["outcome"] = "mutated"
    proposals = case.proposals
    proposals[0]["result"]["outcome"] = "mutated"
    actionable = case.actionable
    actionable[0]["outcome"] = "mutated"

    source = json.loads(baseline)
    restored = ReviewCase.create_from_data(source)
    source["target"]["item"] = "mutated"
    source["results"][0]["outcome"] = "mutated"

    assert baseline == case.to_json()
    assert baseline == restored.to_json()
    assert case.review_case_id == restored.review_case_id


def test_nonactionable_set_retires_without_creating_case_and_replays() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report(actionable=False)
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        first = repository.admit_review_case(proposal)
        replay = repository.admit_review_case(proposal)
        assert "no_case" == json.loads(first)["status"]
        assert "no_case" == json.loads(replay)["status"]
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_cases"
        ).fetchone()
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_case_transitions"
        ).fetchone()
        repository.close()


def test_transition_kind_tracks_an_active_prior_case() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        outcomes: list[str] = []

        first = _report(actionable=False)
        repository.publish(first, lambda: True)
        first_set = evaluate_report(first)
        repository.publish_proposal(first_set)
        outcomes.append(json.loads(repository.admit_review_case(first_set))["status"])

        second = _report("Second", "c", "d")
        repository.publish(second, lambda: True)
        second_set = _actionable(second)
        repository.publish_proposal(second_set)
        outcomes.append(json.loads(repository.admit_review_case(second_set))["status"])

        third = _report("Third", "e", "f", actionable=False)
        repository.publish(third, lambda: True)
        third_set = evaluate_report(third)
        repository.publish_proposal(third_set)
        outcomes.append(json.loads(repository.admit_review_case(third_set))["status"])

        fourth = _report("Fourth", "0", "1", actionable=False)
        repository.publish(fourth, lambda: True)
        fourth_set = evaluate_report(fourth)
        repository.publish_proposal(fourth_set)
        outcomes.append(json.loads(repository.admit_review_case(fourth_set))["status"])

        transitions = [
            row[0]
            for row in repository._connection.execute(
                "SELECT kind FROM review_case_transitions ORDER BY checkpoint"
            ).fetchall()
        ]
        repository.close()

    assert ["no_case", "admitted", "retired", "no_case"] == outcomes
    assert outcomes == transitions


def test_actionable_set_creates_complete_case_and_exact_get() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        opened = json.loads(repository.get_review_case(admitted["review_case_id"]))
        assert "admitted" == admitted["status"]
        assert repository.review_case_exists(admitted["review_case_id"])
        assert not repository.review_case_exists("review_case_" + "f" * 64)
        assert opened["current"]
        assert 16 == len(opened["case"]["results"])
        repository.close()


def test_successor_makes_prior_case_historical() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        first = _report()
        repository.publish(first, lambda: True)
        first_set = _actionable(first)
        repository.publish_proposal(first_set)
        first_case = json.loads(repository.admit_review_case(first_set))[
            "review_case_id"
        ]
        successor = _report("Changed", "c", "d")
        repository.publish(successor, lambda: True)
        successor_set = _actionable(successor)
        repository.publish_proposal(successor_set)
        successor_case = json.loads(repository.admit_review_case(successor_set))[
            "review_case_id"
        ]
        assert not json.loads(repository.get_review_case(first_case))["current"]
        assert json.loads(repository.get_review_case(successor_case))["current"]
        repository.close()


def test_newer_report_immediately_makes_case_historical() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        first = _report()
        repository.publish(first, lambda: True)
        proposal = _actionable(first)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        newer = _report("New", "c", "d")
        repository.publish(newer, lambda: True)
        assert not json.loads(repository.get_review_case(case_id))["current"]
        repository.close()


def test_current_review_case_successor_reads_only_the_rightful_current_case() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        first = _report()
        repository.publish(first, lambda: True)
        first_set = _actionable(first)
        repository.publish_proposal(first_set)
        predecessor = json.loads(repository.admit_review_case(first_set))[
            "review_case_id"
        ]
        successor_report = _report("Changed", "c", "d")
        repository.publish(successor_report, lambda: True)
        successor_set = _actionable(successor_report)
        repository.publish_proposal(successor_set)
        successor = json.loads(repository.admit_review_case(successor_set))[
            "review_case_id"
        ]

        assert repository.current_review_case_successor(predecessor) == successor
        assert repository.current_review_case_successor(successor) is None
        with pytest.raises(MetadataReportError):
            repository.current_review_case_successor("review_case_" + "0" * 64)
        with pytest.raises(MetadataReportError):
            repository.current_review_case_successor("review_case_" + "0" * 65)

        repository._connection.execute("PRAGMA foreign_keys=OFF")
        repository._connection.execute(
            "UPDATE current_review_case_lineages SET review_case_id=?", ("x" * 129,)
        )
        repository._connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(MetadataReportError):
            repository.current_review_case_successor(predecessor)
        repository.close()


def test_current_review_case_successor_rejects_a_wrong_target_identity() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        predecessor = json.loads(repository.admit_review_case(proposal))[
            "review_case_id"
        ]
        other_report = MetadataReport.create(
            target=replace(report.target, item="item-2"),
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
        )
        repository.publish(other_report, lambda: True)
        other_set = _actionable(other_report)
        repository.publish_proposal(other_set)
        other_case = json.loads(repository.admit_review_case(other_set))[
            "review_case_id"
        ]
        repository._connection.execute("PRAGMA foreign_keys=OFF")
        repository._connection.execute(
            "UPDATE current_review_case_lineages SET review_case_id=? WHERE item_id='item-1'",
            (other_case,),
        )
        repository._connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(MetadataReportError):
            repository.current_review_case_successor(predecessor)
        repository.close()


def test_nonactionable_successor_retires_current_case_without_empty_case() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        first = _report()
        repository.publish(first, lambda: True)
        first_set = _actionable(first)
        repository.publish_proposal(first_set)
        case_id = json.loads(repository.admit_review_case(first_set))["review_case_id"]
        successor = _report("Changed", "c", "d", actionable=False)
        repository.publish(successor, lambda: True)
        successor_set = evaluate_report(successor)
        repository.publish_proposal(successor_set)
        assert (
            "retired"
            == json.loads(repository.admit_review_case(successor_set))["status"]
        )
        assert not json.loads(repository.get_review_case(case_id))["current"]
        assert (1,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_cases"
        ).fetchone()
        repository.close()


def test_stale_and_cross_target_proposals_mutate_nothing() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        first = _report()
        repository.publish(first, lambda: True)
        stale = _actionable(first)
        repository.publish_proposal(stale)
        current = _report("Changed", "c", "d")
        repository.publish(current, lambda: True)
        with pytest.raises(ReviewAdmissionStaleError):
            repository.admit_review_case(stale)
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_cases"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_case_transitions"
        ).fetchone()
        repository.close()


def test_synthetic_evaluator_output_is_incompatible_and_mutates_nothing() -> None:
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
        with pytest.raises(ReviewAdmissionIncompatibleError):
            repository.admit_review_case(synthetic)
        for table in (
            "review_cases",
            "review_case_transitions",
            "current_review_case_lineages",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
        repository.close()


def test_target_incompatible_nonactionable_set_mutates_nothing() -> None:
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
        with pytest.raises(ReviewAdmissionIncompatibleError):
            repository.admit_review_case(incompatible)
        for table in (
            "review_cases",
            "review_case_transitions",
            "current_review_case_lineages",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
        repository.close()


def test_replay_uses_current_checkpoint_outcome_not_transition_hash() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        first = _report(actionable=False)
        repository.publish(first, lambda: True)
        first_set = evaluate_report(first)
        repository.publish_proposal(first_set)
        assert (
            "no_case" == json.loads(repository.admit_review_case(first_set))["status"]
        )

        successor = _report("Changed", "c", "d")
        repository.publish(successor, lambda: True)
        successor_set = _actionable(successor)
        repository.publish_proposal(successor_set)
        repository.admit_review_case(successor_set)

        repository.publish(
            first,
            lambda: True,
            expected_current=successor.report_id,
        )
        assert (
            "retired" == json.loads(repository.admit_review_case(first_set))["status"]
        )
        assert (
            "retired" == json.loads(repository.admit_review_case(first_set))["status"]
        )
        repository.close()


def test_impossible_transition_pointer_combination_fails_closed() -> None:
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
        repository._connection.execute("DROP TRIGGER review_case_transitions_no_update")
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
        with pytest.raises(MetadataReportError):
            repository.admit_review_case(proposal)
        repository.close()


def test_concurrent_identical_admission_converges() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reports.sqlite"
        owner = open_repository(database)
        report = _report()
        owner.publish(report, lambda: True)
        proposal = _actionable(report)
        owner.publish_proposal(proposal)
        barrier, results = threading.Barrier(2), []
        failures: list[Exception] = []

        def admit() -> None:
            local = None
            try:
                local = open_repository(
                    database, create=False, authority=owner.authority
                )
                barrier.wait(timeout=5)
                results.append(json.loads(local.admit_review_case(proposal)))
            except Exception as error:
                failures.append(error)
            finally:
                if local is not None:
                    local.close()

        try:
            threads = [threading.Thread(target=admit) for _ in range(2)]
            # The comprehensions are used for their effect; the collected `None`
            # results are discarded.
            [thread.start() for thread in threads]  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
            [thread.join() for thread in threads]  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
            assert [] == failures
            assert 2 == len(results)
            assert 1 == len({value["review_case_id"] for value in results})
        finally:
            owner.close()


def test_transition_failure_rolls_back_case_and_pointer() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        repository._connection.execute(
            "CREATE TRIGGER injected_review_transition_failure BEFORE INSERT ON review_case_transitions BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        with pytest.raises(MetadataReportError):
            repository.admit_review_case(proposal)
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_cases"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM current_review_case_lineages"
        ).fetchone()
        assert (0,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_case_transitions"
        ).fetchone()
        repository._connection.execute(
            "DROP TRIGGER injected_review_transition_failure"
        )
        assert (
            "admitted" == json.loads(repository.admit_review_case(proposal))["status"]
        )
        repository.close()


def test_exact_current_schema_restarts_and_prior_schema_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reports.sqlite"
        repository = open_repository(database)
        repository.close()
        reopened = open_repository(database, create=False)
        reopened.close()
        old = Path(directory) / "old.sqlite"
        old.write_bytes(database.read_bytes())
        import sqlite3

        connection = sqlite3.connect(old)
        connection.execute("DROP TABLE review_case_transitions")
        connection.commit()
        connection.close()
        before = old.read_bytes()
        with pytest.raises(MetadataReportError):
            open_repository(old, create=False)
        assert before == old.read_bytes()


def test_corrupt_case_is_redacted_and_unrelated_history_survives() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        assert proposal.to_json() == repository.get_proposal(proposal.proposal_set_id)
        repository._connection.execute("DROP TRIGGER review_cases_no_update")
        repository._connection.execute(
            "UPDATE review_cases SET document='{}' WHERE review_case_id=?", (case_id,)
        )
        with pytest.raises(MetadataReportError):
            repository.get_review_case(case_id)
        assert proposal.to_json() == repository.get_proposal(proposal.proposal_set_id)
        repository.close()


def test_exact_current_backup_restore_reopens() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reports.sqlite"
        repository = open_repository(database)
        repository.close()
        backup = Path(directory) / "backup.sqlite"
        backup.write_bytes(database.read_bytes())
        restored = open_repository(backup, create=False)
        restored.close()


def test_malformed_current_pointer_is_not_projected_as_historical() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        repository._connection.execute("PRAGMA foreign_keys=OFF")
        repository._connection.execute(
            "UPDATE current_review_case_lineages SET review_case_id='bad' WHERE item_id='item-1'"
        )
        repository._connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(MetadataReportError):
            repository.get_review_case(case_id)
        repository.close()


def test_read_only_database_is_rejected_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reports.sqlite"
        repository = open_repository(database)
        repository.close()
        os.chmod(database, 0o400)
        try:
            with pytest.raises(MetadataReportError):
                open_repository(database, create=False)
        finally:
            os.chmod(database, 0o600)


def test_held_immediate_lock_serializes_identical_admission() -> None:
    if sys.platform.startswith("linux"):
        pytest.skip("Linux serializes repository processes at the binding lock")
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reports.sqlite"
        repository = open_repository(database)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        repository._connection.execute("BEGIN IMMEDIATE")
        result = []

        def admit() -> None:
            local = open_repository(database, create=False)
            try:
                result.append(json.loads(local.admit_review_case(proposal))["status"])
            finally:
                local.close()

        thread = threading.Thread(target=admit)
        thread.start()
        import time

        time.sleep(0.05)
        repository._connection.commit()
        thread.join()
        assert ["admitted"] == result
        repository.close()


def test_disk_full_equivalent_rolls_back_every_review_row() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        repository._connection.execute(
            "CREATE TRIGGER injected_full BEFORE INSERT ON review_case_transitions BEGIN SELECT RAISE(ABORT, 'database or disk is full'); END"
        )
        with pytest.raises(MetadataReportError):
            repository.admit_review_case(proposal)
        for table in (
            "review_cases",
            "review_case_transitions",
            "current_review_case_lineages",
        ):
            assert (0,) == repository._connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
        repository.close()


def test_retrieved_inventaire_proposal_reopens_admits_and_browses(tmp_path: Path) -> None:
    artifact = CoverArtifact.create(b"cover", b"preview", "image/png", 10, 20)
    source = CoverSource(
        cover_source_id(CoverSourceKind.INVENTAIRE, "synthetic-cover"),
        CoverSourceKind.INVENTAIRE, CoverSourceState.RETRIEVED, artifact,
        provenance=CoverProvenance("inventaire", CoverDesignation.WORK, "inventaire-work-wd-Q1"),
    )
    report = _cover_report(covers=(source,))
    original = report.to_json()
    path = tmp_path / "reports.sqlite"
    repository = open_repository(path)
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    repository.close()
    repository = open_repository(path)
    reopened = ProposalSet.from_json(repository.get_proposal(proposal.proposal_set_id))
    assert proposal == reopened
    alternative = next(result for result in reopened.results if result.destination.value == "cover").alternatives[0]
    assert (source.source_id,) == alternative.evidence_references
    assert artifact.artifact_id == alternative.value.value
    admitted = json.loads(repository.admit_review_case(reopened))
    assert admitted["status"] == "admitted"
    case = json.loads(repository.get_review_case(admitted["review_case_id"]))
    assert proposal.proposal_set_id == case["case"]["proposal_set_id"]
    assert len(repository.list_current_review_case_documents(None, 50, 0, None)) == 1
    assert original == repository.get(report.report_id)
    repository.close()
