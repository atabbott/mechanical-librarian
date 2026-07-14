# Testing Strategy

## Goals

- Prove domain behavior independently of external services.
- Detect adapter drift before it reaches production.
- Prove retries and duplicate events are safe.
- Exercise hostile and malformed inputs.
- Reproduce failures without personal library data.
- Test the published container as users run it.

## Test layers

### Unit tests

Pure domain and application behavior: normalization, fingerprinting, evidence
selection, field proposals, policy, validation, state transitions, redaction,
and compatibility decisions.

### Property-based tests

Use generated inputs for invariants such as:

- normalization and fingerprint determinism;
- directional title normalization: adding removable noise and then cleaning it
  returns the same meaningful title, while reversing the transformation is not
  automatically accepted;
- identifier alias and comparable ISBN forms canonicalize consistently without
  changing preserved raw observations;
- non-comparable identifier namespaces produce neither agreement nor conflict;
- resolving one field preserves the complete sibling-proposal set;
- idempotent event handling;
- apply-plan merge associativity where defined;
- no destructive auto-apply disposition;
- stale fingerprints never pass apply validation;
- serialization round trips;
- path and URL safety.

### Contract tests

Every adapter implementation runs the shared suite for its role. Target-service
contracts cover canonical inventory translation, opaque scoped identity,
capabilities, stale-state evidence, supported mutations where applicable,
authentication failure, idempotency, and redaction. Local-extractor contracts
cover bounded vendor-neutral evidence without network or credentials.
Metadata-provider contracts cover query capabilities, provenance, correlation,
unconfigured and unavailable states, timeouts, rate limits, malformed responses,
empty successful outcomes, and denial of target credentials and write ports.
Reviewer contracts consume only canonical packets and proposals.

Provider contract tests accept only a minimal bounded application-built
allowlisted `ProviderQuery`, the provider's own credential, and a restricted
network client. Spies fail the test if the adapter receives a raw `ItemSnapshot`
or service row, target endpoint or credential, unrestricted external ID or path,
media handle, database repository, or mutation port.

Audiobookshelf tests use a fake HTTP and Socket.io service with synthetic item
payloads. Live service tests are opt-in, report-only, and never part of ordinary
CI.

The planned contract suite uses a CWA-shaped fake only as a fixture for the
future target-service port. It models supported API payload and capability
boundaries; it is not a runtime adapter, database integration, UI automation
path, or claim of production support. It traps attempted database, UI,
maintenance-container, media-write, unsupported-API, and production-mutation
fallbacks. The same synthetic canonical target, evidence, candidate, proposal,
and reviewer-packet scenario will run through Audiobookshelf and CWA-shaped
target fakes, each under provider-disabled and configured-provider variants,
without changing domain or reviewer schemas. The CWA-shaped fake exposes no
production mutation capability.

### Integration tests

Exercise SQLite transactions, migrations, durable job recovery, API handlers,
event reconciliation, provider cache, artifact retention, authentication, and
subprocess boundaries.

Review integration tests prove server-authoritative decisions, case
supersession, cursor pagination, exact-version bulk preview/commit, stale-batch
rejection, compensating decisions, audit attribution, and cross-device/restart
recovery.

Durable review-run tests terminate and resume work at each stage checkpoint,
independently recompute exact selected-target coverage, reject incomplete or
unverified required scope and stale upstream versions before dependent stages,
and prove that scoped review unreadiness does not stop unrelated ingestion.
Workflow-definition upgrade cases pin the original version and graph digest,
introduce a newly required upstream stage, and prove that linked superseding or
replanned work preserves immutable history while marking transitively affected
downstream checkpoints ineligible for reuse or current applicability. Resume
never mutates or erases the old graph or its outcomes.
Typed terminal no-result and degraded optional outcomes count toward coverage.
Every blocker identifies the required scope, stage, target, and missing or
failed terminal outcome without pretending that an artifact or proposal exists.

