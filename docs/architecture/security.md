# Security and threat model

Authorities: the ADRs for every trust-boundary decision,
`docs/reference/review-openapi-v1.json` for the authenticated surface, the
modules named in each section for the checks they perform, and
`.github/workflows/public-gate.yml` with `osv-scanner.toml` for scanning. This
document states the invariants those artifacts must keep.

Terminal preparation failure evidence is value-free. Its versioned `prep-`
reference derives only from durable action identity, stage, attempts, and a
normalized code; authenticated operations and re-redacted support evidence may
carry that same bounded reference, never a path, report, provider payload,
credential, root, item, or exception.

## Authority

Qualified automatic policy is decision-only. Its clean-schema built-in profile
may append exact-current immutable dispositions only from the sealed aggregate
F19a or F20 manifests, or from the separately sealed F38 manifest for
`contributor_names:canonical:provider_consensus_display`. That F38 option
requires the exact provider-consensus display candidate and retains local
provenance; it cannot reconcile contributor identity. No automatic disposition
can construct an apply plan or use a target, media, or provider-write client.
Owner pause and durable category/global suspension fail closed to the
authenticated human queue; an owner decision remains final, and recovery is an
explicit CSRF-protected owner action. Private qualification cases, values, and
paths are not stored in the repository or public API.
An equal automatic candidate is a separate identifier-only terminal fact. Its
atomic currentness comparison happens before, and does not read, authority,
consent, capability, backup, target, or read-back controls.

Automatic cover admission requires an exact-current case, report,
consideration, selected artifact, policy, and owner-final state. A known-empty
target may admit the best confirmed candidate. A populated target must carry a
confirmed incumbent and admits only a candidate with a strictly better complete
rank; missing or inconsistent incumbent evidence abstains. Active acquisition,
unconfirmed identity, stale bindings, and any prior owner cover decision refuse
admission. An admitted cover decision remains decision-only and must still pass
consent, backup, capability, target-currentness, supported-API apply, read-back,
and audit gates.

The `RefreshCurrentCatalogue` application operation may make only bounded GET observations of configured ABS
book libraries and publish a local current generation. Its bearer credential is
attached only to the configured origin's library, library-items, and item-detail
GET routes. It has no provider, metadata-write, media-write, report, apply,
background, retry, or cleanup authority.

The `GenerateMetadataReport` application operation may read one configured target item, one selected read-only
EPUB, owner-only credential files, at most nine fixed credential-bearing
Hardcover metadata queries and 27 aggregate attempts, at most nine
credential-free Open Library Search queries and 49 aggregate Open Library
metadata attempts, at most nine Google Books Volumes queries and 54 aggregate
attempts, and separately credential-free bounded cover
request chains for admitted descriptors. These
metadata maxima apply only when their provider and corresponding discovery
lanes are enabled and admitted. Each ISBN family and each distinct work-query
ladder rung consumes one fixed query; a plan that cannot fit every lane inside
that limit is rejected before provider construction rather than partially
dispatched. Candidate-limit narrowing and transport retries consume the
separate aggregate-attempt allowance. It may append a neutral report and update
its current pointer. It has
no target-write, media-write, review, decision, apply, background, durable retry,
deletion, repair, import, schema-migration, or backward-compatibility authority.
An existing database must match the installed increment's exact current schema
before any writable connection or mutation.

This build has no offline product schema-adoption command. Earlier
exact-predecessor plans are retired because reviewer-access and owner-state
tables change the current application schema. Older runtime state is rejected
before mutation and needs a clean rebuild.

The installed qualification-state bridge is a maintainer test-state boundary,
not upgrade authority. It reads only one exact owner-only retained clone,
rejects sidecars, path replacement, unknown schemas, malformed canonical
evidence, changed target builds, and unverified backups, then publishes a
fresh exact-current output through an owner-only temporary inode and atomic
directory entry. Retained local status and complete cover records are validated
against their claims before selective invalidation can discard dependent state.
Backup and published-output inode identities remain frozen through the final
read-back; close or directory-entry cleanup failures require explicit recovery.
That read-back verifies exact report rows, cover references, current pointers,
report-only packages, and remaining downstream state tables against the exact
freshly seeded baseline. The source is never writable. Current derivation
reconstructs reports;
observations missing mandatory work-dispatch lineage, dependent covers,
artifact-bearing references, and every downstream decision or projection are
invalidated. The bridge accepts no artifact path and has no configuration,
credential, network, provider, reviewer, media, target, import, repair,
arbitrary-SQL, or startup authority.

Provider-evidence replay is a separate maintainer qualification boundary. Its
source-bound capture command reads an exact-current owner-only database and one
canonical owner-selected report list, uses only the selected libraries' current
configured metadata providers, and confines HTTPS requests to each adapter's
fixed provider host with redirects disabled. It records the exact bounded
pre-parser response stream and behavior-relevant headers, but replaces API-key
query values, authorization values, and configured contact identity before any
bundle write. A final secret/contact scan precedes exclusive owner-only sealed
publication. Interruptions, unsafe parents or files, sidecars, incomplete
unclassified streams, unexpected endpoints, output collisions, and bundle
limits fail closed without overwrite or deletion.

Replay reads one digest-bound canonical owner-only
`provider-replay-bundle-v1`, supplies only placeholder credentials and contact,
and injects a transport with no network fallback into the current provider
adapters. The current configuration scopes providers and limits. A changed
provider name, adapter, or canonical request invalidates only that provider row;
unchanged rows run current decoding, parsing, report, proposal, and admission
derivation into an absent exact-current database. Private results bind capture
and replay heads, schemas, bundle and database digests, invalidations, and
derived document digests. Standard output is aggregate and failures are
value-free. Neither command has target, media, reviewer, provider-write,
ordinary-startup, import, repair, migration, unattended-acquisition, or deletion
authority. Captured coverage does not establish complete-library or product
compatibility; #938 remains the complete retained qualification fallback.

Foreground batch manifests are private immutable operator artifacts. Preview and
read bind bounded regular no-follow descriptors under a private parent, validate
strict UTF-8 canonical JSON before authority, and bind execution to the
content-addressed manifest ID. The frozen projection excludes credential values
and paths while binding selected egress, providers, library, and limits. One
loaded configuration and credential snapshot remains authoritative for the
whole run, so later configuration or secret-file edits cannot rebind authority.
Each serial item rechecks catalogue/source authority; catalogue, repository,
provider, and service failures stop safely, while item-local evidence failures
remain value-free deterministic stream records. Interrupts propagate without
resume, retry, worker, or background state.

Authenticated review preparation composes the same bounded catalogue and
metadata-report reads, credential loading, provider egress, isolated parser,
artifact publication, ProposalSet creation, and ReviewCase admission. The
owner's CSRF-protected request is persisted before two fixed worker lanes may
claim work. Duplicate requests coalesce and restart recovery returns a claim to
the queue; report publication and its extraction checkpoint share one database
transaction. A third crashed attempt becomes a bounded failed work rather than
an invalid fourth claim. Operational rows contain only bounded identifiers, stage, state,
counts, timestamps, and fixed failure codes. The same transaction maintains one
owner-facing presentation run for explicitly accepted setup, manual-scan, and
library-add triggers. Its authenticated private, no-store snapshot additionally
contains at most five current book display names and eight recent completion
names; those values never enter stream frames, logs, audit identifiers,
diagnostics, support bundles, or reviewer input. Dismissal requires the exact
current terminal run identity plus session, CSRF, Origin, and installed-build
identity, and cannot hide blocked work.
Settings additions persist their bounded presentation-only intent in the same
writer transaction as the new revision and secrets. Worker admission follows
only after publication and an all-or-nothing intent drain; startup validation
rejects stale target/revision bindings, recovery markers, and noncanonical
completion authority without reflecting corrupted values. A recovery marker is
valid only with its exact-current authoritative work-cycle witness; this is a
semantic consistency boundary, not cryptographic tamper evidence. Earlier
pre-release schema state requires a clean rebuild and is never repaired.
Target TLS, authentication, authorization, and unavailable observations
are connection-scoped durable conditions with only a closed code, state, epoch,
and affected-work count. Owner recovery is CSRF-bound and probes the configured
TLS client with bounded JSON only; it never reflects endpoint or transport
detail. A complete target enumeration stages absence candidates, but only a
fully successful scan may tombstone an absent review lineage or retire a
present lineage that is no longer eligible. Catalogue discovery and shared
provider pacers serialize their mutation of global generation and pacing state
across the two lanes. A stale catalogue baseline is rebuilt progressively per
successfully observed library, isolating unavailable connections; later work
validates its exact item and revision against the latest generation. The
return-to-automatic control retains its durable manual hold until its requested
scan is successfully complete. Release compares the hold's human-decision token,
so a concurrent later manual decision remains held; failed or incomplete scans
cannot make stale automatic authority schedulable. The accepted pending release
is durable before the command succeeds and is recovered from the exact-current
hold document after restart. Active discovery is marked for a successor scan,
and bounded recovery pages rotate so incomplete older requests cannot starve a
later completed release. Shutdown closes claim
admission before the configured deadline applies to both review-preparation and
guarded field-effect workers. A live over-deadline worker remains identified and
causes typed shutdown failure before the shared repository closes.
authenticated activity projection reads all library progress and pause state
from one validated repository snapshot. Only one worker lane evaluates automatic
scan admission, using that aggregate snapshot and a retained next deadline rather
than repeated per-library integrity validation. The server performs the full
schema-object, foreign-key, SQLite-integrity, and derived-field equivalence
admission once for its locked database inode. Derived worker connections prove
that same live filesystem authority and borrow its admission instead of
rescanning the database. Store-managed runtime generations likewise borrow the
binding held by a dedicated process-lifetime control repository; predecessor
drain and close cannot revoke a successor, and final listener shutdown closes
the runtime borrowers before that control owner. A Library or Attention
repository request uses
size-bounded closed envelopes and lineage facts instead of repeatedly evaluating
the same provider, proposal, and decision documents. It also proves that each
case still embeds the exact immutable ProposalSet accepted by admission. Before
returning the DTO, the application performs one complete semantic parse of
every visible row.
Canonical documents remain immutable, and every insert still crosses the
complete domain validator and projects its field facts in the same transaction.

