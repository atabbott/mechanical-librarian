"""The deployment-held token that admits the first owner claim (ADR-0086)."""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path

from mechanical_librarian.domain.setup_state import claim_token_digest

SETUP_CLAIM_TOKEN_NAME = "setup-claim-token"


class SetupClaimTokenFileError(RuntimeError):
    """The claim token could not be written; the message carries no value."""


def mint_setup_claim_token(
    private_root: Path, commit: Callable[[str], object], *, announce_fd: int = 1
) -> None:
    """Commit a fresh digest, then write the token file, then announce it once.

    The digest commits first, so an earlier token stops working before the new
    one exists anywhere. The announcement is a raw write to the container's
    output and never passes through logging, whose handlers and redaction are
    not a place for a live credential.
    """
    token = secrets.token_urlsafe(32)
    commit(claim_token_digest(token))
    _write_token_file(private_root / SETUP_CLAIM_TOKEN_NAME, token)
    message = (
        f"Mechanical Librarian setup claim token: {token} "
        f"(also in private/{SETUP_CLAIM_TOKEN_NAME} on the data volume; "
        "a restart replaces it)\n"
    ).encode("ascii")
    try:
        while message:
            message = message[os.write(announce_fd, message) :]
    except OSError:
        # The owner-only file above is the durable channel; a closed or
        # failing output stream must not stop setup from serving.
        pass


def retire_setup_claim_token(private_root: Path) -> None:
    """Remove the token file once it can no longer claim anything; best effort."""
    try:
        os.unlink(private_root / SETUP_CLAIM_TOKEN_NAME)
    except OSError:
        pass


def _write_token_file(path: Path, token: str) -> None:
    try:
        descriptor = os.open(
            path,
            # O_NONBLOCK keeps a planted FIFO from holding startup open.
            os.O_WRONLY
            | os.O_CREAT
            | os.O_TRUNC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK
            | os.O_CLOEXEC,
            0o600,
        )
    except OSError:
        raise SetupClaimTokenFileError("setup claim token is unavailable") from None
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_uid != os.geteuid():
            raise SetupClaimTokenFileError("setup claim token is unavailable")
        # An existing file keeps its mode through O_TRUNC; restate owner-only.
        os.fchmod(descriptor, 0o600)
        content = f"{token}\n".encode("ascii")
        while content:
            content = content[os.write(descriptor, content) :]
        os.fsync(descriptor)
    except OSError:
        raise SetupClaimTokenFileError("setup claim token is unavailable") from None
    finally:
        os.close(descriptor)


__all__ = [
    "SETUP_CLAIM_TOKEN_NAME",
    "SetupClaimTokenFileError",
    "mint_setup_claim_token",
    "retire_setup_claim_token",
]
