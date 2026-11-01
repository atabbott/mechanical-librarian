from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from scripts import mutation_kills_for_test as single
from scripts import run_mutation_audit as audit

SNAPSHOT_TIME = 2_000
BEFORE = 1_000
AFTER = 3_000


def build_tree(root: Path) -> Path:
    """A snapshot plus one test file, with the caller setting the two times.

    Every test starts from the same tree so the verdict each asserts is caused
    by what it sets and by nothing else in the fixture.
    """

    (root / "mutants").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    snapshot = root / "mutants" / "mutmut-stats.json"
    snapshot.write_text("{}", encoding="utf-8")
    os.utime(snapshot, (SNAPSHOT_TIME, SNAPSHOT_TIME))
    return root / "tests" / "unit" / "test_subject.py"


def test_a_test_written_after_the_snapshot_is_reported(tmp_path: Path) -> None:
    subject = build_tree(tmp_path)
    subject.write_text("", encoding="utf-8")
    os.utime(subject, (AFTER, AFTER))

    assert audit.stale_test_files(tmp_path) == [subject]


def test_a_snapshot_newer_than_every_test_is_clean(tmp_path: Path) -> None:
    subject = build_tree(tmp_path)
    subject.write_text("", encoding="utf-8")
    os.utime(subject, (BEFORE, BEFORE))

    assert audit.stale_test_files(tmp_path) == []


def test_a_snapshot_that_reaches_nothing_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A map with no reaching test measured every mutant as reached by none.

    The tree is otherwise clean -- the snapshot is present and newer than the
    only test file -- so the refusal can be caused by nothing but the empty
    map.
    """

    subject = build_tree(tmp_path)
    subject.write_text("", encoding="utf-8")
    os.utime(subject, (BEFORE, BEFORE))
    snapshot = tmp_path / "mutants" / "mutmut-stats.json"
    snapshot.write_text(
        json.dumps({"tests_by_mangled_function_name": {}, "duration_by_test": {}}),
        encoding="utf-8",
    )
    os.utime(snapshot, (SNAPSHOT_TIME, SNAPSHOT_TIME))
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_mutation_audit.py",
            "--root",
            str(tmp_path),
            "--out",
            str(tmp_path / "verdicts.jsonl"),
        ],
    )

    assert audit.main() == 2
    assert not (tmp_path / "verdicts.jsonl").exists()


@pytest.mark.parametrize("consumer", ["audit", "single"])
def test_consumers_dispatch_markerless_package_mutants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, consumer: str
) -> None:
    subject = build_tree(tmp_path)
    subject.write_text("def test_subject(): pass\n", encoding="utf-8")
    os.utime(subject, (BEFORE, BEFORE))
    copied_test = tmp_path / "mutants" / subject.relative_to(tmp_path)
    copied_test.parent.mkdir(parents=True)
    copied_test.write_bytes(subject.read_bytes())
    node = "tests/unit/test_subject.py::test_subject"
    mutant = "subject.x_subject__mutmut_1"
    package = tmp_path / "mutants" / "src" / "subject" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("def x_subject__mutmut_1(): return False\n", encoding="utf-8")
    snapshot = tmp_path / "mutants" / "mutmut-stats.json"
    snapshot.write_text(
        json.dumps({"tests_by_mangled_function_name": {"subject.x_subject": [node]}}),
        encoding="utf-8",
    )
    os.utime(snapshot, (SNAPSHOT_TIME, SNAPSHOT_TIME))
    dispatched: list[tuple[str, str]] = []

    def capture(
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        capture_output: bool,
        text: bool,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        assert command[1:3] == ["-m", "pytest"]
        assert cwd == tmp_path / "mutants"
        assert env["PYTHONPATH"] == str(tmp_path / "mutants" / "src")
        dispatched.append((env["MUTANT_UNDER_TEST"], command[-1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", capture)
    if consumer == "audit":
        monkeypatch.setattr(
            "sys.argv",
            ["audit", "--root", str(tmp_path), "--out", str(tmp_path / "out.jsonl")],
        )
        assert audit.main() == 0
    else:
        monkeypatch.setattr(single, "MUTANTS", tmp_path / "mutants")
        monkeypatch.setattr("sys.argv", ["single", node])
        assert single.main() == 0

    assert dispatched == [(mutant, node)]


_SCANNED = [
    "x_f__mutmut_1",
    "x_f__mutmut_2",
    "x_fg__mutmut_1",
    "xǁCǁm__mutmut_1",
    "xǁCǁn__mutmut_1",
    "xǁCDǁm__mutmut_1",
]


@pytest.mark.parametrize(
    ("names", "selected", "unmatched"),
    [
        pytest.param(
            {"f"}, ["x_f__mutmut_1", "x_f__mutmut_2"], set(), id="function-not-prefix"
        ),
        pytest.param({"C.m"}, ["xǁCǁm__mutmut_1"], set(), id="one-method"),
        pytest.param(
            {"C"}, ["xǁCǁm__mutmut_1", "xǁCǁn__mutmut_1"], set(), id="class-not-prefix"
        ),
        pytest.param({"fg", "nope"}, ["x_fg__mutmut_1"], {"nope"}, id="unmatched"),
        pytest.param({"C.x"}, [], {"C.x"}, id="unknown-method"),
    ],
)
def test_functions_select_exact_qualnames(
    names: set[str], selected: list[str], unmatched: set[str]
) -> None:
    assert audit.select_functions(_SCANNED, names) == (selected, unmatched)


def _functions_tree(root: Path, survivors: Path) -> None:
    subject = build_tree(root)
    subject.write_text("", encoding="utf-8")
    os.utime(subject, (BEFORE, BEFORE))
    package = root / "mutants" / "src" / "subject" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text(
        "".join(f"def {name}(): pass\n" for name in _SCANNED), encoding="utf-8"
    )
    snapshot = root / "mutants" / "mutmut-stats.json"
    snapshot.write_text(
        json.dumps({"tests_by_mangled_function_name": {"subject.x_f": ["t"]}}),
        encoding="utf-8",
    )
    os.utime(snapshot, (SNAPSHOT_TIME, SNAPSHOT_TIME))
    # An earlier pass whose only survivor is a mutant of `f`.
    survivors.write_text(
        json.dumps({"mutant": "subject.x_f__mutmut_1", "status": "survived"}) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("functions", "code", "printed"),
    [
        # `C` has no survivor in the sample, but it is a real name: checked
        # against every scanned mutant, it is not refused.
        pytest.param("f,C", 0, {"mutants": 1, "functions": {"f": 1}}, id="sampled"),
        pytest.param("f,nope", 2, None, id="misspelt"),
    ],
)
def test_functions_check_names_against_every_scanned_mutant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    functions: str,
    code: int,
    printed: dict[str, object] | None,
) -> None:
    survivors = tmp_path / "pass1.jsonl"
    _functions_tree(tmp_path, survivors)
    monkeypatch.setattr(
        "sys.argv",
        [
            "audit",
            "--root",
            str(tmp_path),
            "--out",
            str(tmp_path / "out.jsonl"),
            "--survivors-from",
            str(survivors),
            "--functions",
            functions,
            "--dry-run",
        ],
    )

    assert audit.main() == code
    if printed is not None:
        assert json.loads(capsys.readouterr().out) == printed
    assert not (tmp_path / "out.jsonl").exists()
