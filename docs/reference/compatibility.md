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

## 0.2 title technical-preview contract

F09a binds one in-memory Audiobookshelf-shaped synthetic title snapshot,
exactly one current successful unambiguous applicable F06 `TITLE` observation
from the real child reading a generated EPUB whose
`normalized_value` is non-empty, bounded, and usable rather than an unsafe,
oversized, or redaction marker, and one provider-neutral title candidate to the
same target and snapshot. Selector-less F06 titles are valid. Multiple
applicable titles remain ambiguous; provider agreement or source order never
selects one.

A conservative deterministic comparison applies, in order, supported-runtime
NFC normalization, Unicode default case folding, final NFC normalization, then
trims leading/trailing Unicode whitespace and collapses each internal
Unicode-whitespace run to U+0020. It binds the comparison-policy version and
runtime Unicode-data version. Comparison never alters proposal or evidence
values or removes punctuation, diacritics, subtitle or series text, words, or
token order. When F06 and provider values compare equally, the exact F06
`normalized_value` is the proposed value; F06 `raw_value` remains
evidence/provenance and provider evidence only corroborates.

Each evaluation records an immutable versioned proposal set, including typed
zero-proposal results. Agreement plus a current title field that is present and
loaded with an empty value, or a materially different current title, produces
exactly one `needs_review` proposal. A missing, unloaded, or unavailable current
title produces a typed current-title no-proposal result; current equivalence a
typed no-op; disagreement a typed conflict; an empty, unsafe, oversized,
redacted, or otherwise unusable F06/provider title a typed unusable-title
result; and another failed, unsupported, disabled, no-match, stale, duplicate,
or unjoinable input its exact typed no-proposal result. Provider failure never
erases local evidence or
becomes negative bibliographic evidence. Identical inputs replay without
duplication; changed inputs produce a superseding set without rewriting
history. F10 accepts only one current immutable synthetic-preview proposal set containing exactly
one current `needs_review` title proposal; no other proposal or set creates a
case.

F09a supplies a deterministic fake candidate through the application-owned
provider port. The closed `synthetic_preview` mode and derived false production
applicability are part of compatibility identity. No live snapshot/media join,
configuration, production review, or apply compatibility is claimed; that
binding is deferred. F09b implements the same port with disabled-by-default bounded
Hardcover at fixed HTTPS endpoint `https://api.hardcover.app/v1/graphql` and a
fixed read-only query. Only bounded canonical title evidence and minimal
provenance are compatibility data. Raw provider requests, responses, headers,
tokens, request bodies, upstream identifiers, and caches are not retained
artifacts.

Ordinary CI is fake-based and credential-free. F09b completion requires one
opt-in bounded read-only smoke/contract qualification against the fixed
endpoint using a dedicated non-production account and token, retaining only a
redacted aggregate result. The preview adds no AI, human decision, cover,
rich-field, multi-provider, or apply compatibility promise.

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
| notifications | unavailable |
| concurrency_tokens | unavailable |
| mutation | unavailable |
<!-- f04-capabilities:end -->

These are compatibility declarations; F04 invokes no inventory or content
route. Media-file support additionally depends on the authorized profile's
download permission. Supported inventory and metadata declarations require an
active profile with complete configured-library coverage plus explicit
all-tag and explicit-content visibility; narrower content scopes are
incompatible with the current profile contract. Mutation remains unavailable
because F04 has no mutation authority.

Socket.IO notifications are unavailable at the API-key-only trust boundary.
Audiobookshelf 2.35.1 routes socket authorization through its `SocketAuthority`
and `TokenManager`, which do not accept API keys. Mechanical Librarian does not
request login credentials, mint a session JWT, or construct a socket client;
polling remains the supported inventory observation path.

### Audiobookshelf 2.35.1 inventory contract

