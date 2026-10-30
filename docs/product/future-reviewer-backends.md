# Future Reviewer Backends

This is a deferred direction, not a current capability. Nothing here is
scheduled or authorised for implementation.

## What exists today

Reviewer access is closed and task-scoped. An owner may select Codex or one
OpenRouter model for the advisory `cover_observation` and `description_rewrite`
tasks only
([ADR-0062](../architecture/decisions/0062-owner-managed-reviewer-access.md)).
The installed Codex reviewer runs inside the contained-reviewer kernel, and
the kernel's generic qualification workflow stays source-only
([ADR-0041](../architecture/decisions/0041-contract-contained-reviewer-kernel.md),
[ADR-0050](../architecture/decisions/0050-bound-installed-owner-assistance.md)).
None of this is a generic reviewer backend or pre-decides the port a future
backend would use.

## Direction

Two independent optional backends may be considered after 1.0:

1. **Hosted OpenRouter reviewer.** An owner-controlled OpenRouter account and
   one exact, qualified free or paid model configuration, beyond the closed
   tasks.
2. **Externally operated model server.** A compatible model server that the
   owner installs and runs separately.

Either needs a newly promoted GitHub issue, a product reviewer contract, a
trust-boundary decision, and a configuration, privacy and secret-reference
design before work starts, with external-service behaviour reverified then.

## Constraints any backend must keep

Every backend sits behind a new application-owned port and keeps the
[AI trust boundary](../architecture/ai-boundary.md). A backend:

- receives only the versioned bounded reviewer packet and selected staged
  attachments;
- returns one direct closed-schema object covering every proposal and no
  others;
- receives no target-service or provider credential, main database, backup,
  production media, mutation port, shell, tool or apply authority;
- cannot choose candidates, evidence, policy, prompts, destinations or writes;
- declares its text, image, context, schema, authentication and model-identity
  capabilities before any work is eligible;
- reports actual model, endpoint and provider identity, which the application
  persists as immutable provenance with the prompt, evidence, schema, policy
  and configuration versions;
- applies hard bounds on input, output, response size, time, concurrency,
  retries and circuit breaking; and
- turns authentication, quota, availability, capability, identity, transport,
  timeout, malformed-output and citation failures into typed safe states,
  never an inferred or repaired decision. A failure suspends only the affected
  configuration under bounded retry and is never evidence about a field.

Further rules:

- **No silent switching.** Changing endpoint, provider, model, free or paid
  status, or privacy policy is an explicit audited configuration change. No
  backend falls back across those boundaries.
- **Qualification does not transfer.** A changed identity or material
  configuration is a new evaluation cohort. Eligibility beyond shadow or
  manual review belongs to the exact backend, model, provider, route, prompt,
  schema and policy tuple, never to a vendor or to free models generally.
- **Validate independently.** The application rejects unknown fields,
  trailing content, partial or duplicate decisions, invented values, unknown
  citations and identity mismatches, whatever the provider claims. Tools,
  plugins, web search and response repair stay disabled.
- **Disclose before any request.** No connection test, preflight or packet is
  sent, and no credential leaves the container, until the owner enables the
  backend and accepts a disclosure matching its current configuration. The
  disclosure names every field, excerpt and attachment category sent, the
  processing parties, and the retention, training, region and cost posture.
  It excludes raw media, whole works, production paths and unrelated
  evidence. A material change invalidates acceptance.
- **Owner-controlled secrets.** Credentials are write-only, reviewer-scoped
  secret references. They never appear in exports, browser readback, logs,
  audit content or support bundles. The image ships no shared credential.
- **Narrow transport.** A dedicated client per backend, restricted to its
  configured origin, with normal TLS validation, no redirects that carry
  credentials, and no state shared with target or provider clients.
- **Core work continues.** Inventory, local evidence, deterministic proposals
  and policy, and human review keep working when every reviewer is absent or
  unhealthy. Reviewer failure never affects container liveness.

The [data-privacy contract](../architecture/data-privacy.md) governs any
external processing.

## Backend-specific constraints

**OpenRouter.** Account-default models and the random `openrouter/free` router
are refused for decision-bearing review. A route that cannot meet its
configured provider, retention, training and region constraints makes the
reviewer unavailable rather than less private. Free-tier exhaustion, price
change or model retirement never selects another model, provider or paid
route.

**External model server.** The image carries no inference runtime, model
weights, downloader, GPU dependency or server lifecycle manager, and the
product never discovers, starts or upgrades the server. Configuration names
one exact `http` or `https` origin, compatibility profile, model ID, privacy
mode and optional reviewer-scoped secret. URLs with credentials, fragments,
ambiguous hosts, other schemes or open paths are refused. Authentication is a
reviewer-scoped bearer or file secret only, never arbitrary headers or
cookies. Compatibility is proven by a bounded
handshake, not inferred from an "OpenAI-compatible" label. Plain HTTP,
loopback or private-network access needs an explicit, visible local-endpoint
mode limited to that origin. DNS rebinding, redirects and file, socket,
link-local, metadata-service or undeclared destinations fail closed. There is
no global TLS-disable option. A custom model starts shadow or manual-only, and
a text-only configuration leaves image-dependent work manual.
