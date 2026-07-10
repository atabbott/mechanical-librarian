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
- service payloads from supported compatibility versions, irreversibly
  sanitized or generated from schema.

Fixture licences and provenance must be documented.

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
database lock, duplicate event, changing fingerprint, and verification
mismatch.
