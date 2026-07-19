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
- Establish repository layout, CI, packaging, migrations, and release tooling.
- Create synthetic service, media, metadata-chaos, and reliability fixtures.

Exit: a contributor can build, test, and understand the empty product shell.

## M1: Read-only Audiobookshelf inventory

- Connect through the supported API.
- Consume target notifications when supported and detect changes through
  bounded polling otherwise.
- Reconcile with scheduled polling.
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
- F09b adds `hardcover-title-proposal-report` for one fixed generated
  `agree-empty` case. Omitting its optional secret-file reference performs no
  secret access, DNS, or egress; it changes no `config-v1` field and cannot
  reinterpret F09a's `synthetic_preview` history as live or
  production-applicable evidence. A sanitized live-shape fake qualifies normal
  automation, and the exact installed artifact must also parse one bounded live
  response. F09f retains configured provider-work persistence,
  freshness/reuse, restart, and strict ISBN-backed Hardcover identity; F09g
  retains the later live-target join and proposal authority.
- F10a adds `synthetic-title-review-case-report`. It persists, restarts,
  inspects, and replays one visibly synthetic undecided case only from one
  current immutable proposal set containing exactly one `needs_review` title
  proposal. A qualifying current successor supersedes the case; a
  nonqualifying current successor retires it without an empty replacement.
- Keep AI, human decisions, approval, covers, rich fields, multi-provider
  fusion, and production writes outside the preview.

The active sequence is completed F06 #142 plus #221, then F09a #145/#222,
F09b #204/#223, and F10a #146/#224.

Binding production local evidence to a current Audiobookshelf snapshot,
resolving configured media, and defining live freshness/recovery semantics are
deferred to the 0.3 slices. They are not hidden acceptance criteria for this
proof of concept.

Exit: one deterministic, bounded, explainable title proposal path survives
restart through one undecided case without gaining decision or write authority.

### Planned 1.0 evidence and review breadth

F07a #143 (#30, #31, and #188) and F07b #228 (#32) retain their planned scope
but are not 0.2 prerequisites; they begin the 0.3 checkpoint. F08 #144 (#33 and
#34), the broader candidate and provider program in #16, #46, #50, #68-#73,
and #209, and F11 #147/#74-#80 AI shadow review remain 1.0 work. They extend the
provider-neutral seams and immutable history proven by the preview rather than
being partially implemented inside it.

## M2a: First trustworthy live title report (0.3.0)

- F07a extracts one bounded opening-matter title and edition-applicable ISBN
  report from a safely readable EPUB; F07b binds that evidence durably to one
  current isolated target and snapshot with restart, reuse, and invalidation.
- F09f follows completed F09b and adds the disabled-by-default configured
  Hardcover lane. It persists one bounded title plus strict ISBN-backed book
  and compatible ebook-edition identity as one provider lineage, but remains
  target-independent and has no proposal authority. Its first increment ships
  only strict inert configuration/query/report seams: disabled returns before
  private input, while enabled returns `provider_not_activated`. Later
  increments implement the already-frozen external and durable contracts.
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

The active sequence is completed F06 #142 to F07a #143/#30/#31/#188 to F07b
#228/#32; completed F09b #204/#223 to F09f #239/#242; then F07b plus F09f to
F09g #238/#243. F09g is the 0.3.0 checkpoint exit. Completing this internal
checkpoint does not publish a release.

F09c #205/#210/#211 remains separately planned for 1.0 as a fallback
report/provider lane and is not an F09g blocker. Later provider-neutral work
#69/#70 consumes compatible F09c identity, with qualification in #73 and F17
#153. F10b #240/#241 remains 1.0 work after completed F10a and F09g; it admits a
qualifying live report into production review. F11 remains an independently
deliverable synthetic shadow path, and the production title/case and reviewer
paths converge only at F17 qualification.

Exit: one current Audiobookshelf ebook produces a restartable, evidence-backed
live title proposal or exact zero-proposal report without gaining review,
decision, mutation, destructive, or release-publication authority.

## M3: AI review in shadow mode

- Add isolated Codex CLI reviewer.
- Enforce schema-constrained decisions.
- Version prompts, policies, evidence, and models.
- Complete at least two shadow batches covering at least fifty mixed items.

Exit: no systematic unsafe decision pattern remains unexplained.

## M4: Production apply

- Enforce backup readiness.
- Add optimistic concurrency and service locks.
- Apply only validated field changes through supported APIs.
- Verify and roll back failures.
- Complete a ten-item canary and bounded production batches.

Exit: production writes are recoverable, idempotent, and fully audited.

## M5: Continuous operation

- Enable event-driven unattended processing.
- Add daily reconciliation and weekly summaries.
- Add circuit breakers, authentication recovery, and support diagnostics.
- Expose typed active stage, adapter, retry, and suspension state for unattended
  operation.
- Document upgrade and disaster-recovery drills.

Exit: new items are normally corrected without human involvement.

## M6: Review experience

- Implement the DUX-derived, snapshot-aware review-case and decision contract.
- Expose current unresolved work and historical automatic decisions.
- Provide target-level queue, decision-first detail, progressive evidence,
  confirmed sibling navigation, and review summaries.
- Support guarded individual and bulk decisions without coupling authoritative
  state to browser storage or run files.
- Pass real-browser mobile, keyboard, accessibility, stale-state, and
  performance gates.

Exit: occasional review is efficient, persistent, and server-authoritative.

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
