# ADR-0030: Use one authenticated Python review web boundary

Status: accepted

## Context

F16a requires a private owner to inspect exact-current review state in a real
browser before any decision or apply authority exists. The selected variant M
prototype is directional product evidence only. Production must keep workflow
state server-authoritative, run as one Python application, reject prior database
schemas, and avoid exposing credentials, paths, target clients, or future write
state. The implementation-stack proof compared a server-rendered bounded HTML,
CSS, and JavaScript surface with a compiled React/Vite client. Both could express
the layout, but #447 has only exact-case reads and no client-side state problem
that forces a framework or build-time Node graph.

## Decision

Use FastAPI and directly pinned Uvicorn in the existing Python composition
root. Serve bounded HTML, CSS, and JavaScript from that process. HTTP handlers
adapt application-owned session and exact-current case queries; they do not own
workflow policy. Use an explicit owner-only Argon2id hash file and opaque
SQLite-backed sessions in the application database. Store only token digests.
Require exact HTTPS Host and Origin, per-session CSRF, private/no-store caching,
restrictive browser headers, and explicit direct-peer proxy trust. Only bounded
liveness and the login resources are anonymous.

The exact-current schema adds only session and login-throttle tables. There is
no migration or compatibility reader. The surface exposes case detail, bounded
evidence, and bounded current-case history. It cannot create a decision,
automatic authority, apply plan, target client, or write. Variant M guides
hierarchy and interaction outcomes without supplying code or exact pixels. Its
library activity view is deferred with cross-case history to #195.

## Consequences

The supported runtime gains one HTTP port and pinned ASGI/password dependencies,
which require security, licence, lockfile, OpenAPI, installed-artifact, and
browser qualification. The small same-origin client stays replaceable because
the versioned application/API boundary owns the behavior. React/Vite remains a
future option only if a concrete later interaction or performance constraint
earns that cost. Password recovery, public signup, OIDC, multi-user/RBAC,
websockets, background work, decision mutation, apply, and target writes remain
unsupported.
