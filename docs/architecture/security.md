# Security and threat model

## Authority

The `catalogue` command may make only bounded GET observations of configured ABS
book libraries and publish a local current generation. Its bearer credential is
attached only to the configured origin's library, library-items, and item-detail
GET routes. It has no provider, metadata-write, media-write, report, apply,
background, retry, or cleanup authority.

`metadata-report` may read one configured target item, one selected read-only
EPUB, owner-only credential files, one fixed credential-bearing metadata request
per applicable provider, and separately credential-free bounded cover request
chains for admitted descriptors. It may append a neutral report and update its
current pointer. It has
no target-write, media-write, review, decision, apply, background, retry,
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

Disabled providers cause no secret access, client construction, or egress.
Unreferenced providers are never bound. One usable ISBN family selects only
the exact ISBN operation. With no usable family, the application permits one
title-plus-complete-author operation; title-only, incomplete authors, or
multiple usable families return `insufficient_input` without egress. There is
no automatic retry.

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
excerpts, typed claims, provider candidate edition and provider-local work
identities, comparison facts, and derived ISBN relationships containing only
claim references and those already-admitted bounded identities. The
relationship projection does not duplicate observations or excerpts. Report
generation performs no decision and grants no write authority. The separate
ProposalSet contains only bounded alternatives, assessments, stable evidence
references, hashed correlation groups, and policy/version bindings; it does
not duplicate report excerpts or provider/cover payloads. These documents
contain no credentials, private paths, raw provider bodies, or arbitrary EPUB
text. Target and provider titles pass the same closed public-safe projection
before they can enter a report. Failure output is fixed and value-free.

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
user configuration and rules are ignored; shell and unified
execution, web search, apps, agents, hooks, memories, remote plugins, update,
feedback, analytics, history, and approval are disabled by exact configuration.
Resource, time, file, output, and process counts are bounded. Request, attempt,
dispatch, and outcome facts are append-only. Raw streams and reasoning are
discarded. Dispatched ambiguity is `unknown`; there is no automatic retry, and
explicit reevaluation requires proof that the complete prior supervised process
group is absent.

The final report database, `catalogue-v1` database, `config-v4`, artifact
records, and `batch-manifest-v2` each accept only absent or their exact current
schema. `metadata-report-v1` through `metadata-report-v7`, prior development schemas, extra objects, altered
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
Candidate admission accepts only exact normalized ISBN-family rows with
canonical Open Library edition/work keys. Cover URLs are constructed from
bounded numeric IDs at the fixed Covers origin; malformed present IDs are
retained as `rejected` report outcomes without a request. Valid URLs are paced
with metadata, reject
redirects, and still pass through the existing public-address pinning, bounded
decoder, and private artifact path.
