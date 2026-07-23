# Compatibility policy for the 0.4 and 0.5 checkpoints

The current installed contract is intentionally narrow:

- Python 3.13 or 3.14;
- `config-v2` only;
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

At no point in 0.5 is schema backward compatibility supported.
`metadata-report-v1`, prior 0.5 increment schemas, and all earlier development
schemas are rejected fail-closed before mutation. There is no migration, import,
preservation, or compatibility parser. Recovery is a clean database plus
supported report regeneration; old databases may be retained offline.
Exact-ID and current-scope replay apply only within the exact current schema.

This is a pre-release reset and not a deprecation cycle. A future public release
must document its own compatibility promises before users depend on them.
