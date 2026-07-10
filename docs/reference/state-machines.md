# Lifecycle and State Machines

State machines are authoritative domain contracts. Implementations may add
timestamps and metadata but cannot invent transitions outside these rules.

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
- Terminal failure cannot silently become success.
- A deduplication key prevents concurrent equivalent jobs.

## Review case lifecycle

```text
open -> auto_resolved
     -> needs_review -> human_resolved
     -> unsupported
     -> superseded
```

A new target fingerprint supersedes an unresolved case. History remains
immutable. Rejected automatic work is normally hidden from the active queue but
remains queryable.

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
