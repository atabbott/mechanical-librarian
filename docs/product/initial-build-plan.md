# Initial Build Plan

This plan turns the product and architecture documents into an implementation
sequence. The completed DUX prototype now supplies the review behavior and test
scenarios that must shape the domain and API from the start. Detailed interface
implementation remains in WP9, after those contracts are production-ready.

Each work package must leave the repository releasable, documented, and testable. A later package may not compensate for missing safety or quality work in an earlier one.

## WP0: project foundation

Deliverables:

- select and record the project licence;
- initialize the repository and protected default branch;
- add formatter, linter, type checker, test runner, dependency review, secret scanning, and documentation checks;
- define contribution, security, release, and code-ownership workflows;
- create reproducible development and CI environments;
- record the frozen DUX baseline and establish sanitized extraction fixtures;
- establish an independently authored synthetic metadata-chaos and reliability
  corpus with explicit fixture provenance; and
- publish an initial threat model and ADR set.

Exit criteria:

- a clean checkout can run all checks with one documented command;
- CI enforces the same checks;
- no production or prototype credentials exist in history; and
- every DUX-derived fixture has provenance, sanitization, and licence evidence;
  and
- the public API and compatibility promises are explicitly marked pre-stable.

## WP1: domain kernel and configuration

Deliverables:

- implement the domain entities and state machines;
- define configuration schemas, validation, and secret references;
- define adapter interfaces and capability declarations;
- define separate application-owned ports for target-service adapters, local
  evidence extractors, metadata providers, and reviewer backends, while keeping
  deterministic policy in the domain;
- define vendor-neutral canonical target, snapshot, evidence, candidate,
  proposal, reviewer-packet, decision, and apply-plan schemas with stable opaque
  scoped identifiers, clocks, and idempotency keys;
- keep raw vendor payload semantics adapter-local and permit an external item
  value to cross only as an uninterpreted `ExternalItemId` inside a fully scoped
  canonical `TargetId`, never as an unscoped ID;
- implement the snapshot-aware ReviewCase lifecycle, ReviewNotice classes,
  HumanDecision and DecisionAuthorization contracts, and display-only
  ReviewGroup projection; and
- add property tests for domain invariants.

Exit criteria:

- the domain layer imports no framework, database, network, or vendor modules;
- no canonical domain or application schema exposes Audiobookshelf, CWA, or
  provider payload semantics; generic opaque source provenance and correlation
  remain allowed without becoming semantic proposal identity;
- sibling groups cannot propagate decisions or apply state;
- invalid transitions cannot be constructed through public interfaces; and
- configuration errors fail before any job or network mutation starts.

## WP2: persistence and durable jobs

Deliverables:

- introduce versioned SQLite migrations;
- implement repositories behind application-owned interfaces;
- implement leases, retries, backoff, cancellation, and dead-letter handling;
- persist bounded acyclic run dependency graphs, expected typed outcomes, and a
  nonterminal `waiting` run/stage state distinct from job `retry_wait`;
- pin an immutable workflow-definition version and graph digest to each run;
  incompatible definitions create linked superseding or replanned runs,
  preserve the original graph and outcomes, and mark every transitively affected
  downstream checkpoint in the linked run ineligible for reuse or current
  applicability without mutating or erasing history;
- persist and durably enqueue the complete dependency-ready set, make
  capacity-bounded claims under leases with stable job identity and
  deduplication, and accept one monotonic-fenced independently validated outcome
  per job before fan-in;
- treat at-least-once redelivery as expected and require idempotent handlers plus
  provider idempotency or reconciliation to prevent or recover duplicate
  external effects;
- persist resumable stage checkpoints for bounded multi-stage review runs and
  reject downstream execution when required scope is incomplete or unverified
  or an upstream version is stale, while accepting typed terminal no-result and
  degraded optional outcomes;
- model typed readiness facts as exact coverage over a selected target scope,
  including terminal no-result outcomes, without blocking unrelated ingestion;
- persist typed blocker facts naming the required scope, stage, target, and
  missing or failed terminal outcome that prevents completion;
- persist event and ingestion deduplication outcomes, including failed or
  intentionally suppressed work, across process restarts;
- persist audit events and immutable evidence; and
- add backup-aware migration tests.

