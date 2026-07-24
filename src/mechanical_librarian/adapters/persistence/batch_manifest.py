"""Bounded immutable local batch-manifest persistence."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from mechanical_librarian.application.batch import BatchError, parse_manifest

_MAX = 1_048_576


def _parent(path: Path) -> None:
    info = path.parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise BatchError("batch manifest is unavailable")


def create(path: Path, raw: bytes) -> None:
    parse_manifest(raw)
    descriptor = -1
    try:
        _parent(path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600)
        written = os.write(descriptor, raw)
        if written != len(raw):
            raise OSError("short manifest write")
        os.fsync(descriptor)
    except (OSError, BatchError):
        raise BatchError("batch manifest is unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read(path: Path) -> bytes:
    descriptor = -1
    try:
        _parent(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= _MAX:
            raise BatchError("batch manifest is invalid")
        raw = os.read(descriptor, info.st_size + 1)
        if len(raw) != info.st_size:
            raise BatchError("batch manifest is invalid")
        parse_manifest(raw)
        return raw
    except (OSError, BatchError):
        raise BatchError("batch manifest is unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


__all__ = ["create", "read"]
