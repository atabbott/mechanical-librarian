# Lifecycle and State Machines

State machines are authoritative domain contracts. Implementations may add
timestamps and metadata but cannot invent transitions outside these rules.

The domain transition API is pure: a successful transition returns an immutable
record containing the old state, new state, actor, and reason. Persistence
records that fact separately; callers cannot bypass the transition table by
constructing a replacement workflow state.

## Run and stage lifecycle

```text
pending -> running | cancelled
running -> waiting | succeeded | failed_terminal | cancelled
waiting -> running | failed_terminal | cancelled
```

`waiting` is nonterminal: a run or stage has incomplete dependencies but no
currently eligible work left to launch, and every required prerequisite can
still reach an accepted outcome. It is distinct from a job's `retry_wait`, and
never means ready, complete, successful, or authorized. An exhausted required
prerequisite or a missing or malformed expected outcome records a typed blocker
and moves the affected run or stage to `failed_terminal`; it cannot wait
indefinitely or advance fan-in.

Each run persists a bounded acyclic dependency graph and the expected typed
outcome of every node. A scheduling pass persists and durably enqueues the
complete eligible set, then workers make capacity-bounded claims using stable
job identity and deduplication keys. Delivery is at least once, so redelivery is
expected; idempotent handlers plus provider idempotency or reconciliation
prevent or recover duplicate external effects. Fan-in advances only after one
monotonic-fenced accepted outcome per job has been independently validated
against its current input versions.
Leases, fencing tokens, durable job IDs, cancellation, and expiry make resume
safe after worker, scheduler, or process restart. Fencing tokens increase
monotonically for each new lease holder. A stale holder is rejected when it
renews a lease or records an outcome, artifact, checkpoint, audit completion,
or fan-in transition. Operating-system process IDs, shell sessions, and
detached watchers are diagnostics only and never workflow identity or
transition authority.

Expiry is a typed cancellation reason, not a separate state. It may cancel an
eligible `pending`, `running`, or `waiting` run or stage and preserves the
expiry reason, actual transition actor, and initiating authorization reference
in the audit event.

## Job lifecycle

```text
pending -> leased -> running -> succeeded
                     |       -> retry_wait -> pending
                     |       -> failed_terminal
                     |       -> cancelled
leased/running --lease expiry--> pending
```

Rules:

- Lease ownership and expiry are transactional.
- Retrying increments attempt count and records a typed reason.
- Retry eligibility, backoff deadlines, and intentional suppression are
  persisted and survive process restart.
- Terminal failure cannot silently become success.
- A deduplication key prevents concurrent equivalent jobs and lets redelivery
  reuse or inspect the durable prior outcome instead of starting a retry storm.

## Review case lifecycle

```text
open -> auto_resolved
     -> needs_review -> human_resolved
     -> unsupported
     -> superseded
needs_review -> superseded
```

A new target fingerprint or materially changed proposal set supersedes an
unresolved case. History remains immutable. Rejected automatic work is normally
hidden from the active queue but remains queryable. Operational failures do not
move a case into `human_resolved`; they retain job, health, or capability state.

## Field proposal decision

```text
proposed -> ai_approved
         -> ai_rejected
         -> ai_manual
         -> deterministic_rejected
         -> unsupported
```

Policy maps the complete evidence and decision state to disposition:

```text
auto_apply | needs_review | closed_rejected | unsupported
```

AI approval alone does not guarantee `auto_apply`.

## Human decision actions

```text
unresolved -> accept_selected
           -> keep_current
           -> reject_proposals
           -> defer -> unresolved
```

The first three actions create immutable `HumanDecision` records and may move a
`needs_review` case to `human_resolved` when every required field or action is
resolved. `defer` records no approval and leaves the case unresolved. `blocked`
is not a human action.

A human decision becomes inapplicable when its case, proposal-set version, or
expected item fingerprint changes. Reconsideration creates a new decision
event; history is never rewritten.

Applicability is typed as `current`, `stale`, `superseded`, or `rejected`.
Authorization results separately record `authorized`, `rejected`, `stale`,
`superseded`, or `operational_failure`; operational failure is retryable and
never becomes a human decision or an authorization rejection.

## Decision authorization

An apply plan may reference exactly one applicable authorization path:

```text
qualified policy disposition: auto_apply
current human decision:       needs_review -> human_resolved
```

Both paths retain field-level evidence and decision provenance. AI output alone
cannot authorize an apply plan. Destructive work has no automatic path.
Authorization construction retains the immutable review case and, where
required, the full current human decision rather than accepting an unchecked
decision identifier.

## Bulk decision batch

```text
previewed -> committed
          -> stale
          -> expired
          -> cancelled
```

Commit requires the exact previewed case and proposal versions. It creates
individual human decisions plus a batch audit event. Reversing committed but
unapplied decisions creates compensating decision events. It never rolls back a
production write.

## Apply plan lifecycle

```text
draft -> validated -> backup_ready -> queued -> applying
                                      |          -> verified
                                      |          -> rollback_pending
                                      |          -> failed
                                      -> stale
                                      -> cancelled
rollback_pending -> rolled_back
                 -> rollback_failed
```

Rules:

- `validated` requires current adapter capability and strict schema validation.
- `backup_ready` references verified backups.
- Fingerprint drift moves a non-terminal plan to `stale`.
- `verified` requires read-back comparison, not merely a successful HTTP code.
- Rollback failure trips the service write circuit breaker.

## Service write circuit breaker

```text
closed -> open -> half_open -> closed
                     |      -> open
```

- `closed`: permitted validated writes may proceed.
- `open`: writes are blocked; read-only work continues.
- `half_open`: a bounded diagnostic or canary is allowed by explicit recovery
  policy.
- Repeated apply, verification, backup, authentication, or rollback failure can
  open the breaker.

Every transition is an audit event with a reason and actor.

The [review-experience contract](../product/review-experience.md) defines the
user-facing behavior associated with these states.
