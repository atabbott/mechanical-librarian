from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_team as validation  # noqa: E402


class RunRecordValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.team = validation.validate_team()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.repository = Path(cls.temporary.name).resolve() / "repository"
        cls.repository.mkdir()
        cls.repository_id = "owner/repository"
        cls.git("init", "-b", "main")
        cls.git("config", "user.email", "test@example.invalid")
        cls.git("config", "user.name", "Test User")
        cls.git(
            "remote",
            "add",
            "origin",
            "https://github.com/owner/repository.git",
        )
        manifest = validation.load_toml(validation.MANIFEST)
        sources = {".codex/binding-preflight.md"}
        for role in manifest["workflows"]["deliver-focused-change"]["roles"]:
            if manifest["roles"][role]["kind"] != "reviewer":
                continue
            sources.add(f".codex/{manifest['roles'][role]['config']}")
            sources.update(manifest["roles"][role]["required_policies"])
        for source in sorted(sources):
            target = cls.repository / source
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(validation.CODEX.parent / source, target)
        (cls.repository / "docs").mkdir()
        (cls.repository / "docs" / "contract.md").write_text(
            "base\n", encoding="utf-8"
        )
        cls.git("add", ".")
        cls.git("commit", "-m", "base fixture")
        cls.base_ref = cls.git("rev-parse", "HEAD")
        cls.base_tree_oid = cls.git("rev-parse", "HEAD^{tree}")
        cls.base_tree = hashlib.sha256(cls.base_tree_oid.encode()).hexdigest()
        (cls.repository / "docs" / "contract.md").write_text(
            "base\nchanged\n", encoding="utf-8"
        )
        cls.git("add", "docs/contract.md")
        cls.git("commit", "-m", "target fixture")
        cls.target_ref = cls.git("rev-parse", "HEAD")
        cls.target_tree = cls.git("rev-parse", "HEAD^{tree}")
        cls.head_tree_digest = hashlib.sha256(cls.target_tree.encode()).hexdigest()
        cls.repository_diff = cls.git(
            "diff", "--find-renames", cls.base_ref, cls.target_ref, "--", strip=False
        )
        cls.repository_name_status = cls.git(
            "diff", "--name-status", cls.base_ref, cls.target_ref, "--", strip=False
        )
        cls.repository_changed_path_count = len(
            [line for line in cls.repository_name_status.splitlines() if line]
        )
        cls.run_requirement_source = (
            ".codex/runs/qualified-fixture/requirements-live.md"
        )
        run_requirement = cls.repository / cls.run_requirement_source
        run_requirement.parent.mkdir(parents=True, exist_ok=True)
        run_requirement.write_text("trusted run requirement\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    @classmethod
    def git(cls, *arguments: str, strip: bool = True) -> str:
        completed = subprocess.run(
            ["git", "-C", str(cls.repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip() if strip else completed.stdout

    def legacy_v2_record(self) -> dict:
        workflow = "deliver-focused-change"
        frozen_ref = "a" * 40
        return {
            "schema_version": 2,
            "run_id": "synthetic-v2-upgrade-fixture",
            "workflow": workflow,
            "status": "INTAKE",
            "target": {
                "kind": "ticket",
                "id": "251",
                "frozen_ref": frozen_ref,
            },
            "team_context": validation.frozen_context(
                workflow,
                self.team,
                validation.load_toml(validation.MANIFEST),
            ),
            "bindings": [],
            "events": [
                {
                    "sequence": 1,
                    "type": "RUN_STARTED",
                    "at": "2026-07-15T10:00:00Z",
                    "actor": None,
                    "phase": None,
                    "reason": None,
                },
                {
                    "sequence": 2,
                    "type": "PHASE_ENTERED",
                    "at": "2026-07-15T10:00:00Z",
                    "actor": None,
                    "phase": "INTAKE",
                    "reason": None,
                },
            ],
            "criteria": [],
            "findings": [],
            "handoffs": [],
            "checks": [],
            "delivery": {
                "branch": None,
                "commit": None,
                "pull_request": None,
                "merge_authorized": False,
            },
        }

    def legacy_v1_record(self) -> dict:
        source = self.legacy_v2_record()
        return {
            "schema_version": 1,
            "run_id": "synthetic-v1-read-fixture",
            "workflow": source["workflow"],
            "status": source["status"],
            "target": source["target"],
            "bindings": [],
            "criteria": [],
            "findings": [],
            "handoffs": [],
            "checks": [],
            "delivery": source["delivery"],
        }

    def binding(
        self,
        declaration: dict,
        *,
        binding_id: str,
        target_id: str,
        ref: str,
        thread_id: str | None = None,
    ) -> dict:
        role = declaration["role"]
        return {
            "binding_id": binding_id,
            "target_id": target_id,
            "ref": ref,
            "requested_role": role,
            "thread_id": thread_id or f"thread-{binding_id}",
            "role": {"value": role, "state": "VERIFIED", "provenance": "runtime identity"},
            "model": {"value": declaration["model"], "state": "VERIFIED", "provenance": "runtime identity"},
            "reasoning": {"value": declaration["reasoning"], "state": "VERIFIED", "provenance": "runtime identity"},
            "permissions": {"value": declaration["permissions"], "state": "VERIFIED", "provenance": "runtime identity"},
            "capability_evidence": {
                "verified": list(declaration["capabilities"]),
                "absent": list(declaration["forbidden_capabilities"]),
                "provenance": "runtime capability receipt",
            },
        }

    def packet_blob(
        self, blob_id: str, kind: str, purpose: str, source: str, revision: str, content: str
    ) -> dict:
        return {
            "id": blob_id,
            "kind": kind,
            "purpose": purpose,
            "source": source,
            "revision": revision,
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "content": content,
        }

    def replace_review_packet(self, review: dict, packet: dict) -> None:
        snapshot = validation.canonical_json_bytes(packet).decode()
        review["packet_snapshot"] = snapshot
        review["packet_digest"] = hashlib.sha256(snapshot.encode()).hexdigest()

    def review_packet(
        self,
        record: dict,
        *,
        role: str,
        binding_id: str,
        task: str,
        lens: str,
        stage: str = "I0",
        context_mode: str = "FRESH_ISOLATED",
        originating_review_id: str | None = None,
        originating_binding_id: str | None = None,
        originating_thread_id: str | None = None,
        finding_ids: list[str] | None = None,
    ) -> tuple[str, str, str]:
        declaration = next(
            item
            for item in record["team_context"]["workflow_roles"]
            if item["role"] == role
        )
        final_ref = record["targets"][0]["final_ref"]
        manifest = validation.load_toml(validation.MANIFEST)
        config_source = f".codex/{manifest['roles'][role]['config']}"
        policy_sources = sorted(
            set(manifest["roles"][role]["required_policies"])
            | {".codex/binding-preflight.md"}
        )
        config_content = (validation.CODEX.parent / config_source).read_text(encoding="utf-8")
        policies = [
            self.packet_blob(
                f"policy-{index}",
                "REPO_BLOB",
                "POLICY",
                source,
                final_ref,
                (validation.CODEX.parent / source).read_text(encoding="utf-8"),
            )
            for index, source in enumerate(policy_sources, 1)
        ]
        name_status = self.repository_name_status
        repairs = []
        if context_mode != "FRESH_ISOLATED":
            repairs = [
                self.packet_blob(
                    "repair-evidence", "INLINE", "REPAIR", "bounded-repair", final_ref, "repair evidence"
                )
            ]
        packet_id = f"packet-{binding_id}"
        packet = {
            "schema_version": 2,
            "packet_id": packet_id,
            "workflow": {
                "run_id": record["run_id"],
                "workflow": record["workflow"],
                "target_id": "target-1",
                "stage": stage,
                "context_mode": context_mode,
            },
            "target": {
                "kind": "ticket",
                "id": "251",
                "revision": final_ref,
                "url": "https://example.invalid/issues/251",
                "requirements": [
                    self.packet_blob(
                        "requirement", "INLINE", "REQUIREMENT", "issue-251", final_ref, "required outcome"
                    )
                ],
            },
            "repository": {
                "mode": "COMMIT",
                "repository_id": record["repository"]["repository_id"],
                "worktree": str(self.repository),
                "base_ref": record["repository"]["base_ref"],
                "target_ref": final_ref,
                "tree": self.target_tree,
                "changed_path_count": self.repository_changed_path_count,
                "diff": self.packet_blob(
                    "repository-diff", "DIFF", "DIFF", "git-diff",
                    f"{record['repository']['base_ref']}..{final_ref}",
                    self.repository_diff,
                ),
                "name_status": self.packet_blob(
                    "repository-name-status", "NAME_STATUS", "NAME_STATUS",
                    "git-name-status",
                    f"{record['repository']['base_ref']}..{final_ref}",
                    name_status,
                ),
            },
            "role": {
                "requested_role": role,
                "config": self.packet_blob(
                    "role-config", "REPO_BLOB", "ROLE_CONFIG", config_source, final_ref, config_content
                ),
                "model": declaration["model"],
                "reasoning": declaration["reasoning"],
                "permissions": declaration["permissions"],
                "required_capabilities": list(declaration["capabilities"]),
                "forbidden_capabilities": list(declaration["forbidden_capabilities"]),
                "policies": policies,
            },
            "binding_request": {
                "binding_id": binding_id,
                "allowed_actual_roles": [role, "isolated_cli_reviewer"],
                "launch": {
                    "ignore_user_config": True,
                    "model": declaration["model"],
                    "reasoning": declaration["reasoning"],
                    "approval_policy": "never",
                    "sandbox": "read-only",
                },
                "originating_review_id": originating_review_id,
                "originating_binding_id": originating_binding_id,
                "originating_thread_id": originating_thread_id,
            },
            "task": {"text": task, "lens": lens, "output_contract": "findings or PASS with evidence"},
            "artifacts": [],
            "lineage": {"finding_ids": finding_ids or [], "repair_evidence": repairs},
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
        snapshot = validation.canonical_json_bytes(packet).decode()
        return packet_id, snapshot, hashlib.sha256(snapshot.encode()).hexdigest()

    def qualified_record(self) -> dict:
        record = validation.initial_record(
            "qualified-fixture",
            "deliver-focused-change",
            "ticket",
            "251",
            self.base_ref,
            self.team,
            "2026-07-15T10:00:00Z",
        )
        target_id = "target-1"
        final_ref = self.target_ref
        head_tree = self.head_tree_digest
        record["targets"][0]["final_ref"] = final_ref
        record["repository"].update(
            {
                "repository_id": self.repository_id,
                "base_tree": self.base_tree,
                "head_ref": final_ref,
                "head_tree": head_tree,
            }
        )
        environment_payload = {
            "kind": "LOCAL",
            "platform": "linux-x86_64",
            "runtime": "CPython 3.13",
            "container_image": None,
            "lock_digest": "e" * 64,
        }
        environment = {
            **environment_payload,
            "digest": validation.canonical_fingerprint(environment_payload),
        }
        for kind, suffix in (
            ("REPOSITORY", "repo"),
            ("TREE", "tree"),
            ("PACKAGE", "package"),
            ("MODULE", "module"),
            ("WHEEL", "wheel"),
        ):
            record["artifacts"].append(
                {
                    "id": f"artifact-{suffix}",
                    "version": 1,
                    "target_id": target_id,
                    "ref": final_ref,
                    "kind": kind,
                    "identifier": (
                        record["repository"]["repository_id"]
                        if kind == "REPOSITORY"
                        else suffix
                    ),
                    "digest": (
                        head_tree
                        if kind == "REPOSITORY"
                        else validation.canonical_fingerprint([kind, final_ref])
                    ),
                    "built_at": "2026-07-15T10:20:00Z",
                    "environment_digest": environment["digest"],
                }
            )
        declarations = [
            record["team_context"]["root"],
            *record["team_context"]["workflow_roles"],
        ]
        for declaration in declarations:
            role = declaration["role"]
            record["bindings"].append(
                self.binding(
                    declaration,
                    binding_id=f"runtime-{role}",
                    target_id=target_id,
                    ref=final_ref,
                )
            )
        criterion_evidence = {
            "id": "criterion-evidence",
            "source_kind": "ROLE",
            "source": "focused_verifier",
            "target_id": target_id,
            "ref": final_ref,
            "summary": "criterion falsification passed",
            "evidence_state": "FRESH",
        }
        record["criteria"] = [
            {
                "id": "criterion-1",
                "version": 1,
                "target_id": target_id,
                "text": "The requested outcome is demonstrated",
                "status": "PASS",
                "na_justification": None,
                "skip_id": None,
                "evidence": [criterion_evidence],
            }
        ]
        reviewer_roles = [
            item["role"]
            for item in record["team_context"]["workflow_roles"]
            if item["kind"] == "reviewer"
        ]
        for index, role in enumerate(reviewer_roles):
            binding_id = f"runtime-{role}"
            task = f"Review final ref as {role}"
            lens = f"independent-lens-{index}"
            packet_id, packet_snapshot, packet_digest = self.review_packet(
                record,
                role=role,
                binding_id=binding_id,
                task=task,
                lens=lens,
            )
            record["reviews"].append(
                {
                    "id": f"review-{role}",
                    "version": 1,
                    "target_id": target_id,
                    "ref": final_ref,
                    "stage": "I0",
                    "reviewer_role": role,
                    "binding_id": binding_id,
                    "task": task,
                    "lens": lens,
                    "context_mode": "FRESH_ISOLATED",
                    "originating_review_id": None,
                    "originating_thread_id": None,
                    "packet_id": packet_id,
                    "packet_digest": packet_digest,
                    "packet_snapshot": packet_snapshot,
                    "started_at": f"2026-07-15T10:{1 + index:02d}:00Z",
                    "ended_at": f"2026-07-15T10:{2 + index:02d}:00Z",
                    "result": "PASS",
                    "finding_ids": [],
                    "evidence": ["attempted falsification"],
                    "evidence_state": "FRESH",
                }
            )
        record["checks"] = [
            {
                "id": "check-full",
                "version": 1,
                "target_id": target_id,
                "ref": final_ref,
                "kind": "FULL_GATE",
                "command": "python3 -m unittest",
                "started_at": "2026-07-15T10:15:00Z",
                "ended_at": "2026-07-15T10:19:00Z",
                "result": "PASS",
                "artifact_ids": [item["id"] for item in record["artifacts"]],
                "environment": environment,
                "invalidation_rule": "any head, tree, artifact, lock, runtime, or platform change",
                "validity": "FRESH",
                "skip_id": None,
                "summary": "complete gate passed",
            }
        ]
        record["external_evidence"] = [
            {
                "id": "external-tracking",
                "version": 1,
                "target_id": target_id,
                "source_pr": "owner/repo#1",
                "source_head": final_ref,
                "observed_at": "2026-07-15T10:21:00Z",
                "invalidation_rule": "head or tracking state changes",
                "evidence_state": "FRESH",
                "summary": "tracking gate observed",
            }
        ]
        record["external_gates"] = [
            {
                "id": "gate-tracking",
                "version": 1,
                "target_id": target_id,
                "ref": final_ref,
                "kind": "TRACKING_READY",
                "required": True,
                "status": "SATISFIED",
                "checked_at": "2026-07-15T10:22:00Z",
                "evidence_ids": ["external-tracking"],
                "skip_id": None,
            }
        ]
        record["deliveries"] = [
            {
                "id": "delivery-1",
                "version": 1,
                "target_id": target_id,
                "ref": final_ref,
                "mode": "PULL_REQUEST",
                "status": "DELIVERED",
                "branch": "feature/workflow-controls",
                "commit": final_ref,
                "pull_request": "owner/repo#1",
                "at": "2026-07-15T10:31:00Z",
                "evidence": ["ready pull request"],
            }
        ]
        for declaration in declarations:
            record["obligations"].append(
                {
                    "id": f"binding-{declaration['role']}", "version": 1, "kind": "BINDING",
                    "target_id": target_id, "ref": final_ref, "stage": None,
                    "role": declaration["role"], "status": "SATISFIED",
                    "evidence_ids": [f"runtime-{declaration['role']}"], "justification": None, "skip_id": None,
                }
            )
        for role in reviewer_roles:
            record["obligations"].append(
                {
                    "id": f"review-obligation-{role}", "version": 1, "kind": "REVIEW",
                    "target_id": target_id, "ref": final_ref, "stage": "I0", "role": role,
                    "status": "SATISFIED", "evidence_ids": [f"review-{role}"],
                    "justification": None, "skip_id": None,
                }
            )
        for kind, evidence_id in (
            ("CRITERIA", "criterion-1"), ("CHECK", "check-full"),
            ("QUALIFICATION", "check-full"), ("EXTERNAL_GATE", "gate-tracking"),
            ("DELIVERY", "delivery-1"),
        ):
            record["obligations"].append(
                {
                    "id": f"core-{kind.lower()}", "version": 1, "kind": kind,
                    "target_id": target_id, "ref": final_ref, "stage": None, "role": None,
                    "status": "SATISFIED", "evidence_ids": [evidence_id],
                    "justification": None, "skip_id": None,
                }
            )
        record["status"] = "DELIVERED"
        events = record["events"]
        events.extend(
            [
                validation.v3_event(3, "PHASE_ENTERED", "2026-07-15T10:00:01Z", from_state="INTAKE", to_state="DISCOVERY"),
                validation.v3_event(4, "PHASE_ENTERED", "2026-07-15T10:00:02Z", from_state="DISCOVERY", to_state="IMPLEMENTATION"),
                validation.v3_event(5, "PHASE_ENTERED", "2026-07-15T10:00:03Z", from_state="IMPLEMENTATION", to_state="REVIEW"),
                validation.v3_event(6, "STAGE_STARTED", "2026-07-15T10:00:04Z", target_id=target_id, ref=final_ref, stage="I0"),
                validation.v3_event(7, "STAGE_COMPLETED", "2026-07-15T10:14:30Z", target_id=target_id, ref=final_ref, stage="I0"),
                validation.v3_event(8, "PHASE_ENTERED", "2026-07-15T10:20:00Z", from_state="REVIEW", to_state="QUALIFICATION"),
                validation.v3_event(9, "QUALIFICATION_RECORDED", "2026-07-15T10:30:00Z", target_id=target_id, ref=final_ref, related_id="core-qualification", digest=validation.canonical_fingerprint([target_id, final_ref, "qualified"])),
                validation.v3_event(10, "PHASE_ENTERED", "2026-07-15T10:31:00Z", from_state="QUALIFICATION", to_state="DELIVERED"),
                validation.v3_event(11, "RUN_ENDED", "2026-07-15T10:32:00Z"),
            ]
        )
        self.add_valid_c1_history(record)
        record["obligations"].append(
            {
                "id": "core-connector",
                "version": 1,
                "kind": "CONNECTOR",
                "target_id": target_id,
                "ref": final_ref,
                "stage": "C1",
                "role": None,
                "status": "SATISFIED",
                "evidence_ids": ["connector-c1"],
                "justification": None,
                "skip_id": None,
            }
        )
        return record

    def assert_semantic_failure(self, record: dict, pattern: str) -> None:
        validation.validate_record(record, self.team)
        with self.assertRaisesRegex(validation.ValidationError, pattern):
            self.assert_qualified(record)

    def assert_qualified(self, record: dict) -> dict:
        return validation.assert_record_qualified(
            record, self.team, trusted_worktree=self.repository
        )

    def add_capability_deficient_review_binding(
        self,
        record: dict,
        *,
        role: str,
        review_id: str,
        stage: str,
        started_at: str,
        ended_at: str,
    ) -> str:
        declaration = next(
            item
            for item in record["team_context"]["workflow_roles"]
            if item["role"] == role
        )
        final_ref = record["targets"][0]["final_ref"]
        binding_id = f"runtime-{role}-capability-deficient"
        binding = self.binding(
            declaration,
            binding_id=binding_id,
            target_id="target-1",
            ref=final_ref,
        )
        binding["capability_evidence"]["verified"] = binding[
            "capability_evidence"
        ]["verified"][1:]
        record["bindings"].append(binding)
        task = f"Review final ref as {role} through a deficient invocation"
        lens = f"adversarial-{role}-binding"
        packet_id, packet_snapshot, packet_digest = self.review_packet(
            record,
            role=role,
            binding_id=binding_id,
            task=task,
            lens=lens,
            stage=stage,
        )
        record["reviews"].append(
            {
                "id": review_id,
                "version": 1,
                "target_id": "target-1",
                "ref": final_ref,
                "stage": stage,
                "reviewer_role": role,
                "binding_id": binding_id,
                "task": task,
                "lens": lens,
                "context_mode": "FRESH_ISOLATED",
                "originating_review_id": None,
                "originating_thread_id": None,
                "packet_id": packet_id,
                "packet_digest": packet_digest,
                "packet_snapshot": packet_snapshot,
                "started_at": started_at,
                "ended_at": ended_at,
                "result": "PASS",
                "finding_ids": [],
                "evidence": ["attempted falsification"],
                "evidence_state": "FRESH",
            }
        )
        return review_id

    def renumber_events(self, record: dict) -> None:
        record["events"].sort(key=lambda event: event["at"])
        for sequence, event in enumerate(record["events"], 1):
            event["sequence"] = sequence

    def add_valid_c1_history(self, record: dict) -> None:
        target_id = "target-1"
        final_ref = record["targets"][0]["final_ref"]
        actor = "chatgpt-codex-connector[bot]"

        def reaction(event_id: int, content: str, occurred_at: str) -> dict:
            return {
                "type": "REACTION",
                "id": event_id,
                "actor": actor,
                "occurred_at": occurred_at,
                "content": content,
                "target_kind": "PR_DESCRIPTION",
                "target_id": 1,
                "on_target": True,
                "commit_id": None,
                "commit_binding": "TARGET_BOUND",
            }

        def snapshot(
            observed_at: str,
            elapsed: float,
            events: list[dict],
            *,
            pickup: float | None = None,
            terminal: float | None = None,
        ) -> tuple[str, str, str]:
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
                    "requested_at": "2026-07-15T10:22:00Z",
                    "target_kind": "PR_DESCRIPTION",
                    "target_id": 1,
                    "trigger_normalized": None,
                },
                "heads": {
                    "opening": final_ref,
                    "requested": final_ref,
                    "current": final_ref,
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
            raw = validation.canonical_json_bytes(report).decode()
            return raw, hashlib.sha256(raw.encode()).hexdigest(), state

        start_snapshot, start_digest, _ = snapshot(
            "2026-07-15T10:22:00.250000Z", 0.25, []
        )
        pickup_events = [reaction(1, "eyes", "2026-07-15T10:22:05Z")]
        pickup_snapshot, pickup_digest, _ = snapshot(
            "2026-07-15T10:23:00Z", 60.0, pickup_events, pickup=5.0
        )
        terminal_events = [reaction(2, "+1", "2026-07-15T10:23:50Z")]
        terminal_snapshot, terminal_digest, terminal_state = snapshot(
            "2026-07-15T10:24:00Z",
            120.0,
            terminal_events,
            pickup=110.0,
            terminal=110.0,
        )
        record["connector_rounds"].append(
            {
                "id": "connector-c1",
                "version": 1,
                "target_id": target_id,
                "round": "C1",
                "repository": "owner/repo",
                "pull_request": "owner/repo#1",
                "ref": final_ref,
                "trigger_kind": "AUTOMATIC",
                "trigger_id": None,
                "anchor_kind": "PULL_REQUEST_DESCRIPTION",
                "anchor_id": "owner/repo#1",
                "state": terminal_state,
                "started_at": "2026-07-15T10:22:00Z",
                "completed_at": "2026-07-15T10:24:00Z",
                "finding_ids": [],
                "observation_ids": ["c1-start", "c1-pickup", "c1-removal", "c1-terminal"],
            }
        )
        record["events"].extend(
            [
                validation.v3_event(
                    0,
                    "CONNECTOR_ROUND_STARTED",
                    "2026-07-15T10:22:00.250000Z",
                    actor="root",
                    target_id=target_id,
                    ref=final_ref,
                    stage="C1",
                    reason="AUTOMATIC",
                    related_id="connector-c1",
                    observation_id="c1-start",
                    external_id=None,
                    latency_ms=250,
                    anchor_kind="PULL_REQUEST_DESCRIPTION",
                    anchor_id="owner/repo#1",
                    on_target=True,
                    from_state=None,
                    to_state="PENDING_PICKUP",
                    digest=start_digest,
                    observation_snapshot=start_snapshot,
                ),
                validation.v3_event(
                    0,
                    "CONNECTOR_PICKUP_OBSERVED",
                    "2026-07-15T10:23:00Z",
                    actor=actor,
                    target_id=target_id,
                    ref=final_ref,
                    stage="C1",
                    reason="eyes",
                    related_id="connector-c1",
                    observation_id="c1-pickup",
                    external_id="1",
                    latency_ms=5000,
                    anchor_kind="PULL_REQUEST_DESCRIPTION",
                    anchor_id="owner/repo#1",
                    on_target=True,
                    from_state="PENDING_PICKUP",
                    to_state="IN_PROGRESS",
                    digest=pickup_digest,
                    observation_snapshot=pickup_snapshot,
                ),
                validation.v3_event(
                    0,
                    "CONNECTOR_PICKUP_REMOVED",
                    "2026-07-15T10:24:00Z",
                    actor="root",
                    target_id=target_id,
                    ref=final_ref,
                    stage="C1",
                    reason="eyes_removed",
                    related_id="connector-c1",
                    observation_id="c1-removal",
                    external_id="1",
                    latency_ms=120000,
                    anchor_kind="PULL_REQUEST_DESCRIPTION",
                    anchor_id="owner/repo#1",
                    on_target=True,
                    from_state="IN_PROGRESS",
                    to_state="PICKUP_REMOVED",
                    digest=terminal_digest,
                    observation_snapshot=terminal_snapshot,
                ),
                validation.v3_event(
                    0,
                    "CONNECTOR_TERMINAL_OBSERVED",
                    "2026-07-15T10:24:00Z",
                    actor=actor,
                    target_id=target_id,
                    ref=final_ref,
                    stage="C1",
                    reason="+1",
                    related_id="connector-c1",
                    observation_id="c1-terminal",
                    external_id="2",
                    latency_ms=110000,
                    anchor_kind="PULL_REQUEST_DESCRIPTION",
                    anchor_id="owner/repo#1",
                    on_target=True,
                    from_state="PICKUP_REMOVED",
                    to_state="CLEAN",
                    digest=terminal_digest,
                    observation_snapshot=terminal_snapshot,
                ),
            ]
        )
        self.renumber_events(record)

    def add_family_expansions(
        self,
        record: dict,
        count: int,
        *,
        second_controls: bool = False,
        third_authorization: bool = False,
    ) -> None:
        target_id = "target-1"
        final_ref = record["targets"][0]["final_ref"]
        family = "stable-family-id"
        record["findings"].append(
            {
                "id": "family-base", "version": 1, "target_id": target_id,
                "ref": final_ref, "source_kind": "ROLE", "source": "focused_verifier",
                "severity": "MEDIUM", "summary": "bounded base manifestation",
                "decision": "FIX_NOW", "status": "RESOLVED", "duplicate_of": None,
                "stage": "I0", "family": family, "miss_reason": None,
                "miss_reason_detail": None, "evidence": ["bounded synthesis"],
            }
        )
        reasons = ["FIXTURE_GAP", "INTERACTION_MISSED", "REMEDIATION_REGRESSION"]
        inserted_events = []
        for ordinal in range(1, count + 1):
            finding_id = f"family-expansion-{ordinal}"
            stage = f"I{ordinal}"
            record["findings"].append(
                {
                    "id": finding_id, "version": 1, "target_id": target_id,
                    "ref": final_ref, "source_kind": "ROLE", "source": "focused_verifier",
                    "severity": "MEDIUM", "summary": f"material expansion {ordinal}",
                    "decision": "FIX_NOW", "status": "RESOLVED", "duplicate_of": None,
                    "stage": stage, "family": family, "miss_reason": reasons[ordinal - 1],
                    "miss_reason_detail": None, "evidence": [f"expansion evidence {ordinal}"],
                }
            )
            inserted_events.append(
                validation.v3_event(
                    0, "FAMILY_EXPANSION_RECORDED",
                    f"2026-07-15T10:{6 + ordinal:02d}:00Z",
                    target_id=target_id, ref=final_ref, actor="root", stage=stage,
                    from_state=family, to_state=f"EXPANSION_{ordinal}",
                    reason=reasons[ordinal - 1], related_id=finding_id,
                    digest=validation.canonical_fingerprint([family, ordinal, final_ref]),
                )
            )
            if ordinal == 2 and second_controls:
                inserted_events.extend(
                    [
                        validation.v3_event(
                            0, "FAMILY_BOUNDARY_STOP", "2026-07-15T10:09:00Z",
                            target_id=target_id, ref=final_ref, actor="root", stage=stage,
                            from_state="EXPANSION_2",
                            to_state="ROOT_CAUSE_DESIGN_BOUNDARY_STOP",
                            reason="root-cause and design-boundary analysis complete",
                            related_id=finding_id,
                            digest=validation.canonical_fingerprint([finding_id, "boundary"]),
                        ),
                        validation.v3_event(
                            0, "PUBLICATION_CHALLENGE_REQUIRED",
                            "2026-07-15T10:10:00Z", target_id=target_id,
                            ref=final_ref, actor="root", stage=stage,
                            from_state="EXPANSION_2",
                            to_state="FRESH_PUBLICATION_CHALLENGE_REQUIRED",
                            reason="second expansion requires a fresh challenge",
                            related_id=finding_id,
                            digest=validation.canonical_fingerprint([finding_id, "challenge"]),
                        ),
                    ]
                )
                record["obligations"].extend(
                    [
                        {
                            "id": "family-boundary-analysis", "version": 1,
                            "kind": "FAMILY_BOUNDARY_ANALYSIS", "target_id": target_id,
                            "ref": final_ref, "stage": stage, "role": "root",
                            "status": "SATISFIED", "evidence_ids": [finding_id],
                            "justification": "root cause and design boundary recorded",
                            "skip_id": None,
                        },
                        {
                            "id": "family-publication-challenge", "version": 1,
                            "kind": "PUBLICATION_CHALLENGE", "target_id": target_id,
                            "ref": final_ref, "stage": stage,
                            "role": "publication_challenger", "status": "SATISFIED",
                            "evidence_ids": ["review-publication_challenger"],
                            "justification": "fresh challenge required by expansion two",
                            "skip_id": None,
                        },
                    ]
                )
                publication_review = next(
                    review for review in record["reviews"]
                    if review["reviewer_role"] == "publication_challenger"
                )
                publication_review["started_at"] = "2026-07-15T10:13:00Z"
                publication_review["ended_at"] = "2026-07-15T10:14:00Z"
            if ordinal == 3 and third_authorization:
                inserted_events.append(
                    validation.v3_event(
                        0, "MAINTAINER_DECISION_RECORDED",
                        "2026-07-15T10:12:00Z", target_id=target_id,
                        ref=final_ref, actor="maintainer", stage=stage,
                        from_state="EXPANSION_3", to_state="BOUNDED_REDESIGN",
                        reason="bounded redesign authorized within recorded constraints",
                        related_id=finding_id,
                        digest=validation.canonical_fingerprint([finding_id, "redesign"]),
                    )
                )
                record["obligations"].append(
                    {
                        "id": "family-maintainer-decision", "version": 1,
                        "kind": "MAINTAINER_DECISION", "target_id": target_id,
                        "ref": final_ref, "stage": stage, "role": "maintainer",
                        "status": "SATISFIED", "evidence_ids": [finding_id],
                        "justification": "bounded redesign only; preserve frozen scope",
                        "skip_id": None,
                    }
                )
        qualification_index = next(
            index for index, event in enumerate(record["events"])
            if event["type"] == "PHASE_ENTERED" and event["to_state"] == "QUALIFICATION"
        )
        record["events"][qualification_index:qualification_index] = inserted_events
        record["events"].sort(key=lambda event: event["at"])
        for sequence, event in enumerate(record["events"], 1):
            event["sequence"] = sequence

    def add_targeted_continuation(
        self, record: dict, *, thread_id: str
    ) -> None:
        role = "focused_verifier"
        original_review = next(
            item for item in record["reviews"] if item["reviewer_role"] == role
        )
        original_binding = next(
            item
            for item in record["bindings"]
            if item["binding_id"] == original_review["binding_id"]
        )
        declaration = next(
            item
            for item in record["team_context"]["workflow_roles"]
            if item["role"] == role
        )
        binding_id = "runtime-focused-verifier-continuation"
        record["bindings"].append(
            self.binding(
                declaration,
                binding_id=binding_id,
                target_id="target-1",
                ref=record["targets"][0]["final_ref"],
                thread_id=thread_id,
            )
        )
        finding_id = "continuation-finding"
        record["findings"].append(
            {
                "id": finding_id,
                "version": 1,
                "target_id": "target-1",
                "ref": record["targets"][0]["final_ref"],
                "source_kind": "ROLE",
                "source": role,
                "severity": "MEDIUM",
                "summary": "bounded reviewer finding",
                "decision": "FIX_NOW",
                "status": "RESOLVED",
                "duplicate_of": None,
                "stage": "I0",
                "family": "continuation-family",
                "miss_reason": None,
                "miss_reason_detail": None,
                "evidence": ["bounded repair"],
            }
        )
        task = "Verify the bounded repair"
        lens = original_review["lens"]
        packet_id, packet_snapshot, packet_digest = self.review_packet(
            record,
            role=role,
            binding_id=binding_id,
            task=task,
            lens=lens,
            context_mode="TARGETED_CONTINUATION",
            originating_review_id=original_review["id"],
            originating_binding_id=original_binding["binding_id"],
            originating_thread_id=thread_id,
            finding_ids=[finding_id],
        )
        review_id = "review-focused-verifier-continuation"
        record["reviews"].append(
            {
                "id": review_id,
                "version": 1,
                "target_id": "target-1",
                "ref": record["targets"][0]["final_ref"],
                "stage": "I0",
                "reviewer_role": role,
                "binding_id": binding_id,
                "task": task,
                "lens": lens,
                "context_mode": "TARGETED_CONTINUATION",
                "originating_review_id": original_review["id"],
                "originating_thread_id": thread_id,
                "packet_id": packet_id,
                "packet_digest": packet_digest,
                "packet_snapshot": packet_snapshot,
                "started_at": "2026-07-15T10:10:00Z",
                "ended_at": "2026-07-15T10:11:00Z",
                "result": "PASS",
                "finding_ids": [finding_id],
                "evidence": ["bounded repair falsification passed"],
                "evidence_state": "FRESH",
            }
        )
        next(
            item
            for item in record["obligations"]
            if item["kind"] == "REVIEW" and item["role"] == role
        )["evidence_ids"] = [review_id]
        next(
            item
            for item in record["obligations"]
            if item["kind"] == "BINDING" and item["role"] == role
        )["evidence_ids"] = [binding_id]

    def test_complete_fixture_qualifies(self) -> None:
        self.assertTrue(self.assert_qualified(self.qualified_record())["qualified"])

    def test_self_consistently_forged_review_diff_cannot_qualify(self) -> None:
        record = self.qualified_record()
        review = next(
            item
            for item in record["reviews"]
            if item["reviewer_role"] == "code_quality_reviewer"
        )
        packet = json.loads(review["packet_snapshot"])
        forged = "forged reviewer diff\n"
        packet["repository"]["diff"]["content"] = forged
        packet["repository"]["diff"]["sha256"] = hashlib.sha256(
            forged.encode()
        ).hexdigest()
        self.replace_review_packet(review, packet)
        validation.validate_record(record, self.team)
        with self.assertRaisesRegex(
            validation.ValidationError, "review packet repository diff drifted"
        ):
            self.assert_qualified(record)

    def test_self_consistently_forged_name_status_cannot_qualify(self) -> None:
        record = self.qualified_record()
        review = next(
            item
            for item in record["reviews"]
            if item["reviewer_role"] == "code_quality_reviewer"
        )
        packet = json.loads(review["packet_snapshot"])
        forged = "M\tdocs/forged.md\n"
        packet["repository"]["name_status"]["content"] = forged
        packet["repository"]["name_status"]["sha256"] = hashlib.sha256(
            forged.encode()
        ).hexdigest()
        self.replace_review_packet(review, packet)
        validation.validate_record(record, self.team)
        with self.assertRaisesRegex(
            validation.ValidationError,
            "review packet repository name-status drifted",
        ):
            self.assert_qualified(record)

    def test_self_consistently_forged_repository_blob_cannot_qualify(self) -> None:
        record = self.qualified_record()
        review = next(
            item
            for item in record["reviews"]
            if item["reviewer_role"] == "code_quality_reviewer"
        )
        packet = json.loads(review["packet_snapshot"])
        packet["artifacts"].append(
            self.packet_blob(
                "forged-source",
                "REPO_BLOB",
                "SOURCE",
                "docs/contract.md",
                self.target_ref,
                "forged repository source\n",
            )
        )
        self.replace_review_packet(review, packet)
        validation.validate_record(record, self.team)
        with self.assertRaisesRegex(
            validation.ValidationError,
            "review packet repository blob drifted: docs/contract.md",
        ):
            self.assert_qualified(record)

    def test_self_consistently_forged_run_blob_cannot_qualify(self) -> None:
        record = self.qualified_record()
        review = next(
            item
            for item in record["reviews"]
            if item["reviewer_role"] == "code_quality_reviewer"
        )
        packet = json.loads(review["packet_snapshot"])
        packet["target"]["requirements"].append(
            self.packet_blob(
                "forged-run-requirement",
                "RUN_BLOB",
                "REQUIREMENT",
                self.run_requirement_source,
                self.target_ref,
                "forged run requirement\n",
            )
        )
        self.replace_review_packet(review, packet)
        validation.validate_record(record, self.team)
        with self.assertRaisesRegex(
            validation.ValidationError,
            f"review packet run blob drifted: {self.run_requirement_source}",
        ):
            self.assert_qualified(record)

    def test_run_and_repository_blob_namespaces_fail_closed(self) -> None:
        cases = (
            (
                "run-outside-namespace",
                "target",
                self.packet_blob(
                    "misplaced-run-requirement",
                    "RUN_BLOB",
                    "REQUIREMENT",
                    "docs/contract.md",
                    self.target_ref,
                    "base\nchanged\n",
                ),
                "review packet run blob is outside its restricted requirement namespace",
            ),
            (
                "repository-aliases-run-state",
                "artifact",
                self.packet_blob(
                    "aliased-run-source",
                    "REPO_BLOB",
                    "SOURCE",
                    self.run_requirement_source,
                    self.target_ref,
                    "trusted run requirement\n",
                ),
                "review packet repository blob is off-ref or aliases mutable run state",
            ),
        )
        for label, location, blob, pattern in cases:
            with self.subTest(label=label):
                record = self.qualified_record()
                review = next(
                    item
                    for item in record["reviews"]
                    if item["reviewer_role"] == "code_quality_reviewer"
                )
                packet = json.loads(review["packet_snapshot"])
                if location == "target":
                    packet["target"]["requirements"].append(blob)
                else:
                    packet["artifacts"].append(blob)
                self.replace_review_packet(review, packet)
                with self.assertRaisesRegex(validation.ValidationError, pattern):
                    validation.validate_record(record, self.team)

    def test_packet_worktree_cannot_replace_the_trusted_qualification_root(self) -> None:
        record = self.qualified_record()
        review = next(
            item
            for item in record["reviews"]
            if item["reviewer_role"] == "code_quality_reviewer"
        )
        packet = json.loads(review["packet_snapshot"])
        packet["repository"]["worktree"] = str(
            self.repository.parent / "substitute-repository"
        )
        self.replace_review_packet(review, packet)
        validation.validate_record(record, self.team)
        with self.assertRaisesRegex(
            validation.ValidationError,
            "review packet worktree differs from the trusted qualification root",
        ):
            self.assert_qualified(record)

    def test_packet_tree_is_rederived_from_the_trusted_repository(self) -> None:
        record = self.qualified_record()
        review = next(
            item
            for item in record["reviews"]
            if item["reviewer_role"] == "code_quality_reviewer"
        )
        packet = json.loads(review["packet_snapshot"])
        packet["repository"]["tree"] = "f" * 40
        self.replace_review_packet(review, packet)
        validation.validate_record(record, self.team)
        with self.assertRaisesRegex(
            validation.ValidationError, "review packet repository tree drifted"
        ):
            self.assert_qualified(record)

    def test_record_tree_hash_is_rederived_from_the_trusted_repository(self) -> None:
        record = self.qualified_record()
        record["repository"]["head_tree"] = "f" * 64
        repository_artifact = next(
            item for item in record["artifacts"] if item["kind"] == "REPOSITORY"
        )
        repository_artifact["digest"] = "f" * 64
        validation.validate_record(record, self.team)
        with self.assertRaisesRegex(
            validation.ValidationError,
            "review packet repository evidence differs from the qualified record",
        ):
            self.assert_qualified(record)

    def test_check_obligation_rejects_cross_kind_evidence(self) -> None:
        record = self.qualified_record()
        check_obligation = next(
            item for item in record["obligations"] if item["kind"] == "CHECK"
        )
        check_obligation["evidence_ids"] = ["criterion-1"]
        self.assert_semantic_failure(
            record, "CHECK obligation cites cross-kind evidence"
        )

    def test_no_checks_cannot_qualify_via_criterion_evidence(self) -> None:
        record = self.qualified_record()
        record["checks"] = []
        for obligation in record["obligations"]:
            if obligation["kind"] in {"CHECK", "QUALIFICATION"}:
                obligation["evidence_ids"] = ["criterion-1"]
        self.assert_semantic_failure(
            record, "CHECK obligation cites cross-kind evidence"
        )

    def test_external_gate_rejects_stale_off_target_and_off_ref_evidence(self) -> None:
        cases = {}

        stale = self.qualified_record()
        stale["external_evidence"][0]["evidence_state"] = "STALE"
        cases["stale"] = (stale, "external gate evidence is stale or unavailable")

        off_ref = self.qualified_record()
        off_ref["external_evidence"][0]["source_head"] = "f" * 40
        cases["off-ref"] = (off_ref, "external gate evidence is off-ref")

        off_target = self.qualified_record()
        off_target["targets"].append(
            {
                "target_id": "target-2",
                "kind": "ticket",
                "id": "252",
                "initial_ref": off_target["targets"][0]["initial_ref"],
                "final_ref": off_target["targets"][0]["final_ref"],
            }
        )
        off_target["external_evidence"][0]["target_id"] = "target-2"
        cases["off-target"] = (
            off_target,
            "external gate evidence target differs from its gate",
        )

        for label, (record, pattern) in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(validation.ValidationError, pattern):
                    validation.validate_record(record, self.team)

    def test_exact_inherited_external_gate_evidence_remains_valid(self) -> None:
        record = self.qualified_record()
        record["external_evidence"][0]["evidence_state"] = "INHERITED"
        self.assertTrue(self.assert_qualified(record)["qualified"])

        observed_too_late = self.qualified_record()
        observed_too_late["external_evidence"][0]["evidence_state"] = "INHERITED"
        observed_too_late["external_gates"][0]["checked_at"] = (
            "2026-07-15T10:20:00Z"
        )
        with self.assertRaisesRegex(
            validation.ValidationError,
            "external gate evidence was observed after the gate check",
        ):
            validation.validate_record(observed_too_late, self.team)

    def test_repository_head_ref_must_match_a_final_target(self) -> None:
        record = self.qualified_record()
        record["repository"]["head_ref"] = "f" * 40
        with self.assertRaisesRegex(
            validation.ValidationError,
            "review packet base/head differs from the frozen run repository",
        ):
            validation.validate_record(record, self.team)

    def test_repository_head_tree_requires_exact_repository_artifact(self) -> None:
        record = self.qualified_record()
        record["repository"]["head_tree"] = "f" * 64
        self.assert_semantic_failure(
            record, "repository head/tree lacks one exact repository artifact"
        )

    def test_qualification_event_must_name_its_evidence_obligation(self) -> None:
        record = self.qualified_record()
        event = next(
            item
            for item in record["events"]
            if item["type"] == "QUALIFICATION_RECORDED"
        )
        event["related_id"] = "core-check"
        self.assert_semantic_failure(
            record, "lacks exact final-ref qualification event"
        )

    def test_qualified_reviewer_requires_a_non_null_thread(self) -> None:
        record = self.qualified_record()
        binding = next(
            item
            for item in record["bindings"]
            if item["requested_role"] == "focused_verifier"
        )
        binding["thread_id"] = None
        with self.assertRaisesRegex(
            validation.ValidationError, "review binding lacks a runtime thread"
        ):
            validation.validate_record(record, self.team)

    def test_duplicate_reviewer_threads_are_rejected(self) -> None:
        record = self.qualified_record()
        reviewer_roles = {
            item["role"]
            for item in record["team_context"]["workflow_roles"]
            if item["kind"] == "reviewer"
        }
        reviewer_bindings = [
            item
            for item in record["bindings"]
            if item["requested_role"] in reviewer_roles
        ]
        self.assertGreaterEqual(len(reviewer_bindings), 2)
        reviewer_bindings[1]["thread_id"] = reviewer_bindings[0]["thread_id"]
        with self.assertRaisesRegex(
            validation.ValidationError, "runtime thread participates as multiple roles"
        ):
            validation.validate_record(record, self.team)

    def test_targeted_continuation_requires_exact_original_thread(self) -> None:
        record = self.qualified_record()
        self.add_targeted_continuation(record, thread_id="wrong-thread")
        with self.assertRaisesRegex(
            validation.ValidationError,
            "targeted continuation must name the reviewer's exact original thread",
        ):
            validation.validate_record(record, self.team)

    def test_matching_thread_targeted_continuation_qualifies(self) -> None:
        record = self.qualified_record()
        binding = next(
            item
            for item in record["bindings"]
            if item["requested_role"] == "focused_verifier"
        )
        self.add_targeted_continuation(record, thread_id=binding["thread_id"])
        self.assertTrue(self.assert_qualified(record)["qualified"])

    def test_focused_delivery_requires_completed_final_ref_i0_cycle(self) -> None:
        missing = self.qualified_record()
        missing["events"] = [
            event
            for event in missing["events"]
            if event["type"] not in {"STAGE_STARTED", "STAGE_COMPLETED"}
        ]
        self.renumber_events(missing)
        self.assert_semantic_failure(
            missing, "lacks one completed final-ref I0 review cycle"
        )

        incomplete = self.qualified_record()
        incomplete["events"] = [
            event
            for event in incomplete["events"]
            if event["type"] != "STAGE_COMPLETED"
        ]
        self.renumber_events(incomplete)
        with self.assertRaisesRegex(
            validation.ValidationError, "run end must be final with no active work"
        ):
            validation.validate_record(incomplete, self.team)

        wrong_ref = self.qualified_record()
        for event in wrong_ref["events"]:
            if event["type"] in {"STAGE_STARTED", "STAGE_COMPLETED"}:
                event["ref"] = "wrong-review-ref"
        self.assert_semantic_failure(
            wrong_ref, "lacks one completed final-ref I0 review cycle"
        )

    def test_valid_ordered_i0_cycle_qualifies(self) -> None:
        report = self.assert_qualified(self.qualified_record())
        self.assertEqual(
            report["event_report"]["completed_stages"],
            [
                {
                    "target_id": "target-1",
                    "ref": self.target_ref,
                    "stage": "I0",
                    "started_at": "2026-07-15T10:00:04Z",
                    "completed_at": "2026-07-15T10:14:30Z",
                }
            ],
        )

    def test_stage_writer_and_review_activity_after_delivery_are_rejected(self) -> None:
        stage = self.qualified_record()
        stage["events"].extend(
            [
                validation.v3_event(
                    0,
                    "STAGE_STARTED",
                    "2026-07-15T10:31:10Z",
                    target_id="target-1",
                    ref=self.target_ref,
                    stage="I1",
                ),
                validation.v3_event(
                    0,
                    "STAGE_COMPLETED",
                    "2026-07-15T10:31:20Z",
                    target_id="target-1",
                    ref=self.target_ref,
                    stage="I1",
                ),
            ]
        )
        self.renumber_events(stage)

        writer = self.qualified_record()
        writer["events"].extend(
            [
                validation.v3_event(
                    0,
                    "WRITER_STARTED",
                    "2026-07-15T10:31:10Z",
                    target_id="target-1",
                    ref=self.target_ref,
                    actor="focused_worker",
                    reason="late writer activity",
                ),
                validation.v3_event(
                    0,
                    "WRITER_STOPPED",
                    "2026-07-15T10:31:20Z",
                    target_id="target-1",
                    ref=self.target_ref,
                    actor="focused_worker",
                    reason="late writer activity stopped",
                ),
            ]
        )
        self.renumber_events(writer)

        review = self.qualified_record()
        review["reviews"][0]["ended_at"] = "2026-07-15T10:31:30Z"

        for label, record, pattern in (
            ("stage", stage, "semantic activity occurred after DELIVERED"),
            ("writer", writer, "semantic activity occurred after DELIVERED"),
            ("review", review, "review activity occurred after DELIVERED"),
        ):
            with self.subTest(label=label):
                with self.assertRaisesRegex(validation.ValidationError, pattern):
                    validation.validate_record(record, self.team)

    def test_valid_c1_history_validates(self) -> None:
        record = self.qualified_record()
        report = validation.validate_record(record, self.team)
        self.assertEqual(report["event_report"]["observation_count"], 4)

    def test_unknown_connector_observation_fails_without_type_error(self) -> None:
        record = self.qualified_record()
        record["connector_rounds"][0]["observation_ids"].append(
            "unknown-observation"
        )
        with self.assertRaisesRegex(
            validation.ValidationError, "connector round cites unknown observation"
        ):
            validation.validate_record(record, self.team)

    def test_connector_snapshot_tampering_is_rejected(self) -> None:
        record = self.qualified_record()
        terminal = next(
            item
            for item in record["events"]
            if item["type"] == "CONNECTOR_TERMINAL_OBSERVED"
        )
        report = json.loads(terminal["observation_snapshot"])
        report["events"][0]["content"] = "eyes"
        terminal["observation_snapshot"] = validation.canonical_json_bytes(report).decode()
        with self.assertRaisesRegex(
            validation.ValidationError, "connector observation snapshot digest mismatch"
        ):
            validation.validate_record(record, self.team)

    def test_unexpected_reaction_is_preserved_as_durable_protocol_variance(self) -> None:
        record = self.qualified_record()
        actor = "chatgpt-codex-connector[bot]"
        unexpected = {
            "type": "REACTION",
            "id": 99,
            "actor": actor,
            "occurred_at": "2026-07-15T10:22:30Z",
            "content": "heart",
            "target_kind": "PR_DESCRIPTION",
            "target_id": 1,
            "on_target": True,
            "commit_id": None,
            "commit_binding": "TARGET_BOUND",
        }
        start = next(
            item
            for item in record["events"]
            if item["type"] == "CONNECTOR_ROUND_STARTED"
        )
        report = json.loads(start["observation_snapshot"])
        report["observed_at"] = "2026-07-15T10:22:30Z"
        report["events"] = [unexpected]
        report["ids"]["reaction_ids"] = [99]
        report["latency_seconds"] = {
            "observed": 30.0,
            "pickup": None,
            "terminal": None,
            "off_target_terminal": None,
        }
        report["state"], report["protocol_variances"] = (
            validation.connector_protocol.derive_state(report["events"], 30.0)
        )
        snapshot = validation.canonical_json_bytes(report).decode()
        digest = hashlib.sha256(snapshot.encode()).hexdigest()
        variance = validation.v3_event(
            0,
            "CONNECTOR_TERMINAL_OBSERVED",
            report["observed_at"],
            actor=actor,
            target_id="target-1",
            ref=record["targets"][0]["final_ref"],
            stage="C1",
            reason="heart",
            related_id="connector-c1",
            observation_id="c1-variance",
            external_id="99",
            latency_ms=30000,
            anchor_kind="PULL_REQUEST_DESCRIPTION",
            anchor_id="owner/repo#1",
            on_target=True,
            from_state="PENDING_PICKUP",
            to_state="PROTOCOL_VARIANCE",
            digest=digest,
            observation_snapshot=snapshot,
        )
        record["events"] = [
            item
            for item in record["events"]
            if item["type"]
            not in {
                "CONNECTOR_PICKUP_OBSERVED",
                "CONNECTOR_PICKUP_REMOVED",
                "CONNECTOR_TERMINAL_OBSERVED",
            }
        ]
        record["events"].append(variance)
        self.renumber_events(record)
        connector = record["connector_rounds"][0]
        connector["state"] = "PROTOCOL_VARIANCE"
        connector["completed_at"] = None
        connector["observation_ids"] = ["c1-start", "c1-variance"]

        validation.validate_record(record, self.team)
        self.assertEqual(
            ["UNEXPECTED_ON_TARGET_REACTION"], report["protocol_variances"]
        )
        with self.assertRaisesRegex(
            validation.ValidationError,
            "CONNECTOR obligation evidence is incomplete or off-ref",
        ):
            self.assert_qualified(record)

    def test_pickup_unobserved_enforces_the_exact_two_minute_boundary(self) -> None:
        record = self.qualified_record()
        start = next(
            item
            for item in record["events"]
            if item["type"] == "CONNECTOR_ROUND_STARTED"
        )
        report = json.loads(start["observation_snapshot"])
        report["observed_at"] = "2026-07-15T10:24:00Z"
        report["latency_seconds"]["observed"] = 120.0
        report["state"] = "PICKUP_UNOBSERVED"
        snapshot = validation.canonical_json_bytes(report).decode()
        digest = hashlib.sha256(snapshot.encode()).hexdigest()
        unobserved = validation.v3_event(
            0,
            "CONNECTOR_PICKUP_UNOBSERVED",
            "2026-07-15T10:24:00Z",
            actor="root",
            target_id="target-1",
            ref=record["targets"][0]["final_ref"],
            stage="C1",
            reason="NO_EYES_WITHIN_120_SECONDS",
            related_id="connector-c1",
            observation_id="c1-unobserved",
            external_id=None,
            latency_ms=120000,
            anchor_kind="PULL_REQUEST_DESCRIPTION",
            anchor_id="owner/repo#1",
            on_target=True,
            from_state="PENDING_PICKUP",
            to_state="PICKUP_UNOBSERVED",
            digest=digest,
            observation_snapshot=snapshot,
        )
        record["events"] = [
            item
            for item in record["events"]
            if item["type"]
            not in {
                "CONNECTOR_PICKUP_OBSERVED",
                "CONNECTOR_PICKUP_REMOVED",
                "CONNECTOR_TERMINAL_OBSERVED",
            }
        ]
        record["events"].append(unobserved)
        self.renumber_events(record)
        connector = record["connector_rounds"][0]
        connector["state"] = "PICKUP_UNOBSERVED"
        connector["completed_at"] = None
        connector["observation_ids"] = ["c1-start", "c1-unobserved"]
        validation.validate_record(record, self.team)

        early = copy.deepcopy(record)
        early_event = next(
            item
            for item in early["events"]
            if item["type"] == "CONNECTOR_PICKUP_UNOBSERVED"
        )
        early_report = json.loads(early_event["observation_snapshot"])
        early_report["observed_at"] = "2026-07-15T10:23:59.999000Z"
        early_report["latency_seconds"]["observed"] = 119.999
        early_report["state"] = "PENDING_PICKUP"
        early_snapshot = validation.canonical_json_bytes(early_report).decode()
        early_event["at"] = early_report["observed_at"]
        early_event["latency_ms"] = 119999
        early_event["observation_snapshot"] = early_snapshot
        early_event["digest"] = hashlib.sha256(early_snapshot.encode()).hexdigest()
        self.renumber_events(early)
        with self.assertRaisesRegex(
            validation.ValidationError,
            "pickup-unobserved evidence violates the two-minute boundary",
        ):
            validation.validate_record(early, self.team)

    def test_pr_delivery_without_c1_cannot_qualify(self) -> None:
        record = self.qualified_record()
        record["connector_rounds"] = []
        record["events"] = [
            item for item in record["events"] if not item["type"].startswith("CONNECTOR_")
        ]
        record["obligations"] = [
            item for item in record["obligations"] if item["kind"] != "CONNECTOR"
        ]
        self.renumber_events(record)
        self.assert_semantic_failure(record, "lacks complete CONNECTOR obligation")

    def test_applied_connector_triggers_require_one_exact_c2_round(self) -> None:
        record = self.qualified_record()
        for index in (1, 2):
            record["mutations"].append(
                {
                    "id": f"trigger-mutation-{index}",
                    "version": 1,
                    "target_id": "target-1",
                    "kind": "CONNECTOR_TRIGGER",
                    "at": f"2026-07-15T10:25:0{index}Z",
                    "actor": "root",
                    "ref_before": record["targets"][0]["final_ref"],
                    "ref_after": record["targets"][0]["final_ref"],
                    "external_id": f"trigger-{index}",
                    "summary": "manual connector trigger",
                    "result": "APPLIED",
                    "evidence_state": "FRESH",
                    "project": None,
                }
            )
        with self.assertRaisesRegex(
            validation.ValidationError,
            "successful connector triggers and C2 rounds are not bijective",
        ):
            validation.validate_record(record, self.team)

    def test_second_family_expansion_requires_boundary_and_challenge(self) -> None:
        record = self.qualified_record()
        self.add_family_expansions(record, 2)
        self.assert_semantic_failure(
            record, "second family expansion lacks root-cause/design-boundary stop"
        )

    def test_second_family_expansion_with_fresh_challenge_qualifies(self) -> None:
        record = self.qualified_record()
        self.add_family_expansions(record, 2, second_controls=True)
        report = self.assert_qualified(record)
        self.assertEqual(
            report["family_expansions"],
            [{"target_id": "target-1", "family": "stable-family-id", "expansion_count": 2}],
        )

    def test_publication_challenge_cannot_use_an_unqualified_invocation_binding(self) -> None:
        record = self.qualified_record()
        review_id = self.add_capability_deficient_review_binding(
            record,
            role="publication_challenger",
            review_id="review-publication_challenger-deficient",
            stage="I0",
            started_at="2026-07-15T10:10:00Z",
            ended_at="2026-07-15T10:11:00Z",
        )
        record["obligations"].append(
            {
                "id": "standalone-publication-challenge",
                "version": 1,
                "kind": "PUBLICATION_CHALLENGE",
                "target_id": "target-1",
                "ref": record["targets"][0]["final_ref"],
                "stage": "I0",
                "role": "publication_challenger",
                "status": "SATISFIED",
                "evidence_ids": [review_id],
                "justification": "standalone publication challenge",
                "skip_id": None,
            }
        )
        self.assert_semantic_failure(
            record, "satisfied review obligation uses an unqualified invocation binding"
        )

    def test_second_family_expansion_rejects_inherited_challenge(self) -> None:
        record = self.qualified_record()
        self.add_family_expansions(record, 2, second_controls=True)
        publication_review = next(
            review for review in record["reviews"]
            if review["reviewer_role"] == "publication_challenger"
        )
        publication_review["evidence_state"] = "INHERITED"
        self.assert_semantic_failure(
            record, "later fresh isolated publication challenge"
        )

    def test_third_family_expansion_requires_maintainer_authorization(self) -> None:
        record = self.qualified_record()
        self.add_family_expansions(record, 3, second_controls=True)
        self.assert_semantic_failure(
            record, "third-or-later family expansion lacks maintainer authorization"
        )

    def test_third_family_expansion_with_bounded_redesign_qualifies(self) -> None:
        record = self.qualified_record()
        self.add_family_expansions(
            record, 3, second_controls=True, third_authorization=True
        )
        self.assertTrue(self.assert_qualified(record)["qualified"])

    def test_duplicate_family_finding_does_not_inflate_expansion_count(self) -> None:
        record = self.qualified_record()
        self.add_family_expansions(record, 1)
        record["findings"].append(
            {
                "id": "duplicate-expansion", "version": 1,
                "target_id": "target-1", "ref": self.target_ref,
                "source_kind": "EXTERNAL", "source": "independent-review",
                "severity": "MEDIUM", "summary": "same expansion reported twice",
                "decision": "DUPLICATE", "status": "RESOLVED",
                "duplicate_of": "family-expansion-1", "stage": "I2",
                "family": "stable-family-id", "miss_reason": "INTERACTION_MISSED",
                "miss_reason_detail": None, "evidence": ["duplicate evidence"],
            }
        )
        report = self.assert_qualified(record)
        self.assertEqual(report["family_expansions"][0]["expansion_count"], 1)

    def test_family_expansion_event_must_bind_miss_and_remediation_ref(self) -> None:
        record = self.qualified_record()
        self.add_family_expansions(record, 1)
        expansion_event = next(
            event for event in record["events"]
            if event["type"] == "FAMILY_EXPANSION_RECORDED"
        )
        expansion_event["ref"] = "wrong-remediation-ref"
        with self.assertRaisesRegex(
            validation.ValidationError,
            "does not bind its count/family/miss/ref",
        ):
            validation.validate_record(record, self.team)

    def test_writer_cannot_start_after_second_expansion_before_stop_controls(self) -> None:
        record = self.qualified_record()
        self.add_family_expansions(record, 2)
        qualification_index = next(
            index for index, event in enumerate(record["events"])
            if event["type"] == "PHASE_ENTERED" and event["to_state"] == "QUALIFICATION"
        )
        record["events"][qualification_index:qualification_index] = [
            validation.v3_event(
                0, "WRITER_STARTED", "2026-07-15T10:11:00Z",
                target_id="target-1", ref=self.target_ref, actor="focused_worker",
                reason="routine patching continued",
            ),
            validation.v3_event(
                0, "WRITER_STOPPED", "2026-07-15T10:12:00Z",
                target_id="target-1", ref=self.target_ref, actor="focused_worker",
                reason="routine patching stopped",
            ),
        ]
        record["events"].sort(key=lambda event: event["at"])
        for sequence, event in enumerate(record["events"], 1):
            event["sequence"] = sequence
        with self.assertRaisesRegex(
            validation.ValidationError,
            "second family expansion lacks root-cause/design-boundary stop",
        ):
            validation.validate_record(record, self.team)

    def test_empty_delivered_does_not_false_pass(self) -> None:
        record = validation.initial_record("empty", "deliver-focused-change", "ticket", "1", "a" * 40, self.team, "2026-07-15T09:00:00Z")
        record["status"] = "DELIVERED"
        record["events"].extend([
            validation.v3_event(3, "PHASE_ENTERED", "2026-07-15T09:00:01Z", from_state="INTAKE", to_state="DISCOVERY"),
            validation.v3_event(4, "PHASE_ENTERED", "2026-07-15T09:00:02Z", from_state="DISCOVERY", to_state="QUALIFICATION"),
            validation.v3_event(5, "PHASE_ENTERED", "2026-07-15T09:00:03Z", from_state="QUALIFICATION", to_state="DELIVERED"),
            validation.v3_event(6, "RUN_ENDED", "2026-07-15T09:00:04Z"),
        ])
        self.assert_semantic_failure(record, "lacks exact repository head/tree")

    def test_duplicate_or_contradictory_criteria_are_structurally_rejected(self) -> None:
        record = self.qualified_record()
        duplicate = copy.deepcopy(record["criteria"][0])
        duplicate["id"] = "criterion-2"
        duplicate["status"] = "NOT_APPLICABLE"
        duplicate["na_justification"] = "contradictory duplicate"
        duplicate["skip_id"] = "missing"
        duplicate["evidence"] = []
        record["criteria"].append(duplicate)
        with self.assertRaisesRegex(validation.ValidationError, "duplicate or contradictory criterion text"):
            validation.validate_record(record, self.team)

    def test_failed_check_does_not_false_pass(self) -> None:
        record = self.qualified_record()
        record["checks"][0]["result"] = "FAIL"
        self.assert_semantic_failure(record, "failed/not-run check")

    def test_unresolved_critical_c3_is_rejected(self) -> None:
        record = self.qualified_record()
        record["findings"].append({
            "id": "critical-c3", "version": 1, "target_id": "target-1", "ref": self.target_ref,
            "source_kind": "EXTERNAL", "source": "connector", "severity": "CRITICAL",
            "summary": "forbidden third connector round", "decision": "FIX_NOW", "status": "OPEN",
            "duplicate_of": None, "stage": "C3", "family": None, "miss_reason": None,
            "miss_reason_detail": None, "evidence": [],
        })
        with self.assertRaisesRegex(validation.ValidationError, r"C0 and C3\+ are prohibited"):
            validation.validate_record(record, self.team)

    def test_null_delivery_does_not_false_pass(self) -> None:
        record = self.qualified_record()
        record["deliveries"][0]["pull_request"] = None
        self.assert_semantic_failure(record, "non-null branch/commit/pull request")

    def test_delivered_to_review_reversal_is_rejected(self) -> None:
        record = self.qualified_record()
        record["events"].pop()
        record["events"].append(validation.v3_event(len(record["events"]) + 1, "PHASE_ENTERED", "2026-07-15T10:32:00Z", from_state="DELIVERED", to_state="REVIEW"))
        record["status"] = "REVIEW"
        with self.assertRaisesRegex(validation.ValidationError, "semantic activity occurred after DELIVERED"):
            validation.validate_record(record, self.team)

    def test_workflow_target_mismatch_is_rejected(self) -> None:
        record = validation.initial_record("mismatch", "deliver-functional-slice", "ticket", "1", "a" * 40, self.team)
        with self.assertRaisesRegex(validation.ValidationError, "workflow/target mismatch"):
            validation.validate_record(record, self.team)

    def test_pass_from_unknown_binding_does_not_false_pass(self) -> None:
        record = self.qualified_record()
        binding = next(item for item in record["bindings"] if item["requested_role"] == "focused_verifier")
        for field in ("role", "model", "reasoning", "permissions"):
            binding[field] = {"value": None, "state": "UNKNOWN", "provenance": "runtime did not expose identity"}
        self.assert_semantic_failure(record, "required binding lacks exact strength/capability proof")

    def test_review_cannot_substitute_an_unqualified_invocation_binding(self) -> None:
        record = self.qualified_record()
        review_id = self.add_capability_deficient_review_binding(
            record,
            role="focused_verifier",
            review_id="review-focused_verifier-deficient",
            stage="I0",
            started_at="2026-07-15T10:09:00Z",
            ended_at="2026-07-15T10:10:00Z",
        )
        obligation = next(
            item
            for item in record["obligations"]
            if item["kind"] == "REVIEW" and item["role"] == "focused_verifier"
        )
        obligation["evidence_ids"] = [review_id]
        self.assert_semantic_failure(
            record, "satisfied review obligation uses an unqualified invocation binding"
        )

    def test_weaker_fallback_model_is_rejected_structurally(self) -> None:
        record = self.qualified_record()
        binding = next(
            item
            for item in record["bindings"]
            if item["requested_role"] == "focused_verifier"
        )
        binding["role"] = {
            "value": "isolated_cli_reviewer",
            "state": "FALLBACK",
            "provenance": "explicit fallback receipt",
        }
        binding["model"] = {
            "value": "weaker-model",
            "state": "FALLBACK",
            "provenance": "explicit launch flag",
        }
        with self.assertRaisesRegex(
            validation.ValidationError,
            "fallback model must equal the frozen declared model",
        ):
            validation.validate_record(record, self.team)

    def test_reviewer_fallback_rejects_every_writer_or_orchestrator_role(self) -> None:
        template = self.qualified_record()
        declaration = next(
            item
            for item in template["team_context"]["workflow_roles"]
            if item["role"] == "safety_reviewer"
        )
        disallowed_roles = [
            template["team_context"]["root"]["role"],
            *[
                item["role"]
                for item in template["team_context"]["workflow_roles"]
                if item["kind"] in {"writer", "orchestrator"}
            ],
        ]
        self.assertTrue(disallowed_roles)
        for actual_role in disallowed_roles:
            with self.subTest(actual_role=actual_role):
                record = self.qualified_record()
                binding = self.binding(
                    declaration,
                    binding_id=f"unlinked-safety-fallback-{actual_role}",
                    target_id="target-1",
                    ref=record["targets"][0]["final_ref"],
                )
                binding["role"] = {
                    "value": actual_role,
                    "state": "FALLBACK",
                    "provenance": "forged fallback receipt",
                }
                record["bindings"].append(binding)
                with self.assertRaisesRegex(
                    validation.ValidationError,
                    "reviewer binding actual role exceeds the frozen fallback contract",
                ):
                    validation.validate_record(record, self.team)

    def test_v2_upgrade_is_separate_and_non_qualifying(self) -> None:
        source = self.legacy_v2_record()
        validation.validate_record(source, self.team)
        upgraded = validation.upgrade_record(source, self.team, "2026-07-15T11:00:00Z")
        self.assertEqual(source["schema_version"], 2)
        self.assertEqual(upgraded["schema_version"], 3)
        self.assertEqual(upgraded["obligations"][0]["status"], "UNAVAILABLE")
        with self.assertRaises(validation.ValidationError):
            self.assert_qualified(upgraded)

    def test_v1_and_v2_records_remain_readable(self) -> None:
        v1_report = validation.validate_record(self.legacy_v1_record(), self.team)
        v2_report = validation.validate_record(self.legacy_v2_record(), self.team)
        self.assertEqual(v1_report["compatibility"], "LEGACY_READ_ONLY")
        self.assertEqual(v2_report["schema_version"], 2)

    def test_upgraded_v3_cli_reports_are_json_serializable(self) -> None:
        source = self.legacy_v2_record()
        upgraded = validation.upgrade_record(
            source, self.team, "2026-07-15T11:00:00Z"
        )
        script = validation.CODEX / "scripts" / "validate_team.py"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "team-run-v3.json"
            path.write_text(json.dumps(upgraded, indent=2) + "\n", encoding="utf-8")
            plain = subprocess.run(
                [sys.executable, str(script), "--record", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(plain.returncode, 0, plain.stderr)
            self.assertIn("record validated:", plain.stdout)
            self.assertIn('"last_at": "2026-07-15T11:00:00Z"', plain.stdout)

            encoded = subprocess.run(
                [sys.executable, str(script), "--record", str(path), "--json"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(encoded.returncode, 0, encoded.stderr)
            report = json.loads(encoded.stdout)
            event_report = report["record_report"]["event_report"]
            self.assertEqual(event_report["last_at"], "2026-07-15T11:00:00Z")
            self.assertEqual(event_report["writer_intervals"], [])

    def test_synthetic_upgrade_fixture_works_without_runs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            absent_runs = Path(directory) / ".codex" / "runs"
            self.assertFalse(absent_runs.exists())
            upgraded = validation.upgrade_record(
                self.legacy_v2_record(),
                self.team,
                "2026-07-15T11:00:00Z",
            )
            self.assertEqual(upgraded["continuity"]["source_schema_version"], 2)


if __name__ == "__main__":
    unittest.main()
