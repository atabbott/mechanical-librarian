import sqlite3
import threading
from _thread import RLock
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


class _InterruptingReleaseLock:
    def __init__(self, fault: BaseException, *, before_effect: bool) -> None:
        self.lock = RLock()
        self.fault = fault
        self.before_effect = before_effect
        self.armed = False

    def acquire(self) -> None:
        self.lock.acquire()

    def release(self) -> None:
        if self.armed:
            self.armed = False
            if self.before_effect:
                raise self.fault
            self.lock.release()
            raise self.fault
        self.lock.release()

    def _recursion_count(self) -> int:
        return self.lock._recursion_count()


def _assert_contender_acquires(lock: RLock) -> None:
    acquired = threading.Event()

    def contend() -> None:
        with lock:
            acquired.set()

    contender = threading.Thread(target=contend)
    contender.start()
    assert acquired.wait(1)
    contender.join(timeout=1)
    assert not contender.is_alive()


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE facts(value TEXT PRIMARY KEY) STRICT")
    connection.commit()
    return connection


def _values(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0] for row in connection.execute("SELECT value FROM facts ORDER BY value")
    ]


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


@pytest.mark.parametrize("fault_type", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("before_effect", (False, True))
def test_commit_propagates_balanced_release_process_control(
    fault_type: type[BaseException], before_effect: bool
) -> None:
    connection = _connection()
    fault = fault_type("private commit release")
    lock = _InterruptingReleaseLock(fault, before_effect=before_effect)
    scope = TransactionScope(
        connection, "commit_release", serialization_lock=cast(RLock, lock)
    )
    scope.begin()
    connection.execute("INSERT INTO facts(value) VALUES('committed')")
    lock.armed = True

    with pytest.raises(fault_type) as raised:
        scope.commit()

    assert raised.value is fault
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert not scope.started and not connection.in_transaction
    assert _values(connection) == ["committed"]
    assert lock._recursion_count() == 0
    _assert_contender_acquires(lock.lock)


@pytest.mark.parametrize("fault_type", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("before_effect", (False, True))
def test_direct_rollback_propagates_balanced_release_process_control(
    fault_type: type[BaseException], before_effect: bool
) -> None:
    connection = _connection()
    fault = fault_type("private rollback release")
    lock = _InterruptingReleaseLock(fault, before_effect=before_effect)
    scope = TransactionScope(
        connection, "rollback_release", serialization_lock=cast(RLock, lock)
    )
    scope.begin()
    connection.execute("INSERT INTO facts(value) VALUES('discarded')")
    lock.armed = True

    with pytest.raises(fault_type) as raised:
        scope.rollback()

    assert raised.value is fault
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert not scope.started and not connection.in_transaction
    assert _values(connection) == []
    assert lock._recursion_count() == 0
    _assert_contender_acquires(lock.lock)


@pytest.mark.parametrize("fault_type", (KeyboardInterrupt, SystemExit))
@pytest.mark.parametrize("before_effect", (False, True))
def test_rollback_preserves_existing_process_control_over_release_fault(
    fault_type: type[BaseException], before_effect: bool
) -> None:
    connection = _connection()
    original = fault_type("private body interruption")
    release = SystemExit("private release interruption")
    lock = _InterruptingReleaseLock(release, before_effect=before_effect)
    scope = TransactionScope(
        connection, "body_release", serialization_lock=cast(RLock, lock)
    )
    scope.begin()
    connection.execute("INSERT INTO facts(value) VALUES('discarded')")
    lock.armed = True

    with pytest.raises(fault_type) as raised:
        try:
            raise original
        except BaseException as error:
            scope.rollback()
            raise error from None

    assert raised.value is original
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert not scope.started and not connection.in_transaction
    assert _values(connection) == []
    assert lock._recursion_count() == 0
    _assert_contender_acquires(lock.lock)


def test_outer_commit_callback_precedes_release_and_never_runs_for_savepoint() -> None:
    connection = _connection()
    lock = RLock()
    outer = TransactionScope(connection, "outer", serialization_lock=lock)
    outer.begin()
    inner = TransactionScope(connection, "inner", serialization_lock=lock)
    inner.begin()
    calls: list[bool] = []
    inner.commit(after_commit=lambda: calls.append(False))
    assert calls == []

    def committed() -> None:
        assert not connection.in_transaction
        assert lock._recursion_count() == 1
        calls.append(True)

    outer.commit(after_commit=committed)
    assert calls == [True]
    assert lock._recursion_count() == 0
    connection.close()


def test_failed_outer_commit_never_transfers_ownership() -> None:
    connection = _connection()
    scope = TransactionScope(
        cast(sqlite3.Connection, _FailingCommit(connection)), "outer"
    )
    scope.begin()
    connection.execute("INSERT INTO facts VALUES('pending')")
    calls: list[bool] = []
    with pytest.raises(sqlite3.OperationalError):
        scope.commit(after_commit=lambda: calls.append(True))
    assert calls == []
    scope.rollback()
    assert _values(connection) == []
    connection.close()


@pytest.mark.parametrize("fault", (RuntimeError("callback"), KeyboardInterrupt()))
def test_commit_callback_fault_preserves_committed_state_and_balances_release(
    fault: BaseException,
) -> None:
    connection = _connection()
    lock = RLock()
    scope = TransactionScope(connection, "outer", serialization_lock=lock)
    scope.begin()
    connection.execute("INSERT INTO facts VALUES('committed')")

    def fail() -> None:
        raise fault

    with pytest.raises(type(fault)) as raised:
        scope.commit(after_commit=fail)
    assert raised.value is fault
    assert not scope.started and not connection.in_transaction
    assert lock._recursion_count() == 0
    assert _values(connection) == ["committed"]
    connection.close()
