# Security and threat model

This is the authority for security review of any change that touches
credentials, network egress, parsers, AI inputs, writes, backups,
authentication or upgrades. It states the invariants the code must keep. Exact
shapes live in their artifacts: the
[review OpenAPI contract](../reference/review-openapi-v1.json) for responses,
the [configuration reference](../reference/configuration.md) for settings, the
[ADRs](decisions/README.md) for rationale, and
`.github/workflows/public-gate.yml` with `osv-scanner.toml` for scanning.

The invariants, in short:

- Media is read-only. The only writes are guarded metadata and cover updates
  through Audiobookshelf's supported API, each with a pre-read and a read-back.
- No report, proposal, model output or policy preview carries write authority.
  Only an owner decision or a qualified automatic decision does, and only
  through the guarded effect pipeline.
- Secrets are write-only at the HTTP boundary and never appear in a report,
  log, diagnostic, support bundle, URL, browser cache or model input.
- Every external input (provider response, media file, sidecar, image, model
  output, stored row) is hostile. It is bounded, strictly decoded and reduced
  to closed typed facts before use. Failures are value-free.
- Persisted state is accepted only in its exact current schema. Nothing is
  migrated or repaired; an earlier or damaged store needs a clean rebuild.

## Assets and actors

The assets are the owner's library metadata, the target, provider and
reviewer credentials, the owner password and sessions, and the private
evidence the product keeps (reports, cover previews, decisions, history).

