#!/usr/bin/env python3
"""Run one explicit, non-overlapping public test cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TestCohort:
    discoveries: tuple[tuple[str, str], ...]


COHORTS = {
    "fast": TestCohort(
        discoveries=(
            ("tests/unit", "test_*.py"),
            ("tests/integration", "test_*.py"),
            ("tests/architecture", "test_*.py"),
        )
    ),
    "browser": TestCohort(discoveries=(("tests/browser", "test_*.py"),)),
    "slow": TestCohort(discoveries=(("tests/slow", "test_*.py"),)),
    "required-installed": TestCohort(
        discoveries=(
            ("tests/unit", "test_package.py"),
            ("tests/architecture", "test_import_boundaries.py"),
            ("tests/architecture", "test_epub_fixture_distribution.py"),
            ("tests/integration", "test_review_case_lifecycle.py"),
        )
    ),
}


def _leaf_tests(suite: unittest.TestSuite) -> tuple[unittest.TestCase, ...]:
    tests: list[unittest.TestCase] = []
    for value in suite:
        if isinstance(value, unittest.TestSuite):
            tests.extend(_leaf_tests(value))
        else:
            tests.append(value)
    return tuple(tests)


def load_cohort(root: Path, name: str) -> unittest.TestSuite:
    try:
        cohort = COHORTS[name]
    except KeyError:
        raise ValueError("unknown test cohort") from None
    loader = unittest.TestLoader()
    suites = [
        loader.discover(
            str(root / relative),
            pattern=pattern,
            top_level_dir=str(root),
        )
        for relative, pattern in cohort.discoveries
    ]
    if loader.errors:
        raise RuntimeError("test_cohort_import_failed")
    suite = unittest.TestSuite(suites)
    if not _leaf_tests(suite):
        raise RuntimeError("empty test cohort")
    return suite


def cohort_manifest(root: Path, name: str) -> dict[str, object]:
    identifiers = tuple(
        sorted(test.id() for test in _leaf_tests(load_cohort(root, name)))
    )
    digest = hashlib.sha256("\n".join(identifiers).encode("utf-8")).hexdigest()
    return {
        "schema": "test-cohort-v1",
        "tier": name,
        "test_count": len(identifiers),
        "test_ids_sha256": digest,
    }


def run_cohort(root: Path, name: str) -> bool:
    result = unittest.TestResult()
    with Path(os.devnull).open("w", encoding="utf-8") as discarded:
        with redirect_stdout(discarded), redirect_stderr(discarded):
            load_cohort(root, name).run(result)
    if result.wasSuccessful():
        print(
            json.dumps(
                {
                    "schema": "test-tier-result-v1",
                    "skipped": len(result.skipped),
                    "status": "passed",
                    "tests_run": result.testsRun,
                    "tier": name,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return True
    failed_ids = sorted(
        [test.id() for test, _detail in (*result.failures, *result.errors)]
        + [test.id() for test in result.unexpectedSuccesses]
    )[:32]
    print(
        json.dumps(
            {
                "errors": len(result.errors),
                "failed_test_ids": failed_ids,
                "failures": len(result.failures),
                "reason": "test_tier_failed",
                "schema": "test-tier-result-v1",
                "status": "failed",
                "tests_run": result.testsRun,
                "tier": name,
                "unexpected_successes": len(result.unexpectedSuccesses),
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
    )
    return False


def _verify_exact_source(root: Path, expected_sha: str) -> None:
    if len(expected_sha) != 40 or any(
        value not in "0123456789abcdef" for value in expected_sha
    ):
        raise RuntimeError("source_identity_invalid")
    try:
        actual = subprocess.run(
            ("git", "-C", str(root), "rev-parse", "HEAD"),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = subprocess.run(
            (
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise RuntimeError("source_provenance_unavailable") from None
    if actual != expected_sha:
        raise RuntimeError("source_identity_mismatch")
    if dirty:
        raise RuntimeError("source_not_clean")


def _run_complete(root: Path, source_sha: str) -> bool:
    _verify_exact_source(root, source_sha)
    if not run_cohort(root, "fast"):
        return False
    browser_verified = subprocess.run(
        (sys.executable, str(root / "scripts" / "verify_playwright_browser.py")),
        cwd=root,
        check=False,
    )
    if browser_verified.returncode != 0:
        return False
    if not run_cohort(root, "browser"):
        return False
    completed = subprocess.run(
        (
            sys.executable,
            str(root / "scripts" / "qualify_exact_artifact.py"),
            "--source-sha",
            source_sha,
            "--test-tier",
            "complete",
        ),
        cwd=root,
        check=False,
    )
    return completed.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tier", choices=(*COHORTS, "complete-installed", "complete")
    )
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--source-sha")
    arguments = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        if arguments.tier == "complete":
            if arguments.list or arguments.source_sha is None:
                parser.error("complete requires --source-sha and cannot be listed")
            passed = _run_complete(root, arguments.source_sha)
        elif arguments.tier == "complete-installed":
            if arguments.list:
                manifests = tuple(
                    cohort_manifest(root, name)
                    for name in ("required-installed", "slow")
                )
                print(json.dumps(manifests, sort_keys=True, separators=(",", ":")))
                return 0
            passed = run_cohort(root, "required-installed") and run_cohort(root, "slow")
        elif arguments.list:
            print(
                json.dumps(
                    cohort_manifest(root, arguments.tier),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0
        else:
            passed = run_cohort(root, arguments.tier)
    except RuntimeError as error:
        print(
            json.dumps(
                {
                    "reason": str(error),
                    "schema": "test-tier-result-v1",
                    "status": "failed",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
