# ADR-0024: Contain qualification reviewer provider state

- Status: accepted
- Date: 2026-08-01

## Context

The qualification reviewer originally mounted its owner-private authentication
store as the writable `CODEX_HOME` for ordinary dispatch. A retained private
qualification run showed that the primary credential record remained unchanged
while other provider-owned state in that store changed. That made credential
authority and mutable cache, database, log, and runtime state share one mount,
and response identity unavailable from the provider could too easily be
mistaken for the requested configuration.

The reviewer remains qualification-only and untrusted. It must authenticate by
supported means, but ordinary inference has no authority to persist credentials
or provider state outside its one bounded attempt.

## Decision

Authentication management and ordinary review use separate mount contracts.
`reviewer-auth-start`, status, and logout retain the explicitly invoked writable
authentication-store workflow. An ordinary review instead creates a private
per-attempt `provider-state` directory beneath the existing job root, mounts
that directory as writable `CODEX_HOME`, and nests only the primary `auth.json`
credential file into it as a read-only mount from the validated, held no-follow
descriptor rather than from its replaceable pathname. The same contained mount is used
for the ordinary preflight authentication-status check and the inference
process. The executable version probe also runs inside Bubblewrap with an empty
credential-free home and no network namespace access. The complete job and
provider state are removed at terminal exit.

The runner fingerprints the owner-only primary credential before dispatch and
again after terminal cleanup. It never restores or copies changed bytes. Drift
creates an owner-only, value-free trust-failure marker and stops later ordinary
dispatch pending explicit recovery. A retained attempt directory or durable
value-free marker from failed cleanup likewise blocks later dispatch, including
when removal completed before reporting its error. Cleanup failure is a typed execution
failure; primary-credential drift is a typed authentication failure.

Every durable terminal outcome stores `shadow-review-execution-provenance-v1`.
The document keeps the exact requested profile identity separate from seven
provider observations: model, route, tier, effort, fallback, usage, and cost.
Each observation is `observed`, `not_exposed`, or `unverifiable`; a value is
allowed only for `observed`. The current pinned transport does not expose those
response fields, so a successful response records `not_exposed` and a failure
without a trustworthy response records `unverifiable`. Requested configuration
is never treated as observed execution.

Offline inspection exposes a bounded projection: requested model, effort,
profile version, pinned executable/sandbox digests, and a digest of the exact
requested profile, plus the observation states. It does not expose request,
packet, prompt, credential, private path, raw provider output, or inferred
metadata.

The exact-current SQLite outcome table requires the provenance document.
Earlier pre-release databases are rejected under ADR-0013; no migration,
conversion, import, or compatibility parser is provided.

## Consequences

- Ordinary reviewer code cannot invoke login/logout or receive a writable mount
  of primary credential material.
- Provider cache and runtime mutation is confined to one disposable attempt.
- Cleanup and credential-integrity failures are visible, terminal trust-boundary
  outcomes rather than best-effort housekeeping.
- Qualification analyses can distinguish requested dispatch from observed
  provider identity without inventing route, tier, fallback, usage, or cost.
- Authentication management remains an explicit operator action and therefore
  retains its separately writable store.
- A clean database rebuild is required after this pre-release schema change.
