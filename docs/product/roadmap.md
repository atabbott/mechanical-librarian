# Roadmap

Milestones are outcome-based. Dates should not be promised until the preceding
milestone has met its exit criteria.

During pre-release development, the numbered delivery groupings are internal
checkpoints rather than release-publication commitments. The roadmap milestones
and numbered checkpoints retain all of their exit criteria whether or not a
release is published. Completing one does not create a tag, GitHub Release,
supported version, or published release artifact. The first release publication
requires a separate maintainer decision selecting a checkpoint with genuinely
usable functionality that provides value to the owner or another user, followed
by every applicable release gate and release-publication gate. This roadmap
selects neither that checkpoint nor its version. After the first published
release, maintainers may explicitly designate later milestones as release
trains; release publication does not make that automatic.

## M0: Foundation

- Enforce the recorded `AGPL-3.0-or-later` licence and notice policy.
- Publish architecture, security, quality, and contribution contracts.
- Establish repository layout, CI, packaging, clean-schema safety, and release tooling.
- Create synthetic service, media, metadata-chaos, and reliability fixtures.

Exit: a contributor can build, test, and understand the empty product shell.

## M1: Read-only Audiobookshelf inventory

- Connect through the supported API.
- Reconcile through bounded scheduled polling. Optional notification-assisted
  reconciliation remains deferred under #65.
- Persist canonical items, snapshots, fingerprints, and jobs.
- Provide health and inventory CLI/API surfaces.
- Prove persistent deduplication and bounded retry behavior under repeated
  failed delivery, worker death, and restart.

Exit: restarts and duplicate events cannot lose or duplicate inventory work.

## M2: Title-curation technical preview (0.2.0)

- Use completed F06 package evidence without reopening it.
- F09a proves that join only inside a closed synthetic harness: it generates a
  licence-safe EPUB, runs the real F06 child, creates an in-memory
  Audiobookshelf-shaped snapshot, and invokes one deterministic fake through a
  provider-neutral port. It accepts no live target, media, title, or provider
  input.
- Create one immutable versioned proposal set containing at most one
  `needs_review` title proposal. The eligible F06 `normalized_value` is
  non-empty, bounded, and usable rather than an unsafe, oversized, or redaction
  marker; its exact value becomes the proposal, F06 `raw_value` remains
  evidence/provenance, and provider evidence only corroborates.
- Treat a current title field that is present and loaded with an empty value as
  eligible for fill when F06 and provider corroborate; a materially different
  current title also remains eligible. Record a missing, unloaded, or
  unavailable current title, an empty or unusable F06/provider title,
  current-equivalence, disagreement, and other non-proposal paths as exact typed
  no-proposal results.
- F09b historically added `hardcover-title-proposal-report` for one fixed generated
  `agree-empty` case. Omitting its optional secret-file reference performs no
  secret access, DNS, or egress; it changes no `config-v1` field and cannot
  reinterpret F09a's `synthetic_preview` history as live or
  production-applicable evidence. A sanitized live-shape fake qualifies normal
  automation, and its exact installed artifact completed one bounded live
  response qualification. The credentialed F09b command is retired and that
  request must not be repeated. F09f now owns configured provider-work persistence,
  freshness/reuse, restart, and strict ISBN-backed Hardcover identity; F09g
  retains the later live-target join and proposal authority.
- F10a historically added `synthetic-title-review-case-report` for one visibly
  synthetic undecided case. The command is retired and the installed CLI rejects
  its name; its persistence, replay, supersession, and retirement behavior is
  historical only.
- Keep AI, human decisions, approval, covers, rich fields, multi-provider
  fusion, and production writes outside the preview.

The historical sequence was completed F06 #142 plus #221, then F09a #145/#222,
F09b #204/#223, and F10a #146/#224.

Binding production local evidence to a current Audiobookshelf snapshot,
resolving configured media, and defining live freshness/recovery semantics are
deferred to the 0.3 slices. They are not hidden acceptance criteria for this
proof of concept.

Exit: one deterministic, bounded, explainable title proposal path survives
restart through one undecided case without gaining decision or write authority.

