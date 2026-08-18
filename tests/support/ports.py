"""One definition of a held loopback port for every test that needs one.

`bind(("127.0.0.1", 0))` followed by `close()` reserves nothing. The kernel
hands back a number and is immediately free to hand the same number to the
next automatic allocation anywhere on the host, so what such a helper returns
is a hint with no holder. Every statement between that call and the server's
own `bind()` is an open window, and the fix is always the same shape: keep
the socket, use `number`, and release it in the statement before the thing
that binds it. This module exists so that shape has one definition rather
than one per caller (#774).
"""

from __future__ import annotations

import socket
import time


def bind_loopback(port: int) -> socket.socket | None:
    """Hold `port` on loopback, or report that something else already does.

    `SO_REUSEADDR` is deliberately not set. This is the only instrument that
    answers "is anybody on this port", and reuse exists precisely to make that
    answer come back yes when it should be no.
    """

    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", port))
    except OSError:
        listener.close()
        return None
    return listener


class ReservedPort:
    """A loopback port held by an open socket until its server binds it.

    The socket is held for every part of the window the calling seam can hold,
    and released in the statement before the bind. It usually cannot be held
    further: a server that takes a port number -- on a command line, in a
    configuration document, through a constructor argument -- performs its own
    `bind()`, and two live binds on one loopback port is what the operating
    system exists to refuse. The bind itself therefore stays a window, narrow
    enough that no caller here does anything about it; the qualifier in
    `continuous_review_scale` is the one exception and says why at its own
    call site.

    The reservation is never a precondition, only a protection, so a caller
    that cannot take the port back must not treat that as a failure.
    """

    def __init__(self) -> None:
        listener = bind_loopback(0)
        if listener is None:  # pragma: no cover - loopback has ephemeral ports
            raise AssertionError("no loopback port was available to reserve")
        self._listener: socket.socket | None = listener
        self.number = int(listener.getsockname()[1])

    def take(self, timeout: float = 0.0) -> bool:
        """Hold the port again, waiting up to `timeout` for it to come free.

        Returns whether it is now held. A `timeout` of zero asks the question
        without waiting, which is how a stolen port is told from a server that
        died of its own accord.
        """

        if self._listener is not None:
            return True
        deadline = time.monotonic() + timeout
        while True:
            listener = bind_loopback(self.number)
            if listener is not None:
                self._listener = listener
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def release(self) -> None:
        """Give the port up. The bind must be the next statement."""

        if self._listener is not None:
            self._listener.close()
            self._listener = None

    def close(self) -> None:
        self.release()
