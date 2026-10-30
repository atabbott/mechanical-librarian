# ADR-0044: Activate one persistent authenticated React workspace

Status: Accepted

## Context

ADR-0043 established an exact, packaged React platform without changing the
delivered authenticated routes. The five workspace documents still used a
server-rendered Python frontend with embedded CSS and JavaScript, full-document
navigation, and route-specific request coordination. Keeping that frontend
beside the packaged shell would leave two browser architectures and make the
currentness, cancellation, focus, polling, and recovery rules diverge.

Issue #496 requires one atomic cutover while preserving the completed 0.13
application and security authority. It does not authorize interface-fidelity
repair, new product behavior, broader mutation authority, or a Node runtime.

## Decision

Serve `/library`, `/attention`, `/review/{review_case_id}`, `/policy`, and
`/activity` through one packaged React document and persistent TanStack Router
shell. Keep `/login` as the only server-rendered page. FastAPI authenticates
every direct document entry, rotates one CSRF token into the private/no-store
shell, validates the exact packaged build, and serves only manifest-admitted
hashed assets. The client removes the CSRF token and build identity from the
DOM at startup and retains them only in memory.

TanStack Router owns canonical route and durable search state. TanStack Query
owns server-state freshness, cancellation, polling, invalidation, and teardown.
Critical route data establishes a destination; independent evidence, history,
preparation, policy posture, and operational panels load progressively behind
local pending and error boundaries. React owns only drafts, disclosure state,
focus intent, and pending controls. Navigation commits one ready destination,
updates the title and live announcement, and restores meaningful focus without
persisting private browser state.

The handwritten HTTP boundary remains the only browser transport seam. It
maps bounded responses, treats authentication expiry as document navigation,
renews CSRF once after a safe 403 challenge but requires an explicit user retry
instead of resending the mutation, and maps stale/currentness conflicts to
named recovery. Unsafe requests carry the exact
`x-ml-build-identity` value. FastAPI rejects a missing or mismatched identity
before invoking application authority, so an open page from another build
cannot start a mutation. Mutations do not retry automatically and never claim
optimistic success; success or ambiguity is reconciled from authoritative
server state, with idempotency retained only where the existing command owns it.

Delete the authenticated embedded HTML, CSS, JavaScript, legacy asset routes,
and their representation-specific tests in the same activation. Preserve a
small login-only server-rendered asset. The temporary Accept/Keep/Reject
wording remains local to the review-detail route and does not become a shared
decision vocabulary.

## Consequences

Authenticated internal navigation no longer reloads the document, while every
existing URL remains directly addressable. FastAPI remains the sole server,
authentication, currentness, policy, effect, persistence, and mutation
authority. The restrictive self-only CSP, private/no-store caching, exact
Origin and Host checks, secure cookie, CSRF, idempotency, optimistic
concurrency, and hostile-text rendering boundaries remain in force.
Amended 2026-10-14 (#1193): content-hashed review bundles and digest-addressed
cover previews are now cached `private, max-age=31536000, immutable`; every
other response keeps `no-store`.
Amended 2026-10-22 (#1018): after the one CSRF renewal, a request that carries
an idempotency key or is side-effect free is sent once more instead of waiting
for an explicit retry; every other unsafe request still requires one, and the
owner is told the session was refreshed.

A corrupt or mismatched installed bundle produces one stable owner-rebuild
state and never an automatic reload loop. Missing chunks permit at most one
controlled document reload for the current build. Node, npm, source maps,
frontend source, generated DTOs, and browser storage remain absent from the
runtime contract.

Known Library, Attention, detail, Policy, and Activity interface-fidelity
findings stay deferred to 0.14.1. Activation tests characterize the safety and
functional envelope, not the legacy presentation as a desired design.

## Validation

- Direct authenticated entry to every route plus no-refresh navigation,
  back/forward, durable URL state, focus, title, announcement, and scroll.
- Critical and progressive loading, cancellation, staleness, invalidation,
  polling teardown, authentication expiry, CSRF renewal, stale conflicts,
  ambiguous mutations, build mismatch, and missing-chunk recovery.
- Hostile metadata, self-only CSP, private caching, browser-storage absence,
  desktop and 390-pixel light/dark layouts, and clean console/page-error runs.
- Exact installed wheel and container proofs for the activated shell, hashed
  assets, build identity, and exclusion of the deleted frontend and build tools.

## Later evolution

Milestone 0.14.1 restored the five authenticated surfaces without changing this
architecture or authority boundary. Its protected surface ownership and
cumulative source, wheel, container, responsive, accessibility, and interaction
qualification are recorded in the
[0.14.1 interface-fidelity reconciliation](https://github.com/atabbott/mechanical-librarian/blob/858789a80217ccfc69c279ad42efe25cf286a9f4/docs/development/0-14-1-interface-fidelity.md).
