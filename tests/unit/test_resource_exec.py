from __future__ import annotations

import os
import resource

import pytest

from mechanical_librarian.adapters.reviewers import resource_exec


def test_resource_exec_applies_fixed_limits_and_preserves_exact_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: list[tuple[int, tuple[int, int]]] = []
    spawned: list[tuple[tuple[str, ...], object, tuple[int, ...]]] = []
    budgets: list[tuple[int, int, int, int, int | None]] = []
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
    monkeypatch.setattr(resource_exec.os, "getpgrp", lambda: 100)
    monkeypatch.setattr(resource_exec.os, "getpid", lambda: 100)
    monkeypatch.setattr(resource_exec, "_inheritable_descriptors", lambda *_: (10, 13))
    monkeypatch.setattr(resource_exec.subprocess, "Popen", popen)
    monkeypatch.setattr(resource_exec, "read_sandbox_child_pid", lambda *_: 102)
    monkeypatch.setattr(
        resource_exec,
        "apply_aggregate_budget",
        lambda payload, supervisor, group, limit, *, broker_pid=None: budgets.append(
            (payload, supervisor, group, limit, broker_pid)
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
    assert budgets == [(102, 101, 100, 32, 100)]
    assert writes == [(11, b"G")]


@pytest.mark.parametrize("arguments", ((), ("",), ("ok", "")))
def test_resource_exec_rejects_missing_or_empty_argv(
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit, match="64"):
        resource_exec.main(arguments)
