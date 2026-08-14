# Deployment model

## Supported single container

`Dockerfile` and `compose.yaml` define the pre-release installed runtime. It is
one non-root container, one named `/data` volume, one HTTPS port, a read-only
root filesystem, and no runtime package installation or extra application
process. Put the exact `config-v5` document at `/data/config.json` and keep its
referenced owner-only TLS, password and service-secret files within explicitly
mounted private locations. Add ebook media mounts as read-only Compose
overrides; never grant a writable media mount or Docker socket.

On an absent data volume the entrypoint creates the exact-current application
and catalogue stores and private artifact area, then starts the existing review
server. On restart it accepts only those exact-current stores. A schema mismatch,
unowned volume, invalid configuration, backup/recovery error or permission
failure stops startup rather than importing, migrating or repairing data.

Use `docker compose up --build` for a local installed-product qualification.
`scripts/qualify_container_lifecycle.py --source-sha <exact-head>` repeats the
head-bound build, synthetic owner-secret/TLS seeding, non-root read-only-root
run, health probes, bounded graceful termination and exact-current restart.
Stop with `docker compose down`; do not add `-v` unless intentionally deleting
the complete local application state. Image publication remains a separate
release authority and is not part of this support contract.

The image build has an exact pinned Node 24.19.0/npm 11.17.0 stage that
regenerates browser DTOs from the committed OpenAPI document and emits the
validated React asset bundle. The Python build admits only that bundle, and the
final image contains neither Node/npm nor frontend source, generated DTOs,
`node_modules`, Vite metadata, or source maps. Library, Attention, review detail,
Policy, and Activity use that one persistent shell; `/login` remains the small
server-rendered authentication page.

The normal container composes the supported Audiobookshelf guarded target, but
remains report-only until the owner completes the authenticated, library-scoped
Activity consent flow and every backup, currentness, capability and claim gate
passes. Shutdown closes both preparation and guarded-effect claim admission
before either worker waits. They share one configured graceful-shutdown
deadline; an over-deadline worker remains tracked, raises a typed shutdown
failure before the shared repository closes, and resumes from its durable
checkpoint on restart. No fake target, direct database write or writable media
path is composed.

The installed command surface contains `review-serve`, read-only
`policy-preview`, owner-explicit `cover-review`, and read-only
`review-activity`. The latter two reuse the same cover decision/history and
guarded Activity application services as HTTP/React; neither bypasses consent or
write gates. Catalogue, report, ProposalSet, ReviewCase, batch, and
qualification-only reviewer experiments are not installed commands or container
entrypoints; contributor-only access is
source-bound as documented in the
[workbench guide](../development/contributor-workbench.md).

## Runtime prerequisites

Run as a non-root dedicated identity with an explicit `config-v5` file and
application and catalogue database paths on reliable local storage. Each
database path is either absent for its clean first invocation or already holds
the exact-current schema. The owner-controlled artifact-store directory must
already exist. When using batches, provide an immutable manifest path. Target
and configured provider secrets must remain owner-only (Google Books may
instead use credential-free access), and ebook media mounts are read-only. The
configuration and supported egress rules are defined in the
[configuration reference](../reference/configuration.md) and [security
model](../architecture/security.md).

The installed runtime has no generic reviewer service, model-selected write, or
raw model-output persistence. Cover consideration may invoke the bounded
contained visual observer when an exact qualified private profile and credential
are configured. Only locally admitted closed observation or typed failure
evidence is persisted, and deterministic policy plus an explicit owner choice
remain authoritative. If the observer is absent, unavailable, or unqualified,
the supported manual cover path remains available. Repository-only qualification
artifacts are not deployment prerequisites and never enter the wheel or
container.

The process exposes one configured HTTPS port and runs its persisted preparation
and guarded-effect workers in the same Python process. It needs no external
queue, scheduler, database, Node runtime, or writable media mount. Do not mount
the Docker socket, grant elevated capabilities, or write-enable media. Allow
only the network paths required for the configured owner access, target service,
and enabled metadata providers; keep the root filesystem read-only.

## Backup and recovery

Back up the stopped application SQLite database together with its paired cover
artifact store using the recovery procedure in
[backups and recovery](backups-and-recovery.md). Keep configuration,
runtime configuration in the host's encrypted secret-backup policy.

The review runtime can gain bounded target-field authority only through the
authenticated Activity consent flow. Consent is library scoped, pause and
revocation stop new claims, and all target calls use the existing API-key
adapter and guarded application service. Media mounts remain read-only; release,
deployment publication, dashboard, bulk, rollback, and destructive authority
remain outside this contract.

The authenticated Activity ledger is a private read projection, not an
operations console. It pages a deterministic newest-first union of durable
review decisions, policy transitions, safeguards, guarded effects, and
verification, with bounded search, type and date filters. It never offers
queue administration, blind retries, telemetry, exports, or extra write
authority; recovery and consent remain the existing server-validated actions.
