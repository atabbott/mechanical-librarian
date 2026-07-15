from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


CODEX = Path(__file__).resolve().parents[1]
SCRIPT = CODEX / "scripts" / "review_packet.py"
sys.path.insert(0, str(CODEX / "scripts"))

import validate_team as validation  # noqa: E402


class ReviewPacketTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.draft = self.root / "draft.json"
        self.packet = self.root / "packet.json"
        self.git("init", "-b", "main")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test User")
        self.git("remote", "add", "origin", "https://github.com/owner/repository.git")
        required = [
            ".codex/agents/focused-verifier.toml",
            ".codex/engineering-judgment.md",
            ".codex/review-method.md",
            ".codex/binding-preflight.md",
        ]
        for relative in required:
            target = self.repository / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(validation.CODEX.parent / relative, target)
        (self.repository / "requirements.md").write_text(
            "The review packet must be immutable.\n", encoding="utf-8"
        )
        (self.repository / "docs").mkdir()
        (self.repository / "docs" / "contract.md").write_text(
            "base\n", encoding="utf-8"
        )
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        (self.repository / "docs" / "contract.md").write_text(
            "base\nchanged\n", encoding="utf-8"
        )
        self.git("add", "docs/contract.md")
        self.git("commit", "-m", "change")
        self.target = self.git("rev-parse", "HEAD")
        self.target_tree = self.git("rev-parse", "HEAD^{tree}")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def blob(
        self,
        blob_id: str,
        kind: str,
        purpose: str,
        source: str,
        revision: str,
        content: str | None,
    ) -> dict:
        return {
            "id": blob_id,
            "kind": kind,
            "purpose": purpose,
            "source": source,
            "revision": revision,
            "content": content,
        }

    def value(self, *, repository_mode: str = "COMMIT") -> dict:
        if repository_mode == "NONE":
            repository = {
                "mode": "NONE",
                "repository_id": "private-proposal",
                "worktree": None,
                "base_ref": None,
                "target_ref": "proposal-v3",
            }
            requirement = self.blob(
                "requirement", "INLINE", "REQUIREMENT", "proposal", "proposal-v3",
                "A complete, observable vertical proposal.",
            )
            artifacts = [
                self.blob(
                    "proposal", "INLINE", "SOURCE", "proposal.md", "proposal-v3",
                    "Outcome, criteria, demonstration, and dependencies.",
                )
            ]
            revision = "proposal-v3"
        else:
            target_ref = None if repository_mode == "STAGED" else self.target
            revision = self.target_tree if repository_mode == "STAGED" else self.target
            repository = {
                "mode": repository_mode,
                "repository_id": "owner/repository",
                "worktree": str(self.repository),
                "base_ref": self.base,
                "target_ref": target_ref,
            }
            requirement = self.blob(
                "requirement", "REPO_BLOB", "REQUIREMENT", "requirements.md",
                revision, None,
            )
            artifacts = [
                self.blob(
                    "changed-source", "REPO_BLOB", "SOURCE", "docs/contract.md",
                    revision, None,
                )
            ]
        return {
            "schema_version": 2,
            "packet_id": f"workflow-controls-251-{repository_mode.lower()}",
            "workflow": {
                "run_id": "workflow-controls-251",
                "workflow": "deliver-focused-change" if repository_mode != "NONE" else "prepare-ready-work",
                "target_id": "target-1",
                "stage": "I0",
                "context_mode": "FRESH_ISOLATED",
            },
            "target": {
                "kind": "ticket" if repository_mode != "NONE" else "proposal",
                "id": "251" if repository_mode != "NONE" else "private-proposal",
                "revision": revision,
                "url": "https://example.invalid/issues/251" if repository_mode != "NONE" else None,
                "requirements": [requirement],
            },
            "repository": repository,
            "role": {"requested_role": "focused_verifier"},
            "binding_request": {
                "binding_id": f"binding-{repository_mode.lower()}",
                "originating_review_id": None,
                "originating_binding_id": None,
                "originating_thread_id": None,
            },
            "task": {
                "text": "Attempt to falsify the frozen outcome.",
                "lens": "observable outcome and failure boundary",
                "output_contract": "Findings ordered by severity with raw evidence, or PASS.",
            },
            "artifacts": artifacts,
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

    def write_draft(self, value: dict) -> None:
        self.draft.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def invoke(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def freeze(self, value: dict | None = None) -> dict:
        self.write_draft(value or self.value())
        result = self.invoke("freeze", "--draft", str(self.draft), "--output", str(self.packet))
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_freeze_is_canonical_deterministic_and_digest_verifiable(self) -> None:
        result = self.freeze()
        raw = self.packet.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result["sha256"])
        self.assertEqual(raw, validation.canonical_json_bytes(json.loads(raw)))
        verified = self.invoke(
            "verify", "--packet", str(self.packet), "--digest", result["sha256"]
        )
        self.assertEqual(0, verified.returncode, verified.stderr)
        second = self.root / "second.json"
        repeated = self.invoke("freeze", "--draft", str(self.draft), "--output", str(second))
        self.assertEqual(0, repeated.returncode, repeated.stderr)
        self.assertEqual(raw, second.read_bytes())

    def test_tampered_or_pretty_packet_does_not_verify(self) -> None:
        pretty = self.value()
        self.write_draft(pretty)
        result = self.invoke("verify", "--packet", str(self.draft))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not canonical", result.stderr)
        frozen = self.freeze()
        tampered = json.loads(self.packet.read_text(encoding="utf-8"))
        tampered["task"]["text"] = "substituted task"
        tampered_path = self.root / "tampered.json"
        tampered_path.write_bytes(validation.canonical_json_bytes(tampered))
        mismatch = self.invoke(
            "verify", "--packet", str(tampered_path), "--digest", frozen["sha256"]
        )
        self.assertNotEqual(0, mismatch.returncode)

    def test_non_git_proposal_packet_freezes_and_verifies(self) -> None:
        result = self.freeze(self.value(repository_mode="NONE"))
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        self.assertEqual("NONE", packet["repository"]["mode"])
        self.assertEqual("INLINE", packet["role"]["config"]["kind"])
        verified = self.invoke(
            "verify", "--packet", str(self.packet), "--digest", result["sha256"]
        )
        self.assertEqual(0, verified.returncode, verified.stderr)

    def test_staged_packet_freezes_and_verifies_against_its_tree(self) -> None:
        result = self.freeze(self.value(repository_mode="STAGED"))
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        self.assertEqual("STAGED", packet["repository"]["mode"])
        self.assertEqual(self.target_tree, packet["repository"]["target_ref"])
        self.assertEqual(self.target_tree, packet["repository"]["tree"])
        verified = self.invoke(
            "verify", "--packet", str(self.packet), "--digest", result["sha256"]
        )
        self.assertEqual(0, verified.returncode, verified.stderr)

    def test_staged_live_verification_never_invokes_write_tree(self) -> None:
        self.freeze(self.value(repository_mode="STAGED"))
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        original_run = subprocess.run
        attempted_writes: list[list[str]] = []

        def reject_write_tree(arguments: list[str], **kwargs):
            if "write-tree" in arguments:
                attempted_writes.append(arguments)
                return subprocess.CompletedProcess(
                    arguments, 128, "", "read-only sandbox rejected write-tree"
                )
            return original_run(arguments, **kwargs)

        with mock.patch.object(
            validation.subprocess, "run", side_effect=reject_write_tree
        ):
            validation.validate_review_packet_live(
                packet, trusted_worktree=self.repository
            )
        self.assertEqual([], attempted_writes)

    def test_self_consistently_relabelled_staged_target_does_not_verify(self) -> None:
        self.freeze(self.value(repository_mode="STAGED"))
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        forged_ref = "f" * 40
        packet["target"]["revision"] = forged_ref
        packet["repository"]["target_ref"] = forged_ref
        packet["repository"]["diff"]["revision"] = (
            f"{packet['repository']['base_ref']}..{forged_ref}"
        )
        packet["repository"]["name_status"]["revision"] = (
            f"{packet['repository']['base_ref']}..{forged_ref}"
        )
        for blob in [
            *packet["target"]["requirements"],
            packet["role"]["config"],
            *packet["role"]["policies"],
            *packet["artifacts"],
        ]:
            blob["revision"] = forged_ref
        raw = validation.canonical_json_bytes(packet)
        self.packet.write_bytes(raw)
        verified = self.invoke(
            "verify",
            "--packet",
            str(self.packet),
            "--digest",
            hashlib.sha256(raw).hexdigest(),
        )
        self.assertNotEqual(0, verified.returncode)
        self.assertIn("review packet staged target ref drifted", verified.stderr)

    def test_runtime_receipt_binds_exact_strength_permissions_and_capabilities(self) -> None:
        frozen = self.freeze()
        declaration = validation.validate_team()["roles"]["focused_verifier"]
        receipt = self.root / "receipt.json"
        arguments = [
            "receipt", "--packet", str(self.packet), "--digest", frozen["sha256"],
            "--output", str(receipt), "--actual-role", "focused_verifier",
            "--thread-id", "thread-1", "--model", declaration["model"],
            "--reasoning", declaration["reasoning"], "--permissions", ":read-only",
            "--model-state", "VERIFIED", "--reasoning-state", "VERIFIED",
            "--role-provenance", "runtime identity", "--model-provenance", "runtime identity",
            "--reasoning-provenance", "runtime identity", "--permissions-provenance", "sandbox probe",
            "--capability-provenance", "runtime capability probes",
            "--runtime-evidence", "isolated read-only invocation",
        ]
        for capability in declaration["capabilities"]:
            arguments.extend(["--verified-capability", capability])
        for capability in declaration["forbidden_capabilities"]:
            arguments.extend(["--absent-capability", capability])
        result = self.invoke(*arguments)
        self.assertEqual(0, result.returncode, result.stderr)
        value = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(frozen["sha256"], value["packet_digest"])
        self.assertEqual("binding-commit", value["binding_id"])
        arguments[arguments.index(declaration["model"])] = "weaker-model"
        failed = self.invoke(*arguments)
        self.assertNotEqual(0, failed.returncode)

    def test_invalid_lineage_role_or_blob_purpose_fails_atomically(self) -> None:
        self.packet.write_text("sentinel\n", encoding="utf-8")
        invalid = self.value()
        invalid["binding_request"]["originating_thread_id"] = "inherited-thread"
        self.write_draft(invalid)
        result = self.invoke("freeze", "--draft", str(self.draft), "--output", str(self.packet))
        self.assertNotEqual(0, result.returncode)
        self.assertEqual("sentinel\n", self.packet.read_text(encoding="utf-8"))

        invalid = self.value()
        invalid["role"]["requested_role"] = "safety_reviewer"
        self.write_draft(invalid)
        result = self.invoke("freeze", "--draft", str(self.draft), "--output", str(self.packet))
        self.assertNotEqual(0, result.returncode)

        invalid = self.value()
        invalid["artifacts"][0]["purpose"] = "INVENTED"
        self.write_draft(invalid)
        result = self.invoke("freeze", "--draft", str(self.draft), "--output", str(self.packet))
        self.assertNotEqual(0, result.returncode)

    def test_duplicate_json_keys_are_rejected(self) -> None:
        self.draft.write_text('{"schema_version":2,"schema_version":2}\n', encoding="utf-8")
        result = self.invoke("freeze", "--draft", str(self.draft), "--output", str(self.packet))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("duplicate JSON key", result.stderr)


if __name__ == "__main__":
    unittest.main()
