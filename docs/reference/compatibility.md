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

## F07a EPUB opening compatibility

`epub-opening-report-v1` is a transient local compatibility surface for EPUBs
that first pass the F06 package gate. EPUB 3.3 package/navigation semantics and
Structural Semantics Vocabulary 1.1 landmarks are supported. Legacy EPUB 2
OPF/OPS 2.0.1 guide declarations participate in opening selection. Candidate
content media are declared `application/xhtml+xml` and `text/html`; image
resources are typed image-only and receive no OCR. ZIP compression and package
compatibility remain exactly the F06 contract.

Strict XHTML and bounded deterministic recovery are supported outcomes.
Recovery is compatibility behavior, not conformance repair: it never rewrites
the EPUB or adds rendering, CSS, active-content, base-URL, external-resource,
filesystem, or network authority. UTF-8, Windows-1252, and ISO-8859-1 recovery
are supported when a real XML declaration or supported HTML charset declaration
selects them; conflicting declarations and other recovered encodings are typed
unsupported. Declaration lookalikes in comments, prose, arbitrary attributes,
scripts, or styles are inert. Ordinary HTML/XHTML doctypes and their external
identifiers are inert recovery syntax and are never resolved. Entity declarations, internal
subsets, non-HTML doctypes, and external resolution remain rejected. Parser,
recovery, projection, selection, normalizer, vocabulary,
limits, extractor, and schema versions all invalidate later reuse when changed.
Parsed resource records and their citations share one exact resource-byte
digest, and the ordered selection digest binds that byte digest alongside the
resource identity and selection path.
Recovered qualified start/end tags share resolved namespace identity. Active
local elements remain inert across XHTML, SVG, and foreign namespaces.
Extension-prefixed `epub:type` values remain distinct from the unprefixed
Structural Semantics Vocabulary and have no F07a semantic authority.
F06 and F07 use the same capability-independent, source-fingerprint-derived
lineage value; a child payload cannot establish correlation by coordinating
forged report and citation fields.
Raw OPF annotations never re-admit an itemref that F06 rejected. Same-resource
opening, body, and exclusion fragment targets retain distinct boundaries, and
all recognition-material opening roles survive deterministic one-open
deduplication. Fragment validity is resolved before precedence, and only the
highest-precedence tier with a surviving valid opening role can authorize
recognition; one surviving role from that tier supplies canonical provenance.
A valid higher tier suppressed by body or exclusion semantics prevents
lower-tier fallback. Invalid-only optional roles cannot authorize claims when
no valid guide or spine association exists. Empty and absent fragments are equivalent; nonempty decoded
fragments require one unique HTML/XHTML `id` or `xml:id`. Body targets stop at
the earliest valid position and exclusions are scoped to their target element.
Raw `linear` accepts only absent, exact `yes`, or exact `no` values.
Duplicate declaration and controlled recovery attributes, malformed or
reordered XML declarations, inert RCDATA/template/foreign-meta lookalikes,
BOM-less UTF-16/UTF-32 signatures or NULs anywhere in a bounded payload,
missing or empty named-prefix bindings, and undeclared-prefix HTML lookalikes all
fail or remain inert according to the closed recovery profile. Implicit
recovered SVG and MathML subtrees remain foreign beneath an XHTML default;
HTML implied-close searches stop at foreign boundaries. Explicit end-tag
searches stay within the current contiguous namespace region, may close its
matching foreign boundary, and cannot reach an HTML/XHTML ancestor below that
boundary. Unqualified ends resolve through the current default namespace, and
missing, empty-bound, or duplicated qualified prefixes use opaque
prefix-specific identities. Self-closing syntax cannot close an older element
when its own start was not pushed. Namespace declarations are grouped per
prefix, preserving unique bindings (including a foreign default), treating an
explicit unique empty default as an HTML-equivalent no-namespace reset, and
keeping empty named prefixes opaque. Only a duplicated prefix is unknown. Any
duplicated prefix is malformed rather than an encoding failure, while supported
active charset declarations remain available for decoding before malformed
classification and foreign meta remains inert. Duplicate href anchors are
charged before target validation.

The reusable generated registry is `epub-evidence-f07a-v2`. Its F07a
file-backed cohort is materialized offline and every declared consumer runs
through the exact installed wheel in normal tests. The external private gate is
348 EPUBs, of which the F06 baseline identifies 347 safely readable packages.
Every eligible F06-readable resource within the hard F07a profile must be
`success` or `no_applicable_evidence`; only root-caused encrypted, unsupported
media/encoding, unsafe external/active construct, unstable source, or hard
resource/time/output rejection categories are permitted. Only aggregate
categories may be published. Private qualification requires an external closed
root-cause record that exactly covers every permitted rejection and cannot
publish its case identities. This is not a promise that every book contains an
opening claim. To reproduce the closed F06 denominator, the private qualifier
sets the shared document ceiling to the EPUB package profile's 268,435,456-byte
default; this does not change the ordinary deployment default. The safe
aggregate records that accepted qualification profile and allowlisted usage
totals and maxima, without case names, resource names, citations, excerpts,
identifiers, or source hashes.
The qualifier independently validates the complete registry schema, nested F06
and F07 limit profiles, exact opened/parser/resource/path/citation equations,
exit/outcome mapping, and the closed resource-rejection decision table before
aggregation. Rejected parser work is charged by the nodes and projected text
actually consumed before rejection, including both a failed strict attempt and
the following recovery attempt. A safe sibling remains eligible at the exact
aggregate boundary and is rejected only above it. Ordered selection paths are
bound to the source-derived lineage with the versioned selection digest.

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
history. F10a's `synthetic-title-review-case-report` accepts only the
already-persisted current F09a lineage selected by a closed scenario. Exactly
one current immutable synthetic-preview set containing one `needs_review` title
proposal creates a deterministic undecided case. Exact replay creates no
duplicate; a qualifying current successor supersedes it, and a nonqualifying
current successor retires it without an empty replacement. No other proposal or
set creates or revives a case.

