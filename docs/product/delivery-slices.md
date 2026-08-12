# Delivery Slices

## Purpose

Mechanical Librarian is delivered through small end-to-end outcomes rather than
through completed horizontal work packages. WP0-WP9 remain historical
capability-ownership buckets, not current issue parents. A functional slice
crosses whichever capabilities are needed to produce one executable, reviewable
result.

The sequence below is the default implementation order. Each assigned row has
one slice tracker and one bounded delivery transaction; a row explicitly marked
as requiring promotion remains an outcome only until that promotion completes.
The transaction may use one `slice/...` branch and pull request or sequential
independently safe vertical increment branches and pull requests. The tracker
contains the final ticket list and may narrow the outcome before work starts.
Divide the outcome into suffixed slices such as `F05a` and `F05b` only when they
are genuinely distinct supported outcomes. Split a transaction into additional
pull requests only for distinct vertical increments or a required security or
privacy separation, never into horizontal layers or merely because review found
more work.

The tables preserve assigned and historical delivery records. A future outcome
outside them remains a concise deferred issue with no `F` ID; a stable
functional-slice designation is assigned only when that same issue is
explicitly promoted for delivery. Deferred outcomes do not acquire trackers,
constituents, milestones, or implementation design merely to reserve future
sequence.

Where a table includes an increment-branch column, it gives the conventional
branch stem for a one-increment slice. A multi-increment transaction appends a
short increment slug to that stem; it does not reuse one branch for the whole
transaction.

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

Publication-readiness #43 remains a separate first-release gate. Any frontend
architecture decision needed for F16a is owned by #152 during promotion rather
than by the closed #192 ticket. The previously listed correction, governance,
and prerequisite tickets are completed or superseded history, not current work.

## Delivery checkpoint 0.1.0: trustworthy report-only product spine

Production metadata and cover writes remain disabled throughout these slices.

| Slice | Tracker | Demonstrable outcome | Primary backlog inputs | Increment branch stem/example |
| --- | --- | --- | --- | --- |
| F01 Durable state check | #136 | A CLI diagnostic creates or opens exact-current application state, records and reads a safe audit event, and reports incompatible state without exposing a database path. | #51-#53 | `slice/f01-durable-state-check` |
| F02 Recoverable job heartbeat | #137 | A bounded synthetic dependency graph pins an immutable workflow-definition version and graph digest, schedules the complete eligible set with stable deduplication, survives worker, scheduler, and process restart under leases and fencing, persists nonterminal `waiting` separately from `retry_wait`, and advances fan-in only after independently validating expected outcomes. An incompatible graph creates linked superseding or replanned work whose transitively affected downstream checkpoints are ineligible for reuse or current applicability without mutating or erasing original history. | #54-#56, #58, #126 | `slice/f02-recoverable-job-heartbeat` |
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
| F09b Bounded Hardcover title evidence | #204 | Historical retired surface: `hardcover-title-proposal-report` formerly ran only the fixed generated `agree-empty` case. The installed CLI now rejects the command name; F09f owns current configured Hardcover identity operation. | #223 | `slice/f09b-hardcover-title` |
| F10a Synthetic title review case | #146 | Historical retired surface: `synthetic-title-review-case-report` formerly admitted one deterministic synthetic case from a qualifying F09a lineage. The installed CLI now rejects the command name. | #224 | `slice/f10a-title-review-case` |

The historical delivery chain was:

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
this 0.2.0 chain; they begin the later 0.3.0 checkpoint. F08 #144 and F11 #147
remain later 1.0.0 outcomes. Their former constituent tickets and the broader
closed program are historical planning inputs, not current delivery work; the
current outcome set is defined below.

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

F07a freezes the current `epub-opening-report-v2`,
`epub-opening-limits-v1`, and the
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

The 0.4 activation command was exactly
`metadata-report --config PATH --database PATH --connection NAME --library NAME --item ITEM_ID --file-index INDEX`.
At that boundary, both path inputs were required; no defaults, CWD discovery,
implicit filenames, or undocumented environment fallbacks existed. Success
emitted exactly the persisted canonical JSON report plus a newline; failure
emitted no stdout, one
bounded redacted stderr diagnostic, and a nonzero exit.

