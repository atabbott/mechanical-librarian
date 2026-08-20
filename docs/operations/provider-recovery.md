# Provider recovery

Terminal provider failures are recorded locally. A due scope is retried only by
the existing review-preparation supervisor: it claims one exact-current canary,
then authorizes durable fanout only after that canary succeeds. Each sibling is
leased independently; shutdown drains between members and restart resumes
unfinished fanout without repeating a completed canary.
Each exact lease is renewed between the bounded target/provider operation and
the proposal, admission, and projection stages. A manual invocation therefore
cannot reclaim work merely because the composed path has passed the initial
five-minute lease horizon.
Transient and rate-limited failures wait for either a strict 1–86,400 second
`Retry-After` delay or 2, 4, 8, 16, then 24 hours. Authentication,
authorization, and configuration failures are dormant.

After correcting a dormant condition, increment the positive
`provider_recovery_generation` in the `config-v6` file and restart the
container. Startup first performs a read-only generation compatibility check;
it rejects persisted generations newer than configuration before any outbox
delivery or database mutation. It then drains committed package cutovers from
the durable outbox. Each delivery atomically enrolls its exact member and
health scope before its job can be claimed; an existing current health record
is preserved. If the outbox is the first record of the scope, its fallback is
the documented two-hour transient cooldown rather than immediate provider
egress. A failed row stays pending for a later retry while the bounded
drain continues to younger deliverable rows, so startup remains available and
does not discard recovery work. Authentication and authorization wake only
when that delivered exact job proves the new configured generation; otherwise
they remain dormant. Outbox delivery and job registration are idempotent, so a
crash between cutover and enqueue is recovered on restart. No environment variable,
file watcher, configuration hash, or manual retry endpoint wakes it. This
recovery path rebuilds only retry-eligible provider lanes and publishes an immutable
successor through the ordinary proposal, admission, shadow projection, and
atomic review-package cutover. The predecessor package remains visible until
that cutover. A no-match is a successful terminal result. A shared
provider failure creates no successor and no sibling fanout; it waits for
Retry-After when supplied, otherwise uses bounded backoff. Stale package,
request, deadline, cancellation, and target/media checks close safely without
replaying a sibling. Recovery never writes target metadata.
One stale member is retired without retiring current siblings. A restart after
an exact successor package cutover resumes from its persisted lineage, so the
completed canary is not sent to the provider again.

If inline delivery fails after a package cutover claimed the next preparation
action, that successor is returned to the existing bounded preparation state
machine with the typed `persistence` failure. It is never left running to
occupy a worker lane; restart retains the same three-attempt recovery limit.

Recovery state contains bounded connection, adapter, scope, generation, phase,
attempt, and timing data plus the exact private work, source-report, lane, file,
and package identifiers required for currentness and restart. It never stores
credentials, provider payloads, raw retry headers, query values, or filesystem
paths. Activity aggregates the private identifiers into scope or work counts;
logs, API responses, and operator copy never expose them.

The trusted owner-only `private` directory is created and validated before the
server opens recovery state. The shared local store serializes every recovery
transaction and snapshot across supervisor and Activity threads, then closes
after those workers drain.

Terminal health observations are idempotent. In one generation, checking and
dormant state take precedence; a dormant observation may replace waiting once,
while repeated transient or rate-limited observations do not reschedule it.
The authenticated Activity page shows waiting, checking, fanout, recovered,
dormant configuration/authentication, and exhausted phases with scope counts
or, during fanout, work counts and any next eligible timestamp. It has no retry
control and never exposes connection identifiers,
provider payloads, or credentials.

The owner may run `mechanical-librarian provider-recovery` with the same
configuration, database, catalogue, and artifact paths used by the server. It
uses the same durable one-claim path as the supervisor; it neither bypasses
leases nor retries a completed sibling.
