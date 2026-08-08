# 0035 — Persist non-authoritative policy Preview outcomes

## Status

Accepted for F19a #478.

## Context

The provider-independent F19 decision set is deterministic for one exact
ReviewCase, but its operator command is deliberately read-only and ephemeral.
Policy qualification needs durable observations from ordinary background case
progression without changing which fields need a human choice or implying that
an observed recommendation is a decision.

## Decision

Metadata policy posture is a closed `review | preview` state whose default is
`review`. Entering `preview` is an authenticated, CSRF-protected owner command.
It grants no authority. While Preview is active, the existing projection stage
computes the exact F19 decision set for a newly eligible exact-current case and
atomically persists one immutable `shadow-outcome-set-v1`.

The outcome binds the exact case, report, proposal set, target fingerprint,
resolved policy/profile/generation, source decision set and catalogue, fixed
decision policy, policy category/option/destination, action, candidate,
citations, bibliographic combinations, rule, disposition, and qualification
candidate identity. Its closed dispositions are `candidate`, `keep_current`,
`ignore`, `manual`, `abstention`, `conflict`, and `unsupported`. Replay of the
same inputs returns the same canonical identity and retains the first event
time.

Currentness and policy generation are revalidated inside the publishing
transaction. A stale evaluation publishes nothing and requeues the already
persisted work row through the existing pipeline seam, including when that sole
row is running the obsolete projection. It does not create another scheduler
or queue.

Authenticated history and the policy notebook expose Preview outcomes
separately from human decisions and model advice. The sample accepts at most 50
requested records and enforces a one-MiB encoded response bound even when every
field carries the maximum 256 citations and 256 combinations. Malformed
persisted outcome or history state fails the whole read without a partial
projection.

Leaving Preview is impact preview only in this increment. Its response always
has `can_confirm: false`; qualified decision-only activation belongs to #479.

## Consequences

Preview creates an auditable qualification corpus under normal report-only
operation while leaving Library and Attention membership unchanged. Entering
Preview immediately confirms that authority remains `none`; it does not create
an automatic or human decision, reduce a queue, schedule an apply, construct a
target client, or write target metadata, covers, media, or artifacts.

The append-only outcomes increase private application-state retention and have
no selective purge or export operation. Pre-release ADR-0013 applies: absent or
exact-current schema only, with no migration, import, or compatibility reader.
