# ADR-0100: Confine parser helpers, and keep parsing where Landlock is missing

- Status: proposed
- Date: 2026-11-15
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Four helpers parse untrusted media: the EPUB child
([ADR-0011](0011-isolated-epub-inspector.md)), `ffprobe` and the audio child
([ADR-0063](0063-isolated-audiobook-inspector.md)), and the cover image
decoder. Each already ran as its own process over passed descriptors, with
resource limits, a minimal environment and its own session. A parser defect
that gave an attacker code execution inside a helper was bounded by those
limits and by the container, but not by what the helper could open, write or
reach as the container user.

Linux offers two unprivileged mechanisms that work inside the supported
container: Landlock, which limits filesystem and some network and signal
access, and seccomp, which filters system calls. Landlock needs Linux 5.13 or
newer with it enabled, and a container profile that admits its calls. Some
self-hosting kernels, older NAS kernels in particular, lack it. seccomp is
available wherever the supported Compose deployment runs.

## Decision

1. Every parser helper confines itself before it reads any untrusted byte,
   through one shared module (`adapters/child_confinement.py`), as described
   in [parser confinement](../parser-confinement.md): Landlock limits it to
   its passed descriptors and read-only program code, and one seccomp filter
   checks the architecture first and refuses sockets, new processes, exec
   (except the probe shim's one exec into `ffprobe`), cross-process calls and
   the truncate and file-metadata changes Landlock does not cover.
2. Landlock is applied at the best ABI the kernel offers. Where the kernel
   reports no Landlock (not built, disabled at boot, or refused by the
   container profile), the helpers still parse, with the seccomp filter and
   their limits only. Any Landlock failure after the kernel reports support
   stops the helper.
3. The seccomp filter is required. On an architecture other than amd64 and
   arm64, or where the filter is refused, the helper exits before parsing.
4. The product does not report which mode a host runs in. The docs state the
   degraded protection.

## Consequences

### Positive

- On a current kernel, a compromised helper cannot read the state volume,
  other media, or the server's memory, cannot write by path and cannot open a
  network connection.
- Every host keeps working: a host without Landlock gets the earlier
  protection plus the filter.

### Negative

- On a host without Landlock, a compromised helper can still read and
  overwrite what the container user can, and the probe can execute other
  programs (still under the filter). The owner is not told.
- A parse that needs a file outside its descriptor or code now fails closed.

### Neutral or deferred

- An owner-visible notice of the degraded mode, or a setting that refuses to
  parse without Landlock, is new behaviour and left for a later decision.

## Alternatives considered

### Refuse to parse without Landlock

Strongest guarantee, but every host on an older kernel would lose EPUB,
audiobook and cover inspection entirely, which is most of the product's
evidence. Rejected while no owner-visible notice or opt-in exists.

### Bubblewrap or namespaces

The image already carries Bubblewrap for the contained reviewer, but it needs
unprivileged user namespaces, which the deployment documents as optional. It
would add a hard dependency to every parse. Landlock and seccomp need no
namespace and no extra binary.

## Validation

`tests/unit/test_child_confinement.py` proves, per helper, that reading or
writing another file, sockets, new processes and exec are refused while the
handed descriptor and lazily imported code stay usable; that the filter kills
foreign-architecture and x32 calls; and that the degraded mode keeps the
filter while a later Landlock failure stops the helper. The helpers were run in
the shipped image configuration with and without Landlock.