Each report had a small relational identity, lookup, digest, and schema-version
envelope plus one immutable canonical JSON document. The envelope and document
identity agreed, and the closed typed document was validated on write and read.
Exact re-observation replayed the same whole report identity and bytes; changed
evidence created a successor without changing the prior document.

An absent database or an existing exact two-table 0.4 schema was supported at
that boundary. Earlier or inexact development state had no recognition,
migration, import, preservation, or tailored compatibility path. No partial 0.4
runtime was exposed before #197. The existing final-0.3 report stayed
report-only during the contributor work.

### 0.4.0 checkpoint exit

- #253, #255, #257, and #261 are complete; #197 alone activates the complete
  outcome and reconciles historical #254, #256, #258, and #260;
- an installed artifact demonstrated the exact one-command, clean-state
  contract, agreeing relational envelope and canonical document, whole-report
  replay, privacy/currentness/hostile-input safeguards, and report-only/no-write
  boundary; and
- no review, decision, approval, apply, target/media write, background worker,
  retry, provider cache, compatibility machinery, tag, release, or release
  artifact was introduced.

Completing this internal checkpoint did not create a release train, supported
version, tag, GitHub Release, or published artifact.

## Delivery checkpoint 0.5.0: complete ebook metadata and cover evidence

This completed checkpoint expands the neutral report into one complete current
ebook metadata-and-cover evidence document.

| Slice | Tracker | Demonstrable outcome |
| --- | --- | --- |
| F09i Complete local ebook metadata evidence | #293 | Audiobookshelf and bounded structured EPUB evidence form one closed v2 field graph with deterministic source order, comparison links, exact replay, and complete opening/rear limits. |
| F09j Complete Hardcover ebook metadata evidence | #294 | One configured ISBN-filtered Hardcover observation contributes bounded source-attributed work and edition evidence while disabled, failed, empty, and malformed outcomes remain independent facts. |
| F09k Complete ebook cover evidence | #295 | Audiobookshelf, declared EPUB, and enabled Hardcover cover sources produce validated original and offline-preview artifact facts without selecting or writing a cover. |

The slices completed in that order. F09k is the checkpoint activation and
retains metadata output when cover acquisition is absent or degraded.
Throughout the checkpoint, ADR-0013 applies repository-wide: each schema
recognizes only absent or exact-current state; earlier state is rejected before
mutation without migration, import, preservation, or compatibility parsing.
Recovery uses a clean current state and supported regeneration; old state may
be retained offline. Exact-ID/current-scope replay is required only within the
exact current schema.

### 0.5.0 checkpoint exit

- one selected current Audiobookshelf EPUB produces a complete immutable report
  containing current target state, bounded local field evidence, configured
  Hardcover candidates, field-local comparisons, and validated cover source
  outcomes;
- valid JPEG, PNG, and static WebP cover inputs can produce private
  content-addressed originals and deterministic report-bound offline previews;
- exact re-observation replays the same report and artifact identities, while
  changed evidence creates an immutable successor; and
- the runtime gains no proposal selection, review, decision, apply,
  target/media write, background execution, retry, tag, or release authority.

## Delivery checkpoint 0.6.0: catalogue and library-scale ebook evidence

This completed checkpoint composes the final 0.5 operation with a bounded
current Audiobookshelf book catalogue and operator-controlled foreground
batches.

| Slice | Tracker | Activation | Demonstrable outcome |
| --- | --- | --- | --- |
| F05c Current Audiobookshelf book catalogue | #300 | #301 | One refresh atomically publishes at most 10,000 current scoped entries across configured book libraries, preserving independent ebook/audio facets and exact eligible, ineligible, and needs-attention classifications. |
| F18 Library-scale ebook evidence batch | #302 | #303 | An operator freezes explicit selections or all eligible EPUBs into one immutable secret-free manifest, inspects it, and serially runs the final 0.5 report operation in the foreground. |

#299 records the repository-wide pre-release clean-schema policy applied by
both slices. #309 corrected reachable catalogue edge cases before the milestone
closed. The proposed durable attempt/resume design #304 was explicitly closed
as not planned: recovery remains a fresh operator-created manifest and
foreground run.

