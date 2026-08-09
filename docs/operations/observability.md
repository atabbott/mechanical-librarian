# Observability and support

The supported review runtime exposes anonymous process liveness at
`/health/live` and startup/drain readiness at `/health/ready`. Liveness says
only that the HTTPS process responds. Readiness becomes successful only after
the exact-current application store opens, and becomes unavailable before
worker shutdown begins.

Authenticated `GET /api/v1/operations/status` returns
`operational-status-v1`: readiness, aggregate counts, bounded stage timing and
typed incidents. Status distinguishes healthy activity, owner action,
automatic recovery, field/category suspension, authentication failure, backup
failure and service incompatibility. Common service causes aggregate at their
narrow connection, library or category scope. A currentness conflict stays
attached to its opaque work and field. Routine processing creates no external
alert.

Timing is disabled by default. Set the documented
`review.diagnostic_timings_enabled` configuration field to retain at most 256
stage duration/wait-class records in the owner-private data area. The closed
stages are extraction, provider search, policy evaluation, human wait, apply,
verification and reconciliation. Timing is advisory and never audit authority.
Timing-store failure cannot change a preparation, decision or field-effect
outcome; unreadable advisory evidence produces a bounded authenticated 503.

`GET /api/v1/operations/support/preview` shows the exact allow-listed bundle
shape and size. Authenticated, CSRF-bound
`POST /api/v1/operations/support/export` creates it again and re-runs redaction.
The JSON is capped at 64 KiB. It excludes secrets and auth state, unrestricted
environment data, databases, metadata values, excerpts, paths, media, covers
and the private recovery store. Review the preview before sharing an export.
