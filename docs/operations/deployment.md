# Deployment Model

## Supported shape

The normal deployment requires:

- Docker-compatible Linux host;
- one Mechanical Librarian container;
- one persistent `/data` volume on reliable local storage;
- ebook and audiobook mounts when content evidence is enabled;
- network access to the configured library service and permitted providers;
- one HTTP port for the dashboard and API.

Read-only media mounts are strongly recommended and are required for the normal
application-metadata-only mode.

## One-container contract

The image contains the web/API service, scheduler, durable worker, evidence
tools, SQLite runtime, static assets, and AI runner. Users do not deploy an
external database, queue, scheduler, or AI sidecar.

The project should publish:

- a documented `docker run` example;
- a one-service Compose example;
- TrueNAS, Unraid, Synology, and Portainer notes that adapt the same contract;
- a configuration export and restore procedure.

## Persistent paths

All product-owned persistent data lives under `/data`:

```text
/data
├── database
├── artifacts
├── backups
├── cache
├── configuration
├── reviewer-auth
└── support
```

Runtime scratch space is disposable and kept outside `/data`.

Do not place the SQLite database on storage that cannot provide correct local
locking and atomic rename semantics. Network-filesystem support requires an
explicit compatibility decision and tests.

## Initial setup

The setup workflow should:

1. Create or validate the data schema.
2. Require creation of the owner account.
3. Configure and test the library-service URL and token.
4. Validate media mounts and read-only behavior.
5. Connect and test the AI reviewer.
6. Test provider access without treating failure as negative evidence.
7. Configure and verify backups.
8. Run a bounded report-only sample.
9. Enable shadow mode.
10. Offer production writes only after readiness passes.

## Runtime security

- Run application and reviewer work as separate non-root principals.
- Drop unnecessary Linux capabilities.
- Use a read-only root filesystem where runtime compatibility permits.
- Use no-new-privileges and bounded temporary storage.
- Do not mount the Docker socket.
- Do not require host networking or privileged mode.
- Provide Docker secret/file-secret support for service tokens.

## Health endpoints

- Liveness: process and event loop are responsive.
- Readiness: database migrated, configuration valid, required secrets readable,
  and no blocking startup failure.
- Detailed health: authenticated report covering service, mounts, reviewer,
  providers, backups, storage, job queue, and circuit breakers.

External service or provider failure should not necessarily fail container
liveness. It should affect readiness for the specific workflow and appear in
detailed health.

## Upgrades

Users pin stable versions for production. The application validates backups and
migrations before enabling work. A failed upgrade must leave enough state and
documentation to restore the previous image and data volume backup.