A claimed stage retains its already-validated repository connection, without an open
transaction, until its separate durable transition; composed report, proposal,
admission, and projection services borrow that validated connection without
owning or closing it. Proposal
and admission output publication shares the full-synchronous transaction that
advances its stage and may claim the next globally fair action, so the next
action is durable before execution and crash recovery can requeue it. External
work still occurs outside SQLite write transactions. Provider disposition is a
pure classification of the report already being published and shares that
publication checkpoint; a retry is durably returned to extraction before it runs,
while a successful checkpoint may atomically claim the next globally fair action.
Staged absence and ineligibility transitions
require one final confirmation discovery; failed rediscovery makes queued child
work unclaimable. Final pipeline publication also requires the
target to remain singular. External target, media, cover, and artifact
currentness checks precede the short SQLite write transaction, which retains
its local expected-pointer check; one slow external read cannot hold the writer
lock against the other lane. A complete report retains each executed lane's
bounded terminal status; only an admitted candidate from an eligible usable ISBN
family, with no recovery-eligible identifier sibling, may suppress its planned
discovery sibling. Every other identifier outcome dispatches that sibling, and
technical failure remains typed and inspectable but
cannot erase coherent sibling evidence or prevent proposal creation; only a
failure before a complete report exists terminalizes the work. Public
projection allowlists safe provider identity, lane kind, aggregate bounded
request count, status, reason, and recovery text, never raw diagnostics,
queries, headers, credentials, paths, or payloads. The path cannot decide a
field, construct an apply plan, write a target or media file, or delete media.
The supported review server may poll on
the one bounded configured interval and hold pre-package terminal failure for
an authenticated owner retry. Per-work retry eligibility, durable pause, and
fair-claim state contain only bounded operational values; one work never opens
a library-wide provider barrier.

## External actors

- A configured target or provider may return hostile bytes. Responses pass
  through size bounds, strict decoding, and closed semantic projections before
  reduction to typed facts. One provider cannot erase local or sibling-provider
  evidence.
- A mounted-media owner may provide a hostile EPUB or replace it during an
  invocation. The descriptor-bound isolated child has no credentials, network,
  persistence, matching, or decision authority. It hashes and parses one
  bounded immutable byte snapshot, and the parent validates file content and
  target identity again as the publication transaction's precondition. Explicit
  non-ISBN schemes and URIs retain ownership of their digit sequences, so a
  checksum-valid embedded substring cannot manufacture identifier evidence.
  Series recovery is a closed full-match indicia vocabulary over already bounded
  selected front/rear resources; it has no filename, prose, catalogue, ranking,
  provider, decision, or write authority.
- A mounted-media owner may provide a hostile audiobook, a hostile sidecar
  document, or a hostile file or folder name. No media byte is parsed in
  process: the parent runs `ffprobe` as its own isolated child over a passed
  descriptor, with no network, no inherited `PATH`, an empty-but-minimal
  environment, `cwd="/"`, its own session, and core, file-size, descriptor,
  CPU, address-space, output, and wall-clock bounds. The resource bounds are
  installed by a shim process that then becomes the probe, so nothing runs
  between a fork and an exec; the parent still resolves the binary from a
  closed list, so a missing probe is refused before anything is spawned. Its
  bounded JSON document and each sidecar descriptor are then interpreted inside
  the same descriptor-bound Python child the EPUB capability uses, whose
  process cap stays at one. The interpretation itself is a closed recognition
  grammar — never an XML or container parser — over already bounded text; a
  sidecar that is over-long, unreadable, or not valid UTF-8 is dropped whole
  rather than truncated or repaired into a fragment; an identifier whose shape
  is wrong is retained and reported as malformed rather than silently believed;
  and a sibling listing refuses at its entry bound — 1,024 entries — instead of
  materializing an unbounded directory. Path evidence reads bare name
  components only, never a joined path.
  Embedded cover art is recorded as presence and never acquired, decoded, or
  elected.
  A multi-file item multiplies that per-file exposure, so the item carries its
  own bounds on top of the per-file ones: at most 512 files — counted after the
  service's own excluded records are dropped — each file's opened length under
  the audio ceiling before anything is digested, which bounds the whole item at
  that ceiling times its file count, and a 600-second reading wall enforced
  through the same cancellation the per-file children honour. That wall also
  bounds revalidation, which re-digests every track. Each file still gets its own child, holding no
  descriptor for any other file, and the folder surface — the sidecars beside
  the files and the folder name — is read once for the item and only when
  every track shares one directory, so a file placed in a sibling directory
  cannot lend its neighbours' documents to the item. Exceeding an item bound,
  or composing more
  claims than the report document admits, is one named terminal outcome; a
  single unreadable or hostile file denies the whole item rather than yielding
  the partial evidence set an attacker would be choosing.
- A local actor may replace or corrupt configuration, secret, media, manifest,
  or SQLite
  files. No-follow descriptor binding, side-effect-free validation of existing
  databases, owner-only secret permissions, exact schema validation, canonical
  row decoding, envelope/document agreement, and fail-closed diagnostics bound
  this risk. The current runtime does not repair tampered state.

Ordinary typed in-process collaborators are trusted to honor their interfaces;
arbitrary descriptor, subclass, traceback, or interpreter-control-flow
sabotage is outside the supported threat model.

## Authenticated review surface

The policy notebook is the same owner-only review surface: state and immutable
draft reads require the session, and validate, create, and preview also require
the session CSRF token and canonical Origin/Host. Its bounded 16 KiB mutation
body path accepts closed maps only and returns value-free structured failures.
Every response, including its separately served policy script, is private,
no-store, and covered by the same restrictive CSP. Creating, reading,
validating, or previewing a draft cannot activate a policy, create a successor
or queue item, record a decision, or write metadata.

Rich-field impact preview reads at most 20 exact-current lineage pairs per
library and re-evaluates each immutable report with the existing pure proposal
compiler under the draft's resolved identity. The field compiler accepts only
already admitted typed alternatives and exact retained evidence references;
Google description transformation, accepted ISBN relation, and ebook ASIN
applicability are checked without parsing, conversion, inference, provider
access, or target access. An Automatic rich choice projects only when its exact
field-option identity is in the pinned passing set; every other choice returns
a closed unqualified abstention. The CLI uses the same application path against
an existing database and creates no file or SQLite state.

The F20 rich-metadata qualification candidate is aggregate-only. The runtime
pins its public digest and closed per-option disposition before validation,
decision construction, or presentation. The three `series` options, both
`isbn_form` options, genre fill/add/replace, tag fill/add/replace, safe
plaintext description, and locally corroborated subtitle fill have
decision-only Automatic authority. The subtitle fill projects only a
provider-backed edition value that a valid local packaged subtitle claim
attests exactly; a local claim never corroborates itself, an uncorroborated or
non-provider candidate abstains closed, and an acting title-combination
witness retains the destination. Lists must remain one
complete ordered work-bound source projection, and descriptions must remain
exact safe work-bound text without rewriting or truncation;
`not_qualified` and `insufficient_coverage` cannot be fallback authority,
`tags:off` remains policy-only, and human-only `explicit:unsupported` cannot
construct an effect. Each automatic decision binds the F19a, F20, or F38 digest
that actually qualified its exact policy option; F38 binds only
`contributor_names:canonical:provider_consensus_display`. Effect consent,
target concurrency, backup, apply, and read-back gates remain unchanged.
Previously persisted F20 decisions remain readable only when their historical
digest and option identity match the exact earlier passing set; new decisions
always bind the current candidate, and a historical digest grants no new option.
For series, populated Automatic targets are preserved. Provider-wide unsupported
output with no named membership is not negative evidence. A selected WORK-backed
membership may lack a number; null remains null. Selected malformed or incomplete
evidence, incomparable or conflicting alternatives, and failures from dispatches
that contributed to the elected evidence abstain closed. Unrelated providers,
candidates or query lanes cannot veto selected evidence. A uniquely dominating
complete source bundle retains its exact values, order and provenance; no union
or inferred number is constructed. Election collapses only closed leading-article and trailing
`Series`/`Trilogy` display variants, never fuzzy names or title-specific rules.

Activation is a separate authenticated CSRF-protected owner mutation with the
same 16 KiB pre-parse request bound as policy drafts. It binds
the expected active identity and exact immutable draft, revalidates qualified
canonical per-library resolutions, and atomically retires the current cohort
before changing generation. Old proposals and human submissions then fail
stale. Retry and acknowledgement are owner-only mutations; neither can
reacquire provider data, transfer a decision, authorize Automatic, construct an
apply plan, or access target/media write capability.

