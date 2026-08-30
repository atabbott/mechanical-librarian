"""One re-entrant SQLite transaction scope shared by every persistence module.

The scope owns begin, commit, and rollback mechanics and nothing else. It never
translates an exception. Acquisition cleanup re-raises the exact original
object; every caller keeps its own error identity and translation surface.
An unproven accepted boundary revokes its physical connection before
serialization is released; if revocation itself cannot be proven,
serialization stays held.

An outermost scope uses a real transaction; a scope entered while the connection
is already in a transaction uses a ``SAVEPOINT`` instead, so a nested repository
call can fail and undo only its own work rather than committing or discarding an
enclosing caller's partial writes.
"""

from __future__ import annotations

import sqlite3
import sys
from _thread import RLock
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, TypeVar, cast

if TYPE_CHECKING:
    from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding

_T = TypeVar("_T")


class _RecursionDepth(Protocol):
    def _recursion_count(self) -> int: ...


class _SerializationLock:
    """Expose this thread's exact recursion depth around one RLock acquire.

    CPython's pinned ``_thread.RLock`` has no public depth observer. Keeping its
    private observer here lets acquisition cleanup balance only the increment
    accepted by this invocation; callers never infer ownership by releasing.
    """

    __slots__ = ("_lock",)

    def __init__(self, lock: RLock) -> None:
        self._lock = lock

    def depth(self) -> int:
        return cast(_RecursionDepth, self._lock)._recursion_count()

    def acquire(self) -> None:
        self._lock.acquire()

    def release(self) -> None:
        self._lock.release()

    def __enter__(self) -> None:
        self.acquire()

    def __exit__(self, *_args: object) -> None:
        self.release()


def _deny_sqlite(*_args: object) -> int:
    return sqlite3.SQLITE_DENY


def _shutdown_connection(connection: sqlite3.Connection) -> bool:
    """Boundedly release one physical SQLite authority."""
    for _attempt in range(2):
        try:
            connection.close()
            return True
        except BaseException:
            pass
    return False


def _revoke_connection(connection: sqlite3.Connection) -> bool:
    """Close or physically poison a connection before authority is released."""
    if _shutdown_connection(connection):
        return True
    try:
        connection.set_authorizer(_deny_sqlite)
        return True
    except BaseException:
        return False


def _release_lock_increment(
    lock: _SerializationLock, prior_depth: int
) -> tuple[bool, BaseException | None]:
    """Balance this invocation's increment and retain its first release fault."""
    failure: BaseException | None = None
    for _attempt in range(2):
        try:
            depth = lock.depth()
        except BaseException as error:
            return False, failure or error
        if depth == prior_depth:
            return True, failure
        if depth != prior_depth + 1:
            return False, failure
        try:
            lock.release()
        except BaseException as error:
            failure = failure or error
    try:
        return lock.depth() == prior_depth, failure
    except BaseException as error:
        return False, failure or error


def _acquire_lock_increment(lock: _SerializationLock) -> int:
    """Acquire one recursion increment or balance it before preserving failure."""
    prior_depth = lock.depth()
    try:
        lock.acquire()
    except BaseException as error:
        try:
            accepted = lock.depth() == prior_depth + 1
        except BaseException:
            accepted = False
        if accepted:
            _release_lock_increment(lock, prior_depth)
        raise error from None
    return prior_depth


def _missing_savepoint(error: BaseException, savepoint: str) -> bool:
    return isinstance(error, sqlite3.OperationalError) and str(error) == (
        f"no such savepoint: {savepoint}"
    )


def _unwind_outer(
    connection: sqlite3.Connection,
) -> tuple[bool, BaseException | None]:
    failure: BaseException | None = None
    for _attempt in range(2):
        try:
            if not connection.in_transaction:
                return True, failure
        except BaseException as error:
            failure = failure or error
        try:
            connection.rollback()
        except BaseException as error:
            failure = failure or error
        try:
            if not connection.in_transaction:
                return True, failure
        except BaseException as error:
            failure = failure or error
    return False, failure


def _unwind_savepoint(
    connection: sqlite3.Connection,
    savepoint: str,
    *,
    missing_before_rollback_is_clean: bool,
) -> tuple[bool, BaseException | None]:
    failure: BaseException | None = None
    rollback_clean = False
    for _attempt in range(2):
        try:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            rollback_clean = True
            break
        except BaseException as error:
            if _missing_savepoint(error, savepoint):
                return missing_before_rollback_is_clean, failure or error
            failure = failure or error
    if not rollback_clean:
        return False, failure
    for _attempt in range(2):
        try:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            return True, failure
        except BaseException as error:
            if _missing_savepoint(error, savepoint):
                return True, failure
            failure = failure or error
    return False, failure


