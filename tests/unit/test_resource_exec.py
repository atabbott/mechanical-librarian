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
    monkeypatch.setattr(resource_exec, "_inheritable_descriptors", lambda *_: (10, 13))
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


def test_resource_exec_installs_and_scans_the_told_descriptor_budget(
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
    scanned: list[int] = []

    def get_inheritable(descriptor: int) -> bool:
        scanned.append(descriptor)
        return False

    monkeypatch.setattr(resource_exec.os, "get_inheritable", get_inheritable)
    spawned: list[tuple[str, ...]] = []

    def refuse(command: tuple[str, ...], **_kwargs: object) -> None:
        spawned.append(command)
        raise OSError("stop after the scan")

    monkeypatch.setattr(resource_exec.subprocess, "Popen", refuse)
    with pytest.raises(SystemExit, match="126"):
        resource_exec.main(("--descriptor-limit=164", "/usr/bin/bwrap", "--x"))
    assert installed == [
        (resource.RLIMIT_FSIZE, (1_048_576, 1_048_576)),
        (resource.RLIMIT_NOFILE, (164, 164)),
    ]
    assert spawned[0][0] == "/usr/bin/bwrap"
    assert spawned[0][-1] == "--x"
    assert scanned == list(range(3, 164))


@pytest.mark.parametrize(
    "arguments",
    (
        ("--descriptor-limit=63", "/usr/bin/bwrap"),
        ("--descriptor-limit=165", "/usr/bin/bwrap"),
        ("--descriptor-limit=0164", "/usr/bin/bwrap"),
        ("--descriptor-limit=", "/usr/bin/bwrap"),
        ("--descriptor-limit=1e2", "/usr/bin/bwrap"),
        ("--descriptor-limit=164",),
    ),
)
def test_resource_exec_refuses_a_descriptor_budget_outside_the_contract(
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
    monkeypatch.setattr(resource_exec, "_inheritable_descriptors", lambda *_: ())
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
