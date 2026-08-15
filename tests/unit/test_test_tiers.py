from __future__ import annotations

import json
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

from scripts.run_test_tier import (
    _MAX_TEST_TRACEBACK_BYTES,
    _MAX_TRACEBACK_STREAM_BYTES,
    _STALL_DIAGNOSTIC_SECONDS,
    COHORTS,
    _DiagnosticTestResult,
    _leaf_tests,
    cohort_manifest,
    load_cohort,
    run_cohort,
)

_FAILURE_SUMMARY_KEYS = {
    "errors",
    "failed_test_ids",
    "failures",
    "reason",
    "schema",
    "status",
    "tests_run",
    "tier",
    "unexpected_successes",
}


def _synthetic_cohort(count: int, detail: str) -> unittest.TestSuite:
    """Build a suite of `count` independently failing tests."""

    suite = unittest.TestSuite()
    for index in range(count):
        name = f"test_synthetic_{index:03d}"

        def _failure(case: unittest.TestCase, message: str = detail) -> None:
            print("suppressed-cohort-stdout")
            case.fail(message)

        cls = type(
            f"SyntheticCohort{index:03d}",
            (unittest.TestCase,),
            {name: _failure},
        )
        suite.addTest(cls(name))
    return suite


def _run_failing_cohort(suite: unittest.TestSuite) -> tuple[dict[str, Any], str]:
    """Run `suite` as a tier and return its JSON summary and narrative stream."""

    summary = StringIO()
    narrative = StringIO()
    with (
        patch("scripts.run_test_tier.load_cohort", return_value=suite),
        redirect_stderr(summary),
        redirect_stdout(narrative),
    ):
        passed = run_cohort(Path.cwd(), "fast")
    if passed:
        raise AssertionError("the synthetic cohort was expected to fail")
    decoded: dict[str, Any] = json.loads(summary.getvalue())
    return decoded, narrative.getvalue()


