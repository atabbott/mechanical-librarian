# Security and threat model

## Authority

Qualified automatic policy is decision-only. Its clean-schema built-in profile
may append exact-current immutable dispositions only from the sealed aggregate
F19a manifest; it cannot construct an apply plan or use a target, media, or
provider-write client. Owner pause and durable category/global suspension fail
closed to the authenticated human queue, and recovery is an explicit
CSRF-protected owner action. Private qualification cases, values, and paths are
not stored in the repository or public API.

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
lanes are enabled and admitted. It may append a neutral report and update its
current pointer. It has
no target-write, media-write, review, decision, apply, background, durable retry,
deletion, repair, import, schema-migration, or backward-compatibility authority.
An existing database must match the installed increment's exact current schema
before any writable connection or mutation.

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
counts, timestamps, and fixed failure codes; the authenticated progress response
contains no metadata values, paths, credentials, provider payloads, or raw
errors. A complete target enumeration stages absence candidates, but only a
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
than repeated per-library integrity validation. A claimed stage retains its
already-validated repository connection, without an open transaction, until its
separate durable transition; composed report, proposal, admission, and projection
services borrow that validated connection without owning or closing it. Proposal
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
lock against the other lane. Technical failure in any
configured provider lane stops that work before proposal creation and prevents
that scan's staged retirements. The path cannot decide a field, construct an apply plan, write a
target or media file, or delete media. The supported review server may poll on
the one bounded configured interval, resume transient work, and hold terminal
provider failure for an authenticated owner retry. Durable pause, fair-claim,
and circuit state contains only bounded operational values.

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

Activation is a separate authenticated CSRF-protected owner mutation with the
same 16 KiB pre-parse request bound as policy drafts. It binds
the expected active identity and exact immutable draft, revalidates qualified
canonical per-library resolutions, and atomically retires the current cohort
before changing generation. Old proposals and human submissions then fail
stale. Retry and acknowledgement are owner-only mutations; neither can
reacquire provider data, transfer a decision, authorize Automatic, construct an
apply plan, or access target/media write capability.

The supported network path terminates TLS directly in `review-serve`. Its
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
plaintext mode, TLS-disable flag, ambient proxy-header handling, certificate
issuance, or certificate renewal authority is present. Both the preflight and
Uvicorn load use an explicit non-passphrase rejection value, so neither load
can fall back to OpenSSL's interactive password channel.

The #447 review server adds one private owner-facing HTTP entry point and no
target-service client. An unauthenticated network client can reach only bounded
liveness, the login form/assets, and the fixed login failure envelope. Review
case, evidence, current-case history, and OpenAPI data require an unexpired
opaque session. The password is verified against one owner-only regular
no-follow Argon2id hash file; the database stores only SHA-256 digests of fresh
high-entropy session and CSRF tokens. Expired session rows are removed during
login, logout records revocation, and failed logins use a durable five-attempt
per five-minute global throttle without retaining client addresses.

The server receives a composed application boundary rather than a database or
broad repository. Authentication, currentness, decisions, policy, preparation,
Activity, guarded effects, recovery, and support execute as named application
operations behind one serialization gate; SQLite remains the sole transaction
owner. FastAPI retains only request bounds, exact Host/Origin enforcement,
session/CSRF dependencies, response translation, and lifecycle delegation.

The generated API contract declares that opaque owner session as an API-key
cookie on every protected operation and marks mutation CSRF and idempotency
headers as required. Runtime failures retain separate, value-free meanings:
invalid or expired sessions are unauthorized, CSRF mismatches are forbidden,
absent case identifiers are not found, and repository outages are unavailable.
Logout deletes the cookie only after durable revocation succeeds, so an outage
cannot be presented as a completed logout.

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
authority. Exact canonical HTTPS Host and state-changing Origin checks prevent
cross-origin use; secure HttpOnly SameSite cookies and per-session CSRF protect
browser mutation. Forwarded Host or scheme is rejected unless the direct peer
is explicitly configured, and Uvicorn ambient proxy handling remains disabled.
Every response is private/no-store with a restrictive CSP, frame denial,
no-referrer, no-sniff, and HSTS. Validation and runtime errors use fixed
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
to the fixed HTTPS Hardcover GraphQL URL. Open Library has no secret; its
operational contact appears only in the User-Agent of requests to the fixed
metadata and Covers origins. HTTP clients ignore ambient proxy/environment
configuration and redirects.
The Hardcover secret file contains the bare provider token without an
authorization scheme. Infrastructure adds the required `Bearer` scheme only to
an exact POST to the fixed GraphQL URL.

