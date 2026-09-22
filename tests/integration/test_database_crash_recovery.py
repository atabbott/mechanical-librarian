"""Native Linux retained aliases: admission precedes authoritative rollback."""

from __future__ import annotations

import hashlib
import os
import signal
import sqlite3
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import MetadataReportError

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux inode binding")

_CHILD = """
import os,signal,sys
from pathlib import Path
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
repository = open_repository(Path(sys.argv[1]))
repository._connection.execute('PRAGMA cache_size=1')
if sys.argv[2] == 'clean':
    os.kill(os.getpid(), signal.SIGKILL)
def stop(sql):
    if sql == 'COMMIT':
        os.kill(os.getpid(), signal.SIGKILL)
repository._connection.set_trace_callback(stop)
if sys.argv[2] == 'multi':
    repository._connection.execute('BEGIN IMMEDIATE')
    repository._connection.execute('UPDATE review_preparation_scans SET updated_at=11')
    repository._connection.execute('COMMIT')
else:
    repository.request_review_scan('primary','books','library','f'*64,10)
"""


def _crash(path: Path, mode: str) -> tuple[Path, Path]:
    repository = open_repository(path)
    if mode == "multi":
        for index in range(150):
            repository.request_review_scan(
                "primary", f"books-{index}", f"lib-{index}", "f" * 64, 10
            )
    repository.close()
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, str(path), mode],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    (alias,) = path.parent.glob(".metadata-report-binding-*.sqlite")
    return alias, Path(str(alias) + "-journal")


def _snapshot(paths: tuple[Path, ...]) -> tuple[tuple[int, int, bytes], ...]:
    return tuple(
        (
            path.stat().st_dev,
            path.stat().st_ino,
            hashlib.sha256(path.read_bytes()).digest(),
        )
        for path in paths
    )


@pytest.mark.parametrize("mode", ["clean", "single", "multi"])
def test_normal_root_opt_in_recovers_native_kill(tmp_path: Path, mode: str) -> None:
    path = tmp_path / "application.sqlite3"
    alias, journal = _crash(path, mode)
    retained = (path, alias) if mode == "clean" else (path, alias, journal)
    before = _snapshot(retained)
    with pytest.raises(MetadataReportError):
        open_repository(path)
    assert _snapshot(retained) == before
    recovered = open_repository(path, recover_stale_bindings=True)
    try:
        assert not alias.exists()
        assert not journal.exists()
        assert not list(tmp_path.glob(".metadata-report-recovery-*"))
        assert (path.stat().st_dev, path.stat().st_ino) == before[0][:2]
        count = recovered._connection.execute(
            "SELECT count(*) FROM review_preparation_scans"
        ).fetchone()[0]
        assert count == (150 if mode == "multi" else 0)
        assert (
            recovered._connection.execute("PRAGMA integrity_check").fetchone()[0]
            == "ok"
        )
    finally:
        recovered.close()
    assert set(tmp_path.iterdir()) == {path}


@pytest.mark.parametrize("fault", ["truncate", "checksum", "tail", "padding", "count"])
def test_journal_refusal_preserves_original_bytes_and_inodes(
    tmp_path: Path, fault: str
) -> None:
    path = tmp_path / "application.sqlite3"
    alias, journal = _crash(path, "multi")
    data = bytearray(journal.read_bytes())
    _, _, _, sector, size = struct.unpack(">5I", data[8:28])
    if fault == "truncate":
        del data[30:]
    elif fault == "checksum":
        data[sector + size + 4] ^= 1
    elif fault == "tail":
        data[-1] ^= 1
    elif fault == "padding":
        data[28] = 1
    else:
        data[8:12] = bytes.fromhex("ffffffff")
    journal.write_bytes(data)
    before = _snapshot((path, alias, journal))
    with pytest.raises(MetadataReportError):
        open_repository(path, recover_stale_bindings=True)
    assert _snapshot((path, alias, journal)) == before
    assert set(tmp_path.iterdir()) == {path, alias, journal}