### Planned 1.0 evidence and review breadth

F07a #143 and F07b #228 were not 0.2 prerequisites; they began the 0.3
checkpoint. Their former constituents, F08's #33/#34 inputs, the broader #16,
#46, #50, #68–#73 and #209 program, and F11's #74–#80 inputs are historical
planning detail rather than current 1.0 tickets. The current 1.0 outcome set is
defined below and in the [delivery-slice plan](delivery-slices.md).

## M2a: First trustworthy live title report (0.3.0)

- F07a extracts one bounded opening-matter title and edition-applicable ISBN
  report from a safely readable EPUB; F07b binds that evidence durably to one
  current isolated target and snapshot with restart, reuse, and invalidation.
- F09f follows completed F09b and now provides the disabled-by-default configured
  Hardcover lane. It persists one bounded title plus strict ISBN-backed book
  and compatible ebook-edition identity as one provider lineage, but remains
  target-independent and has no proposal authority. Its strict inert
  configuration/query/report seams were followed by the bounded
  fixed adapter, schema-11 durable lifecycle, and configured activation.
  Disabled returns before private input; enabled persists before lazy provider
  authority and emits only closed redacted outcomes.
- F09g follows F07b and F09f and owns the current Audiobookshelf target/source
  join. One usable current F07 title, exactly one distinct canonical
  checksum-valid edition-applicable local ISBN, one unique compatible
  ISBN-equivalent Hardcover ebook edition and book with an agreeing title, and
  a loaded empty or materially different current title produce the sole
  positive output: one `needs_review`, `production_report_only` proposal whose
  value is the exact current F07 normalized title.
- Repeated equivalent claims of the same canonical local ISBN do not force a
  zero-proposal result. Missing, unloaded, invalid, conflicting, ambiguously
  applicable, multiple distinct, ambiguous, incompatible, stale, disabled,
  failed, equivalent, and disagreeing inputs produce exact typed zero-proposal
  results without discarding valid evidence. The independence claim is local
  EPUB evidence versus the external Hardcover catalogue; the Hardcover book,
  edition, and title are one provider lineage.
- Keep review admission, human and AI decisions, approval, apply, target and
  media writes, destructive authority, tagging, and release publication out of
  the checkpoint.

The historical sequence was completed F06 #142 to F07a #143/#30/#31/#188 to F07b
#228/#32; completed F09b #204/#223 to F09f #239/#242; then F07b plus F09f to
F09g #238/#243. F09g is the 0.3.0 checkpoint exit. Completing this internal
checkpoint does not publish a release.

The current 1.0 outcome set is F08 #144, F10b #240, F11 #147, F15a #151,
F16a #152, F13 #149, F14 #150, and F17 #153. The retained `F` IDs are planned
designations rather than `Ready` status. F09o #70 has moved into milestone
0.9.0 with #344 as its pre-activation evidence-report increment. Open Library
#210 is the completed 0.8.0 F09l slice tracker, while the other
deferred provider outcomes remain outside this checkpoint. Closed WP parents,
provider trackers, and constituent forests are historical planning inputs, not
current delivery work. The authoritative outcome definitions are in the
[delivery-slice plan](delivery-slices.md).

F09g #238 remains historical characterization evidence for the current report
boundary, not an active F10b input. Release artifact production #94 and
publication-readiness #43 remain separate `first-release` gates rather than F17
work.

Exit: one current Audiobookshelf ebook produces a restartable, evidence-backed
live title proposal or exact zero-proposal report without gaining review,
decision, mutation, destructive, or release-publication authority.

### Pre-deployment 0.4.0 simplification checkpoint

The qualified F09g #238 report is characterization evidence for the reset: its
current target/media reading, local title/ISBN extraction, provider observation,
currentness, privacy, and persistence behavior remain the safety baseline, but
its proposal gate is replaced at #197 by one neutral evidence report. The
checkpoint starts from a clean database and exposes exactly one synchronous
report-only command with explicit configuration, database, connection, library,
item, and EPUB-file inputs. #253, #255, #257, and #261 are the four contributors
to the #197 activation; #254, #256, #258, and #260 are superseded historical
work to reconcile, not active contributors.

