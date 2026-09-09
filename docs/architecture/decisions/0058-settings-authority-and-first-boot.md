# ADR-0058: Make settings-store authority explicit after first boot

Transport provisions are superseded by [ADR-0071](0071-explicit-local-transport.md);
all other provisions remain in force.


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
write with `409`; the UI renders those fields read-only and identifies
`/data/config.json` as their authority.
It never combines file and store values, and no UI copy instructs an owner to
delete the file manually.

When that file is absent, the settings store in `/data/application.sqlite3` is
the authority. Its secret fields are write-only: an authenticated owner may set,
replace, or clear them, but reads return only presence and a short non-secret
indicator. Application-database and complete `/data` backups therefore contain
secrets and must receive the same protected backup handling as the current
secret files. The project will not claim encryption at rest from an
application-held key: a single-container key stored with `/data` would not
protect a stolen volume.

Disabling a provider is separate from clearing its credential or contact value.
Disable retains the write-only value, removes every library association in the
same save, and causes no provider egress. Re-enabling does not restore those
associations; the owner chooses them again explicitly.

The owner credential is an exception to generic secret clearing. An established
owner password is changed only by an authenticated, CSRF-protected atomic
replacement that records the new verifier and invalidates affected sessions; it
cannot be cleared through the settings API. Lost-owner recovery is a separately
designed, explicit recovery flow with its own durable proof and is not an
alternate settings write.

Compose remains authoritative forever for the published port mapping, `/data`
volume, and read-only media mounts. File-managed mode reads the TLS certificate
and key, canonical origin, and trusted proxy IPs solely from `config-v6`; it
does not read a bootstrap document. Only setup and store mode require Compose
to mount the strict, owner-only `/run/secrets/review-bootstrap.json` document,
which supplies those values. It supplies no owner credential: the first visitor
creates the owner password in the ordinary application.
Bootstrap material and referenced TLS files are deploy-side material, never
settings-store fields; missing, invalid, or unsafe bootstrap material stops
setup/store startup before setup. An empty volume means an empty `/data` volume
only: it does not create, replace, or relax the required bootstrap material. The
wizard verifies media mounts but cannot author bind mounts. The settings API
does not accept Compose or bootstrap fields and never returns an apply effect
for them; the UI directs the owner to deployment configuration. Deploy-side
changes take effect only after an operator restart.

On an empty `/data` volume, the ordinary application initially exposes only its
first-run journey on the ordinary HTTPS listener. The first visitor
receives one bounded synchronizer nonce and creates the owner password; one
SQLite transaction records its Argon2id verifier and an ordinary owner session,
so a race has exactly one winner and no partial owner state. Resume uses the ordinary
password and session boundary. If interruption lands between exact schema
creation and the first setup-state commit, startup accepts only that exact
seeded-schema predecessor with no settings, secrets, session, throttle, or
operational rows. Older incompatible incomplete setup state requires a clean
rebuild under ADR-0013; arbitrary nonempty, malformed, or store-mode state still
fails closed.

Kickoff first validates and prepares an independent dormant runtime generation.
Every store runtime repository borrows the database binding owned by a dedicated
process-lifetime control repository, so closing the predecessor cannot revoke the
successor's validated database authority. The control owner closes only after the
dispatcher and all runtime generations have closed.
Under one cutover lock it closes predecessor admission, commits complete owner
settings, publishes the successor on the same listener, and opens successor
admission. It then drains requests and claims already admitted to the setup
generation. Setup routes close permanently at authority commit and
preparation begins immediately; no container restart, second listener, or
partially authoritative runtime exists.

Existing deployments stay file-managed. No current in-app action imports or
retires that authority, and the interface does not advertise a future action.

Every store-managed field declares `applied_live`. A settings write validates
and prepares an independent candidate generation through that same control-owned
database authority before taking the persistence lock; candidate failure leaves
the current revision and runtime untouched.
Under one cutover lock the process closes predecessor admission, performs the
revision-CAS commit, publishes the candidate, and opens candidate admission;
it then drains the predecessor per admitted request and claim. The saved value therefore
governs the next admitted operation. Compose/bootstrap fields remain
`deploy_managed` and require an operator restart because they own the process
listener, TLS identity, and mounts.

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
- First-run implementation must prove its atomic first-owner transaction, same-listener handoff,
  immediate preparation, terminal endpoint closure, and distinction between
  the sole empty-volume setup state and corrupt or partial state.
- This decision does not introduce a generic migration, compatibility layer,
  remote secret backend, RBAC system, consent change, or new write authority.

## Alternatives considered

### Merge file and store fields

Rejected: partial precedence makes the same settings page misleading and leaves
an edit vulnerable to silent replacement at restart.

### Encrypt store secrets with a colocated application key

Rejected: it adds key handling without protecting the `/data` theft case the
claim would imply.

## Validation

- File-mode API/UI tests prove read-only fields and `409`; restart proves no
  store value overrides a present file.
- Store-mode tests prove secret values never appear in reads, logs, errors,
  exports, or backups' public manifests, while presence and generic
  clear/replace work; owner-password replacement is separately atomic and
  generic clear is refused.
- Startup and integration tests prove the empty-volume setup boundary, owner
  authentication, atomic first-owner transaction and same-listener generation handoff, permanent
  setup-route closure, bootstrap rejection, fail-closed cutover ordering, and
  the stated live/deploy-managed response for every
  store setting category.
- Security review verifies secret permissions, backup guidance, CSRF/origin
  protection, and preserved contained-reviewer isolation.
