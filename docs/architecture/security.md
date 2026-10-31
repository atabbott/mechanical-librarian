# Security and threat model

This is the authority for security review of any change to credentials,
egress, parsers, AI inputs, writes, backups, authentication or upgrades. It
states the invariants the code must keep; exact shapes live in the
[OpenAPI contract](../reference/review-openapi-v1.json),
[configuration reference](../reference/configuration.md) and
[ADRs](decisions/README.md).

The invariants, in short:

- Media is read-only. The only writes are guarded metadata and cover updates
  through Audiobookshelf's supported API, each with a pre-read and a read-back.
- No report, proposal, model output or policy preview carries write authority.
  Only an owner decision or a qualified automatic decision does, through the
  guarded effect pipeline.
- Secrets are write-only at the HTTP boundary and never appear in a report,
  log, diagnostic, support bundle, URL, browser cache or model input.
- Every external input (provider response, media file, sidecar, image, model
  output, stored row) is hostile: bounded, strictly decoded and reduced to
  closed typed facts before use. Failures are value-free.
- Persisted state is accepted only in its exact current schema. Nothing is
  migrated or repaired.

## Assets and actors

The assets are the owner's library metadata, the target, provider and
reviewer credentials, the owner password and sessions, and the private
evidence the product keeps (reports, cover previews, decisions, history).

