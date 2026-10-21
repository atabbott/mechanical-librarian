# Observability and support

The supported review runtime exposes two anonymous operational endpoints:
process liveness at `/health/live` and startup/drain readiness at
`/health/ready`. Neither requires a credential, and the container's own
`HEALTHCHECK` reads readiness on that basis. Liveness says only that the HTTPS
process responds. Readiness becomes successful only after the exact-current
application store opens, and becomes unavailable before worker shutdown begins.

Authenticated `GET /api/v1/operations/status` returns
`operational-status-v1`: readiness, aggregate counts, bounded stage timing and
typed incidents. Status distinguishes healthy activity, owner action,
automatic recovery, field/category suspension, authentication failure, backup
failure and service incompatibility. Common service causes aggregate at their
narrow connection, library or category scope. A currentness conflict stays
attached to its opaque work and field. Routine processing creates no external
alert.

Authenticated `GET /api/v1/activity` is the owner-facing, value-bounded status
projection for continuous guarded application. It reports consented libraries,
prepared origin counts, aggregate plan states, a bounded chronological ledger,
pause, and scoped capability suspensions. It is not audit authority; canonical
field-effect events remain the durable proof. Activity never exposes credentials,
cover bytes, evidence excerpts, unrestricted paths, or a general queue console.
The authenticated Activity page presents those facts as a calm state and outcome
ledger: consent is explicit, pause prevents new claims while existing safe
checkpoints can finish, and removing a library's permission prevents new work
there. It derives bounded readable title and author labels from the exact current
report lineage without exposing report documents, work IDs, or connection IDs as
display identity. A common suspension is grouped only when its persisted reason,
connection, and scope match; Activity reports affected destinations and an honest
indeterminate reconciliation state rather than an invented completion estimate.
It does not add a retry command or duplicate the human decision queue in Attention.
Authenticated `GET /api/v1/activity/consent-state` returns only the consent
inputs of that projection -- waiting reasons, destination capabilities,
configured libraries and a null backup readiness -- for Library's write-ready
notice, without reading Activity history (#1236).
An equal automatic candidate is shown as **No change required** from its
identifier-only immutable ledger fact; it is not pending guarded work and no
write guard or service operation is consulted.

Timing is disabled by default. Set the documented
`review.diagnostic_timings_enabled` configuration field to retain at most 256
stage duration/wait-class records in the owner-private data area. The closed
stages are extraction, provider search, policy evaluation, human wait, apply,
verification, reconciliation, cover, proposal and persistence: cover
acquisition and the proposal stage are measured on their own (#1258), and
`persistence` covers report publication: the currentness re-check (a target
read and, for an audiobook, a re-digest of its tracks), the durable write and
the read-back. Its wait class is `none` although it contains that target I/O.
With no cover observer configured, the cover stage is still recorded, at close
to 0 ms. The ring keeps the newest 256, so a long run keeps only its
tail. Timing is advisory and never audit authority.
Timing-store failure cannot change a preparation, decision or field-effect
outcome; unreadable advisory evidence produces a bounded authenticated 503.

Preparation status keeps one validated, lifecycle-owned foreground repository
open before its two worker lanes start. It serializes independent preparation
progress and connection-condition snapshots with owner scan and retry controls,
and closes only after both lanes stop. Worker and recovery actions open dependent
repositories under that lifecycle owner's authority on every platform: they
inherit its schema admission and inode identity, and treat the owner's hot
rollback journal as a live transaction rather than recovery state. Ordinary
Linux server roots can admit one verified private binding journal through the
source-preserving copy boundary in
[ADR-0078](../architecture/decisions/0078-preparation-claim-and-crash-recovery.md).
Other root opens remain reject-only; configured-basename journals and WAL are
always refused.

If a claimed stage cannot commit its failure settlement, preparation reports
**Preparation stopped**. Restart the Mechanical Librarian service; refreshing
the browser, Scan and Resume cannot clear this process-local stop. Ready books
remain browsable, and the last durable progress is retained. Authenticated status
and support evidence carry `preparation_restart_required`, runtime scope, the
bounded `prep-` reference, stage, primary code and attempt, with optional
`settlement_code: persistence`. This does not assert that the work has a durable
terminal row. Successful fresh startup uses the existing checkpoint recovery.
Subscriber wake failures do not fail the producer or another subscriber.

Unclassified preparation exceptions emit a bounded structured JSON WARNING through one
best-effort service stderr write (`preparation_unknown_failure`). Match `reference` to the
terminal preparation reference in authenticated operations/support evidence when
settlement commits; if settlement fails, match `restart_reference` to the runtime
stop instead. `observation: before_fail` records an observed exception, not a
committed outcome. Execution and owned stages can differ after a committed handoff.
The record contains only validated code identities and application frame locations,
never exception text, metadata or paths. Typed failures retain their existing codes.
Keep service logs under operator-controlled retention: these advisory records are
not durable audit history and cannot reconstruct a failure from before this change.
Logging failure does not change settlement, recovery or retry behavior (#1136).

A preparation claim that loses SQLite's writer lock to a long reader, failing
with plain `SQLITE_BUSY` once its transaction scope has ended and its connection
holds no open transaction, committed nothing, so its lane retries on the next
one-second wake instead of stopping preparation (#1216). Each retry writes one
fixed WARNING line, `preparation_claim_contended` with `code: contention` and no
value-bearing field. A run of them means a reader is holding the database past
the five-second busy timeout; status meanwhile shows preparation active with
unchanged counts. Any other claim failure still reports **Preparation stopped**
as above, and so does a claimed stage whose failure settlement is blocked by the
same long read; #1217 tracks that settlement case.

Provider dispatch and provider recovery write one value-free JSON line to
stderr through the same best-effort write (#1299). They are on by default and
have no switch, environment variable or `logging` configuration.

| `event` | Written when |
|---|---|
| `provider_request_failed` | A transport, unexpected-status or parse failure, or a malformed lane with no failing attempt. A `http_404` or `region_unavailable` attempt is not one when every lane answered with candidates or no match: that is the provider's ordinary "not found". |
| `provider_throttled` | `quota_rate_limit`, `burst_throttle`, `daily_quota_exhausted`, or `held` for a lane the shared pacer held without a request |
| `provider_deadline_stop` | `stop: before_dispatch` when the deadline left a request unsent; `stop: in_flight` when it ended a retry |
| `provider_recovery_job` | A recovery job's `transition`: `started`, `settled`, `failed`, `stale` or `held` |

Dispatch events are de-duplicated per observation, with a `count`. Every line
carries `adapter`, `provider` and `item_ref`: `item-` plus 16 hex characters
of a SHA-256 over connection, library and item, never the item id. It also
carries `phase` (`discovery`, `asin_feed` or `recovery`), `lane_kind`,
`query_family`, `outcome_class`, `http_status`, `elapsed_ms`, `deadline_ms` and
`deadline_kind` (`provider_share`, `aggregate` or `recovery_window`).

Recovery events add `job_attempt`, `physical_requests` and `dispatch_reason`.
Each key is allow-listed. Each value is a closed vocabulary or a bounded
integer, and an out-of-shape value is written as `null`. A line is at most 1024
bytes, written in one call. A clean dispatch writes nothing.

Lines carry no title, author, query, URL, body, exception text, credential or
path. Attempt records carry no lane, so an observation that dispatched more than
one lane reports `mixed`. A failed write changes no dispatch, report or
settlement.

For a library of about 240 audiobooks, the expected volume is a few hundred
lines per preparation. That is an estimate, not a bound. Recovery adds about
two lines per job attempt. These are advisory, not audit history. Docker's default
`json-file` log driver does not rotate, so set `max-size` and `max-file` or
another retention the operator controls.

`GET /api/v1/operations/support/preview` shows the exact allow-listed bundle
shape and size. Authenticated, CSRF-bound
`POST /api/v1/operations/support/export` creates it again and re-runs redaction.
The JSON is capped at 64 KiB. It excludes secrets and auth state, unrestricted
environment data, databases, metadata values, excerpts, paths, media, covers
and the private recovery store. Review the preview before sharing an export.
When a browse read maps a SQLite storage or resource failure to the owner's
value-free `unavailable` response, this existing export retains only the bounded
exception class. It does not retain the database error text, query, row, path,
or a telemetry record.

Library and Attention can also report unavailable when live cover policy or
provider-recovery identities change during an otherwise consistent collection
read. Retry uses a fresh observation. Pending or unavailable totals are not
zero, and this state does not authorize a decision or target effect. Collection
reads have their own persistent connection; a completed SQL snapshot is released
before visible work is projected. The bounded projection still owns local
admission, so a waiting owner interaction enters at the next safe boundary.
Preparation and external reviewer/target work cannot retain that admission across
network calls. Timing investigations distinguish admission wait, SQLite transaction
wait, local computation and rendered confirmation; a health response is not owner
interaction evidence. [ADR-0077](../architecture/decisions/0077-owned-responsive-review-reads.md)
states the ownership and clean-rebuild boundary.
