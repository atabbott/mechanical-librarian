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

Fixes, audit corrections, security work, documentation, tooling,
dependency-only changes, maintenance, and standalone tickets use focused
ticket branches and PRs. They may be native prerequisites for a
new-functionality slice, but they are never absorbed into that slice merely to
satisfy its checks. A slice may still introduce a dependency needed for its
observable outcome under the repository dependency policy.

Current focused work includes:

- contributor, CI, licence, publication, and governance work under #36-#43,
  #135, and #154;
- durability and architecture corrections #127, #130, and #131 before F01;
- port, scope, and configuration corrections #129, #132, and #134 before F03;
- evidence-bound correction #133 before F06; and
- lifecycle and authorization correction #128 before F10; and
- frontend architecture decision #192 before F16a.

## Release 0.1.0: trustworthy report-only product spine

Production metadata and cover writes remain disabled throughout these slices.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Expected branch |
| --- | --- | --- | --- | --- |
| F01 Durable state check | #136 | A CLI diagnostic creates or opens application state, applies migrations atomically, records and reads a safe audit event, and reports failure without exposing a database path. | #51-#53 | `slice/f01-durable-state-check` |
| F02 Recoverable job heartbeat | #137 | A bounded synthetic dependency graph pins an immutable workflow-definition version and graph digest, schedules the complete eligible set with stable deduplication, survives worker, scheduler, and process restart under leases and fencing, persists nonterminal `waiting` separately from `retry_wait`, and advances fan-in only after independently validating expected outcomes. An incompatible graph creates linked superseding or replanned work whose transitively affected downstream checkpoints are ineligible for reuse or current applicability without mutating or erasing original history. | #54-#58, #126 | `slice/f02-recoverable-job-heartbeat` |
| F03 Synthetic inventory round trip | #139 | Validated configuration drives a vendor-neutral synthetic target-service read; one item whose uninterpreted external value exists only inside an opaque adapter/connection/library-scoped canonical identity and one immutable snapshot are persisted and inspected through the CLI without database access or vendor fields. | #49, #187 | `slice/f03-synthetic-inventory` |
| F04 Audiobookshelf connection report | #140 | A configured isolated Audiobookshelf instance can be authenticated, versioned, capability-discovered, and diagnosed without reading or mutating library content. | #60, #61 | `slice/f04-audiobookshelf-connection` |
| F05a Persisted Audiobookshelf inventory | #141 | One real isolated Audiobookshelf book library is boundedly paged twice through the pinned 2.35.1 API, translated at the adapter boundary into canonical vendor-neutral inventory, fingerprinted, persisted, safely reconciled, restarted, and inspected. | #62-#64 | `slice/f05a-audiobookshelf-inventory` |
| F05b Audiobookshelf reconciliation | #215 | Persisted polling triggers duplicate-safe reconciliation with bounded retry/backoff, restart recovery, and explicit compatibility-drift states; Socket.IO notifications remain excluded. | #66, #67 | `slice/f05b-audiobookshelf-reconciliation` |
| F06 EPUB package evidence report | #142 | One generated EPUB is parsed within hostile-input bounds and produces a cited, bounded package-metadata evidence report through a supported surface. | #26-#29; #13 and #21 are non-closing inputs | `slice/f06-epub-package-evidence` |
| F07 EPUB opening-matter evidence report | #143 | The same isolated workflow consumes F06's completed bounded local-evidence contract, selects bounded opening matter, extracts edition-aware claims with exact provenance, persists them, and explains unsupported or rejected input. | #25, #30-#32, #188; consumes completed #26 | `slice/f07-epub-opening-evidence` |
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
| F09 Candidate and proposal report | #145 | A provider-disabled configuration turns canonical current-state and local evidence into target- and work-identified, evidence-family-aware independent proposals, deterministic no-ops/rejections, and bounded human-review work under a versioned sufficiency matrix keyed by field, media, operation, and change class; a configured-provider variant improves evidence without making provider name part of semantic proposal identity. Manifests derive each role from only its relevant dimensions—inventory from target/scope, local evidence from target/media/field/extractor, provider evidence from target/media/field/provider/query/configuration, and candidates from completed relevant evidence/policy—then record applicability for every derived lane, typed execution outcome for applicable lanes, and candidate count plus validated `executed` or `reused` mode only for successfully completed applicable lanes, with missing lanes treated as coverage failures; identical normalized values retain per-source assessments, generic adapter/provider provenance, correlation, and contradictions for deterministic aggregation; title cleanup preserves meaningful structure and removes meaningful series/order text only with independently supported structured sibling capture; identifier comparison retains raw lineage, uses canonical compatible namespaces including ISBN-10/ISBN-13 equivalence, applies the two-family replacement gate only to validated current comparable identifiers, and accepts one distinct checksum-valid content ISBN as strong edition evidence only with typed format- and edition-applicable provenance, a materially independent format-appropriate provider match, and agreeing target, work, and media identity, leaving multiple ISBNs, unrelated previews or other-work claims, correlated providers, edition, format, narration, abridgement, dramatization, omnibus, and cross-media conflicts unresolved; qualified versioned series-label mappings preserve raw lineage and unknown labels; projected source AI recommendations remain provenance only while destination evidence and policy are independently requalified; deterministic cleanup or degradation resolves through policy without erasing sibling proposals or requiring AI `manual`. | #16, #46, #68-#73 | `slice/f09-candidate-proposals` |
| F10 Persistent review case | #146 | One proposal set becomes a snapshot-aware review case whose typed, target-scoped coverage readiness, durable stage checkpoints, blocker facts naming the required scope, stage, target, and outcome, history, supersession, and authorization guards survive restart without blocking unrelated ingestion. | #50 and slice-specific implementation tickets | `slice/f10-persistent-review-case` |
| F11 AI shadow recommendation | #147 | An isolated reviewer receives a complete bounded vendor-neutral packet carrying the exact normalized current snapshot and explicit field-load state only after required authoritative inputs join successfully, without target-service or provider credentials or vendor payloads, then returns one direct closed-schema `AIDecision` for every proposal; its required `problems` list contains adverse or unresolved problem codes, with `problems: []` for a clean approval. Malformed output, unknown fields, missing required fields, duplicate or partial decisions, trailing content, or semantic repair becomes a typed failure without authorizing apply. A zero-proposal selection terminates without invocation, and optional provider failure remains operational. | #74-#80 | `slice/f11-ai-shadow-review` |

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
| F12a Authenticated API foundation | #189 | The supported HTTP service exposes deterministic versioned OpenAPI, minimal anonymous liveness, and one bounded authenticated owner diagnostic; invalid sessions, origins, CSRF, proxy and rate-limit contexts fail closed. | #96, #97 | `slice/f12a-authenticated-api` |
| F12b Human decision round trip | #148 | An authenticated owner accepts, keeps, or rejects one field, restarts and retrieves the immutable decision/history, then observes a safe stale-version conflict. | #19 | `slice/f12b-human-decision` |
| F13 Guarded fake-service apply | #149 | A current human-approved field change is planned, backed up, idempotently applied to a fake service through the guarded application/API surfaces, read back, verified, audited, and safely rolled back on injected failure. | #17, #81-#83, #86, #190 | `slice/f13-guarded-fake-apply` |
| F14 Audiobookshelf canary apply | #150 | The same gates perform one bounded field or cover mutation against a disposable qualified Audiobookshelf instance and prove before/request/after evidence. | #84, #85, #87 | `slice/f14-audiobookshelf-canary` |
| F15a Report-only container runtime | #151 | The supported single container proves its reproducible image, explicit principals, filesystem/mount isolation, supervised lifecycle, first-run health and durable report-only restart with writes disabled. | #88-#91 | `slice/f15a-container-runtime` |
| F15b Diagnostics and recovery | #191 | The F15a container exposes authenticated report-only operational status, bounded correlated diagnostics, a previewable redacted support bundle, and documented backup/restore support without write authority. | #92, #93, #98 | `slice/f15b-diagnostics-recovery` |
| F16a Browser review case round trip | #152 | In a real browser, an authenticated owner finds one target, inspects decision-first detail and progressive evidence, navigates siblings, makes one individual decision, refreshes/restarts, and observes server-authoritative persistence. | #18, #99, #100; focused prerequisite #192 | `slice/f16a-review-case-browser` |
| F16b Browser summaries, bulk, and qualification | #195 | The browser presents server-authoritative summaries/history, commits one exact-version bulk decision, reverses one unapplied decision, and qualifies every applicable category across the supported browser matrix. | #20, #101-#103, #193, #194 | `slice/f16b-review-bulk-qualification` |
| F17 Continuous-operation qualification | #153 | The same canonical target/evidence/candidate scenario passes Audiobookshelf and CWA-shaped target fakes, each with provider-disabled and configured-provider variants, without schema changes; provider-disabled runs construct no provider adapter/client, resolve no provider secret, and make no provider network call, while the CWA-shaped fake traps database, UI, maintenance-container, media-write, unsupported-API, and production-mutation fallbacks. Events, reconciliation, review, guarded apply, circuit breakers, immutable workflow upgrades, dependency fan-out and fan-in, duplicate-safe resume, cancellation, worker and scheduler death, candidate-lane reuse and retry, physical JSONL framing, report-only authority isolation, browser-category coverage, advisory progress, restart, upgrade, backup, restore, release provenance, and support diagnostics pass the release cohort. | #94, #95 and a slice-specific release cohort | `slice/f17-release-qualification` |

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
embedded-file updates, plugin loading, and the
[post-1.0 reviewer backends](future-reviewer-backends.md) remain unassigned
backlog work until their prerequisite design and qualification decisions are
accepted. When scheduled, they receive new functional slice IDs and executable
outcomes; they are not appended opportunistically to an active branch. The
reviewer-backend plan does not alter F11, the F01-F17 order, or the 1.0.0 exit.

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
