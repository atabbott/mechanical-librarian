# Application service contracts

## Current boundary

The installed runtime owns bounded evidence observation and publication,
catalogue refresh and inspection, deterministic ProposalSet creation,
exact-current ReviewCase admission, deterministic decision/outcome persistence,
and authenticated review operations. Its cover-consideration service may retain
one locally admitted, closed visual observation or typed observer failure as
advisory evidence. It has no generic model-review application service, reviewer
queue, raw model-output persistence, or model-selected write authority.

Each contract exchanges typed, bounded application or domain values. Vendor
payloads, transport clients, SQLite rows, credential bytes, and direct
filesystem operations remain implementation-side; the composition root selects
concrete adapters. The domain does not depend on them.

The authenticated browser consumes one pre-composed `ReviewApplication`, not a
repository. Its named operations own sessions, current ReviewCase inspection,
human decisions, Library and Attention projections, policy, preparation,
Activity, field effects, recovery, and support. Narrow application-owned store
protocols describe only the persistence each operation needs. A shared
application gate serializes access, and the concrete SQLite adapter remains the
sole owner of transactions and cross-capability atomicity.

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

The repository workbench and installed cover-consideration service share the
same `contained-reviewer` adapter. Its narrow contract accepts one sanitized canonical JSON input, bounded
instruction, and closed output schema, then returns one locally schema-validated
canonical JSON object or a typed abstention/failure. The installed caller adds
only report-bound PNG preview descriptors and persists only locally admitted
closed observation/failure evidence. The adapter receives no target or provider
credentials and has no decision or write authority.

## Authority limits

The contained reviewer remains experimental and advisory. Its isolated adapter
binds private artifacts and credentials, disables tools and write authority,
durably gates process release behind the caller callback, bounds every input and
output, validates the returned object locally, and proves process-group death on
timeout or cancellation. Authentication actions remain adapter-owned and create
no product lifecycle.

The installed cover caller may request an explicit retry only while advice is
failed or unavailable and before an owner outcome; it creates a superseding
immutable consideration rather than a generic reviewer workflow. These contracts
grant no extra target/media write, approval, apply, generic provider framework,
reviewer queue, cache, release, or production-service authority. Current operations and compatibility are defined
by the [operations guides](../operations/metadata-report.md), [compatibility
contract](../reference/compatibility.md), and [security model](security.md).
