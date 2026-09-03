# Pre-release compatibility policy

Audiobookshelf field-effect canaries support v2.26.0 and later without an
upper-version promise. Each qualification records the exact latest stable image
version and digest. Compatibility is field-local and demonstrated for metadata/
tags and multipart cover endpoints; a service version alone is not capability
proof.

The current installed contract is intentionally narrow:

- Python 3.13 or 3.14;
- `config-v8` only;
- an absent database or the installed exact current report database containing
  `metadata-report-v37` plus `proposal-set-v6` under
  `fixed-conservative-v19`, or the separate `catalogue-v2` schema;
- `review-case-v2`, `shadow-review-packet-v6`,
  `shadow-decision-set-v1` under
  `deterministic-shadow-decision-policy-v11`,
  and the exact-current deterministic shadow decision/outcome schema;
- `review-session-v1`, `review-case-detail-v2`, `review-case-evidence-v4`,
  `review-case-history-v2`, `human-decision-v2`,
  `human-decision-event-v1`, `human-decision-ack-v1`, bounded
  evidence/current-case history projections, and the exact-current
  opaque-session, login-throttle, immutable human-decision and decision-event
  tables; older application databases without those exact tables are rejected;
- strict `batch-manifest-v5` for foreground evidence batches;
- Audiobookshelf v2.35.1 library, library-items, and current-item responses for
  explicitly configured book libraries;
- an explicitly selected EPUB on a mapped read-only mount; and
- fixed Hardcover, Inventaire, Open Library, and Google Books metadata operations for
  referenced enabled providers: at most eight exact ISBN-family lanes plus one
  title-plus-complete-author work lane with at most three deterministic widening
  variants and one provider-grammar narrowing retry. The final variant may use
  the canonical ampersand fold followed by the closed Latin combining-mark
  fold, with no partial egress on overflow;
  Hardcover selections use at most three relationship levels and reject
  provider-declared GraphQL errors before considering partial data; safely
  anchored records retain independently valid bounded optional fields and
  members while strict response shape, candidate identity, cardinality and
  required edition/work joins fail closed; all four ebook providers share
  one-second request-start pacing across foreground and recovery work, and
  exact Google Books and Hardcover authority distinguishes burst waits from
  daily reset waits;
- report-only bounded cover evidence from ABS, an EPUB-declared cover, and
  configured cover-enabled providers: at most eight provider sources overall
  and four per provider, with explicit provider/gallery truncation;
- serial foreground execution for explicit selections or at most 10,000
  all-eligible catalogue entries when Open Library is disabled. With Open
  Library enabled, only an explicit selection of at most five entries is
  supported.

Evidence v4 associates each retained observation with its bounded, nonempty
set of current field destinations. One observation can support several fields
without duplicating its transport row; the association is reconstructed only
from retained field provenance and is part of the opaque page-cursor identity.

Pre-0.4 and intermediate development databases,
`config-v1`/`config-v2`/`config-v3`,
earlier manifest shapes, retired inventory selectors, pre-0.9 proposal/review state,
preview fixtures, retry/work state, and all retired CLI commands are
unsupported. They are rejected rather than migrated, imported, or interpreted.

The installed runtime exposes `review-serve`, read-only `policy-preview`,
owner-explicit `cover-review`, and read-only `review-activity`. The cover and
Activity CLI views reuse the same application projections as HTTP/React and add
no write bypass. Its authenticated browser and API inspect exact-current Library, Attention, case, evidence, history, policy,
preparation, operation, and Activity projections; record individual immutable
ordinary-field human decisions and overrides; run persisted continuous review
preparation; admit qualified deterministic decision-only automatic outcomes;
and execute consent-gated exact field-effect plans through supported target APIs
with backup, currentness, verification, audit, pause, revocation, and recovery
controls. Publishing an automatic outcome itself grants neither an effect plan
nor target-write authority. Only a persisted qualified automatic decision may
later enter the same consent, backup, capability, field-currentness,
verification, audit, and reconciliation-guarded field-effect path as an
accepted human decision. The HTTP surface consumes one pre-composed application
boundary with bounded top-level view models.

Catalogue, foreground batch, report, cover-preview, ProposalSet, ReviewCase,
deterministic preview, and generic contained-reviewer operations are source-only typed
workbench scenarios against disposable or explicitly approved private test
state. They are not installed commands or container entrypoints. The installed
cover-consideration service is the sole narrow exception: it may send exact
report-bound PNG previews through the same isolated adapter and persist one
locally admitted closed observation or typed failure as advisory evidence. It
has no production-target bridge or write authority. Deterministic preview
performs no network or provider/model dispatch and persists nothing.

