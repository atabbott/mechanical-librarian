# Pre-release compatibility policy

Audiobookshelf field-effect canaries support v2.26.0 and later without an
upper-version promise. Each qualification records the exact latest stable image
version and digest. Compatibility is field-local and demonstrated for metadata/
tags and multipart cover endpoints; a service version alone is not capability
proof.

The current installed contract is intentionally narrow:

- Python 3.13 or 3.14;
- `config-v5` only;
- an absent database or the installed exact current report database containing
  `metadata-report-v22` plus `proposal-set-v4` under
  `fixed-conservative-v6`, or the separate `catalogue-v2` schema;
- `review-case-v2`, `shadow-review-packet-v5`,
  `shadow-decision-set-v1` under
  `deterministic-shadow-decision-policy-v5`,
  and the exact-current deterministic shadow decision/outcome schema;
- `review-session-v1`, `review-case-detail-v1`, `human-decision-v1`,
  `human-decision-event-v1`, `human-decision-ack-v1`, bounded
  evidence/current-case history projections, and the exact-current
  opaque-session, login-throttle, immutable human-decision and decision-event
  tables; older application databases without those exact tables are rejected;
- strict `batch-manifest-v5` for foreground evidence batches;
- Audiobookshelf v2.35.1 library, library-items, and current-item responses for
  explicitly configured book libraries;
- an explicitly selected EPUB on a mapped read-only mount; and
- fixed Hardcover, Open Library, and Google Books metadata operations for
  referenced enabled providers: at most eight exact ISBN-family lanes plus one exact
  title-plus-complete-author work lane, with no partial egress on overflow;
  Hardcover selections use at most three relationship levels and reject
  provider-declared GraphQL errors before considering partial data; safely
  anchored records retain independently valid bounded optional fields and
  members while strict response shape, candidate identity, cardinality and
  required edition/work joins fail closed;
- report-only bounded cover evidence from ABS, an EPUB-declared cover, and
  configured cover-enabled providers: at most eight provider sources overall
  and four per provider, with explicit provider/gallery truncation;
- serial foreground execution for explicit selections or at most 10,000
  all-eligible catalogue entries when Open Library is disabled. With Open
  Library enabled, only an explicit selection of at most five entries is
  supported.

Pre-0.4 and intermediate development databases,
`config-v1`/`config-v2`/`config-v3`,
earlier manifest shapes, retired inventory selectors, pre-0.9 proposal/review state,
preview fixtures, retry/work state, and all retired CLI commands are
unsupported. They are rejected rather than migrated, imported, or interpreted.

The installed runtime supports current catalogue refresh/list/get, immutable
foreground batch preview/inspect/run, single-item metadata report generation,
exact/current report reopen, report-bound cover-preview reopen, exact-current
ProposalSet create, offline exact ProposalSet reopen, review-case admission,
offline exact ReviewCase reopen, authenticated exact-current ReviewCase inspection, and
one individual immutable ordinary-field human decision through the bundled
`review-serve` API and browser round trip in pinned Playwright Chromium.
The HTTP surface consumes a pre-composed application boundary with bounded
top-level view models; this changes no route, DTO, schema discriminator, or
persistence compatibility contract.
It does not support
durable batch attempts, resume, automatic retry, workers, schedulers, parallel
execution, model advice, reviewer activation, automatic decisions, bulk or reversal,
or apply.

The source-only workbench retains one `contained-reviewer` scenario for
sanitized, bounded JSON/text experiments. It is not an installed runtime or
database compatibility contract.

`shadow-decision-preview` supports one exact current ReviewCase from an existing
regular report database on the platform where its read-only/no-create
filesystem contract passes. It performs no network or provider/model dispatch
and persists nothing. Audiobookshelf ebook detail responses may express
`ebookFile.metadata.relPath` relative to the top-level item `relPath` when the
absolute service path proves the same final library-relative file; disagreement,
noncanonical paths, and traversal fail closed.

Audiobookshelf `publishedDate` target reads remain exact ISO calendar days.
Shared `YYYY-MM` proposal candidates are qualification-only and report-only;
partial-date service acceptance and exact read-back have not been qualified,
so month candidates are not write-eligible.

Language and publisher proposal alternatives retain their admitted bytes and
provenance. `acceptable_choice` exposes multiple closed-policy-acceptable
outcomes without choosing one. No earlier proposal policy is interpreted:
pre-release databases using `fixed-conservative-v5` or earlier are rejected
before mutation and require a clean rebuild.

Packet v5 decision summaries are exact deterministic projections of the current
report, ProposalSet, ReviewCase, and bibliographic combinations. The former F11
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
