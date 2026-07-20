"""Descriptor-bound deployment gate for Hardcover identity network starts."""

from __future__ import annotations

import errno
import fcntl
import os
import sqlite3
import stat
from dataclasses import dataclass
from pathlib import Path


LOCK_NAME = ".mechanical-librarian-hardcover-identity.lock"
_RUNTIME_BINDING_APPLICATION_IDS = (0x4D4C4201, 0x4D4C4202)
_RUNTIME_BINDING_RECOVERY_KEY = "hardcover-identity-binding-recovery-v1"
_SQLITE_APPLICATION_ID_OFFSET = 68


class HardcoverIdentityGateError(RuntimeError):
    """Public-value-free failure to establish stable deployment identity."""


@dataclass(frozen=True, slots=True)
class _BindingRecovery:
    database_identity: tuple[int, int]
    marker: int
    original: int
    canonical: str


@dataclass(slots=True)
class _DescriptorGateLease:
    directory_descriptor: int
    database_descriptor: int
    lock_descriptor: int
    database_name: str
    directory_identity: tuple[int, int, int, int]
    database_identity: tuple[int, int]
    lock_identity: tuple[int, int]
    canonical_parent: Path
    supplied_parent: Path
    _released: bool = False

    def validate_for_send(self) -> None:
        if self._released:
            raise HardcoverIdentityGateError("Hardcover deployment gate is not held")
        try:
            if self.supplied_parent.resolve(strict=True) != self.canonical_parent:
                raise HardcoverIdentityGateError(
                    "Hardcover deployment identity changed"
                )
            directory = os.fstat(self.directory_descriptor)
            directory_entry = self.canonical_parent.lstat()
            database = os.fstat(self.database_descriptor)
            database_entry = os.stat(
                self.database_name,
                dir_fd=self.directory_descriptor,
                follow_symlinks=False,
            )
            lock = os.fstat(self.lock_descriptor)
            lock_entry = os.stat(
                LOCK_NAME,
                dir_fd=self.directory_descriptor,
                follow_symlinks=False,
            )
        except (OSError, RuntimeError):
            raise HardcoverIdentityGateError(
                "Hardcover deployment identity changed"
            ) from None
        if (
            _directory_identity(directory) != self.directory_identity
            or _directory_identity(directory_entry) != self.directory_identity
            or not _valid_regular(database, self.database_identity, private=False)
            or not _valid_regular(database_entry, self.database_identity, private=False)
            or not _valid_regular(lock, self.lock_identity, private=True)
            or not _valid_regular(lock_entry, self.lock_identity, private=True)
        ):
            raise HardcoverIdentityGateError(
                "Hardcover deployment identity changed"
            )

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        failure: OSError | None = None
        try:
            fcntl.flock(self.lock_descriptor, fcntl.LOCK_UN)
        except OSError as error:
            failure = error
        for descriptor in (
            self.lock_descriptor,
            self.database_descriptor,
            self.directory_descriptor,
        ):
            try:
                os.close(descriptor)
            except OSError as error:
                failure = failure or error
        if failure is not None:
            raise HardcoverIdentityGateError(
                "Hardcover deployment gate could not be released"
            ) from None


