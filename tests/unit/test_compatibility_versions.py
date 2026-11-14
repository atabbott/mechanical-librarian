"""The compatibility page's version identifiers come from the code (#1847)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from scripts import compatibility_versions as versions
from scripts import verify_documentation_integrity as checker

ROOT = Path(__file__).resolve().parents[2]

LABELS = ("alpha-v3", "pair-v1", "pair-v3")


def test_the_page_names_every_current_version_and_nothing_else() -> None:
    assert versions.version_findings(ROOT) == ()
    assert checker.stale_version_identifiers(ROOT) == ()


def test_each_registered_constant_is_one_distinct_label() -> None:
    labels = versions.current_versions()
    assert len(labels) == len(versions.CURRENT_VERSIONS)
    assert len(set(labels)) == len(labels)


def test_a_moved_version_is_rewritten_wherever_the_page_names_it() -> None:
    page = (
        "| `alpha-v2` | The configuration (its schema) |\n"
        "| `pair-v1`, `pair-v3` | x |\n"
        "Documents other than `alpha-v2` are refused.\n"
    )
    updated, problems = versions.reconcile(page, LABELS)
    assert problems == ()
    assert updated == page.replace("`alpha-v2`", "`alpha-v3`")


def test_one_of_two_current_versions_moves_on_its_own() -> None:
    page = "`pair-v1`, `pair-v2`, `alpha-v3`"
    updated, problems = versions.reconcile(page, LABELS)
    assert problems == ()
    assert updated == page.replace("-v2`", "-v3`")


def test_what_cannot_be_matched_is_reported_not_guessed() -> None:
    page = "`alpha-v3`, `pair-v1`, `pair-v2`"
    ambiguous_page = page + ", `invented-document-v1`"
    updated, problems = versions.reconcile(
        ambiguous_page,
        ("alpha-v3", "pair-v4", "pair-v5"),
    )
    assert updated == ambiguous_page
    assert [problem.split(": ", 1)[1] for problem in problems] == [
        "`pair-v1` is stale and matches no single current "
        "version of pair "
        "(pair-v4, pair-v5)",
        "`pair-v2` is stale and matches no single current "
        "version of pair "
        "(pair-v4, pair-v5)",
        "`invented-document-v1` is not a version this build defines; add its "
        "constant to CURRENT_VERSIONS in scripts/compatibility_versions.py",
        "`pair-v4` is current but the page does not name it",
        "`pair-v5` is current but the page does not name it",
    ]


def test_prose_without_backticks_is_left_alone() -> None:
    page = "`alpha-v3` `pair-v1` `pair-v3`"
    prose = page + " /api/v1 alpha-v2 `matching.work_identity`"
    assert versions.reconcile(prose, LABELS) == (prose, ())


def _copied_page(tmp_path: Path) -> Path:
    page = tmp_path / versions.PAGE
    page.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / versions.PAGE, page)
    return page


def test_a_stale_page_fails_the_integrity_check_until_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    page = _copied_page(tmp_path)
    current = page.read_text(encoding="utf-8")
    label = versions.current_versions()[0]
    document, number = label.rsplit("-v", 1)
    stale = current.replace(f"`{label}`", f"`{document}-v{int(number) - 1}`")
    assert stale != current
    page.write_text(stale, encoding="utf-8")

    assert checker.stale_version_identifiers(tmp_path) == (
        f"{versions.PAGE}: version identifiers are stale; run "
        "`uv run python scripts/compatibility_versions.py --write`",
    )
    assert versions.main(["--root", str(tmp_path)]) == 1
    assert page.read_text(encoding="utf-8") == stale
    assert "run with --write" in capsys.readouterr().err

    assert versions.main(["--root", str(tmp_path), "--write"]) == 0
    assert page.read_text(encoding="utf-8") == current
    assert checker.stale_version_identifiers(tmp_path) == ()


def test_a_tree_without_the_page_has_nothing_to_check(tmp_path: Path) -> None:
    assert checker.stale_version_identifiers(tmp_path) == ()
