# ADR-0008: Freeze the versioned title proposal contract

- Status: accepted
- Date: 2026-07-14
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The 0.2 technical preview introduces the first durable proposal and review-case
path. It compares a current Audiobookshelf title, bounded F06 package evidence,
and one provider title, but it must remain deterministic across runtime Unicode
updates, restart, replay, missing inputs, and provider substitution. A
comparison key alone cannot identify an immutable proposal: exact retained
values can change without changing that key. Successful-value-only identities
also cannot represent the required typed zero-proposal sets because missing,
disabled, failed, empty, and unsupported paths have no evidence value or
provider candidate to bind.

The provider is a corroborating source across a credential and network trust
boundary. It must not become the retained proposal value or gain decision or
write authority. The detailed Hardcover query, field allowlist, authentication
revision, and retained-evidence lifecycle remain pre-Ready work for F09b.

## Decision

Adopt `title-normalization-v1` as the 0.2 comparison algorithm. It uses the
complete byte-exact Unicode 16.0.0 source bundle and conformance data recorded
in the compatibility contract, applies the documented NFC, whitespace, default
case-folding, and final NFC sequence, and never uses host-runtime Unicode data.
Changing the source bundle or algorithm requires a new normalization version.

Retain the bounded original successful F06 package title in every eligible
proposal. Provider spelling and comparison keys are never proposal values. A
provider may corroborate only through the application-owned, report-only
provider port and grants no human, AI, or write authority.

Identify every proposal set using an ordered `proposal-input-slot-v1` identity
for the current-title, F06-title, and provider-title roles. Each slot binds its
target/snapshot/field scope, role, and contract version plus exactly one tagged
payload:

- a successful exact value and immutable evidence or candidate identity; or
- the exact typed observation, capability, execution, or candidate-count
  outcome for that scope.

Absence and failure never use invented values, nonexistent candidates, or a
shared sentinel. The current-title slot preserves `PRESENT`, `MISSING`, `NULL`,
`EMPTY`, `MALFORMED`, and `UNSUPPORTED`. Only `EMPTY` permits fill-empty policy;
`PRESENT` is partitioned into equivalent and materially different values; the
other four states produce distinct typed zero-proposal sets. Provider disabled,
unsupported, incompatible, failed, successful-empty, and no-match outcomes are
likewise distinct.

The immutable proposal-set identity binds all three ordered slot identities,
the normalization version, and the policy version. Identical replay resolves
to the same semantic set identity. Any state, exact value, evidence identity,
scope, normalization, or policy change creates a superseding set without
mutating prior sets, proposals, or cases. F10 may create a case only from the
current set containing exactly one current `needs_review` proposal.

## Consequences

### Positive

- Zero-proposal sets have constructible, deterministic identities without
  collapsing distinct failure or absence states.
- Exact-value drift supersedes history even when normalization hides it.
- Unicode behavior is reproducible across supported runtimes and upgrades.
- Provider substitution preserves comparison behavior without making provider
  identity a policy fact or expanding provider authority.

### Negative

- The implementation must ship and validate a frozen Unicode data bundle.
- Persistence and fixtures must model every required slot and typed outcome,
  increasing schema and test-matrix size.
- A Unicode or policy revision requires explicit versioning and supersession
  rather than an in-place behavioral change.

### Neutral or deferred

- The exact Hardcover transport fields, authentication revision, and private
  evidence lifecycle remain governed by F09b and are not frozen here.
- Providerless proposal policy, additional fields, covers, AI review, human
  decision, and apply behavior remain outside the 0.2 preview.

## Alternatives considered

### Use host-runtime Unicode normalization

Rejected because runtime Unicode upgrades could change comparison results and
proposal identity without a policy-version change.

### Identify sets only by normalized successful values

Rejected because exact retained values and evidence can drift under the same
comparison key, and zero-result paths do not have successful values to bind.

### Omit proposal sets when no proposal is produced

Rejected because it loses typed outcome history, deterministic replay, and the
ability to explain why F10 correctly created no case.

## Validation

- Run the full Unicode 16.0.0 normalization conformance corpus and golden
  algorithm fixtures without consulting host Unicode data.
- Exercise all six canonical current-title states; partition `PRESENT` into
  equivalent and different values and prove only `EMPTY` can enter fill policy.
- Exercise successful and every required typed F06/provider zero-result slot,
  including provider disabled, unsupported, incompatible, failed, empty, and
  no-match outcomes.
- Prove identical replay reuses set and case identities, while any slot state,
  value, evidence, scope, normalization, or policy change supersedes without
  rewriting history.
- Prove zero-proposal sets never create F10 cases and no 0.2 path invokes an AI
  reviewer, human decision, or write surface.
