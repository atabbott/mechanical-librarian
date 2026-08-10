# AI trust boundary

## Principle

An external model is an untrusted source-only experiment. It does not discover
production credentials, select production inputs, change policy, construct
service requests, persist product advice, or execute writes.

The deterministic `shadow-decision-preview` and qualified automatic-decision
paths are outside the AI boundary. They consume the retained exact,
source-validated `shadow-review-packet-v5` and trusted compiled action
catalogue without model/provider dispatch. Their packet, rule identity,
`source_outcome_id`, `shadow_decision`, and `shadow_outcome` contracts
remain deterministic and authoritative.

## Source-only contained reviewer

The repository workbench has one semantic-neutral `contained-reviewer`
scenario. Its request contains only:

- a bounded instruction;
- a canonical, bounded, sanitized JSON input document; and
- a canonical, bounded output schema from the locally admitted JSON Schema
  subset.

The caller supplies no ReviewCase, packet transport, model-advice, recommendation,
decision, or persistence semantics. The adapter returns exactly one canonical
JSON object that is validated locally against the bound schema, or a closed
value-free failure. CLI structured-output enforcement is never trusted as the
sole validator.

The profile binds exact executable and sandbox digests, versions, model,
reasoning, timeout, output, memory, process, and file limits. Private directory
and no-follow artifact checks occur before invocation. The ordinary child sees
one empty job directory and a read-only descriptor-bound primary credential;
repository, database, media, service configuration, target/provider credentials,
user configuration, and rules remain absent. Shell and unified execution, web
search, apps, agents, plugins, hooks, memories, history, feedback, analytics,
updates, environment inheritance, and approval are disabled.

The workbench durably records bounded authority and input identities before
adapter invocation. The adapter then withholds child release until its
in-memory callback has observed the exact child identity; it does not persist
that identity. Timeout or cancellation kills the captured process group and
proves that identity dead. Credential drift or cleanup
failure creates a value-free trust failure and blocks success. Raw process
streams, prompts, private paths, credentials, and model reasoning never cross
the boundary.

Authentication start/status/logout actions remain adapter-owned convenience
operations over the same private profile. They do not create a product request,
attempt, dispatch, result, inspection, reevaluation, retry, or advice lifecycle.

## Product boundary

The installed runtime, database, HTTP API, and browser contain no reviewer
invocation, reviewer authentication, model-advice projection, or F11
qualification workflow. The former request/result/profile/prompt domain
contracts and request/attempt/dispatch/result tables are intentionally absent
under the pre-release exact-current schema policy.

Historical ADRs 0015 through 0028 record the retired qualification-only design.
[ADR-0041](decisions/0041-contract-contained-reviewer-kernel.md) supersedes
their current implementation decision while preserving that history.
