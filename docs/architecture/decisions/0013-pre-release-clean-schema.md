# ADR-0013: Require clean schemas until the first production release

- Status: accepted
- Date: 2026-07-24
- Owners: project maintainers
- Supersedes: the pre-release schema-compatibility provisions of ADR-0003,
  ADR-0006, ADR-0009, and ADR-0012

## Context

The project is pre-release and has no supported historical installation base.
Schema evolution machinery would add authority and recovery promises without a
named present user need. The retained 2,022-work first-boot experiment later
created one such need: repeating hours of provider observations merely to add
reconstructible read indexes would discard valuable canonical development
state without improving its meaning.

## Decision

Until the first production release, every database, report, artifact,
configuration, and internal-document schema accepts only absent state or its
exact current state. Earlier schemas are rejected clearly and are never
silently deleted; they need not be read, migrated, imported, or converted. A
clean rebuild may be required. Current-schema restart, replay, and crash safety
remain mandatory.

A persisted-schema change may instead deliver one closed, code-defined adoption
from its exact immediate predecessor when every mutation is confined to
reconstructible derived objects. The plan belongs to that schema change's issue
and pull request, names the complete predecessor and result contracts, creates
and verifies a private backup, validates canonical state before and after, and
runs only through explicit offline owner action. Ordinary startup does not run
it. Runtime SQL, plugins, configuration-supplied operations, canonical row
conversion, general migration machinery, repair, import, and downgrade remain
out of scope.

Maintainer qualification continuity is a separate, narrower authority. A
code-defined bridge may read one named complete private test-state clone,
create and reverify an independent private backup, and reconstruct a fresh
exact-current qualification database through current domain derivation. The
plan must bind one exact source and target contract, preserve only evidence
whose meaning current code accepts, explicitly invalidate observations missing
current provenance, and discard every downstream decision or projection. It
never mutates the retained source, runs at startup, contacts a provider,
accepts configuration-supplied operations, or establishes product compatibility.

The #800 index change predates this revision and is the sole retrospective
exception: #805 delivers and qualifies its adoption separately because #800 was
already merged when the retained experiment established the preservation need.
Later schema changes do not inherit that exception. #802 follows the ordinary
same-change rule: its `review-field-facts-v1` adoption accepts only the frozen
exact #805 schema, adds reconstructible derived field-fact objects and delayed
eligibility state, and atomically replaces that predecessor's historical
`updated_at` claim index with the current ordinal and due-promotion indexes. It
does not adopt #826's immediate predecessor, which already has the field-fact
objects and remains clean-rebuild required.

Compatibility requires a named present user need. Post-release migration and
restore work is deferred to #57; this decision grants no general migration,
repair, retention, downgrade, or destructive authority.

## Consequences

- Pre-release schema changes update the exact-current contract, prove clean
  creation plus current-schema safety, and declare either `clean rebuild
  required` or one same-change exact-predecessor derived-object adoption.
- Operators retain older state offline and create a clean current state when a
  schema changes unless that exact predecessor has a delivered adoption plan.
- The first production release must make a separate, explicit compatibility
  decision before users can rely on upgrade support.

## Supersession

The named ADR provisions remain historical context. This ADR governs every
pre-release schema, rather than only the 0.5 metadata-report increment.
Issue #830 retires the historical `review-field-facts-v1` plan and introduces
`review-owner-decision-actions-v1`, a literal exact-predecessor adoption that
rebuilds only the derived owner-actionability fact and trigger. Its literal
target is checked against the installed build before any filesystem access;
future schema changes therefore require a new reviewed plan or a clean rebuild.

Issue #872/#873 takes the clean-rebuild branch. Its reviewer-access and owner
state objects cannot update the canonical historical state targeted by the
three previous exact-predecessor plans, so those plans and the now-empty public
command are retired. This does not weaken ordinary absent-or-exact-current
startup or create a generic conversion seam.

Issue #938 restores only the maintainer qualification boundary above. Its
`retained-library-v26-to-v31` plan reads the exact retained #744 checkpoint,
re-derives current reports, invalidates provider observations that predate
mandatory work-dispatch lineage, their dependent covers, and artifact-bearing
cover references whose complete private store was not verified, and creates
fresh report-only current state. The earlier `schema-adopt` plans remain
retired.

Issue #869 also takes the clean-rebuild branch. Its exact-current application
schema adds the derived `review_preparation_presentation` object, including its
canonical contiguous first-terminal completion sequence and guarded persisted
sequence count, Settings-revision-bound deferred enrollments, and exact-cycle
recovery high-water markers used for authoritative recent ordering, and supplies no
migration, adoption, repair, or compatibility path. Absent state is created at
bootstrap; any earlier application schema is rejected before mutation.
