"""Confine one process that parses untrusted bytes, before it reads any.

Every parser child (the EPUB, audio and cover decoders, and `ffprobe` through
its shim) calls one entry point here first. Two kernel mechanisms do the work:

- Landlock: the process may read only the descriptors it was handed and the
  read-only program and library code it runs from. It can create, write,
  rename or remove nothing by path, open no TCP connection and, where the
  kernel knows how, signal or reach no process outside itself. A Landlock
  domain also stops it tracing or reading the memory of any process outside
  it, such as the server.
- seccomp: a filter that first checks the architecture (a foreign or x32
  system call kills the process), then refuses sockets, io_uring, new
  processes, namespace changes and cross-process access. Threads still work.

Landlock is best effort by kernel: where the kernel has none, or the
container's profile refuses it, the child keeps the seccomp filter and its
resource limits and the entry point returns 0. Any failure after the kernel
said it supports Landlock is raised, never half applied. The seccomp filter
is mandatory on Linux. Other platforms are development-only and unconfined.

This module is stdlib only, so a child imports nothing parent-side for it.
"""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import struct
import sys
from typing import NamedTuple

# Landlock (include/uapi/linux/landlock.h).
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_EXECUTE = 1 << 0
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
# Every filesystem right each ABI handles: 13 rights in ABI 1, then REFER
# (2), TRUNCATE (3) and IOCTL_DEV (5).
_FS_RIGHTS_BY_ABI = ((1, (1 << 13) - 1), (2, 1 << 13), (3, 1 << 14), (5, 1 << 15))
_NET_TCP = (1 << 0) | (1 << 1)  # bind and connect, ABI 4
_SCOPES = (1 << 0) | (1 << 1)  # abstract unix sockets and signals, ABI 6
_UNSUPPORTED = frozenset({errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM})
_LIBRARY_DIRECTORIES = ("/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/local/lib")
_LOADER_CACHE = "/etc/ld.so.cache"

# Classic BPF over `struct seccomp_data` (nr, arch, ip, args).
_INSTRUCTION = struct.Struct("<HBBI")
_LD_W_ABS = 0x20
_JEQ_K = 0x15
_JGE_K = 0x35
_AND_K = 0x54
_RET_K = 0x06
_ARCH_OFFSET = 4
_ARGUMENT_0_OFFSET = 16
_RET_KILL_PROCESS = 0x80000000
_RET_ERRNO = 0x00050000
_RET_ALLOW = 0x7FFF0000
_X32_BIT = 0x40000000
_CLONE_THREAD = 0x00010000
_PR_SET_NO_NEW_PRIVS = 38
_SECCOMP_SET_MODE_FILTER = 1
_SECCOMP_FILTER_FLAG_TSYNC = 1


# Per machine: audit architecture, the syscall numbers this module calls, and
# the refused calls. `clone` and `clone3` are handled apart; `fork` and
# `vfork` exist on x86_64 only.
class _Machine(NamedTuple):
    arch: int
    x32: bool
    seccomp: int
    landlock: tuple[int, int, int]
    clone: int
    clone3: int
    exec: tuple[int, int]
    refused: tuple[int, ...]


_MACHINES = {
    "x86_64": _Machine(
        arch=0xC000003E,
        x32=True,
        seccomp=317,
        landlock=(444, 445, 446),
        clone=56,
        clone3=435,
        exec=(59, 322),
        # socket, socketpair, io_uring_setup/enter/register, ptrace,
        # process_vm_readv/writev, pidfd_open, pidfd_getfd, unshare, setns,
        # fork, vfork, bpf, perf_event_open, userfaultfd, keyctl, add_key,
        # request_key.
        refused=(41, 53, 425, 426, 427, 101, 310, 311, 434, 438, 272, 308, 57, 58)
        + (321, 298, 323, 250, 248, 249),
    ),
    "aarch64": _Machine(
        arch=0xC00000B7,
        x32=False,
        seccomp=277,
        landlock=(444, 445, 446),
        clone=220,
        clone3=435,
        exec=(221, 281),
        # The same calls in the same order, without fork and vfork.
        refused=(198, 199, 425, 426, 427, 117, 270, 271, 434, 438, 97, 268)
        + (280, 241, 282, 219, 217, 218),
    ),
}
_MACHINE_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}


class ConfinementError(OSError):
    """The child could not be confined, so it must not parse anything."""


def _machine(value: str | None = None) -> _Machine:
    name = platform.machine() if value is None else value
    machine = _MACHINES.get(_MACHINE_ALIASES.get(name, name))
    if machine is None:
        raise ConfinementError("parser confinement is unavailable on this machine")
    return machine


