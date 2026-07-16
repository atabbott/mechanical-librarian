from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.merge_exact_head import (
    CommandResult,
    MergeGateError,
    MergeRequest,
    MutationRecoveryError,
    PostMutationError,
    ServerEnforcementBlocked,
    SubprocessRunner,
    _artifact,
    _validate_local_evidence,
    execute,
    main,
)


REPOSITORY = "example/mechanical-librarian"
BASE = "a" * 40
HEAD = "b" * 40
MERGE = "c" * 40


class FakeSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.calls.append(seconds)


class FakeRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.merged = False
        self.pull_queries = 0
        self.check_queries = 0
        self.review_queries = 0
        self.post_check_queries = 0
        self.second_head = HEAD
        self.second_check = "pass"
        self.second_review: str | None = "APPROVED"
        self.protected = True
        self.parent = BASE
        self.contained = True
        self.post_pending = 0
        self.post_failure = False
        self.merge_response = "confirmed"
        self.merge_puts = 0
        self.omit_required_check = False
        self.malformed_review = False

    def run(self, argv: object, *, check: bool = True) -> CommandResult:
        command = tuple(argv)  # type: ignore[arg-type]
        self.commands.append(command)
        if command[0].endswith("python") or "python" in Path(command[0]).name:
            return CommandResult(0, "PASS\n")
        if command == ("git", "status", "--porcelain"):
            return CommandResult(0, "")
        if command == ("git", "rev-parse", "HEAD"):
            return CommandResult(0, f"{HEAD}\n")
        if command[:3] == ("gh", "repo", "view"):
            return CommandResult(0, f"{REPOSITORY}\n")
        if len(command) > 2 and command[0:2] == ("gh", "api") and command[2].endswith("branches/main/protection"):
            if not self.protected:
                raise MergeGateError("command failed: gh")
            return CommandResult(0, json.dumps(self._protection()))
        if command[:3] == ("gh", "pr", "checks"):
            self.check_queries += 1
            bucket = self.second_check if self.check_queries > 1 else "pass"
            checks = [{"name": "public-gate", "bucket": bucket}]
            if not self.omit_required_check:
                checks.append({"name": "linked-issue", "bucket": bucket})
            return CommandResult(0, json.dumps(checks))
        if command[:3] == ("gh", "api", "graphql"):
            self.review_queries += 1
            decision = self.second_review if self.review_queries > 1 else "APPROVED"
            page_info: object = (
                "malformed" if self.malformed_review else {"hasNextPage": False}
            )
            return CommandResult(0, json.dumps({"data": {"repository": {"pullRequest": {
                "reviewDecision": decision,
                "reviewThreads": {"totalCount": 1, "pageInfo": page_info, "nodes": [{"isResolved": True}]},
            }}}}))
        if command[:4] == ("gh", "api", "-X", "PUT"):
            self.merge_puts += 1
            self.merged = True
            if self.merge_response == "transport_failure":
                raise MergeGateError("command could not complete: gh")
            if self.merge_response == "non_json":
                return CommandResult(0, "not-json")
            if self.merge_response == "malformed":
                return CommandResult(0, json.dumps({"merged": "yes"}))
            return CommandResult(0, json.dumps({"merged": True, "sha": MERGE}))
        if command[:4] == ("gh", "api", "--paginate", "--slurp"):
            self.post_check_queries += 1
            if self.post_check_queries <= self.post_pending:
                status, conclusion = "in_progress", None
            elif self.post_failure:
                status, conclusion = "completed", "failure"
            else:
                status, conclusion = "completed", "success"
            return CommandResult(0, json.dumps([{"check_runs": [{
                "name": "public-gate", "status": status, "conclusion": conclusion,
            }]}]))
        if command[:2] == ("gh", "api"):
            self.pull_queries += 1
            return CommandResult(0, json.dumps(self._pull()))
        if command[:2] == ("git", "fetch"):
            return CommandResult(0, "")
        if command[:3] == ("git", "rev-list", "--parents"):
            return CommandResult(0, f"{MERGE} {self.parent}\n")
        if command[:3] == ("git", "merge-base", "--is-ancestor"):
            return CommandResult(0 if self.contained else 1, "")
        raise AssertionError(f"unexpected command: {command}")

    def _protection(self) -> dict[str, object]:
        return {
            "required_status_checks": {
                "strict": True,
                "contexts": ["public-gate", "linked-issue"],
                "checks": [],
            },
            "required_pull_request_reviews": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews": True,
                "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []},
            },
            "enforce_admins": {"enabled": True},
            "required_conversation_resolution": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        }

    def _pull(self) -> dict[str, object]:
        head = self.second_head if self.pull_queries > 1 and not self.merged else HEAD
        return {
            "number": 17, "state": "closed" if self.merged else "open",
            "draft": False, "merged": self.merged, "mergeable": True,
            "merge_commit_sha": MERGE if self.merged else None,
            "base": {"ref": "main", "sha": BASE, "repo": {"full_name": REPOSITORY}},
            "head": {"sha": head},
        }


class ExactHeadMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.review_repo = root / "review-repository"
        controls = self.review_repo / ".codex" / "scripts"
        controls.mkdir(parents=True)
        self.review_validator = controls / "review_packet.py"
        self.run_validator = controls / "run_record.py"
        self.qualification_validator = controls / "qualification_cache.py"
        self.workflow_manifest = self.review_repo / ".codex" / "team.toml"
        self.evidence = root / "evidence.json"
        self.acceptance = root / "acceptance.json"
        self.quality = root / "quality.json"
        self.safety = root / "safety.json"
        self.challenge = root / "publication-challenge.json"
        self.extra = root / "extra.json"
        self.record = root / "run.json"
        for path in (
            self.review_validator,
            self.run_validator,
            self.qualification_validator,
        ):
            path.write_text("# validator\n", encoding="utf-8")
        self.workflow_manifest.write_text("[workflows]\n", encoding="utf-8")
        self.evidence.write_text("{}\n", encoding="utf-8")
        for task, path in (
            ("slice_acceptance", self.acceptance),
            ("code_quality", self.quality),
            ("safety", self.safety),
            ("publication_challenge", self.challenge),
            ("requirements", self.extra),
        ):
            path.write_text(json.dumps({"task": task}) + "\n", encoding="utf-8")
        self.record.write_text(json.dumps({
            "schema_version": 2,
            "workflow": "deliver-functional-slice",
            "tier": 1,
            "target": {"base_commit": BASE, "final_commit": HEAD},
            "delivery": {"kind": "PULL_REQUEST", "url": f"https://github.com/{REPOSITORY}/pull/17"},
            "review_epochs": [{"reviews": [
                {
                    "task": "slice_acceptance", "result": "PASS",
                    "evidence_digest": self._digest(self.evidence),
                    "envelope_digest": self._digest(self.acceptance),
                    "identity": {"commit": HEAD},
                },
                {
                    "task": "code_quality", "result": "PASS",
                    "evidence_digest": self._digest(self.evidence),
                    "envelope_digest": self._digest(self.quality),
                    "identity": {"commit": HEAD},
                },
            ]}],
        }), encoding="utf-8")

    def _enable_tier_two(self) -> None:
        value = json.loads(self.record.read_text(encoding="utf-8"))
        value["tier"] = 2
        value["review_epochs"][0]["reviews"].extend([
            {
                "task": "safety", "result": "PASS",
                "evidence_digest": self._digest(self.evidence),
                "envelope_digest": self._digest(self.safety),
                "identity": {"commit": HEAD},
            },
            {
                "task": "publication_challenge", "result": "PASS",
                "evidence_digest": self._digest(self.evidence),
                "envelope_digest": self._digest(self.challenge),
                "identity": {"commit": HEAD},
            },
        ])
        self.record.write_text(json.dumps(value), encoding="utf-8")

    def _replace_review_digest(self, task: str, path: Path) -> None:
        value = json.loads(self.record.read_text(encoding="utf-8"))
        reviews = value["review_epochs"][0]["reviews"]
        match = next(review for review in reviews if review["task"] == task)
        match["envelope_digest"] = self._digest(path)
        self.record.write_text(json.dumps(value), encoding="utf-8")

    @staticmethod
    def _digest(path: Path) -> str:
        import hashlib
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def request(self, *, dry_run: bool = False, attempts: int = 3) -> MergeRequest:
        return MergeRequest(
            repository=REPOSITORY, pull_request=17, base_ref="main",
            base_sha=BASE, head_sha=HEAD,
            required_checks=frozenset({"public-gate", "linked-issue"}),
            post_merge_checks=frozenset({"public-gate"}),
            review_repo=self.review_repo,
            review_evidence=self.evidence,
            review_envelopes=(("slice_acceptance", self.acceptance), ("code_quality", self.quality)),
            run_record=self.record,
            qualification_evidence=(), post_merge_attempts=attempts,
            post_merge_interval=0.25, dry_run=dry_run,
        )

    def test_tier_two_derives_all_mandatory_review_tasks(self) -> None:
        self._enable_tier_two()
        request = replace(
            self.request(dry_run=True),
            review_envelopes=(
                ("slice_acceptance", self.acceptance),
                ("code_quality", self.quality),
                ("safety", self.safety),
                ("publication_challenge", self.challenge),
            ),
        )
        result = execute(request, FakeRunner(), FakeSleeper())
        self.assertEqual(result["outcome"], "dry_run_passed")
        self.assertEqual(
            {key for key in result["evidence"] if key.startswith("review:")},
            {
                "review:slice_acceptance",
                "review:code_quality",
                "review:safety",
                "review:publication_challenge",
            },
        )

    def test_tier_two_caller_cannot_omit_a_mandatory_review(self) -> None:
        self._enable_tier_two()
        with self.assertRaisesRegex(MergeGateError, "mandatory workflow reviews"):
            execute(self.request(dry_run=True), FakeRunner(), FakeSleeper())

    def test_caller_cannot_add_a_non_workflow_review(self) -> None:
        request = replace(
            self.request(dry_run=True),
            review_envelopes=(
                ("slice_acceptance", self.acceptance),
                ("code_quality", self.quality),
                ("requirements", self.extra),
            ),
        )
        with self.assertRaisesRegex(MergeGateError, "mandatory workflow reviews"):
            execute(request, FakeRunner(), FakeSleeper())

    def test_forged_envelope_task_cannot_hide_behind_a_valid_digest(self) -> None:
        self.quality.write_text(
            json.dumps({"task": "slice_acceptance"}) + "\n", encoding="utf-8"
        )
        self._replace_review_digest("code_quality", self.quality)
        with self.assertRaisesRegex(MergeGateError, "task does not match"):
            execute(self.request(dry_run=True), FakeRunner(), FakeSleeper())

    def test_dry_run_validates_artifacts_and_server_without_mutation(self) -> None:
        runner, sleeper = FakeRunner(), FakeSleeper()
        result = execute(self.request(dry_run=True), runner, sleeper)
        self.assertEqual(result["outcome"], "dry_run_passed")
        self.assertIn("run_record", result["evidence"])
        self.assertFalse(runner.merged)
        self.assertEqual(sleeper.calls, [])

    def test_merge_revalidates_all_mutable_state_and_exact_parent(self) -> None:
        runner, sleeper = FakeRunner(), FakeSleeper()
        result = execute(self.request(), runner, sleeper)
        self.assertEqual(result["merge_commit"], MERGE)
        self.assertEqual(runner.pull_queries, 3)
        self.assertEqual(runner.check_queries, 2)
        self.assertEqual(runner.review_queries, 2)
        self.assertIn(("git", "rev-list", "--parents", "-n", "1", MERGE), runner.commands)

    def test_ambiguous_merge_response_recovers_from_live_pull_request(self) -> None:
        for response in ("transport_failure", "non_json", "malformed"):
            with self.subTest(response=response):
                runner = FakeRunner()
                runner.merge_response = response
                result = execute(self.request(), runner, FakeSleeper())
                self.assertEqual(result["outcome"], "merged")
                self.assertEqual(result["merge_commit"], MERGE)
                self.assertEqual(runner.merge_puts, 1)

    def test_ambiguous_merge_recovery_rejects_different_head_or_base(self) -> None:
        mutations = (
            ("head", {"sha": "f" * 40}),
            (
                "base",
                {
                    "ref": "other",
                    "sha": BASE,
                    "repo": {"full_name": REPOSITORY},
                },
            ),
            (
                "base",
                {
                    "ref": "main",
                    "sha": "d" * 40,
                    "repo": {"full_name": REPOSITORY},
                },
            ),
        )
        for field, replacement in mutations:
            with self.subTest(field=field, replacement=replacement):
                runner = FakeRunner()
                runner.merge_response = "transport_failure"
                original_pull = runner._pull

                def mismatched_pull() -> dict[str, object]:
                    value = original_pull()
                    if runner.merge_puts:
                        value[field] = replacement
                    return value

                runner._pull = mismatched_pull  # type: ignore[method-assign]
                with self.assertRaises(MutationRecoveryError) as caught:
                    execute(self.request(), runner, FakeSleeper())
                self.assertEqual(caught.exception.mutation_occurred, "unknown")
                self.assertEqual(runner.merge_puts, 1)

    def test_unresolved_ambiguous_merge_response_requires_manual_recovery(self) -> None:
        runner = FakeRunner()
        runner.merge_response = "transport_failure"
        original_pull = runner._pull

        def unresolved_pull() -> dict[str, object]:
            value = original_pull()
            if runner.merge_puts:
                value.update({"state": "open", "merged": False, "merge_commit_sha": None})
            return value

        runner._pull = unresolved_pull  # type: ignore[method-assign]
        with self.assertRaises(MutationRecoveryError) as caught:
            execute(self.request(), runner, FakeSleeper())
        self.assertEqual(caught.exception.phase, "merge_response")
        self.assertEqual(caught.exception.mutation_occurred, "unknown")
        self.assertIsNone(caught.exception.merge_sha)
        self.assertEqual(caught.exception.known_state["pull_request_state"], "open")
        self.assertEqual(runner.merge_puts, 1)

    def test_head_race_fails_before_mutation(self) -> None:
        runner = FakeRunner()
        runner.second_head = "f" * 40
        with self.assertRaisesRegex(MergeGateError, "head does not match"):
            execute(self.request(), runner, FakeSleeper())
        self.assertFalse(runner.merged)

    def test_check_race_fails_before_mutation(self) -> None:
        runner = FakeRunner()
        runner.second_check = "fail"
        with self.assertRaisesRegex(MergeGateError, "unambiguous pass"):
            execute(self.request(), runner, FakeSleeper())
        self.assertFalse(runner.merged)

    def test_missing_required_check_fails_without_merge_put(self) -> None:
        runner = FakeRunner()
        runner.omit_required_check = True
        with self.assertRaisesRegex(MergeGateError, "unambiguous pass"):
            execute(self.request(), runner, FakeSleeper())
        self.assertEqual(runner.merge_puts, 0)

    def test_review_race_and_review_required_fail_before_mutation(self) -> None:
        for decision in ("CHANGES_REQUESTED", "REVIEW_REQUIRED", None):
            with self.subTest(decision=decision):
                runner = FakeRunner()
                runner.second_review = decision
                with self.assertRaisesRegex(MergeGateError, "not APPROVED"):
                    execute(self.request(), runner, FakeSleeper())
                self.assertFalse(runner.merged)

    def test_missing_server_enforcement_blocks_before_mutation(self) -> None:
        runner = FakeRunner()
        runner.protected = False
        with self.assertRaisesRegex(
            ServerEnforcementBlocked, "enforcement is unavailable"
        ):
            execute(self.request(), runner, FakeSleeper())
        self.assertFalse(runner.merged)

    def test_malformed_server_and_review_shapes_are_controlled(self) -> None:
        runner = FakeRunner()
        runner._protection = lambda: {  # type: ignore[method-assign]
            **FakeRunner()._protection(),
            "required_status_checks": {
                "strict": True,
                "contexts": {"public-gate": True},
                "checks": [],
            },
        }
        with self.assertRaisesRegex(
            ServerEnforcementBlocked, "malformed required checks"
        ):
            execute(self.request(), runner, FakeSleeper())
        self.assertEqual(runner.merge_puts, 0)

        runner = FakeRunner()
        runner.malformed_review = True
        with self.assertRaisesRegex(MergeGateError, "query is incomplete"):
            execute(self.request(), runner, FakeSleeper())
        self.assertEqual(runner.merge_puts, 0)

    def test_wrong_squash_parent_is_structured_post_mutation_failure(self) -> None:
        runner = FakeRunner()
        runner.parent = "d" * 40
        with self.assertRaises(PostMutationError) as caught:
            execute(self.request(), runner, FakeSleeper())
        self.assertEqual(caught.exception.phase, "git_identity")
        self.assertEqual(caught.exception.merge_sha, MERGE)
        self.assertTrue(runner.merged)

    def test_post_merge_checks_wait_without_real_sleep(self) -> None:
        runner, sleeper = FakeRunner(), FakeSleeper()
        runner.post_pending = 2
        result = execute(self.request(), runner, sleeper)
        self.assertEqual(result["outcome"], "merged")
        self.assertEqual(sleeper.calls, [0.25, 0.25])

    def test_post_merge_check_failure_is_recoverable_structured_state(self) -> None:
        runner = FakeRunner()
        runner.post_failure = True
        with self.assertRaises(PostMutationError) as caught:
            execute(self.request(attempts=1), runner, FakeSleeper())
        self.assertEqual(caught.exception.phase, "post_merge_checks")
        self.assertTrue(runner.merged)

    def test_artifact_size_limit_is_enforced(self) -> None:
        oversized = Path(self.temporary.name) / "oversized"
        oversized.write_bytes(b"x" * 1_048_577)
        with self.assertRaisesRegex(MergeGateError, "size limit"):
            _artifact(oversized, "review evidence")

    def test_run_record_cannot_substitute_a_different_review_envelope(self) -> None:
        value = json.loads(self.record.read_text(encoding="utf-8"))
        value["review_epochs"][0]["reviews"][0]["envelope_digest"] = "f" * 64
        self.record.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(MergeGateError, "does not bind review"):
            execute(self.request(dry_run=True), FakeRunner(), FakeSleeper())

    def test_non_utf8_record_and_envelope_fail_as_merge_gate_errors(self) -> None:
        valid_record = self.record.read_bytes()
        self.record.write_bytes(b"\xff")
        with self.assertRaisesRegex(MergeGateError, "not valid UTF-8"):
            execute(self.request(dry_run=True), FakeRunner(), FakeSleeper())

        self.record.write_bytes(valid_record)
        self.quality.write_bytes(b"\xff")
        self._replace_review_digest("code_quality", self.quality)
        with self.assertRaisesRegex(MergeGateError, "not valid UTF-8"):
            execute(self.request(dry_run=True), FakeRunner(), FakeSleeper())

    def test_review_validator_uses_explicit_external_repository_root(self) -> None:
        calls = Path(self.temporary.name) / "validator-calls.jsonl"
        self.review_validator.write_text(
            "from pathlib import Path\n"
            "import json\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            f"expected = {str(self.review_repo.resolve())!r}\n"
            "repo = args[args.index('--repo') + 1]\n"
            "if args[0].startswith('verify-') and str(Path(repo).resolve()) != expected:\n"
            "    raise SystemExit(9)\n"
            "if args[0] == 'verify-envelope':\n"
            "    value = json.loads(Path(args[args.index('--envelope') + 1]).read_text())\n"
            "    if not isinstance(value.get('task'), str):\n"
            "        raise SystemExit(8)\n"
            f"with Path({str(calls)!r}).open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps(args) + '\\n')\n",
            encoding="utf-8",
        )
        self.run_validator.write_text(
            "from pathlib import Path\n"
            "import json\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            f"with Path({str(calls)!r}).open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps(args) + '\\n')\n",
            encoding="utf-8",
        )
        request = self.request(dry_run=True)
        _validate_local_evidence(request, SubprocessRunner())
        invocations = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        review_invocations = [args for args in invocations if args[0].startswith("verify-")]
        run_invocations = [args for args in invocations if args[0] == "validate"]
        self.assertEqual(len(review_invocations), 3)
        self.assertTrue(all(args[args.index("--repo") + 1] == str(self.review_repo.resolve()) for args in review_invocations))
        self.assertEqual(run_invocations[0][run_invocations[0].index("--repo") + 1], ".")

    def test_forged_envelope_is_rejected_by_trusted_validator_integration(self) -> None:
        self.review_validator.write_text(
            "import json\n"
            "from pathlib import Path\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "if args[0] == 'verify-envelope':\n"
            "    envelope = Path(args[args.index('--envelope') + 1])\n"
            "    if json.loads(envelope.read_text(encoding='utf-8')).get('forged'):\n"
            "        raise SystemExit(9)\n",
            encoding="utf-8",
        )
        self.quality.write_text(
            json.dumps({"task": "code_quality", "forged": True}) + "\n",
            encoding="utf-8",
        )
        self._replace_review_digest("code_quality", self.quality)
        with self.assertRaisesRegex(MergeGateError, "command failed"):
            _validate_local_evidence(self.request(dry_run=True), SubprocessRunner())

    def test_trusted_controls_reject_absent_symlink_and_concurrent_change(self) -> None:
        self.review_validator.unlink()
        with self.assertRaisesRegex(MergeGateError, "control is unavailable"):
            _validate_local_evidence(self.request(dry_run=True), FakeRunner())

        self.review_validator.symlink_to(self.run_validator)
        with self.assertRaisesRegex(MergeGateError, "control is unsafe"):
            _validate_local_evidence(self.request(dry_run=True), FakeRunner())

        self.review_validator.unlink()
        self.review_validator.write_text(
            "from pathlib import Path\n"
            "path = Path(__file__)\n"
            "path.write_text(path.read_text() + '# changed\\n')\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MergeGateError, "changed during validation"):
            _validate_local_evidence(self.request(dry_run=True), SubprocessRunner())

    def test_input_artifact_change_during_validation_is_rejected(self) -> None:
        self.review_validator.write_text(
            "from pathlib import Path\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            "if args[0] == 'verify-evidence':\n"
            "    path = Path(args[args.index('--evidence') + 1])\n"
            "    path.write_text(path.read_text() + ' ')\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MergeGateError, "changed during validation"):
            _validate_local_evidence(self.request(dry_run=True), SubprocessRunner())

    def test_public_gate_concurrency_preserves_each_main_commit(self) -> None:
        workflow = (Path(__file__).parents[2] / ".github/workflows/public-gate.yml").read_text(encoding="utf-8")
        self.assertIn("github.event_name == 'push' && github.sha", workflow)
        self.assertIn("github.event.pull_request.number", workflow)
        self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", workflow)

    def test_cli_serializes_post_mutation_failure(self) -> None:
        arguments = [
            "--repository", REPOSITORY, "--pull-request", "17", "--base-ref", "main",
            "--base-sha", BASE, "--head-sha", HEAD,
            "--required-check", "public-gate", "--post-merge-check", "public-gate",
            "--review-repo", self.temporary.name,
            "--review-evidence", "/evidence", "--review-envelope", "slice_acceptance=/envelope",
            "--run-record", "/record",
        ]
        output = io.StringIO()
        with patch("scripts.merge_exact_head.execute", side_effect=PostMutationError("post_merge_checks", MERGE, "failed")), redirect_stdout(output):
            code = main(arguments)
        self.assertEqual(code, 2)
        value = json.loads(output.getvalue())
        self.assertTrue(value["mutation_occurred"])
        self.assertEqual(value["merge_commit"], MERGE)
        self.assertEqual(value["phase"], "post_merge_checks")

    def test_cli_serializes_unknown_mutation_state(self) -> None:
        arguments = [
            "--repository", REPOSITORY, "--pull-request", "17", "--base-ref", "main",
            "--base-sha", BASE, "--head-sha", HEAD,
            "--required-check", "public-gate", "--post-merge-check", "public-gate",
            "--review-repo", self.temporary.name,
            "--review-evidence", "/evidence",
            "--review-envelope", "slice_acceptance=/envelope",
            "--run-record", "/record",
        ]
        error = MutationRecoveryError(
            "merge_response", "unknown", "ambiguous", known_state={"pull_request_state": "open"}
        )
        output = io.StringIO()
        with patch("scripts.merge_exact_head.execute", side_effect=error), redirect_stdout(output):
            code = main(arguments)
        self.assertEqual(code, 2)
        value = json.loads(output.getvalue())
        self.assertEqual(value["mutation_occurred"], "unknown")
        self.assertEqual(value["outcome"], "mutation_state_unknown")
        self.assertEqual(value["phase"], "merge_response")
        self.assertEqual(value["known_state"], {"pull_request_state": "open"})

    def test_cli_serializes_only_server_enforcement_as_blocked(self) -> None:
        arguments = [
            "--repository", REPOSITORY, "--pull-request", "17",
            "--base-ref", "main", "--base-sha", BASE, "--head-sha", HEAD,
            "--required-check", "public-gate",
            "--post-merge-check", "public-gate",
            "--review-repo", self.temporary.name,
            "--review-evidence", "/evidence",
            "--review-envelope", "slice_acceptance=/envelope",
            "--run-record", "/record",
        ]
        output = io.StringIO()
        with (
            patch(
                "scripts.merge_exact_head.execute",
                side_effect=ServerEnforcementBlocked("strict checks unavailable"),
            ),
            redirect_stdout(output),
        ):
            code = main(arguments)
        self.assertEqual(code, 2)
        value = json.loads(output.getvalue())
        self.assertEqual(value["outcome"], "BLOCKED")
        self.assertEqual(value["phase"], "server_enforcement")
        self.assertFalse(value["mutation_occurred"])

        output, errors = io.StringIO(), io.StringIO()
        with (
            patch(
                "scripts.merge_exact_head.execute",
                side_effect=MergeGateError("head mismatch"),
            ),
            redirect_stdout(output),
            redirect_stderr(errors),
        ):
            code = main(arguments)
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("merge gate failed: head mismatch", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
