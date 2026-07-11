from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock

from mechanical_librarian.application.state_check import (
    StateCheckOutcome,
    StateCheckResult,
)
from mechanical_librarian.interfaces.cli import main


class CliTests(unittest.TestCase):
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
                    state_check_runner=lambda _path, value=outcome: StateCheckResult(value),
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
