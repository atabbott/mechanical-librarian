# ADR-0013: Require clean schemas until the first production release

- Status: accepted
- Date: 2026-07-24
- Owners: project maintainers
- Supersedes: the pre-release schema-compatibility provisions of ADR-0003,
  ADR-0006, ADR-0009, and ADR-0012

## Context

The project is pre-release and has no supported historical installation base.
Schema evolution machinery would add authority and recovery promises without a
named present user need.

## Decision

Until the first production release, every database, report, artifact,
configuration, and internal-document schema accepts only absent state or its
exact current state. Earlier schemas are rejected clearly and are never
silently deleted; they need not be read, migrated, imported, or converted. A
clean rebuild may be required. Current-schema restart, replay, and crash safety
remain mandatory.

Compatibility requires a named present user need. Post-release migration and
restore work is deferred to #57; this decision grants no general migration,
repair, retention, downgrade, or destructive authority.

## Consequences

- Pre-release schema changes update the exact-current contract and prove clean
  creation plus current-schema safety, not an upgrade matrix.
- Operators retain older state offline and create a clean current state when a
  schema changes.
- The first production release must make a separate, explicit compatibility
  decision before users can rely on upgrade support.

## Supersession

The named ADR provisions remain historical context. This ADR governs every
pre-release schema, rather than only the 0.5 metadata-report increment.
