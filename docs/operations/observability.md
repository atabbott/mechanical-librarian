# Observability

Mechanical Librarian reports its state through two anonymous health endpoints,
authenticated status and Activity pages, short value-free JSON lines on
stderr, optional stage timings and an owner-reviewed support bundle. It sends
no telemetry and raises no external alerts.

None of these is audit history. The durable record is the application
database; see [SQLite topology](../architecture/sqlite-topology.md).

## Health endpoints

| Endpoint | Credential | Meaning |
| --- | --- | --- |
| `GET /health/live` | None | The process responds. |
| `GET /health/ready` | None | `200 ready` once the stores are open and Activity's history is loaded; `503 draining` before that and from the start of shutdown. |

The container's own health check reads `/health/ready`; see
[deployment](deployment.md#starting-stopping-and-restarting).

## Operational status

`GET /api/v1/operations/status` (signed in) returns `operational-status-v1`:
readiness, aggregate counts, bounded stage timing and typed incidents. An
incident is one of healthy activity, owner action needed, automatic recovery,
field or category suspension, authentication failure, backup failure or service
incompatibility. Common causes are grouped by connection, library or category;
a currentness conflict stays with its work item and field.

## Activity

`GET /api/v1/activity` (signed in) returns guarded-write state and a bounded,
searchable, newest-first ledger. `GET /api/v1/activity/consent-state` returns
only the inputs Library's write-ready notice needs. Neither exposes internal
identifiers, credentials, cover bytes, evidence excerpts or paths.
Reconciliation shows as indeterminate, never as a completion estimate. An
automatic decision that matched the current value appears as **No change
required** and is not pending work.

The Activity ledger is read-only. Activity offers no queue administration,
blind retry or export. Its only actions are the existing server-checked
consent, pause and recovery commands. Provider recovery phases are in
[provider recovery](provider-recovery.md#what-activity-shows).

## Preparation stopped

If a claimed preparation stage cannot record its own failure, preparation shows
**Preparation stopped**. Restart the service: refreshing the browser, Scan and
Resume cannot clear it. Ready books stay browsable and the last durable progress
is kept. Status and support bundles carry `preparation_restart_required` with
the runtime scope, a `prep-` reference, the stage, the primary code and the
attempt, and `settlement_code: persistence` when that applies. The work may not
have a durable terminal row. A clean restart resumes from the last checkpoint
([ADR-0078](../architecture/decisions/0078-preparation-claim-and-crash-recovery.md)).

A claim that loses SQLite's write lock to a long reader commits nothing and
retries on the lane's next one-second wake instead of stopping. Each retry logs
`preparation_claim_contended` (below). Many in a row mean a reader is holding
the database past the five-second busy timeout; status meanwhile shows
preparation active with unchanged counts. Any other claim failure reports
**Preparation stopped**.

## Log lines

The service writes single-line JSON events to stderr. Nothing configures Python
`logging`; these lines are always on and have no switch. Each write is best
effort: a failed write never changes preparation, dispatch, settlement or
recovery. Lines carry no title, author, query, URL, response body, exception
text, credential or path.

| `event` | Written when |
| --- | --- |
| `preparation_unknown_failure` | An unclassified preparation exception is observed (WARNING). |
| `preparation_claim_contended` | A preparation claim lost the write lock and will retry (WARNING, `code: contention`). |
| `activity_projector` | The background Activity projector stopped. `code` is `stopped`, `released` or `admission_closed` (info), or `unexpected` (WARNING, with the exception type in `error`). Shutdown adds `join_timeout` (WARNING) if the projector's current step outlasts the join bound; closing still waits for that step. |
| `provider_recovery_schedule` | The review library cannot read the recovery schedule, so it withdraws the retry promise (WARNING, `code: unavailable`). |
| `provider_request_failed`, `provider_throttled`, `provider_deadline_stop`, `provider_recovery_job` | See [provider logs](#provider-logs). |

`preparation_unknown_failure` holds only validated code identities and
application frame locations. Match its `reference` to the preparation reference
in status or support evidence when the failure was recorded, or its
`restart_reference` to a **Preparation stopped** reference when it was not.
`observation: before_fail` means the exception was seen, not that an outcome
was committed.

### Provider logs

Provider dispatch and provider recovery write one line per event. A clean
dispatch writes nothing.

| `event` | Written when |
| --- | --- |
| `provider_request_failed` | A transport, unexpected-status or parse failure, or a malformed lane. `outcome_class` separates `malformed_response`, `oversized_response` and `candidate_limit_exceeded`. A provider's ordinary "not found" (`not_found`, or `http_404` or `region_unavailable` when every lane still answered) is not a failure. |
| `provider_throttled` | `quota_rate_limit`, `burst_throttle`, `daily_quota_exhausted`, or `held` when the shared pacer held a lookup without sending it. |
| `provider_deadline_stop` | `stop: before_dispatch` when the deadline left a request unsent; `stop: in_flight` when it ended a retry. |
| `provider_recovery_job` | A recovery job changed state; `transition` is below. |

| `transition` | Written after |
| --- | --- |
| `started` | a job is claimed |
| `settled` | the canary authorises the other items, or an item succeeds |
| `failed` | a canary failure, an exhausted scope, interrupted retries or an item failure |
| `stale` | a job closes as stale |
| `held` | a target hold is released |

A recovery line is written only after its store change commits.

Dispatch events are de-duplicated per lookup, with a `count`. Every provider
line carries:

- `adapter`, `provider`;
- `item_ref`: `item-` plus 16 hex characters of a SHA-256 over connection,
  library and item, never the item ID;
- `phase` (`discovery`, `asin_feed` or `recovery`), `lane_kind`,
  `query_family`, `outcome_class`, `http_status`;
- `elapsed_ms`, `deadline_ms` and `deadline_kind` (`provider_share`,
  `aggregate` or `recovery_window`).

Recovery lines add `job_attempt`, `physical_requests` and `dispatch_reason`.
A lookup that used more than one lane reports `lane_kind` as `mixed`. Every key
is allow-listed and every value is a closed word or bounded integer; anything
else is written as `null`. A line is at most 1,024 bytes.

### Log retention

As a rough estimate, expect a few hundred lines per full preparation of a
library of a few hundred books, and about two per recovery attempt. Docker's
default `json-file` driver does not rotate logs: set `max-size` and `max-file`,
or use another retention you control.

## Diagnostic timings

Timings are off by default. Set `review.diagnostic_timings_enabled` to keep the
newest 256 stage records (duration and wait class) in the private data area.
The stages are extraction, provider search, policy evaluation, human wait,
apply, verification, reconciliation, cover, proposal and persistence. The
cover stage is recorded even with no cover reviewer configured.
`persistence` is report publication: the currentness re-check (a target read
and, for an audiobook, a re-digest of its tracks), the write and the read-back.
It has wait class `none` although it includes target I/O. See
[configuration](../reference/configuration.md#diagnostic-timings).

Timings are advisory. A timing-store failure never changes an outcome;
unreadable timing data returns a bounded `503` to the signed-in owner.

## Support bundle

`GET /api/v1/operations/support/preview` shows the exact bundle and its size.
`POST /api/v1/operations/support/export` (signed in, CSRF-protected) builds it
again with fresh redaction. The JSON is at most 64 KiB. It excludes secrets,
authentication state, environment data, databases, metadata values, excerpts,
paths, media, covers and the private recovery store. When a browse read fails
on a SQLite storage or resource error, the bundle keeps only the exception
class, never its message, query, row or path. Review the preview before sharing
an export.

## Unavailable reads

Library and Attention report **unavailable** when cover policy or provider
recovery changes during a read, or when storage fails. Pending or unavailable
totals are not zero, and an unavailable page authorises no decision or write.
Retrying reads afresh. How reads stay responsive under background work is in
[ADR-0077](../architecture/decisions/0077-owned-responsive-review-reads.md).
