# ADR-0058: Make settings-store authority explicit after first boot

- Status: accepted
- Date: 2026-08-22
- Owners: project maintainers

## Context

`config-v6` is currently a strict, boot-only file and its private files supply
target, provider, reviewer, TLS, and owner-password material. The product now
needs an authenticated settings surface and a guided first run. Letting either
surface quietly shadow the other would make a visible edit disappear on restart.
An empty `/data` volume is also the one legitimate unconfigured state; an
existing malformed volume must remain a startup failure, not become a repair
or import path.

## Decision

`/data/config.json` remains fully supported. When it exists, it is the sole
authority for every setting it covers. The settings API rejects every covered
write with `409`; the UI renders those fields read-only and says `Managed by
/data/config.json — remove it to manage in-app`. It never combines file and
store values.

When that file is absent, the settings store in `/data/application.sqlite3` is
the authority. Its secret fields are write-only: an authenticated owner may set,
replace, or clear them, but reads return only presence and a short non-secret
indicator. Application-database and complete `/data` backups therefore contain
secrets and must receive the same protected backup handling as the current
secret files. The project will not claim encryption at rest from an
application-held key: a single-container key stored with `/data` would not
protect a stolen volume.

Compose remains authoritative forever for the published port mapping, `/data`
volume, and read-only media mounts. File-managed mode reads the TLS certificate
and key, canonical origin, and trusted proxy IPs solely from `config-v6`; it
does not read a bootstrap document. Only setup and store mode require Compose
to mount the strict, owner-only `/run/secrets/review-bootstrap.json` document,
which supplies those values. Bootstrap material and referenced TLS files are
deploy-side material, never settings-store fields; missing, invalid, or unsafe
bootstrap material stops setup/store startup before setup. An empty volume means
an empty `/data` volume only: it does not create, replace, or relax the required
bootstrap material. The wizard verifies media mounts but cannot author bind
mounts. The settings API does not accept Compose or bootstrap fields and never
returns an apply effect for them; the UI directs the owner to deployment
configuration. Deploy-side changes take effect only after an operator restart.

On an empty `/data` volume, the server enters explicit setup mode and exposes
only the first-run wizard. Its first successful mutation claims ownership by
setting the owner password. Completion is two phase: it first commits staged
settings and a durable completion record, then enters a restart-requested,
draining state. It returns that explicit instruction and a bounded
setup-complete state while the setup listener remains reachable, so the owner
can see that no normal service has started; the process then shuts down for the
Compose restart policy. It starts no preparation or normal routes. Only that
next boot composes the exact staged settings, closes setup endpoints
permanently, and admits normal routes and preparation. Every state other than
that empty-volume first boot retains the existing stop-rather-than-repair
startup rule.

Existing deployments stay file-managed. The supported move to in-app management
is an authenticated, CSRF-protected **Import and retire file management** action:
it reads and validates the exact file and every referenced secret through the
existing private-file rules, and, for this action only, validates the future
store-mode bootstrap document and its referenced TLS identity. If that bootstrap
material is missing, invalid, or unsafe, retirement refuses and the file remains
authoritative. It then commits the resulting settings and a retirement intent
bound to those exact file and secret snapshots, then rereads and revalidates all
of them before renaming. A mismatch fails closed with the file still
authoritative. The rename is same-volume and atomic to a newly created,
owner-only, non-clobbering private retired destination. On restart, only a
recorded intent may resume; it repeats the snapshot and bootstrap checks before
rename and otherwise fails closed. Until the rename succeeds, the file remains
authoritative; after it, the committed store is authoritative. Startup must not
infer, merge, or repair any other state. This keeps the transition one deliberate
owner action rather than asking operators to manually delete a configuration
that the next boot still needs.

Store writes declare either `applied_live` or `restart_required`. Policy choices
and cover-preference values apply live to later evaluations; target connections,
libraries, providers and their credentials, reviewer profile, owner-session
settings, and runtime limits are restart-applied. V1 does not rebuild an adapter
graph in a running process.

The contained-reviewer credential remains an owner-only file-path setting in
v1. Pasting, uploading, or using a device flow for that credential is staged
until after the core settings surface; it must preserve the contained reviewer's
current credential and artifact boundary.

## Consequences

- The settings API, UI, startup composition, backup guidance, and tests have
  one authority rule and an observable `409` failure rather than a precedence
  convention.
- Store-mode secret backup is an intentional operational responsibility; no
  false encryption guarantee is introduced.
- First-run implementation must prove its staged handoff, owner-visible restart
  state, terminal endpoint closure, and distinction between the sole
  empty-volume setup state and corrupt or partial state.
- File retirement needs snapshot-bound durable intent and restart tests. It is
  not a generic migration, compatibility layer, remote secret backend, RBAC
  system, consent change, or new write authority.

## Alternatives considered

### Merge file and store fields

Rejected: partial precedence makes the same settings page misleading and leaves
an edit vulnerable to silent replacement at restart.

### Operator deletes the file manually

Rejected: removal creates an avoidable interval in which an existing deployment
has no authoritative configuration. The explicit retirement action can preserve
the file until a validated store is durable.

### Encrypt store secrets with a colocated application key

Rejected: it adds key handling without protecting the `/data` theft case the
claim would imply.

## Validation

- File-mode API/UI tests prove read-only fields and `409`; restart proves no
  store value overrides a present file.
- Store-mode tests prove secret values never appear in reads, logs, errors,
  exports, or backups' public manifests, while presence and clear/replace work.
- Startup and integration tests prove the empty-volume setup boundary, owner
  claim, staged restart handoff, permanent setup-route closure, bootstrap
  rejection, retirement crash/drift points, and the stated live/restart response
  for every store setting category.
- Security review verifies secret permissions, backup guidance, CSRF/origin
  protection, and preserved contained-reviewer isolation.
