# Compatibility Policy

Compatibility is an explicit, tested product contract. It is not inferred from whether an integration happened to work once.

## Published matrix

Each release must publish a machine-readable and human-readable matrix covering:

- supported host architectures;
- supported container runtimes and minimum versions;
- supported browsers for the operator interface;
- application database upgrade paths;
- Audiobookshelf versions and capabilities;
- AI backend versions or protocol revisions;
- supported ebook and audiobook formats; and
- optional metadata-provider API revisions.

The matrix should distinguish:

- **tested**: exercised in continuous integration or a release qualification environment;
- **supported**: maintainers intend to diagnose defects;
- **experimental**: available without stability guarantees; and
- **unsupported**: known not to work or outside the support boundary.

## Adapter capability negotiation

An adapter must report capabilities based on the connected target, not only on the Mechanical Librarian build. Examples include:

- item discovery and incremental scanning;
- field-level metadata updates;
- cover updates;
- reading-state access;
- concurrency tokens or reliable updated timestamps;
- rollback-relevant reads; and
- API rate-limit behavior.

Workflows are built from available capabilities. A missing capability must disable or narrow the affected workflow with a clear explanation; it must not trigger a database workaround.

## Audiobookshelf support

Audiobookshelf compatibility is verified with:

- versioned, redacted response fixtures;
- adapter contract tests;
- supported-version integration tests against disposable instances; and
- mutation canaries that verify before-state, request, response, and after-state.

When an upstream release changes undocumented behavior, the project may mark that version experimental or unsupported until the adapter is verified. Direct writes to the Audiobookshelf database are not a compatibility strategy.

## File formats

Format support is capability-specific. A format may support identification and embedded metadata extraction without supporting safe metadata write-back.

The matrix must list read, package-metadata extraction, opening-matter
extraction, cover extraction, and write-back support separately. EPUB support
also records tested package versions, navigation/guide behavior, encryption
limits, and qualified producer-tool variants. Unknown or malformed files remain
visible as unsupported evidence sources; they must not block the rest of a
library scan.

## Public API compatibility

Before a stable release, public endpoints and schemas may change, but every change must update the OpenAPI description and changelog.

After the first stable release:

- backward-compatible fields and endpoints may be added within a major version;
- existing meanings must not change silently;
- removals require deprecation notice and a documented replacement;
- clients must tolerate unknown response fields; and
- server-side mutation validation must remain strict.

See [HTTP API](../architecture/http-api.md).

## Configuration and database compatibility

Unknown configuration keys should produce actionable diagnostics. Renamed or removed keys require a migration warning and, where safe, an automated conversion.

Database compatibility follows [Upgrades and migrations](../operations/upgrades.md). The project supports documented upgrade paths, not arbitrary downgrades or skipped major versions.

## Durable domain representations

Persisted identifiers, versions, fingerprints, and idempotency keys are stable
only within their declared namespace and schema version. New fields may be
added to a versioned canonical fingerprint input only by introducing a new
`SchemaVersion`; existing canonical serialization, digest algorithms, and
identifier scopes must not change silently. A future migration must retain the
source representation and record the exact compatibility path before replacing
or interpreting a durable value.

Evidence persistence, reviewer packet, review API, and readiness projections
use independent v1 schema markers. Their allowlisted fields, ordering, text and
collection limits, target scope, cross-target policy evidence, and privacy
classification are compatibility semantics. Changing one requires a new schema
version and a documented reader or migration path; a schema from one boundary
is never accepted by another.

### Canonical inventory snapshot v1

`snapshot-v1` is the first durable canonical input for inventory fingerprints
and semantic snapshot equality. Its canonical JSON contains:

- the complete adapter-, connection-, library-, and external-item target scope;
- media type, lifecycle, and normalized metadata with fields ordered by name;
- sorted one-way identities for logical file references;
- a one-way cover-reference identity and optional versioned cover-content
  fingerprint;
- adapter-bound adapter and upstream service release versions;
- the versioned raw-observation payload fingerprint;
- a one-way identity for opaque concurrency evidence; and
- the declared inventory, metadata, file, notification, pagination,
  concurrency, cover, writable-field, and supported-media capability state.

Snapshot IDs and observation timestamps are deliberately excluded: repeating
the same observation at another time or under a new immutable record ID does
not change canonical state. Canonical JSON uses sorted object keys, ASCII JSON,
finite values, and compact separators. Set-like capabilities and file
identities are sorted; order within a multi-value metadata field remains
meaningful.

The serialized v1 input is bounded to 1 MiB. Generation rejects more than
10,000 collection entries or more than 128 KiB of source text before sorting,
hashing, or JSON serialization, leaving room for worst-case JSON escaping and
structural overhead. Stored input is accepted only when every
documented field is present, no unknown field exists, nested values have their
declared type, normalized text contains no unpaired Unicode surrogate, and
set-like values are sorted and unique. Corrupt, oversized,
deeply malformed, or non-canonical stored input fails before it can become a
fingerprint or snapshot.

Changing any included field, identity rule, ordering rule, or equality meaning
requires a new snapshot schema version. Readers must retain the original
canonical bytes and schema marker until a documented migration can recompute
and attribute the replacement. Adapter implementation versions and upstream
service versions are distinct typed values and must remain bound to the target
adapter family.

## Inventory and capability contracts

Inventory records preserve stable target identity separately from immutable
snapshots. Adding an optional normalized metadata field or a read-only adapter
capability is compatible only when an older reader can ignore it. Removing or
changing a field's meaning, target scope, fingerprint input, or declared
read/write semantics requires a new schema or adapter contract version and a
documented migration. Capability declarations narrow available workflows; they
never authorize a write.

## Deprecation

Each deprecation notice states:

- the affected capability;
- the first version that warns;
- the replacement path;
- the earliest removal version or date; and
- any export or migration action required.

Security fixes may require faster removal. The security advisory must explain the exception without disclosing exploit details prematurely.