F09a supplies a deterministic fake candidate through the application-owned
provider port. The closed `synthetic_preview` mode and derived false production
applicability are part of compatibility identity. No live snapshot/media join,
configuration, production review, or apply compatibility is claimed; that
binding is deferred. F09b implements the same port for one generated
`agree-empty` case through `hardcover-title-proposal-report`. It is disabled by
omitting `--secret-reference`, adds no `config-v1` field, and fixes the HTTPS
endpoint and read-only query. Only bounded canonical title evidence and minimal
provenance are compatibility data. Raw provider traffic, credentials, secret
paths, upstream identifiers, and caches are not retained artifacts.

Ordinary CI is fake-based and credential-free against the sanitized live key,
type, count, and resource shape. F09b completion additionally requires the
exact installed artifact to pass a bounded read-only run against the fixed
endpoint with a dedicated non-production account and disposable state. Only a
redacted aggregate result is retained. This proves the narrow F09b request,
authentication, parser, and canonical title seam described by the
[F09b Hardcover proof-of-concept contract](hardcover-f09b-contract.md). It does
not provide F09f's configured production operation, durable provider work,
freshness/reuse, retry/restart, or ISBN-backed identity, nor F09g's live-target
binding, and adds no AI, human decision, cover, rich-field, multi-provider, or
apply compatibility promise.

F09f increment 3 installed `hardcover-isbn-identity-v1` through the
application-owned ISBN identity port; activation now composes it at the
installed command boundary.
Its compatibility identity fixes the query and operation bytes, canonical ISBN
array and body, HTTPS destination, public-address and Host/SNI/peer policy,
authentication revalidation, status mapping, hostile header/body parser bounds,
complete row validation, and `hardcover-ebook-compatibility-v1` decision. The
supported `fixed-adapter-v3` demonstration uses only the built-in post-policy
in-memory byte channel and exports `actual_network:false`; it proves no live
credential, egress, production configuration, target join, persistence,
lifecycle, retry, proposal, or write compatibility. The retained demonstration
remains credential-free after activation.

This compatibility claim treats contributor and network bytes/metadata,
credentials, DNS/address/peer/TLS data, real external service `Exception`
failures, and resource correctness as hostile. Application-owned typed
collaborators, documented dependency objects, and interface-honouring
structural fakes are trusted; arbitrary same-process descriptor, subclass,
post-construction mutation, forged-exception, traceback-local, `SystemExit`, or
`GeneratorExit` sabotage is not a supported compatibility boundary.

The exact cumulative compatibility registry is
`preview-registry-c3-v6.json` / SHA-256
`394ac103c85548ca3a1de44ef236ed59323fada7340a952b66aab986d0621a02`.
It derives from installed C2 and contains exactly the inert, identity-decision,
and fixed-adapter scenarios; future durable-work and executor definitions are
not current compatibility data.

The fixed-adapter schema uses the project-owned UTF-8 byte-length meta-schema,
digest `6e661639a611c463c65303efc2c83f97e9b522d23ee30c2e4e78f0d4c4e703b1`,
so registry resolution must recognize the required vocabulary and execute its
byte keyword rather than silently treating it as an annotation.

Schema 7 adds only the F10a synthetic case, current-or-retired state, and
append-only attributable transitions. It remains forward-only and does not
reclassify schema-6 `synthetic_preview` history. F09f configured Hardcover
title/identity and F09g live report-only proposals are separate 0.3
compatibility boundaries and must not reclassify F09a, F09b, or F10a preview
history. F09f's activated boundary is tested and supported by its exact
activation registry; F09g creates no claim until its slice qualifies and
merges. F09c fallback identity and F10b production case
admission remain separate planned 1.0 compatibility claims. F11 is an
independent synthetic reviewer path; F17 is their convergence gate.

## Planned 0.3 live title-report compatibility contract

