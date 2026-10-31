# Provider recovery

When a metadata provider lookup fails in a way that can clear by itself, such
as a rate limit or a temporary outage, the failure is recorded and the lookup
is retried automatically on the schedule below. Authentication, authorisation
and configuration failures cannot clear by themselves: they stay dormant until
you fix the cause and press **Retry now**. Recovery never writes to
Audiobookshelf.

The design is in
[ADR-0053](../architecture/decisions/0053-dormant-provider-recovery.md) and
[ADR-0054](../architecture/decisions/0054-scoped-provider-successors.md). The
store is described in
[SQLite topology](../architecture/sqlite-topology.md#provider-recovery-database).

## Retry schedule

The same rule applies to the first report and to every retry:

| Failure | Next attempt |
| --- | --- |
| Burst throttling, identified from the provider's own response | After the provider's burst window |
| Daily quota exhausted | After the provider's daily reset |
| Other rate limiting, or rate-limit evidence that is missing, malformed or unknown | After a valid `Retry-After` (1 second to 24 hours), otherwise 2, 4, 8, 16, then 24 hours |
| Temporary failure | 2, 4, 8, 16, then 24 hours |
| Authentication, authorisation or configuration | Dormant until **Retry now** |

A scope shows as exhausted when its jobs have used all five execution
attempts, or when the canary fails in a way that cannot be retried. Burst and
daily-quota waits do not use up an attempt. A burst or quota wait can lengthen
a scheduled retry but never bring it forward.

Provider specifics:

- **Google Books.** A burst response holds every Google lookup in the running
  process for 100 seconds; later lookups record a rate limit for the remaining
  wait instead of sending a request. A daily-limit response waits until the
  next midnight Pacific time.
- **Hardcover.** A depleted burst bucket waits at most 60 seconds; a depleted
  daily bucket waits until the next midnight UTC.
- **Pacing.** Google Books, Hardcover, Inventaire and Open Library each start
  at most one request per second, shared by normal preparation and recovery.
  The pacer and an uncommitted Google hold are in memory and are lost on
  restart; a recorded wait survives restart.

Header values themselves are never stored.

## Time limits

Each item has a 60-second provider window during preparation. Each provider
gets at most 20 seconds of it; a provider that runs out of its share records a
temporary failure. A recovery retry gives its single provider the whole 60
seconds.

## Retry now

After fixing a dormant cause (a key, a permission, a setting), press
**Retry now** on Library or Settings. It raises `provider_recovery_generation`
by exactly one through an ordinary Settings save:

- The request carries the generation you saw. A second press, another tab or a
  changed settings revision is refused with `provider_recovery_stale` or
  `settings_stale` and changes nothing.
- In file mode the command is refused with `settings_managed_by_file`. Raise
  `provider_recovery_generation` by one in `config.json` and restart instead
  ([configuration](../reference/configuration.md#provider-recovery-generation)).
- If the save fails, nothing changes. If the save commits but applying it
  fails, the service stops rather than run on old settings; restart it and the
  new generation takes effect.

Retry now requeues every outstanding lookup at once. A scope with none is
never left dormant: a dormant one waits to be checked again, one caught
mid-check ends as exhausted, and any other keeps its state. A lookup that
fails there later is still retried.

Nothing else wakes dormant recovery: no environment variable, file watcher or
configuration hash. A dormant authentication or authorisation scope wakes only
through Retry now, and counts as recovered only once a job proves the fix.

The preparation control in the workspace header offers **Scan now**, and
**Retry** after a run with failures. Both start a new preparation cycle for a
library. They do not change the recovery schedule.

## What recovery does

A recovery worker runs inside the server. For each due scope (one connection,
provider and scope) it:

1. Claims one item as a canary and retries only the lanes that can be retried.
2. If the canary succeeds, retries the scope's other items, each under its own
   lease. Shutdown stops between items; restart resumes without repeating a
   completed item.
3. Publishes each result as a new immutable report through the normal proposal,
   admission and review-package steps. The previous package stays visible
   until the new one replaces it.

A failure shared by the whole scope creates no new report and does not retry
the other items; the scope waits under the schedule above. A no-match is a
successful result.

Before contacting a provider, the isolated EPUB or audio inspector rechecks the
book's files. If they have changed, recovery for that item is refused. Stale
packages, requests, deadlines and target changes close safely without
retrying other items.

Startup refuses a recovery store whose generation is newer than the
configuration, before changing anything. It then delivers queued enrolments; a
scope seen for the first time waits the two-hour temporary-failure delay rather
than calling the provider at once. A failed enrolment stays queued for a later
start and does not block others.

If the server cannot record a preparation's failure after a package switch,
preparation stops and asks for a service restart; see
[observability](observability.md#preparation-stopped). Restart keeps the
three-attempt preparation limit and does not repeat committed work.

You can also run one recovery claim by hand with
`mechanical-librarian provider-recovery`, using the server's configuration,
database, catalogue and artifact paths. It uses the same claims and leases as
the worker.

## Audiobooks and ebooks

Audiobook lookups use only Audible, Audnexus, AudioSilo, Hardcover and Open
Library; ebook lookups use only Google Books, Hardcover, Inventaire, iTunes
Search and Open Library. Recovery and failure evidence cover only the providers
of the work's media kind. A queued job for any other provider closes as `stale`
without a request.

## What is not retried

- A structural failure before a report is published is recorded as `unknown`
  and is not retried. Fix the cause, then use **Scan now** or **Retry**.
- Missing ISBNs, malformed evidence, too many candidates and insufficient input
  are final for that report.
- A lookup the adapter itself failed is recorded as `adapter_fault` and is not
  retried: it fails the same way each time. A provider that could not be
  reached is still retried.
- An iTunes Search lookup (ebook libraries only) that returns more than five
  candidates is final; the adapter does not truncate or repeat the query.
- A construction or dispatch error during a retry publishes nothing; only the
  provider's final `http_429` response can set the retry delay.

## What Activity shows

Activity shows each scope's phase: waiting, checking, retrying items,
recovered, dormant (configuration or authentication) or exhausted, with scope
or item counts and the next eligible time. The next eligible time is when a
retry may start, not an estimate of when it will finish. The reason shown is
the latest failure for the current state; a later transition clears it.
Completed books stay available in Library while providers wait. Activity has no
retry control of its own.

Recovery state holds only connection, provider, scope, generation, phase,
attempt and timing data, plus the internal identifiers needed to resume. It
never stores credentials, provider responses, retry headers, query values or
file paths, and Activity, logs and API responses never show the internal
identifiers.

## Recovery history

Each recovery event is recorded with the job and health change it describes.
A settled item records `outcome`, `job_attempt`, `physical_requests` (at most
54) and `request_count_status` (`observed`, `partial` or `unknown`). This
history is neither a complete request log nor a billing record. The matching
stderr lines are in [observability](observability.md#provider-logs).
