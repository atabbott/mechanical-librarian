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
from types import ModuleType

import pytest

_STALL_DIAGNOSTIC_SECONDS = 30
_SAFE_TEST_ID = re.compile(r"[A-Za-z0-9_.]{1,512}")
_UNSAFE_IDENTITY_CHARACTER = re.compile(r"[^A-Za-z0-9_]")
# The bound `_SAFE_TEST_ID` already enforces, applied where an identity can
# actually grow: a parameter set is arbitrary text, while the module, class,
# and function names before it are Python identifiers.
_MAX_IDENTITY_CHARACTERS = 512
_PARAMETER_DIGEST_CHARACTERS = 16
# Every variable pytest documents under `Environment variables` in `--help`
# that can reach collection, execution, plugin loading, or the diagnostic
# streams. `PYTEST_THEME` and `PYTEST_THEME_MODE` are deliberately absent:
# they only colour the terminal stream, which every tier already discards to
# `os.devnull`, and cannot change what runs or what either schema reports.
_REJECTED_PYTEST_ENVIRONMENT = (
    "PYTEST_ADDOPTS",
    "PYTEST_DEBUG",
    "PYTEST_DEBUG_TEMPROOT",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTEST_PLUGINS",
)
# Ceilings for the failure-diagnostic stream. An over-long traceback keeps its
# head and its tail, split within the single-traceback ceiling, and declares the
# elided middle. Neither end alone is sufficient, because the two failure classes
# a tier reports put the answer at opposite ends.
#
# The tail holds the assertion and the innermost frame, which is what
# `--tb=native` guarantees a truncated traceback still ends at, so it keeps the
# larger share. But a transport error is not an assertion: a Playwright failure
# opens with `Locator.click: Route.fetch: socket hang up` and closes with
# kilobytes of `Call log:` request headers. Tail-only retention reported exactly
# that -- a fragment ending mid-header-dump with the error identity and every
# Python frame elided as leading bytes -- and three sightings of #730 were
# recorded as "the tooling lost the traceback" because of it. The head is sized
# for an error identity and its opening frames, not for a whole traceback.
#
# The aggregate ceiling bounds every emitted byte, framing included, so a broad
# failure stays readable in a hosted log and well inside the exact-artifact
# qualifier's one-mebibyte per-stream capture bound -- being terminated at that
# bound would cost the result schema line itself.
_MAX_TEST_TRACEBACK_BYTES = 4_096
_TEST_TRACEBACK_HEAD_BYTES = 1_024
_TEST_TRACEBACK_TAIL_BYTES = _MAX_TEST_TRACEBACK_BYTES - _TEST_TRACEBACK_HEAD_BYTES
_MAX_TRACEBACK_STREAM_BYTES = 262_144

# pytest is the execution engine; these arguments carry the properties the
# `unittest` runner used to supply directly.
#
# `--capture=sys` replaces only `sys.stdout`/`sys.stderr`, exactly as the
# `redirect_*` context managers below already do, so file descriptor 2 keeps
# pointing at the real stream and the stall diagnostic still reaches an
# observer while the cohort's own output stays discarded. Descriptor-level
# capture would swallow it.
#
# `--tb=native` emits the interpreter's own traceback format rather than
# pytest's annotated one, so a traceback truncated to its retained tail still
# ends with the assertion and the innermost frame.
#
# `-p no:cacheprovider` leaves no `.pytest_cache` behind, so a qualified source
# snapshot stays byte-identical to the commit it was taken from.
#
# `--disable-plugin-autoload` keeps the cohort out of reach of whatever happens
# to be installed. Every distribution exposing a `pytest11` entry point is
# loaded automatically, so a `pytest-randomly` or `pytest-xdist` in a
# contributor's environment could reorder, shard, or drop cases with no
# environment variable set and nothing rejected. A tier loads pytest's builtins
# and the runner's own plugin, and nothing else.
_PYTEST_ARGUMENTS = (
    "--capture=sys",
    "--tb=native",
    "-q",
    "--no-header",
    "--disable-plugin-autoload",
    "-p",
    "no:cacheprovider",
)
_RUN_EXIT_CODES = frozenset((pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED))


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