F07b will supply one current durable local evidence lineage. F09f will supply a
separate current configured Hardcover lineage containing a bounded title and
strict ISBN-backed book and compatible ebook-edition identity. The provider
query, configuration, authentication, parser, response/error shape,
cardinality, compatibility policy, bounds, fixtures, and live-qualification
versions are compatibility dimensions. The request, adapter, parser,
identity-decision, durable work, retry/restart, and production activation
dimensions are frozen and structurally supported. The separately authorized
one-request live aggregate is exact-head qualification evidence only. F09f remains
target-independent and claims no target join or proposal compatibility.

F09g will bind the current target, snapshot, source/file identity, F07b
evidence, F09f observation, comparison policy, and proposal policy. Exactly one
distinct canonical checksum-valid edition-applicable local ISBN must be
equivalent to the ISBN of one unique compatible Hardcover ebook edition
belonging to one book, and the one usable current F07 title and Hardcover title
must agree. With a loaded current Audiobookshelf title that is empty or
materially different, the sole positive output will be one `needs_review`,
`production_report_only` proposal whose value is the exact current F07
normalized title.

A missing or unloaded current title; a missing or invalid local ISBN;
conflicting or ambiguously applicable ISBN claims; multiple distinct canonical
local ISBNs; no match; multiple, ambiguous, or incompatible provider matches;
stale target, local, or provider state; disabled or failed provider;
current-title equivalence; or local/provider title disagreement will have its
own typed zero-proposal compatibility outcome. Repeated equivalent claims of
the same canonical local ISBN will remain one ISBN and will not alone force a
zero-proposal result. Valid local evidence will be retained.
Semantically duplicate provider rows may be normalized only under the frozen
F09f cardinality contract; genuinely distinct compatible editions remain
ambiguous. The Hardcover book, edition, and title are a single provider
lineage, so compatibility claims independence only between local EPUB evidence
and the external catalogue.

Identical current inputs will replay without duplicate state. A changed bound
input will create a superseding result and cannot rewrite or revive earlier
history. Only the one positive `needs_review`, `production_report_only` result
will derive production applicability for the report; zero-proposal results and
all 0.2 synthetic history will remain structurally ineligible. This planned
contract adds no current tested or supported compatibility claim and promises
no review admission, decision, approval, AI, apply, target/media write,
destructive, tagging, or release publication.

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

For Audiobookshelf 2.35.1, F07b additionally consumes the already captured
expanded-item `ebookFile.metadata.path` and `relPath` values. A binding exists
only when `ebookFormat` is exactly `epub`, the full path is normalized and
absolute, the relative path is normalized, and exactly one configured
read-only ebook service root yields that same suffix. Missing, null, malformed,
mismatched, wrong-media, and ambiguous projections create no binding. The
frozen source/version/digest and fixture revision are recorded in
[`audiobookshelf-f05a-compatibility.json`](audiobookshelf-f05a-compatibility.json).

## Inventory and capability contracts

Inventory records preserve stable target identity separately from immutable
snapshots. Adding an optional normalized metadata field or a read-only adapter
capability is compatible only when an older reader can ignore it. Removing or
changing a field's meaning, target scope, fingerprint input, or declared
read/write semantics requires a new schema or adapter contract version and a
documented migration. Capability declarations narrow available workflows; they
never authorize a write.

## Durable Hardcover identity compatibility

Schemas 9 and 10 add Hardcover identity work and lifecycle state without
transforming schema-8 rows. Schema 11 adds no table column; it marks every
legacy matched-current pointer expired so only a complete v2 private matched
observation can be reused while preserving the old immutable observation as
history. A
work generation is compatible for matched-current reuse only when its opaque
application-derived work key denotes the same validated identity inputs and
its matched pointer is no older than exactly 24 hours. Generation number,
candidate number, attempt number, and fence are monotonic within that work-key
namespace and are never reset or inferred after restart.

That opaque work key is the sole ISBN-derived persisted identifier permitted by
the F09f privacy contract. Its exact derivation is a currentness compatibility
boundary; it excludes title and raw/reversible input and is never a runtime
output, log, export, report, or audit value. Installed production JSON conforms
to `hardcover-identity-report-output-v6`. A matched report additionally carries
positive edition/book IDs and `equal` or `different`; a non-matched report
carries only the schema marker and closed outcome. Changing that shape or the
closed outcome enum requires a new versioned output contract.

The state values `pending`, `claimed`, `retry_wait`, `succeeded`, `failed`, and
`cancelled`, the terminal result meanings, the 30-second aggregate window,
15-second lease, six-second start threshold, three-attempt bound, and exact
24-hour matched-currentness boundary are compatibility semantics. Changing a
meaning or bound requires a new application contract and a documented durable
migration; an older binary must not open schema 11. Append-only history remains
attributable to its original contract rather than being reinterpreted.

The installed `durable-work-v3` input is a closed qualification DSL, not a
production job or persisted request format. Its canonical public fixtures may
grow only through a new scenario/schema version. Contributor input cannot
override schema bindings, work keys, database identity, gate ownership, or
provider authority.

## Deprecation

Each deprecation notice states:

- the affected capability;
- the first version that warns;
- the replacement path;
- the earliest removal version or date; and
- any export or migration action required.

Security fixes may require faster removal. The security advisory must explain the exception without disclosing exploit details prematurely.
