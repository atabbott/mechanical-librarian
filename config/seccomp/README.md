# Contained-reviewer seccomp profile

`contained-reviewer.json` is the Docker seccomp profile the supported Compose
file loads for the application container. It is a modified copy of a
third-party work, and this file is its provenance and modification notice.

## Upstream

- Project: Moby Profiles, <https://github.com/moby/profiles>
- Tag: `seccomp/v0.2.3`
- File: `seccomp/default.json`, the Docker default seccomp profile
- Upstream SHA-256:
  `536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74`
- Licence: Apache License 2.0. The exact licence text from that tag is
  `LICENSE.moby-profiles` beside this file, SHA-256
  `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.
  The upstream tag carries no `NOTICE` file.

## Modifications

The derivative was changed from the upstream document in two ways:

- three rules were appended to the end of the `syscalls` array; and
- the JSON was re-indented with two spaces in place of tabs.

Every other upstream key, value, rule, and rule order is unchanged, so
Docker's other default syscall admissions and denials stay as they were. The three
appended rules admit:

1. `clone` with the exact non-network namespace mask used by contained Codex
   device authentication and cover review;
2. `clone` with the same mask plus a private network namespace, used only by
   the offline version probe; and
3. `mount`, `pivot_root`, and `umount2`, used to construct the nested
   filesystem after either exact clone.

The two `clone` rules match the flags in argument 0 and exclude the `s390` and
`s390x` architectures, whose `clone` argument order differs; no `s390`
counterpart is added. The filter installed after Bubblewrap finishes
construction is application-owned and is not part of this profile.

Derivative SHA-256:
`04151878f5bb17b00d216d552f8b8a8bc3683549517193e4f8d6501f1e7530ff`

## Changing the profile

The decision behind these admissions is
[ADR-0062](../../docs/architecture/decisions/0062-owner-managed-reviewer-access.md),
and the dependency record is the contained-reviewer section of
[dependencies](../../docs/development/dependencies.md). A change to the
profile, its upstream tag, or its licence is an ordinary tracked security
change that updates this notice, the checksums in that record, and the
licence file together.