### 0.6.0 checkpoint exit

- catalogue refresh covers every configured Audiobookshelf book library and
  publishes only a complete generation after bounded summary/detail
  validation;
- deterministic list and exact-get inspection expose the current stored
  projection without provider access or target/media writes;
- a content-addressed `batch-manifest-v1` binds catalogue generation, exact
  scoped entries, report-relevant configuration, enabled providers, and
  conservative call maxima without storing private values or execution state;
- batch run revalidates the generation and exact entry before each serial
  final-0.5 report, streams deterministic item outcomes and one summary, and
  preserves completed reports on a later stop or interruption; and
- no proposal, review, decision, apply, API/browser control plane, scheduler,
  queue, worker, resume, automatic retry, parallelism, write, tag, or release
  authority is introduced.

## Delivery checkpoint 0.7.0: qualified ebook metadata matching

This completed report-only checkpoint restored and qualified trustworthy ebook
metadata matching against the aggregate 50-case cohort. Its bounded sequence
was #319, #320, #321, #324, F09n #328, F07c #322, F06a #323, F09m #327, then
the focused planning reconciliation #325.

The programme fixed the supported scoring vocabulary, classified remaining
gaps by recoverability, preserved evidence across the optional rear-pass limit,
retained provider work identity, classified ISBN relationships, improved
contributor and series evidence precision, admitted signed-offset publication
dates, and kept explicit publisher and imprint roles distinct. It added no
provider, proposal, review, apply, write, hierarchy inference, generic scoring
engine, cache, retry, scheduler, or plugin framework.

### 0.7.0 checkpoint exit

- the installed-wheel aggregate improves matched rows from 258 to 266 and
  complete rows from 230 to 238 relative to the accepted F09n baseline;
- candidate-atom precision improves from 0.805233 to 0.819484 and
  explicit-negative false positives fall from 7 to 0;
- title, creator, language, identifier, and supported-population controls do
  not regress;
- every delivery unit #319, #320, #321, #322, #323, #324, #327, #328, and
  #325 is complete; and
- the runtime remains report-only and the checkpoint creates no tag, release,
  publication artifact, or production-write authority.

Numeric figures are qualification results rather than promises for unrelated
future work. Each promoted unit carried its own minimum measurable gate and
published its actual aggregate result.

## Delivery checkpoint 0.8.0: independent multi-provider ebook evidence

The [0.8.0 working plan](0-8-working-plan.md) adds Open Library as
a second real metadata adapter and proves that the report and
foreground-batch boundaries preserve independent Hardcover and Open Library
observations. Exact ISBN evidence, candidate identity, lineage, failure,
absence, malformed or incomplete data, and contradiction remain
provider-specific and canonical; Open Library reaches behavioral parity with
each existing Hardcover evidence capability for which it has semantically
equivalent data and records explicit absent or unsupported outcomes otherwise.
Beyond parity, it preserves a bounded allowlist of typed provider identities
and revision provenance, selected
identifiers present on required edition or work records, subjects and
classifications, edition characteristics, contributor identities, the exact
edition-to-work relationship, and cover identities. It does not traverse
author records, enumerate editions, or issue fallback searches solely for
enrichment.

The checkpoint persists no raw provider payload, generic property map,
arbitrary upstream link collection, community signals, lending or full-text
state, or unbounded edition graph. Rich evidence remains source-scoped and
performs no provider voting, fusion, subject-to-tag mapping, proposal selection,
review, or write.

Open Library participation is restricted to a documented low-volume envelope.
A larger or all-eligible batch with Open Library enabled fails closed before
provider construction or egress; the existing batch remains usable with that
provider disabled. A permitted explicit selection reuses the existing
foreground lifecycle with a fixed ceiling and single-attempt posture rather
than adding a cache, retry, scheduler, rate-policy language, or provider-cost
framework.

Open Library #210 is the completed F09l tracker in milestone 0.8.0. It is a
one-issue vertical slice: configuration, schema, adapter, covers, batch
participation, proof, and documentation have no separate constituents.
Focused issue #341 is non-slice qualification work: it binds the installed
artifact to exact clean source, wheel, and installed-payload evidence without
adding a constituent to F09l. The checkpoint also requires two ignored,
maintainer-local completion conditions: an exact-clean-snapshot rebaseline of
the unchanged 50-case regression gate, and a separately observable bounded
publisher/imprint role cohort. Those local controls create no GitHub issue,
branch, commit, or pull request.

