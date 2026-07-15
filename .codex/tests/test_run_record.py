from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_record  # noqa: E402


FINAL = "b" * 40
TIME = "2026-07-16T00:00:00Z"


def qualified_record() -> dict:
    return {
        "schema_version": 1,
        "run_id": "focused-example",
        "workflow": "deliver-focused-change",
        "tier": 1,
        "status": "AWAITING_EXTERNAL",
        "risk_triggers": [],
        "target": {"kind": "workflow", "id": "controls", "base_ref": "a" * 40, "final_ref": FINAL},
        "writer": {"profile": "root", "thread_id": None, "started_at": TIME, "ended_at": TIME},
        "checks": [
            {"name": "control tests", "command": "python3 -m unittest", "result": "PASS", "head_ref": FINAL, "observed_at": TIME}
        ],
        "reviews": [
            {
                "tasks": ["acceptance", "code_quality"],
                "profile": "reviewer",
                "thread_id": "review-thread-1",
                "context": "FRESH",
                "origin_thread_id": None,
                "packet_digest": "c" * 64,
                "result": "PASS",
                "head_ref": FINAL,
                "observed_at": TIME,
                "finding_ids": [],
            }
        ],
        "findings": [],
        "connector": {"round": "C1", "anchor": "PR description", "state": "IN_PROGRESS", "requested_head": FINAL, "observed_at": TIME},
        "delivery": {"kind": "PULL_REQUEST", "url": "https://example.invalid/pr/1", "head_ref": FINAL, "observed_at": TIME},
        "notes": ["runtime fields are operator attestations"],
    }


class RunRecordTests(unittest.TestCase):
    def test_minimal_awaiting_external_record_qualifies(self) -> None:
        run_record.validate_record(qualified_record(), qualified=True)

    def test_failed_or_stale_check_does_not_qualify(self) -> None:
        failed = qualified_record()
        failed["checks"][0]["result"] = "FAIL"
        with self.assertRaisesRegex(run_record.RecordError, "checks are failed"):
            run_record.validate_record(failed, qualified=True)
        stale = qualified_record()
        stale["checks"][0]["head_ref"] = "d" * 40
        with self.assertRaisesRegex(run_record.RecordError, "checks are failed"):
            run_record.validate_record(stale, qualified=True)

    def test_missing_required_review_task_does_not_qualify(self) -> None:
        record = qualified_record()
        record["reviews"][0]["tasks"] = ["acceptance"]
        with self.assertRaisesRegex(run_record.RecordError, "required review tasks"):
            run_record.validate_record(record, qualified=True)

    def test_unresolved_blocking_finding_does_not_qualify(self) -> None:
        record = qualified_record()
        record["findings"] = [{"id": "finding-1", "summary": "real blocker", "blocking": True, "resolved": False}]
        record["reviews"][0]["finding_ids"] = ["finding-1"]
        with self.assertRaisesRegex(run_record.RecordError, "unresolved blocking"):
            run_record.validate_record(record, qualified=True)

    def test_tier_two_requires_safety_and_independent_challenger(self) -> None:
        record = qualified_record()
        record["tier"] = 2
        record["risk_triggers"] = ["security boundary"]
        with self.assertRaisesRegex(run_record.RecordError, "independent challenger"):
            run_record.validate_record(record, qualified=True)
        record["reviews"].extend(
            [
                {
                    "tasks": ["safety"], "profile": "reviewer", "thread_id": "safety-thread",
                    "context": "FRESH", "origin_thread_id": None, "packet_digest": "d" * 64,
                    "result": "PASS", "head_ref": FINAL, "observed_at": TIME, "finding_ids": [],
                },
                {
                    "tasks": ["publication_challenge"], "profile": "challenger", "thread_id": "challenge-thread",
                    "context": "FRESH", "origin_thread_id": None, "packet_digest": "e" * 64,
                    "result": "PASS", "head_ref": FINAL, "observed_at": TIME, "finding_ids": [],
                },
            ]
        )
        run_record.validate_record(record, qualified=True)

    def test_targeted_review_must_reuse_origin_thread(self) -> None:
        record = qualified_record()
        review = record["reviews"][0]
        review["context"] = "TARGETED"
        review["origin_thread_id"] = "another-thread"
        with self.assertRaisesRegex(run_record.RecordError, "targeted review changed thread"):
            run_record.validate_record(record)

    def test_connector_variance_never_qualifies(self) -> None:
        record = qualified_record()
        record["connector"]["state"] = "PROTOCOL_VARIANCE"
        record["status"] = "DELIVERED"
        with self.assertRaisesRegex(run_record.RecordError, "connector protocol"):
            run_record.validate_record(record, qualified=True)


if __name__ == "__main__":
    unittest.main()
