"""Runtime-local admission for short foreground and background database work."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

WorkClass = Literal["foreground", "background"]


class AdmissionClosedError(RuntimeError):
    """The runtime no longer accepts new database operations."""


@dataclass(eq=False, frozen=True)
class _Ticket:
    work_class: WorkClass


class OperationAdmission:
    """Serialize local database lifetimes with finite foreground priority.

    Connection and transaction locks belong inside this admission. Blocking
    actor locks and external I/O belong outside it. Classification alone holds
    no permit and can therefore span an actor's external work.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._classification: ContextVar[WorkClass] = ContextVar(
            "database_work_class", default="foreground"
        )
        self._local = threading.local()
        self._waiting: deque[_Ticket] = deque()
        self._cohort: deque[_Ticket] = deque()
        self._cohort_open = False
        self._selected: _Ticket | None = None
        self._active: _Ticket | None = None
        self._closed = False

    @contextmanager
    def classify(self, work_class: WorkClass) -> Iterator[None]:
        """Set an operation's purpose without acquiring database admission."""
        if self.held:
            yield
            return
        token = self._classification.set(work_class)
        try:
            yield
        finally:
            self._classification.reset(token)

    @property
    def held(self) -> bool:
        """Whether this execution thread already owns the local boundary."""
        return getattr(self._local, "depth", 0) > 0

    def _choose(self) -> _Ticket | None:
        if self._selected in self._waiting:
            return self._selected
        self._selected = None
        self._cohort = deque(t for t in self._cohort if t in self._waiting)
        if not self._cohort:
            background = next(
                (t for t in self._waiting if t.work_class == "background"), None
            )
            if self._cohort_open:
                self._cohort_open = False
                if background is not None:
                    self._selected = background
                    return background
            self._cohort.extend(
                t for t in self._waiting if t.work_class == "foreground"
            )
            if self._cohort:
                self._cohort_open = True
            else:
                self._selected = background
                return background
        self._selected = self._cohort[0]
        return self._selected

    def acquire(self, check_active: Callable[[], None] | None = None) -> None:
        """Wait for a reserved turn; a submitted mutation supplies no cancel check."""
        if self.held:
            self._local.depth += 1
            return
        if check_active is not None:
            check_active()
        ticket = _Ticket(self._classification.get())
        with self._condition:
            if self._closed:
                raise AdmissionClosedError("database admission is closed")
            self._waiting.append(ticket)
            self._condition.notify_all()
            try:
                while True:
                    if self._closed:
                        raise AdmissionClosedError("database admission is closed")
                    if check_active is not None:
                        check_active()
                    if self._active is None and self._choose() is ticket:
                        break
                    # Only cancellable reads need to observe an external event.
                    self._condition.wait(None if check_active is None else 0.05)
                self._waiting.remove(ticket)
                if self._cohort and self._cohort[0] is ticket:
                    self._cohort.popleft()
                self._active = ticket
                self._selected = None
                self._local.depth = 1
            except BaseException:
                if ticket in self._waiting:
                    self._waiting.remove(ticket)
                if self._active is ticket:
                    self._active = None
                    self._local.depth = 0
                if self._selected is ticket:
                    self._selected = None
                self._condition.notify_all()
                raise

    def try_acquire(self) -> bool:
        """Take a turn only when ``acquire`` would be granted it without waiting.

        Nothing is queued: with no operation active or waiting, the waiting
        ticket ``acquire`` would add is chosen at once whatever its class, so
        the same bookkeeping runs here, and otherwise this declines. It never
        precedes queued work, and a closed admission still refuses.
        """
        if self.held:
            self._local.depth += 1
            return True
        with self._condition:
            if self._closed:
                raise AdmissionClosedError("database admission is closed")
            if self._active is not None or self._waiting:
                return False
            ticket = _Ticket(self._classification.get())
            self._waiting.append(ticket)
            self._choose()
            self._waiting.remove(ticket)
            if self._cohort and self._cohort[0] is ticket:
                self._cohort.popleft()
            self._active = ticket
            self._selected = None
            self._local.depth = 1
            return True

    def release(self) -> None:
        """Release only this thread's accepted increment after commit or rollback."""
        if not self.held:
            raise RuntimeError("database admission is not held")
        self._local.depth -= 1
        if self.held:
            return
        with self._condition:
            self._active = None
            self._choose()
            self._condition.notify_all()

    @contextmanager
    def enter(self, check_active: Callable[[], None] | None = None) -> Iterator[None]:
        self.acquire(check_active)
        try:
            yield
        finally:
            self.release()

    def close(self) -> None:
        """Reject new/waiting operations and drain an already accepted boundary."""
        if self.held:
            raise RuntimeError("cannot close an admitted database operation")
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            self._condition.wait_for(lambda: self._active is None)
