# Delivery Slices

## Purpose

Mechanical Librarian is delivered through small end-to-end outcomes rather than
through completed horizontal work packages. WP0-WP9 remain capability ownership
buckets and issue parents. A functional slice crosses whichever packages are
needed to produce one executable, reviewable result.

The sequence below is the default implementation order. Each row becomes a
slice tracker, one `slice/...` branch, and one ready pull request. The tracker
contains the final ticket list and may narrow the outcome further before work
starts. If a row would exceed an `L` review, divide it into suffixed slices such
as `F05a` and `F05b`, each with an independent supported demonstration. Never
divide it into unreviewable horizontal-layer PRs.

The authoritative branch, issue, and review mechanics are defined in
[functional slice delivery](../development/slice-delivery.md).

## Focused work outside the slice sequence

Fixes, audit corrections, security work, documentation, tooling, dependencies,
maintenance, and standalone tickets use focused ticket branches and PRs. They
may be native prerequisites for a new-functionality slice, but they are never
absorbed into that slice merely to satisfy its checks.

Current focused work includes:

- contributor, CI, licence, publication, and governance work under #36-#43,
  #135, and #154;
- durability and architecture corrections #127, #130, and #131 before F01;
- port, scope, and configuration corrections #129, #132, and #134 before F03;
- evidence-bound correction #133 before F06; and
- lifecycle and authorization correction #128 before F10.

## Release 0.1.0: trustworthy report-only product spine

Production metadata and cover writes remain disabled throughout these slices.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Expected branch |
| --- | --- | --- | --- | --- |
| F01 Durable state check | #136 | A CLI diagnostic creates or opens application state, applies migrations atomically, records and reads a safe audit event, and reports failure without exposing a database path. | #51-#53 | `slice/f01-durable-state-check` |
| F02 Recoverable job heartbeat | #137 | A bounded synthetic dependency graph schedules the complete eligible set with stable deduplication, survives worker, scheduler, and process restart under leases and fencing, persists nonterminal `waiting` separately from `retry_wait`, and advances fan-in only after independently validating expected outcomes. | #54-#58, #126 | `slice/f02-recoverable-job-heartbeat` |
| F03 Synthetic inventory round trip | #139 | Validated configuration drives a synthetic library read; one scoped item and immutable snapshot are persisted and inspected through the CLI without database access. | #49 and slice-specific implementation tickets | `slice/f03-synthetic-inventory` |
| F04 Audiobookshelf connection report | #140 | A configured isolated Audiobookshelf instance can be authenticated, versioned, capability-discovered, and diagnosed without reading or mutating library content. | #60, #61 | `slice/f04-audiobookshelf-connection` |
| F05 Persisted Audiobookshelf inventory | #141 | One real isolated library is paged, normalized, fingerprinted, persisted, reconciled, and inspected; duplicate notifications and restart do not duplicate work. | #62-#67 | `slice/f05-audiobookshelf-inventory` |
| F06 EPUB package evidence report | #142 | One generated EPUB is parsed within hostile-input bounds and produces a cited, bounded package-metadata evidence report through a supported surface. | #13, #21, #27-#29 | `slice/f06-epub-package-evidence` |
| F07 EPUB opening-matter evidence report | #143 | The same isolated workflow selects bounded opening matter, extracts edition-aware claims with exact provenance, persists them, and explains unsupported or rejected input. | #25, #26, #30-#32 | `slice/f07-epub-opening-evidence` |
| F08 Audiobook health report | #144 | One generated audiobook produces bounded tag, chapter, duration, and media-health observations without transcription or production mutation. | #33, #34 | `slice/f08-audiobook-health` |

### 0.1.0 release exit

- the focused foundation and trusted-change-loop prerequisites pass from a
  clean checkout;
- an operator can inspect isolated Audiobookshelf inventory and local evidence
  without shell-level database access;
- duplicate work, process restart, malformed media, authentication failure, and
  unsupported capability behavior are bounded and visible; and
- no development or CI path points at a contributor's production library.

## Release 0.2.0: explainable decisions and shadow review

These slices produce proposals and reviewer recommendations but still grant no
production write authority.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Expected branch |
| --- | --- | --- | --- | --- |
| F09 Candidate and proposal report | #145 | Persisted current, embedded, and provider evidence becomes target- and work-identified, evidence-family-aware, independent field proposals under the identity, independence, no-op, conflict, and provider-failure rules; title cleanup preserves meaningful structure and removes meaningful series/order text only with independently supported structured sibling capture; identifier comparison retains raw lineage, uses canonical compatible namespaces, and applies the two-family replacement gate only to validated current comparable identifiers; qualified versioned series-label mappings preserve raw lineage and unknown labels; projected source AI recommendations remain provenance only while destination evidence and policy are independently requalified; deterministic cleanup or degradation resolves through policy without erasing sibling proposals or requiring AI `manual`. | #16, #46, #68-#73 | `slice/f09-candidate-proposals` |
| F10 Persistent review case | #146 | One proposal set becomes a snapshot-aware review case whose typed, target-scoped coverage readiness, durable stage checkpoints, blocker facts naming the required scope, stage, target, and outcome, history, supersession, and authorization guards survive restart without blocking unrelated ingestion. | #50 and slice-specific implementation tickets | `slice/f10-persistent-review-case` |
| F11 AI shadow recommendation | #147 | An isolated reviewer receives a bounded packet only after required authoritative evidence and provider inputs have accepted terminal outcomes, returns exact per-proposal recommendations, and exposes independently audited coverage or typed failure without authorizing apply; a zero-proposal selection terminates without reviewer invocation, and optional provider failure remains operational. | #74-#80 | `slice/f11-ai-shadow-review` |

