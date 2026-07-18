"""Race-checked owner-only reader for private Hardcover queries."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from mechanical_librarian.domain.hardcover_identity import (
    PrivateIdentityQuery,
    PrivateQueryError,
    validate_private_query,
)


_MAX_QUERY_BYTES = 4_096


def read_private_query(path: Path) -> PrivateIdentityQuery:
    """Read one exact private query through a canonical parent descriptor."""

    parent_descriptor: int | None = None
    descriptor: int | None = None
    try:
        if not isinstance(path, Path) or path.name in {"", ".", ".."}:
            raise PrivateQueryError("private query is invalid")
        parent = path.parent.resolve(strict=True)
        parent_descriptor = os.open(
            parent,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent_descriptor,
        )
        before = os.fstat(descriptor)
        if not _safe_file(before):
            raise PrivateQueryError("private query is invalid")
        payload = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or _identity(before) != _identity(after):
            raise PrivateQueryError("private query is invalid")
        text = payload.decode("utf-8", "strict")
        if text.startswith("\ufeff"):
            raise PrivateQueryError("private query is invalid")
        source = json.loads(text, object_pairs_hook=_unique_object)
        return validate_private_query(source)
    except PrivateQueryError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise PrivateQueryError("private query is invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def _safe_file(information: os.stat_result) -> bool:
    return (
        stat.S_ISREG(information.st_mode)
        and information.st_uid == os.geteuid()
        and information.st_nlink == 1
        and bool(information.st_mode & stat.S_IRUSR)
        and not information.st_mode & 0o077
        and 1 <= information.st_size <= _MAX_QUERY_BYTES
    )


def _identity(information: os.stat_result) -> tuple[int, ...]:
    return (
        information.st_dev,
        information.st_ino,
        information.st_mode,
        information.st_uid,
        information.st_nlink,
        information.st_size,
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PrivateQueryError("private query is invalid")
        result[key] = value
    return result


__all__ = ["read_private_query"]
