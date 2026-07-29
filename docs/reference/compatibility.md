# Pre-release compatibility policy

The current installed contract is intentionally narrow:

- Python 3.13 or 3.14;
- `config-v5` only;
- an absent database or the installed exact current report database containing
  `metadata-report-v22` plus `proposal-set-v2`, or the separate `catalogue-v1`
  schema;
- `review-case-v1`, `shadow-review-request-v4`, `shadow-review-inspect-v2`,
  `shadow-review-failure-v2`, and the exact-current append-only
  qualification-only shadow request/attempt/dispatch/outcome schema;
- canonical `reviewer-qualification-profile-v1` with exact
  `gpt-5.6-sol`/`medium` and pinned Codex CLI/Bubblewrap identities; shadow
  request execution requires Linux;
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
offline exact ReviewCase reopen, ChatGPT subscription auth, no-egress shadow
preview, one qualification-only foreground request, offline inspect, and
explicit reevaluation. It does not support
durable batch attempts, resume, automatic retry, workers, schedulers, parallel
execution, normal reviewer activation, review decisions, or apply.

Until the first production release, every database, report, artifact,
configuration, and internal-document schema accepts only absent state or its
exact current state. Earlier schemas are rejected fail-closed and never silently
deleted; there is no migration, import, conversion, preservation, or compatibility
parser. A clean rebuild may be required and old state may be retained offline.
Exact-ID and current-scope replay apply only within the exact current schema;
current-schema restart, replay, and crash safety remain mandatory.

This is a pre-release reset and not a deprecation cycle. Compatibility requires
a named present user need. A future public release must document its own
compatibility, migration, and restore promises before users depend on them.
