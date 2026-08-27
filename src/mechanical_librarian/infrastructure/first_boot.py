"""Fail-closed process-entry selection for file, normal-store, and setup mode."""

from __future__ import annotations

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
    if not entries:
        return FirstBootMode.INITIALIZE_SETUP
    if database_name not in entries:
        raise FirstBootError("first boot state is unavailable")
    # The private directory holds the dedicated session database (#912), which
    # setup's first sign-in legitimately creates before setup completes; it is
    # not evidence of a completed runtime. Any other private content is
    # runtime state and keeps the volume failing closed as before.
    setup_entries = set(entries)
    if "private" in entries:
        try:
            private_entries = {
                entry.name for entry in (data_path / "private").iterdir()
            }
        except OSError as error:
            raise FirstBootError("first boot state is unavailable") from error
        if private_entries <= {"review-sessions.sqlite"}:
            setup_entries.discard("private")
    try:
        if complete_store():
            return FirstBootMode.NORMAL
        if setup_entries == {database_name} and uninitialized_setup():
            return FirstBootMode.INITIALIZE_SETUP
        if setup_entries == {database_name} and resumable_setup():
            return FirstBootMode.RESUME_SETUP
    except Exception as error:
        raise FirstBootError("first boot state is unavailable") from error
    raise FirstBootError("first boot state is unavailable")


__all__ = ["FirstBootError", "FirstBootMode", "select_first_boot_mode"]