The checkpoint excludes review, decisions, apply, writes, background execution,
retry, caching, compatibility paths, tags, releases, and publication. It is an
internal pre-deployment checkpoint, not a supported version or release train.

### Completed 0.5.0 ebook metadata-and-cover evidence checkpoint

- F09i #293 completes the bounded Audiobookshelf and structured EPUB field
  graph, including deterministic opening and rear evidence.
- F09j #294 adds the configured ISBN-backed Hardcover work/edition observation
  with rich field-local comparisons and explicit disabled/failure outcomes.
- F09k #295 adds validated Audiobookshelf, EPUB-declared, and Hardcover cover
  source evidence with private content-addressed originals and deterministic
  offline previews.
- Exact replay remains within the exact current schema; changed evidence creates
  immutable successors.
- Keep proposal selection, review, decisions, apply, target/media writes,
  background work, retries, compatibility paths, tags, releases, and
  publication out of the checkpoint.

Exit: one selected current Audiobookshelf EPUB produces and reopens one complete
source-attributed metadata-and-cover evidence report without gaining proposal,
review, decision, or write authority.

### Completed 0.6.0 catalogue and library-scale evidence checkpoint

- F05c #300/#301 atomically refreshes a current catalogue of at most 10,000
  scoped entries across configured Audiobookshelf book libraries, preserving
  independent ebook/audio facets and exact eligibility classifications.
- F18 #302/#303 freezes explicit selections or all eligible EPUBs into one
  immutable secret-free manifest and serially runs the complete final-0.5
  report operation in the foreground.
- Batch execution revalidates the current generation and item before each
  report, streams deterministic outcomes, and preserves completed reports on a
  later stop.
- Recovery is a fresh operator-created manifest and run. #304's durable
  attempt, resume, retry, lock, and runner-state design was explicitly removed;
  scheduling, queues, workers, concurrency, and continuous intake remain later
  work.

Exit: an operator can discover a current configured book catalogue and run the
complete evidence operation for selected or all eligible EPUBs without gaining
proposal, review, decision, write, or background authority.

### Completed 0.7.0 qualified ebook metadata-matching checkpoint

- #319 fixed the supported qualification vocabulary; #320 classified remaining
  evidence gaps by recoverability.
- #321 preserved validated evidence across optional rear-pass exhaustion; #324
  retained provider-local work identity independently from edition identity.
- F09n #328 classified ISBN evidence relationships without discarding claims.
- F07c #322 improved contributor precision and one bounded series-position cue;
  F06a #323 admitted standards-valid signed-offset publication dates.
- F09m #327 preserved explicit publisher and imprint roles without hierarchy
  inference; focused #325 reconciled the resulting plans.
- Relative to the accepted F09n aggregate baseline, matched rows finish at 266
  from 258, complete rows at 238 from 230, candidate-atom precision at
  0.819484 from 0.805233, and explicit-negative false positives at 0 from 7.

Exit: the qualified report-only matching programme is complete without adding a
provider, proposal, review, decision, mutation, generic scoring framework,
background operation, tag, or release.

### Completed 0.8.0 independent multi-provider ebook evidence checkpoint

The [0.8.0 working plan](0-8-working-plan.md) records the delivered independent
provider outcome: Open Library is the second real metadata adapter, and
qualification proves
that one immutable report preserves independent local, Hardcover, and Open
Library evidence. Provider order, failure, absence, malformed or incomplete
data, and contradiction remain separate canonical observations rather than
votes or a fused result. Open Library reaches behavioral parity with every
existing Hardcover evidence capability for which it exposes semantically
equivalent data; unsupported parity fields remain explicit rather than
guessed. Beyond
parity, the report preserves a bounded allowlist of typed provider identities
and revision provenance, selected identifiers present on the required edition
or work records, subjects and classifications, edition characteristics,
contributor identities, the exact edition-to-work relationship, and cover
identities. It does not traverse author records, enumerate editions, or issue
fallback searches solely for enrichment.

