# Changelog

All notable user-visible changes will be documented here.

The project intends to follow Keep a Changelog conventions and semantic
versioning after the first public release.

## Unreleased

- Added `epub-opening-evidence`, the durable F07b round trip from a path-free
  inventory selector to restart-reusable immutable opening evidence. Inventory
  now atomically persists validated private logical EPUB bindings; schema 8
  adds retry-bounded fenced work, attempts, immutable canonical evidence,
  monotonic current applicability, checkpoints, and append-only events. Source
  or governing changes create new evidence, reverse replay cannot move current
  state backwards, stale workers cannot publish, and operational failures
  remain job health. The installed-wheel qualifier uses a disposable shadow
  cohort and emits aggregate-only results.

- Fixed Hardcover provider-secret validation so leading or trailing space, tab,
  carriage return, or line feed is rejected before client construction, DNS,
  or transport activity while preserving existing target-secret compatibility.

- Added `epub-opening-report`, a transient F07a command that composes the F06
  archive/package gate with bounded EPUB 3 landmark, EPUB 2 guide, and linear-
  spine selection; strict and deterministic recovered XHTML/HTML projection;
  edition-aware cited claim recognition; typed ISBN applicability; exact
  source/resource/version provenance with lineage-bound selection paths;
  cumulative strict/recovery work accounting; and legitimate no-applicable or typed
  rejection outcomes. The command retains no decoded document or diagnostic
  markup, uses no network/rendering/script/style/persistence/write authority,
  and is qualified through a reusable offline fixture registry, property tests,
  isolated-process cohorts, and the exact installed wheel. Private compatibility
  evidence calibrates the default projected-text selection ceiling to 524,288
  characters, below the unchanged 2,097,152-character hard cap; the public
  report remains capped at 524,288 UTF-8 bytes.
  Hostile-input handling preserves same-resource fragment boundaries and role
  semantics, admits raw spine annotations only for F06-accepted itemrefs,
  recognizes recovery encodings only from real supported declarations, gates
  semantic consumers by namespace capability, and binds every post-open report
  to its source-derived lineage and selection digest. Parsed resource records,
  citations, and the selection digest share the exact resource-byte digest so
  canonical hydration rejects resource-substitution provenance.
  Fragment roles now resolve path-free against unique HTML/XHTML IDs, enforce
  earliest body and scoped exclusion positions, and select the
  highest-precedence tier with a surviving valid opening role while preserving
  valid higher-tier boundary suppression. Recovery also rejects duplicate
  declaration attributes, non-exact raw `linear` annotations, BOM-less
  UTF-16/UTF-32 signatures, treats missing or empty named-prefix bindings as
  unknown,
  requires ordered XML declaration grammar, removes duplicate recovered
  namespace/semantic/relationship/identity authority, suppresses invalid-only
  optional roles, and keeps RCDATA, templates, foreign meta, and implicit
  SVG/MathML semantics inert beneath XHTML defaults. Installed-wheel fixtures
  now assert exact hostile-case claims, warnings, dispositions, paths, and link
  usage. Recovery also keeps implied/end-tag stack operations namespace-safe,
  scans the complete bounded payload for NULs, classifies duplicate namespaces
  as malformed after independent supported-charset discovery, resolves
  namespace declarations per prefix so unrelated unique bindings remain
  authoritative, confines recovered end-tag searches to the current contiguous
  namespace region (including a matching foreign boundary), stops before HTML
  ancestors below an open foreign boundary, assigns unresolved qualified names
  opaque prefix-specific identities, resolves unqualified ends through the
  current default namespace, couples self-closing ends to starts that actually
  pushed, treats a unique empty default declaration as an HTML/no-namespace
  reset while keeping named empty prefixes opaque, and charges duplicate href
  anchors before validation.

- Added `synthetic-title-review-case-report`, a closed F10a inspection command
  that admits one current synthetic needs-review title proposal into immutable
  schema-7 case history, replays idempotently, supersedes or retires atomically,
  and remains visibly report-only and ineligible for production decisions or
  writes.

- Added `hardcover-title-proposal-report`, a disabled-by-default F09b proof of
  concept that runs only the generated `agree-empty` title case through a
  fixed, bounded Hardcover query seam. An omitted provider secret performs no
  secret access, DNS, or egress and records `provider_disabled`; unavailable
  credentials and provider failures preserve the local F06 evidence and record
  `provider_failed`. The command remains `synthetic_preview`, is never
  production-applicable, adds no `config-v1` fields, constructs the provider-
  specific `Bearer` header from a raw secret, validates the closed bounded live
  Typesense response shape, records the live contract as durable provider
  manifest v2 while retaining v1 readability, and is qualified with both a
  credential-free structural fake and one redacted exact-installed-artifact
  live run.

- Added `synthetic-title-proposal-report`, a credential-free F09a technical
  preview that runs a closed scenario through a licence-safe generated EPUB,
  the real descriptor-bound F06 child, an in-memory Audiobookshelf-shaped
  snapshot, and a deterministic fake behind the provider-neutral metadata
  port. Every valid scenario atomically persists one immutable schema-6
  `synthetic_preview` proposal set, including exact typed zero-proposal results,
  with duplicate-safe restart replay and same-lineage supersession. Production
  applicability is structurally false; the command has no live target/media,
  configuration, network, credential, AI, review, approval, apply, or write
  surface. The live F06/current-snapshot binding remains explicitly deferred.

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
