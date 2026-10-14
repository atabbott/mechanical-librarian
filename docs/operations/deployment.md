# Deployment model

## Supported single container

`Dockerfile` and `compose.yaml` define the pre-release installed runtime. It is
one non-root container, one named `/data` volume, one web port, a read-only
root filesystem, and no runtime package installation or extra application
process. Existing deployments may put the exact `config-v9` document at
`/data/config.json` and keep its referenced owner-only TLS, password and
service-secret files within explicitly mounted private locations. When that
file is absent, store mode instead requires the exact-current owner settings and
verifier in `/data/application.sqlite3` and an owner-only, read-only
`/run/secrets/review-bootstrap.json` containing optional deploy-side TLS, canonical
origin, trusted proxies, read-only mount mappings, and any contained-reviewer
profile path. It supplies no owner credential; the first visitor creates the
owner password inside the bounded guided first run.
Add ebook media mounts as read-only Compose overrides; never grant
a writable media mount or Docker socket.

The published mapping is `127.0.0.1:8080:8443`: open
`http://localhost:8080` on the container host. Before starting Compose, copy the
[bootstrap example](../examples/review-bootstrap.json) to `review-bootstrap.json`
beside `compose.yaml`. Make it a regular owner-only file readable by container
UID 65532 (for example `sudo chown 65532:65532 review-bootstrap.json` and
`sudo chmod 600 review-bootstrap.json`). Compose mounts that explicit document
read-only; add deployment-owned media mappings and matching read-only mounts
before setup. Bare Compose without this prepared bootstrap is not supported.

HTTP carries passwords, session cookies, metadata, and guarded-effect traffic
in plaintext. Use it only on an owner-controlled local network or VPN. To expose
it to a chosen LAN address, explicitly replace the host binding and set the
matching canonical origin; for example `192.0.2.10:8080:8443` with
`http://192.0.2.10:8080` (replace this documentation address with yours).
Do not expose plaintext directly to the internet. The browser's passive
insecure-connection indicator truthfully describes HTTP; no interstitial bypass
is needed.

For direct HTTPS, configure the external HTTPS origin and both certificate
paths, mount the matching files, and publish the desired host port. Use a
certificate already trusted by the browser. For HTTPS at an existing reverse
proxy, omit the TLS paths and list the exact backend peer IP in
`trusted_proxy_ips`. The proxy terminates browser-trusted TLS, forwards one
canonical `X-Forwarded-Host`/`X-Forwarded-Proto` pair, and must support streaming
without buffering live updates. Restrict backend reachability to that edge;
there is no automatic proxy discovery, certificate issuance, or trust setup.
A browser sent to first-run setup through any other address, or through a proxy
whose forwarding is incomplete, is refused with a readable page naming that
cause and how to reach the published address; programmatic callers keep the
refusal in their own shape.

Owner sessions are persistent opaque cookies with a fixed absolute
lifetime of up to 30 days (the default); choose a shorter
`review.session_lifetime_seconds` value when the deployment requires it. A
remembered browser session is still a bearer credential, so do not use it on an
untrusted endpoint or transport.
The 0.18.0 upgrade signs out existing owners once because remembered sessions
are now bound to the exact owner verifier. Sign in again after upgrading;
otherwise-valid sessions continue across exact-current application and
repository restarts until logout or absolute expiry. The #912 session
decoupling likewise signs existing owners out once: sessions now persist in
the dedicated `/data/private/review-sessions.sqlite` database (ADR-0060), so
owner sign-in never waits on a running library scan, and the sessions
recorded inside an older application database are discarded by its adoption.

The container declares its own health. `HEALTHCHECK` runs the installed
`mechanical-librarian-container-health` and the Compose `healthcheck` block runs
the same command, so `docker inspect` and an orchestrator both see the readiness
defined in [observability and support](observability.md). That probe reads
`/health/ready` over loopback HTTP for HTTP/proxy listeners. For direct TLS it
verifies the configured certificate as its own trust anchor and the canonical
origin as its hostname. It presents no credential, returns no library data, and
adds no `curl` or `wget` to the image.

Compose also bounds the blast radius of a runaway container with `mem_limit`,
`memswap_limit`, and `pids_limit`. The serving baseline is measured; bounded
preparation children and the contained reviewer's 1 GiB per-run contract are
then included in the whole-container ceiling. `compose.yaml` records that
evidence and arithmetic beside each setting so a later reader can tell it from
a guess. A 2 GiB cap is an upper bound, not an idle reservation: the live host
gate still refuses Codex unless a full 1 GiB remains available inside both the
host and cgroup limits.

Compose also enables Docker's minimal init process. Nested reviewer PID
namespaces can orphan short-lived descendants after the sandbox exits; init
reaps those descendants without making the application a global child reaper
or changing its ownership of graceful shutdown and direct-child verification.
Direct `docker run` reproductions of the supported application container must
therefore include `--init`.

