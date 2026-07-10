# Bounded Opening-Audio Evidence

## Status and purpose

This document defines a planned post-foundation capability. It is not part of
the first stable release and does not authorize production writes.

The goal is to treat explicitly spoken opening credits as bounded evidence for
audiobook identity and edition fields. A typical introduction may state the
title, author, narrator, series, publisher, or performance type more reliably
than a download path or incomplete tag.

The capability is worthwhile only if it remains optional, resource-bounded,
privacy-preserving, and independently qualified. Failure or absence of spoken
credits must leave the audiobook available and continue the ordinary evidence
workflow.

## Product decision

Plan for opening-credit transcription and structured claim extraction as a
future local-evidence adapter with these constraints:

- inspect only bounded opening windows from the most likely first playable
  track;
- prefer local transcription and require explicit opt-in before audio leaves
  the deployment;
- separate track selection, sample extraction, transcription, spoken-claim
  extraction, candidate matching, and policy decisions;
- retain transcript provenance and exact supporting spans;
- count a transcript and claims parsed from it as one correlated evidence
  lineage, not independent confirmation;
- introduce no direct apply authority; and
- qualify each field independently in shadow mode before it may affect an
  automatic disposition.

This capability does not include general story-content identification,
middle-of-book transcription, voice identity, whole-book transcription, or
community contribution of audio or voice data.

## Intended fields

Opening credits may support:

- title and subtitle;
- author or authors, with the spoken role preserved;
- narrator, cast, or performer names;
- series name and spoken sequence;
- abridged, unabridged, dramatized, full-cast, or similar performance markers;
- spoken language; and
- publisher, imprint, copyright year, or production credit when the field and
  edition applicability are clear.

Identifiers are not expected unless they are actually spoken. A transcript
parser never invents an identifier, person, series relationship, or sequence.

## Processing pipeline

### 1. Select the source track

Selection is deterministic and records why a file was chosen. Prefer target-
service track and chapter ordering when available. Use natural filename order
only as a documented fallback, and exclude samples, bonus material, interviews,
and obvious non-book extras where evidence supports that classification.

A changed item fingerprint invalidates the extraction job before transcription
begins.

### 2. Extract bounded opening windows

Use a staged strategy rather than assuming that the first fixed interval always
contains credits:

1. Inspect the initial speech-bearing window.
2. If it contains only silence, music, or incomplete credits, inspect a bounded
   continuation window.
3. Stop when explicit credit claims are found or the configured total opening
   budget is exhausted.

Candidate evaluation should compare total budgets such as 45, 90, and 180
seconds, with a hard maximum well inside the opening portion of the work.
Voice-activity detection may skip silence and long music sections, but the
original offsets remain part of provenance.

Sampling uses read-only input and a transient, low-bitrate speech-oriented
working file. Temporary audio is deleted on success, failure, timeout, process
recovery, and retention cleanup.

### 3. Transcribe through a replaceable port

The application owns a narrow `Transcriber` contract. A backend returns typed
segments, language facts, timing, confidence facts, model identity, and failure
classification. It cannot return metadata decisions or service payloads.

Language detection remains evidence-driven. A backend may use an explicitly
configured library language, but must not force a language from a path guess or
global default when multilingual operation is enabled.

