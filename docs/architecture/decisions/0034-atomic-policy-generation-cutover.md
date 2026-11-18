# 0034 — Cut over metadata policy with one atomic generation

## Status

Accepted for F09p #477. Amendment 1 (a work written since its report is read
again) is proposed.

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

Generation one binds an exact configured sparse override on first loaded use;
the stored resolution is then authoritative and conflicting configuration is
rejected. After activation, a newly configured library may be represented in
read-only preview, but proposal execution remains unavailable until the next
explicit activation persists that library's resolution.

Policy-independent extraction and provider work may finish. Later policy-bound
stages are rewound to proposal evaluation from the same immutable report and
eligible evidence. Missing work becomes a durable typed transition failure and
can be explicitly retried if F17a later supplies the work; no provider is
reacquired solely for cutover. Existing terminal extraction/provider work is
restarted at its safe stage during activation, and atomic extraction publication
updates the fixed transition when its report is stale or its provider
disposition is terminal.

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

## Amendment 1: a work written since its report is read again (proposed, #1986)

The rewind to proposal "from the same immutable report" assumed that report
still says what the library holds. After a write the product verified it does
not: proposal and admission refuse it as stale, so the item failed and the
work waited, caseless, for the next library scan. Leaving Automatic after an
automatic write reached this every time.

- **The predicate.** A report predates a verified write when the item's
  `verified` field effect that landed last was planned against a case built on
  that report's target. Only the last one counts: once a later write puts a
  value back, the earlier target is what the library holds again. A write
  still in flight does not count: one that lands after the restart is refused
  at proposal, as before, and a retry then reads the work again.
- **Read again, not re-proposed.** Activation, leaving Automatic (which
  reactivates the unchanged policy), transition retry and the projection
  recovery that restarts a work from its current report restart such a work at
  extraction, as a fresh start rather than a scheduled search. Every other work
  restarts exactly as before; leaving Automatic still returns an unwritten
  work's same case.
- **Discovery first.** When the work's scan is idle, activation and retry queue
  that scan for discovery with the standard reset, so the work's written title
  or authors are listed before it is extracted and the next scan does not
  extract it again. The other works of that library are carried forward at
  their restarted stage. Activation no longer turns a scan that was queued and
  not yet started into a processing one: its discovery still runs.
- **The item follows the work.** When extraction publishes a report other than
  the item's, the item fails stale as before unless its report predates a
  verified write. Then it is rebound to the published report, stays pending
  and completes with the work, or completes at once when that publication
  completes the work. A scheduled search that keeps the previous report leaves
  the work on it, so its item fails stale and a retry reads the work again.
- **A work that leaves the library.** Discovery deletes the work of an item it
  no longer lists. Its transition items stop naming it first, and a pending
  one fails as `pipeline_unavailable`, so the transition can finish.

This reverses "no provider is reacquired solely for cutover" for these works
only: a re-read with changed authors or title can ask providers again. A
re-read report is a new report, so the work gets a new proposal set and a new
case, starting a new episode; holds carry by work (ADR-0085). Nothing in the
schema, persisted documents or versions changes.
