# Deployment model

## Supported single container

`Dockerfile` and `compose.yaml` define the pre-release installed runtime. It is
one non-root container, one named `/data` volume, one HTTPS port, a read-only
root filesystem, and no runtime package installation or extra application
process. Existing deployments may put the exact `config-v6` document at
`/data/config.json` and keep its referenced owner-only TLS, password and
service-secret files within explicitly mounted private locations. When that
file is absent, store mode instead requires the exact-current owner settings and
verifier in `/data/application.sqlite3` and an owner-only, read-only
`/run/secrets/review-bootstrap.json` containing deploy-side TLS, canonical
origin, trusted proxies, read-only mount mappings, and any contained-reviewer
profile path. Add ebook media mounts as read-only Compose overrides; never grant
a writable media mount or Docker socket.

The published mapping is `127.0.0.1:8443:8443`, so the review surface reaches
the host and nothing further. Exposing it to a LAN is an owner decision rather
than a default: republish it as `8443:8443`, or on a chosen host address, only
together with the canonical origin, certificate names, and network path that
decision implies. Nothing but TLS and the owner password stands in front of that
surface.

The container declares its own health. `HEALTHCHECK` runs the installed
`mechanical-librarian-container-health` and the Compose `healthcheck` block runs
the same command, so `docker inspect` and an orchestrator both see the readiness
defined in [observability and support](observability.md). That probe reads
`/health/ready` over loopback HTTPS and verifies the connection against the
configured certificate as its own trust anchor and the configured canonical
origin as its hostname. It presents no credential, returns no library data, and
adds no `curl` or `wget` to the image.

Compose also bounds the blast radius of a runaway container with `mem_limit`,
`memswap_limit`, and `pids_limit`. Each value is sized from a measured run of
this image, and `compose.yaml` records that measurement beside the setting so a
later reader can tell it from a guess.

Both healthchecks set a start interval, which needs Docker Engine 25 or newer
and Compose v2.20 or newer. That is the newest runtime either file requires;
nothing else in them assumes a version that recent.

On an absent data volume the entrypoint creates the exact-current application
and catalogue stores and private artifact area. Store-mode review startup then
refuses until #745 has atomically established complete owner settings and an
owner verifier; this change introduces no implicit first-run defaults. On
restart it accepts only exact-current, owner-only stores. A schema mismatch,
missing/incomplete settings, an unowned volume, invalid file or bootstrap,
backup/recovery error, or permission failure stops startup rather than falling
back, importing, migrating, or repairing data. A present `/data/config.json`
always selects file mode, even when invalid; file mode never opens the settings
store. Store-mode restart debt remains pending unless the whole runtime graph
composes and acknowledges its exact settings revision.

Use `docker compose up --build` for a local installed-product qualification.
`scripts/qualify_container_lifecycle.py --source-sha <exact-head>` repeats the
head-bound build, synthetic owner-secret/TLS seeding, non-root read-only-root
run, health probes, the container's own `HEALTHCHECK` verdict, bounded graceful
termination and exact-current restart.
Stop with `docker compose down`; do not add `-v` unless intentionally deleting
the complete local application state. The public gate's `container-image` job
runs `docker build` at every candidate revision, so a Dockerfile regression
fails there. That build reads no `compose.yaml`, and running the built image
remains maintainer-manual through the commands above. Image publication
remains a separate release authority and is not part of this support contract.

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

Each open browser tab holds one long-lived `text/event-stream` response on that
same listener, which is worth knowing when sizing the direct edge's 100-
connection admission or placing a reverse proxy in front of it: the response
sets `X-Accel-Buffering: no`, and a proxy that buffers or times out idle
responses will turn a live surface into a polling one rather than break it. The
stream carries a comment heartbeat so an idle connection stays observably alive,
bounds its own lifetime so no connection outlives a working day, and refuses a
subscriber beyond its bound with a plain `503` rather than growing without
limit. Shutdown closes the update channel before the workers wait, so every
open stream ends as an ordinary completed response inside the same configured
graceful-shutdown deadline; the server's own graceful-shutdown timeout is set
from that deadline so a client that stops reading cannot hold the process open.
While a stream is down the surfaces fall back to polling and say so, so a
buffering proxy degrades freshness and never correctness.

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

Run as a non-root dedicated identity with an explicit `config-v6` file and
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
verification, with bounded search over the owner-readable values composed from
each event's named immutable authority, type and date filters. It never offers
queue administration, blind retries, telemetry, exports, or extra write
authority; recovery and consent remain the existing server-validated actions.
