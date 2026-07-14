# Changelog

All notable user-visible changes will be documented here.

The project intends to follow Keep a Changelog conventions and semantic
versioning after the first public release.

## Unreleased

- Added `epub-package-report`: a deterministic cited EPUB 2/3 package-evidence
  report with bounded ZIP64/Deflate and Expat processing, no-follow read-only
  source access, a transient descriptor-bound child, source-stability checks,
  exact typed exits, configurable immutable resource caps, and no persistence,
  content-resource dereference, temporary extraction, network, or write path.
  Unsafe metadata is projected one-way to a digest placeholder and cited
  warning. The complete suite is qualified on CPython 3.13.14 and 3.14.6 for
  macOS development, and the F06 parser/process cohort is qualified on
  non-root Linux CPython 3.13.11 with a read-only filesystem, no network, and
  exact child resource/process gates. Read-only compatibility includes opaque
  ZIP directory entries, a warned exact producer-residue ZIP flag pattern,
  bounded noncanonical `mimetype` packaging, compatible package-version
  families, and bounded EPUB 3 title-type/display-sequence refinements. Optional
  broken package declarations and deprecated tours are warned and omitted
  without exposing or dereferencing content. The effective document ceiling is
  the narrower of the shared 64 MiB default and the EPUB profile's 256 MiB
  default, while ordinary metadata values up to 16,384 characters are preserved
  within the unchanged 512 KiB public-report cap. JSON and line-oriented text
  expose the same complete privacy-safe cited projection.

- Added durable duplicate-safe Audiobookshelf polling with persisted cumulative
  budgets, bounded retry/backoff, governing-state suppression, monotonic lease
  fencing, credential-free poll inspection, and cohort-atomic removal
  publication. Socket.IO remains unavailable at the API-key trust boundary.
- Added bounded, GET-only Audiobookshelf 2.35.1 inventory traversal with
  two-pass stability proof, versioned vendor-neutral snapshots, durable scan
  recovery, atomic disappearance publication, immutable history, and a
  credential-free inspection command.

### Fixed

- Evidence persistence now rejects exact normalized `credential` and
  `credentials` labels before construction or strict deserialization while
  preserving non-credential near misses.
- Audiobookshelf inventory now prepares and recovers durable cohort scan state
  before resolving target credentials or performing compatibility,
  authorization, library, or item requests.
- Audiobookshelf connection readiness now requires unrestricted configured-
  library, tag, and explicit-content visibility so partial content scopes
  cannot advertise complete inventory and metadata capabilities.
- The Audiobookshelf connection check now validates anonymous target identity
  and pinned-version evidence before sending a Bearer credential, while
  retaining status-only incompatibility reports for unqualified versions.
- The Audiobookshelf connection check now accepts unrelated validated provider,
  reviewer, notification, and read-only capability-override settings without
  constructing those components or widening target authority.
- Audiobookshelf connection scope now accepts and preserves canonical lowercase
  UUIDv4 library identifiers while continuing to reject malformed identities.
- Evidence values, references, readiness, and persistence/reviewer/API
  projections now enforce deterministic size, privacy, target-scope, and
  versioned cross-target policy bounds before untrusted data crosses a boundary.
- Ready pull requests now pass the linked-work gate only with GitHub-recognized
  closing references to open issues in this repository.
- Configuration import plans now validate both inputs identically and report a
  deterministic, value-free impact matrix for connection, evidence, provider,
  reviewer, policy, notification, and backup changes.
- Inventory records now reject cross-adapter, connection, library, target,
  snapshot, media, lifecycle, and capability combinations, and snapshots use a
  versioned deterministic canonical state for fingerprints and equality.
- Audit persistence now returns typed safe outcomes and rejects unsafe summary
  values without exposing SQLite errors or stored payloads, with bounded
  indexed chronological queries in the initial schema.
- SQLite migrations now execute and record their checksums atomically, with
  fail-closed detection of partial, tampered, newer, and unrecognized schemas.
- First-run durable state is now verified in a private same-filesystem staging
  file and published atomically without replacing a concurrent creator, so a
  failed initialization leaves the requested database path retryable.
- Decision authorizations now retain and validate immutable review-case and
  human-decision or qualified automatic-policy evidence, rejecting stale,
  mismatched, disposition-only, or ID-only authority.

### Added

- A target-scoped `connection-check --config PATH` report for a configured
  Audiobookshelf instance, with bounded Bearer authorization, version and
  capability compatibility, redacted output, and no library/content reads,
  persistence, provider/reviewer access, or mutation authority.
- A report-only `synthetic-inventory --config PATH --database PATH` round trip
  with strict config-v1 JSON validation, additive migration-3 persistence,
  restart-safe inspection, and executable mutation-authority isolation.
- A report-only `job-heartbeat --database PATH` demonstration with a durable
  bounded dependency graph, transactional complete-set scheduling,
  capacity-bounded leased claims, monotonic fencing, finite restart-safe retry,
  cancellation, validated fan-in, and immutable incompatible replan history.
- A `state-check --database PATH` operator diagnostic for durable filesystem
  state that creates or opens application state, applies migrations, and
  verifies idempotent audit write/read behavior with fixed path-free success and
  failure output.
- Structured Project work-kind classification and separate Defect/Bug intake
  for pre-release contract failures versus failures of delivered behavior.
- Functional-slice delivery governance for new functionality during the initial
  build, with focused ticket PRs retained for fixes, security, documentation,
  tooling, dependencies, maintenance, and standalone work.
- Append-only SQLite audit repository with bounded recent-event queries.
- Deterministic dry-run configuration import plans with restart and re-extraction impact.
- Bounded evidence references and compact review-summary projections.
- Snapshot-aware review cases, sibling projections, notices, and decision authorization records.
- Immutable evidence, candidate, field-proposal, risk, notice, and disposition vocabulary.
- Versioned, fail-fast, secret-free application configuration validation.
- Pull-request workflow enforcing a real auto-closing implementation-issue link.
- Typed least-authority application ports and adapter failure vocabulary.
- Explicit pure domain lifecycle transitions and authorization-boundary checks.
- Immutable library connection, item, snapshot, normalized metadata, and
  target-capability domain models.
- Typed, deterministic, secret-safe domain identifiers, version markers,
  fingerprints, and idempotency keys.
- Installable typed Python package, initial CLI entrypoint, locked dependency
  metadata, layered package scaffold, smoke tests, and executable architecture
  boundary checks.
- `AGPL-3.0-or-later` project licensing, including the canonical licence text,
  accepted architecture decision, contribution terms, compatibility policy,
  and publication gate.
- Initial product, architecture, security, quality, operations, and governance
  documentation scaffold.
- DUX-derived review-experience and prototype-extraction contracts, including
  server-authoritative decisions, target-level cases, sibling isolation,
  guarded bulk review, accessibility, and performance requirements.
- Clean-room metadata-chaos and reliability fixture requirements, including
  source-correlation policy, restart-safe retry suppression, bounded audio
  health observations, typed operator diagnostics, and future extension
  isolation requirements.
- Future bounded opening-audio transcription and spoken-credit evidence
  contract, including staged sampling, local-first processing, privacy and
  retention boundaries, resource controls, synthetic cohorts, and field-level
  qualification gates.
- Initial bounded EPUB package and opening-matter evidence contract, including
  edition-aware field mining, semantic and reading-order selection, exact
  provenance, identifier applicability, archive/parser security, synthetic
  qualification, and first-class policy use.
