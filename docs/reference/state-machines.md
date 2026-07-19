# Lifecycle and State Machines

## Durable EPUB opening evidence

```text
pending -> running -> succeeded
pending -> running -> retry_wait -> running
pending -> running -> failed_terminal
pending -> running -> cancelled
pending -> cancelled
retry_wait -> failed_terminal
retry_wait -> cancelled
```

Every transition retains the attempt and fence. Only the current live owner may
publish. Publication atomically creates or verifies immutable evidence, advances
current applicability, checkpoints the work, appends an event, finishes the
attempt, and marks the job succeeded. An expired worker can move to
`retry_wait`; its old fence can never publish. An identical succeeded work key
reuses its evidence. A historical key whose evidence is no longer current is
reused with `current=false` and does not reverse the pointer.

Cancellation may close pending work before its first claim, running work while
finishing its live attempt, or retry-waiting work between claims. Every terminal
replay preserves the stored typed failure; `retry_exhausted` is used only when
that is the validated durable cause.

State machines are authoritative domain contracts. Implementations may add
timestamps and metadata but cannot invent transitions outside these rules.

The domain transition API is pure: a successful transition returns an immutable
record containing the old state, new state, actor, and reason. Persistence
records that fact separately; callers cannot bypass the transition table by
constructing a replacement workflow state.

## Job lifecycle

```text
pending -> leased -> running -> succeeded
                     |       -> retry_wait -> pending
                     |       -> failed_terminal
                     |       -> cancelled
leased/running --lease expiry--> pending
pending/leased/retry_wait --required dependency failure--> failed_terminal
pending/leased/retry_wait --cancellation--> cancelled
```

Rules:

- Lease ownership and expiry are transactional.
- Retrying increments attempt count and records a typed reason.
- Retry eligibility, backoff deadlines, and intentional suppression are
  persisted and survive process restart.
- Terminal failure cannot silently become success.
- A deduplication key prevents concurrent equivalent jobs and lets redelivery
  reuse or inspect the durable prior outcome instead of starting a retry storm.

## Workflow run and stage lifecycle

The F02 synthetic scheduler persists dependency readiness separately from job
execution:

```text
run:   waiting -> running -> succeeded
                    |      -> failed_terminal
                    |      -> cancelled
                    |      -> superseded

stage: waiting -> ready -> running -> succeeded
          |         |         |    -> failed_terminal
          |         |         |    -> cancelled
          |         +--------------> cancelled
          +------------------------> dependency_failed
          +------------------------> dependency_cancelled
```

`waiting` means that required dependency outcomes are not yet independently
validated. It is never interchangeable with job `retry_wait`. A required
terminal failure, cancellation, or a terminal prerequisite with missing or
invalid durable outcome evidence produces a typed terminal dependency state; it
cannot leave descendants waiting indefinitely.

An exact workflow-definition version and canonical graph-digest match resumes
the pinned run. An incompatible definition creates one reciprocally linked
replacement and marks the original superseded without rewriting its history.

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

F10a's closed synthetic preview has no human or AI transition:

```text
no admitted state -> undecided synthetic case
undecided synthetic case -> superseded by qualifying current successor
undecided synthetic case -> retired by nonqualifying current successor
no admitted state -> current typed no-case state
```

Exact replay adds no state or transition. The scoped current record retains the
latest admitted set even when no case is current, so stale or reverse replay
cannot revive a retired case. Case and transition history remain immutable;
`production_applicable=false` is derived and no preview state is an
authorization source. Both current no-case and retired states are terminal in
the bounded two-revision preview; only exact replay may inspect them again.

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

## Hardcover identity work

Each opaque work key has monotonically increasing generations. At most one
generation is active:

```text
pending -> claimed -> succeeded
   |          |    -> failed
   |          |    -> retry_wait -> claimed
   |          |    -> cancelled
   |          -> cancelled
   -> cancelled
```

Acceptance creates generation, candidate 1, aggregate start, exact 30-second
deadline, and the first event in one transaction. Failure creates none of
them. A claim reserves a candidate and advances its fence but does not consume
an attempt. Only a gate-held `renew_and_start` transaction records a started
attempt and increments the bounded attempt count. It requires at least six
seconds remaining and renews the lease to wall-now plus 15 seconds.

Only the exact current fence and lease owner can start or publish. After a
started claim's lease expires, a later gate owner finalizes that attempt as
`interrupted`, advances the fence, appends candidate and retry facts, and makes
the generation claimable. An old owner cannot mutate the new fence, preventing
ABA publication. A terminal publication finalizes the attempt, appends result
and bounded observation, advances currentness, transitions the generation, and
adds its event atomically. Any failure rolls all of those effects back.

A compatible matched pointer is reusable through exactly 24 hours after its
publication timestamp. At 24 hours plus one microsecond, one transaction marks
the pointer expired and creates exactly one unreserved pending next generation.
Repeated readers reuse that active generation. Wall timestamps determine
durable chronology; fences determine ownership and never derive authority from
time alone.