def filter_program(machine_name: str, *, allow_exec: bool) -> bytes:
    """Return the classic BPF program for one machine."""
    machine = _machine(machine_name)
    instructions: list[tuple[int, int, int, int]] = [
        (_LD_W_ABS, 0, 0, _ARCH_OFFSET),
        (_JEQ_K, 1, 0, machine.arch),
        (_RET_K, 0, 0, _RET_KILL_PROCESS),
        (_LD_W_ABS, 0, 0, 0),
    ]
    if machine.x32:
        instructions.extend(
            ((_JGE_K, 0, 1, _X32_BIT), (_RET_K, 0, 0, _RET_KILL_PROCESS))
        )
    numbers = machine.refused if allow_exec else (*machine.refused, *machine.exec)
    for number in numbers:
        instructions.extend(
            (
                (_JEQ_K, 0, 1, number),
                (_RET_K, 0, 0, _RET_ERRNO | errno.EPERM),
            )
        )
    instructions.extend(
        (
            (_JEQ_K, 0, 1, machine.clone3),
            (_RET_K, 0, 0, _RET_ERRNO | errno.ENOSYS),
            (_JEQ_K, 0, 5, machine.clone),
            (_LD_W_ABS, 0, 0, _ARGUMENT_0_OFFSET),
            (_AND_K, 0, 0, _CLONE_THREAD),
            (_JEQ_K, 1, 0, 0),
            (_RET_K, 0, 0, _RET_ALLOW),
            (_RET_K, 0, 0, _RET_ERRNO | errno.EPERM),
            (_RET_K, 0, 0, _RET_ALLOW),
        )
    )
    return b"".join(_INSTRUCTION.pack(*value) for value in instructions)


class _Program(ctypes.Structure):
    _fields_ = (("length", ctypes.c_ushort), ("filter", ctypes.c_void_p))


def _libc() -> ctypes.CDLL:
    library = ctypes.CDLL(None, use_errno=True)
    library.syscall.restype = ctypes.c_long
    return library


