# HTTP API Contract

## Purpose

The HTTP API is the stable boundary between the curator core, the bundled
dashboard, automation clients, and optional future hosted frontends. UI state
must not become the authoritative workflow state.

## Principles

- Version public routes from the first release.
- Generate and publish an OpenAPI document from the same typed contracts used
  by handlers.
- Keep domain and application behavior out of HTTP handlers.
- Use stable machine codes in addition to human-readable error messages.
- Make mutating requests idempotent or guarded by explicit preconditions.
- Never expose secrets, raw authentication caches, unrestricted paths, or
  copyrighted evidence excerpts through general list endpoints.
- Treat API compatibility as a release contract with migration guidance.

## Resource families

The expected API surface includes:

- instance setup, configuration shape, and capability status;
- libraries, items, and immutable snapshots;
- evidence summaries and artifact references;
- candidates and field proposals;
- current review cases and historical decisions;
- apply plans, attempts, verification, and rollback status;
- jobs, run history, circuit breakers, and health;
- backup readiness and retention status;
- authentication and reviewer connection status.

## Review resources

The delivered #447–#450 surface is intentionally smaller than the future
resource family below: authenticated `/api/v1` exact-current case detail,
bounded evidence, bounded current-case admission history, session
login/logout, an attention-first current-case library, and a separate exact
field queue, plus one exact-version individual human decision. Library-wide
activity/history, cross-case summaries, bulk and reversal remain #195.

The bundled browser obtains a freshly rotated per-page CSRF token from its
authenticated private detail document, removes it from the DOM at startup, and
holds it only in page memory. It submits an exact server-presented decision and
advances only after the typed acknowledgement. This presentation seam adds no
API operation and no browser-side workflow authority.

The generated contract is frozen at
`docs/reference/review-openapi-v1.json`. Regenerate it from the same closed
handler response models with:

```sh
uv run python scripts/generate_review_openapi.py
```

Tests compare the canonical generated document with that artifact so response
or privacy drift fails before merge.

The API exposes server-authoritative review projections suitable for the
bundled interface and automation clients:

- a cursor-paginated active-case collection with review-state, library,
  media-type, change-type, notice, and advanced source filters;
- immutable historical cases and supersession links;
- one case-detail resource containing field choices, typed cover/series
  choices, applicable decision state, and bounded evidence summaries;
- confirmed sibling-group projections whose member decisions remain
  independent;
- evidence-detail resources loaded separately from case collections;
- aggregate review counts and adapter-separated planned-change summaries; and
- automatic decision history suitable for audit and quality sampling.

List responses never embed the complete evidence store, unrestricted source
rows, full-size covers, or a whole-library review payload. Stable sort keys and
cursor semantics keep confirmed siblings adjacent without treating title
similarity as identity.

The implemented library accepts the explicit `all`, `needs_attention`, and
`no_attention_needed` lenses plus a bounded title/library/item search. It
returns at most 50 works. The separate attention response returns at most 50
exact-current fields whose deterministic result remains unresolved or
explicitly requires manual comparison. Its cursor is bound to the query and
lens, and a queue link identifies both the exact case and destination. Policy
resolved, ineligible, rejected, disproven, and no-op fields never inflate the
queue or its counts.

Representations expose AI recommendation, deterministic disposition, human
decision, stale state, and apply readiness as separate fields. An omitted
human decision must not be inferred from an AI or source recommendation.

## Review mutations

An individual decision mutation supplies:

- review-case and proposal-set versions;
- expected item fingerprint;
- explicit field, cover, and series selections;
- action and optional bounded notes; and
- an idempotency key.

The implemented #449 operation is
`POST /api/v1/review-cases/{review_case_id}/decisions`. It accepts only
`accept`, `keep_current`, or `reject`; `accept` binds one exact value already
presented by the server. The acknowledgement and current-case audit event are
committed in one exact-current transaction. The operation has no automatic,
apply, target-client, target-write, bulk, or reversal authority.

The server validates selections, target applicability, stale state, capability,
and the permitted state transition. Clients post an action, not an arbitrary
desired lifecycle state.

Bulk review uses separate preview and commit operations. Preview returns an
opaque preview identity, exact case/proposal versions, eligible counts, adapter
and field totals, and every exclusion reason. Commit supplies that preview
identity and an idempotency key; any changed case fails safely instead of
widening the batch. A committed batch creates individual decisions and one
batch audit record.

Reversing an unapplied human decision creates a compensating decision event.
Once apply begins, recovery uses apply-plan cancellation, verification, and
rollback resources rather than a review-interface undo operation.

## Mutation guards

Mutating endpoints require the narrowest applicable combination of:

- authenticated owner session;
- CSRF protection for browser sessions;
- expected resource version or fingerprint;
- idempotency key;
- explicit action capability;
- typed confirmation for destructive operations;
- server-side policy and state-transition validation.

A client cannot force an invalid transition by posting a desired state name.

## Errors

Errors use a stable envelope:

```json
{
  "error": {
    "code": "item_snapshot_stale",
    "message": "The item changed after this proposal was reviewed.",
    "correlation_id": "opaque-id",
    "details": {}
  }
}
```

`details` contains bounded, non-secret structured context. Stack traces and raw
upstream responses are never returned to ordinary clients.

## Collection behavior

- Cursor pagination for review queues, unbounded history, and job collections.
- Explicit filters with documented semantics.
- Stable sort keys and deterministic tie-breaking.
- Bounded page sizes.
- Summary endpoints for counts instead of forcing clients to download entire
  histories.

The bundled interface may retain non-authoritative display preferences in the
browser. Acknowledged decisions, notes, bulk previews, and workflow state live
on the server and survive refresh, restart, and device changes.

## Long-running work

Requests enqueue durable jobs and return a job reference rather than holding an
HTTP connection through provider, AI, apply, or verification work. The API may
offer server-sent events or WebSocket updates as a convenience, but polling the
job resource remains supported.

Job and detailed-health representations expose typed stage, active adapter,
attempt count, next retry eligibility, last failure class, and applicable
circuit-breaker or automatic-suspension reason. They never infer that a
provider result is absent from an outage, timeout, authentication failure, or
malformed response. Human-readable activity text is presentation data and
cannot be the only durable representation of progress or failure.

## Authentication

The first release supports a private owner account. Authentication design must
still allow future separation of configuration, review, and write permissions.
The private owner boundary uses an explicit Argon2id hash file, opaque
SQLite-backed sessions, durable bounded login throttling, secure HttpOnly
SameSite cookies, CSRF, exact Host/Origin checks, private caching, and proxy
forwarding only from configured direct peers. Password recovery is unsupported.

Health liveness may be unauthenticated and minimal. Detailed health and all
library data require authentication.

## Compatibility

- Additive response fields are allowed within a stable API version when clients
  are required to ignore unknown fields.
- Removing, renaming, or changing field semantics requires a new API version or
  a documented compatibility window.
- Generated clients and schema snapshots run in CI to detect accidental drift.