The contained reviewer applies its exact profile `RLIMIT_NPROC` only after
Bubblewrap has entered the reviewer's private user namespace. Authentication,
recheck, and review share one activity gate. A post-namespace seccomp filter
permits threads but no further processes. The broker, outer Bubblewrap
supervisor, namespace PID 1, and Codex payload receive 64, 64, 64, and 832 MiB
address-space ceilings that sum to 1 GiB. `pids_limit`
remains defence in depth for the whole container, not reviewer scheduling
authority.

The supported Compose file loads
`config/seccomp/contained-reviewer.json` rather than disabling seccomp. It
retains Moby's deny-by-default profile and admits only Bubblewrap's two exact
namespace clone masks and mount-namespace construction. The application-owned
inner filter performs post-construction namespace denial. Capabilities remain dropped and
no-new-privileges remains enabled. The reviewer PID namespace exposes no
procfs; its only `/proc` entry is a synthetic `self/exe` link to the sealed
Codex binding. A runtime that rejects the admitted user namespace leaves Codex
unavailable; do not replace this profile with `seccomp=unconfined` or add
`SYS_ADMIN`.

The pinned Codex invocation binds sealed local model metadata with apply-patch
and code mode absent, then disables every model-callable shell, execution,
agent, app, plugin, browser, computer, hook, web-search, view-image, plan,
owner-input, and optional tool surface. Its internal sandbox mode is therefore
`danger-full-access` only
inside the outer Bubblewrap boundary; this avoids an unsupported nested
user-namespace sandbox and grants no path, process, credential, or tool beyond
that outer view.

The image contains the checksummed Codex CLI 0.147.0 and its paired Bubblewrap
artifact at `/opt/mechanical-librarian/reviewer/bin/`. They are read-only image
content, not runtime downloads or owner mounts. The build generates
`/opt/mechanical-librarian/reviewer/profile.json` from those verified artifacts
and current supported parameters. It is UID 65532-owned, mode 0400. Store-mode
setup and normal runtime use it by default; an explicit bootstrap profile path
overrides it without fallback. File-mode keeps explicit deployment authority.
The image entrypoint initializes only private `/data/reviewer`, `auth` and
`jobs` directories, refusing unsafe existing entries without repair. These
known directories and bounded device staging participate in setup resumption;
unknown volume content never becomes a fresh setup.

Missing, unsafe or contract-drifted profiles appear as not provisioned, with a
deployment remedy. Host headroom and sign-in are separate subsequent checks.
Restore the supported image profile, or correct the explicit override and
restart. No shell editing is part of ordinary reviewer setup. The browser
cannot supply executable paths or limits. See
[ADR-0081](../architecture/decisions/0081-image-owned-reviewer-provisioning.md).
The same image retains Codex's Apache-2.0 licence, Bubblewrap's
LGPL-2.0-or-later notice, and the exact full upstream source archive containing
the paired Bubblewrap source and build recipe under
`/opt/mechanical-librarian/reviewer/`.

Both healthchecks set a start interval, which needs Docker Engine 25 or newer
and Compose v2.20 or newer. That is the newest runtime either file requires;
nothing else in them assumes a version that recent. The engine must also
support seccomp and unprivileged user namespaces for Codex; the application and
OpenRouter paths remain available when that independent Codex requirement
fails.

On a truly empty data volume the entrypoint captures that empty state before
creating an exact incomplete setup store and serves only health, setup login,
setup assets, and the minimal authenticated setup API. The seven steps are
claim, Audiobookshelf connection, optional providers, libraries, read-only
mount comparison, optional cover reviewer, and kickoff. Kickoff prepares a
dormant runtime generation, atomically replaces incomplete state with complete
owner settings, publishes
the successor on the existing listener, closes setup routes, and starts
preparation immediately. Ordinary startup accepts only exact-current, owner-only
stores. On Linux, ordinary server roots can clean verified stale private
bindings and recover one supported interrupted DELETE transaction. Recovery first
admits a byte-verified private copy through native SQLite and full current-schema
validation, then rechecks the original before native rollback through its retained
alias. It requires free space proportional to the database and journal. Unknown
files, malformed journals, drift and unavailable copy space stop admission without
replaying the original. Do not manually remove private aliases or journals.
Verified interrupted copy directories are cleaned on restart; arbitrary contents
are refused. Offline tools and dependent connections have no recovery authority.
A schema mismatch,
missing/incomplete settings, an unowned volume, invalid file or bootstrap,
backup/recovery error, or permission failure stops startup rather than falling
back, importing, migrating, or repairing data. A present `/data/config.json`
always selects file mode, even when invalid; file mode never opens the settings
store. Every store-managed Settings write uses the same prepare, CAS commit,
same-listener publish, successor wake, and predecessor-drain boundary.

