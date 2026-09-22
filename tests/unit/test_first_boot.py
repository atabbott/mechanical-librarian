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


def test_private_session_store_does_not_block_setup_resume(tmp_path: Path) -> None:
    """Setup's first sign-in creates the session database inside private/ (#912)."""
    (tmp_path / "application.sqlite3").touch()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    (private / "review-sessions.sqlite").touch()
    assert _select(tmp_path, resume=True) is FirstBootMode.RESUME_SETUP


def test_interrupted_session_write_still_resumes_setup(tmp_path: Path) -> None:
    """A crash mid sign-in leaves a rollback journal; setup must still resume.

    The session store is `journal_mode=DELETE`, so an interrupted write leaves
    `review-sessions.sqlite-journal` beside the database and the next open
    rolls it back. Treating that as runtime state would turn a power loss
    during setup into a volume that never boots again (#912).
    """
    (tmp_path / "application.sqlite3").touch()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    (private / "review-sessions.sqlite").touch()
    (private / "review-sessions.sqlite-journal").touch()
    assert _select(tmp_path, resume=True) is FirstBootMode.RESUME_SETUP


def test_orphan_session_journal_fails_closed(tmp_path: Path) -> None:
    """A rollback journal is resumable only beside its database."""
    (tmp_path / "application.sqlite3").touch()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    journal = private / "review-sessions.sqlite-journal"
    journal.touch()

    with pytest.raises(FirstBootError):
        _select(tmp_path, resume=True)

    assert journal.exists()


def test_session_write_ahead_sidecars_still_fail_closed(tmp_path: Path) -> None:
    """No WAL is adopted anywhere, so a `-wal` sidecar is not this store's."""
    (tmp_path / "application.sqlite3").touch()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    (private / "review-sessions.sqlite").touch()
    (private / "review-sessions.sqlite-wal").touch()
    with pytest.raises(FirstBootError):
        _select(tmp_path, resume=True)


def test_other_private_runtime_state_still_fails_closed(tmp_path: Path) -> None:
    """Only the session database is a setup-time private artifact."""
    (tmp_path / "application.sqlite3").touch()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    (private / "review-sessions.sqlite").touch()
    (private / "provider-recovery.sqlite").touch()
    with pytest.raises(FirstBootError):
        _select(tmp_path, resume=True)


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


@pytest.mark.parametrize("foreign", [False, True])
def test_setup_inventory_is_refreshed_after_root_admission(
    tmp_path: Path, foreign: bool
) -> None:
    (tmp_path / "application.sqlite3").touch()
    alias = tmp_path / (".metadata-report-binding-" + "a" * 32 + ".sqlite")
    alias.touch()

    def complete() -> bool:
        alias.unlink()
        if foreign:
            (tmp_path / "foreign").touch()
        return False

    def select() -> FirstBootMode:
        return select_first_boot_mode(
            tmp_path, complete_store=complete, resumable_setup=lambda: True
        )

    if foreign:
        with pytest.raises(FirstBootError):
            select()
        assert (tmp_path / "foreign").exists()
    else:
        assert select() is FirstBootMode.RESUME_SETUP
