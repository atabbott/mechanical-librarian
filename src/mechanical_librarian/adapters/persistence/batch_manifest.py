"""Protected immutable local batch-manifest persistence."""

from __future__ import annotations

import os
import secrets
import stat
import sys
from pathlib import Path

from mechanical_librarian.adapters.persistence.metadata_reports import (
    _LinuxDirectoryChain,
)
from mechanical_librarian.application.batch import (
    MAX_MANIFEST_BYTES,
    BatchError,
    parse_manifest,
)


def _binding(path: Path) -> tuple[_LinuxDirectoryChain | None, int]:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise BatchError("batch manifest is unavailable")
    chain = (
        _LinuxDirectoryChain.open(path.parent)
        if sys.platform.startswith("linux")
        else None
    )
    descriptor = (
        chain.parent_descriptor
        if chain is not None
        else os.open(
            path.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        )
    )
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o077
    ):
        os.close(descriptor) if chain is None else None
        chain.close() if chain is not None else None
        raise BatchError("batch manifest is unavailable")
    return chain, descriptor


def create(path: Path, raw: bytes) -> None:
    parse_manifest(raw)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise BatchError("batch manifest is invalid")
    chain = None
    parent = -1
    descriptor = -1
    temporary = ".batch-manifest-" + secrets.token_hex(16)
    try:
        chain, parent = _binding(path)
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        os.link(
            temporary,
            path.name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=parent)
        temporary = ""
        os.fsync(parent)
    except Exception:
        if temporary and parent >= 0:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
        raise BatchError("batch manifest is unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if chain is not None:
            chain.close()
        elif parent >= 0:
            os.close(parent)


def read(path: Path) -> bytes:
    chain = None
    parent = -1
    descriptor = -1
    try:
        chain, parent = _binding(path)
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o222
            or not 1 <= info.st_size <= MAX_MANIFEST_BYTES
        ):
            raise BatchError("batch manifest is invalid")
        raw = os.read(descriptor, info.st_size + 1)
        if len(raw) != info.st_size:
            raise BatchError("batch manifest is invalid")
        parse_manifest(raw)
        return raw
    except Exception:
        raise BatchError("batch manifest is unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if chain is not None:
            chain.close()
        elif parent >= 0:
            os.close(parent)


__all__ = ["create", "read"]
