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
from types import ModuleType, TracebackType

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
# Ceilings for the failure-diagnostic stream. An over-long traceback keeps two
# windows -- one anchored on the exception line, one on the end of the text --
# and declares every byte it drops between them. Neither the literal head nor
# the tail alone is sufficient, and the literal head is the weakest of the
# three, because of what `--tb=native` actually produces.
#
# `report.longreprtext` does not open with the error. It opens with the frame
# block, and the frames a tier reports are pytest's own runner internals before
# they are the test's. The error identity is the exception line, which sits
# after that whole block, and anything the exception message carries -- a
# Playwright `Call log:` runs to kilobytes -- follows it. So for the failure
# this bound exists for, the identity is in the middle of the text: measured at
# byte 4,738 of 15,862 for a `Route.fetch: socket hang up` raised through a
# realistic call chain. Tail-only retention elided it as leading bytes, and
# three sightings of #730 were recorded as "the tooling lost the traceback"
# because of it. A literal head would have elided it too, as trailing bytes,
# and would have spent the whole window on `_pytest/runner.py`.
#
# Anchoring the first window on the exception line is what keeps it. The
# anchored window is sized for an error identity and the opening of its
# message, not for a whole traceback; the frame block ahead of it is dropped at
# that structural boundary rather than mid-line. The tail keeps the larger
# share because it holds the assertion and the innermost frame, which is what
# `--tb=native` guarantees a truncated traceback still ends at, and because a
# long exception message pushes its own end out of the anchored window.
#
# The aggregate ceiling bounds every emitted byte, framing included, so a broad
# failure stays readable in a hosted log and well inside the exact-artifact
# qualifier's one-mebibyte per-stream capture bound -- being terminated at that
# bound would cost the result schema line itself.
_MAX_TEST_TRACEBACK_BYTES = 4_096
_TEST_TRACEBACK_HEAD_BYTES = 1_024
_TEST_TRACEBACK_TAIL_BYTES = _MAX_TEST_TRACEBACK_BYTES - _TEST_TRACEBACK_HEAD_BYTES
# The line CPython writes above every frame block, which `--tb=native` inherits.
_NATIVE_TRACEBACK_HEADER = b"Traceback (most recent call last):"
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
# The shape `unittest.TestResult` is handed a failure in, spelled out
# because typeshed keeps its own alias private.
_ExceptionInfo = (
    tuple[type[BaseException], BaseException, TracebackType | None]
    | tuple[None, None, None]
)
_SubTestInfo = (
    tuple[type[BaseException], BaseException, TracebackType]
    | tuple[None, None, None]
    | None
)


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
    # The hosted `container-image` job runs exactly the probe cases against
    # the binary extracted from the image it just built (#929). The file stays
    # a member of `slow`, the same way `required-installed` re-runs files that
    # `fast` owns: the slow tier remains the complete maintainer-local cohort.
    "probe": TestCohort(discoveries=(("tests/slow", "test_audio_probe.py"),)),
    "required-installed": TestCohort(
        discoveries=(
            ("tests/unit", "test_package.py"),
            ("tests/architecture", "test_import_boundaries.py"),
            ("tests/architecture", "test_epub_fixture_distribution.py"),
            ("tests/integration", "test_review_case_lifecycle.py"),
            ("tests/installed", "test_continuous_review_scale.py"),
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

    A case the `load_tests` protocol added carries unittest's own identity,
    which it was constructed with and this runner cannot rebuild from a node
    id it never had.
    """

    added = getattr(item, "unittest_identity", None)
    if added is not None:
        return added
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


def _shared_fixture_identity(
    owner: str | None,
    phase: str,
    shared_setup: bool,
) -> str | None:
    """Name a class-scoped setup or teardown failure once, as unittest does.

    pytest injects `setUpClass`/`tearDownClass` as class-scoped fixtures and
    reports their outcome against every method of the class, so an unexecuted
    two-method class would otherwise report two tests run and two errors where
    unittest reports none run and one class-level error. A `TestCase` runs its
    instance `setUp` inside the call phase, so a call-phase failure belongs to
    the method rather than to the class.

    Not every setup phase a `TestCase` reports is that shared fixture, though,
    and `shared_setup` is what the caller has established about the one that
    actually raised. It is positive rather than an absence: a setup phase no
    fixture at all stands behind -- a `skip` mark, which pytest evaluates
    before any fixture runs -- is per-case, and reading it as shared would
    charge a per-method skip to `setUpClass` and drop that case from the run
    count. A function-scoped fixture is the instance `setUp` of a
    pytest-native suite: it runs once per case, so unittest's accounting for it
    is one error per case, not one for the class. A class carrying
    `__unittest_skip__` is likewise per-case, because unittest never reaches
    `setUpClass` for it and records one skip against each method it did not
    run -- where a `SkipTest` raised inside `setUpClass` is one class-level
    skip with nothing run.

    The teardown phase stays class-level unconditionally. Its unittest-parity
    edge is the mirror of the setup one -- a function-scoped fixture's
    finalizer raising is per-case -- but pytest reports a finalizer failure
    with no hook that names the fixture behind it, and #701 bounds this to the
    three setup-side and collection-side cases it names.
    """

    if owner is None or phase == "call":
        return None
    if phase == "setup" and not shared_setup:
        return None
    return f"{owner}.{'setUpClass' if phase == 'setup' else 'tearDownClass'}"


class _AddedCaseResult(unittest.TestResult):
    """Capture one added case's outcome without unittest's own reporting.

    `TestResult` keeps formatted text, not the exception, and the tier's
    failure-versus-error rule needs the exception itself. Retaining it lets the
    item re-raise the original, so an `AssertionError` stays a failure and
    everything else stays an error on the same rule every other case uses.
    """

    def __init__(self) -> None:
        super().__init__()
        self.raised: BaseException | None = None
        self.skip_reason: str | None = None

    def addError(self, test: unittest.TestCase, err: _ExceptionInfo) -> None:
        self.raised = err[1]

    def addFailure(self, test: unittest.TestCase, err: _ExceptionInfo) -> None:
        self.raised = err[1]

    def addSkip(self, test: unittest.TestCase, reason: str) -> None:
        self.skip_reason = reason

    def addSubTest(
        self,
        test: unittest.TestCase,
        subtest: unittest.TestCase,
        # Narrower than `_ExceptionInfo` and optional, because a passing
        # subtest is reported through the same call with no exception.
        outcome: _SubTestInfo,
    ) -> None:
        # `TestResult` records a failed subtest directly, without passing it
        # through `addFailure`, so the first one is retained here too. Without
        # this the case still refuses the tier, but under the unexpected-
        # success message rather than the exception that actually raised.
        super().addSubTest(test, subtest, outcome)
        if outcome is not None and self.raised is None:
            self.raised = outcome[1]


class _AddedCaseItem(pytest.Item):
    """Run one case a module's `load_tests` added that pytest cannot collect.

    A `FunctionTestCase`, a case built inside `load_tests`, or one imported
    from a namespace pytest never sees has no node for pytest to collect, so
    the cohort would otherwise run everything else in the module and report a
    count that silently omits it. This item is the node the session needs to
    schedule that case and report its phases like any other.

    The case runs inside a one-case `unittest.TestSuite` rather than being
    called directly. `TestCase.__call__` runs the case body and its instance
    fixtures; `setUpClass`, `tearDownClass`, and the module-level pair belong
    to `TestSuite`, which is what actually orders and brackets them. Calling
    the leaf directly would run a case whose class setup never happened --
    reporting a failure unittest does not report. Delegating keeps that
    orchestration where unittest implements it instead of restating it here.
    """

    def __init__(self, *, name: str, parent: pytest.Module, case: unittest.TestCase):
        super().__init__(name=name, parent=parent)
        self._case = case
        self._raised: BaseException | None = None
        self.unittest_identity = case.id()

    def runtest(self) -> None:
        result = _AddedCaseResult()
        unittest.TestSuite((self._case,)).run(result)
        # A case can skip and still raise, from a cleanup or a `tearDown` that
        # runs anyway: unittest records one skip *and* one error and refuses
        # the run. Reporting only the skip here would let the tier pass, and
        # reporting only the error would lose a skip unittest counted, so the
        # error is held for the teardown phase -- the phase unittest raised it
        # in -- and both reach the accounting.
        self._raised = result.raised
        if result.skip_reason is not None:
            pytest.skip(result.skip_reason)
        if result.raised is not None:
            self._raised = None
            raise result.raised
        if not result.wasSuccessful():
            # Everything `wasSuccessful` refuses beyond the two branches above
            # is an unexpected success, which pytest reports through `xfail`
            # marks an added case has no node to carry. Refusing the tier is
            # the accounting that matters; the field it lands in is not.
            raise AssertionError("added_case_unexpected_success")

    def teardown(self) -> None:
        raised, self._raised = self._raised, None
        if raised is not None:
            raise raised

    def reportinfo(self) -> tuple[Path, None, str]:
        return self.path, None, self.unittest_identity


class _CohortRefusal(RuntimeError):
    """A refusal raised from a collection hook, carrying its own reason.

    pytest owns the exception: a hook that raises leaves the engine reporting
    an internal error and an exit code that says only "not OK", which the
    caller would otherwise flatten onto whichever generic reason it checks
    first. The reason is recorded on the session so the caller can state the
    one that actually happened.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _load_tests_reconciliation(
    modules: list[pytest.Module],
    items: list[pytest.Item],
) -> tuple[list[pytest.Item], list[pytest.Item], dict[ModuleType, list[pytest.Item]]]:
    """Reconcile a `load_tests` module's cohort with unittest's own case set.

    pytest does not implement `load_tests`, so a module that uses it to select
    a subset of its own namespace would otherwise contribute every `TestCase`
    reachable from that namespace -- including imported bases, whose cases
    already belong to another tier. Ask unittest for the module's authoritative
    case set and drop anything outside it.

    The protocol adds as well as selects, and the authoritative set is the one
    unittest returns, not the intersection with what pytest happened to
    collect. A case the protocol constructed, or imported from somewhere
    pytest cannot reach, is admitted as an item of its own rather than
    silently dropped.

    The suite is a sequence, not a set: `load_tests` chooses the order its
    cases run in, and a case it lists twice runs twice. The reconciled module
    keeps that order, and a repeated identity is refused rather than silently
    collapsed -- the cohort cannot account for two cases under one name, which
    is the same reason the collision guard exists.
    """

    loader = unittest.TestLoader()
    collected: dict[ModuleType, dict[str, pytest.Item]] = {}
    for item in items:
        module = getattr(item, "module", None)
        if module is not None:
            collected.setdefault(module, {})[_case_identity(item)] = item
    excluded: list[pytest.Item] = []
    added: list[pytest.Item] = []
    ordered: dict[ModuleType, list[pytest.Item]] = {}
    for collector in modules:
        try:
            module = collector.obj
        except (pytest.skip.Exception, unittest.SkipTest):
            # A module that skipped at import reports a skipped collection
            # rather than a failed one, and pytest caches no object for it, so
            # asking again re-imports and re-raises. It contributed no case to
            # reconcile.
            #
            # Both exceptions are load-bearing: pytest's `Skipped` and
            # `unittest.SkipTest` are unrelated classes, neither a subclass of
            # the other, and a module written against `unittest` reaches for
            # the latter -- which is the whole point of a tier that runs an
            # unconverted file unchanged.
            continue
        if not hasattr(module, "load_tests"):
            continue
        cases = _leaf_tests(loader.loadTestsFromModule(module))
        if loader.errors:
            raise _CohortRefusal("test_cohort_import_failed")
        identities = [case.id() for case in cases]
        if len(set(identities)) != len(identities):
            # unittest runs two cases that share an `id()` -- two
            # `FunctionTestCase`s over one function, say -- and reports them
            # separately. The manifest is keyed by identity and cannot, so
            # admitting them would run one and account for one where unittest
            # ran two. The cohort refuses instead of silently halving itself.
            raise _CohortRefusal("test_cohort_identity_collision")
        admitted = frozenset(identities)
        already = collected.get(module, {})
        excluded.extend(
            item for identity, item in already.items() if identity not in admitted
        )
        sequence: list[pytest.Item] = []
        for identity, case in zip(identities, cases, strict=True):
            existing = already.get(identity)
            if existing is not None:
                sequence.append(existing)
                continue
            item = _AddedCaseItem.from_parent(collector, name=identity, case=case)
            added.append(item)
            sequence.append(item)
        ordered[module] = sequence
    return excluded, added, ordered


def _in_load_tests_order(
    items: list[pytest.Item],
    ordered: dict[ModuleType, list[pytest.Item]],
) -> list[pytest.Item]:
    """Rebuild the session's items with each reconciled module in suite order.

    `load_tests` chooses the order its cases run in, and unittest honours it.
    Appending the added cases after everything pytest collected would run a
    module's own cases first and the protocol's additions last, so a suite
    whose order carries meaning would execute in an order unittest never
    produces. Each reconciled module's block is replaced in place, which also
    keeps the modules themselves in collection order.
    """

    if not ordered:
        return items
    emitted: set[int] = set()
    rebuilt: list[pytest.Item] = []
    for item in items:
        module = getattr(item, "module", None)
        sequence = ordered.get(module) if module is not None else None
        if sequence is None:
            rebuilt.append(item)
            continue
        if id(module) in emitted:
            continue
        emitted.add(id(module))
        rebuilt.extend(sequence)
    # A module whose protocol added every case it owns collected nothing to
    # anchor its block to, so its sequence has not been emitted yet.
    for module, sequence in ordered.items():
        if id(module) not in emitted:
            emitted.add(id(module))
            rebuilt.extend(sequence)
    return rebuilt


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
        self._modules: list[pytest.Module] = []
        self._shared_setup_nodes: set[str] = set()
        self._decorator_skipped: set[str] = set()
        self._records: list[_PhaseRecord] = []
        self._assertions: dict[tuple[str, str], bool] = {}
        self.identifiers: tuple[str, ...] = ()
        self.import_failed = False
        self.identity_collision = False
        self.refused: str | None = None
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

    def pytest_collectstart(self, collector: pytest.Collector) -> None:
        # A module whose `load_tests` adds every case it owns contributes no
        # collected item to reach it through, so the modules are recorded as
        # they are collected rather than recovered from the items afterwards.
        if isinstance(collector, pytest.Module):
            self._modules.append(collector)

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(
        self,
        fixturedef: pytest.FixtureDef[object],
        request: pytest.FixtureRequest,
    ) -> object:
        """Record which node owns a shared fixture that failed or skipped.

        This is the only point that names the fixture behind a setup phase:
        the report carries the phase and the outcome, never the fixture. What
        is recorded is the *node the fixture belongs to* -- the class for a
        class-scoped fixture, the module for a module-scoped one -- because a
        broader-scoped fixture raises once and pytest replays the cached
        exception for every later case in its scope, so there is no per-item
        call to observe.

        Recording shared ownership positively is what keeps the class-level
        rule off setup phases no fixture is behind at all. pytest evaluates a
        `skip` mark during setup without running any fixture, so treating an
        unexplained setup skip as shared would charge a per-method
        `@pytest.mark.skip` to `setUpClass` and drop that case from the run
        count.
        """

        try:
            return (yield)
        except BaseException:
            if fixturedef.scope != "function":
                self._shared_setup_nodes.add(request.node.nodeid)
            raise

    def _shared_setup(self, nodeid: str) -> bool:
        """Whether a shared fixture owning this case failed or skipped.

        A class-scoped fixture is recorded against its class node and a
        module-scoped one against its module node, so ownership is a node-id
        prefix rather than an equality. A `__unittest_skip__` class is excluded
        because unittest attributes that skip to each method it did not run,
        not to the class.
        """

        if nodeid in self._decorator_skipped:
            return False
        return any(
            not owner or nodeid == owner or nodeid.startswith(f"{owner}::")
            for owner in self._shared_setup_nodes
        )

    def pytest_collection_modifyitems(
        self,
        config: pytest.Config,
        items: list[pytest.Item],
    ) -> None:
        if self.import_failed:
            # A module that failed to collect has no imported object to ask
            # about `load_tests`, and asking raises rather than answering. The
            # cohort is already refused on the collection failure alone, so
            # leaving the identities empty reports that and nothing else.
            return
        try:
            excluded, added, ordered = _load_tests_reconciliation(self._modules, items)
        except _CohortRefusal as refusal:
            self.refused = refusal.reason
            raise
        # Every other identity is built path-free from a module, class, and
        # function name. An added one is whatever `TestCase.id()` returned, so
        # a case reached through a path-named module or an overridden `id()`
        # would carry that text into the manifest digest and into the reported
        # failure identity. The cohort refuses rather than reporting it, and
        # the flag carries the reason past the engine's own exit code.
        if any(
            _SAFE_TEST_ID.fullmatch(item.unittest_identity) is None for item in added
        ):
            self.refused = "test_cohort_identity_unsafe"
            raise _CohortRefusal(self.refused)
        if excluded:
            dropped = {item.nodeid for item in excluded}
            items[:] = [item for item in items if item.nodeid not in dropped]
            config.hook.pytest_deselected(items=excluded)
        items[:] = _in_load_tests_order(items, ordered)
        self._identities = {item.nodeid: _case_identity(item) for item in items}
        # unittest never runs `setUpClass` for a class it was told to skip; it
        # records one skip against each method instead, so the class-level rule
        # must not claim those the way it claims a `SkipTest` raised inside a
        # `setUpClass` that did run. pytest reports both through the same
        # class-scoped fixture, so the class itself is the only thing that
        # separates them.
        self._decorator_skipped.update(
            item.nodeid
            for item in items
            if getattr(getattr(item, "cls", None), "__unittest_skip__", False)
        )
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
                shared=_shared_fixture_identity(
                    self._owners.get(report.nodeid),
                    phase,
                    self._shared_setup(report.nodeid),
                ),
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
            # A case whose setup ran started, whether that setup then passed,
            # skipped, or failed for that case alone -- exactly what unittest
            # counts for an instance `setUp` that raises. Shared setup is the
            # one that runs nothing: unittest reports no tests run for a
            # `setUpClass` that failed and for one that raised `SkipTest`.
            if record.phase == "setup" and (
                record.outcome == "passed" or record.shared is None
            ):
                self.tests_run += 1
            if record.outcome == "skipped":
                if not record.expected_failure:
                    # Only shared *setup* collapses onto one identity, and only
                    # because nothing ran. A teardown-phase skip follows a case
                    # that already ran, so it stays that case's own.
                    shared_skip = record.shared if record.phase == "setup" else None
                    self.skipped.add(shared_skip or record.identity)
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
    if session.refused is not None:
        raise RuntimeError(session.refused)
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
    if session.refused is not None:
        raise RuntimeError(session.refused)
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


def _exception_line_offset(encoded: bytes) -> int | None:
    """Byte offset of the last raised exception's own line, or `None`.

    A `--tb=native` report is a frame block introduced by
    `Traceback (most recent call last):`, then the exception line, then
    whatever the exception message itself carries. Every frame line is
    indented, so the exception line is the first unindented non-empty line
    after the block -- which is also what stops this at the identity rather
    than at an unindented line inside the message, such as Playwright's
    `Call log:`.

    The *last* block wins. A chained failure prints the original, then
    `During handling of the above exception, another exception occurred:`,
    then the exception that actually reached the runner. That final one is the
    identity; the earlier ones are context and are the first thing worth
    dropping.

    `None` means the detail is not a native traceback at all -- pytest reports
    a collection error or an internal failure as prose -- and the caller falls
    back to retaining the literal head.
    """

    block_end: int | None = None
    offset = 0
    for line in encoded.split(b"\n"):
        if line == _NATIVE_TRACEBACK_HEADER:
            block_end = offset + len(line) + 1
        offset += len(line) + 1
    if block_end is None:
        return None
    offset = block_end
    for line in encoded[block_end:].split(b"\n"):
        if line.strip() and not line[:1].isspace():
            return offset
        offset += len(line) + 1
    return None


def _retained_traceback(encoded: bytes) -> str:
    """Reduce an over-long traceback to two windows and declare what it drops.

    The first window is anchored on the exception line so the error identity
    survives; it falls back to the literal head when there is no frame block
    to anchor on, and when the exception line already sits inside the tail,
    where anchoring would only narrow what is kept.

    The windows never overlap and never run backwards: the tail is clamped to
    start no earlier than the first window ends. A detail only slightly over
    budget therefore yields windows that meet, and two windows that meet are
    merged into one before anything is emitted -- otherwise the separator
    would fabricate a line break in the middle of bytes that are contiguous in
    the report, which is the same class of lie as a wrong elision count. Each
    declared count is the length of the gap it stands in, so the arithmetic
    stays true whichever way the windows were clamped.
    """

    total = len(encoded)
    anchor = _exception_line_offset(encoded)
    head_start = 0
    if anchor is not None and anchor < total - _TEST_TRACEBACK_TAIL_BYTES:
        head_start = anchor
    head_end = min(head_start + _TEST_TRACEBACK_HEAD_BYTES, total)
    tail_start = max(
        total - (_MAX_TEST_TRACEBACK_BYTES - (head_end - head_start)), head_end
    )

    regions: list[list[int]] = []
    for start, end in ((head_start, head_end), (tail_start, total)):
        if end <= start:
            continue
        if regions and regions[-1][1] == start:
            regions[-1][1] = end
        else:
            regions.append([start, end])

    pieces: list[str] = []
    cursor = 0
    for start, end in regions:
        if start > cursor:
            pieces.append(f"[elided {start - cursor} bytes]")
        pieces.append(encoded[start:end].decode("utf-8", "replace"))
        cursor = end
    return "\n".join(pieces)


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
        if len(encoded) > _MAX_TEST_TRACEBACK_BYTES:
            body = _retained_traceback(encoded)
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
