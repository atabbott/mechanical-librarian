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
    read_sandbox_child_pid,
)
from mechanical_librarian.application.contained_reviewer_contract import (
    COVER_PROCESS_LIMIT,
)

_INHERITED_LIMITS = (
    (resource.RLIMIT_FSIZE, 1_048_576),
    (resource.RLIMIT_NOFILE, 64),
)


def main(arguments: Sequence[str] | None = None) -> None:
    """Start Bubblewrap, split the 1 GiB ceiling, release it, and relay exit."""
    command = tuple(sys.argv[1:] if arguments is None else arguments)
    if not command or any(type(value) is not str or not value for value in command):
        raise SystemExit(64)
    for limit, maximum in _INHERITED_LIMITS:
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
        inherited = _inheritable_descriptors(budget_read, info_write)
        process = subprocess.Popen(wrapped, env=os.environ, pass_fds=inherited)
        os.close(budget_read)
        budget_read = -1
        os.close(info_write)
        info_write = -1
        payload_pid = read_sandbox_child_pid(info_read, time.monotonic() + 30)
        apply_aggregate_budget(
            payload_pid,
            process.pid,
            os.getpgrp(),
            COVER_PROCESS_LIMIT,
            broker_pid=os.getpid(),
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


def _inheritable_descriptors(*required: int) -> tuple[int, ...]:
    """Preserve only descriptors explicitly inherited by this trusted broker."""
    descriptors = set(required)
    soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    maximum = 1_024 if soft == resource.RLIM_INFINITY else min(soft, 1_024)
    for descriptor in range(3, maximum):
        try:
            if os.get_inheritable(descriptor):
                descriptors.add(descriptor)
        except OSError:
            continue
    return tuple(sorted(descriptors))


if __name__ == "__main__":
    main()
