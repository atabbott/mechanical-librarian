from __future__ import annotations

import os
import resource

import pytest

from mechanical_librarian.adapters.reviewers import resource_exec
from mechanical_librarian.adapters.reviewers.resource_budget import ProcessIdentity


def test_resource_exec_applies_fixed_limits_and_preserves_exact_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: list[tuple[int, tuple[int, int]]] = []
    spawned: list[tuple[tuple[str, ...], object, tuple[int, ...]]] = []
    budgets: list[tuple[object, object, object, int]] = []
    writes: list[tuple[int, bytes]] = []

    class Process:
        pid = 101

        @staticmethod
        def poll() -> int:
            return 17

        @staticmethod
        def wait(timeout: int | None = None) -> int:
            assert timeout is None
            return 17

    def popen(
        command: tuple[str, ...], *, env: object, pass_fds: tuple[int, ...]
    ) -> Process:
        spawned.append((command, env, pass_fds))
        return Process()

    monkeypatch.setattr(
        resource, "getrlimit", lambda _limit: (resource.RLIM_INFINITY,) * 2
    )
    monkeypatch.setattr(
        resource, "setrlimit", lambda limit, value: installed.append((limit, value))
    )
    pipes = iter(((10, 11), (12, 13)))
    monkeypatch.setattr(resource_exec.os, "pipe", lambda: next(pipes))
    monkeypatch.setattr(resource_exec.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(
        resource_exec.os, "write", lambda fd, value: writes.append((fd, value))
    )
    monkeypatch.setattr(resource_exec.os, "getpid", lambda: 100)
    identities = {
        pid: ProcessIdentity(pid, 100, str(pid * 10)) for pid in (100, 101, 102)
    }
    monkeypatch.setattr(
        resource_exec, "capture_process_identity", lambda pid: identities[pid]
    )
    monkeypatch.setattr(resource_exec.subprocess, "Popen", popen)
    monkeypatch.setattr(resource_exec, "read_sandbox_child_pid", lambda *_: 102)
    monkeypatch.setattr(
        resource_exec,
        "apply_aggregate_budget",
        lambda broker, supervisor, sandbox, limit: budgets.append(
            (broker, supervisor, sandbox, limit)
        ),
    )

    with pytest.raises(SystemExit, match="17"):
        resource_exec.main(("/usr/bin/bwrap", "--unshare-all", "literal argument"))

    assert installed == [
        (resource.RLIMIT_FSIZE, (1_048_576, 1_048_576)),
        (resource.RLIMIT_NOFILE, (64, 64)),
    ]
    assert spawned == [
        (
            (
                "/usr/bin/bwrap",
                "--block-fd",
                "10",
                "--info-fd",
                "13",
                "--unshare-all",
                "literal argument",
            ),
            os.environ,
            (10, 13),
        )
    ]
    assert budgets == [(identities[100], identities[101], identities[102], 32)]
    assert writes == [(11, b"G")]


def test_resource_exec_installs_the_budget_and_forwards_exactly_the_named_fds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: list[tuple[int, tuple[int, int]]] = []
    current = {resource.RLIMIT_NOFILE: (1_024, 1_024)}

    def setrlimit(limit: int, value: tuple[int, int]) -> None:
        installed.append((limit, value))
        current[limit] = value

    monkeypatch.setattr(
        resource,
        "getrlimit",
        lambda limit: current.get(limit, (resource.RLIM_INFINITY,) * 2),
    )
    monkeypatch.setattr(resource, "setrlimit", setrlimit)
    checked: list[int] = []

    def get_inheritable(descriptor: int) -> bool:
        checked.append(descriptor)
        return True

    monkeypatch.setattr(resource_exec.os, "get_inheritable", get_inheritable)
    pipes = iter(((20, 21), (22, 23)))
    monkeypatch.setattr(resource_exec.os, "pipe", lambda: next(pipes))
    monkeypatch.setattr(resource_exec.os, "close", lambda _descriptor: None)
    spawned: list[tuple[tuple[str, ...], tuple[int, ...]]] = []

    def refuse(
        command: tuple[str, ...], *, env: object, pass_fds: tuple[int, ...]
    ) -> None:
        spawned.append((command, pass_fds))
        raise OSError("stop after the spawn arguments")

    monkeypatch.setattr(resource_exec.subprocess, "Popen", refuse)
    with pytest.raises(SystemExit, match="126"):
        resource_exec.main(
            ("--descriptor-limit=164", "--forward-fds=5,300", "/usr/bin/bwrap", "--x")
        )
    assert installed == [
        (resource.RLIMIT_FSIZE, (1_048_576, 1_048_576)),
        (resource.RLIMIT_NOFILE, (164, 164)),
    ]
    # Only the named descriptors are checked and forwarded, 300 past the budget.
    assert checked == [5, 300]
    assert spawned == [
        (
            ("/usr/bin/bwrap", "--block-fd", "20", "--info-fd", "23", "--x"),
            (5, 20, 23, 300),
        )
    ]


@pytest.mark.parametrize("inheritable", (False, OSError("closed")))
def test_resource_exec_refuses_a_named_fd_it_did_not_inherit(
    inheritable: bool | OSError, monkeypatch: pytest.MonkeyPatch
) -> None:
    def get_inheritable(_descriptor: int) -> bool:
        if isinstance(inheritable, OSError):
            raise inheritable
        return inheritable

    monkeypatch.setattr(resource_exec.os, "get_inheritable", get_inheritable)
    monkeypatch.setattr(
        resource, "setrlimit", lambda *_: pytest.fail("no limit before refusal")
    )
    with pytest.raises(SystemExit, match="64"):
        resource_exec.main(("--forward-fds=300", "/usr/bin/bwrap"))


@pytest.mark.parametrize(
    "arguments",
    (
        ("--descriptor-limit=63", "/usr/bin/bwrap"),
        ("--descriptor-limit=165", "/usr/bin/bwrap"),
        ("--descriptor-limit=0164", "/usr/bin/bwrap"),
        ("--descriptor-limit=", "/usr/bin/bwrap"),
        ("--descriptor-limit=1e2", "/usr/bin/bwrap"),
        ("--descriptor-limit=164",),
        ("--forward-fds=2", "/usr/bin/bwrap"),
        ("--forward-fds=5,5", "/usr/bin/bwrap"),
        ("--forward-fds=05", "/usr/bin/bwrap"),
        ("--forward-fds=5,", "/usr/bin/bwrap"),
        ("--forward-fds=x", "/usr/bin/bwrap"),
        ("--forward-fds=2147483648", "/usr/bin/bwrap"),
        ("--forward-fds=5",),
        ("--forward-fds=5", "--descriptor-limit=64", "/usr/bin/bwrap"),
    ),
)
def test_resource_exec_refuses_options_outside_the_contract(
    arguments: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        resource, "setrlimit", lambda *_: pytest.fail("no limit before refusal")
    )
    with pytest.raises(SystemExit, match="64"):
        resource_exec.main(arguments)


@pytest.mark.parametrize("arguments", ((), ("",), ("ok", "")))
def test_resource_exec_rejects_missing_or_empty_argv(
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit, match="64"):
        resource_exec.main(arguments)


def test_resource_exec_does_not_release_changed_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Process:
        pid = 101

        @staticmethod
        def poll() -> int:
            return 1

    monkeypatch.setattr(resource, "getrlimit", lambda _limit: (64, 64))
    monkeypatch.setattr(resource, "setrlimit", lambda *_args: None)
    monkeypatch.setattr(resource_exec.os, "pipe", lambda: (10, 11))
    monkeypatch.setattr(resource_exec.os, "close", lambda _descriptor: None)
    writes: list[bytes] = []
    monkeypatch.setattr(
        resource_exec.os, "write", lambda _fd, value: writes.append(value)
    )
    monkeypatch.setattr(resource_exec.os, "getpid", lambda: 100)
    monkeypatch.setattr(
        resource_exec.subprocess, "Popen", lambda *_args, **_kwargs: Process()
    )
    monkeypatch.setattr(resource_exec, "read_sandbox_child_pid", lambda *_: 102)
    monkeypatch.setattr(
        resource_exec,
        "capture_process_identity",
        lambda pid: ProcessIdentity(pid, 100, str(pid)),
    )
    monkeypatch.setattr(
        resource_exec,
        "apply_aggregate_budget",
        lambda *_args: (_ for _ in ()).throw(
            resource_exec.ReviewerResourceError("identity changed")
        ),
    )

    with pytest.raises(SystemExit, match="126"):
        resource_exec.main(("/usr/bin/bwrap",))
    assert writes == []