Scheduler integration tests use bounded acyclic graphs to cover durable enqueue
of the complete eligible set, capacity-bounded claims, stable identity and
deduplication, partial launch, fan-in dependency promotion, missing or malformed
expected outcomes, worker and scheduler death, process restart, cancellation
and expiry, stale leases and fencing tokens, and report-only authority
isolation. They prove `waiting` is nonterminal and
distinct from `retry_wait`; at-least-once redelivery is expected, while
idempotent handlers plus provider idempotency or reconciliation prevent or
recover duplicate external effects. Process IDs or watcher liveness never
establish outcome or authority. Competing-scheduler tests prove fencing
increases monotonically, only one fenced outcome is accepted, and reject stale
lease renewal, completion, artifacts, checkpoints, audit
completion, and fan-in. ETA tests cover bounded basis and timestamp reporting,
freshness, insufficient samples, stale estimates, and the explicit unknown
state.

### Interface and accessibility tests

Test the bundled review interface against the versioned API using synthetic
cases. Required scenarios include:

- one ordinary case per target snapshot;
- independent field, cover, and series selections;
- separate destructive cases;
- confirmed sibling navigation without decision leakage;
- queue filtering, empty results, stable routing, and Back/Forward behavior;
- detail scroll reset with queue-position preservation;
- individual and bulk decision preview, confirmation, stale rejection, and
  reversal;
- no document-level overflow at 390 pixels, plus representative 768-pixel and
  desktop viewports;
- keyboard-only use, visible focus, semantic controls, live announcements, and
  automated accessibility checks; and
- bounded initial DOM, API payload, asset, cover-decode, and interaction costs.

The real-browser cohort derives review categories from the versioned product and
category-capability contract, records a representative fixture for every
applicable category and an explicit reason for every not-applicable category,
and fails on unexpected page or console errors. Version-change cases distinguish
untouched presentation defaults, which may refresh with the current proposal,
from acknowledged user decisions, which must remain server-authoritative and
must never be silently rewritten.

Static markup assertions may guard a narrow contract but cannot be the only
evidence for interaction, responsive, or accessibility acceptance. Run real
browser tests and an automated accessibility scanner against representative
metadata, cover, series, notice, sibling, and review-summary states.

### End-to-end tests

Run the published container against synthetic services and media fixtures:

1. Discover an item.
2. Extract evidence.
3. Source candidates.
4. Use a deterministic fake reviewer.
5. Prepare and apply a safe plan.
6. Verify resulting service state.
7. Restart and prove no duplicate write occurs.

Separate scenarios cover reviewer failure, provider outage, backup failure,
stale state, partial apply, verification disagreement, rollback, and circuit
breaker behavior.

#### 0.2 title technical-preview cohort

The 0.2 cohort stops at one persistent, undecided title review case and runs
without production credentials or writes. F09a uses a deterministic fake
provider. F09b runs the bounded Hardcover adapter against a fake transport; CI
never uses a live provider token. That revision remains `experimental` while
qualification is fake-transport-only. Both variants exercise the same immutable,
versioned, provider-neutral candidate and proposal-set contracts. Each evaluated
input combination persists one proposal-set version bound to the exact target,
snapshot, three `proposal-input-slot-v1` identities, normalization, and policy
versions. The current-title, F06-title, and provider-title slots each contain a
tagged successful value/evidence identity or the exact typed non-success outcome
with its scope and contract version. This makes typed zero-proposal sets
constructible without sentinel values or nonexistent provider candidates.

The `title-normalization-v1` cohort uses the Unicode 16.0.0 definition of NFC
and frozen, byte-exact copies of `UnicodeData.txt`,
`DerivedNormalizationProps.txt`, `CompositionExclusions.txt`, `PropList.txt`,
and `CaseFolding.txt`, with a recorded SHA-256 digest for each file. It runs the
full Unicode 16.0.0 `NormalizationTest.txt` conformance data and proves the
exact sequence: NFC under Unicode 16.0.0; map each maximal run of code
points with `White_Space=Yes` in Unicode 16.0.0 `PropList.txt` to U+0020 and
remove leading/trailing U+0020; apply Unicode 16.0.0 Default Case Folding status
C/F mappings from `CaseFolding.txt`; apply NFC under Unicode 16.0.0 again; and
compare resulting keys byte-for-byte. Golden fixtures prove implementations use
the complete frozen baseline rather than host-runtime Unicode data.

