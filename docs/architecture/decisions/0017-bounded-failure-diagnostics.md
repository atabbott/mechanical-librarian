# ADR-0017: Bound provider and reviewer failure diagnostics

- Status: accepted
- Date: 2026-07-28
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

Provider adapters and the isolated reviewer already classified failures, but
their vocabularies were adapter-owned and incompatible. Provider attempt
records also contain retry ordinals and timings, while shadow-review failures
persisted a legacy code, count, and exception-type digest. Copying either form
into reports, CLI failures, or inspection output would expose implementation
details and make exact replay depend on process-local evidence.

Operators need stable distinctions without receiving credentials, paths,
prompts, packets, provider records or bodies, raw process streams, headers,
timings, ordinals, or exception text. The classifications must survive restart
and exact replay without reconstructing a provider or invoking a reviewer.

## Decision

Use one closed diagnostic value at application boundaries. It contains only:

- one closed category;
- one closed component and stage;
- optional retryability and currentness booleans when known; and
- one integer count bounded to 2,147,483,647.

The closed categories are `setup_configuration`, `authentication`,
`lookup_construction`, `provider_declared_error`, `http_non_success`,
`quota_rate_limit`, `empty_no_match`, `malformed_over_bound_response`,
`schema_construction`, `live_schema_rejection`, `spawn_runtime`, `transport`,
`timeout`, `cancellation`, `malformed_output`, `citation_membership`,
`identity`, `currentness`, `persistence`, `adapter_fault`, and `unknown`.
Unknown inputs always map to `unknown`; exception text is never used to
classify them.

