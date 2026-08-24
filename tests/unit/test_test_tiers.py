from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from scripts.run_test_tier import (
    _MAX_IDENTITY_CHARACTERS,
    _MAX_TEST_TRACEBACK_BYTES,
    _MAX_TRACEBACK_STREAM_BYTES,
    _PYTEST_ARGUMENTS,
    _REJECTED_PYTEST_ENVIRONMENT,
    _SAFE_TEST_ID,
    _STALL_DIAGNOSTIC_SECONDS,
    _TEST_TRACEBACK_HEAD_BYTES,
    COHORTS,
    _CohortSession,
    _retained_traceback,
    cohort_manifest,
    cohort_targets,
    collect_cohort,
    main,
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


def _synthetic_cohort(
    directory: Path,
    count: int,
    detail: str,
    identifier_padding: int = 0,
) -> tuple[str, ...]:
    """Write a module of `count` independently failing tests and name its path.

    The engine collects and runs real files, so a synthetic cohort is a real
    module. `identifier_padding` lengthens each generated class name, so a
    cohort can carry long test identifiers with short failure bodies. The
    module name is unique per call because the engine imports it for real and
    a reused name would collide with an earlier cohort in the same process.
    """

    padding = "N" * identifier_padding
    lines = ["import unittest", ""]
    for index in range(count):
        lines.extend(
            (
                f"class SyntheticCohort{padding}{index:04d}(unittest.TestCase):",
                f"    def test_synthetic_{index:04d}(self) -> None:",
                '        print("suppressed-cohort-stdout")',
                f"        self.fail({detail!r})",
                "",
            )
        )
    module = directory / f"test_synthetic_{uuid4().hex}.py"
    module.write_text("\n".join(lines), encoding="utf-8")
    return (str(module),)


def _module(directory: Path, body: str) -> tuple[str, ...]:
    """Write one real test module and name its path as a cohort target.

    The name is unique per call because the engine imports it for real and a
    reused name would collide with an earlier cohort in the same process.
    """

    module = directory / f"test_synthetic_{uuid4().hex}.py"
    module.write_text(body, encoding="utf-8")
    return (str(module),)


@contextmanager
def _installed_pytest_plugin(directory: Path) -> Iterator[None]:
    """Install a real `pytest11` distribution that would hijack collection.

    Written as a distribution rather than a stub because auto-loading is what
    is under test: pytest discovers entry points through `importlib.metadata`,
    so only a discoverable distribution proves the runner keeps one out.
    """

    name = f"ml_collection_hijack_{uuid4().hex}"
    (directory / f"{name}.py").write_text(
        "def pytest_collection_modifyitems(items):\n    del items[1:]\n",
        encoding="utf-8",
    )
    metadata = directory / f"{name}-0.0.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.0.0\n", encoding="utf-8"
    )
    (metadata / "entry_points.txt").write_text(
        f"[pytest11]\n{name} = {name}\n", encoding="utf-8"
    )
    sys.path.insert(0, str(directory))
    importlib.invalidate_caches()
    try:
        yield
    finally:
        sys.path.remove(str(directory))
        sys.modules.pop(name, None)
        importlib.invalidate_caches()


def _cohort_summary(targets: tuple[str, ...]) -> tuple[bool, dict[str, Any]]:
    """Run `targets` as a tier and return its verdict beside its JSON summary."""

    summary = StringIO()
    narrative = StringIO()
    with (
        patch("scripts.run_test_tier.cohort_targets", return_value=targets),
        redirect_stderr(summary),
        redirect_stdout(narrative),
    ):
        passed = run_cohort(Path.cwd(), "fast")
    # A passing tier writes its summary to standard output; a failing one
    # writes it to standard error, with only tracebacks on standard output.
    stream = narrative if passed else summary
    decoded: dict[str, Any] = json.loads(stream.getvalue().strip().splitlines()[-1])
    return passed, decoded


def _run_failing_cohort(targets: tuple[str, ...]) -> tuple[dict[str, Any], str]:
    """Run `targets` as a tier and return its JSON summary and narrative stream."""

    summary = StringIO()
    narrative = StringIO()
    with (
        patch("scripts.run_test_tier.cohort_targets", return_value=targets),
        redirect_stderr(summary),
        redirect_stdout(narrative),
    ):
        passed = run_cohort(Path.cwd(), "fast")
    if passed:
        raise AssertionError("the synthetic cohort was expected to fail")
    decoded: dict[str, Any] = json.loads(summary.getvalue())
    return decoded, narrative.getvalue()


