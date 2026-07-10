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
- define stable target, snapshot, review-case, proposal, and decision
  identifiers, clocks, and idempotency keys;
- implement the snapshot-aware ReviewCase lifecycle, ReviewNotice classes,
  HumanDecision and DecisionAuthorization contracts, and display-only
  ReviewGroup projection; and
- add property tests for domain invariants.

Exit criteria:

- the domain layer imports no framework, database, network, or vendor modules;
- sibling groups cannot propagate decisions or apply state;
- invalid transitions cannot be constructed through public interfaces; and
- configuration errors fail before any job or network mutation starts.

## WP2: persistence and durable jobs

Deliverables:

- introduce versioned SQLite migrations;
- implement repositories behind application-owned interfaces;
- implement leases, retries, backoff, cancellation, and dead-letter handling;
- persist event and ingestion deduplication outcomes, including failed or
  intentionally suppressed work, across process restarts;
- persist audit events and immutable evidence; and
- add backup-aware migration tests.

Exit criteria:

- process termination cannot silently lose acknowledged work;
- duplicate delivery, persistent failure redelivery, and worker restart cannot
  create unbounded retries or duplicate external work;
- every state transition is attributable; and
- upgrade and rollback rehearsals pass against representative databases.

## WP3: Audiobookshelf read adapter

Deliverables:

- implement authentication and connection testing;
- discover libraries and scan items incrementally;
- normalize supported ebook and audiobook metadata;
- preserve connection- and library-scoped external identity and media type;
- capture optimistic-concurrency snapshots;
- expose capability and version diagnostics; and
- build contract fixtures from redacted API responses.

Exit criteria:

- read-only operation is the default;
- the adapter performs no database or media-file writes;
- pagination, rate limiting, timeouts, and partial failures are tested; and
- unsupported target behavior degrades to an explicit capability state.

## WP4: local evidence extraction

Deliverables:

- extract metadata from EPUB;
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
- evidence is reproducible and content-addressed where practical; and
- unsupported formats fail visibly without preventing ingestion; and
- media-health diagnostics remain distinct from metadata evidence and never
  quarantine an otherwise consumable item solely because curation is pending.

## WP5: candidate and policy engine

Deliverables:

- generate field-level proposals from current values and local evidence;
- collapse identical values while retaining source consensus and alternatives;
- implement configurable risk and confidence policies;
- model source applicability, independence, correlation, and contradiction
  before agreement can increase automatic-write eligibility;
- handle generic titles, initial-aware author matching, contributor-role
  confusion, swapped fields, and release-name debris as explicit risk facts;
- distinguish no-op, safe automatic, AI-review, and human-review outcomes;
- implement adapter/media applicability and reviewer-facing notice classes;
- generate typed cover and series choices without applying them; and
- retain complete reasons for accepted and rejected candidates.

Exit criteria:

- policy decisions are deterministic for identical inputs;
- provenance is never lost during normalization or ranking;
- unsafe, rejected, inapplicable, and no-op choices cannot contaminate a safe
  field selection;
- high-risk changes cannot enter the automatic apply path; and
- golden fixtures cover ambiguous editions, series order, conflicting
  identifiers, generic titles, initial-only names, contributor-role confusion,
  and same-title/different-author works.

## WP6: untrusted AI reviewer

Deliverables:

- define the versioned AI request and response schemas;
- implement the Codex CLI backend behind a generic reviewer port;
- validate, bound, and audit all responses;
- add replayable evaluation cohorts; and
- support disabling AI without disabling the core product.

Exit criteria:

- the reviewer cannot access target credentials or mutation interfaces;
- arbitrary reviewer text cannot become an apply instruction;
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
- expose typed job stage, active adapter, attempt, retry eligibility, and
  circuit-breaker or automatic-suspension reasons for operator diagnostics;
- publish TrueNAS-oriented and generic container deployment examples; and
- produce multi-architecture images, SBOMs, signatures, and provenance.

Exit criteria:

- a new user can deploy with one container, one data volume, media mounts, and one web port;
- restart, backup, restore, and upgrade drills pass;
- no internal component requires an exposed port; and
- the image passes vulnerability and secret scans.

## WP9: review API and operator surface

Deliverables:

- expose versioned, cursor-paginated endpoints for cases, confirmed sibling
  groups, evidence, decisions, summaries, jobs, audit events, and rollback;
- implement target-level queue, decision-first detail, progressive evidence,
  sibling navigation, and global review-summary surfaces;
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
- evaluate a CWA adapter only against supported mutation paths;
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

Implementation tickets should reference the governing documents and state which invariant, ADR, threat, and test layer they affect. A ticket is not complete when code merely works; its contracts, tests, operations, and documentation must agree.
