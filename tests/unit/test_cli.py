from __future__ import annotations

import io
import unittest
from datetime import datetime, timezone
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock

from mechanical_librarian.application.job_heartbeat import (
    HeartbeatInspection,
    HeartbeatOutcome,
    HeartbeatResult,
)
from mechanical_librarian.domain.workflow import (
    JobState,
    RunId,
    RunState,
    StageId,
    StageState,
)
from mechanical_librarian.application.state_check import (
    StateCheckOutcome,
    StateCheckResult,
)
from mechanical_librarian.application.inventory_reconciliation import (
    InventoryLibraryResult,
    InventoryOutcome,
    InventoryRunResult,
)
from mechanical_librarian.application.inventory_polling import (
    InventoryPollOutcome,
    InventoryPollResult,
)
from mechanical_librarian.domain.inventory_polling import PollInspection, PollState
from mechanical_librarian.domain.identifiers import Fingerprint, LibraryId, ScanId, SchemaVersion
from mechanical_librarian.domain.inventory_v2 import (
    InventoryInspection,
    ScanState,
    ScanUsage,
)
from mechanical_librarian.domain.local_evidence import (
    EpubPackageLimits,
    LocalEvidenceCapability,
    LocalEvidenceOutcome,
    LocalEvidenceReport,
)
from mechanical_librarian.interfaces.cli import main


