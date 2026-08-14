# ADR-0053: Dormant provider recovery eligibility

- Status: accepted
- Date: 2026-08-14
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Provider failures must not repeatedly contact a failing provider or leak a
credential/configuration condition into Activity. Recovery execution is not
yet an authorized product capability.

## Decision

Persist a closed provider-connection, adapter, and scope recovery state. A
transient or rate-limited failure waits for a bounded delay; authentication,
authorization, and configuration failures are dormant until the operator
increments `provider_recovery_generation` in `config-v6` and restarts. The
store may atomically lease one due canary, but has no provider, replay, fanout,
or write call.

Health rows are exact-current and events are append-only. Existing state must
match this exact schema; there are no migrations or imports.

Startup rejects a persisted generation newer than configuration and advances
every older row atomically through its domain generation transition. Checking
can only transition through strict lease completion. Terminal health
observations are atomic and idempotent: checking and dormant take precedence,
and a dormant observation replaces waiting once without rescheduling repeated
transient or rate-limited observations.

## Consequences

### Positive

- A restart and competing workers cannot claim more than one eligible scope.
- Operators have one explicit, documented recovery wake mechanism.

### Negative

- A claimed canary remains non-executing until the activation increment.

### Neutral or deferred

- Activity exposes only aggregate recovery state; provider execution, replay,
  fanout, and target writes remain outside this report-only increment.

## Validation

Unit tests cover bounded delays, dormancy, all-state generation reconciliation,
idempotent observation, strict lease completion, restart, stale leases, and
schema corruption.
