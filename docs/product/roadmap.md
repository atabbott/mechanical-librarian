# Roadmap

Milestones are outcome-based. Dates should not be promised until the preceding
milestone has met its exit criteria.

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

- Preserve the F06 bounded EPUB package-title evidence outcome unchanged.
- Join one current Audiobookshelf title, exactly one eligible title observation
  from a current successful F06 report, and one provider title under explicit
  target, snapshot, and contract versions.
- Prove one provider-neutral, immutable, version-bound proposal set containing
  at most one title proposal with a deterministic fake provider, then substitute
  a disabled-by-default bounded Hardcover adapter behind the same provider
  port. Typed zero-proposal outcomes are durable proposal sets too.
- Persist, restart, replay, and supersede only the current proposal set
  containing exactly one current `needs_review` title proposal as one undecided
  review case; identical replay preserves semantic set and case identity.
- Keep the preview report-only: no automatic approval, human or AI decision,
  production write, cover, rich-field, or multi-provider workflow.

Exit: the supported title agreement matrix is deterministic, bounded,
explainable, and durable through one undecided review case.

### 1.0 expansion of evidence and candidates

The remaining evidence-and-candidate goals retain their long-term scope but are
not 0.2 exit criteria:

- integrate and use the already delivered F07 opening-matter and navigation
  evidence and F08 structural and audio evidence in candidate policy;
- use applicable mined identifiers and edition facts to seed candidates and
  distinguish work-level from edition-level matches;
- normalize broader provider candidates with provenance;
- produce field-level proposals without production writes;
- cache provider results only under a separately reviewed compatibility and
  retention contract, while classifying systemic provider failures; and
- qualify generic-title, initial-aware author, contributor-role, correlated-
  source, and same-title/different-author behavior against synthetic cohorts.

Exit: broader candidate generation is repeatable, bounded, and explainable for
1.0 qualification.

## M3: AI review in shadow mode (1.0.0, after the technical preview)

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