def test_interrupted_copy_cleanup_is_closed_and_source_preserving(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.sqlite3"
    alias, _ = _crash(path, "clean")
    scratch = tmp_path / (".metadata-report-recovery-" + "a" * 32)
    scratch.mkdir(mode=0o700)
    (scratch / "foreign").write_text("unrecognized")
    before = _snapshot((path, alias))
    with pytest.raises(MetadataReportError):
        open_repository(path, recover_stale_bindings=True)
    assert _snapshot((path, alias)) == before
    assert (scratch / "foreign").read_text() == "unrecognized"
    (scratch / "foreign").unlink()
    copied = scratch / "database.sqlite"
    copied.touch(mode=0o600)
    repository = open_repository(path, recover_stale_bindings=True)
    repository.close()
    assert not scratch.exists()
    assert not alias.exists()


@pytest.mark.parametrize(
    "fault",
    ["disk", "short_write", "incorrect_write", "eof", "copy_schema", "source_drift"],
)
def test_copy_admission_failures_never_replay_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from mechanical_librarian.adapters.persistence import crash_recovery

    path = tmp_path / "application.sqlite3"
    alias, journal = _crash(path, "multi")
    before = _snapshot((path, alias, journal))
    native = crash_recovery._native_rollback
    replayed: list[Path] = []
    mutated: tuple[tuple[int, int, bytes], ...] | None = None

    def rollback(candidate: Path, descriptor: int) -> None:
        nonlocal mutated
        replayed.append(candidate)
        assert candidate.name == "database.sqlite", (
            "authoritative replay before admission"
        )
        native(candidate, descriptor)
        if fault == "copy_schema":
            # Deliberate corruption fixture, not an alternate production opener.
            connection = sqlite3.connect(candidate)
            try:
                connection.execute("CREATE TABLE foreign_schema (value TEXT)")
                connection.commit()
            finally:
                connection.close()
        if fault == "source_drift":
            with journal.open("r+b") as stream:
                stream.seek(-1, os.SEEK_END)
                value = stream.read(1)
                stream.seek(-1, os.SEEK_END)
                stream.write(bytes([value[0] ^ 1]))
            mutated = _snapshot((path, alias, journal))

    monkeypatch.setattr(crash_recovery, "_native_rollback", rollback)
    if fault == "disk":
        import errno

        def fail_write(_descriptor: int, _data: bytes) -> int:
            raise OSError(errno.ENOSPC, "synthetic full device")

        monkeypatch.setattr(crash_recovery.os, "write", fail_write)
    elif fault == "short_write":
        monkeypatch.setattr(crash_recovery.os, "write", lambda *_: 0)
    elif fault == "incorrect_write":
        original_write = crash_recovery.os.write

        def incorrect_write(descriptor: int, data: bytes) -> int:
            altered = bytes([data[0] ^ 1]) + data[1:]
            return original_write(descriptor, altered)

        monkeypatch.setattr(crash_recovery.os, "write", incorrect_write)
    elif fault == "eof":
        original = crash_recovery.os.pread

        def short_read(descriptor: int, size: int, offset: int) -> bytes:
            data = original(descriptor, size, offset)
            return data[:-1] if size > 65536 else data

        monkeypatch.setattr(crash_recovery.os, "pread", short_read)
    with pytest.raises(MetadataReportError):
        open_repository(path, recover_stale_bindings=True)
    assert _snapshot((path, alias, journal)) == (mutated or before)
    assert set(tmp_path.iterdir()) == {path, alias, journal}
    assert len(replayed) == (1 if fault in {"copy_schema", "source_drift"} else 0)


@pytest.mark.parametrize("fault", ["multiple", "foreign_alias", "wal", "scratch_link"])
def test_unsupported_retained_inventory_preserves_every_source(
    tmp_path: Path, fault: str
) -> None:
    path = tmp_path / "application.sqlite3"
    alias, journal = _crash(path, "single")
    extra = tmp_path / (".metadata-report-binding-" + "b" * 32 + ".sqlite")
    retained = [path, alias, journal]
    if fault == "multiple":
        os.link(path, extra)
        second = Path(str(extra) + "-journal")
        second.write_bytes(journal.read_bytes())
        second.chmod(0o600)
        retained.extend((extra, second))
    elif fault == "foreign_alias":
        extra.write_bytes(path.read_bytes())
        extra.chmod(0o600)
        retained.append(extra)
    elif fault == "wal":
        extra = Path(str(alias) + "-wal")
        extra.touch(mode=0o600)
        retained.append(extra)
    else:
        scratch = tmp_path / (".metadata-report-recovery-" + "c" * 32)
        scratch.mkdir(mode=0o700)
        extra = scratch / "database.sqlite"
        os.link(path, extra)
        retained.append(extra)
    before = _snapshot(tuple(retained))
    with pytest.raises(MetadataReportError):
        open_repository(path, recover_stale_bindings=True)
    assert _snapshot(tuple(retained)) == before


def test_active_root_and_borrowed_authority_never_gain_recovery(tmp_path: Path) -> None:
    path = tmp_path / "application.sqlite3"
    root = open_repository(path)
    try:
        before = _snapshot((path,))
        with pytest.raises(MetadataReportError):
            open_repository(path, recover_stale_bindings=True)
        with pytest.raises(MetadataReportError, match="recovery authority"):
            open_repository(path, authority=root.authority, recover_stale_bindings=True)
        assert _snapshot((path,)) == before
    finally:
        root.close()


@pytest.mark.parametrize("fault", ["mkdir", "open", "cleanup"])
def test_partial_scratch_failure_preserves_source_and_primary_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from mechanical_librarian.adapters.persistence import crash_recovery

    path = tmp_path / "application.sqlite3"
    alias, journal = _crash(path, "single")
    before = _snapshot((path, alias, journal))
    scratch = tmp_path / (".metadata-report-recovery-" + "d" * 32)
    monkeypatch.setattr(crash_recovery.secrets, "token_hex", lambda _: "d" * 32)
    original_open = crash_recovery.os.open
    original_mkdir = crash_recovery.os.mkdir
    if fault == "mkdir":

        def collision(name: str, mode: int, *, dir_fd: int) -> None:
            original_mkdir(name, mode, dir_fd=dir_fd)
            (scratch / "foreign").write_text("preserve collision")
            raise FileExistsError("synthetic collision")

        monkeypatch.setattr(crash_recovery.os, "mkdir", collision)
    elif fault == "open":

        def fail_open(name, flags, *args, **kwargs):
            if name == scratch.name:
                raise OSError("synthetic scratch open failure")
            return original_open(name, flags, *args, **kwargs)

        monkeypatch.setattr(crash_recovery.os, "open", fail_open)
    else:

        def fail_copy(*_args):
            raise MetadataReportError("primary copy refusal")

        def fail_cleanup(*_args, **_kwargs):
            raise RuntimeError("secondary cleanup refusal")

        monkeypatch.setattr(crash_recovery, "_digest", fail_copy)
        monkeypatch.setattr(crash_recovery, "_cleanup_scratch", fail_cleanup)
    with pytest.raises(MetadataReportError) as failure:
        open_repository(path, recover_stale_bindings=True)
    if fault == "cleanup":
        assert str(failure.value) == "primary copy refusal"
    assert _snapshot((path, alias, journal)) == before
    assert scratch.is_dir()
    if fault == "mkdir":
        assert (scratch / "foreign").read_text() == "preserve collision"
    else:
        monkeypatch.undo()
        # A subsequent ordinary root recognizes only the closed scratch inventory.
        recovered = open_repository(path, recover_stale_bindings=True)
        recovered.close()
        assert not scratch.exists()
        assert not alias.exists()
        assert not journal.exists()


@pytest.mark.parametrize("fault", ["configured_path", "parent", "alias"])
def test_copy_admission_rechecks_original_path_and_parent_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from mechanical_librarian.adapters.persistence import crash_recovery

    parent = tmp_path / "bound"
    parent.mkdir(mode=0o700)
    path = parent / "application.sqlite3"
    alias, journal = _crash(path, "single")
    before = _snapshot((path, alias, journal))
    native = crash_recovery._native_rollback
    retained = [path, alias, journal]
    copies = []

    def replace_after_private_replay(candidate: Path, descriptor: int) -> None:
        assert candidate.name == "database.sqlite", "source replay after binding drift"
        copies.append(candidate)
        native(candidate, descriptor)
        if fault == "parent":
            moved = tmp_path / "retained"
            parent.rename(moved)
            parent.mkdir(mode=0o700)
            retained[:] = [moved / value.name for value in retained]
        else:
            selected = path if fault == "configured_path" else alias
            moved = parent / "retained.sqlite3"
            selected.rename(moved)
            selected.write_bytes(moved.read_bytes())
            selected.chmod(0o600)
            retained[0 if fault == "configured_path" else 1] = moved

    monkeypatch.setattr(
        crash_recovery, "_native_rollback", replace_after_private_replay
    )
    with pytest.raises(MetadataReportError):
        open_repository(path, recover_stale_bindings=True)
    assert len(copies) == 1
    assert _snapshot(tuple(retained)) == before


@pytest.mark.parametrize("change", ["added", "removed"])
def test_fresh_inventory_refuses_changes_before_source_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from mechanical_librarian.adapters.persistence import crash_recovery

    path = tmp_path / "application.sqlite3"
    alias, journal = _crash(path, "single")
    foreign = tmp_path / "foreign"
    if change == "removed":
        foreign.write_text("synthetic sibling")
    before = _snapshot((path, alias, journal))
    native = crash_recovery._native_rollback
    copies = []

    def change_inventory(candidate: Path, descriptor: int) -> None:
        assert candidate.name == "database.sqlite", (
            "source replay after inventory drift"
        )
        copies.append(candidate)
        native(candidate, descriptor)
        if change == "added":
            foreign.write_text("synthetic sibling")
        else:
            foreign.unlink()

    monkeypatch.setattr(crash_recovery, "_native_rollback", change_inventory)
    with pytest.raises(MetadataReportError):
        open_repository(path, recover_stale_bindings=True)
    assert len(copies) == 1
    assert _snapshot((path, alias, journal)) == before
    assert foreign.exists() is (change == "added")


@pytest.mark.parametrize("fault", ["identity", "enumeration"])
def test_fresh_inventory_descriptor_refusal_closes_without_source_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from mechanical_librarian.adapters.persistence import crash_recovery

    parent = tmp_path / "bound"
    parent.mkdir(mode=0o700)
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    path = parent / "application.sqlite3"
    alias, journal = _crash(path, "single")
    before = _snapshot((path, alias, journal))
    original_open = os.open
    original_listdir = os.listdir
    descriptors = []

    def open_inventory(name, flags, *args, **kwargs):
        if name != ".":
            return original_open(name, flags, *args, **kwargs)
        assert flags & os.O_DIRECTORY and flags & os.O_NOFOLLOW
        descriptor = (
            original_open(other, flags)
            if fault == "identity"
            else original_open(name, flags, *args, **kwargs)
        )
        descriptors.append(descriptor)
        return descriptor

    def list_inventory(descriptor):
        if descriptor in descriptors:
            assert fault == "enumeration", "enumerated an unbound directory"
            raise OSError("synthetic enumeration failure")
        return original_listdir(descriptor)

    def no_replay(*_args):
        pytest.fail("native replay before directory admission")

    monkeypatch.setattr(crash_recovery.os, "open", open_inventory)
    monkeypatch.setattr(crash_recovery.os, "listdir", list_inventory)
    monkeypatch.setattr(crash_recovery, "_native_rollback", no_replay)
    with pytest.raises(MetadataReportError):
        open_repository(path, recover_stale_bindings=True)
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert _snapshot((path, alias, journal)) == before
