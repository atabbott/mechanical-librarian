# Backups and recovery

Back up the complete `/data` volume while the container is stopped, together
with your deploy-side files. The application database and the cover artifact
store are a pair: always back up and restore them together.

There is no import, selective restore, per-record purge, repair or migration
command. To recover, you restore a matching backup or start clean. Never edit
database rows, pointers or artifact files to force a store open.

## What to keep

| Item | Path | Notes |
| --- | --- | --- |
| Application database | `/data/application.sqlite3` | Reports, proposals, decisions, policy, consent, write history. In store mode also owner settings and secrets. |
| Cover artifact store | `/data/artifacts/` | Must match the database. Files are named by the SHA-256 of their content. |
| Provider-recovery database | `/data/private/provider-recovery.sqlite` | Restore it from the same backup as the application database. Startup refuses a recovery store whose generation is newer than the configuration. |
| Session database | `/data/private/review-sessions.sqlite` | Optional. Losing it costs one sign-in. Holds only token and client digests. |
| Catalogue database | `/data/catalogue.sqlite3` | Optional; regenerable. |
| Codex sign-in | `/data/reviewer/auth/` | Present only if you use the contained Codex reviewer. A credential. |
| Deploy-side files | outside `/data` | Store mode: `review-bootstrap.json` and its TLS files. File mode: `/data/config.json` and every secret, password-hash and TLS file it names. |

Where each store's schema and recovery rules are defined is in
[SQLite topology](../architecture/sqlite-topology.md).

## Taking a backup

1. Stop the container gracefully with `docker compose down` (without `-v`).
   Shutdown lets in-flight work reach a durable checkpoint first; see
   [deployment](deployment.md#starting-stopping-and-restarting).
2. Copy the complete `/data` volume, preserving ownership (UID 65532) and
   owner-only modes. Startup refuses files with the wrong owner or mode. Copy
   the deploy-side files with it, under your encrypted secret-backup policy.
3. Verify the copy:
   - run `PRAGMA integrity_check` and `PRAGMA foreign_key_check` on the copied
     application database;
   - check that each file in `artifacts/` has a SHA-256 equal to its name.
     Do not print or log the files' contents.

There is no built-in backup verifier. The runtime checks each artifact's
digest on every read, so a damaged artifact is refused rather than served.

## Protecting backups

A complete backup holds library metadata, cited evidence, decisions and write
history. In store mode it also holds service and provider secrets. Keep
backups owner-only and encrypted at rest, set a retention period and destroy
expired copies through your storage system.

The Settings page, the API and support bundles never return secrets, so none
of them is a way to export credentials.

## Restoring

1. Stop the container.
2. Restore the application database and artifact store from the same backup.
   Never pair a database with another backup's or another install's
   artifacts: on start the runtime deletes artifacts the database does not
   reference.
3. Restore the matching deploy-side files. They must pass the same validation
   as at any start.
4. Start the container.

Restore only into a build whose schemas match the backup. Otherwise startup
refuses the stores without changing them, and you need a clean rebuild; see
[upgrades](upgrades.md). Remove any `/data/config.json` left over from earlier
use before you restore a store-mode backup. If that file is present, even if it
is invalid, the container runs in file mode and ignores the restored settings.

Restore the whole database, never parts of it: records such as cover
considerations are refused if the reports and artifacts they cite are missing.

Restoring does not change Audiobookshelf. On restart, consent, pause and
authority are reloaded from the restored state, and interrupted writes recover
as described below.

Starting clean (an absent database and an empty artifact store) regenerates
reports from the library but loses history and, in store mode, settings.

### Session database refused

The session database opens only when absent or at its exact current schema.
Anything else stops startup without changing the file. To recover:

1. Stop the container.
2. Move the file out of `/data/private/` (keep it if you need to diagnose it).
3. Start the container. A fresh session database is created.

Every browser must sign in again and at most five minutes of failed-sign-in
throttle history is lost. Do not edit the file in place.

## Guarded field-effect recovery

A field effect is one planned write to Audiobookshelf. Restart reads each
persisted state and never assumes a write completed:

| State at restart | What happens |
| --- | --- |
| `planned`, `retryable` | Crosses every gate again before it can be claimed. |
| `claimed` | Nothing was sent. Returns to `retryable` and needs a fresh claim. |
| `applying`, ambiguous | Reads the target field back, then reconciles. |
| `verified`, `stale`, `superseded`, `abandoned` | Stays terminal. Any pending cover cleanup is retried. |

Reconciliation compares the read-back value:

- the desired value: the effect is verified, with no second request;
- the value captured before the write: the effect may be queued again, only
  under current consent and with a current plan;
- any other value: the effect is stale and the field is re-evaluated.

Each recovery pass handles at most 100 read-back or re-evaluation actions, then
at most 100 pending cover cleanups. An interrupted action stays restartable.

Never repeat an ambiguous write by hand. Another change to the same field
during a write can be overwritten, because Audiobookshelf has no
compare-and-set; the reconciliation outcome in Activity records what happened.
The per-write rules are in
[security](../architecture/security.md#consent-and-guarded-effects).

### Cover recovery files

Before replacing a cover, the runtime captures the current cover to a private,
owner-only file so the write can be reconciled. The file is removed when that
cover's work ends, with a failed removal retried on restart. It is not a backup
and not an undo. A failed or invalid capture blocks only that cover.

### Suspended destinations

Some failures suspend writes until they are resolved. Suspensions survive
restart.

- **Connection suspension.** Expired credentials, an inactive account, loss of
  library or item access, or a timeout or dropped connection during a guarded
  read or write. A later successful capability check clears it, and retried
  plans proceed.
- **Destination suspension.** A permission failure, an incompatible field
  shape, or an unexpected write response. Other destinations stay eligible. A
  fresh successful capability check clears it if it disproves the cause.
- **`unexpected_effect_behavior`.** Any other non-success response, such as a
  proxy's 5xx, or an HTTP protocol error. A capability check never clears it.

A denied readiness probe immediately removes that destination from Library and
Attention. Before you grant or extend consent, Activity lists every included
and excluded destination, and the server refuses the consent if a fresh probe
no longer matches what you saw.

Problems that block the runtime itself (permissions, disk space, a read-only
filesystem, failed cleanup) show as degraded status in
[observability](observability.md). Fix the cause and restart; do not edit the
database or recovery files to get past them.
