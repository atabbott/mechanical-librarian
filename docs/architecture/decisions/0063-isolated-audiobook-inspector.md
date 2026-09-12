# ADR-0063: Isolated audiobook inspector

- Status: accepted
- Date: 2026-08-29
- Owners: project maintainers
- Supersedes: none
- Superseded by: ADR-0066 for the multi-file exclusion

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
   stdout/stderr, wall clock, cancellation). The rlimits are installed by
   `bootstrap.probe_shim`, a shim the parent spawns that applies them and then
   `execv`s the probe: a `preexec_fn` would run Python between a fork and an
   exec, and this spawn happens on a supervisor thread, where that window is a
   deadlock the parent waits on before any clock starts. The parent still
   resolves the binary, so a missing probe is a pre-spawn refusal. Its bounded
   JSON document is then
   handed to a second child — the existing ADR-0011 Python child, with
   `RLIMIT_NPROC=1` intact — which owns every remaining untrusted byte: the
   probe document and each sidecar it is passed a descriptor for. The
   interpretation module itself imports only `json` and `re` and touches no
   filesystem, socket, or process.
2. **`ffprobe` is built from a checksum-pinned upstream release.** A builder
   stage fetches FFmpeg 7.1.5, verifies its SHA-256 against a pinned value and
   hard-fails on mismatch, configures with `--disable-everything` plus exactly
   the `mov` and `mp3` demuxers -- the two container grammars behind the `m4b`,
   `m4a` and `mp3` files this library admits -- the `aac`, `mp3`, `mjpeg` and
   `png` decoders and parsers those containers carry, and the `file`, `pipe`
   and `fd` protocols, builds the `ffprobe` target alone, and strips it.
   `--disable-x86asm` keeps an assembler out of the build's supply chain --
   header reading uses no SIMD decode path, and the flag is a no-op on arm64 --
   so both architectures build from the same inputs. The result is a
   1,845,864-byte binary on arm64 and a 1,858,088-byte one on x86-64,
   copied into the final image at `/usr/local/bin/ffprobe`; the parent resolves
   it from a closed path list rather than an inherited `PATH`, which the child
   environment does not carry. `--disable-network` makes the zero-network
   invariant a property of the binary rather than of the sandbox around it, and
   the enabled set closes the grammar: `ffprobe` sniffs by content rather than
   by extension, so a payload outside those two containers is refused as an
   extraction failure instead of being demuxed by a component nothing here
   reads.
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
   `local_evidence_only`. That transaction also registers the item's
   `report_only` review package, which is what the owner browses and searches
   the work by; an ebook acquires the same row later, at proposal or admission,
   and an audiobook reaches neither stage. The provider stage refuses such a
   report as a guard, for the case it is reached by a path that does not exist
   today.
5. **A failure-that-isn't is excluded everywhere failures are counted.** The
   work row ends `failed` because nothing follows the report, so
   `local_evidence_only` is excluded from the operational signal query, from
   the scan drain that decides whether a scan's staged absences are committed
   or discarded, and from the blocked count an owner reads. Excluding it on
   one surface only is worse than not excluding it at all: one prepared
   audiobook would settle the whole scan as `completed_with_failures`, and
   every absence that scan staged — a disappeared ebook among them, in a
   mixed-format library — would be discarded unread on every pass.
6. **Recorded evidence is addressable without a review case.** An audiobook
   acquires no review case, so the `report_only` package row is what addresses
   its report: a read-only detail route keyed by connection, library and item
   serves the current document, reconstructed as a domain report rather than
   taken on the browse envelope's word. The identity travels in the query
   because a library server's item id may contain a slash, which no path match
   survives. Its row in the browser is a successful, openable entry rather
   than a failure card, and each observation carries the state the extraction
   gave it, so a malformed claim cannot read as sound evidence. Evidence that
   is recorded and retained but unreachable is not evidence an owner has —
   and a browser that drops the address is exactly as unreachable as no
   address at all.
