# Parser confinement

Four helpers parse untrusted bytes, each a short-lived process over passed
descriptors: the EPUB child, `ffprobe` (through `bootstrap/probe_shim.py`),
the audio child that reads `ffprobe`'s output and the sidecars, and the cover
image decoder. None runs in the server process. This page says what confines
them; what each parses, and the bounds on it, is in
[security](security.md#parsers-and-hostile-input).

Before reading any untrusted byte, each helper confines itself with
`adapters/child_confinement.py`, on top of its resource limits, minimal
environment and own session.

## Files (Landlock)

- A helper can read the descriptors it was handed and read-only program code:
  the interpreter's import directories and the system library directories for
  the Python children; the one `ffprobe` binary, its dynamic loader and the
  library directories for the probe.
- The probe's media is allowed by a rule on the passed descriptor itself, so
  `/dev/fd/N` reopens that file and no other. The probe can execute nothing
  but its binary and loader. The shim refuses a binary that is not a 64-bit
  ELF and, with Landlock, a descriptor Landlock cannot name.
- A helper can create, write, rename or remove nothing by path, so `/data`,
  the media mounts and other processes' `/proc` entries are out of reach.
  Writes to the output pipes it was handed are unaffected.
- Where the kernel supports them, TCP bind and connect, abstract Unix sockets
  and signals to processes outside the helper are refused too.
- A Landlock domain also stops the helper tracing, or reading the memory or
  descriptors of, the server.

## System calls (seccomp)

- The filter first checks the architecture: a call through another
  architecture's entry point (the 32-bit `int 0x80` path) or with the x32 bit
  kills the helper.
- It refuses `socket`, `socketpair`, io_uring, `ptrace`, `process_vm_readv`,
  `process_vm_writev`, `pidfd_open`, `pidfd_getfd`, `unshare`, `setns`, `bpf`,
  `perf_event_open`, `userfaultfd` and the kernel keyring calls.
- It refuses the changes Landlock does not cover, by path or descriptor:
  `truncate` (Landlock handles it only from ABI 3), and every permission,
  owner, timestamp and extended-attribute change (the `chmod`, `chown`,
  `utime` and `setxattr`/`removexattr` families).
- It refuses any new process: `fork`, `vfork` and `clone` without
  `CLONE_THREAD`. Threads still work; `clone3` answers "not implemented" so
  the C library falls back to `clone`.
- The Python children may not `execve` at all. The probe shim's filter allows
  the one exec into `ffprobe`, and Landlock limits what can be executed.

## Order

`no_new_privs`, then Landlock, then the filter, synchronised to every thread.
A Python child confines itself before it starts any thread, because Landlock
binds the calling thread and the threads it starts later, never one already
running. The shim confines itself last, just before it executes `ffprobe`;
both layers survive the exec.

## Hosts without Landlock

Landlock needs Linux 5.13 or newer with it enabled, and a container profile
that admits its three calls; the supported Compose profile and Docker Engine
25's default profile both do. Where it is missing, the helpers still run with
the system-call filter and their limits. Sockets, new processes,
truncation and metadata changes stay refused, and so does exec for the Python
children; the probe can then execute other programs, which still run under
the filter and limits. Reading and writing files is then guarded only by
ordinary permissions: a compromised helper could read or overwrite what the
container user can. Nothing in the product reports this state.

Any Landlock failure after the kernel reports support stops the helper rather
than leaving it half confined. The filter itself is required: on an
unsupported architecture (anything but amd64 and arm64) or where seccomp is
refused, the helper exits before parsing. Other platforms are development-only
and run the helpers unconfined.
