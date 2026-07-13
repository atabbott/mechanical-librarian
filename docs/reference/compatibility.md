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

Planned lane derivation uses only dimensions relevant to the lane role:
inventory uses target capability and scope; local evidence uses target, media,
field, and extractor; provider evidence uses target, media, field, provider,
query, and configuration; candidates use successfully completed relevant
evidence and policy. An irrelevant dimension is absent, not falsely required. Target
compatibility, provider configuration, provider capability, connection
availability, execution outcome, candidate count, and evidence assessment
remain orthogonal. Unconfigured, unsupported, unavailable, degraded, failed,
and successful-empty states are not interchangeable and cannot block unrelated
capable lanes.

## Audiobookshelf support

Audiobookshelf compatibility is verified with:

- versioned, redacted response fixtures;
- adapter contract tests;
- supported-version integration tests against disposable instances; and
- mutation canaries that verify before-state, request, response, and after-state.

When an upstream release changes undocumented behavior, the project may mark that version experimental or unsupported until the adapter is verified. Direct writes to the Audiobookshelf database are not a compatibility strategy.

The F04 connection contract is `abs-connection-v1`, backed by fixture revision
`abs-2.35.1-fixture-v1`. Bearer API-key authentication requires Audiobookshelf
2.26.0 or newer, but only the pinned 2.35.1 contract is currently `supported`.
Older, newer, missing, malformed, and otherwise unknown versions are
`incompatible` until separately qualified; authentication success does not
widen compatibility evidence. F04 does not send a credential or claim tested
authentication/authorization for those unqualified versions; only anonymous
status evidence from the pinned version may reach the authorization probe.

The reviewed machine source is
[`audiobookshelf-f04-compatibility.json`](audiobookshelf-f04-compatibility.json).
The runtime profile and this human view are drift-tested against it.

<!-- f04-capabilities:start -->
| Capability | Exact 2.35.1 state |
| --- | --- |
| authentication | tested |
| authorization | tested |
| version_discovery | tested |
| inventory_read | supported |
| metadata_read | supported |
| cover_read | supported |
| media_file_read | supported |
| pagination | supported |
| incremental_scan | unavailable |
| metadata_fields | supported |
| cover_behavior | supported |
| rollback_read | unavailable |
| rate_limits | unavailable |
| notifications | experimental |
| concurrency_tokens | unavailable |
| mutation | unavailable |
<!-- f04-capabilities:end -->

These are compatibility declarations; F04 invokes no inventory or content
route. Media-file support additionally depends on the authorized profile's
download permission. Mutation remains unavailable because F04 has no mutation
authority.

The evidence pins official container digest
`sha256:1eef6716183c52abafe5405e7d6be8390248ecd59c7488c44af871757ac8fc4d`.
The endpoint contract follows the tagged implementation's observational
authorization POST, not the out-of-date API-reference GET.

Audiobookshelf remains the first production target adapter. A future CWA adapter
may be declared supported only after its target-service contract, capability
matrix, and supported service API have passed qualification. A CWA-shaped fake
is test evidence for the vendor-neutral contract only; it does not establish a
runtime adapter, database compatibility, UI-automation fallback, or production
write support. Direct Calibre or CWA database access is not a compatibility
strategy.

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

Canonical target, snapshot, evidence, candidate, proposal, reviewer-packet,
decision, and apply-plan schemas contain only application/domain-owned fields
and opaque namespace-scoped identities. Raw Audiobookshelf, CWA, and metadata-
provider semantics and payload fields stay adapter-local. A raw external item
value crosses only as an uninterpreted `ExternalItemId` inside its fully scoped
canonical `TargetId`, never unscoped. The planned shared contract cohort will
run the same canonical scenario through Audiobookshelf and CWA-shaped target
fakes, each with provider-disabled and configured-provider variants, without
schema forks.

Workflow definitions are also durable compatibility inputs. A run records its
immutable workflow-definition version and graph digest. Adding a required
stage, changing dependencies or expected outcomes, or otherwise making the
definition incompatible creates linked superseding or replanned work and
marks transitively affected downstream checkpoints in that linked run ineligible
for reuse or current applicability. Readers preserve and can explain the
original graph and outcomes, which are never mutated or erased.

Candidate-lane manifests record applicability for every derived lane and typed
execution outcome for applicable lanes. Only successfully completed applicable
lanes record candidate count and `executed` or `reused` mode. Reuse is
compatible only when target scope and input fingerprints match, provider,
query, and configuration revisions are declared compatible, the lane contract
version is supported, and the completed expected artifact validates. A missing
lane is a coverage failure, and failed execution is never reusable. A capability
change causes the manifest to be re-derived and cannot silently turn missing
execution or not-applicable into a completed applicable zero-candidate result.

JSONL is an artifact-specific framing contract, not the reviewer protocol.
Approved JSONL boundaries use physical LF or CRLF record delimiters and do not
treat Unicode U+2028 or U+2029 inside a JSON string as record breaks. Each
artifact schema publishes blank-line, malformed-record, final-newline, size,
bare-carriage-return, invalid-UTF-8, BOM, and recovery behavior. Reviewer
responses remain direct closed-schema JSON objects.

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
