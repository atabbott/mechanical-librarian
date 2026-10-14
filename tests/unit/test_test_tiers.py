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
    _merge_worker_records,
    _retained_traceback,
    _verify_slow_partition,
    cohort_manifest,
    cohort_targets,
    collect_cohort,
    main,
    run_cohort,
    run_cohort_in_parallel,
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


def test_the_probe_cohort_is_exactly_the_audio_probe_slice_of_slow() -> None:
    """The hosted gate runs the probe cases and only the probe cases (#929).

    The file stays a member of `slow`, so the maintainer-local tier is still
    complete; the probe cohort exists so the `container-image` job can execute
    real ffprobe evidence without inheriting the scale and resource cases,
    which need an installed artifact and a longer budget than a gate job has.
    """

    root = Path(__file__).resolve().parents[2]
    probe = set(collect_cohort(root, "probe"))
    slow = set(collect_cohort(root, "slow"))
    assert probe
    assert probe < slow
    assert all(
        identifier.startswith("tests.slow.test_audio_probe.") for identifier in probe
    )
    assert cohort_targets(root, "probe") == (
        str(root / "tests" / "slow" / "test_audio_probe.py"),
    )


def test_required_installed_adds_explicit_preparation_nodes_to_fast() -> None:
    root = Path(__file__).resolve().parents[2]
    fast = set(collect_cohort(root, "fast"))
    installed_nodes = list(collect_cohort(root, "required-installed"))
    installed = set(installed_nodes)
    installed_only = {
        "test_continuous_review_scale.test_current_authority_and_predecessor_refusal_without_history",
        "test_continuous_review_scale.test_installed_owner_decision_activity_smoke",
        "test_continuous_review_scale.test_inventaire_failure_then_authenticated_reprepare_starts_new_run.retrieved",
        "test_continuous_review_scale.test_inventaire_failure_then_authenticated_reprepare_starts_new_run.absent",
        "test_continuous_review_scale.test_inventaire_failure_then_authenticated_reprepare_starts_new_run.unavailable",
        "test_continuous_review_scale.test_inventaire_failure_then_authenticated_reprepare_starts_new_run.timeout",
    }
    assert installed
    assert installed_only.isdisjoint(fast)
    assert all(installed_nodes.count(node) == 1 for node in installed_only)
    assert installed - installed_only < fast


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
            "probe",
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


@pytest.mark.parametrize(
    "declaration, expected_passed, tests_run, errors, skipped, failed_suffixes",
    [
        pytest.param(
            "class SharedSetupTests(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls) -> None:\n"
            "        raise RuntimeError('shared setup exploded')\n",
            False,
            0,
            1,
            0,
            ("SharedSetupTests.setUpClass",),
            id="shared-class-error",
        ),
        pytest.param(
            "class InstanceSetupTests(unittest.TestCase):\n"
            "    def setUp(self) -> None:\n"
            "        raise RuntimeError('instance setup exploded')\n",
            False,
            2,
            2,
            0,
            ("InstanceSetupTests.test_first", "InstanceSetupTests.test_second"),
            id="instance-errors",
        ),
        pytest.param(
            "@pytest.fixture(autouse=True)\n"
            "def exploding() -> None:\n"
            "    raise RuntimeError('function fixture exploded')\n"
            "\n"
            "class FixtureSetupTests(unittest.TestCase):\n",
            False,
            2,
            2,
            0,
            ("FixtureSetupTests.test_first", "FixtureSetupTests.test_second"),
            id="function-fixture-errors",
        ),
        pytest.param(
            "class SkippedSetupTests(unittest.TestCase):\n"
            "    @classmethod\n"
            "    def setUpClass(cls) -> None:\n"
            "        raise unittest.SkipTest('shared setup skipped')\n",
            True,
            0,
            0,
            1,
            (),
            id="shared-class-skip",
        ),
        pytest.param(
            "@unittest.skip('the whole class')\n"
            "class SkippedClassTests(unittest.TestCase):\n",
            True,
            2,
            0,
            2,
            (),
            id="class-skip",
        ),
    ],
)
def test_setup_outcomes_preserve_native_case_accounting(
    declaration: str,
    expected_passed: bool,
    tests_run: int,
    errors: int,
    skipped: int,
    failed_suffixes: tuple[str, ...],
) -> None:
    # Shared setup is one class-level outcome; instance/fixture errors and
    # a skipped class retain one outcome per native case.
    body = (
        "import pytest\nimport unittest\n\n" + declaration + "\n"
        "    def test_first(self) -> None:\n"
        "        pass\n\n"
        "    def test_second(self) -> None:\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        passed, summary = _cohort_summary(_module(Path(directory), body))
    assert passed is expected_passed
    assert summary["tests_run"] == tests_run
    if expected_passed:
        assert summary["skipped"] == skipped
    else:
        assert summary["errors"] == errors
        assert summary["failures"] == 0
        assert (
            tuple(
                ".".join(identifier.rsplit(".", 2)[-2:])
                for identifier in summary["failed_test_ids"]
            )
            == failed_suffixes
        )


_LOAD_TESTS_MODULE = (
    "import unittest\n"
    "\n"
    "class CollectedTests(unittest.TestCase):\n"
    "    def test_collected(self) -> None:\n"
    "        pass\n"
    "\n"
    "    def test_deselected(self) -> None:\n"
    "        raise AssertionError('outside the protocol case set')\n"
    "\n"
    "def _dynamic() -> None:\n"
    "    raise RuntimeError('the added case ran')\n"
    "\n"
    "def load_tests(loader, standard_tests, pattern):\n"
    "    suite = unittest.TestSuite()\n"
    "    suite.addTest(CollectedTests('test_collected'))\n"
    "    suite.addTest(unittest.FunctionTestCase(_dynamic))\n"
    "    return suite\n"
)


def test_load_tests_admits_the_cases_pytest_cannot_collect() -> None:
    # The protocol adds as well as selects. Intersecting pytest's collection
    # with it dropped a `FunctionTestCase` silently: unittest saw two cases
    # and the cohort reported one, with nothing to say a case was missing.
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), _LOAD_TESTS_MODULE)
        with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
            identifiers = collect_cohort(Path.cwd(), "fast")
        summary, _ = _run_failing_cohort(targets)
    assert "_dynamic" in identifiers
    assert any(
        identifier.endswith(".CollectedTests.test_collected")
        for identifier in identifiers
    )
    # The selection half of the protocol still holds: a case the module owns
    # but did not admit stays out, so its deliberate failure never runs.
    assert not any(identifier.endswith("test_deselected") for identifier in identifiers)
    assert len(identifiers) == 2
    assert summary["tests_run"] == 2
    assert summary["errors"] == 1
    assert summary["failed_test_ids"] == ["_dynamic"]