### 0.8.0 checkpoint exit

- one exact usable ISBN family can yield independent canonical Hardcover and
  Open Library observations in one immutable `metadata-report-v6`;
- Open Library retains only the closed finite rich-evidence allowlist from exact
  admitted edition/work records and at most two cover sources;
- adapter-specific `config-v5` excludes the Open Library operational contact
  from report identity, persistence, manifests, fingerprints, and diagnostics;
- `batch-manifest-v2` binds provider adapters and truthful maxima, while enabled
  Open Library accepts only explicit batches of at most five items; and
- the public gate builds an explicit wheel from the exact clean event source
  with caches disabled, proves its installed payload and origin in a new
  environment, and runs its console command outside the checkout;
- the private 50-case regression thresholds equal one accepted clean-main
  snapshot with no headroom and row-level readable-rate reset justification;
- a bounded publisher/imprint cohort scores those roles distinctly and proves
  zero cross-role or inferred matches while the unchanged 50-case comparison
  remains separately visible; and
- the runtime remains report-only and creates no provider fusion, proposal,
  review, decision, mutation, generic provider framework, tag, or release.

## Delivery checkpoint 0.9.0: explainable ebook proposals

The [0.9 working plan](0-9-working-plan.md) promotes F09o #70 with #344 as its
sole pre-activation sub-issue. #344 completes `metadata-report-v8`: all 16 ABS
destinations, exact ISBN versus complete-author work lookup, fail-closed work
qualification, typed taxonomy/first-publication evidence, and the bounded
provenance-bearing provider cover gallery. Delivered #70 consumes that exact
current report to activate one deterministic immutable ProposalSet covering
all 16 destinations.

| Outcome | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| F09o evidence input | #344 | One selected ABS EPUB produces or reopens the complete immutable evidence report required by explainable proposals, without proposal or write authority. | Delivered pre-activation increment. |
| F09o explainable proposals | #70 | One exact completed evidence report deterministically produces one immutable result for every destination, without review or write authority. | Delivered activation increment. |

The checkpoint excludes new providers or discovery, fuzzy search, configurable
policy, AI, review/admission/decision/apply/write, batch/background/retry/cache,
UI/API, migration, compatibility, tag, release, and deployment.

### 0.9.0 checkpoint exit

- one exact current `metadata-report-v8` produces one immutable ProposalSet for
  all 16 Audiobookshelf destinations without further provider access; and
- no review admission, shadow advice, decision, apply, target/media write, or
  background authority is introduced.

## Delivery checkpoint 0.10.0: review admission and qualification-only shadow advice

This completed checkpoint owns the exact-current ReviewCase admission
and isolated qualification-only shadow-advice surfaces. It does not grant human
or AI decision, approval, apply, target/media write, or normal activation
authority.

| Outcome | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| F10b Production review admission | #240 | One qualifying exact-current ProposalSet becomes an immutable undecided ReviewCase, or records typed no-case retirement, without a decision or write. | Delivered. |
| F11 Isolated AI shadow recommendation | #147 | One exact current ReviewCase can be previewed, requested, inspected, or explicitly reevaluated through an isolated qualification-only path with no normal activation authority. | Terminal non-activation delivered; model-review qualification failed and no reviewer was activated. |
| F19 Deterministic shadow decision preview | #384 | One exact current ReviewCase produces a canonical provider-independent read-only decision preview with exact catalogue closure and safe manual abstention. | Delivered by #442; remains read-only, non-persisted, and provider independent. |

## Delivery checkpoint 0.11.0: authenticated human-exception review

F16a #152 is delivered through four ordered production increments and the
milestone-13 corrective sequence. The closed design record #446 and corrective
visual contract #455 lock variant M at `026e338` as the authoritative visual
contract for the in-scope Library, Attention, and Detail surfaces; prototype
code remains directional evidence and is not production code. Variant I
remains lineage only. F16a owns bounded current-case history; the prototype's
separate library activity view and all library-wide or cross-case history
remain deferred to #195.

