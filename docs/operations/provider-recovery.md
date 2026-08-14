# Provider recovery

Terminal provider failures are recorded locally. A due scope is retried only by
the existing review-preparation supervisor: it claims one exact-current canary,
then processes durable sibling members only after that canary succeeds.
Transient and rate-limited failures wait for either a strict 1–86,400 second
`Retry-After` delay or 2, 4, 8, 16, then 24 hours. Authentication,
authorization, and configuration failures are dormant.

After correcting a dormant condition, increment the positive
`provider_recovery_generation` in the `config-v6` file and restart the
container. Startup rejects persisted generations newer than configuration and
advances every older row atomically: dormant rows wake immediately while
waiting and checking rows retain their domain state. No environment variable,
file watcher, configuration hash, or manual retry endpoint wakes it. This
recovery path rebuilds only retry-eligible provider lanes and publishes an immutable
successor review package. A no-match is a successful terminal result. A shared
provider failure creates no successor and no sibling fanout; it waits for
Retry-After when supplied, otherwise uses bounded backoff. Stale package,
request, deadline, cancellation, and target/media checks close safely without
replaying a sibling. Recovery never writes target metadata.

Recovery state deliberately contains only bounded connection, adapter, scope,
generation, state, attempt, and timing data. It never stores credentials,
provider payloads, raw retry headers, query values, filesystem paths, or work
identifiers.

The trusted owner-only `private` directory is created and validated before the
server opens recovery state. The shared local store serializes every recovery
transaction and snapshot across supervisor and Activity threads, then closes
after those workers drain.

Terminal health observations are idempotent. In one generation, checking and
dormant state take precedence; a dormant observation may replace waiting once,
while repeated transient or rate-limited observations do not reschedule it.
The authenticated Activity page shows aggregate waiting, checking, dormant,
and recovered recovery states with affected counts and any next eligible
timestamp. It has no retry control and never exposes connection identifiers,
provider payloads, or credentials.

The owner may run `mechanical-librarian provider-recovery` with the same
configuration, database, catalogue, and artifact paths used by the server. It
uses the same durable one-claim path as the supervisor; it neither bypasses
leases nor retries a completed sibling.
