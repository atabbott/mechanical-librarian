# Application service contracts

## Current boundary

The current 0.10 runtime owns application contracts for bounded evidence
observation and durable report publication, catalogue refresh and inspection,
foreground manifest batches, deterministic ProposalSet creation and reopening,
exact-current ReviewCase admission and reopening, and qualification-only
shadow-review execution and persistence.

Each contract exchanges typed, bounded application or domain values. Adapters
contain target/provider payloads, transport clients, SQLite rows, filesystem
paths, and credentials; the composition root selects the concrete adapter and
credentials. The domain does not depend on those implementations.

## Contract families

- Evidence reporting reads one selected current item, isolates EPUB inspection,
  records configured provider and cover observations, verifies currentness, and
  publishes one immutable report with its report-bound artifacts.
- Catalogue and batch contracts publish or inspect a bounded current catalogue,
  create and inspect immutable manifests, and serially execute the selected
  report work in the foreground.
- Proposal and review contracts reopen immutable report evidence, create or
  reopen deterministic ProposalSets, and admit or reopen an immutable ReviewCase
  only at the documented exact-current boundary.
- Qualification-only reviewer contracts preview, request, inspect, or
  explicitly reevaluate one isolated shadow-review attempt through the
  application-owned runner and repository contracts, without exposing target or
  provider credentials to the reviewer.

## Bootstrap authentication

Reviewer authentication is not an application-owned contract. Bootstrap loads
the declared qualification profile and invokes `CodexShadowReviewer.auth`
directly. The application-owned shadow-review contracts begin with execution
and durable request/attempt/dispatch/outcome persistence after that separate
authentication path.

The supported command and persistence details are authoritative in the
[operations guides](../operations/metadata-report.md),
[reference contracts](../reference/compatibility.md), and
[security model](security.md), not in this overview.

## Authority limits

These contracts remain synchronous and bounded. They provide no target or media
write, decision, approval, apply, generic provider framework, background
executor, queue, scheduler, automatic retry, or cache authority. The
qualification-only reviewer returns advice only; it cannot grant normal
activation or mutation authority.