Direct HTTPS terminates TLS in `review-serve`. Its
certificate and private-key paths are explicit schema fields, must be supplied
together as bounded owner-controlled regular no-follow files with an owner-only
unencrypted private key, and are opened and validated noninteractively as one
matching identity before the application database or listener is opened. The
key boundary admits only explicit unencrypted PKCS#8, RSA, or EC PEM envelopes,
so encrypted keys—including empty-passphrase encryption—fail before SSL parsing.
The immutable anonymous snapshot descriptors prevent path replacement or in-place
mutation from changing the served identity after validation. The direct H11
edge admits at most 100 connections, limits incomplete events to 16 KiB, and
closes incomplete headers after 10 seconds. TLS 1.2 is confined to ECDHE AEAD
ciphers while TLS 1.3 retains the runtime suites. No environment substitution,
TLS-disable flag, ambient proxy-header handling, certificate
issuance, or certificate renewal authority is present. Both the preflight and
Uvicorn load use an explicit non-passphrase rejection value, so neither load
can fall back to OpenSSL's interactive password channel.

The #447 review server adds one private owner-facing HTTP entry point and no
target-service client. An unauthenticated network client can reach only bounded
liveness, the login form/assets, and the fixed login failure envelope. Review
case, evidence, current-case history, and OpenAPI data require an unexpired
opaque session. Its fixed absolute lifetime is configured from five minutes to
30 days and defaults to 30 days; activity never extends it. The password is
verified against either one owner-only regular no-follow Argon2id hash file or
the exact owner verifier inside the settings store. The dedicated owner-only
`private/review-sessions.sqlite` stores only a domain-separated HMAC-SHA-256
session lookup keyed by that exact encoded verifier, SHA-256 CSRF digests,
expiry/revocation facts, and bounded throttle timestamps. It has its own
connection and lock, so session writes never borrow application-database write
authority. Replacing the verifier therefore rejects earlier sessions before
authorization. Expired session rows are removed during login, logout records
revocation, and failed logins use a durable five-attempt per five-minute global
throttle without retaining client addresses. The store admits only absence or
its exact schema and integrity; incompatible or corrupt state fails closed
without repair. Its clean rebuild deletes only session authority and throttle
history, requiring every browser to sign in again.
Pre-0.18 sessions used an unbound digest, so upgrading to 0.18.0 rejects them
once. This deliberate sign-in boundary avoids a legacy lookup fallback that
could preserve authority after a verifier replacement.

ADR-0058's empty-volume setup state adds no unauthenticated settings mutation.
The first visitor may fetch only a bounded synchronizer nonce. Creating the
owner password requires the exact configured Origin plus the same nonce in a
host-only cookie and request header. One application-database transaction
claims ownership and stores the Argon2id verifier, so concurrent first-owner
attempts have one winner. Ordinary session issuance follows that commit in the
dedicated session database; failure returns unavailable while retaining the
claim, and resubmitting the password retries ordinary login. Every later wizard
read or mutation uses that ordinary session, with CSRF and Origin checks for
mutations. Resume verifies the owner password against the store verifier.
Startup admits only the exact seeded predecessor or
valid current incomplete setup; older incompatible incomplete setup requires a
clean rebuild and arbitrary nonempty, malformed, or store-mode state fails
closed. Kickoff commits authority, permanently closes setup routes, and swaps to
the ordinary runtime on the same listener. An unauthenticated client therefore
cannot stage settings, complete setup, or reach a generic write route.

Connection setup performs one supported, bounded `GET /api/libraries` with TLS
verification on, ambient environment and redirects off, one connection, and
the submitted API key attached only to the admitted Audiobookshelf origin.
Endpoint and key enter the incomplete store in one SQLite transaction only
after that read succeeds. Later library discovery passes the stored key
directly from persistence to the same setup-only infrastructure consumer; no
application or HTTP read returns it. Provider keys follow the same write-only
delta boundary, while the optional Open Library contact email remains a local
non-secret setting used only for Open Library requests.

When the supported response supplies service folder paths, setup retains only
bounded non-secret path evidence for exact comparison with deployment-owned
mount mappings. Each comparison performs one read-only root metadata check for
directory type and reachability; it never scans, creates, writes, repairs, or
reads media. HTTP exposes the service path and a matched, missing, ambiguous,
or unavailable state, never the container path.

Runtime replacement starts every fallible successor component while dormant.
One cutover lock closes predecessor admission, commits authority, publishes the
successor, and opens its local admission latch. Commit failure restores the
predecessor before unlock. An impossible post-commit fault closes both claim
admissions, serves a fixed unavailable target, drains both generations, and
requests outer-server termination rather than serving mismatched authority.

The server receives a composed application boundary rather than a database or
broad repository. Authentication, currentness, decisions, policy, preparation,
guarded effects, recovery, and support execute as named application operations
behind the owner-operation serialization gate. Activity executes as a named
application operation through its dedicated reader connection and reader-local
request gate; SQLite remains the sole transaction owner. FastAPI retains only
request bounds, exact Host/Origin enforcement, session/CSRF dependencies,
response translation, and lifecycle delegation.

The authenticated Settings API shares that boundary. Its closed GET/PATCH
documents are bounded to 32 connections/providers and 128 libraries, responses
are private and `no-store`, and unsafe requests additionally require CSRF,
exact Origin, and the installed build identity. Revision comparison and the
settings/secret update are one SQLite transaction. Secret values are accepted
only as 1–2,048 printable non-whitespace ASCII characters without an
authorization-scheme prefix; they are never returned, logged, diagnosed, put
in URLs or browser caches, or written to public artifacts. Reads disclose only
presence plus four indicator characters. Invalid and persistence failures use
fixed value-free envelopes. A present `/data/config.json` prevents every
covered write and prevents any settings-store read, so an invalid file cannot
switch authority.

The #641 live update stream is one further authenticated read on that same
entry point and adds no authentication mechanism. `GET /api/v1/updates` requires
the same unexpired opaque session, refuses a cross-origin subscribe, and is
served private and `no-store` like every other response. Because it is the one
read whose body keeps arriving after the check that admitted it, the session is
re-checked as the stream runs and a lapsed session ends it rather than being
allowed to keep reading through a connection opened while it was valid; the
stream is also bounded by its own lifetime and closed by the existing
graceful-shutdown path.

Its frames carry invalidation and never state. Each frame's data is a closed
object of exactly a sequence, a topic, and either null or a review case
identity, so there is nowhere in the encoding to place a count, a work record, a
status string, a preparation run, a book display name, a path, a credential, a provider payload, an unfinished proposal,
or book content; the browser discards whole any frame that is not exactly those
three fields. A subscriber is told what changed and refetches the authoritative
answer over the ordinary HTTP surface, which is what keeps every existing
redaction, bound, and authority check on the only path that carries content. A
reader whose pending set outgrows its bound is answered with more invalidation
rather than less -- its identities collapse to whole topics -- which is sound
only because a frame carries nothing that could be lost by collapsing it.
Resume is the browser's own `Last-Event-ID`, and an identity minted by a
previous process is answered with a full resync rather than a position in this
order. A hidden tab does not refetch from frames; on its next foreground
transition it takes one ordinary snapshot read, so HTTP still supplies the
current redacted state and no stale frame can manufacture a transition. An
untouched visible tab defers Library and attention frames and takes only bounded
preparation snapshots; pointer or keyboard input pays that deferred debt with
the same one ordinary snapshot resync, while active input retains immediate
coalesced invalidation.
Activity keeps at most two reader-local pages, stamped inside the same SQLite
snapshot from the five append-only ledgers and the exact owner-visible policy
transition shape. Field-effect rows resolve their optional report through the
immutable originating decision and review case, never through a later current
report pointer; a cache hit serves only after one current stamp comparison. The
dedicated cross-thread Activity reader serializes each complete Activity or
transition request locally, so two requests cannot share or end one another's
read transaction; it remains separate from the guarded background-operation
gate.

The generated API contract declares that opaque owner session as an API-key
cookie on every protected operation and marks mutation CSRF and idempotency
headers as required. Runtime failures retain separate, value-free meanings:
invalid or expired sessions are unauthorized, CSRF mismatches are forbidden,
absent case identifiers are not found, and repository outages are unavailable.
Logout deletes the cookie only after durable revocation succeeds, so an outage
cannot be presented as a completed logout. Creation and deletion retain the
same Path, HttpOnly, SameSite=Strict, and canonical-origin Secure attributes;
the cookie carries only its opaque bearer token.

The #448 library and attention responses join only exact-current report and
case pointers. Pages contain at most 50 rows, cursors are bound to their lens
and search, and the field queue admits only unresolved or explicitly manual
destinations. Sibling identifiers cannot widen a query, and automatic-only
cases expose no mutation control. Each case detail, evidence, bounded
current-case history, Library page with its lens counts and decision summaries,
and Attention page with its field count is read from one authoritative SQLite
snapshot. Concurrent supersession and decision commits therefore appear wholly
before or wholly after a response; a failed snapshot yields only the existing
bounded unavailable response.

The reachable assets are private case metadata and evidence plus session
authority. Exact canonical Host and state-changing Origin checks prevent
cross-origin use; HttpOnly SameSite cookies (Secure for browser HTTPS) and per-session CSRF protect
browser mutation. Forwarded Host or scheme is rejected unless the direct peer
is explicitly configured, and Uvicorn ambient proxy handling remains disabled.
Every response is private/no-store with a restrictive CSP, frame denial,
no-referrer, and no-sniff; browser HTTPS additionally receives HSTS. Validation and runtime errors use fixed
value-free codes, so passwords, tokens, paths, hostile metadata, and internal
exceptions do not enter responses. The one #449 mutation accepts only a
server-presented exact ordinary-field proposal under owner session, CSRF,
Origin, target-fingerprint, case-currentness, and idempotency checks. Its
decision and audit event commit atomically and are append-only. The interface
has no automatic-decision, apply-plan, target client, media-write,
target-write, bulk, or reversal constructor.

