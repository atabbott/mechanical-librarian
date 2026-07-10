# Repository Structure

## Current and planned layout

```text
.
├── src/mechanical_librarian/
│   ├── domain/
│   ├── application/
│   ├── adapters/
│   │   ├── library_services/
│   │   ├── reviewers/
│   │   ├── providers/
│   │   └── notifications/
│   ├── infrastructure/
│   │   ├── persistence/
│   │   ├── jobs/
│   │   ├── security/
│   │   └── observability/
│   └── interfaces/
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
The initial package supports CPython 3.13 and 3.14, has no runtime dependencies,
and is typed through its `py.typed` marker. Development is expected on Linux
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

Implement application ports for external systems. Adapters may depend on
application and domain contracts; adapters never import one another directly.

### `infrastructure`

Persistence, job execution, configuration, security primitives, logging,
metrics, migrations, and process integration. It wires contracts but does not
own metadata policy.

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