Disabled providers cause no secret access, client construction, or egress.
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
54 aggregate attempts. The lanes share one client, response-byte aggregate, and
deadline; Hardcover and Open Library also use shared one-second request-start
pacers.

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
`metadata-report-v23`; provider, attempt ordinal, delay, elapsed time,
headers, bodies, and exception text are dropped. Application-created
query-limit outcomes follow the same closed mapping and persist
provider/lookup_construction/lookup without constructing or invoking a
provider. Missing, malformed, or exception-raising optional attempt records map
to a bounded provider/unknown/response aggregate. Provider observations admit
only sorted unique provider-component aggregates whose category and stage are
one supported provider combination.

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
Provider cover admission is limited to four sources per provider and eight
overall. Only descriptors bound to a qualified provider-local work are
admitted. Reports retain safe allowlisted provenance and explicit truncation,
never the remote URL.

## Output and persistence

Successful output and persisted documents contain only bounded target names,
item identity, opaque file identity/digest, current title, fact-local opening
excerpts, source-explicit target-work subtitle/publisher/imprint observations
with closed rear resource/spine/tail/marker provenance, controlled
contributor/production roles, typed date event and precision claims, provider
candidate edition and provider-local work
identities, canonical provider access time when egress occurred, comparison
facts and their closed method, structurally parsed provider name-to-identity
bindings, closed provider evidence kinds with work/edition scope, controlled
contributor roles and occurrence ordinals, and derived ISBN relationships
containing only
claim references and those already-admitted bounded identities. Comparison
normalization is closed, never strips diacritics, never uses fuzzy distance,
and cannot turn aliases, rank, or repeated queries into another source. The
relationship projection does not duplicate observations or excerpts. Report
generation performs no decision and grants no write authority. The separate
ProposalSet contains only bounded alternatives, assessments, stable evidence
references, hashed correlation groups, closed date event/precision context,
policy/version bindings, and hashed source-bound bibliographic combination
relationships; it does
not duplicate report excerpts or provider/cover payloads. These documents
contain no credentials, private paths, unadmitted or rejected provider values,
provider bodies, or arbitrary EPUB text. Optional invalid values cross the
adapter boundary only as value-free bounded schema counts. Target and provider
titles pass the same closed public-safe projection before they can enter a
report. Failure output is fixed and value-free.

Field-specific acceptable outcomes do not expand the trust boundary. Language
comparison uses a closed registered-code and canonical script/region policy.
Publisher acceptability starts only from an already admitted edition-bound
local claim or a provider claim whose exact candidate ISBN anchor has an
`accepted` identifier relation and passes the closed format/work contradiction
checks, plus a bounded statement syntax; target metadata, work-scoped or
malformed values, and associated, unrelated, or unresolved provider facts
cannot grant authority.
Distinct publisher/imprint/brand/group alternatives remain distinct bounded
values with their own citations. The policy stores no inferred corporate
equality, hierarchy, registry result, or fuzzy-match relationship.
Bibliographic combinations likewise grant no new evidence authority. Members
can reference only already-admitted title/subtitle/series alternatives or an
exact fingerprint of the bound target field. Provider grouping includes the
candidate manifestation and work identities. Ambiguous same-scope titles and
explicitly disagreeing edition/work titles cannot re-enter through a
combination. Exact normalized component containment can reject a tuple but can
never split text, create a value, rank a provider, or infer an omnibus or
translation preference.
Production, conversion, scanning, software, retailer, and workflow credits
remain a separate contextual claim kind and therefore cannot cross the
bibliographic contributor or author candidate boundary.
Copyright-holder, printer, distributor, retailer, conversion, advertisement,
preview, and other-work text likewise cannot cross into rear
subtitle/publisher/imprint claims. This rejection is repeated on the value
inside an exact label and uses the complete contextual-credit and promotion
vocabularies, including scanning, production, retail, and workflow cues. Rear
extraction retains the existing fixed resource, byte, text, node, fact,
excerpt, and hostile-markup bounds; source position changes provenance only
and grants neither candidate nor write authority.

Report and current-pointer publication is one SQLite transaction. Immutable
report rows have update/delete triggers. The transaction runs the bounded
target/media currentness precondition before inserting the report or advancing
the pointer. The separate pointer may advance, but predecessor reports remain
unchanged.

Validated cover originals and previews stage before that transaction. Only
report-bound preview digests can be reopened offline; raw originals and remote
locations have no CLI or replay surface. A failed currentness check leaves no
new report or pointer; any unreferenced staged bytes are recoverable cleanup,
not a database repair protocol.

