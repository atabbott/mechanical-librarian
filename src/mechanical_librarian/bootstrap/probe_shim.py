"""Private entry point that bounds the probe and then becomes it.

``subprocess``'s ``preexec_fn`` runs arbitrary Python between ``fork`` and
``exec`` in a child that holds whatever locks the forking thread's process
held.  The probe is spawned from a threaded supervisor, so that window is a
deadlock the parent waits on untimed -- the wall clock the drain enforces has
not started yet.

This module closes the window by making the bounded step a process of its own:
the parent spawns a plain interpreter, which applies exactly the rlimit ladder
``preexec_fn`` used to apply and then ``execv``s the probe binary the parent
resolved.  The limits are installed by an ordinary process before an ordinary
exec, so nothing runs between a fork and an exec at all.

The parent resolves the binary and passes it here.  This module never searches
for one, so a missing probe stays the parent's pre-spawn decision.
"""

from __future__ import annotations

import os
import resource
import sys

# The probe's own ceilings, which are not the interpretation child's.
# `RLIMIT_NPROC` is deliberately absent: it is a per-uid ceiling on processes
# *and* threads, and ffprobe is threaded, so the child's cap of one would
# refuse every probe.  Keeping the cap off this lane is exactly why the parent
# runs ffprobe itself rather than letting `bootstrap.audio_child` spawn it.
_ADDRESS_SPACE = 512 * 1024 * 1024
_CPU_SECONDS = 30
_OPEN_FILES = 64
# Any non-zero exit is a probe failure to the parent. This one says the shim
# never became the probe, so no probe document was ever going to arrive.
_UNAVAILABLE = 71


def apply_limits() -> None:
    """Install the probe's resource ceilings on this process."""
    for limit, value in (
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_NOFILE, _OPEN_FILES),
        (resource.RLIMIT_CPU, _CPU_SECONDS),
        (resource.RLIMIT_FSIZE, 0),
    ):
        current_soft, current_hard = resource.getrlimit(limit)
        hard = (
            value
            if current_hard == resource.RLIM_INFINITY
            else min(value, current_hard)
        )
        resource.setrlimit(limit, (min(value, hard), hard))
    try:
        current_soft, current_hard = resource.getrlimit(resource.RLIMIT_AS)
        hard = (
            _ADDRESS_SPACE
            if current_hard == resource.RLIM_INFINITY
            else min(_ADDRESS_SPACE, current_hard)
        )
        resource.setrlimit(resource.RLIMIT_AS, (min(_ADDRESS_SPACE, hard), hard))
    except ValueError:
        # Same development-only compatibility branch `bootstrap.epub_child`
        # documents: Darwin refuses an address-space limit below its framework
        # reservations. Linux production must fail closed instead.
        if sys.platform != "darwin":
            raise


def main(arguments: list[str]) -> int:
    """Bound this process, keep the passed descriptor, and become the probe."""
    if len(arguments) < 3:
        return _UNAVAILABLE
    try:
        descriptor = int(arguments[1])
    except ValueError:
        return _UNAVAILABLE
    binary = arguments[2]
    try:
        # The parent passed this descriptor for the probe, not for this
        # process: it has to survive the exec below, which only a descriptor
        # without the close-on-exec flag does.
        os.set_inheritable(descriptor, True)
        apply_limits()
        os.execv(binary, [binary, *arguments[3:]])
    except BaseException:
        return _UNAVAILABLE
    # `execv` does not return. Reaching this line is itself the failure.
    return _UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
