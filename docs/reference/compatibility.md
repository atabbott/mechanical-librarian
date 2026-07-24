# Pre-release compatibility policy

The current installed contract is intentionally narrow:

- Python 3.13 or 3.14;
- `config-v3` only;
- an absent database or the installed increment's exact current report schema;
- Audiobookshelf current-item responses for explicitly configured book
  libraries;
- an explicitly selected EPUB on a mapped read-only mount; and
- the fixed Hardcover metadata operation for referenced enabled providers.
- report-only bounded cover evidence from ABS, an EPUB-declared cover, and up to
  five candidate-bound Hardcover cover descriptors.

Pre-0.4 databases, `config-v1`, inventory selectors, proposal/review state,
preview fixtures, retry/work state, and all retired CLI commands are
unsupported. They are rejected rather than migrated, imported, or interpreted.

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