Proposal creation performs one fresh target/media/ABS-cover validation and
verifies every referenced private cover preview without constructing provider
clients. After that external read finishes, an immediate SQLite transaction
rechecks the exact immutable report and current pointer before inserting
the ProposalSet. Drift or malformed input inserts nothing. The unavoidable
interval after the last external read is documented; later review admission
must revalidate freshness. Proposal rows are append-only and have no current
pointer, review state, lifecycle, or mutation authority.

Review admission rechecks persisted ProposalSet/report and durable current
pointers inside SQLite. External target/media/artifact currentness remains an
application preflight. It stores bounded identities and canonical proposal
projections only; no credentials, reviewer identity, decision, or target-write
capability is accepted by the command.

The installed runtime has no model reviewer, reviewer authentication, model-advice
projection, or request/attempt/dispatch/result persistence. The retained
deterministic packet and shadow decision/outcome contracts perform no model or
provider dispatch.

The repository-only contained reviewer accepts only bounded canonical sanitized
JSON/text, one locally admitted output schema, and—only for the closed cover
packet scenario—exact report-bound PNG previews. Its private profile binds exact
artifacts, credential store, limits, and model configuration. The isolated child
has no repository, database, media, service configuration, target/provider
credential, shell, tool, plugin, web, history, approval, or environment authority.
A durable workbench attempt records the bounded authority and input identities
before adapter invocation. The adapter separately withholds child release until
its in-memory callback has observed the exact PID, process group, and start
token; those process facts are deliberately not persisted. Returned JSON is
bounded, duplicate-free, canonical, and locally validated against the bound
schema. Timeout or cancellation kills the captured process group and proves its
identity dead; credential drift or cleanup failure blocks success. Raw streams,
paths, prompts, credentials, and reasoning never cross the boundary. Cover
identity classification is a local pre-review projection. It binds the exact
report and already-admitted packet candidate identities, emits only opaque
references and fixed outcome/reason codes, and gives retained identifier and
edition/work contradictions plus incompatible manifestation publication/format
evidence precedence over verification. Other retained bibliographic comparison
disagreements remain bounded ambiguity evidence and cannot independently prove
a conflicting identity. Only positive retained agreement supports a plausible
match; ambiguous or insufficient compatibility remains unknown. Admission
recomputes exact report-bound candidate reference, provenance, incumbent,
dimension, and variant facts, so digest-equal foreign packets fail closed.
Retained date-evidence comparisons are included, and default provider covers
remain work-level rather than manifestation-level evidence.
It has no image inference, provider dispatch, child process, persistence,
policy, or write authority.

The source-only cover observer is the sole semantic consumer of the admitted
packet images. It has a fixed four-property visual vocabulary with explicit
unknown values and no owner policy, eligibility, ranking, winner, identity, or
write fields. Its instruction states that packet data, provider context,
images, visible text, and OCR-like text are hostile evidence. The local parser
requires exactly one closed observation for every admitted opaque candidate,
rejects invented or repeated references and extra or hostile values, verifies
the complete contained-reviewer execution/request/image/result bindings, and
emits only opaque identities plus exact preview digest/variant facts. Raw model
output is not an observation report until that validation passes.

The source-only cover-policy adjudicator introduces no model or provider call.
It re-admits the exact packet/images, recomputes identity outcomes from the
exact retained source report instead of trusting a caller-authored identity
document, and requires the current classifier, taxonomy, observer instruction
and output-schema identities before using any candidate facts. Its policy is
three canonical booleans, never prompt text.
Conflict candidates cannot be rescued; unknown identity or policy-relevant
unknown observations abstain. Missing, duplicate, invented, mismatched or
hostile references fail before output. The report contains only opaque
acceptable references, one exact admitted recommendation or null, closed
reason codes and content identities. Unavailable observations produce the
fixed `observations_unavailable` abstention. No image, path, URL, free text,
production target, persistence or write authority enters this boundary.

