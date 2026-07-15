from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CODEX = Path(__file__).resolve().parents[1]
SCRIPTS = CODEX / "scripts"
SCRIPT = SCRIPTS / "run_record.py"
sys.path.insert(0, str(SCRIPTS))

import validate_team as validation  # noqa: E402


STARTED = "2026-07-15T10:00:00Z"
BASE_REF = "a" * 40
FINAL_REF = "b" * 40
HEAD_TREE = "c" * 64
PACKET_DIGEST = "d" * 64


class RunRecordCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.team = validation.validate_team()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.record_path = self.root / "team-run.json"
        self.record = self.base_record()
        self.write_record()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def environment(self) -> dict:
        payload = {
            "kind": "LOCAL",
            "platform": "linux-x86_64",
            "runtime": "CPython 3.13",
            "container_image": None,
            "lock_digest": "e" * 64,
        }
        return {**payload, "digest": validation.canonical_fingerprint(payload)}

    def connector_snapshot(
        self,
        name: str,
        observed_at: str,
        elapsed: float,
        events: list[dict],
        *,
        pickup: float | None = None,
        terminal: float | None = None,
    ) -> Path:
        state, variances = validation.connector_protocol.derive_state(events, elapsed)
        report = {
            "schema_version": 1,
            "observed_at": observed_at,
            "repo": "owner/repo",
            "pr": 1,
            "round": "C1",
            "state": state,
            "protocol_variances": variances,
            "request": {
                "kind": "AUTOMATIC_PR_OPEN",
                "requested_at": "2026-07-15T10:00:04.500000Z",
                "target_kind": "PR_DESCRIPTION",
                "target_id": 1,
                "trigger_normalized": None,
            },
            "heads": {
                "opening": FINAL_REF,
                "requested": FINAL_REF,
                "current": FINAL_REF,
                "current_matches_requested": True,
            },
            "latency_seconds": {
                "observed": elapsed,
                "pickup": pickup,
                "terminal": terminal,
                "off_target_terminal": None,
            },
            "ids": {
                "reaction_ids": [item["id"] for item in events],
                "review_ids": [],
                "result_comment_ids": [],
            },
            "events": events,
        }
        path = self.root / f"{name}.json"
        path.write_bytes(validation.canonical_json_bytes(report))
        return path

    def binding(self, role: str) -> dict:
        declaration = next(
            item
            for item in [
                self.record["team_context"]["root"],
                *self.record["team_context"]["workflow_roles"],
            ]
            if item["role"] == role
        )
        return {
            "binding_id": f"binding-{role}",
            "target_id": "target-1",
            "ref": FINAL_REF,
            "requested_role": role,
            "thread_id": f"thread-{role}",
            "role": {"value": role, "state": "VERIFIED", "provenance": "test runtime"},
            "model": {"value": declaration["model"], "state": "VERIFIED", "provenance": "test runtime"},
            "reasoning": {"value": declaration["reasoning"], "state": "VERIFIED", "provenance": "test runtime"},
            "permissions": {"value": declaration["permissions"], "state": "VERIFIED", "provenance": "test runtime"},
            "capability_evidence": {
                "verified": list(declaration["capabilities"]),
                "absent": list(declaration["forbidden_capabilities"]),
                "provenance": "test capability receipt",
            },
        }

    def base_record(self) -> dict:
        record = validation.initial_record(
            "cli-fixture",
            "deliver-focused-change",
            "ticket",
            "251",
            BASE_REF,
            self.team,
            STARTED,
        )
        self.record = record
        environment = self.environment()
        record["targets"][0]["final_ref"] = FINAL_REF
        record["repository"].update(
            {"base_tree": "f" * 64, "head_ref": FINAL_REF, "head_tree": HEAD_TREE}
        )
        record["artifacts"].append(
            {
                "id": "artifact-repository",
                "version": 1,
                "target_id": "target-1",
                "ref": FINAL_REF,
                "kind": "REPOSITORY",
                "identifier": "mechanical-librarian",
                "digest": HEAD_TREE,
                "built_at": "2026-07-15T10:00:01Z",
                "environment_digest": environment["digest"],
            }
        )
        record["bindings"] = [
            self.binding("root"),
            self.binding("focused_worker"),
        ]
        record["skip_manifest"]["entries"].append(
            {
                "id": "skip-criterion",
                "version": 1,
                "target_id": "target-1",
                "ref": FINAL_REF,
                "obligation_kind": "CRITERIA",
                "role": None,
                "test_id": "criterion-na",
                "platform": "all",
                "reason": "criterion is outside this fixture",
                "owning_gate": "CRITERIA",
                "authority": "test contract",
                "at": "2026-07-15T10:00:01Z",
                "evidence_state": "NOT_APPLICABLE",
            }
        )
        return record

    def write_record(self) -> None:
        validation.validate_record(self.record, self.team)
        self.record_path.write_text(json.dumps(self.record, indent=2) + "\n", encoding="utf-8")

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(self.record_path), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def loaded(self) -> dict:
        record = json.loads(self.record_path.read_text(encoding="utf-8"))
        validation.validate_record(record, self.team)
        return record

    def assert_atomic_failure(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        before = self.record_path.read_bytes()
        result = self.invoke(*arguments)
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertEqual(before, self.record_path.read_bytes())
        self.assertNotIn("Traceback", result.stderr)
        return result

    def test_v3_check_and_criterion_commands_emit_exact_valid_shapes(self) -> None:
        environment_json = json.dumps(self.environment(), separators=(",", ":"))
        result = self.invoke(
            "check",
            "PASS",
            "--id",
            "check-full",
            "--target-id",
            "target-1",
            "--ref",
            FINAL_REF,
            "--kind",
            "FULL_GATE",
            "--command-text",
            "python3 -m unittest",
            "--started-at",
            "2026-07-15T10:00:02Z",
            "--ended-at",
            "2026-07-15T10:00:03Z",
            "--artifact-id",
            "artifact-repository",
            "--environment-json",
            environment_json,
            "--invalidation-rule",
            "head, artifact, environment, command, or policy changes",
            "--validity",
            "FRESH",
            "--summary",
            "full gate passed",
        )
        self.assertEqual(0, result.returncode, result.stderr)

        result = self.invoke(
            "criterion",
            "criterion-pass",
            "--text",
            "The command records exact v3 evidence",
            "--status",
            "PASS",
            "--target-id",
            "target-1",
            "--evidence-id",
            "criterion-evidence",
            "--ref",
            FINAL_REF,
            "--source-kind",
            "ROLE",
            "--source",
            "root",
            "--evidence-state",
            "FRESH",
            "--summary",
            "direct CLI evidence",
        )
        self.assertEqual(0, result.returncode, result.stderr)

        result = self.invoke(
            "criterion",
            "criterion-na",
            "--text",
            "An intentionally inapplicable fixture criterion",
            "--status",
            "NOT_APPLICABLE",
            "--target-id",
            "target-1",
            "--na-justification",
            "outside fixture scope",
            "--skip-id",
            "skip-criterion",
            "--summary",
            "not applicable",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        record = self.loaded()
        self.assertEqual(
            {
                "id", "version", "target_id", "ref", "kind", "command",
                "started_at", "ended_at", "result", "artifact_ids", "environment",
                "invalidation_rule", "validity", "skip_id", "summary",
            },
            set(record["checks"][0]),
        )
        self.assertEqual([], record["criteria"][1]["evidence"])

        self.assert_atomic_failure(
            "check", "PASS", "--command-text", "true", "--summary", "incomplete"
        )

    def test_v3_handoff_delivery_and_finalize_fail_or_mutate_cleanly(self) -> None:
        result = self.invoke(
            "handoff",
            "--id",
            "handoff-1",
            "--from",
            "focused_worker",
            "--to",
            "root",
            "--reason",
            "bounded escalation",
            "--frozen-ref",
            FINAL_REF,
            "--packet-digest",
            PACKET_DIGEST,
            "--scope",
            "one bounded repair",
            "--constraint",
            "no unrelated edits",
            "--expected-evidence",
            "focused tests",
            "--at",
            "2026-07-15T10:00:02Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(FINAL_REF, self.loaded()["handoffs"][0]["ref"])

        failure = self.assert_atomic_failure("delivery", "--branch", "feature/example")
        self.assertIn("requires --json", failure.stderr)
        delivery = {
            "id": "delivery-1",
            "version": 1,
            "target_id": "target-1",
            "ref": FINAL_REF,
            "mode": "PULL_REQUEST",
            "status": "DELIVERED",
            "branch": "feature/example",
            "commit": FINAL_REF,
            "pull_request": "owner/repo#1",
            "at": "2026-07-15T10:00:03Z",
            "evidence": ["ready pull request"],
        }
        result = self.invoke("delivery", "--json", json.dumps(delivery))
        self.assertEqual(0, result.returncode, result.stderr)

        self.assert_atomic_failure(
            "finalize",
            "--target-id",
            "target-1",
            "--final-ref",
            FINAL_REF,
            "--head-ref",
            "9" * 40,
            "--head-tree",
            HEAD_TREE,
        )
        result = self.invoke(
            "finalize",
            "--target-id",
            "target-1",
            "--final-ref",
            FINAL_REF,
            "--head-ref",
            FINAL_REF,
            "--head-tree",
            HEAD_TREE,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_binding_corrections_and_invalid_variants_are_explicit(self) -> None:
        self.record["bindings"] = []
        self.write_record()
        result = self.invoke(
            "bind", "focused_verifier", "--binding-id", "review-binding",
            "--ref", FINAL_REF, "--state", "UNKNOWN"
        )
        self.assertEqual(0, result.returncode, result.stderr)
        binding = self.loaded()["bindings"][0]
        self.assertIsNone(binding["role"]["value"])

        result = self.invoke(
            "bind",
            "focused_verifier",
            "--binding-id",
            "review-binding",
            "--ref",
            FINAL_REF,
            "--replace",
            "--thread-id",
            "review-thread",
            "--state",
            "VERIFIED",
            "--provenance",
            "runtime identity",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("review-thread", self.loaded()["bindings"][0]["thread_id"])
        self.assert_atomic_failure(
            "bind", "focused_worker", "--binding-id", "bad-fallback",
            "--ref", FINAL_REF, "--state", "FALLBACK"
        )
        result = self.invoke(
            "bind",
            "code_quality_reviewer",
            "--binding-id",
            "quality-fallback",
            "--ref",
            FINAL_REF,
            "--thread-id",
            "isolated-review-thread",
            "--state",
            "FALLBACK",
            "--actual-role",
            "isolated_cli_reviewer",
            "--provenance",
            "digest-bound launch receipt plus runtime read-only enforcement",
            "--permissions-state",
            "VERIFIED",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        fallback = self.loaded()["bindings"][1]
        self.assertEqual("isolated_cli_reviewer", fallback["role"]["value"])
        self.assertEqual("FALLBACK", fallback["role"]["state"])

    def test_writer_finding_and_legacy_disposition_paths_are_not_ambiguous(self) -> None:
        self.assert_atomic_failure("writer", "start", "focused_worker", "--reason", "missing scope")
        result = self.invoke(
            "writer",
            "start",
            "focused_worker",
            "--reason",
            "bounded edit",
            "--ref",
            FINAL_REF,
            "--at",
            "2026-07-15T10:00:02Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.invoke(
            "handoff",
            "--id",
            "writer-handoff",
            "--from",
            "focused_worker",
            "--to",
            "root",
            "--reason",
            "cross-cutting escalation",
            "--frozen-ref",
            FINAL_REF,
            "--packet-digest",
            PACKET_DIGEST,
            "--scope",
            "cross-cutting repair",
            "--expected-evidence",
            "root qualification",
            "--at",
            "2026-07-15T10:00:03.500000Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.invoke(
            "writer",
            "stop",
            "focused_worker",
            "--reason",
            "bounded edit complete",
            "--ref",
            FINAL_REF,
            "--at",
            "2026-07-15T10:00:03Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.invoke(
            "writer",
            "escalate",
            "root",
            "--reason",
            "cross-cutting repair",
            "--ref",
            FINAL_REF,
            "--packet-digest",
            PACKET_DIGEST,
            "--at",
            "2026-07-15T10:00:04Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.invoke(
            "writer",
            "start",
            "root",
            "--reason",
            "cross-cutting repair",
            "--ref",
            FINAL_REF,
            "--packet-digest",
            PACKET_DIGEST,
            "--at",
            "2026-07-15T10:00:05Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.invoke(
            "writer",
            "stop",
            "root",
            "--reason",
            "cross-cutting repair complete",
            "--ref",
            FINAL_REF,
            "--at",
            "2026-07-15T10:00:06Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.invoke(
            "finding",
            "finding-1",
            "--source-kind",
            "ROLE",
            "--source",
            "root",
            "--severity",
            "HIGH",
            "--summary",
            "bounded reproduced defect",
            "--ref",
            FINAL_REF,
            "--decision",
            "FIX_NOW",
            "--family",
            "cli-integrity",
            "--evidence",
            "direct reproduction",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.invoke("disposition", "finding-1", "RESOLVED")
        self.assertEqual(0, result.returncode, result.stderr)
        finding = self.loaded()["findings"][0]
        self.assertEqual(("FIX_NOW", "RESOLVED"), (finding["decision"], finding["status"]))
        self.assert_atomic_failure("finding-update", "finding-1")

    def test_event_surface_records_stages_qualification_connector_and_expansion(self) -> None:
        for event_type, at in (("STAGE_STARTED", "2026-07-15T10:00:02Z"), ("STAGE_COMPLETED", "2026-07-15T10:00:03Z")):
            result = self.invoke(
                "event",
                event_type,
                "--target-id",
                "target-1",
                "--ref",
                FINAL_REF,
                "--stage",
                "I0",
                "--at",
                at,
            )
            self.assertEqual(0, result.returncode, result.stderr)

        result = self.invoke(
            "event",
            "QUALIFICATION_RECORDED",
            "--target-id",
            "target-1",
            "--ref",
            FINAL_REF,
            "--related-id",
            "qualification-obligation",
            "--digest",
            PACKET_DIGEST,
            "--at",
            "2026-07-15T10:00:04Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)

        actor = "chatgpt-codex-connector[bot]"

        def reaction(event_id: int, content: str, occurred_at: str) -> dict:
            return {
                "type": "REACTION", "id": event_id, "actor": actor,
                "occurred_at": occurred_at, "content": content,
                "target_kind": "PR_DESCRIPTION", "target_id": 1,
                "on_target": True, "commit_id": None,
                "commit_binding": "TARGET_BOUND",
            }

        start_snapshot = self.connector_snapshot(
            "connector-start", "2026-07-15T10:00:04.750000Z", 0.25, []
        )
        pickup_snapshot = self.connector_snapshot(
            "connector-pickup",
            "2026-07-15T10:00:05Z",
            0.5,
            [reaction(1, "eyes", "2026-07-15T10:00:04.600000Z")],
            pickup=0.1,
        )
        terminal_snapshot = self.connector_snapshot(
            "connector-terminal",
            "2026-07-15T10:00:05.500000Z",
            1.0,
            [reaction(2, "+1", "2026-07-15T10:00:05.400000Z")],
            pickup=0.9,
            terminal=0.9,
        )
        connector_events = [
            (
                "CONNECTOR_ROUND_STARTED", "root", "AUTOMATIC", "start-1", None,
                "250", None, "PENDING_PICKUP", start_snapshot,
                "2026-07-15T10:00:04.750000Z",
            ),
            (
                "CONNECTOR_PICKUP_OBSERVED", actor, "eyes", "pickup-1", "1",
                "100", "PENDING_PICKUP", "IN_PROGRESS", pickup_snapshot,
                "2026-07-15T10:00:05Z",
            ),
            (
                "CONNECTOR_PICKUP_REMOVED", "root", "eyes_removed", "removal-1", "1",
                "1000", "IN_PROGRESS", "PICKUP_REMOVED", terminal_snapshot,
                "2026-07-15T10:00:05.500000Z",
            ),
            (
                "CONNECTOR_TERMINAL_OBSERVED", actor, "+1", "terminal-1", "2",
                "900", "PICKUP_REMOVED", "CLEAN", terminal_snapshot,
                "2026-07-15T10:00:05.500000Z",
            ),
        ]
        for event_type, event_actor, reason, observation_id, external_id, latency, from_state, to_state, snapshot_path, at in connector_events:
            arguments = [
                "event", event_type, "--actor", event_actor, "--reason", reason,
                "--target-id", "target-1", "--ref", FINAL_REF, "--stage", "C1",
                "--related-id", "connector-c1", "--observation-id", observation_id,
                "--latency-ms", latency, "--to-state", to_state,
                "--anchor-kind", "PULL_REQUEST_DESCRIPTION", "--anchor-id", "owner/repo#1",
                "--on-target", "--observation-snapshot", str(snapshot_path), "--at", at,
            ]
            if external_id is not None:
                arguments.extend(["--external-id", external_id])
            if from_state is not None:
                arguments.extend(["--from-state", from_state])
            result = self.invoke(*arguments)
            self.assertEqual(0, result.returncode, result.stderr)
        connector = {
            "id": "connector-c1",
            "version": 1,
            "target_id": "target-1",
            "round": "C1",
            "repository": "owner/repo",
            "pull_request": "owner/repo#1",
            "ref": FINAL_REF,
            "trigger_kind": "AUTOMATIC",
            "trigger_id": None,
            "anchor_kind": "PULL_REQUEST_DESCRIPTION",
            "anchor_id": "owner/repo#1",
            "state": "CLEAN",
            "started_at": "2026-07-15T10:00:04.500000Z",
            "completed_at": "2026-07-15T10:00:05.500000Z",
            "finding_ids": [],
            "observation_ids": ["start-1", "pickup-1", "removal-1", "terminal-1"],
        }
        result = self.invoke("append", "connector_rounds", "--json", json.dumps(connector))
        self.assertEqual(0, result.returncode, result.stderr)

        result = self.invoke(
            "finding",
            "expansion-1",
            "--source-kind",
            "ROLE",
            "--source",
            "root",
            "--severity",
            "HIGH",
            "--summary",
            "new family manifestation",
            "--ref",
            FINAL_REF,
            "--stage",
            "I1",
            "--decision",
            "FIX_NOW",
            "--family",
            "cli-integrity",
            "--miss-reason",
            "SIBLING_NOT_ENUMERATED",
            "--evidence",
            "forward audit",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.invoke(
            "event",
            "FAMILY_EXPANSION_RECORDED",
            "--actor",
            "root",
            "--target-id",
            "target-1",
            "--ref",
            FINAL_REF,
            "--stage",
            "I1",
            "--from-state",
            "cli-integrity",
            "--to-state",
            "EXPANSION_1",
            "--reason",
            "SIBLING_NOT_ENUMERATED",
            "--related-id",
            "expansion-1",
            "--digest",
            PACKET_DIGEST,
            "--at",
            "2026-07-15T10:00:06Z",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        event_types = [item["type"] for item in self.loaded()["events"]]
        self.assertIn("QUALIFICATION_RECORDED", event_types)
        self.assertIn("CONNECTOR_PICKUP_OBSERVED", event_types)
        self.assertIn("FAMILY_EXPANSION_RECORDED", event_types)


if __name__ == "__main__":
    unittest.main()
