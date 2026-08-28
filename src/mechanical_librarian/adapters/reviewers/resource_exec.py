"""Trusted single-threaded exec boundary for contained reviewer child limits."""

from __future__ import annotations

import os
import resource
import sys
from collections.abc import Sequence

_LIMITS = (
    (resource.RLIMIT_AS, 1_073_741_824),
    (resource.RLIMIT_FSIZE, 1_048_576),
    (resource.RLIMIT_NOFILE, 64),
    (resource.RLIMIT_NPROC, 32),
)


def main(arguments: Sequence[str] | None = None) -> None:
    """Apply the fixed envelope, then replace this process with exact argv."""
    command = tuple(sys.argv[1:] if arguments is None else arguments)
    if not command or any(type(value) is not str or not value for value in command):
        raise SystemExit(64)
    for limit, maximum in _LIMITS:
        soft, hard = resource.getrlimit(limit)
        accepted = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
        resource.setrlimit(limit, (min(soft, accepted), accepted))
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
