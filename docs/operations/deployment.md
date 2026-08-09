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

Shutdown first closes preparation and guarded-effect claim admission. Active
preparation workers receive the configured bounded graceful-shutdown window;
their durable checkpoint remains restart authority. Guarded-effect restart is
an injectable lifecycle seam and remains unavailable in the normal container
until a real target adapter is supplied—no fake or product write activation is
composed by this milestone.

The command-line development runtime remains unreleased.
It supports current catalogue, report, ProposalSet, ReviewCase, and
qualification-only shadow-review operations; it is not a daemon or published
container release.

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

Reviewer authentication, shadow request, and explicit reevaluation additionally
require an owner-only qualification profile, private `0700` auth-store, empty
private job parent, and the exact maintainer-provisioned Codex CLI and
Bubblewrap artifacts declared by that profile. Preview and inspect remain
offline: they require the complete owner-only canonical qualification profile
file and application database, validate the profile bytes, and do not require
or inspect its declared executable, auth-store, or job-parent paths. The runtime
neither installs, upgrades, nor bundles the external artifacts; their profile,
authentication, and isolation requirements are authoritative in the
[shadow-review guide](shadow-review.md).
Authentication and shadow advice do not activate normal review or grant
decision, apply, or write authority.

The process needs no HTTP port, queue, scheduler, worker, external database, or
writable media mount. Do not mount the Docker socket or grant host networking,
elevated capabilities, or write access to media. Keep the root filesystem
read-only where packaging permits.

## Backup and recovery

Back up the stopped application SQLite database together with its paired cover
artifact store using the recovery procedure in
[backups and recovery](backups-and-recovery.md). Keep configuration,
qualification profiles, executable paths, and auth stores in the host's
encrypted secret-backup policy.

The review runtime can gain bounded target-field authority only through the
authenticated Activity consent flow. Consent is library scoped, pause and
revocation stop new claims, and all target calls use the existing API-key
adapter and guarded application service. Media mounts remain read-only; release,
deployment publication, dashboard, bulk, rollback, and destructive authority
remain outside this contract.