Exit criteria:

- process termination cannot silently lose acknowledged work;
- duplicate delivery, persistent failure redelivery, and worker restart cannot
  create unbounded retries, and duplicate external effects are prevented or
  recovered through idempotency and reconciliation;
- restart resumes from the last valid checkpoint, and independently recomputed
  coverage must match the selected scope before a dependent stage advances;
- worker, scheduler, or process restart cannot duplicate completed work, lose
  cancellation, or treat `waiting` as readiness or success;
- every state transition is attributable; and
- upgrade and rollback rehearsals pass against representative databases.

## WP3: Audiobookshelf read adapter

Deliverables:

- implement authentication and connection testing;
- discover libraries and scan items incrementally;
- normalize supported ebook and audiobook metadata;
- translate Audiobookshelf payloads and identifiers at the adapter boundary into
  canonical inventory records; only an uninterpreted external value wrapped
  inside the opaque adapter/connection/library-scoped `TargetId` crosses the
  boundary;
- preserve connection- and library-scoped external identity and media type;
- capture optimistic-concurrency snapshots;
- expose capability and version diagnostics; and
- build contract fixtures from redacted API responses.

Exit criteria:

- read-only operation is the default;
- the adapter performs no database or media-file writes;
- pagination, rate limiting, timeouts, and partial failures are tested; and
- unsupported target behavior degrades to an explicit capability state; and
- the same target-service port remains implementable by a CWA-shaped fake
  without changing inventory or downstream schemas.

## WP4: local evidence extraction

Deliverables:

- extract raw and normalized EPUB package metadata under the
  [opening-ebook evidence contract](opening-ebook-evidence.md);
- select semantic title, copyright, imprint, edition, contributor, and other
  opening-matter resources, with a bounded reading-order fallback;
- extract typed identifiers, contributor roles, dates, publisher/imprint,
  series, edition, translation, illustration, omnibus, and structural facts
  with exact resource and text-span provenance;
- define the format boundary so AZW3 and MOBI can follow without changing the
  evidence model;
- extract audio tags, chapters, and duration without transcription;
- optionally record bounded read-only media-health observations such as a
  missing audio stream, invalid duration, parser timeout, or inability to read
  near the end of a file;
- isolate parsers and enforce resource limits;
- fingerprint source files without copying whole libraries;
- attach provenance and confidence to every fact;
- store each bounded evidence fact once and reference it by stable ID from
  proposals, notices, and review cases; and
- expose compact evidence summaries separately from detailed bounded facts.

Exit criteria:

- malformed or hostile files cannot block the job system;
- extraction is read-only and bounded;
- package, opening-matter, navigation, and structural observations retain raw
  values, normalized interpretations, same-file correlation, contradictions,
  and edition-applicability warnings;
- evidence is reproducible and content-addressed where practical; and
- unsupported formats fail visibly without preventing ingestion; and
- media-health diagnostics remain distinct from metadata evidence and never
  quarantine an otherwise consumable item solely because curation is pending.

### Near-term delivery staging

The initial proof deliberately narrows WP5 to a provider-neutral title path.
Completed F06 plus planning ticket #221 leads to F09a #145/#222, then F09b
#204/#223, then F10a #146/#224. F09a combines a closed in-memory
Audiobookshelf-shaped synthetic snapshot, one current successful unambiguous
applicable `TITLE` produced by the real F06 child from an application-generated
EPUB, and a deterministic fake provider candidate. The F06 title's
`normalized_value` is non-empty, bounded, and usable rather than an unsafe,
oversized, or redaction marker, and one deterministic fake candidate. A current
title field that is present and loaded with an empty value is eligible for fill
when F06 and provider corroborate, and a materially different current title
remains eligible. A missing, unloaded, or unavailable current title, or an empty
or unusable F06/provider title, produces an exact typed no-proposal result. F09b
adds `hardcover-title-proposal-report` for only the fixed generated
`agree-empty` case behind the same application-owned port. Omitting its optional
secret-file reference performs no secret access, DNS, or egress and records
`provider_disabled`; provider and credential failures retain local evidence as
`provider_failed`. It changes no `config-v1` field. Required qualification uses
a sanitized live-shape fake through the same adapter seam and the exact
installed artifact against the bounded real endpoint. Production provider
configuration, durable work, freshness/reuse, restart, and strict ISBN-backed
Hardcover identity remain F09f scope; the live-target join remains F09g scope.
F10a admits one current undecided synthetic case only from
one current immutable proposal set containing exactly one current
`needs_review` title proposal. It replays idempotently, supersedes for a
qualifying successor, and retires for a nonqualifying successor without an
empty replacement; no other proposal or set creates a case.

