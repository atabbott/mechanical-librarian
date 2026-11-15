"""Parser children read only what they were handed and reach nothing else."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import platform
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters import child_confinement

_INSTRUCTION = struct.Struct("<HBBI")
_ALLOW = 0x7FFF0000
_KILL = 0x80000000
_EPERM = 0x00050000 | errno.EPERM
_ENOSYS = 0x00050000 | errno.ENOSYS
_MACHINES = {
    # machine: (arch, socket, execve, clone, clone3, getpid, ptrace, socketpair)
    "x86_64": (0xC000003E, 41, 59, 56, 435, 39, 101, 53),
    "aarch64": (0xC00000B7, 198, 221, 220, 435, 172, 117, 199),
}
_LINUX = sys.platform.startswith("linux") and platform.machine() in _MACHINES
linux_only = pytest.mark.skipif(not _LINUX, reason="Linux parser confinement")
_SOURCE = str(Path(child_confinement.__file__).parents[2])


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
        elif code == 0x35:
            position += 1 + (jump_true if accumulator >= value else jump_false)
        elif code == 0x54:
            accumulator &= value
            position += 1
        elif code == 0x06:
            return value
        else:
            raise AssertionError(f"unexpected BPF instruction {code:#x}")
    raise AssertionError("seccomp program did not return")


@pytest.mark.parametrize("machine", tuple(_MACHINES))
def test_the_filter_checks_the_architecture_before_any_number(machine: str) -> None:
    arch, socket_call, _execve, _clone, _clone3, getpid, _ptrace, _pair = _MACHINES[
        machine
    ]
    program = child_confinement.filter_program(machine, allow_exec=False)
    for foreign in (0, 0x40000003, 0xC000003E ^ 0xC00000B7 ^ arch):
        for syscall in (socket_call, getpid, 102, 359):
            assert _evaluate(program, arch=foreign, syscall=syscall) == _KILL
    assert _evaluate(program, arch=arch, syscall=getpid) == _ALLOW


def test_the_filter_kills_x32_numbers_that_share_the_x86_64_arch() -> None:
    program = child_confinement.filter_program("x86_64", allow_exec=False)
    for syscall in (0x40000000 | 41, 0x40000000 | 39, 0x40000000, 0x7FFFFFFF):
        assert _evaluate(program, arch=0xC000003E, syscall=syscall) == _KILL


@pytest.mark.parametrize("machine", tuple(_MACHINES))
def test_the_filter_refuses_network_processes_and_exec(machine: str) -> None:
    arch, socket_call, execve, clone, clone3, getpid, ptrace, pair = _MACHINES[machine]
    program = child_confinement.filter_program(machine, allow_exec=False)
    for syscall in (socket_call, pair, execve, ptrace, 425, 434, 438):
        assert _evaluate(program, arch=arch, syscall=syscall) == _EPERM
    assert _evaluate(program, arch=arch, syscall=clone) == _EPERM
    assert _evaluate(program, arch=arch, syscall=clone, argument_0=0x3D0F00) == _ALLOW
    assert _evaluate(program, arch=arch, syscall=clone3) == _ENOSYS
    assert _evaluate(program, arch=arch, syscall=getpid) == _ALLOW
    probe = child_confinement.filter_program(machine, allow_exec=True)
    assert _evaluate(probe, arch=arch, syscall=execve) == _ALLOW
    assert _evaluate(probe, arch=arch, syscall=socket_call) == _EPERM


def test_an_unknown_machine_is_refused_rather_than_left_open() -> None:
    with pytest.raises(child_confinement.ConfinementError):
        child_confinement.filter_program("riscv64", allow_exec=False)


def test_a_root_of_the_whole_filesystem_is_never_made_readable() -> None:
    with pytest.raises(child_confinement.ConfinementError):
        child_confinement._readable_roots(["/usr/lib/../.."])


class _FakeLibc:
    def __init__(self, failure: int) -> None:
        self.failure = failure

    def syscall(self, *_arguments: object) -> int:
        ctypes.set_errno(self.failure)
        return -1


@pytest.mark.parametrize(
    "failure",
    (errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM),
    ids=("not_built", "disabled_at_boot", "refused_by_profile"),
)
def test_a_kernel_without_landlock_reads_as_abi_zero(failure: int) -> None:
    with (
        patch.object(child_confinement.sys, "platform", "linux"),
        patch.object(child_confinement.platform, "machine", return_value="x86_64"),
        patch.object(child_confinement, "_libc", return_value=_FakeLibc(failure)),
    ):
        assert child_confinement.landlock_abi() == 0


def test_any_other_landlock_failure_is_raised() -> None:
    with (
        patch.object(child_confinement.sys, "platform", "linux"),
        patch.object(child_confinement.platform, "machine", return_value="x86_64"),
        patch.object(child_confinement, "_libc", return_value=_FakeLibc(errno.EINVAL)),
        pytest.raises(child_confinement.ConfinementError),
    ):
        child_confinement.landlock_abi()


@pytest.fixture
def secret() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "application.sqlite3"
        path.write_text("stand-in credential", encoding="utf-8")
        path.chmod(0o600)
        yield path


@pytest.fixture
def listener() -> Iterator[tuple[int, list[bytes]]]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(5)
    received: list[bytes] = []

    def accept() -> None:
        try:
            connection, _address = server.accept()
            received.append(connection.recv(100))
            connection.close()
        except OSError:
            pass

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield server.getsockname()[1], received
    server.close()


_PROBES = """
import json, os, socket, sys, threading
def attempt(action):
    try:
        action()
        return "allowed"
    except Exception as error:
        return error.__class__.__name__