class DescriptorHardcoverIdentityGate:
    """Converge canonical parent aliases on one stable flock entry."""

    def __init__(
        self,
        database: Path,
        *,
        expected_database_identity: tuple[int, int] | None = None,
    ) -> None:
        if not isinstance(database, Path) or not database.is_absolute():
            raise ValueError("Hardcover deployment database path is invalid")
        if database.name in {"", ".", ".."} or any(
            part in {".", ".."} for part in database.parts
        ):
            raise ValueError("Hardcover deployment database path is invalid")
        try:
            canonical_parent = database.parent.resolve(strict=True)
        except (OSError, RuntimeError):
            raise HardcoverIdentityGateError(
                "Hardcover deployment state root is unavailable"
            ) from None
        if not canonical_parent.is_absolute():
            raise HardcoverIdentityGateError(
                "Hardcover deployment state root is unavailable"
            )
        try:
            root = canonical_parent.stat()
            root_entry = canonical_parent.lstat()
        except OSError:
            raise HardcoverIdentityGateError(
                "Hardcover deployment state root is unavailable"
            ) from None
        if (
            not stat.S_ISDIR(root.st_mode)
            or not stat.S_ISDIR(root_entry.st_mode)
            or _directory_identity(root) != _directory_identity(root_entry)
            or root.st_uid != os.geteuid()
            or stat.S_IMODE(root.st_mode) & 0o077
        ):
            raise HardcoverIdentityGateError(
                "Hardcover deployment state root is invalid"
            )
        if expected_database_identity is not None and (
            type(expected_database_identity) is not tuple
            or len(expected_database_identity) != 2
            or any(type(value) is not int or value < 0 for value in expected_database_identity)
        ):
            raise ValueError("Hardcover deployment database identity is invalid")
        try:
            database_entry = database.stat(follow_symlinks=False)
        except FileNotFoundError:
            database_identity = None
        except OSError:
            raise HardcoverIdentityGateError(
                "Hardcover deployment database identity is unavailable"
            ) from None
        else:
            database_identity = (database_entry.st_dev, database_entry.st_ino)
            if not _valid_regular(
                database_entry, database_identity, private=False
            ):
                raise HardcoverIdentityGateError(
                    "Hardcover deployment database identity is invalid"
                )
        if expected_database_identity is not None and (
            database_identity != expected_database_identity
        ):
            raise HardcoverIdentityGateError(
                "Hardcover deployment database identity changed"
            )
        self._parent = canonical_parent
        self._supplied_parent = database.parent
        self._database_name = database.name
        self._expected_database_identity = (
            expected_database_identity
            if expected_database_identity is not None
            else database_identity
        )

    def bind_connection(self, connection: sqlite3.Connection) -> None:
        """Prove the writable SQLite connection owns the gate's exact inode.

        The normal deployment flock serializes the complete marker transaction.
        A private durable recovery record preserves every original application
        ID across interruption and is removed atomically with restoration.
        """

        if type(connection) is not sqlite3.Connection or connection.in_transaction:
            raise HardcoverIdentityGateError(
                "Hardcover database connection is not bindable"
            )
        lease = self.try_acquire()
        if lease is None:
            raise HardcoverIdentityGateError("Hardcover deployment gate is busy")
        marked = False
        original_application_id = 0
        failure: BaseException | None = None
        try:
            lease.validate_for_send()
            recovery = self._load_recovery(
                connection, lease.database_identity
            )
            if recovery is not None:
                self._restore_binding(connection, recovery)
                if (
                    self._read_application_id(lease.database_descriptor)
                    != recovery.original
                ):
                    raise HardcoverIdentityGateError(
                        "Hardcover database binding recovery was not synchronized"
                    )
                original_application_id = recovery.original
            else:
                original_application_id = self._connection_application_id(connection)
            marker = (
                _RUNTIME_BINDING_APPLICATION_IDS[1]
                if original_application_id == _RUNTIME_BINDING_APPLICATION_IDS[0]
                else _RUNTIME_BINDING_APPLICATION_IDS[0]
            )
            recovery = self._publish_binding(
                connection,
                lease.database_identity,
                marker,
                original_application_id,
            )
            marked = True
            if (
                self._read_application_id(lease.database_descriptor)
                != marker
            ):
                raise HardcoverIdentityGateError(
                    "Hardcover database connection identity changed"
                )
            self._restore_binding(connection, recovery)
            marked = False
            if (
                self._read_application_id(lease.database_descriptor)
                != original_application_id
            ):
                raise HardcoverIdentityGateError(
                    "Hardcover database binding marker was not removed"
                )
            lease.validate_for_send()
        except HardcoverIdentityGateError as error:
            failure = error
            raise
        except (OSError, sqlite3.Error, RuntimeError):
            translated = HardcoverIdentityGateError(
                "Hardcover database connection could not be bound"
            )
            failure = translated
            raise translated from None
        except BaseException as error:
            failure = error
            raise
        finally:
            cleanup_failure: HardcoverIdentityGateError | None = None
            if marked:
                try:
                    recovery = self._load_recovery(
                        connection, lease.database_identity
                    )
                    if recovery is None:
                        raise HardcoverIdentityGateError(
                            "Hardcover database binding recovery is unavailable"
                        )
                    self._restore_binding(connection, recovery)
                    if (
                        self._read_application_id(lease.database_descriptor)
                        != original_application_id
                    ):
                        raise HardcoverIdentityGateError(
                            "Hardcover database binding marker cleanup failed"
                        )
                except (HardcoverIdentityGateError, sqlite3.Error, RuntimeError):
                    cleanup_failure = HardcoverIdentityGateError(
                        "Hardcover database binding marker cleanup failed"
                    )
            try:
                lease.validate_for_send()
            except HardcoverIdentityGateError:
                cleanup_failure = cleanup_failure or HardcoverIdentityGateError(
                    "Hardcover database binding cleanup validation failed"
                )
            try:
                lease.release()
            except HardcoverIdentityGateError:
                cleanup_failure = cleanup_failure or HardcoverIdentityGateError(
                    "Hardcover database binding lease release failed"
                )
            if cleanup_failure is not None:
                if failure is not None:
                    failure.add_note(str(cleanup_failure))
                else:
                    raise cleanup_failure

    @classmethod
    def _publish_binding(
        cls,
        connection: sqlite3.Connection,
        database_identity: tuple[int, int],
        marker: int,
        original: int,
    ) -> _BindingRecovery:
        canonical = cls._encode_recovery(database_identity, marker, original)
        connection.execute("BEGIN IMMEDIATE")
        try:
            if cls._recovery_value(connection) is not None:
                raise HardcoverIdentityGateError(
                    "Hardcover database binding recovery already exists"
                )
            if cls._connection_application_id(connection) != original:
                raise HardcoverIdentityGateError(
                    "Hardcover database binding marker changed"
                )
            connection.execute(
                "INSERT INTO instance_metadata(key,value) VALUES(?,?)",
                (_RUNTIME_BINDING_RECOVERY_KEY, canonical),
            )
            connection.execute(f"PRAGMA application_id={marker}")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        cls._checkpoint(connection)
        return _BindingRecovery(database_identity, marker, original, canonical)

    @classmethod
    def _restore_binding(
        cls,
        connection: sqlite3.Connection,
        recovery: _BindingRecovery,
    ) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if cls._recovery_value(connection) != recovery.canonical:
                raise HardcoverIdentityGateError(
                    "Hardcover database binding recovery changed"
                )
            if cls._connection_application_id(connection) != recovery.marker:
                raise HardcoverIdentityGateError(
                    "Hardcover database binding marker changed"
                )
            connection.execute(f"PRAGMA application_id={recovery.original}")
            changed = connection.execute(
                "DELETE FROM instance_metadata WHERE key=? AND value=?",
                (_RUNTIME_BINDING_RECOVERY_KEY, recovery.canonical),
            ).rowcount
            if changed != 1:
                raise HardcoverIdentityGateError(
                    "Hardcover database binding recovery changed"
                )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        cls._checkpoint(connection)

    @staticmethod
    def _checkpoint(connection: sqlite3.Connection) -> None:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or len(checkpoint) != 3 or checkpoint[0] != 0:
            raise HardcoverIdentityGateError(
                "Hardcover database binding marker could not be synchronized"
            )

    @classmethod
    def _load_recovery(
        cls,
        connection: sqlite3.Connection,
        database_identity: tuple[int, int],
    ) -> _BindingRecovery | None:
        value = cls._recovery_value(connection)
        if value is None:
            return None
        parts = value.split(":")
        if len(parts) != 5 or parts[0] != "v1":
            raise HardcoverIdentityGateError(
                "Hardcover database binding recovery is invalid"
            )
        values = parts[1:]
        if any(
            not item
            or len(item) > 20
            or not item.isascii()
            or not item.isdecimal()
            for item in values
        ):
            raise HardcoverIdentityGateError(
                "Hardcover database binding recovery is invalid"
            )
        device, inode, marker, original = (int(item) for item in values)
        if (
            any(
                str(number) != item
                for number, item in zip(
                    (device, inode, marker, original), values, strict=True
                )
            )
            or device < 0
            or inode < 1
            or marker not in _RUNTIME_BINDING_APPLICATION_IDS
            or not 0 <= original <= 2_147_483_647
            or (device, inode) != database_identity
        ):
            raise HardcoverIdentityGateError(
                "Hardcover database binding recovery is invalid"
            )
        recovery = _BindingRecovery(
            (device, inode), marker, original, value
        )
        if cls._connection_application_id(connection) != marker:
            raise HardcoverIdentityGateError(
                "Hardcover database binding recovery is inconsistent"
            )
        return recovery

    @staticmethod
    def _recovery_value(connection: sqlite3.Connection) -> str | None:
        row = connection.execute(
            "SELECT value FROM instance_metadata WHERE key=?",
            (_RUNTIME_BINDING_RECOVERY_KEY,),
        ).fetchone()
        if row is None:
            return None
        if type(row) is not tuple or len(row) != 1 or type(row[0]) is not str:
            raise HardcoverIdentityGateError(
                "Hardcover database binding recovery is invalid"
            )
        return row[0]

    @staticmethod
    def _connection_application_id(connection: sqlite3.Connection) -> int:
        current = connection.execute("PRAGMA application_id").fetchone()
        if (
            type(current) is not tuple
            or len(current) != 1
            or type(current[0]) is not int
            or not 0 <= current[0] <= 2_147_483_647
        ):
            raise HardcoverIdentityGateError(
                "Hardcover database binding marker is invalid"
            )
        return current[0]

    @staticmethod
    def _encode_recovery(
        database_identity: tuple[int, int], marker: int, original: int
    ) -> str:
        device, inode = database_identity
        if (
            type(device) is not int
            or device < 0
            or type(inode) is not int
            or inode < 1
            or marker not in _RUNTIME_BINDING_APPLICATION_IDS
            or type(original) is not int
            or not 0 <= original <= 2_147_483_647
        ):
            raise HardcoverIdentityGateError(
                "Hardcover database binding recovery is invalid"
            )
        return f"v1:{device}:{inode}:{marker}:{original}"

    @staticmethod
    def _read_application_id(descriptor: int) -> int:
        value = os.pread(descriptor, 4, _SQLITE_APPLICATION_ID_OFFSET)
        if len(value) != 4:
            raise HardcoverIdentityGateError(
                "Hardcover database binding marker is unavailable"
            )
        return int.from_bytes(value, "big")

    def try_acquire(self) -> _DescriptorGateLease | None:
        directory_descriptor: int | None = None
        database_descriptor: int | None = None
        lock_descriptor: int | None = None
        try:
            nofollow = getattr(os, "O_NOFOLLOW", None)
            cloexec = getattr(os, "O_CLOEXEC", None)
            directory_flag = getattr(os, "O_DIRECTORY", None)
            if nofollow is None or cloexec is None or directory_flag is None:
                raise HardcoverIdentityGateError(
                    "Hardcover deployment gate is unsupported"
                )
            if self._supplied_parent.resolve(strict=True) != self._parent:
                raise HardcoverIdentityGateError(
                    "Hardcover deployment state root changed"
                )
            directory_descriptor = os.open(
                self._parent, os.O_RDONLY | directory_flag | nofollow | cloexec
            )
            directory = os.fstat(directory_descriptor)
            parent_entry = self._parent.lstat()
            directory_identity = _directory_identity(directory)
            if (
                not stat.S_ISDIR(directory.st_mode)
                or not stat.S_ISDIR(parent_entry.st_mode)
                or directory_identity != _directory_identity(parent_entry)
                or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) & 0o077
            ):
                raise HardcoverIdentityGateError(
                    "Hardcover deployment state root is invalid"
                )
            database_descriptor = os.open(
                self._database_name,
                os.O_RDONLY | nofollow | cloexec | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory_descriptor,
            )
            database = os.fstat(database_descriptor)
            database_identity = (database.st_dev, database.st_ino)
            if (
                not _valid_regular(database, database_identity, private=False)
                or (
                    self._expected_database_identity is not None
                    and database_identity != self._expected_database_identity
                )
            ):
                raise HardcoverIdentityGateError(
                    "Hardcover deployment database identity is invalid"
                )
            lock_descriptor = os.open(
                LOCK_NAME,
                os.O_RDWR | os.O_CREAT | nofollow | cloexec | getattr(os, "O_NONBLOCK", 0),
                0o600,
                dir_fd=directory_descriptor,
            )
            lock = os.fstat(lock_descriptor)
            lock_identity = (lock.st_dev, lock.st_ino)
            if not _valid_regular(lock, lock_identity, private=True):
                raise HardcoverIdentityGateError(
                    "Hardcover deployment lock identity is invalid"
                )
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in {errno.EACCES, errno.EAGAIN}:
                    _close(lock_descriptor, database_descriptor, directory_descriptor)
                    return None
                raise
            lease = _DescriptorGateLease(
                directory_descriptor,
                database_descriptor,
                lock_descriptor,
                self._database_name,
                directory_identity,
                database_identity,
                lock_identity,
                self._parent,
                self._supplied_parent,
            )
            lease.validate_for_send()
            return lease
        except HardcoverIdentityGateError:
            _close(lock_descriptor, database_descriptor, directory_descriptor)
            raise
        except (OSError, RuntimeError):
            _close(lock_descriptor, database_descriptor, directory_descriptor)
            raise HardcoverIdentityGateError(
                "Hardcover deployment gate is unavailable"
            ) from None


def _directory_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return (
        status.st_dev,
        status.st_ino,
        status.st_uid,
        stat.S_IMODE(status.st_mode),
    )


def _valid_regular(
    status: os.stat_result, identity: tuple[int, int], *, private: bool
) -> bool:
    return (
        stat.S_ISREG(status.st_mode)
        and status.st_nlink == 1
        and status.st_uid == os.geteuid()
        and (status.st_dev, status.st_ino) == identity
        and (not private or stat.S_IMODE(status.st_mode) == 0o600)
    )


def _close(*descriptors: int | None) -> None:
    for descriptor in descriptors:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


__all__ = [
    "DescriptorHardcoverIdentityGate",
    "HardcoverIdentityGateError",
    "LOCK_NAME",
]