| Actor | Trust | Boundary |
| --- | --- | --- |
| Network visitor | None | [Unauthenticated reach](#unauthenticated-reach) |
| Signed-in owner | Full product authority | [Requests and CSRF](#requests-and-csrf) |
| Audiobookshelf (the target) | Hostile bytes | [Target](#target-audiobookshelf) |
| Metadata providers | Hostile bytes | [Metadata providers](#metadata-providers) |
| Mounted-media owner | Hostile files and names | [Parsers](#parsers-and-hostile-input) |
| AI reviewer | Untrusted output | [AI reviewer boundary](#ai-reviewer-boundary) |
| Local actor with volume access | Can replace files | [Schema admission](#schema-admission) |
| Dependencies | Untrusted | [Scanning](#automated-scanning-boundary) |

Anyone who can write the state volume can already replace the whole store.
The product refuses corrupt or partial state (no-follow descriptors,
owner-only modes, exact schemas, canonical decoding) but never repairs it.
Typed in-process collaborators are trusted to honour their interfaces;
deliberate interpreter-level sabotage is out of scope.

## Authenticated web surface

The review server is the only network entry point. It has no generic write
route and no target client of its own. FastAPI keeps only request bounds,
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
([ADR-0058](decisions/0058-settings-authority-and-first-boot.md),
[ADR-0086](decisions/0086-deployment-held-setup-claim-token.md)).

- Each unclaimed start mints a token with `secrets.token_urlsafe(32)` and
  stores only its domain-separated SHA-256 digest, revoking any earlier
  token. The token goes, without following links, to the owner-only
  `/data/private/setup-claim-token` and once to standard output, bypassing
  logging.
- A claim needs the exact Origin, a nonce in both a host-only cookie and a
  header, and the token. At most 100 nonces are outstanding for 600 seconds,
  10 per client, in memory only.
- A wrong token is refused with `403 claim_token` before Argon2, with no state
  change or throttle debt. A password shorter than 12 code points or longer
  than 1,024 UTF-8 bytes is refused with `422 invalid_password`.
- One transaction compares the token in constant time, claims ownership,
  clears the digest and stores the Argon2id verifier. A concurrent loser gets
  `409`. The token file is then removed.
- The token never reaches a response, log, exception or evidence. Setup
  validation errors are a fixed `422` that echoes no input.
- Later setup steps use an ordinary session. Kickoff closes setup routes for
  good and swaps to the ordinary runtime on the same listener.

Startup admits only the exact seeded predecessor or valid current
incomplete setup. Older incomplete setup needs a clean rebuild; any other
non-empty, malformed or store-mode state fails closed.

Connection setup makes one bounded `GET /api/libraries` with TLS verification
on, redirects and ambient proxies off, and the key sent only to the admitted
origin. Endpoint and key are stored only after it succeeds. Reach failures
are closed codes; the page names the origin from the owner's own input, so no
key or userinfo is echoed. Service folder paths are kept only to compare with
mount mappings; each comparison checks one root's type and reachability and
never reads media.

### Sign-in and sessions

- The password is checked against an owner-only, no-follow Argon2id hash file
  or the settings-store verifier.
- A session is an opaque cookie: HttpOnly, SameSite=Strict, Secure when the
  browser scheme is HTTPS. Its absolute lifetime is five minutes to 30 days,
  default 30 days; activity never extends it.
- Sessions live in `private/review-sessions.sqlite`
  ([ADR-0060](decisions/0060-dedicated-review-session-database.md)) with its
  own connection and lock. It stores an HMAC session lookup keyed by the exact
  verifier, CSRF digests, expiry, revocation and throttle rows. Replacing the
  verifier invalidates every session.
- Logout deletes the cookie only after revocation is durable.

Sign-in allows five attempts per client in any five minutes, under a global
ceiling of 100; a refusal is `429 throttled`. A granted session clears only
its own client's attempts. The client key is an HMAC, never a stored address.

The client is resolved from the transport, never from a header it chooses. An
untrusted peer is its socket address and its `X-Forwarded-For` is ignored. A
peer in `trusted_proxy_ips` is the rightmost entry of its single
`X-Forwarded-For` line. An IPv6 address counts as its /64. A trusted proxy
request with no, several or unparsable lines maps to one shared key. A trusted
proxy must therefore append or overwrite `X-Forwarded-For`; one that passes a
client's header through lets that client choose its key.

The bound holds per identity, not per attacker. Twenty identities (twenty IPv4
addresses or IPv6 /64s) can hold sign-in at the global ceiling for every
client. This is accepted: closing it needs an allow list or owner lockout. The
forced Argon2 work stays near one verification every three seconds.

### Requests and CSRF

- Exact Host is enforced on every request, exact Origin on every unsafe one.
  Forwarded Host or scheme is honoured only from a configured trusted proxy;
  Uvicorn's own proxy handling is off.
- Every unsafe request needs the session, the CSRF token and an
  `x-ml-build-identity` header matching the installed build.
- Serving an authenticated page rotates the CSRF digest. The raw token
  appears only in that private, no-store HTML and then only in page memory,
  never in a cookie, browser storage, URL or log.
- After a `403 csrf` the client renews the token and resends once, only for a
  side-effect-free request or one with an idempotency key. A `401` goes to
  sign-in; nothing retries without fresh authentication.
- Mutations never show optimistic success. A lost acknowledgement reloads
  authoritative state without resubmitting.
- A `GET` under `/api/v1/` may carry `x-ml-work-class: background`. It
  changes local scheduling only, after the Host and Origin checks, never
  authentication, CSRF or what a read returns. Mutations are always
  foreground.

The React workspace uses no browser storage, inline script, external origin,
worker or source map. The packaged loader verifies every asset's path, size,
digest and build identity before serving an authenticated shell; a failure is
one recovery state with no reload loop.

### Responses and caching

Every response has a restrictive same-origin CSP, frame denial,
`no-referrer` and `nosniff`; browser HTTPS adds HSTS. Fonts are vendored, so
no untrusted font is parsed. Errors are fixed value-free codes.

Everything is `private, no-store` except content-hashed assets and
session-checked cover previews addressed by digest, which are
`private, max-age=31536000, immutable`. Logout does not clear the browser
cache, so that browser profile can still show a viewed cover for up to a year.

The library cover route serves only the preview the item's current report
binds to its Audiobookshelf cover, verified against its digest and decoded
nowhere.

The preparation panel's snapshot carries at most five current and eight
recently completed book names. Those names never enter update-stream frames,
logs, audit identifiers, diagnostics, support bundles or reviewer input.
Dismissing the panel needs the exact terminal run identity and cannot hide
blocked work.

`GET /api/v1/updates` streams invalidation, never state: each frame is exactly
a sequence, a topic and an optional case identity, and the browser refetches
over ordinary HTTP. The session is re-checked while the stream runs, and the
stream has its own lifetime.

### Settings API

Settings documents are closed and bounded (32 connections or providers, 128
libraries, a 256 KiB request body) and private. The revision check and the settings and secret update
are one transaction. A secret must be 1 to 2,048 printable non-whitespace
ASCII characters with no scheme prefix. Reads show only presence and, for a
secret of eight or more characters, its last four characters. Reviewer bootstrap and profile paths are deployment
settings, never browser fields.

When `/data/config.json` exists, settings are file-managed: covered writes
are refused and the store is not read.

New settings start a dormant successor runtime. One cutover lock commits and
switches; a commit failure keeps the predecessor, and a fault after commit
stops the server rather than run mismatched authority.

### Transport

[ADR-0072](decisions/0072-explicit-local-transport.md) admits three modes:

- **HTTP**: plaintext, including credentials. For an owner-controlled network
  or VPN only.
- **Direct HTTPS**: TLS ends in `review-serve`. Certificate and key are a
  pair of owner-controlled, no-follow files; the key must be an owner-only,
  unencrypted PKCS#8, RSA or EC PEM. They are validated as one identity from
  immutable snapshots before the database or listener opens. TLS 1.2 is
  limited to ECDHE AEAD ciphers. There is no TLS-disable flag.
- **Proxied HTTPS**: at least one trusted proxy IP. Only that peer may send one
  complete `X-Forwarded-Host` and `X-Forwarded-Proto` pair; duplicates,
  partial pairs, chains and `Forwarded` are rejected.

The edge admits at most 100 connections, limits incomplete events to 16 KiB
and closes incomplete headers after 10 seconds.

## Credentials and egress

Secrets are read only in infrastructure while building their named client. A
disabled provider may keep its stored key, but nothing is built from it.
Every HTTP client ignores ambient proxy settings, follows no redirect itself,
verifies certificate and host name on HTTPS, and uses an explicit timeout and one connection. No endpoint is
arbitrary and nothing is cached.

### Target (Audiobookshelf)

The API key goes only to the configured origin and only on the route shapes
the operation uses: library and item reads, the capability probe, and the
guarded write and upload. Catalogue refresh is read-only.

An `http://` endpoint is allowed. It sends the API key, metadata and writes in
plaintext, so use it only on an owner-controlled network or VPN
([ADR-0072](decisions/0072-explicit-local-transport.md)).

Target TLS, authentication, authorization and availability failures become
durable connection conditions with a closed code only. Owner recovery is a
CSRF-bound probe that echoes no endpoint or transport detail.

### Metadata providers

A disabled provider builds no client. Disabling also removes its library
associations, which enabling does not restore. Shared rules:

- Every adapter dispatches through the bounded session in
  `adapters/providers/sdk.py`, which enforces request, retry, byte and
  deadline limits.
- Responses are read under per-response and aggregate byte ceilings and
  projected to closed facts. Overruns, quota and service failures are typed
  outcomes, never a no-match.
- A plan that cannot fit (for example, more than eight ISBN families) is
  refused before a client exists, so no partial set is sent. Input with no
  ISBN and no complete title-and-author returns `insufficient_input` with no
  request.
- Retries cover only named transient statuses and allow-listed transport
  causes. Exception text is never inspected; TLS, proxy and decoding failures
  are not retried.
- Each provider gets a 20-second share of the 60-second preparation deadline,
  and one-second request-start pacers are shared with recovery work.
- Query shaping changes only the request. Reports keep a closed dispatch
  lineage, never queries, credentials or responses.
- Audiobooks go only to `AUDIOBOOK_DISPATCH_ADAPTERS`: Audible catalog,
  Audnexus, AudioSilo, Hardcover and Open Library
  ([ADR-0061](decisions/0061-audiobook-provider-posture.md)).

| Provider | Egress | Credential | Retries |
| --- | --- | --- | --- |
| Hardcover | `POST` to the fixed GraphQL URL | Bearer token, this request only | 500, 502, 503, 504; 429 only when `Retry-After` or Hardcover's rate-limit reset gives a wait that fits the deadline |
| Open Library | Fixed-origin `GET` | Contact email in User-Agent | 500, 502, 503, 504; never 429 |
| Google Books | Fixed-origin `GET` | Optional API key | 503 only |
| Inventaire | Fixed-origin `GET` | None | 500, 502, 503, 504; 429 only with a bounded `Retry-After`; at most three attempts per request |
| Audible catalog | `GET` to ten fixed marketplace hosts | None | `Retry-After` on 429 and 503, up to one minute |
| Audnexus | `GET` to `api.audnex.us` | None | `Retry-After` on 429 and 503 |
| AudioSilo | `GET` under `meta.audiosilo.app/api/v1` | None | `Retry-After` on 429 and 503 |
| iTunes Search | `GET` to `itunes.apple.com/search`, ebooks only | None | `Retry-After` on 429 and 503 |

Provider-specific boundaries:

- **Hardcover**: GraphQL `errors` are checked before `data` and yield nothing;
  their text is never kept.
- **Open Library**: the contact email is a non-secret setting sent only to
  its fixed origins and stored nowhere else.
- **Google Books**: only the credential-bearing controller reads the key.
  Only an actual Google burst response arms its burst pacer.
- **Audible catalog**: hosts come from a closed marketplace map, so no
  provider value becomes a host. An oversized page is refused, never
  truncated.
- **Audnexus, AudioSilo**: paths are built only from identifiers that match a
  closed shape, so they cannot leave the base path. AudioSilo's HTML error
  pages are never read as evidence.
- **iTunes Search**: the query goes only in `term`. It states no narrator or
  runtime, so it can never satisfy runtime corroboration.

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
outcome and no socket opens. Remote
covers use their own address-pinned transport with bounded bytes, attempts and
deadline. Embedded audiobook art is never acquired. Reports never keep the
remote URL.

### Provider recovery

Recovery is background egress under the same limits
([provider recovery](../operations/provider-recovery.md)). Before building a
client it rechecks the source report, review package, target and media
identity, provider binding and configuration generation. Provider work runs
outside write transactions. Missing or contradictory quota evidence takes the
conservative path.

The Library throttling view shows closed states and a remedy based on whether
a key exists, never its value. **Retry now** only raises
`provider_recovery_generation` through the ordinary Settings write; it sends
no request and is refused when settings are file-managed.

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
inherited `PATH`, a minimal environment, its own session, and limits on CPU,
memory, files, output and wall clock, installed before exec. Its output and
sidecars are read in the EPUB child under a closed recognition grammar, never
an XML or container parser.

- An over-long, unreadable or non-UTF-8 sidecar is dropped whole.
- A badly shaped identifier is reported as malformed, not believed.
- A directory listing refuses past 1,024 entries; path evidence uses bare
  names only.
- A description over 512 characters is checked whole, then kept only as
  `oversized`; the cut text is never proposed or written.
- A multi-file item allows at most 512 files and a 600-second reading wall
  ([ADR-0066](decisions/0066-multi-file-audiobook-composition.md)). Each file
  gets its own child, and one hostile file denies the whole item.
- A folder name or title tag used for discovery is refused whole if it is
  over 255 characters or holds a control, format, surrogate or bidirectional
  code point. It becomes query text only, never a claim.

**Text.** Every value entering a report passes the shared projection grammar:
forbidden code points, private paths and credentials are refused, never
repaired. Comparison never strips diacritics or uses fuzzy distance.

Audiobookshelf stores descriptions as sanitised HTML. Markup in an incumbent
description is rendered by the one provider renderer, which drops attributes
and refuses event handlers and unbalanced markup, and path and credential
checks run on the rendered text. Character references are decoded exactly
once. Any description containing `<` or `>` is ineligible for proposal, so
markup never reaches a write. Markup built deliberately to hide a path is an
accepted risk: the check guards against accidental leakage.

**Images.** JPEG, PNG and static WebP are decoded only in a constrained
child, which also computes the perceptual hash. Reports keep digests,
dimensions and preview references.

## AI reviewer boundary

A model is untrusted ([ADR-0004](decisions/0004-ai-is-untrusted.md),
[AI trust boundary](ai-boundary.md)). It never sees a target credential,
chooses inputs, changes policy or gets write authority. Packet data, provider
text and images are evidence, never instructions. Without a reviewer the
manual path is complete.

Reviewer access is separate authority
([ADR-0062](decisions/0062-owner-managed-reviewer-access.md)). Dispatch reads
the mapping revision and secret together, releases the owner-operation gate
before network work, and the result write rechecks the revision, so an
A-B-A change cannot make old evidence current.

### OpenRouter

Egress is fixed to `https://openrouter.ai` and one exact model, with
`require_parameters: true`. Cover requests also deny data collection;
description requests also set `allow_fallbacks: false`. Upstream data
practice is not verified. Responses are read in
chunks and refused as `response_too_large` past each endpoint's cap,
whatever `Content-Length` says. One deadline spans the key, catalogue and
chat requests; a single read can overrun it by its phase cap (30 seconds by
default). Error bodies are read up to 8 KiB to recognise known refusals and
never kept.

### Contained Codex reviewer

Boot only reads the image-owned or deployment-owned profile
([ADR-0081](decisions/0081-image-owned-reviewer-provisioning.md)). Each use
re-admits the current profile bytes; an invalid override never falls back.
Device sign-in uses a private staging `CODEX_HOME`, and cancellation or
shutdown kills the exact process group and proves it dead. A new credential
is not used until the owner selects Codex again.

- The container is non-root, read-only, capability-free and no-new-privileges,
  with PID and memory ceilings. Its deny-by-default seccomp profile admits
  only Bubblewrap's exact namespace setup.
- Inside, a second seccomp program denies `unshare`, `setns` and process
  creation. No procfs is mounted.
- Broker, supervisor, namespace init and Codex have 64, 64, 64 and 832 MiB
  address-space ceilings, 1 GiB in total.
- Every optional Codex tool is off, including shell, agents, plugins, browser,
  web search and image viewing. Its `danger-full-access` mode only skips a
  redundant inner sandbox: the process still sees only the sealed binary,
  read-only credential, private job directory, images and one request's
  network.

### Cover observer

The cover observer is the only model use that can influence a decision, and
only as advisory evidence
([ADR-0049](decisions/0049-owner-confirmed-cover-considerations.md),
[ADR-0050](decisions/0050-bound-installed-owner-assistance.md)). Its output is
a fixed set of visual properties with explicit unknowns and no ranking,
winner or identity field. The local parser needs exactly one observation per
admitted candidate and checks every binding. The adjudicator makes no model
call and recomputes identity from the retained report.

Each preview is verified against its report digest and re-encoded into a
metadata-free single-frame PNG; admission requires byte equality after
re-decoding. Bytes go into sealed memory descriptors, re-verified in the child
before Bubblewrap copies them read-only, so no other process can alter them.

### Description advice

The request holds only bounded candidate texts, three cleanup flags and the
pinned instruction and schema: no owner decisions, titles, identifiers, paths
or credentials. The response is one description and one closed disposition.
An attempt is durable before transport; after a crash only an explicit retry
calls again.

## Decision and write authority

Only these decisions project authority, and only into the guarded effect
pipeline: a human decision that accepts a proposal or enters a value
(`accept`, `owner_entered`), a human `keep_current` as a corrective write back
to the case target, or a sealed qualified automatic decision (`candidate`).
Raw proposals, previews, advice and policy dispositions cannot
([ADR-0037](decisions/0037-origin-neutral-guarded-field-effects.md),
[ADR-0071](decisions/0071-book-media-guarded-application.md)). Which values a
field may take, and when automation may decide, is in
[automation policy](../product/automation-policy.md).

### Owner decisions

An owner decision needs the session, CSRF, Origin, target fingerprint, case
currentness and an idempotency key. It names a server-presented proposal or an
owner-entered value. The decision and its audit event commit atomically and
are append-only. Automation never overrides an owner decision.

The owner may withdraw a decision. Withdrawal appends a row with no value,
revokes the decision's unclaimed authority in the same transaction, and never
re-arms automatic handling. The previous value offered as a suggestion grants
no authority: choosing it is an ordinary owner-entered write and refuses as
stale if the field has changed.

A manual hold follows the work to its successor case. Return to automatic
releases it only after the scan it requests completes successfully, and only
if no later owner decision replaced the hold
([ADR-0039](decisions/0039-continuous-guarded-application.md)).

Owner-entered text is refused, never rewritten, unless it is 1 to 512 code
points of NFC text with no control, path, credential or URI. No automatic path
reads an owner-entered value. Clearing, rollback and undo are not available
([ADR-0085](decisions/0085-owner-entered-values-withdrawal-and-reselection.md)).

An owner may choose a retained cover whose match is not confirmed; a positive
contradiction is still refused.

### Automatic decisions

Automatic policy is decision-only
([ADR-0036](decisions/0036-qualified-decision-only-automatic-policy.md),
[ADR-0087](decisions/0087-operation-bound-qualification-authority.md)). Only
qualified field options can decide, each binding the qualification digest
that allowed it. Everything else abstains to the owner. Pause and suspension
fail closed to the owner queue; lifting a suspension is an explicit
CSRF-protected owner action. The built-in posture is automatic, but nothing is
written to Audiobookshelf until the owner grants library consent.

Policy drafts, previews and activation take a 16 KiB closed body. Drafts and
previews activate nothing.
Activation is a separate CSRF-protected mutation bound to the expected active
policy and the exact draft; it retires the current cohort atomically, so old
proposals fail as stale.

The security-relevant field limits:

- A populated cover is replaced automatically only by a strictly better
  confirmed candidate against a confirmed incumbent. An audiobook cover fill
  needs a known-empty target.
- Deterministic policy never fills a populated ebook description. Only
  description advice with `description_automatic_application` on may replace
  one ([configuration](../reference/configuration.md)).
- Evidence never creates series, contributor, title or recording identity on
  its own. Conflicts route to the owner, never to a write.
- The explicit flag can be set only by an exact positive; its absence never
  claims content suits children.

### Consent and guarded effects

Writes need the owner's library-scoped consent for the exact destination set
shown in Activity. Adding a destination lapses existing consent. Policy
activation carries forward only consent current for the outgoing policy and
never widens it. A grant must match a fresh capability probe.

The browser re-reads the consent basis (libraries, capabilities, consent and
the active policy) before a grant. A policy activation or consent change
between that read and the grant is not detected; a capability or library
change is still refused by the server.

Every claim rechecks backup readiness, capability, authority, consent,
dependencies, target pre-state, pause and optimistic state in one transaction.
Authentication or library-access failure suspends the connection; a
permission or unexpected-behaviour failure suspends that destination.
A read-only probe may clear only evidence it disproves, never
`unexpected_effect_behavior`. Read-only keys cannot write; update-only keys
cannot upload covers.

Audiobookshelf has no compare-and-set. The adapter makes one minimal request
after its final pre-read, then reads back and reconciles. Another actor's
change to the same field in that gap can be lost; there is no cross-system
atomicity.

- A request with an unknown outcome is never resent. A retry whose pre-read
  already shows the desired value is verified without a request.
- A connection-staled plan is re-planned at most three times, after a fresh
  successful probe.
- A claim found at startup that sent nothing returns as retryable and must
  pass every guard again.
- Withdrawn authority or consent cancels a retry without a request.
- Paired title and subtitle go in one request; both must verify, and a
  partial result is reconciled, never blindly retried.
- A description pre-read and read-back compare the text Audiobookshelf
  shows. Another actor's markup-only edit, such as bolding a word, is not a
  conflict and is overwritten. Read-back verifies only when the stored form
  shows the desired text; a partially dropped value is never resent.
- Restoring a description whose original formatting would be lost is refused
  with `restore_unavailable`; the owner restores the formatting in
  Audiobookshelf.
- Cover upload reads only the digest-verified original.

[Backups and recovery](../operations/backups-and-recovery.md) covers the
backup readiness each claim requires. The private cover recovery directory
must belong to the runtime user with `0700` and `0600` modes; a failure there
stops only the affected cover effect.

## Persistence and output

### What may cross the boundary

Reports, proposals and cases hold only bounded identities, digests, admitted
claims with closed provenance, and comparison facts. They hold no credential,
private path, rejected provider value, response body or arbitrary EPUB text.
See [data privacy](data-privacy.md).

### Publication

Report and pointer publication is one transaction that first rechecks target
and media currentness. Reports are immutable by trigger. Proposal creation and
review admission recheck the report and pointers inside SQLite and accept no
credential or write capability. External reads always happen outside write
transactions, so a slow read never holds the writer lock.

Preparation runs on two fixed worker lanes after the owner's request is
persisted ([ADR-0031](decisions/0031-persisted-review-preparation.md),
[ADR-0078](decisions/0078-preparation-claim-and-crash-recovery.md)). Restart
requeues a claim; a third crashed attempt becomes a bounded failure. Only a
fully successful scan may tombstone a missing work.

### Schema admission

Every store accepts only absence or its exact current schema. An earlier
schema, an extra object, altered SQL or a failed integrity check fails closed
before any mutation and is never migrated, imported or repaired
([ADR-0013](decisions/0013-pre-release-clean-schema.md)). Recovery is a clean
rebuild; see [upgrades](../operations/upgrades.md).

- A read-only open re-evaluates every CHECK constraint itself, because
  SQLite's read-only integrity check skips them. A CHECK it cannot extract
  refuses.
- The fresh permission-readiness check does not see a CHECK-breaking row.
  This gap is accepted: adding one needs write access to the volume.
- Stored content is validated when a read selects it.
- Browse and attention facts are compared with their documents at open for
  every current row; any difference refuses the database
  ([ADR-0084](decisions/0084-persisted-attention-facts.md)). No product code
  writes with `REPLACE`, which would skip delete triggers.
- Derived facts are bound to Python's Unicode data version; an upgrade that
  changes it requires a clean rebuild.

Crash recovery of the database root is explicit, runs under an exclusive
lock, and rolls back a private journal on verified copies first. Unknown or
malformed state refuses.

### Read caches

Caches hold only documents the store already admitted, never credentials,
are never persisted and grant no authority.

- Browse documents are reused only for the exact bytes they were validated
  from: 512 entries and 8 MiB of key bytes
  ([ADR-0083](decisions/0083-validated-browse-document-reuse.md)). A refusal
  is never cached.
- Cover currentness: 16,384 entries and 8 MiB.
- Activity facts: up to 64 MiB, witnessed against source rows with a rolling
  re-verification ([ADR-0082](decisions/0082-runtime-activity-facts.md)). An
  out-of-band edit may show briefly in Activity, which feeds no write path.

Library, Attention and Activity each read one snapshot and fail closed on a
malformed row. Consent grants and claims always read the writable store
afresh.

### Diagnostics and support

Status and support routes need the session; export also needs CSRF and is
re-redacted and size-bounded. Secrets, target values, paths, databases,
environment, media and cover bytes never enter diagnostics
([observability](../operations/observability.md)).

Process events go to standard error through `emit_provider_events` in
`application/failure_diagnostics.py` and its sibling projections. Lines carry
allow-listed keys with closed values, at most 1,024 bytes, and never a title,
query, URL, body, exception message, credential, path or item id; items
appear only as a hashed reference. Writing can never raise into the caller,
and nothing is persisted. A failed preparation shows only a `prep-` reference
built from action identity, stage, attempts and a closed code.

## Operator and maintainer tools

None of these has target-write, media-write or startup authority.

- **Foreground batch manifests**
  ([guide](../operations/ebook-evidence-batches.md)): private immutable files
  bound by content-addressed ID, holding no credential. One configuration
  snapshot governs the run.
- **Policy and shadow-decision preview**: read-only database access that
  leaves bytes and sidecars untouched. Output is data, not approval.
- **Provider-evidence replay**
  ([guide](../development/provider-evidence-replay.md)): capture strips keys,
  authorization and contact identity and scans for secrets before sealed
  publication. Replay uses placeholder credentials and no network.
- **Qualification-state bridge** (`scripts/qualification_state_bridge.py`):
  test-state tooling, not upgrade authority. It never writes its source and
  has no credential, network or provider authority.
- **Reviewer cost measurement**
  ([guide](../development/reviewer-cost-measurement.md)): read-only by
  default; paid runs need explicit confirmation and a monitored budget, and an
  in-flight request may overshoot it.

## Automated scanning boundary

`dependency-audit`, a required public-gate job, scans `uv.lock` with
OSV-Scanner on every pull request and push to `main`, failing on any known
advisory. Runtime and development dependencies are audited together. The
scanner is pinned by digest and fetches current advisories; the command in
`.github/workflows/public-gate.yml` reproduces it locally. The frontend is
audited by `npm audit --audit-level=low` and `npm run licenses`.

The only way to hold a failure open is an entry in `osv-scanner.toml` naming
one advisory, a reason and an expiry at most 90 days out. There is no
severity floor or package exclusion. `tests/unit/test_security_scanning_contract.py`
enforces the register and checks that every workflow has `contents: read`,
pins actions to commits and sets `persist-credentials: false`.

Not covered: triage and fixes, source static analysis and pull-request
dependency review (waiting on GitHub Code Security, #674), extra secret
scanning, SBOMs and container image scanning.
