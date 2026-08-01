from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import scripts.qualify_reviewer_cli_parser as parser_probe
from mechanical_librarian.adapters.reviewers import codex_shadow
from mechanical_librarian.adapters.reviewers.codex_shadow import (
    CodexShadowReviewer,
    QualificationProfile,
)
from mechanical_librarian.application.shadow_reviews import (
    CitationNormalizationMarker,
    ShadowReviewRunResult,
)
from mechanical_librarian.domain.shadow_review import (
    ReviewerProfileIdentity,
    ShadowReviewRequest,
    ShadowReviewResult,
)


def _profile(root: Path, codex: Path, sandbox: Path) -> QualificationProfile:
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
        "shadow-review-prompt-v3",
        "codex-shadow-profile-v2",
        "shadow-review-packet-v5",
        "shadow-review-result-v2",
        "qualification-only-policy-v1",
        120,
        65_536,
        536_870_912,
        16,
        131_072,
    )
    return QualificationProfile(codex, sandbox, auth, jobs, identity)


class ReviewerCliParserProbeTests(unittest.TestCase):
    def test_runs_all_wrapped_shapes_and_cleans_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = _profile(root, codex, sandbox)
            sentinel = profile.auth_store_path / "auth.json"
            sentinel.write_bytes(b"synthetic-auth-sentinel")
            os.chmod(sentinel, 0o600)
            sentinel_mode = sentinel.stat().st_mode & 0o777
            version = subprocess.CompletedProcess(
                [], 0, b"codex-cli 0.146.0-alpha.3.1\n", b""
            )
            success = subprocess.CompletedProcess([], 0, b"", b"")
            live_requests: list[ShadowReviewRequest] = []

            def run_canary(
                _reviewer: CodexShadowReviewer,
                request: ShadowReviewRequest,
                dispatched,
            ) -> ShadowReviewRunResult:
                live_requests.append(request)
                dispatched(1234, 1234, "5678")
                proposal = request.packet.proposals[0]
                result = ShadowReviewResult(
                    request.review_request_id,
                    (
                        {
                            "candidate_id": proposal["candidates"][0][
                                "candidate_id"
                            ],
                            "citations": [
                                request.packet.citations[0]["citation_id"]
                            ],
                            "problems": [],
                            "proposal_id": proposal["proposal_id"],
                            "reasons": ["Synthetic cited evidence is sufficient."],
                            "recommendation": "recommend_candidate",
                        },
                    ),
                )
                result.validate_for(request)
                return ShadowReviewRunResult(
                    result, CitationNormalizationMarker(False, 0)
                )

            with (
                patch.object(parser_probe.sys, "platform", "linux"),
                patch.object(
                    parser_probe, "load_qualification_profile", return_value=profile
                ),
                patch.object(
                    codex_shadow.subprocess,
                    "run",
                    side_effect=[version, success, success, success],
                ) as run,
                patch.object(
                    CodexShadowReviewer,
                    "run",
                    autospec=True,
                    side_effect=run_canary,
                ) as live,
            ):
                self.assertEqual(
                    {
                        "checks": 4,
                        "schema_version": "reviewer-cli-parser-probe-v2",
                        "status": "passed",
                    },
                    parser_probe.qualify_reviewer_cli_parser(profile),
                )
            commands = [call.args[0] for call in run.call_args_list[1:]]
            self.assertEqual(1, live.call_count)
            self.assertEqual(1, len(live_requests))
            self.assertEqual(
                live_requests[0],
                ShadowReviewRequest.from_source_validated_json(
                    live_requests[0].to_json()
                ),
            )
            self.assertEqual(
                "shadow-review-request-v8", live_requests[0].schema_version
            )
            self.assertTrue(
                all(str(profile.auth_store_path) not in command for command in commands)
            )
            self.assertTrue(all(command[1] == "--unshare-net" for command in commands))
            self.assertEqual(("login", "--device-auth", "--help"), tuple(commands[0][-3:]))
            self.assertEqual(("login", "status", "--help"), tuple(commands[1][-3:]))
            self.assertEqual(("logout", "--help"), tuple(commands[2][-2:]))
            for command in commands:
                self.assertEqual(
                    set(codex_shadow._auth_config()),
                    {
                        command[index + 1]
                        for index, value in enumerate(command[:-1])
                        if value == "-c"
                    },
                )
                self.assertIn("--ro-bind-data", command)
                self.assertNotIn("/dev/null", command)
            self.assertTrue(
                all(
                    call.kwargs.get("stdout") is subprocess.DEVNULL
                    for call in run.call_args_list[1:]
                )
            )
            schema = json.loads(live_requests[0].output_schema)
            keys: set[str] = set()

            def collect(value: object, *, property_map: bool = False) -> None:
                if type(value) is dict:
                    if not property_map:
                        keys.update(value)
                    for key, item in value.items():
                        collect(item, property_map=key == "properties")
                elif type(value) is list:
                    for item in value:
                        collect(item)

            collect(schema)
            self.assertEqual(
                {
                    "$schema",
                    "additionalProperties",
                    "const",
                    "enum",
                    "items",
                    "maxItems",
                    "maxLength",
                    "minItems",
                    "minLength",
                    "anyOf",
                    "properties",
                    "required",
                    "type",
                },
                keys,
            )
            self.assertFalse(list(profile.job_root_path.glob("reviewer-cli-parser-*")))
            self.assertEqual(b"synthetic-auth-sentinel", sentinel.read_bytes())
            self.assertEqual(sentinel_mode, sentinel.stat().st_mode & 0o777)

    def test_failure_is_value_free_and_fail_closed(self) -> None:
        with patch.object(
            parser_probe, "qualify_reviewer_cli_parser", side_effect=parser_probe.ParserProbeError("parser")
        ):
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(1, parser_probe.main(["--qualification-profile", "/private/profile"]))
        self.assertEqual(
            '{"code":"parser","schema_version":"reviewer-cli-parser-probe-v2","status":"failed"}\n',
            output.getvalue(),
        )

    def test_filesystem_failure_before_execution_is_value_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex, sandbox = root / "codex", root / "bwrap"
            codex.write_bytes(b"codex")
            sandbox.write_bytes(b"bwrap")
            os.chmod(codex, 0o700)
            os.chmod(sandbox, 0o700)
            profile = _profile(root, codex, sandbox)
            version = subprocess.CompletedProcess(
                [], 0, b"codex-cli 0.146.0-alpha.3.1\n", b""
            )
            success = subprocess.CompletedProcess([], 0, b"", b"")
            with (
                patch.object(parser_probe.sys, "platform", "linux"),
                patch.object(
                    parser_probe, "load_qualification_profile", return_value=profile
                ),
                patch.object(
                    codex_shadow.subprocess,
                    "run",
                    side_effect=[version, success, success, success],
                ),
                patch.object(
                    parser_probe.tempfile,
                    "TemporaryDirectory",
                    side_effect=OSError("/private/qualification-job"),
                ),
            ):
                output = io.StringIO()
                with redirect_stderr(output):
                    self.assertEqual(
                        1,
                        parser_probe.main(
                            ["--qualification-profile", "/private/profile"]
                        ),
                    )
        self.assertEqual(
            '{"code":"setup","schema_version":"reviewer-cli-parser-probe-v2","status":"failed"}\n',
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