The #450 browser activation rotates the stored CSRF digest when an authenticated
detail page is served. The raw replacement token is emitted only in that
private/no-store HTML response, removed from the DOM when the bounded script
starts, and retained only in page memory. It is never placed in a cookie,
`localStorage`, `sessionStorage`, URL, log, trace, or durable artifact. A
rotated-away token fails, and each decision still requires the exact Origin,
session, case, proposal, target fingerprint, destination, action, selected
value, and idempotency key. Browser success follows the typed server
acknowledgement; a failed or ambiguous response neither advances nor displays
success. Response loss or an invalid acknowledgement reloads authoritative
detail/history without resubmitting the mutation. A current-case 409 reloads
that state, while a stale 409 routes to the authoritative Library; neither
re-enables obsolete controls. If another authenticated page rotated the sole
session digest, the client fetches a fresh private/no-store page token into
memory, but the owner must explicitly retry the unchanged decision. An expired
session instead crosses to sign-in and never retries a decision without fresh
authentication.

The activated React workspace preserves this boundary. Generated OpenAPI DTOs
grant no request authority; its handwritten same-origin client keeps CSRF and
the exact build identity only in page memory, renews CSRF once after a private
page-token challenge without resending the mutation, converts a 401 into
document navigation to `/login`, and passes abort signals through critical and
progressive reads. Every unsafe
browser request carries `x-ml-build-identity`; FastAPI rejects a missing or
mismatched value before application authority. Mutations do not retry
automatically or claim optimistic success and reconcile ambiguous outcomes from
authoritative state. The client uses no browser storage, inline script,
external origin, worker, blob, source map, credential, target client, or write
adapter. The packaged loader validates bounded exact paths, sizes, digests, a
content-derived build identity, and the independently emitted expected identity
before serving any authenticated shell; any failure becomes one stable
owner-recovery state with no automatic reload loop.

## Credentials and egress

Secret material is read only in infrastructure and represented nowhere in the
report, catalogue, or manifest. Target authorization is attached only to the
configured origin's exact library, library-items, and current-item GET route
shapes used by the selected operation. Provider authorization is attached only
to the fixed HTTPS Hardcover GraphQL URL. Inventaire and Open Library have no
secret; Open Library's operational contact appears only in the User-Agent of
requests to the fixed metadata and Covers origins. HTTP clients ignore ambient
proxy/environment configuration and redirects.
The Hardcover secret file contains the bare provider token without an
authorization scheme. Infrastructure adds the required `Bearer` scheme only to
an exact POST to the fixed GraphQL URL.

In settings-store mode, the same egress boundary consumes credentials from the
owner-only application database only while composing their named target or
enabled provider clients. Every connection requires a credential; enabled
Hardcover requires one and enabled Google Books may omit one. A disabled
store-managed provider may retain its write-only value inside settings
authority, but that value is not copied into the runtime credential map and no
provider client or egress is constructed from it. The deploy-only review
bootstrap and contained-reviewer profile path are never browser fields. Runtime
activation prepares an isolated candidate before
persistence; failure leaves the authoritative revision and admitting generation
unchanged. After CAS commit, the new generation governs the next admitted unit
while requests already admitted to the predecessor drain against it.

Reviewer access is separate application authority in both settings modes. Its
public document contains only task mapping, exact model, revision, secret
presence, and closed status; the OpenRouter key is a write-only `SecretStr` at
the HTTP boundary and a separate validated database row. Dispatch snapshots the
mapping revision and required secret together in one SQLite statement, releases
the owner-operation gate before network work, and the consideration write verifies
the same revision inside its SQLite transaction. An A-B-A replacement therefore
cannot make old evidence current. Provider response bodies and raw errors never
enter the status grammar, logs, or persisted failure identity.

OpenRouter egress is fixed to `https://openrouter.ai`: key and model capability
checks precede the one exact-model chat-completions request. Redirect and
ambient proxy inheritance are disabled. Every response stream is consumed in
bounded chunks up to its endpoint-specific cap plus one rejection byte,
including when `Content-Length` is absent or false. Cover previews, the sealed
packet, instruction, and strict schema are the only model inputs; no target
credential or write authority crosses this boundary.

Codex setup reads the deployment-owned contained profile only when a Codex
status, setup, selection, or dispatch action requires it. Device flow uses a
private staged `CODEX_HOME`; bounded stdout chunks are parsed through the exact
URL/code grammar. Owner cancellation, expiry, runtime cutover, and shutdown
stop the exact process group with TERM, a bounded wait, KILL when needed, a
death check, thread join, and staging cleanup. Generation teardown does not
delete an already validated installed credential. A replacement sign-in first
deselects an active Codex mapping at a new access revision. Installation and
cancellation serialize at the final commit, and a newly installed credential is
not dispatchable until the owner explicitly selects Codex again.

The outer container retains its non-root user, read-only root, dropped
capabilities, no-new-privileges, PID ceiling, and memory ceiling while its
checked-in seccomp profile admits Bubblewrap's two exact namespace-clone flag
sets and mount-namespace construction. The
profile remains deny-by-default and does not admit an arbitrary clone flag set;
the kernel rejects the mount calls before entry to the admitted user namespace.
The nested PID namespace mounts no procfs, because hardened container runtimes
may reject a new proc mount and binding the outer procfs would expose sibling
processes. A synthetic `/proc/self/exe` symlink resolves only to the sealed
`/runtime/codex` binding required by the pinned CLI.

An immutable architecture-specific seccomp program installed after namespace
construction denies `unshare`, `setns`, process-creating `fork`, `vfork`,
`clone3`, and non-thread `clone` calls while permitting `CLONE_THREAD`.
The nested filesystem binds only the six required device nodes and does not use
Bubblewrap's devpts-producing `--dev` option, so setup needs no second user
namespace.
Authentication, credential recheck, and observation share one activity gate.
Their broker, outer Bubblewrap supervisor, namespace PID 1, and sole Codex
payload have respective 64, 64, 64, and 832 MiB address-space ceilings, exactly
the contained profile's 1 GiB contract.

The Codex observation invocation binds a sealed local model catalogue that has
no apply-patch or code-mode selector, so remote model metadata cannot add those
tools. It disables shell, unified execution, agents,
apps, plugins, browser/computer use, hooks, web search, and every other optional
model tool named by its strict pinned configuration, including view-image,
plan, and owner-input. Because no model-callable
process tool remains, the CLI uses internal `danger-full-access` mode inside
the outer Bubblewrap sandbox rather than attempting a second Bubblewrap layer.
The label describes only the skipped inner CLI sandbox: the process still sees
only the outer sandbox's sealed Codex binary, read-only credential, private job
directory, bounded images, selected system files, and network needed for the
one reviewer request.

Disabled providers cause no client construction or egress. Disabling also
removes their library associations; enabling them again does not recreate those
associations.
Unreferenced providers are never bound. Each of at most eight distinct
checksum-valid ISBN-10/13 families selects one exact ISBN lane; one
title-plus-complete-author work lane is also admitted when that complete input
exists. More than eight ISBN families return `query_limit_exceeded` before
provider client construction and permits no subset egress. With neither an ISBN
lane nor a complete work lane, title-only or incomplete-author input returns
`insufficient_input` without a provider request.
The first actual metadata-provider dispatch captures one bounded canonical UTC
access time from the adapter's injected wall clock. Invalid, non-finite, or
out-of-range clock output fails before egress. Disabled, insufficient-input,
query-overflow, repeated-call, and pre-dispatch deadline or budget outcomes do
not read that clock and retain an explicit absent time. Later lanes, retries,
report reopen, proposal derivation, and deterministic packet projection reuse the original
value; none refreshes it.

Each referenced enabled Hardcover provider makes one fixed GraphQL query per
admitted lane, with at most two bounded retries and 27 aggregate attempts.
Each referenced enabled Open Library provider makes at most one fixed Search
query per lane and at most two edition plus two distinct work-detail queries per
lane. An individual request may retry once, with at most four retries across the
observation and 49 aggregate attempts. Each referenced enabled Google Books
provider makes one fixed Volumes query per lane, with at most five retries and
54 aggregate attempts. Inventaire makes fixed-origin search and entity reads
only, with three attempts per request, 27 attempts per observation, batches of
eight, and an aggregate response ceiling of the smaller of nine configured
response limits and 2 MiB. It sends the fixed
`MechanicalLibrarian/0.19.0 (+https://github.com/atabbott/mechanical-librarian)`
user agent and no credential. The lanes share one client, response-byte
aggregate, and deadline; Google Books, Hardcover, Inventaire, and Open Library
use shared one-second request-start pacers across foreground and recovery work.

Within one provider observation, semantically identical provider lookups are
coalesced before dispatch and later lanes replay the exact immutable result.
The private request key contains provider authority and pinned revision,
operation, canonical provider arguments, and response projection; it excludes
local references and raw query text. Coalescing is not a cache and never adds
request, retry, byte, pacing, or deadline authority.

