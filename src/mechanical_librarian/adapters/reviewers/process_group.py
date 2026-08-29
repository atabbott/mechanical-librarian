"""Exact process-group identity and whole-tree cleanup for reviewer commands."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


class ReviewerProcessGroupError(RuntimeError):
    """Value-free failure to own or prove a reviewer process tree dead."""


@dataclass(frozen=True, slots=True)
class ReviewerProcessGroup:
    pid: int
    pgid: int
    start_token: str


def capture_process_group(process: subprocess.Popen[bytes]) -> ReviewerProcessGroup:
    pid = process.pid
    try:
        token, pgid = parse_proc_identity(
            Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ReviewerProcessGroupError("runtime") from error
    if pid < 2 or pgid != pid:
        raise ReviewerProcessGroupError("runtime")
    return ReviewerProcessGroup(pid, pgid, token)


def kill_spawned_process_group(process: subprocess.Popen[bytes]) -> None:
    """Clean a new session while its unreaped child PID still prevents reuse."""
    pid = process.pid
    try:
        if os.getpgid(pid) != pid:
            raise ReviewerProcessGroupError("runtime")
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as error:
        raise ReviewerProcessGroupError("runtime") from error
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise ReviewerProcessGroupError("runtime") from error
    reap_process_group(pid, 5)


def finish_process_group(
    identity: ReviewerProcessGroup,
) -> None:
    """Accept normal leader exit only after the complete captured group is gone."""
    try:
        reap_process_group(identity.pgid, 0.1)
    except ReviewerProcessGroupError as error:
        kill_process_group(identity)
        raise ReviewerProcessGroupError("runtime") from error


def terminate_process_group(
    identity: ReviewerProcessGroup,
) -> None:
    """TERM, then KILL if necessary, and prove the captured group empty."""
    if require_signal_identity(identity):
        try:
            os.killpg(identity.pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise ReviewerProcessGroupError("runtime") from error
    try:
        reap_process_group(identity.pgid, 5)
    except ReviewerProcessGroupError:
        kill_process_group(identity)
    if process_identity_alive(identity):
        raise ReviewerProcessGroupError("runtime")


def kill_process_group(identity: ReviewerProcessGroup) -> None:
    if require_signal_identity(identity):
        try:
            os.killpg(identity.pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as error:
            raise ReviewerProcessGroupError("runtime") from error
    try:
        wait_for_exit(identity.pid, 5)
    except TimeoutError as error:
        raise ReviewerProcessGroupError("runtime") from error
    reap_process_group(identity.pgid, 5)
    if process_identity_alive(identity):
        raise ReviewerProcessGroupError("runtime")


def reap_process_group(pgid: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        while True:
            try:
                waited, _status = os.waitpid(-pgid, os.WNOHANG)
            except ChildProcessError:
                break
            if waited == 0:
                break
        if not process_group_alive(pgid):
            return
        if time.monotonic() >= deadline:
            raise ReviewerProcessGroupError("runtime")
        time.sleep(0.01)


def wait_for_exit(pid: int, timeout_seconds: float) -> int:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            waited, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return 0
        if waited == pid:
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            if os.WIFSIGNALED(status):
                return 128 + os.WTERMSIG(status)
            return 126
        if time.monotonic() >= deadline:
            raise TimeoutError
        time.sleep(0.01)


def require_signal_identity(identity: ReviewerProcessGroup) -> bool:
    _validate_identity(identity)
    try:
        raw = Path(f"/proc/{identity.pid}/stat").read_text(encoding="ascii")
    except FileNotFoundError:
        return process_group_alive(identity.pgid)
    except (OSError, UnicodeError) as error:
        raise ReviewerProcessGroupError("runtime") from error
    try:
        token, pgid = parse_proc_identity(raw)
    except ValueError as error:
        raise ReviewerProcessGroupError("runtime") from error
    if token != identity.start_token or pgid != identity.pgid:
        raise ReviewerProcessGroupError("runtime")
    return True


def process_identity_alive(identity: ReviewerProcessGroup) -> bool:
    _validate_identity(identity)
    try:
        if (
            os.getpgid(identity.pid) == identity.pgid
            and capture_process_group_token(identity.pid) == identity.start_token
        ):
            return True
    except (OSError, ReviewerProcessGroupError):
        pass
    return process_group_alive(identity.pgid)


def capture_process_group_token(pid: int) -> str:
    try:
        token, _ = parse_proc_identity(
            Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ReviewerProcessGroupError("runtime") from error
    return token


def process_group_alive(pgid: int) -> bool:
    if type(pgid) is not int or pgid < 2:
        raise ReviewerProcessGroupError("runtime")
    try:
        names = os.listdir("/proc")
    except OSError as error:
        raise ReviewerProcessGroupError("runtime") from error
    for name in names:
        if not name.isascii() or not name.isdigit():
            continue
        try:
            raw = Path("/proc", name, "stat").read_text(encoding="ascii")
            _, member_group = parse_proc_identity(raw)
        except (OSError, UnicodeError, ValueError):
            continue
        if member_group == pgid:
            return True
    return False


def _validate_identity(identity: ReviewerProcessGroup) -> None:
    if (
        type(identity) is not ReviewerProcessGroup
        or type(identity.pid) is not int
        or type(identity.pgid) is not int
        or identity.pid < 2
        or identity.pgid < 2
        or type(identity.start_token) is not str
        or not identity.start_token.isascii()
        or not identity.start_token.isdigit()
    ):
        raise ReviewerProcessGroupError("runtime")


def parse_proc_identity(raw: str) -> tuple[str, int]:
    try:
        tail = raw[raw.rindex(")") + 2 :].split()
        process_group = int(tail[2], 10)
        start_token = tail[19]
    except (IndexError, ValueError) as error:
        raise ValueError("invalid proc stat") from error
    if not start_token.isascii() or not start_token.isdigit() or process_group < 1:
        raise ValueError("invalid proc stat")
    return start_token, process_group
