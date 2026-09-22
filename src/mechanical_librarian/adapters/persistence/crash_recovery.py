"""Admit one private DELETE journal before native SQLite touches its source.

The envelope reader only rejects unsupported input. SQLite remains the rollback
engine, first on an owner-private copy and only then on the retained alias.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import struct
import sys
from collections.abc import Callable
from pathlib import Path

from mechanical_librarian.adapters.persistence.binding import _connect_bound
from mechanical_librarian.application.metadata_report import MetadataReportError

_ALIAS = re.compile(r"\.metadata-report-binding-[0-9a-f]{32}\.sqlite")
_SCRATCH = re.compile(r"\.metadata-report-recovery-[0-9a-f]{32}")
_COPY = "database.sqlite"
_MAGIC = bytes.fromhex("d9d505f920a163d7")
_CHUNK = 1024 * 1024


def _refuse() -> None:
    raise MetadataReportError("database recovery state is unsupported")


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular(info: os.stat_result, *, copy: bool = False) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or (copy and info.st_nlink != 1)
    ):
        _refuse()


def _read(descriptor: int, length: int, offset: int) -> bytes:
    data = os.pread(descriptor, length, offset)
    if len(data) != length:
        _refuse()
    return data


def _zeros(descriptor: int, start: int, end: int) -> None:
    while start < end:
        chunk = min(_CHUNK, end - start)
        if any(_read(descriptor, chunk, start)):
            _refuse()
        start += chunk


def validate_journal(descriptor: int, length: int, database_length: int) -> None:
    """Check finite segment geometry and SQLite's sparse nonce checksum.

    A new nonce per segment is legitimate. Repeated pages across segments are
    also legitimate; this is not a uniqueness or rollback implementation.
    Complete records in the terminal unsynced header are checked even though
    native recovery can ignore that tail.
    """
    position = 0
    geometry: tuple[int, int, int] | None = None
    segments = 0
    while position < length:
        if length - position < 28:
            _refuse()
        header = _read(descriptor, 28, position)
        count, nonce, pages, sector, size = struct.unpack(">5I", header[8:])
        if (
            not 512 <= sector <= 65536
            or sector & (sector - 1)
            or not 512 <= size <= 65536
            or size & (size - 1)
            or not 0 < pages <= database_length // size
            or database_length % size
            or length - position < sector
        ):
            _refuse()
        shape = pages, sector, size
        if geometry is not None and geometry != shape:
            _refuse()
        geometry = shape
        _zeros(descriptor, position + 28, position + sector)
        unsynced = header[:8] == bytes(8) and count == 0 and segments > 0
        position += sector
        if unsynced:
            if (length - position) % (size + 8):
                _refuse()
            count = (length - position) // (size + 8)
        elif header[:8] != _MAGIC or not 0 < count < 0xFFFFFFFF:
            _refuse()
        if count > (length - position) // (size + 8):
            _refuse()
        for _ in range(count):
            record = _read(descriptor, size + 8, position)
            page = int.from_bytes(record[:4], "big")
            checksum = (
                nonce + sum(record[4 : 4 + size][size - 200 :: -200])
            ) & 0xFFFFFFFF
            if not 0 < page <= pages or checksum != int.from_bytes(record[-4:], "big"):
                _refuse()
            position += size + 8
        segments += 1
        if position == length:
            break
        if unsynced:
            _refuse()
        aligned = ((position + sector - 1) // sector) * sector
        if aligned > length:
            _refuse()
        _zeros(descriptor, position, aligned)
        position = aligned
    if not segments:
        _refuse()


def _digest(
    descriptor: int, expected: os.stat_result, destination: int | None = None
) -> bytes:
    digest = hashlib.sha256()
    offset = 0
    while offset < expected.st_size:
        chunk = _read(descriptor, min(_CHUNK, expected.st_size - offset), offset)
        digest.update(chunk)
        if destination is not None:
            written = 0
            while written < len(chunk):
                amount = os.write(destination, chunk[written:])
                if amount <= 0:
                    _refuse()
                written += amount
        offset += len(chunk)
    if _identity(os.fstat(descriptor)) != _identity(expected):
        _refuse()
    return digest.digest()


def _cleanup_scratch(
    parent: int,
    name: str,
    current: Callable[[], None],
    expected: tuple[int, int] | None = None,
) -> None:
    """Remove only the closed inventory from an interrupted private copy."""
    if _SCRATCH.fullmatch(name) is None:
        _refuse()
    descriptor = os.open(
        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
    )
    try:
        information = os.fstat(descriptor)
        if (
            expected is not None
            and (information.st_dev, information.st_ino) != expected
        ):
            _refuse()
        if (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            _refuse()
        names = os.listdir(descriptor)
        if set(names) - {_COPY, _COPY + "-journal"}:
            _refuse()
        objects = {}
        for child in names:
            value = os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            _regular(value, copy=True)
            objects[child] = value
        current()
        if _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _identity(
            information
        ):
            _refuse()
        for child, expected in objects.items():
            if _identity(
                os.stat(child, dir_fd=descriptor, follow_symlinks=False)
            ) != _identity(expected):
                _refuse()
        for child in names:
            os.unlink(child, dir_fd=descriptor)
        current()
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != (information.st_dev, information.st_ino):
            _refuse()
        os.rmdir(name, dir_fd=parent)
    finally:
        os.close(descriptor)


def _native_rollback(path: Path, descriptor: int) -> None:
    connection = _connect_bound(path, descriptor, allow_rollback_journal=True)
    try:
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        connection.close()


def recover_private_bindings(
    parent: int,
    database: int,
    configured_name: str,
    validate: Callable[[Path, int], None],
    current: Callable[[], None],
) -> None:
    """Called only by an opted-in root while its exclusive parent lock is held."""
    names = set(os.listdir(parent))
    aliases: dict[str, os.stat_result] = {}
    journals: dict[str, os.stat_result] = {}
    scratch: list[str] = []
    expected_db = os.fstat(database)
    _regular(expected_db)
    for name in names:
        if name.startswith(".metadata-report-recovery-"):
            if _SCRATCH.fullmatch(name) is None:
                _refuse()
            scratch.append(name)
        elif name.startswith(".metadata-report-binding-"):
            base = name.removesuffix("-journal")
            if _ALIAS.fullmatch(base) is None or base not in names:
                _refuse()
            information = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _regular(information)
            if name == base:
                if (information.st_dev, information.st_ino) != (
                    expected_db.st_dev,
                    expected_db.st_ino,
                ):
                    _refuse()
                aliases[name] = information
            else:
                if information.st_nlink != 1:
                    _refuse()
                journals[name] = information
    if len(journals) > 1:
        _refuse()
    # Interrupted copies carry no authoritative bytes and have a closed cleanup
    # inventory. Their admission never relaxes the source's own checks.
    for name in scratch:
        _cleanup_scratch(parent, name, current)
    if not aliases:
        return
    journal = -1
    scratch_name: str | None = None
    scratch_fd = -1
    source = Path(f"/proc/self/fd/{parent}/{configured_name}")
    try:
        if journals:
            journal_name = next(iter(journals))
            journal = os.open(journal_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            expected_journal = os.fstat(journal)
            if _identity(expected_journal) != _identity(journals[journal_name]):
                _refuse()
            _regular(expected_journal, copy=True)
            validate_journal(journal, expected_journal.st_size, expected_db.st_size)
            candidate = f".metadata-report-recovery-{secrets.token_hex(16)}"
            os.mkdir(candidate, 0o700, dir_fd=parent)
            scratch_name = candidate
            scratch_fd = os.open(
                scratch_name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            digests = []
            copied_identities: dict[str, os.stat_result] = {}
            for name, descriptor, information in (
                (_COPY, database, expected_db),
                (_COPY + "-journal", journal, expected_journal),
            ):
                copied = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=scratch_fd,
                )
                try:
                    digests.append(_digest(descriptor, information, copied))
                    os.fsync(copied)
                    copied_identities[name] = os.fstat(copied)
                finally:
                    os.close(copied)
            for index, (name, information) in enumerate(copied_identities.items()):
                if _identity(
                    os.stat(name, dir_fd=scratch_fd, follow_symlinks=False)
                ) != _identity(information):
                    _refuse()
                copied = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=scratch_fd)
                try:
                    if _digest(copied, information) != digests[index]:
                        _refuse()
                finally:
                    os.close(copied)
            copy_path = Path(f"/proc/self/fd/{scratch_fd}/{_COPY}")
            copied = os.open(_COPY, os.O_RDWR | os.O_NOFOLLOW, dir_fd=scratch_fd)
            try:
                _regular(os.fstat(copied), copy=True)
                if _identity(os.fstat(copied)) != _identity(copied_identities[_COPY]):
                    _refuse()
                _native_rollback(copy_path, copied)
                validate(copy_path, copied)
            finally:
                os.close(copied)
            current()
            if set(os.listdir(parent)) != (names - set(scratch)) | {scratch_name}:
                _refuse()
            for name, information in aliases.items():
                if _identity(
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                ) != _identity(information):
                    _refuse()
            if _identity(
                os.stat(journal_name, dir_fd=parent, follow_symlinks=False)
            ) != _identity(expected_journal):
                _refuse()
            if digests != [
                _digest(database, expected_db),
                _digest(journal, expected_journal),
            ]:
                _refuse()
            _native_rollback(
                Path(f"/proc/self/fd/{parent}/{journal_name.removesuffix('-journal')}"),
                database,
            )
        validate(source, database)
        current()
        for name in aliases:
            information = os.stat(name, dir_fd=parent, follow_symlinks=False)
            _regular(information)
            if (information.st_dev, information.st_ino) != (
                expected_db.st_dev,
                expected_db.st_ino,
            ):
                _refuse()
        for name in aliases:
            os.unlink(name, dir_fd=parent)
    finally:
        if journal >= 0:
            os.close(journal)
        if scratch_fd >= 0:
            scratch_info = os.fstat(scratch_fd)
            os.close(scratch_fd)
            if scratch_name is not None:
                primary = sys.exception()
                try:
                    _cleanup_scratch(
                        parent,
                        scratch_name,
                        current,
                        (scratch_info.st_dev, scratch_info.st_ino),
                    )
                except BaseException:
                    if primary is None:
                        raise
                    # Preserve the admission failure. A failed cleanup leaves
                    # only the closed scratch inventory for a later startup.
