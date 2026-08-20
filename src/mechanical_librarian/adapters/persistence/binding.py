"""Filesystem authority for the one database file a connection is bound to.

This is a file-authority spine, not a transaction or schema implementation: it
gives one connection over one inode, plus the revalidation that proves the
pathname still resolves to that inode.  A root binding may also attest that this
exact inode passed the installed schema admission check, so derived connections
do not repeat that database-wide check.  It imports nothing from this package,
so every other persistence module may depend on it.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from mechanical_librarian.application.metadata_report import MetadataReportError

if sys.platform.startswith("linux"):
    import fcntl
else:
    fcntl = None


class _LinuxDirectoryChain:
    def __init__(
        self,
        names: tuple[str, ...],
        descriptors: tuple[int, ...],
        identities: tuple[tuple[int, int], ...],
    ) -> None:
        self.names = names
        self.descriptors = descriptors
        self.identities = identities
        self.closed = False

    @classmethod
    def open(cls, path: Path) -> "_LinuxDirectoryChain":
        absolute = path.absolute()
        if not absolute.is_absolute():
            raise MetadataReportError("database parent path is invalid")
        names = tuple(absolute.parts[1:])
        if any(name in ("", ".", "..") for name in names):
            raise MetadataReportError("database parent path is invalid")
        descriptors: list[int] = []
        identities: list[tuple[int, int]] = []
        try:
            descriptor = os.open(
                "/",
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            descriptors.append(descriptor)
            information = os.fstat(descriptor)
            identities.append((information.st_dev, information.st_ino))
            for name in names:
                descriptor = os.open(
                    name,
                    os.O_RDONLY
                    | os.O_DIRECTORY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptors[-1],
                )
                descriptors.append(descriptor)
                information = os.fstat(descriptor)
                if not stat.S_ISDIR(information.st_mode):
                    raise MetadataReportError("database parent path is invalid")
                _require_protected_directory_entry(
                    os.fstat(descriptors[-2]), information
                )
                identities.append((information.st_dev, information.st_ino))
            return cls(names, tuple(descriptors), tuple(identities))
        except MetadataReportError:
            raise
        except (OSError, ValueError) as error:
            raise MetadataReportError("database parent path is unavailable") from error
        finally:
            if len(identities) != len(descriptors):
                for descriptor in reversed(descriptors):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass

    @property
    def parent_descriptor(self) -> int:
        return self.descriptors[-1]

    def open_current_parent(self) -> int:
        if self.closed:
            raise MetadataReportError("database parent path is unavailable")
        current = -1
        try:
            current = os.open(
                "/",
                os.O_RDONLY
                | os.O_DIRECTORY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            root = os.fstat(current)
            if (root.st_dev, root.st_ino) != self.identities[0]:
                raise MetadataReportError("database parent path changed")
            for index, name in enumerate(self.names):
                descriptor = -1
                try:
                    descriptor = os.open(
                        name,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=current,
                    )
                    information = os.fstat(descriptor)
                    if (
                        not stat.S_ISDIR(information.st_mode)
                        or (information.st_dev, information.st_ino)
                        != self.identities[index + 1]
                    ):
                        raise MetadataReportError("database parent path changed")
                    _require_protected_directory_entry(os.fstat(current), information)
                    os.close(current)
                    current = descriptor
                    descriptor = -1
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
            result = current
            current = -1
            return result
        except MetadataReportError:
            raise
        except OSError as error:
            raise MetadataReportError("database parent path changed") from error
        finally:
            if current >= 0:
                os.close(current)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failure: OSError | None = None
        for descriptor in reversed(self.descriptors):
            try:
                os.close(descriptor)
            except OSError as error:
                failure = failure or error
        if failure is not None:
            raise MetadataReportError(
                "database parent binding cleanup failed"
            ) from failure


class _LinuxDatabaseBinding:
    def __init__(
        self,
        configured_name: str,
        descriptor: int,
        directory_chain: _LinuxDirectoryChain,
        alias_name: str,
        expected_identity: tuple[int, int],
        owns_lock: bool,
        authority: "_LinuxDatabaseBinding | None",
    ) -> None:
        self.configured_name = configured_name
        self.descriptor = descriptor
        self.directory_chain = directory_chain
        self.parent_descriptor = directory_chain.parent_descriptor
        self.alias_name = alias_name
        self.expected_identity = expected_identity
        self.owns_lock = owns_lock
        self.authority = authority
        self.alias_path = Path(f"/proc/self/fd/{self.parent_descriptor}/{alias_name}")
        self.closed = False
        self._schema_validated = False

    @classmethod
    def create(
        cls,
        path: Path,
        descriptor: int,
        authority: object | None = None,
        *,
        wait_for_authority: bool = True,
    ) -> "_LinuxDatabaseBinding":
        if fcntl is None:
            raise MetadataReportError("database private binding is unavailable")
        absolute = path.absolute()
        if not absolute.name:
            raise MetadataReportError("database path is invalid")
        directory_chain: _LinuxDirectoryChain | None = None
        alias_name: str | None = None
        locked = False
        succeeded = False
        try:
            directory_chain = _LinuxDirectoryChain.open(absolute.parent)
            parent_descriptor = directory_chain.parent_descriptor
            _require_owner_only_database_parent(os.fstat(parent_descriptor))
            expected = os.fstat(descriptor)
            expected_identity = (expected.st_dev, expected.st_ino)
            if authority is None:
                operation = fcntl.LOCK_EX
                if not wait_for_authority:
                    operation |= fcntl.LOCK_NB
                fcntl.flock(parent_descriptor, operation)
                locked = True
            elif type(authority) is not cls:
                raise MetadataReportError("database authority changed")
            else:
                authority.require_current()
                if (
                    authority.closed
                    or authority.configured_name != absolute.name
                    or authority.expected_identity != expected_identity
                    or authority.directory_chain.identities[-1]
                    != directory_chain.identities[-1]
                ):
                    raise MetadataReportError("database authority changed")
            _require_named_identity(parent_descriptor, absolute.name, expected_identity)
            _reject_database_sidecars_in_directory(parent_descriptor, absolute.name)
            for _ in range(8):
                candidate = f".metadata-report-binding-{secrets.token_hex(16)}.sqlite"
                try:
                    os.link(
                        absolute.name,
                        candidate,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    continue
                alias_name = candidate
                break
            if alias_name is None:
                raise MetadataReportError("database private binding is unavailable")
            _require_named_identity(parent_descriptor, alias_name, expected_identity)
            binding = cls(
                absolute.name,
                descriptor,
                directory_chain,
                alias_name,
                expected_identity,
                locked,
                authority if type(authority) is cls else None,
            )
            succeeded = True
            return binding
        except MetadataReportError:
            raise
        except (OSError, ValueError) as error:
            raise MetadataReportError(
                "database private binding is unavailable"
            ) from error
        finally:
            if alias_name is not None and directory_chain is not None and not succeeded:
                try:
                    os.unlink(
                        alias_name,
                        dir_fd=directory_chain.parent_descriptor,
                    )
                except OSError:
                    pass
            if not succeeded:
                if locked and directory_chain is not None:
                    try:
                        fcntl.flock(
                            directory_chain.parent_descriptor,
                            fcntl.LOCK_UN,
                        )
                    except OSError:
                        pass
                if directory_chain is not None:
                    directory_chain.close()

    def require_current(self) -> None:
        if self.closed:
            raise MetadataReportError("database authority changed")
        if self.authority is not None:
            self.authority.require_current()
        current_parent = self.directory_chain.open_current_parent()
        try:
            _require_owner_only_database_parent(os.fstat(current_parent))
            _require_named_identity(
                current_parent,
                self.configured_name,
                self.expected_identity,
            )
            _reject_database_sidecars_in_directory(current_parent, self.configured_name)
        finally:
            os.close(current_parent)

    def borrows_schema_validation(self) -> bool:
        """Return whether the live root authority admitted this exact inode."""
        if self.authority is None:
            return False
        self.authority.require_current()
        return self.authority._schema_validated

    def mark_schema_validated(self) -> None:
        """Attest schema admission only after the full validator succeeds."""
        self.require_current()
        self._schema_validated = True

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        failure: OSError | None = None
        for suffix in ("-journal", "-wal", "-shm"):
            name = f"{self.alias_name}{suffix}"
            try:
                information = os.stat(
                    name,
                    dir_fd=self.parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                continue
            except OSError as error:
                failure = failure or error
                continue
            if (
                not stat.S_ISREG(information.st_mode)
                or information.st_uid != os.geteuid()
            ):
                failure = failure or OSError("unsafe private database artifact")
                continue
            try:
                os.unlink(name, dir_fd=self.parent_descriptor)
            except OSError as error:
                failure = failure or error
        try:
            _require_named_identity(
                self.parent_descriptor,
                self.alias_name,
                self.expected_identity,
            )
            os.unlink(self.alias_name, dir_fd=self.parent_descriptor)
        except (OSError, MetadataReportError) as error:
            failure = failure or OSError(str(error))
        try:
            os.close(self.descriptor)
        except OSError as error:
            failure = failure or error
        if self.owns_lock:
            try:
                fcntl.flock(self.parent_descriptor, fcntl.LOCK_UN)
            except OSError as error:
                failure = failure or error
        try:
            self.directory_chain.close()
        except MetadataReportError as error:
            failure = failure or OSError(str(error))
        if failure is not None:
            raise MetadataReportError(
                "database private binding cleanup failed"
            ) from failure


def _connect_bound(
    path: Path,
    descriptor: int,
    preconnect: Callable[[], None] | None = None,
    *,
    cross_thread: bool = False,
) -> sqlite3.Connection:
    absolute = str(path.absolute())
    uri = f"file:{quote(absolute, safe='/')}?mode=rw"
    _reject_database_sidecars(path)
    if preconnect is not None:
        preconnect()
    connection = sqlite3.connect(
        uri,
        uri=True,
        isolation_level=None,
        check_same_thread=not cross_thread,
    )
    try:
        _validate_connection_binding(connection, path, descriptor)
        return connection
    except Exception:
        connection.close()
        raise


def _reject_database_sidecars(path: Path) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise MetadataReportError(
                "database recovery state is unavailable"
            ) from error
        raise MetadataReportError("database recovery state is unsupported")


def _reject_database_sidecars_in_directory(
    parent_descriptor: int, basename: str
) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        try:
            os.stat(
                f"{basename}{suffix}",
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        except OSError as error:
            raise MetadataReportError(
                "database recovery state is unavailable"
            ) from error
        raise MetadataReportError("database recovery state is unsupported")


def _require_named_identity(
    parent_descriptor: int,
    name: str,
    expected_identity: tuple[int, int],
) -> None:
    try:
        information = os.stat(
            name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError as error:
        raise MetadataReportError("database pathname changed while opening") from error
    if (
        not stat.S_ISREG(information.st_mode)
        or (
            information.st_dev,
            information.st_ino,
        )
        != expected_identity
    ):
        raise MetadataReportError("database pathname changed while opening")


def _require_protected_directory_entry(
    parent: os.stat_result, entry: os.stat_result
) -> None:
    effective_user = os.geteuid()
    if parent.st_uid not in (0, effective_user):
        raise MetadataReportError("database parent path is not protected")
    writable_by_others = parent.st_mode & 0o022
    sticky = parent.st_mode & stat.S_ISVTX
    if writable_by_others and (not sticky or entry.st_uid != effective_user):
        raise MetadataReportError("database parent path is not protected")


def _require_owner_only_database_parent(
    information: os.stat_result,
) -> None:
    if (
        not stat.S_ISDIR(information.st_mode)
        or information.st_uid != os.geteuid()
        or information.st_mode & 0o077
    ):
        raise MetadataReportError("database parent directory is not owner-only")


def _validate_delete_mode_header(descriptor: int) -> None:
    try:
        header = os.pread(descriptor, 20, 0)
    except OSError as error:
        raise MetadataReportError("database header is unavailable") from error
    if (
        len(header) != 20
        or header[:16] != b"SQLite format 3\x00"
        or header[18:20] != b"\x01\x01"
    ):
        raise MetadataReportError("database recovery mode is unsupported")


def _validate_connection_binding(
    connection: sqlite3.Connection,
    path: Path,
    descriptor: int,
) -> None:
    expected = os.fstat(descriptor)
    expected_identity = (expected.st_dev, expected.st_ino)
    rows = connection.execute("PRAGMA database_list").fetchall()
    if (
        len(rows) != 1
        or len(rows[0]) != 3
        or rows[0][0] != 0
        or rows[0][1] != "main"
        or type(rows[0][2]) is not str
        or not rows[0][2]
        or not os.path.isabs(rows[0][2])
    ):
        raise MetadataReportError("database connection identity is unavailable")
    verification = -1
    try:
        verification = os.open(
            rows[0][2],
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        connected = os.fstat(verification)
        if (
            not stat.S_ISREG(connected.st_mode)
            or (
                connected.st_dev,
                connected.st_ino,
            )
            != expected_identity
        ):
            raise MetadataReportError("database pathname changed while opening")
    except OSError as error:
        raise MetadataReportError("database pathname changed while opening") from error
    finally:
        if verification >= 0:
            os.close(verification)
    try:
        requested = path.lstat()
    except OSError as error:
        raise MetadataReportError("database pathname changed while opening") from error
    if (
        not stat.S_ISREG(requested.st_mode)
        or (
            requested.st_dev,
            requested.st_ino,
        )
        != expected_identity
    ):
        raise MetadataReportError("database pathname changed while opening")


def _unlink_created(path: Path, expected: tuple[int, int]) -> None:
    try:
        information = path.lstat()
        if (
            stat.S_ISREG(information.st_mode)
            and (
                information.st_dev,
                information.st_ino,
            )
            == expected
        ):
            path.unlink()
    except OSError:
        pass
