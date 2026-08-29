# ADR-0062: Isolated audiobook inspector

- Status: accepted
- Date: 2026-08-29
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Milestone 0.20.0 opens the audiobook foundation with a local-evidence slice
(#749). Audiobook containers are not archives of documents like EPUB: the
evidence lives in MP4 `ilst` atoms and `----` freeform atoms, ID3v2 frames and
`TXXX` user-text frames, Nero `chpl` chapter tables, container and stream
headers, owner-written sidecar documents, and the exported file and folder
names. Reading those surfaces in-process would put a general media parser
inside the application, which ADR-0011 already refuses for EPUB.

The established isolation pattern runs one Python child with fd-passed input,
`RLIMIT_NPROC=1`, a minimal environment, `cwd="/"`, its own session, bounded
streams, a wall clock, and a parent-death guard. `RLIMIT_NPROC` is a per-uid
ceiling on processes *and* threads, and every container parser worth using is
threaded, so a child holding that cap cannot itself run one.

The project also has no in-tree container parser and will not gain one: the
formats are large, adversarial, and already implemented correctly elsewhere.
ffmpeg's `ffprobe` reads exactly what this slice needs — `-show_format`,
`-show_streams`, `-show_chapters`, `-export_all` — and emits JSON, so the parse
can be pushed out of the application entirely.

ADR-0061 fixes the provider posture for audiobooks. This slice terminates
before any provider exists, so it must also decide what a report *without* a
provider means to the review pipeline.

## Decision

1. **Two isolated children, no in-process media parsing.** The parent harness
   runs `ffprobe` itself as one isolated child (fd-passed media descriptor,
   `/dev/fd/N`, minimal environment, `cwd="/"`, new session, `RLIMIT_CORE=0`,
   `RLIMIT_FSIZE=0`, `RLIMIT_NOFILE`, `RLIMIT_CPU`, `RLIMIT_AS`, bounded
   stdout/stderr, wall clock, cancellation). Its bounded JSON document is then
   handed to a second child — the existing ADR-0011 Python child, with
   `RLIMIT_NPROC=1` intact — which owns every remaining untrusted byte: the
   probe document and each sidecar it is passed a descriptor for. The
   interpretation module itself imports only `json` and `re` and touches no
   filesystem, socket, or process.
2. **`ffprobe` ships as a version-pinned distribution package.** The final
   image installs `ffmpeg=7:5.1.9-0+deb12u1` and the parent resolves the binary
   from a closed path list rather than an inherited `PATH`, which the child
   environment does not carry.
3. **Chapters and embedded art are structure, not claims.** Embedded chapters
   are a bounded section on the report (at most 1,024, contiguous ordinals,
   millisecond boundaries, projection-validated titles, an explicit truncation
   flag). Embedded cover art is a presence fact with its surface, and
   deliberately never a `CoverSource`: this slice grants no acquisition,
   decoding, or election authority over those bytes.
4. **An audiobook report terminates at local extraction.** No
   `ProviderBinding` is read, no discovery plan is built, and no provider is
   constructed or dispatched. The domain refuses an audio report that carries a
   provider observation at all, and review preparation ends in the same
   transaction that publishes the report, with the terminal, non-retryable code
   `local_evidence_only` projected to the owner as recorded evidence rather
   than as a failure. The provider stage refuses such a report as a guard, for
   the case it is reached by a path that does not exist today.

## Consequences

### Positive

- Every byte of an owner's audiobook is parsed by a process that cannot fork,
  cannot reach the network, holds no descriptor it was not handed, and is
  bounded in memory, CPU, output, and wall clock — and the parse itself is
  ffmpeg's, not this project's.
- The evidence set is closed and per-surface attributed, so an owner sees which
  grammar carried each observation: an embedded tag, a sidecar, or a path.
- The stop is atomic with publication, so an audiobook neither stalls in a
  stage it cannot complete nor reports a provider failure that never happened.

### Negative

- The shipped image grows by 556 MB (261 MB to 817 MB). Debian's `ffprobe`
  links 213 shared objects including `libavdevice` and `libavfilter`, whose
  closure pulls LLVM, Mesa, flite, and librsvg; the cost is what that binary
  links, not recommended packages, and trimming or copying just the binary does
  not avoid it.
- A deployment without the probe binary produces no audiobook evidence. That
  is a typed `invalid_configuration` outcome rather than a partial report.

### Neutral or deferred

- Multi-file audiobooks and unmounted audiobook libraries stay excluded exactly
  as before this slice, and remain a later decision.
- Provider discovery, enrichment, and any write authority for audiobooks stay
  governed by ADR-0061 and scope; this record grants none of them.
- Transcription and audio decoding are out of scope: nothing here decodes a
  sample.

## Alternatives considered

### One child that runs the probe itself

Rejected: the ADR-0011 child holds `RLIMIT_NPROC=1`, which is per-uid and
counts threads, so it cannot start a threaded probe. Relaxing that cap to let
it fork would weaken the same isolation the pattern exists to provide, for
every capability that shares the uid.

### A vendored static `ffprobe` build

Rejected on provenance and maintenance: a static binary carries no
distribution security-update path, and this project would own its supply chain
and its CVE response for a media parser it did not build.

### An in-tree container parser

Rejected: MP4, ID3, and Nero chapter parsing is a large adversarial surface,
and writing one would move exactly the risk this record isolates back into the
application.

### Chapters as claims, and embedded art as a cover source

Rejected: neither names a target field. A chapter table would become
comparable, proposable evidence for a destination that does not exist, and
promoting embedded art to a `CoverSource` would grant this slice cover
authority the milestone deliberately withholds.

## Validation

- The fast tier proves the grammar, the child protocol, the media gates, the
  pipeline stop, and the owner projection against recorded probe documents,
  with no probe binary present.
- The slow tier executes the real probe over generated MP4 and MP3 containers
  and one hostile container, covering the whole composed path from mount to
  closed evidence set.
- Reopen triggers: a measured need for multi-file audiobook evidence, an image
  size budget the probe closure cannot meet, or an audiobook capability that
  requires decoding rather than probing.