Retries are provider-local and fixed. Only HTTP 500, 502, 503, and 504 are
transient for Hardcover and Open Library; Google Books retries only 503.
Hardcover 429 retries only when one canonical, bounded `Retry-After` value fits
the remaining deadline, while Open Library never retries 429. A valid bounded
`Retry-After` may govern 503; otherwise fresh truncated exponential backoff and
jitter applies. The transport classifier uses only a narrow HTTPX type and
bounded typed-cause allowlist; it never inspects exception text and rejects TLS,
proxy/configuration, invalid protocol/URL, local stream-state, decoding,
pool/write-timeout, and unknown failures. Every retry remains inside pacing,
deadline, byte, and request-count authority. Attempt records are bounded and
process-local. Provider adapters also count optional parser/schema rejections
at their actual rejection points. The application canonically merges those
counts once with the attempt categories and incomplete-outcome mapping for
`metadata-report-v43`; provider, attempt ordinal, delay, elapsed time,
headers, bodies, and exception text are dropped. Application-created
query-limit outcomes follow the same closed mapping and persist
provider/lookup_construction/lookup without constructing or invoking a
provider. Missing, malformed, or exception-raising optional attempt records map
to a bounded provider/unknown/response aggregate. Provider observations admit
only sorted unique provider-component aggregates whose category and stage are
one supported provider combination.

Work-query shaping never rewrites retained evidence. The canonical report keeps
only a closed dispatch lineage; rendered queries, shaped values, credentials,
and responses are not retained. Every single transformation family stays
query-only and cannot grant candidate or write authority. One closed compound
production, `author_prefix_then_underscores` — the complete first-author prefix,
a separator from the closed set, and a remainder carrying at least one filename
underscore that normalizes to the provider title under the unchanged closed
comparison — grants WORK admission, and the report persists its ordered lineage
of families, production name, and local side. Replay rejects every lineage that
is not exactly that enabled production. ADR-0068 additionally permits the
closed Latin combining-mark family. Its admission lineage records exact changed
local/provider title and author ordinals, while dispatch lineage records only
the actual request; no retained claim is rewritten and compatibility or
transliteration matching remains unavailable. The identity floor and every
translation, adaptation, abridgement, and variant-marker veto are unchanged.
All widening and narrowing requests remain inside the existing deadline,
pacing, byte, dispatch-count, and retry-accounting boundaries; ISBN/ASIN
dispatch is unchanged.

The fixed Hardcover selections stay within three relationship levels. Typed
taxonomy is joined from bounded sibling-root tag rows by exact provider IDs.
Required edition/book identity and their exact join fail closed; malformed or
over-bound optional taxonomy members and links are excluded locally, retain no
partial prefix when the collection is over-cap, and cannot erase independent
claims.
HTTP-success GraphQL `errors` are inspected before `data` and produce no
candidates or cover descriptors. Their bounded structure is validated only to
type the failure; provider messages, paths, extensions, and partial data are not
logged, persisted, or copied into reviewer material.

Cover acquisition remains report-only. The ABS raw-cover route is bracketed by
the final item read and has no credentials, cookies, referrer, or user agent.
Remote Hardcover cover requests are separately credential-free, require public
HTTPS port 443 at every hop, use identity coding, and have bounded manual
redirects, bytes, attempts, and aggregate deadline. Hostile JPEG, PNG, and
static WebP bytes are decoded in a constrained child; reports retain only
validated content digests, dimensions, media type, and preview references.
Open Library cover requests use the same transport and decoder boundary, but
allow no redirect, use constructed fixed-origin numeric-ID URLs, and share the
adapter's one-second request-start pacer. Its bounded two-edition/two-distinct-
work observation can make at most four remote cover requests.
Inventaire cover requests use that same no-redirect transport and decoder
boundary, accept only fixed-origin `/img/entities/` paths with a 40-character
lowercase hexadecimal identity, and share the observation's one-second pacer.
Provider cover admission is limited to four sources per provider and eight
overall. A descriptor is admitted only against a candidate the same provider
observed in the same observation: by a qualified provider-local work identity,
or, when that provider resolved no work and states the unresolved placeholder,
by an edition identity equal to one of that provider's own candidate editions.
Two unresolved placeholders comparing equal never admit anything, and a
descriptor naming no edition holds no identity to match. A refused descriptor
is dropped before any request is constructed, so it produces no egress and no
source row. Reports retain safe allowlisted provenance and explicit truncation,
never the remote URL.

## Output and persistence

Authorities: `docs/reference/review-openapi-v1.json` for every response shape
and field, `src/mechanical_librarian/domain/metadata_report.py`,
`domain/proposal_set.py`, `domain/review_case.py` and `domain/cover_evidence.py`
for what a persisted document may contain,
`adapters/persistence/` for the transactions and schema admission that publish
it, `application/cover_packets.py`, `application/cover_packet_semantics.py`,
`application/cover_policy.py` and `adapters/reviewers/contained_reviewer.py`
for the contained cover boundary, and `adapters/providers/` for provider egress.
Every bound, field list, chunk profile and closed vocabulary named as fixed
below is stated exactly once, in those artifacts. This section states the
invariants they must keep; it does not restate them.

### What may cross the boundary

Successful output and persisted documents contain only bounded target and item
identity, opaque file identity and digest, current title, fact-local opening
excerpts with their closed provenance, controlled contributor and production
roles, typed date event and precision claims, provider candidate and
provider-local work identities, canonical provider access time where egress
occurred, comparison facts with their closed method, structurally parsed
name-to-identity bindings, closed provider evidence kinds with scope and
occurrence ordinal, and derived relationships built only from those
already-admitted identities.

They contain no credential, private path, unadmitted or rejected provider
value, provider response body, or arbitrary EPUB text. An optional invalid
value crosses the adapter boundary only as a value-free bounded schema count.
Target and provider titles pass the same closed public-safe projection before
either can enter a report. Failure output is fixed and value-free.

Comparison normalization is closed. It never strips diacritics, never uses
fuzzy distance, and cannot turn an alias, a rank, or a repeated query into
another source. No policy stores an inferred corporate equality, hierarchy,
registry result, or fuzzy match. A relationship projection duplicates no
observation or excerpt, and a bibliographic combination grants no new evidence
authority: a member may reference only an already-admitted alternative or an
exact fingerprint of the bound target field, and exact normalized containment
may reject a tuple but may never split text, create a value, rank a provider,
or infer a preference. Production, conversion, scanning, software, retailer and
workflow credits are a separate contextual claim kind and cannot cross into the
bibliographic contributor or author boundary; copyright-holder, printer,
distributor, retailer, conversion, advertisement, preview and other-work text
cannot cross into rear subtitle, publisher, or imprint claims. Source position
changes provenance only, and grants neither candidate nor write authority.

Report generation performs no decision and grants no write authority. A
ProposalSet duplicates no report excerpt and no provider or cover payload, and
carries no current pointer, review state, lifecycle, or mutation authority.

### Publication

Report and current-pointer publication is one SQLite transaction. Immutable
report rows carry update and delete triggers. The transaction runs the bounded
target and media currentness precondition before inserting the report or
advancing the pointer; the pointer may advance, but a predecessor report is
never changed.

Validated cover originals and previews stage before that transaction. Only
report-bound preview digests can be reopened offline; raw originals and remote
locations have no CLI or replay surface. A failed currentness check leaves no
new report or pointer, and unreferenced staged bytes are recoverable cleanup,
not a database repair protocol.

Proposal creation performs one fresh target, media and cover validation and
verifies every referenced private preview without constructing a provider
client. An immediate transaction then rechecks the exact immutable report and
current pointer before inserting the ProposalSet; drift or malformed input
inserts nothing. The interval after the last external read is unavoidable and
is why review admission revalidates freshness. Review admission rechecks the
persisted ProposalSet, report and durable pointers inside SQLite, stores only
bounded identities and canonical projections, and accepts no credential,
reviewer identity, decision, or target-write capability.

### Schema admission

The report database, `catalogue-v2` database, `config-v9`, artifact records,
and `batch-manifest-v5` each accept only absent or their exact current schema.
An earlier schema, an extra object, altered SQL, or a failed integrity check
fails closed before mutation, and is never migrated, imported, preserved, or
passed through a compatibility parser. Persisted content is validated when a
read selects it and when a selected report participates in publication;
malformed targeted content fails closed, and unrelated historical rows are not
a whole-database semantic preflight. Recovery uses clean current state plus
catalogue refresh, fresh manifest creation, or supported report regeneration.
This is the narrower runtime application of ADR-0013's repository-wide
pre-release clean-schema rule; it does not limit that rule to database state.

### The contained cover observer

There is no generic model-selected write authority and no generic reviewer
queue in the installed runtime. ADR-0049 and ADR-0050 define the sole installed
exception: one cover-specific contained observer over bounded private
report-cover previews. Its result is advisory evidence only. Deterministic
identity and cover policy decide eligibility, and an explicit owner interaction
is the sole source of cover write authority; a missing, failed, or unqualified
observer preserves the fully supported model-free manual path. Generic kernel
use remains reachable only through the source workbench and receives only
synthetic or irreversibly sanitized inputs.

The isolated child has no repository, database, media, service configuration,
target or provider credential, shell, tool, plugin, web, history, approval, or
environment authority. Its private profile binds the exact artifacts,
credential store, limits and model configuration, and the policy path binds the
exact profile bytes: a substituted profile or a well-shaped invented observer
reference fails closed. A durable consideration records bounded attempt
evidence and input identities before any result can influence advice. The
adapter withholds child release until an in-memory callback has observed the
exact PID, process group and start token, and those process facts are
deliberately not persisted. Timeout or cancellation kills the captured process
group and proves its identity dead; credential drift or cleanup failure blocks
success. Raw streams, paths, prompts, credentials and reasoning never cross the
boundary.

