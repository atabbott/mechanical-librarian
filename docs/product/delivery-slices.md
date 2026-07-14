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
| F06 EPUB package evidence report | #142 | Ordinary, noncanonical, and partially defective EPUBs are parsed within hostile-input bounds through the supported CLI; every trustworthy package-metadata fact is cited and bounded, optional defects warn and are omitted without silent resolution, and an external private-corpus shadow qualification records aggregate compatibility without retaining copyrighted content or private reports. | #26-#29; #13 and #21 are non-closing inputs | `slice/f06-epub-package-evidence` |
| F07 EPUB opening-matter evidence report | #143 | The same isolated workflow selects bounded opening matter, extracts edition-aware claims with exact provenance, persists them, and explains unsupported or rejected input. | #25, #26, #30-#32, #188 | `slice/f07-epub-opening-evidence` |
| F08 Audiobook health report | #144 | One generated audiobook produces bounded tag, chapter, duration, and media-health observations without transcription or production mutation. | #33, #34 | `slice/f08-audiobook-health` |

### 0.1.0 release exit

- the focused foundation and trusted-change-loop prerequisites pass from a
  clean checkout;
- an operator can inspect isolated Audiobookshelf inventory and local evidence
  without shell-level database access;
- duplicate work, process restart, malformed media, authentication failure, and
  unsupported capability behavior are bounded and visible; and
- no development or CI path points at a contributor's production library.

## Release 0.2.0: title-curation technical preview

This release proves one deliberately narrow, report-only path from local and
provider title evidence to one persistent review case. It grants no production
write or review-decision authority.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Expected branch |
| --- | --- | --- | --- | --- |
| F09a Provider-neutral title proposal | #145 | Canonical current Audiobookshelf title, exactly one eligible successful F06 package-title observation, and one fake provider title candidate produce one immutable, versioned proposal set containing at most one provider-neutral title proposal. Every typed zero-proposal outcome is also a proposal set. Conservative normalization requires local/provider agreement: canonical current-title `EMPTY` produces a fill proposal; `PRESENT` produces a replacement only when materially different and no proposal when equivalent; `MISSING`, `NULL`, `MALFORMED`, and `UNSUPPORTED` remain distinct zero-proposal states. Ambiguous, non-unique, role-ineligible, missing, failed, stale, duplicate, or unjoinable evidence produces a typed zero-proposal set without hiding unrelated inventory or local evidence. | #222 | `slice/f09a-title-proposal` |
| F09b Bounded Hardcover title evidence | #204 | A disabled-by-default Hardcover adapter replaces the F09a fake behind the existing provider port. It submits one fixed read-only POST query to `https://api.hardcover.app/v1/graphql` through a dedicated bounded client, preserves the same provider-neutral comparison grouping, and retains only a bounded canonical title candidate and opaque provenance. Provider disablement or failure creates a typed zero-proposal set while local evidence remains visible. The API revision remains `experimental` while qualification is fake-transport-only. | #223 | `slice/f09b-hardcover-title` |
| F10 Persistent title review case | #146 | Only the current proposal set containing exactly one current `needs_review` title proposal becomes one durable review case. Persistence, restart, idempotent replay, immutable history, and stale-input supersession are demonstrable; zero-proposal sets create no empty case, and no human or AI decision or write authority is introduced. | #224 | `slice/f10-title-review-case` |

The explicit entry chain is `#221 + completed F06 #142 -> F09a -> F09b -> F10`.
F07a, F07b, and F08 remain planned under their existing 0.1 trackers, but their
execution is deferred. After this planning change, F09a is the next selected
functional slice.

For 0.2, `title-normalization-v1` creates a comparison key using the Unicode
16.0.0 definition of NFC and a frozen, byte-exact Unicode 16.0.0 data bundle:
`UnicodeData.txt`, `DerivedNormalizationProps.txt`,
`CompositionExclusions.txt`, `PropList.txt`, and `CaseFolding.txt`. The bundle
records a SHA-256 digest for each source file and is verified against the full
Unicode 16.0.0 `NormalizationTest.txt` conformance data. It first applies NFC
under Unicode 16.0.0, maps each maximal
run of code points whose Unicode 16.0.0 `PropList.txt` property is
`White_Space=Yes` to U+0020, removes leading and trailing U+0020, applies the
Unicode 16.0.0 Default Case Folding mappings with status C or F from
`CaseFolding.txt`, and applies NFC under Unicode 16.0.0 again. Resulting keys
are compared byte-for-byte. Implementations use this complete frozen baseline
rather than host-runtime Unicode data.

The normalization does not strip or replace punctuation, diacritics, subtitle
separators or content, series/order text, or words, and it does not reorder
tokens. Equal keys are equivalent; different keys are materially different.
The provider only corroborates: when F06 and provider keys agree, an eligible
proposal always retains the bounded original F06 package title, never the
provider spelling or comparison key.

F09a accepts an F06 package title only from a successful, current report with
no `ambiguous_title` or `ambiguous_metadata_refinement` warning and exactly one
applicable `TITLE` observation whose citation selector is exactly
`title-type-main@<source-ordinal>` or
`title-type-main@<source-ordinal>;display-seq@<source-ordinal>`, using base-10
F06 source ordinals. The optional display component records provenance and does
not select the title.
Either disqualifying warning maps to `f06_title_ambiguous` before the remaining
cardinality, role, projection, and value-bound partitions are evaluated.
The exact retained value must be at most 4,096 characters and must not use the
reserved `unsafe-source-text-sha256:` or `oversized-source-text-sha256:`
projection prefixes. Unrefined titles are role-unproven because F06 cannot
distinguish a genuinely absent refinement from a discarded invalid one;
`short` and `expanded` are role-ineligible. Multiple observations are never
selected by document order, display sequence, or provider agreement. The F06
slot instead records the exact typed `f06_title_missing`,
`f06_title_ambiguous`, `f06_title_role_unproven`,
`f06_title_ineligible_role`, `f06_title_redacted`, or
`f06_title_out_of_bounds` zero-result outcome, as applicable.

