"""One re-entrant SQLite transaction scope shared by every persistence module.

The scope owns begin, commit, and rollback mechanics and nothing else. It never
translates an exception. Acquisition cleanup re-raises the exact original
object; every caller keeps its own error identity and translation surface.
An unproven accepted boundary revokes its connection before serialization is
released; if revocation itself cannot be proven, serialization stays held.

An outermost scope uses a real transaction; a scope entered while the connection
is already in a transaction uses a ``SAVEPOINT`` instead, so a nested repository
call can fail and undo only its own work rather than committing or discarding an
enclosing caller's partial writes.
"""

from __future__ import annotations

import sqlite3
from _thread import RLock
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding


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


def _invalidate_connection(connection: sqlite3.Connection) -> bool:
    """Revoke a connection whose accepted boundary could not be unwound."""
    for _attempt in range(2):
        try:
            connection.close()
            return True
        except BaseException:
            pass
    return False


class TransactionScope:
    """Own one transaction or savepoint over a shared connection."""

    __slots__ = (
        "_connection",
        "_savepoint",
        "_deferred",
        "_lock",
        "_nested",
        "_started",
    )

    def __init__(
        self,
        connection: sqlite3.Connection,
        savepoint: str,
        *,
        deferred: bool = False,
        serialization_lock: RLock | _SerializationLock | None = None,
    ) -> None:
        self._connection = connection
        self._savepoint = f"{savepoint}_{id(self):x}"
        self._deferred = deferred
        self._lock = (
            serialization_lock
            if serialization_lock is None
            or isinstance(serialization_lock, _SerializationLock)
            else _SerializationLock(serialization_lock)
        )
        self._nested = False
        self._started = False

    @property
    def started(self) -> bool:
        """Report whether this scope currently owns a transaction or savepoint."""
        return self._started

    @property
    def nested(self) -> bool:
        """Report whether this scope runs inside an enclosing transaction."""
        return self._nested

    def begin(self) -> None:
        """Start this scope, nesting on a savepoint inside an open transaction."""
        lock_depth = 0
        if self._lock is not None:
            lock_depth = self._lock.depth()
            try:
                self._lock.acquire()
            except BaseException as error:
                try:
                    accepted = self._lock.depth() > lock_depth
                except BaseException:
                    accepted = False
                if accepted:
                    try:
                        self._lock.release()
                    except BaseException:
                        pass
                raise error from None
        boundary_issued = False
        failure: BaseException | None = None
        try:
            self._nested = self._connection.in_transaction
            boundary_issued = True
            if self._nested:
                self._connection.execute(f"SAVEPOINT {self._savepoint}")
            elif self._deferred:
                self._connection.execute("BEGIN")
            else:
                self._connection.execute("BEGIN IMMEDIATE")
        except BaseException as error:
            failure = error
            cleanup_proven = not boundary_issued
            if boundary_issued and self._nested:
                rollback_failure: BaseException | None = None
                release_failure: BaseException | None = None
                try:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {self._savepoint}")
                except BaseException as cleanup_error:
                    rollback_failure = cleanup_error
                try:
                    self._connection.execute(f"RELEASE SAVEPOINT {self._savepoint}")
                except BaseException as cleanup_error:
                    release_failure = cleanup_error
                cleanup_proven = (
                    rollback_failure is None and release_failure is None
                ) or (
                    isinstance(error, sqlite3.Error)
                    and isinstance(rollback_failure, sqlite3.Error)
                    and isinstance(release_failure, sqlite3.Error)
                )
            elif boundary_issued:
                try:
                    acquired = self._connection.in_transaction
                except BaseException:
                    acquired = False
                if acquired:
                    try:
                        self._connection.rollback()
                    except BaseException:
                        pass
                try:
                    cleanup_proven = not self._connection.in_transaction
                except BaseException:
                    cleanup_proven = False
            authority_releasable = cleanup_proven or _invalidate_connection(
                self._connection
            )
            if self._lock is not None and authority_releasable:
                try:
                    self._lock.release()
                except BaseException:
                    pass
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
        self._started = False
        if self._lock is not None:
            self._lock.release()

    def rollback(self) -> None:
        """Undo this scope's writes, leaving any enclosing transaction intact."""
        if not self._started:
            return
        try:
            if self._nested:
                self._connection.execute(f"ROLLBACK TO SAVEPOINT {self._savepoint}")
                self._connection.execute(f"RELEASE SAVEPOINT {self._savepoint}")
            elif self._connection.in_transaction:
                self._connection.rollback()
            self._started = False
        finally:
            if self._lock is not None:
                self._lock.release()


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
