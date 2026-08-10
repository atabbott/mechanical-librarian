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
cross-case bibliographic history, bulk and reversal remain #195.

Continuous guarded application adds authenticated `GET /api/v1/activity`,
CSRF-protected library-envelope consent and revocation, existing pause/resume,
and an exact field `return-to-automatic` command. Activity exposes bounded
counts, configured/consented library identity, bounded per-destination readiness
for the existing 14 destinations, a value-limited representative
example, plan events, and typed suspensions. It does not expose credentials,
cover bytes, unrestricted paths, an arbitrary target, or a generic queue/apply
command. Consent is restricted to configured libraries and the existing 14
destinations; return-to-automatic requests a fresh scan and does not delete an
immutable human decision.

Progressive preparation adds authenticated
`GET /api/v1/review-preparation` and CSRF-protected
`POST /api/v1/review-preparation/{connection}/{library_name}/scan`. The first
returns only configured library identity, opaque scan identity, closed state,
counts, and coalescing state. The second accepts no metadata values or arbitrary
scope and returns `202` only after the configured-library request is persisted.
Unknown libraries return `404`; unavailable persistence returns `503`.
Preparation creates no field decision and has no apply or target-write route.

Metadata-policy Preview adds four owner-authenticated operations:

- `GET /api/v1/metadata-policy/shadow` returns the closed `review | preview`
  posture and literal authority `none`;
- CSRF-protected `POST /api/v1/metadata-policy/shadow/enter` accepts no body and
  enters Preview without changing human work;
- `GET /api/v1/metadata-policy/shadow/leave-preview` returns per-library impact
  with `can_confirm: false`; and
- `GET /api/v1/metadata-policy/shadow/outcomes?limit=1..50` returns a bounded,
  one-MiB maximum sample of closed identity-rich outcome documents.

While posture is `preview`, the existing policy validate, draft-create,
preview, and activate operations accept Automatic as non-authoritative intent.
In `review`, the first three reject it with `422
policy_automatic_unqualified`; persistence also rechecks posture atomically.
Automatic remains absent from qualified authority.

There is no leave commit, cursor, automatic-decision, queue, apply, target, or
write operation. Shadow history events are a separate typed variant from human
decision events. Malformed persistence fails the complete
request with a value-free unavailable response.

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

Search uses locale-independent Unicode `casefold()` on both the bounded query
and the stored title, library, and item projections before literal substring
matching. SQL wildcard characters in the query remain escaped. The browser
preserves the selected library lens when submitting a search, while the
attention search remains on its separate route. Cursor offsets are capped at
100,000; a response fails before it can emit a cursor that its decoder would
reject.

The current-case evidence resource exposes every reference used by that exact
case through deterministic, case-bound cursor pages of at most 50 items. Cover
references are keyed by their serialized `source_id`; provider-backed claims
and rich evidence carry their enclosing provider and candidate identities.
Only the already-persisted bounded projection is returned—there is no provider
call, raw credential, unrestricted source dump, or library-wide evidence
browser. The bundled detail page loads additional pages only on an explicit
“Load more evidence” action and renders every value as text.

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

If the same idempotency key and exact payload already committed, its immutable
acknowledgement is returned before current-case supersession is considered.
The lookup uses only the stored key digest and canonical decision document;
the raw key is not persisted. Reusing the key for a changed payload remains an
explicit conflict, and an absent replay continues through the ordinary
exact-current validation and atomic commit path.

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

The guarded fake-effect service adds authenticated status plus CSRF-protected
apply, reconcile, pause, and resume operations under `/api/v1/field-effects`.
Ambiguity already schedules a persisted targeted read; the reconcile route
only resumes that same idempotent action after interruption and does not create
an independent or blind retry.
Callers supply only an existing deterministic plan identity; they cannot post
authority, a desired transition, target credentials, cover bytes, or an
arbitrary destination. Responses are the bounded canonical operation
projection. The routes return unavailable until the fake-effect application
service is composed; when composed, they call that same service used by
application callers.

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
## Operational API

`GET /health/live` and `GET /health/ready` return fixed anonymous text.
Authenticated `GET /api/v1/operations/status` returns typed value-free
`operational-status-v1`. Authenticated support preview and CSRF-bound export use
`/api/v1/operations/support/preview` and `/api/v1/operations/support/export`;
both are private, bounded and no-store under the existing response boundary.
