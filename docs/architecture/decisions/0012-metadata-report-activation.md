# ADR-0012: Activate one neutral metadata-report runtime

- Status: accepted
- Date: 2026-07-23
- Owners: project maintainers
- Supersedes: ADR-0003, ADR-0009, and the migration-specific provisions of ADR-0006;
- Superseded in part by: ADR-0013 (repository-wide pre-release schema policy)

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
open and retain a no-follow directory chain from the filesystem root to the
owner-only database parent. Every entry is protected by root/process ownership
and non-writable permissions, or by sticky-directory ownership. Hold an
exclusive owner-directory binding for the repository lifetime and open SQLite
only through a verified private hard-link alias to the already-opened database
inode. The opened connection's `main` path is independently reopened with
no-follow access and must have that exact device/inode identity; this proof
does not depend on process-wide descriptor-number allocation. Before
transaction writes and again at the final pre-commit boundary, rewalk the
absolute chain from the current root and require every directory,
the configured database name, and configured-name sidecar absence to match.
The private alias confines SQLite-created journal artifacts to the owned
binding.

The directory lock coordinates supported repository instances; it is not a
security boundary. Linux discretionary access control supplies the exclusion
invariant against other unprivileged user identities. Production therefore
runs under a dedicated identity with no untrusted same-identity processes.
Linux has no unprivileged primitive that atomically couples an arbitrary
absolute-path rename exclusion to SQLite commit. A detectable same-identity
parent move at either publication boundary fails and rolls back, but hostile
same-identity or privileged mutation is outside the supported guarantee.

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

## Later evolution (2026-07-26)

The 0.5 increments later added `metadata-report-get`, richer v2 evidence,
cover-artifact storage and requests, and the third report-bound artifact
reference table. Those current-runtime features amend the 0.4 baseline; they
do not describe its original decision. ADR-0013 remains the repository-wide
authority for pre-release clean-schema policy.

## Supersession note

ADR-0013 generalizes this checkpoint's clean-state rule to every pre-release
schema and supersedes this ADR's increment-specific migration policy.
