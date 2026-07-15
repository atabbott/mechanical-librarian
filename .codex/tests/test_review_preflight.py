from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "review_preflight.py"
SPEC = importlib.util.spec_from_file_location("review_preflight", SCRIPT)
assert SPEC and SPEC.loader
review_preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_preflight)

FROZEN_AT = "2026-07-15T12:00:00Z"
OBSERVED_AT = "2026-07-15T11:59:00Z"
COMPLETED_AT = "2026-07-15T11:58:00Z"
STARTED_AT = "2026-07-15T11:50:00Z"
BUILT_AT = "2026-07-15T11:45:00Z"
COMMIT_AT = "2026-07-15T10:00:00Z"


def git(root: Path, *args: str) -> str:
    environment = os.environ.copy()
    if args and args[0] == "commit":
        environment.update({"GIT_AUTHOR_DATE": COMMIT_AT, "GIT_COMMITTER_DATE": COMMIT_AT})
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    ).stdout.strip()


class RepositoryFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        git(root, "init", "-b", "topic")
        git(root, "config", "user.email", "test@example.invalid")
        git(root, "config", "user.name", "Test User")
        git(root, "remote", "add", "origin", "https://example.invalid/owner/repo.git")
        (root / "README.md").write_text("base\n", encoding="utf-8")
        git(root, "add", "README.md")
        git(root, "commit", "-m", "base")
        self.base = git(root, "rev-parse", "HEAD")
        self.base_tree = git(root, "rev-parse", "HEAD^{tree}")
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("Required contract v2\n", encoding="utf-8")
        git(root, "add", "docs/guide.md")
        git(root, "commit", "-m", "docs")
        self.head = git(root, "rev-parse", "HEAD")
        self.head_tree = git(root, "rev-parse", "HEAD^{tree}")
        self.environment = {
            "kind": "CI",
            "platform": "linux-x86_64",
            "runtime": "python-3.12",
            "container_image": "python@sha256:" + "1" * 64,
            "lock_digest": hashlib.sha256(b"uv.lock fixture").hexdigest(),
        }
        self.environment["digest"] = review_preflight._canonical_digest(self.environment)
        self.invalidation_rule = "head, artifact, environment, command, or policy bundle changes"
        self.snapshot_path = root / ".codex" / "runs" / "preflight-evidence.json"
        self.snapshot_path.parent.mkdir(parents=True)
        self.snapshot = self._snapshot_record()
        self.snapshot_sha256 = self.write_snapshot()

    def _snapshot_record(self) -> dict:
        validation = review_preflight.team_validation
        team = validation.validate_team()
        record = validation.initial_record(
            "fixture-run",
            "deliver-focused-change",
            "ticket",
            "251",
            self.base,
            team,
            "2026-07-15T09:00:00Z",
        )
        repository_digest = hashlib.sha256(self.head_tree.encode()).hexdigest()
        record["targets"][0]["final_ref"] = self.head
        record["repository"].update(
            {
                "base_tree": hashlib.sha256(self.base_tree.encode()).hexdigest(),
                "head_ref": self.head,
                "head_tree": repository_digest,
            }
        )
        record["artifacts"] = [
            {
                "id": "artifact-repository",
                "version": 1,
                "target_id": "target-1",
                "ref": self.head,
                "kind": "REPOSITORY",
                "identifier": f"fixture-repository@{self.head}",
                "digest": repository_digest,
                "built_at": BUILT_AT,
                "environment_digest": self.environment["digest"],
            }
        ]
        record["checks"] = [self._check("linux"), self._check("review")]
        return record

    def _check(self, check_id: str) -> dict:
        return {
            "id": check_id,
            "version": 1,
            "target_id": "target-1",
            "ref": self.head,
            "kind": "FULL_GATE",
            "command": f"python3 -m unittest --gate {check_id}",
            "started_at": STARTED_AT,
            "ended_at": COMPLETED_AT,
            "result": "PASS",
            "artifact_ids": ["artifact-repository"],
            "environment": copy.deepcopy(self.environment),
            "invalidation_rule": self.invalidation_rule,
            "validity": "FRESH",
            "skip_id": None,
            "summary": f"{check_id} passed",
        }

    def write_snapshot(self, packet: dict | None = None) -> str:
        raw = (json.dumps(self.snapshot, indent=2) + "\n").encode()
        self.snapshot_path.write_bytes(raw)
        self.snapshot_sha256 = hashlib.sha256(raw).hexdigest()
        if packet is not None:
            packet["evidence_snapshot"]["sha256"] = self.snapshot_sha256
        return self.snapshot_sha256

    def evidence(self, evidence_id: str = "qualification") -> dict:
        source = next(item for item in self.snapshot["checks"] if item["id"] == evidence_id)
        return {
            "id": evidence_id,
            "source_kind": "CHECK",
            "source_id": evidence_id,
            "expected_command": source["command"],
            "expected_artifact_ids": source["artifact_ids"],
            "expected_environment_digest": source["environment"]["digest"],
            "expected_invalidation_rule": source["invalidation_rule"],
        }

    def review_packet_snapshot(
        self, declaration: dict, binding_id: str, task: str, lens: str
    ) -> tuple[str, str, str]:
        validation = review_preflight.team_validation

        def blob(
            blob_id: str,
            kind: str,
            purpose: str,
            source: str,
            content: str,
            revision: str | None = None,
        ) -> dict:
            return {
                "id": blob_id,
                "kind": kind,
                "purpose": purpose,
                "source": source,
                "revision": revision or self.head,
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            }

        manifest = validation.load_toml(validation.MANIFEST)
        role = declaration["role"]
        config_source = f".codex/{manifest['roles'][role]['config']}"
        policy_sources = sorted(
            set(manifest["roles"][role]["required_policies"])
            | {".codex/binding-preflight.md"}
        )
        name_status = "A\tdocs/guide.md\n"
        packet_id = "review-packet-source"
        packet = {
            "schema_version": 2,
            "packet_id": packet_id,
            "workflow": {
                "run_id": "fixture-run",
                "workflow": "deliver-focused-change",
                "target_id": "target-1",
                "stage": "I0",
                "context_mode": "FRESH_ISOLATED",
            },
            "target": {
                "kind": "ticket", "id": "251", "revision": self.head,
                "url": "https://example.invalid/issues/251",
                "requirements": [blob("requirement", "INLINE", "REQUIREMENT", "issue-251", "required contract")],
            },
            "repository": {
                "mode": "COMMIT", "repository_id": self.snapshot["repository"]["repository_id"],
                "worktree": str(self.root), "base_ref": self.base,
                "target_ref": self.head, "tree": self.head_tree,
                "changed_path_count": 1,
                "diff": blob(
                    "repository-diff", "DIFF", "DIFF", "git-diff",
                    "diff --git a/docs/guide.md b/docs/guide.md\n",
                    f"{self.base}..{self.head}",
                ),
                "name_status": blob(
                    "repository-name-status", "NAME_STATUS", "NAME_STATUS",
                    "git-name-status", name_status, f"{self.base}..{self.head}",
                ),
            },
            "role": {
                "requested_role": role,
                "config": blob(
                    "role-config", "REPO_BLOB", "ROLE_CONFIG", config_source,
                    (validation.CODEX.parent / config_source).read_text(encoding="utf-8"),
                ),
                "model": declaration["model"], "reasoning": declaration["reasoning"],
                "permissions": declaration["permissions"],
                "required_capabilities": list(declaration["capabilities"]),
                "forbidden_capabilities": list(declaration["forbidden_capabilities"]),
                "policies": [
                    blob(
                        f"policy-{index}", "REPO_BLOB", "POLICY", source,
                        (validation.CODEX.parent / source).read_text(encoding="utf-8"),
                    )
                    for index, source in enumerate(policy_sources, 1)
                ],
            },
            "binding_request": {
                "binding_id": binding_id,
                "allowed_actual_roles": [role, "isolated_cli_reviewer"],
                "launch": {
                    "ignore_user_config": True, "model": declaration["model"],
                    "reasoning": declaration["reasoning"], "approval_policy": "never",
                    "sandbox": "read-only",
                },
                "originating_review_id": None, "originating_binding_id": None,
                "originating_thread_id": None,
            },
            "task": {"text": task, "lens": lens, "output_contract": "findings with evidence"},
            "artifacts": [],
            "lineage": {"finding_ids": [], "repair_evidence": []},
            "exclusions": {
                "worker_or_orchestrator_conclusions": True,
                "prior_findings_or_verdicts": True,
                "family_dispositions": True,
                "unrelated_remediation_history": True,
                "iteration_framing": True,
                "named_green_tests": True,
                "review_method": True,
                "family_policy": True,
                "review_synthesis": True,
            },
        }
        raw = validation.canonical_json_bytes(packet).decode()
        return packet_id, raw, hashlib.sha256(raw.encode()).hexdigest()

    def packet(self) -> dict:
        return {
            "schema_version": 2,
            "packet_id": "test-packet",
            "frozen_at": FROZEN_AT,
            "repository": {
                "common_dir": str((self.root / ".git").resolve()),
                "origin": "https://example.invalid/owner/repo.git",
            },
            "worktree": {"root": str(self.root.resolve()), "branch": "topic"},
            "target": {
                "target_id": "target-1",
                "base_ref": self.base,
                "base_commit": self.base,
                "base_tree": self.base_tree,
                "head_ref": self.head,
                "head_commit": self.head,
                "head_tree": self.head_tree,
            },
            "evidence_snapshot": {
                "path": self.snapshot_path.relative_to(self.root).as_posix(),
                "sha256": self.snapshot_sha256,
                "run_id": "fixture-run",
            },
            "changed_paths": {"allowed": ["docs/**"], "required": ["docs/guide.md"]},
            "obligations": {
                "documentation": [
                    {
                        "path": "docs/guide.md",
                        "claims": [
                            {"type": "LITERAL", "value": "Required contract"},
                            {"type": "REGEX", "value": r"contract v[0-9]+"},
                        ],
                    }
                ],
                "migrations": {"state": "NOT_APPLICABLE", "paths": [], "reason": "no persisted schema"},
                "security": {"state": "NOT_APPLICABLE", "paths": [], "reason": "no trust-boundary change"},
                "changelog": {"state": "NOT_APPLICABLE", "paths": [], "reason": "not user visible"},
                "private_qualification": {
                    "state": "NOT_APPLICABLE",
                    "reason": "no private resources",
                    "evidence": [],
                },
                "linux_qualification": {
                    "state": "REQUIRED",
                    "reason": None,
                    "evidence": [self.evidence("linux")],
                },
                "gates": [
                    {"id": "independent-review", "state": "PASS", "evidence": [self.evidence("review")]}
                ],
            },
        }


class ReviewPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repository = RepositoryFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def evaluate(self, packet: dict) -> dict:
        validated = review_preflight.validate_packet(packet)
        raw = json.dumps(packet, sort_keys=True).encode()
        return review_preflight.evaluate(validated, hashlib.sha256(raw).hexdigest(), self.root)

    def test_exact_frozen_packet_passes_with_machine_readable_evidence(self) -> None:
        report = self.evaluate(self.repository.packet())
        self.assertEqual("PASS", report["result"])
        self.assertEqual(self.repository.head, report["target"]["head_commit"])
        self.assertEqual(self.repository.head_tree, report["target"]["head_tree"])
        self.assertEqual(["docs/guide.md"], report["changed_paths"])
        self.assertTrue(all(check["state"] == "PASS" for check in report["checks"]))

    def test_review_and_external_gate_entries_resolve_from_the_snapshot(self) -> None:
        packet = self.repository.packet()
        declaration = next(
            item
            for item in self.repository.snapshot["team_context"]["workflow_roles"]
            if item["role"] == "focused_verifier"
        )
        binding = {
            "binding_id": "review-binding",
            "target_id": "target-1",
            "ref": self.repository.head,
            "requested_role": "focused_verifier",
            "thread_id": "review-thread",
            "role": {"value": "focused_verifier", "state": "VERIFIED", "provenance": "runtime"},
            "model": {"value": declaration["model"], "state": "VERIFIED", "provenance": "runtime"},
            "reasoning": {"value": declaration["reasoning"], "state": "VERIFIED", "provenance": "runtime"},
            "permissions": {"value": ":read-only", "state": "VERIFIED", "provenance": "runtime"},
            "capability_evidence": {
                "verified": list(declaration["capabilities"]),
                "absent": list(declaration["forbidden_capabilities"]),
                "provenance": "runtime capability receipt",
            },
        }
        task = "Review the exact frozen target"
        lens = "focused outcome"
        packet_id, packet_snapshot, packet_digest = self.repository.review_packet_snapshot(
            declaration, binding["binding_id"], task, lens
        )
        self.repository.snapshot["bindings"].append(binding)
        self.repository.snapshot["reviews"].append(
            {
                "id": "review-source",
                "version": 1,
                "target_id": "target-1",
                "ref": self.repository.head,
                "stage": "I0",
                "reviewer_role": "focused_verifier",
                "binding_id": binding["binding_id"],
                "task": task,
                "lens": lens,
                "context_mode": "FRESH_ISOLATED",
                "originating_review_id": None,
                "originating_thread_id": None,
                "packet_id": packet_id,
                "packet_digest": packet_digest,
                "packet_snapshot": packet_snapshot,
                "started_at": STARTED_AT,
                "ended_at": COMPLETED_AT,
                "result": "PASS",
                "finding_ids": [],
                "evidence": ["attempted falsification"],
                "evidence_state": "FRESH",
            }
        )
        packet["obligations"]["gates"][0]["evidence"] = [
            {
                "id": "review-proof",
                "source_kind": "REVIEW",
                "source_id": "review-source",
                "expected_command": "Review the exact frozen target",
                "expected_artifact_ids": [
                    f"repository-ref:{self.repository.head}",
                    f"review-packet:{packet_digest}",
                ],
                "expected_environment_digest": review_preflight._canonical_digest(binding),
                "expected_invalidation_rule": "target ref, review packet, reviewer binding, or policy bundle changes",
            }
        ]
        self.repository.write_snapshot(packet)
        self.assertEqual("PASS", self.evaluate(packet)["result"])

        external = {
            "id": "external-source",
            "version": 1,
            "target_id": "target-1",
            "source_pr": "owner/repo#1",
            "source_head": self.repository.head,
            "observed_at": OBSERVED_AT,
            "invalidation_rule": "source head or external result changes",
            "evidence_state": "FRESH",
            "summary": "external gate observed",
        }
        self.repository.snapshot["external_evidence"].append(external)
        self.repository.snapshot["external_gates"].append(
            {
                "id": "external-gate",
                "version": 1,
                "target_id": "target-1",
                "ref": self.repository.head,
                "kind": "REMOTE_CI",
                "required": True,
                "status": "SATISFIED",
                "checked_at": FROZEN_AT,
                "evidence_ids": ["external-source"],
                "skip_id": None,
            }
        )
        packet["obligations"]["gates"][0]["evidence"] = [
            {
                "id": "external-proof",
                "source_kind": "EXTERNAL_GATE",
                "source_id": "external-gate",
                "expected_command": "observe external gate REMOTE_CI",
                "expected_artifact_ids": [f"external-head:{self.repository.head}"],
                "expected_environment_digest": review_preflight._canonical_digest(
                    [{"source_pr": "owner/repo#1", "source_head": self.repository.head}]
                ),
                "expected_invalidation_rule": "source head or external result changes",
            }
        ]
        self.repository.write_snapshot(packet)
        self.assertEqual("PASS", self.evaluate(packet)["result"])

    def test_exact_inherited_evidence_passes_when_invalidation_identity_is_unchanged(self) -> None:
        packet = self.repository.packet()
        for check in self.repository.snapshot["checks"]:
            check["validity"] = "INHERITED"
        self.repository.write_snapshot(packet)
        report = self.evaluate(packet)
        self.assertEqual("PASS", report["result"])

    def test_legacy_free_form_pass_evidence_is_rejected(self) -> None:
        packet = self.repository.packet()
        packet["obligations"]["gates"][0]["evidence"][0] = {
            "id": "review",
            "state": "PASS",
            "source": "unverified free-form assertion",
            "observed_at": "2000-01-01T00:00:00Z",
            "head_commit": self.repository.head,
            "head_tree": self.repository.head_tree,
        }
        with self.assertRaisesRegex(review_preflight.PacketError, "invalid fields"):
            review_preflight.validate_packet(packet)

    def test_pass_evidence_requires_every_provenance_component(self) -> None:
        fields = [
            "source_kind",
            "source_id",
            "expected_command",
            "expected_artifact_ids",
            "expected_environment_digest",
            "expected_invalidation_rule",
        ]
        for field in fields:
            with self.subTest(field=field):
                packet = self.repository.packet()
                del packet["obligations"]["gates"][0]["evidence"][0][field]
                with self.assertRaisesRegex(review_preflight.PacketError, "invalid fields"):
                    review_preflight.validate_packet(packet)

    def test_stale_or_unavailable_evidence_cannot_claim_pass(self) -> None:
        for validity in ("STALE", "UNAVAILABLE"):
            with self.subTest(validity=validity):
                packet = self.repository.packet()
                next(item for item in self.repository.snapshot["checks"] if item["id"] == "review")["validity"] = validity
                self.repository.write_snapshot(packet)
                report = self.evaluate(packet)
                self.assertEqual("FAIL", report["result"])
                self.assertEqual("gate.independent-review", report["failures"][-1]["check_id"])

    def test_snapshot_bytes_and_source_entry_are_resolved(self) -> None:
        packet = self.repository.packet()
        self.repository.snapshot_path.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(review_preflight.PacketError, "digest does not match"):
            self.evaluate(packet)

        self.repository.write_snapshot(packet)
        packet["obligations"]["gates"][0]["evidence"][0]["source_id"] = "missing-entry"
        with self.assertRaisesRegex(review_preflight.PacketError, "does not resolve uniquely"):
            self.evaluate(packet)

    def test_exact_command_and_entry_substitution_fail_closed(self) -> None:
        packet = self.repository.packet()
        packet["obligations"]["gates"][0]["evidence"][0]["expected_command"] += " --different"
        report = self.evaluate(packet)
        self.assertEqual("FAIL", report["result"])

        packet = self.repository.packet()
        packet["obligations"]["gates"][0]["evidence"][0]["source_id"] = "linux"
        report = self.evaluate(packet)
        self.assertEqual("FAIL", report["result"])

    def test_environment_digest_and_timing_are_verified(self) -> None:
        packet = self.repository.packet()
        check = next(item for item in self.repository.snapshot["checks"] if item["id"] == "review")
        check["environment"]["platform"] = "other"
        self.repository.write_snapshot(packet)
        with self.assertRaisesRegex(review_preflight.PacketError, "digest mismatch"):
            self.evaluate(packet)

        packet = self.repository.packet()
        new_environment = copy.deepcopy(check["environment"])
        new_environment["digest"] = review_preflight._canonical_digest(
            {key: value for key, value in new_environment.items() if key != "digest"}
        )
        for source_check in self.repository.snapshot["checks"]:
            source_check["environment"] = copy.deepcopy(new_environment)
        self.repository.snapshot["artifacts"][0]["environment_digest"] = new_environment["digest"]
        self.repository.write_snapshot(packet)
        report = self.evaluate(packet)
        self.assertEqual("FAIL", report["result"])

        packet = self.repository.packet()
        check["ended_at"] = check["started_at"]
        self.repository.write_snapshot(packet)
        with self.assertRaisesRegex(review_preflight.PacketError, "positive duration"):
            self.evaluate(packet)

    def test_changed_path_outside_frozen_boundary_fails(self) -> None:
        packet = self.repository.packet()
        packet["changed_paths"] = {"allowed": ["README.md"], "required": []}
        report = self.evaluate(packet)
        self.assertEqual("FAIL", report["result"])
        self.assertTrue(
            any(
                failure["check_id"] == "changed_path.allowed"
                and failure["evidence"]["path"] == "docs/guide.md"
                for failure in report["failures"]
            )
        )

    def test_declared_gate_cannot_be_bypassed_with_unknown_evidence(self) -> None:
        packet = self.repository.packet()
        packet["obligations"]["gates"][0]["state"] = "UNKNOWN"
        report = self.evaluate(packet)
        self.assertEqual("FAIL", report["result"])
        self.assertEqual("gate.independent-review", report["failures"][-1]["check_id"])

    def test_off_target_evidence_and_traversal_are_clean_input_errors(self) -> None:
        off_target = self.repository.packet()
        next(item for item in self.repository.snapshot["checks"] if item["id"] == "review")["ref"] = self.repository.base
        self.repository.write_snapshot(off_target)
        report = self.evaluate(off_target)
        self.assertEqual("FAIL", report["result"])

        traversal = self.repository.packet()
        traversal["changed_paths"]["allowed"] = ["../outside/**"]
        with self.assertRaisesRegex(review_preflight.PacketError, "unsafe path segment"):
            review_preflight.validate_packet(traversal)

    def test_symlink_escape_is_rejected_by_path_boundary_probe(self) -> None:
        outside = Path(self.temporary.name).parent / "outside-review-preflight"
        link = self.root / "escape"
        try:
            os.symlink(outside, link)
            self.assertFalse(review_preflight._within(self.root, "escape/evidence.txt"))
        finally:
            link.unlink(missing_ok=True)

    def test_head_ref_drift_fails_even_when_expected_objects_still_exist(self) -> None:
        packet = self.repository.packet()
        packet["target"]["head_ref"] = "topic"
        (self.root / "docs" / "later.md").write_text("later\n", encoding="utf-8")
        git(self.root, "add", "docs/later.md")
        git(self.root, "commit", "-m", "later")
        report = self.evaluate(packet)
        self.assertEqual("FAIL", report["result"])
        failed_ids = {failure["check_id"] for failure in report["failures"]}
        self.assertIn("target.head_commit", failed_ids)
        self.assertIn("worktree.head_commit", failed_ids)


if __name__ == "__main__":
    unittest.main()
