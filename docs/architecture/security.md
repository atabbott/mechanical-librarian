# Security and threat model

This is the authority for security review of any change to credentials,
egress, parsers, AI inputs, writes, backups, authentication or upgrades. It
states the invariants the code must keep; exact shapes live in the
[OpenAPI contract](../reference/review-openapi-v1.json),
[configuration reference](../reference/configuration.md) and
[ADRs](decisions/README.md).

In short:

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
- Persisted state is accepted only in its exact current schema. Only
  `regenerate-derived` migrates, and only the ledger.

## Assets and actors

The assets are the owner's library metadata, the target, provider and
reviewer credentials, the owner password and sessions, and the product's
private evidence (reports, cover previews, decisions, history).

- Network visitor, untrusted: [unauthenticated reach](#unauthenticated-reach).
- Signed-in owner, full product authority:
  [requests and CSRF](#requests-and-csrf).
- Audiobookshelf (the target) and metadata providers, hostile bytes:
  [target](#target-audiobookshelf), [providers](provider-egress.md#metadata-providers).
- Mounted-media owner, hostile files and names:
  [parsers](#parsers-and-hostile-input).
- AI reviewer, untrusted output: [AI boundary](#ai-reviewer-boundary).
- Local actor with volume access, can replace files:
  [schema admission](#schema-admission).
- Dependencies, untrusted: [scanning](#automated-scanning-boundary).

Anyone who can write the state volume can replace the whole store;
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
  `409`. The token file is removed. Later steps need a session, and
  kickoff closes setup for good.

Startup admits only the exact seeded predecessor, valid current incomplete
setup, or
[kept provider answers](decisions/0101-reuse-stored-provider-answers.md)
alone; any other non-empty, malformed or store-mode state fails closed.
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
- In store mode, Settings changes the password after checking the current
  one, which spends a sign-in attempt. One transaction stores the new verifier
  only while the stored one is the one checked. The changing browser gets a
  new session and every other session ends. File mode answers `409`.
- In either mode, Sign out other browsers checks this session's CSRF token and
  deletes every other session in one transaction. It clears no attempts.
- A workspace page opened without a session sends the browser to sign-in with
  that page as `next`. Sign-in returns only to a workspace page on this
  origin: the server matches `next` against the workspace routes and a
  URL-safe query, and the page checks the origin again before it navigates.
  Anything else, including a scheme, host, `//` or backslash, returns to
  Library.

Sign-in allows five attempts per client in any five minutes, under a global
ceiling of 100; a refusal is `429 throttled`. A granted session clears only
its own client's attempts; a password change clears every client's. The
client key is an HMAC keyed by the verifier, never a stored address.

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
  proxy handling is off.
- Every request but `GET`, `HEAD` and `OPTIONS` has its body bounded before
  routing, at 2 KiB unless its route needs more (`413 body_too_large`).
- Every unsafe request needs the session, the CSRF token and an
  `x-ml-build-identity` header matching the installed build.
- The build identity is a non-secret version gate. It is sent only as a
  response header to owner-authenticated `/api/v1` requests: never on a 401,
  sign-in, asset recovery, an early Host, Origin or body-size refusal, or
  without a valid session
  ([ADR-0091](decisions/0091-decode-browser-responses-and-report-uncertain-writes.md)).
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
and build identity before serving an authenticated shell. Setup's Start sets
a cookie, `ml_first_review`, holding no credential. The workspace clears it
when the first review guide closes. Covers' walkthrough sets
`ml_covers_intro=seen` when it closes, and its Hide sets `ml_covers_picks=hidden`;
neither holds a credential.

A description's formatting is read in the browser without handing markup
to the page: a strict reader rebuilds a closed set of tags (bold, italic,
underline, strikethrough, paragraphs, breaks and lists) as React elements
with no attributes, and drops any other tag, keeping its words. A refused
element, an event handler, a link, a comment, unbalanced or over-deep
markup, or an over-bound value leaves the description shown as written
([ADR-0106](decisions/0106-show-description-formatting.md)).

### Responses and caching

Every response has a restrictive same-origin CSP, frame denial,
`no-referrer` and `nosniff`, plus HSTS under browser HTTPS. Fonts are
vendored. Errors are fixed value-free codes.

Everything is `private, no-store` except content-hashed assets and
session-checked cover previews addressed by digest (`private,
max-age=31536000, immutable`). Logout keeps the browser cache, so that profile
can show a viewed cover for up to a year. The library cover route serves only
the preview the current report binds to the item's cover, digest-verified and
never decoded.

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

### Find this book

The [Find this book](../product/find-this-book.md) routes read and write
only through named application operations, addressed by connection, library
and item. The link view, search and preview need the session; search, choose
and withdraw are unsafe requests under every check above, with the CSRF token
checked before the body is read. Bodies are bounded at 16 KiB for a search
and 4 KiB for a withdrawal. A work that could not finish, or whose library
has no searchable source, is refused before any request is sent.

A search is refused, before any request, unless its title and authors pass
the owner-entered text rule, its ISBN has a valid checksum and its ASIN
matches the ASIN grammar. One runs at a time per session and 30 per source per
sliding hour. A source with a current recovery deferral is refused, and one
whose shared pacer cannot admit it within 10 seconds is refused as busy. The
search then sends one request in the source's existing shape
([provider egress](provider-egress.md#owner-search)), on a worker thread,
never in the event loop.

Search state is held only in process memory, keyed by a digest of the session
token, under a 256-bit random search ID, for 15 minutes. A new search replaces
it, and logout, a settings change or a restart discards it. A foreign search
and an expired one get the same `search_not_found`. Results are text only.
The per-session and per-source limits are held for the whole process, so a
settings change keeps them and only a restart clears them.

A preview is served only to the session that owns the search, only for a
position on that search's cards, and refused when `Sec-Fetch-Site` is present
and not `same-origin`. It is fetched only from the cover URL the source's
adapter already admits, only while the connection's cover switch is on, and
decoded and re-encoded to a bounded PNG in the cover child. It is kept with the
search, served `private, no-store`, never written to disk or the artifact
store, and never a cover candidate.

Choose records from the server's retained card, never from an identity the
browser sends. Choose and withdraw each need an `Idempotency-Key` of 16 to 128
printable characters. The key is read from the database first: the same
request returns the stored answer, even after the search expired, and a
different request under it is refused `idempotency_conflict`. Choose then
refuses a card that names no record, a scope the card does not offer, a work
whose catalogue identity changed, and a link that changed since the search.
Withdraw is refused unless the row it names is current. The link row and the
work's fresh mark commit in one write transaction or not at all. A fault after
it answers `503 unavailable`, and the browser asks the owner to check the
link before choosing again.

An [identifiers file](../reference/identifiers-file.md) is sent as its own
bytes, as `text/plain`, and refused above 1 MiB before it is read. It is
decoded as strict UTF-8 and parsed in process in linear time. Its paths are matched in memory only: no path, row or item ID is
kept, logged or answered. The preview is held in process memory for its
session for 15 minutes. Import and withdraw each need an `Idempotency-Key`.
An import's links, its record and its event commit in one transaction, and
Withdraw this import changes only links that are still current.

### Transport

Transport modes and their rules are in
[origin, TLS and proxies](../reference/configuration.md#origin-tls-and-proxies)
and [network access](../operations/deployment.md#network-access)
([ADR-0072](decisions/0072-explicit-local-transport.md)). HTTP carries
credentials, sessions and metadata in plaintext, so it is for an
owner-controlled network or VPN only. There is no TLS-disable flag. Direct
TLS validates certificate and key as one identity before the database or
listener opens and limits TLS 1.2 to ECDHE AEAD ciphers. The edge admits at
most 100 connections, limits incomplete events to 16 KiB and closes a
request whose headers take over 10 seconds or whose body takes 30 more.

## Credentials and egress

Secrets are read only in infrastructure while building their named client.
Every HTTP client ignores ambient proxy settings, follows no redirect itself,
verifies certificate and host name on HTTPS, and uses an explicit timeout and
one connection. No endpoint is arbitrary.

### Target (Audiobookshelf)

The API key goes only to the configured origin, on the route shapes the
operation uses: library and item reads, the capability probe, and the
guarded write and upload. An `http://` endpoint is plaintext, under the
[transport](#transport) rule. Target TLS, authentication, authorization and
availability failures become durable connection conditions with a closed
code only; owner recovery is a CSRF-bound probe that echoes no endpoint or
transport detail.

### Providers and recovery

Metadata providers, provider cover admission and provider recovery are
specified in [provider egress](provider-egress.md), which is part of this
authority: a change there is reviewed exactly as a change here.

## Parsers and hostile input

No untrusted media or image byte is parsed in the server process. Where the
kernel allows, each parser helper is [confined](parser-confinement.md) to its
handed files.

**EPUB.** A child with no credentials or persistence parses one bounded
immutable snapshot
([ADR-0011](decisions/0011-isolated-epub-inspector.md)). The parent
revalidates file content and target identity inside the publication
transaction, so a replaced file is refused. Digits inside non-ISBN schemes
and URIs never become ISBNs. A declared cover that `encryption.xml` lists is
refused as `unsupported_format` before any byte is read, and no member
name reaches a report.

When neither the EPUB 3 `cover-image` nor the legacy `meta name="cover"` is
valid, the child tries three cover fallbacks in a fixed order: a legacy meta
naming a non-image item, a guide `cover` reference, and `cover-image` on a
non-image item. It reads at most one wrapper page, with the opening reader's
document rules and per-resource bounds, charged to the selection totals and
the child's deadline; the page is never rendered, stored or sent. Only HTML
`img/@src` and SVG `image/@href` or `@xlink:href` count, and each must resolve
through the package's member-name normalisation to exactly one local manifest
image, one hop only. A remote, `data:` or fragment-only reference, a traversal,
a directory, a self or wrapper reference, a repeated reference attribute, a
second image, a wrapper or image the package declares encrypted, or fallbacks
that name different images is a closed refusal, never a choice. A recovery is shadow evidence only: the cover source stays
`absent`, no image byte is read, and it is neither a candidate nor
verification of the current cover. No bound is raised. Only closed fallback
and refusal codes reach the report; no reference, member name, wrapper text or
image byte reaches a report, log, diagnostic, status or support bundle.

**Audiobooks.** `ffprobe` runs over a passed descriptor
([ADR-0063](decisions/0063-isolated-audiobook-inspector.md)) with no inherited
`PATH`, and CPU, memory, file, output and wall-clock limits. Its output and
sidecars are read in the audio child under a closed recognition grammar, never
an XML or container parser.

- An over-long, unreadable or non-UTF-8 sidecar is dropped whole; a badly
  shaped identifier is reported as malformed, not believed. A UTF-8 sidecar
  starting with one byte-order mark is also read once without it, in the same
  child under the same bounds; what only that reading finds is recorded in its
  manifest entry, never as a claim, a refusal or a reason to withhold one.
  Recorded values pass the projection grammar, carry no excerpt or
  association text, and are dropped for size before they could deny an item
  or its report. UTF-16 and UTF-32 sidecars are dropped whole.
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
- Sidecars (#1481) are opened `O_NOFOLLOW` and `O_NONBLOCK`, regular files
  only, at most 8 per item and 1 MiB each, track-bound documents first.
  Audiobookshelf's own `metadata.json` and `metadata.abs` are digested and
  never handed to a child. A folder-level document beside another item's audio
  or ebook file, or beside an item whose file list is unknown, is never handed
  to a child either. No archive member is opened.
- The OPF reader is two linear scanners: each tag closes within 4 KiB, names
  within 64 characters, at most 64 matches, and no DTD or entity beyond the
  five XML escapes is resolved. A JSON document is parsed whole within 1 MiB;
  deep nesting or an over-long integer drops it. Every remaining pattern is
  anchored and bounded, and the child's CPU and wall limits contain it.
- A sidecar's title, creators and CUE `FILE` value are association text: each
  at most 255 characters and 16 creators, refused whole on a forbidden code
  point, used only to decide whether the document belongs to the item, and
  never a claim, persisted, logged or query text.
- Refusals are closed codes in the report's sidecar manifest only. No sidecar
  name, path or text reaches an operational event, diagnostic, status or
  support bundle. A sidecar fault drops that document, never the item.
- Currentness re-derives each document's kind, binding and parent-side refusal
  from the listing and the re-read target file list, so an added foreign file
  or an edited refused document makes the report stale.

**Text.** Every value entering a report passes the shared projection grammar:
forbidden code points, private paths and credentials are refused, never
repaired. Comparison never strips diacritics or uses fuzzy distance.
ADR-0096's creator predicates fold dotted initials ("R.F." and "R. F."
agree), only in a name whose dotted tokens are letters and that keeps a token
of two or more letters. Letter count and order count, no NFKC,
confusable mapping or other dot character applies, a fold that would merge two
listed creators is skipped, and the fold never removes an agreement the
unfolded comparison makes. Sidecar belonging compares creators exactly until
#1418 (#1780). The fold is read only on ADR-0096's level path (#1355).
Belonging never reads the level.

Incumbent description HTML is rendered by the one provider renderer, which
drops attributes and refuses event handlers and unbalanced markup; path and
credential checks run on the rendered text, with character references
decoded once, and on the formatted copy with its tags removed. A
description's visible text, never its formatting, is compared and proposed,
and one containing `<` or `>` is never proposed. Only Mechanical Librarian's
own re-serialisation of a closed set of tags without attributes reaches a
write ([ADR-0107](decisions/0107-write-description-formatting.md)). Markup
built to hide a path is an accepted risk.

**Images.** JPEG, PNG and static WebP are decoded only in a constrained
child that also computes the perceptual hash.

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

Owner taste rules reach the builder and the
[judge](ai-boundary.md#taste-judge) only as sealed packet data; edits are
capped at 8 KiB. A judge verdict is admitted only by a reference sent in that
request and rule numbers of its version, so it can pick only among the covers
it was shown. A verdict marks and orders a suggestion, and the owner's answer
is the ordinary cover decision, with its own checks. Once qualified, a verdict
may also choose an automatic write. That write stays inside the eligible set,
under its own identity and record. The owner's choice or hold refuses it when
it is minted, and again when it is written. It adds no egress and no
credential. Try again needs the
owner session and its request token, and is refused unless it names the
profile version in use.

### OpenRouter

Egress is fixed to `https://openrouter.ai` and one exact model, with
`require_parameters: true`. Cover, [taste
profile](ai-boundary.md#taste-profile-builder) and taste judge requests deny
data collection;
description requests set `allow_fallbacks: false`. Upstream data practice is
unverified. Responses past each endpoint's cap are refused as
`response_too_large`, whatever `Content-Length` says. One deadline spans the
key, catalogue and chat requests; a single read can overrun it by its phase
cap (30 seconds by default). Error bodies are read up to 8 KiB to recognise
known refusals and never kept.

### Contained Codex reviewer

Each use re-admits the image-owned or deployment-owned profile bytes; an
invalid override never falls back
([ADR-0081](decisions/0081-image-owned-reviewer-provisioning.md)). Device
sign-in uses a private staging `CODEX_HOME`; cancellation or shutdown kills
the exact process group and proves it dead. A new credential is used only
after the owner selects Codex again.

- The container is non-root, read-only, capability-free and
  no-new-privileges, with PID and memory ceilings; its seccomp profile admits
  only Bubblewrap's namespace setup
  ([deployment](../operations/deployment.md#contained-codex-reviewer)).
- Inside, a second seccomp program checks the architecture and denies
  `unshare`, `setns` and process creation. No procfs is mounted.
- Broker, supervisor and namespace init have 64 MiB address-space ceilings,
  Codex 832 MiB.
- Every optional Codex tool is off (shell, agents, plugins, browser, web
  search, image viewing). `danger-full-access` only skips a
  redundant inner sandbox: the process sees only the sealed binary,
  read-only credential, private job directory, images and one request's
  network.

### Cover observer

Only the cover observer influences decisions, as advisory evidence
([ADR-0049](decisions/0049-owner-confirmed-cover-considerations.md)). It
returns a fixed set of visual properties with explicit unknowns and no
ranking, winner or identity field. The local parser needs exactly one
observation per admitted candidate and checks every binding. The adjudicator
makes no model call and recomputes identity from the retained report, under
the Book matching level the comparison binds.

Each preview is verified against its report digest and re-encoded into a
metadata-free single-frame PNG; admission requires byte equality after
re-decoding. Bytes, taste profile JPEGs (`/images/NN.jpg`) and taste judge
PNGs (`/images/NN.png`) go into sealed memory descriptors, re-verified in the child before Bubblewrap copies them
read-only.

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
transaction and never re-arms automation. A previous value offered as a
suggestion grants no authority: choosing it is an ordinary owner-entered write,
stale if the field changed. Every other decision is kept in one ledger record
of the owner's latest answer per field, covers included, which keeps Automatic
off that field of the work until Resume automatic handling completes after its
scan succeeds, or a withdrawal leaves no earlier answer
([ADR-0085](decisions/0085-owner-entered-values-withdrawal-and-reselection.md),
[ADR-0108](decisions/0108-ledger-and-derived-state.md)).

The server carries an answer to a later case or episode of the same book that
asks the same question, within a release or after a regeneration. The carried
row names its original and grants exactly what it granted, under current
consent and every write guard. It is the one owner decision not made by an
owner request, and the one exception to preparation creating no owner
decision.

Owner-entered text is refused, never rewritten, unless it is 1 to 512 code
points of NFC text with no control, path, credential or URI. No automatic path
reads an owner-entered value. Rollback and undo are unavailable; only a
subtitle, publisher, genres or narrators can be cleared
([ADR-0085](decisions/0085-owner-entered-values-withdrawal-and-reselection.md),
[ADR-0105](decisions/0105-going-back-after-a-write.md)).
An owner may choose a retained cover whose match is unconfirmed, but not
one with a positive contradiction.

Decisions bind their offer's digest. Until its case is checked at the saved
Book matching level, owner effects wait and new decisions refuse
(`offer_changed`). A changed offer gets a valueless `matching_changed` row; its
pending effect, a cover's too, closes stale.

The original an automatic switch replaced is a `retained` cover source, read
locally from the owner-only retained store, never in a reviewer packet,
provider request or support bundle, and never served. Only the owner can
choose it. Put back is an owner cover decision
([ADR-0049](decisions/0049-owner-confirmed-cover-considerations.md)) under
every effect guard; it is refused as stale before commit once the copy is
released, and an original changed in Audiobookshelf first ends the write stale
(#1363). An undecodable original is offered with no preview (#1362).

An owner link names the provider record that is a work's book
([ADR-0104](decisions/0104-owner-identity-links.md)). It is written only by
`record_owner_link` inside the caller's write transaction: the row is its own
audit record, append-only (update and delete abort), keyed by connection,
library and item, and sequenced so one link is current. Writing it marks the
work fresh in the same transaction. Only the owner's choose and withdraw
routes write one, under the checks in [Find this book](#find-this-book). While
a case's report binds another link than the current one, or a link where none
stands now, every owner decision on it, pin, unpin, withdrawal and description
choice is refused `link_changed`, checked inside the decision's own write
transaction, until preparation records a new report. A link grants
no write authority: it never fills an identifier field and never withdraws an
owner decision. Preparation re-reads it each time and re-checks an edition or
recording link against the book's own facts; one that fails admits nothing.

### Automatic decisions

Automatic policy is decision-only
([ADR-0036](decisions/0036-qualified-decision-only-automatic-policy.md),
[ADR-0087](decisions/0087-operation-bound-qualification-authority.md)). Only
qualified field options can decide, each binding the qualification digest
that allowed it; everything else abstains to the owner. Pause and suspension
fail closed to the owner queue; only a CSRF-protected owner action lifts a
suspension.

Policy bodies are closed and capped at 16 KiB; drafts and previews activate
nothing. [Activation](../operations/review-case.md#policy-activation)
is a CSRF-protected mutation bound to the expected active policy and exact
draft, and old proposals then fail as stale.

Automation replaces a populated cover only for a fault the measured cover
rules record, under Current covers, whose settings change stales any switch
planned before it, and a qualification no release ships yet, a separate one
for an unconfirmed current cover
([ADR-0093](decisions/0093-measured-cover-rules-and-current-cover-protection.md),
[ADR-0094](decisions/0094-unconfirmed-current-covers-take-part.md)). Identity
never replaces a cover, and one whose sources name another book is held.
Unreadable means a 404 or decoder-refused bytes, never a transient read.
Replaced bytes are kept, decoded or not, or there is no switch; a 404 is
written as known-empty. Any other automatic cover write
needs a known-empty target. It replaces a populated ebook
description only through
description advice with `description_automatic_application` on. Evidence
never creates series, contributor, title or recording identity on its own;
conflicts go to the owner, never to a write. The explicit flag is set only by
an exact positive. A level admission under Book matching
(`review.work_identity`) reaches owner review and the stored would-admit
note only, and decides nothing automatically (no qualified option, ADR-0093
switch or fill, or ADR-0095 automatic mode) until #1418 (#1355). Evidence
only an owner link or its anchor admitted is shadow-first in the same way: it
reaches owner review and the shadow decision's would-admit note, never an
automatic decision, cover switch, description application or identifier
proposal, until its own rules qualify (#1479).

### Consent and guarded effects

Writes need the owner's library-scoped consent for the exact destination set
shown in Activity; adding a destination lapses it, and policy activation
carries forward only current consent, never wider. A grant must match
a fresh capability probe. A policy or consent change between the browser's
re-read and the grant goes undetected; a capability or library change is
refused.

Every claim rechecks backup readiness, capability, authority, consent,
dependencies, target pre-state, pause and optimistic state in one transaction.
Failures suspend the connection or destination
([suspended destinations](../operations/backups-and-recovery.md#suspended-destinations)),
and a probe never clears `unexpected_effect_behavior`. Read-only keys cannot
write; update-only keys cannot upload covers.

Audiobookshelf has no compare-and-set: the adapter makes one minimal request
after its final pre-read, then reads back and reconciles, so another actor's
change to the same field in that gap can be lost.

- A request with an unknown outcome is never resent. A retry passes every
  guard again; withdrawn authority or consent cancels it without a request,
  and a pre-read showing the desired value verifies without one.
- Paired title and subtitle go in one request; both must verify, and a
  partial result is reconciled, never blindly retried.
- Descriptions are compared as the text Audiobookshelf shows, so another
  actor's markup-only edit is overwritten. Read-back verifies only when the
  stored form shows the desired text; a partially dropped value is never
  resent. A keep that would restore a description and lose formatting
  outside that set, such as a link, is refused when it is recorded, and one
  recorded earlier is refused before any request; both say
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
([data privacy](data-privacy.md)). When Audiobookshelf names no author, an
ebook's own EPUB creators may leave the host as query text: one to four
distinct package creators with no role or an author role. Every enabled ebook
provider in the [dispatch](../reference/configuration.md#dispatch) set receives
them through the shared plan.

### Publication

Report and pointer publication is one transaction that first rechecks target
and media currentness; reports are immutable by trigger. Proposal creation
and review admission recheck the report and pointers inside SQLite and hold
no credential or write capability. External reads stay outside write
transactions. [Preparation](../operations/review-case.md#preparation) creates
no owner decision, plan or write.

### Schema admission

A store fails closed before any mutation on anything
[the clean-state rule](../reference/compatibility.md#the-clean-state-rule)
does not admit. Each accepted gap below needs write access to the volume:

- The application database reads `ledger_meta` first: derived state from
  another build, or an earlier ledger this build migrates, refuses and names
  `regenerate-derived`; no record or any other format needs a clean rebuild.
  First-boot detection passes both refusals through.
- `regenerate-derived` checks an earlier ledger's rows only on a migrated
  scratch copy, so a value only the newer format admits is accepted.
- A read-only open re-evaluates every CHECK constraint, because
  SQLite's read-only integrity check skips them. A CHECK it cannot extract
  refuses. The permission-readiness check misses a CHECK-breaking row.
- Every current row's browse and attention facts are compared with their
  documents at open; any difference refuses
  ([ADR-0084](decisions/0084-persisted-attention-facts.md)). No product code
  writes with `REPLACE`, which would skip delete triggers.
- Ledger rows are checked against ledger tables, and a live write
  plan still needs its derived rows, except a subtitle companion under
  `regenerate-derived`
  ([ADR-0108](decisions/0108-ledger-and-derived-state.md)). While the derived
  rows exist, an owner decision's work, fingerprint, title, authors and target
  field must match its case, an authored description's work, title and authors
  its case, a plan's or no-change row's title, an owner row's authors and an
  automatic no-change row's kept value, its report, a cover choice's copies
  its comparison, and a policy transition's
  size and counts its items. Once a
  regeneration removes them, nothing checks a copy against its source, nor
  any case, proposal, comparison, selection or report a ledger row names; a
  description's chosen form is never checked, and an answer's episode only
  against the answer it replaced. The record of the owner's answers must equal
  its recomputation.

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
- The owner-run `regenerate-derived` command
  ([ADR-0108](decisions/0108-ledger-and-derived-state.md)) is the one tool
  that writes the canonical database. It runs on a stopped volume under the
  binding's lock, never contacts Audiobookshelf or a provider, keeps a
  verified backup before its one transaction, and replaces only derived state,
  apart from migrating an earlier ledger, revoking authority, repointing cover
  pointers and, with
  `--retire-pending`, closing writes not yet sent. Its report names items by
  item id and library, never a title, path, value or credential.
- [Reviewer cost measurement](../development/reviewer-cost-measurement.md)
  is read-only by default; paid runs need explicit confirmation and a
  monitored budget, which an in-flight request may overshoot.

## Automated scanning boundary

Dependency audits, CodeQL and what scanning does not cover are in
[automated scanning](../development/dependencies.md#automated-scanning).