class TransactionScope:
    """Own one transaction or savepoint over a shared connection."""

    __slots__ = (
        "_connection",
        "_savepoint",
        "_deferred",
        "_lock",
        "_lock_depth",
        "_nested",
        "_savepoint_only",
        "_started",
    )

    def __init__(
        self,
        connection: sqlite3.Connection,
        savepoint: str,
        *,
        deferred: bool = False,
        savepoint_only: bool = False,
        serialization_lock: RLock | _SerializationLock | None = None,
    ) -> None:
        self._connection = connection
        self._savepoint = f"{savepoint}_{id(self):x}"
        self._deferred = deferred
        self._savepoint_only = savepoint_only
        self._lock = (
            serialization_lock
            if serialization_lock is None
            or isinstance(serialization_lock, _SerializationLock)
            else _SerializationLock(serialization_lock)
        )
        self._nested = False
        self._lock_depth = 0
        self._started = False

    @property
    def started(self) -> bool:
        """Report whether this scope currently owns a transaction or savepoint."""
        return self._started

    @property
    def nested(self) -> bool:
        """Report whether this scope owns a savepoint boundary."""
        return self._nested

    def begin(self) -> None:
        """Start this scope, using a savepoint when nested or explicitly required."""
        lock_depth = 0
        if self._lock is not None:
            lock_depth = _acquire_lock_increment(self._lock)
            self._lock_depth = lock_depth
        boundary_issued = False
        failure: BaseException | None = None
        try:
            self._nested = self._savepoint_only or self._connection.in_transaction
            boundary_issued = True
            if self._nested:
                self._connection.execute(f"SAVEPOINT {self._savepoint}")
            elif self._deferred:
                self._connection.execute("BEGIN")
            else:
                self._connection.execute("BEGIN IMMEDIATE")
        except BaseException as error:
            failure = error
            cleanup_proven, _cleanup_error = (
                (True, None)
                if not boundary_issued
                else (
                    _unwind_savepoint(
                        self._connection,
                        self._savepoint,
                        missing_before_rollback_is_clean=True,
                    )
                    if self._nested
                    else _unwind_outer(self._connection)
                )
            )
            authority_releasable = cleanup_proven or _revoke_connection(
                self._connection
            )
            if self._lock is not None and authority_releasable:
                _release_lock_increment(self._lock, lock_depth)
        if failure is not None:
            raise failure from None
        self._started = True

    def commit(self) -> None:
        """Release this scope's writes, committing only when outermost.

        The scope stays open until the statement succeeds, so a failing commit
        still reaches its call site's own ``except`` block with a live scope to
        roll back -- exactly as the hand-rolled ``commit()``/``rollback()`` pair
        behaved before.
        """
        if not self._started:
            return
        if self._nested:
            self._connection.execute(f"RELEASE SAVEPOINT {self._savepoint}")
        else:
            self._connection.commit()
        balanced = True
        release_error: BaseException | None = None
        if self._lock is not None:
            balanced, release_error = _release_lock_increment(
                self._lock, self._lock_depth
            )
        if not balanced:
            if release_error is not None and not isinstance(release_error, Exception):
                raise release_error from None
            raise RuntimeError("transaction serialization release failed") from None
        self._started = False
        if release_error is not None and not isinstance(release_error, Exception):
            raise release_error from None

    def rollback(self) -> None:
        """Undo this scope's writes, leaving any enclosing transaction intact."""
        if not self._started:
            return
        original = sys.exception()
        cleanup_proven, cleanup_error = (
            _unwind_savepoint(
                self._connection,
                self._savepoint,
                missing_before_rollback_is_clean=False,
            )
            if self._nested
            else _unwind_outer(self._connection)
        )
        authority_releasable = cleanup_proven or _revoke_connection(self._connection)
        release_error: BaseException | None = None
        if authority_releasable:
            if self._lock is not None:
                balanced, release_error = _release_lock_increment(
                    self._lock, self._lock_depth
                )
                if not balanced:
                    authority_releasable = False
            if authority_releasable:
                self._started = False
        if original is not None:
            return
        if cleanup_error is not None and not isinstance(cleanup_error, Exception):
            raise cleanup_error from None
        if release_error is not None and not isinstance(release_error, Exception):
            raise release_error from None
        if cleanup_error is not None:
            raise cleanup_error from None
        if not authority_releasable:
            raise RuntimeError("transaction serialization release failed") from None


class ConnectionScoped:
    """Share one SQLite connection and the scope factory over it.

    Every aggregate repository composes over one connection owned by one
    filesystem binding, so this carries both references and the one line that
    opens a scope on the connection, rather than repeating all three in each
    module.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        serialization_lock: RLock | _SerializationLock | None = None,
    ) -> None:
        self._connection = connection
        self._binding = binding
        self._serialization_lock = (
            serialization_lock
            if serialization_lock is None
            or isinstance(serialization_lock, _SerializationLock)
            else _SerializationLock(serialization_lock)
        )

    def _transaction(
        self, savepoint: str, *, deferred: bool = False
    ) -> TransactionScope:
        """Open one re-entrant scope over this repository's shared connection."""
        return TransactionScope(
            self._connection,
            savepoint,
            deferred=deferred,
            serialization_lock=self._serialization_lock,
        )

    def _serialized(self, operation: Callable[[], _T]) -> _T:
        """Run one non-transactional operation under the shared depth authority."""
        lock = self._serialization_lock
        if lock is None:
            return operation()
        prior_depth = _acquire_lock_increment(lock)
        try:
            result = operation()
        except BaseException as error:
            _release_lock_increment(lock, prior_depth)
            raise error from None
        balanced, release_error = _release_lock_increment(lock, prior_depth)
        if not balanced:
            if release_error is not None:
                raise release_error from None
            raise RuntimeError("transaction serialization release failed") from None
        if release_error is not None:
            raise release_error from None
        return result
