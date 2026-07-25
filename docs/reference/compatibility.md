# Pre-release compatibility policy

The current installed contract is intentionally narrow:

- Python 3.13 or 3.14;
- `config-v4` only;
- an absent database or the installed exact current `metadata-report-v7` or `catalogue-v1`
  schema, using separate database files for their separate stores;
- strict `batch-manifest-v2` for foreground evidence batches;
- Audiobookshelf v2.35.1 library, library-items, and current-item responses for
  explicitly configured book libraries;
- an explicitly selected EPUB on a mapped read-only mount; and
- fixed Hardcover and Open Library metadata operations for referenced enabled
  providers: exact ISBN-family observation when one usable family exists, or
  one exact title-plus-complete-author work observation when none exists;
- report-only bounded cover evidence from ABS, an EPUB-declared cover, and
  configured cover-enabled providers: at most eight provider sources overall
  and four per provider, with explicit provider/gallery truncation;
- serial foreground execution for explicit selections or at most 10,000
  all-eligible catalogue entries when Open Library is disabled. With Open
  Library enabled, only an explicit selection of at most five entries is
  supported.

Pre-0.4 and intermediate development databases,
`config-v1`/`config-v2`/`config-v3`,
earlier manifest shapes, retired inventory selectors, proposal/review state,
preview fixtures, retry/work state, and all retired CLI commands are
unsupported. They are rejected rather than migrated, imported, or interpreted.

The installed runtime supports current catalogue refresh/list/get, immutable
foreground batch preview/inspect/run, single-item metadata report generation,
exact/current report reopen, and report-bound cover-preview reopen. It does not
support durable batch attempts, resume, automatic retry, workers, schedulers, or
parallel execution.

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
