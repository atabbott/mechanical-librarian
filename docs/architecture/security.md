# Security and threat model

## Authority

`metadata-report` may read one configured target item, one selected read-only
EPUB, owner-only credential files, and one fixed request per applicable
provider. It may append a neutral report and update its current pointer. It has
no target-write, media-write, review, decision, apply, background, retry,
deletion, repair, import, schema-migration, or backward-compatibility authority.
An existing database must match the installed increment's exact current schema
before any writable connection or mutation.

## External actors

- A configured target or provider may return hostile bytes. Responses pass
  through size bounds, strict decoding, and closed semantic projections before
  reduction to typed facts. One provider cannot erase local or sibling-provider
  evidence.
- A mounted-media owner may provide a hostile EPUB or replace it during an
  invocation. The descriptor-bound isolated child has no credentials, network,
  persistence, matching, or decision authority. It hashes and parses one
  bounded immutable byte snapshot, and the parent validates file content and
  target identity again as the publication transaction's precondition.
- A local actor may replace or corrupt configuration, secret, media, or SQLite
  files. No-follow descriptor binding, side-effect-free validation of existing
  databases, owner-only secret permissions, exact schema validation, canonical
  row decoding, envelope/document agreement, and fail-closed diagnostics bound
  this risk. 0.4 does not repair tampered state.

Ordinary typed in-process collaborators are trusted to honor their interfaces;
arbitrary descriptor, subclass, traceback, or interpreter-control-flow
sabotage is outside the supported threat model.

## Credentials and egress

Secret material is read only in infrastructure and represented nowhere in the
report. Target authorization is attached only to GET current-item requests at
the configured origin. Provider authorization is attached only to the fixed
HTTPS Hardcover GraphQL URL. HTTP clients ignore ambient proxy/environment
configuration and redirects.

Disabled providers cause no secret access, client construction, or egress.
Unreferenced providers are never bound. A provider with no usable ISBN input
returns `insufficient_input` without egress. There is no automatic retry.

Cover acquisition remains report-only. The ABS raw-cover route is bracketed by
the final item read and has no credentials, cookies, referrer, or user agent.
Remote Hardcover cover requests are separately credential-free, require public
HTTPS port 443 at every hop, use identity coding, and have bounded manual
redirects, bytes, attempts, and aggregate deadline. Hostile JPEG, PNG, and
static WebP bytes are decoded in a constrained child; reports retain only
validated content digests, dimensions, media type, and preview references.

## Output and persistence

Successful output and persisted documents contain only bounded target names,
item identity, opaque file identity/digest, current title, fact-local opening
excerpts, typed claims, provider candidate fields, and comparison facts. They
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

Every 0.5 increment accepts only an absent database or its own exact current
schema. `metadata-report-v1`, prior 0.5 increment schemas, extra objects,
altered SQL, failed foreign-key/integrity checks, and all other schemas fail
closed before mutation. Persisted report content is validated when an exact-ID
or current-scope read selects it and when a selected current report or exact-ID
collision participates in publication. Malformed targeted content fails closed;
unrelated historical rows are not a whole-database semantic preflight. Earlier
schemas are not migrated, imported, preserved, or passed through a compatibility
parser. Recovery uses a clean database and supported report regeneration;
same-schema exact-ID and current-scope replay retain their existing transaction
and validation controls.
