"""Infrastructure capabilities for isolated Hardcover network preparation."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from mechanical_librarian.application.transport_capabilities import (
    AddressResolver,
    ResolverBudget,
    ResolverFailure,
    ResolverFailureKind,
)


_MAX_RESOLVER_OUTPUT_BYTES = 8_192
_RESOLVER_SCRIPT = """\
import json
import socket
import sys

answers = socket.getaddrinfo(
    sys.argv[1],
    int(sys.argv[2]),
    family=socket.AF_UNSPEC,
    type=socket.SOCK_STREAM,
    proto=socket.IPPROTO_TCP,
)
sys.stdout.write(json.dumps([answer[4][0] for answer in answers]))
"""
_TRANSPORT_LOGGERS = (
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
)
_TRANSPORT_LOG_CONTEXT: ContextVar[bool] = ContextVar(
    "hardcover_transport_log_isolation", default=False
)


def _budget_failure(budget: ResolverBudget, wall_deadline: float) -> ResolverFailureKind | None:
    try:
        if budget.is_cancelled():
            return ResolverFailureKind.CANCELLED
        remaining = min(budget.remaining_seconds(), wall_deadline - time.monotonic())
    except Exception:
        return ResolverFailureKind.UNAVAILABLE
    if type(remaining) is not float or remaining <= 0:
        return ResolverFailureKind.TIMEOUT
    return None


def _remaining(budget: ResolverBudget, wall_deadline: float) -> float:
    try:
        return max(0.0, min(budget.remaining_seconds(), wall_deadline - time.monotonic()))
    except Exception:
        return 0.0


def _terminate(process: subprocess.Popen[bytes]) -> None:
    process.kill()
    try:
        process.wait(timeout=0.1)
    except subprocess.TimeoutExpired:
        process.wait()


class IsolatedAddressResolverFactory:
    """Build one resolver whose subprocess is bounded by application policy."""

    def __call__(self, budget: ResolverBudget) -> AddressResolver:
        if type(budget) is not ResolverBudget:
            raise TypeError("resolver budget is malformed")
        return _IsolatedAddressResolver(budget)


class _IsolatedAddressResolver:
    def __init__(self, budget: ResolverBudget) -> None:
        self._budget = budget
        try:
            remaining = budget.remaining_seconds()
        except Exception as error:
            raise ResolverFailure(ResolverFailureKind.UNAVAILABLE) from error
        if type(remaining) is not float or remaining <= 0:
            raise ResolverFailure(ResolverFailureKind.TIMEOUT)
        self._wall_deadline = time.monotonic() + remaining

    def __call__(self, host: str, port: int) -> tuple[str, ...]:
        if type(host) is not str or not host or len(host) > 253 or type(port) is not int:
            raise ResolverFailure(ResolverFailureKind.UNAVAILABLE)
        failure = _budget_failure(self._budget, self._wall_deadline)
        if failure is not None:
            raise ResolverFailure(failure)
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-c", _RESOLVER_SCRIPT, host, str(port)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                cwd="/",
                env={},
            )
        except (OSError, ValueError):
            raise ResolverFailure(ResolverFailureKind.UNAVAILABLE) from None
        try:
            while process.poll() is None:
                failure = _budget_failure(self._budget, self._wall_deadline)
                if failure is not None:
                    _terminate(process)
                    raise ResolverFailure(failure)
                time.sleep(min(0.01, _remaining(self._budget, self._wall_deadline)))
            output, _ = process.communicate(timeout=0.1)
        except (OSError, subprocess.SubprocessError):
            raise ResolverFailure(ResolverFailureKind.UNAVAILABLE) from None
        finally:
            if process.poll() is None:
                _terminate(process)
        if process.returncode != 0 or not 1 <= len(output) <= _MAX_RESOLVER_OUTPUT_BYTES:
            raise ResolverFailure(ResolverFailureKind.UNAVAILABLE)
        try:
            values = json.loads(output.decode("ascii", "strict"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise ResolverFailure(ResolverFailureKind.UNAVAILABLE) from None
        if type(values) is not list or any(type(value) is not str for value in values):
            raise ResolverFailure(ResolverFailureKind.UNAVAILABLE)
        return tuple(values)


class _TransportLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _TRANSPORT_LOG_CONTEXT.get()


class ContextTransportLogIsolation:
    """Suppress dependency logs only in the current transport context."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._filter = _TransportLogFilter()
        self._active_contexts = 0
        self._installed_loggers: tuple[logging.Logger, ...] = ()

    @contextmanager
    def __call__(self) -> Iterator[None]:
        entered = False
        token = None
        try:
            with self._lock:
                installed: list[logging.Logger] = []
                if self._active_contexts == 0:
                    try:
                        for name in _TRANSPORT_LOGGERS:
                            logger = logging.getLogger(name)
                            logger.addFilter(self._filter)
                            installed.append(logger)
                    except Exception:
                        for logger in reversed(installed):
                            logger.removeFilter(self._filter)
                        raise
                    self._installed_loggers = tuple(installed)
                self._active_contexts += 1
                entered = True
            token = _TRANSPORT_LOG_CONTEXT.set(True)
            yield
        finally:
            if token is not None:
                _TRANSPORT_LOG_CONTEXT.reset(token)
            if entered:
                with self._lock:
                    self._active_contexts -= 1
                    if self._active_contexts == 0:
                        installed = self._installed_loggers
                        self._installed_loggers = ()
                        for logger in reversed(installed):
                            logger.removeFilter(self._filter)


__all__ = ["ContextTransportLogIsolation", "IsolatedAddressResolverFactory"]
