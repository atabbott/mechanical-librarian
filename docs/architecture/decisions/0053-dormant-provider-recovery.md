# ADR-0053: Dormant provider recovery eligibility

- Status: accepted
- Date: 2026-08-14
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Provider failures must not repeatedly contact a failing provider or leak a
credential/configuration condition into Activity. F24 authorizes one durable,
provider- and configuration-scoped canary before bounded sibling fanout.

## Decision

Persist a closed provider-connection, adapter, and scope recovery state. A
transient or rate-limited failure waits for a bounded delay; authentication,
authorization, and configuration failures are dormant until the operator
increments `provider_recovery_generation` in `config-v6` and restarts. The
store atomically leases one due canary for five minutes. Exact job and canary
ownership is renewed from a fresh clock between target preflight, the bounded
60-second provider call, publication, and each proposal, admission, and
projection stage; no individual credential-bearing or local transaction stage
may silently consume the next stage's lease. The existing preparation supervisor is its
only wake, clock, drain, and shutdown owner; it has no independent queue or
endpoint. A successful canary may process remaining durable members in that
exact scope once. Canary success first commits an explicit durable fanout phase;
each sibling then receives its own lease, so restart can continue partial fanout
without extending a scope lease or repeating the canary. A shared failure
requeues the canary, publishes no successor, has zero fanout,
and takes Retry-After when supplied before bounded backoff. Target and package
drift close safely without a provider call.
Staleness retires only the affected member; another exact-current member may
become the canary. If package cutover commits before fanout authorization, the
persisted predecessor-to-successor lineage recognizes that completed canary on
restart and authorizes fanout without another provider request.

Health rows are exact-current and events are append-only. Existing state must
match this exact schema; there are no migrations or imports.

Startup first drains committed package-cutover outbox rows into durable exact
jobs, then rejects a persisted generation newer than configuration and advances
every older row. A delivered job at the configured generation is the durable
provenance that authorizes an authentication or authorization wake; without it
the row remains dormant. Checking
can only transition through strict lease completion. Terminal health
observations are atomic and idempotent: checking, fanout, and dormant take precedence,
and a dormant observation replaces waiting once without rescheduling repeated
transient or rate-limited observations.

The recovery database is created atomically only when its named objects are
absent; unrelated SQLite objects are preserved, while partial or mismatched
recovery objects fail closed. Its one shared connection serializes every query
and transaction across worker threads and closes after the supervisor drains.

## Consequences

### Positive

- A restart and competing manual/background workers cannot duplicate canary or
  sibling egress.
- Operators have one explicit, documented recovery wake mechanism.

### Negative

- A claimed canary is bounded to one exact-current provider scope.

### Neutral or deferred

- Activity exposes aggregate recovery state only; there is no owner retry
  control, bulk recovery, or target metadata write.

## Validation

Unit tests cover bounded delays, dormancy, all-state generation reconciliation,
idempotent observation, strict lease completion, restart, stale leases, and
schema corruption.
