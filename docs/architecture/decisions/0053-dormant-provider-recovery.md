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
store atomically leases one due provider cohort for five minutes. Exact job and
canary ownership is renewed from a fresh clock between target preflight, the bounded
60-second provider call, publication, and each proposal, admission, and
projection stage; no individual credential-bearing or local transaction stage
may silently consume the next stage's lease. The existing preparation supervisor is its
only wake, clock, drain, and shutdown owner; it has no independent queue or
endpoint. A successful canary may process remaining durable members in that
finite provider cohort once. Canary success first commits an explicit durable
fanout phase;
each sibling then receives its own lease, so restart can continue partial fanout
without extending the provider lease or repeating the canary. A shared failure
requeues the canary, publishes no successor, has zero fanout,
and takes Retry-After when supplied before bounded backoff. Target and package
drift close safely without a provider call.
Staleness retires only the affected member; another exact-current member may
become the canary. If package cutover commits before fanout authorization, the
persisted predecessor-to-successor lineage recognizes that completed canary on
restart and authorizes fanout without another provider request.

Health rows are exact-current and events are append-only. Existing state must
match this exact schema; there are no migrations or imports.

Startup first performs a read-only compatibility check that rejects a persisted
generation newer than configuration before either database changes or outbox
delivery. It then drains committed package-cutover outbox rows into durable exact
jobs and advances
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

## Amendment: quota-aware recovery pacing (2026-08-27)

The application now owns one closed recovery classifier used by both initial
report qualification and retry successors. Google Books accepts only its exact
closed API reason vocabulary; Hardcover accepts only its exact current
`RateLimit` response grammar or exact legacy remaining/reset pair.
Exact burst evidence produces a bounded short wait. Exact daily exhaustion
waits until the provider's documented reset boundary: Pacific midnight for
Google Books and UTC midnight for Hardcover. Missing, malformed,
contradictory, or unknown evidence remains generic rate limiting and cannot
claim either distinction.

Google Books, Hardcover, and Open Library each receive one shared one-second
request-start pacer across foreground and recovery work inside a composed run.
A burst- or daily-quota-deferred failure neither increments recovery health nor
spends a job execution attempt. This amendment changes no recovery-database
shape. The two new persisted report diagnostics are governed by ADR-0017 and
require the `metadata-report-v29` clean rebuild under ADR-0013.

## Amendment: decided-field quota dispatch (2026-09-01)

Once exact-current recovery work exists after provider quota pressure, its
durable job records the closed, application-classified dispatch reason:
missing independent corroboration, zero provider evidence, settleable
disagreement, then blanket scan. Claims order those classes and retain the
former `member_id` tie-break. The persisted reason is the recovery request's
accounting diagnostic and is exposed through the existing bounded Activity
recovery projection; it carries no provider payload or query text.

For each `(connection, adapter, generation)`, one canonical health owner
atomically freezes a finite cohort from every currently due, eligible and
unblocked scope. Claims use strict `(dispatch reason rank, member_id, scope)`
order inside that provider-wide cohort. Jobs registered or changed later are
unstamped and cannot enter it, including a newly due higher-ranked job; they
remain reachable in the next cohort, so arrivals cannot starve already frozen
blanket work. Unchanged registration preserves its ordinal. Per-scope health
continues to own eligibility, backoff and Activity outcomes, while only the
canonical owner carries the cohort lease and cutoff. This bounded ownership
makes restart and competing-worker claims deterministic without adding a queue
or scheduler. The recovery-store schema is exact-current under ADR-0013:
existing pre-release recovery state requires a clean rebuild, with no migration
or repair.

## Consequences

### Positive

- A restart and competing manual/background workers cannot duplicate canary or
  sibling egress.
- Operators have one explicit, documented recovery wake mechanism.

### Negative

- A claimed canary is bounded to one exact-current finite provider cohort.

### Neutral or deferred

- Activity exposes aggregate recovery state and closed dispatch-reason names;
  there is no owner retry control, bulk recovery, or target metadata write.

## Validation

Unit tests cover bounded delays, dormancy, all-state generation reconciliation,
idempotent observation, strict lease completion, restart, stale leases, and
schema corruption.

## Amendment: shared Google burst admission (#1082)

The composed Google request pacer retains an observed burst deadline under its
existing admission lock. Exact burst responses may extend the deadline; suppressed
requests cannot renew it. HTTP runs outside the lock and previously admitted work
may complete. Initial ebook, terminal audiobook and successor recovery read the
remaining wait through a trusted same-process callback. Only exact burst evidence
or wholly undispatched inherited Google rate-limited work may use that reading;
actual generic or conflicting rate limits keep existing precedence. Known expired
state uses the existing minimum delay; unknown state remains distinct.

The hold is process-local. Existing committed recovery records retain restart
eligibility; no new persistence or coordination contract is added. The callback
is not part of configuration identity. iTunes overflow remains terminal because
its narrowing flag leaves the request term identical; widening or truncating its
candidate bound would change the evidence contract and is not adopted.