Local processing is the default posture. Candidate local implementations such
as [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
[whisper.cpp](https://github.com/ggml-org/whisper.cpp) must be benchmarked for
licence, model distribution, image size, CPU time, memory, language coverage,
determinism, and supported architectures before selection. Models and runtime
dependencies are installed at image build time, pinned, checksummed, and
included in supply-chain review; the running application does not install
packages dynamically.

A hosted transcription backend is optional, separately configured, and
disabled by default. Setup discloses the exact audio duration and data sent,
credential and billing requirements, known retention controls, and the local
functionality that remains when it is disabled.

### 4. Extract structured spoken claims

Transcription and claim extraction are separate facts. Deterministic patterns
handle clear constructions such as "written by", "read by", "narrated by",
and "book two of". An isolated reviewer may resolve ambiguous grammar from a
bounded transcript packet, but must select from transcript-supported spans and
closed role vocabularies.

Current tags or clean path values may be evaluated as optional spelling
vocabulary for uncommon proper nouns. The run records every hint, compares a
no-hint control during qualification, and never counts a hint and the resulting
transcript as independent support.

Each claim records:

- normalized field and proposed value;
- spoken role;
- exact bounded transcript span and segment identifiers;
- audio offset range;
- transcript, extractor, schema, and model versions;
- source item fingerprint and selected-track identity;
- language and confidence facts;
- ambiguity, truncation, and contradiction warnings; and
- one upstream lineage identifier shared by all claims derived from the same
  transcript.

Unknown or unclear values remain absent. Prompt-like spoken text is untrusted
content and never changes extraction instructions, policy, or tool access.

### 5. Match and arbitrate

Spoken claims join the ordinary evidence and candidate pipeline. They may:

- confirm an existing value;
- improve catalogue search and candidate ranking;
- provide a new field proposal; or
- expose a contradiction for policy or human review.

Directly spoken credits are relevant evidence, not absolute truth. Credits may
name a performer before the author, contain production-company language,
describe another work in a preview, or be transcribed incorrectly. Catalogue,
tag, path, edition, and current-service evidence remain independently visible.

The transcript, parsed claims, and any reviewer interpretation of that same
transcript are correlated. Agreement among them cannot be counted as several
sources.

## Privacy and retention

Opening audio and its transcript are content evidence under the
[data-privacy policy](../architecture/data-privacy.md).

- Production audio remains read-only.
- Transient samples never enter logs, support bundles, backups, or provider
  caches.
- The default durable record contains transcript and sample hashes, segment
  metadata, cited bounded spans, claims, and provenance rather than the audio
  clip.
- Retaining a complete bounded opening transcript is a separate configurable
  retention choice with local-only storage and explicit deletion behavior.
- Reviewer packets contain only the cited spans needed for the decision.
- External transcription requires an explicit destination-specific opt-in.
- Export and support workflows exclude audio and transcript content by default.

## Resource and scheduling policy

Transcription is lower priority than ingestion availability, target reads,
review actions, applies, verification, backups, and recovery.

The job contract includes:

- configurable CPU, memory, duration, temporary-space, and concurrency bounds;
- one concurrent local transcription by default until a hardware profile is
  qualified;
- durable timeout, retry, and suppression state;
- cancellation and lease recovery with temporary-file cleanup;
- no open database transaction, item write lock, or service write lock while
  audio is decoded or transcribed;
- circuit breakers for repeated model, parser, hosted-provider, or resource
  failures;
- optional playback-aware deferral when target activity is available; and
- continued non-transcription evidence processing while this capability is
  disabled, saturated, or unhealthy.

An unavailable model, unsupported codec, quota failure, or transcript with no
credits is an operational or evidence outcome. It is never evidence that a
field is absent.

## Qualification plan

### Phase 0: backend and sampling benchmark

- Compare deterministic first-track selection and staged-window strategies.
- Benchmark candidate local backends on representative CPU architectures.
- Record image, model, memory, runtime, temporary-space, and language costs.
- Verify hard limits, cleanup, cancellation, and read-only operation.

### Phase 1: synthetic contract cohort

Use generated speech and licence-safe audio covering:

- clear title/author/narrator announcements;
- multiple authors, narrators, and cast members;
- author/narrator role reversal traps;
- series numbers, decimals, and ambiguous reading order;
- abridged, dramatized, and full-cast markers;
- silence, music, publisher idents, previews, and delayed credits;
- accents, multilingual credits, homophones, initials, and uncommon names;
- no credits, story text only, corrupted input, and unsupported codecs; and
- hostile spoken instructions that must remain inert evidence.

### Phase 2: report-only shadow cohort

Run against an explicitly selected private cohort without creating apply
authority. Measure:

- item-level credit-detection yield;
- precision and recall per field and role;
- transcript and structured-claim error classes;
- false author/narrator assignment;
- candidate improvement and contradiction rates;
- human correction and unresolved rates;
- runtime, queue delay, CPU, memory, storage, and optional hosted cost; and
- failures and resource effects during concurrent ordinary operation.

### Phase 3: bounded field qualification

Enable proposal support one field at a time. Automatic policy eligibility
requires versioned thresholds, representative evidence, no unexplained
systematic error, and the same backup, stale-state, apply, verification, and
rollback gates as every other evidence source.

## Stop conditions

The feature remains disabled or returns to report-only when:

- resource use materially affects playback or core processing;
- transcript retention or external disclosure is not configured safely;
- field or role error rates exceed the qualified profile;
- model, prompt, schema, sampling, or backend versions change without a new
  evaluation cohort;
- temporary sample cleanup cannot be verified;
- target fingerprint drift makes the sample stale; or
- repeated backend or parser failures open the applicable circuit breaker.

## Decisions required before implementation

- Select and record the local transcription backend and model packaging policy.
- Define hard sample-duration and resource limits from benchmark evidence.
- Decide whether complete bounded transcripts are retained by default.
- Define supported languages and fallback behavior.
- Decide whether hosted transcription is supported in the first implementation
  of this extension.
- Specify target-aware first-track selection and playback-aware scheduling.
- Define field-specific qualification thresholds and sampling cohorts.

These decisions require a reviewed ADR or configuration contract before the
feature becomes implementation-ready.