The observer's instruction is the only authoritative input. Packet data,
provider context, images, visible text and OCR-like text are hostile evidence,
not instructions. Its vocabulary is a fixed set of visual properties with
explicit unknown values and no owner-policy, eligibility, ranking, winner,
identity or write field. The local parser requires exactly one closed
observation for every admitted opaque candidate, rejects an invented or
repeated reference and an extra or hostile value, verifies the complete
execution, request, image and result bindings, and emits only opaque identities
plus exact preview digest and variant facts. Raw model output is not an
observation report until that validation passes.

The adjudicator introduces no model or provider call. It re-admits the exact
packet and images, recomputes identity outcomes from the exact retained source
report rather than trusting a caller-authored identity document, and requires
the current classifier, taxonomy, instruction and output-schema identities
before using any candidate fact. Its policy is canonical booleans, never prompt
text. Conflict and unknown-identity candidates cannot be rescued and are
excluded individually; identity abstains only when no eligible candidate
remains, and unavailable observations produce the fixed abstention. Its report
carries only opaque acceptable references, one exact admitted recommendation or
null, and closed reason and content identities. No image, path, URL, free text,
production target, persistence, or write authority enters this boundary.

Cover identity classification is a local pre-review projection. It binds the
exact report and already-admitted candidate identities, emits only opaque
references and fixed outcome and reason codes, gives retained identifier,
edition/work and manifestation contradictions precedence over provider
verification, and has no image inference, provider dispatch, child process,
persistence, policy, or write authority. Plausible is not verified: an
unresolved provider cover is plausible only under the retained-lane, title-
agreement and creator-agreement conditions the module states, and anything else
remains unknown. Admission reopens every bound source preview through the
report reader, verifies its source digest, re-normalizes it deterministically
to the candidate transport identity, and recomputes the report-bound reference,
provenance, incumbent, dimension and variant facts, so a foreign packet fails
closed.

### Image transport

The generic reviewer entry point is image-free: its closed request and adapter
method have no image member or argument. A distinct cover request is created
only by the full packet parser, which validates every closed field and
invariant and requires exact ordered reference and digest equality with the
supplied images; producer and admission use that same semantic validator.

Packet preparation verifies each exact report-bound source digest and bounded
PNG framing, discards every ancillary and profile chunk, decodes one RGBA
frame, and deterministically re-encodes the exact pixels into a metadata-free
derived transport. That encoder is platform-independent and delegates no
serialization or compression decision to Pillow, libpng, or the platform zlib
build; Pillow remains the bounded decoder only. Source reference and digest
bindings stay separate from the derived transport's. Admission checks the
complete bounded, canonically ordered chunk structure and checksums, fully
decodes, re-encodes, and requires byte equality before mounting, and admits
only a single-frame RGBA PNG within the fixed dimension bound whose decoded and
declared dimensions agree. ICC profiles, palettes and every other ancillary or
unknown chunk are absent from the derived transport; those source bytes cannot
cross the reviewer boundary. A forged signature, corrupt or truncated stream,
invalid chunk state, trailing byte, unsafe dimension, or decoder warning
crosses only the value-free response-format boundary.

Before process creation, admitted bytes are copied into anonymous Linux memory
descriptors, rehashed, and sealed against write, growth, shrink, and further
seal changes; an image over the profile's child file limit fails before
dispatch. After gated release the child reopens every sealed descriptor
read-only through its own `/proc`, verifies its identity, seals, size and
digest, rewinds the independent open-file description, and replaces the
inherited descriptor before limits and exec. Bubblewrap copies from those
child-private cursors through descriptor-data mounts into private read-only
sandbox files, so a dispatch callback or another same-UID process can neither
mutate the sealed bytes nor steer Bubblewrap through a shared inherited file
offset. The image-capable adapter independently reconstructs and fully re-admits
the packet and exact ordered image tuple immediately before this path, so a
directly constructed private request object has no admission authority.
Arbitrary paths, URLs, originals, remote fetches, target or service identities,
and generic attachments remain unavailable.

Qualification history for this boundary — the sealed-v2 attempt, its Bubblewrap
diagnosis, the memory-envelope correction, and the semantic and policy
benchmark cohorts and their coverage caveats — is in git history and in the
issues those runs closed. What survives from it is a rule, not a result: every
run retains immutable configuration and aggregate-only receipts, makes no
unseen-holdout claim, and widens no model, prompt, schema, taxonomy, threshold,
resource, provider, network, corpus, production, or write authority.

### Provider egress

A provider adds no production-write authority and no new network authority. A
disabled provider constructs no client and produces no egress. Per-response,
aggregate-byte, row, lane, request and deadline bounds fail closed into typed
provider outcomes, and a quota or service failure is never reclassified as a
no-match. Lane overflow is typed before client construction and causes no
subset egress. One adapter instance owns lane sequencing, request maxima, the
aggregate byte ceiling, and the shared pacer and deadline.

Every provider metadata client is constructed in
`infrastructure/security/credentials.py` with environment and proxy trust off,
client-level redirect following off, TLS verification on, an explicit timeout,
and one connection; arbitrary endpoints and caching are unavailable to all of
them. Remote cover acquisition does not use that client at all: it has its own
address-pinned socket transport, described below. The method and origin
contract differs by provider and is not provider-wide:

- **Open Library** issues fixed-origin `GET` requests only. Its cover requests
  use constructed fixed-origin numeric-ID URLs, allow no redirect, and share the
  adapter's request-start pacer.
- **Google Books** issues fixed-origin `GET` requests only, with compression
  declined.
- **Hardcover** issues one `POST` per request, carrying a fixed GraphQL query to
  one fixed HTTPS URL with identity coding, and is the only provider whose
  metadata traffic is not a `GET`. Its remote cover requests are separately
  credential-free and follow **bounded manual redirects**, each hop revalidated
  against public HTTPS port 443 before use, as recorded under credentials and
  egress above. No redirect is ever followed by an HTTP client automatically;
  the cover transport resolves and re-checks each hop itself.
- **Audible catalog** issues `GET` requests only, to the ten fixed origins of a
  closed marketplace-code-to-host mapping held in the adapter —
  `api.audible.com`, `.co.uk`, `.de`, `.fr`, `.com.au`, `.in`, `.ca`, `.co.jp`,
  `.it`, `.es`. Every URL is built from that mapping, so a configured code that
  is not a key in it is refused at construction and no provider value ever
  becomes part of a host. It is keyless by design: no credential surface exists
  for it in the configuration schema or the client. Compression is declined, one
  observation spends at most three attempts per request, twenty-four dispatches
  in total, the configured per-response ceiling and nine times that ceiling
  capped at 1 MiB in aggregate, under one request timeout and one observation
  deadline, behind the adapter's shared request-start pacer. `Retry-After` is
  honoured on `429` and `503`, bounded to one minute; `401`, `403` and `404` are
  typed as authentication, authorization and no-match, and any other status is
  terminally unavailable rather than a no-match. Responses must be
  `application/json`; a product without a canonical ASIN fails closed, and every
  other field is tolerated as optional. Its cover requests carry no credential,
  follow no redirect, and share the adapter's request-start pacer. The URL is
  the widest product image the product itself stated, admitted only if its host
  is `m.media-amazon.com`: the closed host set is checked where the descriptor
  is built, so a URL naming any other origin becomes a typed `unsafe_remote`
  cover outcome and no socket is opened for it. Acquisition happens only when
  the connection's `cover_enabled` is set.
- **Audnexus** issues `GET` requests only, to the single fixed origin
  `https://api.audnex.us`, on the two paths `/books/{asin}` and
  `/books/{asin}/chapters` built from a canonical ASIN, with the marketplace
  region carried as a query parameter drawn from the same closed code set. It is
  keyless by design. Compression is declined; one observation spends at most
  three attempts per request, twenty dispatches in total, and the configured
  per-response ceiling with nine times that ceiling capped at 1 MiB in
  aggregate, under one request timeout, one observation deadline, and the shared
  request-start pacer. `Retry-After` is honoured on `429` and `503`; a `404`
  carrying the provider's `REGION_UNAVAILABLE` code advances the region walk and
  never becomes a service failure, while `401` and `403` end it. Responses must
  be `application/json`; a book without a canonical ASIN fails closed, and a
  chapter document that cannot be read removes one evidence value rather than
  the candidate. It acquires no cover bytes and constructs no cover source.
- **AudioSilo** issues `GET` requests only, to the single fixed origin
  `https://meta.audiosilo.app` under the fixed base path `/api/v1`. Work and
  recording identifiers are matched against a closed identifier shape before any
  URL is built, so a hostile identifier cannot traverse out of that base path.
  It is keyless by design. Compression is declined; one observation spends at
  most three attempts per request, eighteen dispatches in total — a lookup, a
  work read, and at most one chapter read per identifier — and the configured
  per-response ceiling with nine times that ceiling capped at 1 MiB in
  aggregate, under one request timeout, one observation deadline, and the shared
  request-start pacer. `Retry-After` is honoured on `429` and `503`. The service
  answers an unknown API path with an HTML single-page document rather than a
  structured error: a `404` is classified from its status alone, and an HTML
  `200` fails the strict `application/json` check, so no HTML body is ever read
  as evidence on either path. A lookup that does not name both a work and its
  recording fails closed. It acquires no cover bytes and constructs no cover
  source.
