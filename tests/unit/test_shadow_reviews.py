from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.reviewers.codex_shadow import (
    ShadowReviewerError,
    load_qualification_profile,
)
from mechanical_librarian.application.shadow_reviews import (
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
                    repository, case_id, _identity(), runner
                )
            )
            replay = json.loads(
                execute_shadow_review(
                    repository, case_id, _identity(), runner
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
                    repository, case_id, _identity(), failing
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
                    reevaluate=True,
                    process_alive=lambda *_: False,
                )
            )
            self.assertEqual("qualification_only", result["status"])
            self.assertEqual(2, len(result["attempts"]))
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
        request_id = "review_request_" + "b" * 64
        output, errors = io.StringIO(), io.StringIO()
        self.assertEqual(
            0,
            main(
                [
                    "shadow-review-inspect",
                    "--database",
                    "/db",
                    "--review-request-id",
                    request_id,
                    "--qualification-profile",
                    "/profile",
                ],
                shadow_inspect_runner=lambda *args: self.assertEqual(
                    (Path("/db"), request_id, Path("/profile")), args
                )
                or canonical_json(
                    {
                        "mode": "qualification_only",
                        "schema_version": "shadow-review-inspect-v1",
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


if __name__ == "__main__":
    unittest.main()