The cohort also proves punctuation, diacritics, subtitle separators and content,
series/order text, words, and token order are neither stripped nor replaced.
Provider data only corroborates. Every eligible proposal retains the bounded
original F06 package title, never the provider spelling or comparison key.

The F06-title input cohort accepts only a successful current report with no
`ambiguous_title` or `ambiguous_metadata_refinement` warning and exactly one
applicable `TITLE` observation whose citation selector is exactly
`title-type-main@<source-ordinal>` or
`title-type-main@<source-ordinal>;display-seq@<source-ordinal>`, with base-10
ordinals. The optional display component is provenance only.
Either warning maps first to the exact `f06_title_ambiguous` slot, including
when a retained `main` title is otherwise unique and valid.
Its exact retained value must be at most 4,096 characters and must not use an
F06 reserved one-way redaction or oversize-marker prefix. Multiple applicable
titles, a selector-less title, a sole `short` or `expanded` title, a redaction
marker, and an oversized exact value become the exact typed
`f06_title_ambiguous`, `f06_title_role_unproven`,
`f06_title_ineligible_role`, `f06_title_redacted`, or
`f06_title_out_of_bounds` zero-result slot; no applicable title produces
`f06_title_missing`. Provider agreement, document order, and `display-seq`
never select among local title observations.

The decision matrix binds the current Audiobookshelf title, exactly one eligible
title from a current successful F06 report, and one provider title to the same
target and snapshot:

- equal local and provider comparison keys plus canonical current state `EMPTY`
  produce a set with exactly one fill-empty `needs_review` proposal;
- equal local and provider comparison keys plus canonical current state
  `PRESENT` produce one replacement proposal when materially different and a
  typed zero-proposal set when equivalent;
- canonical current states `MISSING`, `NULL`, `MALFORMED`, and `UNSUPPORTED`
  each produce their own typed zero-proposal set and never enter the empty row;
- local/provider key disagreement produces a typed conflict zero-proposal set;
- missing, failed, unsupported, stale, or unjoinable required evidence produces
  a typed zero-proposal set; and
- provider disablement, unsupported or incompatible capability, timeout,
  malformed response, empty result, or no match leaves inventory and F06
  evidence visible and creates its exact typed zero-proposal set.

Duplicate and unjoinable input tests create no current proposal. Identical
replay resolves to the same semantic proposal-set identity and, where
applicable, the same case identity without a duplicate. Changed or stale input
supersedes current applicability while preserving every prior set, proposal,
case, and typed outcome.

The identity cohort includes exact-value drift that normalization deliberately
hides: replacing retained F06 title `DUNE` with `Dune` keeps the same comparison
key but creates a new immutable proposal-set version bound to the new exact F06
value and evidence identity. It supersedes current applicability; it is not an
idempotent replay of the prior proposal.

F10 tests prove that only a current set containing exactly one current
`needs_review` proposal creates one case, and that persistence, restart,
idempotent replay, immutable history, and stale-input supersession create
neither duplicates nor empty cases. Negative spies prove the cohort invokes no
AI reviewer, human-decision path, apply path,
cover path, rich-field policy, multi-provider fusion, raw-response cache, or
production network service.

Before F09b can become Ready or implementation begins, its supported contract
freezes the exact outbound field allowlist and a disclosure purpose for every
field; tests assert that the adapter sends exactly that allowlist and no
undeclared field. Fake-transport qualification covers:

