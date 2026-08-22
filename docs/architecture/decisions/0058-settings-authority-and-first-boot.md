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

When that file is absent, the settings store on `/data` is authoritative. Its
secret fields are write-only: an authenticated owner may set, replace, or clear
them, but reads return only presence and a short non-secret indicator. Backups
of `/data` therefore contain secrets and must receive the same protected backup
handling as the current secret files. The project will not claim encryption at
rest from an application-held key: a single-container key stored with `/data`
would not protect a stolen volume.

Compose remains authoritative forever for the published port mapping, `/data`
volume, read-only media mounts, TLS certificate and key, and trusted proxy IPs.
The wizard verifies mounts but cannot author host bind mounts. Other product
settings are eligible for the store.

On an empty `/data` volume, the server enters explicit setup mode and exposes
only the first-run wizard. Its first successful mutation claims ownership by
setting the owner password. Setup-only endpoints become permanently unreachable
after completion. Every state other than that empty-volume first boot retains
the existing stop-rather-than-repair startup rule.

Existing deployments stay file-managed. The supported move to in-app management
is an authenticated, CSRF-protected **Import and retire file management** action:
it validates the current file, commits the resulting settings store and a
retirement intent, then atomically renames the same-volume file to a private
retired name. Until the rename succeeds, the file remains authoritative; after
it, the committed store is authoritative. Startup may resume that recorded
retirement only; it must not infer, merge, or repair any other state. This keeps
the transition one deliberate owner action rather than asking operators to
manually delete a configuration that the next boot still needs.

Store writes declare either `applied_live` or `restart_required`. Policy choices
and cover-preference values apply live to later evaluations; target connections,
libraries, mounts, providers and their credentials, reviewer profile, origin,
owner-session settings, TLS, trusted proxies, and runtime limits are
restart-applied. V1 does not rebuild an adapter graph in a running process.

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
- First-run implementation must prove its terminal endpoint closure and must
  distinguish the sole empty-volume setup state from corrupt or partial state.
- File retirement needs durable intent and restart tests. It is not a generic
  migration, compatibility layer, remote secret backend, RBAC system, consent
  change, or new write authority.

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
  claim, permanent setup-route closure, retirement crash points, and the stated
  live/restart response for every setting category.
- Security review verifies secret permissions, backup guidance, CSRF/origin
  protection, and preserved contained-reviewer isolation.
