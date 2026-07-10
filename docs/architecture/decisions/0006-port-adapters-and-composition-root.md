# ADR-0006: Separate port adapters from runtime infrastructure

- Status: accepted
- Date: 2026-07-10
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The initial architecture used both `adapters` and `infrastructure` as production
layers. The documents described storage and task execution as adapters in one
place while assigning persistence and jobs to infrastructure elsewhere. That
left package ownership dependent on terminology rather than a testable rule.

The application must remain independent of SQLite, Audiobookshelf, AI backends,
job engines, HTTP frameworks, and process supervision. At the same time, the
runtime needs one privileged location that selects concrete implementations,
loads configuration, and starts interfaces without allowing those dependencies
to leak back into application code.

## Decision

1. The application layer owns ports and use-case contracts.
2. A concrete implementation of an application-owned port belongs under
   `mechanical_librarian.adapters`, regardless of whether the boundary is a
   remote service, local filesystem, database, clock, identifier source, or
   task-execution mechanism.
3. Adapter families include library services, metadata providers, AI reviewers,
   notifications, persistence repositories, and task-execution implementations.
   Adapter families do not import one another.
4. `mechanical_librarian.infrastructure` contains process-wide runtime mechanisms
   that do not implement application ports: configuration-source loading,
   process control, migration execution, security primitives, observability,
   and packaging integration. It owns no metadata or workflow policy.
5. Adapters and infrastructure do not import one another. The composition root
   constructs concrete objects and passes dependencies explicitly.
6. `mechanical_librarian.bootstrap` is the sole composition-root package. It may
   import interfaces, adapters, infrastructure, application, and domain code.
   No ordinary production layer may import `bootstrap`.
7. Executable entry points delegate immediately to `bootstrap`; they do not
   assemble dependencies independently.
8. Versioned migration files remain under the top-level `migrations/` directory.
   Application-facing repository implementations live under
   `adapters/persistence`; migration execution remains runtime infrastructure.

The ownership test is therefore:

- if a module implements or translates an application port, it is an adapter;
- if it starts, configures, observes, secures, migrates, or packages the process
  without implementing an application port, it is infrastructure;
- if it selects concrete implementations and connects them, it is bootstrap.

## Consequences

### Positive

- Persistence and task execution have one unambiguous application-facing owner.
- The application remains independent of concrete technologies.
- The privileged dependency assembly point is explicit and statically testable.
- New integrations can be reviewed against a mechanical ownership rule rather
  than project-specific naming convention.
- Executable entry points cannot silently become additional composition roots.

### Negative

- Some technology-specific code commonly called infrastructure will live under
  `adapters` when it implements an application port.
- Runtime mechanisms and their adapter wrappers may require small explicit
  constructor boundaries rather than importing one another directly.
- The composition root is intentionally coupled to concrete implementations and
  will change when runtime selections change.

### Neutral or deferred

- This decision does not select a dependency-injection framework. Explicit
  construction remains the default.
- This decision does not require dynamic plugin loading.
- Package families should be created only when they contain an executable
  boundary or implementation, not as empty speculative structure.

## Alternatives considered

### Keep persistence and jobs entirely under infrastructure

This matched part of the initial layout but contradicted the rule that adapters
implement application ports. It would continue to require exceptions whenever a
repository or task executor implemented an application-owned contract.

### Eliminate the infrastructure package

All runtime code could be placed in adapters and bootstrap. This is simple but
would blur port implementations with process supervision, configuration-source
loading, observability setup, and release/runtime integration.

### Allow adapters and infrastructure to import one another

This would make implementation convenient initially but create a bidirectional
technical layer with unclear ownership and a high risk of cycles. Explicit
construction in bootstrap keeps the dependency graph directional.

## Validation

- Static architecture tests reject imports of `bootstrap` from domain,
  application, adapters, infrastructure, and interfaces.
- Static tests reject cross-family adapter imports.
- Static tests reject unclassified top-level production packages.
- The package and module entry points resolve through `bootstrap`.
- Repository-structure and architecture documents use the ownership test above.
- Future persistence and task-execution implementations are reviewed against
  shared adapter contract suites.