- **iTunes Search** issues `GET` requests only, to the single fixed origin
  `https://itunes.apple.com` on the single fixed path `/search`. The query
  travels entirely in the `term` parameter beside a fixed `media=audiobook` and
  a fixed result limit, so no provider or owner value ever becomes part of a URL
  path or a host. It is keyless by design. Compression is declined; one
  observation spends at most three attempts per request, twelve dispatches in
  total — one work lane walking a ladder of at most three queries — and the
  configured per-response ceiling with nine times that ceiling capped at 1 MiB
  in aggregate, under one request timeout, one observation deadline, and the
  shared request-start pacer. `Retry-After` is honoured on `429` and `503`;
  `401`, `403` and `404` are typed as authentication, authorization and
  no-match, and any other status is terminally unavailable. The store answers
  `text/javascript` rather than `application/json`, so this adapter — and only
  this adapter — admits that one additional media type by name; the admission is
  a closed pair rather than a fallback, and anything outside it is still
  malformed. A result without a collection identifier fails closed. The adapter
  states no narrator and no running time at all, because the store publishes
  neither, so no candidate it builds can satisfy the runtime corroboration gate.
  Its cover requests carry no credential, follow no redirect, and share the
  adapter's request-start pacer. The URL is built from the `artworkUrl100` the
  store stated: when — and only when — that URL ends in the exact segment
  `/100x100bb.jpg`, that one segment is replaced by `/600x600bb.jpg`, asking
  the same origin for the same asset at a legible rendition; any other shape is
  used unmodified, because a URL the adapter cannot read is one it may not
  rewrite. This is Open Library's constructed fixed-origin cover URL in the one
  shape iTunes publishes. The result is admitted only if its host is one of the
  five artwork shards `is1-ssl.mzstatic.com` through `is5-ssl.mzstatic.com`;
  the store balances one result set across that set, so pinning a single
  observed shard would silently drop the art of every result answered by
  another. The closed host set is checked where the descriptor is built, so a
  URL naming any other origin — `is6-ssl`, a shard without `-ssl`, or any
  non-`mzstatic` host — becomes a typed `unsafe_remote` cover outcome and no
  socket is opened for it. Acquisition happens only when the connection's
  `cover_enabled` is set.

Every cover byte, whichever provider supplied it, passes through the same
public-address pinning, bounded decoder child, and private artifact path. The
decoder child also states each image's 64-bit perceptual hash, computed beside
the preview from the bytes it already decoded, so no other process decodes an
untrusted image to obtain one.

The #967 parser expansion retains bounded typed publisher, release date,
language, taxonomy and contributor outcomes from existing audiobook responses.
Malformed or differently attributed credits remain completeness evidence;
they cannot silently disappear and authorize a partial cast. Publisher and
imprint, genre and explicitly typed tag, and unknown-format ISBN remain distinct.
The only added title admission grammar removes a whole suffix explained by the
same candidate's structured series and sequence, with author agreement and
replayable lineage. It grants no broader subtitle or recording authority.
Audiobook cover consideration uses existing bounded descriptors and deterministic
recording evidence with no model observer. Its separately qualified Automatic
fill requires a known-empty target at creation, reopening and effect validation;
it cannot authorize replacement. Existing source, owner-final and concurrency
checks still precede authority and effect.

Open Library's enabled configuration requires a non-secret contact email, held
only long enough to identify its fixed-origin requests; it enters no
fingerprint, report, ID, manifest, persisted artifact, log, or diagnostic.
Google Books may be credential-free or may use one owner-only API-key file;
only the credential-bearing controller reads that file, and report, proposal,
admission and reviewer code receives neither credential nor network authority.

Provider recovery is credential-bearing background egress under those same
limits. The claimant revalidates the exact source report, the full current
review-package tuple, target and media identity, the enabled provider binding,
and the positive configuration generation immediately before constructing the
client. Provider work runs outside SQLite writer transactions. Shared
authentication, authorization, quota, unavailable and incomplete results are
classified before publication by one application-owned rule shared with the
initial report. Exact Google API reasons and exact Hardcover rate-limit header
grammars distinguish bounded burst waits from daily quota waits aligned to
Pacific and UTC midnight respectively. Missing, malformed, contradictory, and
unknown evidence remains the generic conservative case. A deferred burst or
daily-quota result consumes no recovery-health or job execution attempt.
Outbox delivery atomically enrolls each job's
bounded member and health scope before it is claimable; a failed delivery keeps
its row for retry, continues the bounded drain, and returns any already-claimed
preparation successor through the existing typed state machine. Diagnostics retain no credential, raw
header, query, payload, response body, work identifier, or private path.

After a quota-pressure recovery package is exact-current, application code
classifies its durable job from that package and report only. The stored closed
reason names a decided-field opportunity or blanket fallback, never provider
heuristics or provider data. One canonical health owner atomically freezes a
finite cohort of every currently due, eligible and unblocked job for the same
provider connection, adapter and generation. Credential-bearing claims use
strict reason-rank, member and scope order inside it. Later or refreshed jobs
cannot enter that cohort, but remain claimable in the next one. Per-scope
health remains authoritative for eligibility, backoff and Activity outcomes;
only cohort participation shares provider transport failure effects. Target
holds restore the exact owner, cohort and job snapshot. The recovery database
accepts only this exact schema; earlier pre-release recovery state requires a
clean rebuild under ADR-0013.

### Decision authority

The deterministic shadow-decision preview adds no network or credential
authority. It opens only an existing regular exact-current report database
through the dedicated read-only, no-create path, verifies query-only behaviour,
and never initializes, upgrades, migrates or persists state; success and every
accepted failure preserve database bytes, directory entries and SQLite
sidecars. The domain policy may select only an exact compiler action with
closed citations and combinations, and abstains to manual on ambiguity or
unsupported state. Its canonical output is operator-visible data, not approval
or an apply token.

Policy Preview composes that same provider-independent policy into the persisted
projection stage without adding a network client or provider call. The
owner-session and CSRF commands move through the closed
`review | preview | automatic` posture. Review and Preview report authority
`none`; explicit confirmation revalidates the exact preview before reporting
the qualified decision-only authority. Outcome publication revalidates every
identity it depends on — case, report, proposal, target fingerprint, policy
generation and resolution, source decision set, catalogue, action, evidence and
rule — in one immediate transaction; stale work publishes no outcome. Outcome
and history reads validate the whole append-only document before returning
anything, cap the sample and the encoded projection, and construct no target or
apply client. Leave-Preview is impact-only, and the separate confirmation
command is CSRF-protected and fails closed when the preview is unavailable.

Automatic policy intent is accepted only after the authenticated owner enters
Preview, and both draft persistence and activation recheck that stored posture
inside their transactions. Human admission substitutes Ask only for Automatic
categories and preserves explicit Ask, Ignore and Unsupported choices. Only the
closed qualified category and option matrix may reach decision-only Automatic;
all other automatic intent is rejected, and no automatic outcome grants effect
or target-write authority.

### Authenticated per-work diagnostics

The Evidence and History endpoints are private, authenticated, `no-store`
projections with no mutation, retry, recovery, provider-call, or target-write
authority. Evidence keeps the raw reference index inside the application for
Library choice contexts, but its diagnostic response projects only bounded
semantic observations, aggregate missing-lineage truth, and the existing closed
provider outcomes. Missing references produce a partial-lineage limitation;
malformed report or persisted content still fails closed. The browser maps
History to owner language and joins automatic values only by exact durable
decision identity, never by destination or display order. Default diagnostic
reading contains no raw retained fact document or internal case, report, target,
event, decision, consideration, candidate, or reference identifiers.

### Authenticated Activity history

The authenticated Activity endpoint is a private, `no-store`, read-only history
projection. Its bounded event type, date range, page, page size, and search
inputs are validated before a parameterized SQL query selects one page and its
count from the same durable ledger projection. The response has no export,
write, target, provider, or recovery authority. Stored documents are hostile
input: malformed, oversized, or inconsistent rows fail closed rather than
exposing a raw row, private path, or invented verification claim. The projection
contains only bounded readable labels and source-recorded facts. Current waiting
work is independently read from the present field-effect state, so filtering or
pagination cannot make completed historical work appear active. Count and page
are snapshot-consistent only for the individual read transaction; concurrent
append-only activity can change a later request. Focused repository,
application, API, and browser tests cover bounds, redaction, malformed rows,
current-state recovery, and private-cache headers.

