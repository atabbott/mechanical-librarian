#!/usr/bin/env python3
"""Run one explicit, non-overlapping public test cohort."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

_STALL_DIAGNOSTIC_SECONDS = 30
_SAFE_TEST_ID = re.compile(r"[A-Za-z0-9_.]{1,512}")
# Ceilings for the failure-diagnostic stream. A single traceback keeps its tail,
# which holds the assertion and the innermost frame. The aggregate ceiling bounds
# every emitted byte, framing included, so a broad failure stays readable in a
# hosted log and well inside the exact-artifact qualifier's one-mebibyte
# per-stream capture bound -- being terminated at that bound would cost the
# result schema line itself.
_MAX_TEST_TRACEBACK_BYTES = 4_096
_MAX_TRACEBACK_STREAM_BYTES = 262_144


@dataclass(frozen=True, slots=True)
class TestCohort:
    discoveries: tuple[tuple[str, str], ...]


class _DiagnosticTestResult(unittest.TestResult):
    def __init__(self) -> None:
        super().__init__()
        self._progress_lock = threading.Lock()
        self._current_test_id: str | None = None
        self._current_started_at = 0.0

    def startTest(self, test: unittest.TestCase) -> None:
        with self._progress_lock:
            identifier = test.id()
            self._current_test_id = (
                identifier if _SAFE_TEST_ID.fullmatch(identifier) is not None else None
            )
            self._current_started_at = time.monotonic()
        super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:
        with self._progress_lock:
            self._current_test_id = None
            self._current_started_at = 0.0
        super().stopTest(test)

    def stalled_test(self, now: float) -> str | None:
        with self._progress_lock:
            if (
                self._current_test_id is None
                or now - self._current_started_at < _STALL_DIAGNOSTIC_SECONDS
            ):
                return None
            return self._current_test_id


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
            ("tests/integration", "test_continuous_review_scale.py"),
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
    result = _DiagnosticTestResult()
    stopped = threading.Event()
    reporter = threading.Thread(
        target=_report_stalled_test,
        args=(result, stopped),
        daemon=True,
    )
    reporter.start()
    with Path(os.devnull).open("w", encoding="utf-8") as discarded:
        try:
            with redirect_stdout(discarded), redirect_stderr(discarded):
                load_cohort(root, name).run(result)
        finally:
            stopped.set()
            reporter.join(timeout=2)
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
    tracebacks = tuple(
        sorted(
            (test.id(), detail)
            for test, detail in (*result.failures, *result.errors)
        )
    )
    failed_ids = sorted(
        [identifier for identifier, _detail in tracebacks]
        + [test.id() for test in result.unexpectedSuccesses]
    )
    _report_failure_tracebacks(name, tracebacks)
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


def _report_failure_tracebacks(
    tier: str,
    tracebacks: tuple[tuple[str, str], ...],
) -> None:
    """Emit bounded per-test tracebacks on the narrative diagnostic stream.

    The machine-readable ``test-tier-result-v1`` and ``test-tier-stall-v1``
    lines stay on standard error. This stream stays on standard output so no
    consumer of either schema sees narrative text on its stream. Raw cohort
    output remains discarded; only the formatted traceback of a failing test
    is reported.

    Every emitted byte is charged against the aggregate ceiling, framing
    included: many short failures carrying long test identifiers would
    otherwise be bounded by nothing. Room for the closing omission notice is
    reserved up front so truncation is always declared.
    """
    reserved = len(
        f"==== {tier} tracebacks omitted: {len(tracebacks)} ====\n".encode()
    )
    remaining = _MAX_TRACEBACK_STREAM_BYTES - reserved
    omitted = 0
    for index, (identifier, detail) in enumerate(tracebacks):
        encoded = detail.encode("utf-8", "replace")
        elided = len(encoded) - _MAX_TEST_TRACEBACK_BYTES
        if elided > 0:
            encoded = encoded[-_MAX_TEST_TRACEBACK_BYTES:]
        body = encoded.decode("utf-8", "replace")
        block = f"==== {tier} failure: {identifier} ====\n"
        if elided > 0:
            block += f"[elided {elided} leading bytes]\n"
        block += body if body.endswith("\n") else f"{body}\n"
        charged = len(block.encode("utf-8"))
        if charged > remaining:
            omitted = len(tracebacks) - index
            break
        remaining -= charged
        sys.stdout.write(block)
    if omitted:
        sys.stdout.write(f"==== {tier} tracebacks omitted: {omitted} ====\n")
    sys.stdout.flush()


def _report_stalled_test(
    result: _DiagnosticTestResult,
    stopped: threading.Event,
) -> None:
    reported: str | None = None
    while not stopped.wait(timeout=1):
        identifier = result.stalled_test(time.monotonic())
        if identifier is None or identifier == reported:
            continue
        print(
            json.dumps(
                {
                    "schema": "test-tier-stall-v1",
                    "status": "running",
                    "test_id": identifier,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.__stderr__,
            flush=True,
        )
        reported = identifier


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
