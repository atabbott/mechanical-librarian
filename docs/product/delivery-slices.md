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

The currently numbered delivery milestones are internal pre-release checkpoints,
not automatic release-publication commitments. Their exit criteria remain
authoritative, but completing a checkpoint does not itself create a tag, GitHub
Release, supported version, or published release artifact. The checkpoint and
version for the first published release remain undecided and require a separate
maintainer selection plus all applicable release gates and release-publication
gates.

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

## Delivery checkpoint 0.1.0: trustworthy report-only product spine

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

### 0.1.0 checkpoint exit

- the focused foundation and trusted-change-loop prerequisites pass from a
  clean checkout;
- an operator can inspect isolated Audiobookshelf inventory and local evidence
  without shell-level database access;
- duplicate work, process restart, malformed media, authentication failure, and
  unsupported capability behavior are bounded and visible; and
- no development or CI path points at a contributor's production library.

Once the F01-F06 functional slices are complete, dependency-ready 0.2.0 work may
proceed without waiting for a `0.1.0` tag, GitHub Release, or published release
artifact. That progression does not declare the wider 0.1.0 checkpoint exit
complete; every exit criterion above remains authoritative.

## Delivery checkpoint 0.2.0: title-curation technical preview

This checkpoint proves one deliberately narrow, report-only path from local and
provider title evidence to one persistent undecided review case. It grants no
human or AI decision, approval, production write, or destructive authority.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Expected branch |
| --- | --- | --- | --- | --- |
| F09a Provider-neutral title proposal | #145 | The current Audiobookshelf title, exactly one eligible title from a current successful unambiguous applicable F06 `TITLE` observation, and one deterministic fake candidate supplied through an application-owned provider-neutral port produce one immutable versioned title proposal set containing at most one `needs_review` proposal. The F06 `normalized_value` must be non-empty, bounded, and usable rather than an unsafe, oversized, or redaction marker; selector-less titles remain valid. Agreement creates a proposal only when the current title field is present and loaded with an empty value or the current title is materially different. Its value is the exact F06 `normalized_value`; F06 `raw_value` remains evidence/provenance and provider evidence only corroborates. A missing, unloaded, or unavailable current title, current equivalence, disagreement, an empty or unusable F06/provider title, or another failed, unsupported, disabled, no-match, stale, duplicate, or unjoinable input produces an exact typed no-proposal result. | #222 | `slice/f09a-title-proposal` |
| F09b Bounded Hardcover title evidence | #204 | A disabled-by-default bounded Hardcover adapter replaces the fake behind the same provider-neutral port. It uses one fixed read-only query at one fixed HTTPS endpoint through a dedicated bounded client, keeps its account-capable token isolated and redacted, and retains only bounded canonical title evidence plus minimal provenance. It never caches raw provider traffic or turns provider failure or disagreement into negative local evidence. | #223 | `slice/f09b-hardcover-title` |
| F10 Persistent title review case | #146 | One current immutable proposal set containing exactly one current `needs_review` title proposal becomes one durable undecided review case; no other proposal or set creates a case. Persistence, restart, identical replay, inspection, immutable history, and changed-input supersession are demonstrable; zero-proposal results create no empty case. | #224 | `slice/f10-title-review-case` |

The only active delivery chain is:

```text
completed F06 #142 + planning #221 -> F09a #145/#222
                                      -> F09b #204/#223
                                      -> F10 #146/#224
```

F09a derives a conservative deterministic comparison key in this exact order:
supported-runtime NFC normalization, Unicode default case folding, final NFC
normalization, then trimming leading/trailing Unicode whitespace and collapsing
each internal Unicode-whitespace run to U+0020. The comparison-policy version
and runtime Unicode-data version are bound. This is comparison-only: it does
not alter proposal or evidence values, strip punctuation, diacritics, subtitle
or series text, words, or token order. Proposal sets include typed zero-proposal
results, bind the current inputs and contract versions, resolve identical replay
without duplication, and are superseded rather than rewritten when inputs
change. The implementation need not introduce speculative general-purpose slot
identity or migration machinery beyond this outcome.

