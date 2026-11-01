# Deployment

Mechanical Librarian runs as one non-root container built from this
repository's `Dockerfile` and `compose.yaml`. It has one named `/data` volume,
one web port, a read-only root filesystem and no other services. There is no
published image: you build it from source.

This guide takes you from a clone to a running, claimed install. Settings
fields are defined in the [configuration reference](../reference/configuration.md);
the threat model is in [security](../architecture/security.md).

## Requirements

- Docker Engine 25 or newer and Docker Compose v2.20 or newer. The health
  checks use `start_interval`, which needs those versions.
- An Audiobookshelf server and an API key for it.
- Your ebook and audiobook folders, readable on the Docker host.
- For the optional contained Codex reviewer only: a Docker engine that
  supports seccomp and unprivileged user namespaces, and at least 1 GiB of
  free memory when a review runs. Everything else works without it.

## Install

From a clone of the repository:

1. Copy the [bootstrap example](../examples/review-bootstrap.json) beside
   `compose.yaml` and make it owner-only for the container user (UID 65532):

   ```text
   cp docs/examples/review-bootstrap.json review-bootstrap.json
   sudo chown 65532:65532 review-bootstrap.json
   sudo chmod 600 review-bootstrap.json
   ```

   Compose mounts it read-only at `/run/secrets/review-bootstrap.json`. Compose
   without this file is not supported.

