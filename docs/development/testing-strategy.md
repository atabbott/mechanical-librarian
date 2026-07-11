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
- idempotent event handling;
- apply-plan merge associativity where defined;
- no destructive auto-apply disposition;
- stale fingerprints never pass apply validation;
- serialization round trips;
- path and URL safety.

### Contract tests

Every adapter implementation runs a shared suite covering capabilities,
authentication failure, timeouts, rate limits, malformed responses, unsupported
fields, idempotency, and redaction.

Audiobookshelf tests use a fake HTTP and Socket.io service with synthetic item
payloads. Live service tests are opt-in, report-only, and never part of ordinary
CI.

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
Typed terminal no-result and degraded optional outcomes count toward coverage.
Every blocker identifies the required scope, stage, target, and missing or
failed terminal outcome without pretending that an artifact or proposal exists.

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
- repeated and projected records from one evidence family that must not count
  as independent agreement;
- identity-backed title cleanup with supported target and work identity, plus
  edition identity or applicability when the field policy or proposed change is
  edition-sensitive;
- decisive single-family evidence with supported identity, direct
  authoritative exact-identifier or content provenance, and edition
  applicability, plus controls where those conditions are absent;
- close specifically supported title alternatives that route to AI `manual`;
- current-equivalent title candidates that produce no proposal;
- conflicting work identity, plus edition or comparable-identifier conflicts
  that reject the candidate when relevant to the field;
- provider timeout and failure outcomes that remain operational rather than
  becoming negative evidence;
- prompt injection embedded in every untrusted text field;
- repeated direct and projected candidates for one target;
- evidence-only observations that must not create queue cases;
- agreeing sources, rejected alternatives, no-ops, unresolved replacements,
  and incompatible media fields;
- independent ebook/audiobook sibling cases plus same-title non-identity
  controls;
- explicit automatic, human, stale, unsupported, and destructive review paths;
- checkpointed review runs with missing, duplicate, stale, terminal-no-result,
  degraded optional, typed-blocker, and exact-coverage stage outcomes;
- AI completion cases with independently audited proposal IDs, decisions,
  reasons, problem lists, selected targets, and authoritative upstream input
  outcomes, including required-input blockers and invalid claimed-complete
  artifacts;
- zero-proposal selection that records a terminal orchestration outcome without
  reviewer invocation, plus invoked packets with exact per-proposal coverage;
- AI `manual` results with multiple specifically supported alternatives, plus
  controls proving that `manual` may complete reviewer output but creates
  neither a human decision nor apply readiness;
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

DUX-derived scenarios cite the frozen source revision and follow the fixture,
licence, and rejection rules in the
[DUX extraction record](dux-extraction.md). Golden files preserve behavior, not
prototype HTML or browser formatting.

Post-DUX operational scenarios additionally cite the applicable full commit
from the separate baseline in that record. Tests preserve the stage-coverage,
evidence-independence, and completion-audit contracts, not the prototype's
agent controller, static dashboard, service topology, or scoring weights.

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
