from __future__ import annotations

import os
from pathlib import Path

from scripts import run_mutation_audit as audit

SNAPSHOT_TIME = 2_000
BEFORE = 1_000
AFTER = 3_000


def build_tree(root: Path) -> Path:
    """A snapshot plus one test file, with the caller setting the two times.

    Both tests start from the same tree so the verdict each asserts is caused by
    the modification times it sets and by nothing else in the fixture.
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
