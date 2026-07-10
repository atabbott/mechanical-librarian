# Delivery Slices

## Purpose

Mechanical Librarian is delivered through thin end-to-end slices that prove a
usable product outcome across the required work packages. WP0-WP9 remain
capability ownership buckets and parent issue groups; they are not chronological
phases and are not completed wholesale before later work begins.

Each slice must be demonstrable through a real executable surface, use synthetic
or isolated services, include its required failure behavior, and leave the
repository releasable. Work that does not contribute to the active slice remains
in its owning work package until needed.

## Slice 0: executable foundation

Prove that a clean checkout builds, installs, runs checks, and starts the empty
CLI through the composition root.

Cross-cutting ownership:

- WP0: package, dependency lock, checks, CI, release foundations;
- WP8: the earliest process and packaging constraints where needed.

Exit criteria:

- the typed package installs from the committed lockfile;
- all executable entry points delegate through `mechanical_librarian.bootstrap`;
- architecture, package, documentation, and privacy checks pass from a clean
  checkout; and
- no network, persistence, credential, media, or production-write behavior is
  introduced.

## Slice 1: read-only inventory

Connect to a synthetic Audiobookshelf service, load validated configuration,
discover one library and item, persist the item and immutable snapshot, and
expose the result through a minimal CLI or HTTP diagnostic surface.

Cross-cutting ownership:

- WP1: target identity, fingerprints, configuration, and ports;
- WP2: minimal SQLite repository, migration, and durable job path;
- WP3: read-only Audiobookshelf adapter;
- WP8/WP9: minimal health and inventory surface needed to demonstrate the slice.

Exit criteria:

- duplicate discovery and process restart do not create duplicate item work;
- configuration and authentication failures are explicit and non-mutating;
- unsupported service behavior produces a typed capability result; and
- a user can inspect discovered inventory without shell-level database access.

## Slice 2: local evidence to review case

For one discovered synthetic EPUB, extract bounded evidence, produce one or more
field proposals, resolve deterministic policy, persist a snapshot-aware review
case, and expose it through a minimal review API or CLI projection.

Cross-cutting ownership:

- WP1: review-case and notice lifecycle;
- WP2: evidence and review-case persistence;
- WP4: bounded EPUB extraction;
- WP5: candidate and policy resolution;
- WP9: minimal decision-first review projection.

Exit criteria:

- malformed or hostile input fails within declared resource bounds;
- evidence provenance survives normalization and proposal creation;
- no-op, rejected, unsupported, and unresolved outcomes remain distinct; and
- restart preserves the same authoritative review case without browser-local
  state.

## Slice 3: guarded human-approved apply

Approve one safe field-level change through the server-authoritative decision
contract, build an immutable apply plan, back up the current value, apply through
a fake or isolated Audiobookshelf API, read back, and verify the result.

Cross-cutting ownership:

- WP1/WP5: decision authorization and deterministic policy;
- WP2: audit, backup references, jobs, and idempotency;
- WP7: guarded apply, verification, and rollback;
- WP9: minimal review and approval mutation surface.

Exit criteria:

- stale target state blocks the write;
- retries cannot duplicate or widen the change;
- every mutation records authorization, before state, after state, and
  verification; and
- verification failure produces safe rollback or a visible write-disabled state.

## Slice 4: AI shadow review

Stage only bounded evidence for the isolated reviewer, receive a
schema-constrained recommendation, validate its citations and proposal IDs, and
compare it with deterministic and human decisions without granting write
authority.

Cross-cutting ownership:

- WP6: reviewer process, schemas, validation, and evaluation;
- WP5: policy interpretation of reviewer output;
- WP8: internal principal separation;
- WP9: reviewer reasoning and diagnostic projection where useful.

Exit criteria:

- the reviewer cannot access service secrets, the main database, backups, or
  production media;
- malformed, timed-out, or hostile output becomes a typed safe state;
- AI output alone cannot create decision authorization; and
- replayable synthetic cohorts expose disagreement and systematic failure modes.

## Slice 5: unattended single-container operation

Package the proven workflows into the supported container, process events and
reconciliation durably, expose the bounded review queue, and exercise restart,
upgrade, backup, restore, diagnostics, and circuit-breaker behavior.

Cross-cutting ownership:

- all work packages, limited to behavior proven by preceding slices.

Exit criteria:

- one container, one persistent volume, read-only media mounts, and one HTTP port
  support the normal deployment;
- process death, duplicate events, and persistent failures remain bounded;
- writes can be disabled independently of discovery and diagnosis; and
- backup, restore, upgrade, container isolation, and end-to-end tests pass.

## Planning rules

- The active delivery slice determines execution order across work packages.
- Issues retain their WP parent and area labels but also link the active slice or
  target release they advance.
- A slice may use deliberately narrow implementations; later issues extend them
  without bypassing the established contracts.
- A work-package parent is complete only when all of its owned outcomes are
  complete, but its incomplete status does not block slices that need only a
  bounded subset.
- No slice may defer its safety, recovery, observability, documentation, or test
  obligations to a later slice.
