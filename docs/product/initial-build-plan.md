# Initial Build Plan

This plan turns the product and architecture documents into an implementation sequence. It deliberately postpones detailed dashboard UX work while preserving the backend contracts that the dashboard will need.

Each work package must leave the repository releasable, documented, and testable. A later package may not compensate for missing safety or quality work in an earlier one.

## WP0: project foundation

Deliverables:

- select and record the project licence;
- initialize the repository and protected default branch;
- add formatter, linter, type checker, test runner, dependency review, secret scanning, and documentation checks;
- define contribution, security, release, and code-ownership workflows;
- create reproducible development and CI environments; and
- publish an initial threat model and ADR set.

Exit criteria:

- a clean checkout can run all checks with one documented command;
- CI enforces the same checks;
- no production or prototype credentials exist in history; and
- the public API and compatibility promises are explicitly marked pre-stable.

## WP1: domain kernel and configuration

Deliverables:

- implement the domain entities and state machines;
- define configuration schemas, validation, and secret references;
- define adapter interfaces and capability declarations;
- define stable identifiers, clocks, and idempotency keys; and
- add property tests for domain invariants.

Exit criteria:

- the domain layer imports no framework, database, network, or vendor modules;
- invalid transitions cannot be constructed through public interfaces; and
- configuration errors fail before any job or network mutation starts.

## WP2: persistence and durable jobs

Deliverables:

- introduce versioned SQLite migrations;
- implement repositories behind application-owned interfaces;
- implement leases, retries, backoff, cancellation, and dead-letter handling;
- persist audit events and immutable evidence; and
- add backup-aware migration tests.

Exit criteria:

- process termination cannot silently lose acknowledged work;
- duplicate delivery is safe;
- every state transition is attributable; and
- upgrade and rollback rehearsals pass against representative databases.

## WP3: Audiobookshelf read adapter

Deliverables:

- implement authentication and connection testing;
- discover libraries and scan items incrementally;
- normalize supported ebook and audiobook metadata;
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
- isolate parsers and enforce resource limits;
- fingerprint source files without copying whole libraries; and
- attach provenance and confidence to every fact.

Exit criteria:

- malformed or hostile files cannot block the job system;
- extraction is read-only and bounded;
- evidence is reproducible and content-addressed where practical; and
- unsupported formats fail visibly without preventing ingestion.

## WP5: candidate and policy engine

Deliverables:

- generate field-level proposals from current values and local evidence;
- implement configurable risk and confidence policies;
- distinguish no-op, safe automatic, AI-review, and human-review outcomes;
- generate cover candidates without applying them; and
- retain complete reasons for accepted and rejected candidates.

Exit criteria:

- policy decisions are deterministic for identical inputs;
- provenance is never lost during normalization or ranking;
- high-risk changes cannot enter the automatic apply path; and
- golden fixtures cover ambiguous editions, series order, and conflicting identifiers.

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

- build immutable apply plans from approved proposals;
- re-read target state immediately before mutation;
- create rollback evidence before each change;
- apply through supported Audiobookshelf APIs only;
- verify the resulting target state; and
- provide field-level rollback where the target supports it.

Exit criteria:

- stale targets stop safely;
- retries cannot duplicate or widen a change;
- every mutation has a principal, plan, before state, after state, and verification result; and
- fault-injection tests cover timeouts before, during, and after mutation.

## WP8: single-container product shell

Deliverables:

- package the API, job runtime, scheduler, static web assets, and database into one image;
- implement first-run setup and health endpoints;
- add structured logging, metrics, and redacted support bundles;
- publish TrueNAS-oriented and generic container deployment examples; and
- produce multi-architecture images, SBOMs, signatures, and provenance.

Exit criteria:

- a new user can deploy with one container, one data volume, media mounts, and one web port;
- restart, backup, restore, and upgrade drills pass;
- no internal component requires an exposed port; and
- the image passes vulnerability and secret scans.

## WP9: review API and minimal operator surface

Deliverables:

- expose versioned endpoints for queues, evidence, decisions, jobs, audit events, and rollback;
- add a deliberately minimal interface for setup, status, and required review;
- preserve backend pagination, filtering, bulk-decision, and accessibility contracts for later UX work; and
- document all mutations and their concurrency guards.

Exit criteria:

- a user can leave the service unattended and return to a bounded review queue;
- safe decisions can be applied without shell access;
- rejected and historical work remains auditable; and
- detailed dashboard design can proceed without changing core domain semantics.

## Post-foundation work

After the first production-quality vertical slice:

- improve dashboard UX using the separately developed DUX findings;
- expand metadata providers and cover comparison;
- add optional application-level write-back to ebook files;
- evaluate a CWA adapter only against supported mutation paths;
- add PostgreSQL only if measured scale or deployment requirements justify it; and
- consider transcription only with an explicit cost, privacy, and resource model.

## Planning rule

Implementation tickets should reference the governing documents and state which invariant, ADR, threat, and test layer they affect. A ticket is not complete when code merely works; its contracts, tests, operations, and documentation must agree.
