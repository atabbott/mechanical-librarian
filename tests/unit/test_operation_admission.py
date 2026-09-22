"""Actual SQLite boundaries, priority fairness and cancellation ownership."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.transactions import TransactionScope
from mechanical_librarian.application.operation_admission import (
    AdmissionClosedError,
    OperationAdmission,
    WorkClass,
)


def _waiting(admission: OperationAdmission, count: int) -> None:
    with admission._condition:
        assert admission._condition.wait_for(
            lambda: len(admission._waiting) == count, timeout=5
        )


def test_finite_foreground_cohort_precedes_one_background_transaction(
    tmp_path: Path,
) -> None:
    database = tmp_path / "admission.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE observed (name TEXT)")
    admission = OperationAdmission()
    entered = threading.Event()
    release = threading.Event()
    first = threading.Event()
    continue_first = threading.Event()
    failures: list[BaseException] = []
    threads: list[threading.Thread] = []

    def work(name: str, role: WorkClass) -> None:
        connection = sqlite3.connect(database)
        try:
            with admission.classify(role):
                transaction = TransactionScope(connection, name, admission=admission)
                transaction.begin()
                try:
                    connection.execute("INSERT INTO observed VALUES (?)", (name,))
                    if name == "B0":
                        entered.set()
                        assert release.wait(5)
                    if name == "F1":
                        first.set()
                        assert continue_first.wait(5)
                    nested = TransactionScope(connection, "nested", admission=admission)
                    nested.begin()
                    nested.commit()
                    assert admission.held
                    transaction.commit()
                except BaseException:
                    transaction.rollback()
                    raise
        except BaseException as error:
            failures.append(error)
        finally:
            connection.close()

    def start(name: str, role: WorkClass) -> None:
        thread = threading.Thread(target=work, args=(name, role))
        threads.append(thread)
        thread.start()

    try:
        start("B0", "background")
        assert entered.wait(5)
        start("B1", "background")
        _waiting(admission, 1)
        start("F1", "foreground")
        _waiting(admission, 2)
        start("F2", "foreground")
        _waiting(admission, 3)
        release.set()
        assert first.wait(5)
        start("F3", "foreground")
        _waiting(admission, 3)
        continue_first.set()
    finally:
        release.set()
        continue_first.set()
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
    assert failures == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM observed ORDER BY rowid"
        ).fetchall() == [("B0",), ("F1",), ("F2",), ("B1",), ("F3",)]
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_cancelled_reserved_read_releases_its_turn_to_background() -> None:
    admission = OperationAdmission()
    cancelled = threading.Event()
    events: list[str] = []
    failures: list[BaseException] = []

    def check() -> None:
        if cancelled.is_set():
            raise InterruptedError("read disconnected")

    def read() -> None:
        try:
            with admission.enter(check):
                events.append("forbidden")
        except InterruptedError:
            events.append("cancelled")
        except BaseException as error:
            failures.append(error)

    def background() -> None:
        with admission.classify("background"), admission.enter():
            events.append("background")

    with admission.classify("background"):
        admission.acquire()
    reader = threading.Thread(target=read)
    worker = threading.Thread(target=background)
    try:
        reader.start()
        _waiting(admission, 1)
        worker.start()
        _waiting(admission, 2)
        with admission._condition:
            admission.release()
            assert admission._selected is not None
            assert admission._selected.work_class == "foreground"
            cancelled.set()
    finally:
        if admission.held:
            admission.release()
        reader.join(5)
        worker.join(5)
    assert not reader.is_alive() and not worker.is_alive()
    assert failures == []
    assert events == ["cancelled", "background"]


def test_nested_classification_cannot_upgrade_background_or_cancel_accepted_work() -> (
    None
):
    admission = OperationAdmission()
    with admission.classify("background"), admission.enter():
        active = admission._active
        assert active is not None and active.work_class == "background"
        with (
            admission.classify("foreground"),
            admission.enter(lambda: pytest.fail("accepted work must not be cancelled")),
        ):
            assert admission._active is active
        assert admission.held
    assert not admission.held
    with admission.enter():
        assert admission._active is not None
        assert admission._active.work_class == "foreground"


def test_close_refuses_waiters_but_drains_an_accepted_transaction() -> None:
    admission = OperationAdmission()
    closed = threading.Event()
    rejected = threading.Event()
    admission.acquire()

    def waiter() -> None:
        with pytest.raises(AdmissionClosedError):
            with admission.enter():
                pytest.fail("closed admission accepted queued work")
        rejected.set()

    def close() -> None:
        admission.close()
        closed.set()

    waiting = threading.Thread(target=waiter)
    closer = threading.Thread(target=close)
    waiting.start()
    _waiting(admission, 1)
    closer.start()
    try:
        assert rejected.wait(5)
        assert not closed.is_set()
    finally:
        admission.release()
        waiting.join(5)
        closer.join(5)
    assert closed.is_set()
    with pytest.raises(AdmissionClosedError):
        admission.acquire()


def test_failed_begin_releases_admission_without_starting_a_transaction() -> None:
    admission = OperationAdmission()
    connection = sqlite3.connect(":memory:")
    connection.set_authorizer(
        lambda operation, *_: (
            sqlite3.SQLITE_DENY
            if operation == sqlite3.SQLITE_TRANSACTION
            else sqlite3.SQLITE_OK
        )
    )
    scope = TransactionScope(connection, "denied", admission=admission)
    with pytest.raises(sqlite3.DatabaseError):
        scope.begin()
    assert not scope.started and not admission.held
    assert not connection.in_transaction
    connection.close()
    with admission.enter():
        assert admission.held


def test_failed_commit_retains_admission_until_rollback() -> None:
    class FailingCommit(sqlite3.Connection):
        def commit(self) -> None:
            raise sqlite3.OperationalError("synthetic commit failure")

    admission = OperationAdmission()
    connection = sqlite3.connect(":memory:", factory=FailingCommit)
    scope = TransactionScope(connection, "commit_failure", admission=admission)
    scope.begin()
    connection.execute("CREATE TABLE pending(value TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="synthetic commit failure"):
        scope.commit()
    assert scope.started and admission.held and connection.in_transaction
    scope.rollback()
    assert not scope.started and not admission.held and not connection.in_transaction
    assert connection.execute("SELECT name FROM sqlite_master").fetchall() == []
    connection.close()


def test_failed_rollback_revokes_connection_before_releasing_admission() -> None:
    class FailingRollback(sqlite3.Connection):
        def rollback(self) -> None:
            raise sqlite3.OperationalError("synthetic rollback failure")

    admission = OperationAdmission()
    connection = sqlite3.connect(":memory:", factory=FailingRollback)
    scope = TransactionScope(connection, "rollback_failure", admission=admission)
    scope.begin()
    with pytest.raises(sqlite3.OperationalError, match="^synthetic rollback failure$"):
        scope.rollback()
    assert not scope.started and not admission.held
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