_AMBIENT_OPTION_MODULE = (
    "import unittest\n"
    "\n"
    "class AmbientOptionTests(unittest.TestCase):\n"
    "    def test_passing(self) -> None:\n"
    "        pass\n"
    "\n"
    "    def test_failing(self) -> None:\n"
    "        self.fail('this must be reported')\n"
)


@pytest.fixture(scope="module")
def ambient_option_targets() -> Iterator[tuple[str, ...]]:
    """Name a real cohort holding one passing case beside one failing case."""

    with tempfile.TemporaryDirectory() as directory:
        yield _module(Path(directory), _AMBIENT_OPTION_MODULE)


def test_stall_diagnostic_exposes_only_a_safe_test_identifier() -> None:
    session = _CohortSession()
    identifier = f"{__name__}.test_empty_cohort_fails_closed"
    session._identities = {
        "node::safe": identifier,
        "node::unsafe": "tests/unit/x.py::A::b",
    }
    session.pytest_runtest_logstart("node::safe", None)
    assert (
        session.stalled_test(time.monotonic() + _STALL_DIAGNOSTIC_SECONDS) == identifier
    )
    session.pytest_runtest_logfinish("node::safe", None)
    assert session.stalled_test(time.monotonic() + _STALL_DIAGNOSTIC_SECONDS) is None
    # A path-shaped identity is withheld rather than published.
    session.pytest_runtest_logstart("node::unsafe", None)
    assert session.stalled_test(time.monotonic() + _STALL_DIAGNOSTIC_SECONDS) is None


def test_source_tiers_are_disjoint_and_all_tiers_own_every_public_test_file() -> None:
    root = Path(__file__).resolve().parents[2]
    identifiers = {
        name: set(collect_cohort(root, name)) for name in ("fast", "browser", "slow")
    }
    assert identifiers["fast"]
    assert identifiers["browser"]
    assert identifiers["slow"]
    assert identifiers["fast"].isdisjoint(identifiers["browser"])
    assert identifiers["fast"].isdisjoint(identifiers["slow"])
    assert identifiers["browser"].isdisjoint(identifiers["slow"])

    owned_files = {
        path.resolve()
        for cohort in ("fast", "browser", "slow", "required-installed")
        for relative, pattern in COHORTS[cohort].discoveries
        for path in (root / relative).glob(pattern)
    }
    expected_files = {path.resolve() for path in (root / "tests").rglob("test_*.py")}
    assert owned_files == expected_files
    # The engine is handed exactly those files, never a directory it would
    # then sweep on its own collection rules.
    assert {
        Path(target).resolve()
        for cohort in ("fast", "browser", "slow", "required-installed")
        for target in cohort_targets(root, cohort)
    } == expected_files


def test_required_installed_adds_the_exact_wheel_scale_node_to_fast_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    fast = set(collect_cohort(root, "fast"))
    installed = set(collect_cohort(root, "required-installed"))
    assert installed
    scale = "test_continuous_review_scale.test_installed_pipeline_reconciles_then_restarts_completed_cohort"
    assert scale not in fast
    assert list(collect_cohort(root, "required-installed")).count(scale) == 1
    assert installed - {scale} < fast


def test_the_tier_inventory_lists_standalone_and_a_bare_invocation_refuses() -> None:
    # `run_test_tier.py --list` is the documented way to check the tier
    # inventory against the prose. It used to exit with a usage error, because
    # `tier` was a required positional, so the one command the documentation
    # offered for reconciling the tier set could not be run at all.
    inventory = StringIO()
    with redirect_stdout(inventory):
        assert main(["--list"]) == 0
    decoded = json.loads(inventory.getvalue())
    assert decoded == {
        "schema": "test-tier-inventory-v1",
        "tiers": [
            "browser",
            "complete",
            "complete-installed",
            "fast",
            "required-installed",
            "slow",
        ],
    }
    # Every listed name is one the runner accepts, and no accepted name is
    # missing: the inventory is the argument parser's own set, not a copy.
    assert set(decoded["tiers"]) == set(COHORTS) | {"complete-installed", "complete"}
    # Dropping the positional is a listing request only; a bare invocation
    # still refuses rather than defaulting to some tier.
    with pytest.raises(SystemExit):
        with redirect_stderr(StringIO()):
            main([])