def _install_filter(machine: _Machine, *, allow_exec: bool) -> None:
    library = _libc()
    payload = filter_program(platform.machine(), allow_exec=allow_exec)
    buffer = ctypes.create_string_buffer(payload, len(payload))
    program = _Program(len(payload) // _INSTRUCTION.size, ctypes.addressof(buffer))
    if (
        library.syscall(
            ctypes.c_long(machine.seccomp),
            ctypes.c_long(_SECCOMP_SET_MODE_FILTER),
            ctypes.c_long(_SECCOMP_FILTER_FLAG_TSYNC),
            ctypes.byref(program),
        )
        != 0
    ):
        raise ConfinementError(ctypes.get_errno(), "seccomp filter is unavailable")


def landlock_abi() -> int:
    """Return the kernel's Landlock ABI here, or 0 when it is unavailable."""
    if not sys.platform.startswith("linux"):
        return 0
    create = _machine().landlock[0]
    library = _libc()
    version = library.syscall(
        ctypes.c_long(create),
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint(_CREATE_RULESET_VERSION),
    )
    if version < 0:
        failure = ctypes.get_errno()
        if failure in _UNSUPPORTED:
            return 0
        raise ConfinementError(failure, "Landlock is unavailable")
    return int(version)


def _fs_rights(abi: int) -> int:
    return sum(rights for first, rights in _FS_RIGHTS_BY_ABI if abi >= first)


def _restrict(
    machine: _Machine,
    abi: int,
    directories: tuple[str, ...],
    files: tuple[tuple[int | str, int], ...],
) -> None:
    create, add_rule, restrict_self = machine.landlock
    library = _libc()
    attribute = struct.pack(
        "<QQQ",
        _fs_rights(abi),
        _NET_TCP if abi >= 4 else 0,
        _SCOPES if abi >= 6 else 0,
    )[: 8 if abi < 4 else 16 if abi < 6 else 24]
    ruleset = library.syscall(
        ctypes.c_long(create),
        ctypes.c_char_p(attribute),
        ctypes.c_size_t(len(attribute)),
        ctypes.c_uint(0),
    )
    if ruleset < 0:
        raise ConfinementError(ctypes.get_errno(), "Landlock ruleset failed")
    try:
        rules: list[tuple[int | str, int]] = [
            (path, _READ_FILE | _READ_DIR) for path in directories
        ]
        rules.extend(files)
        for target, access in rules:
            _add_rule(library, add_rule, ruleset, target, access)
        if library.syscall(ctypes.c_long(restrict_self), ruleset, 0) != 0:
            raise ConfinementError(ctypes.get_errno(), "Landlock restriction failed")
    finally:
        os.close(ruleset)


def _add_rule(
    library: ctypes.CDLL, add_rule: int, ruleset: int, target: int | str, access: int
) -> None:
    if isinstance(target, str):
        descriptor = os.open(target, os.O_PATH | os.O_CLOEXEC)
        owned = True
    else:
        descriptor, owned = target, False
    try:
        attribute = struct.pack("<Qi", access, descriptor)
        if (
            library.syscall(
                ctypes.c_long(add_rule),
                ctypes.c_long(ruleset),
                ctypes.c_long(_RULE_PATH_BENEATH),
                ctypes.c_char_p(attribute),
                ctypes.c_uint(0),
            )
            != 0
        ):
            raise ConfinementError(ctypes.get_errno(), "Landlock rule failed")
    finally:
        if owned:
            os.close(descriptor)


def _readable_roots(paths: list[str]) -> tuple[str, ...]:
    roots: list[str] = []
    for value in (*paths, *_LIBRARY_DIRECTORIES):
        if not value or not os.path.isdir(value):
            continue
        resolved = os.path.realpath(value)
        if resolved == "/":
            raise ConfinementError("a parser child may not read the whole filesystem")
        if resolved not in roots:
            roots.append(resolved)
    return tuple(roots)


def _confine(
    directories: tuple[str, ...],
    files: tuple[tuple[int | str, int], ...],
    *,
    allow_exec: bool,
) -> int:
    machine = _machine()
    if _libc().prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise ConfinementError(ctypes.get_errno(), "no_new_privs is unavailable")
    abi = landlock_abi()
    if abi:
        loader = ((_LOADER_CACHE, _READ_FILE),) if os.path.isfile(_LOADER_CACHE) else ()
        _restrict(machine, abi, directories, (*loader, *files))
    _install_filter(machine, allow_exec=allow_exec)
    return abi


def confine_interpreter() -> int:
    """Confine this Python parser child; return the Landlock ABI applied.

    Call it before any thread starts and before any untrusted byte is read.
    Code stays importable (each `sys.path` directory and the system library
    directories are readable); nothing else is.
    """
    if not sys.platform.startswith("linux"):
        return 0
    return _confine(_readable_roots(list(sys.path)), (), allow_exec=False)


def _elf_interpreter(binary: str) -> str | None:
    """Return the dynamic loader a 64-bit little-endian ELF names, if any.

    The kernel opens that loader as an executable too, so it needs the same
    execute right as the binary. A static binary names none.
    """
    with open(binary, "rb") as handle:
        header = handle.read(64)
        if len(header) < 64 or header[:6] != b"\x7fELF\x02\x01":
            raise ConfinementError("probe is not a 64-bit little-endian ELF")
        (offset,) = struct.unpack_from("<Q", header, 32)  # e_phoff
        size, count = struct.unpack_from("<HH", header, 54)  # e_phentsize, e_phnum
        if size < 56 or count > 64:
            raise ConfinementError("probe has an unexpected program header")
        handle.seek(offset)
        table = handle.read(size * count)
        for index in range(count):
            kind, _flags, start, _vaddr, _paddr, length = struct.unpack_from(
                "<IIQQQQ", table, index * size
            )
            if kind == 3:  # PT_INTERP
                if not 1 < length <= 4096:
                    raise ConfinementError("probe names an unexpected loader")
                handle.seek(start)
                return os.fsdecode(handle.read(length).rstrip(b"\0"))
    return None


def confine_probe(binary: str, descriptor: int) -> int:
    """Confine the probe shim just before it executes `binary`.

    The probe may execute only `binary`, read the system libraries it links,
    and read the one media descriptor it was handed, through that descriptor
    or `/dev/fd`. Landlock and the filter survive the exec.
    """
    if not sys.platform.startswith("linux"):
        return 0
    executables = [os.path.realpath(binary)]
    loader = _elf_interpreter(executables[0])
    if loader is not None:
        executables.append(os.path.realpath(loader))
    return _confine(
        _readable_roots([]),
        (
            *((path, _READ_FILE | _EXECUTE) for path in executables),
            (descriptor, _READ_FILE),
        ),
        allow_exec=True,
    )


__all__ = [
    "ConfinementError",
    "confine_interpreter",
    "confine_probe",
    "filter_program",
    "landlock_abi",
]