- Network visitor, untrusted: [unauthenticated reach](#unauthenticated-reach).
- Signed-in owner, full product authority:
  [requests and CSRF](#requests-and-csrf).
- Audiobookshelf (the target) and metadata providers, hostile bytes:
  [target](#target-audiobookshelf), [providers](#metadata-providers).
- Mounted-media owner, hostile files and names:
  [parsers](#parsers-and-hostile-input).
- AI reviewer, untrusted output: [AI boundary](#ai-reviewer-boundary).
- Local actor with volume access, can replace files:
  [schema admission](#schema-admission).
- Dependencies, untrusted: [scanning](#automated-scanning-boundary).

Anyone who can write the state volume can already replace the whole store;
the product refuses corrupt or partial state but never repairs it. Typed
in-process collaborators are trusted; interpreter-level sabotage is out of
scope.

## Authenticated web surface

The review server is the only network entry point, with no generic write
route and no target client. FastAPI keeps only request bounds,
Host and Origin checks, session and CSRF dependencies and response
translation; everything else is a named application operation behind the
owner-operation gate
([ADR-0030](decisions/0030-authenticated-review-web-boundary.md),
[ADR-0042](decisions/0042-compose-authenticated-browser-operations.md)).

### Unauthenticated reach

Without a session a client can reach only liveness and readiness words, the
login form and its assets, the fixed login failure envelope and, while setup
is unclaimed, the setup page and its nonce.

### First-owner setup

Claiming an empty volume needs the deployment-held setup claim token, so a
visitor who reaches the port first cannot take the workspace
([ADR-0086](decisions/0086-deployment-held-setup-claim-token.md),
[first-run setup](../operations/deployment.md#first-run-setup)).

- Each unclaimed start mints a `secrets.token_urlsafe(32)` token, revokes any
  earlier one and stores only its domain-separated SHA-256 digest. The token
  goes, without following links, to the owner-only
  `/data/private/setup-claim-token` and once to standard output, bypassing
  logging. It never reaches a response, log, exception or evidence.
- A claim needs the exact Origin, the token and a nonce in a host-only cookie
  and a header (at most 100 nonces for 600 seconds, 10 per client, in
  memory).
- A wrong token is `403 claim_token` before Argon2, with no state change or
  throttle debt. A password under 12 code points or over 1,024 UTF-8 bytes is
  `422 invalid_password`. Validation errors echo no input.
- One transaction compares the token in constant time, claims ownership,
  clears the digest and stores the Argon2id verifier; a concurrent loser gets
  `409`. The token file is then removed. Later steps need a session, and
  kickoff closes setup for good.

Startup admits only the exact seeded predecessor or valid current incomplete
setup; any other non-empty, malformed or store-mode state fails closed.
Connection setup stores endpoint and key only after one bounded
`GET /api/libraries` under the [egress rules](#credentials-and-egress)
succeeds, and failures echo no key or userinfo. Service folder paths are kept
only to compare with mount mappings, and those checks never read media.

### Sign-in and sessions

- The password is checked against an owner-only, no-follow Argon2id hash file
  or the settings-store verifier.
- A session is an opaque HttpOnly, SameSite=Strict cookie, Secure under
  browser HTTPS, with a fixed absolute lifetime (five minutes to 30 days,
  default 30) that activity never extends.
- Sessions live in `private/review-sessions.sqlite`
  ([ADR-0060](decisions/0060-dedicated-review-session-database.md)): an HMAC
  lookup keyed by the exact verifier, CSRF digests, expiry, revocation and
  throttle rows. Replacing the verifier invalidates every session. Logout
  deletes the cookie only after revocation is durable.
- A workspace page opened without a session sends the browser to sign-in with
  that page as `next`. Sign-in returns only to a workspace page on this
  origin: the server matches `next` against the workspace routes and a
  URL-safe query, and the page checks the origin again before it navigates.
  Anything else, including a scheme, host, `//` or backslash, returns to
  Library.

Sign-in allows five attempts per client in any five minutes, under a global
ceiling of 100; a refusal is `429 throttled`. A granted session clears only
its own client's attempts. The client key is an HMAC, never a stored address.

The client comes from the transport: an untrusted peer is its socket
address, whatever its `X-Forwarded-For` says, and a peer in
`trusted_proxy_ips` is the rightmost entry of its single `X-Forwarded-For`
line (none, several or unparsable lines share one key). An IPv6 address
counts as its /64. A trusted proxy that passes a client's header through,
rather than appending or overwriting, lets that client choose its key.

The bound holds per identity: twenty IPv4 addresses or IPv6 /64s can hold
sign-in at the global ceiling for everyone, an accepted gap that only an
allow list or owner lockout would close. Forced Argon2 work stays near one
verification every three seconds.

### Requests and CSRF

- Exact Host is enforced on every request, exact Origin on every unsafe one.
  Forwarded Host or scheme is honoured only from a trusted proxy; Uvicorn's
  own proxy handling is off.
- Every unsafe request needs the session, the CSRF token and an
  `x-ml-build-identity` header matching the installed build.
- Serving an authenticated page rotates the CSRF digest. The raw token lives
  only in that no-store HTML and page memory.
- After a `403 csrf` the client renews the token and resends once, only a
  side-effect-free or idempotency-keyed request. A `401` goes to sign-in.
  A lost acknowledgement reloads authoritative state and never resubmits.
- `x-ml-work-class: background` on a `GET` changes only local scheduling,
  never authentication, CSRF or what a read returns. Mutations are always
  foreground.

The React workspace uses no browser storage, inline script, external origin,
worker or source map. The loader verifies every asset's path, size, digest
and build identity before serving an authenticated shell.

### Responses and caching

Every response has a restrictive same-origin CSP, frame denial,
`no-referrer` and `nosniff`, plus HSTS under browser HTTPS. Fonts are
vendored. Errors are fixed value-free codes.

Everything is `private, no-store` except content-hashed assets and
session-checked cover previews addressed by digest
(`private, max-age=31536000, immutable`). Logout does not clear the browser
cache, so that profile can show a viewed cover for up to a year. The library
cover route serves only the preview the current report binds to the item's
cover, digest-verified and never decoded.

The preparation panel's book names (at most five current and eight recent)
never enter update-stream frames, logs, audit identifiers, diagnostics,
support bundles or reviewer input. `GET /api/v1/updates` streams
invalidation, never state: each frame is a sequence, a topic and an optional
case identity, and the session is re-checked while the stream runs.

### Settings API

Settings documents are closed, private and bounded (a 256 KiB body, with
[collection limits](../reference/configuration.md#top-level-fields)). The
revision check and the settings and secret update are one transaction. A
secret is 1 to 2,048 printable non-whitespace ASCII characters with no scheme
prefix; reads show only presence and, from eight characters, the last four.
Reviewer paths are never browser fields. When `/data/config.json` exists,
covered writes are refused and the store is not read.

New settings start a dormant successor runtime under one cutover lock. A
commit failure keeps the predecessor; a fault after commit stops the server
rather than run mismatched authority.

### Transport

Transport modes and their rules are in
[origin, TLS and proxies](../reference/configuration.md#origin-tls-and-proxies)
and [network access](../operations/deployment.md#network-access)
([ADR-0072](decisions/0072-explicit-local-transport.md)). HTTP carries
credentials, sessions and metadata in plaintext, so it is for an
owner-controlled network or VPN only. There is no TLS-disable flag. Direct
TLS validates certificate and key as one identity before the database or
listener opens and limits TLS 1.2 to ECDHE AEAD ciphers. The edge admits at
most 100 connections, limits incomplete events to 16 KiB and closes
incomplete headers after 10 seconds.

## Credentials and egress

Secrets are read only in infrastructure while building their named client;
a disabled provider's stored key builds nothing.
Every HTTP client ignores ambient proxy settings, follows no redirect itself,
verifies certificate and host name on HTTPS, and uses an explicit timeout and
one connection. No endpoint is arbitrary and nothing is cached.

### Target (Audiobookshelf)

The API key goes only to the configured origin, on the route shapes the
operation uses: library and item reads, the capability probe, and the
guarded write and upload. An `http://` endpoint is plaintext, under the
[transport](#transport) rule. Target TLS, authentication, authorization and
availability failures become durable connection conditions with a closed
code only; owner recovery is a CSRF-bound probe that echoes no endpoint or
transport detail.

### Metadata providers

A disabled provider builds no client and loses its library associations,
which enabling does not restore.

- Every adapter dispatches through the bounded session in
  `adapters/providers/sdk.py` (request, retry, byte and deadline limits),
  with a 20-second share of the 60-second preparation deadline.
- Responses are read under per-response and aggregate byte ceilings and
  projected to closed facts. Overruns, quota and service failures are typed
  outcomes, never a no-match.
- A plan that cannot fit (more than eight ISBN families) is refused before
  a client exists; input with no ISBN and no complete title-and-author sends
  nothing.
- Retries cover only the statuses below and allow-listed transport causes,
  never TLS, proxy or decoding failures, and never inspect exception text.
- Reports keep a closed dispatch lineage, never queries, credentials or
  responses. [Dispatch](../reference/configuration.md#dispatch) sends
  audiobooks only to `AUDIOBOOK_DISPATCH_ADAPTERS`.

Per provider, where *5xx* means 500, 502, 503 and 504:

- **Hardcover**: `POST` to the fixed GraphQL URL, with the bearer token on
  that request only. GraphQL `errors` are checked before `data` and yield
  nothing; their text is never kept. Retries 5xx, and 429 only when
  `Retry-After` or Hardcover's rate-limit reset fits the deadline. An
  audiobook WORK lane that admits one or two books also sends one constant
  audio-edition document per page, keyed by an integer book id: 100 rows a
  page, at most 2 pages per book, inside the same ceilings. A row naming
  another book refuses the whole fan-out
  ([ADR-0090](decisions/0090-hardcover-audio-edition-witness.md)).
- **Open Library**: fixed-origin `GET`. The contact email goes only in the
  User-Agent to its fixed origins and is stored nowhere else. Retries 5xx,
  never 429.
- **Google Books**: fixed-origin `GET` with an optional API key that only the
  credential-bearing controller reads. Retries 503 only.
- **Inventaire**: fixed-origin `GET`, no credential. Retries 5xx, and 429
  only with a bounded `Retry-After`; at most three attempts per request.
- **Audible catalog**: `GET` to ten hosts from a closed marketplace map, so no
  provider value becomes a host. An oversized page is refused, not truncated.
- **Audnexus** (`api.audnex.us`) and **AudioSilo** (under
  `meta.audiosilo.app/api/v1`): `GET` paths built only from identifiers of a
  closed shape, so they cannot leave the base path. AudioSilo's HTML error
  pages are never evidence.
- **iTunes Search**: `GET` to `itunes.apple.com/search`, ebooks only, with the
  query only in `term`. It states no narrator or runtime, so it never
  satisfies runtime corroboration.

Audible, Audnexus, AudioSilo and iTunes send no credential and retry only
after `Retry-After` on 429 and 503, which Audible caps at one minute.

### Provider cover admission

Cover acquisition is report-only and needs the connection's `cover_enabled`.
At most four sources per provider and eight overall are admitted. A
descriptor is admitted only against a candidate the same provider observed in
the same observation: by a provider-local work identity or, when the provider
resolved no work, by one of that provider's own candidate editions. Two
unresolved placeholders never match, and a descriptor with no edition matches
nothing. A refused descriptor is dropped before any request is built.

Allowed cover origins:

- Audiobookshelf's raw cover route, with no credential, cookie or referrer.
- Hardcover, with bounded manual redirects; every hop must be public HTTPS on
  port 443.
- Open Library and Inventaire, on constructed fixed-origin URLs.
- Audible on `m.media-amazon.com`, and iTunes on `is1-ssl` to
  `is5-ssl.mzstatic.com`.

Only Hardcover follows redirects. Any other host is a typed `unsafe_remote`
outcome and no socket opens. Remote covers use their own address-pinned
transport with bounded bytes, attempts and deadline. Embedded audiobook art
is never acquired. Reports never keep the remote URL.

### Provider recovery

[Provider recovery](../operations/provider-recovery.md) is background egress
under the same limits. Before building a client it rechecks the source
report, review package, target and media identity, provider binding and
configuration generation, and it runs outside write transactions. Missing or
contradictory quota evidence takes the conservative path. **Retry now** is a
Settings write that sends no request, and no view shows a key's value.

## Parsers and hostile input

No untrusted media or image byte is parsed in the server process.

**EPUB.** A descriptor-bound child with no credentials, network or
persistence parses one bounded immutable snapshot
([ADR-0011](decisions/0011-isolated-epub-inspector.md)). The parent
revalidates file content and target identity inside the publication
transaction, so a replaced file is refused. Digits inside non-ISBN schemes
and URIs never become ISBNs.

**Audiobooks.** `ffprobe` runs as an isolated child over a passed descriptor
([ADR-0063](decisions/0063-isolated-audiobook-inspector.md)): no network, no
inherited `PATH`, a minimal environment, its own session, and CPU, memory,
file, output and wall-clock limits installed before exec. Its output and
sidecars are read in the EPUB child under a closed recognition grammar, never
an XML or container parser.

- An over-long, unreadable or non-UTF-8 sidecar is dropped whole; a badly
  shaped identifier is reported as malformed, not believed.
- A directory listing refuses past 1,024 entries; path evidence uses bare
  names only.
- A description over 512 characters is checked whole, then kept only as
  `oversized`; the cut text is never proposed or written.
- A multi-file item allows at most 512 files and a 600-second reading wall
  ([ADR-0066](decisions/0066-multi-file-audiobook-composition.md)). Each file
  gets its own child; one hostile file denies the whole item.
- A folder name or title tag over 255 characters, or holding a control,
  format, surrogate or bidirectional code point, is refused whole. It is only
  ever query text, never a claim.

**Text.** Every value entering a report passes the shared projection grammar:
forbidden code points, private paths and credentials are refused, never
repaired. Comparison never strips diacritics or uses fuzzy distance.

Incumbent description HTML is rendered by the one provider renderer, which
drops attributes and refuses event handlers and unbalanced markup; path and
credential checks run on the rendered text, with character references
decoded once. A description containing `<` or `>` is never proposed, so
markup never reaches a write. Markup built to hide a path is an accepted
risk.

**Images.** JPEG, PNG and static WebP are decoded only in a constrained
child, which also computes the perceptual hash.

## AI reviewer boundary

A model is untrusted ([ADR-0004](decisions/0004-ai-is-untrusted.md),
[AI trust boundary](ai-boundary.md)). It never sees a target credential,
chooses inputs, changes policy or gets write authority; everything it reads
is evidence, never instructions.

Reviewer access is separate authority
([ADR-0062](decisions/0062-owner-managed-reviewer-access.md)). Dispatch reads
the mapping revision and secret together and releases the owner-operation
gate before network work; the result write rechecks the revision, so an A-B-A
change cannot make old evidence current.

### OpenRouter

Egress is fixed to `https://openrouter.ai` and one exact model, with
`require_parameters: true`. Cover requests also deny data collection;
description requests also set `allow_fallbacks: false`. Upstream data
practice is not verified. Responses past each endpoint's cap are refused as
`response_too_large`, whatever `Content-Length` says. One deadline spans the
key, catalogue and chat requests; a single read can overrun it by its phase
cap (30 seconds by default). Error bodies are read up to 8 KiB to recognise
known refusals and never kept.

### Contained Codex reviewer

Each use re-admits the image-owned or deployment-owned profile bytes, and an
invalid override never falls back
([ADR-0081](decisions/0081-image-owned-reviewer-provisioning.md)). Device
sign-in uses a private staging `CODEX_HOME`; cancellation or shutdown kills
the exact process group and proves it dead. A new credential is used only
after the owner selects Codex again.

- The container is non-root, read-only, capability-free and
  no-new-privileges, with PID and memory ceilings; its seccomp profile admits
  only Bubblewrap's namespace setup
  ([deployment](../operations/deployment.md#contained-codex-reviewer)).
- Inside, a second seccomp program denies `unshare`, `setns` and process
  creation. No procfs is mounted.
- Broker, supervisor, namespace init and Codex have 64, 64, 64 and 832 MiB
  address-space ceilings.
- Every optional Codex tool is off (shell, agents, plugins, browser, web
  search, image viewing). `danger-full-access` only skips a
  redundant inner sandbox: the process still sees only the sealed binary,
  read-only credential, private job directory, images and one request's
  network.

### Cover observer

The cover observer is the only model use that can influence a decision, as
advisory evidence
([ADR-0049](decisions/0049-owner-confirmed-cover-considerations.md)). It
returns a fixed set of visual properties with explicit unknowns and no
ranking, winner or identity field. The local parser needs exactly one
observation per admitted candidate and checks every binding. The adjudicator
makes no model call and recomputes identity from the retained report.

Each preview is verified against its report digest and re-encoded into a
metadata-free single-frame PNG; admission requires byte equality after
re-decoding. Bytes go into sealed memory descriptors, re-verified in the child
before Bubblewrap copies them read-only.

### Description advice

Description advice sends only bounded provider-backed texts and the owner's
cleanup choices, never owner decisions, titles, identifiers, paths or
credentials, and accepts one bounded description with a closed disposition
([AI trust boundary](ai-boundary.md#where-models-are-used),
[configuration](../reference/configuration.md#description-advice)). An
attempt is durable before transport; after a crash only an explicit retry
calls again.

## Decision and write authority

Only these project authority, and only into the guarded effect pipeline: an
owner `accept` or `owner_entered`, an owner `keep_current` as a corrective
write back to the case target, or a sealed qualified automatic decision
(`candidate`). Proposals, previews, advice and policy dispositions cannot
([ADR-0037](decisions/0037-origin-neutral-guarded-field-effects.md),
[ADR-0071](decisions/0071-book-media-guarded-application.md)). Which values a
field may take, and when automation may decide, is in
[automation policy](../product/automation-policy.md).

### Owner decisions

An owner decision needs the session, CSRF, Origin, target fingerprint, case
currentness and an idempotency key. It names a server-presented proposal or an
owner-entered value. The decision and its audit event commit atomically and
are append-only. Automation never overrides an owner decision.

Withdrawal appends a valueless row, revokes unclaimed authority in the same
transaction and never re-arms automation. The previous value offered as a
suggestion grants no authority: choosing it is an ordinary owner-entered write
that refuses as stale if the field has changed. A manual hold follows the work
to its successor case; Return to automatic releases it only after the scan it
requests succeeds, and only if no later owner decision replaced the hold
([ADR-0039](decisions/0039-continuous-guarded-application.md)).

Owner-entered text is refused, never rewritten, unless it is 1 to 512 code
points of NFC text with no control, path, credential or URI. No automatic path
reads an owner-entered value. Clearing, rollback and undo are not available
([ADR-0085](decisions/0085-owner-entered-values-withdrawal-and-reselection.md)).
An owner may choose a retained cover whose match is not confirmed, but not
one with a positive contradiction.

### Automatic decisions

Automatic policy is decision-only
([ADR-0036](decisions/0036-qualified-decision-only-automatic-policy.md),
[ADR-0087](decisions/0087-operation-bound-qualification-authority.md)). Only
qualified field options can decide, each binding the qualification digest
that allowed it; everything else abstains to the owner. Pause and suspension
fail closed to the owner queue; only a CSRF-protected owner action lifts a
suspension.

Policy bodies are closed and capped at 16 KiB, and drafts and previews
activate nothing. [Activation](../operations/review-case.md#policy-activation)
is a CSRF-protected mutation bound to the expected active policy and exact
draft, and old proposals then fail as stale.

Automation replaces a populated cover only with a confirmed candidate
against a confirmed incumbent, and any other automatic cover write needs a
known-empty target. It replaces a populated ebook description only through
description advice with `description_automatic_application` on. Evidence
never creates series, contributor, title or recording identity on its own;
conflicts go to the owner, never to a write. The explicit flag is set only by
an exact positive.

### Consent and guarded effects

Writes need the owner's library-scoped consent for the exact destination set
shown in Activity; adding a destination lapses it, and policy activation
carries forward only current consent, never widening it. A grant must match
a fresh capability probe. A policy or consent change between the browser's
re-read and the grant is not detected; a capability or library change is
still refused.

Every claim rechecks backup readiness, capability, authority, consent,
dependencies, target pre-state, pause and optimistic state in one transaction.
Failures suspend the connection or destination
([suspended destinations](../operations/backups-and-recovery.md#suspended-destinations)),
and a probe never clears `unexpected_effect_behavior`. Read-only keys cannot
write; update-only keys cannot upload covers.

Audiobookshelf has no compare-and-set. The adapter makes one minimal request
after its final pre-read, then reads back and reconciles; another actor's
change to the same field in that gap can be lost.

- A request with an unknown outcome is never resent. Anything retried passes
  every guard again; withdrawn authority or consent cancels it without a
  request, and a pre-read already showing the desired value verifies without
  one.
- Paired title and subtitle go in one request; both must verify, and a
  partial result is reconciled, never blindly retried.
- Descriptions are compared as the text Audiobookshelf shows, so another
  actor's markup-only edit is overwritten. Read-back verifies only when the
  stored form shows the desired text, and a partially dropped value is never
  resent. A restore that would lose formatting is refused with
  `restore_unavailable`.
- Cover upload reads only the digest-verified original. The private cover
  recovery directory must belong to the runtime user with `0700` and `0600`
  modes; a failure there stops only that cover effect.

Restart and reconciliation are in
[guarded field-effect recovery](../operations/backups-and-recovery.md#guarded-field-effect-recovery).

## Persistence and output

### What may cross the boundary

Reports, proposals and cases hold only bounded identities, digests, admitted
claims with closed provenance and comparison facts, never a credential,
private path, rejected provider value, response body or arbitrary EPUB text
([data privacy](data-privacy.md)).

### Publication

Report and pointer publication is one transaction that first rechecks target
and media currentness; reports are immutable by trigger. Proposal creation
and review admission recheck the report and pointers inside SQLite and hold
no credential or write capability. External reads stay outside write
transactions. [Preparation](../operations/review-case.md#preparation) creates
no owner decision, plan or write.

### Schema admission

Every store follows
[the clean-state rule](../reference/compatibility.md#the-clean-state-rule):
anything but absence or the exact current schema fails closed before any
mutation. Admission does not rely on SQLite alone:

- A read-only open re-evaluates every CHECK constraint itself, because
  SQLite's read-only integrity check skips them. A CHECK it cannot extract
  refuses.
- The permission-readiness check does not see a CHECK-breaking row, an
  accepted gap since adding one needs write access to the volume.
- Browse and attention facts are compared with their documents at open for
  every current row; any difference refuses the database
  ([ADR-0084](decisions/0084-persisted-attention-facts.md)). No product code
  writes with `REPLACE`, which would skip delete triggers.

Database-root crash recovery is explicit, runs under an exclusive lock and
rolls back a private journal on verified copies first; unknown or malformed
state refuses.

### Read caches

In-memory caches ([sizes](data-privacy.md#what-surfaces-show)) hold only
admitted documents, never credentials, and grant no authority. Browse
documents are reused only for the exact bytes they were validated from; a
refusal is never cached
([ADR-0083](decisions/0083-validated-browse-document-reuse.md)). Activity
facts are re-verified against source rows
([activity reads](sqlite-topology.md#activity-reads)); an out-of-band edit
may show briefly there, but Activity feeds no write. Library, Attention
and Activity each read one snapshot and fail closed on a malformed row;
consent grants and claims read the writable store afresh.

### Diagnostics and support

Status and support routes need the session; export also needs CSRF and is
re-redacted and size-bounded. Secrets, target values, paths, databases,
environment, media and cover bytes never enter diagnostics. Process events on
standard error carry only allow-listed keys with closed values, never a
title, query, URL, body, exception message, credential, path or item id;
writing them never raises into the caller and nothing is persisted
([log lines](../operations/observability.md#log-lines)).

## Operator and maintainer tools

None of these has target-write, media-write or startup authority.

- [Batch manifests](../operations/ebook-evidence-batches.md) are private
  immutable files bound by content-addressed ID, holding no credential.
- Policy and shadow-decision preview is read-only; output is not approval.
- [Provider-evidence replay](../development/provider-evidence-replay.md)
  capture strips keys, authorization and contact identity and scans for
  secrets before sealed publication; replay uses placeholder credentials and
  no network.
- The qualification-state bridge (`scripts/qualification_state_bridge.py`)
  is test-state tooling with no credential, network or provider authority,
  and never writes its source.
- [Reviewer cost measurement](../development/reviewer-cost-measurement.md)
  is read-only by default; paid runs need explicit confirmation and a
  monitored budget, which an in-flight request may overshoot.

## Automated scanning boundary

`dependency-audit`, a required public-gate job, scans `uv.lock` with a
digest-pinned OSV-Scanner on every pull request and push to `main`, failing
on any known advisory in runtime or development dependencies;
`.github/workflows/public-gate.yml` has the command. Frontend checks are in
[what merging proves](../../CONTRIBUTING.md#what-merging-proves).

The only way to hold a failure open is an `osv-scanner.toml` entry naming one
advisory, a reason and an expiry at most 90 days out; there is no severity
floor or package exclusion.
`tests/unit/test_security_scanning_contract.py` enforces the register and
checks that every workflow has `contents: read`, pins actions to commits and
sets `persist-credentials: false`.

Not covered: triage and fixes, source static analysis, pull-request
dependency review (these need GitHub Code Security), extra secret scanning,
SBOMs and container image scanning.