def connect():
    connection = socket.create_connection(("127.0.0.1", PORT), timeout=3)
    connection.sendall(b"leak")
    connection.close()
def thread():
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join()
def report(extra=None):
    result = {
        "read_other": attempt(lambda: open(SECRET, "rb").read()),
        "append_other": attempt(lambda: open(SECRET, "ab").close()),
        "create": attempt(lambda: open(SECRET + ".new", "wb").close()),
        "connect": attempt(connect),
        "socketpair": attempt(socket.socketpair),
        "fork": attempt(os.fork),
        "exec": attempt(lambda: os.execv("/bin/true", ["true"])),
        "proc_of_parent": attempt(
            lambda: open(f"/proc/{os.getppid()}/environ", "rb").read()
        ),
        "lazy_import": attempt(lambda: __import__("encodings.cp1252")),
        "thread": attempt(thread),
    }
    result.update(extra or {})
    os.write(2, json.dumps(result).encode() + b"\\n")
"""


def _run(body: str, secret: Path, port: int, **kwargs: object) -> tuple[int, dict]:
    program = (
        f"SECRET = {str(secret)!r}\nPORT = {port}\n" + _PROBES + textwrap.dedent(body)
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program],
        capture_output=True,
        cwd="/",
        env={"PYTHONPATH": _SOURCE, "LC_ALL": "C.UTF-8"},
        timeout=30,
        check=False,
        **kwargs,  # type: ignore[call-overload]  # reason: pass_fds and stdin only
    )
    lines = completed.stderr.decode().strip().splitlines()
    return completed.returncode, json.loads(lines[-1]) if lines else {}


_CONFINED = {
    "read_other": "PermissionError",
    "append_other": "PermissionError",
    "create": "PermissionError",
    "connect": "PermissionError",
    "socketpair": "PermissionError",
    "fork": "PermissionError",
    "exec": "PermissionError",
    "proc_of_parent": "PermissionError",
    "lazy_import": "allowed",
    "thread": "allowed",
}


@linux_only
def test_a_confined_interpreter_reads_only_its_descriptor_and_code(
    secret: Path, listener: tuple[int, list[bytes]]
) -> None:
    port, received = listener
    with secret.open("rb") as handed:
        code, result = _run(
            f"""
            from mechanical_librarian.adapters.child_confinement import (
                confine_interpreter,
            )
            abi = confine_interpreter()
            report({{"abi": abi, "handed": os.pread({handed.fileno()}, 8, 0).decode()}})
            """,
            secret,
            port,
            pass_fds=(handed.fileno(),),
        )
    assert code == 0
    if result.pop("abi") == 0:
        pytest.skip("this kernel has no Landlock; the degraded test covers it")
    assert result == {**_CONFINED, "handed": "stand-in"}
    assert received == []
    assert secret.read_text(encoding="utf-8") == "stand-in credential"


@linux_only
def test_without_landlock_the_filter_and_limits_still_hold(
    secret: Path, listener: tuple[int, list[bytes]]
) -> None:
    """The stated degraded state: sockets, processes and exec stay refused;
    reading and writing by path is then down to file permissions."""
    port, received = listener
    code, result = _run(
        """
        from mechanical_librarian.adapters import child_confinement
        child_confinement.landlock_abi = lambda: 0
        report({"abi": child_confinement.confine_interpreter()})
        """,
        secret,
        port,
    )
    assert code == 0
    assert result == {
        **_CONFINED,
        "abi": 0,
        "read_other": "allowed",
        "append_other": "allowed",
        "create": "allowed",
        "proc_of_parent": "allowed",
    }
    assert received == []


@linux_only
@pytest.mark.skipif(platform.machine() != "x86_64", reason="i386 entry is x86 only")
def test_the_32_bit_entry_point_kills_the_child(secret: Path) -> None:
    code, _result = _run(
        """
        import ctypes, mmap
        from mechanical_librarian.adapters.child_confinement import (
            confine_interpreter,
        )
        confine_interpreter()
        # socket(AF_INET, SOCK_STREAM, 0) as i386 system call 359 via int 0x80.
        machine_code = bytes.fromhex("53b867010000bb02000000b901000000ba00000000cd805bc3")
        page = mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
        page.write(machine_code)
        address = ctypes.addressof(ctypes.c_char.from_buffer(page))
        ctypes.CFUNCTYPE(ctypes.c_int)(address)()
        report()
        """,
        secret,
        0,
    )
    assert code == -signal.SIGSYS


@linux_only
def test_the_probe_reads_only_its_media_and_runs_only_its_binary(
    secret: Path, listener: tuple[int, list[bytes]]
) -> None:
    port, received = listener
    with tempfile.NamedTemporaryFile() as media:
        media.write(b"media bytes\n")
        media.flush()
        descriptor = media.fileno()
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "mechanical_librarian.bootstrap.probe_shim",
                str(descriptor),
                "/bin/sh",
                "-c",
                'IFS= read -r line < "/dev/fd/$1" && printf "%s;" "$line";'
                ' if read -r other < "$2"; then printf "read;"; else printf "denied;"; fi;'
                ' if printf "" >> "$2"; then printf "wrote;"; else printf "denied;"; fi;'
                " exec /bin/true",
                "sh",
                str(descriptor),
                str(secret),
            ],
            pass_fds=(descriptor,),
            capture_output=True,
            cwd="/",
            env={"PYTHONPATH": _SOURCE, "LC_ALL": "C.UTF-8"},
            timeout=30,
            check=False,
        )
    if child_confinement.landlock_abi() == 0:
        pytest.skip("this kernel has no Landlock")
    assert completed.stdout.decode() == "media bytes;denied;denied;"
    # Executing anything but the probe binary is refused (the shell exits).
    assert completed.returncode != 0
    assert b"Permission denied" in completed.stderr
    assert received == []


@linux_only
def test_a_probe_handed_anything_but_a_file_is_never_started() -> None:
    if child_confinement.landlock_abi() == 0:
        pytest.skip("this kernel has no Landlock")
    read, write = os.pipe()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "mechanical_librarian.bootstrap.probe_shim",
                str(read),
                "/bin/sh",
                "-c",
                "echo started",
            ],
            pass_fds=(read,),
            capture_output=True,
            env={"PYTHONPATH": _SOURCE},
            timeout=30,
            check=False,
        )
    finally:
        os.close(read)
        os.close(write)
    assert (completed.returncode, completed.stdout) == (71, b"")


_CHILD_ENTRIES = {
    # Each child's first step after confinement, replaced by the probes.
    "epub_child": (
        "_read_context",
        "main([str(media), str(control), str(os.getppid())])",
    ),
    "audio_child": ("_read_context", "main([str(control), str(os.getppid())])"),
    "cover_child": ("_decode", "main()"),
}


@linux_only
@pytest.mark.parametrize("child", tuple(_CHILD_ENTRIES))
def test_each_parser_child_is_confined_before_it_reads_untrusted_bytes(
    child: str, secret: Path, listener: tuple[int, list[bytes]]
) -> None:
    port, received = listener
    replaced, call = _CHILD_ENTRIES[child]
    control_read, control_write = os.pipe()
    try:
        with secret.open("rb") as media:
            _code, result = _run(
                f"""
                from mechanical_librarian.bootstrap import {child} as child
                media, control = {media.fileno()}, {control_read}
                def probe(*_arguments):
                    report()
                    raise ValueError("probed")
                child.{replaced} = probe
                child.{call}
                """,
                secret,
                port,
                pass_fds=(media.fileno(), control_read),
                input=b"\x89PNG",
            )
    finally:
        os.close(control_read)
        os.close(control_write)
    if child_confinement.landlock_abi() == 0:
        pytest.skip("this kernel has no Landlock")
    # The child's own process cap already applies here, so a thread may be
    # refused by it rather than by the filter.
    result.pop("thread")
    expected = {key: value for key, value in _CONFINED.items() if key != "thread"}
    if child == "cover_child":
        # Its Python-level socket stub answers first, with a plain OSError.
        expected["connect"] = "OSError"
    assert result == expected
    assert received == []
