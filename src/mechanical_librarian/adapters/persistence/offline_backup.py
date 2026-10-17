"""Owner-only verified backups and source rechecks for offline database plans.

The qualification-state bridge and the schema adoption both copy their source
to an owner-only backup beside it, verify the copy byte for byte, and recheck
the source and backup identities before and after they write. `subject` names
the plan in every refusal, and `recovery` is the error a plan raises when a
failed backup could not be cleaned up.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path

from mechanical_librarian.adapters.persistence.binding import (
    _LinuxDatabaseBinding,
    _reject_database_sidecars,
    _require_named_identity,
)
from mechanical_librarian.adapters.persistence.schema import _expected_schema_objects
from mechanical_librarian.application.metadata_report import MetadataReportError

_CHUNK_BYTES = 1024 * 1024


def _schema_objects_digest(connection: sqlite3.Connection) -> str:
    observed = connection.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()
    return hashlib.sha256(
        json.dumps(observed, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


def _expected_schema_objects_digest() -> str:
    return hashlib.sha256(
        json.dumps(
            _expected_schema_objects(), ensure_ascii=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _descriptor_digest(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(descriptor, _CHUNK_BYTES, offset)
        if not block:
            return digest.hexdigest()
        digest.update(block)
        offset += len(block)


def _copy_verified_backup(
    source_descriptor: int,
    parent_descriptor: int,
    backup_name: str,
    *,
    subject: str,
    recovery: type[MetadataReportError],
) -> tuple[tuple[int, int], str]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    backup_descriptor = -1
    created_identity: tuple[int, int] | None = None
    succeeded = False
    try:
        backup_descriptor = os.open(backup_name, flags, 0o600, dir_fd=parent_descriptor)
        information = os.fstat(backup_descriptor)
        created_identity = (information.st_dev, information.st_ino)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
        ):
            raise MetadataReportError(f"{subject} backup is unavailable")
        offset = 0
        while True:
            block = os.pread(source_descriptor, _CHUNK_BYTES, offset)
            if not block:
                break
            view = memoryview(block)
            while view:
                written = os.write(backup_descriptor, view)
                if written <= 0:
                    raise OSError("backup write made no progress")
                view = view[written:]
            offset += len(block)
        os.fsync(backup_descriptor)
        source_digest = _descriptor_digest(source_descriptor)
        if source_digest != _descriptor_digest(backup_descriptor):
            raise MetadataReportError(f"{subject} backup is unavailable")
        os.fsync(parent_descriptor)
        succeeded = True
        return created_identity, source_digest
    except FileExistsError as error:
        raise MetadataReportError(f"{subject} backup already exists") from error
    except MetadataReportError:
        raise
    except OSError as error:
        raise MetadataReportError(f"{subject} backup is unavailable") from error
    finally:
        cleanup_failure: BaseException | None = None
        if backup_descriptor >= 0:
            try:
                os.close(backup_descriptor)
            except OSError as error:
                cleanup_failure = error
        if created_identity is not None and not succeeded:
            try:
                information = os.stat(
                    backup_name, dir_fd=parent_descriptor, follow_symlinks=False
                )
                if (information.st_dev, information.st_ino) != created_identity:
                    raise MetadataReportError(f"{subject} backup identity changed")
                os.unlink(backup_name, dir_fd=parent_descriptor)
                os.fsync(parent_descriptor)
            except (MetadataReportError, OSError) as error:
                cleanup_failure = cleanup_failure or error
        if cleanup_failure is not None:
            raise recovery from cleanup_failure


def _require_backup_current(
    parent_descriptor: int,
    backup_name: str,
    expected_digest: str,
    expected_identity: tuple[int, int] | None = None,
    *,
    subject: str,
) -> tuple[int, int]:
    descriptor = -1
    try:
        descriptor = os.open(
            backup_name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        information = os.fstat(descriptor)
        observed_identity = (information.st_dev, information.st_ino)
        if (
            not stat.S_ISREG(information.st_mode)
            or information.st_uid != os.geteuid()
            or information.st_mode & 0o077
            or _descriptor_digest(descriptor) != expected_digest
            or (
                expected_identity is not None and observed_identity != expected_identity
            )
        ):
            raise MetadataReportError(f"{subject} backup identity changed")
        return observed_identity
    except OSError as error:
        raise MetadataReportError(f"{subject} backup identity changed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_source_current(
    database: Path,
    source_identity: tuple[int, int],
    parent_descriptor: int,
    binding: _LinuxDatabaseBinding | None,
    *,
    subject: str,
) -> None:
    if binding is not None:
        binding.require_current()
        return
    current_parent = -1
    try:
        current_parent = os.open(
            database.parent,
            os.O_RDONLY
            | os.O_DIRECTORY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        expected_parent = os.fstat(parent_descriptor)
        observed_parent = os.fstat(current_parent)
        if (observed_parent.st_dev, observed_parent.st_ino) != (
            expected_parent.st_dev,
            expected_parent.st_ino,
        ):
            raise MetadataReportError(f"{subject} source pathname changed")
        _require_named_identity(current_parent, database.name, source_identity)
        _reject_database_sidecars(database)
    except OSError as error:
        raise MetadataReportError(f"{subject} source pathname changed") from error
    finally:
        if current_parent >= 0:
            os.close(current_parent)