class TestTierTests(unittest.TestCase):
    def test_stall_diagnostic_exposes_only_a_safe_test_identifier(self) -> None:
        result = _DiagnosticTestResult()
        test = self.__class__("test_empty_cohort_fails_closed")
        result.startTest(test)
        self.assertEqual(
            result.stalled_test(time.monotonic() + _STALL_DIAGNOSTIC_SECONDS),
            test.id(),
        )
        result.stopTest(test)
        self.assertIsNone(
            result.stalled_test(time.monotonic() + _STALL_DIAGNOSTIC_SECONDS)
        )

    def test_source_tiers_are_disjoint_and_own_every_public_test_file(self) -> None:
        root = Path(__file__).resolve().parents[2]
        identifiers = {
            name: {test.id() for test in _leaf_tests(load_cohort(root, name))}
            for name in ("fast", "browser", "slow")
        }
        self.assertTrue(identifiers["fast"])
        self.assertTrue(identifiers["browser"])
        self.assertTrue(identifiers["slow"])
        self.assertTrue(identifiers["fast"].isdisjoint(identifiers["browser"]))
        self.assertTrue(identifiers["fast"].isdisjoint(identifiers["slow"]))
        self.assertTrue(identifiers["browser"].isdisjoint(identifiers["slow"]))

        owned_files = {
            path.resolve()
            for cohort in ("fast", "browser", "slow")
            for relative, pattern in COHORTS[cohort].discoveries
            for path in (root / relative).glob(pattern)
        }
        expected_files = {
            path.resolve() for path in (root / "tests").rglob("test_*.py")
        }
        self.assertEqual(owned_files, expected_files)

    def test_required_installed_is_an_explicit_fast_subset(self) -> None:
        root = Path(__file__).resolve().parents[2]
        fast = {test.id() for test in _leaf_tests(load_cohort(root, "fast"))}
        installed = {
            test.id() for test in _leaf_tests(load_cohort(root, "required-installed"))
        }
        self.assertTrue(installed)
        self.assertLess(installed, fast)

    def test_manifest_is_path_free_and_stable(self) -> None:
        root = Path(__file__).resolve().parents[2]
        first = cohort_manifest(root, "slow")
        second = cohort_manifest(root, "slow")
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "test-cohort-v1")
        self.assertNotIn(str(root), str(first))

    def test_representative_failure_reports_its_traceback(self) -> None:
        summary, narrative = _run_failing_cohort(_synthetic_cohort(1, "explain-me"))
        self.assertEqual(set(summary), _FAILURE_SUMMARY_KEYS)
        self.assertEqual(summary["schema"], "test-tier-result-v1")
        self.assertEqual(summary["reason"], "test_tier_failed")
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["tier"], "fast")
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(len(summary["failed_test_ids"]), 1)
        identifier = summary["failed_test_ids"][0]

        self.assertIn(f"==== fast failure: {identifier} ====", narrative)
        self.assertIn("Traceback (most recent call last)", narrative)
        self.assertIn("explain-me", narrative)
        # The JSON summary stream carries no narrative text.
        self.assertNotIn("Traceback", json.dumps(summary))
        # Raw cohort output stays discarded on every stream.
        self.assertNotIn("suppressed-cohort-stdout", narrative)
        self.assertNotIn("suppressed-cohort-stdout", json.dumps(summary))

    def test_success_summary_carries_no_narrative_diagnostics(self) -> None:
        class SyntheticPass(unittest.TestCase):
            def test_pass(self) -> None:
                print("suppressed-cohort-stdout")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticPass)
        narrative = StringIO()
        with (
            patch("scripts.run_test_tier.load_cohort", return_value=suite),
            redirect_stdout(narrative),
        ):
            self.assertTrue(run_cohort(Path.cwd(), "fast"))
        summary = json.loads(narrative.getvalue())
        self.assertEqual(
            summary,
            {
                "schema": "test-tier-result-v1",
                "skipped": 0,
                "status": "passed",
                "tests_run": 1,
                "tier": "fast",
            },
        )

    def test_failed_identifiers_are_reported_without_truncation(self) -> None:
        summary, narrative = _run_failing_cohort(_synthetic_cohort(40, "broad"))
        self.assertEqual(summary["tests_run"], 40)
        self.assertEqual(len(summary["failed_test_ids"]), 40)
        self.assertEqual(
            summary["failed_test_ids"], sorted(summary["failed_test_ids"])
        )
        for identifier in summary["failed_test_ids"]:
            with self.subTest(identifier=identifier):
                self.assertIn(f"==== fast failure: {identifier} ====", narrative)

    def test_tracebacks_are_bounded_per_test_and_in_total(self) -> None:
        detail = "boundary-" + ("x" * 8_192) + "-tail"
        summary, narrative = _run_failing_cohort(_synthetic_cohort(70, detail))
        self.assertEqual(len(summary["failed_test_ids"]), 70)

        emitted = narrative.encode("utf-8")
        self.assertLess(len(emitted), 2 * _MAX_TRACEBACK_STREAM_BYTES)
        self.assertIn("[elided ", narrative)
        self.assertIn("==== fast tracebacks omitted: ", narrative)
        # The retained tail keeps the assertion detail; the head is dropped.
        self.assertIn("-tail", narrative)
        self.assertNotIn("boundary-", narrative)

        blocks = narrative.split("==== fast failure: ")[1:]
        self.assertTrue(blocks)
        for block in blocks:
            with self.subTest(block=block[:64]):
                self.assertLessEqual(
                    len(block.encode("utf-8")),
                    _MAX_TEST_TRACEBACK_BYTES + 512,
                )

    def test_empty_cohort_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests" / "slow").mkdir(parents=True)
            (root / "tests" / "__init__.py").touch()
            (root / "tests" / "slow" / "__init__.py").touch()
            with self.assertRaises(RuntimeError):
                load_cohort(root, "slow")


if __name__ == "__main__":
    unittest.main()
