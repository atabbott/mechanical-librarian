"""One re-entrant SQLite transaction scope shared by every persistence module.

The scope owns begin, commit, and rollback mechanics and nothing else. It never
catches, translates, or re-raises an exception: every ``except`` block keeps its
own error identity at its own call site.

An outermost scope uses a real transaction; a scope entered while the connection
is already in a transaction uses a ``SAVEPOINT`` instead, so a nested repository
call can fail and undo only its own work rather than committing or discarding an
enclosing caller's partial writes.
"""

from __future__ import annotations

import sqlite3
from _thread import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding


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
        serialization_lock: RLock | None = None,
    ) -> None:
        self._connection = connection
        self._savepoint = savepoint
        self._deferred = deferred
        self._lock = serialization_lock
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
        if self._lock is not None:
            self._lock.acquire()
        try:
            self._nested = self._connection.in_transaction
            if self._nested:
                self._connection.execute(f"SAVEPOINT {self._savepoint}")
            elif self._deferred:
                self._connection.execute("BEGIN")
            else:
                self._connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            if self._lock is not None:
                self._lock.release()
            raise
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
        serialization_lock: RLock | None = None,
    ) -> None:
        self._connection = connection
        self._binding = binding
        self._serialization_lock = serialization_lock

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
