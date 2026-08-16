import sqlite3
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.transactions import TransactionScope


class _FailingCommit:
    """Delegate everything but ``commit``, which fails as a locked write would."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def commit(self) -> None:
        raise sqlite3.OperationalError("database is locked")

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE facts(value TEXT PRIMARY KEY) STRICT")
    connection.commit()
    return connection


def _values(connection: sqlite3.Connection) -> list[str]:
    return [row[0] for row in connection.execute("SELECT value FROM facts ORDER BY value")]


def test_outermost_scope_commits_a_real_transaction() -> None:
    connection = _connection()
    scope = TransactionScope(connection, "outer")
    scope.begin()
    assert not scope.nested
    connection.execute("INSERT INTO facts(value) VALUES('one')")
    scope.commit()
    assert not connection.in_transaction
    assert ["one"] == _values(connection)


def test_outermost_scope_rolls_back_a_real_transaction() -> None:
    connection = _connection()
    scope = TransactionScope(connection, "outer")
    scope.begin()
    connection.execute("INSERT INTO facts(value) VALUES('one')")
    scope.rollback()
    assert not connection.in_transaction
    assert [] == _values(connection)


def test_nested_scope_uses_a_savepoint_and_keeps_the_outer_open() -> None:
    connection = _connection()
    outer = TransactionScope(connection, "outer")
    outer.begin()
    connection.execute("INSERT INTO facts(value) VALUES('outer')")
    inner = TransactionScope(connection, "inner")
    inner.begin()
    assert inner.nested
    connection.execute("INSERT INTO facts(value) VALUES('inner')")
    inner.commit()
    assert connection.in_transaction
    outer.commit()
    assert ["inner", "outer"] == _values(connection)


def test_nested_rollback_discards_only_its_own_writes() -> None:
    connection = _connection()
    outer = TransactionScope(connection, "outer")
    outer.begin()
    connection.execute("INSERT INTO facts(value) VALUES('outer')")
    inner = TransactionScope(connection, "inner")
    inner.begin()
    connection.execute("INSERT INTO facts(value) VALUES('inner')")
    inner.rollback()
    assert connection.in_transaction
    outer.commit()
    assert ["outer"] == _values(connection)


def test_nested_commit_does_not_durably_commit_the_outer_transaction() -> None:
    connection = _connection()
    outer = TransactionScope(connection, "outer")
    outer.begin()
    connection.execute("INSERT INTO facts(value) VALUES('outer')")
    inner = TransactionScope(connection, "inner")
    inner.begin()
    inner.commit()
    outer.rollback()
    assert [] == _values(connection)


def test_deferred_scope_begins_without_taking_a_write_lock() -> None:
    connection = _connection()
    scope = TransactionScope(connection, "reader", deferred=True)
    scope.begin()
    assert connection.in_transaction
    scope.commit()
    assert not connection.in_transaction


def test_a_deferred_scope_nested_in_a_write_transaction_uses_a_savepoint() -> None:
    connection = _connection()
    outer = TransactionScope(connection, "outer")
    outer.begin()
    inner = TransactionScope(connection, "reader", deferred=True)
    inner.begin()
    assert inner.nested
    inner.commit()
    assert connection.in_transaction
    outer.rollback()


def test_commit_and_rollback_are_inert_before_and_after_the_scope() -> None:
    connection = _connection()
    scope = TransactionScope(connection, "outer")
    assert not scope.started
    scope.commit()
    scope.rollback()
    assert not connection.in_transaction
    scope.begin()
    assert scope.started
    scope.commit()
    assert not scope.started
    scope.rollback()
    assert not connection.in_transaction


def test_a_failing_commit_leaves_the_scope_open_for_its_own_rollback() -> None:
    connection = _connection()
    failing = cast(sqlite3.Connection, _FailingCommit(connection))
    scope = TransactionScope(failing, "outer")
    scope.begin()
    connection.execute("INSERT INTO facts(value) VALUES('one')")
    with pytest.raises(sqlite3.OperationalError):
        scope.commit()
    # The call site's own ``except`` block still owns a live scope, so the
    # partial write is undone exactly as the hand-rolled pair undid it.
    assert scope.started
    scope.rollback()
    assert not connection.in_transaction
    assert [] == _values(connection)