- the fixed HTTPS origin, `/v1/graphql` path, POST method, fixed read-only query,
  frozen outbound fields, TLS validation, redirect denial, DNS and SSRF
  rejection, and a dedicated client isolated from target credentials, cookies,
  endpoints, and write transports;
- request bytes, response bytes, JSON nesting, retained text, result count,
  per-attempt and total deadline, one-request concurrency, attempt count,
  backoff, and retry bounds, including cancellation without unbounded retry;
- typed authentication, rate-limit, cancellation, outage, GraphQL error,
  malformed JSON, oversized response, incompatible schema, successful-empty,
  and no-match outcomes; and
- token and authorization-header redaction plus evidence-retention tests proving
  that only bounded canonical candidate/evidence and opaque provenance are
  inspectable and exportable by the owner; expiry under
  `retention.evidence_days` and owner deletion purge private retained values;
  any referential-history tombstone is minimal and non-private; and raw
  requests, responses, headers, and upstream identifiers are never cached or
  persisted.

Ordinary CI and the technical-preview release remain token-free. Before the
Hardcover API revision can move from `experimental` to `tested` or `supported`,
an opt-in release job must use a dedicated synthetic Hardcover account and
short-lived account token to run the exact fixed read-only query against the
frozen schema and authentication contract. The job retains only the redacted,
aggregate qualification result; the token and provider payload never become
fixtures or artifacts. Absence of this live credential does not block the
experimental technical preview.

The broader 1.0 report-only cohort disables every external metadata provider
and still completes inventory, local evidence extraction, deterministic no-op
and rejection handling, bounded proposal creation, and human-review projection.
A paired configured-provider scenario may improve evidence but must preserve
the same canonical proposal identity rules. Versioned per-field sufficiency
fixtures span media type, operation, and change class—including fill-empty,
replace-populated, no-op, rejection, and edition-sensitive work—and prove
provider-required cases route to review or explicit unsupported outcomes rather
than disappearing. These broader field, providerless-proposal, multi-target,
and AI-review scenarios are not 0.2 release gates.

The provider-disabled variant asserts that composition constructs no provider
adapter or provider network client, resolves no provider secret, and performs no
provider DNS lookup or network request. It is a configuration variant against
the same target fake, never a substitute providerless target adapter.

### Container security tests

From the reviewer principal, assert denial of:

- service secrets;
- main database;
- production media mounts;
- backup directories;
- supervisor control;
- curator API write endpoints.

Also assert that the container runs without unnecessary capabilities, accepts
read-only media mounts, and preserves state across upgrade/restart tests.

## Fixtures

Fixture families include:

- the exact 0.2 title matrix: canonical current title `PRESENT`, `MISSING`,
  `NULL`, `EMPTY`, `MALFORMED`, and `UNSUPPORTED`, with `PRESENT` partitioned
  into different and equivalent values and only `EMPTY` eligible to fill;
  agreeing and disagreeing `title-normalization-v1` keys with preservation of
  the bounded original F06 package title; missing, failed,
  unsupported, stale, and unjoinable F06 input; and provider disabled,
  unsupported, incompatible, timed out, malformed, empty, unmatched, stale,
  and successful through a fake Hardcover transport;
- F06 title-selection fixtures asserting exact selector components for one
  valid `title-type-main@<n>` title and
  `title-type-main@<n>;display-seq@<m>`; selector-less input; repeated identical
  and distinct titles; `main` plus `short` or `expanded`; sole
  `title-type-short@<n>` and `title-type-expanded@<n>`; duplicate, conflicting,
  invalid, and
  unsupported title-type refinements; retained `main` plus duplicate
  `display-seq`; cited unsafe/oversized projections; and
  exact retained lengths 4,095, 4,096, and 4,097. Every non-unique, ambiguous,
  role-unproven, role-ineligible, redacted, or out-of-bounds case creates its
  exact zero-result slot even when the provider agrees with one local value;