F05a freezes the official 2.35.1 tag object
`71764d118a5f12466beeaf8f1d4d5e0370a199dc`, commit
`aacdcc47ec0b6fa778b84627c79deb7ee0cecd70`, and the same digest-pinned
container used by F04. The machine-readable contract is
[`audiobookshelf-f05a-compatibility.json`](audiobookshelf-f05a-compatibility.json).
Only the libraries list, zero-indexed numeric library-item pages, and expanded
item detail routes are used. The upstream API supplies no stable inventory
cursor, snapshot token, or concurrency token, so no incremental-scan claim is
made. Equal bounded complete passes are the stability proof; any disagreement
is an `unstable` scan and cannot publish removals.

The expanded book response is authoritative because the minified page response
joins contributors and omits complete files and chapters. Mixed book libraries
are supported: audio-bearing books are audiobooks, ebook-only books are ebooks,
and a hybrid is an audiobook with supplementary ebook file evidence. File and
cover source locations are hashed before crossing the adapter boundary.

The frozen `Book` serializer establishes independent `isbn` and `asin` keys;
ordinary null, empty, or malformed sibling state cannot discard the other
valid bounded key. Invalid sibling values are omitted and represented only by
a bounded warning. No additional identifier namespace is claimed by F05a.

The file contract follows `AudioFile`, `EBookFile`, `LibraryFile`, and
`FileMetadata`: inode and audio/ebook format facts are top-level, while
filename, extension, private path, relative path, size, and timestamps are
nested below `metadata`. All of these allowlisted facts contribute to the
opaque file identity. Safe format/stat facts may appear in the descriptor;
filenames and paths never appear unhashed. Audio entries marked `exclude=true`
do not establish audiobook classification or file evidence. Missing or false
`exclude` values are included, while null and other types are malformed.

### Canonical inventory snapshot v2

`inventory-snapshot-v2` is additive to the F03 `snapshot-v1` contract. It
carries an adapter-neutral target, media/lifecycle, typed source-load states,
separate bounded raw and normalized structured field values, opaque file/cover identities,
adapter/normalizer/service lineage, and bounded warning codes. Observation time
is durable provenance but excluded from semantic fingerprints, so identical
rescans reuse history. No write capability, credential, endpoint, raw vendor
payload, playback state, private path, or upstream concurrency token is
present.

The evidence pins official container digest
`sha256:1eef6716183c52abafe5405e7d6be8390248ecd59c7488c44af871757ac8fc4d`.
The endpoint contract follows the tagged implementation's observational
authorization POST, not the out-of-date API-reference GET.

F05b retains that exact upstream and container baseline. Its machine-readable
operational profile is
[`audiobookshelf-f05b-compatibility.json`](audiobookshelf-f05b-compatibility.json).
Polling is numeric-page, two-pass, GET-only reconciliation with durable bounded
retry; it does not claim an upstream rate-limit contract, stable cursor,
concurrency token, incremental scan, or Socket.IO notification support. A
controlled 429 and validated `Retry-After` exercise generic HTTP recovery only.

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

### EPUB package compatibility baseline for F06

F06 uses these authoritative revisions to interpret package semantics while
preferring useful bounded extraction over rejecting common producer defects:

