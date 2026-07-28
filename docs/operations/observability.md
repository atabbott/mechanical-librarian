# Observability

The current 0.10 runtime is a set of synchronous CLI operations, not a daemon.
It has no health endpoint, metrics server, support-bundle generator, verbose
mode, or background worker. Use the supported command output and durable
inspection surfaces rather than direct database access.

## Command outcomes

Catalogue refresh/list/get, metadata-report generation and reopening,
ProposalSet creation/reopening, ReviewCase admission/reopening, batch
preview/inspect, and reviewer authentication and shadow-review operations emit
their command-specific canonical JSON on success. `batch run` streams canonical
JSON lines followed by its final summary. Cover preview is the sole binary
surface: it writes only the requested report-bound preview bytes to a
non-interactive stdout.

Failures are command-specific and bounded. Metadata, catalogue, ProposalSet,
and batch commands use fixed redacted diagnostics; ReviewCase and
qualification-only reviewer commands emit their closed typed failure objects.
The reviewer failure discriminator is `shadow-review-failure-v2`.
Successful and failed commands use their documented exit status. Failure
diagnostics and logs never expose credentials, private paths, target values,
raw provider bodies, EPUB text, reviewer reasoning, or raw reviewer streams.
ADR-0017 additionally forbids exception text, provider records, prompts,
packets, headers, timings, digests, and attempt ordinals from public failure
status.
Successful canonical output is private application data and can carry
the bounded documented target/display values for that command; raw provider
bodies, EPUB text, credentials, and reviewer reasoning or streams remain
excluded by the relevant contract.

## Durable inspection and recovery

Inspect durable state through `catalogue list`/`get`, `batch inspect`,
`metadata-report-get`, `proposal-set-get`, `review-case-get`, and
`shadow-review-inspect`. These surfaces validate and reopen the documented
canonical records without relying on direct SQLite queries or row edits.
Shadow inspection v2 is deliberately aggregate-only: it returns the exact
request identity, currentness, status, bounded attempt count, and canonical
diagnostics, never the stored packet, result, process identity, or attempt
history.

For command prerequisites, exact output forms, and recovery, use the
[catalogue](catalogue.md), [metadata-report](metadata-report.md),
[ProposalSet](proposal-set.md), [ReviewCase](review-case.md),
[shadow-review](shadow-review.md), and
[backups-and-recovery](backups-and-recovery.md) guides. The clean-state and
authority boundary remains defined by the [compatibility reference](../reference/compatibility.md).