Guarded field effects add the first persisted mutation boundary. The installed
product composes the API-key Audiobookshelf target only after authenticated,
library-scoped consent for the exact eligible destination subset disclosed in
Activity. That set is the fifteen book-media destinations, identical on ebooks
and audiobooks (ADR-0071); because the consent envelope identity digests the
whole set, a build that adds one -- narrators, with F47 -- lapses every
existing library consent, and Activity re-offers it before anything is
scheduled. The submitted destination matrix must still equal a fresh capability
probe before its atomic consent replacement is accepted; a changed subset fails
closed and must be reviewed again. A partially consented library remains
available when a newly eligible destination needs incremental consent. Only exact
`HumanDecision(accept)` or sealed
qualified `AutomaticDecision(candidate)` instances project authority; raw
proposals, previews, advice, policy dispositions, and caller-supplied origin
strings cannot. Planning rejects any decision/current-case mismatch in
destination, work, target fingerprint, or target version, and requires a
separate current consent for the exact connection/library/destination/action.
Every claim rechecks backup, capability, authority, consent, dependencies,
target pre-state/version, circuit, pause, and optimistic state. Persisted
authority, consent, pause, and claim state are conditioned atomically in one
repository transaction together with persisted decision/current-lineage and
automatic safety currentness. Capability observations have closed application-
owned outcomes. Authentication, inactive-account, or basic library/item access
failure durably suspends the connection; insufficient destination permission,
an incompatible destination shape, or unexpected effect behavior durably
suspends only that destination. A successful read-only capability observation
may clear only older connection, permission, or field-shape evidence it can
disprove; it cannot clear `unexpected_effect_behavior`. The claim transaction rechecks both scopes, so
a suspension racing the final claim cannot be bypassed. The supported Audiobookshelf adapter has no
target-side CAS: it makes one minimal API-key request only after its final
authoritative pre-read, then read-backs the destination and reconciles any
refusal, ambiguity, drift or disagreement. A same-field update may still be
lost in the interval before Audiobookshelf accepts that request; this boundary
does not claim cross-system atomicity. Cover upload reads only the immutable
digest-verified original artifact, never recovery bytes, and neither bytes nor
credentials enter diagnostics, logs, support bundles or reviewers.
Unknown and stale facts fail closed. Target and credential authority are absent
from domain code and reviewers and credentials stay outside diagnostics and the
API. Continuous composition contains the Audiobookshelf target and API-key
capability observer; qualification proves
the selected item/library and destination permission, never a version alone.
Read-only keys are insufficient for every destination; update-only keys permit
metadata/tags but are insufficient for cover, which also requires upload.
Qualification reads the actual canonical, gap-free append-only event documents
from SQLite and publishes only their aggregate count and digest.
## Guarded-operation diagnostics

Operational status and support routes share the authenticated owner boundary;
support export additionally requires CSRF. Anonymous health exposes only the
fixed liveness/readiness words. Diagnostic producers are allow-listed and
export is re-redacted and size bounded. Secrets, target values, private paths,
databases, environment, media, cover bytes and recovery storage are outside the
diagnostic trust boundary. The private recovery directory and files require the
runtime UID with `0700`/`0600` modes and bounded count/size; failures stop only
the affected cover effect and remain recoverable from durable state.

## Automated scanning boundary

One continuous integration check scans the repository itself rather than the
product's behavior. It is a supply-chain control and is unrelated to the
library scan the product performs over media.

`dependency-audit`, a job of the public gate, scans `uv.lock` with OSV-Scanner
on every pull request and every push to `main`, and fails on any known advisory
affecting the locked closure. `uv.lock` is the complete resolved closure, so
the runtime dependencies and the development group are audited together: CI
installs both, and a compromised test-time dependency executes on the same
machine as a compromised runtime one. The scanner is pinned by image digest
rather than by tag or third-party action, and fetches advisory data from OSV at
run time, so a fixed scanner still sees current advisories. The pin, the exact
arguments, and the reason for that pinning choice are in
`.github/workflows/public-gate.yml`; the same command reproduces the check on a
maintainer's machine. The frontend half of the same supply chain is audited
separately inside `fast-source` by `npm audit --audit-level=low`, with licence
metadata enforced by `npm run licenses`.

The only mechanism that may hold a `dependency-audit` failure open is the
time-boxed advisory acceptance register, `osv-scanner.toml`. Each entry names
one advisory, states why it cannot be fixed now, and carries an explicit expiry
date; there is no severity floor, package exclusion, or repository-wide
loosening, and an advisory that can be fixed is fixed rather than accepted. The
time box is enforced from both sides. OSV-Scanner stops filtering an advisory
once its date has passed, so an expired acceptance fails the audit again, and
`tests/unit/test_security_scanning_contract.py` fails the required
`fast-source` check when an entry is already expired, runs more than 90 days
past the current date, carries no substantive reason, or introduces any table
other than `IgnoredVulns`. An acceptance therefore cannot be open-ended and
cannot rot unnoticed if the advisory is later withdrawn.

No static analysis of this repository's own source runs anywhere, and no check
compares a pull request's dependency graph with its base. Both were designed
against the pinned CodeQL and dependency-review actions and then deferred to
issue #674: each requires GitHub code scanning and the dependency review API,
which a private repository cannot use without GitHub Code Security, and neither
can report a truthful result until the repository is public or covered by that
plan. Nothing dormant, commented, or conditionally skipped was left behind for
them.

Every workflow runs with `contents: read` at the workflow level, grants no
write authority over the repository, pins every action to a commit with its
version comment, and sets `persist-credentials: false` on every checkout.
`tests/unit/test_security_scanning_contract.py` sweeps `.github/workflows` and
fails the gate if a workflow added later breaks any of those.

These remain maintainer-manual and are deliberately outside the scanning
boundary:

- Making the audit merge-blocking. The default-branch ruleset names the checks
  it requires, and `dependency-audit` is not among them: it runs and reports on
  every pull request and every push to `main`, but a red result is advisory
  until a maintainer adds that check name to the ruleset.
- Upgrading the pinned scanner image. Renovate observes manifests, lockfiles,
  Dockerfiles, and action references, not an image named inside a workflow
  command, so the digest is raised deliberately rather than automatically.
- Triaging and fixing what the audit reports. An advisory is not remediated by
  this boundary; each is tracked and fixed as its own work, and accepting one
  through the register is the exception rather than the route.
- Static analysis of this repository's source and pull-request dependency
  review, until issue #674 lands.
- Secret scanning beyond GitHub's own defaults, software bill of materials
  publication, and container image scanning. None of the three is performed
  here.

The application-owned provider return and failure snapshots add no egress or
effect authority. Operational records remain process-local and pass through the
existing bounded diagnostic normalization; raw chained exceptions are never
persisted or exposed. Descriptor locators and pacing callbacks remain confined
to acquisition. Cancellation-compatible carriers preserve shutdown semantics
and the original cause even when client cleanup fails. No provider credentials
or arbitrary response payload fields enter this contract.


## Explicit local transport

[ADR-0072](decisions/0072-explicit-local-transport.md) admits explicit HTTP
Audiobookshelf origins and three browser modes. HTTP carries credentials,
metadata and effect requests in plaintext and is limited operationally to an
owner-controlled local network or VPN. It grants no new effect authority.
Target HTTPS retains strict certificate/hostname verification, no redirects,
no ambient proxies and same-origin closed-route authentication. Provider, cover
and reviewer egress remain under their existing HTTPS contracts.

HTTP listeners require no TLS identity. Direct HTTPS requires the existing
validated paired identity before database/listener creation. Proxied HTTPS
requires no local identity and at least one exact trusted peer IP; only that
actual peer may provide one complete canonical X-Forwarded-Host/Proto pair.
Duplicate Host/forwarding fields, partial pairs, chains and Forwarded are
rejected, with Uvicorn proxy handling disabled. Setup uses the same boundary.
Cookie creation/deletion and HSTS follow the canonical browser scheme, including
body limits and other early failures. All other authentication, CSRF, request
bounds, build checks and shutdown controls are unchanged.


## Authored description advice

F39 adds a closed description-only request to the existing contained and fixed
OpenRouter transports. Provider text is untrusted data, never instructions or
write authority. Projection omits owner decisions, target-only text, titles,
identifiers, file paths and credentials; bounded canonical alternatives, three
cleanup booleans, and pinned instruction/schema are the entire input. The
response is bounded, duplicate-free structured JSON with one description and
one closed disposition. There is no additional semantic classifier or model
confidence authority. OpenRouter uses the exact selected model with
`require_parameters:true` and `allow_fallbacks:false`; upstream data practices
vary and are not verified by the application.

An attempt is durable before transport starts, and transport holds no SQLite
write transaction. Access revision, exact model/profile and request identity
are distinct from case/report/proposal/target/policy/preferences admission
identity. Publication, owner selection, automatic admission and guarded apply
recheck currentness. A passive composition-owned bounded profile read checks
the live contained model/profile without auth, transport or a second database.
A verified changing Description effect retires only its original case binding;
durable application history remains truthful after that binding becomes stale.
Crash recovery interrupts ambiguous running attempts;
only explicit bounded retry can request another call. Immutable candidates and
decisions retain authored/advised provenance through stale and no-change states.
Raw provider errors and streams never enter failure history.

The default-OFF automatic preference does not suppress deterministic fill.
Ready automatic advice still requires current policy, absence of owner-final
choice, consent, backup, concurrency, read-back and audit. Pending competing
authority is revoked transactionally; existing replacement blocking prevents
racing an in-flight effect. Owner reversal uses ordinary field decisions and
manual holds. Same-value selections use the existing no-change terminal path.
Schema creation, exact object inventory, immutable triggers and cross-row
validation enforce the clean-rebuild boundary. See the
[configuration contract](../reference/configuration.md) and
[reviewer-access decision](decisions/0062-owner-managed-reviewer-access.md).

## Independently supported ebook titles

The ISBN-linked title election (#1007) uses only retained report evidence and
selects an existing alternative. Packet-v7 binds the independent-title fact to
that candidate; it cannot grant the same exception to dissenting alternatives.
The reason-specific current F19a option is decision-only, and old manifests
cannot authorize it. The existing title-layout pause/suspension, owner-final,
consent, backup, currentness, read-back and audit boundaries remain unchanged.
No new external call, parser, provider or write capability is introduced.

The #1004 chapter fallback grants owner review only. Failed-set audiobook WORK
lookup candidates retain original bounded evidence separately from admitted
metadata, with exact work and local-reference validation. Only bounded ASIN
acquisition and matched-runtime vetoes consume them; they grant no proposal,
identifier, cover, promotion, automatic or effect authority. Existing provider
limits, isolated probe limits, read-only media and explicit retry stay unchanged.