The runtime does not support generic model advice, model-selected writes,
reviewer queues, bulk or reversal effects, destructive operations, media writes,
arbitrary target requests, or a generic plugin/provider interface.
Audiobookshelf ebook detail responses may express `ebookFile.metadata.relPath`
relative to a directory item's top-level `relPath`. Flat `isFile` items instead
repeat their one service-relative file location. Optional item and file absolute
paths must agree with those coordinates whenever supplied; disagreement,
noncanonical paths, and traversal fail closed.

Audiobookshelf `publishedDate` target reads remain exact ISO calendar days.
Shared `YYYY-MM` proposal candidates are qualification-only and report-only;
partial-date service acceptance and exact read-back have not been qualified,
so month candidates are not write-eligible.

Language claims retain their admitted bytes and provenance in the immutable
report. Valid ISO aliases and regional variants group into one
base-and-exact-optional-script alternative; publisher alternatives remain
distinct. `acceptable_choice` exposes multiple closed-policy-acceptable
publisher outcomes without choosing one. No earlier proposal policy is
interpreted: pre-release databases using `fixed-conservative-v18` or earlier
are rejected before mutation and require a clean rebuild.

Packet v6 decision summaries and typed provider evidence are exact deterministic
projections of the current report, ProposalSet, ReviewCase, and bibliographic
combinations. The former F11
request, prompt, qualification-profile, provider-transport, and result contracts
are retired and have no current compatibility surface. Their stored lifecycle
state is not interpreted or migrated. The source-only contained-reviewer
workbench has its own bounded request and result contract and is excluded from
the installed wheel and production container.

The activated React workspace has one exact build surface:
`review-react-assets-v1`, its content-derived build identity, and the
independently packaged expected identity must agree. Missing, extra, stale,
corrupt, source-map-bearing, or earlier browser bundles are not interpreted or
migrated; direct entry receives only the stable owner-rebuild recovery state.
An open page must present the exact identity on every unsafe API request, and a
different or missing identity is rejected before mutation. Generated OpenAPI
DTOs and frontend source are build inputs, not installed compatibility surfaces.

Until the first production release, every database, report, artifact,
configuration, and internal-document schema accepts only absent state or its
exact current state. Earlier schemas are rejected fail-closed and never silently
deleted; there is no migration, import, conversion, preservation, or compatibility
parser. A clean rebuild may be required and old state may be retained offline.
Exact-ID and current-scope replay apply only within the exact current schema;
current-schema restart, replay, and crash safety remain mandatory.

The F45 audiobook-decision increment declares a clean rebuild. It admits only
`proposal-set-v6`, `human-decision-v2`, and a `review_preparation_work` table
whose completion is recorded in an `outcome` column: a prepared item completes
on `case`, `no_case`, or `evidence_only`, and `evidence_only` is no longer a
member of the failure-code vocabulary. Earlier proposal-set, human-decision, or
preparation-work state is rejected without migration or conversion.

The F31 reviewer-access increment declares a clean rebuild. It admits only
`setup-state-v3`, `owner-settings-v3`, `reviewer-access-v1`, and the exact
current application objects; the former offline exact-predecessor plans and
their public command are retired. Reviewer engine/model provenance remains
readable only inside `cover-consideration-v3` and its matching current report
schema.

The guarded-effect plan, authority, consent, recovery-action, cleanup-obligation
tables, control row, and append-only audit events are part
of the exact current application schema. A database without them, with altered
SQL, extra columns/objects, or an earlier experimental effect schema is
rejected without migration or conversion. Operation, plan, nested
authority/consent, action, cleanup, and event documents and their matching
columns are validated canonically before an existing database opens. Scalar
members retain exact JSON types: booleans are never accepted as integer
attempts, sequences, or timestamps. Restart compatibility applies only inside
that exact schema.

Private qualification attempt, retry-pair, scoring, cohort, analysis, and
manifest records are evidence formats, not installed compatibility surfaces.
They are frozen and validated by their owning private programme and cannot be
loaded as application state or used to widen the supported reviewer tuple.

This is a pre-release reset and not a deprecation cycle. Compatibility requires
a named present user need. A future public release must document its own
compatibility, migration, and restore promises before users depend on them.
