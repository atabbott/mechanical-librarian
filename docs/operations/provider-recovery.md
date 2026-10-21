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
The application applies one closed recovery rule to both the initial report and
every retry successor. Exact provider evidence distinguishes burst throttling
from exhausted daily quota. Burst throttling waits only for the provider's
bounded burst window; exhausted quota waits until the provider's documented
daily reset. Missing, malformed, contradictory, or unknown rate-limit evidence
stays the conservative generic rate-limited case and uses a strict bounded
`Retry-After` or the 2, 4, 8, 16, then 24 hour ladder. Authentication,
authorization, and configuration failures are dormant.
A preparation gives each provider at most a 20-second share of the item's
60-second window (#1248). A provider that uses up its share classifies
`transient` exactly as before, while later providers are no longer recorded
deadline-incomplete without ever sending a request. A retry successor still
gives its single provider the whole 60 seconds.

Google Books recognizes only the closed API reason vocabulary: burst reasons
start a shared 100-second admission hold in the composed process. Later Google
lanes and works return rate-limited without starting HTTP, sleeping through the
hold, or renewing its deadline. Initial ebook and terminal audiobook reports,
and recovery successors, commit the remaining wait through existing recovery
state (82 seconds when 18 seconds of the window have elapsed). A known expired
window uses the existing one-second minimum; an unknown window does not inherit
a hold. Already admitted requests may finish. Daily-limit reasons resume at the next Pacific
midnight. Hardcover recognizes either its exact current `RateLimit` response
grammar or its exact legacy remaining/reset pair. A depleted
burst bucket waits at most 60 seconds; a depleted daily bucket resumes at the
next UTC midnight. Header values themselves are never persisted. A deferred
burst or daily-quota result consumes neither a recovery-health attempt nor the
provider job's five-attempt execution allowance.

After correcting a dormant condition, increment the positive
`provider_recovery_generation` in the `config-v9` file and restart the
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
provider failure creates no successor and no sibling fanout; it waits under
the same closed recovery rule as the initial report. Stale package,
request, deadline, cancellation, and target/media checks close safely without
replaying a sibling. For audiobooks, the existing isolated audio inspector rechecks the retained
audio set before provider dispatch and publication, just as the EPUB inspector
checks ebooks. Changed audio refuses recovery before contacting a provider.
Recovery never writes target metadata.
One stale member is retired without retiring current siblings. A restart after
an exact successor package cutover resumes from its persisted lineage, so the
completed canary is not sent to the provider again.

An audiobook enrolls recovery and records provider failure evidence only for
adapters in its dispatch set, the Audible catalog, Audnexus, AudioSilo,
Hardcover and Open Library (#1247), even when a successor carried an older
Google Books, Inventaire or iTunes Search observation forward, so those three
get no request and no failure evidence from audiobooks. A job for one of them
queued before the set closes as `stale` as soon as its source report is read:
no current-report read, resolver, target read or factory call follows, only
that job closes, and it records no failure evidence. A recovery row whose
remaining members all close as stale can still end through the runner's
existing success path.

If inline delivery fails after a package cutover claimed the next preparation
action, that successor is returned to the existing bounded preparation state
machine with the typed `persistence` failure. If that settlement cannot commit,
the exact running claim remains owned and preparation stops with an explicit
service-restart instruction. Other active work finishes its safe checkpoint;
no new claim is admitted. Ordinary restart retains the same three-attempt
recovery limit and does not repeat committed report or package output.

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

Google Books, Hardcover, Inventaire, and Open Library each use one shared one-second
request-start pacer across foreground and recovery work inside a composed run.
This serializes starts for one provider without holding a SQLite transaction or
creating a separate request budget. Google alone arms its pacer from an exact
burst response. The lock covers admission and hold updates, never the HTTP
exchange. Restart loses an uncommitted process-local hold; committed recovery
eligibility survives. This provides no restart-wide quota coordination (#1082).

The owner may run `mechanical-librarian provider-recovery` with the same
configuration, database, catalogue, and artifact paths used by the server. It
uses the same durable one-claim path as the supervisor; it neither bypasses
leases nor retries a completed sibling.

A structural failure before report publication remains terminal `unknown` and
enrolls no provider recovery job. After correcting its cause, use the supported
Library scan/retry to prepare a new cycle. A scan after a terminal run starts
a new preparation display; completed reports remain immutable. This deliberate
reprepare differs from durable recovery of a published
Inventaire unavailable, incomplete, or rate-limited lane. Missing ISBNs,
malformed evidence, overflow and insufficient input do not gain automatic
retries. HTTP attempts, shaped search dispatches and durable queued recovery
attempts remain separate counts (#1006).

Recovery reads the same immutable provider result and operational evidence as
initial generation. Its policy remains distinct: an ordinary construction or
dispatch exception becomes `ProviderRetrySuccessorError` with the cause chained,
and publishes no successor. Cancellation-compatible failures propagate with their
partial snapshot. Retry delay comes only from the final canonical attempt:
terminal or exhausted `http_429`, with an integral non-Boolean value from 1 to
86,400 seconds. Initial-report unavailable-lane handling is not a recovery fallback.

An iTunes Search overflow is terminal for that observation. The adapter asks for
six rows and admits at most five distinct candidates; it neither truncates an
overflow nor repeats the same request as a supposed narrower query. Its narrowing
flag does not change the wire term. The ordinary query ladder removes information
and advances only after no-match, so it is not an overflow remedy (#1082).
iTunes Search no longer runs for audiobooks (#1254), so this applies only to
ebook work lanes where a library connects it.


## Settlement diagnostics

Recovery event history separates ordinary health transitions from one diagnostic
per settled executing member (#1157). `outcome` is a closed failure or result;
`job_attempt` is the charged attempt after settlement. `physical_requests` counts
only the actual replacement observation's initial and retry dispatches, before
retained lanes are merged. A shared scope failure records this cost once, not once
per affected scope. Burst/quota deferral can therefore record physical requests
while retaining a zero charged attempt. Existing pacing and retry allowances do
not change.

`request_count_status` distinguishes `observed` complete accounting, `partial`
completed typed dispatch records, and `unknown` unavailable or invalid evidence.
Observed zero means that returned observation dispatched nothing. Partial zero is
a lower bound and does not prove no unrecorded request began. Unknown has no count.
Counts are bounded at 54, the current maximum per observation, never clamped or
inferred from exception text, elapsed time, predecessor evidence or job attempts.
Malformed responses retain accounting without becoming successor reports. A
construction or publication failure retains `terminal_failure` diagnostics while
its existing retry classification remains authoritative. Success is reported to
the runner only after the complete review package succeeds.

The job, health and diagnostic commit together in the existing recovery SQLite
transaction; provider work remains outside it. Rejected leases and rolled-back
commits append nothing. A crash after egress but before settlement can leave no
diagnostic, so this is neither a complete request ledger nor a billing record.
Older checking transitions cannot establish total request cost or prove checks
were avoidable. Controlled clocks can distinguish due canaries, non-due idle,
shared failures, interrupted fanout and eventual recovery; that does not establish
an historical cost reduction.

Each recovery job transition also writes one value-free
`provider_recovery_job` line to stderr after its store call commits (#1299).

| `transition` | Written after |
|---|---|
| `started` | a job is claimed |
| `settled` | the canary authorizes fanout, or a member succeeds |
| `failed` | a canary failure, an exhausted scope, interrupted fanout, or a member failure |
| `stale` | a job is completed as stale |
| `held` | a target hold is released |

A store call that raises writes nothing. Each line carries:
- `outcome_class`: the recovery attempt outcome;
- `job_attempt`, `physical_requests` and `dispatch_reason`;
- `elapsed_ms` since the runner turn began.

The replacement dispatch itself writes `phase: recovery` dispatch lines bounded
by `recovery_window`. [Observability](observability.md) lists the fields.

Activity shows only current waiting/dormant failure reasons from the latest
ordinary event matching the current health identity. A later transition clears
an old reason. The existing next-eligible timestamp is eligibility, not a finish
estimate. Completed Library work and detail remain available during provider waits;
existing owner retry and provider configuration controls retain their meaning.

**Clean rebuild required:** predecessor recovery stores, including empty ones,
are rejected without modification under ADR-0013. No adoption, migration or
retained-run rewrite is provided. The report schema is unchanged. Diagnostics add
no work/report/member identifiers, queries, headers, payloads, exception text,
credentials or paths; existing scope, generation and sequence provide attribution.
