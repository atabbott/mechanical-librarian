# ADR-0012: Activate one neutral metadata-report runtime

- Status: accepted
- Date: 2026-07-23
- Owners: project maintainers
- Supersedes: ADR-0003, ADR-0009, and the migration-specific provisions of ADR-0006

## Context

ADR-0010 chose a clean 0.4 neutral evidence baseline. ADR-0011 consolidated
EPUB parsing into one isolated inspector. The accumulated pre-release runtime
still exposed inventory, job, preview, proposal, and provider-work machinery
and a multi-table migration history that the baseline explicitly rejected.

## Decision

Activate one installed `metadata-report` command and remove every superseded
runtime surface in the same change. The application directly composes one
scoped current-item read, the isolated EPUB inspector, explicit per-library
provider references, one fresh request per enabled supported provider, final
target/media currentness, and whole-report publication.

Persist exactly two application tables: immutable canonical metadata reports
and their separate current pointers. Accept only a new database or that exact
schema. Provide no migration or compatibility path for pre-0.4 development
state.

Use SQLite rollback-journal `DELETE` mode for this sole baseline. WAL,
migration-ledger, forward-migration, compatibility, recovery, and repair
machinery are outside the accepted contract. On supported Linux deployments,
hold an exclusive owner-directory binding for the repository lifetime and open
SQLite only through a verified private hard-link alias to the already-opened
database inode. Configured-name recovery sidecars are rejected; the private
alias confines SQLite-created journal artifacts to the owned binding.

## Replacement scope

ADR-0003 remains the historical rationale for selecting SQLite, but this ADR
replaces its WAL, durable-job, migration, and prior-schema validation policy.
The dependency-direction and composition-root decisions in ADR-0006 remain
accepted; only its versioned-migration directory and runtime migration
execution provisions are replaced. ADR-0009 is superseded in full.

## Consequences

The installed surface is small, neutral, synchronous, and report-only. Exact
content replay supplies stable identity without retry/cache/work machinery.
Operators who want to retain older development state must keep it offline;
0.4 neither reads nor transforms it. Review, decisions, automation, AI, apply,
production writes, and retention remain future work.