def test_manifest_is_path_free_and_stable() -> None:
    root = Path(__file__).resolve().parents[2]
    first = cohort_manifest(root, "slow")
    second = cohort_manifest(root, "slow")
    assert first == second
    assert first["schema"] == "test-cohort-v1"
    assert str(root) not in str(first)


def test_representative_failure_reports_its_traceback() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary, narrative = _run_failing_cohort(
            _synthetic_cohort(Path(directory), 1, "explain-me")
        )
    assert set(summary) == _FAILURE_SUMMARY_KEYS
    assert summary["schema"] == "test-tier-result-v1"
    assert summary["reason"] == "test_tier_failed"
    assert summary["status"] == "failed"
    assert summary["tier"] == "fast"
    assert summary["failures"] == 1
    assert summary["errors"] == 0
    assert len(summary["failed_test_ids"]) == 1
    identifier = summary["failed_test_ids"][0]

    assert f"==== fast failure: {identifier} ====" in narrative
    assert "Traceback (most recent call last)" in narrative
    assert "explain-me" in narrative
    # The JSON summary stream carries no narrative text.
    assert "Traceback" not in json.dumps(summary)
    # Raw cohort output stays discarded on every stream.
    assert "suppressed-cohort-stdout" not in narrative
    assert "suppressed-cohort-stdout" not in json.dumps(summary)


def test_success_summary_carries_no_narrative_diagnostics() -> None:
    with tempfile.TemporaryDirectory() as directory:
        module = Path(directory) / f"test_synthetic_{uuid4().hex}.py"
        module.write_text(
            "import unittest\n"
            "\n"
            "class SyntheticPass(unittest.TestCase):\n"
            "    def test_pass(self) -> None:\n"
            '        print("suppressed-cohort-stdout")\n',
            encoding="utf-8",
        )
        narrative = StringIO()
        with (
            patch(
                "scripts.run_test_tier.cohort_targets",
                return_value=(str(module),),
            ),
            redirect_stdout(narrative),
        ):
            assert run_cohort(Path.cwd(), "fast")
    summary = json.loads(narrative.getvalue())
    assert summary == {
        "schema": "test-tier-result-v1",
        "skipped": 0,
        "status": "passed",
        "tests_run": 1,
        "tier": "fast",
    }


def test_failed_identifiers_are_reported_without_truncation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        summary, narrative = _run_failing_cohort(
            _synthetic_cohort(Path(directory), 40, "broad")
        )
    assert summary["tests_run"] == 40
    assert len(summary["failed_test_ids"]) == 40
    assert summary["failed_test_ids"] == sorted(summary["failed_test_ids"])
    for identifier in summary["failed_test_ids"]:
        assert f"==== fast failure: {identifier} ====" in narrative


def test_tracebacks_are_bounded_per_test_and_in_total() -> None:
    detail = "boundary-" + ("x" * 8_192) + "-tail"
    with tempfile.TemporaryDirectory() as directory:
        summary, narrative = _run_failing_cohort(
            _synthetic_cohort(Path(directory), 70, detail)
        )
    assert len(summary["failed_test_ids"]) == 70

    emitted = narrative.encode("utf-8")
    assert len(emitted) < 2 * _MAX_TRACEBACK_STREAM_BYTES
    assert "[elided " in narrative
    assert "==== fast tracebacks omitted: " in narrative
    # Both ends of the retained span survive and the elisions are declared in
    # place. The first window is anchored on the exception line, so the frame
    # block ahead of the identity is declared as a leading elision rather than
    # spending the window on it; the tail still ends at the assertion.
    assert "boundary-" in narrative
    assert "-tail" in narrative
    assert narrative.index("[elided ") < narrative.index("boundary-")
    assert narrative.index("boundary-") < narrative.rindex("-tail")

    blocks = narrative.split("==== fast failure: ")[1:]
    assert blocks
    for block in blocks:
        assert len(block.encode("utf-8")) <= _MAX_TEST_TRACEBACK_BYTES + 512