Cover
previews use opaque references, fixed 12-image/1-MiB-each/8-MiB-total limits,
digest/order binding, a separate ephemeral read-only mount, and repeatable exact
image arguments. The generic reviewer entry point is image-free. Its closed
request and adapter method have no image member or image argument. A distinct
cover request is created only by one full `cover-review-packet-v1` parser that
validates all closed fields and invariants, a content-bound packet reference,
preview byte/dimension facts, incumbent/provenance/omission facts and limits,
canonical candidate order, reason-specific omission preconditions, domain
privacy-safe context text, and exact ordered reference/digest equality with the
supplied images. Producer and admission use that same semantic validator. It
checks the complete bounded, canonically ordered PNG chunk structure and
checksums and fully decodes each image before mounting it, admitting only
single-frame RGBA PNG previews no larger than 320 by 480 whose decoded and
declared preview dimensions agree. Its closed profile accepts only exact IHDR,
one consecutive IDAT run containing exactly one completed zlib stream with no
trailing bytes, and final IEND. ICC profiles, PLTE, and every other
ancillary or unknown chunk fail before mount; their bytes cannot cross the
reviewer boundary. Original-cover technical dimensions remain separate ordering
evidence; digest-equal groups bind the canonical best observed dimensions by
area, width, then height, and every represented source kind affects diversity
priority and counts. Forged signatures, corrupt or truncated streams,
invalid chunk state, trailing bytes, unsafe dimensions, and decoder warnings or
failures cross only the value-free response-format boundary.
Before process creation, admitted bytes are copied into anonymous Linux memory
descriptors, rehashed, and sealed against write, growth, shrink, and further
seal changes. An image larger than the profile's child file limit fails before
dispatch. After gated release, the child reopens every sealed descriptor
read-only through its own `/proc`, verifies its identity, seals, size, and digest,
rewinds the independent open-file description, and replaces the inherited
descriptor before limits and exec. Bubblewrap copies from these child-private
cursors through descriptor-data mounts into private read-only sandbox files. A
dispatch callback or another same-UID process can therefore neither mutate the
sealed bytes nor steer Bubblewrap with a shared inherited file offset.
The image-capable adapter independently reconstructs and fully re-admits the
packet and exact ordered image tuple immediately before this path. A directly
constructed private request object therefore has no admission authority.
Duplicate groups bind to a retained representative only after final selection;
fully excluded groups contain only their reason-specific omission evidence.
Only the bounded Instruction is authoritative; canonical input, provider
metadata, images, and OCR text are hostile evidence, not instructions;
arbitrary paths, URLs, originals, remote fetches, target/service identities, and
generic attachments remain unavailable.

Historical sealed-v2 five-preview qualification attempted 17 development and
qualification-holdout packets. Its aggregate-only receipt records 17 typed
`contained_reviewer_invocation_insufficient` failures, zero ordered-reference
mismatches, zero admitted image or reviewer-output bytes, p50 129 ms, p95 160
ms, successful cleanup, and a failed sandbox aggregate.

Diagnosis proved that the historic attempt never reached reviewer execution:
Bubblewrap could not resolve the sealed images through `/proc` paths. The
corrected adapter uses verified child-private descriptor cursors as
descriptor-data mounts; a five-image no-model Linux proof reproduced exact bytes
inside the sandbox while the parent repeatedly sought its inherited descriptors
to EOF.
Authentication-only success had also hidden an under-sized private inference
envelope. A bounded 512 MiB memory and 32-task isolated profile completed a
structured reviewer probe without changing the model or widening prompt,
network, image, output, provider, corpus, or production authority.

The corrected exact implementation **QUALIFIED** one retained real
five-preview development packet with five admitted ordered observations, zero
reference mismatch, 16.263-second end-to-end latency, and successful cleanup.
The seam remains source-only/report-only; this is transport and schema-admission
evidence, not semantic-quality proof or runtime activation.

The subsequent semantic qualification reused retained report-bound artifacts
and the same authority boundary. One development configuration admitted 50/50
candidates with zero failures, retries, or violations; its seeded 20-case repeat
matched every field and the joint result. A fresh one-shot final cohort admitted
48/50 candidates, retaining two post-dispatch, pre-admission runtime failures
without retry. Their exact shared cause was not retained or proven. Successful
exact preflight and the other 48 admissions falsified systemic always-on
authentication, profile, sandbox, binding, or schema failures, while bounded
transient child, resource, or packet-interaction hypotheses remain. The 4%
missingness rate remains within the frozen 5%
tolerance, but its bias magnitude is unknown and conclusions are conditional on
the 48 admitted cases. Per-field lower bounds remain useful or strong when both
missing cases are counted as disagreements; joint agreement falls to 72% and is
not robustly useful across all dispatched cases. Qualification recorded zero
binding or safety violations, provider
calls, production-library accesses, or writes. An earlier final cohort was
invalidated after a broad private JSON discovery scanner opened final-reference
files without exposing values to tested or model-visible output; another was
spent by credential-mount setup. Both supplied no semantic evidence and were
replaced only by fresh disjoint cohorts after diagnosis. No model, prompt,
schema, packaging, taxonomy, threshold, resource, provider, network, corpus,
production, or write authority was widened.

Authentication actions remain adapter-owned and create no product lifecycle. The
kernel is reachable only through the source workbench, excluded from installed
artifacts, and receives only synthetic or irreversibly sanitized inputs.

