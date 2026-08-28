from __future__ import annotations

import fcntl
import os
import resource
import struct
import sys

import pytest

from mechanical_librarian.adapters.reviewers import resource_budget
from mechanical_librarian.application.contained_reviewer_contract import (
    COVER_MEMORY_BYTES,
)

_INSTRUCTION = struct.Struct("<HBBI")
_ALLOW = 0x7FFF0000
_ERRNO = 0x00050000
_KILL = 0x80000000


def _evaluate(program: bytes, *, arch: int, syscall: int, argument_0: int = 0) -> int:
    instructions = tuple(
        _INSTRUCTION.unpack_from(program, offset)
        for offset in range(0, len(program), _INSTRUCTION.size)
    )
    accumulator = 0
    position = 0
    while position < len(instructions):
        code, jump_true, jump_false, value = instructions[position]
        if code == 0x20:
            accumulator = {0: syscall, 4: arch, 16: argument_0}[value]
            position += 1
        elif code == 0x15:
            position += 1 + (jump_true if accumulator == value else jump_false)
        elif code == 0x54:
            accumulator &= value
            position += 1
        elif code == 0x06:
            return value
        else:
            raise AssertionError(f"unexpected BPF instruction {code:#x}")
    raise AssertionError("seccomp program did not return")


@pytest.mark.parametrize(
    ("machine", "arch", "clone", "process_syscalls"),
    (
        ("x86_64", 0xC000003E, 56, (57, 58, 272, 308, 435)),
        ("aarch64", 0xC00000B7, 220, (97, 268, 435)),
    ),
)
def test_seccomp_program_allows_threads_and_denies_new_processes(
    machine: str, arch: int, clone: int, process_syscalls: tuple[int, ...]
) -> None:
    program = resource_budget.no_process_seccomp_program(machine)
    assert _evaluate(program, arch=arch, syscall=clone, argument_0=0x00010000) == _ALLOW
    assert _evaluate(program, arch=arch, syscall=clone) & 0xFFFF0000 == _ERRNO
    for syscall in process_syscalls:
        assert _evaluate(program, arch=arch, syscall=syscall) & 0xFFFF0000 == _ERRNO
    assert _evaluate(program, arch=arch, syscall=0) == _ALLOW
    assert _evaluate(program, arch=0, syscall=0) == _KILL


def test_aggregate_budget_sums_to_one_gib_with_and_without_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[tuple[int, int, tuple[int, int]]] = []
    monkeypatch.setattr(resource_budget.os, "getpgid", lambda _pid: 100)
    monkeypatch.setattr(
        resource_budget.resource,
        "prlimit",
        lambda pid, limit, value: applied.append((pid, limit, value)),
        raising=False,
    )

    resource_budget.apply_aggregate_budget(102, 101, 100, 32)
    direct = [value[0] for _pid, limit, value in applied if limit == resource.RLIMIT_AS]
    assert sum(direct) == COVER_MEMORY_BYTES
    assert (102, resource.RLIMIT_NPROC, (32, 32)) in applied

    applied.clear()
    resource_budget.apply_aggregate_budget(102, 101, 100, 32, broker_pid=100)
    brokered = [value for _pid, limit, value in applied if limit == resource.RLIMIT_AS]
    assert brokered == [
        (resource_budget.SUPERVISOR_MEMORY_BYTES, resource_budget.CODEX_MEMORY_BYTES),
        (resource_budget.SUPERVISOR_MEMORY_BYTES,) * 2,
        (resource_budget.BROKER_MEMORY_BYTES,) * 2,
    ]
    assert (
        resource_budget.CODEX_MEMORY_BYTES
        + (2 * resource_budget.SUPERVISOR_MEMORY_BYTES)
        + resource_budget.BROKER_MEMORY_BYTES
        == COVER_MEMORY_BYTES
    )
    assert [pid for pid, limit, _value in applied if limit == resource.RLIMIT_AS] == [
        102,
        101,
        100,
    ]


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(os, "memfd_create"),
    reason="Linux sealed descriptor proof",
)
def test_seccomp_descriptor_is_immutable_and_rewound() -> None:
    with resource_budget.no_process_seccomp_descriptor() as descriptor:
        seals = fcntl.fcntl(descriptor, getattr(fcntl, "F_GET_SEALS", 1034))
        assert seals & 0x000F == 0x000F
        assert os.lseek(descriptor, 0, os.SEEK_CUR) == 0
        assert os.read(descriptor, _INSTRUCTION.size)
