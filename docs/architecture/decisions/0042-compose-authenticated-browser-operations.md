# ADR-0042: Compose authenticated browser operations before transport

Status: Accepted

## Context

The authenticated browser was assembled inside `create_review_app`. That
factory constructed session, review-case, decision, Library, Attention,
metadata-policy, preparation, field-effect, and operational workflows around a
broad repository contract and a transport-owned serialization wrapper. The
HTTP routes therefore knew application composition and persistence shape even
though SQLite already owned the authoritative cross-capability transaction.

The existing security, currentness, idempotency, exact-schema, and API
contracts are sound and must remain unchanged. The change is a boundary
deepening, not a new workflow or transport redesign.

## Decision

Compose one `ReviewApplication` in the bootstrap layer before FastAPI is
created. It exposes named session, case, decision, Library, Attention, policy,
preparation, Activity, effect, recovery, and support operations. Critical
top-level reads return bounded application view models; independently refreshed
evidence and history remain separate named reads.

Replace the broad handler-facing repository with narrow application-owned
store protocols. One application-owned re-entrant gate serializes operations
that share the state owner. `SQLiteMetadataReportRepository` remains the sole
transaction owner and continues to enforce cross-capability atomicity and
exact-current schema admission. No second repository, transaction coordinator,
migration, compatibility wrapper, or generic resource interface is introduced.

FastAPI owns only authenticated transport concerns: middleware, route
declaration, bounded request/response translation, security dependencies,
typed HTTP failure mapping, and application lifecycle delegation. Generated
OpenAPI remains byte-for-byte deterministic.

## Consequences

HTTP code cannot call persistence or construct workflows. Bootstrap explicitly
wires every capability, and non-HTTP callers can consume the same named
application operations without depending on FastAPI. Mutations retain their
server-authoritative currentness, CSRF, idempotency, effect safety, and typed
failure behavior.

The application boundary is intentionally cohesive rather than a DI container
or family of layer-shaped repositories. New operations must remove real caller
knowledge or repeated authority logic; symmetry alone is not sufficient.

## Validation

- Application-operation tests cover bounded case, Library, Attention, and
  session outcomes without HTTP.
- Transport tests prove the existing route, authentication, CSRF, origin,
  currentness, idempotency, and failure contracts through the composed boundary.
- Architecture tests prove the interface has no broad repository dependency.
- Generated OpenAPI, exact-current schema, browser, restart/lifecycle, and
  exact installed-artifact suites remain unchanged and pass.