- immutable proposal-set fixtures covering typed zero-proposal outcomes, exact
  tagged identity for all three required slots, duplicate/unjoinable input,
  identical replay identity, and state-, value-, evidence-, or outcome-changed
  supersession without history mutation;
- F10 case creation, restart, idempotent replay, and supersession fixtures that
  retain immutable set, proposal, and case history and reject zero-proposal-set
  or stale case creation;
- valid and malformed EPUB packages;
- EPUB package and opening-matter variants covering title, copyright, imprint,
  edition, contributor, identifier, series, translation, illustration,
  omnibus, navigation, same-file contradiction, and edition applicability
  under the
  [opening-ebook evidence contract](../product/opening-ebook-evidence.md);
- bounded synthetic audio tags and chapter data;
- generated opening-credit audio covering speech, silence, music, delayed
  credits, spoken roles, multilingual cases, no-credit controls, and hostile
  spoken instructions under the
  [opening-audio evidence contract](../product/opening-audio-evidence.md);
- ambiguous title/author/series cases;
- omnibus, abridged, dramatized, summary, and alternate-edition conflicts;
- missing and low-quality synthetic covers;
- duplicate provider results and redirects;
- orthogonal provider configuration, declared capability, connection
  availability, execution outcome, candidate count, and evidence disposition;
- role-specific lane derivation: inventory from target capability and scope;
  local evidence from target, media, field, and extractor; provider evidence
  from target, media, field, provider, query, and configuration; and candidates
  from successfully completed relevant evidence and policy. Fixtures prove an
  absent irrelevant dimension is not a false requirement and cover provider-
  disabled, unsupported, unavailable, degraded, failed, empty-success, and
  unrelated-lane controls;
- sufficiency matrices keyed by field, media type, operation, and change class,
  including fill-empty, replace-populated, no-op, rejection, and edition-
  sensitive cases;
- equal normalized candidates from different source adapters/providers proving
  generic provenance and correlation survive while provider name does not alter
  provider-neutral comparison grouping for the same target, snapshot, field,
  and value;
- repeated and projected records from one evidence family that must not count
  as independent agreement;
- identity-backed title cleanup with supported target and work identity, plus
  edition identity or applicability when the field policy or proposed change is
  edition-sensitive;
- recognized series/order, format, identifier, and filename-noise removal that
  preserves meaningful subtitles and independently supported structured
  series/order sibling facts, paired with unsupported inverse additions that
  policy rejects;
- deterministic title cleanup and degradation outcomes that never route to AI
  `manual`;
- decisive single-family evidence with supported identity, direct
  authoritative exact-identifier or content provenance, and edition
  applicability, plus controls where those conditions are absent;
- close specifically supported title alternatives that route to AI `manual`;
- current-equivalent title candidates that produce no proposal;
- conflicting work identity, plus edition or comparable-identifier conflicts
  that reject the candidate when relevant to the field;
- raw identifier observations with validated aliases, canonical comparable ISBN
  forms, compatible-namespace agreement and conflict, and non-comparable
  namespace neutrality;
- malformed or invalid current identifiers resolved under ordinary applicable
  field evidence;
- validated current comparable identifiers with an applicable embedded/content
  identifier and a materially independent provider identifier that agree on the
  same validated canonical replacement identity, plus disagreement, missing-
  family, correlated-lineage, and inapplicable controls;
- one distinct checksum-valid ISBN with typed format- and edition-applicable
  embedded/content provenance, a canonically equivalent ISBN-10 or ISBN-13 from
  a materially independent format-appropriate provider, and agreeing target,
  work, and media identity, plus multiple-content-ISBN, unrelated-preview,
  other-work, correlated-provider, edition, format, narrator, abridgement,
  dramatization, omnibus, cross-media, and spoken-ISBN-only controls;
- current metadata that is outweighed by applicable evidence rather than
  treated as privileged truth;
- multi-field cases proving that resolution of one field preserves title,
  identifier, series, cover, and other sibling proposals;
- provider timeout and failure outcomes that remain operational rather than
  becoming negative evidence;