def _transport_failure_module(directory: Path) -> tuple[str, ...]:
    """A module whose real `--tb=native` report has the #730 shape.

    The shape is the whole point of the test, so it is built rather than
    assumed. A `--tb=native` report opens with the frame block -- pytest's own
    runner internals before the test's frames -- and only then reaches the
    exception line that names the error. A Playwright failure then follows
    that line with kilobytes of `Call log:` request headers, so the identity
    ends up in the *middle* of the text: measured at byte 4,738 of 15,862.

    Two details make this module reproduce that rather than something easier.
    The call chain is deep, so the frame block ahead of the identity is longer
    than the whole first retention window. And the message is a module
    constant rather than a literal at the raise site, so it is not echoed into
    a frame's source line -- an echo would put a copy of the identity early in
    the text and let a head-from-byte-zero policy pass a test it should fail,
    which is exactly how the first version of this test passed.
    """

    headers = "\n".join(
        f'  - sec-ch-ua-platform: "Linux" {index:04d}' for index in range(300)
    )
    call_log = f"Locator.click: Route.fetch: socket hang up\nCall log:\n{headers}"
    lines = [
        "class TransportError(Exception):",
        "    pass",
        "",
        f"_CALL_LOG = {call_log!r}",
        "",
        "def _frame_00() -> None:",
        "    raise TransportError(_CALL_LOG)",
        "",
    ]
    depth = 24
    for index in range(1, depth):
        lines.extend(
            (
                f"def _frame_{index:02d}() -> None:",
                f"    _frame_{index - 1:02d}()",
                "",
            )
        )
    lines.extend(
        (
            "def test_transport_failure() -> None:",
            f"    _frame_{depth - 1:02d}()",
            "",
        )
    )
    return _module(directory, "\n".join(lines))


def test_transport_failure_keeps_the_identity_buried_in_the_middle() -> None:
    """The #730 case, which neither end of the text on its own retains.

    Tail-only retention elided the identity as leading bytes and emitted a
    fragment ending mid-header-dump, which three sightings were recorded as
    "the tooling lost the traceback". Adding a literal head did not fix it:
    under `--tb=native` the first bytes are `_pytest/runner.py` frames and the
    identity is further in than the head window reaches.
    """

    with tempfile.TemporaryDirectory() as directory:
        summary, narrative = _run_failing_cohort(
            _transport_failure_module(Path(directory))
        )
    assert len(summary["failed_test_ids"]) == 1

    body = narrative.split("==== fast failure: ")[1].split(" ====\n", 1)[1]
    # The report really did bury the identity: more bytes were dropped ahead of
    # the first retained window than that window could have held, so no
    # head-from-byte-zero policy could have reached the identity. Without this
    # the test would pass on a report that never had the failing shape.
    leading = int(body.split("[elided ", 1)[1].split(" bytes]", 1)[0])
    assert body.startswith("[elided ")
    assert leading > _TEST_TRACEBACK_HEAD_BYTES

    # The identity, which both earlier policies lost.
    assert "Locator.click: Route.fetch: socket hang up" in body
    # The tail is still the end of the text, and still last.
    assert body.rstrip().endswith('  - sec-ch-ua-platform: "Linux" 0299')
    assert body.index("socket hang up") < body.rindex("[elided ")

    blocks = narrative.split("==== fast failure: ")[1:]
    assert len(blocks) == 1
    assert len(blocks[0].encode("utf-8")) <= _MAX_TEST_TRACEBACK_BYTES + 512


# A frame block long enough that the identity cannot sit in a literal head,
# built to the shape `--tb=native` emits: an introduced block of indented
# frames, then the exception line.
_FRAME_BLOCK = "\n".join(
    f'  File "/x/site-packages/_pytest/runner.py", line {index}, in call_{index}'
    f"\n    result = func()"
    for index in range(60)
)
_CALL_LOG = "\n".join(
    f'  - sec-ch-ua-platform: "Linux" {index:04d}' for index in range(300)
)


_ELISION = re.compile(r"(?:^|\n)\[elided (\d+) bytes\](?:\n|$)")