def cohort_targets(root: Path, name: str) -> tuple[str, ...]:
    """Resolve one cohort's discoveries to the exact files pytest is given.

    Naming the files rather than the directories keeps the tier contract in
    `COHORTS`: a cohort admits exactly the paths its declared patterns match,
    and never a sibling that a directory-wide collection would sweep in.
    """

    try:
        cohort = COHORTS[name]
    except KeyError:
        raise ValueError("unknown test cohort") from None
    targets: list[str] = []
    for relative, pattern in cohort.discoveries:
        targets.extend(sorted(str(path) for path in (root / relative).glob(pattern)))
    return tuple(targets)


def _verify_pytest_environment() -> None:
    """Refuse to run a tier under ambient pytest configuration.

    pytest incorporates these variables on its own. The runner supplies the
    exact arguments a tier's contract needs, so anything that adds options,
    loads or suppresses plugins, or writes to the diagnostic streams can change
    what runs or what is reported: `PYTEST_ADDOPTS='-k passing'` alone reduces a
    cohort to its passing subset, returns success, and reports the tier passed,
    while `PYTEST_ADDOPTS='--capture=fd'` silences the stall diagnostic. A tier
    fails closed rather than proceeding under rules it did not choose.

    Only the variable names are reported. They come from the closed list below,
    so naming them stays path-free; the values are arbitrary and may carry
    paths, so they are not echoed.
    """

    present = tuple(
        name for name in _REJECTED_PYTEST_ENVIRONMENT if os.environ.get(name)
    )
    if present:
        raise RuntimeError(f"pytest_environment_override:{','.join(present)}")


def _reduced_parameters(parameters: str, available: int) -> str:
    """Reduce one parameter set to a bounded component of the safe alphabet.

    Reduction alone leaves the identity as long as the parameters, so a long
    one still fails `_SAFE_TEST_ID` and still costs the stall diagnostic.
    Truncating alone would map distinct parameter sets onto one identity, which
    the collision guard would then refuse -- correctly, but on a legitimate
    cohort. The retained head is therefore paired with a digest of the exact
    parameters, so distinct parameters keep distinct identities and the guard
    stays a backstop rather than a tripwire.
    """

    reduced = _UNSAFE_IDENTITY_CHARACTER.sub("_", parameters)
    if len(reduced) <= available:
        return reduced
    digest = hashlib.sha256(parameters.encode("utf-8")).hexdigest()
    digest = digest[:_PARAMETER_DIGEST_CHARACTERS]
    head = reduced[: max(0, available - _PARAMETER_DIGEST_CHARACTERS - 1)]
    return f"{head}_{digest}"


def _case_identity(item: pytest.Item) -> str:
    """Name a collected case as a dotted, path-free identity.

    A `unittest.TestCase` case keeps exactly the name `TestCase.id()` produces,
    so a cohort digest is comparable across the engine change.

    A native pytest case has only a node id, which carries the file path and,
    when parameterized, its bracketed parameters. Neither may reach a cohort
    manifest, a reported failure identity, or the stall diagnostic, whose
    `_SAFE_TEST_ID` guard would withhold it entirely. It is rebuilt from the
    same parts a unittest identity uses, with the parameters as one further
    dotted component reduced to the safe alphabet.
    """

    cls = getattr(item, "cls", None)
    if isinstance(cls, type):
        prefix = f"{cls.__module__}.{cls.__qualname__}"
    else:
        module = getattr(item, "module", None)
        prefix = getattr(module, "__name__", None) or item.nodeid.split("::")[0]
    name, bracket, parameters = item.name.partition("[")
    stem = f"{prefix}.{name}"
    if not bracket:
        return stem
    available = _MAX_IDENTITY_CHARACTERS - len(stem) - 1
    return f"{stem}.{_reduced_parameters(parameters.removesuffix(']'), available)}"


