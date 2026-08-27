# Glossary

**Canonical report**
The exact compact, key-sorted UTF-8 JSON document validated by the closed
`metadata-report-v28` model. It carries the closed ordered local-field results,
source capabilities, bounded claim occurrence, and (when present) rear-spine
provenance as well as the report's scope and evidence.

**Work dispatch lineage**
The closed base rung and ordered query-only transformations used for a work
request, plus whether its single candidate-limit narrowing retry occurred. It
records no query text and grants no evidence or write authority.

**Work transformation lineage**
The nullable v28 replay field reserved for a future evidence-qualified
decoration family and local or provider side. The 0.18.0 admitted subset is
empty, so every non-null value is invalid.

**Current pointer**
The separate mutable reference from one connection/library/item/file identity
scope to its latest immutable report. It does not mutate or delete predecessors.
The application repository and source-only contributor workbench can reopen the
current scope offline; exact-ID reopening always returns the named immutable
predecessor instead.

**Local claim**
A source-attributed target-title, package-metadata, or opening-matter field
fact with a stable content-derived reference. The current field-state vocabulary is
closed: `valid`, `absent`, `malformed`, `oversized`, `unsupported`, `untyped`,
and `inapplicable`.
Its bounded occurrence ordinal preserves duplicate source/DOM occurrences in
canonical replay.

**Exact current schema**
The sole database shape recognized by the current pre-release application after
initialization. Prior and intermediate schemas are rejected before mutation;
same-schema report replay remains supported, but no backward migration, import,
preservation, or compatibility parser exists.

This metadata-report definition is one application of repository-wide ADR-0013,
not the full limit of the pre-release clean-schema rule.

**Neutral provider observation**
One provider's independent status and complete bounded candidate set. It has no
winner, score, merge, or decision meaning.

**Opaque file identity**
An adapter-derived digest of stable Audiobookshelf file facts. It is scoped by
the named target and allows final currentness after file-list reorder without
persisting a private host path.

**Report-only**
Authority to observe and persist evidence, never to review, approve, apply, or
write target/media metadata.

**Policy representation**
One closed field-value choice. The Policy page names its admitted source/value,
any bounded transformation, the affected field, and when it abstains. A
manual-review-only representation never gains Automatic authority; selecting
one while Automatic is active visibly changes the field to Ask me. In
particular, Visible Google text admits only eligible Google Books visible text
after bounded inert HTML-to-text extraction, fills an empty Description, and
abstains rather than falling back to Hardcover.