def _retained_regions(body: str, encoded: bytes) -> tuple[int, int]:
    """Check every retained window against `encoded` and total the accounting.

    Returns the retained and declared byte counts. Each window must be the
    exact slice of `encoded` that begins where the declared elisions before it
    say it does, and the windows and elisions together must account for every
    byte of the report. That is what makes a declared count a fact about the
    report rather than a number, and it is what catches a window emitted
    twice, a gap counted from stale arithmetic, or a separator inserted
    between bytes that are contiguous in the report.

    The elision markers are split out with their surrounding framing newlines,
    rather than walked line by line, because a window is cut at a byte offset
    and so both begins and ends mid-line. Only this split can tell a newline
    the report contains from one the framing added.
    """

    windows = _ELISION.split(body)
    cursor = retained = declared = 0
    for index, piece in enumerate(windows):
        if index % 2:
            gap = int(piece)
            declared += gap
            cursor += gap
            continue
        window = piece.encode("utf-8")
        assert encoded[cursor : cursor + len(window)] == window, (
            f"window at {cursor} is not the report's own bytes"
        )
        cursor += len(window)
        retained += len(window)
    assert cursor == len(encoded), f"accounting ends at {cursor}, not {len(encoded)}"
    return retained, declared


def test_retention_anchors_on_the_last_exception_line() -> None:
    """A chained failure names the error that reached the runner, not the first."""

    encoded = (
        f"Traceback (most recent call last):\n{_FRAME_BLOCK}\nmod.First: WRONG-ONE\n"
        "\nDuring handling of the above exception, another exception occurred:\n\n"
        f"Traceback (most recent call last):\n{_FRAME_BLOCK}\n"
        f"mod.Second: RIGHT-ONE\nCall log:\n{_CALL_LOG}"
    ).encode("utf-8")
    body = _retained_traceback(encoded)

    assert "RIGHT-ONE" in body
    assert "WRONG-ONE" not in body
    retained, declared = _retained_regions(body, encoded)
    assert retained <= _MAX_TEST_TRACEBACK_BYTES
    assert retained + declared == len(encoded)


def test_retention_falls_back_to_the_literal_head_without_a_frame_block() -> None:
    """Not every reported detail is a traceback; a collection error is prose."""

    encoded = ("PROSE-HEAD " + "z" * 9_000 + " PROSE-TAIL").encode("utf-8")
    body = _retained_traceback(encoded)

    assert body.startswith("PROSE-HEAD")
    assert body.endswith("PROSE-TAIL")
    retained, declared = _retained_regions(body, encoded)
    assert retained <= _MAX_TEST_TRACEBACK_BYTES
    assert retained + declared == len(encoded)


def test_retention_keeps_the_literal_head_when_the_tail_already_holds_the_identity() -> (
    None
):
    """Anchoring a short message already inside the tail would only narrow it."""

    frames = "\n".join(
        f'  File "/x/site-packages/_pytest/runner.py", line {index}, in call_{index}'
        f"\n    result = func()"
        for index in range(300)
    )
    encoded = (
        f"Traceback (most recent call last):\n{frames}\nmod.Error: LATE-IDENTITY"
    ).encode("utf-8")
    body = _retained_traceback(encoded)

    assert body.startswith("Traceback (most recent call last):")
    assert "LATE-IDENTITY" in body
    retained, declared = _retained_regions(body, encoded)
    assert retained <= _MAX_TEST_TRACEBACK_BYTES
    assert retained + declared == len(encoded)


@pytest.mark.parametrize("excess", (1, 2, 17, 512, 1_023, 1_025, 3_071, 4_096))
def test_retention_declares_what_it_drops_at_every_window_boundary(
    excess: int,
) -> None:
    """Windows that meet must neither duplicate bytes nor misdeclare the gap.

    A detail only slightly over budget puts the anchored window's end against
    the tail's start. The two are merged rather than joined, because a
    separator between contiguous bytes would invent a line break that the
    report does not have.
    """

    frames = "\n".join(
        f'  File "/x.py", line {index}, in frame\n    inner()' for index in range(12)
    )
    encoded = (
        f"Traceback (most recent call last):\n{frames}\nmod.Error: IDENTITY-HERE\n"
        + "y" * (_MAX_TEST_TRACEBACK_BYTES + excess)
    ).encode("utf-8")
    body = _retained_traceback(encoded)

    assert body.count("IDENTITY-HERE") == 1
    assert "[elided -" not in body
    retained, declared = _retained_regions(body, encoded)
    assert retained <= _MAX_TEST_TRACEBACK_BYTES
    assert retained + declared == len(encoded)