class CliTests(unittest.TestCase):
    def test_epub_invalid_invocation_is_a_typed_private_safe_envelope(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        runner = Mock()
        exit_code = main(
            ["epub-package-report", "--config", "/private/config.json"],
            state_check_runner=lambda _path: StateCheckResult(StateCheckOutcome.READY),
            epub_package_report_runner=runner,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn('"outcome":"invalid_invocation"', errors.getvalue())
        self.assertNotIn("usage:", errors.getvalue())
        self.assertNotIn("/private", errors.getvalue())
        runner.assert_not_called()

    def test_epub_package_report_has_exact_exit_stream_and_typed_json_envelope(self) -> None:
        expected_exits = {
            LocalEvidenceOutcome.INVALID_INVOCATION: 2,
            LocalEvidenceOutcome.SUCCESS: 0,
            LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE: 0,
            LocalEvidenceOutcome.INVALID_CONFIGURATION: 3,
            LocalEvidenceOutcome.INVALID_REFERENCE: 3,
            LocalEvidenceOutcome.UNAVAILABLE_SOURCE: 3,
            LocalEvidenceOutcome.UNSUPPORTED_FORMAT: 4,
            LocalEvidenceOutcome.UNSUPPORTED_VERSION: 4,
            LocalEvidenceOutcome.UNSUPPORTED_COMPRESSION: 4,
            LocalEvidenceOutcome.UNSUPPORTED_MULTIPLE_RENDITIONS: 4,
            LocalEvidenceOutcome.ENCRYPTED: 4,
            LocalEvidenceOutcome.MALFORMED: 5,
            LocalEvidenceOutcome.INTEGRITY_FAILURE: 5,
            LocalEvidenceOutcome.RESOURCE_LIMIT: 5,
            LocalEvidenceOutcome.TIMEOUT: 5,
            LocalEvidenceOutcome.CANCELLATION: 5,
            LocalEvidenceOutcome.UNSTABLE_SOURCE: 5,
            LocalEvidenceOutcome.CHILD_FAILURE: 70,
            LocalEvidenceOutcome.INTERNAL_FAILURE: 70,
        }
        for outcome, expected_exit in expected_exits.items():
            with self.subTest(outcome=outcome):
                report = LocalEvidenceReport(
                    outcome,
                    LocalEvidenceCapability.PACKAGE,
                    EpubPackageLimits(),
                    (
                        Fingerprint("sha256", SchemaVersion("source-v1"), "a" * 64)
                        if outcome is LocalEvidenceOutcome.SUCCESS
                        else None
                    ),
                )
                output = io.StringIO()
                errors = io.StringIO()
                runner = Mock(return_value=report)
                exit_code = main(
                    [
                        "epub-package-report",
                        "--config",
                        "/private/config.json",
                        "--mount",
                        "books",
                        "--file",
                        "private/book.epub",
                    ],
                    state_check_runner=lambda _path: StateCheckResult(
                        StateCheckOutcome.READY
                    ),
                    epub_package_report_runner=runner,
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual(exit_code, expected_exit)
                runner.assert_called_once_with(
                    Path("/private/config.json"), "books", "private/book.epub"
                )
                rendered = output.getvalue() if expected_exit == 0 else errors.getvalue()
                self.assertEqual(rendered, report.to_json() + "\n")
                self.assertEqual(errors.getvalue() if expected_exit == 0 else output.getvalue(), "")
                self.assertIn(f'"outcome":"{outcome.value}"', rendered)
                self.assertNotIn("/private", rendered)

    def test_epub_text_uses_same_projection_and_runner_failure_is_internal(self) -> None:
        report = LocalEvidenceReport(
            LocalEvidenceOutcome.NO_APPLICABLE_EVIDENCE,
            LocalEvidenceCapability.PACKAGE,
            EpubPackageLimits(),
        )
        output = io.StringIO()
        errors = io.StringIO()
        exit_code = main(
            [
                "epub-package-report",
                "--config",
                "config.json",
                "--mount",
                "books",
                "--file",
                "book.epub",
                "--format",
                "text",
            ],
            state_check_runner=lambda _path: StateCheckResult(StateCheckOutcome.READY),
            epub_package_report_runner=lambda *_: report,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(exit_code, 0)
        self.assertIn('outcome="no_applicable_evidence"\n', output.getvalue())
        self.assertIn('"profile":"epub-package-limits-v1"', output.getvalue())
        self.assertEqual(errors.getvalue(), "")

        def hostile(*_args: object) -> LocalEvidenceReport:
            raise RuntimeError("/private/sentinel token=secret")

        output = io.StringIO()
        errors = io.StringIO()
        exit_code = main(
            [
                "epub-package-report",
                "--config",
                "config.json",
                "--mount",
                "books",
                "--file",
                "book.epub",
            ],
            state_check_runner=lambda _path: StateCheckResult(StateCheckOutcome.READY),
            epub_package_report_runner=hostile,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(exit_code, 70)
        self.assertEqual(output.getvalue(), "")
        self.assertIn('"outcome":"internal_failure"', errors.getvalue())
        self.assertNotIn("sentinel", errors.getvalue())

        for format_arguments in (
            ["--format=text"],
            ["--format", "json", "--format=text"],
        ):
            with self.subTest(format_arguments=format_arguments):
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    ["epub-package-report", *format_arguments],
                    state_check_runner=lambda _path: StateCheckResult(
                        StateCheckOutcome.READY
                    ),
                    epub_package_report_runner=Mock(),
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual(exit_code, 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn('outcome="invalid_invocation"', errors.getvalue())

    def test_poll_inspection_needs_no_config_or_target_access(self) -> None:
        result = InventoryPollResult(
            InventoryPollOutcome.DEFERRED,
            PollInspection(
                "poll_public_evidence",
                PollState.RETRY_WAIT,
                1,
                3,
                1,
                ScanUsage(2, 3, 400),
                datetime(2026, 7, 13, tzinfo=timezone.utc),
                "unavailable",
                "supported",
                False,
            ),
        )
        output = io.StringIO()
        errors = io.StringIO()
        runner = Mock(return_value=result)

        exit_code = main(
            [
                "inventory-poll-inspect",
                "--database",
                "/private/state.sqlite",
                "--poll-id",
                "poll_public_evidence",
            ],
            state_check_runner=lambda _path: StateCheckResult(StateCheckOutcome.READY),
            inventory_poll_inspection_runner=runner,
            stdout=output,
            stderr=errors,
        )

        self.assertEqual(exit_code, 1)
        runner.assert_called_once_with(
            Path("/private/state.sqlite"), "poll_public_evidence"
        )
        self.assertEqual(output.getvalue(), "")
        self.assertIn("state=retry_wait attempt=1/3 fence=1", errors.getvalue())
        self.assertIn("pages=2 items=3 bytes=400", errors.getvalue())
        self.assertNotIn("/private", errors.getvalue())

    def test_incomplete_inventory_is_inspectable_without_external_identity(
        self,
    ) -> None:
        result = InventoryRunResult(
            InventoryOutcome.INCOMPLETE,
            (
                InventoryLibraryResult(
                    LibraryId("01234567-89ab-4def-8123-456789abcdef"),
                    InventoryOutcome.INCOMPLETE,
                    InventoryInspection(
                        ScanId("scan-private-scope"),
                        ScanState.INCOMPLETE,
                        "timeout",
                        2,
                        2,
                        0,
                        3,
                        4,
                        512,
                        0,
                    ),
                ),
            ),
        )
        output = io.StringIO()
        errors = io.StringIO()
        exit_code = main(
            [
                "audiobookshelf-inventory",
                "--config",
                "/private/config.json",
                "--database",
                "/private/state.sqlite",
            ],
            state_check_runner=lambda _path: StateCheckResult(StateCheckOutcome.READY),
            audiobookshelf_inventory_runner=lambda _config, _database: result,
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("state=incomplete failure=timeout", errors.getvalue())
        self.assertIn("items=2 active=2 removed=0 history=3", errors.getvalue())
        self.assertNotIn("01234567", errors.getvalue())
        self.assertNotIn("scan-private", errors.getvalue())
        self.assertNotIn("/private", errors.getvalue())

    def test_heartbeat_projection_rejects_unknown_or_private_values(self) -> None:
        with self.assertRaises(ValueError):
            HeartbeatResult(HeartbeatOutcome.PASSED, ("/private/sentinel",))
        with self.assertRaises(ValueError):
            HeartbeatInspection(
                RunId("heartbeat-main"),
                RunState.SUPERSEDED,
                "/private/version",
                "a" * 64,
                RunId("replacement-one"),
                RunState.WAITING,
                "heartbeat-v2",
                "b" * 64,
                RunState.FAILED_TERMINAL,
                2,
                RunState.CANCELLED,
                JobState.CANCELLED,
                RunState.FAILED_TERMINAL,
                ((StageId("root"), StageState.CANCELLED),),
                RunState.FAILED_TERMINAL,
            )

    def test_job_heartbeat_success_is_fixed_and_path_free(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        result = HeartbeatResult(
            HeartbeatOutcome.PASSED,
            ("complete-set", "lease-recovery"),
            HeartbeatInspection(
                RunId("heartbeat-main"),
                RunState.SUPERSEDED,
                "heartbeat-v1",
                "a" * 64,
                RunId("replacement-one"),
                RunState.WAITING,
                "heartbeat-v2",
                "b" * 64,
                RunState.FAILED_TERMINAL,
                2,
                RunState.CANCELLED,
                JobState.CANCELLED,
                RunState.FAILED_TERMINAL,
                (
                    (StageId("root"), StageState.CANCELLED),
                    (StageId("left"), StageState.DEPENDENCY_CANCELLED),
                ),
                RunState.FAILED_TERMINAL,
            ),
        )

        exit_code = main(
            ["job-heartbeat", "--database", "/private/sentinel.sqlite"],
            state_check_runner=lambda _path: StateCheckResult(StateCheckOutcome.READY),
            job_heartbeat_runner=lambda _path: result,
            stdout=output,
            stderr=errors,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            "Job heartbeat passed.\n"
            "checks=complete-set,lease-recovery\n"
            "authority=synthetic-report-only\n"
            f"source=heartbeat-main state=superseded version=heartbeat-v1 digest={'a' * 64}\n"
            f"replacement=replacement-one state=waiting version=heartbeat-v2 digest={'b' * 64}\n"
            "retry=failed_terminal attempts=2\n"
            "cancellation=cancelled job=cancelled\n"
            "required-cancellation=failed_terminal stages=root:cancelled,left:dependency_cancelled\n"
            "negative-fan-in=failed_terminal\n",
        )
        self.assertNotIn("private", output.getvalue())
        self.assertEqual(errors.getvalue(), "")

    def test_job_heartbeat_failure_and_exception_do_not_leak(self) -> None:
        def hostile(_path: Path) -> HeartbeatResult:
            raise RuntimeError("/private/sentinel.sqlite token=secret")

        for runner in (
            lambda _path: HeartbeatResult(HeartbeatOutcome.FAILED),
            hostile,
            None,
        ):
            with self.subTest(runner=runner):
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    ["job-heartbeat", "--database", "/private/sentinel.sqlite"],
                    state_check_runner=lambda _path: StateCheckResult(
                        StateCheckOutcome.READY
                    ),
                    job_heartbeat_runner=runner,
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual(exit_code, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(
                    errors.getvalue(),
                    "Job heartbeat failed: durable synthetic recovery could not be verified.\n",
                )
                self.assertNotIn("sentinel", errors.getvalue())

    def test_state_check_success_uses_injected_runner_and_fixed_stdout(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        received: list[Path] = []

        exit_code = main(
            ["state-check", "--database", "state.sqlite"],
            state_check_runner=lambda path: (
                received.append(path) or StateCheckResult(StateCheckOutcome.READY)
            ),
            stdout=output,
            stderr=errors,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(received, [Path("state.sqlite")])
        self.assertEqual(output.getvalue(), "State check passed.\n")
        self.assertEqual(errors.getvalue(), "")

    def test_failures_have_fixed_stderr_and_no_stdout(self) -> None:
        for outcome in StateCheckOutcome:
            if outcome is StateCheckOutcome.READY:
                continue
            with self.subTest(outcome=outcome):
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    ["state-check", "--database", "/private/sentinel.sqlite"],
                    state_check_runner=lambda _path, value=outcome: StateCheckResult(
                        value
                    ),
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual(exit_code, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertNotIn("sentinel", errors.getvalue())
                self.assertNotIn("/private", errors.getvalue())
                self.assertTrue(errors.getvalue().startswith("State check failed:"))

    def test_runner_exception_and_invalid_result_do_not_leak_values(self) -> None:
        def hostile_runner(_path: Path) -> StateCheckResult:
            raise RuntimeError("/private/sentinel.sqlite token=private")

        for runner in (hostile_runner, lambda _path: object()):
            with self.subTest(runner=runner):
                output = io.StringIO()
                errors = io.StringIO()
                exit_code = main(
                    ["state-check", "--database", "/private/sentinel.sqlite"],
                    state_check_runner=runner,  # type: ignore[arg-type]
                    stdout=output,
                    stderr=errors,
                )
                self.assertEqual(exit_code, 1)
                self.assertEqual(output.getvalue(), "")
                self.assertEqual(
                    errors.getvalue(),
                    "State check failed: internal diagnostic error.\n",
                )

    def test_invalid_arguments_retain_argparse_exit_two(self) -> None:
        errors = io.StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            main(
                [],
                state_check_runner=lambda _path: StateCheckResult(
                    StateCheckOutcome.READY
                ),
            )

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("usage:", errors.getvalue())

    def test_in_memory_database_is_rejected_before_runner_invocation(self) -> None:
        runner = Mock()
        errors = io.StringIO()

        with redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            main(["state-check", "--database", ":memory:"], state_check_runner=runner)

        self.assertEqual(raised.exception.code, 2)
        runner.assert_not_called()
        self.assertNotIn(":memory:", errors.getvalue())
        self.assertIn("durable filesystem path", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
