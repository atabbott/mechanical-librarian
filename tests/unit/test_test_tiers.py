from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.run_test_tier import (
    COHORTS,
    _leaf_tests,
    cohort_manifest,
    load_cohort,
    run_cohort,
)


class TestTierTests(unittest.TestCase):
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

    def test_representative_failure_fails_the_selected_tier(self) -> None:
        class SyntheticFailure(unittest.TestCase):
            def test_failure(self) -> None:
                print("private synthetic output")
                self.fail("synthetic")

        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SyntheticFailure)
        diagnostics = StringIO()
        with (
            patch("scripts.run_test_tier.load_cohort", return_value=suite),
            redirect_stderr(diagnostics),
        ):
            self.assertFalse(run_cohort(Path.cwd(), "fast"))
        self.assertIn('"reason":"test_tier_failed"', diagnostics.getvalue())
        self.assertNotIn("synthetic", diagnostics.getvalue())

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
