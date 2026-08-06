"""Descriptor-bound TLS identity for the authenticated review server."""

from __future__ import annotations

import base64
import binascii
import os
import ssl
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from mechanical_librarian.application.configuration import ConfigurationError


_MAX_CERTIFICATE_BYTES = 65_536
_MAX_PRIVATE_KEY_BYTES = 32_768
REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD = "\0"
_UNENCRYPTED_PRIVATE_KEY_ENVELOPES = {
    b"-----BEGIN PRIVATE KEY-----": b"-----END PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----": b"-----END RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----": b"-----END EC PRIVATE KEY-----",
}


@dataclass(frozen=True)
class ReviewTLSIdentity:
    certificate_file: str
    private_key_file: str


@contextmanager
def open_review_tls_identity(
    certificate_value: str | None, private_key_value: str | None
) -> Iterator[ReviewTLSIdentity]:
    """Open, validate, and retain one exact certificate/key pair."""
    snapshots = []
    try:
        if certificate_value is None or private_key_value is None:
            raise ConfigurationError("review TLS configuration is unavailable")
        certificate = _read_tls_file(
            certificate_value, _MAX_CERTIFICATE_BYTES, private=False
        )
        private_key = _read_tls_file(
            private_key_value, _MAX_PRIVATE_KEY_BYTES, private=True
        )
        for payload in (certificate, private_key):
            snapshot = tempfile.TemporaryFile()
            os.fchmod(snapshot.fileno(), 0o600)
            snapshot.write(payload)
            snapshot.flush()
            snapshot.seek(0)
            snapshots.append(snapshot)
        prefix = "/proc/self/fd" if Path("/proc/self/fd").is_dir() else "/dev/fd"
        identity = ReviewTLSIdentity(
            f"{prefix}/{snapshots[0].fileno()}",
            f"{prefix}/{snapshots[1].fileno()}",
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            identity.certificate_file,
            identity.private_key_file,
            password=REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD,
        )
        snapshots[0].seek(0)
        snapshots[1].seek(0)
        yield identity
    except ConfigurationError:
        raise
    except (OSError, ssl.SSLError, ValueError):
        raise ConfigurationError("review TLS configuration is unavailable") from None
    finally:
        for snapshot in reversed(snapshots):
            snapshot.close()


def _read_tls_file(
    path_value: str, maximum: int, *, private: bool
) -> bytes:
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
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_mode & (0o077 if private else 0o022)
            or not 1 <= before.st_size <= maximum
        ):
            raise ConfigurationError("review TLS configuration is unavailable")
        payload = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ConfigurationError("review TLS configuration is unavailable")
        if private:
            _require_unencrypted_private_key(payload)
        return payload
    finally:
        os.close(descriptor)


def _require_unencrypted_private_key(payload: bytes) -> None:
    lines = payload.splitlines()
    closing = (
        None if not lines else _UNENCRYPTED_PRIVATE_KEY_ENVELOPES.get(lines[0])
    )
    if closing is None or len(lines) < 3 or lines[-1] != closing:
        raise ConfigurationError("review TLS configuration is unavailable")
    try:
        decoded = base64.b64decode(b"".join(lines[1:-1]), validate=True)
    except (binascii.Error, ValueError):
        raise ConfigurationError("review TLS configuration is unavailable") from None
    if not decoded:
        raise ConfigurationError("review TLS configuration is unavailable")


__all__ = [
    "REJECT_ENCRYPTED_PRIVATE_KEY_PASSWORD",
    "ReviewTLSIdentity",
    "open_review_tls_identity",
]