def test_framing_bytes_are_charged_to_the_aggregate_bound() -> None:
    # Short bodies with long identifiers: the framing, not the traceback,
    # dominates the stream, so an uncharged header escapes every ceiling.
    with tempfile.TemporaryDirectory() as directory:
        summary, narrative = _run_failing_cohort(
            _synthetic_cohort(Path(directory), 800, "short", identifier_padding=400)
        )
    assert len(summary["failed_test_ids"]) == 800

    emitted = len(narrative.encode("utf-8"))
    assert emitted <= _MAX_TRACEBACK_STREAM_BYTES
    # The reported cohort genuinely exceeds the ceiling when uncharged.
    assert (
        sum(len(identifier) for identifier in summary["failed_test_ids"])
        > _MAX_TRACEBACK_STREAM_BYTES
    )
    # Truncation stays declared: the reserve always leaves room to say so.
    assert "==== fast tracebacks omitted: " in narrative
    assert narrative.endswith(" ====\n")


def test_empty_cohort_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "tests" / "slow").mkdir(parents=True)
        (root / "tests" / "__init__.py").touch()
        (root / "tests" / "slow" / "__init__.py").touch()
        assert cohort_targets(root, "slow") == ()
        with pytest.raises(RuntimeError):
            collect_cohort(root, "slow")
        with pytest.raises(RuntimeError):
            run_cohort(root, "slow")


@pytest.mark.parametrize("name", _REJECTED_PYTEST_ENVIRONMENT)
def test_ambient_pytest_options_cannot_narrow_a_tier(
    name: str, ambient_option_targets: tuple[str, ...]
) -> None:
    # `PYTEST_ADDOPTS='-k passing'` would otherwise reduce this cohort to
    # its one passing case and report the tier passed while a failing case
    # sat in it, unrun.
    with patch.dict(os.environ, {name: "-k test_passing"}):
        with pytest.raises(RuntimeError) as refusal:
            _cohort_summary(ambient_option_targets)
    assert str(refusal.value) == f"pytest_environment_override:{name}"
    # The value is never echoed: it is arbitrary and may carry
    # a private path.
    assert "test_passing" not in str(refusal.value)


def test_colour_only_pytest_options_stay_permitted(
    ambient_option_targets: tuple[str, ...],
) -> None:
    # Colour-only variables reach nothing the tier reports, so they stay
    # permitted rather than making a developer's shell fail the gate.
    with patch.dict(
        os.environ, {"PYTEST_THEME": "monokai", "PYTEST_THEME_MODE": "dark"}
    ):
        passed, summary = _cohort_summary(ambient_option_targets)
    assert not passed
    assert summary["tests_run"] == 2
    assert summary["failures"] == 1