The final report database, `catalogue-v2` database, `config-v5`, artifact
records, and `batch-manifest-v5` each accept only absent or their exact current
schema. `metadata-report-v1` through `metadata-report-v10`, prior development
schemas, extra objects, altered
SQL, failed foreign-key/integrity checks, and other schemas fail closed before
mutation. Persisted report content is validated when an exact-ID or
current-scope read selects it and when a selected current report or exact-ID
collision participates in publication. Malformed targeted content fails
closed; unrelated historical rows are not a whole-database semantic preflight.
Earlier schemas are not migrated, imported, preserved, or passed through a
compatibility parser. Recovery uses clean current state plus catalogue refresh,
fresh manifest creation, or supported report regeneration as applicable;
same-schema exact-ID and current-scope replay retain their existing transaction
and validation controls.
This report-database boundary is the narrower runtime application of ADR-0013's
repository-wide pre-release clean-schema rule; it does not limit that rule to
database state.

Open Library adds no credential or production-write authority. Its enabled
configuration requires a non-secret contact email, which is held only long
enough to identify fixed-origin metadata and cover requests. It is not included
in application fingerprints, reports, IDs, manifests, persisted artifacts,
logs, or diagnostics. Search and detail responses use existing strict byte,
content-type, encoding, JSON, projection, and aggregate-deadline bounds.
The application admits at most eight distinct ISBN-family lanes and one
complete-title/author lane. Overflow is typed before client construction and
causes no subset egress. One adapter instance owns all lane sequencing, fixed
request maxima, 2 MiB aggregate response-byte ceiling, and the shared
pacer/deadline. Candidate admission accepts
only exact normalized ISBN-family rows with
canonical Open Library edition/work keys. Cover URLs are constructed from
bounded numeric IDs at the fixed Covers origin; malformed present IDs are
retained as `rejected` report outcomes without a request. Valid URLs are paced
with metadata, reject
redirects, and still pass through the existing public-address pinning, bounded
decoder, and private artifact path.

Google Books adds no production-write authority. It is an ordinary,
explicitly referenced provider and remains disabled without client
construction or egress. Enabled access may be credential-free or may use one
owner-only API-key file. Only the credential-bearing controller reads that
file; report, proposal, admission, and reviewer code receives neither
credentials nor network authority. Requests are fixed-origin GETs with
redirects, environment proxies, compression, retry, caching, and arbitrary
endpoints disabled. Per-response, aggregate-byte, row, lane, request, and
deadline bounds fail closed into typed provider outcomes; quota and service
failures are never reclassified as `no_match`.

The deterministic shadow-decision preview adds no network or credential
authority. It opens only an existing regular exact-current report database by
the dedicated read-only/no-create path, verifies query-only behavior, and never
initializes, upgrades, migrates, or persists state. Success and every accepted
failure preserve database bytes, directory entries, and SQLite sidecars. The
domain policy can select only an exact compiler action with closed citations
and combinations; ambiguity and unsupported state abstain to manual. The
canonical output is operator-visible data, not approval or an apply token.

Policy Preview composes that same provider-independent decision policy into the
existing persisted projection stage without adding a network client or
provider call. The owner-session/CSRF commands move through the closed
`review | preview | automatic` posture. Review and Preview report authority
`none`; explicit confirmation revalidates the exact preview before reporting
the qualified `decision_only` authority. Outcome publication revalidates exact
case, report, proposal, target fingerprint, policy generation/resolution,
source decision set, catalogue, action, evidence, and rule identity in one
immediate transaction. Stale work publishes no outcome and reuses the current
pipeline row. Outcome/history reads validate the whole append-only document
before returning anything and cap the requested sample at 50 and the encoded
projection at one MiB. They never construct target or apply clients.
Leave-Preview remains impact-only; the separate confirmation command is
CSRF-protected and fails closed when the preview is unavailable.

Automatic policy intent is accepted only after the authenticated owner enters
Preview, and both draft persistence and activation recheck that stored posture
inside their transactions. Human admission substitutes Ask only for Automatic
categories; it preserves explicit Ask, Ignore, and Unsupported choices. Only
the closed qualified category/option matrix may reach decision-only Automatic;
all other automatic intent is rejected, and no automatic outcome itself grants
effect or target-write authority. The deterministic F19 projection includes
exact `policy_ignored` proposals; the external-review packet remains unchanged.

Guarded field effects add the first persisted mutation boundary. The installed
product composes the API-key Audiobookshelf target only after authenticated,
library-scoped consent for the exact eligible destination subset disclosed in
Activity. The submitted destination matrix must still equal a fresh capability
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
