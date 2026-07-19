"""Temporary same-process cancellation handlers for foreground commands."""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def temporary_cancellation_signals(event: threading.Event) -> Iterator[None]:
    """Translate SIGINT/SIGTERM to cancellation and restore exact prior handlers."""

    if type(event) is not threading.Event:
        raise TypeError("cancellation signal event is malformed")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("cancellation signals require the main thread")
    signals = (signal.SIGINT, signal.SIGTERM)
    previous: dict[signal.Signals, signal.Handlers] = {}

    def cancel(_number: int, _frame: object) -> None:
        event.set()

    failure: BaseException | None = None
    try:
        for number in signals:
            previous[number] = signal.getsignal(number)
            signal.signal(number, cancel)
        yield
    except BaseException as error:
        failure = error
        raise
    finally:
        restore_failure: BaseException | None = None
        for number in reversed(signals):
            if number not in previous:
                continue
            try:
                signal.signal(number, previous[number])
            except BaseException as error:
                restore_failure = restore_failure or error
        if restore_failure is not None:
            if failure is not None:
                failure.add_note("cancellation signal restoration failed")
            else:
                raise restore_failure


__all__ = ["temporary_cancellation_signals"]