This staging uses the exact F06 `normalized_value` as the proposal value, keeps
F06 `raw_value` as evidence/provenance, uses provider evidence only for
corroboration, records exact typed no-proposal outcomes, and supports replay
and supersession without introducing AI, human decisions, covers, rich fields,
multiple providers, or writes. F07a #143 and F07b #228 begin the later 0.3
checkpoint and do not block the 0.2 preview. F08 #144, the broader #16, #46,
#50, #68-#73, and #209 program, and F11 #147/#74-#80 remain 1.0 scope. The WP5
and WP6 contracts below describe that long-term architecture; they are not 0.2
exit criteria.

Every F09a set carries the closed `synthetic_preview` mode in its identity and
derives `production_applicable=false`; it cannot enter production review or
apply authority. The command accepts no configuration, live IDs, operator
titles, media references, provider payloads, endpoints, or credentials. A live
local-evidence-to-current-Audiobookshelf binding, configured-media resolution,
and their freshness and recovery semantics are deliberately deferred to the
explicit 0.3 work. F09b and F10a may continue the preview only without erasing
that synthetic lineage or granting it production applicability.

The 0.3 internal checkpoint turns the preview into the first trustworthy live
title report: durable local EPUB title and edition-applicable ISBN evidence and
one target-independent Hardcover title/book/compatible-ebook lineage join one
current Audiobookshelf snapshot to produce one immutable
`production_report_only` proposal or its exact typed zero-proposal result. It
adds no review admission, decision, approval, apply, target or media write,
destructive authority, tag, or release publication. The exact slice graph,
issue ownership, outcome partitions, and exit criteria are maintained in
[Delivery checkpoint 0.3.0](delivery-slices.md#delivery-checkpoint-030-first-trustworthy-live-title-report).

The following 0.4 internal checkpoint is a focused pre-stable consolidation,
not a product slice. It preserves that complete report while reducing the
application to one current inventory model, durable-work substrate, EPUB
isolation and qualification control plane, and least-authority executable
boundary. It also retires the F02/F03 demonstrations and the F09a/F09b/F10a
preview runtime and persistence lanes, transfers the exact production-case
lifecycle to F10b, removes future-only contract/fixture and rendered-HTML gate
control planes, and corrects stale DUX ownership. A retained custom mechanism
must have a current runtime or mandatory-assurance consumer and named invariant;
a maintained replacement must delete its old owner without widening authority.
The unconsumed #49 export/import helpers return to WP1 parent #2 until a real
setup or administration surface claims them. Historical preview staging ends
after final 0.3, and unsupported state is rejected before mutation with explicit
retain-and-rebuild guidance. The exact issue order, dependencies, native
parents, compatibility decision, and exit criteria are maintained in
[Delivery checkpoint 0.4.0](delivery-slices.md#delivery-checkpoint-040-pre-stable-consolidation).

## WP5: candidate and policy engine

Deliverables:

- generate field-level proposals from current values and local evidence;
- model the current snapshot as authoritative scoped target state for no-op,
  concurrency, planning, and verification while treating its metadata as one
  versioned bibliographic observation rather than privileged truth;
- derive capabilities per lane role, never from one global intersection or
  hard-coded service/provider names: inventory lanes use target and scope;
  local-evidence lanes use target, media, field, and extractor; provider lanes
  use target, media, field, provider, query, and configuration; candidate lanes
  use relevant successfully completed evidence and policy. An irrelevant
  dimension is absent rather than falsely required;
- build minimal bounded allowlisted `ProviderQuery` values in the application;
  pass a provider adapter only that query, its own credential, and restricted
  network client, never a raw `ItemSnapshot` or service row, service endpoint,
  target credential, unrestricted external ID or path, or mutation port;
- collapse identical normalized values while retaining every source
  assessment, evidence lineage, contradiction, and source-specific problem
  fact; recompute one deterministic aggregate disposition without unioning
  stale risks or allowing a strongest-source winner to erase conflict;
- keep generic source adapter/provider provenance and correlation on candidates,
  while defining semantic proposal identity from canonical target, snapshot,
  field, normalized value, schema, and policy rather than provider name;
- persist a versioned, capability-derived candidate-lane manifest that records
  applicability for every derived lane and a typed execution outcome for each
  applicable lane; only successfully completed applicable lanes record a
  candidate count, which may be zero, and execution mode `executed` or `reused`.
  Reuse requires exact target scope, matching input fingerprints, compatible
  provider, query, configuration, and contract versions, and a validated
  complete expected artifact. Missing lanes are coverage failures, and failed
  execution is never reusable and follows governed retry, backoff, and
  suppression;
- implement configurable risk and confidence policies;
- model source applicability, independence, correlation, and contradiction
  before agreement can increase automatic-write eligibility;
- require supported target and work identity before proposing a correction;
  additionally require edition identity or applicability when the field policy
  or proposed change is edition-sensitive, and reject identity or comparable
  identifier conflicts relevant to the field;
- count agreement across independent evidence families rather than repeated or
  projected records, while allowing a policy-defined decisive single-family
  result from direct authoritative, identity-supported, edition-applicable
  evidence;
- handle generic titles, initial-aware author matching, contributor-role
  confusion, swapped fields, and release-name debris as explicit risk facts;
- implement directional title normalization that removes specifically
  supported format, identifier, and filename noise while preserving meaningful
  subtitles, removes meaningful series/order text only when independently
  supported separate structured facts are already persisted or emitted as
  preserved sibling proposals, and rejects the unsupported inverse addition;
- treat current metadata as versioned sourced evidence rather than privileged
  truth;
- preserve raw identifier observations, validate and canonicalize recognized
  aliases and comparable ISBN forms, compare compatible namespaces only, and
  keep non-comparable identifier types neutral;
- treat one distinct checksum-valid ISBN with typed format- and edition-
  applicable embedded/content provenance as strong edition evidence only when
  a materially independent, format-appropriate provider returns the same
  canonical ISBN, including ISBN-10/ISBN-13 equivalence, and target, work, and
  media identity agree; multiple content ISBNs, unrelated previews or other-work
  claims, correlated providers, and edition, format, narration, abridgement,
  dramatization, omnibus, or cross-media conflicts remain unresolved;
- allow malformed or invalid current identifiers to follow ordinary applicable
  field evidence, but supersede a validated current comparable identifier only
  when an applicable embedded/content identifier and a materially independent
  provider identifier agree on the same validated canonical replacement
  identity, preserving source lineage and correlation;
- distinguish no-op, safe automatic, AI-review, and human-review outcomes;
- omit proposals whose normalized value is equivalent to current state;
- implement adapter/media applicability and reviewer-facing notice classes;
- generate typed cover and series choices without applying them;
- apply only qualified, versioned series-label mappings, preserving raw,
  normalized, and canonical labels, source lineage, work identity, rule
  version, and replay or supersession history while leaving unknown labels
  unchanged;
- treat source AI recommendations carried by projected shared evidence as
  provenance only and requalify destination identity, applicability, current
  state, evidence, and policy independently;
- retain complete reasons for accepted and rejected candidates;
- treat provider failure as an operational outcome rather than negative
  metadata evidence or support for another candidate;
- publish a versioned sufficiency matrix by field, media type, operation, and
  change class—including fill-empty, replace-populated, no-op, rejection, and
  edition-sensitive work—stating when current/local evidence is sufficient,
  when materially independent provider evidence is required, and when missing
  provider evidence produces human review or an explicit unsupported outcome
  rather than silent suppression;
- support a providerless report-only path through current/local evidence,
  deterministic no-op and rejection handling, bounded proposals, and human
  review; and
- keep every field proposal independent so resolving one field cannot erase or
  implicitly decide a sibling proposal.

Exit criteria:

- policy decisions are deterministic for identical inputs;
- provenance is never lost during normalization or ranking;
- correlated records cannot manufacture consensus, and no universal
  two-family minimum suppresses a qualifying decisive source;
- identity-backed title cleanup, exact-identifier or direct-content decisive
  evidence, close supported alternatives, no-op omission, and conflicting
  identifier rejection pass the synthetic reconciliation cohort;
- recognized-noise removal and unsupported-noise addition have directional,
  deterministic policy outcomes without AI `manual`, while meaningful subtitle
  information survives cleanup and meaningful series/order removal requires an
  independently supported persisted or newly emitted structured sibling fact;
- identifier alias and ISBN canonicalization, namespace compatibility,
  non-comparable neutrality, raw-value retention, and independently
  corroborated replacement of validated current comparable identifiers pass
  synthetic and metamorphic tests, while malformed or invalid current values
  use ordinary applicable field evidence;
- resolving any field leaves every sibling proposal intact;
- unsafe, rejected, inapplicable, and no-op choices cannot contaminate a safe
  field selection;
- high-risk changes cannot enter the automatic apply path;
- disabling or losing every external metadata provider does not disable
  inventory, local evidence, deterministic policy, bounded proposals, or human
  review, while any provider-required field follows the sufficiency matrix; and
- golden fixtures cover ambiguous editions, series order, conflicting
  identifiers, generic titles, initial-only names, contributor-role confusion,
  and same-title/different-author works.

## WP6: untrusted AI reviewer

Deliverables:

- define the versioned AI request and response schemas;
- require each authoritative packet row to contain its exact bounded normalized
  current snapshot, explicit loaded, missing, or unavailable field state,
  relevant bounded evidence, candidate and policy versions, and completeness
  provenance; fail the join before reviewer invocation when required context is
  absent;
- represent optional per-run reviewer intent as typed, bounded, versioned data
  in the closed request schema, separated from policy instructions and
  untrusted evidence;
- implement the Codex CLI backend behind a generic reviewer port;
- validate, bound, and audit direct closed-schema responses, rejecting duplicate
  JSON object keys, trailing content, unknown fields or missing required fields,
  duplicate or partial proposal decisions, and malformed values without
  semantic repair;
- enumerate the authoritative upstream evidence and provider inputs for each
  selected target, and require accepted terminal outcomes for required inputs;
- allow optional provider failure as a typed terminal operational outcome that
  neither blocks unrelated ingestion nor becomes negative metadata evidence;
- consume only canonical evidence and proposal contracts; the reviewer backend
  cannot call target-service or metadata-provider adapters or depend on their
  vendor payloads;
- independently recompute selected-target and proposal coverage before marking
  an AI review complete;
- record zero proposals as a proposal-selection/orchestration terminal outcome
  without invoking the reviewer, and require exact per-proposal coverage for
  every packet that is invoked;
- keep `manual` as an AI result only for multiple specifically supported
  alternatives; it may complete reviewer output but never creates a human
  decision or apply readiness;
- require the `AIDecision.problems` list to contain only unresolved or adverse
  problem codes; positive support belongs in cited evidence and the concise
  reason, so a clean approval uses `problems: []`;
- add replayable evaluation cohorts; and
- support disabling AI without disabling the core product.

Exit criteria:

- the reviewer cannot access target credentials or mutation interfaces;
- arbitrary reviewer text cannot become an apply instruction;
- per-run intent cannot override policy, safety, scope, authority, or schema,
  and structured output is validated directly rather than carried as encoded
  JSON inside a string field;
- backend normalization may remove a documented transport envelope but cannot
  infer missing decisions, drop unknown fields, repair substantive output, or
  convert JSONL into the reviewer response contract;
- a successful process, artifact, or claimed count cannot establish completion
  without an independent audit of decisions, reasons, required `problems`
  lists, proposal references, and exact selected-target coverage;
- missing accepted terminal outcomes for required upstream inputs block only
  the affected review scope;
- timeouts and malformed output produce safe workflow states; and
- evaluation results meet the documented minimum before automation is enabled.

## WP7: guarded Audiobookshelf apply

Deliverables:

- build immutable apply plans from either qualified automatic policy
  authorization or an applicable human decision;
- preserve field-level evidence, proposal, policy, AI, and human provenance;
- re-read target state immediately before mutation;
- create rollback evidence before each change;
- apply through supported Audiobookshelf APIs only;
- verify the resulting target state; and
- provide field-level rollback where the target supports it.

Exit criteria:

- stale targets stop safely;
- AI recommendation alone cannot authorize a write;
- retries cannot duplicate or widen a change;
- every mutation has a principal, plan, before state, after state, and verification result; and
- fault-injection tests cover timeouts before, during, and after mutation.

## WP8: single-container product shell

Deliverables:

- package the API, job runtime, scheduler, static web assets, and database into one image;
- implement first-run setup and health endpoints;
- add structured logging, metrics, and redacted support bundles;
- run supervised report-only auto-resume through the application scheduler with
  an immutable initiating principal and authorization context, scope,
  policy/schema versions, resource bounds, cancellation, and expiry; record
  each transition actor linked to that authority, and never enable writes or
  mutate the governing authority through continuation;
- expose typed job stage, active adapter, attempt, retry eligibility, and
  circuit-breaker or automatic-suspension reasons for operator diagnostics,
  plus bounded advisory ETA with calculation basis, timestamp, freshness, and
  an explicit unknown state;
- publish TrueNAS-oriented and generic container deployment examples; and
- produce multi-architecture images, SBOMs, signatures, and provenance.

Exit criteria:

- a new user can deploy with one container, one data volume, media mounts, and one web port;
- restart, backup, restore, and upgrade drills pass;
- scheduler restart and supervised continuation resume from durable state, not
  process IDs, shell sessions, or detached watcher ownership;
- no internal component requires an exposed port; and
- the image passes vulnerability and secret scans.

## WP9: review API and operator surface

Deliverables:

- expose versioned, cursor-paginated endpoints for cases, confirmed sibling
  groups, evidence, decisions, summaries, jobs, audit events, and rollback;
- implement target-level queue, decision-first detail, progressive evidence,
  sibling navigation, and global review-summary surfaces;
- expose server-authoritative target, ordinary-case, destructive-case,
  field-work-item, and planned-change counts with explicit partial and stale
  semantics;
- implement exact-version bulk preview and commit with stale rejection,
  idempotency, individual decision records, and compensating reversal;
- satisfy the responsive, keyboard, accessibility, privacy, and performance
  contracts in the [review experience](review-experience.md); and
- document all mutations and their concurrency guards.

Exit criteria:

- a user can leave the service unattended and return to a bounded review queue;
- safe decisions can be applied without shell access;
- rejected and historical work remains auditable; and
- acknowledged decisions survive refresh, restart, and device changes;
- sibling decisions cannot leak across targets; and
- real-browser accessibility, narrow-viewport, pagination, stale-decision, and
  payload-budget gates pass.

## Post-foundation work

After the first production-quality vertical slice:

- refine review ergonomics from measured production usage without changing the
  DUX-derived server contracts silently;
- expand metadata providers and cover comparison;
- add optional application-level write-back to ebook files;
- evaluate a CWA adapter only against supported service APIs, explicit
  capability declarations, and the common target-service contract; no CWA
  database, UI automation, runtime adapter, or production write path is added by
  this planning work;
- define any future extension manifest around typed capabilities,
  configuration schema, compatibility, health, and automatic suspension before
  permitting third-party code loading;
- implement bounded opening-audio transcription only after the sampling,
  privacy, backend, resource, retention, and field-qualification decisions in
  the [opening-audio evidence contract](opening-audio-evidence.md) are resolved;
- add PostgreSQL only if measured scale or deployment requirements justify it; and
- consider broader story-content transcription only through a separate scope,
  cost, privacy, copyright, and resource decision.

## Planning rule

Implementation tickets should reference the governing documents and state which
invariant, ADR, threat, and test layer they affect. New functionality also names
its functional delivery slice. Work packages remain ownership buckets; they do
not define branch or pull-request boundaries. Initial product functionality
follows the branch and review workflow in
[functional slice delivery](../development/slice-delivery.md) and the ordered
[delivery-slice plan](delivery-slices.md). Fixes, corrections, documentation,
tooling, maintenance, and standalone tickets retain focused ticket-level
delivery. A ticket is not complete when code merely works; its contracts, tests,
operations, and documentation must agree.