- prompt injection embedded in every untrusted text field;
- repeated direct and projected candidates for one target;
- qualified versioned series-label aliases with raw lineage, replay and
  supersession, unknown-label preservation, collision and many-to-one cases,
  wrong-work-identity, correlated-lineage non-consensus, stale-rule, and
  out-of-scope mapping controls;
- projected shared evidence carrying a source AI recommendation, with controls
  proving it adds no evidence family, consensus, destination disposition,
  human decision, or authority and that the destination requalifies it;
- evidence-only observations that must not create queue cases;
- agreeing sources, rejected alternatives, no-ops, unresolved replacements,
  and incompatible media fields;
- independent ebook/audiobook sibling cases plus same-title non-identity
  controls;
- explicit automatic, human, stale, unsupported, and destructive review paths;
- checkpointed review runs with missing, duplicate, stale, terminal-no-result,
  degraded optional, typed-blocker, and exact-coverage stage outcomes;
- AI completion cases with independently audited proposal IDs, decisions,
  reasons, required `problems` lists, selected targets, and authoritative
  upstream input outcomes, including required-input blockers and invalid
  claimed-complete artifacts;
- direct closed-schema reviewer objects with clean approvals carrying
  `problems: []`, plus duplicate JSON keys, trailing content, unknown fields,
  missing required fields, unknown and duplicate proposal IDs, partial coverage,
  malformed values, backend-envelope normalization, and controls proving no
  semantic repair or JSON-inside-string fallback occurs;
- authoritative packet joins with the exact bounded normalized current snapshot,
  explicit loaded, missing, and unavailable field states, relevant evidence,
  candidate and policy versions, and completeness provenance, including
  policy-qualified cross-target evidence, plus pre-invocation failures for
  missing, duplicate, unauthorized or target-incompatible cross-target
  evidence (including consuming-target mismatches and absent explicit
  relationships, immutable policy versions, or qualifying rules), stale, required-
  context-truncated, raw-row, or otherwise unjoinable context;
- capability-derived candidate manifests proving every derived lane records
  applicability, applicable lanes record typed execution outcome, and only
  successfully completed applicable lanes record candidate count and `executed`
  or compatibility-validated `reused` mode; cases include completed applicable
  zero-candidate lanes, not-applicable reasons, missing-lane coverage failure,
  failed, stale, and partial execution, exact target and input fingerprints,
  compatible provider, query, configuration, and contract versions, validated
  expected artifacts, and bounded retry, backoff, and suppression proving
  failures are never reused;
- identical normalized candidates with separate agreeing and contradicting
  source assessments, stale risks, and lineage, proving deterministic aggregate
  recomputation without risk union or strongest-row conflict erasure;
- zero-proposal selection that records a terminal orchestration outcome without
  reviewer invocation, plus invoked packets with exact per-proposal coverage;
- AI `manual` results with multiple specifically supported alternatives, plus
  controls proving that `manual` may complete reviewer output but creates
  neither a human decision nor apply readiness;
- typed run-local reviewer intent within fixed scope and controls rejecting
  policy, safety, authority, destructive, schema, or candidate overrides,
  free-form instruction smuggling, and JSON encoded inside string fields;
- review summaries where targets, ordinary and destructive cases, field work
  items, and planned changes deliberately differ, including partial and stale
  results that cannot claim complete current totals;
- service payloads from supported compatibility versions, irreversibly
  sanitized or generated from schema.

### Synthetic metadata chaos corpus

Maintain an independently authored, licence-recorded corpus of deliberately
messy but synthetic library observations. It complements adapter fixtures and
DUX-derived scenarios without copying source text, formatting, or personal
library facts. At minimum it covers:

- title and author fields swapped or reversed in paths;
- narrator names presented as authors and ambiguous contributor roles;
- initial-only, collapsed-initial, particle, suffix, pseudonym, collective,
  and multi-author names;
- generic, numeric, very short, Unicode, translated, and same-title/different-
  author works;
