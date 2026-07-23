# Delivery Slices

## Purpose

Mechanical Librarian is delivered through small end-to-end outcomes rather than
through completed horizontal work packages. WP0-WP9 remain capability ownership
buckets and issue parents. A functional slice crosses whichever packages are
needed to produce one executable, reviewable result.

The sequence below is the default implementation order. Each row becomes one
slice tracker and one bounded delivery transaction. The transaction may use one
`slice/...` branch and pull request or sequential independently safe vertical
increment branches and pull requests. The tracker contains the final ticket
list and may narrow the outcome before work starts. Divide the outcome into
suffixed slices such as `F05a` and `F05b` only when they are genuinely distinct
supported outcomes. Split a transaction into additional pull requests only for
distinct vertical increments or a required security or privacy separation,
never into horizontal layers or merely because review found more work.

Each table's increment-branch column gives the conventional branch stem for a
one-increment slice. A multi-increment transaction appends a short increment
slug to that stem; it does not reuse one branch for the whole transaction.

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
- evidence-bound correction #133 before F06;
- lifecycle and authorization correction #128 before F10a;
- frontend architecture decision #192 before F16a.

## Delivery checkpoint 0.1.0: trustworthy report-only product spine

Production metadata and cover writes remain disabled throughout these slices.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Increment branch stem/example |
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

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Increment branch stem/example |
| --- | --- | --- | --- | --- |
| F09a Provider-neutral title proposal | #145 | Historical retired surface: `synthetic-title-proposal-report` formerly ran closed generated scenarios through the real F06 EPUB child, a synthetic snapshot, and a deterministic provider-neutral fake. The installed CLI now rejects the command name. | #222 | `slice/f09a-title-proposal` |

F09i retains the exact pre-release metadata-report-v1 to v2 widening solely to
preserve immutable report/current-pointer replay; it does not establish a
general migration surface.
| F09b Bounded Hardcover title evidence | #204 | Historical retired surface: `hardcover-title-proposal-report` formerly ran only the fixed generated `agree-empty` case. The installed CLI now rejects the command name; F09f owns current configured Hardcover identity operation. | #223 | `slice/f09b-hardcover-title` |
| F10a Synthetic title review case | #146 | Historical retired surface: `synthetic-title-review-case-report` formerly admitted one deterministic synthetic case from a qualifying F09a lineage. The installed CLI now rejects the command name. | #224 | `slice/f10a-title-review-case` |

The only active delivery chain is:

