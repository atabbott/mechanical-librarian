# ADR-0004: Treat AI as an Untrusted Reviewer

- Status: accepted
- Date: 2026-07-10
- Owners: project maintainers
- Supersedes: none
- Superseded by: none

## Context

AI review is valuable for resolving noisy metadata and visual cover choices,
but inputs may contain prompt injection and model output is nondeterministic.
The curator holds credentials capable of changing a production library.

## Decision

AI backends receive only staged, bounded evidence and return closed-schema
recommendations. They never receive service credentials, direct production
media access, policy control, database access, or write authority.

Deterministic code validates reviewer output, calculates final disposition,
constructs apply plans, invokes service APIs, and verifies results.

## Consequences

### Positive

- Prompt injection cannot directly become production authority.
- AI backends remain replaceable.
- Decisions are reproducible enough to evaluate by model, prompt, and policy
  version.
- Service credentials are not exposed to hosted model execution.

### Negative

- Internal isolation is more complex in a single container.
- Reviewer output needs strict schemas and validation.
- Some agentic convenience is intentionally unavailable.

### Neutral or deferred

- Better models may improve decisions but do not relax the trust boundary.

## Validation

- Container tests prove the reviewer cannot read service secrets, media mounts,
  the main database, or backup paths.
- Hostile evidence and malformed output cannot create an apply plan.
- Production writes succeed when the reviewer process is completely absent by
  using previously validated human decisions or deterministic policy inputs.
