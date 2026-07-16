# ADR-0009: Bind durable EPUB opening evidence to inventory snapshots

- Status: accepted
- Date: 2026-07-16
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

ADR-0008 deliberately made opening extraction transient and caller-addressed.
F07b must resume and inspect the same work after restart without accepting a
media path from the caller, recontacting Audiobookshelf, rewriting immutable
history, or allowing a stale worker to publish.

## Decision

1. A successful Audiobookshelf inventory reconciliation may atomically attach
   an application-private `InventoryFileBinding` to an immutable snapshot. The
   binding contains a canonical file index, opaque file identity, media kind,
   material mount-configuration digest, and logical mount-relative reference.
   Each completed reconciliation also publishes an immutable binding-set
   revision, including explicit absence, and moves a separate current pointer.
   Historical bindings remain immutable but cannot leak back into selectors or
   later reconciliation. Bindings are never part of CLI output, ordinary logs,
   or audit summaries.
2. Inventory inspection emits only versioned opaque target and snapshot
   references, file index, and media kind. Existing snapshots without bindings
   emit no selector and are not reconstructed from caller input or the network.
   A private immutable locator index covers every freshly reconciled snapshot
   so wrong-target, stale, and non-EPUB requests can fail distinctly without
   scanning or exposing the inventory.
3. Durable work is accepted only after the selector resolves to exactly one
   current binding and a bounded no-follow source preflight establishes a
   private source generation. The compatibility identity binds the snapshot,
   binding, source generation, F07a extractor/parser/recovery/projection/
   normalization/vocabulary/selection/schema contracts, limits, and material
   configuration. Strict or recovered parser mode is a result, not a prework key.
4. Work and attempts are persisted before child extraction. Claims are
   retry-bounded, lease-owned, fenced, cancellable, and recover expired workers.
   Operational resource, timeout, cancellation, and infrastructure failures
   remain attempt facts and never become evidence.
5. One SQLite transaction revalidates the current binding, source generation,
   work identity, attempt, owner, live lease, fence, and cancellation; inserts
   or verifies immutable canonical evidence; advances a monotonic current
   pointer; writes a checkpoint and append-only event; and completes the job.
   An identity/payload mismatch is corruption. Reverse replay reuses history
   without moving current applicability backwards.
6. Current applicability advances only for the newest accepted lane revision.
   The durable request records initiating authority; immutable events record
   actor, attempt, fence, and state transition. Claim admission enforces the
   configured parser capacity, final lease exhaustion terminalizes, and an
   eligible request can resume without selector redelivery.
7. Migration 8 is additive and forward-only. Downgrade is restore-only.

## Consequences

The installed CLI can execute once and reuse the same evidence identity after
restart, while source or governing changes create distinct immutable history.
The application database now contains private logical media locations and
bounded cited evidence, so its backup, retention, and access controls carry the
same sensitivity as library metadata. The feature remains report-only: it adds
no service, media, metadata, cover, or embedded-file write authority.

## Validation

Validation covers binding projection partitions, selector redaction, identity
mutation, every genuine schema prefix, restart reuse, controlled invalidation,
reverse replay, expired leases and stale fences, atomic rollback, installed
wheel execution, and aggregate-only disposable private qualification.
