"""Drive mutmut-generated mutants through subprocess pytest runs.

mutmut 3 forks a child per mutant. On darwin that child dies with SIGSEGV
(mutmut reports every mutant as `segfault`), so the tool's own execution loop
cannot produce a verdict on this host. Everything else mutmut does still works:
`mutmut run` writes the trampolined mutant sources under `mutants/` and the
per-function test selection into `mutants/mutmut-stats.json`. This driver
reuses both and executes each mutant in a fresh subprocess instead of a fork.

Every verdict is appended to the output as one JSON line and flushed before the
next mutant starts, so an interrupted run leaves the mutants it already
executed rather than nothing. A run that finds the output already holding a
verdict for a mutant skips it, so an interrupted run resumes.

`--max-tests` truncates a mutant's reaching-test set. That is safe for a
`killed` verdict -- one test failed, and truncation cannot un-fail it -- but it
is not safe for `survived`, because the killer may be one of the tests the
truncation dropped. `--survivors-from` re-runs exactly the survivors of an
earlier pass with no cap, which is the only verdict this driver will call
unkilled.

`--functions` narrows a run to named functions: `f` selects module function
`f`, `C.m` selects method `m` of class `C`, and a bare `C` selects every method
of `C`. A name that matches no mutant the run scanned is refused, so a typo
cannot pass as a function with nothing to kill. `--dry-run` prints the
selection, before `--limit` and before skipping recorded verdicts, and runs
nothing. Names are not tied to a module: narrow with `--only` when two
modules define the same name.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MUTANT = re.compile(r"^[ \t]*def (x.+?__mutmut_\d+)\(", re.MULTILINE)


def module_dotted(path: Path, root: Path) -> str:
    relative = path.relative_to(root / "mutants" / "src")
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts.pop()
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def mangled(module: str, mutant: str) -> str:
    return f"{module}.{mutant.rsplit('__mutmut_', 1)[0]}"


def qualname(mutant: str) -> str:
    """The source qualname behind a mutmut 3 mutant name.

    mutmut names a module function's mutants `x_f__mutmut_N` and a method's
    `xǁCǁm__mutmut_N`.
    """

    base = mutant.rsplit("__mutmut_", 1)[0]
    if base.startswith("xǁ"):
        return ".".join(base[2:].split("ǁ"))
    return base.removeprefix("x_")


def select_functions(mutants: list[str], names: set[str]) -> tuple[list[str], set[str]]:
    """The mutants of the named functions, and the names that matched none."""

    selected: list[str] = []
    matched: set[str] = set()
    for mutant in mutants:
        name = qualname(mutant)
        # A method answers to its own qualname and to its bare class name.
        hits = {name, name.split(".", 1)[0]} & names
        if hits:
            selected.append(mutant)
            matched |= hits
    return selected, names - matched


def load_lines(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    loaded: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        loaded[record["mutant"]] = record
    return loaded


def stale_test_files(root: Path) -> list[Path]:
    """Return the test files written after the snapshot the driver will use.

    `mutants/tests` and `mutants/mutmut-stats.json` are snapshots taken by
    `scripts/generate_mutants.py`. Editing a test afterwards and re-running only
    this driver selects tests from the snapshot, so the new test never executes
    and the mutants it was written to kill are reported as surviving. That reads
    exactly like a test that earns nothing, which is the one conclusion this
    audit exists to draw, so it is refused rather than reported.
    """

    snapshot = (root / "mutants" / "mutmut-stats.json").stat().st_mtime
    return sorted(
        path
        for path in (root / "tests").rglob("*.py")
        if path.stat().st_mtime > snapshot
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--out", required=True, help="JSON-lines verdict stream")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-tests", type=int, default=40, help="0 means no cap")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--only", default="")
    parser.add_argument(
        "--functions",
        default="",
        help="comma-separated qualnames: f, C.m, or C for every method of C",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--survivors-from",
        default="",
        help="re-run only the survivors recorded in this stream, uncapped",
    )
    arguments = parser.parse_args()

    root = Path(arguments.root)
    mutants_root = root / "mutants"
    if not (mutants_root / "mutmut-stats.json").exists():
        print(
            "mutants/mutmut-stats.json is missing -- run scripts/generate_mutants.py",
            file=sys.stderr,
        )
        return 2
    stale = stale_test_files(root)
    if stale:
        print(
            f"{len(stale)} test file(s) were written after "
            f"mutants/mutmut-stats.json, so this run would measure the previous "
            f"suite. Re-run scripts/generate_mutants.py.",
            file=sys.stderr,
        )
        for path in stale[:5]:
            print(f"  {path.relative_to(root)}", file=sys.stderr)
        return 2
    stats = json.loads((mutants_root / "mutmut-stats.json").read_text())
    tests_by_function = stats["tests_by_mangled_function_name"]
    if not tests_by_function:
        # An empty map is not a suite that reaches nothing; it is a snapshot
        # taken while the tests imported the unmutated package, so the
        # trampoline never recorded a hit. Measured rather than refused, it
        # reports every mutant as reached by no test -- a total failure that
        # reads as a clean result.
        print(
            "mutants/mutmut-stats.json records no reaching tests, so it was "
            "taken against the unmutated package -- re-run "
            "scripts/generate_mutants.py",
            file=sys.stderr,
        )
        return 2

    wanted: set[str] | None = None
    if arguments.survivors_from:
        survivors = Path(arguments.survivors_from)
        if not survivors.exists():
            # `load_lines` treats a missing stream as empty, which would select
            # no mutants and exit zero having measured nothing.
            print(f"--survivors-from names no file: {survivors}", file=sys.stderr)
            return 2
        earlier = load_lines(survivors)
        wanted = {
            name
            for name, record in earlier.items()
            if record["status"] in ("survived", "timeout")
        }

    scanned: list[tuple[str, str]] = []
    for path in sorted((mutants_root / "src").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        module = module_dotted(path, root)
        if arguments.only and arguments.only not in module:
            continue
        scanned.extend((module, name) for name in MUTANT.findall(source))

    if arguments.functions:
        names = {n.strip() for n in arguments.functions.split(",") if n.strip()}
        # Names are checked against every scanned mutant, before
        # `--survivors-from` narrows the list: a sample holding no survivor of
        # a named function is not a misspelt name.
        chosen, unmatched = select_functions([name for _, name in scanned], names)
        if unmatched or not chosen:
            print(
                f"--functions names no scanned mutant: {sorted(unmatched) or names}",
                file=sys.stderr,
            )
            return 2
        keep = set(chosen)
        scanned = [(module, name) for module, name in scanned if name in keep]

    work: list[tuple[str, str, list[str]]] = []
    for module, name in scanned:
        full = f"{module}.{name}"
        if wanted is not None and full not in wanted:
            continue
        key = mangled(module, name)
        work.append((full, key, sorted(tests_by_function.get(key, ()))))

    if arguments.dry_run:
        functions: dict[str, int] = {}
        for full, _, _ in work:
            name = qualname(full.rsplit(".", 1)[1])
            functions[name] = functions.get(name, 0) + 1
        print(json.dumps({"mutants": len(work), "functions": functions}, indent=1))
        return 0

    if arguments.limit:
        work = work[: arguments.limit]

    out_path = Path(arguments.out)
    done = load_lines(out_path)
    remaining = [item for item in work if item[0] not in done]
    print(
        f"{len(work)} mutants in scope; {len(done)} already recorded; "
        f"{len(remaining)} to run",
        file=sys.stderr,
        flush=True,
    )

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(mutants_root / "src")
    environment.pop("PYTEST_ADDOPTS", None)

    handle = out_path.open("a", encoding="utf-8")
    write_lock = threading.Lock()
    started = time.monotonic()

    def record(payload: dict[str, object]) -> None:
        with write_lock:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def run_one(item: tuple[str, str, list[str]]) -> None:
        full, key, tests = item
        if not tests:
            record({"mutant": full, "function": key, "status": "no tests", "tests": 0})
            return
        selected = tests if arguments.max_tests == 0 else tests[: arguments.max_tests]
        child = dict(environment)
        child["MUTANT_UNDER_TEST"] = full
        begin = time.monotonic()
        stdout = ""
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
                    *selected,
                ],
                cwd=mutants_root,
                env=child,
                capture_output=True,
                text=True,
                timeout=arguments.timeout,
            )
            code = completed.returncode
            stdout = completed.stdout
        except subprocess.TimeoutExpired:
            code = -24
        if code == 0:
            status = "survived"
        elif code in (1, 2, 3):
            status = "killed"
        elif code == -24:
            status = "timeout"
        elif code == 5:
            status = "no tests"
        else:
            status = f"error:{code}"
        first_failure = ""
        if status == "killed":
            for line in stdout.splitlines():
                if line.startswith(("FAILED ", "ERROR ")):
                    first_failure = line.split(" ", 1)[1].split(" ")[0]
                    break
        record(
            {
                "mutant": full,
                "function": key,
                "status": status,
                "tests": len(selected),
                "capped": arguments.max_tests != 0 and len(tests) > len(selected),
                "seconds": round(time.monotonic() - begin, 2),
                # Under `-x` this names the test that failed first, which is not
                # the set of tests that would have killed the mutant. It is a
                # breadcrumb for reading a verdict, never evidence that any
                # other test kills nothing.
                "first_failure": first_failure,
            }
        )

    with ThreadPoolExecutor(max_workers=arguments.workers) as pool:
        for index, _ in enumerate(pool.map(run_one, remaining), start=1):
            if index % 50 == 0:
                rate = index / (time.monotonic() - started)
                print(
                    f"{index}/{len(remaining)}  {rate:.2f}/s  "
                    f"{(len(remaining) - index) / rate / 60:.0f} min left",
                    file=sys.stderr,
                    flush=True,
                )
    handle.close()

    counts: dict[str, int] = {}
    for value in load_lines(out_path).values():
        counts[str(value["status"])] = counts.get(str(value["status"]), 0) + 1
    print(json.dumps(counts, sort_keys=True))
    print(f"elapsed {time.monotonic() - started:.1f}s over {len(remaining)} mutants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
