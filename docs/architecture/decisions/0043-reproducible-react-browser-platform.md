# ADR-0043: Build a reproducible React browser platform

Status: Accepted

## Context

The authenticated browser is currently a server-rendered Python shell with
embedded CSS and JavaScript. Its authority, security, API, and behavior are
stable, but that representation leaves the project owning routing, typed search
state, request cancellation, stale-response suppression, focus restoration,
progressive loading conventions, and server-state coordination.

Issue #495 requires the replacement platform without activating it on the
existing user-visible routes. The build must remain exact and independently
verifiable: no Node runtime belongs in the production image, generated DTOs and
compiled assets are not source, and the exact OpenAPI document is the only DTO
input.

## Decision

Use React 19, strict TypeScript 5, Vite 8, TanStack Router, and TanStack Query.
TanStack Router was selected after the bounded comparison recorded in
[router platform comparison](../../development/router-platform-comparison.md).
It provides the required route-owned typed search state and intent preloading
without adopting a server framework or second runtime. Query is the sole
server-state cache; React owns only ephemeral interface state.

Keep one handwritten HTTP module. It owns session expiry, CSRF renewal,
structured error normalization, cancellation, currentness, and conversion from
generated transport DTOs to view models. `openapi-typescript` generates types
only from the committed exact `review-openapi-v1.json`; it generates no client.

Pin Node 24.19.0 and npm 11.17.0 for every exact build. Frontend source,
generator rules, `package.json`, and `package-lock.json` are committed.
Generated DTOs, `node_modules`, compiled assets, and source maps are not. The
wheel build creates them out of tree, packages only the shell, one canonical
asset manifest, its independent expected build identity, and reachable hashed
scripts/styles. The container builds the same bundle in a pinned Node stage and
copies only validated assets into the Python build stage. Node and npm never
enter the runtime image.

The Python asset loader bounds the manifest and every member, validates exact
paths, sizes, digests, reachability, and the independently packaged build
identity, and converts any missing, stale, corrupt, or mismatched installation
into one stable owner-recovery state that never requests automatic reload.

Keep `/login` and every authenticated user-visible route on the existing
frontend in this change. The new shell is packaged and tested but unactivated;
route cutover belongs only to #496.

## Consequences

The repository gains an exactly pinned development build graph and a larger
wheel containing only browser runtime assets. FastAPI remains the only server,
authentication boundary, data authority, and effect owner. Browser state is
memory-only, hostile metadata is rendered as text, and the existing CSP needs
no inline-script, external-origin, worker, blob, or source-map exception.

Frontend dependency and build qualification become required source and exact
artifact gates. A source checkout can still run Python tests without generated
assets; attempting to activate an absent or mismatched bundle fails into the
stable recovery state.

## Validation

- `npm ci`, DTO generation drift, lint, strict type checking, Vitest/Testing
  Library, deterministic double build, manifest/digest, and advisory checks.
- Unit tests for the handwritten HTTP boundary, typed route/search ownership,
  critical loader, progressive panel, focus/announcement, and recovery state.
- Python tests for bounded manifest loading, corruption, source-map exclusion,
  and build-identity mismatch.
- Exact wheel/container tests prove reachable asset inclusion and the absence
  of Node, npm, `node_modules`, frontend source, generated DTOs, and source maps.
- Existing browser tests prove that no current route or presentation was cut
  over by this decision.
