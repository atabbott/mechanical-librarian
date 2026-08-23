"""No-follow descriptor access for configured read-only media mounts."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass


class LocalMediaError(OSError):
    """A controlled unavailable local-source boundary failure."""


class LocalMediaLimitError(LocalMediaError):
    """A local media source exceeded a configured byte limit."""


@dataclass(frozen=True)
class OpenedLocalMedia:
    descriptor: int
    identity: tuple[int, int, int, int, int]
    length: int


def validate_relative_reference(reference: str) -> tuple[str, ...]:
    if (
        type(reference) is not str
        or not reference
        or "\x00" in reference
        or "\\" in reference
    ):
        raise LocalMediaError("invalid local media reference")
    try:
        encoded = reference.encode("utf-8")
    except UnicodeError:
        raise LocalMediaError("invalid local media reference") from None
    parts = reference.split("/")
    if (
        len(encoded) > 1_024
        or reference.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(len(part.encode("utf-8")) > 255 for part in parts)
    ):
        raise LocalMediaError("invalid local media reference")
    return tuple(parts)


def _identity(descriptor: int) -> tuple[int, int, int, int, int]:
    value = os.fstat(descriptor)
    if not stat.S_ISREG(value.st_mode) or value.st_size < 0:
        raise LocalMediaError("local media source is unavailable")
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def open_local_media(container_path: str, reference: str) -> OpenedLocalMedia:
    """Open one regular file by walking every component without following links."""

    parts = validate_relative_reference(reference)
    if (
        type(container_path) is not str
        or not container_path.startswith("/")
        or "\x00" in container_path
    ):
        raise LocalMediaError("local media mount is unavailable")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow or os.open not in os.supports_dir_fd:
        raise LocalMediaError("no-follow local media access is unavailable")
    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | nofollow
    )
    file_flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) | nofollow
    current = -1
    descriptor = -1
    try:
        current = os.open(container_path, directory_flags)
        for component in parts[:-1]:
            next_descriptor = os.open(component, directory_flags, dir_fd=current)
            os.close(current)
            current = next_descriptor
        descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        identity = _identity(descriptor)
        result = OpenedLocalMedia(descriptor, identity, identity[2])
        descriptor = -1
        return result
    except (OSError, UnicodeError) as error:
        raise LocalMediaError("local media source is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if current >= 0:
            os.close(current)


def sha256_descriptor(descriptor: int, length: int, *, maximum: int) -> str:
    if type(length) is not int or length < 0 or length > maximum:
        raise LocalMediaLimitError("local media source exceeds its byte limit")
    digest = hashlib.sha256()
    offset = 0
    while offset < length:
        amount = min(1_048_576, length - offset)
        try:
            value = os.pread(descriptor, amount, offset)
        except OSError as error:
            raise LocalMediaError("local media source read failed") from error
        if len(value) != amount:
            raise LocalMediaError("local media source changed while reading")
        digest.update(value)
        offset += len(value)
    if _identity(descriptor)[2] != length:
        raise LocalMediaError("local media source changed while reading")
    return digest.hexdigest()


def descriptor_identity(descriptor: int) -> tuple[int, int, int, int, int]:
    return _identity(descriptor)


__all__ = [
    "LocalMediaError",
    "OpenedLocalMedia",
    "descriptor_identity",
    "open_local_media",
    "sha256_descriptor",
    "validate_relative_reference",
]
