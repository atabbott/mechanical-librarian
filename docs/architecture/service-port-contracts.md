# Application service contracts

## Current boundary

The installed runtime owns bounded evidence observation and publication,
catalogue refresh and inspection, deterministic ProposalSet creation,
exact-current ReviewCase admission, deterministic decision/outcome persistence,
and authenticated review operations. It has no model-review application service
or reviewer persistence.

Each contract exchanges typed, bounded application or domain values. Vendor
payloads, transport clients, SQLite rows, credential bytes, and direct
filesystem operations remain implementation-side; the composition root selects
concrete adapters. The domain does not depend on them.

## Contract families

- Evidence reporting reads one selected current item, isolates EPUB inspection,
  records configured provider and cover observations, verifies currentness, and
  publishes one immutable report with report-bound artifacts.
- Catalogue and batch contracts publish or inspect a bounded current catalogue,
  create immutable manifests, and execute selected report work serially.
- Proposal and review contracts reopen immutable evidence by exact ID, create
  deterministic ProposalSets, admit ReviewCases at the exact-current boundary,
  and preserve explicit historical currentness.
- The deterministic decision service consumes the retained source-validated
  `ShadowReviewPacket` and compiled action catalogue. It performs no model or
  provider dispatch and preserves the existing automatic decision/outcome
  contracts.

The repository-only workbench owns a separate `contained-reviewer` caller. Its
narrow source contract accepts one sanitized canonical JSON input, bounded
instruction, and closed output schema, then returns one locally schema-validated
canonical JSON object or a typed abstention/failure. It is not composed into the
installed runtime, does not use application persistence, and receives no target
or provider credentials.

## Authority limits

The contained reviewer is source-only and experimental. Its isolated adapter
binds private artifacts and credentials, disables tools and write authority,
durably gates process release behind the caller callback, bounds every input and
output, validates the returned object locally, and proves process-group death on
timeout or cancellation. Authentication actions remain adapter-owned and create
no product lifecycle.

These contracts grant no extra target/media write, approval, apply, generic
provider framework, reviewer workflow, retry scheduler, cache, release, or
production-service authority. Current operations and compatibility are defined
by the [operations guides](../operations/metadata-report.md), [compatibility
contract](../reference/compatibility.md), and [security model](security.md).
