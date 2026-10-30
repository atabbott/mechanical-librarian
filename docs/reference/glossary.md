# Glossary

**Canonical report**
The exact compact, key-sorted UTF-8 JSON document validated by the closed
`metadata-report-v63` model. It carries the report's scope and evidence, the
ordered local-field results, source capabilities, bounded claim occurrences
and, when present, rear-spine provenance.

**Current pointer**
The mutable reference from one connection, library, item and file identity
to its latest immutable report. Moving it never changes or deletes earlier
reports. Reopening the current scope follows the pointer; reopening by exact
report ID returns that named report.

**Exact current schema**
The only database shape the application accepts. Earlier schemas are refused
before any change. Same-schema report replay works; there is no migration,
import or compatibility parser. See [ADR-0013](../architecture/decisions/0013-pre-release-clean-schema.md)
and [compatibility](compatibility.md).

**Local claim**
A source-attributed field fact about the item: EPUB package metadata or
opening matter, Audiobookshelf target metadata, audio tags, sidecars and
paths, or an ASIN carried forward from an earlier admission of the same track
set. It has a stable content-derived reference and a bounded occurrence
ordinal, so duplicate occurrences survive replay. Its field state is one of
`valid`, `absent`, `malformed`, `oversized`, `unsupported`, `untyped` or
`inapplicable`.

**Neutral provider observation**
One provider's own status and complete bounded candidate set. It has no
winner, score, merge or decision meaning.

**Opaque file identity**
A digest of stable Audiobookshelf file facts, scoped to the named target. It
lets currentness be checked after the file list is reordered without storing
a private host path.

**Policy representation**
One closed field-value choice. The Policy page names its admitted source and
value, any bounded transformation, the affected field and when it abstains. A
manual-review-only representation never gains Automatic authority; choosing
one while the field is Automatic changes it to Ask me. For example, Visible
Google text fills an empty Description only from eligible Google Books visible
text, after bounded inert HTML-to-text extraction, and never falls back to
Hardcover.

**Report-only**
Authority to observe and store evidence, never to review, approve, apply or
write target or media metadata.

**Work dispatch lineage**
The base rung and ordered query-only transformations used for one work
request, and whether its single candidate-limit narrowing retry ran. It
records no query text and grants no evidence or write authority.

**Work transformation lineage**
The nullable replay field naming the production that admitted a provider
work identity, and every field it changed: family, local or provider side,
title or authors field, and author ordinal. Only an admission-enabled
production is valid:

- `local_audiobook_unabridged`
- `provider_associated_series_suffix`
- `author_prefix_then_underscores`
- `latin_combining_marks`
- `band_parenthesized_series_name`, `band_order_inverted_series_prefix`,
  `band_initials_fold` and `band_corroborated_title_relation`, which admit
  only inside the runtime band of an audiobook's asserted work lane

Every other transformation is query-only.