Each evaluated input combination has one immutable proposal-set version bound
to the exact target, snapshot, `proposal-input-slot-v1` identity for the current
title, F06 title, and provider title, `title-normalization-v1`, and policy
versions. Each slot identity is a tagged success value/evidence identity or the
exact typed missing, null, malformed, unsupported, disabled, failed, empty, or
no-match outcome with its scope and contract version. Zero-result paths never
invent a value or provider candidate. An idempotent replay resolves to the same
semantic proposal-set identity. A changed state, value, evidence identity, or
stale input creates a superseding applicability result without mutating prior
set or case history.

| Current/F06/provider condition | Proposal-set result | F10 case | Retained visibility |
| --- | --- | --- | --- |
| Current title is canonical `EMPTY`; current F06 and provider keys agree | Exactly one current `needs_review` fill-empty proposal retaining the bounded original F06 package title | Exactly one current case | Inventory, F06 evidence, bounded provider evidence, proposal set, and case |
| Current title is canonical `PRESENT` and materially different; current F06 and provider keys agree | Exactly one current `needs_review` replacement proposal retaining the bounded original F06 package title | Exactly one current case | Inventory, F06 evidence, bounded provider evidence, proposal set, and case |
| Current title is canonical `PRESENT` and equivalent; current F06 and provider keys agree | Typed current-equivalent zero-proposal set | No case | Inventory, F06 evidence, bounded provider evidence, and proposal set |
| Current title is canonical `MISSING`, `NULL`, `MALFORMED`, or `UNSUPPORTED` | Distinct typed current-observation zero-proposal set; it is never treated as empty | No case | Inventory with exact current observation state, other bounded evidence, and proposal set |
| Current F06 and provider keys disagree | Typed conflict zero-proposal set | No case | Inventory, both source observations, conflict, and proposal set |
| F06 title missing, ambiguous, non-unique, role-unproven, role-ineligible, redacted, out of proposal bounds, failed, or unsupported | Exact typed F06 zero-proposal set | No case | Inventory, retained F06 report and exact title-selection outcome, any bounded provider evidence, and proposal set |
| Provider disabled, unsupported, incompatible, failed, empty, or no match | Distinct typed provider zero-proposal set | No case | Inventory, F06 evidence, exact provider outcome, and proposal set |
| Required input stale, duplicate, or unjoinable | Typed no-proposal set; prior set becomes non-current where applicable | No case | Immutable inputs, typed blocker, prior history, and proposal set |
| Identical replay of an accepted input combination | Same semantic proposal-set identity; no duplicate set or proposal | No new case; replay returns the same case identity when one exists | Existing immutable set, case, evidence, and replay outcome |

### 0.2.0 release exit

- the current Audiobookshelf title, exactly one eligible current F06 package
  title observation, and one provider title are version-bound to the same
  target and snapshot;
- F09a proves the title decision matrix with a deterministic fake provider, and
  F09b proves the same provider-neutral contract with a fake transport for the
  bounded Hardcover adapter and no live token; that Hardcover API revision is
  published as `experimental`, not `tested` or `supported`, until a separate
  opt-in release qualification succeeds against a dedicated synthetic account;
- `title-normalization-v1` and the decision table produce one immutable,
  version-bound proposal set containing at most one `needs_review` title
  proposal; current equivalence, disagreement, missing or failed input,
  unsupported capability, and stale, duplicate, or unjoinable evidence produce
  the documented typed zero-proposal or conflict set;
- provider disablement constructs no adapter or client, resolves no provider
  secret, performs no DNS lookup or egress, and provider failure never hides
  unrelated inventory or F06 evidence;
- only a current proposal set containing exactly one current `needs_review`
  proposal creates a durable case; restart, identical replay, immutable
  history, and supersession pass without duplicate sets, proposals, cases, or
  empty cases; and
- no automatic approval, human or AI decision, production write, cover,
  rich-field, or multi-provider workflow enters the technical preview.

## Release 1.0.0: guarded mutation and product operation

Write-capable slices use fake or disposable services until their explicit
canary slice. Destructive operations remain outside ordinary review and apply.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Expected branch |
| --- | --- | --- | --- | --- |
| F11 AI shadow recommendation | #147 | An isolated reviewer receives a complete bounded vendor-neutral packet carrying the exact normalized current snapshot and explicit field-load state only after required authoritative inputs join successfully, without target-service or provider credentials or vendor payloads, then returns one direct closed-schema `AIDecision` for every proposal; its required `problems` list contains adverse or unresolved problem codes, with `problems: []` for a clean approval. Malformed output, unknown fields, missing required fields, duplicate or partial decisions, trailing content, or semantic repair becomes a typed failure without authorizing apply. A zero-proposal selection terminates without invocation, and optional provider failure remains operational. | #74-#80 | `slice/f11-ai-shadow-review` |
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

- AI completion is independently audited rather than inferred from process or
  artifact success; zero proposals cause no reviewer invocation, while every
  invoked packet has exact per-proposal output coverage;
- reviewer timeout, malformed output, unknown or missing fields, duplicate or
  partial decisions, trailing content, citation failure, and authentication
  recovery are typed safe states and create no apply authority;
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

The broader candidate, field, provider, and review program in #16, #46, #50,
#68-#73, and #209 remains 1.0.0 backlog. It requires separately ready slice
assignment and is not part of the 0.2.0 exit.

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
