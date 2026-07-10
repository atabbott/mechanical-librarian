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

Metrics labels must avoid titles, authors, paths, identifiers, and other high-
cardinality or private values.

## Audit history

Audit events are user-facing product records, not debug logs. They remain
queryable after log rotation and include sufficient provenance to reconstruct
the decision and apply chain without exposing secrets.

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