def test_shared_class_setup_failure_is_one_error_that_runs_no_tests() -> None:
    body = (
        "import unittest\n"
        "\n"
        "class SharedSetupTests(unittest.TestCase):\n"
        "    @classmethod\n"
        "    def setUpClass(cls) -> None:\n"
        "        raise RuntimeError('shared setup exploded')\n"
        "\n"
        "    def test_first(self) -> None:\n"
        "        pass\n"
        "\n"
        "    def test_second(self) -> None:\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), body)
        passed, summary = _cohort_summary(targets)
    assert not passed
    # unittest reports no tests run and one class-level error. pytest emits
    # a failed setup report per collected method, so without class-level
    # accounting this reads as two run and two errors.
    assert summary["tests_run"] == 0
    assert summary["errors"] == 1
    assert summary["failures"] == 0
    assert len(summary["failed_test_ids"]) == 1
    assert summary["failed_test_ids"][0].endswith(".SharedSetupTests.setUpClass")


def test_instance_setup_failures_stay_one_error_per_case() -> None:
    # The sibling of the class-level rule: an instance `setUp` failure runs
    # inside the call phase and is still one error per case, so class-level
    # accounting must not collapse these two into one.
    body = (
        "import unittest\n"
        "\n"
        "class InstanceSetupTests(unittest.TestCase):\n"
        "    def setUp(self) -> None:\n"
        "        raise RuntimeError('instance setup exploded')\n"
        "\n"
        "    def test_first(self) -> None:\n"
        "        pass\n"
        "\n"
        "    def test_second(self) -> None:\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), body)
        passed, summary = _cohort_summary(targets)
    assert not passed
    assert summary["tests_run"] == 2
    assert summary["errors"] == 2
    assert len(summary["failed_test_ids"]) == 2


def test_unexpected_success_refuses_the_tier() -> None:
    # pytest returns success for a non-strict `xfail` that passes, so the
    # engine's exit code alone would report the tier passed.
    body = (
        "import pytest\n"
        "\n"
        "@pytest.mark.xfail(reason='expected to fail', strict=False)\n"
        "def test_unexpectedly_passes() -> None:\n"
        "    assert True\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), body)
        passed, summary = _cohort_summary(targets)
    assert not passed
    assert summary["unexpected_successes"] == 1
    assert summary["status"] == "failed"
    assert len(summary["failed_test_ids"]) == 1


def test_native_case_identities_are_dotted_and_stall_safe() -> None:
    # A native node id carries the file path and bracketed parameters,
    # neither of which may reach a manifest or the stall diagnostic.
    body = (
        "import pytest\n"
        "\n"
        "def test_plain() -> None:\n"
        "    pass\n"
        "\n"
        "@pytest.mark.parametrize('value', ['alpha', 'beta-two'])\n"
        "def test_parameterized(value: str) -> None:\n"
        "    assert value\n"
        "\n"
        "class TestNativeClass:\n"
        "    def test_method(self) -> None:\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), body)
        with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
            identifiers = collect_cohort(Path.cwd(), "fast")
    assert len(identifiers) == 4
    for identifier in identifiers:
        assert _SAFE_TEST_ID.fullmatch(identifier) is not None
        assert "/" not in identifier
        assert "::" not in identifier
    leaves = {identifier.rsplit(".", 2)[-2:][0] for identifier in identifiers}
    assert "test_parameterized" in leaves
    assert any(
        identifier.endswith("test_parameterized.beta_two") for identifier in identifiers
    )


def test_colliding_case_identities_fail_closed() -> None:
    # Reducing parameters to the safe alphabet can map two distinct cases
    # onto one name. Silently shrinking the manifest is the one outcome the
    # cohort accounting cannot tolerate.
    body = (
        "import pytest\n"
        "\n"
        "@pytest.mark.parametrize('value', ['a-b', 'a_b'])\n"
        "def test_parameterized(value: str) -> None:\n"
        "    assert value\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), body)
        with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
            with pytest.raises(RuntimeError) as collision:
                collect_cohort(Path.cwd(), "fast")
    assert str(collision.value) == "test_cohort_identity_collision"


def test_installed_plugins_cannot_change_a_cohort() -> None:
    # Rejecting `PYTEST_PLUGINS` closed one door; auto-loading every
    # installed `pytest11` entry point left the next one open, with no
    # environment variable set and nothing to reject.
    body = (
        "import unittest\n"
        "\n"
        "class AutoloadTests(unittest.TestCase):\n"
        "    def test_first(self) -> None:\n"
        "        pass\n"
        "\n"
        "    def test_second(self) -> None:\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        targets = _module(root, body)
        with _installed_pytest_plugin(root):
            passed, summary = _cohort_summary(targets)
            assert passed
            assert summary["tests_run"] == 2

            # And the plugin genuinely would have taken a case away, so the
            # assertion above cannot pass because nothing was installed.
            permissive = tuple(
                argument
                for argument in _PYTEST_ARGUMENTS
                if argument != "--disable-plugin-autoload"
            )
            with patch("scripts.run_test_tier._PYTEST_ARGUMENTS", permissive):
                _, hijacked = _cohort_summary(targets)
    assert hijacked["tests_run"] == 1


def test_oversized_parameters_stay_bounded_and_distinct() -> None:
    # A long parameter set produced an identity past the safe bound, so the
    # stall diagnostic was withheld from exactly the parameterized style
    # the suite's conventions recommend.
    shared = "A" * 600
    body = (
        "import pytest\n"
        "\n"
        f"@pytest.mark.parametrize('value', [1, 2], ids=['{shared}one', '{shared}two'])\n"
        "def test_parameterized(value: int) -> None:\n"
        "    assert value\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), body)
        with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
            identifiers = collect_cohort(Path.cwd(), "fast")
    assert len(identifiers) == 2
    for identifier in identifiers:
        assert len(identifier) <= _MAX_IDENTITY_CHARACTERS
        assert _SAFE_TEST_ID.fullmatch(identifier) is not None
    # The two differ only past the truncation point, so a bound that merely
    # truncated would collapse them and refuse a legitimate cohort.
    assert len(set(identifiers)) == 2
