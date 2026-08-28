# ADR-0062: Keep reviewer access owner-managed and task-scoped

- Status: accepted
- Date: 2026-08-28
- Owners: project maintainers
- Supersedes: deployment-owned reviewer enablement in ADR-0058 and the
  deferred OpenRouter boundary in ADR-0041 for `cover_observation` only
- Superseded by: none

## Context

The installed cover flow already has a closed observation packet and no write
authority, but selecting or authenticating its reviewer was deployment work.
Owners need an optional first-run and Settings path that works in both file and
store deployments without turning models into plugins, endpoints, or generic
tasks. Credentials and hosted cover processing require a narrow trust boundary.

## Decision

Application state owns one closed `cover_observation` mapping: absent, Codex,
or OpenRouter with one provider-local model ID. Deployment configuration still
owns the contained Codex profile, TLS, mounts, and process limits. The
OpenRouter key is write-only secret state; Codex authentication is staged in a
private contained `CODEX_HOME` and atomically installed only after the pinned
CLI reports the exact usable account state.

Dispatch rereads the mapping and credential/account state at the invocation
boundary, constructs exactly one selected engine, and never falls back. Both
engines receive the same bounded cover packet and strict result schema.
Persisted provenance binds engine, model, schema/profile bytes, and candidate
identity. Reviewer output remains advisory and receives neither target
credentials nor write authority.

OpenRouter uses only `https://openrouter.ai/api/v1/chat/completions`, the exact
selected model, strict structured output with required parameters, bounded
responses, and no redirects or arbitrary endpoint. Ordinary Settings reads do
not probe the network; explicit refresh and dispatch use typed redacted failure
states. Model catalogue entries are evidence, not durable recommendations. No
recommendation is qualified because no live-key end-to-end call was authorized.

The new application tables, `setup-state-v2`, `owner-settings-v2`, and
`cover-consideration-v3` declare a clean rebuild under ADR-0013. Earlier
exact-predecessor plans and their public command are retired, not retargeted.

## Consequences

- First-run and Settings expose the same optional-first state grammar.
- Clearing or changing access cannot silently switch engines; an unavailable
  selected engine makes that observation unavailable.
- Public views expose secret presence and typed status only, never secret
  values, private paths, raw provider errors, or raw reviewer streams.
- OAuth, provider marketplaces, arbitrary endpoints, generic task registries,
  retries, and reviewer lifecycle management remain outside this decision.