- [Open Container Format (OCF) 2.0.1](https://idpf.org/doc_library/epub/OCF_2.0.1_draft.doc), document version 1.0.1, Recommended
  Specification dated 2010-09-04. The frozen official Word source
  has SHA-256
  `aa64a61ca430257f1aa22fe555bb1281053a9cf3741b2d645c27d77a8a0d09d9`.
- [Open Packaging Format (OPF) 2.0.1](https://idpf.org/epub/20/spec/OPF_2.0.1_draft.htm), Recommended Specification dated
  2010-09-04. Legacy `version="1.0"` and other 2.x declarations use the
  bounded OPF 2 interpretation and emit a compatibility warning.
- [EPUB 3.3](https://www.w3.org/TR/2026/REC-epub-33-20260113/), W3C Recommendation dated 2026-01-13. Package documents in the 3.x
  family use EPUB 3 semantics; noncanonical versions emit a warning.

The package subset supports bounded ZIP64 plus stored and Deflate members. A
component name over 255 UTF-8 bytes is `malformed`; a complete member name over
1,024 UTF-8 bytes is `resource_limit`. Identical repeated rootfile declarations
are malformed rather than filtered. Multiple distinct canonical rootfiles are
`unsupported_multiple_renditions`. Required container/rootfile ambiguity
remains fatal; defects in optional metadata, manifest, spine, navigation,
guide, cover, tour, and refinement declarations are omitted or preserved as
applicable with typed warnings. CRC is checked for each required member that is
opened; F06 makes no integrity claim for opaque, unopened content members.

For read-only interoperability, zero-length trailing-slash directory entries
remain opaque. The exact known producer-residue pattern with ZIP bits 1, 2 and
4 all set is tolerated on stored or Deflated entries when the local and central
headers agree, and emits `noncanonical_zip_flags`. A bounded `mimetype` member
with the exact media type after BOM/whitespace normalization may be compressed
or appear out of order and emits `noncanonical_mimetype`. The compression method
still controls decoding; partial or adjacent flag patterns, encryption, patched
data, strong encryption, unknown flags, unsafe names and header disagreement
remain rejected. EPUB 3 `title-type` values and `display-seq` values through the
public-domain cap of 1,000,000 refine one originating title observation without
inventing document order or duplicating the title. Invalid optional datatypes
remain cited but inapplicable, or are omitted when no safe bounded fact remains.

Every DOCTYPE and general, parameter, unparsed, or external entity declaration
or reference is intentionally unsupported and maps to `malformed`. OPF 2
multi-file/entity package forms are likewise outside the supported subset.
F06 preserves bounded trustworthy manifest, spine, guide, NCX, navigation,
cover, and encryption declarations but does not dereference or parse their
content. Semantic navigation, cover, spine and NCX evidence requires a present
local resource and a compatible media type where the format defines one.
Missing, incompatible or ambiguous optional graph declarations are warned and
omitted rather than failing unrelated metadata evidence. EPUB 2 deprecated `tours` structure
is inspected without exposing its titles or dereferencing its sites and emits
`deprecated_tours`; invalid or EPUB 3 tour structure is ignored with an
additional warning. EPUB 3 container `links`,
package `collection` elements, deprecated `bindings`, and XML Encryption
`EncryptedKey` elements are accepted as bounded opaque package structure;
encrypted resource classification still requires a complete local
`EncryptedData`/`CipherReference` declaration. Runtime memory, CPU, descriptor
and elapsed-time peaks remain qualification
evidence; canonical reports contain deterministic logical counters only.

The closed generated manifest executes the ordinary archive, package,
encryption, malformed-input, resource and unsafe-metadata partitions and runs
each process-only partition through its named integration test. The complete
609-test repository suite is tested on macOS arm64 with CPython 3.13.14 (Expat 2.8.1,
zlib 1.2.12) and CPython 3.14.6 (Expat 2.7.4, zlib 1.2.12); macOS remains a
development target for F06 because framework Python cannot install the Linux
address-space cap.

The 135-test F06 parser/process cohort is separately tested as UID/GID
65534:65534 on Linux arm64 with CPython 3.13.11, Expat 2.7.3 and zlib 1.2.13 in
`ghcr.io/astral-sh/uv@sha256:531f855bda2c73cd6ef67d56b733b357cea384185b3022bd09f05e002cd144ca`.
That qualification disables networking, mounts both repository and installed
environment read-only, uses a read-only container root, and proves the fixed
256 MiB `RLIMIT_AS`, one-process `RLIMIT_NPROC`, 30-second CPU, 64-descriptor
and zero-core gates. It also proves that an additional process is denied,
parent loss terminates the child, and the ordinary sibling remains usable after
cancellation or timeout. This records F06 evidence for that exact combination;
it does not publish whole-product Linux support or widen the frozen format
subset.

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
