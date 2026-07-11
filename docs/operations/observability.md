# Observability and Supportability

## Goals

The owner should be able to answer:

- Is the system healthy?
- What is it doing now?
- Why did an item change or remain unresolved?
- What failed, what will retry, and what requires action?
- Are backups and production writes safe?
- Is AI authentication or quota blocking work?

## Structured logs

Logs use stable event names and fields. They include correlation, target, job,
attempt, adapter, policy, and duration identifiers where applicable.

Logs exclude:

- tokens, cookies, passwords, and authorization headers;
- full provider payloads or copyrighted excerpts;
- raw personal filesystem paths when a logical reference suffices;
- reviewer authentication content;
- unbounded HTML, metadata, or exception payloads.

## Metrics

Initial metrics include:

- discovered, changed, skipped, and unresolved items;
- job queue depth, age, retries, and terminal failures;
- evidence outcomes and cache reuse;
- EPUB package and opening-matter coverage, selected-resource counts, bounded
  bytes/text, identifier and edition outcomes, contradictions, parser failures,
  and resource-limit stops;
- opening-credit detection yield, transcription and claim outcomes, bounded
  sample duration, local resource use, and optional hosted usage when that
  future capability is enabled;
- provider calls, latency, rate limits, and systemic failures;
- AI decisions, schema failures, authentication failures, and usage when
  available;
- auto-apply, review, rejection, override, apply, verification, rollback, and
  circuit-breaker counts;
- backup freshness and integrity status;
- processing duration and artifact storage use.

Progress and ETA are advisory diagnostics. They are derived from the persisted
dependency graph and completed comparable work, bounded to the selected run
scope, and include the calculation basis, calculation timestamp, freshness,
sample count and comparison cohort, and an optional uncertainty bound. The API
reports ETA as unknown when evidence is insufficient or stale. ETA, a zero
queue-depth sample, a live process ID, or a watcher heartbeat never establishes
readiness, completion, or authorization.

Metrics labels must avoid titles, authors, paths, identifiers, and other high-
cardinality or private values.

## Audit history

Audit events are user-facing product records, not debug logs. They remain
queryable after log rotation and include sufficient provenance to reconstruct
the decision and apply chain without exposing secrets. They use typed logical
identifiers, kinds, UTC timestamps, and single-line safe summaries. Summaries
use a closed public-safe vocabulary rather than accepting arbitrary text. The
exact permitted phrases are: `apply completed`, `apply failed`, `apply
started`, `apply verified`, `authorization denied`, `authorization granted`,
`authorization revoked`, `backup failed`, `backup verified`, `decision
recorded`, `discovered item`, `item changed`, `item discovered`, `item skipped`,
`item unresolved`, `job completed`, `job failed`, `job queued`, `job retrying`,
`job started`, `plan created`, `plan validated`,
`review approved`, `review rejected`, `review requested`, `rollback completed`,
`rollback failed`, `rollback started`, `verification
failed`, and `verification succeeded`. Every other value fails closed.
Repository outcomes are typed: duplicate
replay, conflicting duplicate, invalid input, unavailability, integrity
failure, and corrupt stored data never expose SQLite exceptions or SQL values.
Stored timestamps use fixed-width UTC microseconds. The initial managed schema
enforces bounded identifiers, the public-safe summary vocabulary, and canonical
timestamps, and includes the chronological index. Recent-event queries use that
index, then event ID for deterministic ties; a malformed selected row returns
`CORRUPT` without exposing its contents.

## Notifications

Default notification posture:

- weekly health and unresolved-queue summary;
- immediate notification for disabled writes, backup failure, repeated apply
  or verification failure, database migration failure, or reviewer
  authentication failure;
- no per-item success spam.

## Support bundle

Generate an explicit, previewable support bundle containing:

- versions and capability matrix;
- redacted configuration shape;
- health and readiness results;
- recent job and failure summaries;
- migration status;
- backup freshness summary;
- bounded redacted logs;
- no media, covers, content excerpts, secrets, or raw production databases.

The user must be able to inspect the bundle before sharing it.
