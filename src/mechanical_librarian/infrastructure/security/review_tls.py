"""Descriptor-bound TLS identity for the authenticated review server."""

from __future__ import annotations

import os
import ssl
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from mechanical_librarian.application.configuration import ConfigurationError


_MAX_CERTIFICATE_BYTES = 65_536
_MAX_PRIVATE_KEY_BYTES = 32_768


@dataclass(frozen=True)
class ReviewTLSIdentity:
    certificate_file: str
    private_key_file: str


@contextmanager
def open_review_tls_identity(
    certificate_value: str | None, private_key_value: str | None
) -> Iterator[ReviewTLSIdentity]:
    """Open, validate, and retain one exact certificate/key pair."""
    descriptors: list[int] = []
    try:
        if certificate_value is None or private_key_value is None:
            raise ConfigurationError("review TLS configuration is unavailable")
        certificate = _open_tls_file(
            certificate_value, _MAX_CERTIFICATE_BYTES, private=False
        )
        descriptors.append(certificate)
        private_key = _open_tls_file(
            private_key_value, _MAX_PRIVATE_KEY_BYTES, private=True
        )
        descriptors.append(private_key)
        prefix = "/proc/self/fd" if Path("/proc/self/fd").is_dir() else "/dev/fd"
        identity = ReviewTLSIdentity(
            f"{prefix}/{certificate}", f"{prefix}/{private_key}"
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            identity.certificate_file, identity.private_key_file, password=""
        )
        os.lseek(certificate, 0, os.SEEK_SET)
        os.lseek(private_key, 0, os.SEEK_SET)
        yield identity
    except ConfigurationError:
        raise
    except (OSError, ssl.SSLError, ValueError):
        raise ConfigurationError("review TLS configuration is unavailable") from None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_tls_file(
    path_value: str, maximum: int, *, private: bool
) -> int:
    path = Path(path_value)
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise ConfigurationError("review TLS configuration is unavailable")
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    parent = os.open(path.parent, directory_flags)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
    finally:
        os.close(parent)
    try:
        information = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        raise
    if (
        not stat.S_ISREG(information.st_mode)
        or information.st_uid != os.geteuid()
        or information.st_nlink != 1
        or information.st_mode & (0o077 if private else 0o022)
        or not 1 <= information.st_size <= maximum
    ):
        os.close(descriptor)
        raise ConfigurationError("review TLS configuration is unavailable")
    return descriptor


__all__ = ["ReviewTLSIdentity", "open_review_tls_identity"]
