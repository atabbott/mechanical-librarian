from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
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
