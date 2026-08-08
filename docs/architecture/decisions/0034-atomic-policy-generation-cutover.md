# 0034 — Cut over metadata policy with one atomic generation

## Status

Accepted for F09p #477.

## Context

Activating a reviewed policy must change current queue authority without ever
showing old- and new-policy cases as simultaneously actionable. Existing
eligible evidence should be reused, while human decisions and policy-bound
proposals must not transfer to successors.

## Decision

Activation revalidates the immutable draft, its exact canonical per-library
resolutions, qualification, and the current unapplied cohort in one immediate
database transaction. The transaction records one new generation, retires all
exact-current cohort pointers (using the same report/file joins as queue
authority), fixes the transition denominator, links each item and exact report
to its existing F17a work, and only then makes the generation current.

The resolved per-library documents are persisted with the generation and are
replayed during proposal publication and review admission. `Ask` retains the
manual evaluator result; `Ignore` produces a typed, non-actionable
`policy_ignored` result. Unqualified `Automatic` cannot be activated and does
not grant decision or write authority.

Policy-independent extraction and provider work may finish. Later policy-bound
stages are rewound to proposal evaluation from the same immutable report and
eligible evidence. Missing work becomes a durable typed transition failure and
can be explicitly retried if F17a later supplies the work; no provider is
reacquired solely for cutover.

## Consequences

Old-generation evaluation cannot publish after the generation changes, and an
old human submission is stale. New work uses the current generation but is not
added to the fixed transition denominator. Aggregate completion, failures,
retry, and acknowledgement survive restart. Acknowledgement hides only the
transition notification; history and diagnostics remain.

A response-loss replay naming the exact original from/to pair returns that
generation's transition. Any other stale pair fails. A following activation is
blocked while the current transition is processing or retains unacknowledged
failures; a completed transition, or an acknowledged failed completion, may be
followed by another explicit generation. Historical proposal validation
resolves its immutable destinations by resolved identity rather than assuming
the currently active profile.

This adds no second queue, staging batch, migration path, automatic authority,
apply plan, target/media write, provider credential access, or destructive
operation.