def test_an_added_case_cannot_carry_an_unsafe_identity() -> None:
    # An added case is the one identity the runner does not build itself, so
    # it is the one that can carry a path. Reaching a case through a module
    # loaded under its own file name puts that path in `TestCase.id()`, and
    # from there into the manifest digest and the reported failure identity.
    helper = (
        "import unittest\n"
        "\n"
        "class Helper(unittest.TestCase):\n"
        "    def test_helper(self) -> None:\n"
        "        pass\n"
    )
    body = (
        "import importlib.util\n"
        "import pathlib\n"
        "import unittest\n"
        "\n"
        "_source = pathlib.Path(__file__).parent / 'support_helper.py'\n"
        "_spec = importlib.util.spec_from_file_location(str(_source), _source)\n"
        "_helper = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_helper)\n"
        "\n"
        "class LocalTests(unittest.TestCase):\n"
        "    def test_local(self) -> None:\n"
        "        pass\n"
        "\n"
        "def load_tests(loader, standard_tests, pattern):\n"
        "    suite = unittest.TestSuite()\n"
        "    suite.addTest(LocalTests('test_local'))\n"
        "    suite.addTest(_helper.Helper('test_helper'))\n"
        "    return suite\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "support_helper.py").write_text(helper, encoding="utf-8")
        targets = _module(root, body)
        with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
            with pytest.raises(RuntimeError) as unsafe:
                collect_cohort(Path.cwd(), "fast")
    assert str(unsafe.value) == "test_cohort_identity_unsafe"


def test_an_added_case_reports_the_subtest_that_actually_failed() -> None:
    # `TestResult` records a failed subtest without routing it through
    # `addFailure`, so an added case whose only failure is a subtest would
    # otherwise be reported under the unexpected-success message instead of
    # the assertion that raised.
    #
    # The case has to live outside the module namespace to be an added one at
    # all: pytest collects every `TestCase` subclass it can see there, and
    # `getTestCaseNames` falls back to `runTest` when a class declares no
    # `test*` method, so a locally declared class would be collected normally
    # and never reach the added-case path.
    helper = (
        "import unittest\n"
        "\n"
        "class Subtests(unittest.TestCase):\n"
        "    def test_sub(self) -> None:\n"
        "        with self.subTest(index=1):\n"
        "            self.fail('the subtest is what failed')\n"
    )
    body = (
        "import importlib.util\n"
        "import pathlib\n"
        "import unittest\n"
        "\n"
        "_source = pathlib.Path(__file__).parent / 'support_subtests.py'\n"
        "_spec = importlib.util.spec_from_file_location('support_subtests', _source)\n"
        "_helper = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_helper)\n"
        "\n"
        "class LocalTests(unittest.TestCase):\n"
        "    def test_local(self) -> None:\n"
        "        pass\n"
        "\n"
        "def load_tests(loader, standard_tests, pattern):\n"
        "    suite = unittest.TestSuite()\n"
        "    suite.addTest(LocalTests('test_local'))\n"
        "    suite.addTest(_helper.Subtests('test_sub'))\n"
        "    return suite\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "support_subtests.py").write_text(helper, encoding="utf-8")
        targets = _module(root, body)
        summary, narrative = _run_failing_cohort(targets)
    # A clean module name keeps the imported case's identity safe, so it is
    # admitted where the path-named import above is refused.
    assert summary["failed_test_ids"] == ["support_subtests.Subtests.test_sub"]
    assert summary["failures"] == 1
    assert summary["errors"] == 0
    assert "the subtest is what failed" in narrative


@pytest.mark.parametrize(
    "body",
    (
        "import pytest\n\npytest.importorskip('a_module_that_does_not_exist')\n",
        "import pytest\n\npytest.skip('not here', allow_module_level=True)\n",
        # `unittest.SkipTest` is not a `pytest.skip.Exception`, and it is the
        # form an unconverted `unittest` module reaches for.
        "import unittest\n\nraise unittest.SkipTest('not here')\n",
    ),
    ids=("importorskip", "module_level_skip", "unittest_skiptest"),
)
def test_a_module_skipped_at_import_leaves_the_rest_of_the_cohort_alone(
    body: str,
) -> None:
    # A module-level skip reports a skipped collection, not a failed one, and
    # pytest caches no object for it -- so asking it for `load_tests`
    # re-imports and re-raises. Reconciliation must pass it by rather than
    # refuse the tier it never belonged to.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        targets = (*_module(root, _AMBIENT_OPTION_MODULE), *_module(root, body))
        with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
            identifiers = collect_cohort(Path.cwd(), "fast")
    assert [identifier.rsplit(".", 1)[-1] for identifier in identifiers] == [
        "test_failing",
        "test_passing",
    ]


def test_a_teardown_skip_stays_one_skip_per_case() -> None:
    # Only shared setup collapses onto one identity, and only because nothing
    # ran. A fixture that skips in its finaliser follows two cases that both
    # already ran, so it is two skips rather than one class-level skip.
    body = (
        "import pytest\n"
        "import unittest\n"
        "\n"
        "@pytest.fixture(autouse=True)\n"
        "def finalising() -> object:\n"
        "    yield\n"
        "    pytest.skip('skipped on the way out')\n"
        "\n"
        "class TeardownSkipTests(unittest.TestCase):\n"
        "    def test_first(self) -> None:\n"
        "        pass\n"
        "\n"
        "    def test_second(self) -> None:\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), body)
        passed, summary = _cohort_summary(targets)
    assert passed
    assert summary["tests_run"] == 2
    assert summary["skipped"] == 2


def test_an_added_case_runs_under_its_own_class_fixtures() -> None:
    # An added case is a `TestCase` leaf, and `setUpClass` belongs to the
    # `TestSuite` that brackets it, not to the case. Calling the leaf directly
    # ran a case whose class setup never happened and reported a failure
    # unittest does not report.
    helper = (
        "import unittest\n"
        "\n"
        "class NeedsClassSetup(unittest.TestCase):\n"
        "    ready = False\n"
        "\n"
        "    @classmethod\n"
        "    def setUpClass(cls) -> None:\n"
        "        cls.ready = True\n"
        "\n"
        "    def test_needs_setup(self) -> None:\n"
        "        assert self.ready, 'setUpClass never ran'\n"
    )
    body = (
        "import importlib.util\n"
        "import pathlib\n"
        "import unittest\n"
        "\n"
        "_source = pathlib.Path(__file__).parent / 'support_class_setup.py'\n"
        "_spec = importlib.util.spec_from_file_location('support_class_setup', _source)\n"
        "_helper = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_helper)\n"
        "\n"
        "class LocalTests(unittest.TestCase):\n"
        "    def test_local(self) -> None:\n"
        "        pass\n"
        "\n"
        "def load_tests(loader, standard_tests, pattern):\n"
        "    suite = unittest.TestSuite()\n"
        "    suite.addTest(LocalTests('test_local'))\n"
        "    suite.addTest(_helper.NeedsClassSetup('test_needs_setup'))\n"
        "    return suite\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "support_class_setup.py").write_text(helper, encoding="utf-8")
        passed, summary = _cohort_summary(_module(root, body))
    assert passed
    assert summary["tests_run"] == 2


def test_added_cases_run_in_the_order_load_tests_chose() -> None:
    # `load_tests` chooses the order its cases run in and unittest honours it.
    # Appending the added cases after everything pytest collected ran a
    # module's own cases first and the protocol's additions last.
    body = (
        "import unittest\n"
        "\n"
        "ORDER: list[str] = []\n"
        "\n"
        "def _added() -> None:\n"
        "    ORDER.append('added')\n"
        "\n"
        "class OrderedTests(unittest.TestCase):\n"
        "    def test_first(self) -> None:\n"
        "        ORDER.append('first')\n"
        "\n"
        "    def test_last(self) -> None:\n"
        "        assert ORDER == ['added', 'first'], ORDER\n"
        "\n"
        "def load_tests(loader, standard_tests, pattern):\n"
        "    suite = unittest.TestSuite()\n"
        "    suite.addTest(unittest.FunctionTestCase(_added))\n"
        "    suite.addTest(OrderedTests('test_first'))\n"
        "    suite.addTest(OrderedTests('test_last'))\n"
        "    return suite\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        passed, summary = _cohort_summary(_module(Path(directory), body))
    assert passed
    assert summary["tests_run"] == 3


def test_added_cases_that_share_an_identity_fail_closed() -> None:
    # unittest runs two cases that share an `id()` and reports them
    # separately. The manifest is keyed by identity and cannot, so admitting
    # them ran one and accounted for one where unittest ran two -- and the
    # second case here is the one that fails, so the tier passed a run
    # unittest refused.
    body = (
        "import unittest\n"
        "\n"
        "_calls: list[int] = []\n"
        "\n"
        "def _stateful() -> None:\n"
        "    _calls.append(1)\n"
        "    if len(_calls) > 1:\n"
        "        raise AssertionError('the second invocation must fail')\n"
        "\n"
        "class LocalTests(unittest.TestCase):\n"
        "    def test_local(self) -> None:\n"
        "        pass\n"
        "\n"
        "def load_tests(loader, standard_tests, pattern):\n"
        "    suite = unittest.TestSuite()\n"
        "    suite.addTest(LocalTests('test_local'))\n"
        "    suite.addTest(unittest.FunctionTestCase(_stateful))\n"
        "    suite.addTest(unittest.FunctionTestCase(_stateful))\n"
        "    return suite\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        targets = _module(Path(directory), body)
        with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
            with pytest.raises(RuntimeError) as refused:
                collect_cohort(Path.cwd(), "fast")
    assert str(refused.value) == "test_cohort_identity_collision"


def test_an_added_case_that_skips_still_reports_its_cleanup_error() -> None:
    # unittest records a case that skips and then raises in a cleanup both
    # ways and refuses the run. Reporting only the skip dropped the error and
    # let the tier pass. The error is held for the teardown phase -- the phase
    # unittest raised it in -- so both reach the accounting.
    helper = (
        "import unittest\n"
        "\n"
        "class SkipThenCleanupError(unittest.TestCase):\n"
        "    def setUp(self) -> None:\n"
        "        self.addCleanup(self._boom)\n"
        "\n"
        "    def _boom(self) -> None:\n"
        "        raise RuntimeError('the cleanup exploded')\n"
        "\n"
        "    def test_skips(self) -> None:\n"
        "        raise unittest.SkipTest('skipping')\n"
    )
    body = (
        "import importlib.util\n"
        "import pathlib\n"
        "import unittest\n"
        "\n"
        "_source = pathlib.Path(__file__).parent / 'support_skip_cleanup.py'\n"
        "_spec = importlib.util.spec_from_file_location(\n"
        "    'support_skip_cleanup', _source\n"
        ")\n"
        "_helper = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_helper)\n"
        "\n"
        "class LocalTests(unittest.TestCase):\n"
        "    def test_local(self) -> None:\n"
        "        pass\n"
        "\n"
        "def load_tests(loader, standard_tests, pattern):\n"
        "    suite = unittest.TestSuite()\n"
        "    suite.addTest(LocalTests('test_local'))\n"
        "    suite.addTest(_helper.SkipThenCleanupError('test_skips'))\n"
        "    return suite\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "support_skip_cleanup.py").write_text(helper, encoding="utf-8")
        passed, summary = _cohort_summary(_module(root, body))
    assert not passed
    assert summary["tests_run"] == 2
    assert summary["errors"] == 1
    # The failure summary carries no skip count by contract, so the skip half
    # of unittest's accounting is not observable here; the error is what the
    # tier was passing over.
    assert summary["failed_test_ids"] == [
        "support_skip_cleanup.SkipThenCleanupError.test_skips"
    ]


def test_a_marked_skip_stays_that_case_and_not_its_class() -> None:
    # pytest evaluates a `skip` mark during setup without running any fixture,
    # so shared ownership has to be recorded positively. Treating an
    # unexplained setup skip as shared charged a per-method skip to
    # `setUpClass` and dropped that case from the run count.
    body = (
        "import pytest\n"
        "import unittest\n"
        "\n"
        "class MarkedSkipTests(unittest.TestCase):\n"
        "    @pytest.mark.skip(reason='this one only')\n"
        "    def test_skipped(self) -> None:\n"
        "        pass\n"
        "\n"
        "    def test_run(self) -> None:\n"
        "        pass\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        passed, summary = _cohort_summary(_module(Path(directory), body))
    assert passed
    assert summary["tests_run"] == 2
    assert summary["skipped"] == 1


def test_a_module_that_cannot_be_imported_fails_closed() -> None:
    # Reconciling `load_tests` asks each collected module for its imported
    # object, and a module that failed to collect answers that with an
    # exception rather than a namespace. The cohort is refused on the
    # collection failure itself, which is the reason worth reporting.
    body = "import a_module_that_does_not_exist\n"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        targets = (*_module(root, _AMBIENT_OPTION_MODULE), *_module(root, body))
        with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
            with pytest.raises(RuntimeError) as refused:
                collect_cohort(Path.cwd(), "fast")
    assert str(refused.value) == "test_cohort_import_failed"


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


@pytest.mark.parametrize("phase", ["setup", "call", "teardown", "flock", "swallow"])
def test_fast_deadline_hard_exits_real_hangs(tmp_path: Path, phase: str) -> None:
    import subprocess

    hang = "time.sleep(60)"
    if phase == "flock":
        hang = "a=os.open('.', os.O_RDONLY); b=os.open('.', os.O_RDONLY); fcntl.flock(a, fcntl.LOCK_EX); fcntl.flock(b, fcntl.LOCK_EX)"
    if phase == "swallow":
        hang = "\n    while True:\n        try: time.sleep(60)\n        except BaseException: pass"
    if phase == "flock" and sys.platform != "linux":
        pytest.skip("Linux directory flock semantics")
    body = "import time, os, fcntl, pytest\n"
    if phase in ("setup", "teardown"):
        body += "@pytest.fixture(autouse=True)\ndef fixture():\n"
        body += (
            "    time.sleep(60)\n    yield\n"
            if phase == "setup"
            else "    yield\n    time.sleep(60)\n"
        )
        body += "def test_hang(): pass\n"
    else:
        body += "def test_hang():\n    " + hang + "\n"
    targets = _module(tmp_path, body)
    driver = (
        "from pathlib import Path\nfrom unittest.mock import patch\n"
        "from scripts.run_test_tier import run_cohort\n"
        "if __name__ == '__main__':\n"
        f"    with patch('scripts.run_test_tier.cohort_targets', return_value={targets!r}):\n"
        "        run_cohort(Path.cwd(), 'fast', 1)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", driver], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 1
    diagnostics = [json.loads(line) for line in result.stderr.splitlines()]
    assert len(diagnostics) == 1
    assert diagnostics[0]["schema"] == "test-tier-timeout-v1"
    assert diagnostics[0]["tier"] == "fast"
    assert diagnostics[0]["timeout_seconds"] == 1
    assert _SAFE_TEST_ID.fullmatch(diagnostics[0]["test_id"])
    assert diagnostics[0]["test_id"].endswith("test_hang")
    assert "test-tier-result-v1" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["fast", "--test-timeout-seconds", "0"],
        ["fast", "--test-timeout-seconds", "-1"],
        ["fast", "--test-timeout-seconds", "x"],
        ["fast", "--list", "--test-timeout-seconds", "1"],
        ["--list", "--test-timeout-seconds", "1"],
        ["complete", "--test-timeout-seconds", "1"],
        ["slow", "--test-timeout-seconds", "1"],
        ["browser", "--test-timeout-seconds", "1"],
        ["required-installed", "--test-timeout-seconds", "1"],
    ],
)
def test_deadline_option_refuses_nonexecution_and_invalid_values(
    args: list[str],
) -> None:
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_deadline_ignores_finished_state_and_resets_start() -> None:
    import threading

    from scripts.run_test_tier import _report_stalled_test

    session = _CohortSession()
    with patch("scripts.run_test_tier.time.monotonic", return_value=1):
        session.pytest_runtest_logstart("first.test", None)
        session.pytest_runtest_logfinish("first.test", None)
    stopped = threading.Event()
    with (
        patch.object(stopped, "wait", side_effect=[False, True]),
        patch("scripts.run_test_tier.os._exit") as terminate,
    ):
        _report_stalled_test(session, stopped, 1)
        terminate.assert_not_called()
    with patch("scripts.run_test_tier.time.monotonic", return_value=100):
        session.pytest_runtest_logstart("second.test", None)
        with (
            patch.object(stopped, "wait", side_effect=[False, True]),
            patch("scripts.run_test_tier.os._exit") as terminate,
        ):
            _report_stalled_test(session, stopped, 1)
            terminate.assert_not_called()


def test_deadline_exits_even_when_diagnostic_write_fails() -> None:
    import subprocess

    driver = """
import threading
from unittest.mock import patch
from scripts.run_test_tier import _CohortSession, _report_stalled_test
session = _CohortSession()
session.pytest_runtest_logstart('safe.test', None)
session._current_started_at = 0
with patch('scripts.run_test_tier.os.write', side_effect=OSError('write failed')):
    _report_stalled_test(session, threading.Event(), 1)
"""
    result = subprocess.run(
        [sys.executable, "-c", driver], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 1
    assert not result.stdout and not result.stderr


@pytest.mark.parametrize("deadline", [None, 1])
def test_passing_cohort_with_optional_deadline(
    tmp_path: Path, deadline: int | None
) -> None:
    targets = _module(tmp_path, "def test_pass(): pass\n")
    with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
        assert run_cohort(Path.cwd(), "fast", deadline)


@pytest.mark.parametrize("early_stall", [False, True])
def test_deadline_bypasses_blocked_python_stderr(early_stall: bool) -> None:
    import subprocess

    driver = """
import threading, time
from unittest.mock import patch
from scripts.run_test_tier import _CohortSession, _report_stalled_test
class Blocked:
    def write(self, value): time.sleep(60)
    def flush(self): time.sleep(60)
session = _CohortSession()
session.pytest_runtest_logstart('safe.test', None)
session._current_started_at = 0
with patch('scripts.run_test_tier.sys.__stderr__', Blocked()):
    _report_stalled_test(session, threading.Event(), 1)
"""
    if early_stall:
        driver = driver.replace(
            "session._current_started_at = 0",
            "session.stalled_test = lambda now: 'safe.test'",
        )
        driver = driver.replace("threading.Event(), 1", "threading.Event(), 3")
    result = subprocess.run(
        [sys.executable, "-c", driver], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 1
    diagnostics = [json.loads(line) for line in result.stderr.splitlines()]
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic == {
        "schema": "test-tier-timeout-v1",
        "tier": "fast",
        "test_id": "safe.test",
        "timeout_seconds": 3 if early_stall else 1,
    }
    assert not result.stdout


@pytest.mark.parametrize("deadline", [None, 180])
def test_early_stall_output_only_without_deadline(deadline: int | None) -> None:
    import threading

    from scripts.run_test_tier import _report_stalled_test

    session = _CohortSession()
    session.pytest_runtest_logstart("safe.test", None)
    stopped = threading.Event()
    output = StringIO()
    with (
        patch.object(stopped, "wait", side_effect=[False, True]),
        patch.object(session, "stalled_test", return_value="safe.test"),
        patch("scripts.run_test_tier.sys.__stderr__", output),
        patch("scripts.run_test_tier.os.write") as write,
    ):
        _report_stalled_test(session, stopped, deadline)
    write.assert_not_called()
    if deadline is None:
        assert json.loads(output.getvalue()) == {
            "schema": "test-tier-stall-v1",
            "status": "running",
            "test_id": "safe.test",
        }
    else:
        assert not output.getvalue()


@pytest.mark.parametrize("early_threshold", [False, True])
def test_deadline_exits_with_full_os_stderr_pipe(early_threshold: bool) -> None:
    import subprocess

    driver = """
import threading
from scripts.run_test_tier import _CohortSession, _report_stalled_test
session = _CohortSession()
session.pytest_runtest_logstart('safe.test', None)
session._current_started_at = 0
_report_stalled_test(session, threading.Event(), 1)
"""
    if early_threshold:
        driver = driver.replace(
            "session._current_started_at = 0",
            "session.stalled_test = lambda now: 'safe.test'",
        )
        driver = driver.replace("threading.Event(), 1", "threading.Event(), 3")
    reader, writer = os.pipe()
    child = None
    try:
        os.set_blocking(writer, False)
        filled = 0
        for size in (4096, 1):
            while True:
                try:
                    filled += os.write(writer, b"x" * size)
                except BlockingIOError:
                    break
        os.set_blocking(writer, True)
        child = subprocess.Popen(
            [sys.executable, "-c", driver], stdout=subprocess.PIPE, stderr=writer
        )
        os.close(writer)
        writer = -1
        # Do not drain even one byte until exit: draining would hide the defect.
        assert child.wait(timeout=6) == 1
        stdout, _ = child.communicate(timeout=1)
        assert not stdout
        drained = bytearray()
        while data := os.read(reader, 65536):
            drained.extend(data)
        assert drained == b"x" * filled
        assert b"test-tier-result-v1" not in drained
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=2)
        os.close(reader)
        if writer != -1:
            os.close(writer)


@pytest.mark.parametrize("failure", ["construct", "start", "join"])
def test_deadline_exits_if_terminal_writer_thread_fails(failure: str) -> None:
    import subprocess

    driver = """
import threading
from unittest.mock import patch
from scripts.run_test_tier import _CohortSession, _report_stalled_test
session = _CohortSession()
session.pytest_runtest_logstart('safe.test', None)
session._current_started_at = 0
with patch('scripts.run_test_tier.threading.Thread') as writer:
    FAILURE
    _report_stalled_test(session, threading.Event(), 1)
"""
    failure_code = (
        "writer.side_effect = RuntimeError('construct')"
        if failure == "construct"
        else f"writer.return_value.{failure}.side_effect = RuntimeError('{failure}')"
    )
    result = subprocess.run(
        [sys.executable, "-c", driver.replace("FAILURE", failure_code)],
        capture_output=True,
        text=True,
        timeout=6,
    )
    assert result.returncode == 1
    assert not result.stdout and not result.stderr


def test_slow_partition_covers_the_actual_full_inventory() -> None:
    root = Path(__file__).resolve().parents[2]
    inventories: dict[str, tuple[str, ...]] = {}

    def collect(root: Path, name: str) -> tuple[str, ...]:
        values = collect_cohort(root, name)
        inventories[name] = values
        return values

    with patch(
        "scripts.run_test_tier.collect_cohort", side_effect=collect
    ) as collector:
        _verify_slow_partition(root)
    assert collector.call_count == 3
    full = set(inventories["slow"])
    owner = set(inventories["slow-owner-browser"])
    remaining = set(inventories["slow-remaining"])
    assert len(full) == 31
    assert len(owner) == 1
    assert len(remaining) == 30
    assert owner.isdisjoint(remaining)
    assert owner | remaining == full
    assert all("test_owner_browser_responsiveness" in name for name in owner)


@pytest.mark.parametrize(
    "inventories",
    [
        (("a", "b"), ("a",), ("a", "b")),
        (("a", "b", "c"), ("a",), ("b",)),
        (("a", "b"), ("a",), ("b", "extra")),
        (("a",), (), ("a",)),
    ],
    ids=["overlap", "omission", "extra", "empty"],
)
def test_slow_partition_refuses_incomplete_or_overlapping_ids(inventories) -> None:
    with patch("scripts.run_test_tier.collect_cohort", side_effect=inventories):
        with pytest.raises(RuntimeError, match="^slow_partition_invalid$"):
            _verify_slow_partition(Path.cwd())


def test_slow_part_refuses_before_execution_when_partition_is_invalid() -> None:
    with (
        patch(
            "scripts.run_test_tier._verify_slow_partition",
            side_effect=RuntimeError("slow_partition_invalid"),
        ),
        patch("scripts.run_test_tier.run_cohort") as run,
    ):
        assert main(["slow", "--slow-part", "owner-browser"]) == 1
    run.assert_not_called()


@pytest.mark.parametrize("part", ["remaining", "owner-browser"])
def test_slow_part_validates_before_running_only_its_selected_subset(part: str) -> None:
    calls: list[str] = []

    def run(_root: Path, name: str) -> bool:
        calls.append(name)
        return True

    with (
        patch(
            "scripts.run_test_tier._verify_slow_partition",
            side_effect=lambda _root: calls.append("validated"),
        ),
        patch(
            "scripts.run_test_tier.run_cohort",
            side_effect=run,
        ),
    ):
        assert main(["slow", "--slow-part", part]) == 0
    assert calls == ["validated", f"slow-{part}"]


@pytest.mark.parametrize("fault", ["omission", "overlap"])
def test_slow_partition_refuses_real_collection_membership_fault(fault: str) -> None:
    root = Path(__file__).resolve().parents[2]
    full = cohort_targets(root, "slow")
    owner = tuple(
        path
        for path in full
        if Path(path).name == "test_owner_browser_responsiveness.py"
    )
    remaining = tuple(path for path in full if path not in owner)
    faulty = remaining[1:] if fault == "omission" else remaining + owner
    targets = {"slow": full, "slow-remaining": faulty, "slow-owner-browser": owner}
    with patch(
        "scripts.run_test_tier.cohort_targets",
        side_effect=lambda _root, name: targets[name],
    ):
        with pytest.raises(RuntimeError, match="^slow_partition_invalid$"):
            _verify_slow_partition(root)


_PARALLEL_MODULES = (
    "import unittest\n"
    "\n"
    "class First(unittest.TestCase):\n"
    "    def test_passing(self) -> None:\n"
    "        pass\n"
    "\n"
    "    def test_failing(self) -> None:\n"
    "        self.fail('first must be reported')\n",
    "import pytest\n"
    "\n"
    "@pytest.mark.parametrize('value', (1, 2, 3))\n"
    "def test_values(value: int) -> None:\n"
    "    assert value < 3\n"
    "\n"
    "@pytest.mark.skip(reason='skipped on purpose')\n"
    "def test_skipped() -> None:\n"
    "    pass\n",
    "def test_erroring() -> None:\n    raise ValueError('an error, not a failure')\n",
    "import unittest\n"
    "\n"
    "class Fourth(unittest.TestCase):\n"
    "    def test_passing(self) -> None:\n"
    "        pass\n",
)


def _tier_streams(run: Any) -> tuple[bool, dict[str, Any], str]:
    summary = StringIO()
    narrative = StringIO()
    with redirect_stderr(summary), redirect_stdout(narrative):
        passed = run()
    stream = narrative if passed else summary
    decoded: dict[str, Any] = json.loads(stream.getvalue().strip().splitlines()[-1])
    tracebacks = narrative.getvalue() if not passed else ""
    return passed, decoded, tracebacks


@pytest.mark.parametrize("tier", ("fast", "browser"))
def test_parallel_workers_report_exactly_what_a_serial_run_reports(
    tmp_path: Path, tier: str
) -> None:
    """Dividing a cohort between workers changes its wall time and nothing else.

    `fast` divides by file and `browser` by test function, so both unit kinds
    are proved against the serial verdict, counts, failed identities, skips
    and traceback stream of the same real cohort.
    """

    targets = tuple(
        target for body in _PARALLEL_MODULES for target in _module(tmp_path, body)
    )
    with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
        serial = _tier_streams(lambda: run_cohort(Path.cwd(), tier))
        parallel = _tier_streams(lambda: run_cohort_in_parallel(Path.cwd(), tier, 3))
    assert serial[0] is False
    assert serial[1]["tests_run"] == 8
    assert serial[1]["failures"] == 2
    assert serial[1]["errors"] == 1
    assert parallel == serial


def test_parallel_workers_pass_a_passing_cohort_with_its_skips(
    tmp_path: Path,
) -> None:
    targets = tuple(
        target
        for body in (_PARALLEL_MODULES[3], "def test_one() -> None:\n    pass\n")
        for target in _module(tmp_path, body)
    )
    with patch("scripts.run_test_tier.cohort_targets", return_value=targets):
        passed, summary, _ = _tier_streams(
            lambda: run_cohort_in_parallel(Path.cwd(), "fast", 2)
        )
    assert passed is True
    assert summary == {
        "schema": "test-tier-result-v1",
        "skipped": 0,
        "status": "passed",
        "tests_run": 2,
        "tier": "fast",
    }


def test_a_unit_that_cannot_import_refuses_the_parallel_tier(tmp_path: Path) -> None:
    targets = tuple(
        target
        for body in (_PARALLEL_MODULES[3], "import a_module_that_is_not_there\n")
        for target in _module(tmp_path, body)
    )
    with (
        patch("scripts.run_test_tier.cohort_targets", return_value=targets),
        pytest.raises(RuntimeError, match="^test_cohort_import_failed$"),
    ):
        run_cohort_in_parallel(Path.cwd(), "fast", 2)


def test_a_worker_that_leaves_no_record_refuses_the_parallel_tier(
    tmp_path: Path,
) -> None:
    # A worker that hard-exits -- the deadline's `os._exit`, a crash -- writes
    # no record, and the tier must not read its absence as an empty unit.
    targets = _module(tmp_path, "import os\nos._exit(3)\n")
    with (
        patch("scripts.run_test_tier.cohort_targets", return_value=targets),
        pytest.raises(RuntimeError, match="^test_tier_worker_exited$"),
    ):
        run_cohort_in_parallel(Path.cwd(), "fast", 2)


def _worker_record(*identifiers: str) -> dict[str, object]:
    return {
        "engine_ok": True,
        "errors": 0,
        "failed_test_ids": [],
        "failures": 0,
        "identifiers": list(identifiers),
        "schema": "test-tier-worker-v1",
        "skipped": [],
        "tests_run": len(identifiers),
        "tracebacks": [],
        "unexpected_successes": 0,
    }


def test_merged_units_refuse_duplicates_omissions_and_emptiness() -> None:
    complete = [_worker_record("a.one"), _worker_record("a.two")]
    assert _merge_worker_records(complete, ("a.one", "a.two")).tests_run == 2
    with pytest.raises(RuntimeError, match="^test_cohort_identity_collision$"):
        _merge_worker_records([_worker_record("a.one"), _worker_record("a.one")], None)
    with pytest.raises(RuntimeError, match="^test_cohort_partition_invalid$"):
        _merge_worker_records([_worker_record("a.one")], ("a.one", "a.two"))
    with pytest.raises(RuntimeError, match="^empty test cohort$"):
        _merge_worker_records([_worker_record()], None)
    # The first refusing unit names the reason, whatever later units found.
    with pytest.raises(RuntimeError, match="^test_cohort_import_failed$"):
        _merge_worker_records(
            [
                _worker_record("a.one"),
                {"reason": "test_cohort_import_failed"},
                {"reason": "test_tier_worker_exited"},
            ],
            None,
        )
    malformed = _worker_record("a.one")
    malformed["tests_run"] = True
    with pytest.raises(RuntimeError, match="^test_tier_worker_exited$"):
        _merge_worker_records([malformed], None)


@pytest.mark.parametrize(
    "args",
    (
        ["fast", "--workers", "1"],
        ["slow", "--workers", "4"],
        ["fast", "--workers", "4", "--list"],
        ["fast", "--worker-result", "record.json"],
        ["fast", "--worker-target", "tests/unit/test_package.py"],
        [
            "slow",
            "--worker-result",
            "record.json",
            "--worker-target",
            "tests/slow/test_audio_probe.py",
        ],
    ),
)
def test_workers_option_refuses_tiers_it_cannot_divide(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_the_gate_divides_the_fast_tier_into_four_workers() -> None:
    workflow = (
        Path(__file__).parents[2] / ".github/workflows/public-gate.yml"
    ).read_text(encoding="utf-8")
    assert "run: uv run python scripts/run_test_tier.py fast --workers 4\n" in workflow
    assert "run: uv run python scripts/run_test_tier.py browser\n" in workflow
