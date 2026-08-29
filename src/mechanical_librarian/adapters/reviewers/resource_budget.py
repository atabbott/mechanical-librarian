"""Aggregate resource boundary for one contained Codex invocation."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import platform
import resource
import select
import struct
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

from mechanical_librarian.application.contained_reviewer_contract import (
    COVER_MEMORY_BYTES,
)

BROKER_MEMORY_BYTES = 64 * 1024 * 1024
SUPERVISOR_MEMORY_BYTES = 64 * 1024 * 1024
CODEX_MEMORY_BYTES = (
    COVER_MEMORY_BYTES - BROKER_MEMORY_BYTES - (2 * SUPERVISOR_MEMORY_BYTES)
)
MAX_SANDBOX_INFO_BYTES = 4_096
_SANDBOX_INFO_FIELDS = (
    frozenset(
        {
            "child-pid",
            "cgroup-namespace",
            "ipc-namespace",
            "mnt-namespace",
            "pid-namespace",
            "uts-namespace",
        }
    ),
    frozenset(
        {
            "child-pid",
            "cgroup-namespace",
            "ipc-namespace",
            "mnt-namespace",
            "net-namespace",
            "pid-namespace",
            "uts-namespace",
        }
    ),
)

_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_ALU_AND_K = 0x54
_BPF_RET_K = 0x06
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_CLONE_THREAD = 0x00010000
_SECCOMP_DATA_ARCH_OFFSET = 4
_SECCOMP_DATA_ARGUMENT_0_OFFSET = 16
_AUDIT_ARCH_X86_64 = 0xC000003E
_AUDIT_ARCH_AARCH64 = 0xC00000B7
_INSTRUCTION = struct.Struct("<HBBI")


class ReviewerResourceError(RuntimeError):
    """A value-free failure to establish the contained resource boundary."""


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """One Linux process identity, stable across PID reuse."""

    pid: int
    process_group: int
    start_time: str


def capture_process_identity(pid: int) -> ProcessIdentity:
    """Capture one bounded exact Linux PID/group/start identity."""
    if type(pid) is not int or pid < 1:
        raise ReviewerResourceError("invalid reviewer resource identity")
    descriptor = -1
    try:
        descriptor = os.open(f"/proc/{pid}/stat", os.O_RDONLY | os.O_CLOEXEC)
        payload = os.read(descriptor, MAX_SANDBOX_INFO_BYTES + 1)
    except OSError as error:
        raise ReviewerResourceError("reviewer resource identity unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > MAX_SANDBOX_INFO_BYTES:
        raise ReviewerResourceError("reviewer resource identity unavailable")
    try:
        text = payload.decode("ascii")
        tail = text[text.rindex(")") + 2 :].split()
        process_group = int(tail[2])
        start_time = tail[19]
    except (UnicodeDecodeError, ValueError, IndexError) as error:
        raise ReviewerResourceError("reviewer resource identity unavailable") from error
    if process_group < 1 or not start_time.isascii() or not start_time.isdigit():
        raise ReviewerResourceError("reviewer resource identity unavailable")
    return ProcessIdentity(pid, process_group, start_time)


def no_process_seccomp_program(machine: str) -> bytes:
    """Return classic BPF that permits threads but denies new processes."""
    if machine in {"x86_64", "amd64"}:
        arch = _AUDIT_ARCH_X86_64
        clone, clone3, fork, vfork, unshare, setns = 56, 435, 57, 58, 272, 308
        instructions: tuple[tuple[int, int, int, int], ...] = (
            (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARCH_OFFSET),
            (_BPF_JMP_JEQ_K, 1, 0, arch),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
            (_BPF_LD_W_ABS, 0, 0, 0),
            (_BPF_JMP_JEQ_K, 6, 0, clone),
            (_BPF_JMP_JEQ_K, 8, 0, clone3),
            (_BPF_JMP_JEQ_K, 7, 0, fork),
            (_BPF_JMP_JEQ_K, 6, 0, vfork),
            (_BPF_JMP_JEQ_K, 5, 0, unshare),
            (_BPF_JMP_JEQ_K, 4, 0, setns),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
            (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARGUMENT_0_OFFSET),
            (_BPF_ALU_AND_K, 0, 0, _CLONE_THREAD),
            (_BPF_JMP_JEQ_K, 0, 1, 0),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
        )
    elif machine in {"aarch64", "arm64"}:
        arch = _AUDIT_ARCH_AARCH64
        clone, clone3, unshare, setns = 220, 435, 97, 268
        instructions = (
            (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARCH_OFFSET),
            (_BPF_JMP_JEQ_K, 1, 0, arch),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
            (_BPF_LD_W_ABS, 0, 0, 0),
            (_BPF_JMP_JEQ_K, 4, 0, clone),
            (_BPF_JMP_JEQ_K, 6, 0, clone3),
            (_BPF_JMP_JEQ_K, 5, 0, unshare),
            (_BPF_JMP_JEQ_K, 4, 0, setns),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
            (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARGUMENT_0_OFFSET),
            (_BPF_ALU_AND_K, 0, 0, _CLONE_THREAD),
            (_BPF_JMP_JEQ_K, 0, 1, 0),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_ERRNO | errno.EPERM),
            (_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
        )
    else:
        raise ReviewerResourceError("unsupported reviewer architecture")
    return b"".join(_INSTRUCTION.pack(*instruction) for instruction in instructions)


@contextmanager
def no_process_seccomp_descriptor() -> Iterator[int]:
    """Expose the immutable architecture-specific filter through one descriptor."""
    descriptor = -1
    try:
        if not all(
            hasattr(os, name)
            for name in ("memfd_create", "MFD_ALLOW_SEALING", "MFD_CLOEXEC")
        ):
            raise ReviewerResourceError("seccomp descriptor unavailable")
        descriptor = os.memfd_create(
            "contained-reviewer-seccomp",
            os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING,
        )
        payload = no_process_seccomp_program(platform.machine())
        if os.write(descriptor, payload) != len(payload):
            raise ReviewerResourceError("seccomp descriptor write failed")
        os.fsync(descriptor)
        seals = 0x0001 | 0x0002 | 0x0004 | 0x0008
        fcntl.fcntl(descriptor, getattr(fcntl, "F_ADD_SEALS", 1033), seals)
        if (
            fcntl.fcntl(descriptor, getattr(fcntl, "F_GET_SEALS", 1034)) & seals
            != seals
        ):
            raise ReviewerResourceError("seccomp descriptor changed")
        os.lseek(descriptor, 0, os.SEEK_SET)
        yield descriptor
    except (OSError, ValueError) as error:
        raise ReviewerResourceError("seccomp descriptor unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_sandbox_child_pid(descriptor: int, deadline: float) -> int:
    """Read Bubblewrap's one bounded child identity document."""
    payload = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            readable, _, _ = select.select((descriptor,), (), (), remaining)
            if not readable:
                raise TimeoutError
            chunk = os.read(
                descriptor,
                MAX_SANDBOX_INFO_BYTES + 1 - len(payload),
            )
        except OSError as error:
            raise ReviewerResourceError("sandbox identity unavailable") from error
        if not chunk:
            break
        payload.extend(chunk)
        if len(payload) > MAX_SANDBOX_INFO_BYTES:
            raise ReviewerResourceError("sandbox identity unavailable")
    try:
        document = json.loads(payload)
        sandbox_pid = document["child-pid"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReviewerResourceError("sandbox identity unavailable") from error
    if type(document) is not dict or frozenset(document) not in _SANDBOX_INFO_FIELDS:
        raise ReviewerResourceError("sandbox identity unavailable")
    if any(
        type(document[field]) is not int or document[field] < 1 for field in document
    ):
        raise ReviewerResourceError("sandbox identity unavailable")
    if sandbox_pid < 2:
        raise ReviewerResourceError("sandbox identity unavailable")
    return cast(int, sandbox_pid)


def apply_aggregate_budget(
    broker: ProcessIdentity,
    supervisor: ProcessIdentity,
    sandbox: ProcessIdentity,
    process_limit: int,
) -> None:
    """Split the fixed 1 GiB ceiling across every permitted address space."""
    identities = (broker, supervisor, sandbox)
    if (
        any(type(value) is not ProcessIdentity for value in identities)
        or any(
            type(value.pid) is not int
            or value.pid < 1
            or type(value.process_group) is not int
            or value.process_group < 1
            or type(value.start_time) is not str
            or not value.start_time
            or not value.start_time.isascii()
            or not value.start_time.isdigit()
            for value in identities
        )
        or len({value.pid for value in identities}) != 3
        or len({value.process_group for value in identities}) != 1
        or broker.process_group != broker.pid
        or type(process_limit) is not int
        or process_limit < 1
    ):
        raise ReviewerResourceError("invalid reviewer resource identity")
    try:
        _require_current_identities(identities)
        resource.prlimit(
            sandbox.pid,
            resource.RLIMIT_AS,
            (SUPERVISOR_MEMORY_BYTES, CODEX_MEMORY_BYTES),
        )
        resource.prlimit(
            sandbox.pid,
            resource.RLIMIT_NPROC,
            (process_limit, process_limit),
        )
        resource.prlimit(
            supervisor.pid,
            resource.RLIMIT_AS,
            (SUPERVISOR_MEMORY_BYTES, SUPERVISOR_MEMORY_BYTES),
        )
        resource.prlimit(
            broker.pid,
            resource.RLIMIT_AS,
            (BROKER_MEMORY_BYTES, BROKER_MEMORY_BYTES),
        )
        _require_current_identities(identities)
    except (OSError, ValueError) as error:
        raise ReviewerResourceError("reviewer resource admission failed") from error


def _require_current_identities(identities: tuple[ProcessIdentity, ...]) -> None:
    if any(capture_process_identity(value.pid) != value for value in identities):
        raise ReviewerResourceError("reviewer resource identity changed")


__all__ = [
    "BROKER_MEMORY_BYTES",
    "CODEX_MEMORY_BYTES",
    "ProcessIdentity",
    "ReviewerResourceError",
    "SUPERVISOR_MEMORY_BYTES",
    "apply_aggregate_budget",
    "capture_process_identity",
    "no_process_seccomp_descriptor",
    "no_process_seccomp_program",
    "read_sandbox_child_pid",
]
