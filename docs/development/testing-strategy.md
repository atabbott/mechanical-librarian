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
- bounded synthetic audio tags and chapter data;
- ambiguous title/author/series cases;
- omnibus, abridged, dramatized, summary, and alternate-edition conflicts;
- missing and low-quality synthetic covers;
- duplicate provider results and redirects;
- prompt injection embedded in every untrusted text field;
- repeated direct and projected candidates for one target;
- evidence-only observations that must not create queue cases;
- agreeing sources, rejected alternatives, no-ops, unresolved replacements,
  and incompatible media fields;
- independent ebook/audiobook sibling cases plus same-title non-identity
  controls;
- explicit automatic, human, stale, unsupported, and destructive review paths;
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