- release prefixes, encoding labels, years, sequence markers, and other path
  debris that must not erase meaningful title text;
- missing metadata, conflicting identifiers, wrong series membership or
  sequence, and plausible candidates for different editions;
- repeated, correlated, malformed, oversized, redirected, timed-out, and
  rate-limited provider responses; and
- duplicate events, persistent failure redelivery, worker death, and restart
  scenarios capable of producing retry storms.

Each production defect becomes a minimized synthetic regression when it
reveals a reusable behavior. The regression records the violated contract and
failure class rather than copying a user's metadata or preserving incidental
implementation output.

Fixture licences and provenance must be documented.

Directional title and identifier cohorts are independently authored from
generic token classes and generated identifier shapes. They contain no titles,
identifiers, paths, or other observations taken from the prototype. Metamorphic
variants add and remove synthetic noise, permute independent source order,
substitute identifier aliases, vary ISBN presentation, and add unrelated sibling
proposals while asserting unchanged policy outcomes where the contract says
those transformations are neutral.

Named generic cohorts include compact series-prefix removal, title-appended
series/order removal with separate structured capture, and clean-title to
year/source-suffix addition rejection. Each series/order-removal cohort includes
controls proving that an unchanged pre-existing field is insufficient without
independent support and a persisted or newly emitted preserved sibling fact.

DUX-derived scenarios cite the frozen source revision and follow the fixture,
licence, and rejection rules in the
[DUX extraction record](dux-extraction.md). Golden files preserve behavior, not
prototype HTML or browser formatting.

Post-DUX operational scenarios additionally cite the applicable full commit
from the separate baseline in that record. Tests preserve the stage-coverage,
evidence-independence, and completion-audit contracts, not the prototype's
agent controller, static dashboard, service topology, or scoring weights.

JSONL interoperability tests delimit records only with the artifact contract's
physical CRLF or LF separators. Fixtures include escaped newlines, literal
Unicode U+2028 and U+2029 within JSON strings, a missing final newline, blank
and malformed physical records, a bare carriage return rejected as a delimiter,
oversize records, and the artifact contract's declared invalid-UTF-8 and BOM
policy, with bounded recovery or rejection. Broad host-language `splitlines()`
behavior is not the parser contract. AI reviewer output remains one direct
schema object, not JSONL.

## Determinism

- Time, random IDs, network, and provider responses use injected fakes.
- Golden files represent stable public contracts, not implementation formatting.
- AI behavior in CI uses deterministic fake outputs. Hosted model evaluations
  run separately and cannot make ordinary CI flaky.
- Tests must not depend on execution order or the contributor's timezone,
  locale, home directory, media library, or network.

## Migration tests

For every supported source schema:

- create or restore the old fixture;
- migrate to current;
- validate constraints and data semantics;
- run migration again to prove expected behavior;
- start the current application;
- test documented backup and downgrade/restore handling.

## Failure injection

Provide deterministic controls for connection loss, timeout, malformed JSON,
rate limit, disk-full, read-only volume, expired authentication, worker death,
database lock, duplicate event, persistent failure redelivery, changing
fingerprint, and verification mismatch. Recovery assertions prove that
deduplication and backoff survive restart and bound both retries and external
calls.

Future opening-audio tests additionally inject decoder failure, unsupported
codec, model-load failure, transcription timeout, temporary-space exhaustion,
stale source fingerprints, hosted quota failure, cancellation, and orphaned
temporary samples. They assert that ordinary non-transcription evidence work
continues and no transcript outcome becomes direct write authority.

EPUB extraction tests additionally inject archive traversal, entity expansion,
compression bombs, oversized and deeply nested documents, invalid encodings,
duplicate identifiers, broken spines, missing semantics, image-only front
matter, encrypted resources, unrelated previews, hostile instructions, and
concurrent source changes. They assert bounded reads, exact provenance,
same-file correlation, typed failure, and continued ingestion.
