# Upgrades

Mechanical Librarian is pre-release: an upgrade either starts normally or
refuses a store that needs a clean rebuild, under the rule in
[Compatibility](../reference/compatibility.md#the-clean-state-rule).

[CHANGELOG.md](../../CHANGELOG.md) marks every change that needs one with
**Clean rebuild required.** Read the entries between your build and the new
one before you upgrade.

## Before every upgrade

1. Read the changelog entries since your build.
2. Stop the container gracefully (`docker compose down`, without `-v`).
3. Take a complete backup of the stopped `/data` volume, as described in
   [Backups and recovery](backups-and-recovery.md). The application database
   and `/data/artifacts` are a pair; keep them together.
4. Keep your deploy-side material: in file mode, `/data/config.json` and every
   secret and TLS file it references; in store mode, the
   `review-bootstrap.json` document and its TLS material.

## Upgrading without a rebuild

If no entry since your build says **Clean rebuild required**, rebuild and start
as usual:

```text
docker compose up --build -d
```

Startup opens the existing stores, and restart recovery resumes any work that
was in flight. Reload any browser tab left open: the workspace refuses changes
from a page built for a different bundle.

## Clean rebuild

A clean rebuild means starting the application with an absent application
database and an empty artifact store. It is needed when the changelog says so,
or when startup refuses the application database.

What you lose from Mechanical Librarian: reports, proposals, review cases,
owner and automatic decisions, consents, cover work, guarded-write history and
preparation progress. In store mode you also lose the owner settings, because
they live in the application database. Audiobookshelf is not touched: values
already written stay written. Preparation regenerates reports and review cases
from the library.

### File mode

File mode is selected whenever `/data/config.json` exists.

1. Stop the container and back up `/data`.
2. Move the old `/data` contents somewhere outside the volume and keep them
   offline if their history matters. The new build will not read them.
3. Put back only `config.json`. Leave the secret, password-hash and TLS
   mounts it names as they are. If a changelog entry names a new configuration
   version, write that version instead; see
   [Configuration](../reference/configuration.md).
4. Start the container. It creates fresh stores, and preparation regenerates
   reports.
5. Sign in, then grant consent again in Activity before anything is written.

### Store mode

Store mode is used when `/data/config.json` is absent. Owner settings are in
the application database, so a clean rebuild repeats first-run setup.

1. Stop the container and back up `/data`.
2. Move the old `/data` contents out of the volume, or start with a new empty
   volume. First-run setup only starts on an empty volume.
3. Start the container. Read the new setup claim token from
   `docker compose logs mechanical-librarian`.
4. Complete the setup steps in
   [First-run setup](deployment.md#first-run-setup). Your
   `review-bootstrap.json` is reused as it is.
5. Grant consent in Activity before anything is written.

## Rebuilding one store

Some refusals affect one store only. Fix only that store.

| Refused | What to do |
| --- | --- |
| Session database (`/data/private/review-sessions.sqlite`) | Follow [Session database refused](backups-and-recovery.md#session-database-refused). |
| Incomplete first-run setup state | Start setup again on an empty `/data` volume. |
| Configuration document | Replace it with the current version (`config-v9` or `review-bootstrap-v2`). No conversion is provided. |
| Catalogue (`/data/catalogue.sqlite3`) or provider-recovery database (`/data/private/provider-recovery.sqlite`) | Stop the container, move the file aside, restart. A fresh store is created; the next refresh rebuilds the catalogue. Pending provider recovery jobs, retry backoff and their history are lost. |

## Changes that need owner action without a rebuild

- **Consent lapses when write destinations change.** Library consent is bound
  to the whole set of write destinations. A build that changes that set lapses
  every existing consent. Activity and the Library write notice say why. Grant
  consent again in Activity; nothing is planned, claimed or scheduled until you
  do. Activating a new metadata policy does not lapse consent.
- **A different Python can need a rebuild.** If the new build's Python has
  different Unicode data, startup refuses the application database
  ([Compatibility](../reference/compatibility.md#the-clean-state-rule)). Do a
  clean rebuild.
- **Open pages must be reloaded.** A page from an older bundle is refused on
  every unsafe request.

## What is refused

Everything listed under
[Not supported](../reference/compatibility.md#not-supported) is refused. Two
upgrade-specific cases:

- Editing rows, pointers, schema or artifact files to force a store open is
  refused at the next open, or produces state the runtime will not trust.
- Restore only into a build whose schemas match the backup; see
  [Backups and recovery](backups-and-recovery.md). Otherwise do a clean
  rebuild.

`scripts/qualification_state_bridge.py` is a maintainer tool for private test
state, not an upgrade path. It accepts only one exact retained test schema and
targets a frozen, older contract.
