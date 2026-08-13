# ADR-0050: Bound installed owner assistance

- Status: accepted
- Date: 2026-08-13
- Owners: project maintainers
- Supersedes: ADR-0040 installed-command restriction; ADR-0041 runtime,
  packaging, and import exclusions for the cover observer only
- Superseded by: none

## Context

ADR-0040 reduced the installed launcher to `review-serve` and moved generic
contributor operations into the source-only workbench. ADR-0041 similarly kept
the contracted contained-reviewer kernel out of runtime composition and the
installed artifact. Later accepted product decisions made two narrow parts of
those restrictions obsolete: ADR-0048 established a shared installed policy
preview, and ADR-0049 activated owner-confirmed cover consideration, history,
advice, and guarded application through existing product seams.

Leaving the older restrictions apparently current makes the installed artifact
and bootstrap simultaneously required and forbidden. The exception must be
closed around the owner product outcome without turning the workbench or the
generic reviewer kernel into an operator surface.

## Decision

The installed command dispatcher exposes exactly `review-serve`, read-only
`policy-preview`, owner-explicit `cover-review`, and read-only
`review-activity`. These commands call the same application operations used by
the authenticated browser. They do not expose catalogue, batch, report,
ProposalSet, ReviewCase, provider-acquisition, generic reviewer, or
qualification scenarios from the source workbench.

The installed bootstrap and artifact may import and package the contracted
contained-reviewer kernel only for ADR-0049's cover-specific observer. That
composition accepts sanitized, bounded, report-bound cover material; requires
the qualified exact 1,073,741,824-byte memory and 32-process/task profile; and
returns typed advisory observations or unavailability. It receives no service
credentials or target-write authority and cannot admit identity, select a
candidate, record an owner outcome, or apply a cover. Every other generic or
experimental contained-reviewer use remains source-only and excluded from the
wheel and container.
The generic invocation and authentication wrapper therefore lives only in the
source workbench; the installed bootstrap exports only the exact cover-packet
entry point.

The application captures the exact current case, report, proposal, and prior
consideration under its serialization gate, releases that gate while the
optional external observer runs, then reacquires it and revalidates every
captured binding before persistence. A stable replay reference binds the
policy, observer profile, instruction, schemas, taxonomy, and classifier into
both current projection and the guarded effect dependency check.

`review-activity` opens only the configured application database and composes
the shared query-only Activity projection. Its CLI accepts only configuration
and database paths. It does not compose or accept catalogue, artifact-store,
target-writer, worker, or recovery-writer authority.
Capability display reads only persisted suspension evidence and otherwise
reports `not_ready`; it performs no credential read or service request.

## Consequences

### Positive

- Governing architecture matches the exact installed product boundary.
- Owner assistance remains one coherent browser/CLI/application outcome.
- The generic workbench and reviewer lifecycle remain excluded.

### Negative

- The installed image includes the cover observer kernel and its narrowly
  qualified process boundary.
- Exact-artifact qualification must freeze four installed commands rather than
  one.

### Neutral or deferred

- This decision adds no automatic model-selected write, bulk reevaluation,
  provider, release, tag, deployment, or production-service qualification.
- The observer remains optional; manual comparison remains supported when it
  is unavailable.

## Alternatives considered

### Keep all assistance source-only

Rejected because it would repeat the 0.15.0 workbench demonstration rather
than deliver the installed owner outcome required by ADR-0049.

### Install the generic workbench or reviewer lifecycle

Rejected because those broader systems are unnecessary for the owner-confirmed
cover outcome and would expand authority and maintenance scope.

## Validation

- Architecture and exact-artifact tests freeze the four-command allowlist and
  continue to reject workbench imports and files.
- Installed wheel and container qualification prove the cover observer's exact
  resource profile, credential isolation, bounded transport, typed degradation,
  and absence of autonomous decision/write authority.
- CLI tests prove `review-activity` shares the Activity projection, performs no
  mutation, and rejects obsolete authority-shaped path flags.
