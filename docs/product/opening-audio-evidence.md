# Opening-Audio Evidence

This is planned, unscheduled work. Mechanical Librarian does not transcribe
audio, and nothing here grants write authority.

## Direction

Spoken opening credits often state an audiobook's title, author, narrator,
series, publisher or performance type more reliably than a path or tag. The
plan is an optional adapter that transcribes a bounded opening window and
extracts explicit spoken claims as evidence. It excludes whole-book or
mid-book transcription, story-content identification, voice identity, and
sharing audio or voice data.

## Constraints any implementation must keep

**Optional and non-blocking.** Disabled, saturated or failing transcription
leaves the audiobook available and other evidence processing running. An
unavailable model, unsupported codec, quota failure or transcript with no
credits is an operational outcome, never evidence a field is absent.

**Bounded input.** Only opening windows of the first playable track are read.
Track choice is deterministic and recorded: target-service order first,
filename order as a fallback, extras excluded. Windows stay under a hard total
budget well inside the opening, and original offsets are kept. A changed item
fingerprint cancels the job before transcription.

**Separate stages.** Track selection, sampling, transcription, claim
extraction, matching and policy are separate. A `Transcriber` port
returns typed segments, language, timing, confidence, model identity and
failure class, never metadata decisions. Language comes from the audio or an
explicitly configured library language, never a path guess or global default.
Deterministic patterns handle clear phrases ("written by", "narrated by", "book
two of"). An isolated reviewer may resolve ambiguous grammar only by choosing
transcript-supported spans and closed role vocabularies.

**Local by default.** Models and runtimes are pinned, checksummed and installed
at image build time, never at run time. Any hosted transcription is disabled by
default and needs an explicit destination-specific opt-in that discloses the
audio and data sent, credentials, billing, retention and what works without it.

**Claims carry provenance.** Each claim records field, value, spoken role,
transcript span, audio offsets, component versions, item fingerprint, track,
language, confidence and warnings. Unclear values stay absent. The parser
never invents an identifier, person, series or sequence. Spoken text that
looks like an instruction is inert evidence.

**One lineage, not several sources.** A transcript, the claims parsed from it,
and any reviewer reading of it are one correlated lineage. Their agreement
never counts as independent support. Tags or path values used as spelling
hints are recorded, compared against a no-hint control, and never counted as
separate support.

**Spoken credits are evidence, not truth.** Credits can name the performer
first, describe another work in a preview, or be misheard. Claims may confirm
or propose a value, improve search or expose a contradiction. Catalogue,
tag, path and target evidence stay visible beside them. A spoken ISBN can aid
search or corroborate independent evidence, but alone never establishes an
edition. Several spoken ISBNs, or one that disagrees with other evidence, stay
unresolved; one spoken in a preview or about another work never counts, even
if it matches.

**Privacy.** Opening audio and transcripts are content evidence under the
[data-privacy policy](../architecture/data-privacy.md). Production audio stays
read-only. Temporary samples are always deleted, including after failure or
recovery, and never reach logs, support bundles, backups or caches. The
durable record holds hashes, segment metadata, cited spans, claims and
provenance, not audio. Keeping a full opening transcript is a separate
local-only retention choice. Reviewer packets carry only cited spans; exports
and support bundles carry no audio or transcript.

**Resources.** Transcription ranks below all core work. It runs under CPU,
memory, duration, temporary-space and concurrency bounds, one local job at a
time by default, with durable retry state, lease recovery and circuit
breakers. No database transaction or write lock is held while audio is
decoded or transcribed.

**Qualified per field.** Each field is qualified independently in shadow mode
before it can affect an automatic disposition, under the same gates as other
evidence. A
changed model, prompt, schema, sampling or backend version is a new evaluation
cohort. The feature returns to report-only if it harms playback or core work,
exceeds its qualified error rates, or cannot prove sample cleanup.

## Open decisions

An ADR or configuration contract must settle the backend and model packaging,
sample and resource limits, transcript retention default, languages, hosted
transcription, scheduling, and per-field thresholds before implementation.
