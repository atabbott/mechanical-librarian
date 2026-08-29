"""Install the admitted inner filter, then replace this payload with Codex."""

from __future__ import annotations

import ctypes
import os
import resource
import struct
import sys
from pathlib import Path

_INSTRUCTION = struct.Struct("<HBBI")
_MAX_PROGRAM_BYTES = 4_096
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2


class _Filter(ctypes.Structure):
    _fields_ = (("length", ctypes.c_ushort), ("filter", ctypes.c_void_p))


def main(arguments: list[str] | None = None) -> None:
    values = sys.argv[1:] if arguments is None else arguments
    if len(values) < 2 or values[0] != "/runtime/seccomp.bpf":
        raise SystemExit(126)
    payload = Path(values[0]).read_bytes()
    if not payload or len(payload) > _MAX_PROGRAM_BYTES or len(payload) % 8:
        raise SystemExit(126)
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    if hard == resource.RLIM_INFINITY:
        raise SystemExit(126)
    resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
    buffer = ctypes.create_string_buffer(payload)
    program = _Filter(len(payload) // _INSTRUCTION.size, ctypes.addressof(buffer))
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise SystemExit(126)
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.byref(program)) != 0:
        raise SystemExit(126)
    os.execve(values[1], values[1:], os.environ)


if __name__ == "__main__":
    main()
