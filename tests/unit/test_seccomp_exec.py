from __future__ import annotations

import resource
from unittest.mock import Mock, call, patch

import pytest

from mechanical_librarian.adapters.reviewers import seccomp_exec


def test_wrapper_raises_only_its_soft_budget_installs_filter_and_execs_exactly() -> (
    None
):
    program = b"\x00" * 8
    libc = Mock()
    libc.prctl.return_value = 0
    with (
        patch.object(seccomp_exec.Path, "read_bytes", return_value=program),
        patch.object(
            seccomp_exec.resource,
            "getrlimit",
            return_value=(64 * 1024**2, 832 * 1024**2),
        ),
        patch.object(seccomp_exec.resource, "setrlimit") as set_limit,
        patch.object(seccomp_exec.ctypes, "CDLL", return_value=libc),
        patch.object(
            seccomp_exec.os,
            "execve",
            side_effect=RuntimeError("exec boundary"),
        ) as execute,
        pytest.raises(RuntimeError, match="exec boundary"),
    ):
        seccomp_exec.main(["/runtime/seccomp.bpf", "/runtime/codex", "--version"])

    set_limit.assert_called_once_with(
        resource.RLIMIT_AS,
        (832 * 1024**2, 832 * 1024**2),
    )
    assert libc.prctl.call_args_list[0] == call(38, 1, 0, 0, 0)
    assert libc.prctl.call_args_list[1].args[:2] == (22, 2)
    execute.assert_called_once_with(
        "/runtime/codex",
        ["/runtime/codex", "--version"],
        seccomp_exec.os.environ,
    )


@pytest.mark.parametrize(
    ("arguments", "payload", "hard"),
    (
        (["elsewhere", "/runtime/codex"], b"\x00" * 8, 1),
        (["/runtime/seccomp.bpf", "/runtime/codex"], b"", 1),
        (["/runtime/seccomp.bpf", "/runtime/codex"], b"\x00" * 7, 1),
        (
            ["/runtime/seccomp.bpf", "/runtime/codex"],
            b"\x00" * 8,
            resource.RLIM_INFINITY,
        ),
    ),
)
def test_wrapper_fails_closed_before_exec(
    arguments: list[str], payload: bytes, hard: int
) -> None:
    with (
        patch.object(seccomp_exec.Path, "read_bytes", return_value=payload),
        patch.object(seccomp_exec.resource, "getrlimit", return_value=(1, hard)),
        patch.object(seccomp_exec.os, "execve") as execute,
        pytest.raises(SystemExit, match="126"),
    ):
        seccomp_exec.main(arguments)
    execute.assert_not_called()
