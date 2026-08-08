from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_policy import (
    MAX_SHADOW_SAMPLE_RESPONSE_BYTES,
    PolicyNotebook,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from tests.unit.test_review_case_persistence import _report


def _case(repository):
    report = _report()
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    repository.publish_proposal(proposal)
    admitted = json.loads(repository.admit_review_case(proposal))
    return admitted["review_case_id"]


def _digest(value: object) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _maximum_reference_outcome(repository) -> ShadowOutcomeSet:
    case_id = _case(repository)
    repository.enter_shadow_preview(1)
    base = ShadowOutcomeSet.from_json(
        repository.persist_shadow_outcome(case_id, 2)
    )
    citations = tuple(f"citation_{index:064x}" for index in range(256))
    combinations = tuple(f"combination_{index:064x}" for index in range(256))
    fields = []
    for field in base.outcomes:
        identity = field._identity_data()
        identity["citation_ids"] = list(citations)
        identity["combination_ids"] = list(combinations)
        fields.append(
            replace(
                field,
                outcome_id="shadow_outcome_" + _digest(identity),
                citation_ids=citations,
                combination_ids=combinations,
            )
        )
    identity = base._identity_data()
    identity["outcomes"] = [field.to_data() for field in fields]
    return replace(
        base,
        outcome_set_id="shadow_outcome_set_" + _digest(identity),
        outcomes=tuple(fields),
    )


class ShadowOutcomePersistenceTests(unittest.TestCase):
    def test_review_posture_is_inactive_and_preview_replays_one_exact_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "reports.sqlite"
            repository = open_repository(path)
            case_id = _case(repository)

            self.assertEqual("review", repository.shadow_posture())
            self.assertEqual("inactive", repository.persist_shadow_outcome(case_id, 10))
            self.assertEqual((), repository.list_shadow_outcomes(50))

            self.assertEqual("preview", repository.enter_shadow_preview(11))
            first = repository.persist_shadow_outcome(case_id, 12)
            second = repository.persist_shadow_outcome(case_id, 13)
            self.assertEqual(first, second)
            outcome = ShadowOutcomeSet.from_json(first)
            self.assertEqual("none", outcome.authority)
            self.assertEqual("preview", outcome.posture)
            self.assertEqual(case_id, outcome.review_case_id)
            self.assertEqual({"manual"}, {value.disposition for value in outcome.outcomes})
            self.assertTrue(
                all(value.qualification_candidate_id.startswith("qualification_candidate_") for value in outcome.outcomes)
            )
            self.assertEqual(
                len(outcome.outcomes),
                len({value.qualification_candidate_id for value in outcome.outcomes}),
            )
            self.assertEqual(((first, 12),), repository.list_shadow_outcomes(50))
            history = repository.get_review_case_history(case_id, 50)
            self.assertEqual("shadow_preview_recorded", history[0]["kind"])
            self.assertEqual("none", history[0]["authority"])
            repository.close()

            connection = sqlite3.connect(path)
            self.assertEqual(
                (1,),
                connection.execute(
                    "SELECT COUNT(*) FROM shadow_decision_outcomes"
                ).fetchone(),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE shadow_decision_outcomes SET evaluated_at=99"
                )
            connection.close()

    def test_shadow_history_orders_human_and_preview_events_by_time(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            repository.enter_shadow_preview(1)
            repository.persist_shadow_outcome(case_id, 5)
            # A second deterministic set is a replay, so it cannot manufacture a
            # newer chronology merely because delivery repeats.
            repository.persist_shadow_outcome(case_id, 50)
            events = repository.get_review_case_history(case_id, 50)
            shadow = [value for value in events if value["kind"] == "shadow_preview_recorded"]
            self.assertEqual([5], [value["occurred_at"] for value in shadow])
            repository.close()

    def test_sample_bounds_limit_fifty_at_maximum_reference_cardinality(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            outcome = _maximum_reference_outcome(repository)
            repository.close()

        class SampleRepository:
            def list_shadow_outcomes(self, limit):
                return tuple((outcome.to_json(), index) for index in range(limit))

        sample = PolicyNotebook(SampleRepository(), (), {}).shadow_sample(50)
        serialized = json.dumps(
            sample, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        self.assertGreater(len(sample["items"]), 0)
        self.assertLess(len(sample["items"]), 50)
        self.assertLessEqual(len(serialized), MAX_SHADOW_SAMPLE_RESPONSE_BYTES)
        first = sample["items"][0]["outcomes"][0]
        self.assertEqual(256, len(first["citation_ids"]))
        self.assertEqual(256, len(first["combination_ids"]))

    def test_malformed_persisted_shadow_history_fails_without_partial_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            case_id = _case(repository)
            repository.enter_shadow_preview(1)
            repository.persist_shadow_outcome(case_id, 2)
            repository._connection.execute(
                "DROP TRIGGER shadow_decision_outcomes_no_update"
            )
            repository._connection.execute(
                "UPDATE shadow_decision_outcomes SET document='{}'"
            )
            repository._connection.commit()
            with self.assertRaisesRegex(Exception, "review history is unavailable"):
                repository.get_review_case_history(case_id, 50)
            repository.close()

    def test_stale_running_projection_restarts_the_same_row_for_current_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = open_repository(Path(temporary) / "reports.sqlite")
            old_case_id = _case(repository)
            old_case = json.loads(repository.get_review_case(old_case_id))["case"]
            current_report = _report("Current title")
            repository.publish(
                current_report, lambda: True, old_case["report_id"]
            )
            current_proposal = evaluate_report(current_report)
            repository.publish_proposal(current_proposal)
            current_case_id = json.loads(
                repository.admit_review_case(current_proposal)
            )["review_case_id"]
            connection = repository._connection
            scan_id = "review_scan_" + ("1" * 64)
            work_id = "review_work_" + ("2" * 64)
            connection.execute(
                "INSERT INTO review_preparation_scans(scan_id,connection_name,library_name,library_id,producer_fingerprint,state,requested_at,updated_at,catalogue_generation,catalogue_fingerprint,total) VALUES(?,?,?,?,?,'processing',1,1,1,?,1)",
                (scan_id, "primary", "ebooks", "library-1", "3" * 64, "4" * 64),
            )
            connection.execute(
                "INSERT INTO review_preparation_work(work_id,scan_id,ordinal,connection_name,library_name,library_id,item_id,file_identity,source_revision,source_fingerprint,producer_fingerprint,catalogue_generation,catalogue_fingerprint,stage,state,attempt,recovery_attempt,report_id,proposal_set_id,review_case_id,failure_code,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'projection','running',1,0,?,?,?,NULL,1)",
                (
                    work_id,
                    scan_id,
                    0,
                    "primary",
                    "ebooks",
                    "library-1",
                    "item-1",
                    old_case["file_identity"],
                    1,
                    "5" * 64,
                    "3" * 64,
                    1,
                    "4" * 64,
                    old_case["report_id"],
                    old_case["proposal_set_id"],
                    old_case_id,
                ),
            )
            connection.commit()
            repository.enter_shadow_preview(2)

            self.assertEqual("stale", repository.persist_shadow_outcome(old_case_id, 3))
            self.assertEqual((), repository.list_shadow_outcomes(50))
            self.assertEqual(
                ("proposal", "queued", current_report.report_id, None, None),
                connection.execute(
                    "SELECT stage,state,report_id,proposal_set_id,review_case_id FROM review_preparation_work WHERE work_id=?",
                    (work_id,),
                ).fetchone(),
            )
            self.assertNotEqual(old_case_id, current_case_id)
            repository.close()


if __name__ == "__main__":
    unittest.main()