def _shared_fixture_identity(owner: str | None, phase: str) -> str | None:
    """Name a class-scoped setup or teardown failure once, as unittest does.

    pytest injects `setUpClass`/`tearDownClass` as class-scoped fixtures and
    reports their failure against every method of the class, so an unexecuted
    two-method class would otherwise report two tests run and two errors where
    unittest reports none run and one class-level error. A `TestCase` runs its
    instance `setUp` inside the call phase, so a failure in any other phase
    belongs to the class, not to the method it is reported against.
    """

    if owner is None or phase == "call":
        return None
    return f"{owner}.{'setUpClass' if phase == 'setup' else 'tearDownClass'}"


def _load_tests_exclusions(items: list[pytest.Item]) -> list[pytest.Item]:
    """Return the items a module's `load_tests` protocol excludes.

    pytest does not implement `load_tests`, so a module that uses it to select
    a subset of its own namespace would otherwise contribute every `TestCase`
    reachable from that namespace -- including imported bases, whose cases
    already belong to another tier. Ask unittest for the module's authoritative
    case set and drop anything outside it.
    """

    loader = unittest.TestLoader()
    admitted: dict[ModuleType, frozenset[str]] = {}
    excluded: list[pytest.Item] = []
    for item in items:
        module = getattr(item, "module", None)
        if module is None or not hasattr(module, "load_tests"):
            continue
        if module not in admitted:
            admitted[module] = frozenset(
                test.id() for test in _leaf_tests(loader.loadTestsFromModule(module))
            )
            if loader.errors:
                raise RuntimeError("test_cohort_import_failed")
        if _case_identity(item) not in admitted[module]:
            excluded.append(item)
    return excluded


@dataclass(frozen=True, slots=True)
class _PhaseRecord:
    """One reported phase of one case, kept until the session can be accounted.

    `pytest_runtest_logreport` fires before `pytest_exception_interact`, so the
    exception type that separates a failure from an error is not known when a
    report arrives. Nothing is counted until the session ends.
    """

    nodeid: str
    identity: str
    shared: str | None
    phase: str
    outcome: str
    expected_failure: bool
    detail: str


