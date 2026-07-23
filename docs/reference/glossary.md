# Glossary

**Canonical report**
The exact compact, key-sorted UTF-8 JSON document validated by the closed
`metadata-report-v2` model. It carries the closed ordered local-field results,
source capabilities, bounded claim occurrence, and (when present) rear-spine
provenance as well as the report's scope and evidence.

**Current pointer**
The separate mutable reference from one connection/library/item/file identity
scope to its latest immutable report. It does not mutate or delete predecessors.
`metadata-report-get --current` reopens that scope offline; exact-ID reopening
always returns the named immutable predecessor instead.

**Local claim**
A source-attributed target-title, package-metadata, or opening-matter field
fact with a stable content-derived reference. The v2 field-state vocabulary is
closed: `valid`, `absent`, `malformed`, `oversized`, `unsupported`, `untyped`,
and `inapplicable`.
Its bounded occurrence ordinal preserves duplicate source/DOM occurrences in
canonical v2 replay.

**Narrow v1 widening**
The sole compatibility action: an exact canonical `metadata-report-v1`
two-table/two-trigger database is atomically widened to v2 while retaining its
historical documents and current pointers. It is not a repair, import,
downgrade, retention, or general migration mechanism.

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
