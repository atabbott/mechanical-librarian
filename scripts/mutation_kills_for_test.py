"""Ask what one test kills on its own.

The batched driver runs a mutant's whole selected set under `-x`, so it can only
name the first test that failed. A test that would have killed the mutant on its
own gets no credit whenever an earlier one kills it first. This runs a single
named test against every mutant it reaches, so `0` here means the test kills
nothing rather than that it was never first.

    PYTHONPATH=$PWD/mutants/src uv run python \\
      scripts/mutation_kills_for_test.py \\
      'tests/unit/test_policy_preview_cli.py::test_prior_schema_is_rejected'

This is the only measurement that may be cited as evidence that a test kills
nothing. The `first_failure` field in a verdict stream may not.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MUTANTS = Path(__file__).resolve().parent.parent / "mutants"
MUTANT = re.compile(r"^[ \t]*def (x.+?__mutmut_\d+)\(", re.MULTILINE)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    node = sys.argv[1]
    stats = json.loads((MUTANTS / "mutmut-stats.json").read_text())
    reached = {
        key
        for key, tests in stats["tests_by_mangled_function_name"].items()
        if node in tests
    }
    work: list[str] = []
    for path in sorted((MUTANTS / "src").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        module = str(path.relative_to(MUTANTS / "src"))
        module = module.removesuffix("/__init__.py").removesuffix(".py")
        module = module.replace("/", ".")
        for name in MUTANT.findall(source):
            if f"{module}.{name.rsplit('__mutmut_', 1)[0]}" in reached:
                work.append(f"{module}.{name}")

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(MUTANTS / "src")
    killed: list[str] = []

    def run_one(name: str) -> None:
        child = dict(environment)
        child["MUTANT_UNDER_TEST"] = name
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-x",
                    "-q",
                    "--no-header",
                    "-p",
                    "no:cacheprovider",
                    "--rootdir=.",
                    node,
                ],
                cwd=MUTANTS,
                env=child,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return
        if completed.returncode in (1, 2, 3):
            killed.append(name)

    began = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run_one, work))
    print(
        f"{node}\n  reaches {len(reached)} functions / {len(work)} mutants; "
        f"kills {len(killed)} in {time.monotonic() - began:.0f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