7. **The audio source ceiling is its own setting.** An audiobook is read by
   header rather than whole, and is routinely larger than any EPUB, so the
   digest bound is `audio_source_bytes` (8 GiB by default, 16 GiB maximum,
   which is the domain's immutable inspection cap) rather than the EPUB
   archive limit, which would refuse a routine multi-hour recording outright.
   It is a per-field default on a closed model, so a `config-v6` document
   written before the key existed still validates and needs no increment.
8. **The catalogue fingerprint follows the audio file, not the item.**
   Audiobookshelf advances an item's `updatedAt` on playback, so an audio
   item whose fingerprint fell back to that timestamp was re-prepared —
   re-hashing gigabytes — because somebody listened to it. The single audio
   file's own revision is the material signal, and it is read exactly as an
   EPUB's is. The cover-path fallback stays scoped to the formats whose cover
   bytes this build acquires; an audiobook's art is recorded as presence and
   never acquired, so it earns no dependency on a timestamp playback moves.

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
- The probe costs the image almost nothing: 2 MB on disk (606 MB to 608 MB) and
  under 1 MB pulled (163,578,661 to 164,479,033 bytes compressed, arm64). The
  shipped media parser contains no muxer, encoder, filter, device, or network
  protocol at all, and no demuxer or decoder outside the two admitted
  containers.

### Negative

- The project owns the pin. There is no distribution security feed behind this
  binary, so a published FFmpeg CVE is answered by bumping `FFMPEG_VERSION` and
  `FFMPEG_SHA256` in the Dockerfile and rebuilding. That duty is manual: see
  the Renovate note below.
- Container qualification compiles FFmpeg. The container-image job restores
  unchanged layers from the repository Actions cache, so the compile re-runs
  only when its own inputs — the FFmpeg pin, base digest, or build stage —
  change.
- A deployment without the probe binary produces no audiobook evidence. The
  inspection ends with the typed `invalid_configuration` outcome rather than a
  partial report, and preparation records it as `internal_failure` — the same
  code the EPUB vocabulary gives that outcome — rather than as the unnamed
  `unknown`. Every other audio outcome is named the same way, so no audiobook
  failure reaches an owner without saying which one it was.

### Neutral or deferred

- Renovate does not track the FFmpeg pin. The repository's config enables four
  managers and carries no `customManagers` precedent, and a regex manager would
  bump `FFMPEG_VERSION` while leaving `FFMPEG_SHA256` stale — Renovate cannot
  hash the new tarball — producing a red PR on every release. The pin bump is a
  manual duty until a manager can move version and checksum together.
- The builder stage depends on nothing else in the build, so BuildKit overlaps
  the compile with the existing frontend and uv stages. Layer caching for the
  qualification job (`gha` cache in the workflow) would remove the repeat
  compile cost, and is a delivery-contract change explicitly outside this
  slice.
- Publication hashes the source twice. The currentness guard inside the publish
  transaction re-reads the file to prove it did not change since inspection,
  which for an audiobook is a second multi-gigabyte digest taken microseconds
  after the first, with no provider phase between them. Reusing the digest
  `inspect` computed would make the guard tautological rather than cheap, and
  narrowing it to the media paths that have no provider phase changes what the
  guard proves for three independent callers. That is a decision of its own,
  not a bounded edit, and is left for a later record. That record should weigh
  the middle ground: `open_local_media` already fstats the file at inspection
  time, so the `(dev, ino, size, mtime_ns)` identity tuple is a near-free
  currentness signal that is not tautological the way reusing the digest
  would be.
- Unmounted audiobook libraries stay excluded exactly as before this slice, and
  remain a later decision. Multi-file audiobooks were excluded here on the same
  terms and are admitted by ADR-0066, which composes several per-file documents
  into one item evidence set without changing the extraction this record fixes.
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

### The distribution `ffmpeg` package

Rejected on size, measured rather than estimated: `ffmpeg=7:5.1.9-0+deb12u1`
grew the image by 556 MB against the base this slice was first measured on
(261 MB to 817 MB on disk, 55 MB to 211 MB compressed).
Debian's `ffprobe` links 213 shared objects including `libavdevice` and
`libavfilter`, whose hard-dependency closure pulls LLVM, Mesa, and X11
libraries. `--no-install-recommends` does not avoid it, and neither does
copying only the binary out of the package: the closure is what that binary
links. It also ships every muxer, encoder, filter, device, and network protocol
FFmpeg builds by default, none of which this capability uses.

### A third-party prebuilt static `ffprobe`

Rejected on provenance: roughly 70 MB, and the artifact's build inputs and
flags are attested by whoever published it rather than verified here. Building
from a signature-checked upstream tarball with an explicit flag set costs
build time and gives an auditable binary whose contents this record can state.

### Replacing the probe with a Python metadata library

Rejected on coverage: `mutagen` and its peers read MP4 and ID3 tags but not the
Nero `chpl` chapter table this slice records, and `libmediainfo`'s coverage of
the Audible and tone freeform atom grammar is unverified. The probe already
reads every surface in one bounded document.

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
- Reopen triggers: an image size budget the probe closure cannot meet, or an
  audiobook capability that requires decoding rather than probing. The
  multi-file trigger fired and is answered by ADR-0066.

## Retained chapter omission evidence (#1042)

AudioInterpretation, audio-inspection-v4, ComposedAudio and metadata-report-v41
carry required exact-bool `chapter_rows_omitted`. It is true when an examined row
is discarded, including a malformed trailing row; readable partial chapters and
zero spans remain descriptive. False records no observed parser omission, never
complete original media. Overflow remains separate and may coexist with omission.
Single-file and concatenated embedded composition preserve/OR contributor flags;
generated per-track spans do not inherit discarded embedded rows and cannot admit
the chapter fallback. Failed child outcomes carry no omission evidence. Old v3
protocol and v40 report bytes are refused. Clean rebuild required under ADR-0013;
no adoption or migration is introduced.

The #1004 report-v42 extension consumes the existing v4 duration-absence and
chapter-omission facts without changing this protocol or parser authority. A
complete referenced chapter track may survive an unstated audio runtime; the
chapter endpoint remains coverage evidence, never a synthesized duration claim.
