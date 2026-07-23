# Compatibility policy for the 0.4 checkpoint

The current installed contract is intentionally narrow:

- Python 3.13 or 3.14;
- `config-v2` only;
- an absent database or the exact two-table 0.4 report schema;
- Audiobookshelf current-item responses for explicitly configured book
  libraries;
- an explicitly selected EPUB on a mapped read-only mount; and
- the fixed Hardcover metadata operation for referenced enabled providers.

Pre-0.4 databases, `config-v1`, inventory selectors, proposal/review state,
preview fixtures, retry/work state, and all retired CLI commands are
unsupported. They are rejected rather than migrated, imported, or interpreted.

This is a pre-release reset and not a deprecation cycle. A future public release
must document its own compatibility promises before users depend on them.
