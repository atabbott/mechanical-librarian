# Browser router platform comparison

Decision date: 2026-08-10

## Comparison boundary

One disposable source tree compared TanStack Router 1.170.25 plus TanStack
Query 5.101.4 with React Router Data Mode 8.3.0 plus the same Query version.
Both used React 19.2.8, TypeScript 5.9.3, Vite 8.2.1, and the exact pinned Node
24.19.0/npm 11.17.0 container. Each prototype contained an authenticated-shell
shape, Library route, detail route, search state, one Query-backed critical
loader consuming `AbortSignal`, one progressively rendered Query panel,
direct-entry browser routing, intent preloading, and project-owned route
announcement/focus. The disposable applications and their dependency tree were
deleted after measurement and are not product source.

## Results

| Measure | TanStack Router | React Router Data Mode |
| --- | ---: | ---: |
| strict `tsc --extendedDiagnostics` total | 0.66 s | 0.38 s |
| measured command elapsed | 0.822 s | 0.494 s |
| TypeScript instantiations | 34,214 | 6,020 |
| TypeScript peak memory | 113,906 KiB | 137,744 KiB |
| Vite build | 0.435 s | 0.518 s |
| emitted files | 2 | 2 |
| emitted bytes (`du -sk`) | 300 KiB | 316 KiB |
| gzip entry chunk | 95.85 kB | 99.19 kB |

Both variants built under the unchanged self-only CSP and required the same
small project-owned focus and announcement effect. Both supported direct entry,
critical loader cancellation, Query ownership, and progressive rendering. The
measured differences are too small to be a failure gate at this scale.

## Decision

Select TanStack Router. Its route-level `validateSearch` made the Library query
state part of the registered route type, and its `Link` immediately required
that search contract during the comparison. React Router Data Mode required
handwritten `URLSearchParams` normalization; its generated route types and
automatic intelligent splitting are primarily Framework Mode capabilities,
which would add machinery outside this application boundary. TanStack also
emitted the smaller prototype chunk and built slightly faster, while its higher
type-instantiation count remained well inside the bounded measurement.

Use narrowed route APIs to contain type work. Critical loaders call
`queryClient.ensureQueryData`; progressive panels use ordinary `useQuery` and
consume the supplied abort signal rather than Suspense queries, whose
cancellation contract is insufficient here. No comparison finding requires the
React Router fallback.

## Reproduction notes

The comparison used the multi-platform image
`node:24.19.0-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03`.
Commands were `tsc -p <variant> --extendedDiagnostics` and
`vite build --config <variant>`. Measurements are selection evidence, not
performance thresholds.
