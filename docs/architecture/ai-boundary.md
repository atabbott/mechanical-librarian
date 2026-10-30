# AI trust boundary

An AI model is untrusted
([ADR-0004](decisions/0004-ai-is-untrusted.md)). It never sees a target or
provider credential, never chooses its own inputs, never changes policy,
never builds a service request and never decides or writes a field. Its
output is either advisory evidence or a stored text candidate. Local code
validates it, and only an owner decision or a qualified automatic decision
can use it, through the ordinary guarded write path. Without a reviewer the
manual path is complete.

Deterministic decisions sit outside this boundary. Policy preview, shadow
decisions and automatic decisions make no model or provider call.

## Where models are used

The owner selects a reviewer per task in Settings → Reviewer: the contained
Codex reviewer or an OpenRouter model. Reviewer access is separate authority,
and changing it makes current advice stale while keeping its history
([ADR-0062](decisions/0062-owner-managed-reviewer-access.md)). Setup is in
[deployment](../operations/deployment.md#optional-reviewers).

**Cover observation.** A reviewer looks at report-bound cover previews and
returns a fixed set of visual properties with explicit unknowns, never a
ranking, winner or identity. Identity classification, policy and the owner's
choice stay local, and a missing or failed observation leaves the manual
choice available
([ADR-0049](decisions/0049-owner-confirmed-cover-considerations.md),
[ADR-0050](decisions/0050-bound-installed-owner-assistance.md)).

**Description advice.** A reviewer rewrites the deduplicated provider-backed
descriptions into one description, under the owner's three cleanup choices.
It never receives owner decisions, target-only text, titles, identifiers,
paths or credentials. The reply is one bounded description and `ready` or
`needs_review`, stored as authored content. The owner can select a stored
result by reference. Automatic application is off by default; when the owner
turns it on, only a current `ready` result that passes the description floor
can be admitted, through the normal policy and write rules
([configuration](../reference/configuration.md#description-advice)).

## Request contract

Every request, on either engine, has three parts:

- a pinned, bounded instruction;
- one canonical, sanitised JSON input document of at most 1 MiB;
- a closed output schema of at most 64 KiB from the locally admitted JSON
  Schema subset.

The reply must be exactly one canonical JSON object that validates locally
against that schema; otherwise it is a closed, value-free failure. A
provider's or CLI's structured-output enforcement is never the only check.
Packet text, provider text and images are data, never instructions. Raw
streams, prompts, private paths, credentials and model reasoning never cross
back into the product. A failed observation or description attempt is
retried only on an explicit owner retry.

## Engines

The contained Codex reviewer runs in a sandbox its profile pins
([ADR-0081](decisions/0081-image-owned-reviewer-provisioning.md)); the child
sees one empty job directory, a read-only credential and the request.
OpenRouter egress goes only to one exact model. The mechanics are in
security: [Codex](security.md#contained-codex-reviewer) and
[OpenRouter](security.md#openrouter).

## Contributor workbench

The [workbench](../development/contributor-workbench.md) uses the same
`contained-reviewer` adapter for experiments and persists no product data.

The current reviewer design is
[ADR-0041](decisions/0041-contract-contained-reviewer-kernel.md).
