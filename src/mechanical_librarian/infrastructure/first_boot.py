"""Fail-closed process-entry selection for file, normal-store, and setup mode."""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Callable
from enum import Enum
from pathlib import Path


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
        names = os.listdir(descriptor)
        if len(names) > 66 or any(
            name not in {"auth", "jobs"}
            and re.fullmatch(r"reviewer-auth-[a-z0-9_]{8}", name) is None
            for name in names
        ):
            raise FirstBootError("first boot state is unavailable")
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
                if os.listdir(child):
                    empty = False
            finally:
                os.close(child)
        return empty and set(names) <= {"auth", "jobs"}
    finally:
        os.close(descriptor)


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
    # not evidence of a completed runtime. Any other private content is
    # runtime state and keeps the volume failing closed as before.
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
        if private_entries <= {
            "review-sessions.sqlite",
            "review-sessions.sqlite-journal",
        } and (
            "review-sessions.sqlite-journal" not in private_entries
            or "review-sessions.sqlite" in private_entries
        ):
            setup_entries.discard("private")
    try:
        if setup_entries == {database_name} and uninitialized_setup():
            return FirstBootMode.INITIALIZE_SETUP
        if setup_entries == {database_name} and resumable_setup():
            return FirstBootMode.RESUME_SETUP
    except Exception as error:
        raise FirstBootError("first boot state is unavailable") from error
    raise FirstBootError("first boot state is unavailable")


__all__ = ["FirstBootError", "FirstBootMode", "select_first_boot_mode"]