```text
completed F06 #142 + planning #221 -> F09a #145/#222
                                      -> F09b #204/#223
                                      -> F10a #146/#224
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

F09a is intentionally a technical-preview harness, not a live inventory join.
Its command accepts only a durable database path, one closed scenario name, and
`json` or `text` output. The scenario manifest binds generator, snapshot, file,
F06, fake-provider, Unicode comparison, policy, fixture, proposal, lineage, and
mode versions. It does not accept configuration, live identifiers, titles,
paths, media, provider payloads, endpoints, or credentials. Connecting a real
F06 report to a current Audiobookshelf snapshot is deferred until a later slice
defines the live join, freshness, file-identity, and recovery contract. F09b or
F10a may consume F09a state only by preserving the same `synthetic_preview`
identity and false production applicability; neither may reinterpret it as
production evidence.

| Current/F06/provider condition | Proposal-set result | F10a case |
| --- | --- | --- |
| F06 and provider usable titles normalize equally; current title field is present and loaded with an empty value, or the current title is materially different | One `needs_review` proposal whose value is the exact F06 `normalized_value`; F06 `raw_value` remains evidence/provenance | One current undecided case |
| F06 and provider normalize equally; current title is equivalent | Typed no-op with no proposal | No case |
| Current title is missing, unloaded, or unavailable | Exact typed current-title no-proposal result | No case |
| F06 and provider disagree | Typed conflict with no proposal | No case |
| F06/provider title is empty, unsafe, oversized, redacted, or otherwise unusable; or another required input is missing, failed, unsupported, disabled, no-match, duplicate, or unjoinable | Exact typed unusable-title or other no-proposal result | No case |
| The current proposal set is stale | Exact typed `stale_input` proposal result | Rejected without an F10a transition or current-state mutation |
| Identical replay | Same semantic set and, where applicable, case | No duplicate |
| A bound input changes | New superseding set; prior history remains immutable | Only a qualifying new current set may have a current case |

F09b's omitted-secret state constructs no adapter or client, resolves no
provider secret, and performs no DNS lookup or egress. It records
`provider_disabled`; valid-but-unavailable credentials and provider failures
record `provider_failed` without discarding F06 evidence. When enabled, the adapter owns its
fixed HTTPS origin, path, method, and read-only query; validates TLS; follows no
redirects; rejects private or reserved DNS destinations; uses a dedicated
client; and applies finite request, response, parser, deadline, retry, and
single-request concurrency bounds. The command fixes the generated
`agree-empty` input and accepts no configuration, title, scenario, endpoint, or
query. The closed response shape is untrusted.
Only the minimum allowlisted facts needed for a title lookup leave the
application. The account-capable token is never logged or shared with the
target service or a reviewer. Raw requests, responses, headers, tokens, request
bodies, upstream identifiers, and provider caches are never persisted.

Ordinary CI remains credential-free and fake-based through the same command and
adapter seam using the sanitized live key/type/cardinality shape. F09b
historically completed one exact-installed-artifact bounded real-endpoint
qualification with a dedicated non-production token and disposable state. Its
credentialed command is retired, the request must not be repeated, and only the
redacted aggregate remains. This narrow compatibility does not add F09f production
configuration, persisted provider work, freshness/reuse, restart behavior, or
ISBN-backed book/edition identity, nor F09g's live target/source join or
proposal-applicability decision.

### 0.2.0 checkpoint exit

- F09a demonstrates the complete decision table with the deterministic fake;
- F09b demonstrates the fixed provider-neutral PoC, disabled behavior, bounded
  transport, isolated credential handling, structural fake qualification, and
  exact-installed-artifact live response compatibility;
- F10a persists, restarts, inspects, and replays one current case, supersedes it
  for a qualifying current successor, and retires it for a nonqualifying current
  successor without creating another, empty, revived, or duplicate case; and
- the preview contains no AI, human decision, approval, cover, rich-field,
  multi-provider-fusion, production-write, or destructive behavior.

F07a #143 (#30, #31, and #188) and F07b #228 (#32) are not prerequisites for
this 0.2.0 chain; they begin the later 0.3.0 checkpoint. F08 #144 (#33 and
#34), F11 #147 and #74-#80, the broader #16, #46, #50, and #68-#73 program,
and rich Hardcover work #209 remain 1.0.0 scope.

## Delivery checkpoint 0.3.0: first trustworthy live title report

This internal checkpoint connects current Audiobookshelf state, durable local
EPUB evidence, and one configured Hardcover observation to report one
trustworthy live title proposal or one of four stable zero-proposal results. It
does not publish a release or add review admission, a human or AI decision,
apply, target or media writes, destructive authority, or a tag.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Increment branch stem/example |
| --- | --- | --- | --- | --- |
| F07a EPUB opening-matter evidence report | #143 | One safely readable EPUB produces a bounded transient opening-matter claim report with exact provenance or a typed no-applicable/rejected outcome. | #30, #31, #188; #25 and #26 are non-closing inputs | `slice/f07a-epub-opening-evidence` |
| F07b Durable EPUB evidence round trip | #228 | Completed F07a evidence executes durably for one isolated target and snapshot, survives restart, reuses compatible results, supersedes stale applicability, and remains inspectable without persistence of decoded content. | #32; #25 is a non-closing input | `slice/f07b-durable-epub-evidence` |
| F09f Supported Hardcover title and identity provider | #239 | Disabled-by-default configured Hardcover work persists one bounded current title plus strict ISBN-backed book and compatible ebook-edition identity as one provider lineage, with restart, reuse, freshness, invalidation, and live qualification. It remains target-independent and owns no proposal authority. | #242; completed F09b #204 | `slice/f09f-hardcover-title-identity` |
| F09g Live Audiobookshelf title proposal | #238 | One exact current Audiobookshelf ebook snapshot, selected durable local title and edition-applicable ISBN evidence, and one current compatible Hardcover observation produce either one immutable `needs_review`, `production_report_only` proposal whose value is the exact current F07 normalized title or one of `source_not_ready`, `provider_not_ready`, `evidence_disagreement`, and `no_change`, without review admission or write authority. | #243; F07b #228 and F09f #239 | `slice/f09g-live-title-proposal` |

F07a freezes `epub-opening-report-v1`, `epub-opening-limits-v1`, and the
selection/parser/projection/normalization/vocabulary compatibility identities.
It composes the unchanged F06 package gate into a distinct transient opening
adapter, selects EPUB 3 landmarks then EPUB 2 guide semantics then linear-spine
fallback, and returns cited claims, legitimate no-applicable evidence, or a
typed rejection. It introduces no persistence, durable job, provider, reviewer,
target/media write, or decoded-markup retention. Its shared fixture registry,
offline materializer, exact-installed-wheel cohort, external private aggregate
qualification, configuration/schema, security/ADR, compatibility, and operator
evidence close together through #143 only.

The checkpoint graph is:

```text
completed F06 #142 -> F07a #143/#30/#31/#188 -> F07b #228/#32
completed F09b #204/#223 -> F09f #239/#242
F07b + F09f -> F09g #238/#243 -> 0.3.0 checkpoint exit
```

F09f is the current target-independent configured provider lane. Its completed
preactivation increments installed strict disabled-by-default configuration,
private-query parsing, the fixed adapter, schema-11 work, and its durable
lifecycle. Activation composes provider preparation before claiming and
durably starting the attempt at the final transport boundary.
The slice owns the bounded
Hardcover title and strict ISBN-backed book/compatible ebook-edition
observation, including its durable provider work, restart, reuse, freshness,
invalidation, disabled and failure behavior, and live compatibility
qualification. Disabled mode retains zero authority. The book, edition, and title are one Hardcover provider lineage;
they are not independent provider votes. Its exact external contracts and
qualification are frozen in the Ready slice train. It does not inspect or join
a live target and cannot decide proposal eligibility.

F09g owns the live target/source join. It produces exactly one `needs_review`,
`production_report_only` proposal only when the current inputs supply one
usable F07 title, exactly one distinct canonical checksum-valid
edition-applicable local ISBN, one unique ISBN-equivalent compatible Hardcover
ebook edition belonging to one book with an agreeing title, and a loaded
Audiobookshelf title that is empty or materially different. Repeated equivalent
claims of that same canonical local ISBN do not force a zero-proposal result;
conflicting, ambiguously applicable, or multiple distinct ISBNs do. The sole
positive proposal value is the exact current F07 normalized title. Missing,
invalid, conflicting, ambiguous, or stale local inputs produce
`source_not_ready`; disabled, failed, missing, ambiguous, incompatible, or stale
provider state produces `provider_not_ready`; disagreement produces
`evidence_disagreement`; and equivalence produces `no_change`. Usable local
evidence is preserved. The independence claim is the local EPUB evidence versus
the external Hardcover catalogue lineage.

### 0.3.0 checkpoint exit

- F07a produces one bounded transient opening-matter title and
  edition-applicable ISBN evidence report from a safely readable EPUB;
- F07b binds accepted F07a evidence durably to one current isolated target and
  snapshot, with restart, compatible reuse, invalidation, and inspection;
- F09f produces one bounded, current, qualified Hardcover title and strict
  ISBN-backed book/ebook-edition observation or its typed disabled, failed,
  empty, unmatched, ambiguous, or incompatible outcome;
- F09g atomically revalidates the live target and both evidence lineages, then
  persists and reports the one eligible `needs_review`,
  `production_report_only` proposal whose value is the exact current F07
  normalized title, or one of the four stable zero-proposal results, with
  replay, supersession, and evidence preservation; and
- the checkpoint contains no review admission, human or AI decision, approval,
  apply, target or media write, destructive authority, tag, GitHub Release, or
  release publication.

## Delivery checkpoint 0.4.0: clean-state neutral evidence report

0.4.0 simplifies the qualified #238 path into one clean-state, synchronous,
report-only metadata-evidence command for an explicitly selected EPUB ebook.
The qualified #238 target/media reading, local title/ISBN extraction, provider
observation, currentness, privacy, and persistence behavior are
characterization evidence. #257 preserves that final-0.3 behavior while moving
the EPUB boundary to one isolated inspector; #197 alone replaces #238's final
proposal gate with the neutral evidence-report contract.

| Work | Issue | Role and dependency |
| --- | --- | --- |
| Baseline | #253 | Freeze this contract after qualified #238. |
| Fixture correction | #255 | Remove stale fixture ownership after #253. |
| EPUB inspector | #257 | Preserve final-0.3 evidence behavior until #197 activation. |
| Linked-work gate | #261 | Simplify the independent GitHub policy gate after #253. |
| Activation | #197 | Sole runtime/schema reset after all four contributors. |

The installed activation command is exactly
`metadata-report --config PATH --database PATH --connection NAME --library NAME --item ITEM_ID --file-index INDEX`.
Both path inputs are required; no defaults, CWD discovery, implicit filenames,
or undocumented environment fallbacks exist. Success emits exactly the
persisted canonical JSON report plus a newline; failure emits no stdout, one
bounded redacted stderr diagnostic, and a nonzero exit.

Each report has a small relational identity, lookup, digest, and schema-version
envelope plus one immutable canonical JSON document. The envelope and document
identity agree, and the closed typed document is validated on write and read.
Exact re-observation replays the same whole report identity and bytes; changed
evidence creates a successor without changing the prior document.

Only a new database is a supported 0.4 starting state. Pre-0.4 development
databases have no recognition, migration, import, preservation, or tailored
compatibility path. No partial 0.4 runtime is exposed before #197. The existing
final-0.3 report stays report-only during the contributor work.

### 0.4.0 checkpoint exit

- #253, #255, #257, and #261 are complete; #197 alone activates the complete
  outcome and reconciles historical #254, #256, #258, and #260;
- an installed artifact demonstrates the exact one-command, clean-state
  contract, agreeing relational envelope and canonical document, whole-report
  replay, privacy/currentness/hostile-input safeguards, and report-only/no-write
  boundary; and
- no review, decision, approval, apply, target/media write, background worker,
  retry, provider cache, compatibility machinery, tag, release, or release
  artifact is introduced.

Completing this internal checkpoint does not create a release train, supported
version, tag, GitHub Release, or published artifact.

## Delivery checkpoint 1.0.0: guarded mutation and product operation

Write-capable slices use fake or disposable services until their explicit
canary slice. Destructive operations remain outside ordinary review and apply.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Increment branch stem/example |
| --- | --- | --- | --- | --- |
| F08 Audiobook health report | #144 | One generated audiobook produces bounded tag, chapter, duration, and media-health observations without transcription or production mutation. | #33, #34 | `slice/f08-audiobook-health` |
| F09c Work identity fallback | #205 | Bounded Open Library work/edition discovery and optional LibraryThing ISBN-family clustering preserve ambiguity, lineage, independent-provider limits, and disabled/failure behavior. | #210, #211; unresolved prerequisite #68 | `slice/f09c-work-identity-fallback` |
| F10b Production title review case | #240 | Exactly one current qualifying neutral persisted report claim becomes one durable undecided production review case while preserving exact freshness, evidence, version, replay, and supersession proof. | #241; F10a #146 and #197 | `slice/f10b-production-title-case` |
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

The remaining productionisation dependencies are deliberately not one serial
chain. F09c remains separately blocked on its declared prerequisite #68 and is
not a prerequisite for the 0.3 F09g report. Its fallback identity remains a
separate report/provider lane; later provider-neutral candidate work #69/#70
consumes compatible F09c identity, with qualification in #73 and F17 #153.
F10b starts after completed F10a and #197 and is the first production review-case
admission from the current neutral persisted report/claim boundary. F11 remains
independently deliverable against its synthetic shadow input. The production
title and synthetic reviewer paths converge only in F17 qualification.

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

- A named `Deliver slice <ID>` instruction selects that unblocked Ready tracker.
  Without a named ID, the highest-priority unblocked slice determines execution
  order across work packages.
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