class _CohortSession:
    """Observe one pytest session and account for it as a test tier.

    Failure and error accounting follows unittest's rule exactly: a case that
    raises `AssertionError` is a failure and anything else is an error,
    whichever phase raised it; a class-scoped setup or teardown failure is one
    class-level error and runs no tests.
    """

    def __init__(self) -> None:
        self._progress_lock = threading.Lock()
        self._current_test_id: str | None = None
        self._current_started_at = 0.0
        self._identities: dict[str, str] = {}
        self._owners: dict[str, str] = {}
        self._records: list[_PhaseRecord] = []
        self._assertions: dict[tuple[str, str], bool] = {}
        self.identifiers: tuple[str, ...] = ()
        self.import_failed = False
        self.identity_collision = False
        self.tests_run = 0
        self.failures = 0
        self.errors = 0
        self.unexpected_successes = 0
        self.skipped: set[str] = set()
        self.failed_test_ids: set[str] = set()
        self.tracebacks: list[tuple[str, str]] = []

    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.import_failed = True

    def pytest_collection_modifyitems(
        self,
        config: pytest.Config,
        items: list[pytest.Item],
    ) -> None:
        excluded = _load_tests_exclusions(items)
        if excluded:
            dropped = {item.nodeid for item in excluded}
            items[:] = [item for item in items if item.nodeid not in dropped]
            config.hook.pytest_deselected(items=excluded)
        self._identities = {item.nodeid: _case_identity(item) for item in items}
        self._owners = {
            item.nodeid: f"{item.cls.__module__}.{item.cls.__qualname__}"
            for item in items
            if isinstance(getattr(item, "cls", None), type)
            and issubclass(item.cls, unittest.TestCase)
        }
        self.identifiers = tuple(sorted(self._identities.values()))
        # Reducing a parameter set to the safe alphabet could map two distinct
        # cases onto one name, which would silently shrink the manifest. A
        # cohort whose identities are not unique is not accountable.
        self.identity_collision = len(set(self.identifiers)) != len(self.identifiers)

    def pytest_runtest_logstart(self, nodeid: str, location: object) -> None:
        identifier = self._identities.get(nodeid, nodeid)
        with self._progress_lock:
            self._current_test_id = (
                identifier if _SAFE_TEST_ID.fullmatch(identifier) is not None else None
            )
            self._current_started_at = time.monotonic()

    def pytest_runtest_logfinish(self, nodeid: str, location: object) -> None:
        with self._progress_lock:
            self._current_test_id = None
            self._current_started_at = 0.0

    def pytest_exception_interact(
        self,
        node: pytest.Item | pytest.Collector,
        call: pytest.CallInfo[object],
        report: pytest.CollectReport | pytest.TestReport,
    ) -> None:
        if not isinstance(report, pytest.TestReport) or call.excinfo is None:
            return
        self._assertions[(report.nodeid, call.when)] = isinstance(
            call.excinfo.value, AssertionError
        )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        phase = report.when or "call"
        self._records.append(
            _PhaseRecord(
                nodeid=report.nodeid,
                identity=self._identities.get(report.nodeid, report.nodeid),
                shared=_shared_fixture_identity(self._owners.get(report.nodeid), phase),
                phase=phase,
                outcome=report.outcome,
                expected_failure=hasattr(report, "wasxfail"),
                detail=report.longreprtext,
            )
        )

    def account(self) -> None:
        """Reduce the recorded phases to the tier's reported counts."""

        shared_seen: set[str] = set()
        for record in self._records:
            if record.phase == "setup" and record.outcome != "failed":
                self.tests_run += 1
            if record.outcome == "skipped":
                if not record.expected_failure:
                    self.skipped.add(record.identity)
                continue
            if record.outcome == "passed":
                if record.expected_failure:
                    # A non-strict `xfail` that passes. unittest counts this
                    # separately and still refuses to call the run successful.
                    self.unexpected_successes += 1
                    self.failed_test_ids.add(record.identity)
                continue
            if record.shared is not None:
                if record.shared in shared_seen:
                    continue
                shared_seen.add(record.shared)
                self.errors += 1
                self.failed_test_ids.add(record.shared)
                self.tracebacks.append((record.shared, record.detail))
                continue
            if self._assertions.get((record.nodeid, record.phase), False):
                self.failures += 1
            else:
                self.errors += 1
            self.failed_test_ids.add(record.identity)
            self.tracebacks.append((record.identity, record.detail))

    def stalled_test(self, now: float) -> str | None:
        with self._progress_lock:
            if (
                self._current_test_id is None
                or now - self._current_started_at < _STALL_DIAGNOSTIC_SECONDS
            ):
                return None
            return self._current_test_id


def _drive_pytest(
    root: Path,
    name: str,
    session: _CohortSession,
    *,
    collect_only: bool,
) -> pytest.ExitCode | int:
    _verify_pytest_environment()
    targets = cohort_targets(root, name)
    if not targets:
        raise RuntimeError("empty test cohort")
    arguments = [*_PYTEST_ARGUMENTS, *targets]
    if collect_only:
        arguments.insert(0, "--collect-only")
    with Path(os.devnull).open("w", encoding="utf-8") as discarded:
        with redirect_stdout(discarded), redirect_stderr(discarded):
            return pytest.main(arguments, plugins=[session])


def collect_cohort(root: Path, name: str) -> tuple[str, ...]:
    session = _CohortSession()
    outcome = _drive_pytest(root, name, session, collect_only=True)
    if session.import_failed or outcome != pytest.ExitCode.OK:
        raise RuntimeError("test_cohort_import_failed")
    if session.identity_collision:
        raise RuntimeError("test_cohort_identity_collision")
    if not session.identifiers:
        raise RuntimeError("empty test cohort")
    return session.identifiers