### 0.2.0 release exit

- candidate and proposal generation is deterministic, bounded, and
  provenance-complete;
- independent evidence-family rules, decisive single-family exceptions,
  target and work identity, edition-sensitive identity or applicability, no-op
  omission, field-relevant conflicting-identifier rejection, and provider
  failure separation pass the synthetic title cohort;
- directional title cleanup preserves meaningful subtitle and structured
  series/order information, removes meaningful series/order text only with an
  independently supported persisted or emitted structured sibling proposal,
  rejects unsupported noise additions, and resolves deterministic cleanup or
  degradation through policy rather than AI `manual`;
- raw identifier provenance, alias and ISBN canonicalization, compatible
  namespace comparison, non-comparable neutrality, and independently
  corroborated replacement of a validated current comparable identifier pass
  the synthetic identifier cohort; malformed or invalid current identifiers
  remain governed by ordinary applicable field evidence;
- current metadata remains sourced evidence, and resolving one field leaves
  every sibling proposal intact;
- series-label reconciliation is qualified and versioned, preserves raw source
  lineage and replay or supersession history, and leaves unknown labels
  unchanged; projected source AI recommendations add no destination evidence,
  consensus, disposition, decision, or authority;
- a review case remains authoritative across restart and stale input, resumes
  only from valid checkpoints, and advances only when independently recomputed
  coverage matches its selected target scope; blockers identify the required
  scope, stage, target, and outcome;
- AI completion is independently audited rather than inferred from process or
  artifact success, and `manual` remains AI semantics for multiple specifically
  supported alternatives; it may complete reviewer output but creates neither
  a human decision nor apply readiness;
- zero proposals completes proposal selection without reviewer invocation,
  while every invoked packet has exact per-proposal output coverage;
- arbitrary, stale, AI-only, or destructive authorization cannot be
  constructed; and
- reviewer timeout, malformed output, citation failure, and authentication
  recovery are typed safe states.

## Release 1.0.0: guarded mutation and product operation

Write-capable slices use fake or disposable services until their explicit
canary slice. Destructive operations remain outside ordinary review and apply.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Expected branch |
| --- | --- | --- | --- | --- |
| F12 Human decision round trip | #148 | A user accepts, keeps, or rejects one field through the versioned API; the decision survives restart and stale submissions fail closed. | #17-#19, #97, #98 | `slice/f12-human-decision` |
| F13 Guarded fake-service apply | #149 | A current human-approved field change is planned, backed up, idempotently applied to a fake service, read back, verified, audited, and safely rolled back on injected failure. | #81-#83, #86 | `slice/f13-guarded-fake-apply` |
| F14 Audiobookshelf canary apply | #150 | The same gates perform one bounded field or cover mutation against a disposable qualified Audiobookshelf instance and prove before/request/after evidence. | #84, #85, #87 | `slice/f14-audiobookshelf-canary` |
| F15 Containerized report-only operation | #151 | The trusted read and review paths run in the supported single container with health, diagnostics, backup, restore, and upgrade checks while writes default off; supervised auto-resume retains immutable bounded report-only authority through the application scheduler, and advisory ETA reports its basis, timestamp, freshness, or unknown state. | #88-#93 | `slice/f15-container-report-only` |
| F16 Review operator experience | #152 | The target queue, decision-first detail, progressive evidence, sibling navigation, guarded bulk flow, accessibility, and performance contracts work in a real browser; server-authoritative summaries distinguish targets, ordinary and destructive cases, field work items, and planned changes with explicit stale and partial semantics. | #20, #96, #99-#103 | `slice/f16-review-experience` |
| F17 Continuous-operation qualification | #153 | Events, reconciliation, review, guarded apply, circuit breakers, dependency fan-out and fan-in, duplicate-safe resume, cancellation, worker and scheduler death, report-only authority isolation, advisory progress, restart, upgrade, backup, restore, release provenance, and support diagnostics pass the release cohort. | #94, #95 and a slice-specific release cohort | `slice/f17-release-qualification` |

### 1.0.0 release exit

- every write is current, authorized, backed up, idempotent, verified, audited,
  and recoverable;
- read-only discovery and diagnosis continue when writes are disabled;
- unattended report-only continuation resumes from durable scheduler state
  without widening its initiating principal, authorization context, scope,
  versions, bounds, expiry, or mutation authority; each transition actor is
  recorded against that authority, and process IDs or detached watchers remain
  diagnostics only;
- the normal deployment is one supported container with one persistent volume,
  read-only media mounts, and one HTTP port;
- the review experience is persistent, accessible, responsive, and
  server-authoritative, including typed count units and stale or partial
  summary semantics; and
- release, upgrade, restore, isolation, and end-to-end qualification evidence is
  complete.

## Deferred slice series

Opening-audio transcription (#24 and #104-#110), additional adapters,
embedded-file updates, and plugin loading remain unassigned backlog work until
their prerequisite design and qualification decisions are accepted. When
scheduled, they receive new functional slice IDs and executable outcomes; they
are not appended opportunistically to an active branch.

## Planning rules

- The highest-priority unblocked slice determines execution order across work
  packages.
- The Project `Delivery slice` field records each new-functionality ticket's
  primary slice. Focused work uses `Not applicable`.
- Slice trackers carry the exact closure list; the issue ranges above are
  planning inputs, not permission to close a parent whose full criteria are not
  satisfied.
- A deliberately narrow adapter or interface is acceptable when it completely
  demonstrates the slice and preserves compatibility for later extension.
- No slice may defer its required safety, recovery, observability,
  documentation, migration, or test obligations to a later slice.
- A work-package parent closes only when all owned outcomes are complete. A
  merged slice proves its bounded outcome, not wholesale work-package
  completion.
