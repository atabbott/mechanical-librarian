# Upgrades

Mechanical Librarian is pre-release: an upgrade either starts normally, asks
you to run the offline `regenerate-derived` command, or refuses a store that
needs a clean rebuild, under the rule in
[Compatibility](../reference/compatibility.md#the-clean-state-rule).

[CHANGELOG.md](../../CHANGELOG.md) marks every change that needs the command
with **Derived regeneration required**, and every change that needs a rebuild
with **Clean rebuild required.** Read the entries between your build and the
new one before you upgrade.

## Before every upgrade

1. Read the changelog entries since your build.
2. Let waiting writes finish in Activity before you stop the container.
3. Stop the container gracefully (`docker compose down`, without `-v`).
4. Take a complete backup of the stopped `/data` volume, as described in
   [Backups and recovery](backups-and-recovery.md). The application database
   and `/data/artifacts` are a pair; keep them together.
5. Keep your deploy-side material: in file mode, `/data/config.json` and every
   secret and TLS file it references; in store mode, the
   `review-bootstrap.json` document and its TLS material.

## Upgrading without a rebuild

If no entry since your build is marked **Derived regeneration required** or
**Clean rebuild required**, rebuild and start as usual:

```text
docker compose up --build -d
```

Startup opens the existing stores, and restart recovery resumes any work that
was in flight. Reload any browser tab left open: the workspace refuses changes
from a page built for a different bundle.

## Upgrading with a regeneration

A **Derived regeneration required** entry changes only what preparation
rebuilds from the library, its providers and your decisions. Startup refuses
such a database without changing it, with one line: "This database was
prepared by another build. Stop the container, run docker compose run --rm
mechanical-librarian regenerate-derived, then start it again. Upgrades has the
steps."

`regenerate-derived` keeps your settings, consents and write history, and each
answer and cover choice as its report lists. It drops everything preparation
made and leaves preparation to make it again. It runs on the stopped volume,
refuses while a server holds it, and contacts neither Audiobookshelf nor any
provider.

1. Stop the container and back up `/data`, as in
   [Before every upgrade](#before-every-upgrade).
2. Build the new image with `docker compose build`.
3. Run the command:

   ```text
   docker compose run --rm mechanical-librarian regenerate-derived
   ```

4. If it stops at its report, read it, then run it again with
   `--retire-pending` ([The report](#the-report)).
5. When it prints `Regenerated.`, start the server with
   `docker compose up -d`.

The command exits 0 when it regenerated or found nothing to do, 1 when it
refused or stopped at its report, and 2 when given anything but
`regenerate-derived` with or without `--retire-pending`. Whenever it exits 1
or 2, nothing was changed. A run that is interrupted rolls back; run it again.

### Writes still under way

The command first lists work the previous build has to finish, grouped by
reason, then each work by library, work item id and field. A write that may
have reached Audiobookshelf always stops it, with or without the flag. A
write waiting to be sent, a Resume automatic handling waiting for its scan,
and a policy change still checking works are listed above the report; the
run goes ahead only with `--retire-pending`, which retires them. Where it can
tell offline, the reason says why the work cannot finish now: writes paused,
a suspension, preparation paused, or a library no longer in the
configuration. Each reason ends with what to do; a library no longer
configured has to be added back in Settings on the previous build first.

To let the work finish on the previous build:

1. Check out the version you ran before, and start it with
   `docker compose up --build -d`.
2. With the connection reachable, wait until Activity shows the work done.
3. Stop the container, check out the new version again, and run the command
   again.

### The report

Before it changes anything, the command reports per library, naming works
only by item id and field, never by title, author, path or value. A list
with nothing in it is left out:

- answers not yet written, as a count, each kept if the next read asks the
  same question and asked again otherwise;
- answers whose waiting write `--retire-pending` retires;
- answers already written, as a count;
- descriptions you wrote that are not yet in Audiobookshelf, each asked
  again, with Automatic staying off the description;
- cover choices not yet written, and whether the work keeps your last written
  cover or has no cover choice left;
- writes stopped for your decision, which Activity no longer counts as
  needing you.

Counts can include answers from earlier reads, so they are upper limits. When
anything is listed above the report, or any of the last three lists is not
empty, the command stops after the report and changes nothing. Read the
report, then run it again with `--retire-pending` to go ahead:

```text
docker compose run --rm mechanical-librarian regenerate-derived --retire-pending
```

With the flag the command closes the waiting writes, open Resume requests
and policy checks it listed, and prints the report again as done. An answer
whose waiting write it closes is asked again, and Automatic stays off that
field. A policy change it closes reads as finished with failures and cannot
be retried.

### The restore point

Before it changes anything, the command copies the application database,
checks the copy against it, and keeps it as
`/data/regeneration-backup/epoch-<n>.sqlite3`, replacing any earlier copy. The
folder is private (`0700`) and the file owner-only (`0600`). A
`regeneration-backup` that is not such a folder, or that holds anything else,
refuses the run. The copy is the whole application database, with the owner
settings and their secrets in store mode, so protect it as you protect your
backups.

The volume needs free space for four times the application database: the
copy, the rollback journal, and crash recovery's copies of the file and
journal. The command checks this before its first write, and a volume that
fills while it runs stops it with the same message. After an interrupted run,
while nothing has changed the database since, the copy that run left counts as
free space, since running the command again replaces it: the command removes
it just before taking the new copy. Any other earlier copy takes space;
deleting `/data/regeneration-backup` frees it.

While the folder holds anything, unused cover files in `/data/artifacts` are
kept and released cover originals are not removed, so the copy and the
artifact store stay a pair. An empty folder holds nothing back.

To go back to the build before the regeneration:

1. Stop the container.
2. Move `/data/private/provider-recovery.sqlite` out of the volume. Keep
   `private/provider-responses.sqlite`.
3. From the volume's directory on the host, put the copy in place of the
   database, owned by the container user:

   ```text
   sudo install -m 0600 -o 65532 -g 65532 regeneration-backup/epoch-<n>.sqlite3 application.sqlite3
   ```

4. Start the previous build, as in
   [Writes still under way](#writes-still-under-way).

Anything you did in Mechanical Librarian since the regeneration is lost.
Values already written to Audiobookshelf stay written.

Once you are satisfied with the new build, delete `/data/regeneration-backup`.
The container user (UID 65532) owns it, so from the volume's directory on the
host run:

```text
sudo rm -r regeneration-backup
```

Unused cover files are then removed as usual.

### After a regeneration

Preparation reads every configured library from the start, reusing stored
provider answers, and a preparation pause you set stays. The first read of
each work keeps your answer on a field when it asks the same question, and
asks again when the choices or the value in Audiobookshelf changed. Activity
keeps your rows, every write and every "no change required" row, but loses
Automatic's other rows from before the regeneration. Provider recovery jobs
from before it fail closed.

## Clean rebuild

A clean rebuild means starting the application with an absent application
database and an empty artifact store. It is needed when the changelog says
**Clean rebuild required**, or when startup refuses the application database
without naming `regenerate-derived`.

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
3. Put back only `config.json`, and the provider answers if you are
   [keeping them](#keeping-provider-answers). Leave the secret, password-hash
   and TLS mounts it names as they are. If a changelog entry names a new configuration
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
   volume. First-run setup only starts on an empty volume, or on one that
   holds nothing but [kept provider answers](#keeping-provider-answers).
3. Start the container. Read the new setup claim token from
   `docker compose logs mechanical-librarian`.
4. Complete the setup steps in
   [First-run setup](deployment.md#first-run-setup). Your
   `review-bootstrap.json` is reused as it is.
5. Grant consent in Activity before anything is written.

### Keeping provider answers

This step is optional and works in both modes. The provider-response database
holds the answers your sources gave to lookups that ended clean
([Held provider answers](metadata-report.md#held-provider-answers)). It holds
no decisions and nothing the new build depends on. If you carry it across the
rebuild, preparation reuses those answers instead of asking every source again
for every book. A volume last run by a build before 0.37.0 has no such file.

1. Before you move the old contents away, copy
   `private/provider-responses.sqlite` out of the stopped volume.
2. In the emptied or new volume, create `private/` with mode `0700` and put
   the file back inside it with mode `0600`, both owned by the container user
   (UID 65532). Put nothing else in `private/`. From the volume's directory
   on the host, with your copy beside you:

   ```text
   sudo install -d -m 0700 -o 65532 -g 65532 private
   sudo install -m 0600 -o 65532 -g 65532 provider-responses.sqlite private/
   ```

3. Continue with the steps for your mode.

In either mode, a `private/` with another owner or mode stops startup. In
store mode, first-run setup also refuses to start if `private/` holds anything
else or the file has another owner or mode. A file that is damaged or was
written by an incompatible build never stops startup: every source is asked
live and a `provider_response_store` line says `unavailable`
([Rebuilding one store](#rebuilding-one-store)).

## Rebuilding one store

Some refusals affect one store only. Fix only that store.

| Refused | What to do |
| --- | --- |
| Session database (`/data/private/review-sessions.sqlite`) | Follow [Session database refused](backups-and-recovery.md#session-database-refused). |
| Incomplete first-run setup state | Start setup again on an empty `/data` volume. |
| Configuration document | Replace it with the current version (`config-v14` or `review-bootstrap-v2`). No conversion is provided. |
| Catalogue (`/data/catalogue.sqlite3`) or provider-recovery database (`/data/private/provider-recovery.sqlite`) | Stop the container, move the file aside, restart. A fresh store is created; the next refresh rebuilds the catalogue. Pending provider recovery jobs, retry backoff and their history are lost. |
| Provider-response database (`/data/private/provider-responses.sqlite`) | It never stops startup: while refused, every provider request goes live and a `provider_response_store` line says `unavailable`. To use it again, stop the container, move the file aside and restart. A fresh store is created and providers are asked again. |

## Changes that need owner action without a rebuild

- **Consent lapses when write destinations change.** Library consent is bound
  to the whole set of write destinations. A build that changes that set lapses
  every existing consent. Activity and the Library write notice say why. Grant
  consent again in Activity; nothing is planned, claimed or scheduled until you
  do. Activating a new metadata policy does not lapse consent.
- **A different Python can need a regeneration.** If the new build's Python
  has different Unicode data, startup names `regenerate-derived`
  ([Compatibility](../reference/compatibility.md#the-clean-state-rule)).
  Follow [Upgrading with a regeneration](#upgrading-with-a-regeneration).
- **Open pages must be reloaded.** A page from an older bundle is refused on
  every unsafe request.

## What is refused

Startup refuses the application database in these cases with one line on
standard error and exit status 1, in file and store mode alike, and changes
nothing:

| Startup says | Cause | What to do |
| --- | --- | --- |
| "This database was prepared by another build. …" | What preparation made is from another build | [Upgrading with a regeneration](#upgrading-with-a-regeneration) |
| "This database is from a build this one cannot read, so it needs a clean rebuild. Upgrades has the steps." | Your answers and settings are stored in another build's format, or in one from before this format existed | [Clean rebuild](#clean-rebuild) |

Everything listed under
[Not supported](../reference/compatibility.md#not-supported) is refused. Two
upgrade-specific cases:

- Editing rows, pointers, schema or artifact files to force a store open is
  refused at the next open, or produces state the runtime will not trust.
- Restore only into a build whose schemas match the backup; see
  [Backups and recovery](backups-and-recovery.md). Otherwise follow what
  startup says: a regeneration or a clean rebuild.

`scripts/qualification_state_bridge.py` is a maintainer tool for private test
state, not an upgrade path. It accepts only one exact retained test schema and
targets a frozen, older contract.
