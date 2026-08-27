# ADR-0059: Move review-session state to a dedicated session database

- Status: proposed
- Date: 2026-08-27
- Owners: project maintainers
- Supersedes: the `review_sessions` / `review_login_failures` placement in
  ADR-0057's application-database topology
- Superseded by: none

## Context

#912 established that owner sign-in can block for up to five seconds while a
library scan runs. `ReviewSessions.login` performed two SQLite writes on the
request path against the application database, under `journal_mode=DELETE`,
`synchronous=FULL`, and `busy_timeout=5000`.

The queue is SQLite's, not Python's. The supported deployment opens several
connections to the application database — a state owner, foreground
observation, an activity projection, and per-action connections, all borrowing
one authority — so a scan writing on one connection holds the rollback-journal
write lock against login on another. `busy_timeout=5000` is therefore the
ceiling the owner feels, not a backstop beneath an in-process wait: sessions
and the composing repository do not share a transaction lock. Session
operations additionally took the application-wide serialization gate. The
Argon2id verify is not implicated (measured 27ms at the configured
parameters).

Measured against this topology, with a batch writer holding one transaction
across many rows on a second connection, login latency is:

| session placement | p50 | p95 | max |
| --- | --- | --- | --- |
| application database, `DELETE` | 204ms | 4494ms | 5009ms |
| application database, `WAL` | 198ms | 2218ms | 2643ms |
| dedicated database, `DELETE` | 1.7ms | 3.3ms | 6.1ms |

The owner decided the coupling is unacceptable: login must not wait on scan
writes. Mitigations inside the shared database fall short, and the middle row
is why: WAL removes reader/writer contention but still admits one writer, so
login — itself a writer — keeps waiting whole seconds. Writing the throttle
record only on failure halves the writes but leaves the session insert in the
same queue. Adopting WAL would also change the supported-restart contract,
which rejects `-wal`/`-shm` sidecars and validates the rollback-journal
signature in the database header, and ADR-0057 records that the sustained
topology adds no WAL change. Only separation moves login off the contended
lock entirely.

ADR-0057's own separation criterion already isolates session state
conceptually: it is the only state whose authority is the review surface
itself rather than any report, case, or policy; nothing else reads it; its
lifecycle is expiry and revocation rather than lineage; and its restart cost
is one owner re-login. The supported deployment also already runs one SQLite
database per distinct operational authority beside the application database:
`catalogue.sqlite3` and `private/provider-recovery.sqlite`.

## Decision

`review_sessions` and `review_login_failures` move to a dedicated SQLite
database, `private/review-sessions.sqlite`, beside the other private
operational stores. In particular:

- `SQLiteReviewSessionRepository` alone owns that database: its private
  connection, its own lock, and every session method including
  `revoke_review_session`, which completes its deferred move out of the
  composing repository. Writes use `BEGIN IMMEDIATE` under
  `journal_mode=DELETE` and `synchronous=FULL`; no WAL is adopted anywhere.
- The application database's exact schema drops both tables. The application
  connection, its transaction lock, and its busy timeout no longer stand
  between the owner and a session.
- The four session operations on the review application no longer take the
  application serialization gate. The session store shares no state with any
  gated operation, and re-entering the gate would restore the transitive wait
  the decision removes.
- Setup's owner claim stays atomic in the application database (claim plus
  verifier), and the first session is issued through the ordinary login path
  after the claim commits. The previous single-transaction claim+session
  cannot span two databases; if session issuance fails after a committed
  claim, resubmitting the same form reaches the already-claimed login branch,
  which is the documented recovery.
- The first-boot classifier accepts a `private` directory beside the
  application database while classifying setup states, but only while it
  contains nothing beyond the session database and its rollback journal:
  setup's first sign-in legitimately creates that file, and an interrupted
  sign-in legitimately leaves the journal the next open rolls back. Any other
  private content is runtime state and keeps the volume failing closed as
  before, `-wal` and `-shm` included, because no WAL is adopted anywhere.

Under ADR-0013, the session database accepts absent state (created with the
exact schema) or its exact current state, and rejects anything else clearly
and without mutation. Its clean rebuild is deleting the file, at the cost of
one owner re-login; it never needs adoption machinery of its own.

The application-schema change ships one new offline adoption plan,
`review-session-decoupling-v1`, whose exact immediate predecessor is the
previous target schema and whose only mutation drops the two session tables.
The two existing plans, `review-owner-decision-actions-v1` and
`owner-settings-v1`, gain the same drop, so every documented predecessor
still adopts to the single current target. This extends ADR-0013's adoption
confinement — reconstructible derived objects — to expendable operational
security state for these plans: the drop discards at most a few live session
digests (one owner re-login) and throttle timestamps inside a five-minute
window, state whose loss the product already tolerates through expiry and
pruning. No canonical, audit, control, or derived row is touched, and the
per-table digest equality over every remaining table proves it.

## Consequences

- Owner sign-in, session authentication, CSRF rotation, and logout no
  longer queue behind application-database write traffic. The proof is a
  test that holds an application-database write transaction and the
  application gate for the whole request and still logs in well inside the
  old five-second backstop.

  The independence is from the write queue, not from the application
  database entirely. Deriving a session digest still reads the owner
  verifier through `use_owner_verifier`, which executes on the shared
  application connection. That read takes the setup store's own dedicated
  lock rather than the application transaction lock, so it interleaves per
  statement instead of waiting for a scan transaction to commit, and this
  decision leaves it unchanged. The proof test supplies the verifier from a
  hash file, so it exercises the write-path coupling this decision removes
  and not that read.
- Against ADR-0057's bar for topology change: the application database has
  two fewer persisted concepts and one less cross-cutting lock dependency;
  the simplified consumer is the login path. Transaction rollback, restart,
  idempotency, audit retention, query-result equivalence, and exact-schema
  rejection are each proven by the unit's tests; optimistic currentness does
  not apply to session rows, which carry no lineage.
- A full `/data` backup now includes the session database implicitly. Losing
  or omitting it costs one re-login and five minutes of throttle history,
  never review state. The application database's no-sidecar restart contract
  is unchanged.
- The atomic claim+first-session guarantee is narrowed to an atomic claim;
  first-session issuance is ordinary login with a stated resubmit recovery.
- The topology map's session rows describe state that now lives in the
  dedicated session database; ADR-0057 continues to govern the aggregates
  that remain in the application database.
