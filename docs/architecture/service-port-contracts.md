# Application service-port contracts

Application orchestration owns the vendor-neutral contracts in
`mechanical_librarian.application.ports`. Adapters implement those contracts;
they do not introduce transport, framework, database-row, command-line, or
vendor payload types into the application boundary.

The port families are intentionally separate:

- canonical snapshot repositories and the existing append-only audit and
  durable-work repositories;
- target-library reads and independently authorized supported writes;
- bounded local extraction;
- metadata and cover-provider queries;
- staged reviewer packets and structured reviewer decisions;
- role-scoped secret references, backup-readiness checks, audit, and
  notifications; and
- common diagnostics for capabilities, compatibility, versions, and health.

Capability is descriptive, never authority. A supported write requires a
separately issued target-scoped `WriteGrant`. The immutable grant binds the
exact mutation-set digest, `ExpectedState` fingerprint and optional upstream
concurrency token, target-scoped `IdempotencyKey`, decision-authorization and
apply-plan identities, verified matching backup-readiness result, and expiry.
The request revalidates those bindings against injected current time and its
resource bounds during construction and through its canonical
`preflight_result()` guard immediately before adapter I/O. A writer must invoke
that typed guard at the mutation point and return its exact failed `PortResult`
so delayed cancellation, deadline expiry, grant expiry, backup drift, or
binding changes fail before an external write. The raising `preflight()` helper
is construction/static validation only; adapters do not use it for expected
runtime conditions. Replayed operations return a typed receipt;
stale state is a conflict rather than permission to overwrite. A capability
declaration cannot create or substitute for a grant.

Providers receive only an allowlisted, bounded `ProviderQuery`. They do not
receive an `ItemSnapshot`, target identity or credentials, a service endpoint,
media reference, repository, or write port. Reviewers similarly receive a
bounded `ReviewerRequest` containing the staged canonical evidence packet and
exact typed field proposals, including current and candidate context. Each
applicable field has an explicit typed `loaded`, `missing`, or `unavailable`
current-state value. Proposal supporting, contradicting, and risk evidence must
all be present as compatible references in the staged packet. Each
typed recommendation cites known evidence, and the response must cover every
and only supplied proposal exactly once. Neither capability
declarations nor reviewer recommendations can become mutation authorization.
Secret material is not an application value: application code carries only a
redacted reference and a role-scoped opaque handle at the resolver boundary.
Every request that carries both a `TargetId` and `IdempotencyKey` requires their
targets to match; an operation label is not required to equal a work-kind label.
Metadata and cover providers return bounded canonical candidates with opaque
provenance, correlation, and fingerprints; local extractors return canonical
bounded `EvidenceRecord` values rather than raw payloads.
Provider query facts use bounded safe Unicode metadata text, preserving
accented and non-Latin bibliographic values while rejecting controls,
credential-shaped content, absolute/home/traversal/filesystem-URI paths,
duplicates, and size violations.

Provider request and response bounds are independent. Before returning success,
metadata and cover adapters validate the canonical response page's item count
and serialized bytes against the operation context. Oversize pages return typed
`resource_exhausted`; malformed canonical items return `invalid_response`.
Adapters additionally enforce transport response, streaming, redirect, and
download limits before canonicalization. A page cursor never permits aggregate
or per-page work to bypass these bounds.

Every operation context carries an observation-only cancellation probe, an
injected live time probe, a deadline, and item/byte bounds. The separate owner
may change cancellation or time state while an operation is running, but an
adapter receives no cancellation-control method or ambient clock. Transaction
commit and rollback use the same context. All public diagnostics pass through
one projection validator that rejects credential material, cookies,
credential-bearing URLs, and private paths before constructing a `SafeSummary`.
Accepted summary values have redacted representations.
Provider, extractor, reviewer, backup, and write synthetics repeat live
preflight immediately before their simulated external boundary; request
construction alone is not treated as sufficient.
Expected runtime cancellation, deadline expiry, grant expiry, stale state,
unsupported capability, and resource exhaustion are exact typed failed
`PortResult` values. `ValueError` remains reserved for invalid static request
construction; conforming adapters do not leak it for expected operational
preflight outcomes.

## Compatibility and evolution

Port inputs and outputs are immutable application/domain values. Implementers
must treat unknown enum members, fields, schema versions, and capability
combinations as typed `unsupported` or `incompatible` outcomes; they must not
silently infer or discard semantics. Version tokens are opaque compatibility
identities, not values that can be ordered by spelling.

Before changing a contract:

1. add the new immutable value or an explicitly versioned parallel operation;
2. update the reusable synthetic contract harness and every implementing
   adapter suite;
3. retain the old shape while persisted work or a supported adapter can still
   invoke it, or make the incompatibility explicit at composition time;
4. update capability and compatibility declarations; and
5. reject unsupported combinations before external I/O or mutation.

Adding an optional capability is normally compatible when absence preserves
the existing behavior. Renaming/removing fields, changing bounds or retry
meaning, widening reviewer/provider inputs, changing idempotency scope, or
weakening optimistic-concurrency and authorization preconditions is breaking.
Such changes require coordinated contract-version, test, architecture, and—if
persisted state is affected—migration review.

Contract tests live under `tests/contract`. The reusable
`standard_family_contracts()` factory and common harness validate exact
argument counts, argument and return annotations, exact typed results and page
items, and adapter-family isolation. Representative conforming and deliberately
non-conforming synthetics cover repositories and transactions, existing durable
work and scheduling compatibility, diagnostics, target reads and writes, local
extractors, metadata and cover providers, reviewers, secret resolution, backup,
audit, and notifications. Behavior checks cover pagination and empty success,
typed retry/unsupported outcomes, live cancellation and deadlines, resource
bounds, replay, stale-state conflict, grant tampering, reviewer coverage, safe
diagnostics, and malformed adapter output. Only synthetic data is used.
Provider pagination tests traverse real opaque cursor values across pages,
enforce each requested limit, and require termination.
The repository family requires store, read, and list. Transaction management
and transaction execution are separate families: a manager owns only `begin`,
while the returned transaction owns only `commit` and `rollback`. Implementers
do not need to fuse these roles, and the reusable suite rejects accidental
cross-role methods. Durable scheduling includes the full
existing `JobHeartbeatRepository` method vocabulary plus scheduling. Each
registered role is exercised with a distinct plausible nonconformer, while
runtime result checks reject values such as a notifier returning raw text under
an otherwise correct annotation.
The harness requires every method to be owned concretely by the implementation;
inheriting an executable ellipsis body from a `Protocol` is not conformance. It
compares parameter names, positional versus keyword-only kinds, annotations,
defaults, and return annotations. The durable synthetic explicitly implements
and invokes every heartbeat repository method and validates exact typed
repository results, including representative not-found failures.
