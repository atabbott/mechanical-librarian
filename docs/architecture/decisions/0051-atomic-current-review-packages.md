# ADR-0051: Publish review packages after projection

- Status: accepted
- Date: 2026-08-14
- Owners: project maintainers

## Context

Review preparation persists immutable reports, proposals, cases, and projection
records in separate stages. A first generation must not become a browse result
until that complete package is coherent, and a refresh must not replace an
already browseable package with an intermediate successor.

## Decision

SQLite retains the immutable records and their existing operational lineage
pointers. A separate `current_review_packages` pointer is the only source for
Library and Attention reads. Pipeline work updates it in the same transaction
that marks its projection complete after checking the exact staged report,
proposal, case, and outcome. Direct foreground report/admission operations
continue to publish their complete report-only or admitted package directly.
The projection opens that transaction before calculating its real output;
staged shadow outcomes and their derived automatic decisions or safety changes
remain invisible until the same commit completes the work and package cutover.
Provider-lane outcomes are immutable report-local evidence. Once every
configured lane is terminal, a sibling technical, no-match, disabled, or
over-bound status cannot discard coherent evidence or stop proposal and
projection. A failure before a complete report exists remains separate
Library-only operational state.

Before package cutover, a claimed projection may use the exact staged lineage
and current report only to persist its immutable shadow outcome. That limited
internal authority does not make the staged case owner-visible or available to
owner decisions or field effects.

## Consequences

Rollback, restart, stale work, and projection failure leave the old package
pointer unchanged. A clean schema is required; older databases are rejected
under the existing no-migration policy. Library and Attention counts, lenses,
search, and pagination share the same pointer and one ordered browse window.