| Current/F06/provider condition | Proposal-set result | F10 case |
| --- | --- | --- |
| F06 and provider usable titles normalize equally; current title field is present and loaded with an empty value, or the current title is materially different | One `needs_review` proposal whose value is the exact F06 `normalized_value`; F06 `raw_value` remains evidence/provenance | One current undecided case |
| F06 and provider normalize equally; current title is equivalent | Typed no-op with no proposal | No case |
| Current title is missing, unloaded, or unavailable | Exact typed current-title no-proposal result | No case |
| F06 and provider disagree | Typed conflict with no proposal | No case |
| F06/provider title is empty, unsafe, oversized, redacted, or otherwise unusable; or another required input is missing, failed, unsupported, disabled, no-match, stale, duplicate, or unjoinable | Exact typed unusable-title or other no-proposal result | No case |
| Identical replay | Same semantic set and, where applicable, case | No duplicate |
| A bound input changes | New superseding set; prior history remains immutable | Only a qualifying new current set may have a current case |

F09b's disabled state constructs no adapter or client, resolves no provider
secret, and performs no DNS lookup or egress. When enabled, the adapter owns its
fixed HTTPS origin, path, method, and read-only query; validates TLS; follows no
redirects; rejects private or reserved DNS destinations; uses a dedicated
client; and applies finite request, response, parser, deadline, retry, and
single-request concurrency bounds. The closed response shape is untrusted.
Only the minimum allowlisted facts needed for a title lookup leave the
application. The account-capable token is never logged or shared with the
target service or a reviewer. Raw requests, responses, headers, tokens, request
bodies, upstream identifiers, and provider caches are never persisted.

Ordinary CI remains credential-free and fake-based. F09b completion also
requires one opt-in bounded read-only smoke/contract qualification against the
fixed real endpoint using a dedicated non-production account and token. It
retains only a redacted aggregate result.

### 0.2.0 checkpoint exit

- F09a demonstrates the complete decision table with the deterministic fake;
- F09b demonstrates the same provider-neutral contract, disabled behavior,
  bounded transport, isolated credential handling, and opt-in qualification;
- F10 persists, restarts, inspects, replays, and supersedes one current
  case from one current immutable proposal set containing exactly one current
  `needs_review` title proposal, without creating any other, empty, or duplicate
  case; and
- the preview contains no AI, human decision, approval, cover, rich-field,
  multi-provider-fusion, production-write, or destructive behavior.

F07a #143 (#30, #31, and #188), F07b #228 (#32), and F08 #144 (#33 and
#34) remain intact as planned 1.0.0 work and are not prerequisites for this
0.2.0 chain. F11 #147 and #74-#80, the broader #16, #46, #50, and #68-#73
program, and rich Hardcover work #209 also remain 1.0.0 scope.

## Delivery checkpoint 1.0.0: guarded mutation and product operation

Write-capable slices use fake or disposable services until their explicit
canary slice. Destructive operations remain outside ordinary review and apply.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Expected branch |
| --- | --- | --- | --- | --- |
| F07a EPUB opening-matter evidence report | #143 | One safely readable EPUB produces a bounded transient opening-matter claim report with exact provenance or a typed no-applicable/rejected outcome. | #30, #31, #188; #25 and #26 are non-closing inputs | `slice/f07a-epub-opening-evidence` |
| F07b Durable EPUB evidence round trip | #228 | Completed F07a evidence executes durably for one isolated target and snapshot, survives restart, reuses compatible results, supersedes stale applicability, and remains inspectable without persistence of decoded content. | #32; #25 is a non-closing input | `slice/f07b-durable-epub-evidence` |
| F08 Audiobook health report | #144 | One generated audiobook produces bounded tag, chapter, duration, and media-health observations without transcription or production mutation. | #33, #34 | `slice/f08-audiobook-health` |
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

### 1.0.0 checkpoint exit

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

Satisfying this checkpoint exit does not itself publish a release. Its release
cohort, provenance, upgrade, restore, isolation, and end-to-end qualification
requirements remain mandatory checkpoint evidence regardless of whether or
when a maintainer separately selects a checkpoint for release publication.

## Deferred slice series

Opening-audio transcription (#24 and #104-#110), additional adapters,
embedded-file updates, plugin loading, and the
[reviewer backends after the 1.0 checkpoint](future-reviewer-backends.md) remain unassigned
backlog work until their prerequisite design and qualification decisions are
accepted. When scheduled, they receive new functional slice IDs and executable
outcomes; they are not appended opportunistically to an active branch. The
reviewer-backend plan does not alter the documented active chain, later 1.0
slice placement, or the 1.0.0 exit.

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
