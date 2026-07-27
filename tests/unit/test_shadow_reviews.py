from __future__ import annotations

import io
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.adapters.reviewers import codex_shadow
from mechanical_librarian.adapters.reviewers.codex_shadow import (
    CodexShadowReviewer,
    QualificationProfile,
    ShadowReviewerError,
    load_qualification_profile,
)
from mechanical_librarian.application.shadow_reviews import (
    ShadowReviewError,
    execute_shadow_review,
    preview_shadow_review,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.shadow_review import (
    ReviewerProfileIdentity,
    ShadowReviewRequest,
    ShadowReviewResult,
    ShadowReviewValueError,
    canonical_json,
)
from mechanical_librarian.interfaces.cli import main
from tests.unit.test_review_case_persistence import _report


def _identity() -> ReviewerProfileIdentity:
    return ReviewerProfileIdentity(
        "a" * 64,
        "codex-cli 0.146.0-alpha.3.1",
        "b" * 64,
        "bubblewrap-v1",
        "gpt-5.6-sol",
        "medium",
        "shadow-review-prompt-v1",
        "codex-shadow-profile-v1",
        "shadow-review-packet-v1",
        "shadow-review-result-v1",
        "qualification-only-policy-v1",
        120,
        65_536,
        536_870_912,
        16,
        131_072,
    )


def _result(request: ShadowReviewRequest) -> ShadowReviewResult:
    recommendations = []
    citation = request.packet.citations[0]["citation_id"]
    for proposal in request.packet.proposals:
        candidate = proposal["candidates"][0]["candidate_id"]
        recommendations.append(
            {
                "candidate_id": candidate,
                "citations": [citation],
                "problems": [],
                "proposal_id": proposal["proposal_id"],
                "reasons": ["The cited evidence supports this exact candidate."],
                "recommendation": "recommend_candidate",
            }
        )
    value = ShadowReviewResult(
        request.review_request_id, tuple(recommendations)
    )
    value.validate_for(request)
    return value


class _Runner:
    def __init__(self, *, fail: str | None = None) -> None:
        self.calls = 0
        self.fail = fail

    def run(self, request, dispatched):
        self.calls += 1
        dispatched(12345, 12345, "777")
        if self.fail is not None:
            raise ShadowReviewerError(self.fail, count=9, digest="c" * 64)
        return _result(request)


class ShadowReviewTests(unittest.TestCase):
    def _repository(self, directory: str):
        repository = open_repository(Path(directory) / "reports.sqlite")
        report = _report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        return repository, admitted["review_case_id"]

    def test_preview_is_sanitized_and_has_no_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            preview = json.loads(
                preview_shadow_review(repository, case_id, _identity())
            )
            serialized = canonical_json(preview["request"]["packet"])
            self.assertNotIn("primary", serialized)
            self.assertNotIn("ebooks", serialized)
            self.assertNotIn("item-1", serialized)
            self.assertEqual(
                (0,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_requests"
                ).fetchone(),
            )
            self.assertEqual("preview", preview["status"])
            repository.close()

    def test_request_persists_before_dispatch_and_replay_does_not_egress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            runner = _Runner()
            first = json.loads(
                execute_shadow_review(
                    repository,
                    case_id,
                    _identity(),
                    runner,
                    claimant=(9001, "11"),
                )
            )
            replay = json.loads(
                execute_shadow_review(
                    repository,
                    case_id,
                    _identity(),
                    runner,
                    claimant=(9001, "11"),
                )
            )
            self.assertEqual(1, runner.calls)
            self.assertEqual("qualification_only", first["status"])
            self.assertEqual(first, replay)
            self.assertEqual(
                (1, 1, 1, 1),
                (
                    repository._connection.execute(
                        "SELECT COUNT(*) FROM shadow_review_requests"
                    ).fetchone()[0],
                    repository._connection.execute(
                        "SELECT COUNT(*) FROM shadow_review_attempts"
                    ).fetchone()[0],
                    repository._connection.execute(
                        "SELECT COUNT(*) FROM shadow_review_dispatches"
                    ).fetchone()[0],
                    repository._connection.execute(
                        "SELECT COUNT(*) FROM shadow_review_outcomes"
                    ).fetchone()[0],
                ),
            )
            repository.close()

    def test_dispatched_failure_is_unknown_and_explicit_reevaluation_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            failing = _Runner(fail="timeout")
            with self.assertRaises(ShadowReviewerError) as raised:
                execute_shadow_review(
                    repository,
                    case_id,
                    _identity(),
                    failing,
                    claimant=(9001, "11"),
                )
            self.assertEqual("timeout", raised.exception.code)
            request_id = repository._connection.execute(
                "SELECT review_request_id FROM shadow_review_requests"
            ).fetchone()[0]
            inspected = json.loads(
                repository.inspect_shadow_request(
                    request_id, _identity().to_json()
                )
            )
            self.assertEqual("unknown", inspected["status"])
            successful = _Runner()
            result = json.loads(
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    successful,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: False,
                )
            )
            self.assertEqual("qualification_only", result["status"])
            self.assertEqual(2, len(result["attempts"]))
            repository.close()

    def test_completed_success_cannot_be_reevaluated_or_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            serialized = execute_shadow_review(
                repository,
                case_id,
                _identity(),
                _Runner(),
                claimant=(9001, "11"),
            )
            request_id = json.loads(serialized)["review_request_id"]
            claim = repository.last_shadow_claim(request_id)
            with self.assertRaises(MetadataReportError):
                repository.prepare_shadow_request(
                    request_id,
                    _identity().to_json(),
                    reevaluate=True,
                    claim_pid=9002,
                    claim_start_token="12",
                    expected_prior_attempt_id=claim[0],
                )
            runner = _Runner()
            with self.assertRaises(ShadowReviewError):
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    runner,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: False,
                )
            self.assertEqual(0, runner.calls)
            self.assertEqual(
                (1,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_attempts"
                ).fetchone(),
            )
            repository.close()

    def test_pre_dispatch_failure_is_terminal_and_cannot_be_reevaluated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            prepared = json.loads(
                repository.prepare_shadow_request(
                    case_id,
                    _identity().to_json(),
                    reevaluate=False,
                    claim_pid=9001,
                    claim_start_token="11",
                    expected_prior_attempt_id=None,
                )
            )
            repository.record_shadow_failure(
                prepared["attempt_id"], "setup", 0, None
            )
            request_id = prepared["request"]["review_request_id"]
            inspected = json.loads(
                repository.inspect_shadow_request(
                    request_id, _identity().to_json()
                )
            )
            self.assertEqual("failed", inspected["status"])
            runner = _Runner()
            with self.assertRaises(ShadowReviewError):
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    runner,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: self.fail(
                        "no undispatched process may be probed"
                    ),
                    claim_alive=lambda *_: False,
                )
            self.assertEqual(0, runner.calls)
            self.assertEqual(
                (1,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_attempts"
                ).fetchone(),
            )
            repository.close()

    def test_dispatch_without_terminal_outcome_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            prepared = json.loads(
                repository.prepare_shadow_request(
                    case_id,
                    _identity().to_json(),
                    reevaluate=False,
                    claim_pid=9001,
                    claim_start_token="11",
                    expected_prior_attempt_id=None,
                )
            )
            repository.record_shadow_dispatch(
                prepared["attempt_id"], 12345, 12345, "777"
            )
            request_id = prepared["request"]["review_request_id"]
            inspected = json.loads(
                repository.inspect_shadow_request(
                    request_id, _identity().to_json()
                )
            )
            self.assertEqual("unknown", inspected["status"])
            self.assertEqual("unknown", inspected["attempts"][0]["status"])
            repository.close()

    def test_pending_pre_dispatch_restart_can_be_explicitly_reevaluated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            prepared = json.loads(
                repository.prepare_shadow_request(
                    case_id,
                    _identity().to_json(),
                    reevaluate=False,
                    claim_pid=9001,
                    claim_start_token="11",
                    expected_prior_attempt_id=None,
                )
            )
            request_id = prepared["request"]["review_request_id"]
            runner = _Runner()
            with self.assertRaises(ShadowReviewError):
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    runner,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: self.fail(
                        "no undispatched process may be probed"
                    ),
                    claim_alive=lambda *_: True,
                )
            self.assertEqual(
                (1,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_attempts"
                ).fetchone(),
            )
            result = json.loads(
                execute_shadow_review(
                    repository,
                    request_id,
                    _identity(),
                    runner,
                    claimant=(9002, "12"),
                    reevaluate=True,
                    process_alive=lambda *_: self.fail(
                        "no undispatched process may be probed"
                    ),
                    claim_alive=lambda *_: False,
                )
            )
            self.assertEqual("qualification_only", result["status"])
            self.assertEqual(2, len(result["attempts"]))
            self.assertEqual("pending", result["attempts"][0]["status"])
            repository.close()

    def test_concurrent_reevaluation_claim_has_one_cas_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite"
            repository, case_id = self._repository(directory)
            prepared = json.loads(
                repository.prepare_shadow_request(
                    case_id,
                    _identity().to_json(),
                    reevaluate=False,
                    claim_pid=9001,
                    claim_start_token="11",
                    expected_prior_attempt_id=None,
                )
            )
            repository.record_shadow_dispatch(
                prepared["attempt_id"], 12345, 12345, "777"
            )
            request_id = prepared["request"]["review_request_id"]
            claim = repository.last_shadow_claim(request_id)
            repository.close()
            barrier = threading.Barrier(2)
            outcomes: list[str] = []

            def claim_reevaluation() -> None:
                barrier.wait()
                local = open_repository(database, create=False)
                try:
                    local.prepare_shadow_request(
                        request_id,
                        _identity().to_json(),
                        reevaluate=True,
                        claim_pid=9002,
                        claim_start_token="12",
                        expected_prior_attempt_id=claim[0],
                    )
                    outcomes.append("won")
                except Exception:
                    outcomes.append("lost")
                finally:
                    local.close()

            workers = [
                threading.Thread(target=claim_reevaluation)
                for _ in range(2)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.assertEqual(["lost", "won"], sorted(outcomes))
            repository = open_repository(database, create=False)
            self.assertEqual(
                (2,),
                repository._connection.execute(
                    "SELECT COUNT(*) FROM shadow_review_attempts"
                ).fetchone(),
            )
            repository.close()

    def test_result_rejects_missing_coverage_and_unknown_citations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            request = ShadowReviewRequest.from_json(
                repository.preview_shadow_request(
                    case_id, _identity().to_json()
                )
            )
            with self.assertRaises(ShadowReviewValueError):
                ShadowReviewResult.from_json(
                    canonical_json(
                        {
                            "recommendations": [],
                            "review_request_id": request.review_request_id,
                            "schema_version": "shadow-review-result-v1",
                        }
                    ),
                    request,
                )
            malformed = _result(request).to_data()
            malformed["recommendations"][0]["citations"] = ["evidence_" + "f" * 64]
            with self.assertRaises(ShadowReviewValueError):
                ShadowReviewResult.from_json(canonical_json(malformed), request)
            repository.close()

    def test_profile_load_does_not_touch_declared_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            raw = {
                "auth_store_path": str(root / "missing-auth"),
                "codex": {
                    "path": str(root / "missing-codex"),
                    "sha256": "a" * 64,
                    "version": "codex-cli 0.146.0-alpha.3.1",
                },
                "job_root_path": str(root / "missing-jobs"),
                "limits": {
                    "file_bytes": 131_072,
                    "memory_bytes": 536_870_912,
                    "output_bytes": 65_536,
                    "processes": 16,
                    "timeout_seconds": 120,
                },
                "mode": "qualification_only",
                "model": "gpt-5.6-sol",
                "output_version": "shadow-review-result-v1",
                "packet_version": "shadow-review-packet-v1",
                "policy_version": "qualification-only-policy-v1",
                "profile_version": "codex-shadow-profile-v1",
                "prompt_version": "shadow-review-prompt-v1",
                "reasoning": "medium",
                "sandbox": {
                    "path": str(root / "missing-bwrap"),
                    "sha256": "b" * 64,
                    "version": "bubblewrap-v1",
                },
                "schema_version": "reviewer-qualification-profile-v1",
            }
            profile.write_text(canonical_json(raw), encoding="utf-8")
            os.chmod(profile, 0o600)
            loaded = load_qualification_profile(profile)
            self.assertEqual(_identity(), loaded.identity)

    def test_cli_routes_exact_shadow_ids_and_redacts_failures(self) -> None:
        case_id = "review_case_" + "a" * 64
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            0,
            main(
                [
                    "reviewer-auth-status",
                    "--qualification-profile",
                    "/profile",
                ],
                reviewer_auth_runner=lambda *args: self.assertEqual(
                    (Path("/profile"), "status"), args
                )
                or canonical_json(
                    {
                        "mode": "qualification_only",
                        "schema_version": "reviewer-auth-v1",
                        "status": "chatgpt",
                    }
                ),
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", errors.getvalue())
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            1,
            main(
                [
                    "shadow-review-preview",
                    "--database",
                    "/db",
                    "--review-case-id",
                    case_id,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_preview_runner=lambda *_: (_ for _ in ()).throw(
                    ShadowReviewerError("profile")
                ),
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", output.getvalue())
        self.assertEqual(
            '{"code":"profile","mode":"qualification_only",'
            '"schema_version":"shadow-review-failure-v1"}\n',
            errors.getvalue(),
        )

    def test_cli_rejects_wrong_success_schema_and_exact_id(self) -> None:
        case_id = "review_case_" + "a" * 64
        wrong_case_id = "review_case_" + "b" * 64
        with tempfile.TemporaryDirectory() as directory:
            repository, admitted_case_id = self._repository(directory)
            preview = preview_shadow_review(
                repository, admitted_case_id, _identity()
            )
            repository.close()
        for serialized in (
            preview.replace(admitted_case_id, wrong_case_id),
            canonical_json(
                {
                    "mode": "qualification_only",
                    "schema_version": "wrong-v1",
                    "status": "preview",
                }
            ),
        ):
            with self.subTest(serialized=serialized):
                output, errors = io.StringIO(), io.StringIO()
                self.assertEqual(
                    1,
                    main(
                        [
                            "shadow-review-preview",
                            "--database",
                            "/db",
                            "--review-case-id",
                            case_id,
                            "--qualification-profile",
                            "/profile",
                        ],
                        shadow_preview_runner=lambda *_args, value=serialized: value,
                        stdout=output,
                        stderr=errors,
                    ),
                )
                self.assertEqual("", output.getvalue())
                self.assertEqual(
                    '{"code":"malformed_output","mode":"qualification_only",'
                    '"schema_version":"shadow-review-failure-v1"}\n',
                    errors.getvalue(),
                )

    def test_cli_validates_request_and_inspect_ids_against_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = self._repository(directory)
            serialized = execute_shadow_review(
                repository,
                case_id,
                _identity(),
                _Runner(),
                claimant=(9001, "11"),
            )
            request_id = json.loads(serialized)["review_request_id"]
            repository.close()
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            0,
            main(
                [
                    "shadow-review-request",
                    "--database",
                    "/db",
                    "--review-case-id",
                    case_id,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_request_runner=lambda *_: serialized,
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", errors.getvalue())
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            1,
            main(
                [
                    "shadow-review-inspect",
                    "--database",
                    "/db",
                    "--review-request-id",
                    "review_request_" + "f" * 64,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_inspect_runner=lambda *_: serialized,
                stdout=output,
                stderr=errors,
            ),
        )
        self.assertEqual("", output.getvalue())
        self.assertEqual(
            '{"code":"malformed_output","mode":"qualification_only",'
            '"schema_version":"shadow-review-failure-v1"}\n',
            errors.getvalue(),
        )
        self.assertTrue(request_id.startswith("review_request_"))


class CodexShadowAdapterTests(unittest.TestCase):
    def _profile(
        self, root: Path, codex: Path, sandbox: Path
    ) -> QualificationProfile:
        auth, jobs = root / "auth", root / "jobs"
        auth.mkdir()
        jobs.mkdir()
        os.chmod(auth, 0o700)
        os.chmod(jobs, 0o700)
        identity = ReviewerProfileIdentity(
            hashlib.sha256(codex.read_bytes()).hexdigest(),
            "codex-cli 0.146.0-alpha.3.1",
            hashlib.sha256(sandbox.read_bytes()).hexdigest(),
            "bubblewrap-v1",
            "gpt-5.6-sol",
            "medium",
            "shadow-review-prompt-v1",
            "codex-shadow-profile-v1",
            "shadow-review-packet-v1",
            "shadow-review-result-v1",
            "qualification-only-policy-v1",
            120,
            65_536,
            536_870_912,
            16,
            131_072,
        )
        return QualificationProfile(codex, sandbox, auth, jobs, identity)

    def test_bound_artifact_survives_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "codex"
            path.write_bytes(b"original")
            os.chmod(path, 0o700)
            digest = hashlib.sha256(b"original").hexdigest()
            with codex_shadow._bound_artifact(path, digest) as bound:
                replacement = Path(directory) / "replacement"
                replacement.write_bytes(b"replacement")
                os.chmod(replacement, 0o700)
                os.replace(replacement, path)
                os.lseek(bound.descriptor, 0, os.SEEK_SET)
                self.assertEqual(b"original", os.read(bound.descriptor, 64))
            self.assertEqual(b"replacement", path.read_bytes())

    def test_auth_commands_mask_store_config_and_pin_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = self._profile(root, codex, sandbox)
            version = subprocess.CompletedProcess(
                [], 0, b"codex-cli 0.146.0-alpha.3.1\n", b""
            )
            chatgpt = subprocess.CompletedProcess(
                [], 0, b"Logged in using ChatGPT\n", b""
            )
            success = subprocess.CompletedProcess([], 0, b"", b"")
            scenarios = {
                "start": ([version, success], ("login", "--device-auth")),
                "status": ([version, chatgpt], ("login", "status")),
                "logout": (
                    [version, chatgpt, success],
                    ("logout",),
                ),
            }
            for action, (responses, suffix) in scenarios.items():
                with self.subTest(action=action), patch.object(
                    codex_shadow.subprocess,
                    "run",
                    side_effect=responses,
                ) as run:
                    CodexShadowReviewer(profile).auth(action)
                    invoked = [
                        call.args[0] for call in run.call_args_list[1:]
                    ]
                    self.assertEqual(tuple(invoked[-1][-len(suffix) :]), suffix)
                    self.assertEqual(
                        1,
                        len(
                            {
                                call.kwargs["pass_fds"][2]
                                for call in run.call_args_list[1:]
                            }
                        ),
                    )
                    for index, command in enumerate(invoked, start=1):
                        self.assertNotIn("--ignore-user-config", command)
                        sandbox_descriptor = run.call_args_list[index].kwargs[
                            "pass_fds"
                        ][1]
                        self.assertEqual(
                            str(sandbox_descriptor), command[0].rsplit("/", 1)[1]
                        )
                        self.assertEqual(
                            (
                                "--perms",
                                "0400",
                                "--ro-bind-data",
                                str(run.call_args_list[index].kwargs["pass_fds"][2]),
                                "/auth/config.toml",
                            ),
                            tuple(
                                command[
                                    command.index("--perms", command.index("--bind"))
                                    : command.index("--perms", command.index("--bind")) + 5
                                ]
                            ),
                        )
                        self.assertNotIn("/dev/null", command)
                        self.assertEqual(
                            "/auth",
                            command[command.index("CODEX_HOME") + 1],
                        )
                        overrides = {
                            command[index + 1]
                            for index, value in enumerate(command[:-1])
                            if value == "-c"
                        }
                        self.assertEqual(
                            set(codex_shadow._auth_config()), overrides
                        )
                    self.assertEqual(1, len(run.call_args_list[0].kwargs["pass_fds"]))
                    for call in run.call_args_list[1:]:
                        self.assertEqual(3, len(call.kwargs["pass_fds"]))

    def test_empty_auth_config_descriptor_is_eof_and_closes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            reviewer = CodexShadowReviewer(self._profile(root, codex, sandbox))
            with reviewer._empty_auth_config() as descriptor:
                self.assertEqual(b"", os.read(descriptor, 1))
                os.fstat(descriptor)
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_chatgpt_auth_status_requires_one_exact_canonical_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            reviewer = CodexShadowReviewer(self._profile(root, codex, sandbox))
            canonical = b"Logged in using ChatGPT"
            accepted = [
                subprocess.CompletedProcess([], 0, value, b"")
                for value in (canonical, canonical + b"\n", canonical + b"\r\n")
            ] + [
                subprocess.CompletedProcess([], 0, b"", value)
                for value in (canonical, canonical + b"\n", canonical + b"\r\n")
            ]
            rejected = [
                subprocess.CompletedProcess([], 0, canonical, canonical),
                subprocess.CompletedProcess([], 0, canonical, b"extra"),
                subprocess.CompletedProcess([], 0, canonical + b"\n\n", b""),
                subprocess.CompletedProcess([], 0, b"", b""),
                subprocess.CompletedProcess(
                    [], 0, b"Logged in using an API key\n", b""
                ),
                subprocess.CompletedProcess([], 0, b"prefix " + canonical, b""),
                subprocess.CompletedProcess([], 1, canonical, b""),
            ]
            for completed in accepted:
                with self.subTest(
                    accepted=True,
                    stdout=bool(completed.stdout),
                    ending=completed.stdout[-2:] or completed.stderr[-2:],
                ), patch.object(reviewer, "_run_auth", return_value=completed):
                    reviewer._require_chatgpt_auth([], object(), object(), 0)
            for completed in rejected:
                with self.subTest(
                    accepted=False,
                    returncode=completed.returncode,
                    stdout=bool(completed.stdout),
                    stderr=bool(completed.stderr),
                ), patch.object(reviewer, "_run_auth", return_value=completed):
                    with self.assertRaisesRegex(ShadowReviewerError, "auth"):
                        reviewer._require_chatgpt_auth([], object(), object(), 0)

    def test_sandbox_command_has_exact_tool_disable_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = self._profile(root, codex, sandbox)
            command = CodexShadowReviewer(profile)._sandbox_command(
                root,
                root / "result.json",
                "/proc/self/fd/10",
                "/proc/self/fd/11",
            )
            overrides = {
                command[index + 1]
                for index, value in enumerate(command[:-1])
                if value == "-c"
            }
            self.assertEqual(
                set(codex_shadow._exact_config("medium")), overrides
            )
            for required in (
                "features.plugins=false",
                "features.browser_use=false",
                "features.computer_use=false",
                "features.image_generation=false",
                "features.skill_search=false",
                "features.tool_call_mcp_elicitation=false",
            ):
                self.assertIn(required, overrides)
            self.assertEqual("/proc/self/fd/11", command[0])
            bind = command.index("/runtime/codex")
            self.assertEqual("/proc/self/fd/10", command[bind - 1])
            self.assertIn("--skip-git-repo-check", command)

    def test_output_schema_is_typed_bound_and_digest_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository, case_id = ShadowReviewTests()._repository(directory)
            request = ShadowReviewRequest.from_json(
                repository.preview_shadow_request(case_id, _identity().to_json())
            )
            binding = codex_shadow._schema_binding(request)
            schema = json.loads(binding.serialized)
            self.assertEqual(
                hashlib.sha256(binding.serialized).hexdigest(), binding.digest
            )
            properties = schema["properties"]
            self.assertEqual("string", properties["review_request_id"]["type"])
            item = properties["recommendations"]["items"]["properties"]
            self.assertEqual("string", item["proposal_id"]["type"])
            self.assertEqual("string", item["citations"]["items"]["type"])
            self.assertNotIn("uniqueItems", item["citations"])
            repository.close()

    def test_output_schema_rejects_typeless_or_contradictory_bindings(self) -> None:
        with self.assertRaisesRegex(ShadowReviewerError, "schema"):
            codex_shadow._validate_output_schema({"enum": ["bound"]})
        with self.assertRaisesRegex(ShadowReviewerError, "schema"):
            codex_shadow._validate_output_schema(
                {"const": "bound", "enum": ["other"], "type": "string"}
            )

    def test_start_gate_does_not_exec_when_dispatch_persistence_fails(self) -> None:
        shell_source = Path(os.path.realpath("/bin/sh")).read_bytes()
        digest = hashlib.sha256(shell_source).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shell = root / "shell"
            shell.write_bytes(shell_source)
            os.chmod(shell, 0o700)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            reviewer = CodexShadowReviewer(
                self._profile(root, codex, sandbox)
            )
            marker = root / "executed"
            with (
                codex_shadow._bound_artifact(shell, digest) as bound_codex,
                codex_shadow._bound_artifact(shell, digest) as bound_sandbox,
                (root / "stdin").open("w+b") as stdin,
                (root / "stdout").open("w+b") as stdout,
                (root / "stderr").open("w+b") as stderr,
                patch.object(
                    codex_shadow, "_process_start_token", return_value="123"
                ),
                patch.object(
                    codex_shadow, "process_identity_alive", return_value=False
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "dispatch"):
                    reviewer._run_gated(
                        [
                            bound_sandbox.invocation_path,
                            "-c",
                            f"printf ran > '{marker}'",
                        ],
                        bound_codex,
                        bound_sandbox,
                        stdin.fileno(),
                        stdout.fileno(),
                        stderr.fileno(),
                        lambda *_: (_ for _ in ()).throw(
                            RuntimeError("dispatch")
                        ),
                    )
            self.assertFalse(marker.exists())

    def test_proc_stat_parser_uses_final_comm_delimiter_and_starttime(self) -> None:
        tail = [
            "S",
            "1",
            "4321",
            "1",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "0",
            "987654",
        ]
        raw = "123 (worker name with ) spaces) " + " ".join(tail)
        self.assertEqual(("987654", 4321), codex_shadow._proc_identity(raw))


if __name__ == "__main__":
    unittest.main()