| Increment | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| Exact-current case inspection | #447 | An authenticated owner opens one exact-current case, bounded evidence, and current-case history through the read-only API/browser boundary. | Delivered. |
| Attention-first library and field queue | #448 | The owner browses every current work and a separate queue containing only unresolved human-attention fields. | Delivered by #448. |
| Immutable exact-version decision | #449 | One exact field decision is persisted idempotently and reproduced after restart without automatic, apply, or write authority. | Delivered by #449. |
| Browser activation | #450, #455 | The complete decision-first round trip passes the installed-artifact gates and faithfully presents variant M in light/dark modes at 1440, 884, 390, and 320 CSS pixels. | Activated by #450; visual contract restored by #455. |

## Delivery checkpoint 0.12.0: quiet background metadata decisions

This checkpoint progressively composes continuous report-only review, versioned
policy, and deterministic qualification observations. Human Library and
Attention hierarchy remains authoritative throughout; it adds no metadata
apply or write capability.

| Outcome | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| F17a Continuous review | #473–#475 | Three libraries continuously reconcile through the bounded persisted review pipeline with compact shared activity and restart-safe controls. | Delivered. |
| F09p Policy notebook | #343, #476–#477 | The selected B notebook persists reviewed drafts and atomically cuts over one exact generation; #478 additionally permits Automatic only as non-authoritative Preview intent while Review and decision authority remain unavailable. | Delivered. |
| F19a Qualified automatic decisions | #444, #478–#479 | Deterministic outcomes accumulate non-authoritatively in Preview before an exact-qualified, decision-only automatic posture records immutable policy outcomes. | Delivered: #478 remains the non-activating Preview increment; #479 activates only the sealed F19a envelope with pause, suspension, explicit recovery, and no apply/write capability. |

Issue #478 is deliberately non-activating: its complete posture set is
`review | preview`, leave is impact-only, and qualification observations never
reduce human work or become decisions. #479 separately adds `automatic` only
for the sealed qualified envelope; it records immutable decision-only outcomes
and never applies or writes target, media, or artifact state.

F20 activation #564 pins the exact public zero-authority candidate in the
selection and presentation seams. Its empty automatic set preserves the F19a
manifest and six existing automatic destinations unchanged; the rich fields
remain human-routed with aggregate-safe terminal explanations.

## Delivery checkpoint 1.0.0: guarded mutation and product operation

Write-capable slices use fake or disposable services until their explicit
canary slice. Destructive operations remain outside ordinary review and apply.

The original WP1–WP9 parents and their constituent forests are closed,
superseded planning history. They remain useful context in completed checkpoint
records but are not current delivery tickets and must not be reopened or
recreated.

| Outcome | Issue | Demonstrable result | Planning state |
| --- | --- | --- | --- |
| F08 Audiobook health report | #144 | One generated audiobook produces bounded tag, chapter, duration, and media-health observations without transcription or production mutation. | Retained planned designation. |
| F15a Supported container operation | #151 | The supported single container starts safely, runs the report and review product, survives current-schema restart, and exposes bounded redacted health, diagnostics, support, backup, and recovery evidence. | Delivered by #151; guarded application remains composed through existing F13 contracts. |
| F16a Authenticated browser review and decision | #152 | An authenticated owner finds one current case, inspects bounded evidence, makes one individual field decision, and observes immutable server-authoritative current-case history after refresh or restart. The individual round trip passes the first-release responsive, keyboard/focus, accessibility, private caching/storage, content-security, hostile-rendering, performance, synthetic-artifact, and primary-supported-browser baseline. | Delivered through #447 → #448 → #449 → #450 and milestone-13 corrections through #455; cross-case history remains #195. |
| F17a Continuous review orchestration | #473 | Configured Audiobookshelf book libraries continuously reconcile through the bounded persisted decision-only review pipeline, with compact shared activity and authenticated pause/resume/retry controls. | Delivered through #474 → #475; no automatic decision or write authority. |
| F13 Guarded fake-service apply | #149 | Current immutable human and qualified automatic field effects share one persisted, backed-up, idempotent fake-service plan, claim, readback, verification, reconciliation, and audit path across all 14 ebook destinations. | Delivered by #149; no Audiobookshelf or media-write adapter is enabled. |
| F14 Audiobookshelf canary apply | #150 | After F13, the same gates perform one bounded mutation against a disposable qualified Audiobookshelf instance and prove before/request/after evidence. | Delivered by #150. |
| F17 Continuous pre-release operation | #153 | The useful Audiobookshelf path runs repeatedly under persisted scheduling and reconciliation, survives representative failures and restarts, and remains observable and recoverable without widening authority. | Delivered by #153 through explicit library consent and one guarded instance slot. |

