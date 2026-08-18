"""Generate the mutant tree and the per-function test selection mutmut needs.

`mutmut run` does this itself and then executes the mutants, but its execution
loop forks a child per mutant and that child dies with `SIGSEGV` on darwin --
mutmut reports every mutant as `segfault` and no verdict is produced. This runs
only the two phases that do work, so `scripts/run_mutation_audit.py` can
execute the mutants in subprocesses instead of forks.

Two departures from `mutmut run`, both deliberate:

* The whole package is copied to `mutants/src` before mutmut copies the audited
  modules over it. `[tool.mutmut] paths_to_mutate` names only the modules under
  audit, so mutmut alone would leave `mutants/src` holding those files and
  nothing else, and `mechanical_librarian` would not import at all. Excluding
  the rest through `do_not_mutate` instead does not work: those patterns are
  matched with `fnmatch`, whose `*` crosses a path separator, so a pattern
  naming one directory silently excludes every module under every sibling.
* Stats collection runs the cohort without `-x`. mutmut stops at the first
  failure and writes nothing, so one environment-dependent failure costs the
  whole collection.

    uv run python scripts/generate_mutants.py

Writes `mutants/` and `mutants/mutmut-stats.json`. Both are ignored by git.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Three modules qualify the build, the installed console script, or the
# source-only workbench against the repository root they run from. Inside
# `mutants/` that root is a copy, so they fail for the copy rather than for any
# mutant, and none of them can reach an audited module.
EXCLUDED = (
    "tests/unit/test_exact_artifact_qualification.py",
    "tests/unit/test_runtime_workbench.py",
    "tests/integration/test_continuous_review_scale.py",
)
COHORT = ("tests/unit", "tests/integration")


def generate(root: Path) -> None:
    import shutil

    os.environ["MUTANT_UNDER_TEST"] = "mutant_generation"
    from mutmut.__main__ import (
        copy_also_copy_files,
        copy_src_dir,
        create_mutants,
        ensure_config_loaded,
    )

    mutants = root / "mutants"
    shutil.rmtree(mutants, ignore_errors=True)
    # `PYTHONPATH` already names `mutants/src`, and importlib caches a path
    # entry that does not exist when the interpreter starts. Creating it before
    # anything imports through it is what keeps the mutated package importable.
    # Creating it is not sufficient on its own: a path entry that was missing
    # at start-up is cached as `None`, and `None` is never revalidated by
    # directory mtime the way a real finder is, so the cache has to be dropped
    # explicitly. Without this, the first run in a tree with no `mutants/`
    # imports the unmutated package throughout stats collection and records an
    # empty map -- which `run_mutation_audit.py` then reports as every mutant
    # reached by no test.
    (mutants / "src").mkdir(parents=True)
    importlib.invalidate_caches()
    shutil.copytree(root / "src", mutants / "src", dirs_exist_ok=True)

    ensure_config_loaded()
    copy_src_dir()
    create_mutants(4)
    copy_also_copy_files()


def collect_stats(root: Path) -> int:
    """Record which tests reach which mutated function.

    This is mutmut's own stats phase -- the same `MUTANT_UNDER_TEST=stats`
    trampoline mode and the same collector -- with the cohort run to completion
    rather than under `-x`.
    """

    os.environ["MUTANT_UNDER_TEST"] = "stats"
    mutants = root / "mutants"
    sys.path.insert(0, str(mutants / "src"))
    os.chdir(mutants)

    import mutmut
    import pytest
    from mutmut.__main__ import ensure_config_loaded, strip_prefix

    ensure_config_loaded()
    failures: list[str] = []

    class StatsCollector:
        def pytest_runtest_teardown(self, item, nextitem):
            for function in mutmut._stats:
                mutmut.tests_by_mangled_function_name[function].add(
                    strip_prefix(item._nodeid, prefix="mutants/")
                )
            mutmut._stats.clear()

        def pytest_runtest_logreport(self, report):
            if report.failed:
                failures.append(report.nodeid)

    code = pytest.main(
        [
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
            "--rootdir=.",
            *COHORT,
            *(f"--ignore={name}" for name in EXCLUDED),
        ],
        plugins=[StatsCollector()],
    )
    (mutants / "mutmut-stats.json").write_text(
        json.dumps(
            {
                "tests_by_mangled_function_name": {
                    key: sorted(value)
                    for key, value in mutmut.tests_by_mangled_function_name.items()
                },
                "duration_by_test": dict(mutmut.duration_by_test),
                "stats_time": 0.0,
            }
        )
    )
    reached = len(mutmut.tests_by_mangled_function_name)
    print(
        f"pytest exit {int(code)}; failures {len(set(failures))}; functions {reached}"
    )
    for name in sorted(set(failures)):
        print(f"  failed during stats collection: {name}")
    if not reached:
        # The cohort ran and reached nothing, which no arrangement of these
        # tests can mean: it means the trampoline never executed, so the tests
        # imported the unmutated package. Every mutant would then be recorded
        # as reached by no test -- a total failure that reads as a clean
        # result, which is the one outcome this audit must never produce.
        print(
            "no test reached any mutated function, so the mutated package was "
            "not the one under test; the snapshot is not usable",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    if os.environ.get("PYTHONPATH", "") != str(ROOT / "mutants" / "src"):
        print(
            "PYTHONPATH must name mutants/src, or the tests import the "
            "unmutated package and every mutant survives:\n"
            f"  PYTHONPATH={ROOT / 'mutants' / 'src'} "
            "uv run python scripts/generate_mutants.py",
            file=sys.stderr,
        )
        return 2
    os.chdir(ROOT)
    generate(ROOT)
    return collect_stats(ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
