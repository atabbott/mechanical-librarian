# ADR-0007: Isolate EPUB package extraction in a transient child

- Status: accepted
- Date: 2026-07-14
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

EPUB package evidence requires interpreting attacker-controlled ZIP headers,
compressed streams, XML, declaration graphs, and metadata text. The normal
deployment remains one container and the extractor needs read access to one
configured media file, but it must not acquire service credentials, network,
database, persistence, reviewer, provider, shell, temporary-file, or write
authority. A parser defect must not leave a child running or turn a partial
parse into successful evidence.

The application contract is framework-independent and accepts a bounded
random-access read view. Runtime composition must additionally validate the
configured logical mount and relative reference, resist symlink replacement,
prove source stability, bound child output, and map every terminal condition to
a typed report.

## Decision

1. The composition root validates the closed configuration and resolves one
   read-only logical mount. Infrastructure walks every relative path component
   with directory descriptors and `O_NOFOLLOW`, then opens one regular file
   read-only. No normalized host path enters the report.
2. The parent hashes the complete file and records descriptor identity before
   launch. After the child exits it re-hashes and re-stats the same descriptor,
   then repeats the no-follow walk and hash. Any identity or digest difference
   is `unstable_source` and discards partial output.
3. Each request starts one child without a shell. The child inherits only the
   read-only source descriptor and a parent-liveness control descriptor. A
   bounded canonical
   JSON context supplies the expected fingerprint and the closed
   `epub-package-limits-v1` profile. It contains no path, endpoint, credential,
   database handle, client, or mutation capability.
4. The child receives a minimal secret-free environment, uses no temporary
   files, and emits exactly one canonical `local-evidence-report-v1` JSON object
   followed by one newline. Stderr is empty on a valid protocol exchange.
5. The parent drains stdout and stderr concurrently into hard caps of 1 MiB and
   64 KiB. It validates UTF-8, canonical JSON, duplicate-free closed fields,
   declared limits, capability, and source fingerprint. Invalid, excessive, or
   nonzero-exit output is `child_failure`.
6. The child enforces zero core dumps, 64 open descriptors, 30 seconds CPU and,
   on Linux production, 256 MiB address space plus `RLIMIT_NPROC=1`. Linux
   installs `PR_SET_PDEATHSIG` before applying the process cap and then closes
   the control descriptor; Darwin uses the parent-pipe watcher. The parent
   enforces a 60-second
   wall deadline. Timeout or cancellation terminates the process group, waits
   up to two seconds, kills if necessary, and reaps it. Parent-pipe EOF causes
   immediate child exit.
7. Darwin framework Python reserves an address space larger than 256 MiB before
   application code starts and rejects lowering `RLIMIT_AS`, `RLIMIT_DATA`, and
   `RLIMIT_RSS` below that reservation. macOS is therefore a development
   compatibility target for functional qualification, not a production claim
   for the memory cap. Linux container qualification must prove the 256 MiB
   limit and fails closed if it cannot be installed.
8. The extractor preflights raw local and central ZIP structures before using
   `zipfile`, supports bounded ZIP64, reads members only through
   `ZipFile.open`, and never calls extraction helpers. It opens only
   `mimetype`, `META-INF/container.xml`, optional `META-INF/encryption.xml`, and
   the single OPF resource.
9. Each XML document receives a fresh Expat parser after verifying Expat
   2.7.2 or newer. Every DOCTYPE/entity declaration, external entity request,
   and XInclude is rejected. Runtime counters and configured graph limits abort
   without a partial report.
10. The child-process boundary is least-authority composition, not a claim of a
    kernel syscall sandbox. A future sandbox may strengthen the boundary only
    with a new or superseding decision and container evidence.

## Consequences

### Positive

- Hostile archive/XML state cannot directly acquire unrelated product handles.
- Parent and child have a small versioned protocol with deterministic failure
  semantics and bounded memory in the parent.
- Source replacement and in-place mutation are detected before evidence can be
  accepted.
- Navigation, content, covers, and linked resources remain opaque for F07 or
  later capabilities.

### Negative

- Each extraction pays process-start and two full-file stability-hash costs.
- macOS cannot qualify the Linux address-space limit with framework Python.
- Raw ZIP preflight duplicates a small amount of validation performed by
  `zipfile` because the standard API hides unsafe name and header distinctions.

### Neutral or deferred

- No persistent schema or migration is introduced.
- No evidence is submitted to policy, providers, reviewers, or target writes.
- OS-level syscall filtering and stronger container principals remain separate
  hardening decisions.

## Alternatives considered

### Parse in the main process

This is simpler but gives malformed ZIP/XML handling the lifetime and authority
of the curator process and makes deadline recovery less reliable.

### Extract required files to a temporary directory

This adds path, cleanup, storage, and retained-artifact risk without improving
the package-declaration outcome.

### Treat the child as a general parser service

A reusable service would retain state and broaden its protocol. One transient
descriptor-bound child is easier to reap, audit, and reason about.

## Validation

- Unit cohorts cover EPUB 2/3 normalization, ZIP aliases/NUL/CRC/methods,
  entity rejection, encryption, multiple renditions, and resource limits.
- Integration cohorts exercise the real child, canonical protocol, no-follow
  references, cancellation/reaping followed by sibling success, composed CLI
  privacy, and concurrent source mutation.
- Architecture tests keep parsing/process implementations outside domain and
  application code and prohibit cross-family adapter imports.
- The F06 cohort passes non-root on Linux CPython 3.13.11 with Expat 2.7.3 and
  zlib 1.2.13 in the digest-pinned qualification image. The root filesystem,
  repository and installed environment are read-only and networking is
  disabled. Tests record address-space, process, CPU, descriptor, core, wall,
  output, cleanup and parent-loss behavior. This does not widen whole-product
  Linux compatibility.
