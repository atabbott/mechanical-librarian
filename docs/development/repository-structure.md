# Repository Structure

## Current and planned layout

```text
.
├── src/mechanical_librarian/
│   ├── domain/
│   ├── application/
│   ├── adapters/
│   │   ├── library_services/
│   │   ├── evidence_extractors/
│   │   ├── reviewers/
│   │   ├── providers/
│   │   ├── notifications/
│   │   ├── persistence/
│   │   └── task_execution/
│   ├── infrastructure/
│   │   ├── persistence/ (connection and migration runtime)
│   │   ├── jobs/
│   │   ├── security/
│   │   └── observability/
│   └── interfaces/
│       ├── configuration.py (strict config-v1 transport boundary)
│       ├── http/
│       └── cli/
├── frontend/
├── migrations/
├── tests/
│   ├── unit/
│   ├── property/
│   ├── contract/
│   ├── integration/
│   ├── end_to_end/
│   ├── container/
│   └── fixtures/
├── docs/
├── packaging/
│   ├── container/
│   └── examples/
├── scripts/
└── pyproject.toml
```

The Python package is named `mechanical_librarian`. Layer meaning and dependency
direction remain independent of packaging decisions.

The tracked scaffold creates production packages only where they establish an
import boundary or executable entrypoint. Empty frontend, migration, packaging,
script, and later test-family directories are added by their owning work rather
than retained with placeholder files.

## Runtime baseline

`pyproject.toml` is the authoritative machine-readable runtime declaration.
The initial package supports CPython 3.13 and 3.14, pins Pydantic v2 for the
strict configuration transport boundary, and is typed through its `py.typed`
marker. Development is expected on Linux
and macOS; automated host qualification belongs to the CI foundation. The
supported production target remains the self-contained Linux container. Native
Windows development and deployment are not yet qualified.

The pre-stable version is exposed by `mechanical_librarian.__version__` and the
installed package metadata from the single `_version.py` source. Release
versioning and build projections are expanded under WP0 issue #42.

## Package responsibilities

### `domain`

Pure types, invariants, policies, state transitions, and events. No I/O and no
imports from other production layers.

### `application`

Use cases and ports. Depends on domain. Defines transactions and orchestration
boundaries without choosing HTTP, SQLite, Audiobookshelf, or an AI provider.

### `adapters`

Implement application ports for external systems and technology boundaries,
including persistence repositories and task execution. Adapters may depend on
application and domain contracts; adapters never import one another directly
and never import infrastructure.

The planned adapter packages preserve role boundaries:

- `library_services` translates supported target APIs into canonical inventory
  and accepts validated mutations without owning metadata policy;
- `evidence_extractors` produces bounded local facts without target/provider
  clients or network access;
- `providers` supplies optional remote evidence without target credentials,
  media access, or write ports;
- `reviewers` consumes staged canonical packets and returns schema-constrained
  recommendations without discovering evidence or authorizing writes; and
- deterministic candidate sufficiency, aggregation, and disposition remain in
  domain/application code rather than any adapter.

Adapter-specific payload models and vendor semantic types stay inside the owning
adapter. A raw external item value may cross only as an uninterpreted
`ExternalItemId` inside its fully adapter/connection/library-scoped canonical
`TargetId`, never as an unscoped identifier. Application ports expose vendor-
neutral target, snapshot, evidence, candidate, proposal, reviewer-packet,
decision, and apply-plan schemas. A CWA-shaped implementation under test
fixtures may exercise the common target-service port, but no production CWA
adapter package is created until supported-API compatibility and delivery work
authorize it.

The application-owned request, result, failure, compatibility, and evolution
rules are defined in [application service-port contracts](../architecture/service-port-contracts.md).

### `infrastructure`

Process-wide configuration, connection and migration runtime, security
primitives, logging, metrics, and process integration. Infrastructure does not
implement or import application ports, does not own metadata policy, and does
not import adapters. Bootstrap supplies explicit constructor boundaries when an
adapter needs an infrastructure-owned runtime resource. Infrastructure may
import non-port application contracts, such as configuration validation, but
the static boundary check rejects the application modules that define ports
(`application.audit` and `application.ports`).

### `interfaces`

HTTP and CLI translation. Interfaces validate transport shape, authenticate,
invoke application use cases, and translate results. They do not query adapter
internals or the database directly.

### `frontend`

Bundled review and operator interface. It consumes the public HTTP API and
contains no authoritative decision or apply state. Its queue, detail, evidence,
bulk, responsive, accessibility, and performance behavior follows the
[review-experience contract](../product/review-experience.md).

## Enforcement

A static import-boundary test is required from the first code scaffold. The
test should fail on:

- domain imports from application, adapters, infrastructure, or interfaces;
- application imports from concrete adapters or interfaces;
- adapter-to-adapter imports;
- adapter-to-infrastructure or infrastructure-to-adapter imports;
- infrastructure imports of application ports or implementations of them;
- interface access to concrete persistence repositories;
- production code importing test fixtures or scripts.

The scaffold enforces these rules with a standard-library AST test in
`tests/architecture`. Issue #37 owns the later lint, type, coverage, complexity,
and full static-analysis toolchain; it must extend rather than weaken this
first boundary gate.

## Scripts

Scripts are thin developer or release entrypoints. Product workflows belong in
application services and must not exist only as shell or ad hoc Python scripts.
Every retained script declares purpose, inputs, outputs, side effects, and
replacement path when deprecated.
