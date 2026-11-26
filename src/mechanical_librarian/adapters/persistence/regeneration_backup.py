"""Whether `regenerate-derived`'s restore point is still kept (ADR-0108).

The command keeps its verified copy in `regeneration-backup` beside the
database. While that folder holds anything, the copy and the artifact store
are a pair: the artifact sweep keeps every file and retained-cover removals
wait, for every caller. A leaf module, so the command, the sweep's reference
check and the removal queue read one rule.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding

BACKUP_DIRECTORY = "regeneration-backup"


def backup_held(parent_descriptor: int) -> bool:
    """Whether the database's folder keeps a restore point: the folder holds
    an entry, or is anything this rule cannot read as an empty folder."""
    try:
        entry = os.stat(
            BACKUP_DIRECTORY, dir_fd=parent_descriptor, follow_symlinks=False
        )
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if not stat.S_ISDIR(entry.st_mode):
        return True
    try:
        directory = os.open(
            BACKUP_DIRECTORY,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
    except OSError:
        return True
    try:
        return bool(os.listdir(directory))
    except OSError:
        return True
    finally:
        os.close(directory)


def database_backup_held(
    connection: sqlite3.Connection, binding: _LinuxDatabaseBinding | None
) -> bool:
    """`backup_held` for the open database: through its bound folder, or,
    with no private binding, the folder SQLite names for the main file."""
    if binding is not None:
        return backup_held(binding.parent_descriptor)
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        return False
    try:
        parent = os.open(Path(row[2]).parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return True
    try:
        return backup_held(parent)
    finally:
        os.close(parent)


__all__ = ["BACKUP_DIRECTORY", "backup_held", "database_backup_held"]