Use `docker compose up --build` for a local installed-product qualification.
`scripts/qualify_container_lifecycle.py --source-sha <exact-head>` repeats the
head-bound build, synthetic owner-secret/TLS seeding, non-root read-only-root
run, health probes, the container's own `HEALTHCHECK` verdict, bounded graceful
termination and exact-current restart. The same isolated run drives an empty
volume through first-owner access, fake Audiobookshelf discovery, mount proof,
same-listener promotion, preparation progress and durable ordinary restart.
Stop with `docker compose down`; do not add `-v` unless intentionally deleting
the complete local application state. The public gate's `container-image` job
runs `docker build` at every candidate revision, so a Dockerfile regression
fails there. That build reads no `compose.yaml`, and running the built image
remains maintainer-manual through the commands above. Image publication
remains a separate release authority and is not part of this support contract.

The image build has an exact pinned Node 24.21.0/npm 11.19.0 stage that
regenerates browser DTOs from the committed OpenAPI document and emits the
validated React asset bundle, with Brotli and gzip siblings for each hashed
script and stylesheet recorded in its manifest. The server negotiates those
encodings and lets the browser keep hashed assets as private immutable
responses, while the shell stays uncacheable. The Python build admits only
that bundle, and the final image contains neither Node/npm nor frontend source,
generated DTOs, `node_modules`, Vite metadata, or source maps. Library,
Attention, review detail, Policy, and Activity use that one persistent shell;
`/login` remains the small server-rendered authentication page.

The normal container composes the supported Audiobookshelf guarded target, but
remains report-only until the owner completes the authenticated, library-scoped
Activity consent flow and every backup, currentness, capability and claim gate
passes. Shutdown closes both preparation and guarded-effect claim admission
before either worker waits. They share one configured graceful-shutdown
deadline; an over-deadline worker remains tracked, raises a typed shutdown
failure before the shared repository closes, and resumes from its durable
checkpoint on restart. No fake target, direct database write or writable media
path is composed.

A build that adds a write destination invalidates every existing library
consent, because the consent envelope identity digests the whole destination
set. Nothing is planned or claimed under a lapsed
consent, and Activity offers re-consent before anything is scheduled.

The supported Compose stop budget is 610 seconds. Uvicorn can first use the
configured maximum 300 seconds to drain HTTP requests, then lifespan shutdown
can use the application's separate 300-second worker deadline before repository
and binding cleanup. The remaining ten seconds cover signal delivery and
process-exit overhead; deployments that override Compose must retain at least
that full bound.

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

Run as a non-root dedicated identity with an explicit `config-v9` file and
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


Activity prepares its bounded in-memory readable history before the server becomes
ready. Restarting rebuilds it from canonical history; no schema adoption or state
conversion is required for this optimization. Large retained histories can add
startup time. Capacity overflow uses the original complete read path and may make
search slower; it never loses history or truncates results. A corruption refusal
still requires the existing backup/recovery procedure, not deletion of ledger rows
or a repeated attempt to bypass validation.


## Optional description advice

Before enabling an adviser, open Policy → Curation → Description advice. Review
the three cleanup choices, which start on, and save them with **Save description
preferences**. Automatic application starts off and has its own switch; saving
a metadata-policy draft does not save these preferences. File-managed installs
edit the documented `review.description_*` configuration fields instead.

Follow the link to Settings → Reviewer. Inspect the contained profile/model or
test the exact OpenRouter description model and prices before selecting it.
A saved OpenRouter key can serve different cover and description models.
Selection enables advice preparation; it does not enable automatic application.
Missing model/price information is recoverable through explicit test/refresh.

In Library's Description choices, select the current, provider, or **Advised ·
Authored content** alternative through the same choice interaction. The advised
option identifies its engine, model and status; selection binds that immutable
candidate through separate authored authority. Evidence retains original provider
material. History records advice preparation, selection, application and
supersession with authored provenance; individual choices remain in Library.
Activity distinguishes queued work from verified application. Stale advice stays
visible in Description but cannot be selected against its old binding.
Failed or interrupted work needs explicit retry and never silently spends again.
New requests while paused remain pending until resume; pausing does not promise
to interrupt work already accepted. All existing write gates still apply.

Upgrading to this F39 schema requires a clean application-state rebuild under
[ADR-0013](../architecture/decisions/0013-pre-release-clean-schema.md).
Retain any needed historical state separately; ordinary startup does not migrate
it. See [configuration](../reference/configuration.md) for exact versions and
bounds. Media and target-service libraries remain outside this rebuild.


### Cover reviewer data use

Setup and Settings recommend GPT-6 Luna (`openai/gpt-6-luna`) for advisory cover
review; custom model entry remains available, including the MiMo comparator.
Cover requests deny provider data collection but allow retention, without
requiring ZDR. Review the selected
provider's data policies before enabling hosted advice. A failed or unavailable
observation remains visible and requires explicit retry. Changing reviewer
selection invalidates current advice while retaining its recorded model and
history. Description advice has its own task profile and selection.
