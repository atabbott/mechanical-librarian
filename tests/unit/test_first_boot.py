from __future__ import annotations

from pathlib import Path

import pytest

from mechanical_librarian.infrastructure.first_boot import (
    FirstBootError,
    FirstBootMode,
    select_first_boot_mode,
)


def _select(root: Path, complete: bool = False, resume: bool = False) -> FirstBootMode:
    return select_first_boot_mode(
        root,
        complete_store=lambda: complete,
        resumable_setup=lambda: resume,
    )


def test_true_empty_volume_selects_setup_before_any_runtime_artifact(
    tmp_path: Path,
) -> None:
    assert _select(tmp_path) is FirstBootMode.INITIALIZE_SETUP


def test_present_file_is_authoritative_even_when_the_rest_is_invalid(
    tmp_path: Path,
) -> None:
    (tmp_path / "config.json").write_text("not parsed here", encoding="utf-8")
    (tmp_path / "orphan").write_text("x", encoding="utf-8")
    assert _select(tmp_path) is FirstBootMode.FILE


def test_complete_store_selects_normal_mode_with_its_existing_runtime_artifacts(
    tmp_path: Path,
) -> None:
    for name in ("application.sqlite3", "catalogue.sqlite3", "artifacts"):
        (tmp_path / name).touch()
    assert _select(tmp_path, complete=True) is FirstBootMode.NORMAL


def test_only_exact_bootstrap_bound_database_can_resume_setup(tmp_path: Path) -> None:
    (tmp_path / "application.sqlite3").touch()
    assert _select(tmp_path, resume=True) is FirstBootMode.RESUME_SETUP


@pytest.mark.parametrize(
    "names", (("orphan",), ("application.sqlite3", "artifacts"), ("catalogue.sqlite3",))
)
def test_every_other_nonempty_state_fails_closed(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    for name in names:
        (tmp_path / name).touch()
    with pytest.raises(FirstBootError):
        _select(tmp_path, resume=True)
