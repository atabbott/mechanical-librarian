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

- Use completed F06 package evidence without reopening it.
- F09a joins the current Audiobookshelf title, one eligible title from a current
  successful unambiguous applicable F06 `TITLE` observation, and one
  deterministic fake candidate through a provider-neutral port.
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
- F09b substitutes a disabled-by-default bounded Hardcover adapter behind the
  same port.
- F10 persists, restarts, inspects, replays, and supersedes one current
  undecided case only from one current immutable proposal set containing
  exactly one current `needs_review` title proposal; no other proposal or set
  creates a case.
- Keep AI, human decisions, approval, covers, rich fields, multi-provider
  fusion, and production writes outside the preview.

The active sequence is completed F06 #142 plus #221, then F09a #145/#222,
F09b #204/#223, and F10 #146/#224.

Exit: one deterministic, bounded, explainable title proposal path survives
restart through one undecided case without gaining decision or write authority.

### Planned 1.0 evidence and review breadth

F07a #143 (#30, #31, and #188), F07b #228 (#32), and F08 #144 (#33 and
#34) retain their planned scope but are not 0.2 prerequisites. The broader
candidate and provider program in #16, #46, #50, #68-#73, and #209, and F11
#147/#74-#80 AI shadow review, likewise remain 1.0 work. They extend the
provider-neutral seams and immutable history proven by the preview rather than
being partially implemented inside it.

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
