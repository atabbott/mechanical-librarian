# ADR-0012: Activate one neutral metadata-report runtime

- Status: accepted
- Date: 2026-07-23
- Owners: project maintainers
- Supersedes: ADR-0009

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

## Consequences

The installed surface is small, neutral, synchronous, and report-only. Exact
content replay supplies stable identity without retry/cache/work machinery.
Operators who want to retain older development state must keep it offline;
0.4 neither reads nor transforms it. Review, decisions, automation, AI, apply,
production writes, and retention remain future work.
