# Security and threat model

## Authority

The `catalogue` command may make only bounded GET observations of configured ABS
book libraries and publish a local current generation. Its bearer credential is
attached only to the configured origin's library, library-items, and item-detail
GET routes. It has no provider, metadata-write, media-write, report, apply,
background, retry, or cleanup authority.

`metadata-report` may read one configured target item, one selected read-only
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
report reopen, proposal derivation, and reviewer projection reuse the original
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
`metadata-report-v22`; provider, attempt ordinal, delay, elapsed time,
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
and policy/version bindings; it does
not duplicate report excerpts or provider/cover payloads. These documents
contain no credentials, private paths, raw or rejected provider values,
provider bodies, or arbitrary EPUB text. Optional invalid values cross the
adapter boundary only as value-free bounded schema counts. Target and provider
titles pass the same closed public-safe projection before they can enter a
report. Failure output is fixed and value-free.
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

Qualification-only shadow review rechecks one exact current ReviewCase and its
report/ProposalSet bindings inside SQLite before it persists a content-addressed
sanitized packet and attempt. Preview reads only profile bytes and application
state; it constructs no executable, auth, target, provider, reviewer, or other
network client and is not fresh eligibility proof. Request may invoke only the
exact declared Codex/model/reasoning/prompt/profile/packet/output/policy tuple.
ADR-0019 freezes the current tuple at packet-v3/request-v4. Provider publication
citations expose only bounded allowlisted identifier and physical-format fact
objects already admitted to the report; exact reference membership, closed
shape, ordering, and limits are revalidated so opaque-only or malformed
linkage cannot cross the reviewer boundary. Identifier references are
recomputed from public-safe provider/candidate membership and the exact fixed
valid claim projection, including raw order, ISBN checksum, or closed ASIN
syntax. Typed provider claims and rich date evidence both require linkage;
rich evidence citation identities are recomputed from their closed content.
Those internal identities do not by themselves prove that a sibling occurred
in the source candidate. Provider-linked packet/request decode therefore
requires the immutable persisted report and compares each complete citation
with the projection rebuilt from that exact candidate. Missing source context
or any invented/omitted sibling fails closed without exposing the report to
the reviewer.
Shared-month candidates contain only the common `YYYY-MM` precision supported
by independent retained observations. They never choose or pad a day, and the
original finer observations remain cited. This is reviewer input only:
Audiobookshelf partial-date writes and read-back are unqualified and remain
unreachable.
All auth and reviewer processes serialize on one owner-only auth-store lock.
Only the exact canonical ChatGPT subscription status on one process stream is
accepted when the other stream is empty and the command succeeds. Mixed,
additional, malformed, API-key, and nonzero results fail.

The Linux Bubblewrap boundary exposes the pinned runtime and system libraries,
protected auth store, and one empty private job directory, but no repository,
database, profile, media, service configuration, or target/provider
credentials. Auth uses the same descriptor-bound boundary and masks
`/auth/config.toml` with an immutable empty `--perms 0400 --ro-bind-data`
read-only bind for the entire child lifetime, so a same-UID creation or
replacement after preflight cannot alter the authentication invocation. Review
user configuration and rules are ignored, and the isolated ordinary command
uses `--skip-git-repo-check` because `/job` is deliberately empty; shell and unified
execution, web search, apps, agents, hooks, memories, remote plugins, update,
feedback, analytics, history, and approval are disabled by exact configuration.
Resource, time, file, output, and process counts are bounded. The exact
per-case structured-output schema bytes are subset-validated, digest-bound into
the canonical request and untrusted prompt, and raw output failure classes
remain value-free. Malformed UTF-8/JSON, duplicate-key, noncanonical, and
truncated documents are distinct from canonical parsed results that violate
their request semantics. Only the ADR-0017 diagnostic category, closed
component/stage, optional retryability/currentness, and bounded aggregate count
cross the trust boundary.
The pinned ordinary command enables JSONL output. Only exit status 1 with empty
stderr and a bounded, strict-UTF-8 stream ending in the exact terminal
`turn.failed` response-format rejection for the controller-owned
`codex_output_schema` maps to `live_schema_rejection`. The raw message is never
retained. Every variant, other nonzero exit, and unrecognized failure remains
conservatively `spawn_runtime`; malformed result documents and zero-exit
semantic or citation failures remain distinct. Once canonical outer shape and
exact request-ID syntax are proven, a zero-exit result whose request identity
differs from the bound request maps through a typed domain error to
reviewer/identity/identity before citation normalization or membership checks,
without string matching or retaining either identifier. Missing or malformed
identity remains semantic, and noncanonical input remains malformed output.
Exact repository faults map to the persistence component/category/stage rather
than runtime, including database open and inspection failures crossing the
CLI-runner boundary. A dispatched attempt with no terminal outcome reopens
with a bounded reviewer/unknown/execution aggregate, so unknown inspection
never requires the CLI to invent a replacement runtime failure.
Request, attempt,
dispatch, and outcome facts are append-only. Raw streams and reasoning are
discarded. Dispatched ambiguity is `unknown`; there is no automatic retry, and
explicit reevaluation requires proof that the complete prior supervised process
group is absent.

Shadow outcome rows persist one canonical ADR-0017 diagnostic document, not a
legacy code, exception digest, raw stream, path, prompt, packet, process fact,
or malformed value. Offline `shadow-review-inspect-v2` aggregates identical
documents, rejects duplicate/split/out-of-order aggregates, binds the case and
request identities, and exposes no per-attempt or request/result content. CLI
failures use the same object in `shadow-review-failure-v2`; unknown exceptions
map to `unknown` without inspecting their text. Persistence record and replay,
application inspection, and CLI inspection reject any diagnostic that is not
one exact closed reviewer producer shape; a provider-only category/stage cannot
cross by claiming the reviewer component. The legitimate
persistence/persistence/persistence producer remains accepted.

The final report database, `catalogue-v1` database, `config-v5`, artifact
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
