"""Fail-closed process-entry selection for file, normal-store, and setup mode."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from mechanical_librarian.infrastructure.setup_claim_token import (
    SETUP_CLAIM_TOKEN_NAME,
)


class FirstBootError(ValueError):
    """The data volume is neither a supported normal store nor first boot."""


class FirstBootMode(str, Enum):
    FILE = "file"
    NORMAL = "normal"
    INITIALIZE_SETUP = "initialize_setup"
    RESUME_SETUP = "resume_setup"


def _reviewer_setup_entries(data_path: Path) -> bool:
    """Validate the fixed reviewer directories; report whether all are empty."""
    root = data_path / "reviewer"
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        information = os.fstat(descriptor)
        if (
            information.st_uid != os.geteuid()
            or stat.S_IMODE(information.st_mode) != 0o700
        ):
            raise FirstBootError("first boot state is unavailable")
        names: list[str] = []
        with os.scandir(descriptor) as entries:
            for entry in entries:
                if len(names) == 66 or (
                    entry.name not in {"auth", "jobs"}
                    and re.fullmatch(r"reviewer-auth-[a-z0-9_]{8}", entry.name) is None
                ):
                    raise FirstBootError("first boot state is unavailable")
                names.append(entry.name)
        empty = True
        for name in names:
            child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            try:
                information = os.fstat(child)
                if (
                    information.st_uid != os.geteuid()
                    or stat.S_IMODE(information.st_mode) != 0o700
                ):
                    raise FirstBootError("first boot state is unavailable")
                with os.scandir(child) as entries:
                    if next(entries, None) is not None:
                        empty = False
            finally:
                os.close(child)
        return empty and set(names) <= {"auth", "jobs"}
    finally:
        os.close(descriptor)


def _private_file(path: Path) -> bool:
    try:
        information = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(information.st_mode)
        and stat.S_IMODE(information.st_mode) == 0o600
        and information.st_uid == os.geteuid()
    )


# The provider answers an owner may carry across a clean rebuild (ADR-0101).
# The store is `journal_mode=DELETE`, so only a rollback journal can sit
# beside it.
_KEPT_ANSWERS = "provider-responses.sqlite"
_KEPT_ANSWERS_JOURNAL = _KEPT_ANSWERS + "-journal"


def _kept_answers(private: Path, names: set[str]) -> bool:
    """Whether the kept provider answers among `names` are the owner's files.

    First boot never opens the store. Its own admission at the first
    preparation refuses a damaged or foreign file without changing it and asks
    every source live, so a bad kept file cannot stop setup.
    """
    kept = names & {_KEPT_ANSWERS, _KEPT_ANSWERS_JOURNAL}
    return (not kept or _KEPT_ANSWERS in kept) and all(
        _private_file(private / name) for name in kept
    )


def _only_kept_answers(private: Path) -> bool:
    """Whether `private/` is the owner's directory holding only kept answers."""
    try:
        information = private.lstat()
        names = {entry.name for entry in private.iterdir()}
    except OSError:
        return False
    return (
        stat.S_ISDIR(information.st_mode)
        and stat.S_IMODE(information.st_mode) == 0o700
        and information.st_uid == os.geteuid()
        and _KEPT_ANSWERS in names
        and names <= {_KEPT_ANSWERS, _KEPT_ANSWERS_JOURNAL}
        and _kept_answers(private, names)
    )


def select_first_boot_mode(
    data_path: Path,
    *,
    config_name: str = "config.json",
    database_name: str = "application.sqlite3",
    complete_store: Callable[[], bool],
    resumable_setup: Callable[[], bool],
    uninitialized_setup: Callable[[], bool] = lambda: False,
) -> FirstBootMode:
    """Classify the exact process-entry volume before any runtime artefact exists."""

    config_path = data_path / config_name
    if config_path.exists() or config_path.is_symlink():
        return FirstBootMode.FILE
    try:
        entries = {entry.name for entry in data_path.iterdir()}
    except OSError as error:
        raise FirstBootError("first boot state is unavailable") from error
    reviewer_empty = False
    if "reviewer" in entries:
        try:
            reviewer_empty = _reviewer_setup_entries(data_path)
        except OSError as error:
            raise FirstBootError("first boot state is unavailable") from error
    if not entries or (entries == {"reviewer"} and reviewer_empty):
        return FirstBootMode.INITIALIZE_SETUP
    # A clean rebuild may keep the provider answers and nothing else.
    if (
        entries in ({"private"}, {"private", "reviewer"})
        and ("reviewer" not in entries or reviewer_empty)
        and _only_kept_answers(data_path / "private")
    ):
        return FirstBootMode.INITIALIZE_SETUP
    if database_name not in entries:
        raise FirstBootError("first boot state is unavailable")
    try:
        if complete_store():
            return FirstBootMode.NORMAL
        # Root admission may remove verified retained aliases. Inspect what
        # actually remains; never subtract filenames presumed to be recovered.
        entries = {entry.name for entry in data_path.iterdir()}
    except Exception as error:
        raise FirstBootError("first boot state is unavailable") from error
    # The private directory holds the dedicated session database (#912), which
    # setup's first sign-in legitimately creates before setup completes; it is
    # not evidence of a completed runtime. Any other private content, apart
    # from kept provider answers, is runtime state and keeps the volume
    # failing closed as before.
    setup_entries = set(entries)
    setup_entries.discard("reviewer")
    if "private" in entries:
        try:
            private_entries = {
                entry.name for entry in (data_path / "private").iterdir()
            }
        except OSError as error:
            raise FirstBootError("first boot state is unavailable") from error
        # The rollback journal belongs to the session database, not to a
        # completed runtime: a sign-in interrupted mid-write leaves one behind,
        # and the next open rolls it back. Refusing it would turn a crash
        # during setup into a boot that will not resume. Only `-journal` is
        # admitted, because this store is `journal_mode=DELETE` and no WAL is
        # adopted anywhere (ADR-0060).
        # The setup claim token (ADR-0086) and the provider answers kept
        # across a clean rebuild (ADR-0101) are admitted only as the owner's
        # regular 0600 files; a link, directory or wider mode is not one.
        if private_entries <= {
            "review-sessions.sqlite",
            "review-sessions.sqlite-journal",
            SETUP_CLAIM_TOKEN_NAME,
            _KEPT_ANSWERS,
            _KEPT_ANSWERS_JOURNAL,
        } and (
            "review-sessions.sqlite-journal" not in private_entries
            or "review-sessions.sqlite" in private_entries
        ):
            if (
                SETUP_CLAIM_TOKEN_NAME not in private_entries
                or _private_file(data_path / "private" / SETUP_CLAIM_TOKEN_NAME)
            ) and _kept_answers(data_path / "private", private_entries):
                setup_entries.discard("private")
    try:
        if (
            setup_entries == {database_name}
            and ("reviewer" not in entries or reviewer_empty)
            and uninitialized_setup()
        ):
            return FirstBootMode.INITIALIZE_SETUP
        if setup_entries == {database_name} and resumable_setup():
            return FirstBootMode.RESUME_SETUP
    except Exception as error:
        raise FirstBootError("first boot state is unavailable") from error
    raise FirstBootError("first boot state is unavailable")


__all__ = ["FirstBootError", "FirstBootMode", "select_first_boot_mode"]
