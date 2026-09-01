# Current book catalogue

The `RefreshCurrentCatalogue` application operation creates one bounded,
read-only observation across configured Audiobookshelf book libraries. It is
reached by authenticated review preparation in the supported runtime. The
operation accepts only `config-v8`; it reads no provider credential
and makes no target or media write.

The catalogue stores no more than 10,000 entries. A current generation is
published by one SQLite transaction after summary/detail `updatedAt` validation.
The preceding generation remains readable until that switch. A second refresh
against the same catalogue database returns busy; it does not wait or retry.
Later service drift is outside the generation's bounded observation window.

The catalogue inspector emits deterministic JSON rows with `eligible`,
`ineligible`, `ebook`, or `audio` facets and supports exact scoped lookup. List
output never contains a path. Exact lookup can include the service-relative media path; operation
errors remain value-free and logs must redact titles, authors, identities, paths,
and credentials.

Each entry retains a canonical fingerprint of the bounded Audiobookshelf values
that can affect review evidence or currentness: stable item and library
identity, reviewed metadata and cover identity, file identity, media presence,
and media revision. Volatile playback, bookmark, and progress fields are
excluded. When Audiobookshelf supplies a stable EPUB revision, generic item
activity timestamps do not invalidate completed work. When that material
revision is absent, or a cover has no independent revision, the item timestamp
is retained as a conservative freshness fallback so changed bytes cannot reuse
stale evidence.

Catalogue discovery and current-item validation share one adapter-owned
normalizer for the service-relative EPUB location and opaque file identity.
Nested item directories, flat records, and path-only records therefore bind the
same canonical inputs; disagreement or traversal remains ineligible or fails
before any write gate. This adapter parsing remains API-only and does not grant
media access or target-write authority.

The database retains only the exact-current `catalogue-v2` schema. A different or
tampered schema fails closed and requires an operator-created clean database.
Configuration changes produce `catalogue requires refresh`; they never silently
adapt a current generation.

When normal review preparation observes a target TLS, authentication,
authorization, or availability failure, it records a value-free condition for
that configured connection and pauses only its queued work. Existing completed
review packages remain visible. An authenticated owner uses **Check connection
again** after correcting the condition. The probe uses the configured TLS client,
requires a bounded JSON Audiobookshelf response, and exposes neither endpoint,
credential, path, nor raw transport diagnostic. A restart turns an interrupted
check back into the paused owner-actionable state; it never treats an abandoned
check as recovery.

The current generation can be frozen into an explicit or all-eligible
foreground evidence batch. See
[Foreground ebook evidence batches](ebook-evidence-batches.md).
