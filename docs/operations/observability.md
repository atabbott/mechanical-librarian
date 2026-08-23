# Observability and support

The supported review runtime exposes two anonymous operational endpoints:
process liveness at `/health/live` and startup/drain readiness at
`/health/ready`. Neither requires a credential, and the container's own
`HEALTHCHECK` reads readiness on that basis. Liveness says only that the HTTPS
process responds. Readiness becomes successful only after the exact-current
application store opens, and becomes unavailable before worker shutdown begins.

Authenticated `GET /api/v1/operations/status` returns
`operational-status-v1`: readiness, aggregate counts, bounded stage timing and
typed incidents. Status distinguishes healthy activity, owner action,
automatic recovery, field/category suspension, authentication failure, backup
failure and service incompatibility. Common service causes aggregate at their
narrow connection, library or category scope. A currentness conflict stays
attached to its opaque work and field. Routine processing creates no external
alert.

Authenticated `GET /api/v1/activity` is the owner-facing, value-bounded status
projection for continuous guarded application. It reports consented libraries,
prepared origin counts, aggregate plan states, a bounded chronological ledger,
pause, and scoped capability suspensions. It is not audit authority; canonical
field-effect events remain the durable proof. Activity never exposes credentials,
cover bytes, evidence excerpts, unrestricted paths, or a general queue console.
The authenticated Activity page presents those facts as a calm state and outcome
ledger: consent is explicit, pause prevents new claims while existing safe
checkpoints can finish, and removing a library's permission prevents new work
there. It derives bounded readable title and author labels from the exact current
report lineage without exposing report documents, work IDs, or connection IDs as
display identity. A common suspension is grouped only when its persisted reason,
connection, and scope match; Activity reports affected destinations and an honest
indeterminate reconciliation state rather than an invented completion estimate.
It does not add a retry command or duplicate the human decision queue in Attention.
An equal automatic candidate is shown as **No change required** from its
identifier-only immutable ledger fact; it is not pending guarded work and no
write guard or service operation is consulted.

Timing is disabled by default. Set the documented
`review.diagnostic_timings_enabled` configuration field to retain at most 256
stage duration/wait-class records in the owner-private data area. The closed
stages are extraction, provider search, policy evaluation, human wait, apply,
verification and reconciliation. Timing is advisory and never audit authority.
Timing-store failure cannot change a preparation, decision or field-effect
outcome; unreadable advisory evidence produces a bounded authenticated 503.

Preparation status keeps one validated, lifecycle-owned observation repository
open before its two worker lanes start. It is used only for the independent
preparation progress and connection-condition snapshots, is serialized across
foreground readers, and closes only after both lanes stop. Ordinary preparation
actions continue to open their own exact-current repositories; recovery-sidecar
rejection remains fail-closed.

`GET /api/v1/operations/support/preview` shows the exact allow-listed bundle
shape and size. Authenticated, CSRF-bound
`POST /api/v1/operations/support/export` creates it again and re-runs redaction.
The JSON is capped at 64 KiB. It excludes secrets and auth state, unrestricted
environment data, databases, metadata values, excerpts, paths, media, covers
and the private recovery store. Review the preview before sharing an export.