2. Add your media folders as read-only mounts. See
   [media mounts](#media-mounts) below.

3. If you will reach the service from another machine or through HTTPS, set
   the origin first. See [network access](#network-access).

4. Build and start:

   ```text
   docker compose up --build -d
   ```

5. Read the setup claim token from the log and open `http://localhost:8080`.
   See [first-run setup](#first-run-setup).

Stop with `docker compose down`. Do not add `-v` unless you mean to delete all
application state.

## The bootstrap document

In store mode (the default) the deployment supplies only
`review-bootstrap.json`, a closed `review-bootstrap-v2` document. It holds the
canonical origin, optional TLS file paths, trusted proxy addresses, read-only
mount mappings and an optional reviewer profile path. It never holds an owner
credential: you create the password in the browser. The fields are listed in
[configuration: store mode](../reference/configuration.md#store-mode).

The bootstrap is read at start. Changing it, or any port, volume or mount,
needs a restart.

## Media mounts

The container reads media through read-only bind mounts and never writes to
them. Each mount needs two things:

1. A read-only volume in a Compose override, for example
   `compose.override.yaml` beside `compose.yaml`:

   ```yaml
   services:
     mechanical-librarian:
       volumes:
         - /srv/media/books:/media/books:ro
   ```

2. A matching entry in the bootstrap's `mounts` list that maps the path
   Audiobookshelf reports to the path inside the container:

   ```json
   "mounts": [
     {
       "name": "books",
       "container_path": "/media/books",
       "service_path": "/library/books",
       "read_only": true
     }
   ]
   ```

Add both before setup: the setup wizard compares each library's folders with
these mappings. A book in a library without a mount is not prepared; see
[mounts and media](../reference/configuration.md#mounts-and-media).

Never mount media writable and never mount the Docker socket. All library
changes go through Audiobookshelf's API.

## Network access

The supplied Compose file publishes `127.0.0.1:8080:8443`, so the service is
reachable only from the Docker host at `http://localhost:8080`. The listener
inside the container is always port 8443. Choose one of three modes; the rules
behind them are in
[origin, TLS and proxies](../reference/configuration.md#origin-tls-and-proxies).

### Plain HTTP on a trusted network

HTTP sends passwords, session cookies and metadata unencrypted. Use it only on
a network you control, or over a VPN. Never expose it to the internet.

To reach it from your LAN, override the port binding and set the matching
origin. For a host at `192.0.2.10` (replace with your address):

- port: `192.0.2.10:8080:8443`
- `canonical_origin`: `http://192.0.2.10:8080`

The browser's "not secure" indicator is accurate for HTTP; there is nothing to
bypass.

### Direct HTTPS

Set an `https://` canonical origin and both `tls_certificate_file` and
`tls_private_key_file`. Mount those files read-only at the paths you name, and
publish the host port you want. Use a certificate the browser already trusts.
The key must be an unencrypted PEM; passphrases are not supported.

### HTTPS through a reverse proxy

Set an `https://` canonical origin, leave both TLS paths `null`, and list the
proxy's exact address as seen by the container in `trusted_proxy_ips`. The
proxy must:

- terminate browser-trusted TLS;
- send exactly one `X-Forwarded-Host` and one `X-Forwarded-Proto`, matching the
  canonical origin;
- set or append the client address in one `X-Forwarded-For` line, so sign-in
  throttling is per client;
- stream responses without buffering (see [live updates](#live-updates)).

Allow only the proxy to reach the container's port. Nothing discovers proxies,
issues certificates or sets up trust for you.

If a browser reaches first-run setup through any other address, or through a
proxy whose forwarding headers are incomplete, setup is refused with a page
naming the cause and the published address to use.

Allow the container outbound access only to your Audiobookshelf server, the
metadata providers you enable and, if used, OpenRouter or the Codex service.
What each client contacts is in
[security](../architecture/security.md#credentials-and-egress).

## First-run setup

An empty `/data` volume starts guided setup. Until setup finishes, the service
serves only health checks and the setup pages.

**Claim token.** Every start that finds setup unclaimed creates a new setup
claim token and revokes the previous one. It is printed once in the container
log and written to the owner-only `/data/private/setup-claim-token`:

```text
docker compose logs mechanical-librarian
docker compose exec mechanical-librarian cat /data/private/setup-claim-token
```

Someone who reaches the port before you cannot claim the install without it.
If you lose it, restart the container and use the new one. While setup is
unclaimed, protect the container log as you would the volume. The design is in
[ADR-0086](../architecture/decisions/0086-deployment-held-setup-claim-token.md).

**Steps.** The wizard has seven steps:

1. **Create owner access.** Enter the claim token and choose a password of at
   least 12 characters. Claiming deletes the token file; resuming setup later
   needs only the password.
2. **Connect Audiobookshelf.** The server URL and API key are stored only after
   a test request succeeds.
3. **Choose metadata providers.** Optional.
4. **Choose libraries.**
5. **Check read-only mounts.** Compares each library's folders with the
   bootstrap mappings.
6. **Choose reviewer.** Optional; see [optional reviewers](#optional-reviewers).
7. **Kick off preparation.** Saves the complete settings, closes the setup
   pages, switches to the normal workspace on the same address and starts
   preparing the library.

Interrupted setup resumes at the next start. After kickoff, change settings on
the Settings page.

Nothing is written to Audiobookshelf until you grant consent for a library in
Activity. Until then the product only reads and proposes. See
[review experience](../product/review-experience.md).

## File mode

If `/data/config.json` exists, it is the complete settings authority and the
bootstrap and settings store are not read. This is the case even if the file is
invalid. The file is the `config-v9` document described in the
[configuration reference](../reference/configuration.md#file-mode). Every
secret, password-hash and TLS file it names must be an owner-only regular file,
mounted read-only into the container at the path the configuration uses.

In file mode the browser shows settings read-only. Edit the file and restart
to change them.

## Starting, stopping and restarting

**Startup refuses rather than repairs.** The container starts only from an
empty volume, a resumable setup, or exact-current stores with complete
settings. It stops without changing anything if it finds a schema mismatch,
missing or incomplete settings, a volume owned by another user, an invalid
configuration or bootstrap file, or a permission failure. It never falls back,
imports, migrates or repairs. What to do after a refusal is in
[upgrades](upgrades.md).

**Crash recovery.** After a crash the container can recover one interrupted
database transaction on its own. It checks a private copy first and needs free
space about the size of the database. Unknown or malformed recovery files stop
startup. Do not delete journal or alias files by hand. The procedure is in
[SQLite topology](../architecture/sqlite-topology.md#crash-recovery).

**Readiness.** The service reports ready only after the stores open and
Activity's history has been loaded into memory, so a large history adds
startup time. Restart resumes in-flight work from its last durable checkpoint.

**Graceful stop.** `review.graceful_shutdown_seconds` (default 30, maximum
300) bounds both phases of shutdown: first HTTP requests drain, then
preparation and write workers stop. The Compose stop budget is 610 seconds,
two maximum phases plus ten seconds for signal delivery and exit. If you write
your own Compose file or orchestrator configuration, keep at least that stop
timeout.
A worker still running at the deadline resumes from its checkpoint on restart.

**Health.** The image's `HEALTHCHECK` and the Compose `healthcheck` both run
`mechanical-librarian-container-health`, which reads `/health/ready` over the
container's loopback without a credential. With direct TLS it verifies the
configured certificate against the canonical host name. The endpoints are
described in [observability](observability.md).

## Owner sessions

A sign-in creates a persistent session cookie with a fixed absolute lifetime of
up to 30 days, the default. Set a shorter `review.session_lifetime_seconds` if
you need one. Sessions survive restarts until you sign out or they expire, and
are kept in `/data/private/review-sessions.sqlite`. A remembered session is a
bearer credential: do not use it on an untrusted device or network.

## Forgotten owner password

Mechanical Librarian keeps only an Argon2id verifier of the owner password, so
it cannot show the password, and there is no in-app or offline reset
([ADR-0030](../architecture/decisions/0030-authenticated-review-web-boundary.md)).
The sign-in page links here.

- **File mode** (`review.owner_password_hash_file`): write a new Argon2id PHC
  string to that file, keeping it a regular owner-only file, then restart the
  container. Any Argon2id tool will do; with the `argon2-cffi` library, for
  example:

  ```text
  python3 -c "import getpass; from argon2 import PasswordHasher; print(PasswordHasher().hash(getpass.getpass()))"
  ```

  The restart signs every browser out.
- **Store mode** (password created in guided setup): the verifier is in
  `/data/application.sqlite3` and nothing replaces it. Restore a
  [backup](backups-and-recovery.md) of the volume taken while you knew the
  password, or start again from an empty volume and repeat setup. Starting
  again discards the review history, decisions and settings on that volume.

## Live updates

Each open browser tab holds one long-lived `text/event-stream` response on the
same listener. The server accepts at most 100 concurrent connections in all,
and at most 32 live-update streams. Each stream:

- sends `X-Accel-Buffering: no` and a heartbeat comment every 15 seconds;
- ends after at most 10 minutes, after which the browser reconnects;
- is refused with `503` (`capacity`) when 32 streams are already open.

A proxy that buffers or times out idle responses turns live pages into polling
ones; the pages say so. Freshness suffers but correctness does not. Shutdown
ends every open stream inside the graceful-shutdown deadline.

## Resource limits

`compose.yaml` sets `mem_limit` and `memswap_limit` to 2 GiB and `pids_limit`
to 128. These are ceilings, not reservations; the reasoning is recorded beside
each setting in `compose.yaml`. The Codex reviewer still refuses to start a run
unless 1 GiB is free inside both the host and the container limit.

## Contained Codex reviewer

The image includes a checksummed Codex CLI, its paired Bubblewrap sandbox and a
generated profile at `/opt/mechanical-librarian/reviewer/profile.json`. They are
read-only image content, not downloads or mounts. Store mode uses that profile
unless the bootstrap names another; an explicit path never falls back. The
contract and paths are in the
[contained reviewer profile](../reference/configuration.md#contained-reviewer-profile)
reference, and the sandbox is described in
[security](../architecture/security.md#contained-codex-reviewer) and
[ADR-0081](../architecture/decisions/0081-image-owned-reviewer-provisioning.md).

The supported Compose file sets what the sandbox needs. Keep these if you
write your own:

- `init: true` (or `--init` with `docker run`), so short-lived sandbox
  processes are reaped;
- `seccomp=./config/seccomp/contained-reviewer.json`, which keeps Docker's
  default profile and admits only Bubblewrap's namespace setup;
- `cap_drop: ALL`, `no-new-privileges:true` and `read_only: true`.

Never replace the profile with `seccomp=unconfined` or add `SYS_ADMIN`. If the
engine refuses user namespaces, Codex is unavailable and everything else keeps
working.

On start the entrypoint creates only the private `/data/reviewer`,
`/data/reviewer/auth` and `/data/reviewer/jobs` directories, and refuses unsafe
existing entries rather than repairing them. A missing, unsafe or mismatched
profile shows as not provisioned with its remedy: restore the image profile or
correct the explicit path, then restart. The browser can never supply
executable paths or limits.

The image keeps Codex's Apache-2.0 licence, Bubblewrap's LGPL-2.0-or-later
notice and the full Bubblewrap source archive under
`/opt/mechanical-librarian/reviewer/`.

## Optional reviewers

Reviewers are advisory. They never select or write anything themselves, and
never receive library credentials
([AI trust boundary](../architecture/ai-boundary.md)).

**Cover review.** Choose a reviewer in setup or Settings → Reviewer: the
contained Codex reviewer, or an OpenRouter model. Setup and Settings recommend
GPT-6 Luna (`openai/gpt-6-luna`) and accept any other model ID. OpenRouter
cover requests deny provider data collection but allow retention; read the
provider's data policy before enabling it. A failed observation stays visible
and needs an explicit retry. Changing the reviewer invalidates current advice
but keeps its history.

**Description advice.** Configure this after setup:

1. Open Policy → Curation → Description advice. Review the three cleanup
   choices (all on by default) and press **Save description preferences**.
   Automatic application is off by default and has its own switch. Saving a
   policy draft does not save these preferences. In file mode, set the
   `review.description_*` fields instead.
2. In Settings → Reviewer, test the description model and its prices, then
   select it. One saved OpenRouter key can serve different cover and
   description models. Selecting a model starts advice preparation; it does not
   turn on automatic application.
3. In Library, choose between the current, provider and **Advised · Authored
   content** descriptions. History records advice, selection and application.
   Stale advice stays visible but cannot be selected. Failed work needs an
   explicit retry and never spends again on its own.

Field limits and request contents are in
[description advice](../reference/configuration.md#description-advice).

## Writes to Audiobookshelf

The container composes the Audiobookshelf write path but stays report-only
until you grant consent for a library in Activity. Every write then passes
the checks listed in
[security](../architecture/security.md#consent-and-guarded-effects), is read
back afterwards, and uses Audiobookshelf's API with your API key. Pause and revoking consent stop new
writes. A build that changes the set of write destinations lapses existing
consent; see [upgrades](upgrades.md).
Recovery of interrupted writes is in
[backups and recovery](backups-and-recovery.md#guarded-field-effect-recovery).

## Commands in the image

The container's entrypoint, `mechanical-librarian-container`, prepares `/data`
and starts the review server. The image also installs the
`mechanical-librarian` command with these subcommands, which use the same
configuration and data paths:

| Command | Purpose |
| --- | --- |
| `review-serve` | Serve the owner workspace and API ([review case](review-case.md)) |
| `policy-preview` | Read-only preview of a metadata policy |
| `cover-review` | Show or record one current cover consideration |
| `review-activity` | Read-only guarded-write and recovery history |
| `provider-recovery` | Run one provider recovery claim ([provider recovery](provider-recovery.md)) |

They use the same application services as the browser and bypass no consent or
write check. Catalogue, report and batch tools are not installed; they are
contributor tools described in the
[contributor workbench](../development/contributor-workbench.md).

## Backups

Back up the stopped `/data` volume and keep your deploy-side files (the
bootstrap or `config.json`, and every secret and TLS file they name) under your
encrypted secret-backup policy. The procedure is in
[backups and recovery](backups-and-recovery.md).
