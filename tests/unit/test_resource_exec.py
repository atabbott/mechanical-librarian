from __future__ import annotations

import os
import resource

import pytest

from mechanical_librarian.adapters.reviewers import resource_exec


def test_resource_exec_applies_fixed_limits_and_preserves_exact_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: list[tuple[int, tuple[int, int]]] = []
    executed: list[tuple[str, tuple[str, ...], object]] = []
    monkeypatch.setattr(
        resource, "getrlimit", lambda _limit: (resource.RLIM_INFINITY,) * 2
    )
    monkeypatch.setattr(
        resource, "setrlimit", lambda limit, value: installed.append((limit, value))
    )
    monkeypatch.setattr(
        os,
        "execvpe",
        lambda file, argv, environment: executed.append(
            (file, tuple(argv), environment)
        ),
    )

    resource_exec.main(("/usr/bin/bwrap", "--unshare-all", "literal argument"))

    assert installed == [
        (resource.RLIMIT_AS, (1_073_741_824, 1_073_741_824)),
        (resource.RLIMIT_FSIZE, (1_048_576, 1_048_576)),
        (resource.RLIMIT_NOFILE, (64, 64)),
        (resource.RLIMIT_NPROC, (32, 32)),
    ]
    assert executed == [
        (
            "/usr/bin/bwrap",
            ("/usr/bin/bwrap", "--unshare-all", "literal argument"),
            os.environ,
        )
    ]


@pytest.mark.parametrize("arguments", ((), ("",), ("ok", "")))
def test_resource_exec_rejects_missing_or_empty_argv(
    arguments: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit, match="64"):
        resource_exec.main(arguments)