def cohort_manifest(root: Path, name: str) -> dict[str, object]:
    identifiers = collect_cohort(root, name)
    digest = hashlib.sha256("\n".join(identifiers).encode("utf-8")).hexdigest()
    return {
        "schema": "test-cohort-v1",
        "tier": name,
        "test_count": len(identifiers),
        "test_ids_sha256": digest,
    }


def run_cohort(root: Path, name: str) -> bool:
    session = _CohortSession()
    stopped = threading.Event()
    reporter = threading.Thread(
        target=_report_stalled_test,
        args=(session, stopped),
        daemon=True,
    )
    reporter.start()
    try:
        outcome = _drive_pytest(root, name, session, collect_only=False)
    finally:
        stopped.set()
        reporter.join(timeout=2)
    if session.import_failed:
        raise RuntimeError("test_cohort_import_failed")
    if session.identity_collision:
        raise RuntimeError("test_cohort_identity_collision")
    if not session.identifiers:
        raise RuntimeError("empty test cohort")
    if outcome not in _RUN_EXIT_CODES:
        raise RuntimeError("test_cohort_run_failed")
    session.account()
    # pytest's exit code alone is not the tier's verdict. A non-strict `xfail`
    # that passes leaves it `OK` while the run holds an unexpected success, and
    # unittest refused to call such a run successful. The tier passes only when
    # the engine and the runner's own accounting both find nothing.
    if (
        outcome == pytest.ExitCode.OK
        and not session.failed_test_ids
        and not session.unexpected_successes
    ):
        print(
            json.dumps(
                {
                    "schema": "test-tier-result-v1",
                    "skipped": len(session.skipped),
                    "status": "passed",
                    "tests_run": session.tests_run,
                    "tier": name,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return True
    tracebacks = tuple(sorted(session.tracebacks))
    _report_failure_tracebacks(name, tracebacks)
    print(
        json.dumps(
            {
                "errors": session.errors,
                "failed_test_ids": sorted(session.failed_test_ids),
                "failures": session.failures,
                "reason": "test_tier_failed",
                "schema": "test-tier-result-v1",
                "status": "failed",
                "tests_run": session.tests_run,
                "tier": name,
                "unexpected_successes": session.unexpected_successes,
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
    reserved = len(f"==== {tier} tracebacks omitted: {len(tracebacks)} ====\n".encode())
    remaining = _MAX_TRACEBACK_STREAM_BYTES - reserved
    omitted = 0
    for index, (identifier, detail) in enumerate(tracebacks):
        encoded = detail.encode("utf-8", "replace")
        elided = len(encoded) - _MAX_TEST_TRACEBACK_BYTES
        if elided > 0:
            head = encoded[:_TEST_TRACEBACK_HEAD_BYTES].decode("utf-8", "replace")
            tail = encoded[-_TEST_TRACEBACK_TAIL_BYTES:].decode("utf-8", "replace")
            body = f"{head}\n[elided {elided} bytes]\n{tail}"
        else:
            body = encoded.decode("utf-8", "replace")
        block = f"==== {tier} failure: {identifier} ====\n"
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
    session: _CohortSession,
    stopped: threading.Event,
) -> None:
    reported: str | None = None
    while not stopped.wait(timeout=1):
        identifier = session.stalled_test(time.monotonic())
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


def tier_inventory() -> dict[str, object]:
    """Name every tier this runner accepts, so the documented set is checkable.

    `scripts/run_test_tier.py --list` is the whole inventory and
    `scripts/run_test_tier.py <tier> --list` is one cohort's manifest. Names
    only: a cohort's count and digest cost an import of every module it owns,
    which the inventory has no reason to pay.
    """

    return {
        "schema": "test-tier-inventory-v1",
        "tiers": sorted((*COHORTS, "complete-installed", "complete")),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tier",
        nargs="?",
        choices=(*COHORTS, "complete-installed", "complete"),
    )
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--source-sha")
    arguments = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if arguments.tier is None:
        if not arguments.list:
            parser.error("a tier is required unless --list names the inventory")
        print(json.dumps(tier_inventory(), sort_keys=True, separators=(",", ":")))
        return 0
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
