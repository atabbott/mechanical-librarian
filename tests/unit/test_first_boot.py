from __future__ import annotations

import os
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


def test_owner_only_setup_claim_token_does_not_block_setup_resume(
    tmp_path: Path,
) -> None:
    """Every unclaimed start leaves its claim token in private/ (ADR-0086)."""
    (tmp_path / "application.sqlite3").touch()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    (private / "review-sessions.sqlite").touch()
    token = private / "setup-claim-token"
    token.write_text("token\n", encoding="ascii")
    token.chmod(0o600)
    assert _select(tmp_path, resume=True) is FirstBootMode.RESUME_SETUP
    (private / "review-sessions.sqlite").unlink()
    assert _select(tmp_path, resume=True) is FirstBootMode.RESUME_SETUP


@pytest.mark.parametrize("unsafe", ("0644", "0400", "symlink", "directory", "fifo"))
def test_setup_claim_token_other_than_an_owner_file_fails_closed(
    tmp_path: Path, unsafe: str
) -> None:
    (tmp_path / "application.sqlite3").touch()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    token = private / "setup-claim-token"
    if unsafe == "symlink":
        target = tmp_path / "elsewhere"
        target.write_text("token\n", encoding="ascii")
        target.chmod(0o600)
        token.symlink_to(target)
    elif unsafe == "directory":
        token.mkdir(mode=0o700)
    elif unsafe == "fifo":
        os.mkfifo(token, 0o600)
    else:
        token.write_text("token\n", encoding="ascii")
        token.chmod(int(unsafe, 8))
    with pytest.raises(FirstBootError):
        _select(tmp_path, resume=True)


def test_other_setup_claim_names_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "application.sqlite3").touch()
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    token = private / "setup-claim-token.tmp"
    token.write_text("token\n", encoding="ascii")
    token.chmod(0o600)
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


@pytest.mark.parametrize("unsafe", ["unknown", "symlink", "mode"])
def test_reviewer_directory_refuses_unsafe_setup_entries(tmp_path, unsafe):
    reviewer = tmp_path / "reviewer"
    reviewer.mkdir(mode=0o700)
    auth = reviewer / "auth"
    if unsafe == "unknown":
        (reviewer / "unexpected").mkdir(mode=0o700)
    elif unsafe == "symlink":
        auth.symlink_to(tmp_path, target_is_directory=True)
    else:
        auth.mkdir(mode=0o755)
    with pytest.raises(FirstBootError):
        select_first_boot_mode(
            tmp_path, complete_store=lambda: False, resumable_setup=lambda: True
        )


def test_empty_fixed_reviewer_directories_preserve_fresh_and_resume(tmp_path):
    reviewer = tmp_path / "reviewer"
    reviewer.mkdir(mode=0o700)
    for name in ("auth", "jobs"):
        (reviewer / name).mkdir(mode=0o700)
    assert (
        select_first_boot_mode(
            tmp_path, complete_store=lambda: False, resumable_setup=lambda: False
        )
        == FirstBootMode.INITIALIZE_SETUP
    )
    (tmp_path / "application.sqlite3").touch()
    assert (
        select_first_boot_mode(
            tmp_path, complete_store=lambda: False, resumable_setup=lambda: True
        )
        == FirstBootMode.RESUME_SETUP
    )
    assert (
        select_first_boot_mode(
            tmp_path, complete_store=lambda: True, resumable_setup=lambda: False
        )
        == FirstBootMode.NORMAL
    )
    (tmp_path / "unrelated").touch()
    with pytest.raises(FirstBootError):
        select_first_boot_mode(
            tmp_path, complete_store=lambda: False, resumable_setup=lambda: True
        )


@pytest.mark.parametrize(
    "name",
    [
        "reviewer-auth-",
        "reviewer-auth-1234567",
        "reviewer-auth-123456789",
        "reviewer-auth-ABCDEFGH",
        "reviewer-auth-1234.678",
    ],
)
def test_malformed_reviewer_staging_name_refuses_resume(
    tmp_path: Path, name: str
) -> None:
    (tmp_path / "application.sqlite3").touch()
    reviewer = tmp_path / "reviewer"
    reviewer.mkdir(mode=0o700)
    (reviewer / name).mkdir(mode=0o700)
    with pytest.raises(FirstBootError):
        _select(tmp_path, resume=True)


@pytest.mark.parametrize("name", ["auth", "jobs", "reviewer-auth-abcd1234"])
def test_reviewer_child_must_be_a_private_directory(tmp_path: Path, name: str) -> None:
    (tmp_path / "application.sqlite3").touch()
    reviewer = tmp_path / "reviewer"
    reviewer.mkdir(mode=0o700)
    (reviewer / name).touch(mode=0o600)
    with pytest.raises(FirstBootError):
        _select(tmp_path, resume=True)


@pytest.mark.parametrize("credential", [False, True])
def test_schema_only_database_ignores_only_empty_reviewer_directories(
    tmp_path: Path, credential: bool
) -> None:
    (tmp_path / "application.sqlite3").touch()
    auth = tmp_path / "reviewer" / "auth"
    auth.parent.mkdir(mode=0o700)
    auth.mkdir(mode=0o700)
    retained = auth / "auth.json"
    if credential:
        retained.write_bytes(b'{"synthetic":true}')
    for _ in range(2):
        if credential:
            with pytest.raises(FirstBootError):
                select_first_boot_mode(
                    tmp_path,
                    complete_store=lambda: False,
                    resumable_setup=lambda: False,
                    uninitialized_setup=lambda: True,
                )
            assert retained.read_bytes() == b'{"synthetic":true}'
        else:
            assert (
                select_first_boot_mode(
                    tmp_path,
                    complete_store=lambda: False,
                    resumable_setup=lambda: False,
                    uninitialized_setup=lambda: True,
                )
                is FirstBootMode.INITIALIZE_SETUP
            )
    assert _select(tmp_path, resume=True) is FirstBootMode.RESUME_SETUP


@pytest.mark.parametrize("overflow", [False, True])
def test_reviewer_inventory_scans_are_bounded_and_closed(
    tmp_path, monkeypatch, overflow
):
    import os

    from mechanical_librarian.infrastructure import first_boot

    reviewer = tmp_path / "reviewer"
    reviewer.mkdir(mode=0o700)
    for index in range(67 if overflow else 1):
        child = reviewer / (f"reviewer-auth-{index:08d}" if overflow else "auth")
        child.mkdir(mode=0o700)
        if not overflow:
            for number in range(3):
                (child / str(number)).touch()
    original = os.scandir
    scans = []

    class Scan:
        def __init__(self, descriptor):
            self.descriptor = descriptor
            self.iterator = original(descriptor)
            self.count = 0
            self.closed = False
            scans.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.iterator.close()
            self.closed = True

        def __iter__(self):
            return self

        def __next__(self):
            entry = next(self.iterator)
            self.count += 1
            assert self.count <= (67 if len(scans) == 1 else 1)
            return entry

    monkeypatch.setattr(first_boot.os, "scandir", Scan)
    if overflow:
        with pytest.raises(FirstBootError):
            first_boot._reviewer_setup_entries(tmp_path)
        assert [scan.count for scan in scans] == [67]
    else:
        assert not first_boot._reviewer_setup_entries(tmp_path)
        assert [scan.count for scan in scans] == [1, 1]
    assert all(scan.closed for scan in scans)
    for scan in scans:
        with pytest.raises(OSError):
            os.fstat(scan.descriptor)
