# Glossary

## Adapter

Implementation that translates an external service or provider into a contract
owned by the application.

## Apply attempt

One bounded attempt to execute an immutable apply plan and verify the result.

## Apply plan

Validated set of field changes for one target and one expected fingerprint.

## Audit event

Append-only user-facing record of a meaningful product decision, action, or
state transition.

## Candidate

Normalized external or derived source of one or more possible metadata values.

## Capability

Field or action that a configured service adapter supports through an approved
integration path.

## Circuit breaker

State that blocks production writes after unsafe or repeated failures while
allowing safe read-only work to continue.

## Decision

AI, human, or deterministic conclusion about one proposal. A decision is not
itself an apply authorization.

## Disposition

Policy result for current work: `auto_apply`, `needs_review`,
`closed_rejected`, or `unsupported`.

## Evidence

Immutable bounded observation supporting or contradicting a candidate or field
proposal.

## Fingerprint

Versioned digest of canonical observed item state used for change detection and
optimistic concurrency.

## Item snapshot

Immutable normalized observation of an external library item at a point in
time.

## Job

Durable, retryable unit of background work with leases and a deduplication key.

## Proposal

Candidate value for one field on one item snapshot with evidence references.

## Reviewer

Replaceable AI backend that receives bounded evidence and returns
schema-constrained recommendations without production authority.

## Review case

Current decision surface grouping field proposals for one target. Historical
cases remain immutable when a new fingerprint supersedes them.

## Shadow mode

Mode in which the system makes and records complete decisions but production
writes are disabled. Used to evaluate a reviewer or policy version.

## Target

Stable internal identity for one adapter-scoped external library item.

## Verification

Independent read-back comparison proving whether an apply plan produced the
expected service state.
