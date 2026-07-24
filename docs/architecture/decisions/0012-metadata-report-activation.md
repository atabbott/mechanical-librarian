# ADR-0012: Activate one neutral metadata-report runtime

- Status: accepted
- Date: 2026-07-23
- Owners: project maintainers
- Supersedes: ADR-0003, ADR-0009, and the migration-specific provisions of ADR-0006;
  pre-release schema policy superseded by ADR-0013

## Context

ADR-0010 chose a clean 0.4 neutral evidence baseline. ADR-0011 consolidated
EPUB parsing into one isolated inspector. The accumulated pre-release runtime
still exposed inventory, job, preview, proposal, and provider-work machinery
and a multi-table migration history that the baseline explicitly rejected.

## Decision

Activate one installed `metadata-report` observation command and its
configuration-free `metadata-report-get` exact-ID/current-scope replay surface,
then remove every superseded runtime surface in the same change. The application
directly composes one scoped current-item read, the isolated EPUB inspector,
explicit per-library provider references, one fresh credential-bearing Hardcover
GraphQL metadata request per enabled supported provider, then up to five
separate credential-free bounded cover request chains for admitted candidate
descriptors, final target/media currentness, and whole-report publication.

Persist exactly three application tables: immutable canonical metadata reports,
their separate current pointers, and immutable report-bound cover-artifact
references. The reference table authorizes preview reopening and supplies the
exact artifact liveness set used for safe orphan cleanup. Initialize an absent
database or open only the exact schema required by the installed increment.
`metadata-report-v1`,
prior 0.5 increment schemas, and all earlier development schemas fail closed
before mutation. There is no migration, import, preservation, compatibility
parser, ledger, downgrade, recovery, or repair framework. Exact-ID and
current-scope replay remain supported only within the exact current schema.

The v2 report preserves local evidence occurrence order and records source
capability state separately from aggregate field state. Target metadata is
title-only; EPUB remains the complete local source. Oversized or unsafe source
sentinels are retained only as typed non-canonical evidence. It retains the
closed field vocabulary (title/subtitle, creator/contributor roles,
series/sequence, ISBN, date, publisher/imprint, description, language, and
contextual subject) plus bounded source/DOM occurrence and rear-spine
provenance, rather than arbitrary source text or paths.

Use SQLite rollback-journal `DELETE` mode for this sole baseline. WAL,
migration-ledger, forward-migration, compatibility, recovery, and repair
machinery are outside the accepted contract. On supported Linux deployments,
open and retain a no-follow directory chain from the filesystem root to the
owner-only database parent. Every entry is protected by root/process ownership
and non-writable permissions, or by sticky-directory ownership. Hold an
exclusive owner-directory binding for the repository lifetime and open SQLite
only through a verified private hard-link alias to the already-opened database
inode. Before transaction writes and again at the final pre-commit boundary,
rewalk the absolute chain from the current root and require every directory,
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
neither 0.4 nor any 0.5 increment reads or transforms it. A clean database plus
supported report regeneration is the only recovery path. Review, decisions,
automation, AI, apply, production writes, retention, pruning, purge, repair, and
broader migration remain future work.

## Supersession note

ADR-0013 generalizes this checkpoint's clean-state rule to every pre-release
schema and supersedes this ADR's increment-specific migration policy.
