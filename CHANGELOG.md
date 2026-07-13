# Changelog

All notable user-visible changes will be documented here.

The project intends to follow Keep a Changelog conventions and semantic
versioning after the first public release.

## Unreleased

### Fixed

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