The retained `F` IDs are planned designations on the assigned outcome issues,
not proof of `Ready` status or authority to begin. Each tracker still must meet
the normal readiness and selection rules. Completed F09o #70 supplies the
proposal prerequisite used by later review outcomes. Open Library #210 remains
the completed F09l tracker in milestone 0.8.0.

The outcomes are not one serial chain. Readiness records the real dependencies:
#70 supplies explainable proposals; F10b admits eligible proposals; F16a records
applicable current human decisions and qualifies the first-release browser
baseline; F13 has delivered the common human/qualified-automatic guarded
fake-service path; and F14 follows completed F13 qualification for its disposable
Audiobookshelf canary. F11 is terminal historical context with only its
deterministic packet and decision projection retained; F15a supplies the
supported operating surface; and F17 composes the useful guarded path under
repeated operation without depending on future target CAS. F08 remains an
independent evidence outcome.

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
- one authenticated owner can inspect a current case and make one persistent,
  server-authoritative individual decision through the F16a first-release
  responsive, keyboard/focus, accessibility, private caching/storage,
  content-security, hostile-rendering, performance, synthetic-artifact, and
  primary-supported-browser baseline; and
- the complete useful path passes bounded continuous-operation qualification
  against exact-current state without adding release or compatibility promises.

Satisfying this checkpoint exit does not itself publish a release. First-release
artifact production #94 and publication-readiness audit #43 are separate gates
in the `first-release` milestone and require an explicit maintainer publication
decision. Post-release migration and older-schema restore recovery remain #57;
neither is F17 acceptance.

## Deferred outcomes

The current deferred product outcomes are:

- opening-audio evidence #24; its former #104–#110 decomposition is closed
  superseded history;
- one outcome per further metadata provider: LibraryThing #211, Project
  Gutenberg #212, LibriVox #213, and Audible #214;
  the old F09c/F09d/F09e trackers #205, #206, and #208 are closed superseded;
- bulk browser review and extended qualification #195 after the first public
  release;
- Calibre-Web Automated library-provider support #316 after the first public
  release;
- optional Audiobookshelf notifications and polling enhancement #65;
- post-release migration and restore recovery #57; and
- the [additional reviewer backends](future-reviewer-backends.md), embedded-file
  updates, plugin loading, and other optional extensions.

These outcomes stay concise and have no functional-slice designation, milestone,
or pre-created constituents unless explicitly selected. Open Library #210 is
no longer deferred: it is the completed F09l slice in milestone 0.8.0 with no
constituents. Closed speculative trackers, WP parents, and constituent forests
remain historical inputs and never become current work automatically.

## Planning rules

- A named `Deliver slice <ID>` instruction selects that assigned, unblocked
  `Ready` tracker. Without a named ID, the highest-priority assigned, unblocked
  `Ready` tracker determines execution order across work packages. Neither form
  selects or promotes an `Unassigned backlog` outcome.
- The Project `Delivery slice` field records each new-functionality ticket's
  primary slice. Focused work uses `Not applicable`.
- Slice trackers carry their exact closure mapping. Historical issue ranges in
  completed checkpoint records are context, not permission to reopen or close a
  legacy parent or constituent.
- A deliberately narrow adapter or interface is acceptable when it completely
  demonstrates the slice and preserves compatibility for later extension.
- No slice may defer its required safety, recovery, observability,
  documentation, clean-schema handling (or post-release migration), or test
  obligations to a later slice.
- Historical work-package parents remain closed; a current slice neither
  reopens them nor inherits their superseded constituent forest.
