"""Broker one contained reviewer under a fixed aggregate resource envelope."""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import time
from collections.abc import Sequence

from mechanical_librarian.adapters.reviewers.resource_budget import (
    ReviewerResourceError,
    apply_aggregate_budget,
    capture_process_identity,
    read_sandbox_child_pid,
)
from mechanical_librarian.application.contained_reviewer_contract import (
    COVER_PROCESS_LIMIT,
    MAX_REVIEWER_DESCRIPTOR_LIMIT,
    REVIEWER_DESCRIPTOR_LIMIT,
)

_FILE_SIZE_LIMIT = 1_048_576
_DESCRIPTOR_FLAG = "--descriptor-limit="
_FORWARD_FLAG = "--forward-fds="


def main(arguments: Sequence[str] | None = None) -> None:
    """Start Bubblewrap, split the 1 GiB ceiling, release it, and relay exit.

    Two leading options, in this order, may precede the Bubblewrap argv.
    ``--descriptor-limit=N`` carries the request's descriptor budget: 64 plus
    one per sealed image, never above 64 plus the taste image limit; without
    it the budget is 64. ``--forward-fds=A,B,...`` names the exact descriptors
    the parent passed for Bubblewrap; each must be open and inheritable,
    whatever its number, and no other descriptor is forwarded.
    """
    command = tuple(sys.argv[1:] if arguments is None else arguments)
    if not command or any(type(value) is not str or not value for value in command):
        raise SystemExit(64)
    descriptor_limit, forwarded, command = _options(command)
    for limit, maximum in (
        (resource.RLIMIT_FSIZE, _FILE_SIZE_LIMIT),
        (resource.RLIMIT_NOFILE, descriptor_limit),
    ):
        soft, hard = resource.getrlimit(limit)
        accepted = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
        resource.setrlimit(limit, (accepted, accepted))
    budget_read, budget_write = os.pipe()
    info_read, info_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        wrapped = (
            command[0],
            "--block-fd",
            str(budget_read),
            "--info-fd",
            str(info_write),
            *command[1:],
        )
        inherited = tuple(sorted({*forwarded, budget_read, info_write}))
        process = subprocess.Popen(wrapped, env=os.environ, pass_fds=inherited)
        os.close(budget_read)
        budget_read = -1
        os.close(info_write)
        info_write = -1
        sandbox_pid = read_sandbox_child_pid(info_read, time.monotonic() + 30)
        apply_aggregate_budget(
            capture_process_identity(os.getpid()),
            capture_process_identity(process.pid),
            capture_process_identity(sandbox_pid),
            COVER_PROCESS_LIMIT,
        )
        os.write(budget_write, b"G")
        os.close(budget_write)
        budget_write = -1
        raise SystemExit(process.wait())
    except (
        OSError,
        subprocess.SubprocessError,
        ReviewerResourceError,
        TimeoutError,
    ) as error:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise SystemExit(126) from error
    finally:
        for descriptor in (budget_read, budget_write, info_read, info_write):
            if descriptor >= 0:
                os.close(descriptor)


def _options(
    command: tuple[str, ...],
) -> tuple[int, tuple[int, ...], tuple[str, ...]]:
    """Read the descriptor budget and the exact forwarded descriptors."""
    descriptor_limit = REVIEWER_DESCRIPTOR_LIMIT
    if command[0].startswith(_DESCRIPTOR_FLAG):
        value = command[0].removeprefix(_DESCRIPTOR_FLAG)
        if not _canonical(value) or not (
            REVIEWER_DESCRIPTOR_LIMIT <= int(value) <= MAX_REVIEWER_DESCRIPTOR_LIMIT
        ):
            raise SystemExit(64)
        descriptor_limit = int(value)
        command = command[1:]
    forwarded: tuple[int, ...] = ()
    if command and command[0].startswith(_FORWARD_FLAG):
        values = command[0].removeprefix(_FORWARD_FLAG)
        listed = values.split(",") if values else []
        if (
            len(listed) > MAX_REVIEWER_DESCRIPTOR_LIMIT
            or not all(_canonical(value) for value in listed)
            or len(set(listed)) != len(listed)
            or any(not 3 <= int(value) < 2**31 for value in listed)
        ):
            raise SystemExit(64)
        forwarded = tuple(int(value) for value in listed)
        command = command[1:]
    if not command or command[0].startswith("--"):
        raise SystemExit(64)
    try:
        if not all(os.get_inheritable(descriptor) for descriptor in forwarded):
            raise SystemExit(64)
    except OSError as error:
        raise SystemExit(64) from error
    return descriptor_limit, forwarded, command


def _canonical(value: str) -> bool:
    return value.isascii() and value.isdigit() and value == str(int(value))


if __name__ == "__main__":
    main()