The implementation keeps fuzzy fallback, provider ranking, proposals, review,
decisions, UI, apply, writes, background execution, unbounded provider-backed
batches, raw provider payloads, arbitrary upstream properties and link
collections, community signals, lending or full-text features, a third
provider, and release publication out. Rich evidence remains source-scoped and
cannot rank candidates, map subjects into tags or genres, or create proposals.
Open Library participation is limited to a documented low-volume operating
envelope; larger batches remain usable with that provider disabled and fail
closed before egress when it is enabled outside the envelope. The checkpoint
uses the existing foreground batch lifecycle with a fixed ceiling and
single-attempt posture; it adds no cache, retry, scheduling, rate-policy, or
provider-cost framework.

Open Library #210 completes milestone 0.8.0 as the one-issue F09l vertical
slice with no constituents.

### 0.9.0 explainable ebook proposal checkpoint

The [0.9 working plan](0-9-working-plan.md) records F09o. Its #344
pre-activation increment deepens the report to `metadata-report-v8`, binding
all 16 current ABS destinations, exact ISBN versus fail-closed complete-author
work observation, typed taxonomy/first-publication evidence, and the bounded
provenance-bearing provider cover gallery. The delivered #70 activation
increment derives one deterministic immutable result for each destination from
that exact current report without further provider access.

This checkpoint now includes exact-current immutable review admission. It adds no
reviewer or AI decision, approval, apply, target or media write, fuzzy discovery, configurable
policy, background execution, migration, tag, or release publication.

## M3: AI review in shadow mode

- Add isolated Codex CLI reviewer.
- Enforce schema-constrained decisions.
- Version prompts, policies, evidence, and models.
- Complete at least two shadow batches covering at least fifty mixed items.

Exit: no systematic unsafe decision pattern remains unexplained.

## M4: Production apply

- Require an applicable current human decision from F16a #152 before F13 #149
  proves guarded fake-service apply; F14 #150 follows completed F13
  qualification. These delivery dependencies govern even though this capability
  grouping appears before M6.
- Enforce backup readiness.
- Add optimistic concurrency and service locks.
- Apply only validated field changes through supported APIs.
- Verify and roll back failures.
- Complete a ten-item canary and bounded production batches.

Exit: production writes are recoverable, idempotent, and fully audited.

## M5: Continuous operation

- Enable persisted scheduled unattended processing with polling as the
  authoritative reconciliation path.
- Keep notification-assisted reconciliation and summary enhancements deferred
  under #65 until separately promoted.
- Add circuit breakers, authentication recovery, and support diagnostics.
- Expose typed active stage, adapter, retry, and suspension state for unattended
  operation.
- Qualify exact-current restart and recovery. Older-schema migration and restore
  remain post-release work under #57.

Exit: new items progress continuously into bounded evidence, proposals, and
review; any mutation still requires a qualified policy or human decision and
the guarded apply path.

## M6: Review experience

- Implement the DUX-derived, snapshot-aware review-case and decision contract.
- Deliver the authenticated target-level queue, decision-first detail,
  progressive evidence, sibling navigation, and one guarded individual decision
  through F16a #152.
- Qualify that individual round trip through the F16a first-release responsive,
  keyboard/focus, accessibility, private caching/storage, content-security,
  hostile-rendering, performance, synthetic-artifact, and primary-supported-
  browser baseline.
- Keep cross-case summaries and history, bulk decisions, reversal, additional
  browser and viewport coverage, and extended accessibility, responsive,
  privacy, content-security, performance, and synthetic-artifact budgets
  deferred together under #195.

Exit: occasional individual review is efficient, persistent, server-
authoritative, and qualified against the first-release browser baseline.

## M7: Ecosystem

- Add additional service and AI adapters.
- Evaluate embedded-file updates independently.
- Benchmark and shadow-test bounded opening-audio transcription and spoken-
  credit extraction under the
  [opening-audio evidence contract](opening-audio-evidence.md).
- Qualify transcript-supported title, author, narrator, series, and performance
  fields independently before any automatic-policy eligibility.
- Formalize plugin compatibility and community governance.

Exit: advanced evidence and adapter extensions remain optional, capability-
declared, resource-bounded, and independently qualified without widening core
write authority.
