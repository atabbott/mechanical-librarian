from __future__ import annotations

import fcntl
import json
import os
import resource
import struct
import sys
import time
from typing import Any

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


@pytest.mark.parametrize("network", (False, True))
def test_sandbox_identity_accepts_only_shipped_namespace_documents(
    monkeypatch: pytest.MonkeyPatch, network: bool
) -> None:
    document = {
        "child-pid": 102,
        "cgroup-namespace": 1,
        "ipc-namespace": 2,
        "mnt-namespace": 3,
        "pid-namespace": 4,
        "uts-namespace": 5,
    }
    if network:
        document["net-namespace"] = 6
    payload = json.dumps(document).encode("ascii")
    reads = iter((payload, b""))
    monkeypatch.setattr(
        resource_budget.select, "select", lambda *_args: ((10,), (), ())
    )
    monkeypatch.setattr(resource_budget.os, "read", lambda *_args: next(reads))
    assert resource_budget.read_sandbox_child_pid(10, time.monotonic() + 1) == 102


def test_aggregate_budget_sums_to_one_gib_for_exact_brokered_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[tuple[int, int, tuple[int, int]]] = []
    identities = {
        pid: resource_budget.ProcessIdentity(pid, 100, str(pid * 10))
        for pid in (100, 101, 102)
    }
    monkeypatch.setattr(
        resource_budget, "capture_process_identity", lambda pid: identities[pid]
    )
    monkeypatch.setattr(
        resource_budget.resource,
        "prlimit",
        lambda pid, limit, value: applied.append((pid, limit, value)),
        raising=False,
    )

    resource_budget.apply_aggregate_budget(
        identities[100], identities[101], identities[102], 32
    )
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


def test_aggregate_budget_rejects_identity_change_before_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = resource_budget.ProcessIdentity(100, 100, "1000")
    supervisor = resource_budget.ProcessIdentity(101, 100, "1010")
    sandbox = resource_budget.ProcessIdentity(102, 100, "1020")
    observations = iter(
        (
            broker,
            supervisor,
            sandbox,
            broker,
            supervisor,
            resource_budget.ProcessIdentity(102, 100, "9999"),
        )
    )
    monkeypatch.setattr(
        resource_budget, "capture_process_identity", lambda _pid: next(observations)
    )
    monkeypatch.setattr(
        resource_budget.resource, "prlimit", lambda *_args: None, raising=False
    )

    with pytest.raises(resource_budget.ReviewerResourceError, match="identity"):
        resource_budget.apply_aggregate_budget(broker, supervisor, sandbox, 32)


def test_process_identity_capture_rejects_unknown_and_malformed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resource_budget.os,
        "open",
        lambda *_args: (_ for _ in ()).throw(FileNotFoundError()),
    )
    with pytest.raises(resource_budget.ReviewerResourceError, match="unavailable"):
        resource_budget.capture_process_identity(100)

    monkeypatch.setattr(resource_budget.os, "open", lambda *_args: 10)
    monkeypatch.setattr(resource_budget.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(resource_budget.os, "read", lambda *_args: b"malformed")
    with pytest.raises(resource_budget.ReviewerResourceError, match="unavailable"):
        resource_budget.capture_process_identity(100)


@pytest.mark.parametrize(
    "identity",
    (
        resource_budget.ProcessIdentity(0, 100, "1"),
        resource_budget.ProcessIdentity(100, 0, "1"),
        resource_budget.ProcessIdentity(100, 100, ""),
    ),
)
def test_aggregate_budget_rejects_malformed_identity(
    identity: resource_budget.ProcessIdentity,
) -> None:
    valid = resource_budget.ProcessIdentity(101, 100, "2")
    with pytest.raises(resource_budget.ReviewerResourceError, match="identity"):
        resource_budget.apply_aggregate_budget(identity, valid, valid, 32)


@pytest.mark.parametrize("process_limit", (True, 1.0))
def test_aggregate_budget_rejects_noncanonical_process_limit(
    process_limit: Any,
) -> None:
    broker = resource_budget.ProcessIdentity(100, 100, "1")
    supervisor = resource_budget.ProcessIdentity(101, 100, "2")
    sandbox = resource_budget.ProcessIdentity(102, 100, "3")
    with pytest.raises(resource_budget.ReviewerResourceError, match="identity"):
        resource_budget.apply_aggregate_budget(
            broker, supervisor, sandbox, process_limit
        )


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