`adapter_fault/provider/execution` (#1498) names a lookup the adapter itself
failed: the exception's type alone shows a domain value refusing what the
adapter built, or a programming error in the adapter. It is never retryable and
never enrolled for recovery: the fault is in this build, so retrying would at
best succeed by chance on an answer the adapter should have handled. Any other
raised exception, a transport library's own error included, stays an
unavailable lane with an `unknown` aggregate and keeps its recovery.

Provider adapters retain `ProviderAttemptRecord` only in process memory. The
application maps its category and outcome to bounded deterministic aggregates
and drops provider identity, attempt ordinal, delay, elapsed time, and every
raw response detail before adding diagnostics to `metadata-report-v18`.
Adapters also count optional provider-member/schema rejections at their actual
parser boundary as `schema_construction/provider/schema`. The application
canonically merges those aggregates once with attempt and incomplete-outcome
diagnostics; exact replay admits the persisted result without recounting it.
Missing or malformed optional attempt-record collections and entries map to a
bounded `unknown` aggregate rather than aborting report generation. Report
construction and replay accept only sorted, unique provider aggregates with a
provider component and the category's supported provider stage.
Provider status remains the existing neutral `ProviderStatus`; diagnostics
explain failure shape without changing candidate or availability semantics.
An HTTP 200 Hardcover response with a valid GraphQL `errors` envelope is a
terminal `provider_declared_error` attempt, never a successful response.
Status-only application outcomes also supply their required diagnostic:
discovery-plan overflow maps `query_limit_exceeded` to
provider/lookup_construction/lookup without constructing a provider.

Reviewer adapters may retain bounded raw job evidence inside their existing
owner-private lifetime. Before persistence, their legacy internal signals map
to the same diagnostic value. The exact-current shadow outcome table stores
only its canonical document. `shadow-review-inspect-v2` aggregates those
documents across attempts, binds the case and request identities, and emits no
request, packet, result, process identity, attempt identifier, or ordinal.
Duplicate, split, or out-of-order diagnostic aggregates are rejected.
`shadow-review-failure-v2` emits the same object on stderr. A zero-exit process
becomes a successful result only
after canonical parsing plus exact request identity, citation membership, and
semantic validation. The pinned ordinary reviewer runs in JSONL mode and emits
its internal `response_format` signal only for exit status 1 with empty stderr
and the exact bounded terminal `turn.failed` service rejection for
`response_format 'codex_output_schema'`; that signal maps to
`live_schema_rejection`. Any wire variance or other nonzero exit maps to
`spawn_runtime`, while malformed result documents and zero-exit semantic or
citation failures remain separate. After canonical outer shape and exact
request-ID syntax are established, a typed domain identity mismatch maps to
reviewer/identity/identity before citation normalization, membership, and the
broader semantic catch; missing or malformed identity remains semantic, and no
exception text or result identifier participates in that classification.
Exact repository faults map to persistence/persistence/persistence rather than
runtime at both application and CLI-runner boundaries. A dispatched
attempt with no durable terminal outcome reopens as `unknown` with one bounded
reviewer/unknown/execution diagnostic per such attempt; inspection never emits
an unknown or failed status with an empty diagnostic set. Untyped exceptions
crossing the CLI runner boundary map to reviewer/unknown/execution; typed
reviewer runtime remains reviewer/spawn_runtime/execution.
Shadow failure record, durable replay, application inspection, and CLI
inspection all apply the same application-owned admissibility invariant.
Category, component, stage, retryability, and currentness must exactly match a
closed reviewer producer; provider-only failure shapes cannot be relabelled as
reviewer diagnostics. Persistence/persistence/persistence remains an admitted
typed producer.

The report database accepts only the exact new schema. ADR-0013 applies: prior
development state is rejected before mutation and has no migration, import, or
conversion path. Proposal, packet, request, and result bytes do not contain
these diagnostics and their discriminators do not change.

## Amendment: recovery pacing distinctions (2026-08-27)

Add `burst_throttle` and `daily_quota_exhausted` to the closed provider
diagnostic categories. The application derives both only from exact
adapter-owned authority and applies the same mapping to initial observations
and retry successors. Unknown, missing, malformed, or contradictory authority
continues to map to `quota_rate_limit`; raw reasons and rate-limit headers are
never persisted.

The report discriminator advances to `metadata-report-v29`. Version 28 cannot
represent the two distinctions and requires a clean rebuild under ADR-0013;
there is no adoption or legacy fallback. Provider status, candidate admission,
proposal, packet, reviewer, and target-write authority remain unchanged.

## Amendment: work identity refusal (2026-10-19, #1245)

Add `work_identity_refused` to the closed provider diagnostic categories, at
provider/identity, not retryable. Its count is the products work-identity
admission refused, summed across an audiobook work lane's refused rungs, so a
product refused on two rungs counts twice and a refused rung is no longer
reported as `empty_no_match`. The lane states
the count beside its exhausted ladder; the application lifts it once to the
provider aggregate, and a status-derived `empty_no_match` is dropped only where
refusal is that observation's sole no-match reason. A terminal failure on any
rung keeps that rung's own diagnostic and drops the refusal.

The report discriminator advances to `metadata-report-v50`. **Clean rebuild
required** under ADR-0013: version 49 cannot represent the category or the
ladder, and there is no adoption or legacy fallback. Admission rules, provider
status, ebook lanes, proposal, packet, reviewer, and target-write authority
remain unchanged.

## Amendment: uncorroborated alternative rungs (#1255)

On an audiobook's alternative work lane, a rung whose products admit against
the alternative title but none of which the runtime band corroborates is a
refused rung: its products count toward `work_identity_refused` and the ladder
continues. No new category is added.

## Amendment: over-bound statuses named apart (2026-10-28, #1308)

`malformed_over_bound_response` is replaced by three provider categories at
provider/response, none retryable: `malformed_response` for a `malformed`
status or a `response_malformed` attempt, `oversized_response` for an
`oversized` status, a `response_oversized` attempt or an exhausted
`aggregate_bytes` budget, and `candidate_limit_exceeded` for that status or
attempt. #1294's 53 events under the old name were malformed and
candidate-limited answers, with no response over a byte bound, so one name hid
which happened. The shared provider session names its own malformed and
oversized failures, and Hardcover names a candidate-count overflow
`candidate_limit_exceeded` rather than `response_oversized`.

An identity conflict withholds the status-derived `malformed_response` only
when every `malformed` lane carries the identity diagnostic; an oversized or
candidate-limited lane no longer decides it. That lane keeps its own status,
and its category reaches the provider aggregate only when an attempt names it,
as the shared session and Hardcover do. An
adapter may name an ordinary lookup miss `not_found`, which maps to
`empty_no_match` and is never a `provider_request_failed` event. AudioSilo
names its 404s that way.

The report discriminator advances to `metadata-report-v59`. **Clean rebuild
required** under ADR-0013: a v58 report can name the removed category, and there
is no adoption or legacy fallback. Provider status, admission rules, proposal,
packet, reviewer and target-write authority remain unchanged.

## Consequences

### Positive

- Provider reports, durable reviewer state, offline inspection, and CLI failure
  output use one replay-stable vocabulary.
- Raw provider and reviewer evidence stays inside its existing least-authority
  owner rather than becoming an operational support payload.
- Status semantics remain independent from diagnostics and unknown failures
  fail closed.

### Negative

- Earlier report databases and shadow outcome rows require a clean pre-release
  rebuild.
- Aggregation intentionally cannot answer which attempt, response, process, or
  raw message caused a failure.

### Neutral or deferred

- This decision adds no generic observability, retry, support-bundle, reviewer
  activation, decision, apply, or production-write authority.
- Successful reviewer advice remains validated and persisted internally; the
  aggregate inspection projection is a status surface, not an advice export.

## Alternatives considered

### Persist provider attempts and reviewer digests

Rejected because ordinals, timings, digests, headers, and raw boundary
artifacts are not needed to distinguish the supported categories and would
expand the durable privacy boundary.

### Keep separate provider and reviewer vocabularies

Rejected because CLI, restart, and exact replay would disagree about equivalent
failures and force adapters to own public application semantics.

## Validation

Synthetic tests cover canonical round-trip and aggregate stability, redaction,
malformed UTF-8 and JSON, HTTP non-success, HTTP 200 GraphQL errors,
transport, timeout, cancellation, unknown failures, zero-exit invalid reviewer
output, the exact live schema-rejection signal and its fail-closed variants,
typed request-identity mismatch, exact currentness and persistence producers,
the complete production-signal category matrix, clean-schema rejection,
restart, CLI redaction, optional parser rejection counting, parser/attempt/
incomplete merge without replay inflation, and exact offline replay.
Architecture tests continue to enforce dependency direction.
